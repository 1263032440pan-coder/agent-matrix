import { useCallback, useEffect, useState } from 'react';
import './App.css';
import { listProjects } from './api';
import { Dashboard } from './components/Dashboard';
import { LLMSettingsCard } from './components/LLMSettingsCard';
import { ProjectActions } from './components/ProjectActions';
import { SkillPanel } from './components/SkillPanel';

function App() {
  const [projectId, setProjectId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [busy, setBusy] = useState(false);
  const [projectMode, setProjectMode] = useState<string | null>(null);

  const reload = useCallback(() => {
    listProjects()
      .then((projects) => {
        setProjectId((current) => {
          const id =
            current === null && projects.length > 0
              ? projects[projects.length - 1].id
              : current;
          const p = projects.find((x) => x.id === id);
          setProjectMode(p?.automation_mode ?? null);
          return id;
        });
      })
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    reload();
  }, [reload, refreshKey]);

  return (
    <div style={{ maxWidth: 1200, margin: '0 auto' }}>
      <LLMSettingsCard />
      <ProjectActions
        projectId={projectId}
        currentMode={projectMode}
        onCreated={(id) => {
          setProjectId(id);
          setRefreshKey((k) => k + 1);
        }}
        onImported={() => setRefreshKey((k) => k + 1)}
        onBusyChange={setBusy}
      />
      <SkillPanel projectId={projectId} />
      {error && <div role="alert">{error}</div>}
      {projectId === null && !error && <div>暂无项目 — 请先创建</div>}
      {projectId !== null && (
        <Dashboard key={refreshKey} projectId={projectId} paused={busy} />
      )}
    </div>
  );
}

export default App;