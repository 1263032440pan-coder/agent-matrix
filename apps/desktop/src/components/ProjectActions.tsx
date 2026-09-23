import { useState } from 'react';
import { createProject, executeProject, runPlanning, updateProjectMode, uploadMethodAsset } from '../api';

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
  const [message, setMessage] = useState<string | null>(null);

  const doCreate = async () => {
    if (!name.trim()) return;
    setBusy(true);
    try {
      const p = await createProject(name.trim());
      setMessage(`已创建项目 #${p.id}`);
      setName('');
      onCreated(p.id);
    } catch (e) {
      setMessage(String(e));
    } finally {
      setBusy(false);
    }
  };

  const doUpload = async (f: File | null) => {
    if (!f || projectId === null) return;
    setBusy(true);
    try {
      const r = await uploadMethodAsset(projectId, f);
      setMessage(`已导入方法资料 #${r.id} (hash ${r.content_hash.slice(0, 8)}…) — 点「生成计划」继续`);
      onImported();
    } catch (e) {
      setMessage(String(e));
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
      setMessage(`规划完成：${r.tasks} 个任务 / ${r.gaps} 个 Gap`);
      onImported(); // reuse refresh
    } catch (e) {
      setMessage(String(e));
    } finally {
      setBusy(false);
      onBusyChange?.(false);
    }
  };

  const doExecute = async () => {
    if (projectId === null) return;
    setBusy(true);
    onBusyChange?.(true);
    setMessage('执行中（LLM 调用可能需要几十秒）…');
    try {
      const r = await executeProject(projectId);
      setMessage(
        r.dispatched > 0
          ? `执行完成：${r.dispatched} 个任务已调度`
          : r.hint || '没有可调度的任务'
      );
      onImported();
    } catch (e) {
      setMessage(String(e));
    } finally {
      setBusy(false);
      onBusyChange?.(false);
    }
  };

  const doToggleMode = async () => {
    if (projectId === null) return;
    const next = currentMode === 'FULL_AUTO' ? 'MANUAL' : 'FULL_AUTO';
    setBusy(true);
    try {
      const r = await updateProjectMode(projectId, next);
      setMessage(`模式已切换为 ${r.automation_mode}`);
      onImported();
    } catch (e) {
      setMessage(String(e));
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
      <label style={{ padding: '6px 14px', border: '1px solid #ccc', borderRadius: 4, cursor: 'pointer' }}>
        导入方法资料 (.md/.txt)
        <input
          type="file"
          accept=".md,.txt"
          style={{ display: 'none' }}
          onChange={(e) => doUpload(e.target.files?.[0] ?? null)}
          disabled={busy || projectId === null}
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
      {message && <span style={{ color: '#555', fontSize: 13 }}>{message}</span>}
    </div>
  );
}