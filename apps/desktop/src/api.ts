export interface DashboardData {
  project: {
    id: number;
    name: string;
    status: string;
    automation_mode: string;
  };
  tasks: Array<{
    id: number;
    name: string;
    status: string;
    depends_on?: number[];
    /** V1.6c: 该节点被要求的产物（计划清单子集 / tags / stage_outputs） */
    required_deliverables?: Array<{
      id: string | null;
      name: string;
      match?: Record<string, unknown> | null;
    }>;
    stage_outputs?: string[];
  }>;
  current_task: { id: number; name: string } | null;
  worker_status: { running: number; ready: number };
  // V1.6 序6: 交付画像 + 三件状态
  delivery_profile?: string | null;
  delivery_status?: { ppt: boolean; code: boolean; test_report: boolean };
  // V1.6d: 冻结校验脚本
  delivery_validator_hash?: string | null;
  delivery_validator_hash_prefix?: string | null;
  delivery_validator_generated_at?: string | null;
  plan_status?: string | null;
  delivery_assignment?: {
    manifest_count: number;
    assigned_count: number;
    uncovered_ids: string[];
    items: Array<{
      id: string;
      name?: string | null;
      owner_task_id: number | null;
      owner_task_name: string | null;
    }>;
  };
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
    payload?: {
      grace_text?: string;
      tool_rounds?: number;
      tool_calls?: number;
      tool_ok?: number;
      tools?: string[];
      ok?: boolean;
      problems?: string[];
      [key: string]: unknown;
    } | null;
  }>;
}

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://127.0.0.1:9100';

// 返修单 B: Agent 实时思维链事件（项目级流, 按 instance 过滤）
export interface AgentThinkingEvent {
  event_id: number;
  task_id: number | null;
  timestamp: string | null;
  text: string | null;
  agent_instance_id: number | null;
  agent_name: string | null;
}

export async function fetchAgentThinking(
  projectId: number,
  afterId = 0
): Promise<{
  events: AgentThinkingEvent[];
  last_event_id: number;
}> {
  const resp = await fetch(
    `${API_BASE}/api/projects/${projectId}/agent-thinking?after_id=${afterId}`
  );
  if (!resp.ok) {
    throw new Error(`Agent thinking fetch failed: ${resp.status}`);
  }
  return resp.json();
}

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

