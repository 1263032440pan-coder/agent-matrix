import { useState } from 'react';
import { message as antdMessage } from 'antd';
import {
  createProject,
  executeProject,
  importSkillPackage,
  PlanningError,
  runPlanning,
  updateProjectMode,
  uploadMethodAsset,
} from '../api';

export function ProjectActions({
  projectId,
  currentMode,
  onCreated,
  onImported,
  onBusyChange,
}: {
  projectId: number | null;
  currentMode?: string | null;
  onCreated: (id: number) => void;
  onImported: () => void;
  onBusyChange?: (busy: boolean) => void;
}) {
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [statusText, setStatusText] = useState<string | null>(null);

  const doCreate = async () => {
    if (!name.trim()) return;
    setBusy(true);
    try {
      const p = await createProject(name.trim());
      setStatusText(`已创建项目 #${p.id}`);
      setName('');
      onCreated(p.id);
    } catch (e) {
      setStatusText(String(e));
    } finally {
      setBusy(false);
    }
  };

  const doUpload = async (f: File | null) => {
    if (!f) return;
    if (projectId === null) {
      setStatusText('请先创建项目，再导入方法资料（资料归属于项目）');
      return;
    }
    setBusy(true);
    try {
      const r = await uploadMethodAsset(projectId, f);
      setStatusText(`已导入方法资料 #${r.id} (hash ${r.content_hash.slice(0, 8)}…) — 点「生成计划」继续`);
      onImported();
    } catch (e) {
      setStatusText(String(e));
    } finally {
      setBusy(false);
    }
  };

  const doPlan = async () => {
    if (projectId === null) return;
    setBusy(true);
    onBusyChange?.(true);
    try {
      const r = await runPlanning(projectId);
      setStatusText(`规划完成：${r.tasks} 个任务 / ${r.gaps} 个 Gap`);
      onImported(); // reuse refresh
    } catch (e) {
      const errMsg =
        e instanceof PlanningError
          ? e.message
          : e instanceof Error
            ? e.message
            : String(e);
      setStatusText(errMsg);
      if (
        e instanceof PlanningError &&
        (e.errorCode === 'DELIVERY_MANIFEST_INVALID' ||
          e.errorCode === 'DELIVERY_VALIDATOR_INVALID' ||
          e.errorCode === 'DELIVERY_ASSIGNMENT_INVALID')
      ) {
        antdMessage.error(errMsg, 8);
      }
    } finally {
      setBusy(false);
      onBusyChange?.(false);
    }
  };

  const doExecute = async () => {
    console.log('[doExecute] clicked, projectId=', projectId, 'busy=', busy);
    if (projectId === null) {
      setStatusText('未选择项目 — projectId 为空，请先创建或等待项目列表加载');
      return;
    }
    setBusy(true);
    onBusyChange?.(true);
    setStatusText('执行中（LLM 调用可能需要几十秒）…');
    try {
      const r = await executeProject(projectId);
      setStatusText(
        r.executions.length > 0
          ? `已提交 ${r.executions.length} 个执行任务（后台异步执行，轮询可见进度）` +
            (r.skipped_active ? `；${r.skipped_active} 个已在执行中跳过` : '')
          : r.hint || '没有可执行的任务'
      );
      onImported();
    } catch (e) {
      setStatusText(String(e));
    } finally {
      setBusy(false);
      onBusyChange?.(false);
    }
  };

  const doImportSkill = async (f: File | null) => {
    if (!f) return;
    setBusy(true);
    try {
      const sk = await importSkillPackage(f);
      setStatusText(
        `Skill 已导入: ${sk.name}@${sk.version}（${sk.skill_id}，状态 ${sk.status}）` +
          (projectId !== null ? ' — 可在 Skill 列表绑定到当前项目' : '')
      );
      onImported();
    } catch (e) {
      setStatusText(String(e));
    } finally {
      setBusy(false);
    }
  };

  const doToggleMode = async () => {
    if (projectId === null) return;
    const next = currentMode === 'FULL_AUTO' ? 'MANUAL' : 'FULL_AUTO';
    setBusy(true);
    try {
      const r = await updateProjectMode(projectId, next);
      setStatusText(`模式已切换为 ${r.automation_mode}`);
      onImported();
    } catch (e) {
      setStatusText(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      data-testid="project-actions"
      style={{
        background: '#fff',
        border: '1px solid #e5e7eb',
        borderRadius: 8,
        padding: 16,
        marginBottom: 16,
        display: 'flex',
        gap: 12,
        alignItems: 'center',
        flexWrap: 'wrap',
      }}
    >
      <input
        placeholder="输入项目目标 / 名称"
        value={name}
        onChange={(e) => setName(e.target.value)}
        style={{ padding: '6px 10px', border: '1px solid #ccc', borderRadius: 4, minWidth: 240 }}
      />
      <button
        onClick={doCreate}
        disabled={busy || !name.trim()}
        style={{ padding: '6px 14px' }}
      >
        创建项目
      </button>
      <label
        style={{
          padding: '6px 14px',
          border: `1px solid ${projectId === null ? '#ddd' : '#ccc'}`,
          borderRadius: 4,
          cursor: projectId === null ? 'not-allowed' : 'pointer',
          color: projectId === null ? '#bbb' : undefined,
        }}
        title={projectId === null ? '请先创建项目' : undefined}
      >
        导入方法资料 (.md/.txt){projectId === null ? '（需先建项目）' : ''}
        <input
          type="file"
          accept=".md,.txt"
          style={{ display: 'none' }}
          onChange={(e) => doUpload(e.target.files?.[0] ?? null)}
          disabled={busy || projectId === null}
        />
      </label>
      <label
        style={{ padding: '6px 14px', border: '1px solid #722ed1', borderRadius: 4, cursor: 'pointer', color: '#722ed1' }}
        title="导入声明式 Skill 包（SKILL.md + references，ZIP 格式）——你的方法流程将被注册并可在执行中采用"
      >
        导入 Skill 包 (.zip)
        <input
          type="file"
          accept=".zip"
          style={{ display: 'none' }}
          onChange={(e) => doImportSkill(e.target.files?.[0] ?? null)}
          disabled={busy}
        />
      </label>
      <button
        onClick={doPlan}
        disabled={busy || projectId === null}
        style={{ padding: '6px 14px' }}
      >
        生成计划
      </button>
      <button
        onClick={doExecute}
        disabled={busy || projectId === null}
        style={{ padding: '6px 14px' }}
      >
        执行项目
      </button>
      <button
        onClick={doToggleMode}
        disabled={busy || projectId === null || !currentMode}
        style={{ padding: '6px 14px' }}
        title={`当前：${currentMode ?? '未知'}`}
      >
        {currentMode === 'FULL_AUTO' ? '切换为 MANUAL' : '切换为 FULL_AUTO'}
      </button>
      {statusText && (
        <span data-testid="plan-status" style={{ color: '#555', fontSize: 13 }}>
          {statusText}
        </span>
      )}
      {busy && <span style={{ color: '#fa8c16', fontSize: 13 }}>⏳ 操作进行中（按钮禁用）…</span>}
    </div>
  );
}