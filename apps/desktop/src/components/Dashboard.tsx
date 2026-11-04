import { useEffect, useMemo, useState } from 'react';
import type { DashboardData, UserReworkPreview } from '../api';
import { fetchDashboard, userReworkTask } from '../api';
import { ArtifactViewer } from './ArtifactViewer';

const REOPEN_STATUSES = new Set([
  'ACCEPTED',
  'FAILED',
  'BLOCKED',
  'REWORK',
]);

function hardUpstreamAccepted(
  task: { id: number; depends_on?: number[] },
  tasks: Array<{ id: number; status: string }>,
): boolean {
  const byId = new Map(tasks.map((t) => [t.id, t]));
  for (const up of task.depends_on ?? []) {
    const u = byId.get(up);
    if (!u || u.status !== 'ACCEPTED') return false;
  }
  return true;
}

function matchHint(match: Record<string, unknown> | null | undefined): string {
  if (!match) return '';
  const bits: string[] = [];
  if (Array.isArray(match.ext) && match.ext.length) {
    bits.push(`扩展名 ${match.ext.join('/')}`);
  }
  if (Array.isArray(match.name_contains) && match.name_contains.length) {
    bits.push(`文件名含 ${match.name_contains.join('/')}`);
  }
  if (Array.isArray(match.any_name_prefix) && match.any_name_prefix.length) {
    bits.push(`另需前缀 ${match.any_name_prefix.join('/')}`);
  }
  return bits.join('；');
}

const STATUS_COLORS: Record<string, string> = {
  ACCEPTED: '#52c41a',
  EXECUTION_DONE: '#1890ff',
  RUNNING: '#faad14',
  READY: '#722ed1',
  REWORK: '#f5222d',
  BLOCKED: '#f5222d',
  CREATED: '#8c8c8c',
};

export function StatusTag({ status }: { status: string }) {
  return (
    <span
      style={{
        padding: '2px 8px',
        borderRadius: 4,
        background: STATUS_COLORS[status] ?? '#888',
        color: '#fff',
        fontSize: 12,
      }}
    >
      {status}
    </span>
  );
}

interface TaskNode {
  id: number;
  name: string;
  status: string;
  depends_on?: number[];
  required_deliverables?: Array<{
    id: string | null;
    name: string;
    match?: Record<string, unknown> | null;
  }>;
  stage_outputs?: string[];
  delivery_gap?: {
    reason?: string;
    soft_accepted?: boolean;
    problems?: string[];
    unmet?: Array<{ token_id?: string | null; kind?: string; hint?: string }>;
    scan_root?: string;
    scan_path?: string;
  } | null;
}

/** WP-018: Current Frontier——READY 任务（可推进边界）。 */
export function frontierIds(tasks: TaskNode[]): number[] {
  return tasks.filter((t) => t.status === 'READY').map((t) => t.id);
}

function productLabels(t: TaskNode): string[] {
  // AI 写入的 outputs / stage_outputs 优先展示；清单名仅补未覆盖项
  const fromAi = (t.stage_outputs ?? []).filter(Boolean);
  const fromReq = (t.required_deliverables ?? [])
    .map((d) => d.name)
    .filter(Boolean);
  if (fromAi.length === 0) return fromReq;
  const seen = fromAi.map((s) => s.toLowerCase());
  const uncovered = fromReq.filter((n) => {
    const nl = n.toLowerCase();
    return !seen.some((s) => s.includes(nl) || nl.includes(s));
  });
  return [...fromAi, ...uncovered];
}

