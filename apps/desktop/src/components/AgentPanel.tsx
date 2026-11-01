import { useEffect, useState } from 'react';
import {
  agentLifecycle,
  listAgentDefinitions,
  listProjectAgents,
  type AgentDefinitionInfo,
  type AgentInstanceInfo,
} from '../api';

/** 六态徽章色（20号规范 §9——BUSY/IDLE/DEGRADED 各有展示语义） */
const STATUS_COLOR: Record<string, string> = {
  CREATED: '#8c8c8c',
  IDLE: '#52c41a',
  BUSY: '#1677ff',
  PAUSED: '#faad14',
  DEGRADED: '#fa541c',
  TERMINATED: '#d9d9d9',
};

const STATUS_TEXT: Record<string, string> = {
  CREATED: '已创建',
  IDLE: '空闲',
  BUSY: '执行中',
  PAUSED: '已暂停',
  DEGRADED: '降级中',
  TERMINATED: '已终止',
};

function StatusBadge({ status }: { status: string }) {
  const color = STATUS_COLOR[status] ?? '#8c8c8c';
  return (
    <span
      data-testid={`agent-status-${status}`}
      style={{
        display: 'inline-block',
        padding: '1px 8px',
        borderRadius: 4,
        color: '#fff',
        fontSize: 11,
        background: color,
      }}
    >
      {STATUS_TEXT[status] ?? status}
    </span>
  );
}

/** 能力徽章: 该智能体可用的工具/技能/环节（白名单护栏可视化 V1.4c） */
function CapabilityBadges({ agent }: { agent: AgentInstanceInfo }) {
  return (
    <div data-testid={`capability-${agent.id}`} style={{ maxWidth: '100%' }}>
      {(agent.plugins ?? []).map((p) => (
        <span
          key={`p-${p}`}
          title={`工具: ${p}`}
          style={{
            display: 'inline-block',
            padding: '1px 6px',
            margin: 1,
            borderRadius: 4,
            fontSize: 11,
            background: 'var(--ui-accent-soft)',
            color: 'var(--ui-accent)',
            whiteSpace: 'nowrap',
          }}
        >
          {p}
        </span>
      ))}
      {(agent.skills ?? []).length > 0 ? (
        agent.skills.map((sk) => (
          <span
            key={`s-${sk}`}
            title={`技能: ${sk}`}
            style={{
              display: 'inline-block',
              padding: '1px 6px',
              margin: 1,
              borderRadius: 4,
              fontSize: 11,
              background: 'var(--ui-bg-chrome)',
              color: 'var(--ui-text)',
              whiteSpace: 'nowrap',
            }}
          >
            {sk}
          </span>
        ))
      ) : (
        <span
          title="skills=None——安装的 skill 按任务内容自动选择"
          style={{
            display: 'inline-block',
            padding: '1px 6px',
            margin: 1,
            borderRadius: 4,
            fontSize: 11,
            background: 'var(--ui-bg-chrome)',
            color: 'var(--ui-text-muted)',
            whiteSpace: 'nowrap',
          }}
        >
          全局自动选
        </span>
      )}
      {(agent.stage_affinity ?? []).map((st) => (
        <span
          key={`st-${st}`}
          title={`环节: ${st}`}
          style={{
            display: 'inline-block',
            padding: '1px 6px',
            margin: 1,
            borderRadius: 4,
            fontSize: 11,
            background: 'var(--ui-bg)',
            color: 'var(--ui-text-muted)',
            border: '1px solid var(--ui-stroke-muted)',
            whiteSpace: 'nowrap',
          }}
        >
          {st}
        </span>
      ))}
    </div>
  );
}

function capabilityCount(agent: AgentInstanceInfo): number {
  const skills =
    (agent.skills ?? []).length > 0 ? agent.skills.length : 1; // 全局自动选
  return (
    (agent.plugins ?? []).length + skills + (agent.stage_affinity ?? []).length
  );
}

