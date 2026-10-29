"""Collector options and shared runtime context."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import (
    DEFAULT_GENERATED_SUBDIR,
    DEFAULT_IPMI_PASS,
    DEFAULT_IPMI_USER,
    DEFAULT_MANUAL_SUBDIR,
    DEFAULT_REDFISH_PASS,
    DEFAULT_REDFISH_USER,
    DEFAULT_SNMP_COMMUNITY,
)
from .identity import DeviceResolver

ALL_SOURCES = ("unifi", "snmp", "talos", "redfish", "ipmi")


@dataclass
class CollectOptions:
    inventory: Path = Path("inventory")
    manual_dir: Path | None = None
    output_dir: Path | None = None
    sources_mode: str = "auto"
    disabled_sources: set[str] = field(default_factory=set)

    talos_config_dir: Path | None = None
    kubectl: str = "kubectl"
    kubeconfig: str | None = None
    nodes_json: str | None = None
    talosctl: str | None = None
    talosconfig: str | None = None

    unifi_url: str | None = None
    unifi_user: str | None = None
    unifi_pass: str | None = None
    unifi_secret: str | None = None

    redfish_user: str = DEFAULT_REDFISH_USER
    redfish_pass: str = DEFAULT_REDFISH_PASS
    redfish_verify: bool = False

    ipmi_user: str = DEFAULT_IPMI_USER
    ipmi_pass: str = DEFAULT_IPMI_PASS

    snmp_community: str = DEFAULT_SNMP_COMMUNITY
    snmp_timeout: float = 2.0
    snmp_retries: int = 1
    snmp_report: Path | None = None

    log_file: Path | None = None
    log_level: str = "INFO"

    @property
    def manual_path(self) -> Path:
        return self.manual_dir or self.inventory / DEFAULT_MANUAL_SUBDIR

    @property
    def generated_path(self) -> Path:
        return self.output_dir or self.inventory / DEFAULT_GENERATED_SUBDIR

    @property
    def images_path(self) -> Path:
        return self.inventory / "images"


@dataclass
class CollectContext:
    options: CollectOptions
    manual: dict[str, Any]
    resolver: DeviceResolver

    @property
    def settings(self) -> dict[str, Any]:
        value = self.manual.get("settings")
        return value if isinstance(value, dict) else {}
