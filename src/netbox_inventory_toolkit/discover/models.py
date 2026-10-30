"""Data models shared by the discovery command."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROTOCOLS = ("redfish", "snmp", "ipmi")


@dataclass
class DiscoveryOptions:
    inventory: Path
    targets: list[str] = field(default_factory=list)
    networks: list[str] = field(default_factory=list)
    excludes: list[str] = field(default_factory=list)
    protocols: set[str] = field(default_factory=lambda: set(PROTOCOLS))
    inventory_targets: bool = True
    scan_inventory: bool = False
    ipmi_cidr_scan: bool = False
    max_hosts: int = 1024
    force: bool = False

    concurrency: int = 32
    timeout: float = 3.0
    redfish_auth: bool = False
    redfish_user: str | None = None
    redfish_pass: str | None = None
    redfish_verify: bool = False
    snmp_community: str = "public"
    snmp_timeout: float = 2.0
    snmp_retries: int = 1
    ipmi_user: str | None = None
    ipmi_pass: str | None = None
    ipmi_timeout: float = 5.0
    ipmi_binary: str = "ipmitool"

    config: Any = None
    snmp_credential: str | None = None
    redfish_credential: str | None = None
    ipmi_credential: str | None = None

    default_role: str = ""
    default_device_type: str = ""

    output_format: str = "table"
    output: Path | None = None
    write_new: Path | None = None

    def has_network_scan(self) -> bool:
        return bool(self.targets or self.networks or self.scan_inventory)


@dataclass
class DeviceSnapshot:
    name: str
    layer: str
    site: str = ""
    role: str = ""
    device_type: str = ""
    status: str = ""
    serial: str = ""
    service_tag: str = ""
    system_uuid: str = ""
    redfish_ip: str = ""
    ipmi_ip: str = ""
    snmp_ip: str = ""
    oob_ip: str = ""
    unifi_ip: str = ""
    unifi_mac: str = ""
    talos_node: str = ""
    talos_cluster: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    macs: set[str] = field(default_factory=set)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class InventorySnapshot:
    manual_dir: Path
    generated_dir: Path
    devices: list[DeviceSnapshot]
    manual_names: set[str]
    generated_names: set[str]
    device_types: list[dict[str, Any]]
    sites: set[str]
    roles: set[str]
    settings: dict[str, Any]


@dataclass
class ScanTarget:
    address: str
    source: str  # explicit, network, inventory
    site: str = ""
    device_name: str = ""


@dataclass
class ProbeResult:
    protocol: str
    address: str
    reachable: bool = False
    auth_required: bool = False
    identity: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    @property
    def serial(self) -> str:
        return str(self.identity.get("serial") or "").strip()

    @property
    def system_uuid(self) -> str:
        return str(self.identity.get("system_uuid") or "").strip()

    @property
    def name(self) -> str:
        return str(self.identity.get("name") or "").strip()

    @property
    def model(self) -> str:
        return str(self.identity.get("model") or "").strip()

    @property
    def manufacturer(self) -> str:
        return str(self.identity.get("manufacturer") or "").strip()


@dataclass
class DiscoveryItem:
    status: str  # existing, update, new, conflict, unreachable
    probes: list[ProbeResult] = field(default_factory=list)
    device: DeviceSnapshot | None = None
    conflicts: list[DeviceSnapshot] = field(default_factory=list)
    updates: dict[str, Any] = field(default_factory=dict)
    suggested: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    addresses: set[str] = field(default_factory=set)


@dataclass
class DiscoveryReport:
    items: list[DiscoveryItem]
    scanned: int
    elapsed_seconds: float
    protocols: list[str]
    snapshot: InventorySnapshot


class DiscoveryError(Exception):
    """A user-facing discovery failure."""
