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
