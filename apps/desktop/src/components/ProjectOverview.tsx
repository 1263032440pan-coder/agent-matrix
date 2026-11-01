import { useEffect, useState } from 'react';
import type { DashboardData } from '../api';
import { fetchDashboard } from '../api';
import { DeliveryChecklistPanel } from './DeliveryChecklistPanel';
import { StatusTag } from './Dashboard';

/** SYS-170/171→178: 项目概览 — 指标卡 + 产物清单实时对比（无 Timeline / 无交付质量·方法就绪） */
export function ProjectOverview({
  projectId,
  paused,
  onBack,
  onExecute,
  onPlan,
  busy,
}: {
  projectId: number;
  paused?: boolean;
  onBack: () => void;
  onExecute?: () => void;
  onPlan?: () => void;
  busy?: boolean;
}) {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (paused) return;
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
    const t = setInterval(load, 10000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [projectId, paused]);

  if (error && !data) return <div role="alert">Error: {error}</div>;
  if (!data) return <div className="pane-scroll">Loading…</div>;

  const accepted = data.tasks.filter((t) => t.status === 'ACCEPTED').length;

  return (
    <div
      data-testid="project-overview"
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        minHeight: 0,
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          padding: '10px 12px',
          borderBottom: '1px solid var(--ui-stroke-muted)',
          flexShrink: 0,
        }}
      >
        <button
          type="button"
          className="ui-btn"
          data-testid="overview-back"
          onClick={onBack}
        >
          ← 返回
        </button>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 600 }}>
            {data.project.name} <StatusTag status={data.project.status} />
          </div>
          <div style={{ fontSize: 12, color: 'var(--ui-text-muted)' }}>
            FULL_AUTO · {data.project.status}
          </div>
        </div>
        <button
          type="button"
          className="ui-btn"
          disabled={busy || !onPlan}
          onClick={onPlan}
        >
          重新生成计划
        </button>
        <button
          type="button"
          className="ui-btn ui-btn-primary"
          data-testid="overview-execute"
          disabled={busy || !onExecute}
          onClick={onExecute}
        >
          执行项目
        </button>
      </div>

      <div className="pane-scroll" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 1fr 1fr',
            gap: 10,
          }}
        >
          <div className="ui-metric" data-testid="overview-progress">
            <div className="ui-metric-label">进度</div>
            <div className="ui-metric-value">
              {accepted} / {data.tasks.length} 任务完成
            </div>
          </div>
          <div className="ui-metric" data-testid="current-task">
            <div className="ui-metric-label">Workers</div>
            <div className="ui-metric-value">
              {data.worker_status.running} running · {data.worker_status.ready}{' '}
              ready
            </div>
          </div>
          <div className="ui-metric">
            <div className="ui-metric-label">当前任务</div>
            <div className="ui-metric-value">
              {data.current_task ? data.current_task.name : 'None running'}
            </div>
          </div>
        </div>

        <DeliveryChecklistPanel
          tasks={data.tasks}
          checklist={data.delivery_checklist}
        />
      </div>
    </div>
  );
}
