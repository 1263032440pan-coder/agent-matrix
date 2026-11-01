import type { DashboardData } from '../api';

type Checklist = NonNullable<DashboardData['delivery_checklist']>;
type CheckItem = Checklist['items'][number];
type Task = DashboardData['tasks'][number];

const DELIV_LABEL: Record<CheckItem['status'], string> = {
  matched: '✅',
  in_progress: '⏳',
  gap: '❌',
  pending: '○',
};

const TASK_DONE = new Set(['ACCEPTED']);
const TASK_ACTIVE = new Set([
  'RUNNING',
  'READY',
  'REWORK',
  'EXECUTION_DONE',
]);

function taskBadge(status: string): string {
  if (TASK_DONE.has(status)) return '✅';
  if (TASK_ACTIVE.has(status)) return '⏳';
  if (status === 'FAILED' || status === 'BLOCKED') return '❌';
  return '○';
}

function isRealReq(
  r: NonNullable<Task['required_deliverables']>[number],
): boolean {
  const id = (r.id ?? '').toString().trim();
  const name = (r.name ?? '').toString().trim();
  return Boolean(id || name);
}

function DelivRow({ it }: { it: CheckItem }) {
  return (
    <li
      data-testid={`delivery-checklist-item-${it.id}`}
      style={{ marginBottom: 6 }}
    >
      <div>
        <span>{DELIV_LABEL[it.status]} </span>
        <span>{it.name || it.id}</span>
        {it.owner_task_name ? (
          <span style={{ color: 'var(--ui-text-muted)', fontSize: 12 }}>
            {' '}
            · #{it.owner_task_id} {it.owner_task_name}
          </span>
        ) : null}
      </div>
      {it.matched_paths.length > 0 ? (
        <div
          style={{
            fontSize: 12,
            color: 'var(--ui-text)',
            fontFamily:
              'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
            wordBreak: 'break-all',
          }}
        >
          {it.matched_paths.map((p) => (
            <div key={p}>{p}</div>
          ))}
        </div>
      ) : it.status === 'matched' ? (
        <div style={{ fontSize: 12, color: 'var(--ui-text-muted)' }}>
          已匹配（未解析到相对路径）
        </div>
      ) : null}
      {it.hint && it.status !== 'matched' ? (
        <div style={{ fontSize: 12, color: 'var(--ui-text-muted)' }}>
          {it.hint}
        </div>
      ) : null}
    </li>
  );
}