/** Layered layout: depth = longest upstream chain. Nodes on the left, deps flow right. */
function layoutTasks(tasks: TaskNode[], deps: Array<[number, number]>) {
  const depMap = new Map<number, number[]>();
  deps.forEach(([down, up]) => {
    depMap.set(down, [...(depMap.get(down) ?? []), up]);
  });
  const depth = new Map<number, number>();
  const getDepth = (id: number, seen = new Set<number>()): number => {
    if (seen.has(id)) return 0;
    seen.add(id);
    const ups = depMap.get(id) ?? [];
    if (ups.length === 0) return 0;
    return 1 + Math.max(...ups.map((u) => getDepth(u, seen)));
  };
  tasks.forEach((t) => depth.set(t.id, getDepth(t.id)));

  const layers = new Map<number, TaskNode[]>();
  tasks.forEach((t) => {
    const d = depth.get(t.id) ?? 0;
    layers.set(d, [...(layers.get(d) ?? []), t]);
  });

  const NODE_W = 200;
  const BASE_H = 56;
  const GAP_X = 60;
  const GAP_Y = 24;
  const heights = new Map<number, number>();
  tasks.forEach((t) => {
    const all = productLabels(t);
    const shown = Math.min(all.length, 3);
    const extra = all.length > 3 ? 1 : 0; // "+N 项" 行
    const lines = shown + extra;
    const gapExtra = t.delivery_gap?.soft_accepted ? 14 : 0;
    heights.set(
      t.id,
      BASE_H + gapExtra + (lines > 0 ? 4 + lines * 14 : 0),
    );
  });
  const positions = new Map<number, { x: number; y: number }>();
  [...layers.entries()].forEach(([d, ts]) => {
    let y = 0;
    ts.forEach((t) => {
      positions.set(t.id, { x: d * (NODE_W + GAP_X), y });
      y += (heights.get(t.id) ?? BASE_H) + GAP_Y;
    });
  });
  return { positions, heights, NODE_W, BASE_H };
}

