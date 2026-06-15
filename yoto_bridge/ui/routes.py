"""HTML routes for the bridge UI.

The JSON API at top-level (`/healthz`, `/players/*`, `/schedule`, `/auth/*`)
stays canonical. These UI routes either serve Jinja-rendered pages or wrap the
client/scheduler in form-friendly handlers (returning 204 / 303 redirects)
so that progressive enhancement works even without HTMX.
"""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import auth, storage

_THIS_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(_THIS_DIR / "templates"))


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
    ("Library",  "/ui/library",  False),
    ("Groups",   "/ui/groups",   False),
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


@router.get("/ui/library", response_class=HTMLResponse)
async def library_page(request: Request) -> Any:
    s = _state(request)
    return templates.TemplateResponse(
        request,
        "pages/library.html",
        {"nav": _nav("/ui/library"), "authorized": s.authorized},
    )


@router.get("/ui/groups", response_class=HTMLResponse)
async def groups_page(request: Request) -> Any:
    s = _state(request)
    return templates.TemplateResponse(
        request,
        "pages/groups.html",
        {"nav": _nav("/ui/groups"), "authorized": s.authorized},
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
    flow_view = None
    if s.auth_flow is not None and s.auth_flow.state == "pending":
        flow_view = {
            "verification_uri_complete": s.auth_flow.verification_uri,
            "user_code": s.auth_flow.user_code,
        }
    return templates.TemplateResponse(
        request,
        "pages/settings.html",
        {
            "nav": _nav("/ui/settings"),
            "authorized": s.authorized,
            "player_count": len(s.client.players) if s.authorized else 0,
            "mqtt_connected": s.client.is_mqtt_connected if s.authorized else False,
            "auth_flow": flow_view,
        },
    )


# --- partials (HTMX) -------------------------------------------------------


@router.get("/ui/partials/health", response_class=HTMLResponse)
async def health_partial(request: Request) -> HTMLResponse:
    s = _state(request)
    if s.authorized:
        mqtt = "MQTT ok" if s.client.is_mqtt_connected else "MQTT down"
        return HTMLResponse(f'<span class="dot dot-on"></span> linked · {len(s.client.players)} player(s) · {mqtt}')
    return HTMLResponse('<span class="dot dot-off"></span> not linked')


@router.get("/ui/partials/auth-status", response_class=HTMLResponse)
async def auth_status_partial(request: Request) -> HTMLResponse:
    """Returns inner HTML for the `.auth-status` pill on the settings page.

    Shape: an inline-flex container expects an icon/spinner + a text element.
    """
    s = _state(request)
    if s.authorized:
        return HTMLResponse(
            '<sl-icon name="check-circle-fill" style="color:var(--ys-success);"></sl-icon>'
            '<span><strong>Linked.</strong> Reloading…</span>'
            '<script>setTimeout(()=>location.reload(),500)</script>'
        )
    if s.auth_flow is None:
        return HTMLResponse(
            '<sl-icon name="dash-circle" style="color:var(--ys-muted);"></sl-icon>'
            '<span>No authorisation in progress.</span>'
        )
    if s.auth_flow.state == "linked":
        from ..app import _finalise_auth_flow
        await _finalise_auth_flow(s)
        return HTMLResponse(
            '<sl-icon name="check-circle-fill" style="color:var(--ys-success);"></sl-icon>'
            '<span><strong>Linked.</strong> Reloading…</span>'
            '<script>setTimeout(()=>location.reload(),500)</script>'
        )
    if s.auth_flow.state == "error":
        return HTMLResponse(
            '<sl-icon name="exclamation-circle-fill" style="color:var(--ys-error);"></sl-icon>'
            f'<span>Error: {s.auth_flow.error}</span>'
        )
    return HTMLResponse(
        '<sl-spinner></sl-spinner>'
        '<span>Waiting for you to authorise…</span>'
    )


# --- auth form actions -----------------------------------------------------


@router.post("/ui/auth/start")
async def ui_auth_start(request: Request) -> RedirectResponse:
    s = _state(request)
    if not s.authorized and (s.auth_flow is None or s.auth_flow.state != "pending"):
        try:
            s.auth_flow = await auth.start_flow()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, str(e))
    return RedirectResponse("/ui/settings", status_code=303)


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


