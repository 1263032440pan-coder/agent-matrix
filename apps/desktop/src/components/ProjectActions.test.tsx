import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const toastError = vi.fn();

vi.mock('antd', () => ({
  message: { error: (...args: unknown[]) => toastError(...args) },
}));

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api');
  return {
    ...actual,
    runPlanning: vi.fn(),
    createProject: vi.fn(),
    executeProject: vi.fn(),
    importSkillPackage: vi.fn(),
    updateProjectMode: vi.fn(),
    uploadMethodAsset: vi.fn(),
  };
});

import { PlanningError, runPlanning } from '../api';
import { ProjectActions } from './ProjectActions';

describe('ProjectActions V1.6c toast', () => {
  beforeEach(() => {
    toastError.mockReset();
    vi.mocked(runPlanning).mockReset();
  });

  it('DELIVERY_MANIFEST_INVALID → antd message.error toast', async () => {
    vi.mocked(runPlanning).mockRejectedValue(
      new PlanningError(
        '交付清单 JSON 校验失败：match 含未知字段 foo',
        'DELIVERY_MANIFEST_INVALID',
      ),
    );
    render(
      <ProjectActions
        projectId={1}
        onCreated={() => undefined}
        onImported={() => undefined}
      />,
    );
    await userEvent.click(screen.getByText('生成计划'));
    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith(
        '交付清单 JSON 校验失败：match 含未知字段 foo',
        8,
      );
    });
    expect(screen.getByTestId('plan-status')).toHaveTextContent(
      '交付清单 JSON 校验失败：match 含未知字段 foo',
    );
  });

  it('普通规划错误不弹 DELIVERY toast', async () => {
    vi.mocked(runPlanning).mockRejectedValue(
      new PlanningError('Planning failed: 502'),
    );
    render(
      <ProjectActions
        projectId={1}
        onCreated={() => undefined}
        onImported={() => undefined}
      />,
    );
    await userEvent.click(screen.getByText('生成计划'));
    await waitFor(() => {
      expect(screen.getByTestId('plan-status')).toHaveTextContent(
        'Planning failed: 502',
      );
    });
    expect(toastError).not.toHaveBeenCalled();
  });
});
