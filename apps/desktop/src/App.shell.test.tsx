import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import App from './App';
import { ThemeProvider } from './theme/ThemeProvider';

vi.mock('./api', () => ({
  listProjects: vi.fn(),
  listProjectSummaries: vi.fn(),
  deleteProject: vi.fn(),
  fetchDashboard: vi.fn(),
  listProjectAgents: vi.fn().mockResolvedValue([]),
  listAgentDefinitions: vi.fn().mockResolvedValue([]),
  fetchAgentThinking: vi
    .fn()
    .mockResolvedValue({ events: [], last_event_id: 0 }),
  getLLMSettings: vi.fn().mockResolvedValue({ base_url: '', model: '' }),
  listSkills: vi.fn().mockResolvedValue([]),
  createProject: vi.fn(),
  uploadMethodAsset: vi.fn(),
}));

vi.mock('./projectOps', () => ({
  planProject: vi.fn(),
  runExecute: vi.fn(),
}));

import {
  createProject,
  fetchAgentThinking,
  fetchDashboard,
  listProjects,
  listProjectSummaries,
  uploadMethodAsset,
} from './api';
import { planProject } from './projectOps';

const dash = {
  project: {
    id: 1,
    name: 'demo',
    status: 'ACTIVE',
    automation_mode: 'FULL_AUTO',
  },
  tasks: [{ id: 1, name: 't1', status: 'READY', depends_on: [] }],
  current_task: null,
  worker_status: { running: 0, ready: 1 },
  artifacts: [],
  gate_results: [],
  readiness: null,
  gap_count: 0,
  timeline: [],
  runtime_events: [
    {
      event_id: 1,
      event_type: 'TASK_STARTED',
      timestamp: '2026-10-31T10:00:00',
      execution_id: null,
      task_id: 1,
      phase: 'WORKER',
      message: 'go',
      payload: {},
    },
  ],
};

