"""HTML routes for the bridge UI.

The JSON API at top-level (`/healthz`, `/players/*`, `/schedule`, `/auth/*`)
stays canonical. These UI routes either serve Jinja-rendered pages or wrap the
client/scheduler in form-friendly handlers (returning 204 / 303 redirects)
so that progressive enhancement works even without HTMX.
"""

import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import auth, config, storage

_THIS_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(_THIS_DIR / "templates"))
# Bumps every process start — a container restart invalidates browser caches
# for app.css without forcing users to hard-refresh.
templates.env.globals["static_version"] = str(int(time.time()))


def _hms(seconds: Any) -> str:
    if seconds is None:
        return ""
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


templates.env.filters["hms"] = _hms


NAV_ITEMS = [
    ("Routines", "/ui/routines", False),
    ("Events",   "/ui/events",   False),
    ("Logs",     "/ui/logs",     False),
    ("Settings", "/ui/settings", False),
]


def _nav(active_href: str) -> list[dict]:
    return [
        {"label": label, "href": href, "active": href == active_href, "stub": stub}
        for label, href, stub in NAV_ITEMS
    ]


def _state(request: Request) -> Any:
    return request.app.state.yoto


router = APIRouter()


# --- top-level redirect ----------------------------------------------------


@router.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse("/ui/routines")


@router.get("/ui/", include_in_schema=False)
async def ui_root() -> RedirectResponse:
    return RedirectResponse("/ui/routines")


# --- pages -----------------------------------------------------------------


@router.get("/ui/routines", response_class=HTMLResponse)
async def routines_page(request: Request) -> Any:
    s = _state(request)
    if not s.authorized or s.sched is None:
        return templates.TemplateResponse(
            request,
            "pages/routines.html",
            {"nav": _nav("/ui/routines"), "authorized": False},
        )
    return templates.TemplateResponse(
        request,
        "pages/routines.html",
        {
            "nav": _nav("/ui/routines"),
            "authorized": True,
            "config": s.sched.cfg.model_dump(),
            "known_devices": [
                {"device_id": d, "name": p.name} for d, p in s.client.players.items()
            ],
            "live": {d: s.sched.status_for(d) for d in s.client.players},
        },
    )


@router.get("/ui/logs", response_class=HTMLResponse)
async def logs_page(request: Request) -> Any:
    s = _state(request)
    return templates.TemplateResponse(
        request,
        "pages/logs.html",
        {"nav": _nav("/ui/logs"), "authorized": s.authorized},
    )


@router.get("/ui/events", response_class=HTMLResponse)
async def events_page(request: Request) -> Any:
    s = _state(request)
    return templates.TemplateResponse(
        request,
        "pages/events.html",
        {"nav": _nav("/ui/events"), "authorized": s.authorized},
    )


@router.get("/ui/settings", response_class=HTMLResponse)
async def settings_page(request: Request) -> Any:
    s = _state(request)
    auth_error = None
    if not s.authorized and s.auth_flow is not None and s.auth_flow.state == "error":
        auth_error = s.auth_flow.error
    return templates.TemplateResponse(
        request,
        "pages/settings.html",
        {
            "nav": _nav("/ui/settings"),
            "authorized": s.authorized,
            "player_count": len(s.client.players) if s.authorized else 0,
            "mqtt_connected": s.client.is_mqtt_connected if s.authorized else False,
            "auth_error": auth_error,
            "redirect_uri_configured": bool(config.REDIRECT_URI),
            "client_id_configured": bool(config.CLIENT_ID),
        },
    )


# --- partials (HTMX) -------------------------------------------------------


@router.get("/ui/partials/health", response_class=HTMLResponse)
async def health_partial(request: Request) -> HTMLResponse:
    s = _state(request)
    dry_badge = (
        '<span class="dry-run-badge" title="No mutating calls will hit Yoto">DRY-RUN</span>'
        if getattr(s.client, "dry_run", False) else ''
    )
    if s.authorized:
        mqtt = "MQTT ok" if s.client.is_mqtt_connected else "MQTT down"
        return HTMLResponse(
            f'{dry_badge}<span class="dot dot-on"></span> linked · '
            f'{len(s.client.players)} player(s) · {mqtt}'
        )
    return HTMLResponse(f'{dry_badge}<span class="dot dot-off"></span> not linked')


# --- auth form actions -----------------------------------------------------


@router.post("/ui/auth/start")
async def ui_auth_start(request: Request) -> RedirectResponse:
    """Form-post target for the Connect button. Mints a fresh PKCE flow and
    303-redirects the user's browser to Yoto's /authorize endpoint."""
    s = _state(request)
    if s.authorized:
        return RedirectResponse("/ui/settings", status_code=303)
    if not config.CLIENT_ID:
        raise HTTPException(500, "YOTO_CLIENT_ID is not set")
    if not config.REDIRECT_URI:
        raise HTTPException(500, "YOTO_REDIRECT_URI is not set")
    # Always start fresh — a stale flow's verifier/state is one-shot.
    s.auth_flow = auth.start_flow()
    return RedirectResponse(s.auth_flow.authorize_url(), status_code=303)


@router.post("/ui/auth/logout")
async def ui_auth_logout(request: Request) -> RedirectResponse:
    s = _state(request)
    s.authorized = False
    if s.events_task is not None and not s.events_task.done():
        s.events_task.cancel()
    if s.sched is not None:
        await s.sched.stop()
        s.sched = None
    storage.clear()
    return RedirectResponse("/ui/settings", status_code=303)


