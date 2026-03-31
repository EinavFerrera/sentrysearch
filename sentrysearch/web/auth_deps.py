"""FastAPI dependencies for session-based SSO and role checks."""

from collections.abc import Callable

from fastapi import Depends, HTTPException, Request

from .auth_db import is_auth_enabled


async def require_principal(request: Request) -> dict:
    """Current user, or synthetic admin when SSO is disabled."""
    if not is_auth_enabled():
        return {"id": -1, "email": None, "role": "admin", "anonymous": True}
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def require_roles(*roles: str) -> Callable:
    """Require one of the given roles (viewer | user | admin)."""

    async def dep(principal: dict = Depends(require_principal)) -> dict:
        if principal.get("anonymous"):
            return principal
        if principal.get("role") not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return principal

    return dep


require_viewer = require_roles("viewer", "user", "admin")
require_writer = require_roles("user", "admin")
require_admin = require_roles("admin")


async def require_search_access(principal: dict = Depends(require_viewer)) -> dict:
    """Search, stats, library read, clips read, video stream."""
    return principal


async def require_write_access(principal: dict = Depends(require_writer)) -> dict:
    """Upload, index, delete from index, trim/save clips."""
    return principal
