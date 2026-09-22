import { useEffect, useState } from 'react';
import type { DashboardData } from '../api';
import { fetchDashboard } from '../api';

const STATUS_COLORS: Record<string, string> = {
  ACCEPTED: '#52c41a',
  EXECUTION_DONE: '#1890ff',
  RUNNING: '#faad14',
  READY: '#722ed1',
  REWORK: '#f5222d',
  BLOCKED: '#f5222d',
};

function StatusTag({ status }: { status: string }) {
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

export function Dashboard({ projectId }: { projectId: number }) {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      fetchDashboard(projectId)
        .then((d) => !cancelled && setData(d))
        .catch((e) => !cancelled && setError(String(e)));
    load();
    const timer = setInterval(load, 3000); // V0.1 polling (no event engine)
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [projectId]);

  if (error) return <div role="alert">Error: {error}</div>;
  if (!data) return <div>Loading dashboard…</div>;

  return (
    <div style={{ padding: 16, fontFamily: 'sans-serif' }}>
      <h2>Project Dashboard</h2>
      <section data-testid="project-status">
        <h3>Project Status</h3>
        <p>
          {data.project.name} — <StatusTag status={data.project.status} /> (
          {data.project.automation_mode})
        </p>
      </section>

      <section data-testid="readiness">
        <h3>Method Readiness</h3>
        <p>
          {data.readiness
            ? `Assessment v${data.readiness.version}`
            : 'No assessment yet'}
        </p>
        <p>Gap Count: {data.gap_count}</p>
      </section>

      <section data-testid="task-graph">
        <h3>Task Graph</h3>
        <ul>
          {data.tasks.map((t) => (
            <li key={t.id}>
              {t.name} <StatusTag status={t.status} />
            </li>
          ))}
        </ul>
      </section>

      <section data-testid="current-task">
        <h3>Current Task</h3>
        <p>{data.current_task ? data.current_task.name : 'None running'}</p>
        <p>
          Workers: {data.worker_status.running} running /{' '}
          {data.worker_status.ready} ready
        </p>
      </section>

      <section data-testid="artifacts">
        <h3>Artifacts</h3>
        <ul>
          {data.artifacts.map((a) => (
            <li key={a.id}>
              {a.name} v{a.version}{' '}
              {a.sha256 ? `(sha256 ${a.sha256.slice(0, 8)}…)` : ''}
            </li>
          ))}
        </ul>
      </section>

      <section data-testid="gate-results">
        <h3>Gate Results</h3>
        <ul>
          {data.gate_results.map((g) => (
            <li key={g.id}>
              Task #{g.task_id} — <StatusTag status={g.status} />
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}