import { useEffect, useState } from 'react';
import { bindSkillToProject, listSkills, type SkillInfo } from '../api';

const card: React.CSSProperties = {
  background: '#fff',
  border: '1px solid #e5e7eb',
  borderRadius: 8,
  padding: 16,
  marginBottom: 16,
};

export function SkillPanel({ projectId }: { projectId: number | null }) {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [scopeKey, setScopeKey] = useState('');

  const load = () => {
    listSkills()
      .then((s) => setSkills(s))
      .catch((e) => setError(String(e)));
  };

  useEffect(() => {
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, []);

  const doBind = async (sk: SkillInfo) => {
    if (projectId === null) return;
    try {
      await bindSkillToProject(projectId, sk.skill_id, sk.version, scopeKey);
      setMessage(
        `已绑定 ${sk.skill_id}@${sk.version} 到项目 ${projectId}` +
          (scopeKey ? `（任务类型: ${scopeKey}）` : '（通用）')
      );
    } catch (e) {
      setMessage(String(e));
    }
  };

  return (
    <section data-testid="skill-panel" style={card}>
      <h3 style={{ marginTop: 0 }}>
        Skill 包（已注册 {skills.length} 个）
      </h3>
      {error && <div style={{ color: '#f5222d' }}>{error}</div>}
      {skills.length === 0 && !error && (
        <p style={{ color: '#999' }}>
          暂无 Skill——点上方「导入 Skill 包 (.zip)」上传你的方法流程（SKILL.md + references 打包为 ZIP）
        </p>
      )}
      {skills.length > 0 && (
        <>
          <div style={{ marginBottom: 8, display: 'flex', gap: 8, alignItems: 'center' }}>
            <span style={{ fontSize: 13, color: '#666' }}>绑定任务类型（可空）:</span>
            <input
              value={scopeKey}
              onChange={(e) => setScopeKey(e.target.value)}
              placeholder="如: 需求 / 测试 / 设计"
              style={{ padding: '4px 8px', border: '1px solid #ccc', borderRadius: 4, width: 160 }}
            />
            <span style={{ fontSize: 12, color: '#999' }}>
              {projectId === null ? '（先选择项目才能绑定）' : `将绑定到项目 #${projectId}`}
            </span>
          </div>
          <ul style={{ paddingLeft: 18, margin: 0 }}>
            {skills.map((sk) => (
              <li key={`${sk.skill_id}@${sk.version}`} style={{ marginBottom: 6 }}>
                <strong>{sk.name}</strong>
                <code style={{ color: '#722ed1', marginLeft: 8 }}>
                  {sk.skill_id}@{sk.version}
                </code>
                <span style={{ marginLeft: 8, fontSize: 12, color: '#999' }}>
                  {sk.skill_type} · {sk.status} · hash {sk.content_hash}
                </span>
                {sk.description && (
                  <div style={{ fontSize: 12, color: '#666' }}>{sk.description}</div>
                )}
                <button
                  onClick={() => doBind(sk)}
                  disabled={projectId === null}
                  style={{ marginLeft: 8, padding: '2px 10px', fontSize: 12 }}
                >
                  绑定到当前项目
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
      {message && <div style={{ marginTop: 8, fontSize: 13, color: '#52c41a' }}>{message}</div>}
    </section>
  );
}
