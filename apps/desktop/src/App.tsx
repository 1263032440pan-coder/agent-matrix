import { useEffect, useState } from 'react';
import './App.css';
import { listProjects } from './api';
import { Dashboard } from './components/Dashboard';

function App() {
  const [projectId, setProjectId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listProjects()
      .then((projects) => {
        if (projects.length > 0) setProjectId(projects[0].id);
        else setError('No projects yet');
      })
      .catch((e) => setError(String(e)));
  }, []);

  if (error) return <div role="alert">{error}</div>;
  if (projectId === null) return <div>Loading projects…</div>;
  return <Dashboard projectId={projectId} />;
}

export default App;