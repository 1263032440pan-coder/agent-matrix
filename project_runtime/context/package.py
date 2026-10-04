"""ContextPackage — WP-010 的输出契约.

不可变快照：sections 带优先级与来源引用，token 统计可审计。
Middleware 只能产出新 ContextPackage，不得原地改（验收红线）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace

# 优先级: P0 永不裁剪（验收红线），P3 最先丢
P0 = 0  # Current Task / Acceptance / Constraint / Dependency
P1 = 1  # Decision / Summary / Direct Artifact
P2 = 2  # Retrieval Result / Recent Evidence
P3 = 3  # 非关键历史


@dataclass(frozen=True)
class ContextSection:
    priority: int          # P0..P3
    kind: str              # task / acceptance / constraint / dependency /
                           # summary / artifact / retrieval / evidence / history
    content: str
    source_ref: str        # 溯源（task:{id} / artifact:{id} / retrieval:{q} ...）
    tokens: int = 0

    def to_dict(self) -> dict:
        return {
            "priority": self.priority,
            "kind": self.kind,
            "content": self.content,
            "source_ref": self.source_ref,
            "tokens": self.tokens,
        }


@dataclass(frozen=True)
class ContextPackage:
    project_id: int
    task_id: int
    sections: tuple[ContextSection, ...] = ()
    budget_tokens: int = 0
    truncated: bool = False
    dropped_kinds: tuple[str, ...] = ()

    @property
    def total_tokens(self) -> int:
        return sum(s.tokens for s in self.sections)

    @property
    def p0_sections(self) -> tuple[ContextSection, ...]:
        return tuple(s for s in self.sections if s.priority == P0)

    def to_json(self) -> str:
        return json.dumps(
            {
                "project_id": self.project_id,
                "task_id": self.task_id,
                "budget_tokens": self.budget_tokens,
                "truncated": self.truncated,
                "total_tokens": self.total_tokens,
                "dropped_kinds": list(self.dropped_kinds),
                "sections": [s.to_dict() for s in self.sections],
            },
            ensure_ascii=False,
        )

    # P0 内部构造序：当前任务最先，验收/约束随后，依赖收尾
    _KIND_ORDER = {"task": 0, "acceptance": 1, "constraint": 2,
                   "dependency": 3}

    def render(self) -> str:
        """按优先级渲染为给 Worker 的上下文正文（P0 内部按构造序——
        「当前任务」恒为第一行）。"""
        lines: list[str] = []
        for s in sorted(
            self.sections,
            key=lambda x: (x.priority, self._KIND_ORDER.get(x.kind, 99), x.kind),
        ):
            lines.append(s.content)
        return "\n\n".join(lines)

    def with_sections(
        self, sections: tuple[ContextSection, ...], **updates
    ) -> "ContextPackage":
        """Middleware 的唯一合法变换：产出新 package（frozen dataclass，
        原地修改在类型层就不可能）。"""
        return replace(self, sections=sections, **updates)
