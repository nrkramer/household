"""One-click setup: Cloudflare tunnel, DNS, mTLS, firewall, TURN relay, HA proxy trust, and a self-test.

Every step is idempotent, so "Run setup" doubles as "repair". Steps that need a Home
Assistant restart record where they were, and setup resumes when HA comes back.
"""

import asyncio
import hashlib
import ipaddress
import logging
import shutil
import ssl
import tempfile
import time
from pathlib import Path

import aiohttp
from cryptography.hazmat.primitives import serialization

from .certs import new_key_and_csr
from .cloudflare import Cloudflare, CloudflareError
from .ha import HAError, HomeAssistant
from .store import Settings
from .tunnel import Tunnel

_LOGGER = logging.getLogger(__name__)

ADDON_NETWORK = ipaddress.ip_network("172.30.33.0/24")  # where add-ons (and so cloudflared) live
TURN_DOMAIN = "cloudflare_turn"
INTEGRATION_SRC = Path("/app/integration") / TURN_DOMAIN
INTEGRATION_DST = Path("/homeassistant/custom_components") / TURN_DOMAIN
HTTP_META_KEYS = ("created_at", "error", "error_message")
REQUIRED_PERMISSIONS = (
    "Zone: Read", "DNS: Edit", "SSL and Certificates: Edit", "Zone WAF: Edit", "Cloudflare Tunnel: Edit",
)
TURN_PERMISSION = "Realtime (TURN): Edit"

# The token row each Cloudflare step needs, for "Cloudflare refused" messages.
STEP_PERMISSION = {
    "tunnel": "Account · Cloudflare Tunnel · Edit",
    "dns": "Zone · DNS · Edit",
    "mtls": "Zone · SSL and Certificates · Edit",
    "firewall": "Zone · Zone WAF · Edit",
    "turn": "Account · Cloudflare Calls · Edit",
    "test": "Zone · SSL and Certificates · Edit",
}

STEPS = {
    "permissions": "Cloudflare token permissions",
    "tunnel": "Cloudflare Tunnel",
    "dns": "DNS record",
    "mtls": "Require client certificates (mTLS)",
    "firewall": "Firewall rule blocking everything else",
    "turn": "Camera relay (TURN)",
    "proxy": "Home Assistant trusts the tunnel",
    "integration": "Camera relay integration",
    "test": "End-to-end test",
}


class RestartPending(Exception):
    """Setup paused for a Home Assistant restart; it resumes on reconnect."""


