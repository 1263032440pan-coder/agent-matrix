import { useEffect, useMemo, useRef, useState } from 'react';
import { fetchAgentThinking, type AgentThinkingEvent } from '../api';

type Kind = NonNullable<AgentThinkingEvent['kind']>;

type Seg = {
  seq: number;
  taskId: number | null;
  kind: Kind;
  text: string;
};

const KIND_PREFIX: Record<Kind, string> = {
  thinking: '',
  answer: '',
  tool: '',
  activity: '⏳ ',
};

const ANSWER_MAX_LINES = 50;

/** 仅保留近 maxLines 行 answer；其余 kind 原样保留，维持时间序 */
export function clampAnswerSegments(
  segs: Seg[],
  maxLines = ANSWER_MAX_LINES,
): Seg[] {
  let left = maxLines;
  const rev = [...segs].reverse();
  const kept: Seg[] = [];
  for (const s of rev) {
    if (s.kind !== 'answer') {
      kept.push(s);
      continue;
    }
    if (left <= 0) continue;
    const lines = s.text.split('\n');
    if (lines.length <= left) {
      kept.push(s);
      left -= lines.length;
    } else {
      kept.push({
        ...s,
        text: lines.slice(-left).join('\n'),
      });
      left = 0;
    }
  }
  return kept.reverse();
}

/** 运行动态面板：思考 / 正文 / 工具 / 活动 */
export function ThinkingPanel({
  projectId,
  embedded,
  phaseHint,
}: {
  projectId: number;
  embedded?: boolean;
  phaseHint?: string;
}) {
  const [segments, setSegments] = useState<Seg[]>([]);
  const [open, setOpen] = useState(false);
  const afterIdRef = useRef(0);
  const seqRef = useRef(0);
  const boxRef = useRef<HTMLDivElement>(null);
  const lastTaskRef = useRef<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    afterIdRef.current = 0;
    seqRef.current = 0;
    lastTaskRef.current = null;
    setSegments([]);
    const poll = async () => {
      try {
        const data = await fetchAgentThinking(projectId, afterIdRef.current);
        if (cancelled || data.events.length === 0) {
          if (!cancelled && data.last_event_id > afterIdRef.current) {
            afterIdRef.current = data.last_event_id;
          }
          return;
        }
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

        const ordered = [...data.events].sort(
          (a, b) => a.event_id - b.event_id,
        );
        setSegments((prev) =>
          clampAnswerSegments([
            ...prev,
            ...ordered.map((c) => ({
              seq: ++seqRef.current,
              taskId: c.task_id,
              kind: (c.kind ?? 'thinking') as Kind,
              text: c.text ?? '',
            })),
          ]),
        );
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

  useEffect(() => {
    if (open && boxRef.current) {
      boxRef.current.scrollTop = boxRef.current.scrollHeight;
    }
  }, [segments, open]);

  const textLen = useMemo(
    () => segments.reduce((n, s) => n + s.text.length, 0),
    [segments],
  );

  const showBody = embedded || open;

  if (!embedded && segments.length === 0) return null;

  return (
    <section
      data-testid="thinking-panel"
      style={{
        background: 'var(--ui-bg-elevated)',
        border: embedded ? 'none' : '1px solid var(--ui-stroke-muted)',
        borderRadius: embedded ? 0 : 4,
        padding: 12,
        marginTop: embedded ? 0 : 16,
        fontSize: 13,
        height: embedded ? '100%' : undefined,
        minHeight: 0,
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          marginBottom: showBody ? 8 : 0,
          flexShrink: 0,
        }}
      >
        <strong style={{ color: 'var(--ui-text-muted)' }}>运行动态</strong>
        <span style={{ color: 'var(--ui-text-muted)', fontSize: 12 }}>
          {segments.length} 段 · {textLen} 字 · 思考/工具/活动
        </span>
        {phaseHint ? (
          <span
            data-testid="thinking-phase-hint"
            style={{ fontSize: 12, color: 'var(--ui-accent)' }}
          >
            {phaseHint}
          </span>
        ) : null}
        {!embedded && (
          <button
            onClick={() => setOpen((v) => !v)}
            style={{ marginLeft: 'auto', fontSize: 12, padding: '2px 10px' }}
          >
            {open ? '收起' : '展开'}
          </button>
        )}
        <button
          onClick={() => setSegments([])}
          style={{
            marginLeft: embedded ? 'auto' : undefined,
            fontSize: 12,
            padding: '2px 10px',
          }}
          title="手动清空"
        >
          清空
        </button>
      </div>
      {showBody && (
        <div
          ref={boxRef}
          data-testid="thinking-body"
          style={{
            flex: embedded ? 1 : undefined,
            maxHeight: embedded ? undefined : 260,
            minHeight: 0,
            overflowY: 'auto',
            background: 'var(--ui-bg)',
            border: '1px solid var(--ui-stroke-muted)',
            borderRadius: 4,
            padding: 10,
          }}
        >
          {segments.length === 0 ? (
            <p style={{ margin: 0, color: 'var(--ui-text-muted)' }}>
              暂无运行动态
            </p>
          ) : (
            segments.map((s) => (
              <p
                key={s.seq}
                data-kind={s.kind}
                style={{
                  margin: '0 0 8px 0',
                  color:
                    s.kind === 'activity' || s.kind === 'tool'
                      ? 'var(--ui-text-muted)'
                      : 'var(--ui-text)',
                  lineHeight: 1.6,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  paddingLeft: 8,
                  borderLeft:
                    s.kind === 'tool'
                      ? '2px solid var(--ui-accent)'
                      : s.kind === 'activity'
                        ? '2px dashed var(--ui-stroke)'
                        : s.kind === 'answer'
                          ? '2px solid var(--ui-text-muted)'
                          : '2px solid var(--ui-stroke)',
                  fontFamily:
                    s.kind === 'tool'
                      ? 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace'
                      : undefined,
                  fontSize: s.kind === 'tool' || s.kind === 'activity' ? 12 : 13,
                }}
              >
                {KIND_PREFIX[s.kind]}
                {s.text}
              </p>
            ))
          )}
        </div>
      )}
    </section>
  );
}
