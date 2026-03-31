"""OIDC / OAuth2 SSO login, callback, logout."""

import os

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from .auth_db import (
    add_user,
    get_user_by_email,
    is_auth_enabled,
    normalize_email,
    user_count,
)

router = APIRouter(tags=["auth"])


@router.get("/api/auth/status")
async def auth_status(request: Request):
    auth_on = is_auth_enabled()
    user = request.session.get("user")
    if not auth_on:
        # Match require_principal anonymous admin so the UI can show admin tools.
        user = {"id": -1, "email": None, "role": "admin", "anonymous": True}
    return {
        "auth_enabled": auth_on,
        "user": user,
        "oidc_configured": is_oidc_configured(),
    }

oauth = OAuth()
_oauth_registered = False


def ensure_oauth_registered() -> bool:
    global _oauth_registered
    if _oauth_registered:
        return True
    issuer = os.environ.get("OIDC_ISSUER", "").strip().rstrip("/")
    client_id = os.environ.get("OIDC_CLIENT_ID", "").strip()
    client_secret = os.environ.get("OIDC_CLIENT_SECRET", "").strip() or None
    if not issuer or not client_id:
        return False
    meta = f"{issuer}/.well-known/openid-configuration"
    oauth.register(
        name="oidc",
        server_metadata_url=meta,
        client_id=client_id,
        client_secret=client_secret,
        client_kwargs={"scope": "openid email profile"},
    )
    _oauth_registered = True
    return True


def is_oidc_configured() -> bool:
    return bool(
        os.environ.get("OIDC_ISSUER", "").strip()
        and os.environ.get("OIDC_CLIENT_ID", "").strip()
    )


def oauth_redirect_uri(request: Request) -> str:
    """Callback URL sent to the IdP. Must match an allowed redirect URI exactly."""
    explicit = os.environ.get("OIDC_REDIRECT_URI", "").strip()
    if explicit:
        uri = explicit
    else:
        uri = str(request.url_for("oidc_callback"))
    return uri.rstrip("/")


@router.get("/auth/sso")
async def sso_start(request: Request):
    if not is_auth_enabled():
        return RedirectResponse("/")
    if not ensure_oauth_registered():
        raise HTTPException(
            status_code=503,
            detail="OIDC not configured. Set OIDC_ISSUER and OIDC_CLIENT_ID.",
        )
    return await oauth.oidc.authorize_redirect(request, oauth_redirect_uri(request))


@router.get("/auth/callback", name="oidc_callback")
async def oidc_callback(request: Request):
    if not is_auth_enabled():
        return RedirectResponse("/")
    if not ensure_oauth_registered():
        raise HTTPException(status_code=503, detail="OIDC not configured")
    try:
        token = await oauth.oidc.authorize_access_token(request)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"OAuth error: {e}") from e

    userinfo = token.get("userinfo") or {}
    email = userinfo.get("email")
    if not email:
        raise HTTPException(
            status_code=400,
            detail="Identity provider did not return an email. Check scopes (openid email).",
        )
    email = normalize_email(email)

    row = get_user_by_email(email)
    if not row:
        bootstrap = os.environ.get("OIDC_BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
        if user_count() == 0 and bootstrap and email == bootstrap:
            row = add_user(email, "admin")
        else:
            return RedirectResponse(
                "/login?error=not_registered",
                status_code=302,
            )

    request.session["user"] = {
        "id": row["id"],
        "email": row["email"],
        "role": row["role"],
    }
    return RedirectResponse("/")


@router.get("/auth/logout")
async def logout(request: Request):
    request.session.clear()
    if is_auth_enabled():
        return RedirectResponse("/login")
    return RedirectResponse("/")
