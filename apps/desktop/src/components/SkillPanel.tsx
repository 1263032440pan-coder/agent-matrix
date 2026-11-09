import { useEffect, useState } from 'react';
import {
  deleteSkill,
  importSkillPackage,
  listFsInstalledSkills,
  listSkills,
  type FsInstalledSkill,
  type SkillInfo,
} from '../api';

const card: React.CSSProperties = {
  background: 'var(--ui-bg-elevated)',
  border: '1px solid var(--ui-stroke-muted)',
  borderRadius: 4,
  padding: 16,
  marginBottom: 0,
  textAlign: 'left',
};

const inputStyle: React.CSSProperties = {
  padding: '6px 10px',
  border: '1px solid var(--ui-stroke)',
  borderRadius: 4,
  fontSize: 13,
  background: 'var(--ui-bg)',
  color: 'var(--ui-text)',
};

/**
 * Skill 管理面板：查询 + 安装 + 删除；缺失文件提示。
 * 脚本执行使用工作区 .venv / 系统 Python，无独立 Skill 运行时。
 */
export function SkillPanel() {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [fsSkills, setFsSkills] = useState<FsInstalledSkill[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [q, setQ] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [busy, setBusy] = useState(false);
  /** ADR-039：执行型 = 允许 .py/.sh 落盘；默认勾选以免漏点 */
  const [allowExecutable, setAllowExecutable] = useState(true);

  const loadFs = () => {
    listFsInstalledSkills()
      .then(setFsSkills)
      .catch(() => setFsSkills([]));
  };

  const load = (query = { q, skill_type: typeFilter }) => {
    listSkills(
      query.q || query.skill_type
        ? { q: query.q || undefined, skill_type: query.skill_type || undefined }
        : undefined
    )
      .then((s) => {
        setSkills(s);
        setError(null);
      })
      .catch((e) => setError(String(e)));
    loadFs();
  };

  useEffect(() => {
    load();
    const t = setInterval(() => load(), 30000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const doSearch = () => {
    load();
  };

  /** ZIP 中央目录里文件名多为明文，粗扫是否含脚本 */
  const zipLikelyHasScripts = async (file: File): Promise<boolean> => {
    const buf = new Uint8Array(await file.arrayBuffer());
    const ascii = new TextDecoder('latin1').decode(buf);
    return /\.(py|sh)(\x00|"|'|\]|>|\s|$)/i.test(ascii);
  };

  const doImport = async (files: FileList | File[] | null) => {
    const list = files
      ? Array.from(files).filter((f) => f.name.toLowerCase().endsWith('.zip'))
      : [];
    if (list.length === 0) {
      setMessage('请选择至少一个 .zip 文件');
      return;
    }
    setBusy(true);
    const okNames: string[] = [];
    const errors: string[] = [];
    let exec = allowExecutable;
    try {
      for (let i = 0; i < list.length; i++) {
        const f = list[i];
        setMessage(`正在导入 ${i + 1}/${list.length}：${f.name}…`);
        try {
          if (!exec && (await zipLikelyHasScripts(f))) {
            exec = true;
            setAllowExecutable(true);
          }
          const result = await importSkillPackage(f, {
            allow_executable: exec,
          });
          if (result && typeof result === 'object' && 'installed' in result) {
            okNames.push(
              ...result.installed.map((s) => s.name || s.skill_id),
            );
          } else {
            okNames.push(result.name || result.skill_id);
          }
        } catch (e) {
          errors.push(`${f.name}: ${String(e)}`);
        }
      }
      const parts: string[] = [];
      if (okNames.length) {
        parts.push(
          `已安装 ${okNames.length} 个：${okNames.join(', ')}` +
            (exec ? '（允许脚本）' : ''),
        );
      }
      if (errors.length) {
        parts.push(`失败 ${errors.length} 个：${errors.join('；')}`);
      }
      setMessage(parts.join('\n') || '未导入任何 Skill');
      load();
    } finally {
      setBusy(false);
    }
  };

  const doDelete = async (sk: SkillInfo) => {
    if (
      !window.confirm(
        `确定删除 skill「${sk.name}」(${sk.skill_id}@${sk.version})？\n` +
          '将同时解除所有项目对它的引用，不可恢复。'
      )
    )
      return;
    setBusy(true);
    try {
      const r = await deleteSkill(sk.skill_id, sk.version);
      setMessage(
        `已删除 ${sk.skill_id}@${sk.version}` +
          (r.package_files_removed ? '（包文件已清理）' : '（包文件不在磁盘，仅清注册表）')
      );
      load();
    } catch (e) {
      setMessage(String(e));
    } finally {
      setBusy(false);
    }
  };

  const missingCount = skills.filter((s) => s.missing_file).length;

  return (
    <section data-testid="skill-panel" style={card}>
      <h3 style={{ marginTop: 0 }}>
        Skills（注册 {skills.length} · 磁盘 {fsSkills.length}）
      </h3>
      {error && (
        <div style={{ color: 'var(--ui-danger)' }} role="alert">
          {error}
        </div>
      )}
      {message && (
        <div
          style={{ color: 'var(--ui-accent)', marginBottom: 8 }}
          data-testid="skill-message"
        >
          {message}
        </div>
      )}

      <div style={{ marginBottom: 12, display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
        <label className="ui-btn" style={{ display: 'inline-block' }}>
          导入 Skill 包 (.zip，可多选)
          <input
            data-testid="skill-import"
            type="file"
            accept=".zip,application/zip"
            multiple
            style={{ display: 'none' }}
            disabled={busy}
            onChange={(e) => {
              void doImport(e.target.files);
              e.target.value = '';
            }}
          />
        </label>
        <label
          style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}
          title="ADR-039：允许 .py/.sh 落盘；上限 300 文件 / 100MB"
        >
          <input
            type="checkbox"
            data-testid="skill-allow-executable"
            checked={allowExecutable}
            disabled={busy}
            onChange={(e) => setAllowExecutable(e.target.checked)}
          />
          允许执行型（含脚本时勾选=信任并以本机权限运行）
        </label>
      </div>

      <div
        data-testid="skill-search"
        style={{
          display: 'flex',
          gap: 8,
          marginBottom: 12,
          alignItems: 'center',
          flexWrap: 'wrap',
        }}
      >
        <input
          data-testid="skill-search-input"
          style={{ ...inputStyle, flex: 1, minWidth: 160 }}
          placeholder="搜索: 名称 / 描述 / when_to_use / domain…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && doSearch()}
        />
        <select
          style={inputStyle}
          value={typeFilter}
          onChange={(e) => {
            setTypeFilter(e.target.value);
            setTimeout(() => load({ q, skill_type: e.target.value }), 0);
          }}
        >
          <option value="">全部类型</option>
          <option value="PROJECT_METHOD">PROJECT_METHOD</option>
          <option value="TASK_EXECUTION">TASK_EXECUTION</option>
          <option value="DOMAIN_KNOWLEDGE">DOMAIN_KNOWLEDGE</option>
          <option value="TOOL_USAGE">TOOL_USAGE</option>
        </select>
        <button style={inputStyle} onClick={doSearch}>
          查询
        </button>
      </div>

      {missingCount > 0 && (
        <p
          data-testid="skill-missing-banner"
          style={{ color: 'var(--ui-danger)', fontSize: 13 }}
        >
          {missingCount} 个注册项缺失磁盘文件（不进入智能体目录）
        </p>
      )}

      {fsSkills.length > 0 && (
        <div
          data-testid="skill-fs-list"
          style={{ marginBottom: 12, fontSize: 12, color: 'var(--ui-text-muted)' }}
        >
          磁盘目录：{fsSkills.map((e) => e.name).join(' · ')}
        </div>
      )}

      {skills.length === 0 && !error && (
        <p data-testid="skill-empty" style={{ color: 'var(--ui-text-muted)' }}>
          没有匹配的 Skill——点上方「导入 Skill 包 (.zip)」安装，或换关键词查询
        </p>
      )}

      {skills.length > 0 && (
        <div style={{ display: 'grid', gap: 8 }}>
          {skills.map((sk) => (
            <div
              key={`${sk.skill_id}@${sk.version}`}
              data-testid={`skill-item-${sk.skill_id}`}
              style={{
                border: '1px solid var(--ui-stroke-muted)',
                borderRadius: 4,
                padding: '8px 12px',
                display: 'flex',
                justifyContent: 'space-between',
                gap: 12,
                alignItems: 'flex-start',
                background: 'var(--ui-bg)',
                opacity: sk.missing_file ? 0.75 : 1,
              }}
            >
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ fontWeight: 600, fontSize: 14 }}>
                  {sk.name}
                  <span
                    style={{
                      color: 'var(--ui-text-muted)',
                      fontWeight: 400,
                      fontSize: 12,
                    }}
                  >
                    {' '}
                    {sk.skill_id}@{sk.version}
                  </span>
                  {sk.missing_file && (
                    <span
                      data-testid={`skill-missing-${sk.skill_id}`}
                      style={{
                        marginLeft: 8,
                        color: 'var(--ui-danger)',
                        fontWeight: 500,
                        fontSize: 12,
                      }}
                    >
                      缺失文件
                    </span>
                  )}
                </div>
                <div
                  style={{
                    color: 'var(--ui-text)',
                    fontSize: 13,
                    marginTop: 2,
                  }}
                >
                  {sk.description}
                </div>
                {sk.when_to_use && (
                  <div
                    style={{
                      color: 'var(--ui-text-muted)',
                      fontSize: 12,
                      marginTop: 2,
                    }}
                  >
                    何时用：{sk.when_to_use}
                  </div>
                )}
                <div
                  style={{
                    color: 'var(--ui-text-muted)',
                    fontSize: 12,
                    marginTop: 2,
                  }}
                >
                  {sk.skill_type} · {sk.status}
                  {sk.package_ref ? ` · ${sk.package_ref}` : ''}
                </div>
              </div>
              <button
                data-testid={`skill-delete-${sk.skill_id}`}
                className="ui-btn"
                style={{ color: 'var(--ui-danger)', flexShrink: 0 }}
                disabled={busy}
                onClick={() => doDelete(sk)}
              >
                删除
              </button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
