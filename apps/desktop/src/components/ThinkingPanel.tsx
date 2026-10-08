import { useEffect, useMemo, useRef, useState } from 'react';
import { fetchDashboard } from '../api';

/** 思维链流式面板（WP: CoT UI）
 * - LLM_THINKING_CHUNK 分段追加（事件 payload.text）
 * - 新任务（WORKER_STARTED）开始时清空上一任务显示
 * - 折叠样式：思考过程弱化排版，不干扰主界面
 */
export function ThinkingPanel({ projectId }: { projectId: number }) {
  const [segments, setSegments] = useState<
    Array<{ seq: number; taskId: number | null; text: string }>
  >([]);
  const [open, setOpen] = useState(false);
  const [seenEventId, setSeenEventId] = useState(0);
  const seqRef = useRef(0);
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const d = await fetchDashboard(projectId);
        if (cancelled) return;
        const events =
          (d as { runtime_events?: Array<Record<string, unknown>> })
            .runtime_events ?? [];
        // 新 Worker 开始（> 上次见过的）→ 清空面板（新任务清除旧显示）
        const fresh = events.filter(
          (e) => (e.event_id as number) > seenEventId,
        );
        if (fresh.length === 0) return;
        const workerStart = fresh.find(
          (e) => e.event_type === 'WORKER_STARTED',
        );
        let base = seenEventId;
        if (workerStart) {
          setSegments([]);
          seqRef.current = 0;
          base = (workerStart.event_id as number) - 1;
        }
        const chunks = fresh.filter(
          (e) =>
            e.event_type === 'LLM_THINKING_CHUNK' &&
            (e.event_id as number) > base,
        );
        if (chunks.length > 0) {
          setSegments((prev) => [
            ...prev,
            ...chunks.map((c) => ({
              seq: ++seqRef.current,
              taskId: (c.task_id as number) ?? null,
              text:
                ((c as { payload?: unknown }).payload as
                  | { text?: string }
                  | undefined)?.text ??
                (c.message as string) ??
                '',
            })),
          ]);
          setOpen(true);
        }
        const maxId = Math.max(
          ...events.map((e) => e.event_id as number),
          seenEventId,
        );
        setSeenEventId(maxId);
      } catch {
        /* 轮询静默 */
      }
    };
    poll();
    const t = setInterval(poll, 3000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [projectId, seenEventId]);

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
