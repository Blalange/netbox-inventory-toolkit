"""Redfish source adapter with standard collection discovery."""

from __future__ import annotations

import os
from typing import Any

import requests

from netbox_inventory_toolkit.inventory.util import InventoryError

from ..hardware import normalize_manufacturer
from ..log import get_logger
from .base import Source, SourceState

log = get_logger(__name__)

USER_AGENT = "netbox-inventory-toolkit/0.1"
DEFAULT_SYSTEM_URL = "/redfish/v1/Systems/System.Embedded.1"


def _redfish_session(
    username: str,
    password: str,
    *,
    verify: bool | None = None,
) -> requests.Session:
    if verify is None:
        verify = os.environ.get("REDFISH_VERIFY", "false").lower() in {"1", "true", "yes"}
    if not verify:
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    session = requests.Session()
    session.auth = (username, password)
    session.verify = verify
    session.headers.update({"Accept": "application/json", "User-Agent": USER_AGENT})
    return session


def _redfish_get(session: requests.Session, base: str, path: str) -> dict | None:
    try:
        response = session.get(f"{base}{path}", timeout=30)
    except requests.RequestException as exc:
        log.warning("Redfish %s%s: %s", base, path, exc)
        return None
    if response.status_code != 200:
        log.warning("Redfish %s%s: HTTP %s", base, path, response.status_code)
        return None
    try:
        return response.json()
    except ValueError:
        return None


def _first_member(session: requests.Session, base: str, collection: dict | None) -> str:
    members = (collection or {}).get("Members") or []
    for member in members:
        path = member.get("@odata.id") if isinstance(member, dict) else None
        if path:
            return str(path)
    return ""


def _discover_url(
    session: requests.Session,
    base: str,
    collection_name: str,
    fallback: str,
) -> str:
    root = _redfish_get(session, base, "/redfish/v1") or {}
    collection_url = ((root.get(collection_name) or {}).get("@odata.id")) or ""
    if not collection_url:
        collection_url = f"/redfish/v1/{collection_name}"
    collection = _redfish_get(session, base, collection_url)
    member = _first_member(session, base, collection)
    if member:
        return member
    return fallback


def _members(
    session: requests.Session, base: str, collection_url: str
) -> list[dict[str, Any]]:
    collection = _redfish_get(session, base, collection_url)
    result: list[dict[str, Any]] = []
    for member in (collection or {}).get("Members", []) or []:
        path = member.get("@odata.id") if isinstance(member, dict) else None
        if not path:
            continue
        data = _redfish_get(session, base, str(path))
        if data:
            result.append(data)
    return result