export function TaskGraphView({
  tasks,
  deps,
  onSelect,
  selectedId,
}: {
  tasks: TaskNode[];
  deps: Array<[number, number]>;
  onSelect: (id: number) => void;
  selectedId: number | null;
}) {
  const { positions, heights, NODE_W, BASE_H } = useMemo(
    () => layoutTasks(tasks, deps),
    [tasks, deps]
  );

  const maxX = Math.max(0, ...[...positions.values()].map((p) => p.x)) + NODE_W;
  const maxY = Math.max(
    0,
    ...tasks.map((t) => {
      const p = positions.get(t.id);
      return (p?.y ?? 0) + (heights.get(t.id) ?? BASE_H);
    }),
  );

  return (
    <div style={{ overflowX: 'auto', padding: 8 }}>
      <svg width={Math.max(maxX, 320)} height={Math.max(maxY, 140)}>
        <defs>
          <marker id="arrow" markerWidth="8" markerHeight="8" refX="8" refY="4" orient="auto">
            <path d="M0,0 L8,4 L0,8 z" fill="#888" />
          </marker>
        </defs>
        {deps.map(([down, up]) => {
          const a = positions.get(up);
          const b = positions.get(down);
          if (!a || !b) return null;
          const ah = heights.get(up) ?? BASE_H;
          const bh = heights.get(down) ?? BASE_H;
          const x1 = a.x + NODE_W;
          const y1 = a.y + ah / 2;
          const x2 = b.x;
          const y2 = b.y + bh / 2;
          const midX = (x1 + x2) / 2;
          return (
            <polyline
              key={`${up}-${down}`}
              points={`${x1},${y1} ${midX},${y1} ${midX},${y2} ${x2},${y2}`}
              fill="none"
              stroke="#888"
              strokeWidth={1.5}
              markerEnd="url(#arrow)"
            />
          );
        })}
        {tasks.map((t) => {
          const p = positions.get(t.id)!;
          const h = heights.get(t.id) ?? BASE_H;
          const selected = t.id === selectedId;
          const products = productLabels(t).slice(0, 3);
          const more = productLabels(t).length - products.length;
          return (
            <g
              key={t.id}
              transform={`translate(${p.x},${p.y})`}
              onClick={() => onSelect(t.id)}
              style={{ cursor: 'pointer' }}
              data-testid={`task-node-${t.id}`}
            >
              <rect
                width={NODE_W}
                height={h}
                rx={8}
                fill="#fff"
                stroke={selected ? '#1890ff' : (STATUS_COLORS[t.status] ?? '#888')}
                strokeWidth={selected ? 3 : 2}
              />
              {/* WP-018: Current Frontier——READY 节点金色虚线外框高亮 */}
              {t.status === 'READY' && (
                <rect
                  width={NODE_W + 8}
                  height={h + 8}
                  x={-4}
                  y={-4}
                  rx={10}
                  fill="none"
                  stroke="#faad14"
                  strokeWidth={2}
                  strokeDasharray="6 3"
                  data-testid={`frontier-glow-${t.id}`}
                />
              )}
              <text x={12} y={20} fontSize={13} fontWeight={600} fill="#222">
                {t.name.length > 12 ? t.name.slice(0, 12) + '…' : t.name}
              </text>
              <text x={12} y={38} fontSize={11} fill={STATUS_COLORS[t.status] ?? '#888'}>
                {t.status}
              </text>
              {t.delivery_gap?.soft_accepted && (
                <text
                  x={12}
                  y={50}
                  fontSize={10}
                  fill="#d48806"
                  data-testid={`delivery-gap-badge-${t.id}`}
                >
                  {t.delivery_gap.reason === 'UNMET_AFTER_EARLY_STOP'
                    ? '返工用尽·未齐（已放行）'
                    : '预算用尽·未齐（已放行）'}
                </text>
              )}
              {products.length > 0 && (
                <g data-testid={`task-products-${t.id}`}>
                  {products.map((name, i) => (
                    <text
                      key={`${t.id}-p-${i}`}
                      x={12}
                      y={(t.delivery_gap?.soft_accepted ? 66 : 54) + i * 14}
                      fontSize={10}
                      fill="#595959"
                    >
                      · {name.length > 14 ? name.slice(0, 14) + '…' : name}
                    </text>
                  ))}
                  {more > 0 && (
                    <text
                      x={12}
                      y={
                        (t.delivery_gap?.soft_accepted ? 66 : 54) +
                        products.length * 14
                      }
                      fontSize={10}
                      fill="#8c8c8c"
                    >
                      +{more} 项…
                    </text>
                  )}
                </g>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

export function Dashboard({
  projectId,
  paused,
  onExecute,
  onPlan,
  busy,
}: {
  projectId: number;
  paused?: boolean;
  onExecute?: () => void;
  onPlan?: () => void;
  busy?: boolean;
}) {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [viewingArtifact, setViewingArtifact] = useState<number | null>(null);
  // null = auto-follow mode (show current running task); a task id = user
  // pinned selection (clicking the same node again un-pins back to auto).
  const [pinnedTask, setPinnedTask] = useState<number | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [reworkOpen, setReworkOpen] = useState(false);
  const [reworkCascade, setReworkCascade] = useState(false);
  const [reworkPreview, setReworkPreview] = useState<UserReworkPreview | null>(
    null,
  );
  const [reworkBusy, setReworkBusy] = useState(false);
  const [reworkErr, setReworkErr] = useState<string | null>(null);

  useEffect(() => {
    if (paused) return; // execute 短请求期间暂停；规划中应继续拉任务图
    let cancelled = false;
    const load = () =>
      fetchDashboard(projectId)
        .then((d) => {
          if (!cancelled) {
            setData(d);
            setError(null);
          }
        })
        .catch((e) => {
          if (!cancelled) setError(String(e));
        });
    load();
    const timer = setInterval(load, 10000); // 10s polling (SQLite lock contention)
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [projectId, paused]);

  if (error && !data) return <div role="alert">Error: {error}</div>;
  if (!data) return <div>Loading dashboard…</div>;

  // Dependency edges from TaskDependency (depends_on = upstream ids)
  const deps: Array<[number, number]> = [];
  data.tasks.forEach((t) => {
    (t.depends_on ?? []).forEach((up) => deps.push([t.id, up]));
  });

  // Auto-follow: pinned (user click) wins; otherwise follow the running task;
  // fallback to the latest non-terminal task for context on idle projects.
  const runningTask = data.tasks.find((t) => t.status === 'RUNNING') ?? null;
  const focusFallback =
    data.tasks.find((t) => t.status === 'EXECUTION_DONE') ??
    data.tasks.find((t) => t.status === 'READY') ??
    null;
  const focusId = pinnedTask ?? runningTask?.id ?? focusFallback?.id ?? null;
  const selected = data.tasks.find((t) => t.id === focusId) ?? null;
  const isAutoFollow = pinnedTask === null;

  const card: React.CSSProperties = {
    background: 'var(--ui-bg-elevated)',
    border: '1px solid var(--ui-stroke-muted)',
    borderRadius: 4,
    padding: 12,
    height: '100%',
    display: 'flex',
    flexDirection: 'column',
    minHeight: 0,
    overflow: 'hidden',
  };

  return (
    <div
      data-testid="dashboard-workbench"
      style={{
        height: '100%',
        minHeight: 0,
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
        textAlign: 'left',
        background: 'var(--ui-bg)',
      }}
    >
      <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
        <section data-testid="task-graph" style={card}>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              marginBottom: 8,
              flexShrink: 0,
            }}
          >
            <span style={{ fontSize: 12, color: 'var(--ui-text-muted)' }}>
              {data.project.name}
            </span>
            <div style={{ flex: 1 }} />
            <button type="button" className="ui-btn" disabled={busy || !onPlan} onClick={onPlan}>
              重新生成计划
            </button>
            <button
              type="button"
              className="ui-btn ui-btn-primary"
              data-testid="graph-execute"
              disabled={busy || !onExecute}
              onClick={onExecute}
            >
              执行项目
            </button>
          </div>
          <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
          <TaskGraphView
            tasks={data.tasks}
            deps={deps}
            onSelect={(id) => {
              if (pinnedTask === id) {
                setPinnedTask(null);
                setDetailOpen(false);
              } else {
                setPinnedTask(id);
                setDetailOpen(true);
              }
            }}
            selectedId={focusId}
          />
          </div>
          {selected && detailOpen && (
            <div
              className="overlay-backdrop"
              data-testid="task-detail-overlay"
              onClick={(e) => {
                if (e.target === e.currentTarget) setDetailOpen(false);
              }}
            >
            <div
              className="overlay-panel"
              data-testid="task-detail"
              onClick={(e) => e.stopPropagation()}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                <button type="button" className="ui-btn" onClick={() => setDetailOpen(false)}>
                  关闭
                </button>
              </div>
              <h4 style={{ margin: '4px 0' }}>
                {selected.name} <StatusTag status={selected.status} />{' '}
                <span
                  style={{
                    fontSize: 11,
                    padding: '1px 8px',
                    borderRadius: 8,
                    background: isAutoFollow ? '#e6f7ff' : '#fff7e6',
                    color: isAutoFollow ? '#1890ff' : '#fa8c16',
                    border: `1px solid ${isAutoFollow ? '#91d5ff' : '#ffd591'}`,
                  }}
                >
                  {isAutoFollow ? '自动跟随执行中' : '已固定（点击同节点取消）'}
                </span>
              </h4>
              <p style={{ color: '#555', fontSize: 13, margin: 0 }}>
                Task #{selected.id}
              </p>
              {(() => {
                const canReopen = REOPEN_STATUSES.has(selected.status);
                const upsOk = hardUpstreamAccepted(selected, data.tasks);
                const disabled = !canReopen || !upsOk || reworkBusy;
                let why = '';
                if (!canReopen) why = '当前状态不可重开';
                else if (!upsOk) why = '上游未验收';
                return (
                  <div style={{ marginTop: 8 }}>
                    <button
                      type="button"
                      data-testid="task-user-rework"
                      disabled={disabled}
                      onClick={async () => {
                        setReworkErr(null);
                        setReworkCascade(false);
                        setReworkBusy(true);
                        try {
                          const prev = await userReworkTask(
                            projectId,
                            selected.id,
                            {
                              cascade_hard_downstream: false,
                              dry_run: true,
                            },
                          );
                          setReworkPreview(prev);
                          setReworkOpen(true);
                        } catch (e) {
                          setReworkErr(String(e));
                        } finally {
                          setReworkBusy(false);
                        }
                      }}
                      style={{
                        padding: '4px 12px',
                        fontSize: 13,
                        cursor: disabled ? 'not-allowed' : 'pointer',
                        opacity: disabled ? 0.5 : 1,
                      }}
                    >
                      重新执行
                    </button>
                    {why ? (
                      <span
                        style={{ marginLeft: 8, fontSize: 12, color: '#888' }}
                      >
                        {why}
                      </span>
                    ) : null}
                    {reworkErr ? (
                      <div
                        role="alert"
                        style={{ color: '#cf1322', fontSize: 12, marginTop: 4 }}
                      >
                        {reworkErr}
                      </div>
                    ) : null}
                  </div>
                );
              })()}
              {(selected.task_load != null ||
                (selected.obligation_token_ids?.length ?? 0) > 0 ||
                selected.decomposed_from ||
                selected.bin_packed) && (
                <div
                  data-testid="task-load"
                  style={{ fontSize: 12, color: '#444', marginTop: 6 }}
                >
                  <div>
                    负荷{' '}
                    {selected.task_load ?? '—'}
                    {selected.load_max != null
                      ? ` / ${selected.load_max}`
                      : ''}
                    {selected.bin_packed ? ' · 兜底装箱' : ''}
                    {selected.decomposed_from
                      ? ` · 拆自 ${selected.decomposed_from}`
                      : ''}
                  </div>
                  {(selected.obligation_token_ids?.length ?? 0) > 0 && (
                    <div style={{ color: '#666', wordBreak: 'break-all' }}>
                      令牌：{selected.obligation_token_ids!.join(', ')}
                    </div>
                  )}
                </div>
              )}
              {((selected.required_plugins?.length ?? 0) > 0 ||
                selected.bound_agent ||
                (selected.plugin_gap?.length ?? 0) > 0) && (
                <div
                  data-testid="plugin-capability"
                  style={{ fontSize: 12, color: '#444', marginTop: 6 }}
                >
                  <div>
                    Agent{' '}
                    {selected.bound_agent?.name ||
                      selected.bound_agent?.agent_id ||
                      '—'}
                    {selected.bound_agent?.instance_id != null
                      ? ` #${selected.bound_agent.instance_id}`
                      : ''}
                  </div>
                  {(selected.agent_plugins?.length ?? 0) > 0 && (
                    <div style={{ color: '#666', wordBreak: 'break-all' }}>
                      插件：{selected.agent_plugins!.join(', ')}
                    </div>
                  )}
                  {(selected.required_plugins?.length ?? 0) > 0 && (
                    <div style={{ color: '#666', wordBreak: 'break-all' }}>
                      义务所需：{selected.required_plugins!.join(', ')}
                    </div>
                  )}
                  {(selected.plugin_gap?.length ?? 0) > 0 && (
                    <div
                      style={{
                        color: '#cf1322',
                        fontWeight: 600,
                        wordBreak: 'break-all',
                      }}
                    >
                      缺口：{selected.plugin_gap!.join(', ')}
                    </div>
                  )}
                </div>
              )}
              {selected.verification_level && (
                <div
                  data-testid="task-verify-level"
                  style={{ fontSize: 12, color: '#444', marginTop: 6 }}
                >
                  验证等级：{selected.verification_level}
                </div>
              )}
              {(selected.rework_count != null
                || selected.first_pass != null
                || selected.soft_accepted) && (
                <div
                  data-testid="task-delivery-stats"
                  style={{ fontSize: 12, color: '#444', marginTop: 4 }}
                >
                  {selected.first_pass
                    ? '首轮交付'
                    : selected.soft_accepted
                      ? '软放行'
                      : '非首轮'}
                  {selected.rework_count != null
                    ? ` · 返工 ${selected.rework_count} 次`
                    : ''}
                  {selected.run_attempt != null
                    ? ` · attempt ${selected.run_attempt}`
                    : ''}
                </div>
              )}
              {selected.delivery_gap?.soft_accepted && (
                <div
                  data-testid="delivery-gap-detail"
                  style={{
                    marginTop: 8,
                    padding: '8px 10px',
                    background: '#fffbe6',
                    border: '1px solid #ffe58f',
                    borderRadius: 6,
                    fontSize: 12,
                    color: '#614700',
                  }}
                >
                  <div style={{ fontWeight: 600, marginBottom: 4 }}>
                    {selected.delivery_gap.reason === 'UNMET_AFTER_EARLY_STOP'
                      ? '返工用尽·未齐（已放行）'
                      : '预算用尽·未齐（已放行）'}
                    {selected.delivery_gap.scan_root
                      ? ` · 扫描：${
                          selected.delivery_gap.scan_root === 'project'
                            ? '项目目录'
                            : '本任务目录'
                        }`
                      : ''}
                  </div>
                  <ul style={{ margin: 0, paddingLeft: 18 }}>
                    {(selected.delivery_gap.problems?.length
                      ? selected.delivery_gap.problems
                      : (selected.delivery_gap.unmet ?? []).map(
                          (u) => u.hint || u.token_id || u.kind || '未兑现项',
                        )
                    ).map((p, i) => (
                      <li key={i}>{p}</li>
                    ))}
                  </ul>
                </div>
              )}
              {(() => {
                const evs = data.runtime_events ?? [];
                const graceEv = [...evs]
                  .reverse()
                  .find(
                    (e) =>
                      e.event_type === 'TOOL_BUDGET_GRACE' &&
                      e.task_id === selected.id,
                  );
                if (!graceEv) return null;
                const gtext =
                  (graceEv.payload?.grace_text as string | undefined) ||
                  graceEv.message ||
                  '';
                // Grace 之后同任务的 Stop 结果（DELIVERY_VALIDATOR_RUN）
                const stopEv =
                  evs.find(
                    (e) =>
                      e.event_type === 'DELIVERY_VALIDATOR_RUN' &&
                      e.task_id === selected.id &&
                      e.event_id > graceEv.event_id,
                  ) ??
                  [...evs]
                    .reverse()
                    .find(
                      (e) =>
                        e.event_type === 'DELIVERY_VALIDATOR_RUN' &&
                        e.task_id === selected.id,
                    );
                const stopOk = stopEv?.payload?.ok;
                const stopProblems = (stopEv?.payload?.problems as
                  | string[]
                  | undefined) ?? [];
                const stopLine =
                  stopEv == null
                    ? null
                    : stopOk === true
                      ? 'Stop：通过'
                      : stopOk === false
                        ? `Stop：未通过${
                            stopProblems.length
                              ? ` — ${stopProblems.join('；')}`
                              : stopEv.message
                                ? ` — ${stopEv.message}`
                                : ''
                          }`
                        : stopEv.message
                          ? `Stop：${stopEv.message}`
                          : null;
                return (
                  <div
                    data-testid="task-grace"
                    style={{
                      marginTop: 10,
                      padding: 10,
                      background: '#fff7e6',
                      border: '1px solid #ffd591',
                      borderRadius: 6,
                      fontSize: 13,
                    }}
                  >
                    <div style={{ fontWeight: 600, marginBottom: 4 }}>
                      工具预算用尽 · Grace
                      {graceEv.payload?.tool_rounds
                        ? `（${graceEv.payload.tool_rounds} 轮）`
                        : ''}
                    </div>
                    <pre
                      style={{
                        margin: 0,
                        whiteSpace: 'pre-wrap',
                        fontFamily: 'inherit',
                        color: '#555',
                        maxHeight: 160,
                        overflow: 'auto',
                      }}
                    >
                      {gtext}
                    </pre>
                    {stopLine ? (
                      <div
                        data-testid="task-grace-stop"
                        style={{
                          marginTop: 8,
                          paddingTop: 8,
                          borderTop: '1px dashed #ffd591',
                          color: stopOk === false ? '#cf1322' : '#389e0d',
                          fontWeight: 500,
                        }}
                      >
                        {stopLine}
                      </div>
                    ) : null}
                  </div>
                );
              })()}
              <div
                data-testid="required-deliverables"
                style={{ marginTop: 10 }}
              >
                <div
                  style={{
                    fontSize: 12,
                    fontWeight: 600,
                    color: '#333',
                    marginBottom: 4,
                  }}
                >
                  要求产物
                </div>
                {(selected.required_deliverables?.length ?? 0) === 0 ? (
                  <span style={{ color: '#999', fontSize: 13 }}>
                    本节点无强制交付项（非交付环节或未分配清单）
                  </span>
                ) : (
                  <ul
                    style={{
                      margin: 0,
                      paddingLeft: 18,
                      fontSize: 13,
                      color: '#333',
                    }}
                  >
                    {selected.required_deliverables!.map((d, i) => {
                      const hint = matchHint(d.match);
                      return (
                        <li key={d.id ?? `${d.name}-${i}`}>
                          {d.name}
                          {hint ? (
                            <span style={{ color: '#888', fontSize: 12 }}>
                              {' '}
                              — {hint}
                            </span>
                          ) : null}
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
              {(() => {
                const taskArts = data.artifacts.filter(
                  (a) => a.task_id === selected.id
                );
                return (
                  <div style={{ marginTop: 10 }}>
                    <div
                      style={{
                        fontSize: 12,
                        fontWeight: 600,
                        color: '#333',
                        marginBottom: 4,
                      }}
                    >
                      已产出
                    </div>
                    {taskArts.length === 0 ? (
                      <span style={{ color: '#999', fontSize: 13 }}>
                        该任务暂无产物文件
                      </span>
                    ) : (
                      taskArts.map((a) => {
                        // V1.5a-1: 文件树缩进——logical_id 路径深度
                        const lid =
                          (a as { logical_artifact_id?: string }).logical_artifact_id ||
                          a.name ||
                          '';
                        const depth = Math.max(
                          0, (lid.match(/\//g) || []).length - 1
                        );
                        const shortName = lid.split('/').pop() || a.name;
                        return (
                        <button
                          key={a.id}
                          onClick={() => setViewingArtifact(a.id)}
                          style={{
                            display: 'block',
                            width: '100%',
                            textAlign: 'left',
                            padding: `6px 10px 6px ${10 + depth * 16}px`,
                            marginBottom: 4,
                            border: '1px solid #d9d9d9',
                            borderRadius: 4,
                            background: '#fafafa',
                            cursor: 'pointer',
                            fontSize: 13,
                          }}
                        >
                          📄 {shortName} v{a.version}{' '}
                          <span style={{ color: '#999' }}>
                            ({a.sha256 ? a.sha256.slice(0, 8) : ''}) 点击查看
                          </span>
                        </button>
                        );
                      })
                    )}
                  </div>
                );
              })()}
            </div>
            </div>
          )}
        </section>
      </div>

      {viewingArtifact !== null && (
        <ArtifactViewer
          artifactId={viewingArtifact}
          onClose={() => setViewingArtifact(null)}
        />
      )}
      {reworkOpen && selected && (
        <div
          data-testid="task-user-rework-dialog"
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0,0,0,0.35)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
          }}
        >
          <div
            style={{
              background: '#fff',
              borderRadius: 8,
              padding: 20,
              maxWidth: 480,
              width: '90%',
              boxShadow: '0 8px 24px rgba(0,0,0,0.18)',
            }}
          >
            <h3 style={{ marginTop: 0 }}>重新执行「{selected.name}」？</h3>
            <ul style={{ fontSize: 13, color: '#444', paddingLeft: 18 }}>
              <li>本任务进入返工并立即变为可执行；契约与令牌不变。</li>
              <li>历史产物保留；清单项在文件还在时保持 ✅。</li>
              <li>已在执行的直接下游不会被打断。</li>
              <li>MANUAL：确认后变为 READY，不会自动开跑。</li>
            </ul>
            <label
              style={{
                display: 'flex',
                gap: 8,
                alignItems: 'flex-start',
                fontSize: 13,
                marginTop: 8,
              }}
            >
              <input
                type="checkbox"
                checked={reworkCascade}
                disabled={reworkBusy}
                onChange={async (ev) => {
                  const on = ev.target.checked;
                  setReworkCascade(on);
                  setReworkBusy(true);
                  setReworkErr(null);
                  try {
                    const prev = await userReworkTask(projectId, selected.id, {
                      cascade_hard_downstream: on,
                      dry_run: true,
                    });
                    setReworkPreview(prev);
                  } catch (e) {
                    setReworkErr(String(e));
                  } finally {
                    setReworkBusy(false);
                  }
                }}
              />
              <span>
                连带直接 HARD 下游一并重开（只一层；孙子保持原状）
              </span>
            </label>
            {reworkCascade && reworkPreview && (
              <div style={{ fontSize: 12, marginTop: 10, color: '#555' }}>
                <div>
                  <strong>将打回并等待本任务再次验收：</strong>
                  {(reworkPreview.cascaded_task_ids ?? [])
                    .map(
                      (id) =>
                        data.tasks.find((t) => t.id === id)?.name ?? `#${id}`,
                    )
                    .join('、') || '（无）'}
                </div>
                <div style={{ marginTop: 4 }}>
                  <strong>保持原状：</strong>
                  {(reworkPreview.skipped_downstream ?? [])
                    .map((s) => {
                      const n =
                        data.tasks.find((t) => t.id === s.task_id)?.name ??
                        `#${s.task_id}`;
                      return `${n}(${s.status})`;
                    })
                    .join('、') || '（无）'}
                </div>
              </div>
            )}
            {reworkErr ? (
              <div role="alert" style={{ color: '#cf1322', fontSize: 12 }}>
                {reworkErr}
              </div>
            ) : null}
            <div
              style={{
                display: 'flex',
                justifyContent: 'flex-end',
                gap: 8,
                marginTop: 16,
              }}
            >
              <button
                type="button"
                disabled={reworkBusy}
                onClick={() => {
                  setReworkOpen(false);
                  setReworkPreview(null);
                }}
              >
                取消
              </button>
              <button
                type="button"
                data-testid="task-user-rework-confirm"
                disabled={reworkBusy}
                onClick={async () => {
                  setReworkBusy(true);
                  setReworkErr(null);
                  try {
                    await userReworkTask(projectId, selected.id, {
                      cascade_hard_downstream: reworkCascade,
                      dry_run: false,
                    });
                    setReworkOpen(false);
                    setReworkPreview(null);
                    const d = await fetchDashboard(projectId);
                    setData(d);
                  } catch (e) {
                    setReworkErr(String(e));
                  } finally {
                    setReworkBusy(false);
                  }
                }}
              >
                确认
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
