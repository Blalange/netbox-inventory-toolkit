from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from netbox_inventory_toolkit.discover.cli import main as discover_main
from netbox_inventory_toolkit.discover.inventory_scan import load_snapshot
from netbox_inventory_toolkit.discover.match import match_probes
from netbox_inventory_toolkit.discover.models import (
    DiscoveryError,
    DiscoveryItem,
    DiscoveryOptions,
    DiscoveryReport,
    ProbeResult,
)
from netbox_inventory_toolkit.discover.report import (
    render_json,
    render_yaml,
    write_new_devices,
)
from netbox_inventory_toolkit.discover.targets import expand_targets


def write_inventory(
    root: Path,
    manual_devices: list[dict[str, Any]],
    *,
    generated_devices: list[dict[str, Any]] | None = None,
    settings: dict[str, Any] | None = None,
) -> Path:
    manual = root / "manual"
    generated = root / "generated"
    manual.mkdir(parents=True)
    generated.mkdir()
    document = {
        "layer": "manual",
        "settings": {"site": "example", **(settings or {})},
        "sites": [{"name": "Example", "slug": "example"}],
        "manufacturers": [
            {"name": "Dell", "slug": "dell"},
            {"name": "Generic", "slug": "generic"},
        ],
        "device_roles": [
            {"name": "Server", "slug": "server"},
            {"name": "Switch", "slug": "switch"},
        ],
        "device_types": [
            {
                "manufacturer": "dell",
                "model": "PowerEdge R250",
                "slug": "dell-poweredge-r250",
                "u_height": 1,
            },
            {
                "manufacturer": "generic",
                "model": "Generic switch",
                "slug": "generic-switch",
                "u_height": 1,
            },
        ],
        "devices": manual_devices,
    }
    (manual / "site.yaml").write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )
    if generated_devices is not None:
        (generated / "devices.yaml").write_text(
            yaml.safe_dump(
                {"layer": "generated", "devices": generated_devices},
                sort_keys=False,
            ),
            encoding="utf-8",
        )
    return root


def options(root: Path, **kwargs: Any) -> DiscoveryOptions:
    return DiscoveryOptions(inventory=root, **kwargs)


def test_inventory_scan_finds_generated_gap(tmp_path: Path) -> None:
    root = write_inventory(
        tmp_path / "inventory",
        [
            {
                "name": "server-01",
                "site": "example",
                "role": "server",
                "device_type": "dell-poweredge-r250",
                "redfish_ip": "192.0.2.20",
            },
            {
                "name": "unconfigured",
                "site": "example",
                "role": "server",
                "device_type": "dell-poweredge-r250",
            },
        ],
        generated_devices=[
            {
                "name": "ap-01",
                "site": "example",
                "role": "switch",
                "device_type": "generic-switch",
                "platform": "unifi",
                "serial": "AP123",
                "interfaces": [
                    {
                        "name": "eth0",
                        "mac_address": "00:11:22:33:44:55",
                        "ip_addresses": [{"address": "192.0.2.30/24"}],
                    }
                ],
            }
        ],
    )
    snapshot = load_snapshot(root)
    items = match_probes(snapshot, [], options(root))

    generated = next(item for item in items if item.suggested.get("name") == "ap-01")
    assert generated.status == "new"
    assert generated.suggested["site"] == "example"
    assert generated.suggested["unifi_mac"] == "00:11:22:33:44:55"
    assert generated.suggested["unifi_ip"] == "192.0.2.30"

    unconfigured = next(item for item in items if item.device and item.device.name == "unconfigured")
    assert unconfigured.status == "existing"
    assert any("no source locators" in note for note in unconfigured.notes)


def test_target_expansion_includes_inventory_and_excludes(tmp_path: Path) -> None:
    root = write_inventory(
        tmp_path / "inventory",
        [
            {
                "name": "server-01",
                "site": "example",
                "role": "server",
                "device_type": "dell-poweredge-r250",
                "redfish_ip": "192.0.2.20",
                "snmp_ip": "192.0.2.21",
            }
        ],
    )
    snapshot = load_snapshot(root)
    targets = expand_targets(
        options(
            root,
            targets=["192.0.2.20"],
            networks=["192.0.2.0/30"],
            excludes=["192.0.2.1"],
        ),
        snapshot,
    )

    assert {target.address for target in targets} == {"192.0.2.20", "192.0.2.21", "192.0.2.2"}
    assert next(target for target in targets if target.address == "192.0.2.20").source == "explicit"


def test_target_expansion_respects_max_hosts(tmp_path: Path) -> None:
    root = write_inventory(tmp_path / "inventory", [])
    snapshot = load_snapshot(root)
    with pytest.raises(DiscoveryError, match="refusing to scan"):
        expand_targets(options(root, networks=["192.0.2.0/24"], max_hosts=10), snapshot)
    with pytest.raises(DiscoveryError, match="pass --force"):
        expand_targets(options(root, networks=["192.0.2.0/23"], max_hosts=10000), snapshot)


def test_existing_device_gets_missing_locator(tmp_path: Path) -> None:
    root = write_inventory(
        tmp_path / "inventory",
        [
            {
                "name": "server-01",
                "site": "example",
                "role": "server",
                "device_type": "dell-poweredge-r250",
                "service_tag": "ABC123",
            }
        ],
    )
    snapshot = load_snapshot(root)
    probe = ProbeResult(
        protocol="redfish",
        address="192.0.2.20",
        reachable=True,
        identity={"serial": "ABC123", "model": "PowerEdge R250"},
    )
    items = match_probes(snapshot, [probe], options(root))
    item = next(item for item in items if item.device and item.device.name == "server-01")
    assert item.status == "update"
    assert item.updates == {"redfish_ip": "192.0.2.20", "oob_ip": "192.0.2.20"}