/** 流程节点实时产物：先完整列出全部清单项，再按节点归类 */
export function DeliveryChecklistPanel({
  tasks,
  checklist,
}: {
  tasks: Task[];
  checklist?: Checklist | null;
}) {
  const items = checklist?.items ?? [];
  const byId = new Map(items.map((it) => [it.id, it] as const));
  const byOwner = new Map<number, CheckItem[]>();
  for (const it of items) {
    if (it.owner_task_id == null) continue;
    const arr = byOwner.get(it.owner_task_id) ?? [];
    arr.push(it);
    byOwner.set(it.owner_task_id, arr);
  }
  const unassigned = items.filter((it) => it.owner_task_id == null);

  const doneNodes = tasks.filter((t) => TASK_DONE.has(t.status)).length;
  const delivTotal = checklist?.total ?? 0;
  const delivMatched = checklist?.matched ?? 0;

  return (
    <section
      data-testid="delivery-checklist"
      style={{
        flex: 1,
        minHeight: 200,
        border: '1px solid var(--ui-stroke-muted)',
        background: 'var(--ui-bg-elevated)',
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          padding: '8px 10px',
          borderBottom: '1px solid var(--ui-stroke-muted)',
          fontWeight: 600,
          fontSize: 13,
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          flexWrap: 'wrap',
          flexShrink: 0,
        }}
      >
        <span>流程节点（实时产物对比）</span>
        <span
          data-testid="delivery-checklist-summary"
          style={{
            fontWeight: 400,
            fontSize: 12,
            color: 'var(--ui-text-muted)',
          }}
        >
          {tasks.length === 0
            ? '暂无任务'
            : `节点 ${doneNodes}/${tasks.length} 完成${
                delivTotal > 0
                  ? ` · 产物 ${delivMatched}/${delivTotal} 已齐`
                  : ''
              }`}
        </span>
      </div>

      <div
        style={{
          flex: 1,
          minHeight: 0,
          overflow: 'auto',
          padding: '8px 12px',
        }}
      >
        {/* 全部产物：保证不因滚动/节点折叠而漏看 */}
        <div
          data-testid="delivery-checklist-all"
          style={{ marginBottom: 14 }}
        >
          <div
            style={{
              fontWeight: 600,
              fontSize: 13,
              marginBottom: 6,
              color: 'var(--ui-text)',
            }}
          >
            全部产物
          </div>
          {items.length === 0 ? (
            <div
              style={{ color: 'var(--ui-text-muted)', fontSize: 13 }}
              data-testid="delivery-checklist-empty"
            >
              暂无交付清单（请先完成规划）
            </div>
          ) : (
            <ul style={{ margin: 0, padding: 0, listStyle: 'none' }}>
              {items.map((it) => (
                <DelivRow key={`all-${it.id}`} it={it} />
              ))}
            </ul>
          )}
        </div>

        <div
          style={{
            fontWeight: 600,
            fontSize: 13,
            marginBottom: 6,
            color: 'var(--ui-text)',
          }}
        >
          按流程节点
        </div>
        {tasks.length === 0 && (
          <div style={{ color: 'var(--ui-text-muted)', fontSize: 13 }}>
            暂无流程节点
          </div>
        )}
        <ul style={{ margin: 0, padding: 0, listStyle: 'none' }}>
          {tasks.map((t) => {
            const owned = byOwner.get(t.id) ?? [];
            const fromReq = (t.required_deliverables ?? [])
              .filter(isRealReq)
              .map((r) => {
                const id = String(r.id ?? r.name);
                return (
                  byId.get(id) ??
                  ({
                    id,
                    name: r.name,
                    match: r.match ?? null,
                    owner_task_id: t.id,
                    owner_task_name: t.name,
                    owner_task_status: t.status,
                    status: 'pending' as const,
                    matched_paths: [] as string[],
                    hint: null,
                  } satisfies CheckItem)
                );
              });
            // 合并主责项 + 要求产物，按 id 去重（主责优先保留路径）
            const merged = new Map<string, CheckItem>();
            for (const it of fromReq) merged.set(it.id, it);
            for (const it of owned) merged.set(it.id, it);
            const req = [...merged.values()];

            return (
              <li
                key={t.id}
                data-testid={`delivery-checklist-node-${t.id}`}
                style={{
                  fontSize: 13,
                  marginBottom: 12,
                  paddingBottom: 10,
                  borderBottom: '1px solid var(--ui-stroke-muted)',
                }}
              >
                <div
                  style={{
                    display: 'flex',
                    gap: 8,
                    alignItems: 'baseline',
                    flexWrap: 'wrap',
                  }}
                >
                  <strong>{taskBadge(t.status)}</strong>
                  <span style={{ fontWeight: 600 }}>
                    #{t.id} {t.name}
                  </span>
                  <span
                    style={{ fontSize: 12, color: 'var(--ui-text-muted)' }}
                  >
                    {t.status}
                  </span>
                </div>
                {req.length === 0 ? (
                  <div
                    style={{
                      fontSize: 12,
                      color: 'var(--ui-text-muted)',
                      marginTop: 4,
                      paddingLeft: 20,
                    }}
                  >
                    本节点无清单产物要求
                  </div>
                ) : (
                  <ul
                    style={{
                      margin: '6px 0 0',
                      padding: '0 0 0 20px',
                      listStyle: 'none',
                    }}
                  >
                    {req.map((it) => (
                      <DelivRow key={it.id} it={it} />
                    ))}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>

        {unassigned.length > 0 && (
          <div
            data-testid="delivery-checklist-unassigned"
            style={{ marginTop: 8 }}
          >
            <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 6 }}>
              未分配主责
            </div>
            <ul style={{ margin: 0, padding: 0, listStyle: 'none' }}>
              {unassigned.map((it) => (
                <DelivRow key={`u-${it.id}`} it={it} />
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}
