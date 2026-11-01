import { useEffect, useState } from 'react';
import {
  deleteSkill,
  importSkillPackage,
  listSkills,
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
 * Skill 管理面板（用户决策 2026-10-09）:
 * 查询（元数据关键词/类型）+ 安装（导入）+ 删除。
 * 不再有"绑定"入口——安装的 skill 由程序自动选用
 * （显式绑定优先 → LLM 按任务内容自动选）。
 */
export function SkillPanel() {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [q, setQ] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [busy, setBusy] = useState(false);

  const load = (query = { q, skill_type: typeFilter }) => {
    listSkills(
      query.q || query.skill_type
        ? { q: query.q || undefined, skill_type: query.skill_type || undefined }
        : undefined
    )
      .then((s) => {
        setSkills(s);
        setError(null); // 恢复后清除上次轮询的瞬时错误（重启窗口期残留）
      })
      .catch((e) => setError(String(e)));
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

  const doImport = async (f: File | null) => {
    if (!f) return;
    setBusy(true);
    try {
      const sk = await importSkillPackage(f);
      setMessage(
        `Skill 已导入: ${sk.name}@${sk.version}（${sk.skill_id}，状态 ${sk.status}）`,
      );
      load();
    } catch (e) {
      setMessage(String(e));
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

  return (
    <section data-testid="skill-panel" style={card}>
      <h3 style={{ marginTop: 0 }}>Skills（已安装 {skills.length} 个）</h3>
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

      <div style={{ marginBottom: 12 }}>
        <label className="ui-btn" style={{ display: 'inline-block' }}>
          导入 Skill 包 (.zip)
          <input
            data-testid="skill-import"
            type="file"
            accept=".zip"
            style={{ display: 'none' }}
            disabled={busy}
            onChange={(e) => doImport(e.target.files?.[0] ?? null)}
          />
        </label>
      </div>

      {/* 查询条: 关键词 + 类型 */}
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
