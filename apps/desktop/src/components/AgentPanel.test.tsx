import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { AgentPanel } from './AgentPanel';

// api mock（相对测试文件路径——ProjectPanels 同款范式）
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
      plugins: ['file_rw', 'web_search', 'shell', 'code_search', 'git'],
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
      plugins: ['file_rw', 'web_search'],
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
}));

import { agentLifecycle, listAgentDefinitions, listProjectAgents } from '../api';

describe('AgentPanel（20号规范 §9 双视图+六态+压力条）', () => {
  it('实例视图: 六态徽章+当前任务+失败计数渲染', async () => {
    render(<AgentPanel projectId={1} onChanged={() => {}} />);
    expect(await screen.findByTestId('agent-row-1')).toBeTruthy();
    expect(screen.getByTestId('agent-status-BUSY').textContent).toBe('执行中');
    expect(screen.getByTestId('agent-status-DEGRADED').textContent).toBe('降级中');
    expect(screen.getByText('任务 #42')).toBeTruthy();
    // 失败计数可见（DEGRADED 实例 3 次）
    expect(screen.getByText('3')).toBeTruthy();
  });

  it('能力徽章: 工具/技能/环节可见（白名单护栏可视化）', async () => {
    render(<AgentPanel projectId={1} onChanged={() => {}} />);
    const cap = await screen.findByTestId('capability-1');
    // coder 实例: 5 工具徽章 + 环节徽章
    expect(cap.textContent).toContain('file_rw');
    expect(cap.textContent).toContain('shell');
    expect(cap.textContent).toContain('git');
    expect(cap.textContent).toContain('开发实现');
    // skills=[] → 显示全局自动选提示
    expect(cap.textContent).toContain('全局自动选');
  });

  it('上下文压力条: 三段变色数据源渲染（45K/112K 与 105K/112K）', async () => {
    render(<AgentPanel projectId={1} onChanged={() => {}} />);
    const bars = await screen.findAllByTestId('context-bar');
    expect(bars.length).toBe(2);
    expect(bars[0].textContent).toContain('45.0K/112K');
    expect(bars[1].textContent).toContain('105.0K/112K');
  });

  it('暂停动作: 调 agentLifecycle(pause) 并回调 onChanged', async () => {
    const onChanged = vi.fn();
    render(<AgentPanel projectId={1} onChanged={onChanged} />);
    const btn = await screen.findByTestId('pause-1');
    await userEvent.click(btn);
    await waitFor(() => {
      expect(agentLifecycle).toHaveBeenCalledWith(1, 1, 'pause');
      expect(onChanged).toHaveBeenCalled();
    });
  });

  it('PAUSED 实例显示恢复按钮（而非暂停）', async () => {
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
      },
    ]);
    render(<AgentPanel projectId={1} onChanged={() => {}} />);
    expect(await screen.findByTestId('resume-5')).toBeTruthy();
    expect(screen.queryByTestId('pause-5')).toBeNull();
  });

  it('模板视图: 来源徽章（内置/AI 生成）+ 环节亲和', async () => {
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

  it('空实例态: 引导文案（生成计划后自动创建）', async () => {
    vi.mocked(listProjectAgents).mockResolvedValueOnce([]);
    render(<AgentPanel projectId={1} onChanged={() => {}} />);
    expect(
      await screen.findByText(/暂无智能体实例/)
    ).toBeTruthy();
  });
});
