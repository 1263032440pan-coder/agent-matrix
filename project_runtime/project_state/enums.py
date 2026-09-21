"""Enum types shared across the Project State domain."""

import enum


class AutomationMode(str, enum.Enum):
    FULL_AUTO = "FULL_AUTO"
    MANUAL = "MANUAL"


class ProjectStatus(str, enum.Enum):
    CREATED = "CREATED"
    INITIALIZING = "INITIALIZING"
    ACTIVE = "ACTIVE"
    BLOCKED = "BLOCKED"
    COMPLETING = "COMPLETING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class TaskStatus(str, enum.Enum):
    CREATED = "CREATED"
    READY = "READY"
    RUNNING = "RUNNING"
    EXECUTION_DONE = "EXECUTION_DONE"
    REWORK = "REWORK"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    ACCEPTED = "ACCEPTED"
    CANCELLED = "CANCELLED"


class DependencyType(str, enum.Enum):
    HARD = "HARD"
    SOFT = "SOFT"


class WorkerHealth(str, enum.Enum):
    UNKNOWN = "UNKNOWN"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"


class WorkerInstanceStatus(str, enum.Enum):
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    IDLE = "IDLE"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class ArtifactType(str, enum.Enum):
    FILE = "FILE"
    CODE = "CODE"
    DOCUMENT = "DOCUMENT"
    TEST_RESULT = "TEST_RESULT"
    RESEARCH = "RESEARCH"
    EVIDENCE = "EVIDENCE"
    OTHER = "OTHER"


class IssueSeverity(str, enum.Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class IssueStatus(str, enum.Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class DecisionStatus(str, enum.Enum):
    PROPOSED = "PROPOSED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class ConstraintType(str, enum.Enum):
    SECURITY = "SECURITY"
    COMPLIANCE = "COMPLIANCE"
    RESOURCE = "RESOURCE"
    BUDGET = "BUDGET"
    SCHEDULE = "SCHEDULE"
    TECHNICAL = "TECHNICAL"


class GateStatus(str, enum.Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    NEED_APPROVAL = "NEED_APPROVAL"


class ManualActionStatus(str, enum.Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class ManualActionType(str, enum.Enum):
    TASK_DISPATCH = "TASK_DISPATCH"
    APPLY_REPLAN = "APPLY_REPLAN"
    FINAL_DELIVERY = "FINAL_DELIVERY"
    EXTERNAL_WRITE = "EXTERNAL_WRITE"


class DeliveryContractStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    SATISFIED = "SATISFIED"
    CHANGED = "CHANGED"


class DeliveryItemStatus(str, enum.Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    SATISFIED = "SATISFIED"
    WAIVED = "WAIVED"


class AssetType(str, enum.Enum):
    PROCESS = "PROCESS"
    SOP = "SOP"
    STANDARD = "STANDARD"
    TEMPLATE = "TEMPLATE"
    HISTORICAL_REFERENCE = "HISTORICAL_REFERENCE"
    ACCEPTANCE_RULE = "ACCEPTANCE_RULE"
    ROLE_DEFINITION = "ROLE_DEFINITION"
    DELIVERY_RULE = "DELIVERY_RULE"


class MethodAssetAuthority(str, enum.Enum):
    MANDATORY = "MANDATORY"
    RECOMMENDED = "RECOMMENDED"
    OPTIONAL = "OPTIONAL"
    PROPOSED = "PROPOSED"


class ReadinessStatus(str, enum.Enum):
    READY = "READY"
    PARTIAL = "PARTIAL"
    AMBIGUOUS = "AMBIGUOUS"
    CONFLICTED = "CONFLICTED"
    MISSING = "MISSING"


class GapType(str, enum.Enum):
    PRIVATE = "PRIVATE"
    EXPLICITNESS = "EXPLICITNESS"
    DOMAIN = "DOMAIN"
    TECHNICAL = "TECHNICAL"
    PROCESS = "PROCESS"
    CAPABILITY = "CAPABILITY"
