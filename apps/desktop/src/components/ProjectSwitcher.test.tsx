import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ProjectSwitcher } from './ProjectSwitcher';

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api');
  // 45 个项目（触发分页：默认每页 20）——工厂内定义（vi.mock 提升）
  const mockProjects = Array.from({ length: 45 }, (_, i) => ({
    id: 300 - i,
    name: `项目-${300 - i}`,
    status: 'CREATED',
    automation_mode: i % 2 ? 'MANUAL' : 'FULL_AUTO',
    created_at: '2026-10-08T06:00:00',
    task_total: i,
    task_accepted: i > 40 ? 5 : 0,
  }));
  return {
    ...actual,
    listProjectSummaries: vi.fn().mockResolvedValue(mockProjects),
    deleteProject: vi.fn().mockResolvedValue({
      project_id: 1, name: 'x', tables: {}, disk_removed: [],
    }),
    batchDeleteProjects: vi.fn().mockResolvedValue({ deleted: [] }),
  };
});

async function openHistory() {
  render(<ProjectSwitcher currentId={300} onSelect={() => undefined} />);
  await userEvent.click(await screen.findByText(/历史项目（45）/));
  return screen.findByTestId('project-history');
}

describe('ProjectSwitcher 分页 + 全选（新增）', () => {
  it('paginates: default 20/page, 45 projects → 3 pages', async () => {
    await openHistory();
    const info = await screen.findByTestId('page-info');
    expect(info).toHaveTextContent('第 1 / 3 页');
    expect(screen.getAllByTestId(/^history-row-/).length).toBe(20);
  });

  it('page navigation works (next → last page has 5 rows)', async () => {
    await openHistory();
    await userEvent.click(screen.getByText('下一页 ›'));
    expect(screen.getByTestId('page-info')).toHaveTextContent('第 2 / 3 页');
    await userEvent.click(screen.getByText('末页 »'));
    expect(screen.getByTestId('page-info')).toHaveTextContent('第 3 / 3 页');
    expect(screen.getAllByTestId(/^history-row-/).length).toBe(5);
    await userEvent.click(screen.getByText('« 首页'));
    expect(screen.getByTestId('page-info')).toHaveTextContent('第 1 / 3 页');
  });

  it('page size is customizable (10 → 5 pages)', async () => {
    await openHistory();
    await userEvent.selectOptions(screen.getByTestId('page-size'), '10');
    expect(screen.getByTestId('page-info')).toHaveTextContent('第 1 / 5 页');
    expect(screen.getAllByTestId(/^history-row-/).length).toBe(10);
  });

  it('select-all-on-page toggles page only; selection accumulates across pages', async () => {
    await openHistory();
    await userEvent.click(screen.getByTestId('select-page-all'));
    expect(screen.getByText(/已选 20/)).toBeInTheDocument();
    await userEvent.click(screen.getByText('下一页 ›'));
    await userEvent.click(screen.getByTestId('select-page-all'));
    expect(screen.getByText(/已选 40/)).toBeInTheDocument();
    // 再点一次 = 本页全不选（第 1 页的保留）
    await userEvent.click(screen.getByTestId('select-page-all'));
    expect(screen.getByText(/已选 20/)).toBeInTheDocument();
  });

  it('选择全部 selects filtered set; 全不选 clears', async () => {
    await openHistory();
    await userEvent.click(screen.getByTestId('select-all-filtered'));
    expect(screen.getByText(/已选 45/)).toBeInTheDocument();
    await userEvent.click(screen.getByText('全不选'));
    expect(screen.getByText(/已选 0/)).toBeInTheDocument();
  });

  it('search filters and resets page', async () => {
    await openHistory();
    const search = screen.getByTestId('history-search');
    await userEvent.type(search, '项目-300');
    expect(screen.getByTestId('page-info')).toHaveTextContent('第 1 / 1 页');
    expect(screen.getAllByTestId(/^history-row-/).length).toBe(1);
  });
});

describe('ProjectSwitcher 基础（回归）', () => {
  it('renders current project selector', async () => {
    render(<ProjectSwitcher currentId={300} onSelect={() => undefined} />);
    const select = await screen.findByTestId('project-select');
    expect(
      (select as HTMLSelectElement).selectedOptions[0].value,
    ).toBe('300');
  });

  it('row delete button exists', async () => {
    window.confirm = vi.fn(() => false); // 拒绝确认——不真删
    await openHistory();
    expect(screen.getByTestId('delete-300')).toBeInTheDocument();
  });
});
