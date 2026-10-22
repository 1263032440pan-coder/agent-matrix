import { useEffect, useMemo, useState } from 'react';
import type { DashboardData } from '../api';
import { fetchDashboard } from '../api';
import { ArtifactViewer } from './ArtifactViewer';

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
}

/** WP-018: Current Frontier——READY 任务（可推进边界）。 */
export function frontierIds(tasks: TaskNode[]): number[] {
  return tasks.filter((t) => t.status === 'READY').map((t) => t.id);
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

  const NODE_W = 170;
  const NODE_H = 56;
  const GAP_X = 60;
  const GAP_Y = 24;
  const positions = new Map<number, { x: number; y: number }>();
  [...layers.entries()].forEach(([d, ts]) => {
    ts.forEach((t, i) => {
      positions.set(t.id, { x: d * (NODE_W + GAP_X), y: i * (NODE_H + GAP_Y) });
    });
  });
  return { positions, NODE_W, NODE_H };
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
  const { positions, NODE_W, NODE_H } = useMemo(
    () => layoutTasks(tasks, deps),
    [tasks, deps]
  );

  const maxX = Math.max(0, ...[...positions.values()].map((p) => p.x)) + NODE_W;
  const maxY = Math.max(0, ...[...positions.values()].map((p) => p.y)) + NODE_H;

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
          const x1 = a.x + NODE_W;
          const y1 = a.y + NODE_H / 2;
          const x2 = b.x;
          const y2 = b.y + NODE_H / 2;
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
          const selected = t.id === selectedId;
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
                height={NODE_H}
                rx={8}
                fill="#fff"
                stroke={selected ? '#1890ff' : (STATUS_COLORS[t.status] ?? '#888')}
                strokeWidth={selected ? 3 : 2}
              />
              {/* WP-018: Current Frontier——READY 节点金色虚线外框高亮 */}
              {t.status === 'READY' && (
                <rect
                  width={NODE_W + 8}
                  height={NODE_H + 8}
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
              <text x={12} y={22} fontSize={13} fontWeight={600} fill="#222">
                {t.name.length > 10 ? t.name.slice(0, 10) + '…' : t.name}
              </text>
              <text x={12} y={42} fontSize={11} fill={STATUS_COLORS[t.status] ?? '#888'}>
                {t.status}
              </text>
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
                const taskArts = data.artifacts.filter(
                  (a) => a.task_id === selected.id
                );
                return (
                  <div style={{ marginTop: 8 }}>
                    {taskArts.length === 0 ? (
                      <span style={{ color: '#999', fontSize: 13 }}>
                        该任务暂无产物
                      </span>
                    ) : (
                      taskArts.map((a) => {
                        // V1.5a-1: 文件树缩进——logical_id 路径深度
                        const lid =
                          (a as any).logical_artifact_id || a.name || '';
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
              <li key={e.event_id} style={{ fontSize: 13 }}>
                <code style={{ color: '#666' }}>
                  {e.timestamp ? e.timestamp.slice(11, 19) : '--:--:--'}
                </code>{' '}
                <strong>{e.event_type}</strong>
                {e.phase ? ` · ${e.phase}` : ''}
                {e.message ? ` — ${e.message}` : ''}
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
