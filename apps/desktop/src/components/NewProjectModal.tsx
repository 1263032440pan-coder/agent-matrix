import { useState } from 'react';
import { createProject, uploadMethodAsset } from '../api';
import { pickWorkspaceDirectory } from '../pickWorkspaceDir';

/**
 * SYS-176: 新建弹窗只做「命名 + 选工作区文件夹 + 传材料 + 创建/入库」。
 * 长耗时「生成计划」交给 App 后台跑，不锁死整页 UI（便于看思维链）。
 */
export function NewProjectModal({
  open,
  onClose,
  onReadyToPlan,
}: {
  open: boolean;
  onClose: () => void;
  /** 项目已创建且材料已上传 → 立刻切工作台并后台规划 */
  onReadyToPlan: (projectId: number) => void;
}) {
  const [name, setName] = useState('');
  const [workspacePath, setWorkspacePath] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [picking, setPicking] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  if (!open) return null;

  const canSubmit =
    name.trim().length > 0 &&
    workspacePath.trim().length > 0 &&
    files.length > 0 &&
    !busy;

  const onPickWorkspace = async () => {
    if (busy || picking) return;
    setPicking(true);
    setStatus(null);
    try {
      const path = await pickWorkspaceDirectory();
      if (path) {
        setWorkspacePath(path);
      } else if (!workspacePath) {
        setStatus('未选择文件夹（请在桌面应用内使用「选择文件夹」）');
      }
    } finally {
      setPicking(false);
    }
  };

  const onSubmit = async () => {
    if (!canSubmit) return;
    setBusy(true);
    setStatus('正在创建项目…');
    try {
      const p = await createProject(
        name.trim(),
        'FULL_AUTO',
        workspacePath.trim(),
      );
      setStatus(`项目 #${p.id} 已创建 · 正在上传材料…`);
      for (const f of files) {
        await uploadMethodAsset(p.id, f);
      }
      setName('');
      setWorkspacePath('');
      setFiles([]);
      setStatus(null);
      setBusy(false);
      onClose();
      onReadyToPlan(p.id);
    } catch (e) {
      setStatus(String(e));
      setBusy(false);
    }
  };

  return (
    <div
      className="modal-backdrop"
      data-testid="new-project-modal"
      onClick={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <div className="modal-panel" role="dialog" aria-modal>
        <div style={{ fontWeight: 600, marginBottom: 12 }}>新建项目</div>
        <input
          data-testid="new-project-name"
          placeholder="项目名称 / 目标"
          value={name}
          disabled={busy}
          onChange={(e) => setName(e.target.value)}
          style={{
            width: '100%',
            padding: '8px 10px',
            border: '1px solid var(--ui-stroke)',
            borderRadius: 4,
            marginBottom: 10,
            background: 'var(--ui-bg)',
          }}
        />
        <div
          style={{
            display: 'flex',
            gap: 8,
            alignItems: 'stretch',
            marginBottom: 6,
          }}
        >
          <div
            data-testid="new-project-workspace"
            title={workspacePath || undefined}
            style={{
              flex: 1,
              padding: '8px 10px',
              border: '1px solid var(--ui-stroke)',
              borderRadius: 4,
              background: 'var(--ui-bg)',
              fontFamily: 'ui-monospace, Consolas, monospace',
              fontSize: 12,
              color: workspacePath
                ? 'var(--ui-text)'
                : 'var(--ui-text-muted)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              lineHeight: '20px',
            }}
          >
            {workspacePath || '尚未选择工作区文件夹'}
          </div>
          <button
            type="button"
            className="ui-btn"
            data-testid="new-project-pick-workspace"
            disabled={busy || picking}
            onClick={onPickWorkspace}
          >
            {picking ? '选择中…' : '选择文件夹'}
          </button>
        </div>
        <div
          style={{
            fontSize: 11,
            color: 'var(--ui-text-muted)',
            marginBottom: 10,
          }}
        >
          产物写入所选文件夹；任务文件在其下 .agent-matrix/tasks/。须通过弹窗选择，不可手输。
        </div>
        <label
          className="ui-btn"
          style={{
            display: 'inline-block',
            marginBottom: 8,
            opacity: busy ? 0.5 : 1,
            pointerEvents: busy ? 'none' : 'auto',
          }}
        >
          选择材料 (.md / .txt)
          <input
            data-testid="new-project-files"
            type="file"
            accept=".md,.txt"
            multiple
            disabled={busy}
            style={{ display: 'none' }}
            onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
          />
        </label>
        <div
          style={{
            fontSize: 12,
            color: 'var(--ui-text-muted)',
            marginBottom: 12,
          }}
        >
          {files.length === 0
            ? '未传材料时不可提交；提交后弹窗关闭，规划在后台进行并可看思维链'
            : `已选 ${files.length} 个文件：${files.map((f) => f.name).join(', ')}`}
        </div>
        {status && (
          <div
            data-testid="plan-status"
            style={{ fontSize: 13, marginBottom: 8, color: 'var(--ui-accent)' }}
          >
            {busy ? '⏳ ' : ''}
            {status}
          </div>
        )}
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button
            type="button"
            className="ui-btn"
            disabled={busy}
            onClick={onClose}
          >
            取消
          </button>
          <button
            type="button"
            className="ui-btn ui-btn-primary"
            data-testid="new-project-submit"
            disabled={!canSubmit}
            onClick={onSubmit}
          >
            {busy ? '创建中…' : '创建并生成计划'}
          </button>
        </div>
      </div>
    </div>
  );
}
