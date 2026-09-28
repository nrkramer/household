"""Cloudflare API client: tunnel, DNS, mTLS, WAF, client certificates, TURN keys."""

import logging

import aiohttp

_LOGGER = logging.getLogger(__name__)

API ="https://api.cloudflare.com/client/v4"
TUNNEL_NAME = "home-assistant"
HA_SERVICE = "http://homeassistant:8123"
# Custom WAF rule ref, so re-running setup updates our rule instead of adding another.
WAF_RULE_REF = "household_mtls"
PERMISSION_ERROR_CODES = {9109, 10000, 10001}


class CloudflareError(Exception):
    def __init__(self, message: str, codes: set[int] | None = None, status: int = 0) -> None:
        super().__init__(message)
        self.codes = codes or set()
        self.status = status

    @property
    def is_permission(self) -> bool:
        return bool(self.codes & PERMISSION_ERROR_CODES) or self.status == 403


def waf_expression(hostname: str) -> str:
    return (
        f'(http.host eq "{hostname}" and '
        "(not cf.tls_client_auth.cert_verified or cf.tls_client_auth.cert_revoked))"
    )


class Cloudflare:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._token = ""
        self.account_id = ""
        self.zone_id = ""
        self.hostname = ""

    def configure(self, token: str, account_id: str = "", zone_id: str = "", hostname: str = "") -> None:
        self._token = token.strip()
        self.account_id, self.zone_id, self.hostname = account_id, zone_id, hostname

    @property
    def configured(self) -> bool:
        return bool(self._token and self.zone_id and self.hostname)

    async def request(self, method: str, path: str, **kwargs):
        async with self._session.request(
            method,
            f"{API}{path}",
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=aiohttp.ClientTimeout(total=30),
            **kwargs,
        ) as resp:
            body = await resp.json(content_type=None)
        if not isinstance(body, dict) or not body.get("success"):
            errors = body.get("errors", []) if isinstance(body, dict) else []
            text = "; ".join(f"{e.get('message', '?')} ({e.get('code')})" for e in errors) or f"HTTP {resp.status}"
            raise CloudflareError(f"{method} {path}: {text}", {e.get("code") for e in errors}, resp.status)
        return body["result"]

    # -- token / zones ---------------------------------------------------------

    async def verify_token(self) -> bool:
        try:
            return (await self.request("GET", "/user/tokens/verify")).get("status") == "active"
        except CloudflareError:
            return False

    async def list_zones(self) -> list[dict]:
        zones = await self.request("GET", "/zones", params={"per_page": 50, "status": "active"})
        return [{"id": z["id"], "name": z["name"], "account_id": z["account"]["id"]} for z in zones]

    async def check_permissions(self) -> dict[str, str | None]:
        """Read-only probe of everything setup needs. Edit rights imply read rights.

        Maps each permission to None if usable, else Cloudflare's error text.
        """
        result = {}
        for label, path, allow_404 in self._probes():
            status, message = await self._probe(path)
            ok = status == 200 or (allow_404 and status == 404)
            result[label] = None if ok else message
            if not ok:
                _LOGGER.warning("Permission probe %s failed: HTTP %s %s", label, status, message)
        return result

    def _probes(self) -> list[tuple[str, str, bool]]:
        """(permission label, GET path, whether 404 means allowed-but-empty)."""
        z, a = f"/zones/{self.zone_id}", f"/accounts/{self.account_id}"
        return [
            ("Zone: Read", z, False),
            ("DNS: Edit", f"{z}/dns_records?per_page=1", False),
            ("SSL and Certificates: Edit", f"{z}/client_certificates?per_page=1", False),
            # 404 here just means the zone has no custom firewall rules yet.
            ("Zone WAF: Edit", f"{z}/rulesets/phases/http_request_firewall_custom/entrypoint", True),
            ("Cloudflare Tunnel: Edit", f"{a}/cfd_tunnel?per_page=1", False),
            ("Realtime (TURN): Edit", f"{a}/calls/turn_keys", False),
        ]

    async def _probe(self, path: str) -> tuple[int, str]:
        try:
            await self.request("GET", path)
            return 200, "ok"
        except CloudflareError as err:
            return err.status, str(err).split(": ", 1)[-1]
        except aiohttp.ClientError as err:
            return 0, str(err)

    async def diagnose(self) -> list[dict]:
        """Every call setup relies on, with Cloudflare's raw answer. Shown when setup fails."""
        z = f"/zones/{self.zone_id}"
        paths = [(label, path) for label, path, _ in self._probes()]
        paths += [("Zone rulesets (list)", f"{z}/rulesets"), ("mTLS hostnames", f"{z}/certificate_authorities/hostname_associations"),
                  ("Token verify", "/user/tokens/verify")]
        rows = []
        for label, path in paths:
            status, message = await self._probe(path)
            rows.append({"label": label, "path": path.replace(self.zone_id, "{zone}").replace(self.account_id, "{account}"),
                         "status": status, "message": message})
        return rows

    # -- tunnel ----------------------------------------------------------------

    async def ensure_tunnel(self, known_id: str = "") -> tuple[str, bool]:
        """Reuse the tunnel already serving our hostname (or ours by name), else create one.

        Returns (tunnel_id, created).
        """
        a = f"/accounts/{self.account_id}"
        tunnels = await self.request("GET", f"{a}/cfd_tunnel", params={"is_deleted": "false", "per_page": 100})
        remote = [t for t in tunnels if t.get("remote_config")]
        if known_id and any(t["id"] == known_id for t in remote):
            return known_id, False
        for tunnel in remote:
            config = await self.request("GET", f"{a}/cfd_tunnel/{tunnel['id']}/configurations")
            ingress = ((config or {}).get("config") or {}).get("ingress") or []
            if any(rule.get("hostname") == self.hostname for rule in ingress):
                return tunnel["id"], False
        records = await self.request("GET", f"/zones/{self.zone_id}/dns_records", params={"name": self.hostname})
        for record in records:
            target = record.get("content", "")
            if record["type"] == "CNAME" and target.endswith(".cfargotunnel.com"):
                tunnel_id = target.removesuffix(".cfargotunnel.com")
                if any(t["id"] == tunnel_id for t in remote):
                    return tunnel_id, False
        for tunnel in remote:
            if tunnel["name"] == TUNNEL_NAME:
                return tunnel["id"], False
        created = await self.request(
            "POST", f"{a}/cfd_tunnel", json={"name": TUNNEL_NAME, "config_src": "cloudflare"}
        )
        return created["id"], True

    async def configure_tunnel(self, tunnel_id: str) -> None:
        """Route our hostname to HA, keeping any other routes on the tunnel."""
        path = f"/accounts/{self.account_id}/cfd_tunnel/{tunnel_id}/configurations"
        current = await self.request("GET", path)
        config = dict((current or {}).get("config") or {})
        others = [
            r for r in config.get("ingress") or [] if r.get("hostname") and r.get("hostname") != self.hostname
        ]
        config["ingress"] = [*others, {"hostname": self.hostname, "service": HA_SERVICE}, {"service": "http_status:404"}]
        await self.request("PUT", path, json={"config": config})

    async def tunnel_token(self, tunnel_id: str) -> str:
        return await self.request("GET", f"/accounts/{self.account_id}/cfd_tunnel/{tunnel_id}/token")

    # -- DNS -------------------------------------------------------------------

    async def ensure_dns(self, tunnel_id: str) -> str:
        target = f"{tunnel_id}.cfargotunnel.com"
        path = f"/zones/{self.zone_id}/dns_records"
        records = await self.request("GET", path, params={"name": self.hostname})
        body = {"type": "CNAME", "name": self.hostname, "content": target, "proxied": True,
                "comment": "Home Assistant (Household add-on)"}
        if not records:
            await self.request("POST", path, json=body)
            return "created"
        record = records[0]
        if record["type"] == "CNAME" and record["content"] == target and record.get("proxied"):
            return "ok"
        if record["type"] == "CNAME" and record["content"].endswith(".cfargotunnel.com"):
            await self.request("PUT", f"{path}/{record['id']}", json=body)
            return "updated"
        raise CloudflareError(
            f"{self.hostname} already has a {record['type']} record pointing to {record['content']}. "
            "Choose a different subdomain, or delete that record in Cloudflare first."
        )

    # -- mTLS + WAF ------------------------------------------------------------

    async def ensure_mtls(self) -> str:
        path = f"/zones/{self.zone_id}/certificate_authorities/hostname_associations"
        hostnames = (await self.request("GET", path)).get("hostnames") or []
        if self.hostname in hostnames:
            return "ok"
        # PUT replaces the list, so include the existing ones.
        await self.request("PUT", path, json={"hostnames": [*hostnames, self.hostname]})
        return "enabled"

    async def ensure_waf_rule(self) -> str:
        z = f"/zones/{self.zone_id}"
        expression = waf_expression(self.hostname)
        rule = {"ref": WAF_RULE_REF, "description": f"Home Assistant: require client certificate ({self.hostname})",
                "expression": expression, "action": "block", "enabled": True}
        try:
            ruleset = await self.request("GET", f"{z}/rulesets/phases/http_request_firewall_custom/entrypoint")
        except CloudflareError as err:
            if err.status != 404:
                raise
            await self.request("PUT", f"{z}/rulesets/phases/http_request_firewall_custom/entrypoint",
                               json={"rules": [rule]})
            return "created"
        for existing in ruleset.get("rules") or []:
            if existing.get("ref") == WAF_RULE_REF or existing.get("expression") == expression:
                if (existing.get("expression"), existing.get("action"), existing.get("enabled")) == (expression, "block", True):
                    return "ok"
                # Keep the rule's existing ref; refs can't always be changed.
                update = {k: v for k, v in rule.items() if k != "ref"}
                await self.request("PATCH", f"{z}/rulesets/{ruleset['id']}/rules/{existing['id']}", json=update)
                return "updated"
        # New rules go first so nothing earlier can skip them.
        await self.request("POST", f"{z}/rulesets/{ruleset['id']}/rules", json={**rule, "position": {"index": 1}})
        return "created"

    # -- client certificates ---------------------------------------------------

    async def sign(self, csr_pem: str, validity_days: int) -> dict:
        return await self.request(
            "POST",
            f"/zones/{self.zone_id}/client_certificates",
            json={"csr": csr_pem, "validity_days": validity_days},
        )

    async def revoke(self, cert_id: str) -> None:
        try:
            await self.request("DELETE", f"/zones/{self.zone_id}/client_certificates/{cert_id}")
        except CloudflareError as err:
            if "revoked" not in str(err).lower():
                raise

    # -- TURN ------------------------------------------------------------------

    async def create_turn_key(self, name: str) -> tuple[str, str]:
        result = await self.request("POST", f"/accounts/{self.account_id}/calls/turn_keys", json={"name": name})
        return result["uid"], result["key"]
