"""Tiny device registry.

Maps user_id -> Device. Backed by an in-memory dict, optionally persisted to a
JSON file (DEVICE_STORE_PATH). This is intentionally minimal for the beta; swap
for a real DB (Postgres/Cosmos) before production.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Dict, Optional

from .models import Device, DeviceRegistration


class DeviceStore:
    def __init__(self, path: str = ":memory:") -> None:
        self._path = path
        self._lock = threading.Lock()
        self._devices: Dict[str, Device] = {}
        if self._persistent:
            self._load()

    @property
    def _persistent(self) -> bool:
        return self._path not in ("", ":memory:")

    def _load(self) -> None:
        p = Path(self._path)
        if p.exists():
            raw = json.loads(p.read_text() or "{}")
            self._devices = {k: Device(**v) for k, v in raw.items()}

    def _flush(self) -> None:
        if not self._persistent:
            return
        p = Path(self._path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {k: json.loads(v.model_dump_json()) for k, v in self._devices.items()}
        p.write_text(json.dumps(data, indent=2))

    def upsert(self, reg: DeviceRegistration) -> Device:
        with self._lock:
            existing = self._devices.get(reg.user_id)
            device = Device(**reg.model_dump())
            if existing is not None:
                device.registered_at = existing.registered_at
            self._devices[reg.user_id] = device
            self._flush()
            return device

    def get(self, user_id: str) -> Optional[Device]:
        return self._devices.get(user_id)

    def all(self) -> Dict[str, Device]:
        return dict(self._devices)
