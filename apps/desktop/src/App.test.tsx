import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Dashboard } from './components/Dashboard';
import type { DashboardData } from './api';
import { ThemeProvider } from './theme/ThemeProvider';

const MOCK: DashboardData = {
  project: { id: 1, name: 'demo', status: 'ACTIVE', automation_mode: 'FULL_AUTO' },
  tasks: [
    { id: 1, name: 'task-a', status: 'ACCEPTED', depends_on: [] },
    { id: 2, name: 'task-b', status: 'RUNNING', depends_on: [1] },
  ],
  current_task: { id: 2, name: 'task-b' },
  worker_status: { running: 1, ready: 0 },
  artifacts: [{ id: 9, name: 'out.txt', version: 1, sha256: 'abcdef123456', task_id: 1 }],
  gate_results: [{ id: 3, task_id: 1, status: 'PASS' }],
  readiness: { id: 1, version: 2, coverage_summary: null },
  gap_count: 4,
  timeline: [
    {
      id: 1,
      task_id: 1,
      task_name: 'task-a',
      attempt: 1,
      started_at: '2026-09-22T10:00:00',
      finished_at: '2026-09-22T10:01:00',
    },
  ],
};

vi.mock('./api', () => ({
  fetchDashboard: vi.fn(),
  listProjects: vi.fn(),
  userReworkTask: vi.fn(),
}));

import { fetchDashboard } from './api';

function renderDash() {
  return render(
    <ThemeProvider>
      <Dashboard projectId={1} />
    </ThemeProvider>,
  );
}

describe('Dashboard workbench', () => {
  beforeEach(() => {
    vi.mocked(fetchDashboard).mockResolvedValue(MOCK);
  });

  it('renders graph nodes; no Timeline / readiness side panels', async () => {
    renderDash();
    await waitFor(() => screen.getByTestId('task-graph'));

    expect(screen.getByTestId('task-graph')).toHaveTextContent('task-a');
    expect(screen.getByTestId('task-graph')).toHaveTextContent('task-b');
    expect(screen.getByTestId('task-node-1')).toBeDefined();
    expect(screen.getByTestId('task-node-2')).toBeDefined();
    const svg = screen.getByTestId('task-graph').querySelector('svg')!;
    expect(svg.querySelectorAll('polyline').length).toBe(1);
    expect(screen.queryByTestId('timeline')).toBeNull();
    expect(screen.queryByTestId('readiness')).toBeNull();
  });

  it('shows task detail overlay on node click', async () => {
    renderDash();
    await waitFor(() => screen.getByTestId('task-node-1'));
    await userEvent.click(screen.getByTestId('task-node-1'));
    await waitFor(() => screen.getByTestId('task-detail'));
    expect(screen.getByTestId('task-detail')).toHaveTextContent('task-a');
    expect(screen.getByTestId('task-detail-overlay')).toBeDefined();
  });

  it('shows error on fetch failure', async () => {
    vi.mocked(fetchDashboard).mockRejectedValueOnce(new Error('boom'));
    renderDash();
    await waitFor(() => screen.getByRole('alert'));
    expect(screen.getByRole('alert')).toHaveTextContent('boom');
  });
});
