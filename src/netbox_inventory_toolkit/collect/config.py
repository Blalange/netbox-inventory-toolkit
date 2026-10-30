"""Collector configuration defaults and settings helpers."""

from __future__ import annotations

from typing import Any

from netbox_inventory_toolkit.inventory.util import InventoryError

DEFAULT_MANUAL_SUBDIR = "manual"
DEFAULT_GENERATED_SUBDIR = "generated"
DEFAULT_TAG = "production"
DEFAULT_SNMP_COMMUNITY = "public"
DEFAULT_SNMP_PORT_NAMES = "auto"
DEFAULT_REDFISH_USER = "readonly"
DEFAULT_REDFISH_PASS = "readonly"
DEFAULT_IPMI_USER = "root"
DEFAULT_IPMI_PASS = "calvin"

VALID_SNMP_PORT_NAMES = {"auto", "port-number", "ifname"}


def settings(manual: dict[str, Any]) -> dict[str, Any]:
    value = manual.get("settings")
    return value if isinstance(value, dict) else {}


def require_site(manual: dict[str, Any]) -> str:
    site = str(settings(manual).get("site") or "").strip()
    if not site:
        raise InventoryError(
            "settings.site is required when the collector creates devices; "
            "set it in the manual inventory (for example manual/site.yaml)"
        )
    return site


def default_tag(manual: dict[str, Any]) -> str:
    return str(settings(manual).get("default_tag") or DEFAULT_TAG)


def compute_roles(manual: dict[str, Any]) -> set[str]:
    value = settings(manual).get("compute_roles")
    if isinstance(value, list) and value:
        return {str(item) for item in value}
    return {"server"}


def talos_roles(manual: dict[str, Any]) -> tuple[str, str]:
    value = settings(manual).get("roles")
    roles = value if isinstance(value, dict) else {}
    return (
        str(roles.get("control_plane") or roles.get("control-plane") or "control-plane"),
        str(roles.get("worker") or "worker"),
    )


def talos_platform(manual: dict[str, Any]) -> str:
    return str(settings(manual).get("talos_platform") or "")


def snmp_port_names(manual: dict[str, Any]) -> str:
    value = settings(manual).get("snmp")
    snmp = value if isinstance(value, dict) else {}
    mode = str(snmp.get("port_names") or DEFAULT_SNMP_PORT_NAMES)
    if mode not in VALID_SNMP_PORT_NAMES:
        raise InventoryError(
            f"settings.snmp.port_names must be one of {sorted(VALID_SNMP_PORT_NAMES)}, got {mode!r}"
        )
    return mode


def snmp_auto_unifi(manual: dict[str, Any]) -> bool:
    value = settings(manual).get("snmp")
    snmp = value if isinstance(value, dict) else {}
    return bool(snmp.get("auto_unifi", True))
