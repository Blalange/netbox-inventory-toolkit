"""Read manual and generated inventory layers for discovery."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from netbox_inventory_toolkit.inventory.keys import inventory_files
from netbox_inventory_toolkit.inventory.util import as_list, normalize_mac

from .models import DeviceSnapshot, DiscoveryError, InventorySnapshot


def _read_layer(
    directory: Path,
    layer: str,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]], set[str], set[str]]:
    devices: list[dict[str, Any]] = []
    defaults: dict[str, Any] = {}
    device_types: list[dict[str, Any]] = []
    sites: set[str] = set()
    roles: set[str] = set()

    if not directory.is_dir():
        return devices, defaults, device_types, sites, roles

    for path in inventory_files(directory):
        for document in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if not isinstance(document, dict):
                continue
            document_layer = str(document.get("layer") or layer).strip().lower()
            if document_layer != layer:
                continue
            for key, value in document.items():
                if key == "devices" and isinstance(value, list):
                    devices.extend(copy.deepcopy(value))
                elif key == "defaults" and isinstance(value, dict):
                    defaults.update(copy.deepcopy(value))
                elif key == "device_types" and isinstance(value, list):
                    device_types.extend(copy.deepcopy(value))
                elif key == "sites" and isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict) and item.get("slug"):
                            sites.add(str(item["slug"]))
                elif key == "device_roles" and isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict) and item.get("slug"):
                            roles.add(str(item["slug"]))

    for device in devices:
        for key, value in defaults.items():
            if key == "tags":
                device["tags"] = sorted(
                    {
                        *[str(tag) for tag in as_list(device.get("tags"))],
                        *[str(tag) for tag in as_list(value)],
                    }
                )
            elif key not in device:
                device[key] = copy.deepcopy(value)
    return devices, defaults, device_types, sites, roles


def _snapshot(device: dict[str, Any], layer: str) -> DeviceSnapshot:
    macs: set[str] = set()
    raw_macs = [device.get("unifi_mac")]
    for interface in device.get("interfaces") or []:
        if isinstance(interface, dict):
            raw_macs.append(interface.get("mac_address"))
    for raw in raw_macs:
        if raw:
            try:
                macs.add(normalize_mac(str(raw)).lower())
            except Exception:  # noqa: BLE001 - malformed inventory is reported by check
                pass
    return DeviceSnapshot(
        name=str(device.get("name") or ""),
        layer=layer,
        site=str(device.get("site") or ""),
        role=str(device.get("role") or ""),
        device_type=str(device.get("device_type") or ""),
        status=str(device.get("status") or ""),
        serial=str(device.get("serial") or ""),
        service_tag=str(device.get("service_tag") or ""),
        system_uuid=str(device.get("system_uuid") or ""),
        redfish_ip=str(device.get("redfish_ip") or ""),
        ipmi_ip=str(device.get("ipmi_ip") or ""),
        snmp_ip=str(device.get("snmp_ip") or ""),
        oob_ip=str(device.get("oob_ip") or ""),
        unifi_ip=str(device.get("unifi_ip") or ""),
        unifi_mac=str(device.get("unifi_mac") or ""),
        talos_node=str(device.get("talos_node") or ""),
        description=str(device.get("description") or ""),
        tags=[str(tag) for tag in as_list(device.get("tags"))],
        macs=macs,
        raw=device,
    )


def _merge_device(manual: DeviceSnapshot, generated: DeviceSnapshot) -> DeviceSnapshot:
    """Manual values win; generated values fill missing locators and identity."""
    merged = copy.deepcopy(manual)
    for field in (
        "site",
        "role",
        "device_type",
        "status",
        "serial",
        "service_tag",
        "system_uuid",
        "redfish_ip",
        "ipmi_ip",
        "snmp_ip",
        "oob_ip",
        "unifi_ip",
        "unifi_mac",
        "talos_node",
        "description",
    ):
        if not getattr(merged, field):
            setattr(merged, field, getattr(generated, field))
    merged.macs.update(generated.macs)
    merged.tags = sorted({*merged.tags, *generated.tags})
    return merged


def load_snapshot(inventory: Path) -> InventorySnapshot:
    manual_dir = inventory / "manual"
    generated_dir = inventory / "generated"
    if not manual_dir.is_dir():
        raise DiscoveryError(f"manual inventory directory {manual_dir} does not exist")

    manual_docs, _, device_types, sites, roles = _read_layer(manual_dir, "manual")
    generated_docs, _, _, _, _ = _read_layer(generated_dir, "generated")

    manual = {
        str(device.get("name")): _snapshot(device, "manual")
        for device in manual_docs
        if device.get("name")
    }
    generated = {
        str(device.get("name")): _snapshot(device, "generated")
        for device in generated_docs
        if device.get("name")
    }

    devices: list[DeviceSnapshot] = []
    for name, device in manual.items():
        other = generated.get(name)
        devices.append(_merge_device(device, other) if other else device)
    for name, device in generated.items():
        if name not in manual:
            devices.append(device)

    settings: dict[str, Any] = {}
    for path in inventory_files(manual_dir):
        for document in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if isinstance(document, dict) and isinstance(document.get("settings"), dict):
                settings.update(document["settings"])

    return InventorySnapshot(
        manual_dir=manual_dir,
        generated_dir=generated_dir,
        devices=devices,
        manual_names=set(manual),
        generated_names=set(generated),
        device_types=device_types,
        sites=sites,
        roles=roles,
        settings=settings,
    )
