import { useEffect, useState } from 'react';
import { fetchArtifactContent, type ArtifactContent } from '../api';

/** 产物内容模态预览：展示 markdown 全文 + 元数据（路径/版本/hash/大小）。 */
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
        onClick={(e) => e.stopPropagation()}
        style={{
          background: '#fff',
          borderRadius: 8,
          maxWidth: '80vw',
          width: 900,
          maxHeight: '85vh',
          display: 'flex',
          flexDirection: 'column',
          boxShadow: '0 8px 40px rgba(0,0,0,0.3)',
        }}
      >
        <div
          style={{
            padding: '12px 20px',
            borderBottom: '1px solid #eee',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            gap: 12,
          }}
        >
          <div>
            <strong>{data?.name ?? '加载中…'}</strong>
            {data && (
              <span style={{ marginLeft: 10, fontSize: 12, color: '#999' }}>
                v{data.version} · {(data.size_bytes / 1024).toFixed(1)}KB ·{' '}
                {data.sha256?.slice(0, 8)}… · {data.path}
              </span>
            )}
          </div>
          <button onClick={onClose} style={{ padding: '4px 12px' }}>
            关闭
          </button>
        </div>
        <div
          style={{
            padding: '8px 20px 20px',
            overflowY: 'auto',
            fontSize: 13,
            lineHeight: 1.7,
          }}
        >
          {error && <div style={{ color: '#f5222d' }}>{error}</div>}
          {!data && !error && <p style={{ color: '#999' }}>读取产物内容…</p>}
          {data && (
            <pre
              style={{
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                margin: 0,
                fontFamily: 'inherit',
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
