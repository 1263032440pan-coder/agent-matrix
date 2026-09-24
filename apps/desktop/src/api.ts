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
  runtime_events?: Array<{
    event_id: number;
    event_type: string;
    timestamp: string | null;
    execution_id: number | null;
    task_id: number | null;
    phase: string | null;
    message: string | null;
  }>;
}

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://127.0.0.1:9100';

export interface RuntimeEvent {
  event_id: number;
  event_type: string;
  timestamp: string | null;
  execution_id: number | null;
  task_id: number | null;
  phase: string | null;
  message: string | null;
}

export async function fetchExecutionEvents(
  executionId: number,
  afterId = 0
): Promise<{ events: RuntimeEvent[]; last_event_id: number }> {
  const resp = await fetch(
    `${API_BASE}/api/executions/${executionId}/events?after_id=${afterId}`
  );
  if (!resp.ok) throw new Error(`Events fetch failed: ${resp.status}`);
  return resp.json();
}

export async function fetchDashboard(projectId: number): Promise<DashboardData> {
  const resp = await fetch(`${API_BASE}/api/projects/${projectId}/dashboard`);
  if (!resp.ok) {
    throw new Error(`Dashboard fetch failed: ${resp.status}`);
  }
  return resp.json();
}

export async function listProjects(): Promise<
  Array<{ id: number; name: string; status: string; automation_mode?: string }>
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

export async function updateProjectMode(
  projectId: number,
  automationMode: 'FULL_AUTO' | 'MANUAL'
): Promise<{ id: number; automation_mode: string }> {
  const resp = await fetch(`${API_BASE}/api/projects/${projectId}/mode`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ automation_mode: automationMode }),
  });
  if (!resp.ok) throw new Error(`Update mode failed: ${resp.status}`);
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

export async function runPlanning(
  projectId: number
): Promise<{ model_id: number; gaps: number; tasks: number }> {
  const resp = await fetch(`${API_BASE}/api/projects/${projectId}/plan`, {
    method: 'POST',
  });
  if (!resp.ok) throw new Error(`Planning failed: ${resp.status}`);
  return resp.json();
}

export async function executeProject(
  projectId: number
): Promise<{
  executions: number[];
  skipped_active: number;
  manual_pending: number;
  hint: string;
}> {
  const resp = await fetch(`${API_BASE}/api/projects/${projectId}/execute`, {
    method: 'POST',
  });
  if (!resp.ok) throw new Error(`Execute failed: ${resp.status}`);
  return resp.json();
}

export interface LLMConfig {
  base_url: string;
  model: string;
  api_key_set?: boolean;
  api_key_preview?: string;
}

export async function getLLMSettings(): Promise<LLMConfig> {
  const resp = await fetch(`${API_BASE}/api/settings/llm`);
  if (!resp.ok) throw new Error(`LLM settings fetch failed: ${resp.status}`);
  return resp.json();
}

export async function saveLLMSettings(cfg: {
  base_url: string;
  model: string;
  api_key?: string;
}): Promise<LLMConfig> {
  const resp = await fetch(`${API_BASE}/api/settings/llm`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ api_key: '', ...cfg }),
  });
  if (!resp.ok) throw new Error(`LLM settings save failed: ${resp.status}`);
  return resp.json();
}

export async function testLLMConnection(cfg: {
  base_url: string;
  model: string;
  api_key?: string;
}): Promise<{ ok: boolean; sample?: string; error?: string }> {
  const resp = await fetch(`${API_BASE}/api/settings/llm/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ api_key: '', ...cfg }),
  });
  if (!resp.ok) throw new Error(`LLM test failed: ${resp.status}`);
  return resp.json();
}
// ── Skill 包管理（D17B/D17C 用户入口）─────────────────────────────────

export interface SkillInfo {
  skill_id: string;
  name: string;
  version: string;
  skill_type: string | null;
  description: string | null;
  status: string | null;
  content_hash: string;
  package_ref: string | null;
  created_at: string | null;
}

export async function importSkillPackage(file: File): Promise<SkillInfo> {
  const form = new FormData();
  form.append('file', file);
  const resp = await fetch(`${API_BASE}/api/skills/import`, {
    method: 'POST',
    body: form,
  });
  if (!resp.ok) {
    const detail = await resp.json().catch(() => '');
    throw new Error(`Skill 导入失败: ${detail?.detail ?? resp.status}`);
  }
  return resp.json();
}

export async function listSkills(): Promise<SkillInfo[]> {
  const resp = await fetch(`${API_BASE}/api/skills`);
  if (!resp.ok) throw new Error(`Skills fetch failed: ${resp.status}`);
  return resp.json();
}

export async function bindSkillToProject(
  projectId: number,
  skillId: string,
  skillVersion: string,
  scopeKey = ''
): Promise<Record<string, unknown>> {
  const resp = await fetch(`${API_BASE}/api/skills/bindings/project/${projectId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      skill_id: skillId,
      skill_version: skillVersion,
      scope_key: scopeKey,
    }),
  });
  if (!resp.ok) {
    const detail = await resp.json().catch(() => '');
    throw new Error(`绑定失败: ${detail?.detail ?? resp.status}`);
  }
  return resp.json();
}
