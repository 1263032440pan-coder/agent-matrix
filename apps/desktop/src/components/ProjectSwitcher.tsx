import { useEffect, useState } from 'react';
import {
  batchDeleteProjects,
  deleteProject,
  listProjectSummaries,
  type ProjectSummary,
} from '../api';

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
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    listProjectSummaries()
      .then(setProjects)
      .catch(() => undefined);
  }, [currentId]);

  const current = projects.find((p) => p.id === currentId);
  const sorted = [...projects].sort((a, b) => b.id - a.id);

  const reload = () => {
    listProjectSummaries()
      .then(setProjects)
      .catch(() => undefined);
  };

  const onDelete = async (id: number, name: string) => {
    if (!window.confirm(
      `确定删除项目 #${id}「${name}」吗？
将级联删除其全部任务、产物、执行记录、事件等所有数据，不可恢复。`,
    )) {
      return;
    }
    setBusy(true);
    try {
      const r = await deleteProject(id);
      setMessage(
        `已删除 #${id}「${name}」— ${Object.entries(r.tables)
          .filter(([t]) => t !== 'project')
          .map(([t, n]) => `${t}:${n}`)
          .join(' ')}`,
      );
      if (id === currentId) onSelect(-1); // 当前项目被删 → 清空选择
      reload();
    } catch (e) {
      setMessage(String(e));
    } finally {
      setBusy(false);
    }
  };

  const onBatchDelete = async () => {
    const ids = [...selected];
    if (ids.length === 0) return;
    if (!window.confirm(
      `确定批量删除 ${ids.length} 个项目吗？
所选项目的全部数据（任务/产物/执行/事件）将级联删除，不可恢复。`,
    )) {
      return;
    }
    setBusy(true);
    try {
      const r = await batchDeleteProjects(ids);
      setMessage(`批量删除完成：${r.deleted.length} 个项目`);
      setSelected(new Set());
      if (ids.includes(currentId ?? -1)) onSelect(-1);
      reload();
    } catch (e) {
      setMessage(String(e));
    } finally {
      setBusy(false);
    }
  };

  const toggle = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

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
        {open && selected.size > 0 && (
          <button
            onClick={onBatchDelete}
            disabled={busy}
            style={{ color: '#f5222d', borderColor: '#f5222d' }}
          >
            删除所选（{selected.size}）
          </button>
        )}
        {current && (
          <span style={{ color: '#888', fontSize: 13 }}>
            {current.automation_mode} · {current.status}
            {current.created_at ? ` · ${current.created_at.slice(0, 10)}` : ''}
          </span>
        )}
      </div>
      {message && (
        <div data-testid="delete-message" style={{ marginTop: 8, fontSize: 13, color: '#52c41a' }}>
          {message}
        </div>
      )}

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
                  <td style={{ padding: 6 }}>
                    <input
                      type="checkbox"
                      checked={selected.has(p.id)}
                      onChange={() => toggle(p.id)}
                      data-testid={`select-${p.id}`}
                    />
                  </td>
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
                    <button
                      disabled={busy}
                      onClick={() => onDelete(p.id, p.name)}
                      style={{ marginLeft: 6, color: '#f5222d' }}
                      data-testid={`delete-${p.id}`}
                    >
                      删除
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
