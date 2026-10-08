import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ProjectSwitcher } from './ProjectSwitcher';

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api');
  return {
    ...actual,
    listProjectSummaries: vi.fn().mockResolvedValue([
      {
        id: 247,
        name: 'wp018-test',
        status: 'CREATED',
        automation_mode: 'FULL_AUTO',
        created_at: '2026-10-08T06:00:00',
        task_total: 14,
        task_accepted: 8,
      },
      {
        id: 246,
        name: 'dashboard-test',
        status: 'CREATED',
        automation_mode: 'MANUAL',
        created_at: '2026-10-08T03:54:00',
        task_total: 1,
        task_accepted: 0,
      },
    ]),
  };
});

describe('ProjectSwitcher (WP-018 补)', () => {
  it('renders current project selector with task counts', async () => {
    render(
      <ProjectSwitcher currentId={247} onSelect={() => undefined} />,
    );
    const select = await screen.findByTestId('project-select');
    expect(select).toHaveTextContent('14 任务');
    expect(select).toHaveTextContent('8 完成');
    // option for 247 selected
    expect(
      (select as HTMLSelectElement).selectedOptions[0].value,
    ).toBe('247');
  });

  it('switching via select fires onSelect', async () => {
    const onSelect = vi.fn();
    render(<ProjectSwitcher currentId={247} onSelect={onSelect} />);
    const select = await screen.findByTestId('project-select');
    await userEvent.selectOptions(select, '246');
    expect(onSelect).toHaveBeenCalledWith(246);
  });

  it('history table lists all projects with switch buttons', async () => {
    render(<ProjectSwitcher currentId={247} onSelect={() => undefined} />);
    await userEvent.click(await screen.findByText(/历史项目（2）/));
    const history = await screen.findByTestId('project-history');
    expect(history).toHaveTextContent('dashboard-test');
    expect(history).toHaveTextContent('MANUAL');
    // current row's button disabled
    expect(screen.getByText('当前')).toBeDisabled();
    expect(screen.getByText('切换')).toBeEnabled();
  });
});
