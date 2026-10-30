"""CLI for inventory and network discovery."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

from netbox_inventory_toolkit.collect.context import CollectOptions
from netbox_inventory_toolkit.collect.log import configure_logging, get_logger
from netbox_inventory_toolkit.collect.settings import find_settings_file, load_config
from netbox_inventory_toolkit.inventory.util import InventoryError, env_bool

from .inventory_scan import load_snapshot
from .match import match_probes
from .models import (
    PROTOCOLS,
    DiscoveryError,
    DiscoveryOptions,
    DiscoveryReport,
)
from .probes import probe_targets
from .report import render_json, render_table, render_yaml, write_new_devices, write_report
from .targets import expand_targets

log = get_logger("discover")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Suggest manual inventory entries by inspecting the inventory "
        "and optionally probing Redfish, SNMP and IPMI endpoints."
    )
    parser.add_argument(
        "--inventory",
        default=os.environ.get("INVENTORY_DIR", "inventory"),
        help="inventory root containing manual/ and generated/ (default: inventory)",
    )
    parser.add_argument(
        "--settings",
        default=None,
        help="collector settings.yml (default: COLLECTOR_SETTINGS or <inventory>/settings.yml)",
    )
    parser.add_argument(
        "--targets",
        action="append",
        default=[],
        help="comma-separated IP addresses to probe (repeatable)",
    )
    parser.add_argument(
        "--network",
        action="append",
        default=[],
        help="CIDR network to scan (repeatable)",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="comma-separated IPs or CIDRs to skip (repeatable)",
    )
    parser.add_argument(
        "--protocols",
        default="redfish,snmp,ipmi",
        help="comma-separated protocols to probe (default: redfish,snmp,ipmi)",
    )
    parser.add_argument(
        "--scan-inventory",
        action="store_true",
        help="probe addresses already present in the inventory without a CIDR",
    )
    parser.add_argument(
        "--no-inventory-targets",
        dest="inventory_targets",
        action="store_false",
        help="do not add inventory locators to an explicit/CIDR scan",
    )
    parser.set_defaults(inventory_targets=True)
    parser.add_argument(
        "--ipmi-cidr-scan",
        action="store_true",
        help="also run IPMI probes against CIDR targets (slow; off by default)",
    )
    parser.add_argument("--max-hosts", type=int, default=1024)
    parser.add_argument(
        "--force",
        action="store_true",
        help="allow scans larger than --max-hosts or 256 CIDR addresses",
    )
    parser.add_argument("--concurrency", type=int, default=32)
    parser.add_argument(
        "--timeout",
        type=float,
        default=3.0,
        help="Redfish timeout in seconds (default: 3)",
    )

    parser.add_argument(
        "--snmp-credential",
        default=None,
        help="use this SNMP profile from settings.yml",
    )
    parser.add_argument(
        "--redfish-credential",
        default=None,
        help="use this Redfish profile from settings.yml",
    )
    parser.add_argument(
        "--ipmi-credential",
        default=None,
        help="use this IPMI profile from settings.yml",
    )
    parser.add_argument(
        "--redfish-auth",
        action="store_true",
        default=env_bool("REDFISH_AUTH", False),
        help="legacy mode: send Redfish credentials during discovery",
    )
    parser.add_argument(
        "--redfish-user",
        default=os.environ.get("REDFISH_USER"),
    )
    parser.add_argument(
        "--redfish-pass",
        default=os.environ.get("REDFISH_PASS"),
    )
    parser.add_argument(
        "--redfish-verify",
        action="store_true",
        default=env_bool("REDFISH_VERIFY", False),
        help="verify Redfish TLS certificates",
    )
    parser.add_argument(
        "--snmp-community",
        default=os.environ.get("SNMP_COMMUNITY", "public"),
    )
    parser.add_argument(
        "--snmp-timeout",
        type=float,
        default=float(os.environ.get("SNMP_TIMEOUT", "2")),
    )
    parser.add_argument(
        "--snmp-retries",
        type=int,
        default=int(os.environ.get("SNMP_RETRIES", "1")),
    )
    parser.add_argument(
        "--ipmi-user",
        default=os.environ.get("IPMI_USER"),
    )
    parser.add_argument(
        "--ipmi-pass",
        default=os.environ.get("IPMI_PASS"),
    )
    parser.add_argument(
        "--ipmi-timeout",
        type=float,
        default=float(os.environ.get("IPMI_TIMEOUT", "5")),
    )
    parser.add_argument(
        "--ipmitool",
        default=os.environ.get("IPMI_TOOL", "ipmitool"),
    )

    parser.add_argument(
        "--default-role",
        default="",
        help="role for new devices when it cannot be inferred",
    )
    parser.add_argument(
        "--default-device-type",
        default="",
        help="device type for new devices when it cannot be inferred",
    )
    parser.add_argument(
        "--format",
        dest="output_format",
        choices=("table", "yaml", "json"),
        default="table",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="write the report to this file instead of stdout",
    )
    parser.add_argument(
        "--write-new",
        type=Path,
        help="write complete new manual device entries to this file",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="enable debug logging",
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("COLLECTOR_LOG_LEVEL", "INFO"),
        type=str.upper,
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
    )
    parser.add_argument(
        "--log-file",
        default=os.environ.get("COLLECTOR_LOG_FILE"),
    )
    return parser


def _parse_protocols(value: str) -> set[str]:
    protocols = {item.strip().lower() for item in value.split(",") if item.strip()}
    unknown = protocols - set(PROTOCOLS)
    if unknown:
        raise DiscoveryError(
            f"unknown protocol(s): {', '.join(sorted(unknown))}; "
            f"choose from {', '.join(PROTOCOLS)}"
        )
    return protocols


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging("DEBUG" if args.verbose else args.log_level, args.log_file)

    inventory = Path(args.inventory)
    try:
        protocols = _parse_protocols(args.protocols)
        settings_path = find_settings_file(
            inventory, args.settings or os.environ.get("COLLECTOR_SETTINGS")
        )
        if settings_path is None:
            log.warning(
                "No collector settings file found; using deprecated discovery "
                "flags and environment variables. Create %s to share credentials "
                "with collect.",
                inventory / "settings.yml",
            )
        snapshot = load_snapshot(inventory)
        manual = {"settings": snapshot.settings}
        config = load_config(
            CollectOptions(inventory=inventory, settings_path=settings_path),
            manual,
        )
        config.apply_to_manual(manual)
        options = DiscoveryOptions(
            inventory=inventory,
            targets=args.targets,
            networks=args.network,
            excludes=args.exclude,
            protocols=protocols,
            inventory_targets=args.inventory_targets,
            scan_inventory=args.scan_inventory,
            ipmi_cidr_scan=args.ipmi_cidr_scan,
            max_hosts=args.max_hosts,
            force=args.force,
            concurrency=args.concurrency,
            timeout=args.timeout,
            redfish_auth=args.redfish_auth,
            redfish_user=args.redfish_user,
            redfish_pass=args.redfish_pass,
            redfish_verify=args.redfish_verify,
            snmp_community=args.snmp_community,
            snmp_timeout=args.snmp_timeout,
            snmp_retries=args.snmp_retries,
            ipmi_user=args.ipmi_user,
            ipmi_pass=args.ipmi_pass,
            ipmi_timeout=args.ipmi_timeout,
            ipmi_binary=args.ipmitool,
            config=config,
            snmp_credential=args.snmp_credential,
            redfish_credential=args.redfish_credential,
            ipmi_credential=args.ipmi_credential,
            default_role=args.default_role,
            default_device_type=args.default_device_type,
            output_format=args.output_format,
            output=args.output,
            write_new=args.write_new,
        )
        started = time.monotonic()
        targets = expand_targets(options, snapshot)
        probes = []
        warnings: list[str] = []
        if targets:
            probes, warnings = asyncio.run(probe_targets(targets, options))
        for warning in warnings:
            log.warning("%s", warning)
        report = DiscoveryReport(
            items=match_probes(snapshot, probes, options),
            scanned=len(targets),
            elapsed_seconds=time.monotonic() - started,
            protocols=sorted({probe.protocol for probe in probes}),
            snapshot=snapshot,
        )
        if options.output:
            write_report(report, options.output, options.output_format)
            print(f"Wrote {options.output}")
        else:
            if options.output_format == "json":
                print(render_json(report), end="")
            elif options.output_format == "yaml":
                print(render_yaml(report), end="")
            else:
                print(render_table(report), end="")
        if options.write_new:
            count = write_new_devices(options.write_new, report)
            print(
                f"Wrote {count} new device(s) to {options.write_new}"
                if count
                else "No complete new devices to write"
            )
        return 0
    except (DiscoveryError, InventoryError) as exc:
        log.error("Discovery failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
