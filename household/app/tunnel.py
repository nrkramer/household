"""Runs cloudflared as a child process and restarts it if it exits."""

import asyncio
import logging
import os

_LOGGER = logging.getLogger(__name__)

CLOUDFLARED = "/usr/local/bin/cloudflared"


class Tunnel:
    def __init__(self) -> None:
        self._token = ""
        self._proc: asyncio.subprocess.Process | None = None
        self._changed = asyncio.Event()
        self.connected = False

    def set_token(self, token: str) -> None:
        if token != self._token:
            self._token = token
            self._changed.set()

    async def run(self) -> None:
        backoff = 5
        while True:
            if not self._token:
                self._changed.clear()
                await self._changed.wait()
                continue
            self._changed.clear()
            token = self._token
            _LOGGER.info("Starting cloudflared")
            self._proc = await asyncio.create_subprocess_exec(
                CLOUDFLARED, "tunnel", "--no-autoupdate", "--metrics", "127.0.0.1:0", "run",
                env={**{k: v for k, v in os.environ.items() if k != "SUPERVISOR_TOKEN"}, "TUNNEL_TOKEN": token},
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            output = asyncio.create_task(self._log_output(self._proc))
            changed = asyncio.create_task(self._changed.wait())
            exited = asyncio.create_task(self._proc.wait())
            await asyncio.wait({changed, exited}, return_when=asyncio.FIRST_COMPLETED)
            self.connected = False
            if not exited.done():
                _LOGGER.info("Tunnel token changed, restarting cloudflared")
                self._proc.terminate()
                await exited
                backoff = 5
            else:
                changed.cancel()
                _LOGGER.warning("cloudflared exited with %s, restarting in %ss", self._proc.returncode, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300)
            await output

    async def _log_output(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            if "Registered tunnel connection" in line:
                self.connected = True
            _LOGGER.info("cloudflared: %s", line.split(" ", 1)[-1] if line[:4].isdigit() else line)
