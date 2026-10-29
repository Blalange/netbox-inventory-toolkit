from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from netbox_inventory_toolkit.collect.context import CollectOptions
from netbox_inventory_toolkit.collect.pipeline import collect, selected_sources
from netbox_inventory_toolkit.inventory.util import InventoryError


def write_inventory(root: Path, devices: list[dict[str, Any]], **settings: Any) -> Path:
    manual = root / "manual"
    generated = root / "generated"
    manual.mkdir(parents=True)
    generated.mkdir()
    document = {
        "layer": "manual",
        "settings": {"site": "example", **settings},
        "sites": [{"name": "Example", "slug": "example"}],
        "manufacturers": [{"name": "Generic", "slug": "generic"}],
        "device_roles": [
            {"name": "Server", "slug": "server"},
            {"name": "Switch", "slug": "switch"},
        ],
        "device_types": [
            {
                "manufacturer": "generic",
                "model": "Generic switch",
                "slug": "generic-switch",
                "u_height": 1,
            },
            {
                "manufacturer": "generic",
                "model": "Generic server",
                "slug": "generic-server",
                "u_height": 1,
            },
        ],
        "devices": devices,
    }
    (manual / "site.yaml").write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )
    return root


def load_generated(root: Path, name: str) -> dict[str, Any]:
    return yaml.safe_load((root / "generated" / name).read_text(encoding="utf-8"))


def options(root: Path, sources: str) -> CollectOptions:
    return CollectOptions(inventory=root, sources_mode=sources)


def test_selected_sources_accepts_lists_and_aliases() -> None:
    assert selected_sources(CollectOptions(sources_mode="none")) == set()
    assert selected_sources(CollectOptions(sources_mode="snmp,ipmi")) == {
        "snmp",
        "ipmi",
    }
    with pytest.raises(InventoryError, match="unknown source"):
        selected_sources(CollectOptions(sources_mode="not-a-source"))


def test_collect_none_writes_empty_generated_files(tmp_path: Path) -> None:
    root = write_inventory(tmp_path / "inventory", [])
    summary = collect(options(root, "none"))

    assert summary.network_devices == 0
    assert summary.servers == 0
    assert load_generated(root, "network.yaml")["devices"] == []
    assert load_generated(root, "compute.yaml")["devices"] == []
    assert load_generated(root, "module-types.yaml")["module_types"] == []


def test_missing_site_is_an_error(tmp_path: Path) -> None:
    root = write_inventory(tmp_path / "inventory", [])
    (root / "manual" / "site.yaml").write_text(
        "layer: manual\nsettings: {}\n", encoding="utf-8"
    )
    with pytest.raises(InventoryError, match="settings.site is required"):
        collect(options(root, "none"))


def test_snmp_only_builds_a_complete_device(monkeypatch, tmp_path: Path) -> None:
    from netbox_inventory_toolkit.collect.sources import snmp as snmp_source

    async def fake_collect_snmp(targets, **kwargs):
        return [
            {
                "ip": "192.0.2.10",
                "sys": {"name": "edge-switch"},
                "base_mac": "00:11:22:33:44:55",
                "serial": "SW123",
                "vlans": {1: "default"},
                "interface_vlans": {1: {"untagged": [1], "tagged": []}},
                "interfaces": [
                    {
                        "if_index": 1,
                        "name": "Port 1",
                        "alias": "",
                        "descr": "Port 1",
                        "type": 6,
                        "mtu": 1500,
                        "mac": "00:11:22:33:44:55",
                        "admin_status": 1,
                        "oper_status": 1,
                        "speed": 1000,
                        "candidates": ["Port 1"],
                    }
                ],
                "lldp": [],
                "fdb": [],
            }
        ]

    monkeypatch.setattr(snmp_source, "collect_snmp", fake_collect_snmp)
    root = write_inventory(
        tmp_path / "inventory",
        [
            {
                "name": "edge-switch",
                "site": "example",
                "role": "switch",
                "device_type": "generic-switch",
                "snmp_ip": "192.0.2.10",
                "snmp_interfaces": True,
            }
        ],
    )

    summary = collect(options(root, "snmp"))
    network = load_generated(root, "network.yaml")

    assert summary.network_devices == 1
    device = network["devices"][0]
    assert device["name"] == "edge-switch"
    assert device["role"] == "switch"
    assert device["device_type"] == "generic-switch"
    assert [interface["name"] for interface in device["interfaces"]] == ["Port 1"]


def test_ipmi_only_builds_server_modules(monkeypatch, tmp_path: Path) -> None:
    from netbox_inventory_toolkit.collect.sources import ipmi as ipmi_source

    payload = {
        "system": {"Model": "Generic Server", "SerialNumber": "ABC123"},
        "cpu": [],
        "memory": [],
        "disks": [],
        "fans": [{"bay": "Fan 1", "name": "Fan 1", "rpm": 4000, "status": "active"}],
        "psus": [],
    }
    monkeypatch.setattr(ipmi_source, "read_ipmi", lambda *args: payload)
    monkeypatch.setattr(ipmi_source.shutil, "which", lambda _binary: "/usr/bin/ipmitool")
    root = write_inventory(
        tmp_path / "inventory",
        [
            {
                "name": "server-01",
                "site": "example",
                "role": "server",
                "device_type": "generic-server",
                "ipmi_ip": "192.0.2.20",
                "service_tag": "ABC123",
            }
        ],
    )

    summary = collect(options(root, "ipmi"))
    compute = load_generated(root, "compute.yaml")

    assert summary.servers == 1
    device = compute["devices"][0]
    assert device["name"] == "server-01"
    assert device["modules"][0]["bay"] == "Fan 1"


def test_auto_skips_unconfigured_unifi(monkeypatch, tmp_path: Path) -> None:
    from netbox_inventory_toolkit.collect.sources import unifi as unifi_source

    def fail(*args, **kwargs):
        raise AssertionError("UniFi must not run in auto mode without configuration")

    monkeypatch.setattr(unifi_source, "unifi_get", fail)
    root = write_inventory(tmp_path / "inventory", [])
    collect(options(root, "auto"))


def test_explicit_unconfigured_source_is_an_error(tmp_path: Path) -> None:
    root = write_inventory(tmp_path / "inventory", [])
    with pytest.raises(InventoryError, match="not configured"):
        collect(options(root, "unifi"))
