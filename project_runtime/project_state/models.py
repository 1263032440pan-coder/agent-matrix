"""Core domain entities for Project Autonomous Agent.

Seventeen entities per WP-002 specification.
"""

import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from project_runtime.project_state.base import Base
from project_runtime.project_state.enums import (
    ArtifactType,
    AssetType,
    AutomationMode,
    ConstraintType,
    DecisionStatus,
    DeliveryContractStatus,
    DeliveryItemStatus,
    DependencyType,
    GateStatus,
    IssueSeverity,
    IssueStatus,
    ManualActionStatus,
    ManualActionType,
    MethodAssetAuthority,
    ProjectStatus,
    ReadinessStatus,
    TaskStatus,
    WorkerHealth,
    WorkerInstanceStatus,
)

# ---------------------------------------------------------------------------
# 1. Project
# ---------------------------------------------------------------------------


class Project(Base):
    __tablename__ = "project"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    automation_mode: Mapped[AutomationMode] = mapped_column(
        Enum(AutomationMode), nullable=False, default=AutomationMode.MANUAL
    )
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(ProjectStatus), nullable=False, default=ProjectStatus.CREATED
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# 2. Task
# ---------------------------------------------------------------------------


class Task(Base):
    __tablename__ = "task"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"), nullable=False)
    parent_task_id: Mapped[int | None] = mapped_column(ForeignKey("task.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False, default="generic")
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus), nullable=False, default=TaskStatus.CREATED
    )
    contract: Mapped[str | None] = mapped_column(Text, nullable=True)
    playbook_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    project: Mapped["Project"] = relationship("Project")
    parent: Mapped["Task | None"] = relationship(
        "Task", remote_side=[id], back_populates="children"
    )
    children: Mapped[list["Task"]] = relationship("Task", back_populates="parent")


# ---------------------------------------------------------------------------
# 3. TaskDependency
# ---------------------------------------------------------------------------


class TaskDependency(Base):
    __tablename__ = "task_dependency"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("task.id"), nullable=False)
    depends_on_id: Mapped[int] = mapped_column(ForeignKey("task.id"), nullable=False)
    dependency_type: Mapped[DependencyType] = mapped_column(
        Enum(DependencyType), nullable=False, default=DependencyType.HARD
    )

    task: Mapped["Task"] = relationship("Task", foreign_keys=[task_id])
    depends_on: Mapped["Task"] = relationship("Task", foreign_keys=[depends_on_id])


# ---------------------------------------------------------------------------
# 4. TaskRun
# ---------------------------------------------------------------------------


class TaskRun(Base):
    __tablename__ = "task_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("task.id"), nullable=False)
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus), nullable=False, default=TaskStatus.RUNNING
    )
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    task: Mapped["Task"] = relationship("Task")


# ---------------------------------------------------------------------------
# 5. WorkerRuntime
# ---------------------------------------------------------------------------


class WorkerRuntime(Base):
    __tablename__ = "worker_runtime"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    worker_type: Mapped[str] = mapped_column(String(64), nullable=False)
    capability: Mapped[str | None] = mapped_column(String(256), nullable=True)
    health: Mapped[WorkerHealth] = mapped_column(
        Enum(WorkerHealth), nullable=False, default=WorkerHealth.UNKNOWN
    )
    config: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# 6. WorkerInstance
# ---------------------------------------------------------------------------


class WorkerInstance(Base):
    __tablename__ = "worker_instance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    worker_runtime_id: Mapped[int] = mapped_column(
        ForeignKey("worker_runtime.id"), nullable=False
    )
    task_run_id: Mapped[int | None] = mapped_column(ForeignKey("task_run.id"), nullable=True)
    status: Mapped[WorkerInstanceStatus] = mapped_column(
        Enum(WorkerInstanceStatus), nullable=False, default=WorkerInstanceStatus.STARTING
    )
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    stopped_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)

    worker_runtime: Mapped["WorkerRuntime"] = relationship("WorkerRuntime")
    task_run: Mapped["TaskRun | None"] = relationship("TaskRun")


# ---------------------------------------------------------------------------
# 7. Artifact
# ---------------------------------------------------------------------------


class Artifact(Base):
    __tablename__ = "artifact"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    logical_artifact_id: Mapped[str] = mapped_column(String(256), nullable=False)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"), nullable=False)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("task.id"), nullable=True)
    artifact_type: Mapped[ArtifactType] = mapped_column(
        Enum(ArtifactType), nullable=False, default=ArtifactType.FILE
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    project: Mapped["Project"] = relationship("Project")
    task: Mapped["Task | None"] = relationship("Task")


# ---------------------------------------------------------------------------
# 8. Issue
# ---------------------------------------------------------------------------


class Issue(Base):
    __tablename__ = "issue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"), nullable=False)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("task.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[IssueSeverity] = mapped_column(
        Enum(IssueSeverity), nullable=False, default=IssueSeverity.MEDIUM
    )
    status: Mapped[IssueStatus] = mapped_column(
        Enum(IssueStatus), nullable=False, default=IssueStatus.OPEN
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    project: Mapped["Project"] = relationship("Project")
    task: Mapped["Task | None"] = relationship("Task")


# ---------------------------------------------------------------------------
# 9. Decision
# ---------------------------------------------------------------------------


class Decision(Base):
    __tablename__ = "decision"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[DecisionStatus] = mapped_column(
        Enum(DecisionStatus), nullable=False, default=DecisionStatus.PROPOSED
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    project: Mapped["Project"] = relationship("Project")


# ---------------------------------------------------------------------------
# 10. Constraint
# ---------------------------------------------------------------------------


class Constraint(Base):
    __tablename__ = "constraint"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"), nullable=False)
    constraint_type: Mapped[ConstraintType] = mapped_column(
        Enum(ConstraintType), nullable=False, default=ConstraintType.TECHNICAL
    )
    value: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    project: Mapped["Project"] = relationship("Project")


# ---------------------------------------------------------------------------
# 11. GateResult
# ---------------------------------------------------------------------------


class GateResult(Base):
    __tablename__ = "gate_result"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("task.id"), nullable=False)
    gate_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[GateStatus] = mapped_column(
        Enum(GateStatus), nullable=False, default=GateStatus.PASS
    )
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    task: Mapped["Task"] = relationship("Task")


# ---------------------------------------------------------------------------
# 12. ManualAction
# ---------------------------------------------------------------------------


class ManualAction(Base):
    __tablename__ = "manual_action"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"), nullable=False)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("task.id"), nullable=True)
    action_type: Mapped[ManualActionType] = mapped_column(
        Enum(ManualActionType), nullable=False
    )
    proposal_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ManualActionStatus] = mapped_column(
        Enum(ManualActionStatus), nullable=False, default=ManualActionStatus.PENDING
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    confirmed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    confirmed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)

    project: Mapped["Project"] = relationship("Project")
    task: Mapped["Task | None"] = relationship("Task")


