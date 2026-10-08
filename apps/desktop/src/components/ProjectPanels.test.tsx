import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { frontierIds } from './Dashboard';
import { ProjectPanels } from './ProjectPanels';

// api mock（相对测试文件路径）
vi.mock('../api', () => ({
  confirmManualAction: vi.fn().mockResolvedValue({ status: 'CONFIRMED' }),
  rejectManualAction: vi.fn().mockResolvedValue({ status: 'REJECTED' }),
  createCheckpoint: vi
    .fn()
    .mockResolvedValue({ checkpoint_id: 7 }),
  restoreCheckpoint: vi
    .fn()
    .mockResolvedValue({ restored_project_id: 9 }),
  listCheckpoints: vi.fn().mockResolvedValue([
    { id: 7, trigger: 'MANUAL', summary: 'tasks=3', created_at: null },
  ]),
  fetchDashboard: vi.fn().mockResolvedValue({
    project: { id: 1, name: 'p', status: 'CREATED', automation_mode: 'MANUAL' },
    tasks: [
      { id: 1, name: 't1', status: 'READY', depends_on: [] },
      { id: 2, name: 't2', status: 'RUNNING', depends_on: [1] },
    ],
    timeline: [],
    artifacts: [],
    gate_results: [],
    manual_actions: [
      { id: 5, task_id: 1, action_type: 'TASK_DISPATCH', status: 'PENDING' },
    ],
    delivery: {
      contract_id: 3,
      version: 1,
      status: 'DRAFT',
      items: [{ id: 1, requirement_ref: '功能A', status: 'PENDING' }],
    },
  }),
}));

describe('ProjectPanels (WP-018)', () => {
  it('renders manual actions queue with confirm/reject buttons', async () => {
    render(<ProjectPanels projectId={1} onChanged={() => undefined} />);
    expect(await screen.findByTestId('manual-actions')).toHaveTextContent(
      'TASK_DISPATCH',
    );
    expect(screen.getByText('确认执行')).toBeInTheDocument();
    expect(screen.getByText('拒绝')).toBeInTheDocument();
  });

  it('confirm button calls confirmManualAction and refreshes', async () => {
    const { confirmManualAction } = await import('../api');
    render(<ProjectPanels projectId={1} onChanged={() => undefined} />);
    const btn = await screen.findByText('确认执行');
    await userEvent.click(btn);
    await waitFor(() =>
      expect(confirmManualAction).toHaveBeenCalledWith(5),
    );
  });

  it('renders delivery contract items', async () => {
    render(<ProjectPanels projectId={1} onChanged={() => undefined} />);
    expect(await screen.findByTestId('delivery')).toHaveTextContent('功能A');
  });

  it('renders checkpoints with restore button', async () => {
    render(<ProjectPanels projectId={1} onChanged={() => undefined} />);
    expect(await screen.findByTestId('checkpoints')).toHaveTextContent(
      'tasks=3',
    );
    expect(screen.getByText('恢复为新副本')).toBeInTheDocument();
  });

  it('snapshot button creates a checkpoint', async () => {
    const { createCheckpoint } = await import('../api');
    render(<ProjectPanels projectId={1} onChanged={() => undefined} />);
    await userEvent.click(await screen.findByText('创建快照'));
    await waitFor(() => expect(createCheckpoint).toHaveBeenCalledWith(1));
  });
});

describe('frontierIds (WP-018 Current Frontier)', () => {
  it('returns READY task ids only', () => {
    const tasks = [
      { id: 1, name: 'a', status: 'READY' },
      { id: 2, name: 'b', status: 'RUNNING' },
      { id: 3, name: 'c', status: 'READY' },
      { id: 4, name: 'd', status: 'ACCEPTED' },
    ];
    expect(frontierIds(tasks)).toEqual([1, 3]);
  });
});