class Setup:
    def __init__(self, settings: Settings, cf: Cloudflare, ha: HomeAssistant, tunnel: Tunnel) -> None:
        self.settings = settings
        self.cf = cf
        self.ha = ha
        self.tunnel = tunnel
        self.running = False
        self._lock = asyncio.Lock()
        ha.on_connect(self._after_connect)

    @property
    def results(self) -> dict:
        return self.settings.setdefault("setup_results", {})

    @property
    def complete(self) -> bool:
        return bool(self.settings.get("setup_complete"))

    def _set(self, step: str, status: str, detail: str = "", diagnostics: list | None = None) -> None:
        self.results[step] = {"status": status, "detail": detail, "at": time.time()}
        if diagnostics:
            self.results[step]["diagnostics"] = diagnostics
        self.settings.save()
        log = _LOGGER.warning if status == "fail" else _LOGGER.info
        log("Setup %s: %s %s", step, status, detail)

    # -- entry points ----------------------------------------------------------

    def start(self) -> None:
        if not self.running:
            asyncio.create_task(self.run())

    async def run(self) -> None:
        async with self._lock:
            self.running = True
            self.settings["resume_setup"] = False
            self.settings.save()
            try:
                await self._run_steps()
            except RestartPending:
                pass
            except Exception as err:  # noqa: BLE001 - surface anything in the panel
                _LOGGER.exception("Setup failed")
                running = [k for k, v in self.results.items() if v["status"] == "running"]
                step = running[0] if running else "permissions"
                detail = str(err)
                if isinstance(err, CloudflareError) and err.is_permission and step in STEP_PERMISSION:
                    detail = (f"Cloudflare refused this step. Edit the token in Cloudflare (My Profile → API Tokens) "
                              f"and make sure it has {STEP_PERMISSION[step]} (Edit, not Read). Details: {err}")
                self._set(step, "fail", detail)
            finally:
                self.running = False

    async def _after_connect(self) -> None:
        """After an HA restart: confirm the new HTTP config, then continue setup."""
        if self.settings.get("http_pending_promote"):
            try:
                cfg = await self.ha.http_config()
                if cfg.get("active_config_type") == "pending":
                    await self.ha.promote_http()
                    self._set("proxy", "ok", "Reverse proxy settings applied")
                elif cfg.get("pending") and cfg["pending"].get("error"):
                    self._set("proxy", "fail", f"Home Assistant rejected the settings: {cfg['pending'].get('error_message')}")
                    self.settings["resume_setup"] = False
            except HAError as err:
                _LOGGER.warning("Could not promote HTTP config: %s", err)
            self.settings["http_pending_promote"] = False
            self.settings.save()
        if self.settings.get("resume_setup"):
            await asyncio.sleep(10)  # let integrations finish loading
            self.start()

    # -- steps -------------------------------------------------------------------

    async def _run_steps(self) -> None:
        self.settings["setup_complete"] = False
        for step in STEPS:
            self.results.pop(step, None)
        self.settings.save()

        # 1. Permissions
        self._set("permissions", "running")
        perms = await self.cf.check_permissions()
        missing = [f"{p} (Cloudflare said: {perms[p]})" for p in REQUIRED_PERMISSIONS if perms.get(p)]
        if missing:
            self._set("permissions", "fail", "The token is missing: " + "; ".join(missing),
                      diagnostics=await self.cf.diagnose())
            return
        turn_allowed = perms.get(TURN_PERMISSION) is None
        self._set("permissions", "ok" if turn_allowed else "warn",
                  "" if turn_allowed else f"Missing '{TURN_PERMISSION}': camera relay will be skipped")

        # 2. Tunnel
        self._set("tunnel", "running")
        tunnel_id, created = await self.cf.ensure_tunnel(self.settings.get("tunnel_id", ""))
        await self.cf.configure_tunnel(tunnel_id)
        self.settings["tunnel_id"] = tunnel_id
        self.settings["tunnel_token"] = await self.cf.tunnel_token(tunnel_id)
        self.settings.save()
        self.tunnel.set_token(self.settings["tunnel_token"])
        self._set("tunnel", "ok", "Created" if created else "Using existing tunnel")

        # 3-5. DNS, mTLS, firewall
        self._set("dns", "running")
        self._set("dns", "ok", {"created": "Created", "updated": "Updated", "ok": "Already correct"}[
            await self.cf.ensure_dns(tunnel_id)])
        self._set("mtls", "running")
        self._set("mtls", "ok", "Already enabled" if await self.cf.ensure_mtls() == "ok" else "Enabled")
        self._set("firewall", "running")
        self._set("firewall", "ok", {"created": "Created", "updated": "Updated", "ok": "Already in place"}[
            await self.cf.ensure_waf_rule()])

        # 6. TURN key
        self._set("turn", "running")
        use_turn = await self._ensure_turn(turn_allowed)

        # 7. Integration files, 8. proxy trust; restart at most once for both.
        need_restart = False
        if use_turn:
            self._set("integration", "running")
            files_changed = await asyncio.to_thread(install_integration)
            need_restart = files_changed or not await self.ha.integration_loaded(TURN_DOMAIN)
        else:
            self._set("integration", "skip", "Camera relay not configured")

        self._set("proxy", "running")
        new_http = await self._needed_http_config()
        if new_http is not None:
            self.settings.update(resume_setup=True, http_pending_promote=True)
            self.settings.save()
            self._set("proxy", "running", "Applying; Home Assistant is restarting…")
            if use_turn:
                self._set("integration", "running", "Waiting for restart…")
            await self.ha.configure_http(new_http)
            raise RestartPending
        if need_restart:
            self.settings["resume_setup"] = True
            self.settings.save()
            self._set("integration", "running", "Installed; restarting Home Assistant to load it…")
            await self.ha.restart()
            raise RestartPending

        if use_turn:
            await self._ensure_integration_entry()

        # 9. End-to-end
        self._set("test", "running")
        ok, detail = await self.self_test()
        self._set("test", "ok" if ok else "fail", detail)
        if ok:
            self.settings["setup_complete"] = True
            self.settings.save()

    async def _ensure_turn(self, allowed: bool) -> bool:
        try:
            if await self.ha.config_entries(TURN_DOMAIN):
                self._set("turn", "ok", "Already configured in Home Assistant")
                return True
        except HAError:
            pass
        if self.settings.get("turn_key_id"):
            self._set("turn", "ok", "Relay key exists")
            return True
        if not allowed:
            self._set("turn", "warn", "Skipped: remote camera video may be slow. Add the Realtime permission and re-run.")
            return False
        key_id, key = await self.cf.create_turn_key(f"Home Assistant ({self.cf.hostname})")
        self.settings.update(turn_key_id=key_id, turn_key=key)
        self.settings.save()
        self._set("turn", "ok", "Relay key created (1,000 GB/month free)")
        return True

    async def _ensure_integration_entry(self) -> None:
        if await self.ha.config_entries(TURN_DOMAIN):
            self._set("integration", "ok", "Configured")
            return
        if not self.settings.get("turn_key_id"):
            self._set("integration", "warn", "No relay key to configure")
            return
        result = await self.ha.create_config_entry(
            TURN_DOMAIN, {"key_id": self.settings["turn_key_id"], "api_token": self.settings["turn_key"]}
        )
        if result.get("type") == "create_entry":
            self._set("integration", "ok", "Configured")
        else:
            self._set("integration", "fail", f"Unexpected response: {result.get('errors') or result.get('type')}")

    async def _needed_http_config(self) -> dict | None:
        """The HTTP config to apply, or None if HA already trusts the tunnel."""
        try:
            cfg = await self.ha.http_config()
        except HAError as err:
            if err.code == "unknown_command":
                raise RuntimeError(
                    "This Home Assistant version configures the reverse proxy in YAML. Add to configuration.yaml:  "
                    "http: {use_x_forwarded_for: true, trusted_proxies: [172.30.33.0/24]}  and restart."
                ) from err
            raise
        stable = {k: v for k, v in (cfg.get("stable") or {}).items() if k not in HTTP_META_KEYS}
        proxies = stable.get("trusted_proxies") or []
        trusted = any(ADDON_NETWORK.subnet_of(ipaddress.ip_network(p, strict=False))
                      for p in proxies if ipaddress.ip_network(p, strict=False).version == 4)
        if stable.get("use_x_forwarded_for") and trusted:
            self._set("proxy", "ok", "Already trusted")
            return None
        pending = cfg.get("pending")
        if pending and not pending.get("error"):
            raise RuntimeError("Another HTTP settings change is pending. Finish it under Settings → System → Network.")
        return {**stable, "use_x_forwarded_for": True, "trusted_proxies": [*proxies, str(ADDON_NETWORK)]}

    # -- self-test -------------------------------------------------------------

    async def self_test(self) -> tuple[bool, str]:
        url = f"https://{self.cf.hostname}/"
        for _ in range(30):
            if self.tunnel.connected:
                break
            await asyncio.sleep(1)
        else:
            return False, "cloudflared did not connect. Check the add-on log."

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            try:
                async with session.get(url, allow_redirects=False) as resp:
                    if resp.status != 403:
                        return False, f"Without a certificate {url} returned {resp.status}, expected 403 (blocked)."
            except aiohttp.ClientConnectorCertificateError:
                return False, "Cloudflare is still issuing the HTTPS certificate for your domain. Re-run in a few minutes."
            except aiohttp.ClientError as err:
                return False, f"Could not reach {url}: {err}"

            key, csr = new_key_and_csr("household-self-test")
            cert = await self.cf.sign(csr, 1)
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    cert_file, key_file = Path(tmp, "c.pem"), Path(tmp, "k.pem")
                    cert_file.write_text(cert["certificate"])
                    key_file.write_bytes(key.private_bytes(
                        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
                    ctx = ssl.create_default_context()
                    ctx.load_cert_chain(cert_file, key_file)
                for attempt in range(6):
                    async with session.get(url, ssl=ctx, allow_redirects=False) as resp:
                        status = resp.status
                    if status == 200:
                        return True, f"{url} is reachable with a certificate and blocked without one"
                    await asyncio.sleep(5)  # DNS/tunnel config can take a few seconds
                hint = " (Home Assistant rejected the proxy: check trusted proxies)" if status == 400 else ""
                return False, f"With a certificate {url} returned {status}{hint}."
            finally:
                try:
                    await self.cf.revoke(cert["id"])
                except CloudflareError:
                    _LOGGER.warning("Could not revoke self-test certificate %s", cert["id"])


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    if root.exists():
        for path in sorted(root.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                digest.update(str(path.relative_to(root)).encode())
                digest.update(path.read_bytes())
    return digest.hexdigest()


def install_integration() -> bool:
    """Copy the bundled integration into HA's config. Returns True if files changed."""
    if _tree_hash(INTEGRATION_SRC) == _tree_hash(INTEGRATION_DST):
        return False
    if INTEGRATION_DST.exists():
        shutil.rmtree(INTEGRATION_DST)
    shutil.copytree(INTEGRATION_SRC, INTEGRATION_DST, ignore=shutil.ignore_patterns("__pycache__"))
    return True
