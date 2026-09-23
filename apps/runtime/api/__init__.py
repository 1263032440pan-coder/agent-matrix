"""Runtime API routers."""

from apps.runtime.api.executions import router as executions_router
from apps.runtime.api.projects import router as projects_router
from apps.runtime.api.settings import router as settings_router

__all__ = ["executions_router", "projects_router", "settings_router"]
