import { beforeEach, describe, expect, it, vi } from 'vitest';

const toastError = vi.fn();

vi.mock('antd', () => ({
  message: { error: (...args: unknown[]) => toastError(...args) },
}));

vi.mock('./api', async () => {
  const actual = await vi.importActual<typeof import('./api')>('./api');
  return {
    ...actual,
    runPlanning: vi.fn(),
    executeProject: vi.fn(),
  };
});

import { PlanningError, runPlanning } from './api';
import { notifyPlanningError, planProject } from './projectOps';

describe('projectOps planning toast', () => {
  beforeEach(() => {
    toastError.mockReset();
    vi.mocked(runPlanning).mockReset();
  });

  it('DELIVERY_MANIFEST_INVALID → toast', async () => {
    const err = new PlanningError(
      '交付清单 JSON 校验失败：match 含未知字段 foo',
      'DELIVERY_MANIFEST_INVALID',
    );
    expect(notifyPlanningError(err)).toContain('交付清单');
    expect(toastError).toHaveBeenCalledWith(
      '交付清单 JSON 校验失败：match 含未知字段 foo',
      8,
    );
  });

  it('DELIVERY_ASSIGNMENT_INVALID → toast', () => {
    notifyPlanningError(new PlanningError('认领失败', 'DELIVERY_ASSIGNMENT_INVALID'));
    expect(toastError).toHaveBeenCalledWith('认领失败', 8);
  });

  it('PLAN_DECOMPOSE_INVALID → toast', () => {
    notifyPlanningError(
      new PlanningError('过载拆分失败', 'PLAN_DECOMPOSE_INVALID'),
    );
    expect(toastError).toHaveBeenCalledWith('过载拆分失败', 8);
  });

  it('DELIVERY_VALIDATOR_INVALID → toast', () => {
    notifyPlanningError(
      new PlanningError(
        '交付校验脚本生成失败：差分冒烟失败',
        'DELIVERY_VALIDATOR_INVALID',
      ),
    );
    expect(toastError).toHaveBeenCalledWith(
      '交付校验脚本生成失败：差分冒烟失败',
      8,
    );
  });

  it('普通规划错误不弹 toast', () => {
    const msg = notifyPlanningError(
      new PlanningError('Planning failed: 502'),
    );
    expect(msg).toBe('Planning failed: 502');
    expect(toastError).not.toHaveBeenCalled();
  });

  it('planProject success message', async () => {
    vi.mocked(runPlanning).mockResolvedValue({
      model_id: 1,
      tasks: 3,
      gaps: 1,
    });
    const r = await planProject(1);
    expect(r.ok).toBe(true);
    expect(r.message).toContain('3 任务');
  });
});