// V1.6 序6: 手动重新判定交付画像（P2-3 误判兜底）
export async function reclassifyDelivery(
  projectId: number
): Promise<{ project_id: number; delivery_profile: string }> {
  const resp = await fetch(
    `${API_BASE}/api/projects/${projectId}/reclassify-delivery`,
    { method: 'POST' }
  );
  if (!resp.ok) {
    throw new Error(`Reclassify failed: ${resp.status}`);
  }
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

export class PlanningError extends Error {
  errorCode?: string;
  constructor(message: string, errorCode?: string) {
    super(message);
    this.name = 'PlanningError';
    this.errorCode = errorCode;
  }
}

export async function runPlanning(
  projectId: number
): Promise<{ model_id: number; gaps: number; tasks: number }> {
  const resp = await fetch(`${API_BASE}/api/projects/${projectId}/plan`, {
    method: 'POST',
  });
  if (!resp.ok) {
    let detail: unknown = null;
    try {
      detail = await resp.json();
    } catch {
      /* ignore */
    }
    const body =
      detail && typeof detail === 'object' && 'detail' in detail
        ? (detail as { detail: unknown }).detail
        : detail;
    if (body && typeof body === 'object') {
      const code = (body as { error_code?: string }).error_code;
      if (
        code === 'DELIVERY_MANIFEST_INVALID' ||
        code === 'DELIVERY_VALIDATOR_INVALID' ||
        code === 'DELIVERY_ASSIGNMENT_INVALID'
      ) {
        const msg =
          (body as { message?: string }).message ||
          (code === 'DELIVERY_VALIDATOR_INVALID'
            ? '交付校验脚本生成失败'
            : code === 'DELIVERY_ASSIGNMENT_INVALID'
              ? '交付清单认领失败'
              : '交付清单 JSON 校验失败');
        throw new PlanningError(msg, code);
      }
    }
    const fallback =
      typeof body === 'string'
        ? body
        : body && typeof body === 'object' && 'message' in body
          ? String((body as { message: unknown }).message)
          : `Planning failed: ${resp.status}`;
    throw new PlanningError(fallback);
  }
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
  when_to_use: string | null;
  domain: string | null;
  task_types: string | null;
  status: string | null;
  content_hash: string;
  package_ref: string | null;
  created_at: string | null;
}

export interface SkillQuery {
  q?: string;
  skill_type?: string;
  status?: string;
}

export async function importSkillPackage(
  file: File,
  opts?: { allow_executable?: boolean },
): Promise<SkillInfo> {
  const form = new FormData();
  form.append('file', file);
  // ADR-039: 默认 false——执行型包显式放行
  form.append('allow_executable', opts?.allow_executable ? 'true' : 'false');
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

export async function listSkills(query?: SkillQuery): Promise<SkillInfo[]> {
  const params = new URLSearchParams();
  if (query?.q) params.set('q', query.q);
  if (query?.skill_type) params.set('skill_type', query.skill_type);
  if (query?.status) params.set('status', query.status);
  const qs = params.toString();
  const resp = await fetch(`${API_BASE}/api/skills${qs ? `?${qs}` : ''}`);
  if (!resp.ok) throw new Error(`Skills fetch failed: ${resp.status}`);
  return resp.json();
}

export async function deleteSkill(
  skillId: string,
  version: string
): Promise<Record<string, unknown>> {
  const resp = await fetch(
    `${API_BASE}/api/skills/${encodeURIComponent(skillId)}/${encodeURIComponent(version)}`,
    { method: 'DELETE' }
  );
  if (!resp.ok) {
    const detail = await resp.json().catch(() => '');
    throw new Error(`Skill 删除失败: ${detail?.detail ?? resp.status}`);
  }
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

// ── Artifact 内容预览 ─────────────────────────────────────────────

export interface ArtifactContent {
  id: number;
  task_id: number | null;
  name: string;
  version: number;
  path: string;
  sha256: string | null;
  content: string;
  size_bytes: number;
}

export async function fetchArtifactContent(
  artifactId: number
): Promise<ArtifactContent> {
  const resp = await fetch(`${API_BASE}/api/artifacts/${artifactId}/content`);
  if (!resp.ok) {
    const detail = await resp.json().catch(() => '');
    throw new Error(`产物读取失败: ${detail?.detail ?? resp.status}`);
  }
  return resp.json();
}

// ── WP-018: manual actions / checkpoints ─────────────────────────────

export async function confirmManualAction(actionId: number): Promise<{ status: string }> {
  const res = await fetch(`/api/projects/manual-actions/${actionId}/confirm`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error(`confirm failed: ${res.status}`);
  return res.json();
}

export async function rejectManualAction(actionId: number): Promise<{ status: string }> {
  const res = await fetch(`/api/projects/manual-actions/${actionId}/reject`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error(`reject failed: ${res.status}`);
  return res.json();
}

export async function createCheckpoint(projectId: number): Promise<{ checkpoint_id: number }> {
  const res = await fetch(`/api/projects/${projectId}/checkpoints`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error(`checkpoint failed: ${res.status}`);
  return res.json();
}

export async function listCheckpoints(
  projectId: number,
): Promise<Array<{ id: number; trigger: string; summary: string | null; created_at: string | null }>> {
  const res = await fetch(`/api/projects/${projectId}/checkpoints`);
  if (!res.ok) throw new Error(`list checkpoints failed: ${res.status}`);
  return res.json();
}

export async function restoreCheckpoint(
  checkpointId: number,
): Promise<{ restored_project_id: number }> {
  const res = await fetch(`/api/checkpoints/${checkpointId}/restore`, {
    method: 'POST',
  });
  if (!res.ok) throw new Error(`restore failed: ${res.status}`);
  return res.json();
}

// ── WP-018 补: 项目切换 / 历史项目 ────────────────────────────────────

export interface ProjectSummary {
  id: number;
  name: string;
  status: string;
  automation_mode: string;
  created_at: string | null;
  task_total?: number;
  task_accepted?: number;
}

export async function listProjectSummaries(): Promise<ProjectSummary[]> {
  const projects = await listProjects();
  // 任务统计并行拉取（dashboard 太重——用轻量统计端点）
  const detailed = await Promise.all(
    projects.map(async (p) => {
      try {
        const res = await fetch(`${API_BASE}/api/projects/${p.id}/summary`);
        if (!res.ok) throw new Error(String(res.status));
        return { ...p, ...(await res.json()) };
      } catch {
        return p;
      }
    }),
  );
  return detailed;
}

export async function deleteProject(projectId: number): Promise<{
  project_id: number;
  name: string;
  tables: Record<string, number>;
  disk_removed: string[];
}> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/projects/${projectId}`, {
      method: 'DELETE',
    });
  } catch (e) {
    throw new Error(
      `删除失败：无法连接 Runtime（${String(e)}）。请确认后端 :9100 已启动。`,
    );
  }
  if (!res.ok) {
    let detail = '';
    try {
      const body = await res.json();
      detail = body?.detail ? `: ${JSON.stringify(body.detail)}` : '';
    } catch {
      /* ignore */
    }
    throw new Error(`删除失败: ${res.status}${detail}`);
  }
  return res.json();
}

export async function batchDeleteProjects(
  projectIds: number[],
): Promise<{ deleted: unknown[] }> {
  const res = await fetch(`${API_BASE}/api/projects/batch-delete`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project_ids: projectIds }),
  });
  if (!res.ok) throw new Error(`批量删除失败: ${res.status}`);
  return res.json();
}


// ── 智能体管理面板（20号规范 §9——双视图+六态+上下文压力条）─────────

export interface AgentDefinitionInfo {
  id: number;
  agent_id: string;
  name: string;
  role_description: string | null;
  plugins: string[];
  skills: string[];
  stage_affinity: string[];
  source: string;
  scope: string;
  enabled: boolean;
}

export interface AgentInstanceInfo {
  id: number;
  definition_id: number;
  agent_id: string | null;
  name: string | null;
  project_id: number;
  status: string;
  current_task_id: number | null;
  fail_count: number;
  context_tokens: number;
  context_threshold: number;
  // V1.4c: 能力可见（该智能体可用的工具/技能/环节）
  plugins: string[];
  skills: string[];
  stage_affinity: string[];
}

export async function listProjectAgents(
  projectId: number
): Promise<AgentInstanceInfo[]> {
  const resp = await fetch(`${API_BASE}/api/projects/${projectId}/agents`);
  if (!resp.ok) throw new Error(`Agents fetch failed: ${resp.status}`);
  return resp.json();
}

export async function listAgentDefinitions(): Promise<AgentDefinitionInfo[]> {
  const resp = await fetch(`${API_BASE}/api/agent-definitions`);
  if (!resp.ok) throw new Error(`Agent definitions fetch failed: ${resp.status}`);
  return resp.json();
}

export async function agentLifecycle(
  projectId: number,
  instanceId: number,
  action: 'pause' | 'resume' | 'terminate'
): Promise<AgentInstanceInfo> {
  const resp = await fetch(
    `${API_BASE}/api/projects/${projectId}/agents/${instanceId}/${action}`,
    { method: 'POST' }
  );
  if (!resp.ok) throw new Error(`Agent ${action} failed: ${resp.status}`);
  return resp.json();
}
