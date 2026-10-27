import { useEffect, useMemo, useRef, useState } from 'react';
import { fetchAgentThinking } from '../api';

/** 思维链流式面板（WP: CoT UI）
 * - 走轻量 /agent-thinking（勿轮询沉重 dashboard——会拖 SQLite + 拖慢 CoT 写入）
 * - LLM_THINKING_CHUNK 分段追加
 * - 折叠样式：思考过程弱化排版，不干扰主界面
 */
export function ThinkingPanel({ projectId }: { projectId: number }) {
  const [segments, setSegments] = useState<
    Array<{ seq: number; taskId: number | null; text: string }>
  >([]);
  const [open, setOpen] = useState(false);
  const afterIdRef = useRef(0);
  const seqRef = useRef(0);
  const boxRef = useRef<HTMLDivElement>(null);
  const lastTaskRef = useRef<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    afterIdRef.current = 0;
    const poll = async () => {
      try {
        const data = await fetchAgentThinking(projectId, afterIdRef.current);
        if (cancelled || data.events.length === 0) {
          if (!cancelled && data.last_event_id > afterIdRef.current) {
            afterIdRef.current = data.last_event_id;
          }
          return;
        }
        // 任务切换：清空上一任务显示
        const firstTask = data.events[0]?.task_id ?? null;
        if (
          firstTask != null &&
          lastTaskRef.current != null &&
          firstTask !== lastTaskRef.current
        ) {
          setSegments([]);
          seqRef.current = 0;
        }
        if (firstTask != null) lastTaskRef.current = firstTask;

        setSegments((prev) => [
          ...prev,
          ...data.events.map((c) => ({
            seq: ++seqRef.current,
            taskId: c.task_id,
            text: c.text ?? '',
          })),
        ]);
        setOpen(true);
        afterIdRef.current = data.last_event_id;
      } catch {
        /* 轮询静默 */
      }
    };
    poll();
    const t = setInterval(poll, 1000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [projectId]);

  // 自动滚到底
  useEffect(() => {
    if (open && boxRef.current) {
      boxRef.current.scrollTop = boxRef.current.scrollHeight;
    }
  }, [segments, open]);

  const textLen = useMemo(
    () => segments.reduce((n, s) => n + s.text.length, 0),
    [segments],
  );

  if (segments.length === 0) return null;

  return (
    <section
      data-testid="thinking-panel"
      style={{
        background: '#fafafa',
        border: '1px solid #eee',
        borderRadius: 8,
        padding: 12,
        marginTop: 16,
        fontSize: 13,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          marginBottom: open ? 8 : 0,
        }}
      >
        <span style={{ fontSize: 15 }}>🧠</span>
        <strong style={{ color: '#666' }}>思维链（LLM 思考过程）</strong>
        <span style={{ color: '#999', fontSize: 12 }}>
          {segments.length} 段 · {textLen} 字
        </span>
        <button
          onClick={() => setOpen((v) => !v)}
          style={{ marginLeft: 'auto', fontSize: 12, padding: '2px 10px' }}
        >
          {open ? '收起' : '展开'}
        </button>
        <button
          onClick={() => setSegments([])}
          style={{ fontSize: 12, padding: '2px 10px' }}
          title="手动清空"
        >
          清空
        </button>
      </div>
      {open && (
        <div
          ref={boxRef}
          style={{
            maxHeight: 260,
            overflowY: 'auto',
            background: '#fff',
            border: '1px solid #f0f0f0',
            borderRadius: 6,
            padding: 10,
          }}
        >
          {segments.map((s) => (
            <p
              key={s.seq}
              style={{
                margin: '0 0 8px 0',
                color: '#555',
                lineHeight: 1.6,
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                paddingLeft: 8,
                borderLeft: '2px solid #e8d8f0',
              }}
            >
              {s.text}
            </p>
          ))}
        </div>
      )}
    </section>
  );
}
