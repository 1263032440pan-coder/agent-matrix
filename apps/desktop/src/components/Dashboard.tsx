import { useEffect, useMemo, useState } from 'react';
import type { DashboardData } from '../api';
import { fetchDashboard } from '../api';

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
  const [selectedTask, setSelectedTask] = useState<number | null>(null);

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

  const selected = data.tasks.find((t) => t.id === selectedTask) ?? null;

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
            onSelect={(id) => setSelectedTask(id === selectedTask ? null : id)}
            selectedId={selectedTask}
          />
          {selected && (
            <div
              data-testid="task-detail"
              style={{ marginTop: 12, borderTop: '1px solid #eee', paddingTop: 12 }}
            >
              <h4 style={{ margin: '4px 0' }}>
                {selected.name} <StatusTag status={selected.status} />
              </h4>
              <p style={{ color: '#555', fontSize: 13, margin: 0 }}>
                Task #{selected.id} — 该任务的 artifacts / gate / timeline 在右侧与下方区块
                按 task_id 关联展示。
              </p>
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

          <section data-testid="artifacts" style={card}>
            <h3 style={{ marginTop: 0 }}>Artifacts</h3>
            {data.artifacts.length === 0 && <p style={{ color: '#999' }}>暂无产物</p>}
            <ul style={{ paddingLeft: 18, margin: 0 }}>
              {data.artifacts.map((a) => (
                <li key={a.id}>
                  {a.name} v{a.version} {a.sha256 ? `(sha256 ${a.sha256.slice(0, 8)}…)` : ''}
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
          {data.timeline.map((r) => (
            <li key={r.id}>
              {r.task_name} (attempt {r.attempt}) — {r.started_at ?? '…'} → {r.finished_at ?? '…'}
            </li>
          ))}
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
    </div>
  );
}