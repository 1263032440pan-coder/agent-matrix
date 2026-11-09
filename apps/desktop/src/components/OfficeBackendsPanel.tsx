import { useEffect, useState } from 'react';
import {
  getOfficeBackends,
  patchOfficeBackend,
  patchOfficeBackendGlobal,
  type OfficeBackendInfo,
  type OfficeBackendSettings,
} from '../api';

const sourceLabel: Record<string, string> = {
  builtin: '内置',
  plugin: '插件',
  other: '其他',
};

export function OfficeBackendsPanel() {
  const [data, setData] = useState<OfficeBackendSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pathDraft, setPathDraft] = useState('');

  const load = () => {
    getOfficeBackends()
      .then((d) => {
        setData(d);
        setPathDraft(d.officecli_path || '');
        setError(null);
      })
      .catch((e) => setError(String(e)));
  };

  useEffect(() => {
    load();
  }, []);

  const apply = async (fn: () => Promise<OfficeBackendSettings>) => {
    setBusy(true);
    try {
      const d = await fn();
      setData(d);
      setPathDraft(d.officecli_path || '');
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!data && !error) {
    return <p data-testid="office-backends-loading">加载中…</p>;
  }

  const backends: OfficeBackendInfo[] = data?.backends ?? [];

  return (
    <div data-testid="office-backends-panel">
      <h2 style={{ marginTop: 0 }}>办公产物后端</h2>
      <p style={{ color: 'var(--ui-text-muted)', fontSize: 13 }}>
        配置面：启停 / 优先级 / 回落策略。不改变项目状态；模型仍只见稳定工具门面。
      </p>

      {error && (
        <p style={{ color: 'var(--ui-danger, #c00)' }} data-testid="office-backends-error">
          {error}
        </p>
      )}

      <div style={{ marginTop: 16 }}>
        <div style={{ fontWeight: 600, marginBottom: 8, fontSize: 13 }}>
          回落策略
        </div>
        <div className="ui-segment" data-testid="office-fallback-segment">
          {(['auto', 'strict'] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              disabled={busy}
              data-active={data?.fallback === mode}
              data-testid={`office-fallback-${mode}`}
              onClick={() => apply(() => patchOfficeBackendGlobal({ fallback: mode }))}
            >
              {mode === 'auto' ? 'auto（失败试下一个）' : 'strict（首选失败即停）'}
            </button>
          ))}
        </div>
      </div>

      <div style={{ marginTop: 20 }}>
        <div style={{ fontWeight: 600, marginBottom: 8, fontSize: 13 }}>
          OfficeCLI 路径（用户自备）
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <input
            data-testid="officecli-path-input"
            value={pathDraft}
            onChange={(e) => setPathDraft(e.target.value)}
            placeholder="例如 C:\\Tools\\officecli.exe"
            style={{
              flex: 1,
              minWidth: 220,
              padding: '6px 10px',
              border: '1px solid var(--ui-stroke)',
              borderRadius: 4,
              background: 'var(--ui-bg)',
              color: 'var(--ui-text)',
              fontSize: 13,
            }}
          />
          <button
            type="button"
            disabled={busy}
            data-testid="officecli-path-save"
            onClick={() =>
              apply(() =>
                patchOfficeBackendGlobal({ officecli_path: pathDraft.trim() }),
              )
            }
          >
            保存路径
          </button>
        </div>
      </div>

      {data?.last_fallback && (
        <p
          style={{ marginTop: 16, fontSize: 12, color: 'var(--ui-text-muted)' }}
          data-testid="office-last-fallback"
        >
          最近回落：{data.last_fallback.from} → {data.last_fallback.to}
          {data.last_fallback.op ? `（${data.last_fallback.op}）` : ''}
          {data.last_fallback.at ? ` @ ${data.last_fallback.at}` : ''}
        </p>
      )}

      <table
        data-testid="office-backends-table"
        style={{
          width: '100%',
          marginTop: 20,
          borderCollapse: 'collapse',
          fontSize: 13,
        }}
      >
        <thead>
          <tr style={{ textAlign: 'left', borderBottom: '1px solid var(--ui-stroke-muted)' }}>
            <th style={{ padding: '6px 4px' }}>名称</th>
            <th>来源</th>
            <th>op</th>
            <th>优先级</th>
            <th>启用</th>
            <th>健康</th>
          </tr>
        </thead>
        <tbody>
          {backends.map((b) => (
            <tr
              key={b.name}
              data-testid={`office-backend-row-${b.name}`}
              style={{ borderBottom: '1px solid var(--ui-stroke-muted)' }}
            >
              <td style={{ padding: '8px 4px', fontFamily: 'ui-monospace, monospace' }}>
                {b.name}
              </td>
              <td>{sourceLabel[b.source] || b.source}</td>
              <td style={{ fontFamily: 'ui-monospace, monospace' }}>{b.op}</td>
              <td>
                <input
                  type="number"
                  data-testid={`office-priority-${b.name}`}
                  defaultValue={b.priority}
                  disabled={busy}
                  style={{ width: 72, padding: '2px 6px' }}
                  onBlur={(e) => {
                    const v = Number(e.target.value);
                    if (!Number.isFinite(v) || v === b.priority) return;
                    void apply(() => patchOfficeBackend(b.name, { priority: v }));
                  }}
                />
              </td>
              <td>
                <input
                  type="checkbox"
                  data-testid={`office-enabled-${b.name}`}
                  checked={b.enabled}
                  disabled={busy}
                  onChange={(e) =>
                    void apply(() =>
                      patchOfficeBackend(b.name, { enabled: e.target.checked }),
                    )
                  }
                />
              </td>
              <td data-testid={`office-health-${b.name}`}>
                {b.health ? '✓' : b.usable ? '·' : '✗'}
              </td>
            </tr>
          ))}
          {backends.length === 0 && (
            <tr>
              <td colSpan={6} style={{ padding: 12, color: 'var(--ui-text-muted)' }}>
                暂无注册后端（Worker 尚未加载办公模块时也会在此懒注册内置后端）
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
