"""Entry point: python -m household"""

import asyncio
import json
import logging
import os
from pathlib import Path

import aiohttp
from aiohttp import web

from .cloudflare import Cloudflare
from .ha import HomeAssistant
from .service import Household
from .setup import Setup
from .store import Settings, Store
from .tunnel import Tunnel
from .web import PORTAL_PORT, admin_app, portal_app

ADMIN_PORT = 8098
OPTIONS_FILE = Path(os.environ.get("HOUSEHOLD_OPTIONS", "/data/options.json"))


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    options = json.loads(OPTIONS_FILE.read_text())
    settings = Settings()

    async with aiohttp.ClientSession() as session:
        ha = HomeAssistant(session)
        cf = Cloudflare(session)
        if settings.get("token"):
            cf.configure(settings["token"], settings.get("account_id", ""), settings.get("zone_id", ""),
                         settings.get("hostname", ""))
        tunnel = Tunnel()
        if settings.get("tunnel_token"):
            tunnel.set_token(settings["tunnel_token"])

        hh = Household(Store(), settings, ha, cf, options)
        setup = Setup(settings, cf, ha, tunnel)

        runners = []
        for app, port in ((portal_app(hh), PORTAL_PORT), (admin_app(hh, setup), ADMIN_PORT)):
            runner = web.AppRunner(app, access_log=None)
            await runner.setup()
            await web.TCPSite(runner, "0.0.0.0", port).start()
            runners.append(runner)
        logging.info("Join portal on :%s, admin panel on :%s (ingress)", PORTAL_PORT, ADMIN_PORT)
        if not settings.get("setup_complete"):
            logging.info("Remote access is not set up yet: open the Household panel in Home Assistant")

        try:
            await asyncio.gather(ha.run(), tunnel.run(), hh.cleanup_loop())
        finally:
            for runner in runners:
                await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
