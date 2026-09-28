"""Join / approve / issue / revoke logic shared by the portal and the admin panel."""

import asyncio
import hashlib
import logging
import re
import secrets
import time

from .certs import build_p12, new_key_and_csr
from .cloudflare import Cloudflare
from .ha import HAError, HomeAssistant
from .store import Settings, Store

_LOGGER = logging.getLogger(__name__)

DEFAULT_PANEL = "/local_household"  # replaced at startup with this app's real sidebar path
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")
MAX_PENDING = 10
REQUEST_TTL = 24 * 3600  # pending requests expire after a day
BUNDLE_TTL = 24 * 3600  # issued bundles can be downloaded for a day


class UserError(Exception):
    """Something to show the person filling in the form."""


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Household:
    def __init__(self, store: Store, settings: Settings, ha: HomeAssistant, cf: Cloudflare, options: dict) -> None:
        self.store = store
        self.settings = settings
        self.panel_path = DEFAULT_PANEL
        self.ha = ha
        self.cf = cf
        self.options = options
        self._lock = asyncio.Lock()
        ha.on_event("mobile_app_notification_action", self._on_notification_action)

    @property
    def external_url(self) -> str:
        return self.settings.external_url

    # -- joining ------------------------------------------------------------

    def _require_setup(self) -> None:
        if not self.settings.get("setup_complete"):
            raise UserError("Remote access isn't set up yet. The home owner needs to finish setup in the Household panel.")

    async def request_join(self, name: str, username: str, password: str, device: str, platform: str) -> tuple[str, str]:
        """New member. Creates a disabled HA user and waits for admin approval."""
        self._require_setup()
        name, username, device = name.strip(), username.strip().lower(), device.strip()
        if not name or not device:
            raise UserError("Please fill in your name and a name for this device.")
        if not USERNAME_RE.match(username):
            raise UserError("Usernames are 2-32 characters: lowercase letters, numbers, dot, dash or underscore.")
        if len(password) < 8:
            raise UserError("Please use a password of at least 8 characters.")
        if len(self.store.pending()) >= MAX_PENDING:
            raise UserError("There are too many pending requests right now. Ask the home owner.")
        if await self.ha.find_user_by_username(username):
            raise UserError("That username is taken. If it's yours, use “I already have an account”.")

        try:
            user_id = await self.ha.create_user(name, username, password)
        except HAError as err:
            _LOGGER.error("Creating user %s failed: %s", username, err)
            raise UserError(f"Home Assistant refused the account: {err}") from err

        request, token = self._new_request(name, username, user_id, device, platform, "pending")
        await self._notify_admin(request)
        return request["id"], token

    async def request_device(self, username: str, password: str, device: str, platform: str) -> tuple[str, str]:
        """Existing member adding a device. Their password is the proof; no approval needed."""
        self._require_setup()
        username, device = username.strip().lower(), device.strip()
        if not device:
            raise UserError("Please give this device a name.")
        if not await self.ha.check_password(username, password):
            raise UserError("Wrong username or password.")
        user = await self.ha.find_user_by_username(username)
        if not user or not user.get("is_active"):
            raise UserError("This account is disabled.")

        request, token = self._new_request(user["name"], username, user["id"], device, platform, "approved")
        await self._issue(request)
        return request["id"], token

    def _new_request(self, name, username, user_id, device, platform, status) -> tuple[dict, str]:
        token = secrets.token_urlsafe(32)
        request = {
            "id": secrets.token_hex(8),
            "name": name,
            "username": username,
            "user_id": user_id,
            "device": device[:64],
            "platform": platform if platform in ("ios", "android") else "other",
            "status": status,
            "created": time.time(),
            "token_hash": hash_token(token),
        }
        self.store.requests[request["id"]] = request
        self.store.save()
        return request, token

    def request_for(self, request_id: str, token: str | None) -> dict | None:
        request = self.store.requests.get(request_id)
        if not request or not token or not secrets.compare_digest(request["token_hash"], hash_token(token)):
            return None
        return request

    # -- admin actions ------------------------------------------------------

    async def approve(self, request_id: str) -> None:
        async with self._lock:
            request = self.store.requests.get(request_id)
            if not request or request["status"] not in ("pending", "error"):
                return
            await self.ha.set_user_active(request["user_id"], True)
            request["status"] = "approved"
            self.store.save()
            await self._issue(request)
            await self._dismiss(request)

    async def deny(self, request_id: str) -> None:
        async with self._lock:
            request = self.store.requests.get(request_id)
            if not request or request["status"] != "pending":
                return
            await self._delete_pending_user(request)
            request["status"] = "denied"
            self.store.save()
            await self._dismiss(request)

    async def revoke_device(self, user_id: str, cert_id: str) -> None:
        member = self.store.members.get(user_id)
        if not member:
            return
        for device in member["devices"]:
            if device["cert_id"] == cert_id and device["status"] == "active":
                await self.cf.revoke(cert_id)
                device["status"] = "revoked"
                device["revoked"] = time.time()
        self.store.save()

    async def remove_member(self, user_id: str) -> None:
        """Revoke every certificate and disable the HA account (signs them out everywhere)."""
        member = self.store.members.get(user_id)
        if not member:
            return
        for device in member["devices"]:
            if device["status"] == "active":
                await self.revoke_device(user_id, device["cert_id"])
        try:
            await self.ha.set_user_active(user_id, False)
        except HAError as err:
            _LOGGER.warning("Could not disable HA user %s: %s", user_id, err)
        member["active"] = False
        member["removed"] = time.time()
        self.store.save()

    async def restore_member(self, user_id: str) -> None:
        """Re-enable the HA account. They'll need to add their devices again."""
        member = self.store.members.get(user_id)
        if not member:
            return
        await self.ha.set_user_active(user_id, True)
        member["active"] = True
        member.pop("removed", None)
        self.store.save()

    # -- issuing ------------------------------------------------------------

    async def _issue(self, request: dict) -> None:
        try:
            key, csr = new_key_and_csr(f"{request['username']} / {request['device']}")
            cert = await self.cf.sign(csr, self.options["cert_validity_days"])
            bundle = build_p12(key, cert["certificate"], f"Home Assistant ({request['device']})")
        except Exception as err:
            _LOGGER.exception("Issuing certificate for %s failed", request["username"])
            request["status"] = "error"
            request["error"] = str(err)
            self.store.save()
            return

        self.store.bundle_path(request["id"]).write_bytes(bundle)
        request.update(status="issued", issued=time.time(), cert_id=cert["id"])
        request.pop("error", None)

        member = self.store.member_for(request["user_id"], request["name"], request["username"])
        member["active"] = True
        member["devices"].append(
            {
                "cert_id": cert["id"],
                "name": request["device"],
                "platform": request["platform"],
                "issued": time.time(),
                "expires": cert.get("expires_on"),
                "status": "active",
            }
        )
        self.store.save()
        _LOGGER.info("Issued certificate %s for %s (%s)", cert["id"], request["username"], request["device"])

    # -- notifications ------------------------------------------------------

    async def _notify_admin(self, request: dict) -> None:
        message = (
            f"**{request['name']}** ({request['username']}) wants to join from "
            f"*{request['device']}*.\n\n[Approve or deny]({self.panel_path})"
        )
        try:
            await self.ha.call_service(
                "persistent_notification",
                "create",
                {"title": "Household join request", "message": message, "notification_id": f"household_{request['id']}"},
            )
        except HAError as err:
            _LOGGER.warning("Persistent notification failed: %s", err)

        notify = (self.options.get("notify_service") or "").strip()
        if not notify:
            return
        domain, _, service = notify.partition(".")
        if not service:
            domain, service = "notify", domain
        try:
            await self.ha.call_service(
                domain,
                service,
                {
                    "title": "Household join request",
                    "message": f"{request['name']} wants to join from {request['device']}.",
                    "data": {
                        "tag": f"household_{request['id']}",
                        "url": self.panel_path,
                        "clickAction": self.panel_path,
                        "actions": [
                            {"action": f"HOUSEHOLD_APPROVE_{request['id']}", "title": "Approve", "authenticationRequired": True},
                            {"action": f"HOUSEHOLD_DENY_{request['id']}", "title": "Deny", "destructive": True},
                        ],
                    },
                },
            )
        except HAError as err:
            _LOGGER.warning("Push notification via %s failed: %s", notify, err)

    async def _dismiss(self, request: dict) -> None:
        try:
            await self.ha.call_service(
                "persistent_notification", "dismiss", {"notification_id": f"household_{request['id']}"}
            )
        except HAError:
            pass

    async def _on_notification_action(self, event: dict) -> None:
        action = event.get("data", {}).get("action", "")
        if action.startswith("HOUSEHOLD_"):
            _LOGGER.info("Notification action %s", action)
        if action.startswith("HOUSEHOLD_APPROVE_"):
            await self.approve(action.removeprefix("HOUSEHOLD_APPROVE_"))
        elif action.startswith("HOUSEHOLD_DENY_"):
            await self.deny(action.removeprefix("HOUSEHOLD_DENY_"))

    # -- housekeeping -------------------------------------------------------

    async def _delete_pending_user(self, request: dict) -> None:
        try:
            await self.ha.delete_user(request["user_id"])
        except HAError as err:
            _LOGGER.warning("Could not delete pending user %s: %s", request["username"], err)

    async def cleanup(self) -> None:
        now = time.time()
        async with self._lock:
            for request in self.store.requests.values():
                if request["status"] == "pending" and now - request["created"] > REQUEST_TTL:
                    await self._delete_pending_user(request)
                    request["status"] = "expired"
                    await self._dismiss(request)
                if request["status"] == "issued" and now - request.get("issued", now) > BUNDLE_TTL:
                    self.store.bundle_path(request["id"]).unlink(missing_ok=True)
                    request.pop("bundle_password", None)
                    request["status"] = "collected"
            # Forget finished requests after a week.
            for request_id in [
                r["id"]
                for r in self.store.requests.values()
                if r["status"] in ("denied", "expired", "collected") and now - r["created"] > 7 * 24 * 3600
            ]:
                del self.store.requests[request_id]
            self.store.save()

    async def cleanup_loop(self) -> None:
        while True:
            try:
                await self.cleanup()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Cleanup failed")
            await asyncio.sleep(3600)
