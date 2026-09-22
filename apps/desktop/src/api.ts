export interface DashboardData {
  project: {
    id: number;
    name: string;
    status: string;
    automation_mode: string;
  };
  tasks: Array<{ id: number; name: string; status: string; depends_on?: number[] }>;
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
  timeline: Array<{
    id: number;
    task_id: number;
    task_name: string;
    attempt: number;
    started_at: string | null;
    finished_at: string | null;
  }>;
}

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://127.0.0.1:9100';

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

export async function createProject(
  name: string,
  automationMode = 'FULL_AUTO'
): Promise<{ id: number; name: string }> {
  const resp = await fetch(`${API_BASE}/api/projects`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, automation_mode: automationMode }),
  });
  if (!resp.ok) throw new Error(`Create project failed: ${resp.status}`);
  return resp.json();
}

export async function uploadMethodAsset(
  projectId: number,
  file: File
): Promise<{ id: number; content_hash: string }> {
  const form = new FormData();
  form.append('file', file);
  const resp = await fetch(
    `${API_BASE}/api/projects/${projectId}/method-assets/upload`,
    { method: 'POST', body: form }
  );
  if (!resp.ok) throw new Error(`Upload failed: ${resp.status}`);
  return resp.json();
}