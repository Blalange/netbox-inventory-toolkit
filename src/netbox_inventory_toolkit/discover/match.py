"""Match probe observations against the existing inventory."""

from __future__ import annotations

import difflib
import ipaddress
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from netbox_inventory_toolkit.collect.hardware import normalize_manufacturer
from netbox_inventory_toolkit.collect.identity import normalize_ref

from .models import (
    DeviceSnapshot,
    DiscoveryItem,
    DiscoveryOptions,
    InventorySnapshot,
    ProbeResult,
)


@dataclass
class _NewGroup:
    probes: list[ProbeResult] = field(default_factory=list)

    @property
    def addresses(self) -> set[str]:
        return {probe.address for probe in self.probes}

    @property
    def serial(self) -> str:
        return next((probe.serial for probe in self.probes if probe.serial), "")

    @property
    def system_uuid(self) -> str:
        return next(
            (probe.system_uuid for probe in self.probes if probe.system_uuid), ""
        )

    @property
    def name(self) -> str:
        return next((probe.name for probe in self.probes if probe.name), "")


def _claims(snapshot: InventorySnapshot) -> dict[tuple[str, str], set[str]]:
    claims: dict[tuple[str, str], set[str]] = defaultdict(set)

    def add(kind: str, value: Any, name: str) -> None:
        normalized = normalize_ref(kind, value)
        if normalized:
            claims[(kind, normalized)].add(name)

    for device in snapshot.devices:
        name = device.name
        add("name", name, name)
        add("snmp_name", name, name)
        add("service_tag", device.service_tag, name)
        add("service_tag", device.serial, name)
        add("system_uuid", device.system_uuid, name)
        add("redfish_ip", device.redfish_ip, name)
        add("ipmi_ip", device.ipmi_ip, name)
        add("snmp_ip", device.snmp_ip, name)
        add("oob_ip", device.oob_ip, name)
        add("unifi_ip", device.unifi_ip, name)
        for mac in device.macs:
            add("mac", mac, name)
    return claims


def _probe_refs(probe: ProbeResult) -> dict[str, set[str]]:
    refs: dict[str, set[str]] = {}
    if probe.serial:
        refs["service_tag"] = {probe.serial}
    if probe.system_uuid:
        refs["system_uuid"] = {probe.system_uuid}
    if probe.name:
        refs["name"] = {probe.name}
        refs["snmp_name"] = {probe.name}
    probe_mac = probe.identity.get("mac")
    if probe_mac:
        refs["mac"] = {str(probe_mac)}
    locator_kind = {
        "redfish": "redfish_ip",
        "ipmi": "ipmi_ip",
        "snmp": "snmp_ip",
    }.get(probe.protocol)
    if locator_kind:
        refs[locator_kind] = {probe.address}
    if probe.protocol in {"redfish", "ipmi"}:
        refs["oob_ip"] = {probe.address}
    return refs


def _resolve(
    probe: ProbeResult,
    claims: dict[tuple[str, str], set[str]],
) -> list[str]:
    candidates: set[str] = set()
    for kind, values in _probe_refs(probe).items():
        for value in values:
            normalized = normalize_ref(kind, value)
            if normalized:
                candidates.update(claims.get((kind, normalized), set()))
    return sorted(candidates)


def _update_value(
    device: DeviceSnapshot,
    field: str,
    value: str,
    updates: dict[str, Any],
    conflicts: list[str],
) -> None:
    if not value:
        return
    current = getattr(device, field, "")
    if not current:
        updates[field] = value
    elif str(current).strip().lower() != value.strip().lower():
        conflicts.append(
            f"{field}: inventory has {current!r}, probe reported {value!r}"
        )


def _match_device_type(
    device_types: list[dict[str, Any]],
    manufacturer: str,
    model: str,
) -> str:
    if not model:
        return ""
    manufacturer_slug = normalize_manufacturer(manufacturer) if manufacturer else ""
    normalized_model = re.sub(r"[^a-z0-9]+", " ", model.lower()).strip()
    best: tuple[float, str] = (0.0, "")
    for item in device_types:
        slug = str(item.get("slug") or "")
        candidate_model = str(item.get("model") or "")
        if not slug or not candidate_model:
            continue
        candidate_normalized = re.sub(
            r"[^a-z0-9]+", " ", candidate_model.lower()
        ).strip()
        if not candidate_normalized:
            continue
        score = difflib.SequenceMatcher(
            None, normalized_model, candidate_normalized
        ).ratio()
        if normalized_model in candidate_normalized or candidate_normalized in normalized_model:
            score = max(score, 0.9)
        if manufacturer_slug and str(item.get("manufacturer") or "") != manufacturer_slug:
            score -= 0.15
        if score > best[0]:
            best = (score, slug)
    return best[1] if best[0] >= 0.72 else ""


