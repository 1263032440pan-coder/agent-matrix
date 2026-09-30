"""Research Manager & Gap Resolution (WP-029)."""

from project_runtime.research.models import ResearchRecord
from project_runtime.research.router import (
    ROUTE_TABLE,
    CheckReport,
    GapResolver,
    Resolution,
)

__all__ = ["CheckReport", "GapResolver", "ResearchRecord", "Resolution",
           "ROUTE_TABLE"]
