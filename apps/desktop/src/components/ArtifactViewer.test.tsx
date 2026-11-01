import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ArtifactViewer } from './ArtifactViewer';

vi.mock('../api', () => ({
  fetchArtifactContent: vi.fn().mockResolvedValue({
    id: 1,
    name: 'worker.py',
    version: 1,
    size_bytes: 1200,
    sha256: 'be88fcfbdeadbeef',
    path: 'G:/tmp/worker.py',
    content: 'class Worker:\n    pass\n' + 'x\n'.repeat(80),
  }),
}));

describe('ArtifactViewer', () => {
  it('正文用主题前景色，内容区可纵向滚动', async () => {
    render(<ArtifactViewer artifactId={1} onClose={() => undefined} />);
    expect(await screen.findByTestId('artifact-viewer')).toBeTruthy();
    await waitFor(() => {
      expect(screen.getByTestId('artifact-body')).toHaveTextContent('class Worker');
    });
    const body = screen.getByTestId('artifact-body');
    expect(body.style.overflowY).toBe('auto');
    expect(body.style.minHeight).toBe('0px');
    expect(body.style.flex).toMatch(/1/);
    const pre = screen.getByTestId('artifact-pre');
    expect(pre.style.color).toBe('var(--ui-text)');
    expect(screen.getByTestId('artifact-panel').style.background).toBe(
      'var(--ui-bg-elevated)',
    );
  });
});
