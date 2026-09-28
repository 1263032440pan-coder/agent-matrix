"""Base Pipeline declarations (WP-035 / 03-模型 PipelineDefinition).

首批六条稳定 Base Pipeline（WP-035 首批清单）。Steps are stable core
lifecycle segments; extension_points declare WHERE runtime extensions may
attach (must be from the WP-034 STABLE_POINTS list — single source, no
second point registry).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from project_runtime.extension.runtime.executor import STABLE_POINTS


class PipelineError(Exception):
    pass


@dataclass
class PipelineStep:
    step_id: str
    description: str = ""
    # Core action reference — executed by the engine's built-in runner.
    # Extensions never replace core steps; they attach AROUND them.
    core_action: str | None = None


@dataclass
class PipelineDefinition:
    pipeline_id: str
    version: str
    steps: list[PipelineStep]
    # extension point ids where extensions may attach (subset of STABLE_POINTS)
    extension_points: list[str] = field(default_factory=list)
    status: str = "ACTIVE"

    def validate(self) -> None:
        for ep in self.extension_points:
            if ep not in STABLE_POINTS:
                raise PipelineError(
                    f"Pipeline {self.pipeline_id}: extension point {ep!r} "
                    f"is not a stable point (14-规范清单)"
                )


def _def(pid: str, steps: list[tuple[str, str]], ext_points: list[str]) -> PipelineDefinition:
    return PipelineDefinition(
        pipeline_id=pid,
        version="1.0",
        steps=[PipelineStep(s, d) for s, d in steps],
        extension_points=ext_points,
    )


# ── 首批 Base Pipelines（WP-035 固定清单）───────────────────────────────
BASE_PIPELINES: dict[str, PipelineDefinition] = {
    "project_bootstrap": _def(
        "project_bootstrap",
        [
            ("project.create", "创建项目与初始状态"),
            ("assets.discover", "方法资产发现"),
            ("procedure.normalize", "规范化为过程模型"),
        ],
        ["project.bootstrap.before", "project.bootstrap.after"],
    ),
    "project_understanding": _def(
        "project_understanding",
        [
            ("understanding.analyze", "项目理解分析"),
            ("capability.assess", "能力覆盖评估"),
        ],
        [
            "project.understanding.before",
            "project.understanding.after",
            "capability.assessment.after",
        ],
    ),
    "planning": _def(
        "planning",
        [
            ("readiness.assess", "就绪度评估"),
            ("gap.classify", "缺口分类"),
            ("playbook.build", "构建 Playbook"),
            ("planner.plan", "生成任务图"),
        ],
        ["playbook.build.before", "playbook.build.after", "planner.before", "planner.after"],
    ),
    "task_execution": _def(
        "task_execution",
        [
            ("task.prepare", "任务准备"),
            ("automation.check", "自动化模式检查"),
            ("context.build", "执行上下文构建"),
            ("worker.select", "Worker 选择"),
            ("worker.execute", "执行"),
            ("result.process", "结果处理"),
            ("gate.evaluate", "Gate 验收"),
        ],
        ["scheduler.before_dispatch", "worker.before_execute", "worker.after_execute"],
    ),
    "gate_evaluation": _def(
        "gate_evaluation",
        [
            ("gate.collect", "收集验收物"),
            ("gate.run", "运行验证器"),
            ("gate.decide", "ACCEPTED/REWORK 判定"),
        ],
        ["gate.validators"],
    ),
    "delivery_validation": _def(
        "delivery_validation",
        [
            ("delivery.collect", "收集交付物"),
            ("delivery.checklist", "清单核对"),
            ("delivery.final_gate", "最终验收"),
        ],
        ["delivery.validators"],
    ),
}


def get_pipeline(pipeline_id: str) -> PipelineDefinition:
    d = BASE_PIPELINES.get(pipeline_id)
    if d is None:
        raise PipelineError(f"Unknown pipeline: {pipeline_id}")
    return d
