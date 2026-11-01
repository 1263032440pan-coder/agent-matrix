import { message as antdMessage } from 'antd';
import {
  executeProject,
  PlanningError,
  runPlanning,
} from './api';

const PLAN_TOAST_CODES = new Set([
  'DELIVERY_MANIFEST_INVALID',
  'DELIVERY_VALIDATOR_INVALID',
  'DELIVERY_ASSIGNMENT_INVALID',
  'PLAN_DECOMPOSE_INVALID',
]);

/** SYS-176 / 规划错误：指定 error_code 弹 antd toast，返回可读文案 */
export function notifyPlanningError(e: unknown): string {
  const errMsg =
    e instanceof PlanningError
      ? e.message
      : e instanceof Error
        ? e.message
        : String(e);
  if (
    e instanceof PlanningError &&
    e.errorCode &&
    PLAN_TOAST_CODES.has(e.errorCode)
  ) {
    antdMessage.error(errMsg, 8);
  }
  return errMsg;
}

export async function planProject(
  projectId: number,
  signal?: AbortSignal,
): Promise<{ ok: boolean; message: string }> {
  try {
    const r = await runPlanning(projectId, signal);
    return {
      ok: true,
      message: `规划完成：${r.tasks} 任务 / ${r.gaps} Gap`,
    };
  } catch (e) {
    return { ok: false, message: notifyPlanningError(e) };
  }
}

export async function runExecute(
  projectId: number,
): Promise<{ ok: boolean; message: string }> {
  try {
    const r = await executeProject(projectId);
    return {
      ok: true,
      message:
        r.executions.length > 0
          ? `已提交 ${r.executions.length} 个执行任务`
          : r.hint || '没有可执行的任务',
    };
  } catch (e) {
    return {
      ok: false,
      message: e instanceof Error ? e.message : String(e),
    };
  }
}
