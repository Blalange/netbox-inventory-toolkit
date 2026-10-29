"""Render discovery reports as tables, YAML or JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from .models import DiscoveryError, DiscoveryItem, DiscoveryReport

STATUS_ORDER = ("conflict", "new", "update", "unreachable", "existing")


def _item_name(item: DiscoveryItem) -> str:
    if item.device:
        return item.device.name
    return str(item.suggested.get("name") or "-")


def _item_protocols(item: DiscoveryItem) -> str:
    return ",".join(sorted({probe.protocol for probe in item.probes})) or "-"


def _item_identity(item: DiscoveryItem) -> str:
    if item.device:
        return " / ".join(
            part
            for part in (item.device.device_type, item.device.serial)
            if part
        ) or "-"
    return " / ".join(
        str(part)
        for part in (item.suggested.get("model"), item.suggested.get("serial"))
        if part
    ) or "-"


def _item_action(item: DiscoveryItem) -> str:
    if item.status == "new":
        return "add manual entry"
    if item.status == "update":
        return "add " + ", ".join(sorted(item.updates))
    if item.status == "conflict":
        return "resolve conflict"
    if item.status == "unreachable":
        return "unreachable"
    return "complete"


def render_table(report: DiscoveryReport) -> str:
    rows = [
        (
            item.status,
            _item_name(item),
            ", ".join(sorted(item.addresses)) or "-",
            _item_protocols(item),
            _item_identity(item),
            _item_action(item),
        )
        for item in report.items
    ]
    headers = ("STATUS", "NAME", "ADDRESS", "PROTOCOLS", "MODEL / SERIAL", "ACTION")
    widths = [
        max(len(str(row[index])) for row in [headers, *rows])
        for index in range(len(headers))
    ]
    lines = [
        "  ".join(
            str(value).ljust(widths[index])
            for index, value in enumerate(headers)
        )
    ]
    lines.append("  ".join("-" * width for width in widths))
    for row in rows:
        lines.append(
            "  ".join(
                str(value).ljust(widths[index])
                for index, value in enumerate(row)
            )
        )
    notes = [
        f"{_item_name(item)}: {note}"
        for item in report.items
        for note in item.notes
    ]
    if notes:
        lines.append("")
        lines.append("Notes:")
        lines.extend(f"  - {note}" for note in notes)
    lines.append("")
    lines.append(
        f"Scanned {report.scanned} target(s) with "
        f"{', '.join(report.protocols) or 'no network probes'} "
        f"in {report.elapsed_seconds:.2f}s."
    )
    return "\n".join(lines) + "\n"


def _device_dict(item: DiscoveryItem) -> dict[str, Any] | None:
    if item.device is None:
        return None
    device = item.device
    return {
        "name": device.name,
        "layer": device.layer,
        "site": device.site,
        "role": device.role,
        "device_type": device.device_type,
        "status": device.status,
        "serial": device.serial,
        "service_tag": device.service_tag,
        "system_uuid": device.system_uuid,
        "redfish_ip": device.redfish_ip,
        "ipmi_ip": device.ipmi_ip,
        "snmp_ip": device.snmp_ip,
        "oob_ip": device.oob_ip,
        "macs": sorted(device.macs),
    }


def render_json(report: DiscoveryReport) -> str:
    payload = {
        "scanned": report.scanned,
        "elapsed_seconds": round(report.elapsed_seconds, 3),
        "protocols": report.protocols,
        "items": [
            {
                "status": item.status,
                "name": _item_name(item),
                "addresses": sorted(item.addresses),
                "protocols": sorted({probe.protocol for probe in item.probes}),
                "device": _device_dict(item),
                "conflicts": [device.name for device in item.conflicts],
                "updates": item.updates,
                "suggested": item.suggested,
                "notes": item.notes,
            }
            for item in report.items
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=False) + "\n"


def _yaml_document(items: list[DiscoveryItem]) -> str:
    devices = [
        item.suggested
        for item in items
        if item.status == "new" and item.suggested
    ]
    lines = [
        "# Suggested manual inventory entries.",
        "# Review role/device_type/site before merging.",
        "",
    ]
    if devices:
        lines.append(
            yaml.safe_dump(
                {"layer": "manual", "devices": devices},
                sort_keys=False,
                allow_unicode=True,
            )
        )
    else:
        lines.append("layer: manual\ndevices: []\n")

    updates = [item for item in items if item.status == "update"]
    if updates:
        lines.extend(["", "# Suggested additions to existing manual devices:"])
        for item in updates:
            name = _item_name(item)
            lines.append(f"# {name}:")
            for key, value in sorted(item.updates.items()):
                lines.append(f"#   {key}: {value}")

    conflicts = [item for item in items if item.status in {"conflict", "unreachable"}]
    if conflicts:
        lines.extend(["", "# Needs attention:"])
        for item in conflicts:
            detail = "; ".join(item.notes) or item.status
            lines.append(f"# {_item_name(item)} ({item.status}): {detail}")
    return "\n".join(lines).rstrip() + "\n"


def render_yaml(report: DiscoveryReport) -> str:
    return _yaml_document(report.items)


def write_new_devices(path: Path, report: DiscoveryReport) -> int:
    if path.exists():
        raise DiscoveryError(f"refusing to overwrite existing file {path}")
    entries = [
        item.suggested
        for item in report.items
        if item.status == "new" and item.suggested
    ]
    complete = [
        entry
        for entry in entries
        if entry.get("name")
        and entry.get("role")
        and entry.get("device_type")
        and entry.get("site")
    ]
    if not complete:
        return 0
    name_counts = set(report.snapshot.manual_names)
    duplicates = sorted(
        entry["name"] for entry in complete if entry["name"] in name_counts
    )
    if duplicates:
        raise DiscoveryError(
            "refusing to write duplicate device names: " + ", ".join(duplicates)
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Generated by netbox-inventory-toolkit discover. Review before use.\n"
        + yaml.safe_dump(
            {"layer": "manual", "devices": complete},
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return len(complete)


def write_report(report: DiscoveryReport, path: Path, output_format: str) -> None:
    if output_format == "json":
        content = render_json(report)
    elif output_format == "yaml":
        content = render_yaml(report)
    else:
        content = render_table(report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
