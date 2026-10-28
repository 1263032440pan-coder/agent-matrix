import { useEffect, useMemo, useState } from 'react';
import type { DashboardData } from '../api';
import { fetchDashboard, reclassifyDelivery } from '../api';
import { ArtifactViewer } from './ArtifactViewer';

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
    heights.set(t.id, BASE_H + (lines > 0 ? 4 + lines * 14 : 0));
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
              {products.length > 0 && (
                <g data-testid={`task-products-${t.id}`}>
                  {products.map((name, i) => (
                    <text
                      key={`${t.id}-p-${i}`}
                      x={12}
                      y={54 + i * 14}
                      fontSize={10}
                      fill="#595959"
                    >
                      · {name.length > 14 ? name.slice(0, 14) + '…' : name}
                    </text>
                  ))}
                  {more > 0 && (
                    <text
                      x={12}
                      y={54 + products.length * 14}
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

export function Dashboard({ projectId, paused }: { projectId: number; paused?: boolean }) {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [viewingArtifact, setViewingArtifact] = useState<number | null>(null);
  // null = auto-follow mode (show current running task); a task id = user
  // pinned selection (clicking the same node again un-pins back to auto).
  const [pinnedTask, setPinnedTask] = useState<number | null>(null);

  useEffect(() => {
    if (paused) return; // plan/execute in flight — don't contend for SQLite lock
    let cancelled = false;
    const load = () =>
      fetchDashboard(projectId)
        .then((d) => !cancelled && setData(d))
        .catch((e) => {
          // SQLite lock contention during execute: keep last data, retry next tick
          if (!cancelled && !data) setError(String(e));
        });
    load();
    const timer = setInterval(load, 10000); // 10s polling (SQLite lock contention)
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [projectId, data]);

  if (error) return <div role="alert">Error: {error}</div>;
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
    background: '#fff',
    border: '1px solid #e5e7eb',
    borderRadius: 8,
    padding: 16,
  };

  return (
    <div
      style={{
        padding: 24,
        fontFamily: 'sans-serif',
        background: '#f5f6f8',
        minHeight: '100vh',
        textAlign: 'left',
      }}
    >
      <header style={{ marginBottom: 16 }}>
        <h2 style={{ margin: 0 }}>
          {data.project.name} <StatusTag status={data.project.status} />{' '}
          <small style={{ color: '#888' }}>({data.project.automation_mode})</small>
        </h2>
      </header>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 2fr) minmax(260px, 1fr)',
          gap: 16,
          alignItems: 'start',
        }}
      >
        <section data-testid="task-graph" style={card}>
          <h3 style={{ marginTop: 0 }}>Task Graph</h3>
          <TaskGraphView
            tasks={data.tasks}
            deps={deps}
            onSelect={(id) => {
              // Click: pin this node; click again: unpin → back to auto-follow
              if (pinnedTask === id) {
                setPinnedTask(null);
              } else {
                setPinnedTask(id);
              }
            }}
            selectedId={focusId}
          />
          {selected && (
            <div
              data-testid="task-detail"
              style={{ marginTop: 12, borderTop: '1px solid #eee', paddingTop: 12 }}
            >
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
          )}
        </section>

        <div style={{ display: 'grid', gap: 16 }}>
          <section data-testid="readiness" style={card}>
            <h3 style={{ marginTop: 0 }}>Method Readiness</h3>
            <p>{data.readiness ? `Assessment v${data.readiness.version}` : 'No assessment yet'}</p>
            <p>Gap Count: {data.gap_count}</p>
          </section>

          <section data-testid="current-task" style={card}>
            <h3 style={{ marginTop: 0 }}>Current Task</h3>
            <p>{data.current_task ? data.current_task.name : 'None running'}</p>
            <p>
              Workers: {data.worker_status.running} running / {data.worker_status.ready} ready
            </p>
          </section>

          {/* V1.6 序6: 交付画像标识 + 三件状态行 + 重新判定入口 */}
          <section data-testid="delivery-status" style={card}>
            <h3 style={{ marginTop: 0 }}>交付基线</h3>
            {data.delivery_profile === 'SOFTWARE_DEV' ? (
              <p>
                <span data-testid="dev-badge">🏷️ 软件开发需求</span>
              </p>
            ) : (
              <p>
                <small style={{ color: '#999' }}>未识别为软件开发需求</small>{' '}
                <button
                  data-testid="reclassify-btn"
                  onClick={async () => {
                    try {
                      await reclassifyDelivery(data.project.id);
                      const d2 = await fetchDashboard(data.project.id);
                      setData(d2);
                    } catch {
                      /* best-effort */
                    }
                  }}
                  style={{ fontSize: 12, marginLeft: 6 }}
                >
                  重新判定
                </button>
              </p>
            )}
            {data.delivery_status && (
              <p style={{ fontSize: 14 }}>
                方案PPT {data.delivery_status.ppt ? '✅' : '❌'} |{' '}
                可运行代码 {data.delivery_status.code ? '✅' : '❌'} |{' '}
                测试报告 {data.delivery_status.test_report ? '✅' : '❌'}
              </p>
            )}
            <p
              data-testid="delivery-validator-meta"
              style={{ fontSize: 12, color: '#666', marginBottom: 0 }}
            >
              {data.delivery_validator_hash_prefix
                ? `校验脚本已生成 · hash ${data.delivery_validator_hash_prefix}…`
                : '校验脚本未生成'}
              {data.delivery_validator_generated_at
                ? ` · ${data.delivery_validator_generated_at.slice(0, 19)}`
                : ''}
            </p>
          </section>

          <section data-testid="artifacts" style={card}>
            <h3 style={{ marginTop: 0 }}>Artifacts</h3>
            {data.artifacts.length === 0 && <p style={{ color: '#999' }}>暂无产物</p>}
            <ul style={{ paddingLeft: 18, margin: 0 }}>
              {data.artifacts.map((a) => (
                <li key={a.id}>
                  <a
                    href="#"
                    onClick={(e) => {
                      e.preventDefault();
                      setViewingArtifact(a.id);
                    }}
                    style={{ color: '#1890ff' }}
                  >
                    {a.name} v{a.version}
                  </a>{' '}
                  {a.sha256 ? (
                    <span style={{ color: '#999', fontSize: 12 }}>
                      {a.sha256.slice(0, 8)}…
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
          </section>

          <section data-testid="gate-results" style={card}>
            <h3 style={{ marginTop: 0 }}>Gate Results</h3>
            {data.gate_results.length === 0 && <p style={{ color: '#999' }}>暂无 Gate 结果</p>}
            <ul style={{ paddingLeft: 18, margin: 0 }}>
              {data.gate_results.map((g) => (
                <li key={g.id}>
                  Task #{g.task_id} — <StatusTag status={g.status} />
                </li>
              ))}
            </ul>
          </section>
        </div>
      </div>

      <section data-testid="timeline" style={{ ...card, marginTop: 16 }}>
        <h3 style={{ marginTop: 0 }}>Execution Timeline</h3>
        {data.timeline.length === 0 && <p style={{ color: '#999' }}>暂无执行记录</p>}
        <ul style={{ paddingLeft: 18 }}>
          {data.timeline.map((r) => {
            const art = data.artifacts.find((a) => a.task_id === r.task_id);
            return (
              <li key={r.id}>
                {r.task_name} (attempt {r.attempt}) — {r.started_at ?? '…'} →{' '}
                {r.finished_at ?? '…'}
                {art && (
                  <a
                    href="#"
                    onClick={(e) => {
                      e.preventDefault();
                      setViewingArtifact(art.id);
                    }}
                    style={{ color: '#1890ff', marginLeft: 8, fontSize: 12 }}
                  >
                    📄 产物
                  </a>
                )}
              </li>
            );
          })}
        </ul>
      </section>

      {(data.runtime_events?.length ?? 0) > 0 && (
        <section data-testid="runtime-events" style={{ ...card, marginTop: 16 }}>
          <h3 style={{ marginTop: 0 }}>Runtime Events（实时进度）</h3>
          <ul style={{ paddingLeft: 18, maxHeight: 240, overflowY: 'auto' }}>
            {(data.runtime_events ?? []).map((e) => (
              <li
                key={e.event_id}
                style={{
                  fontSize: 13,
                  ...(e.event_type === 'TOOL_BUDGET_GRACE'
                    ? {
                        background: '#fff7e6',
                        borderLeft: '3px solid #fa8c16',
                        padding: '4px 8px',
                        marginBottom: 4,
                      }
                    : {}),
                }}
              >
                <code style={{ color: '#666' }}>
                  {e.timestamp ? e.timestamp.slice(11, 19) : '--:--:--'}
                </code>{' '}
                <strong>{e.event_type}</strong>
                {e.phase ? ` · ${e.phase}` : ''}
                {e.message ? ` — ${e.message}` : ''}
                {e.event_type === 'TOOL_BUDGET_GRACE' &&
                e.payload?.grace_text ? (
                  <div style={{ color: '#666', marginTop: 2 }}>
                    {String(e.payload.grace_text).slice(0, 200)}
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      )}
      {viewingArtifact !== null && (
        <ArtifactViewer
          artifactId={viewingArtifact}
          onClose={() => setViewingArtifact(null)}
        />
      )}
    </div>
  );
}
