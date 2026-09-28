"""aiohttp apps: the LAN join portal and the ingress admin panel."""

import io
import logging
import re
from urllib.parse import urlparse

import segno
from aiohttp import web

from . import pages
from .cloudflare import CloudflareError
from .service import Household, UserError
from .setup import STEPS, Setup

_LOGGER = logging.getLogger(__name__)

HOUSEHOLD = web.AppKey("household", Household)
SETUP = web.AppKey("setup", Setup)
SUBDOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
INGRESS_PROXY_IP = "172.30.32.2"
PORTAL_PORT = 8099
COOKIE_PREFIX = "hh_"


def _html(body: str, status: int = 200) -> web.Response:
    return web.Response(text=body, status=status, content_type="text/html",
                        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


def _detect_platform(request: web.Request) -> str:
    ua = request.headers.get("User-Agent", "")
    if any(k in ua for k in ("iPhone", "iPad", "iPod")):
        return "ios"
    if "Android" in ua:
        return "android"
    if "Macintosh" in ua and "Mobile" in ua:  # iPadOS pretending to be a Mac
        return "ios"
    return "other"


# -- portal --------------------------------------------------------------------


async def portal_home(request: web.Request) -> web.Response:
    return _html(pages.portal_home())


async def join_get(request: web.Request) -> web.Response:
    return _html(pages.join_form({"platform": _detect_platform(request)}))


async def join_post(request: web.Request) -> web.Response:
    form = await request.post()
    values = {k: str(form.get(k, "")) for k in ("name", "username", "password", "device", "platform")}
    hh = request.app[HOUSEHOLD]
    try:
        request_id, token = await hh.request_join(
            values["name"], values["username"], values["password"], values["device"], values["platform"]
        )
    except UserError as err:
        return _html(pages.join_form(values, str(err)), 400)
    raise _to_request(request_id, token)


async def device_get(request: web.Request) -> web.Response:
    return _html(pages.device_form({"platform": _detect_platform(request)}))


async def device_post(request: web.Request) -> web.Response:
    form = await request.post()
    values = {k: str(form.get(k, "")) for k in ("username", "password", "device", "platform")}
    hh = request.app[HOUSEHOLD]
    try:
        request_id, token = await hh.request_device(
            values["username"], values["password"], values["device"], values["platform"]
        )
    except UserError as err:
        return _html(pages.device_form(values, str(err)), 400)
    raise _to_request(request_id, token)


def _to_request(request_id: str, token: str) -> web.HTTPSeeOther:
    resp = web.HTTPSeeOther(f"/r/{request_id}")
    resp.set_cookie(f"{COOKIE_PREFIX}{request_id}", token, max_age=7 * 24 * 3600,
                    httponly=True, samesite="Strict", path="/r/")
    return resp


def _own_request(request: web.Request) -> dict:
    request_id = request.match_info["id"]
    found = request.app[HOUSEHOLD].request_for(request_id, request.cookies.get(f"{COOKIE_PREFIX}{request_id}"))
    if not found:
        raise web.HTTPNotFound(text="Request not found. Open this page on the device that asked to join.")
    return found


async def request_view(request: web.Request) -> web.Response:
    hh = request.app[HOUSEHOLD]
    return _html(pages.request_page(_own_request(request), hh.external_url))


async def request_status(request: web.Request) -> web.Response:
    return web.json_response({"status": _own_request(request)["status"]}, headers={"Cache-Control": "no-store"})


async def request_bundle(request: web.Request) -> web.Response:
    req = _own_request(request)
    path = request.app[HOUSEHOLD].store.bundle_path(req["id"])
    if req["status"] != "issued" or not path.exists():
        raise web.HTTPGone(text="This download has expired.")
    # Android: x-pkcs12 hands the file straight to the certificate installer.
    # iOS: Safari would treat x-pkcs12 as a system profile, which the HA app can't
    # use, so send it as a plain download that lands in Files.
    content_type = "application/x-pkcs12" if req["platform"] == "android" else "application/octet-stream"
    return web.Response(
        body=path.read_bytes(),
        content_type=content_type,
        headers={"Content-Disposition": 'attachment; filename="household.p12"', "Cache-Control": "no-store"},
    )


def portal_app(hh: Household) -> web.Application:
    app = web.Application()
    app[HOUSEHOLD] = hh
    app.add_routes(
        [
            web.get("/", portal_home),
            web.get("/join", join_get),
            web.post("/join", join_post),
            web.get("/device", device_get),
            web.post("/device", device_post),
            web.get("/r/{id}", request_view),
            web.get("/r/{id}/status", request_status),
            web.get("/r/{id}/household.p12", request_bundle),
        ]
    )
    return app


# -- admin (ingress) -------------------------------------------------------------


@web.middleware
async def ingress_only(request: web.Request, handler):
    if request.remote != INGRESS_PROXY_IP:
        raise web.HTTPForbidden(text="Admin panel is only reachable through Home Assistant.")
    return await handler(request)


def _base(request: web.Request) -> str:
    return request.headers.get("X-Ingress-Path", "").rstrip("/")


async def _portal_urls(hh: Household) -> list[str]:
    urls = []
    try:
        internal = (await hh.ha.command({"type": "network/url"})).get("internal")
        if internal:
            host = urlparse(internal).hostname
            urls.append(f"http://{host}:{PORTAL_PORT}")
    except Exception:  # noqa: BLE001
        pass
    fallback = f"http://homeassistant.local:{PORTAL_PORT}"
    if fallback not in urls:
        urls.append(fallback)
    return urls


def _qr_svg(url: str) -> str:
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="svg", border=0, xmldecl=False, svgns=True, nl=False, omitsize=True)
    return buf.getvalue().decode()


