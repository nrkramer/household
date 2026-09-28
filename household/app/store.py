"""Tiny JSON-file persistence for settings, join requests and members."""

import json
import os
import time
from pathlib import Path

DATA_DIR = Path(os.environ.get("HOUSEHOLD_DATA", "/data"))
STATE_FILE = DATA_DIR / "state.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
BUNDLE_DIR = DATA_DIR / "bundles"


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


class Settings(dict):
    """Setup wizard results: Cloudflare token, zone, tunnel, TURN key, ..."""

    def __init__(self) -> None:
        super().__init__(json.loads(SETTINGS_FILE.read_text()) if SETTINGS_FILE.exists() else {})

    def save(self) -> None:
        _write_json(SETTINGS_FILE, self)

    @property
    def external_url(self) -> str:
        return f"https://{self['hostname']}" if self.get("hostname") else ""


class Store:
    def __init__(self) -> None:
        BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
        self.state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
        self.state.setdefault("requests", {})
        self.state.setdefault("members", {})

    @property
    def requests(self) -> dict:
        return self.state["requests"]

    @property
    def members(self) -> dict:
        return self.state["members"]

    def save(self) -> None:
        _write_json(STATE_FILE, self.state)

    def bundle_path(self, request_id: str) -> Path:
        return BUNDLE_DIR / f"{request_id}.p12"

    def pending(self) -> list[dict]:
        return sorted(
            (r for r in self.requests.values() if r["status"] == "pending"),
            key=lambda r: r["created"],
        )

    def member_for(self, user_id: str, name: str, username: str) -> dict:
        member = self.members.get(user_id)
        if member is None:
            member = {
                "user_id": user_id,
                "name": name,
                "username": username,
                "joined": time.time(),
                "active": True,
                "devices": [],
            }
            self.members[user_id] = member
        return member
