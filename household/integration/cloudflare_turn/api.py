"""Cloudflare Realtime TURN credential generation."""

from datetime import timedelta

import aiohttp
from webrtc_models import RTCIceServer

GENERATE_URL = "https://rtc.live.cloudflare.com/v1/turn/keys/{key_id}/credentials/generate-ice-servers"
CREDENTIAL_TTL = timedelta(hours=48)


class InvalidAuth(Exception):
    """The TURN key ID or API token was rejected."""


async def fetch_ice_servers(session: aiohttp.ClientSession, key_id: str, api_token: str) -> list[RTCIceServer]:
    async with session.post(
        GENERATE_URL.format(key_id=key_id.strip()),
        headers={"Authorization": f"Bearer {api_token.strip()}"},
        json={"ttl": int(CREDENTIAL_TTL.total_seconds())},
        timeout=aiohttp.ClientTimeout(total=15),
    ) as resp:
        if resp.status in (401, 403, 404):
            raise InvalidAuth(f"HTTP {resp.status}")
        resp.raise_for_status()
        data = await resp.json()

    servers = []
    for server in data["iceServers"]:
        urls = server["urls"] if isinstance(server["urls"], list) else [server["urls"]]
        # Port 53 is blocked by browsers and just delays ICE gathering.
        urls = [u for u in urls if ":53?" not in u and not u.endswith(":53")]
        if urls:
            servers.append(
                RTCIceServer(urls=urls, username=server.get("username"), credential=server.get("credential"))
            )
    return servers
