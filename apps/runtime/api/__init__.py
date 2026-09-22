"""Runtime API routers."""

from apps.runtime.api.projects import router as projects_router
from apps.runtime.api.settings import router as settings_router

__all__ = ["projects_router", "settings_router"]