async def admin_home(request: web.Request) -> web.Response:
    hh, setup = request.app[HOUSEHOLD], request.app[SETUP]
    if not setup.complete:
        raise web.HTTPSeeOther(f"{_base(request)}/setup")
    urls = await _portal_urls(hh)
    status = {
        f"Remote access: {hh.external_url}": True,
        "Tunnel connected": setup.tunnel.connected,
        "Push approvals (notify_service set)": bool((hh.options.get("notify_service") or "").strip()),
    }
    members = sorted(hh.store.members.values(), key=lambda m: (not m["active"], m["name"].lower()))
    errors = [r for r in hh.store.requests.values() if r["status"] == "error"]
    return _html(pages.admin_page(_base(request), hh.store.pending(), members, errors, urls, _qr_svg(urls[0]), status))


def _back(request: web.Request) -> web.HTTPSeeOther:
    return web.HTTPSeeOther(f"{_base(request)}/")


async def admin_approve(request: web.Request):
    await request.app[HOUSEHOLD].approve(request.match_info["id"])
    raise _back(request)


async def admin_deny(request: web.Request):
    await request.app[HOUSEHOLD].deny(request.match_info["id"])
    raise _back(request)


async def admin_revoke(request: web.Request):
    await request.app[HOUSEHOLD].revoke_device(request.match_info["user_id"], request.match_info["cert_id"])
    raise _back(request)


async def admin_remove(request: web.Request):
    await request.app[HOUSEHOLD].remove_member(request.match_info["user_id"])
    raise _back(request)


async def admin_restore(request: web.Request):
    await request.app[HOUSEHOLD].restore_member(request.match_info["user_id"])
    raise _back(request)


# -- setup wizard ----------------------------------------------------------------


async def setup_view(request: web.Request) -> web.Response:
    setup = request.app[SETUP]
    base, settings = _base(request), setup.settings
    if not settings.get("token"):
        return _html(pages.wizard_connect(base))
    if not settings.get("hostname"):
        try:
            zones = await setup.cf.list_zones()
        except CloudflareError as err:
            return _html(pages.wizard_connect(base, f"Cloudflare rejected the token: {err}"))
        if not zones:
            return _html(pages.wizard_connect(base, "This token can't see any active domains. "
                                                    "Check the token's Zone Resources, or add a domain to Cloudflare first."))
        return _html(pages.wizard_domain(base, zones))
    return _html(pages.setup_page(base, settings["hostname"], STEPS, setup.results, setup.running, setup.complete))


async def setup_token(request: web.Request):
    setup = request.app[SETUP]
    token = str((await request.post()).get("token", "")).strip()
    setup.cf.configure(token)
    if not token or not await setup.cf.verify_token():
        return _html(pages.wizard_connect(_base(request), "That token isn't valid or active. Copy it again from Cloudflare."), 400)
    for key in ("zone_id", "zone_name", "account_id", "hostname", "setup_results", "setup_complete"):
        setup.settings.pop(key, None)
    setup.settings["token"] = token
    setup.settings.save()
    raise web.HTTPSeeOther(f"{_base(request)}/setup")


async def setup_domain(request: web.Request):
    setup = request.app[SETUP]
    form = await request.post()
    subdomain = str(form.get("subdomain", "")).strip().lower()
    zones = {z["id"]: z for z in await setup.cf.list_zones()}
    zone = zones.get(str(form.get("zone_id", "")))
    if not zone or not SUBDOMAIN_RE.match(subdomain):
        return _html(pages.wizard_domain(_base(request), list(zones.values()), subdomain,
                                         "Use letters, numbers and dashes for the address, e.g. “ha”."), 400)
    hostname = f"{subdomain}.{zone['name']}"
    setup.settings.update(zone_id=zone["id"], zone_name=zone["name"], account_id=zone["account_id"], hostname=hostname)
    setup.settings.save()
    setup.cf.configure(setup.settings["token"], zone["account_id"], zone["id"], hostname)
    raise web.HTTPSeeOther(f"{_base(request)}/setup")


async def setup_run(request: web.Request):
    setup = request.app[SETUP]
    if setup.cf.configured:
        setup.start()
    raise web.HTTPSeeOther(f"{_base(request)}/setup")


async def setup_reset(request: web.Request):
    """Forget token and address. Cloudflare resources, members and certificates stay as they are."""
    setup = request.app[SETUP]
    for key in ("token", "zone_id", "zone_name", "account_id", "hostname", "setup_results", "setup_complete"):
        setup.settings.pop(key, None)
    setup.settings.save()
    setup.cf.configure("")
    raise web.HTTPSeeOther(f"{_base(request)}/setup")


def admin_app(hh: Household, setup: Setup) -> web.Application:
    app = web.Application(middlewares=[ingress_only])
    app[HOUSEHOLD] = hh
    app[SETUP] = setup
    app.add_routes(
        [
            web.get("/", admin_home),
            web.get("/setup", setup_view),
            web.post("/setup/token", setup_token),
            web.post("/setup/domain", setup_domain),
            web.post("/setup/run", setup_run),
            web.post("/setup/reset", setup_reset),
            web.post("/approve/{id}", admin_approve),
            web.post("/deny/{id}", admin_deny),
            web.post("/revoke/{user_id}/{cert_id}", admin_revoke),
            web.post("/remove/{user_id}", admin_remove),
            web.post("/restore/{user_id}", admin_restore),
        ]
    )
    return app
