"""Collector command line interface."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from netbox_inventory_toolkit.inventory.util import InventoryError, env_bool

from .config import (
    DEFAULT_IPMI_PASS,
    DEFAULT_IPMI_USER,
    DEFAULT_REDFISH_PASS,
    DEFAULT_REDFISH_USER,
    DEFAULT_SNMP_COMMUNITY,
)
from .context import CollectOptions
from .log import configure_logging, get_logger
from .pipeline import collect
from .settings import find_settings_file

log = get_logger(__name__)

_DEPRECATED_FLAGS = {
    "unifi_url": "--unifi-url",
    "unifi_user": "--unifi-user",
    "unifi_pass": "--unifi-pass",
    "unifi_secret": "--unifi-secret",
    "talos_config_dir": "--talos-config-dir",
    "kubectl": "--kubectl",
    "kubeconfig": "--kubeconfig",
    "nodes_json": "--nodes-json",
    "talosctl": "--talosctl",
    "talosconfig": "--talosconfig",
    "redfish_user": "--redfish-user",
    "redfish_pass": "--redfish-pass",
    "ipmi_user": "--ipmi-user",
    "ipmi_pass": "--ipmi-pass",
    "snmp_community": "--snmp-community",
    "snmp_timeout": "--snmp-timeout",
    "snmp_retries": "--snmp-retries",
}


def _split_no_sources(args: argparse.Namespace) -> set[str]:
    disabled: set[str] = set()
    if args.no_unifi:
        disabled.add("unifi")
    if args.no_snmp:
        disabled.add("snmp")
    if args.no_talos:
        disabled.add("talos")
    if args.no_redfish:
        disabled.add("redfish")
    if args.no_ipmi:
        disabled.add("ipmi")
    return disabled


def _legacy_value(
    args: argparse.Namespace,
    attribute: str,
    environment: str,
    default: object = None,
) -> object:
    value = getattr(args, attribute)
    if value is not None:
        return value
    return os.environ.get(environment, default)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect live inventory sources into the generated layer."
    )
    parser.add_argument(
        "--inventory",
        default=os.environ.get("INVENTORY_DIR", "inventory"),
        help="inventory root containing manual/, generated/ and images/ (default: inventory)",
    )
    parser.add_argument(
        "--settings",
        default=None,
        help="collector settings.yml (default: COLLECTOR_SETTINGS or <inventory>/settings.yml)",
    )
    parser.add_argument(
        "--manual-dir",
        default=os.environ.get("MANUAL_INVENTORY_DIR"),
        help="manual inventory directory (default: <inventory>/manual)",
    )
    parser.add_argument(
        "--output-dir",
        default=os.environ.get("GENERATED_INVENTORY_DIR"),
        help="generated inventory directory (default: <inventory>/generated)",
    )
    parser.add_argument(
        "--sources",
        default=None,
        help="auto, all, none, or a comma-separated list of unifi,snmp,talos,redfish,ipmi",
    )
    parser.add_argument("--no-unifi", action="store_true", help="disable the UniFi source")
    parser.add_argument("--no-snmp", action="store_true", help="disable the SNMP source")
    parser.add_argument("--no-talos", action="store_true", help="disable the Talos source")
    parser.add_argument("--no-redfish", action="store_true", help="disable the Redfish source")
    parser.add_argument("--no-ipmi", action="store_true", help="disable the IPMI source")

    parser.add_argument(
        "--unifi-url",
        default=None,
        help="deprecated: configure sources.unifi[].url in settings.yml",
    )
    parser.add_argument(
        "--unifi-user",
        default=None,
        help="deprecated: configure sources.unifi[].username in settings.yml",
    )
    parser.add_argument(
        "--unifi-pass",
        default=None,
        help="deprecated: configure sources.unifi[].password in settings.yml",
    )
    parser.add_argument(
        "--unifi-secret",
        default=None,
        help="deprecated: use from_k8s_secret in settings.yml",
    )

    parser.add_argument(
        "--talos-config-dir",
        default=None,
        help="deprecated: configure sources.talos[].config_dir in settings.yml",
    )
    parser.add_argument(
        "--kubectl",
        default=None,
        help="deprecated: configure sources.talos[].kubectl in settings.yml",
    )
    parser.add_argument(
        "--kubeconfig",
        default=None,
        help="deprecated: configure sources.talos[].kubeconfig in settings.yml",
    )
    parser.add_argument(
        "--nodes-json",
        default=None,
        help="deprecated: configure sources.talos[].nodes_json in settings.yml",
    )
    parser.add_argument(
        "--talosctl",
        default=None,
        help="deprecated: configure sources.talos[].talosctl in settings.yml",
    )
    parser.add_argument(
        "--talosconfig",
        default=None,
        help="deprecated: configure sources.talos[].talosconfig in settings.yml",
    )

    parser.add_argument(
        "--redfish-user",
        default=None,
        help="deprecated: configure sources.redfish[].username in settings.yml",
    )
    parser.add_argument(
        "--redfish-pass",
        default=None,
        help="deprecated: configure sources.redfish[].password in settings.yml",
    )
    parser.add_argument(
        "--redfish-verify",
        action="store_true",
        default=None,
        help="deprecated: configure sources.redfish[].verify_tls in settings.yml",
    )
    parser.add_argument(
        "--ipmi-user",
        default=None,
        help="deprecated: configure sources.ipmi[].username in settings.yml",
    )
    parser.add_argument(
        "--ipmi-pass",
        default=None,
        help="deprecated: configure sources.ipmi[].password in settings.yml",
    )

    parser.add_argument(
        "--snmp-community",
        default=None,
        help="deprecated: configure sources.snmp[].community in settings.yml",
    )
    parser.add_argument(
        "--snmp-timeout",
        type=float,
        default=None,
        help="deprecated: configure sources.snmp[].timeout in settings.yml",
    )
    parser.add_argument(
        "--snmp-retries",
        type=int,
        default=None,
        help="deprecated: configure sources.snmp[].retries in settings.yml",
    )
    parser.add_argument(
        "--snmp-report",
        default=None,
        help="write raw SNMP observations to a JSON report",
    )

    parser.add_argument(
        "-v", "--verbose", action="store_true", help="enable DEBUG logging"
    )
    parser.add_argument(
        "--log-level",
        default=None,
        type=str.upper,
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
        help="logging threshold (default: COLLECTOR_LOG_LEVEL or settings.yml)",
    )
    parser.add_argument(
        "--log-file",
        default=os.environ.get("COLLECTOR_LOG_FILE"),
        help="also write logs to this file (default: COLLECTOR_LOG_FILE)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    early_level = (
        "DEBUG"
        if args.verbose
        else args.log_level or os.environ.get("COLLECTOR_LOG_LEVEL", "INFO")
    )
    configure_logging(early_level, args.log_file)
    inventory = Path(args.inventory)
    settings_argument = args.settings or os.environ.get("COLLECTOR_SETTINGS")
    try:
        settings_path = find_settings_file(inventory, settings_argument)
    except InventoryError as exc:
        log.error("%s", exc)
        return 2

    used_deprecated = [
        flag for attribute, flag in _DEPRECATED_FLAGS.items() if getattr(args, attribute)
    ]
    if args.redfish_verify is not None:
        used_deprecated.append("--redfish-verify")
    if settings_path is not None and used_deprecated:
        log.error(
            "%s cannot be combined with %s; move these values into settings.yml",
            ", ".join(used_deprecated),
            settings_path,
        )
        return 2
    for flag in used_deprecated:
        log.warning(
            "%s is deprecated; configure it in settings.yml instead", flag
        )
    if settings_path is None:
        log.warning(
            "No collector settings file found; using deprecated CLI flags and "
            "environment variables. Create %s to make runs reproducible.",
            inventory / "settings.yml",
        )

    options = CollectOptions(
        inventory=inventory,
        settings_path=settings_path,
        manual_dir=Path(args.manual_dir) if args.manual_dir else None,
        output_dir=Path(args.output_dir) if args.output_dir else None,
        sources_mode=args.sources,
        disabled_sources=_split_no_sources(args),
        talos_config_dir=Path(str(_legacy_value(args, "talos_config_dir", "TALOS_CONFIG_DIR")))
        if _legacy_value(args, "talos_config_dir", "TALOS_CONFIG_DIR")
        else None,
        kubectl=str(_legacy_value(args, "kubectl", "KUBECTL", "kubectl")),
        kubeconfig=_legacy_value(args, "kubeconfig", "KUBECONFIG"),
        nodes_json=_legacy_value(args, "nodes_json", "NODES_JSON"),
        talosctl=_legacy_value(args, "talosctl", "TALOSCTL"),
        talosconfig=_legacy_value(args, "talosconfig", "TALOSCONFIG"),
        unifi_url=_legacy_value(args, "unifi_url", "UNIFI_URL"),
        unifi_user=_legacy_value(args, "unifi_user", "UNIFI_USER"),
        unifi_pass=_legacy_value(args, "unifi_pass", "UNIFI_PASS"),
        unifi_secret=_legacy_value(args, "unifi_secret", "UNIFI_SECRET"),
        redfish_user=str(
            _legacy_value(
                args, "redfish_user", "REDFISH_USER", DEFAULT_REDFISH_USER
            )
        ),
        redfish_pass=str(
            _legacy_value(
                args, "redfish_pass", "REDFISH_PASS", DEFAULT_REDFISH_PASS
            )
        ),
        redfish_verify=args.redfish_verify
        if args.redfish_verify is not None
        else env_bool("REDFISH_VERIFY", False),
        ipmi_user=str(
            _legacy_value(args, "ipmi_user", "IPMI_USER", DEFAULT_IPMI_USER)
        ),
        ipmi_pass=str(
            _legacy_value(args, "ipmi_pass", "IPMI_PASS", DEFAULT_IPMI_PASS)
        ),
        snmp_community=str(
            _legacy_value(
                args, "snmp_community", "SNMP_COMMUNITY", DEFAULT_SNMP_COMMUNITY
            )
        ),
        snmp_timeout=float(
            _legacy_value(args, "snmp_timeout", "SNMP_TIMEOUT", 2.0)
        ),
        snmp_retries=int(_legacy_value(args, "snmp_retries", "SNMP_RETRIES", 1)),
        snmp_report=Path(args.snmp_report) if args.snmp_report else None,
        log_level="DEBUG" if args.verbose else args.log_level,
        log_file=Path(args.log_file) if args.log_file else None,
    )
    try:
        summary = collect(options)
    except InventoryError as exc:
        log.error("Collector failed: %s", exc)
        return 1
    print("Generated inventory summary:")
    print(
        f"  network_devices={summary.network_devices}, servers={summary.servers}, "
        f"nodes={summary.nodes}, cables={summary.cables}, vlans={summary.vlans}, "
        f"prefixes={summary.prefixes}, module_types={summary.module_types}, "
        f"modules={summary.modules}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
