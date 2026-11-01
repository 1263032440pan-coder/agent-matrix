import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ProjectOverview } from './ProjectOverview';

vi.mock('../api', () => ({
  fetchDashboard: vi.fn().mockResolvedValue({
    project: {
      id: 2,
      name: '测试2',
      status: 'CREATED',
      automation_mode: 'FULL_AUTO',
    },
    tasks: [
      {
        id: 1,
        name: '项目理解',
        status: 'ACCEPTED',
        required_deliverables: [],
      },
      {
        id: 2,
        name: '做PPT',
        status: 'READY',
        required_deliverables: [
          { id: 'd1', name: '方案PPT', match: { ext: ['.pptx'] } },
        ],
      },
    ],
    current_task: null,
    worker_status: { running: 0, ready: 1 },
    artifacts: [],
    gate_results: [],
    readiness: { id: 1, version: 1, coverage_summary: null },
    gap_count: 5,
    timeline: [{ id: 1, task_id: 1, task_name: 'a', attempt: 1 }],
    runtime_events: [],
    delivery_quality: { accepted: 1, soft_accepted: 1, soft_rate: 1 },
    delivery_checklist: {
      updated_at: '2026-10-31T00:00:00Z',
      total: 1,
      matched: 0,
      items: [
        {
          id: 'd1',
          name: '方案PPT',
          match: { ext: ['.pptx'] },
          owner_task_id: 2,
          owner_task_name: '做PPT',
          owner_task_status: 'READY',
          status: 'in_progress',
          matched_paths: [],
          hint: '缺少满足条件的文件',
        },
      ],
    },
  }),
}));

describe('ProjectOverview', () => {
  it('按流程节点展示产物对比，而非独立总清单', async () => {
    render(
      <ProjectOverview
        projectId={2}
        onBack={() => undefined}
        onExecute={() => undefined}
        onPlan={() => undefined}
      />,
    );
    expect(await screen.findByTestId('project-overview')).toBeTruthy();
    expect(screen.getByTestId('overview-progress')).toHaveTextContent('1 / 2');
    expect(screen.getByTestId('delivery-checklist')).toBeTruthy();
    expect(screen.getByTestId('delivery-checklist-summary')).toHaveTextContent(
      '节点 1/2 完成',
    );
    expect(screen.getByTestId('delivery-checklist-all')).toHaveTextContent(
      '方案PPT',
    );
    expect(screen.getByTestId('delivery-checklist-node-1')).toHaveTextContent(
      '项目理解',
    );
    expect(screen.getByTestId('delivery-checklist-node-2')).toHaveTextContent(
      '做PPT',
    );
    expect(
      screen.getAllByTestId('delivery-checklist-item-d1').length,
    ).toBeGreaterThanOrEqual(1);
    expect(screen.queryByTestId('runtime-events')).toBeNull();
  });
});