def _role_hint(group: _NewGroup) -> str:
    text = " ".join(
        [
            str(probe.identity.get("description") or "")
            for probe in group.probes
        ]
    ).lower()
    if "switch" in text:
        return "switch"
    if "router" in text or "gateway" in text:
        return "gateway"
    if "access point" in text or "wireless" in text or "wifi" in text:
        return "access-point"
    return ""


def _unique_name(base: str, existing: set[str]) -> str:
    if base not in existing:
        return base
    index = 2
    while f"{base}-{index}" in existing:
        index += 1
    return f"{base}-{index}"


def _hostname(address: str) -> str:
    try:
        return f"host-{ipaddress.ip_address(address).exploded.replace(':', '-')[-3:]}"
    except ValueError:
        return "discovered-host"


def _suggest(
    group: _NewGroup,
    snapshot: InventorySnapshot,
    options: DiscoveryOptions,
) -> tuple[dict[str, Any], list[str]]:
    chosen = group.probes[0]
    name = group.name or _hostname(chosen.address)
    existing_names = {device.name for device in snapshot.devices}
    name = _unique_name(name, existing_names)
    generated = next(
        (device for device in snapshot.devices if device.name == name),
        None,
    )
    manufacturer = next(
        (probe.manufacturer for probe in group.probes if probe.manufacturer), ""
    )
    model = next((probe.model for probe in group.probes if probe.model), "")
    role = (
        (generated.role if generated else "")
        or options.default_role
        or _role_hint(group)
    )
    device_type = (
        (generated.device_type if generated else "")
        or options.default_device_type
        or _match_device_type(snapshot.device_types, manufacturer, model)
    )
    site = str(snapshot.settings.get("site") or (generated.site if generated else ""))
    locators: dict[str, str] = {}
    for probe in group.probes:
        field = {
            "redfish": "redfish_ip",
            "ipmi": "ipmi_ip",
            "snmp": "snmp_ip",
        }.get(probe.protocol)
        if field:
            locators.setdefault(field, probe.address)
    if "redfish_ip" in locators or "ipmi_ip" in locators:
        locators.setdefault(
            "oob_ip",
            locators.get("redfish_ip") or locators.get("ipmi_ip") or "",
        )

    suggested: dict[str, Any] = {
        "name": name,
        "site": site,
        "role": role,
        "device_type": device_type,
        "status": "active",
    }
    if manufacturer:
        suggested["manufacturer"] = manufacturer
    if model:
        suggested["model"] = model
    if group.serial:
        suggested["serial"] = group.serial
        if any(probe.protocol in {"redfish", "ipmi"} for probe in group.probes):
            suggested["service_tag"] = group.serial
    if group.system_uuid:
        suggested["system_uuid"] = group.system_uuid
    for field, value in locators.items():
        if value:
            suggested[field] = value
    protocols = ", ".join(sorted({probe.protocol for probe in group.probes}))
    suggested["description"] = f"Discovered via {protocols} on {date.today().isoformat()}."
    default_tag = str(snapshot.settings.get("default_tag") or "")
    if default_tag:
        suggested["tags"] = [default_tag]

    notes: list[str] = []
    if not role:
        notes.append("role is unknown; set role or --default-role")
    if not device_type:
        notes.append("device_type is unknown; set device_type or --default-device-type")
    if not site:
        notes.append("site is unknown; set settings.site or the device site")
    for probe in group.probes:
        if probe.auth_required:
            notes.append(f"{probe.protocol} at {probe.address} requires authentication")
        elif probe.error:
            notes.append(f"{probe.protocol} at {probe.address}: {probe.error}")
    return suggested, notes


def _find_group(groups: list[_NewGroup], probe: ProbeResult) -> _NewGroup:
    for group in groups:
        if probe.address in group.addresses:
            return group
        if probe.serial and group.serial and probe.serial.lower() == group.serial.lower():
            return group
        if (
            probe.system_uuid
            and group.system_uuid
            and probe.system_uuid.lower() == group.system_uuid.lower()
        ):
            return group
        if probe.name and group.name and probe.name.lower() == group.name.lower():
            return group
    group = _NewGroup()
    groups.append(group)
    return group


def _has_locator(device: DeviceSnapshot) -> bool:
    return bool(
        device.redfish_ip
        or device.ipmi_ip
        or device.snmp_ip
        or device.oob_ip
        or device.unifi_ip
        or device.talos_node
    )


def _first_interface_value(
    device: DeviceSnapshot,
    key: str,
) -> str:
    for interface in device.raw.get("interfaces") or []:
        if not isinstance(interface, dict):
            continue
        if key == "mac":
            value = str(interface.get("mac_address") or "")
            if value:
                return value
        elif key == "ip":
            for address in interface.get("ip_addresses") or []:
                if isinstance(address, dict) and address.get("address"):
                    return str(address["address"]).split("/", 1)[0]
    return ""


