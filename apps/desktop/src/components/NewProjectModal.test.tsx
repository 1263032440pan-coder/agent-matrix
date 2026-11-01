import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { NewProjectModal } from './NewProjectModal';

vi.mock('../api', () => ({
  createProject: vi.fn().mockResolvedValue({ id: 9, name: 'demo' }),
  uploadMethodAsset: vi.fn().mockResolvedValue({ id: 1, content_hash: 'abc' }),
}));

import { createProject, uploadMethodAsset } from '../api';

describe('NewProjectModal', () => {
  it('无材料时提交禁用', async () => {
    render(
      <NewProjectModal
        open
        onClose={() => undefined}
        onReadyToPlan={() => undefined}
      />,
    );
    await userEvent.type(screen.getByTestId('new-project-name'), 'demo');
    expect(screen.getByTestId('new-project-submit')).toBeDisabled();
  });

  it('创建上传后关闭弹窗并回调 onReadyToPlan（不在弹窗内等规划）', async () => {
    const onReady = vi.fn();
    const onClose = vi.fn();
    const { rerender } = render(
      <NewProjectModal open onClose={onClose} onReadyToPlan={onReady} />,
    );
    await userEvent.type(screen.getByTestId('new-project-name'), 'demo');
    const file = new File(['# hello'], 'a.md', { type: 'text/markdown' });
    await userEvent.upload(screen.getByTestId('new-project-files'), file);
    await userEvent.click(screen.getByTestId('new-project-submit'));
    await waitFor(() => {
      expect(createProject).toHaveBeenCalled();
      expect(uploadMethodAsset).toHaveBeenCalled();
      expect(onReady).toHaveBeenCalledWith(9);
      expect(onClose).toHaveBeenCalled();
    });
    // 弹窗应可关闭；不调用 runPlanning
    rerender(
      <NewProjectModal
        open={false}
        onClose={onClose}
        onReadyToPlan={onReady}
      />,
    );
    expect(screen.queryByTestId('new-project-modal')).toBeNull();
  });
});
