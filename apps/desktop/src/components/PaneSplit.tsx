import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from 'react';

type Axis = 'row' | 'column';

export function PaneSplit({
  axis,
  first,
  second,
  defaultRatio = 0.5,
  minFirst = 80,
  minSecond = 80,
  firstCollapsed = false,
  secondCollapsed = false,
  onRatioChange,
  storageKey,
  testId = 'pane-split',
}: {
  axis: Axis;
  first: ReactNode;
  second: ReactNode;
  defaultRatio?: number;
  minFirst?: number;
  minSecond?: number;
  firstCollapsed?: boolean;
  secondCollapsed?: boolean;
  onRatioChange?: (ratio: number) => void;
  storageKey?: string;
  testId?: string;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [ratio, setRatio] = useState(() => {
    if (storageKey) {
      try {
        const raw = localStorage.getItem(storageKey);
        if (raw) {
          const n = Number(raw);
          if (Number.isFinite(n) && n > 0.05 && n < 0.95) return n;
        }
      } catch {
        /* ignore */
      }
    }
    return defaultRatio;
  });
  const dragging = useRef(false);

  const persist = useCallback(
    (r: number) => {
      setRatio(r);
      onRatioChange?.(r);
      if (storageKey) {
        try {
          localStorage.setItem(storageKey, String(r));
        } catch {
          /* ignore */
        }
      }
    },
    [onRatioChange, storageKey],
  );

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!dragging.current || !rootRef.current) return;
      const rect = rootRef.current.getBoundingClientRect();
      const total = axis === 'row' ? rect.height : rect.width;
      if (total <= 0) return;
      const offset =
        axis === 'row' ? e.clientY - rect.top : e.clientX - rect.left;
      const minR = minFirst / total;
      const maxR = 1 - minSecond / total;
      const next = Math.min(maxR, Math.max(minR, offset / total));
      persist(next);
    };
    const onUp = () => {
      dragging.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, [axis, minFirst, minSecond, persist]);

  const bothOpen = !firstCollapsed && !secondCollapsed;
  const rootStyle: CSSProperties = {
    display: 'flex',
    flexDirection: axis === 'row' ? 'column' : 'row',
    flex: 1,
    minHeight: 0,
    minWidth: 0,
    overflow: 'hidden',
    height: '100%',
    width: '100%',
  };

  const firstStyle: CSSProperties = firstCollapsed
    ? { flex: '0 0 auto', minHeight: 0, minWidth: 0, overflow: 'hidden' }
    : secondCollapsed
      ? { flex: 1, minHeight: 0, minWidth: 0, overflow: 'hidden' }
      : {
          flex: `0 0 ${ratio * 100}%`,
          minHeight: 0,
          minWidth: 0,
          overflow: 'hidden',
        };

  const secondStyle: CSSProperties = secondCollapsed
    ? { flex: '0 0 auto', minHeight: 0, minWidth: 0, overflow: 'hidden' }
    : firstCollapsed
      ? { flex: 1, minHeight: 0, minWidth: 0, overflow: 'hidden' }
      : {
          flex: 1,
          minHeight: 0,
          minWidth: 0,
          overflow: 'hidden',
        };

  return (
    <div ref={rootRef} data-testid={testId} style={rootStyle}>
      <div style={firstStyle}>{first}</div>
      {bothOpen && (
        <div
          data-testid={`${testId}-handle`}
          role="separator"
          aria-orientation={axis === 'row' ? 'horizontal' : 'vertical'}
          onMouseDown={(e) => {
            e.preventDefault();
            dragging.current = true;
            document.body.style.cursor =
              axis === 'row' ? 'row-resize' : 'col-resize';
            document.body.style.userSelect = 'none';
          }}
          style={{
            flexShrink: 0,
            ...(axis === 'row'
              ? {
                  height: 6,
                  cursor: 'row-resize',
                  borderTop: '1px solid var(--ui-stroke)',
                  borderBottom: '1px solid var(--ui-stroke)',
                }
              : {
                  width: 6,
                  cursor: 'col-resize',
                  borderLeft: '1px solid var(--ui-stroke)',
                  borderRight: '1px solid var(--ui-stroke)',
                }),
            background: 'var(--ui-bg-chrome)',
          }}
        />
      )}
      <div style={secondStyle}>{second}</div>
    </div>
  );
}

export function SectionFold({
  title,
  open,
  onToggle,
  hint,
  children,
  headerExtra,
  testId,
}: {
  title: string;
  open: boolean;
  onToggle: () => void;
  hint?: string;
  children: ReactNode;
  headerExtra?: ReactNode;
  testId?: string;
}) {
  return (
    <div
      data-testid={testId}
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        minHeight: 0,
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '6px 10px',
          borderBottom: '1px solid var(--ui-stroke-muted)',
          flexShrink: 0,
          background: 'var(--ui-bg-elevated)',
        }}
      >
        <button
          type="button"
          data-testid={testId ? `${testId}-fold` : undefined}
          onClick={onToggle}
          style={{
            border: 'none',
            background: 'transparent',
            cursor: 'pointer',
            color: 'var(--ui-text)',
            fontWeight: 600,
            fontSize: 13,
            padding: 0,
          }}
        >
          {open ? '▾' : '▸'} {title}
        </button>
        {hint ? (
          <span style={{ fontSize: 12, color: 'var(--ui-text-muted)' }}>{hint}</span>
        ) : null}
        <div style={{ flex: 1 }} />
        {headerExtra}
      </div>
      {open ? (
        <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>{children}</div>
      ) : null}
    </div>
  );
}
