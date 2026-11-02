from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class DeviceInfo:
    udid: str
    name: str = "Unknown"
    product_type: str = "Unknown"
    product_version: str = "Unknown"
    connection_types: list[str] = field(default_factory=list)
    developer_mode: bool | None = None
    trusted: bool | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Location:
    latitude: float
    longitude: float

    def to_dict(self) -> dict[str, float]:
        return {"latitude": self.latitude, "longitude": self.longitude}