def _suggestion_from_device(
    device: DeviceSnapshot,
    snapshot: InventorySnapshot,
    options: DiscoveryOptions,
) -> tuple[dict[str, Any], list[str]]:
    suggested: dict[str, Any] = {
        "name": device.name,
        "site": device.site or str(snapshot.settings.get("site") or ""),
        "role": device.role or options.default_role,
        "device_type": device.device_type or options.default_device_type,
        "status": device.status or "active",
    }
    for key in (
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
        value = getattr(device, key, "")
        if value:
            suggested[key] = value
    if str(device.raw.get("platform") or "").lower() == "unifi":
        unifi_mac = device.unifi_mac or _first_interface_value(device, "mac")
        unifi_ip = device.unifi_ip or _first_interface_value(device, "ip")
        if unifi_mac and "unifi_mac" not in suggested:
            suggested["unifi_mac"] = unifi_mac
        if unifi_ip and "unifi_ip" not in suggested:
            suggested["unifi_ip"] = unifi_ip
        if device.serial and "unifi_serial" not in suggested:
            suggested["unifi_serial"] = device.serial
    if device.tags:
        suggested["tags"] = device.tags
    notes: list[str] = []
    if not suggested.get("role"):
        notes.append("role is unknown; set role or --default-role")
    if not suggested.get("device_type"):
        notes.append(
            "device_type is unknown; set device_type or --default-device-type"
        )
    if not suggested.get("site"):
        notes.append("site is unknown; set settings.site or the device site")
    return suggested, notes


def _merge_probe_into_suggestion(
    suggested: dict[str, Any],
    probe: ProbeResult,
) -> list[str]:
    notes: list[str] = []
    field = {
        "redfish": "redfish_ip",
        "ipmi": "ipmi_ip",
        "snmp": "snmp_ip",
    }.get(probe.protocol)
    if field:
        current = str(suggested.get(field) or "")
        if not current:
            suggested[field] = probe.address
        elif current != probe.address:
            notes.append(f"{field}: generated inventory has {current!r}, probe found {probe.address!r}")
    if probe.protocol in {"redfish", "ipmi"} and not suggested.get("oob_ip"):
        suggested["oob_ip"] = probe.address
    if probe.serial:
        if not suggested.get("serial"):
            suggested["serial"] = probe.serial
        if probe.protocol in {"redfish", "ipmi"} and not suggested.get("service_tag"):
            suggested["service_tag"] = probe.serial
    if probe.system_uuid and not suggested.get("system_uuid"):
        suggested["system_uuid"] = probe.system_uuid
    if probe.auth_required:
        notes.append(f"{probe.protocol} at {probe.address} requires authentication")
    elif probe.error:
        notes.append(f"{probe.protocol} at {probe.address}: {probe.error}")
    return notes


def inventory_gap_items(
    snapshot: InventorySnapshot,
    options: DiscoveryOptions,
) -> list[DiscoveryItem]:
    items: list[DiscoveryItem] = []
    for device in snapshot.devices:
        if (
            device.layer == "generated"
            and device.name not in snapshot.manual_names
        ):
            suggested, notes = _suggestion_from_device(device, snapshot, options)
            addresses = {
                value
                for value in (
                    device.redfish_ip,
                    device.ipmi_ip,
                    device.snmp_ip,
                    device.oob_ip,
                )
                if value
            }
            items.append(
                DiscoveryItem(
                    status="new",
                    device=device,
                    suggested=suggested,
                    notes=notes,
                    addresses=addresses,
                )
            )
        elif device.layer == "manual" and not _has_locator(device):
            items.append(
                DiscoveryItem(
                    status="existing",
                    device=device,
                    notes=[
                        "no source locators; add redfish_ip, ipmi_ip, snmp_ip or run network discovery"
                    ],
                )
            )
    return items


def _find_device_by_address(
    snapshot: InventorySnapshot,
    address: str,
) -> DeviceSnapshot | None:
    for device in snapshot.devices:
        if address in {
            device.redfish_ip,
            device.ipmi_ip,
            device.snmp_ip,
            device.oob_ip,
        }:
            return device
    return None


def match_probes(
    snapshot: InventorySnapshot,
    probes: list[ProbeResult],
    options: DiscoveryOptions,
) -> list[DiscoveryItem]:
    claims = _claims(snapshot)
    devices_by_name = {device.name: device for device in snapshot.devices}
    items = inventory_gap_items(snapshot, options)
    gap_by_name = {
        item.device.name: item for item in items if item.device is not None
    }
    generated_new = {
        name: item
        for name, item in gap_by_name.items()
        if item.device is not None
        and item.device.layer == "generated"
        and name not in snapshot.manual_names
    }
    existing: dict[str, DiscoveryItem] = {}
    new_groups: list[_NewGroup] = []
    unreachable: dict[str, DiscoveryItem] = {}

    for probe in probes:
        if probe.auth_required and not probe.identity:
            device = _find_device_by_address(snapshot, probe.address)
            if device is None:
                items.append(
                    DiscoveryItem(
                        status="unreachable",
                        probes=[probe],
                        notes=[
                            f"{probe.protocol}: authentication required at {probe.address}"
                        ],
                        addresses={probe.address},
                    )
                )
                continue

        if not probe.reachable:
            device = _find_device_by_address(snapshot, probe.address)
            if device is not None and device.name in generated_new:
                item = generated_new[device.name]
                item.probes.append(probe)
                item.addresses.add(probe.address)
                item.notes.append(f"{probe.protocol}: {probe.error or 'no response'}")
                continue
            key = device.name if device else f"unreachable:{probe.address}"
            item = unreachable.get(key)
            if item is None:
                item = DiscoveryItem(
                    status="unreachable",
                    probes=[probe],
                    device=device,
                    notes=[f"{probe.protocol}: {probe.error or 'no response'}"],
                    addresses={probe.address},
                )
                unreachable[key] = item
                items.append(item)
            else:
                item.probes.append(probe)
                item.addresses.add(probe.address)
                if probe.error:
                    item.notes.append(f"{probe.protocol}: {probe.error}")
            continue

        candidates = _resolve(probe, claims)
        if len(candidates) > 1:
            item = DiscoveryItem(
                status="conflict",
                probes=[probe],
                conflicts=[devices_by_name[name] for name in candidates if name in devices_by_name],
                notes=[
                    "identity matches multiple inventory devices: "
                    + ", ".join(candidates)
                ],
                addresses={probe.address},
            )
            items.append(item)
            continue
        if len(candidates) == 1:
            name = candidates[0]
            device = devices_by_name.get(name)
            if device is None:
                continue
            if device.layer == "generated" and name not in snapshot.manual_names:
                item = generated_new.get(name)
                if item is None:
                    suggested, notes = _suggestion_from_device(device, snapshot, options)
                    item = DiscoveryItem(
                        status="new",
                        device=device,
                        suggested=suggested,
                        notes=notes,
                    )
                    generated_new[name] = item
                    items.append(item)
                item.probes.append(probe)
                item.addresses.add(probe.address)
                item.notes.extend(_merge_probe_into_suggestion(item.suggested, probe))
                continue
            item = existing.get(name) or gap_by_name.get(name)
            if item is None:
                item = DiscoveryItem(
                    status="existing",
                    device=device,
                    addresses={probe.address},
                )
                items.append(item)
            existing[name] = item
            item.probes.append(probe)
            item.addresses.add(probe.address)
            continue

        group = _find_group(new_groups, probe)
        group.probes.append(probe)

    for _name, item in existing.items():
        device = item.device
        if device is None:
            continue
        updates: dict[str, Any] = {}
        conflicts: list[str] = []
        for probe in item.probes:
            if probe.protocol == "redfish":
                _update_value(device, "redfish_ip", probe.address, updates, conflicts)
            elif probe.protocol == "ipmi":
                _update_value(device, "ipmi_ip", probe.address, updates, conflicts)
            elif probe.protocol == "snmp":
                _update_value(device, "snmp_ip", probe.address, updates, conflicts)
            if probe.protocol in {"redfish", "ipmi"}:
                _update_value(device, "oob_ip", probe.address, updates, conflicts)
            if probe.serial:
                _update_value(device, "service_tag", probe.serial, updates, conflicts)
            if probe.system_uuid:
                _update_value(device, "system_uuid", probe.system_uuid, updates, conflicts)
            if probe.auth_required:
                item.notes.append(
                    f"{probe.protocol} at {probe.address} requires authentication"
                )
            elif probe.error:
                item.notes.append(
                    f"{probe.protocol} at {probe.address}: {probe.error}"
                )
        item.updates = updates
        if updates:
            item.notes = [
                note
                for note in item.notes
                if not note.startswith("no source locators")
            ]
        if conflicts:
            item.status = "conflict"
            item.notes.extend(conflicts)
        elif updates:
            item.status = "update"

    for group in new_groups:
        suggested, notes = _suggest(group, snapshot, options)
        items.append(
            DiscoveryItem(
                status="new",
                probes=list(group.probes),
                suggested=suggested,
                notes=notes,
                addresses=group.addresses,
            )
        )

    order = {"conflict": 0, "new": 1, "update": 2, "unreachable": 3, "existing": 4}
    items.sort(
        key=lambda item: (
            order.get(item.status, 9),
            (item.device.name if item.device else item.suggested.get("name", "")),
            sorted(item.addresses)[0] if item.addresses else "",
        )
    )
    return items
