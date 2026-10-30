from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from netbox_inventory_toolkit.collect.context import CollectOptions
from netbox_inventory_toolkit.collect.settings import (
    ConfigError,
    find_settings_file,
    legacy_config,
    load_settings_file,
)
from netbox_inventory_toolkit.inventory.keys import inventory_files


def write_settings(root: Path, document: dict) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "settings.yml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def manual() -> dict:
    return {
        "settings": {
            "site": "example",
            "default_tag": "production",
            "roles": {"control_plane": "control-plane", "worker": "worker"},
            "compute_roles": ["server"],
        },
        "unifi_models": {"OLD": "old-device"},
        "unifi_vlan_names": {"4": "Legacy"},
        "devices": [],
    }


def test_loads_profiles_and_resolves_environment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("UNIFI_PASS", "secret")
    path = write_settings(
        tmp_path,
        {
            "version": 1,
            "collector": {
                "sources": "auto",
                "snmp": {"port_names": "port-number", "auto_unifi": False},
            },
            "defaults": {
                "site": "bygg",
                "unifi_models": {"NEW": "new-device"},
                "unifi_vlan_names": {5: "New"},
            },
            "sources": {
                "unifi": [
                    {
                        "name": "main",
                        "url": "https://unifi.example",
                        "site": "glemmen",
                        "username": "poller",
                        "password": {"from_env": "UNIFI_PASS"},
                        "snmp_credential": "legacy",
                    }
                ],
                "snmp": [
                    {
                        "name": "default",
                        "community": {"from_env": "SNMP_COMMUNITY", "default": "public"},
                        "default": True,
                    },
                    {
                        "name": "legacy",
                        "community": "private",
                        "match": {"devices": ["old-*"], "cidrs": ["192.0.2.0/24"]},
                    },
                ],
                "redfish": [
                    {
                        "name": "idrac",
                        "username": "readonly",
                        "password": {"from_env": "REDFISH_PASS", "default": "readonly"},
                        "verify_tls": False,
                        "default": True,
                    }
                ],
                "ipmi": [
                    {
                        "name": "imm",
                        "username": "root",
                        "password": "calvin",
                        "default": True,
                    }
                ],
                "talos": [
                    {
                        "name": "glemmen",
                        "site": "glemmen",
                        "kubeconfig": "kubeconfig",
                        "talosconfig": "talosconfig",
                        "config_dir": "nodes",
                    }
                ],
            },
        },
    )

    config = load_settings_file(path, manual())

    assert config.settings_path == path.resolve()
    assert config.port_names == "port-number"
    assert config.auto_unifi is False
    assert config.defaults.site == "bygg"
    assert config.defaults.unifi_models == {
        "OLD": "old-device",
        "NEW": "new-device",
    }
    assert config.defaults.unifi_vlan_names == {4: "Legacy", 5: "New"}
    assert config.unifi[0].password == "secret"
    assert config.unifi[0].site == "glemmen"
    assert config.unifi[0].snmp_credential == "legacy"
    assert [profile.name for profile in config.snmp] == ["default", "legacy"]
    assert config.snmp[0].community == "public"
    assert config.redfish[0].password == "readonly"
    assert config.ipmi[0].password == "calvin"
    assert config.talos[0].kubeconfig == str(tmp_path / "kubeconfig")
    assert config.talos[0].config_dir == str(tmp_path / "nodes")


def test_missing_environment_variable_is_an_error(tmp_path: Path) -> None:
    path = write_settings(
        tmp_path,
        {
            "version": 1,
            "sources": {
                "redfish": [
                    {
                        "name": "default",
                        "username": "readonly",
                        "password": {"from_env": "MISSING_REDFISH_PASSWORD"},
                    }
                ]
            },
        },
    )
    with pytest.raises(ConfigError, match="MISSING_REDFISH_PASSWORD"):
        load_settings_file(path, manual())


def test_from_file_is_relative_to_settings(tmp_path: Path) -> None:
    (tmp_path / "passwords").mkdir()
    (tmp_path / "passwords" / "redfish").write_text("from-file\n", encoding="utf-8")
    path = write_settings(
        tmp_path,
        {
            "version": 1,
            "sources": {
                "redfish": [
                    {
                        "name": "default",
                        "username": "readonly",
                        "password": {"from_file": "passwords/redfish"},
                    }
                ]
            },
        },
    )
    config = load_settings_file(path, manual())
    assert config.redfish[0].password == "from-file"