describe('App shell (SYS-162～177)', () => {
  beforeEach(() => {
    vi.mocked(listProjects).mockResolvedValue([
      { id: 1, name: 'demo', status: 'ACTIVE', automation_mode: 'FULL_AUTO' },
    ]);
    vi.mocked(listProjectSummaries).mockResolvedValue([
      {
        id: 1,
        name: 'demo',
        status: 'ACTIVE',
        automation_mode: 'FULL_AUTO',
        created_at: null,
        task_total: 2,
        task_accepted: 1,
      },
    ]);
    vi.mocked(fetchDashboard).mockResolvedValue(dash as never);
  });

  function renderApp() {
    return render(
      <ThemeProvider>
        <App />
      </ThemeProvider>,
    );
  }

  it('renders shell with FULL_AUTO and no Manual/Contract/Checkpoint', async () => {
    renderApp();
    expect(await screen.findByTestId('app-shell')).toBeTruthy();
    expect(screen.getByTestId('mode-badge')).toHaveTextContent('FULL_AUTO');
    expect(screen.queryByText(/切换为 MANUAL/)).toBeNull();
    expect(screen.queryByText(/Delivery Contract/i)).toBeNull();
    expect(screen.queryByText(/Checkpoint/i)).toBeNull();
    expect(screen.queryByTestId('timeline')).toBeNull();
  });

  it('click project opens overview with delivery checklist; back to workbench', async () => {
    vi.mocked(fetchDashboard).mockResolvedValue({
      ...dash,
      delivery_checklist: {
        updated_at: null,
        total: 1,
        matched: 0,
        items: [
          {
            id: 'd1',
            name: '方案PPT',
            match: null,
            owner_task_id: 1,
            owner_task_name: 't1',
            owner_task_status: 'READY',
            status: 'pending',
            matched_paths: [],
            hint: null,
          },
        ],
      },
    } as never);
    renderApp();
    await screen.findByTestId('project-row-1');
    await userEvent.click(screen.getByTestId('project-row-1'));
    expect(await screen.findByTestId('project-overview')).toBeTruthy();
    expect(screen.getByTestId('delivery-checklist')).toHaveTextContent(
      '流程节点',
    );
    expect(screen.getByTestId('delivery-checklist-node-1')).toBeTruthy();
    expect(screen.queryByTestId('runtime-events')).toBeNull();
    expect(screen.queryByTestId('delivery-quality')).toBeNull();
    await userEvent.click(screen.getByTestId('overview-back'));
    await waitFor(() => {
      expect(screen.getByTestId('dashboard-workbench')).toBeTruthy();
    });
  });

  it('settings opens appearance theme segment', async () => {
    renderApp();
    await userEvent.click(await screen.findByTestId('open-settings'));
    expect(await screen.findByTestId('settings-view')).toBeTruthy();
    expect(screen.getByTestId('theme-segment')).toBeTruthy();
    await userEvent.click(screen.getByTestId('theme-opt-dark'));
    expect(document.documentElement.dataset.theme).toBe('dark');
  });

  it('规划进行中任务图仍刷新（不因 planning 冻死 Loading）', async () => {
    let resolvePlan!: (v: { ok: boolean; message: string }) => void;
    vi.mocked(planProject).mockReturnValue(
      new Promise((r) => {
        resolvePlan = r;
      }),
    );
    vi.mocked(fetchDashboard).mockResolvedValue({
      ...dash,
      tasks: [
        { id: 1, name: '项目理解', status: 'CREATED', depends_on: [] },
        { id: 2, name: '方法识别', status: 'CREATED', depends_on: [1] },
      ],
    } as never);

    renderApp();
    expect(await screen.findByTestId('dashboard-workbench')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: '重新生成计划' }));

    expect(await screen.findByTestId('cancel-plan')).toBeTruthy();
    // 规划未结束前也应拉到 dashboard，展示任务节点（旧逻辑 paused=planning 会永久 Loading）
    await waitFor(() => {
      expect(vi.mocked(fetchDashboard).mock.calls.length).toBeGreaterThan(0);
      expect(screen.getByTestId('task-graph')).toHaveTextContent('项目理解');
    });
    expect(screen.queryByText('Loading dashboard…')).toBeNull();

    resolvePlan({ ok: true, message: '规划完成：2 任务 / 0 Gap' });
    await waitFor(() => {
      expect(screen.queryByTestId('cancel-plan')).toBeNull();
    });
  });

  it('新建后关闭弹窗、后台规划，工作台可看思维链', async () => {
    let resolvePlan!: (v: { ok: boolean; message: string }) => void;
    vi.mocked(planProject).mockReturnValue(
      new Promise((r) => {
        resolvePlan = r;
      }),
    );
    vi.mocked(createProject).mockResolvedValue({
      id: 42,
      name: 'demo',
      status: 'ACTIVE',
      automation_mode: 'FULL_AUTO',
    } as never);
    vi.mocked(uploadMethodAsset).mockResolvedValue({
      id: 1,
      content_hash: 'abc',
    } as never);
    vi.mocked(listProjects).mockResolvedValue([
      { id: 42, name: 'demo', status: 'ACTIVE', automation_mode: 'FULL_AUTO' },
    ]);
    vi.mocked(listProjectSummaries).mockResolvedValue([
      {
        id: 42,
        name: 'demo',
        status: 'ACTIVE',
        automation_mode: 'FULL_AUTO',
        created_at: null,
        task_total: 0,
        task_accepted: 0,
      },
    ]);
    vi.mocked(fetchAgentThinking).mockResolvedValue({
      events: [
        {
          event_id: 1,
          task_id: null,
          timestamp: null,
          text: '规划思考片段',
          agent_instance_id: null,
          agent_name: null,
        },
      ],
      last_event_id: 1,
    });

    renderApp();
    await userEvent.click(await screen.findByTestId('open-new-project'));
    await userEvent.type(screen.getByTestId('new-project-name'), 'demo');
    const file = new File(['# x'], 'a.md', { type: 'text/markdown' });
    await userEvent.upload(screen.getByTestId('new-project-files'), file);
    await userEvent.click(screen.getByTestId('new-project-submit'));

    await waitFor(() => {
      expect(screen.queryByTestId('new-project-modal')).toBeNull();
    });
    expect(await screen.findByTestId('thinking-panel')).toBeTruthy();
    expect(screen.getByTestId('thinking-phase-hint')).toBeTruthy();
    expect(screen.getByTestId('cancel-plan')).toBeTruthy();
    expect(screen.getByTestId('app-sidebar')).toBeTruthy();
    await waitFor(() => {
      expect(screen.getByTestId('thinking-panel')).toHaveTextContent(
        '规划思考片段',
      );
    });
    resolvePlan({ ok: true, message: '规划完成：1 任务 / 0 Gap' });
    await waitFor(() => {
      expect(screen.queryByTestId('cancel-plan')).toBeNull();
    });
  });
});
