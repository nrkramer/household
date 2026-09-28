"""Supply Cloudflare Realtime TURN relays to Home Assistant's WebRTC (go2rtc) streams.

Without a relay, remote WebRTC camera streams fail behind carrier-grade NAT and fall
back to slow HLS. Credentials are short-lived, so they are refreshed periodically.
"""

from datetime import timedelta
import logging

import aiohttp

from homeassistant.components.web_rtc import async_register_ice_servers
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .api import InvalidAuth, fetch_ice_servers
from .const import CONF_API_TOKEN, CONF_KEY_ID

_LOGGER = logging.getLogger(__name__)

REFRESH_INTERVAL = timedelta(hours=12)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = async_get_clientsession(hass)
    key_id, api_token = entry.data[CONF_KEY_ID], entry.data[CONF_API_TOKEN]

    try:
        servers = await fetch_ice_servers(session, key_id, api_token)
    except InvalidAuth as err:
        raise ConfigEntryAuthFailed("Cloudflare rejected the TURN key") from err
    except (aiohttp.ClientError, TimeoutError) as err:
        raise ConfigEntryNotReady(f"Could not reach Cloudflare TURN: {err}") from err

    current = {"servers": servers}
    entry.async_on_unload(async_register_ice_servers(hass, lambda: current["servers"]))

    async def _refresh(_now) -> None:
        try:
            current["servers"] = await fetch_ice_servers(session, key_id, api_token)
        except (InvalidAuth, aiohttp.ClientError, TimeoutError) as err:
            # Previous credentials stay valid for the rest of their 48h TTL.
            _LOGGER.warning("Refreshing Cloudflare TURN credentials failed: %s", err)

    entry.async_on_unload(async_track_time_interval(hass, _refresh, REFRESH_INTERVAL))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return True
