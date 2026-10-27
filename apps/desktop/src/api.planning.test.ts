import { afterEach, describe, expect, it, vi } from 'vitest';
import { PlanningError, runPlanning } from './api';

describe('runPlanning DELIVERY_MANIFEST_INVALID', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('422 detail → PlanningError with errorCode', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        json: async () => ({
          detail: {
            ok: false,
            error_code: 'DELIVERY_MANIFEST_INVALID',
            message: '交付清单须含非空 deliverables 数组',
          },
        }),
      }),
    );
    await expect(runPlanning(9)).rejects.toMatchObject({
      name: 'PlanningError',
      message: '交付清单须含非空 deliverables 数组',
      errorCode: 'DELIVERY_MANIFEST_INVALID',
    });
    await expect(runPlanning(9)).rejects.toBeInstanceOf(PlanningError);
  });
});
