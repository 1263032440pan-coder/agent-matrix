import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { DashboardData } from '../api';

vi.mock('../api', () => ({
  fetchDashboard: vi.fn(),
  reclassifyDelivery: vi.fn(),
}));

import { fetchDashboard } from '../api';
import { Dashboard } from './Dashboard';

const base: DashboardData = {
  project: {
    id: 1,
    name: 'demo',
    status: 'ACTIVE',
    automation_mode: 'FULL_AUTO',
  },
  tasks: [
    {
      id: 10,
      name: '做PPT',
      status: 'READY',
      depends_on: [],
      required_deliverables: [
        {
          id: 'd_ppt',
          name: '方案PPT',
          match: { ext: ['.pptx'], min_count: 1 },
        },
      ],
    },
    {
      id: 11,
      name: '理解',
      status: 'ACCEPTED',
      depends_on: [],
      required_deliverables: [],
    },
  ],
  current_task: null,
  worker_status: { running: 0, ready: 1 },
  artifacts: [],
  gate_results: [],
  readiness: null,
  gap_count: 0,
  timeline: [],
};

describe('Dashboard 节点要求产物', () => {
  it('任务图节点上直接显示产物名', async () => {
    vi.mocked(fetchDashboard).mockResolvedValue(base);
    render(<Dashboard projectId={1} />);
    await screen.findByTestId('task-graph');
    expect(screen.getByTestId('task-products-10')).toHaveTextContent('方案PPT');
  });

  it('点击节点可见 required_deliverables', async () => {
    vi.mocked(fetchDashboard).mockResolvedValue(base);
    render(<Dashboard projectId={1} />);
    await screen.findByTestId('task-graph');
    await userEvent.click(screen.getByTestId('task-node-10'));
    await waitFor(() => {
      expect(screen.getByTestId('required-deliverables')).toHaveTextContent(
        '方案PPT',
      );
    });
    expect(screen.getByTestId('required-deliverables')).toHaveTextContent(
      '扩展名 .pptx',
    );
  });

  it('认领兜底时交付区显示标记', async () => {
    vi.mocked(fetchDashboard).mockResolvedValue({
      ...base,
      plan_status: 'READY',
      delivery_assignment: {
        manifest_count: 1,
        assigned_count: 1,
        uncovered_ids: [],
        assignment_mode: 'deterministic_fallback',
        items: [
          {
            id: 'd_ppt',
            name: '方案PPT',
            owner_task_id: 10,
            owner_task_name: '做PPT',
          },
        ],
      },
    });
    render(<Dashboard projectId={1} />);
    const box = await screen.findByTestId('delivery-assignment');
    expect(box).toHaveTextContent('认领兜底');
  });

  it('任务详情展示 load / 令牌 / 兜底装箱', async () => {
    vi.mocked(fetchDashboard).mockResolvedValue({
      ...base,
      tasks: [
        {
          ...base.tasks[0],
          task_load: 5,
          load_max: 6,
          obligation_token_ids: ['ext:d1:.py', 'count:d1:1'],
          bin_packed: true,
          decomposed_from: 'Worker执行',
        },
        base.tasks[1],
      ],
    });
    render(<Dashboard projectId={1} />);
    await screen.findByTestId('task-graph');
    await userEvent.click(screen.getByTestId('task-node-10'));
    const load = await screen.findByTestId('task-load');
    expect(load).toHaveTextContent('负荷 5 / 6');
    expect(load).toHaveTextContent('兜底装箱');
    expect(load).toHaveTextContent('拆自 Worker执行');
    expect(load).toHaveTextContent('ext:d1:.py');
  });

  it('TOOL_BUDGET_GRACE 在任务详情可见，并展示随后 Stop', async () => {
    vi.mocked(fetchDashboard).mockResolvedValue({
      ...base,
      runtime_events: [
        {
          event_id: 1,
          event_type: 'TOOL_BUDGET_GRACE',
          timestamp: '2026-10-28T10:00:00',
          execution_id: null,
          task_id: 10,
          phase: 'WORKER',
          message: '工具预算用尽·Grace',
          payload: {
            tool_rounds: 32,
            grace_text: 'Grace收尾：缺 README',
          },
        },
        {
          event_id: 2,
          event_type: 'DELIVERY_VALIDATOR_RUN',
          timestamp: '2026-10-28T10:00:01',
          execution_id: null,
          task_id: 10,
          phase: 'STOP',
          message: '缺 README',
          payload: {
            ok: false,
            problems: ['缺 README'],
          },
        },
      ],
    });
    render(<Dashboard projectId={1} />);
    await screen.findByTestId('task-graph');
    await userEvent.click(screen.getByTestId('task-node-10'));
    await waitFor(() => {
      expect(screen.getByTestId('task-grace')).toHaveTextContent(
        'Grace收尾：缺 README',
      );
    });
    expect(screen.getByTestId('task-grace')).toHaveTextContent('32 轮');
    expect(screen.getByTestId('task-grace-stop')).toHaveTextContent(
      'Stop：未通过',
    );
    expect(screen.getByTestId('task-grace-stop')).toHaveTextContent(
      '缺 README',
    );
  });

  it('无要求产物时显示说明文案', async () => {
    vi.mocked(fetchDashboard).mockResolvedValue(base);
    render(<Dashboard projectId={1} />);
    await screen.findByTestId('task-graph');
    await userEvent.click(screen.getByTestId('task-node-11'));
    await waitFor(() => {
      expect(screen.getByTestId('required-deliverables')).toHaveTextContent(
        '本节点无强制交付项',
      );
    });
  });
});
