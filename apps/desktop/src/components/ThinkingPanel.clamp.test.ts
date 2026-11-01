import { describe, expect, it } from 'vitest';
import { clampAnswerSegments } from './ThinkingPanel';

describe('clampAnswerSegments', () => {
  it('只保留近 50 行 answer，保留 tool/activity', () => {
    const lines = Array.from({ length: 60 }, (_, i) => `L${i}`);
    const segs = [
      { seq: 1, taskId: 1, kind: 'activity' as const, text: '等待模型' },
      { seq: 2, taskId: 1, kind: 'answer' as const, text: lines.join('\n') },
      { seq: 3, taskId: 1, kind: 'tool' as const, text: '⚙ write_file' },
    ];
    const out = clampAnswerSegments(segs, 50);
    expect(out.some((s) => s.kind === 'activity')).toBe(true);
    expect(out.some((s) => s.kind === 'tool')).toBe(true);
    const ans = out.find((s) => s.kind === 'answer')!;
    expect(ans.text.split('\n')).toHaveLength(50);
    expect(ans.text.startsWith('L10')).toBe(true);
    expect(ans.text.endsWith('L59')).toBe(true);
  });
});
