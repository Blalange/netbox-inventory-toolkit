"""Validate and display the effective collector settings."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import yaml

from netbox_inventory_toolkit.inventory.util import InventoryError

from .context import CollectOptions
from .manual import load_manual
from .settings import CollectorConfig, find_settings_file, load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate or display the collector settings.yml."
    )
    parser.add_argument("command", choices=("check", "show"))
    parser.add_argument(
        "--inventory",
        default="inventory",
        help="inventory root containing manual/ (default: inventory)",
    )
    parser.add_argument(
        "--settings",
        default=None,
        help="collector settings.yml (default: <inventory>/settings.yml)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="show output as JSON (for the show command)",
    )
    return parser


def _availability(config: CollectorConfig) -> list[str]:
    warnings: list[str] = []
    for profile in config.talos:
        if not profile.enabled:
            continue
        if profile.nodes_json:
            if not Path(profile.nodes_json).is_file():
                warnings.append(
                    f"Talos profile {profile.name!r}: nodes_json {profile.nodes_json} does not exist"
                )
        elif shutil.which(profile.kubectl) is None:
            warnings.append(
                f"Talos profile {profile.name!r}: kubectl {profile.kubectl!r} was not found"
            )
        if profile.talosctl and shutil.which(profile.talosctl) is None:
            warnings.append(
                f"Talos profile {profile.name!r}: talosctl {profile.talosctl!r} was not found"
            )
    for profile in config.ipmi:
        if profile.enabled and shutil.which(profile.binary) is None:
            warnings.append(
                f"IPMI profile {profile.name!r}: {profile.binary!r} was not found"
            )
        if not profile.binary:
            warnings.append(
                f"IPMI profile {profile.name!r}: binary must not be empty"
            )
    return warnings


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    inventory = Path(args.inventory)
    try:
        path = find_settings_file(inventory, args.settings)
        options = CollectOptions(inventory=inventory, settings_path=path)
        manual = load_manual(options.manual_path)
        config = load_config(options, manual)
        config.apply_to_manual(manual)
        warnings, errors = config.validate_inventory(manual)
        mode = config.sources_mode or "auto"
        if mode not in {"auto", "all", "none"}:
            for name in (item.strip() for item in mode.split(",")):
                if not getattr(config, name, None):
                    errors.append(f"source {name!r} is selected but has no profiles")
        warnings.extend(_availability(config))
        if not config.defaults.site:
            warnings.append("defaults.site is empty; generated devices need a site")
    except InventoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.command == "show":
        payload = config.as_dict(redact=True)
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=False, default=str))
        else:
            print(
                yaml.safe_dump(payload, sort_keys=False, default_flow_style=False),
                end="",
            )
        return 0

    print(
        f"Collector settings OK: settings={config.settings_path or 'legacy flags/env'}, "
        f"sources={config.sources_mode or 'auto'}, "
        f"unifi={len(config.unifi)}, snmp={len(config.snmp)}, "
        f"redfish={len(config.redfish)}, ipmi={len(config.ipmi)}, "
        f"talos={len(config.talos)}"
    )
    for warning in warnings:
        print(f"warning: {warning}")
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