def test_from_k8s_secret_uses_configured_kubectl(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple] = []

    def fake_read(kubectl, kubeconfig, namespace, name, key):
        calls.append((kubectl, kubeconfig, namespace, name, key))
        return "from-secret"

    monkeypatch.setattr(
        "netbox_inventory_toolkit.collect.settings.read_cluster_secret_value",
        fake_read,
    )
    path = write_settings(
        tmp_path,
        {
            "version": 1,
            "collector": {"kubectl": "kubectl-test", "kubeconfig": "kubeconfig"},
            "sources": {
                "unifi": [
                    {
                        "name": "main",
                        "url": "https://unifi.example",
                        "site": "example",
                        "username": {
                            "from_k8s_secret": "monitoring/unpoller#username"
                        },
                        "password": {
                            "from_k8s_secret": "monitoring/unpoller#password"
                        },
                    }
                ]
            },
        },
    )
    config = load_settings_file(path, manual())
    assert config.unifi[0].username == "from-secret"
    assert config.unifi[0].password == "from-secret"
    assert calls == [
        (
            "kubectl-test",
            str(tmp_path / "kubeconfig"),
            "monitoring",
            "unpoller",
            "username",
        ),
        (
            "kubectl-test",
            str(tmp_path / "kubeconfig"),
            "monitoring",
            "unpoller",
            "password",
        ),
    ]


def test_duplicate_default_profiles_are_rejected(tmp_path: Path) -> None:
    path = write_settings(
        tmp_path,
        {
            "version": 1,
            "sources": {
                "snmp": [
                    {"name": "one", "community": "public", "default": True},
                    {"name": "two", "community": "private", "default": True},
                ]
            },
        },
    )
    with pytest.raises(ConfigError, match="only one profile"):
        load_settings_file(path, manual())


def test_unknown_keys_are_rejected(tmp_path: Path) -> None:
    path = write_settings(
        tmp_path,
        {
            "version": 1,
            "sources": {
                "snmp": [{"name": "default", "community": "public", "typo": True}]
            },
        },
    )
    with pytest.raises(ConfigError, match="unknown key"):
        load_settings_file(path, manual())


def test_config_show_redacts_secrets(tmp_path: Path) -> None:
    path = write_settings(
        tmp_path,
        {
            "version": 1,
            "sources": {
                "snmp": [{"name": "default", "community": "private"}],
                "redfish": [
                    {
                        "name": "default",
                        "username": "readonly",
                        "password": "hunter2",
                    }
                ],
            },
        },
    )
    config = load_settings_file(path, manual())
    shown = config.as_dict(redact=True)
    assert shown["sources"]["snmp"][0]["community"] == "<redacted>"
    assert shown["sources"]["redfish"][0]["password"] == "<redacted>"
    assert "hunter2" not in yaml.safe_dump(shown)


def test_match_precedence_and_default(tmp_path: Path) -> None:
    path = write_settings(
        tmp_path,
        {
            "version": 1,
            "sources": {
                "snmp": [
                    {
                        "name": "legacy",
                        "community": "private",
                        "match": {"devices": ["old-*"]},
                    },
                    {"name": "fallback", "community": "public", "default": True},
                ]
            },
        },
    )
    config = load_settings_file(path, manual())
    assert (
        config.snmp_profile_for(
            name="old-switch", site="example", address="192.0.2.1"
        ).name
        == "legacy"
    )
    assert (
        config.snmp_profile_for(
            name="new-switch", site="example", address="192.0.2.2"
        ).name
        == "fallback"
    )
    with pytest.raises(ConfigError, match="not configured"):
        config.snmp_profile_for(
            name="new-switch",
            site="example",
            address="192.0.2.2",
            explicit="missing",
        )


def test_legacy_config_uses_options_and_manual_settings(tmp_path: Path) -> None:
    options = CollectOptions(
        inventory=tmp_path,
        sources_mode="snmp",
        unifi_url="https://unifi.example",
        unifi_user="poller",
        unifi_pass="secret",
        snmp_community="private",
        snmp_timeout=4,
        snmp_retries=2,
    )
    config = legacy_config(options, manual())
    assert config.settings_path is None
    assert config.sources_mode == "snmp"
    assert config.defaults.site == "example"
    assert config.unifi[0].site == "example"
    assert config.snmp[0].community == "private"
    assert config.snmp[0].timeout == 4
    assert config.snmp[0].retries == 2


def test_settings_file_is_ignored_by_inventory_scan(tmp_path: Path) -> None:
    write_settings(tmp_path, {"version": 1})
    assert inventory_files(tmp_path) == []


def test_settings_are_loaded_from_inventory_root(tmp_path: Path) -> None:
    path = write_settings(tmp_path, {"version": 1})
    assert find_settings_file(tmp_path) == path.resolve()
