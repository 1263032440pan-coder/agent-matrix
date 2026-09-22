export interface DashboardData {
  project: {
    id: number;
    name: string;
    status: string;
    automation_mode: string;
  };
  tasks: Array<{ id: number; name: string; status: string }>;
  current_task: { id: number; name: string } | null;
  worker_status: { running: number; ready: number };
  artifacts: Array<{
    id: number;
    name: string;
    version: number;
    sha256: string | null;
    task_id: number | null;
  }>;
  gate_results: Array<{ id: number; task_id: number; status: string }>;
  readiness: { id: number; version: number; coverage_summary: string | null } | null;
  gap_count: number;
}

const API_BASE = 'http://127.0.0.1:9100';

export async function fetchDashboard(projectId: number): Promise<DashboardData> {
  const resp = await fetch(`${API_BASE}/api/projects/${projectId}/dashboard`);
  if (!resp.ok) {
    throw new Error(`Dashboard fetch failed: ${resp.status}`);
  }
  return resp.json();
}

export async function listProjects(): Promise<
  Array<{ id: number; name: string; status: string }>
> {
  const resp = await fetch(`${API_BASE}/api/projects`);
  if (!resp.ok) {
    throw new Error(`Projects fetch failed: ${resp.status}`);
  }
  return resp.json();
}