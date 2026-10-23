import { useEffect, useState } from 'react';
import {
  agentLifecycle,
  fetchAgentThinking,
  listAgentDefinitions,
  listProjectAgents,
  type AgentDefinitionInfo,
  type AgentInstanceInfo,
  type AgentThinkingEvent,
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
        borderRadius: 10,
        color: '#fff',
        fontSize: 12,
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
    <div data-testid={`capability-${agent.id}`} style={{ maxWidth: 340 }}>
      {(agent.plugins ?? []).map((p) => (
        <span key={`p-${p}`} title={`工具: ${p}`} style={{
          display: 'inline-block', padding: '1px 6px', margin: 1,
          borderRadius: 8, fontSize: 11,
          background: '#e6f4ff', color: '#1677ff', whiteSpace: 'nowrap',
        }}>
          🔧 {p}
        </span>
      ))}
      {(agent.skills ?? []).length > 0
        ? agent.skills.map((sk) => (
            <span key={`s-${sk}`} title={`技能: ${sk}`} style={{
              display: 'inline-block', padding: '1px 6px', margin: 1,
              borderRadius: 8, fontSize: 11,
              background: '#f6ffed', color: '#52c41a', whiteSpace: 'nowrap',
            }}>
              ⚡ {sk}
            </span>
          ))
        : (
            <span title="skills=None——安装的 skill 按任务内容自动选择" style={{
              display: 'inline-block', padding: '1px 6px', margin: 1,
              borderRadius: 8, fontSize: 11,
              background: '#f6ffed', color: '#52c41a', whiteSpace: 'nowrap',
            }}>
              ⚡ 全局自动选
            </span>
          )}
      {(agent.stage_affinity ?? []).map((st) => (
        <span key={`st-${st}`} title={`环节: ${st}`} style={{
          display: 'inline-block', padding: '1px 6px', margin: 1,
          borderRadius: 8, fontSize: 11,
          background: '#f5f5f5', color: '#595959', whiteSpace: 'nowrap',
        }}>
          📍{st}
        </span>
      ))}
    </div>
  );
}

