import { useEffect, useState } from 'react';
import { listProjectSummaries, type ProjectSummary } from '../api';

const card: React.CSSProperties = {
  background: '#fff',
  border: '1px solid #eee',
  borderRadius: 8,
  padding: 16,
  marginBottom: 16,
};

/** WP-018 补：项目切换器 + 历史项目列表（含任务统计）。 */
export function ProjectSwitcher({
  currentId,
  onSelect,
}: {
  currentId: number | null;
  onSelect: (id: number) => void;
}) {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    listProjectSummaries()
      .then(setProjects)
      .catch(() => undefined);
  }, [currentId]);

  const current = projects.find((p) => p.id === currentId);
  const sorted = [...projects].sort((a, b) => b.id - a.id);

  return (
    <section data-testid="project-switcher" style={card}>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <strong>当前项目：</strong>
        <select
          data-testid="project-select"
          value={currentId ?? ''}
          onChange={(e) => onSelect(Number(e.target.value))}
          style={{ padding: '6px 10px', minWidth: 260 }}
        >
          {sorted.length === 0 && <option value="">（无项目）</option>}
          {sorted.map((p) => (
            <option key={p.id} value={p.id}>
              #{p.id} {p.name}（{p.task_total ?? 0} 任务
              {p.task_accepted ? ` / ${p.task_accepted} 完成` : ''}）
            </option>
          ))}
        </select>
        <button onClick={() => setOpen((v) => !v)}>
          {open ? '收起历史' : `历史项目（${sorted.length}）`}
        </button>
        {current && (
          <span style={{ color: '#888', fontSize: 13 }}>
            {current.automation_mode} · {current.status}
            {current.created_at ? ` · ${current.created_at.slice(0, 10)}` : ''}
          </span>
        )}
      </div>

      {open && (
        <div data-testid="project-history" style={{ marginTop: 12 }}>
          <input
            placeholder="按名称搜索历史项目…"
            style={{ padding: '6px 10px', width: 280, marginBottom: 8 }}
            onChange={(e) => {
              const q = e.target.value.trim();
              const rows = document.querySelectorAll<HTMLTableRowElement>(
                '[data-testid^="history-row-"]',
              );
              rows.forEach((r) => {
                r.style.display =
                  !q || r.textContent?.includes(q) ? '' : 'none';
              });
            }}
          />
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ textAlign: 'left', borderBottom: '1px solid #eee' }}>
                <th style={{ padding: 6 }}>ID</th>
                <th style={{ padding: 6 }}>名称</th>
                <th style={{ padding: 6 }}>模式</th>
                <th style={{ padding: 6 }}>任务</th>
                <th style={{ padding: 6 }}>已完成</th>
                <th style={{ padding: 6 }}>创建</th>
                <th style={{ padding: 6 }} />
              </tr>
            </thead>
            <tbody>
              {sorted.map((p) => (
                <tr key={p.id} data-testid={`history-row-${p.id}`}>
                  <td style={{ padding: 6 }}>#{p.id}</td>
                  <td style={{ padding: 6 }}>{p.name}</td>
                  <td style={{ padding: 6 }}>{p.automation_mode}</td>
                  <td style={{ padding: 6 }}>{p.task_total ?? 0}</td>
                  <td style={{ padding: 6 }}>{p.task_accepted ?? 0}</td>
                  <td style={{ padding: 6 }}>
                    {p.created_at ? p.created_at.slice(0, 10) : '-'}
                  </td>
                  <td style={{ padding: 6 }}>
                    <button
                      disabled={p.id === currentId}
                      onClick={() => onSelect(p.id)}
                    >
                      {p.id === currentId ? '当前' : '切换'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
