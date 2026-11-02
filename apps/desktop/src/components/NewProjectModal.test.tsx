import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { NewProjectModal } from './NewProjectModal';

vi.mock('../api', () => ({
  createProject: vi.fn().mockResolvedValue({ id: 9, name: 'demo' }),
  uploadMethodAsset: vi.fn().mockResolvedValue({ id: 1, content_hash: 'abc' }),
}));

vi.mock('../pickWorkspaceDir', () => ({
  pickWorkspaceDirectory: vi.fn().mockResolvedValue('G:\\work\\demo'),
}));

import { createProject, uploadMethodAsset } from '../api';
import { pickWorkspaceDirectory } from '../pickWorkspaceDir';

describe('NewProjectModal', () => {
  it('无材料或无工作区时提交禁用', async () => {
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

  it('选择文件夹后可提交，创建时带上绝对路径', async () => {
    const onReady = vi.fn();
    const onClose = vi.fn();
    const { rerender } = render(
      <NewProjectModal open onClose={onClose} onReadyToPlan={onReady} />,
    );
    await userEvent.type(screen.getByTestId('new-project-name'), 'demo');
    await userEvent.click(screen.getByTestId('new-project-pick-workspace'));
    await waitFor(() => {
      expect(pickWorkspaceDirectory).toHaveBeenCalled();
      expect(screen.getByTestId('new-project-workspace')).toHaveTextContent(
        'G:\\work\\demo',
      );
    });
    const file = new File(['# hello'], 'a.md', { type: 'text/markdown' });
    await userEvent.upload(screen.getByTestId('new-project-files'), file);
    await userEvent.click(screen.getByTestId('new-project-submit'));
    await waitFor(() => {
      expect(createProject).toHaveBeenCalledWith(
        'demo',
        'FULL_AUTO',
        'G:\\work\\demo',
      );
      expect(uploadMethodAsset).toHaveBeenCalled();
      expect(onReady).toHaveBeenCalledWith(9);
      expect(onClose).toHaveBeenCalled();
    });
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