def test_conflicting_identity_is_reported(tmp_path: Path) -> None:
    root = write_inventory(
        tmp_path / "inventory",
        [
            {
                "name": "server-01",
                "site": "example",
                "role": "server",
                "device_type": "dell-poweredge-r250",
                "service_tag": "ABC123",
            },
            {
                "name": "server-02",
                "site": "example",
                "role": "server",
                "device_type": "dell-poweredge-r250",
                "service_tag": "ABC123",
            },
        ],
    )
    snapshot = load_snapshot(root)
    probe = ProbeResult(
        protocol="redfish",
        address="192.0.2.20",
        reachable=True,
        identity={"serial": "ABC123"},
    )
    items = match_probes(snapshot, [probe], options(root))
    item = next(item for item in items if item.status == "conflict")
    assert {device.name for device in item.conflicts} == {"server-01", "server-02"}


def test_redfish_and_ipmi_group_into_one_new_device(tmp_path: Path) -> None:
    root = write_inventory(tmp_path / "inventory", [])
    snapshot = load_snapshot(root)
    probes = [
        ProbeResult(
            protocol="redfish",
            address="192.0.2.20",
            reachable=True,
            identity={
                "name": "server-01",
                "manufacturer": "Dell",
                "model": "PowerEdge R250",
                "serial": "ABC123",
                "system_uuid": "uuid-1",
            },
        ),
        ProbeResult(
            protocol="ipmi",
            address="192.0.2.21",
            reachable=True,
            identity={"manufacturer": "Dell", "model": "PowerEdge R250", "serial": "ABC123"},
        ),
    ]
    items = match_probes(snapshot, probes, options(root))
    new_items = [item for item in items if item.status == "new"]
    assert len(new_items) == 1
    suggested = new_items[0].suggested
    assert suggested["name"] == "server-01"
    assert suggested["redfish_ip"] == "192.0.2.20"
    assert suggested["ipmi_ip"] == "192.0.2.21"
    assert suggested["device_type"] == "dell-poweredge-r250"
    assert new_items[0].addresses == {"192.0.2.20", "192.0.2.21"}


def test_auth_required_without_identity_is_not_a_new_device(tmp_path: Path) -> None:
    root = write_inventory(tmp_path / "inventory", [])
    snapshot = load_snapshot(root)
    probe = ProbeResult(
        protocol="redfish",
        address="192.0.2.20",
        reachable=True,
        auth_required=True,
        error="authentication required",
    )
    items = match_probes(snapshot, [probe], options(root))
    assert [item.status for item in items] == ["unreachable"]
    assert "authentication required" in items[0].notes[0]


def test_cli_inventory_only_json(tmp_path: Path, capsys) -> None:
    root = write_inventory(
        tmp_path / "inventory",
        [
            {
                "name": "server-01",
                "site": "example",
                "role": "server",
                "device_type": "dell-poweredge-r250",
                "redfish_ip": "192.0.2.20",
            }
        ],
        generated_devices=[
            {
                "name": "ap-01",
                "site": "example",
                "role": "switch",
                "device_type": "generic-switch",
            }
        ],
    )
    assert discover_main(["--inventory", str(root), "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["scanned"] == 0
    assert payload["items"][0]["status"] == "new"
    assert payload["items"][0]["name"] == "ap-01"


def test_render_yaml_is_a_manual_layer(tmp_path: Path) -> None:
    root = write_inventory(tmp_path / "inventory", [])
    snapshot = load_snapshot(root)
    report = DiscoveryReport(
        items=[
            DiscoveryItem(
                status="new",
                suggested={
                    "name": "server-01",
                    "site": "example",
                    "role": "server",
                    "device_type": "dell-poweredge-r250",
                },
            )
        ],
        scanned=0,
        elapsed_seconds=0.1,
        protocols=[],
        snapshot=snapshot,
    )
    document = yaml.safe_load(render_yaml(report))
    assert document["layer"] == "manual"
    assert document["devices"][0]["name"] == "server-01"
    assert yaml.safe_load(render_json(report))["items"][0]["status"] == "new"


def test_write_new_refuses_overwrite_and_duplicates(tmp_path: Path) -> None:
    root = write_inventory(
        tmp_path / "inventory",
        [
            {
                "name": "server-01",
                "site": "example",
                "role": "server",
                "device_type": "dell-poweredge-r250",
            }
        ],
    )
    snapshot = load_snapshot(root)
    report = DiscoveryReport(
        items=[
            DiscoveryItem(
                status="new",
                suggested={
                    "name": "server-01",
                    "site": "example",
                    "role": "server",
                    "device_type": "dell-poweredge-r250",
                },
            )
        ],
        scanned=0,
        elapsed_seconds=0.1,
        protocols=[],
        snapshot=snapshot,
    )
    target = tmp_path / "new.yaml"
    with pytest.raises(DiscoveryError, match="duplicate"):
        write_new_devices(target, report)
    target.write_text("layer: manual\n", encoding="utf-8")
    with pytest.raises(DiscoveryError, match="overwrite"):
        write_new_devices(target, report)
