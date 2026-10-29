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

log = get_logger(__name__)


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
        default=os.environ.get("COLLECTOR_SOURCES", "auto"),
        help="auto, all, none, or a comma-separated list of unifi,snmp,talos,redfish,ipmi",
    )
    parser.add_argument("--no-unifi", action="store_true", help="disable the UniFi source")
    parser.add_argument("--no-snmp", action="store_true", help="disable the SNMP source")
    parser.add_argument("--no-talos", action="store_true", help="disable the Talos source")
    parser.add_argument("--no-redfish", action="store_true", help="disable the Redfish source")
    parser.add_argument("--no-ipmi", action="store_true", help="disable the IPMI source")

    parser.add_argument(
        "--unifi-url",
        default=os.environ.get("UNIFI_URL"),
        help="UniFi controller base URL (default: UNIFI_URL)",
    )
    parser.add_argument(
        "--unifi-user",
        default=os.environ.get("UNIFI_USER"),
        help="UniFi username (default: UNIFI_USER)",
    )
    parser.add_argument(
        "--unifi-pass",
        default=os.environ.get("UNIFI_PASS"),
        help="UniFi password (default: UNIFI_PASS)",
    )
    parser.add_argument(
        "--unifi-secret",
        default=os.environ.get("UNIFI_SECRET"),
        help="read UniFi credentials from NAMESPACE/SECRET (default: UNIFI_SECRET)",
    )

    parser.add_argument(
        "--talos-config-dir",
        default=os.environ.get("TALOS_CONFIG_DIR"),
        help="directory containing Talos node YAML files (optional)",
    )
    parser.add_argument(
        "--kubectl",
        default=os.environ.get("KUBECTL", "kubectl"),
        help="kubectl binary used for node discovery (default: KUBECTL or kubectl)",
    )
    parser.add_argument(
        "--kubeconfig",
        default=os.environ.get("KUBECONFIG"),
        help="kubeconfig passed to kubectl (default: KUBECONFIG)",
    )
    parser.add_argument(
        "--nodes-json",
        default=os.environ.get("NODES_JSON"),
        help="use a saved 'kubectl get nodes -o json' file instead of the cluster",
    )
    parser.add_argument(
        "--talosctl",
        default=os.environ.get("TALOSCTL"),
        help="talosctl binary used for hardware discovery (default: TALOSCTL; omit to skip)",
    )
    parser.add_argument(
        "--talosconfig",
        default=os.environ.get("TALOSCONFIG"),
        help="talosconfig passed to talosctl (default: TALOSCONFIG)",
    )

    parser.add_argument(
        "--redfish-user",
        default=os.environ.get("REDFISH_USER", DEFAULT_REDFISH_USER),
        help=f"Redfish username (default: REDFISH_USER or {DEFAULT_REDFISH_USER})",
    )
    parser.add_argument(
        "--redfish-pass",
        default=os.environ.get("REDFISH_PASS", DEFAULT_REDFISH_PASS),
        help=f"Redfish password (default: REDFISH_PASS or {DEFAULT_REDFISH_PASS})",
    )
    parser.add_argument(
        "--redfish-verify",
        action="store_true",
        default=env_bool("REDFISH_VERIFY", False),
        help="verify Redfish TLS certificates (default: REDFISH_VERIFY or false)",
    )
    parser.add_argument(
        "--ipmi-user",
        default=os.environ.get("IPMI_USER", DEFAULT_IPMI_USER),
        help=f"IPMI username (default: IPMI_USER or {DEFAULT_IPMI_USER})",
    )
    parser.add_argument(
        "--ipmi-pass",
        default=os.environ.get("IPMI_PASS", DEFAULT_IPMI_PASS),
        help=f"IPMI password (default: IPMI_PASS or {DEFAULT_IPMI_PASS})",
    )

    parser.add_argument(
        "--snmp-community",
        default=os.environ.get("SNMP_COMMUNITY", DEFAULT_SNMP_COMMUNITY),
        help=f"SNMP v2c community (default: SNMP_COMMUNITY or {DEFAULT_SNMP_COMMUNITY})",
    )
    parser.add_argument(
        "--snmp-timeout",
        type=float,
        default=float(os.environ.get("SNMP_TIMEOUT", "2")),
        help="SNMP timeout in seconds (default: 2)",
    )
    parser.add_argument(
        "--snmp-retries",
        type=int,
        default=int(os.environ.get("SNMP_RETRIES", "1")),
        help="SNMP retry count (default: 1)",
    )
    parser.add_argument(
        "--snmp-report",
        default=os.environ.get("SNMP_REPORT"),
        help="write raw SNMP observations to a JSON report",
    )

    parser.add_argument(
        "-v", "--verbose", action="store_true", help="enable DEBUG logging"
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("COLLECTOR_LOG_LEVEL", "INFO"),
        type=str.upper,
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
        help="logging threshold (default: COLLECTOR_LOG_LEVEL or INFO)",
    )
    parser.add_argument(
        "--log-file",
        default=os.environ.get("COLLECTOR_LOG_FILE"),
        help="also write logs to this file (default: COLLECTOR_LOG_FILE)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging("DEBUG" if args.verbose else args.log_level, args.log_file)
    options = CollectOptions(
        inventory=Path(args.inventory),
        manual_dir=Path(args.manual_dir) if args.manual_dir else None,
        output_dir=Path(args.output_dir) if args.output_dir else None,
        sources_mode=args.sources,
        disabled_sources=_split_no_sources(args),
        talos_config_dir=Path(args.talos_config_dir) if args.talos_config_dir else None,
        kubectl=args.kubectl,
        kubeconfig=args.kubeconfig,
        nodes_json=args.nodes_json,
        talosctl=args.talosctl,
        talosconfig=args.talosconfig,
        unifi_url=args.unifi_url,
        unifi_user=args.unifi_user,
        unifi_pass=args.unifi_pass,
        unifi_secret=args.unifi_secret,
        redfish_user=args.redfish_user,
        redfish_pass=args.redfish_pass,
        redfish_verify=args.redfish_verify,
        ipmi_user=args.ipmi_user,
        ipmi_pass=args.ipmi_pass,
        snmp_community=args.snmp_community,
        snmp_timeout=args.snmp_timeout,
        snmp_retries=args.snmp_retries,
        snmp_report=Path(args.snmp_report) if args.snmp_report else None,
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
