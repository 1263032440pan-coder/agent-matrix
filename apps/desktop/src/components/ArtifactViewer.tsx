import { useEffect, useState } from 'react';
import { fetchArtifactContent, type ArtifactContent } from '../api';

/** 产物内容模态预览：展示全文 + 元数据（路径/版本/hash/大小）。 */
export function ArtifactViewer({
  artifactId,
  onClose,
}: {
  artifactId: number;
  onClose: () => void;
}) {
  const [data, setData] = useState<ArtifactContent | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchArtifactContent(artifactId)
      .then(setData)
      .catch((e) => setError(String(e)));
  }, [artifactId]);

  return (
    <div
      data-testid="artifact-viewer"
      onClick={onClose}
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.45)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
        padding: 24,
      }}
    >
      <div
        data-testid="artifact-panel"
        onClick={(e) => e.stopPropagation()}
        style={{
          background: 'var(--ui-bg-elevated)',
          color: 'var(--ui-text)',
          border: '1px solid var(--ui-stroke)',
          borderRadius: 8,
          maxWidth: '80vw',
          width: 900,
          maxHeight: '85vh',
          display: 'flex',
          flexDirection: 'column',
          minHeight: 0,
          boxShadow: '0 8px 40px rgba(0,0,0,0.3)',
        }}
      >
        <div
          style={{
            padding: '12px 20px',
            borderBottom: '1px solid var(--ui-stroke)',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            gap: 12,
            flexShrink: 0,
          }}
        >
          <div style={{ minWidth: 0 }}>
            <strong>{data?.name ?? '加载中…'}</strong>
            {data && (
              <span
                style={{
                  marginLeft: 10,
                  fontSize: 12,
                  color: 'var(--ui-text-muted)',
                }}
              >
                v{data.version} · {(data.size_bytes / 1024).toFixed(1)}KB ·{' '}
                {data.sha256?.slice(0, 8)}… · {data.path}
              </span>
            )}
          </div>
          <button
            type="button"
            className="ui-btn"
            onClick={onClose}
            style={{ flexShrink: 0 }}
          >
            关闭
          </button>
        </div>
        <div
          data-testid="artifact-body"
          style={{
            flex: 1,
            minHeight: 0,
            padding: '12px 20px 20px',
            overflowY: 'auto',
            overflowX: 'auto',
            fontSize: 13,
            lineHeight: 1.7,
            color: 'var(--ui-text)',
          }}
        >
          {error && (
            <div style={{ color: 'var(--ui-danger, #f5222d)' }}>{error}</div>
          )}
          {!data && !error && (
            <p style={{ color: 'var(--ui-text-muted)' }}>读取产物内容…</p>
          )}
          {data && (
            <pre
              data-testid="artifact-pre"
              style={{
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                margin: 0,
                fontFamily:
                  'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
                color: 'var(--ui-text)',
                background: 'transparent',
              }}
            >
              {data.content}
            </pre>
          )}
        </div>
      </div>
    </div>
  );
}