/** 上下文压力条: 三段变色 <60% 绿 / 60-90% 黄 / ≥90% 红（§9） */
function ContextBar({ used, threshold }: { used: number; threshold: number }) {
  if (!threshold) {
    return <span style={{ color: '#8c8c8c', fontSize: 12 }}>—</span>;
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
          width: 80,
          height: 8,
          borderRadius: 4,
          background: '#f0f0f0',
          overflow: 'hidden',
        }}
      >
        <div style={{ width: `${pct}%`, height: '100%', background: color }} />
      </div>
      <span style={{ fontSize: 12, color }}>
        {(used / 1000).toFixed(1)}K/{Math.round(threshold / 1000)}K
      </span>
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
    const timer = setInterval(load, 5000); // §9: 5s 轮询
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
      <div style={{ color: '#8c8c8c', padding: 12 }}>
        暂无智能体实例 — 生成计划后按任务环节自动创建
      </div>
    );
  }
  return (
    <div>
      {error && <div role="alert">{error}</div>}
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead>
          <tr style={{ textAlign: 'left', color: '#595959' }}>
            <th style={{ padding: 6 }}>实例</th>
            <th style={{ padding: 6 }}>状态</th>
            <th style={{ padding: 6 }}>当前任务</th>
            <th style={{ padding: 6 }}>可用能力</th>
            <th style={{ padding: 6 }}>上下文压力</th>
            <th style={{ padding: 6 }}>失败计数</th>
            <th style={{ padding: 6 }}>操作</th>
          </tr>
        </thead>
        <tbody>
          {agents.map((a) => (
            <tr
              key={a.id}
              data-testid={`agent-row-${a.id}`}
              style={{ borderTop: '1px solid #f0f0f0' }}
            >
              <td style={{ padding: 6 }}>
                {a.name ?? a.agent_id ?? `#${a.id}`}
                <span style={{ color: '#8c8c8c' }}> #{a.id}</span>
              </td>
              <td style={{ padding: 6 }}>
                <StatusBadge status={a.status} />
              </td>
              <td style={{ padding: 6 }}>
                {a.current_task_id !== null ? (
                  <span>任务 #{a.current_task_id}</span>
                ) : (
                  <span style={{ color: '#bfbfbf' }}>—</span>
                )}
              </td>
              <td style={{ padding: 6 }}>
                <CapabilityBadges agent={a} />
              </td>
              <td style={{ padding: 6 }}>
                <ContextBar
                  used={a.context_tokens}
                  threshold={a.context_threshold}
                />
              </td>
              <td style={{ padding: 6 }}>{a.fail_count}</td>
              <td style={{ padding: 6, display: 'flex', gap: 6 }}>
                {a.status === 'BUSY' || a.status === 'IDLE' ? (
                  <button data-testid={`pause-${a.id}`} onClick={() => act(a.id, 'pause')}>
                    暂停
                  </button>
                ) : null}
                {a.status === 'PAUSED' ? (
                  <button data-testid={`resume-${a.id}`} onClick={() => act(a.id, 'resume')}>
                    恢复
                  </button>
                ) : null}
                {a.status !== 'TERMINATED' ? (
                  <button
                    data-testid={`terminate-${a.id}`}
                    onClick={() => act(a.id, 'terminate')}
                  >
                    终止
                  </button>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <AgentThinkingStream projectId={projectId} agents={agents} />
    </div>
  );
}

/** 返修单 B: 每个智能体的实时思维链（2s 增量拉取, 按实例过滤） */
function AgentThinkingStream({
  projectId,
  agents,
}: {
  projectId: number;
  agents: AgentInstanceInfo[];
}) {
  const [lines, setLines] = useState<
    Record<number, AgentThinkingEvent[]>
  >({});
  const [expanded, setExpanded] = useState<number | null>(null);
  const [lastId, setLastId] = useState(0);

  useEffect(() => {
    let alive = true;
    let afterId = 0;
    const load = async () => {
      try {
        const data = await fetchAgentThinking(projectId, afterId);
        if (!alive) return;
        if (data.events.length > 0) {
          setLines((prev) => {
            const next = { ...prev };
            for (const ev of data.events) {
              const key = ev.agent_instance_id ?? 0;
              next[key] = [...(next[key] ?? []), ev].slice(-50);
            }
            return next;
          });
          afterId = data.last_event_id;
          setLastId(data.last_event_id);
        }
      } catch {
        /* best-effort — 显示链路不影响面板 */
      }
    };
    load();
    const timer = setInterval(load, 2000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [projectId]);

  const busy = agents.filter((a) => a.status === 'BUSY');
  const withThoughts = agents.filter(
    (a) => (lines[a.id]?.length ?? 0) > 0
  );
  const shown = busy.length > 0 ? busy : withThoughts;
  if (shown.length === 0) return null;

  return (
    <div style={{ marginTop: 12 }} data-testid='agent-thinking'>
      <div style={{ fontSize: 12, color: '#8c8c8c', marginBottom: 4 }}>
        实时思维链（last_event #{lastId}）
      </div>
      {shown.map((a) => {
        const mine = lines[a.id] ?? [];
        const open = expanded === a.id || a.status === 'BUSY';
        return (
          <div
            key={a.id}
            data-testid={`agent-think-${a.id}`}
            style={{
              border: '1px solid #f0f0f0',
              borderRadius: 6,
              padding: 8,
              marginBottom: 6,
            }}
          >
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                cursor: 'pointer',
              }}
              onClick={() =>
                setExpanded(open && expanded === a.id ? null : a.id)
              }
            >
              <strong style={{ fontSize: 13 }}>
                🧠 {a.name ?? a.agent_id ?? `#${a.id}`}
                <span style={{ color: '#8c8c8c' }}> #{a.id}</span>
                {a.status === 'BUSY' && (
                  <span style={{ color: '#1677ff' }}> · 工作中</span>
                )}
              </strong>
              <span style={{ color: '#bfbfbf', fontSize: 11 }}>
                {mine.length} 段 {open ? '▲' : '▼'}
              </span>
            </div>
            {open && (
              <div
                style={{
                  marginTop: 6,
                  maxHeight: 180,
                  overflowY: 'auto',
                  fontSize: 12,
                  color: '#595959',
                  background: '#fafafa',
                  padding: 8,
                  borderRadius: 4,
                  whiteSpace: 'pre-wrap',
                }}
              >
                {mine.length === 0
                  ? '（等待思维链…）'
                  : mine
                      .slice(-15)
                      .map((ev) => ev.text ?? '')
                      .join('')}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function TemplatesView() {
  const [defs, setDefs] = useState<AgentDefinitionInfo[]>([]);
  const [error, setError] = useState<string | null>(null);

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
        <div style={{ color: '#8c8c8c', padding: 12 }}>暂无模板</div>
      )}
      {defs.map((d) => (
        <div
          key={d.id}
          data-testid={`agent-def-${d.agent_id}`}
          style={{
            border: '1px solid #f0f0f0',
            borderRadius: 6,
            padding: 10,
            marginBottom: 8,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <strong>{d.name}</strong>
            <span
              style={{
                fontSize: 11,
                padding: '0 6px',
                borderRadius: 8,
                background:
                  d.source === 'BUILT_IN'
                    ? '#e6f4ff'
                    : d.source === 'USER'
                      ? '#f6ffed'
                      : '#fff7e6',
                color: '#595959',
              }}
            >
              {SOURCE_BADGE[d.source] ?? d.source}
            </span>
            <span style={{ color: '#8c8c8c', fontSize: 12 }}>
              {d.agent_id}
            </span>
          </div>
          {d.role_description && (
            <div style={{ color: '#595959', fontSize: 12, marginTop: 4 }}>
              {d.role_description}
            </div>
          )}
          <div style={{ marginTop: 4, fontSize: 12, color: '#8c8c8c' }}>
            环节亲和: {d.stage_affinity.join(' / ') || '—'}
          </div>
        </div>
      ))}
    </div>
  );
}

/** 智能体管理面板（双视图页签——§9 批次 1 UI 交付项） */
export function AgentPanel({
  projectId,
  onChanged,
}: {
  projectId: number;
  onChanged: () => void;
}) {
  const [view, setView] = useState<'instances' | 'templates'>('instances');
  return (
    <section style={{ marginTop: 16 }} data-testid="agent-panel">
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <h3 style={{ margin: 0 }}>智能体</h3>
        <div style={{ display: 'flex', gap: 4 }}>
          <button
            data-testid="tab-instances"
            style={{ fontWeight: view === 'instances' ? 700 : 400 }}
            onClick={() => setView('instances')}
          >
            实例
          </button>
          <button
            data-testid="tab-templates"
            style={{ fontWeight: view === 'templates' ? 700 : 400 }}
            onClick={() => setView('templates')}
          >
            模板
          </button>
        </div>
      </div>
      <div style={{ marginTop: 8 }}>
        {view === 'instances' ? (
          <InstancesView projectId={projectId} onChanged={onChanged} />
        ) : (
          <TemplatesView />
        )}
      </div>
    </section>
  );
}