def read_redfish(
    base: str,
    username: str,
    password: str,
    *,
    verify: bool | None = None,
) -> dict[str, Any] | None:
    """Read CPU, memory, disk, fan and PSU data from a Redfish BMC."""
    session = _redfish_session(username, password, verify=verify)
    system_url = _discover_url(
        session,
        base,
        "Systems",
        os.environ.get("REDFISH_SYSTEM_PATH", DEFAULT_SYSTEM_URL),
    )
    system = _redfish_get(session, base, system_url)
    if not system:
        return None
    result: dict[str, Any] = {
        "system": system,
        "cpu": [],
        "memory": [],
        "disks": [],
        "fans": [],
        "psus": [],
    }

    processors_url = ((system.get("Processors") or {}).get("@odata.id")) or (
        f"{system_url}/Processors"
    )
    for index, data in enumerate(_members(session, base, str(processors_url)), 1):
        oem = ((data.get("Oem") or {}).get("Dell") or {}).get("DellProcessor") or {}
        result["cpu"].append(
            {
                "bay": data.get("Name") or f"CPU {index}",
                "manufacturer": normalize_manufacturer(
                    str(data.get("Manufacturer") or "")
                ),
                "model": str(data.get("Model") or f"CPU {index}"),
                "cores": int(
                    data.get("TotalCores") or data.get("TotalEnabledCores") or 0
                ),
                "speed": float(
                    oem.get("CurrentClockSpeedMhz")
                    or data.get("OperatingSpeedMHz")
                    or data.get("MaxSpeedMHz")
                    or 0
                )
                / 1000,
                "architecture": str(data.get("InstructionSet") or "x86_64").lower(),
                "serial": str(data.get("SerialNumber") or ""),
            }
        )

    memory_url = ((system.get("Memory") or {}).get("@odata.id")) or (
        f"{system_url}/Memory"
    )
    for index, data in enumerate(_members(session, base, str(memory_url)), 1):
        if not data.get("CapacityMiB"):
            continue
        result["memory"].append(
            {
                "bay": str(
                    data.get("DeviceLocator") or data.get("Name") or f"DIMM {index}"
                ),
                "manufacturer": normalize_manufacturer(
                    str(data.get("Manufacturer") or "")
                ),
                "model": str(
                    data.get("PartNumber") or data.get("Name") or f"DIMM {index}"
                ),
                "size_gb": int(data.get("CapacityMiB") or 0) // 1024,
                "class": str(data.get("MemoryDeviceType") or "DDR4"),
                "ecc": str(data.get("ErrorCorrection") or "").lower()
                not in {"", "no", "none"},
                "data_rate": int(
                    (
                        data.get("OperatingSpeedMhz")
                        or (data.get("AllowedSpeedsMHz") or [0])[0]
                    )
                    or 0
                ),
                "serial": str(data.get("SerialNumber") or ""),
            }
        )

    storage_url = ((system.get("Storage") or {}).get("@odata.id")) or (
        f"{system_url}/Storage"
    )
    seen_disks: set[str] = set()
    for controller in _members(session, base, str(storage_url)):
        for drive_ref in controller.get("Drives", []) or []:
            path = drive_ref.get("@odata.id") if isinstance(drive_ref, dict) else None
            if not path:
                continue
            data = _redfish_get(session, base, str(path))
            if not data:
                continue
            key = str(data.get("SerialNumber") or data.get("Id") or "")
            if key in seen_disks:
                continue
            seen_disks.add(key)
            media = str(data.get("MediaType") or "HDD").upper()
            media_type = {"HDD": "HD", "SSD": "SSD", "NVME": "NVME"}.get(media, media)
            result["disks"].append(
                {
                    "bay": f"Drive {len(result['disks']) + 1}",
                    "manufacturer": normalize_manufacturer(
                        str(data.get("Manufacturer") or data.get("Model") or "")
                    ),
                    "model": str(
                        data.get("Model") or f"Disk {len(result['disks']) + 1}"
                    ),
                    "size_gb": int(
                        int(data.get("CapacityBytes") or 0) / 1_000_000_000
                    ),
                    "type": media_type,
                    "speed": int(data.get("RotationSpeedRPM") or 0),
                    "serial": str(data.get("SerialNumber") or ""),
                    "protocol": str(data.get("Protocol") or ""),
                }
            )

    chassis_url = _discover_url(
        session,
        base,
        "Chassis",
        os.environ.get(
            "REDFISH_CHASSIS_PATH", "/redfish/v1/Chassis/System.Embedded.1"
        ),
    )
    thermal = _redfish_get(session, base, f"{chassis_url}/Thermal")
    for index, fan in enumerate((thermal or {}).get("Fans", []), 1):
        result["fans"].append(
            {
                "bay": f"Fan {index}",
                "name": str(fan.get("Name") or fan.get("FanName") or f"Fan {index}"),
                "rpm": int(fan.get("Reading") or 0),
                "status": "active"
                if (fan.get("Status") or {}).get("State") != "Absent"
                else "inventory",
            }
        )

    power = _redfish_get(session, base, f"{chassis_url}/Power")
    for index, psu in enumerate((power or {}).get("PowerSupplies", []), 1):
        result["psus"].append(
            {
                "bay": f"PSU {index}",
                "manufacturer": normalize_manufacturer(
                    str(psu.get("Manufacturer") or "")
                ),
                "model": str(psu.get("Model") or f"Power Supply {index}"),
                "serial": str(psu.get("SerialNumber") or ""),
                "wattage": int(psu.get("PowerCapacityWatts") or 0),
                "status": "active"
                if (psu.get("Status") or {}).get("State") != "Absent"
                else "inventory",
            }
        )

    log.debug(
        "Redfish %s: cpu=%d, memory=%d, disks=%d, fans=%d, psus=%d",
        base,
        len(result["cpu"]),
        len(result["memory"]),
        len(result["disks"]),
        len(result["fans"]),
        len(result["psus"]),
    )
    return result

class RedfishSource(Source):
    name = "redfish"

    def enabled(self, ctx: Any, state: SourceState) -> bool:
        options = ctx.options
        return bool(
            options.redfish_user
            and options.redfish_pass
            and any(
                device.get("redfish_ip")
                for device in ctx.manual.get("devices") or []
            )
        )

    def collect(self, ctx: Any, state: SourceState) -> None:
        options = ctx.options
        for device in ctx.manual.get("devices") or []:
            address = str(device.get("redfish_ip") or "").strip()
            if not address:
                continue
            data = read_redfish(
                f"https://{address}",
                options.redfish_user,
                options.redfish_pass,
                verify=options.redfish_verify,
            )
            if not data:
                continue
            system = data.get("system") or {}
            service_tag = str(system.get("SKU") or system.get("SerialNumber") or "").strip()
            system_uuid = str(system.get("UUID") or "").strip()
            refs: dict[str, set[str]] = {"redfish_ip": {address}}
            if service_tag:
                refs["service_tag"] = {service_tag}
            if system_uuid:
                refs["system_uuid"] = {system_uuid}
            resolved = ctx.resolver.resolve(refs, source="redfish", label=address)
            if resolved is None:
                log.warning("Redfish %s is not mapped to a manual device", address)
                continue
            name = str(resolved.get("name") or "")
            if not name:
                continue
            ctx.resolver.add_observation_claims(
                resolved,
                {"service_tag": {service_tag}, "system_uuid": {system_uuid}},
            )
            if name in state.redfish:
                raise InventoryError(f"multiple Redfish BMCs resolve to {name!r}")
            state.redfish[name] = {**data, "source": "redfish"}