/** 上下文压力条: 三段变色 <60% 绿 / 60-90% 黄 / ≥90% 红（§9） */
function ContextBar({
  used,
  threshold,
  compact,
}: {
  used: number;
  threshold: number;
  compact?: boolean;
}) {
  if (!threshold) {
    return (
      <span style={{ color: 'var(--ui-text-muted)', fontSize: 12 }}>—</span>
    );
  }
  const pct = Math.min(100, Math.round((used / threshold) * 100));
  const color = pct >= 90 ? '#ff4d4f' : pct >= 60 ? '#faad14' : '#52c41a';
  return (
    <div
      data-testid="context-bar"
      title={`${used} / ${threshold} tokens (${pct}%)`}
      style={{ display: 'flex', alignItems: 'center', gap: 6 }}
    >
      <div
        style={{
          width: compact ? 48 : 80,
          height: compact ? 6 : 8,
          borderRadius: 3,
          background: 'var(--ui-bg-chrome)',
          overflow: 'hidden',
        }}
      >
        <div style={{ width: `${pct}%`, height: '100%', background: color }} />
      </div>
      <span style={{ fontSize: 11, color }}>
        {(used / 1000).toFixed(1)}K/{Math.round(threshold / 1000)}K
      </span>
    </div>
  );
}

function AgentCompactRow({
  agent,
  expanded,
  onToggle,
  onAct,
}: {
  agent: AgentInstanceInfo;
  expanded: boolean;
  onToggle: () => void;
  onAct: (id: number, action: 'pause' | 'resume' | 'terminate') => void;
}) {
  const capN = capabilityCount(agent);
  return (
    <div
      data-testid={`agent-row-${agent.id}`}
      data-expanded={expanded ? 'true' : 'false'}
      style={{
        border: `1px solid ${
          expanded ? 'var(--ui-accent)' : 'var(--ui-stroke-muted)'
        }`,
        background: expanded ? 'var(--ui-accent-soft)' : 'var(--ui-bg)',
        borderRadius: 4,
        padding: 8,
        marginBottom: 6,
        cursor: 'pointer',
        textAlign: 'left',
      }}
      onClick={onToggle}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          flexWrap: 'wrap',
        }}
      >
        <strong style={{ fontSize: 13 }}>
          {agent.name ?? agent.agent_id ?? `#${agent.id}`}
          <span style={{ color: 'var(--ui-text-muted)', fontWeight: 400 }}>
            {' '}
            #{agent.id}
          </span>
        </strong>
        <StatusBadge status={agent.status} />
        <div style={{ flex: 1 }} />
        <span
          data-testid={`fail-count-${agent.id}`}
          style={{ fontSize: 11, color: 'var(--ui-text-muted)' }}
        >
          败{agent.fail_count}
        </span>
      </div>
      <div
        style={{
          fontSize: 12,
          color: 'var(--ui-text-muted)',
          marginTop: 4,
        }}
      >
        任务{' '}
        {agent.current_task_id !== null ? (
          <span>#{agent.current_task_id}</span>
        ) : (
          '—'
        )}
      </div>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          marginTop: 4,
        }}
      >
        <ContextBar
          used={agent.context_tokens}
          threshold={agent.context_threshold}
          compact
        />
        {!expanded && (
          <span style={{ fontSize: 11, color: 'var(--ui-text-muted)' }}>
            +{capN} 能力 · 点击展开
          </span>
        )}
      </div>

      {expanded && (
        <div
          data-testid={`agent-expand-${agent.id}`}
          style={{ marginTop: 8 }}
          onClick={(e) => e.stopPropagation()}
        >
          <div
            style={{
              fontSize: 11,
              color: 'var(--ui-text-muted)',
              marginBottom: 4,
            }}
          >
            可用能力
          </div>
          <CapabilityBadges agent={agent} />
          <div style={{ marginTop: 8 }}>
            <ContextBar
              used={agent.context_tokens}
              threshold={agent.context_threshold}
            />
          </div>
          <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
            {agent.status === 'BUSY' || agent.status === 'IDLE' ? (
              <button
                type="button"
                className="ui-btn"
                data-testid={`pause-${agent.id}`}
                onClick={() => onAct(agent.id, 'pause')}
              >
                暂停
              </button>
            ) : null}
            {agent.status === 'PAUSED' ? (
              <button
                type="button"
                className="ui-btn"
                data-testid={`resume-${agent.id}`}
                onClick={() => onAct(agent.id, 'resume')}
              >
                恢复
              </button>
            ) : null}
            {agent.status !== 'TERMINATED' ? (
              <button
                type="button"
                className="ui-btn"
                data-testid={`terminate-${agent.id}`}
                onClick={() => onAct(agent.id, 'terminate')}
              >
                终止
              </button>
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}

function InstancesView({
  projectId,
  onChanged,
}: {
  projectId: number;
  onChanged: () => void;
}) {
  const [agents, setAgents] = useState<AgentInstanceInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const data = await listProjectAgents(projectId);
        if (alive) {
          setAgents(data);
          setError(null);
        }
      } catch (e) {
        if (alive) setError(String(e));
      }
    };
    load();
    const timer = setInterval(load, 5000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [projectId]);

  const act = async (id: number, action: 'pause' | 'resume' | 'terminate') => {
    try {
      await agentLifecycle(projectId, id, action);
      onChanged();
    } catch (e) {
      setError(String(e));
    }
  };

  if (agents.length === 0) {
    return (
      <div style={{ color: 'var(--ui-text-muted)', padding: 8, fontSize: 13 }}>
        暂无智能体实例 — 生成计划后按任务环节自动创建
      </div>
    );
  }

  return (
    <div data-testid="agent-instances-compact">
      {error && <div role="alert">{error}</div>}
      {agents.map((a) => (
        <AgentCompactRow
          key={a.id}
          agent={a}
          expanded={expandedId === a.id}
          onToggle={() =>
            setExpandedId((cur) => (cur === a.id ? null : a.id))
          }
          onAct={act}
        />
      ))}
    </div>
  );
}

function TemplatesView() {
  const [defs, setDefs] = useState<AgentDefinitionInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  useEffect(() => {
    listAgentDefinitions()
      .then((d) => {
        setDefs(d);
        setError(null);
      })
      .catch((e) => setError(String(e)));
  }, []);

  const SOURCE_BADGE: Record<string, string> = {
    BUILT_IN: '内置',
    USER: '用户',
    CONTROLLER_GENERATED: 'AI 生成',
  };

  return (
    <div>
      {error && <div role="alert">{error}</div>}
      {defs.length === 0 && !error && (
        <div style={{ color: 'var(--ui-text-muted)', padding: 8 }}>暂无模板</div>
      )}
      {defs.map((d) => {
        const open = expandedId === d.id;
        return (
          <div
            key={d.id}
            data-testid={`agent-def-${d.agent_id}`}
            style={{
              border: `1px solid ${
                open ? 'var(--ui-accent)' : 'var(--ui-stroke-muted)'
              }`,
              borderRadius: 4,
              padding: 8,
              marginBottom: 6,
              background: open ? 'var(--ui-accent-soft)' : 'var(--ui-bg)',
              cursor: 'pointer',
              textAlign: 'left',
            }}
            onClick={() =>
              setExpandedId((cur) => (cur === d.id ? null : d.id))
            }
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <strong style={{ fontSize: 13 }}>{d.name}</strong>
              <span
                style={{
                  fontSize: 11,
                  padding: '0 6px',
                  borderRadius: 4,
                  background: 'var(--ui-bg-chrome)',
                  color: 'var(--ui-text-muted)',
                }}
              >
                {SOURCE_BADGE[d.source] ?? d.source}
              </span>
            </div>
            <div style={{ fontSize: 12, color: 'var(--ui-text-muted)', marginTop: 4 }}>
              环节亲和: {d.stage_affinity.join(' / ') || '—'}
            </div>
            {open && (
              <div style={{ marginTop: 8, fontSize: 12 }} onClick={(e) => e.stopPropagation()}>
                {d.role_description && (
                  <div style={{ color: 'var(--ui-text)', marginBottom: 4 }}>
                    {d.role_description}
                  </div>
                )}
                <div style={{ color: 'var(--ui-text-muted)' }}>
                  plugins: {(d.plugins ?? []).join(', ') || '—'}
                </div>
                <div style={{ color: 'var(--ui-text-muted)' }}>
                  skills: {(d.skills ?? []).join(', ') || '（全局自动选）'}
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** 智能体管理面板（侧栏紧凑 · 维度不删减 SYS-169） */
export function AgentPanel({
  projectId,
  onChanged,
}: {
  projectId: number;
  onChanged: () => void;
}) {
  const [view, setView] = useState<'instances' | 'templates'>('instances');
  return (
    <section data-testid="agent-panel" style={{ textAlign: 'left' }}>
      <div className="ui-segment" style={{ marginBottom: 8 }}>
        <button
          type="button"
          data-testid="tab-instances"
          data-active={view === 'instances'}
          onClick={() => setView('instances')}
        >
          实例
        </button>
        <button
          type="button"
          data-testid="tab-templates"
          data-active={view === 'templates'}
          onClick={() => setView('templates')}
        >
          模板
        </button>
      </div>
      {view === 'instances' ? (
        <InstancesView projectId={projectId} onChanged={onChanged} />
      ) : (
        <TemplatesView />
      )}
    </section>
  );
}
