import { useCallback, useEffect, useState } from 'react';
import './App.css';
import { listProjects } from './api';
import { Dashboard } from './components/Dashboard';
import { ProjectActions } from './components/ProjectActions';

function App() {
  const [projectId, setProjectId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const reload = useCallback(() => {
    listProjects()
      .then((projects) => {
        setProjectId((current) =>
          current === null && projects.length > 0
            ? projects[projects.length - 1].id
            : current
        );
      })
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    reload();
  }, [reload, refreshKey]);

  return (
    <div style={{ maxWidth: 1200, margin: '0 auto' }}>
      <ProjectActions
        projectId={projectId}
        onCreated={(id) => {
          setProjectId(id);
          setRefreshKey((k) => k + 1);
        }}
        onImported={() => setRefreshKey((k) => k + 1)}
      />
      {error && <div role="alert">{error}</div>}
      {projectId === null && !error && <div>暂无项目 — 请先创建</div>}
      {projectId !== null && <Dashboard key={refreshKey} projectId={projectId} />}
    </div>
  );
}

export default App;