# ---------------------------------------------------------------------------
# 13. HumanDecision
# ---------------------------------------------------------------------------


class HumanDecision(Base):
    __tablename__ = "human_decision"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"), nullable=False)
    manual_action_id: Mapped[int | None] = mapped_column(
        ForeignKey("manual_action.id"), nullable=True
    )
    decision: Mapped[str] = mapped_column(String(64), nullable=False)
    decided_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    project: Mapped["Project"] = relationship("Project")
    manual_action: Mapped["ManualAction | None"] = relationship("ManualAction")


# ---------------------------------------------------------------------------
# 14. Event
# ---------------------------------------------------------------------------


class Event(Base):
    __tablename__ = "event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"), nullable=False)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("task.id"), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    project: Mapped["Project"] = relationship("Project")
    task: Mapped["Task | None"] = relationship("Task")


# ---------------------------------------------------------------------------
# 15. Checkpoint
# ---------------------------------------------------------------------------


class Checkpoint(Base):
    __tablename__ = "checkpoint"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"), nullable=False)
    label: Mapped[str | None] = mapped_column(String(256), nullable=True)
    snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    project: Mapped["Project"] = relationship("Project")


# ---------------------------------------------------------------------------
# 16. DeliveryContract
# ---------------------------------------------------------------------------


class DeliveryContract(Base):
    __tablename__ = "delivery_contract"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    items: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[DeliveryContractStatus] = mapped_column(
        Enum(DeliveryContractStatus), nullable=False, default=DeliveryContractStatus.DRAFT
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    project: Mapped["Project"] = relationship("Project")


# ---------------------------------------------------------------------------
# 17. DeliveryItem
# ---------------------------------------------------------------------------


class DeliveryItem(Base):
    __tablename__ = "delivery_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = mapped_column(
        ForeignKey("delivery_contract.id"), nullable=False
    )
    requirement_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[DeliveryItemStatus] = mapped_column(
        Enum(DeliveryItemStatus), nullable=False, default=DeliveryItemStatus.PENDING
    )
    artifact_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    contract: Mapped["DeliveryContract"] = relationship("DeliveryContract")


# ---------------------------------------------------------------------------
# 18. MethodAsset (added D05)
# ---------------------------------------------------------------------------


class MethodAsset(Base):
    __tablename__ = "method_asset"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"), nullable=False)
    asset_type: Mapped[AssetType] = mapped_column(
        Enum(AssetType), nullable=False, default=AssetType.PROCESS
    )
    source_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    authority: Mapped[MethodAssetAuthority] = mapped_column(
        Enum(MethodAssetAuthority), nullable=False, default=MethodAssetAuthority.OPTIONAL
    )
    meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    project: Mapped["Project"] = relationship("Project")


# ---------------------------------------------------------------------------
# 19. ProjectProcedureModel (added D06)
# ---------------------------------------------------------------------------


class ProjectProcedureModel(Base):
    __tablename__ = "project_procedure_model"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source_map: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    project: Mapped["Project"] = relationship("Project")


# ---------------------------------------------------------------------------
# 20. ProcedureNode (added D06)
# ---------------------------------------------------------------------------


class ProcedureNode(Base):
    __tablename__ = "procedure_node"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_id: Mapped[int] = mapped_column(
        ForeignKey("project_procedure_model.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    workstream: Mapped[str | None] = mapped_column(String(256), nullable=True)
    inputs: Mapped[str | None] = mapped_column(Text, nullable=True)
    activities: Mapped[str | None] = mapped_column(Text, nullable=True)
    outputs: Mapped[str | None] = mapped_column(Text, nullable=True)
    roles: Mapped[str | None] = mapped_column(Text, nullable=True)
    dependencies: Mapped[str | None] = mapped_column(Text, nullable=True)
    gate: Mapped[str | None] = mapped_column(Text, nullable=True)
    acceptance_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_requirements: Mapped[str | None] = mapped_column(Text, nullable=True)
    constraints: Mapped[str | None] = mapped_column(Text, nullable=True)
    rework_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    readiness_status: Mapped[ReadinessStatus] = mapped_column(
        Enum(ReadinessStatus), nullable=False, default=ReadinessStatus.MISSING
    )
    source_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    provenance: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    model: Mapped["ProjectProcedureModel"] = relationship("ProjectProcedureModel")
