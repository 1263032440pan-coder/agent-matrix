import { useEffect, useMemo, useState } from 'react';
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

const PAGE_SIZE_OPTIONS = [10, 20, 50, 100];

/** 项目切换器 + 历史项目（分页 / 搜索 / 全选全不选 / 单删批量删）。 */
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
  // 分页状态
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [query, setQuery] = useState('');

  useEffect(() => {
    listProjectSummaries()
      .then(setProjects)
      .catch(() => undefined);
  }, [currentId]);

  const sorted = useMemo(
    () => [...projects].sort((a, b) => b.id - a.id),
    [projects],
  );
  // 搜索过滤（前端过滤——项目量级足够）
  const filtered = useMemo(
    () =>
      query.trim()
        ? sorted.filter((p) =>
            `${p.id} ${p.name}`.toLowerCase().includes(query.trim().toLowerCase()),
          )
        : sorted,
    [sorted, query],
  );
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const pageItems = useMemo(
    () => filtered.slice((safePage - 1) * pageSize, safePage * pageSize),
    [filtered, safePage, pageSize],
  );

  // 当前页全选状态
  const pageIds = pageItems.map((p) => p.id);
  const allPageSelected =
    pageIds.length > 0 && pageIds.every((id) => selected.has(id));
  const somePageSelected = pageIds.some((id) => selected.has(id));

  const reload = () => {
    listProjectSummaries()
      .then(setProjects)
      .catch(() => undefined);
  };

  const onDelete = async (id: number, name: string) => {
    if (
      !window.confirm(
        `确定删除项目 #${id}「${name}」吗？\n将级联删除其全部任务、产物、执行记录、事件等所有数据，不可恢复。`,
      )
    ) {
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
      if (id === currentId) onSelect(-1);
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
    if (
      !window.confirm(
        `确定批量删除 ${ids.length} 个项目吗？\n所选项目的全部数据（任务/产物/执行/事件）将级联删除，不可恢复。`,
      )
    ) {
      return;
    }
    setBusy(true);
    try {
      const r = await batchDeleteProjects(ids);
      const okCount = r.deleted.filter((d) => !(d as { error?: string }).error)
        .length;
      setMessage(`批量删除完成：${okCount}/${ids.length} 个项目`);
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

  // 全选/全不选：作用于当前页可见项（跨页累计选择保留）
  const togglePageAll = () => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (allPageSelected) {
        pageIds.forEach((id) => next.delete(id));
      } else {
        pageIds.forEach((id) => next.add(id));
      }
      return next;
    });
  };

  const selectAllFiltered = () => {
    setSelected(new Set(filtered.map((p) => p.id)));
  };

  const clearSelection = () => {
    setSelected(new Set());
  };

  const current = projects.find((p) => p.id === currentId);

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
            data-testid="batch-delete-btn"
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
          {/* 工具栏：搜索 + 全部选择/清空 + 每页条数 */}
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 8 }}>
            <input
              placeholder="按名称/ID搜索…"
              value={query}
              data-testid="history-search"
              style={{ padding: '6px 10px', width: 220 }}
              onChange={(e) => {
                setQuery(e.target.value);
                setPage(1);
              }}
            />
            <button onClick={selectAllFiltered} data-testid="select-all-filtered">
              选择全部（{filtered.length}）
            </button>
            <button onClick={clearSelection} disabled={selected.size === 0}>
              全不选
            </button>
            <span style={{ color: '#888', fontSize: 13 }}>已选 {selected.size}</span>
            <label style={{ fontSize: 13, color: '#666' }}>
              每页
              <select
                data-testid="page-size"
                value={pageSize}
                onChange={(e) => {
                  setPageSize(Number(e.target.value));
                  setPage(1);
                }}
                style={{ marginLeft: 6, padding: '4px 8px' }}
              >
                {PAGE_SIZE_OPTIONS.map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
              条
            </label>
            <span style={{ color: '#888', fontSize: 13, marginLeft: 'auto' }}>
              共 {filtered.length} 个项目
            </span>
          </div>

          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ textAlign: 'left', borderBottom: '1px solid #eee' }}>
                <th style={{ padding: 6 }}>
                  <input
                    type="checkbox"
                    data-testid="select-page-all"
                    checked={allPageSelected}
                    ref={(el) => {
                      if (el) el.indeterminate = somePageSelected && !allPageSelected;
                    }}
                    onChange={togglePageAll}
                    title="全选/全不选本页"
                  />
                </th>
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
              {pageItems.length === 0 && (
                <tr>
                  <td colSpan={8} style={{ padding: 12, color: '#999' }}>
                    无匹配项目
                  </td>
                </tr>
              )}
              {pageItems.map((p) => (
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

          {/* 分页条 */}
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 10 }}>
            <button disabled={safePage <= 1} onClick={() => setPage(1)}>
              « 首页
            </button>
            <button disabled={safePage <= 1} onClick={() => setPage(safePage - 1)}>
              ‹ 上一页
            </button>
            <span data-testid="page-info" style={{ fontSize: 13 }}>
              第 {safePage} / {totalPages} 页
            </span>
            <button disabled={safePage >= totalPages} onClick={() => setPage(safePage + 1)}>
              下一页 ›
            </button>
            <button disabled={safePage >= totalPages} onClick={() => setPage(totalPages)}>
              末页 »
            </button>
            <span style={{ fontSize: 12, color: '#888' }}>
              （本页 {pageItems.length} 条）
            </span>
          </div>
        </div>
      )}
    </section>
  );
}
