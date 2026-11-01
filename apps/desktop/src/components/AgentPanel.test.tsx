import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { AgentPanel } from './AgentPanel';

vi.mock('../api', () => ({
  listProjectAgents: vi.fn().mockResolvedValue([
    {
      id: 1,
      definition_id: 3,
      agent_id: 'coder',
      name: '编码智能体',
      project_id: 1,
      status: 'BUSY',
      current_task_id: 42,
      fail_count: 0,
      context_tokens: 45000,
      context_threshold: 112000,
      plugins: [
        'read_file',
        'list_dir',
        'write_file',
        'web_search',
        'shell',
        'code_search',
        'git',
      ],
      skills: [],
      stage_affinity: ['开发实现'],
    },
    {
      id: 2,
      definition_id: 4,
      agent_id: 'tester',
      name: '测试智能体',
      project_id: 1,
      status: 'DEGRADED',
      current_task_id: null,
      fail_count: 3,
      context_tokens: 105000,
      context_threshold: 112000,
      plugins: ['read_file', 'list_dir', 'write_file', 'web_search'],
      skills: [],
      stage_affinity: ['misc'],
    },
  ]),
  listAgentDefinitions: vi.fn().mockResolvedValue([
    {
      id: 3,
      agent_id: 'coder',
      name: '编码智能体',
      role_description: '实现开发任务',
      plugins: ['tool:git', 'tool:test_runner'],
      skills: [],
      stage_affinity: ['开发实现'],
      source: 'BUILT_IN',
      scope: 'GLOBAL',
      enabled: true,
    },
    {
      id: 9,
      agent_id: 'proj-helper',
      name: '项目助手',
      role_description: null,
      plugins: [],
      skills: [],
      stage_affinity: [],
      source: 'CONTROLLER_GENERATED',
      scope: 'PROJECT',
      enabled: true,
    },
  ]),
  agentLifecycle: vi.fn().mockResolvedValue({ status: 'PAUSED' }),
  fetchAgentThinking: vi.fn().mockResolvedValue({ events: [], last_event_id: 0 }),
}));

import { agentLifecycle, listAgentDefinitions, listProjectAgents } from '../api';

describe('AgentPanel（侧栏紧凑 · 维度不删减）', () => {
  it('紧凑实例行: 六态徽章+当前任务+失败计数', async () => {
    render(<AgentPanel projectId={1} onChanged={() => {}} />);
    expect(await screen.findByTestId('agent-row-1')).toBeTruthy();
    expect(screen.getByTestId('agent-status-BUSY').textContent).toBe('执行中');
    expect(screen.getByTestId('agent-status-DEGRADED').textContent).toBe(
      '降级中',
    );
    expect(screen.getByText('#42')).toBeTruthy();
    expect(screen.getByTestId('fail-count-2').textContent).toContain('3');
    expect(screen.getByTestId('agent-instances-compact')).toBeTruthy();
  });

  it('展开后能力徽章可达（工具/技能/环节）', async () => {
    render(<AgentPanel projectId={1} onChanged={() => {}} />);
    await userEvent.click(await screen.findByTestId('agent-row-1'));
    const cap = await screen.findByTestId('capability-1');
    expect(cap.textContent).toContain('write_file');
    expect(cap.textContent).toContain('shell');
    expect(cap.textContent).toContain('git');
    expect(cap.textContent).toContain('开发实现');
    expect(cap.textContent).toContain('全局自动选');
  });

  it('上下文压力条在紧凑行可见', async () => {
    render(<AgentPanel projectId={1} onChanged={() => {}} />);
    const bars = await screen.findAllByTestId('context-bar');
    expect(bars.length).toBeGreaterThanOrEqual(2);
    expect(bars[0].textContent).toContain('45.0K/112K');
    expect(bars[1].textContent).toContain('105.0K/112K');
  });

  it('展开后暂停动作可用', async () => {
    const onChanged = vi.fn();
    render(<AgentPanel projectId={1} onChanged={onChanged} />);
    await userEvent.click(await screen.findByTestId('agent-row-1'));
    const btn = await screen.findByTestId('pause-1');
    await userEvent.click(btn);
    await waitFor(() => {
      expect(agentLifecycle).toHaveBeenCalledWith(1, 1, 'pause');
      expect(onChanged).toHaveBeenCalled();
    });
  });

  it('PAUSED 实例展开后显示恢复按钮', async () => {
    vi.mocked(listProjectAgents).mockResolvedValueOnce([
      {
        id: 5,
        definition_id: 3,
        agent_id: 'coder',
        name: '编码智能体',
        project_id: 1,
        status: 'PAUSED',
        current_task_id: null,
        fail_count: 0,
        context_tokens: 0,
        context_threshold: 112000,
        plugins: [],
        skills: [],
        stage_affinity: [],
      },
    ]);
    render(<AgentPanel projectId={1} onChanged={() => {}} />);
    await userEvent.click(await screen.findByTestId('agent-row-5'));
    expect(await screen.findByTestId('resume-5')).toBeTruthy();
    expect(screen.queryByTestId('pause-5')).toBeNull();
  });

  it('模板视图: 来源徽章 + 环节亲和', async () => {
    render(<AgentPanel projectId={1} onChanged={() => {}} />);
    await userEvent.click(screen.getByTestId('tab-templates'));
    expect(await screen.findByTestId('agent-def-coder')).toBeTruthy();
    expect(screen.getByText('内置')).toBeTruthy();
    expect(screen.getByText('AI 生成')).toBeTruthy();
    expect(screen.getByText(/环节亲和: 开发实现/)).toBeTruthy();
  });

  it('模板视图接口被调用', async () => {
    render(<AgentPanel projectId={1} onChanged={() => {}} />);
    await userEvent.click(screen.getByTestId('tab-templates'));
    await waitFor(() => {
      expect(listAgentDefinitions).toHaveBeenCalled();
    });
  });

  it('空实例态: 引导文案', async () => {
    vi.mocked(listProjectAgents).mockResolvedValueOnce([]);
    render(<AgentPanel projectId={1} onChanged={() => {}} />);
    expect(
      await screen.findByText(/暂无智能体实例/),
    ).toBeTruthy();
  });

  it('侧栏不再挂载表底思维链', async () => {
    render(<AgentPanel projectId={1} onChanged={() => {}} />);
    await screen.findByTestId('agent-row-1');
    expect(screen.queryByTestId('agent-thinking')).toBeNull();
  });
});
