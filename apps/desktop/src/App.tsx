import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
} from 'react';
import {
  deleteProject,
  listProjectSummaries,
  listProjects,
  type ProjectSummary,
} from './api';
import { AgentPanel } from './components/AgentPanel';
import { Dashboard } from './components/Dashboard';
import { NewProjectModal } from './components/NewProjectModal';
import { PaneSplit, SectionFold } from './components/PaneSplit';
import { ProjectOverview } from './components/ProjectOverview';
import { SettingsView } from './components/SettingsView';
import { ThinkingPanel } from './components/ThinkingPanel';
import { planProject, runExecute } from './projectOps';

type MainView = 'workbench' | 'overview';

function App() {
  const [projectId, setProjectId] = useState<number | null>(null);
  const [summaries, setSummaries] = useState<ProjectSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [busy, setBusy] = useState(false);
  /** 后台规划中：不锁 UI，顶栏提示；任务图仍轮询（规划写库短事务，可与 10s dashboard 共存） */
  const [planning, setPlanning] = useState(false);
  const planAbortRef = useRef<AbortController | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [newProjectOpen, setNewProjectOpen] = useState(false);
  const [mainView, setMainView] = useState<MainView>('workbench');
  const [projectsOpen, setProjectsOpen] = useState(true);
  const [agentsOpen, setAgentsOpen] = useState(true);
  const [graphOpen, setGraphOpen] = useState(true);
  const [thinkingOpen, setThinkingOpen] = useState(true);
  const [projectQuery, setProjectQuery] = useState('');
  const [leftWidth, setLeftWidth] = useState(() => {
    try {
      const n = Number(localStorage.getItem('am.split.left'));
      return Number.isFinite(n) && n >= 160 && n <= 480 ? n : 260;
    } catch {
      return 260;
    }
  });
  const [statusText, setStatusText] = useState<string | null>(null);

  // 仅 execute 短请求期间暂停；规划中若 paused=true，bump 会 remount 成永久 Loading
  const pollPaused = busy;

  const reload = useCallback(() => {
    listProjects()
      .then((projects) => {
        setProjectId((current) => {
          if (current === null && projects.length > 0) {
            return projects[projects.length - 1].id;
          }
          if (current !== null && !projects.some((p) => p.id === current)) {
            return projects.length > 0 ? projects[projects.length - 1].id : null;
          }
          return current;
        });
      })
      .catch((e) => setError(String(e)));
    listProjectSummaries()
      .then(setSummaries)
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    reload();
  }, [reload, refreshKey]);

  const bump = () => setRefreshKey((k) => k + 1);

  const filteredProjects = useMemo(() => {
    const sorted = [...summaries].sort((a, b) => b.id - a.id);
    const q = projectQuery.trim().toLowerCase();
    if (!q) return sorted;
    return sorted.filter((p) =>
      `${p.id} ${p.name}`.toLowerCase().includes(q),
    );
  }, [summaries, projectQuery]);

  const startBackgroundPlan = useCallback(
    async (id: number) => {
      planAbortRef.current?.abort();
      const ac = new AbortController();
      planAbortRef.current = ac;
      setProjectId(id);
      setMainView('workbench');
      setThinkingOpen(true);
      setPlanning(true);
      setStatusText(`项目 #${id} 正在生成计划（可继续操作，下方看运行动态）…`);
      bump();
      try {
        const r = await planProject(id, ac.signal);
        if (ac.signal.aborted) return;
        setStatusText(r.message);
      } finally {
        if (planAbortRef.current === ac) {
          planAbortRef.current = null;
          setPlanning(false);
        }
        // planning 解除后再 bump，避免 remount 时仍 paused
        bump();
      }
    },
    // bump is stable enough via setState; eslint intentional
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const cancelBackgroundPlan = () => {
    planAbortRef.current?.abort();
    planAbortRef.current = null;
    setPlanning(false);
    setStatusText('已取消生成计划请求（服务端可能仍在收尾）');
  };

  const doPlan = () => {
    if (projectId === null || planning) return;
    void startBackgroundPlan(projectId);
  };

  const doExecute = async () => {
    if (projectId === null) return;
    setBusy(true);
    setStatusText('执行中…');
    try {
      const r = await runExecute(projectId);
      setStatusText(r.message);
      if (r.ok) bump();
    } finally {
      setBusy(false);
    }
  };

  const onDeleteProject = async (p: ProjectSummary) => {
    if (
      !window.confirm(
        `确定删除项目 #${p.id}「${p.name}」吗？\n将级联删除全部任务、产物、执行记录、事件等，不可恢复。`,
      )
    ) {
      return;
    }
    setBusy(true);
    try {
      await deleteProject(p.id);
      if (projectId === p.id) {
        setProjectId(null);
        setMainView('workbench');
      }
      bump();
    } catch (e) {
      setStatusText(String(e));
    } finally {
      setBusy(false);
    }
  };

  const onLeftDrag = (e: ReactMouseEvent) => {
    e.preventDefault();
    const onMove = (ev: MouseEvent) => {
      const next = Math.min(480, Math.max(160, ev.clientX));
      setLeftWidth(next);
      try {
        localStorage.setItem('am.split.left', String(next));
      } catch {
        /* ignore */
      }
    };
    const onUp = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  if (settingsOpen) {
    return (
      <div className="app-shell" data-testid="app-shell">
        <header className="app-topbar">
          <strong>Agent Matrix</strong>
          <span className="ui-pill ui-pill-active">FULL_AUTO</span>
          <div style={{ flex: 1 }} />
          <button
            type="button"
            className="ui-btn"
            onClick={() => setSettingsOpen(false)}
          >
            ← 返回工作台
          </button>
        </header>
        <SettingsView onClose={() => setSettingsOpen(false)} />
      </div>
    );
  }

  return (
    <div className="app-shell" data-testid="app-shell">
      <header className="app-topbar">
        <strong>Agent Matrix</strong>
        <span className="ui-pill ui-pill-active" data-testid="mode-badge">
          FULL_AUTO
        </span>
        {statusText ? (
          <span
            data-testid="plan-status"
            style={{
              fontSize: 12,
              color: planning ? 'var(--ui-accent)' : 'var(--ui-text-muted)',
              maxWidth: 420,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
            title={statusText}
          >
            {planning ? '⏳ ' : ''}
            {statusText}
          </span>
        ) : null}
        {planning ? (
          <button
            type="button"
            className="ui-btn"
            data-testid="cancel-plan"
            onClick={cancelBackgroundPlan}
          >
            取消规划
          </button>
        ) : null}
        <div style={{ flex: 1 }} />
        <button
          type="button"
          className="ui-btn"
          data-testid="open-settings"
          onClick={() => setSettingsOpen(true)}
        >
          设置
        </button>
      </header>

      <div className="app-body">
        <aside
          className="app-sidebar"
          style={{ width: leftWidth, flexShrink: 0 }}
          data-testid="app-sidebar"
        >
          <PaneSplit
            axis="row"
            testId="sidebar-split"
            storageKey="am.split.sidebar"
            firstCollapsed={!projectsOpen}
            secondCollapsed={!agentsOpen}
            first={
              <SectionFold
                title="项目"
                open={projectsOpen}
                onToggle={() => {
                  if (projectsOpen && !agentsOpen) return;
                  setProjectsOpen((v) => !v);
                }}
                testId="projects-pane"
                headerExtra={
                  <button
                    type="button"
                    className="ui-btn"
                    data-testid="open-new-project"
                    onClick={() => setNewProjectOpen(true)}
                  >
                    + 新建
                  </button>
                }
              >
                <div className="pane-scroll">
                  <input
                    data-testid="project-search"
                    placeholder="搜索项目…"
                    value={projectQuery}
                    onChange={(e) => setProjectQuery(e.target.value)}
                    style={{
                      width: '100%',
                      marginBottom: 8,
                      padding: '6px 8px',
                      border: '1px solid var(--ui-stroke)',
                      borderRadius: 4,
                      background: 'var(--ui-bg)',
                    }}
                  />
                  {filteredProjects.length === 0 && (
                    <div
                      style={{ color: 'var(--ui-text-muted)', fontSize: 13 }}
                    >
                      暂无项目
                    </div>
                  )}
                  {filteredProjects.map((p) => (
                    <div
                      key={p.id}
                      className="list-row"
                      data-active={
                        p.id === projectId && mainView === 'overview'
                      }
                      data-testid={`project-row-${p.id}`}
                      style={{
                        display: 'flex',
                        alignItems: 'flex-start',
                        gap: 6,
                        cursor: 'pointer',
                      }}
                      onClick={() => {
                        setProjectId(p.id);
                        setMainView('overview');
                        bump();
                      }}
                    >
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontWeight: 600 }}>
                          #{p.id} {p.name}
                        </div>
                        <div
                          style={{
                            fontSize: 12,
                            color: 'var(--ui-text-muted)',
                          }}
                        >
                          {p.task_accepted ?? 0}/{p.task_total ?? 0} 完成
                        </div>
                      </div>
                      <button
                        type="button"
                        className="ui-btn"
                        data-testid={`delete-project-${p.id}`}
                        disabled={busy}
                        title="删除项目"
                        onClick={(e) => {
                          e.stopPropagation();
                          void onDeleteProject(p);
                        }}
                      >
                        删
                      </button>
                    </div>
                  ))}
                </div>
              </SectionFold>
            }
            second={
              <SectionFold
                title="智能体"
                open={agentsOpen}
                onToggle={() => {
                  if (agentsOpen && !projectsOpen) return;
                  setAgentsOpen((v) => !v);
                }}
                testId="agents-pane"
              >
                {projectId !== null ? (
                  <div className="pane-scroll">
                    <AgentPanel projectId={projectId} onChanged={bump} />
                  </div>
                ) : (
                  <div
                    className="pane-scroll"
                    style={{ color: 'var(--ui-text-muted)' }}
                  >
                    选择项目后显示智能体
                  </div>
                )}
              </SectionFold>
            }
          />
        </aside>

        <div
          data-testid="left-right-handle"
          role="separator"
          aria-orientation="vertical"
          onMouseDown={onLeftDrag}
          style={{
            width: 6,
            flexShrink: 0,
            cursor: 'col-resize',
            background: 'var(--ui-bg-chrome)',
            borderLeft: '1px solid var(--ui-stroke)',
            borderRight: '1px solid var(--ui-stroke)',
          }}
        />

        <main className="app-main" data-testid="app-main">
          {error && <div role="alert">{error}</div>}
          {projectId === null && !error && (
            <div className="pane-scroll">暂无项目 — 请新建</div>
          )}
          {projectId !== null && mainView === 'overview' && (
            <ProjectOverview
              key={`ov-${refreshKey}`}
              projectId={projectId}
              paused={pollPaused}
              busy={busy || planning}
              onBack={() => setMainView('workbench')}
              onExecute={doExecute}
              onPlan={doPlan}
            />
          )}
          {projectId !== null && mainView === 'workbench' && (
            <PaneSplit
              axis="row"
              testId="workbench-split"
              storageKey="am.split.workbench"
              firstCollapsed={!graphOpen}
              secondCollapsed={!thinkingOpen}
              first={
                <SectionFold
                  title="任务流程图"
                  open={graphOpen}
                  onToggle={() => {
                    if (graphOpen && !thinkingOpen) return;
                    setGraphOpen((v) => !v);
                  }}
                  testId="graph-pane"
                >
                  <Dashboard
                    key={refreshKey}
                    projectId={projectId}
                    paused={pollPaused}
                    busy={busy || planning}
                    onExecute={doExecute}
                    onPlan={doPlan}
                  />
                </SectionFold>
              }
              second={
                <SectionFold
                  title={planning ? '运行动态 · 规划中' : '运行动态'}
                  open={thinkingOpen}
                  onToggle={() => {
                    if (thinkingOpen && !graphOpen) return;
                    setThinkingOpen((v) => !v);
                  }}
                  testId="thinking-pane"
                  hint={
                    planning
                      ? '规划阶段活动与模型输出会显示在此'
                      : undefined
                  }
                >
                  <ThinkingPanel
                    projectId={projectId}
                    embedded
                    phaseHint={
                      planning
                        ? '正在生成计划 — 思考/工具/阶段活动'
                        : undefined
                    }
                  />
                </SectionFold>
              }
            />
          )}
        </main>
      </div>

      <NewProjectModal
        open={newProjectOpen}
        onClose={() => setNewProjectOpen(false)}
        onReadyToPlan={(id) => {
          void startBackgroundPlan(id);
        }}
      />
    </div>
  );
}

export default App;
