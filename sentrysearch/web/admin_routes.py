"""Admin API: users, roles, auth toggle."""

import json
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ValidationError, field_validator

from .auth_db import (
    ROLES,
    add_user,
    delete_user,
    get_user_by_id,
    is_auth_enabled,
    list_users,
    set_auth_enabled,
    update_user_role,
    user_count,
)
from .auth_deps import require_admin
from .auth_routes import ensure_oauth_registered, is_oidc_configured, oauth_redirect_uri

router = APIRouter(prefix="/api/admin", tags=["admin"])


class UserCreate(BaseModel):
    email: str
    role: str

    @field_validator("email")
    @classmethod
    def email_ok(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or len(v) < 3:
            raise ValueError("invalid email")
        return v

    @field_validator("role")
    @classmethod
    def role_ok(cls, v: str) -> str:
        if v not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        return v


class UserPatch(BaseModel):
    role: str

    @field_validator("role")
    @classmethod
    def role_ok(cls, v: str) -> str:
        if v not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        return v


class AuthSettingsBody(BaseModel):
    auth_enabled: bool


async def _parse_json_model(request: Request, model: type[BaseModel]) -> BaseModel:
    """Parse JSON from the raw body (works even without Content-Type: application/json).

    FastAPI's default body parsing only treats the payload as JSON when that header is
    set; otherwise the model receives a string and Pydantic fails with a confusing error.
    """
    raw = await request.body()
    if not raw.strip():
        raise HTTPException(status_code=422, detail="Request body is required")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=422, detail=f"Invalid JSON: {e}") from e
    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail="JSON body must be an object")
    try:
        return model.model_validate(data)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors()) from e


@router.get("/config")
async def admin_config(request: Request, _: dict = Depends(require_admin)):
    oidc = is_oidc_configured()
    return {
        "auth_enabled": is_auth_enabled(),
        "oidc_configured": oidc,
        "bootstrap_hint": bool(os.environ.get("OIDC_BOOTSTRAP_ADMIN_EMAIL", "").strip()),
        "oauth_redirect_uri": oauth_redirect_uri(request) if oidc else None,
        "oauth_redirect_uri_is_explicit": bool(
            os.environ.get("OIDC_REDIRECT_URI", "").strip()
        ),
    }


@router.get("/users")
async def admin_list_users(_: dict = Depends(require_admin)):
    return {"users": list_users()}


@router.post("/users")
async def admin_add_user(request: Request, _: dict = Depends(require_admin)):
    body = await _parse_json_model(request, UserCreate)
    try:
        u = add_user(body.email, body.role)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return u


@router.patch("/users/{user_id}")
async def admin_patch_user(
    user_id: int,
    request: Request,
    principal: dict = Depends(require_admin),
):
    body = await _parse_json_model(request, UserPatch)
    if user_id == principal.get("id") and body.role != "admin":
        raise HTTPException(
            status_code=400,
            detail="You cannot remove your own administrator role.",
        )
    u = update_user_role(user_id, body.role)
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    return u


@router.delete("/users/{user_id}")
async def admin_delete_user(user_id: int, principal: dict = Depends(require_admin)):
    if user_id == principal.get("id"):
        raise HTTPException(status_code=400, detail="Cannot remove yourself")
    if user_count() <= 1:
        raise HTTPException(status_code=400, detail="Cannot remove the last user")
    if not delete_user(user_id):
        raise HTTPException(status_code=404, detail="User not found")
    return {"ok": True}


@router.patch("/settings/auth")
async def admin_set_auth(request: Request, _: dict = Depends(require_admin)):
    body = await _parse_json_model(request, AuthSettingsBody)
    if body.auth_enabled and not is_oidc_configured():
        raise HTTPException(
            status_code=400,
            detail="Cannot enable SSO without OIDC_ISSUER and OIDC_CLIENT_ID.",
        )
    if body.auth_enabled:
        ensure_oauth_registered()
    set_auth_enabled(body.auth_enabled)
    return {"auth_enabled": is_auth_enabled()}
