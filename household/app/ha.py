"""Home Assistant access through the Supervisor proxy."""

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable

import aiohttp

_LOGGER = logging.getLogger(__name__)

CORE = "http://homeassistant:8123"
SUPERVISOR = "http://supervisor"
WS_URL = "ws://supervisor/core/websocket"


class HAError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class HomeAssistant:
    """Persistent websocket client with request/response and event subscriptions."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._token = os.environ["SUPERVISOR_TOKEN"]
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._event_handlers: dict[str, Callable[[dict], Awaitable[None]]] = {}
        self._connect_hooks: list[Callable[[], Awaitable[None]]] = []
        self._connected = asyncio.Event()

    # -- connection ---------------------------------------------------------

    async def run(self) -> None:
        while True:
            try:
                await self._connect_and_listen()
            except Exception as err:  # noqa: BLE001 - reconnect on anything
                _LOGGER.warning("Home Assistant websocket dropped: %s", err)
            self._connected.clear()
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(HAError("disconnected", "websocket closed"))
            self._pending.clear()
            await asyncio.sleep(5)

    async def _connect_and_listen(self) -> None:
        async with self._session.ws_connect(WS_URL, heartbeat=30) as ws:
            msg = await ws.receive_json()
            if msg.get("type") != "auth_required":
                raise HAError("auth", f"unexpected greeting {msg}")
            await ws.send_json({"type": "auth", "access_token": self._token})
            msg = await ws.receive_json()
            if msg.get("type") != "auth_ok":
                raise HAError("auth", f"authentication failed: {msg}")
            self._ws = ws
            self._next_id = 1
            _LOGGER.info("Connected to Home Assistant")

            listener = asyncio.create_task(self._listen(ws))
            for event_type in self._event_handlers:
                await self.command({"type": "subscribe_events", "event_type": event_type})
            self._connected.set()
            for hook in self._connect_hooks:
                asyncio.create_task(hook())
            await listener

    async def _listen(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        async for raw in ws:
            if raw.type != aiohttp.WSMsgType.TEXT:
                continue
            msg = raw.json()
            if msg.get("type") == "event":
                event = msg["event"]
                handler = self._event_handlers.get(event.get("event_type"))
                if handler:
                    asyncio.create_task(handler(event))
                continue
            fut = self._pending.pop(msg.get("id"), None)
            if fut is None or fut.done():
                continue
            if msg.get("success", True):
                fut.set_result(msg.get("result"))
            else:
                err = msg.get("error", {})
                fut.set_exception(HAError(err.get("code", "unknown"), err.get("message", "")))

    def on_event(self, event_type: str, handler: Callable[[dict], Awaitable[None]]) -> None:
        self._event_handlers[event_type] = handler

    def on_connect(self, hook: Callable[[], Awaitable[None]]) -> None:
        """Run after every (re)connect, e.g. to resume setup after an HA restart."""
        self._connect_hooks.append(hook)

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    async def command(self, payload: dict, timeout: float = 30):
        if payload.get("type") != "subscribe_events":
            await asyncio.wait_for(self._connected.wait(), timeout)
        assert self._ws is not None
        msg_id = self._next_id
        self._next_id += 1
        fut = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = fut
        await self._ws.send_json({**payload, "id": msg_id})
        return await asyncio.wait_for(fut, timeout)

    # -- users --------------------------------------------------------------

    async def list_users(self) -> list[dict]:
        return await self.command({"type": "config/auth/list"})

    async def find_user_by_username(self, username: str) -> dict | None:
        for user in await self.list_users():
            if (user.get("username") or "").lower() == username.lower():
                return user
        return None

    async def create_user(self, name: str, username: str, password: str) -> str:
        """Create a non-admin user, disabled until approved. Returns the user id."""
        result = await self.command(
            {"type": "config/auth/create", "name": name, "group_ids": ["system-users"], "local_only": False}
        )
        user_id = result["user"]["id"]
        try:
            await self.set_user_active(user_id, False)
            await self.command(
                {
                    "type": "config/auth_provider/homeassistant/create",
                    "user_id": user_id,
                    "username": username,
                    "password": password,
                }
            )
        except Exception:
            await self.delete_user(user_id)
            raise
        return user_id

    async def set_user_active(self, user_id: str, active: bool) -> None:
        await self.command({"type": "config/auth/update", "user_id": user_id, "is_active": active})

    async def delete_user(self, user_id: str) -> None:
        await self.command({"type": "config/auth/delete", "user_id": user_id})

    async def check_password(self, username: str, password: str) -> bool:
        """Validate credentials with Core's own login flow, same as the login page.

        A user with MFA enabled counts as valid once the password step passes.
        """
        client_id = "http://homeassistant.local:8099/"
        try:
            async with self._session.post(
                f"{CORE}/auth/login_flow",
                json={"client_id": client_id, "handler": ["homeassistant", None], "redirect_uri": client_id},
            ) as resp:
                flow = await resp.json(content_type=None)
            async with self._session.post(
                f"{CORE}/auth/login_flow/{flow['flow_id']}",
                json={"client_id": client_id, "username": username, "password": password},
            ) as resp:
                result = await resp.json(content_type=None)
        except (aiohttp.ClientError, KeyError, ValueError) as err:
            _LOGGER.error("Login flow for %s failed: %s", username, err)
            return False
        if result.get("type") == "create_entry" or result.get("step_id") == "mfa":
            return True
        _LOGGER.info("Login check for %s rejected: %s", username, result.get("errors") or result.get("type"))
        return False

    # -- notifications ------------------------------------------------------

    async def call_service(self, domain: str, service: str, data: dict) -> None:
        await self.command(
            {"type": "call_service", "domain": domain, "service": service, "service_data": data}
        )

    async def restart(self) -> None:
        try:
            await self.call_service("homeassistant", "restart", {})
        except HAError as err:
            if err.code != "disconnected":  # the restart closes our socket mid-call
                raise

    # -- HTTP (reverse proxy) config -------------------------------------------

    async def http_config(self) -> dict:
        """{"stable", "pending", "active_config_type", ...}; raises HAError(unknown_command) on older HA."""
        return await self.command({"type": "http/config"})

    async def configure_http(self, config: dict) -> bool:
        """Store a pending HTTP config. HA restarts itself and auto-reverts unless promoted."""
        try:
            result = await self.command({"type": "http/config/configure", "config": config})
        except HAError as err:
            if err.code == "disconnected":
                return True
            raise
        return bool(result and result.get("restart"))

    async def promote_http(self) -> None:
        await self.command({"type": "http/config/promote"})

    # -- this app (Supervisor API) ----------------------------------------------

    async def _supervisor(self, method: str, path: str, payload: dict | None = None) -> dict:
        async with self._session.request(
            method, f"{SUPERVISOR}/{path}", json=payload, headers={"Authorization": f"Bearer {self._token}"}
        ) as resp:
            body = await resp.json(content_type=None)
        if resp.status >= 400 or body.get("result") != "ok":
            raise HAError(str(resp.status), str(body.get("message")))
        return body.get("data") or {}

    async def app_panel_path(self) -> str:
        """Frontend path of this app's sidebar panel ("/<slug>"), enabling the panel if it's off.

        Notification links point here, and the path only exists while the panel is in the sidebar.
        """
        info = await self._supervisor("GET", "addons/self/info")
        if not info.get("ingress_panel"):
            await self._supervisor("POST", "addons/self/options", {"ingress_panel": True})
        return f"/{info['slug']}"

    # -- integrations ----------------------------------------------------------

    async def integration_loaded(self, domain: str) -> bool:
        try:
            await self.command({"type": "manifest/get", "integration": domain})
            return True
        except HAError:
            return False

    async def _rest(self, method: str, path: str, payload: dict | None = None):
        async with self._session.request(
            method,
            f"{SUPERVISOR}/core/api/{path}",
            json=payload,
            headers={"Authorization": f"Bearer {self._token}"},
        ) as resp:
            body = await resp.json(content_type=None)
            if resp.status >= 400:
                raise HAError(str(resp.status), str(body))
            return body

    async def config_entries(self, domain: str) -> list[dict]:
        return await self._rest("GET", f"config/config_entries/entry?domain={domain}")

    async def create_config_entry(self, domain: str, data: dict) -> dict:
        """Run an integration's user config flow non-interactively."""
        flow = await self._rest("POST", "config/config_entries/flow", {"handler": domain})
        if flow.get("type") != "form":
            return flow
        return await self._rest("POST", f"config/config_entries/flow/{flow['flow_id']}", data)
