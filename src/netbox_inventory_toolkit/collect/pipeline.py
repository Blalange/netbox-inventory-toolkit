"""Collector pipeline: run optional sources, merge them and render YAML."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from netbox_inventory_toolkit.inventory.util import InventoryError

from .build import build_nodes, build_servers
from .config import require_site, snmp_port_names
from .context import ALL_SOURCES, CollectContext, CollectOptions
from .identity import DeviceResolver
from .log import get_logger
from .manual import load_manual
from .render import write_yaml
from .snmp_merge import merge_snmp_observations
from .sources.base import SourceState
from .sources.ipmi import IpmiSource
from .sources.redfish import RedfishSource
from .sources.snmp import SnmpSource, write_snmp_report
from .sources.talos import TalosSource
from .sources.unifi import UnifiSource

log = get_logger(__name__)

SOURCE_CLASSES = (UnifiSource, SnmpSource, TalosSource, RedfishSource, IpmiSource)


@dataclass
class CollectSummary:
    network_devices: int = 0
    servers: int = 0
    nodes: int = 0
    cables: int = 0
    vlans: int = 0
    prefixes: int = 0
    module_types: int = 0
    modules: int = 0


def selected_sources(options: CollectOptions) -> set[str]:
    if options.sources_mode == "none":
        return set()
    if options.sources_mode in {"auto", "all"}:
        names = set(ALL_SOURCES)
    else:
        names = {
            name.strip()
            for name in options.sources_mode.split(",")
            if name.strip()
        }
        unknown = names - set(ALL_SOURCES)
        if unknown:
            raise InventoryError(
                f"unknown source(s): {', '.join(sorted(unknown))}; "
                f"choose from {', '.join(ALL_SOURCES)} or auto/all/none"
            )
    return names - options.disabled_sources


def _switch_name(devices: list[dict[str, Any]]) -> str:
    for device in devices:
        if str(device.get("role") or "") == "switch":
            return str(device.get("name") or "")
    return ""


def _run_sources(ctx: CollectContext, selected: set[str], explicit: bool) -> SourceState:
    state = SourceState()
    for source_class in SOURCE_CLASSES:
        name = source_class.name
        if name not in selected:
            continue
        source = source_class()
        if not source.enabled(ctx, state):
            if explicit:
                raise InventoryError(f"source {name!r} is selected but is not configured")
            log.info("Source %s: skipped (not configured)", name)
            continue
        try:
            source.collect(ctx, state)
        except Exception as exc:  # noqa: BLE001 - auto mode must tolerate source outages
            if explicit:
                raise
            log.warning("Source %s: skipped: %s", name, exc)
    return state


def collect(options: CollectOptions) -> CollectSummary:
    manual = load_manual(options.manual_path)
    require_site(manual)
    resolver = DeviceResolver(manual)
    ctx = CollectContext(options=options, manual=manual, resolver=resolver)
    selected = selected_sources(options)
    explicit = options.sources_mode not in {"auto", "all", "none"}
    log.info("Collector sources: %s", ", ".join(sorted(selected)) or "none")
    state = _run_sources(ctx, selected, explicit)

    network_devices = list(state.unifi.devices) if state.unifi else []
    vlans = list(state.unifi.vlans) if state.unifi else []
    prefixes = list(state.unifi.prefixes) if state.unifi else []
    cables = list(state.unifi.cables) if state.unifi else []
    matches: dict[str, str] = {}
    if state.snmp and state.snmp.observations:
        matches = merge_snmp_observations(
            network_devices,
            vlans,
            state.snmp.observations,
            resolver,
            manual,
            cables=cables,
            port_names=snmp_port_names(manual),
        )
        state.snmp.matches = matches
        if options.snmp_report:
            write_snmp_report(Path(options.snmp_report), state.snmp.observations, matches)

    module_types: dict[str, dict[str, Any]] = {}
    clients = state.unifi.clients if state.unifi else []
    has_compute_source = bool(
        state.unifi is not None
        or state.bmc_by_name
        or (state.talos is not None and state.talos.nodes)
    )
    servers = (
        build_servers(
            clients,
            manual,
            _switch_name(network_devices),
            module_types=module_types,
            bmc_by_name=state.bmc_by_name,
        )
        if has_compute_source
        else []
    )
    nodes: list[dict[str, Any]] = []
    if state.talos and state.talos.nodes:
        nodes = build_nodes(
            state.talos.nodes,
            options.talos_config_dir,
            manual,
            talosctl=options.talosctl,
            talosconfig=options.talosconfig,
            module_types=module_types,
            bmc_by_name=state.bmc_by_name,
            manual_by_node=state.talos.manual_by_node,
        )

    output = options.generated_path
    write_yaml(
        output / "network.yaml",
        {
            "devices": network_devices,
            "vlans": vlans,
            "prefixes": prefixes,
            "cables": cables,
        },
        "Network devices, VLANs, prefixes and discovered cables.",
        sources=sorted(selected),
    )
    write_yaml(
        output / "compute.yaml",
        {"devices": [*servers, *nodes]},
        "Canonical servers and nodes with discovered components.",
        sources=sorted(selected),
    )
    write_yaml(
        output / "module-types.yaml",
        {"module_types": list(module_types.values())},
        "Module types discovered across all compute sources.",
        sources=sorted(selected),
    )
    for legacy in ("unifi.yaml", "servers.yaml", "talos.yaml"):
        legacy_path = output / legacy
        if legacy_path.exists():
            legacy_path.unlink()
            log.info("Removed legacy generated file %s", legacy_path)

    summary = CollectSummary(
        network_devices=len(network_devices),
        servers=len(servers),
        nodes=len(nodes),
        cables=len(cables),
        vlans=len(vlans),
        prefixes=len(prefixes),
        module_types=len(module_types),
        modules=sum(
            len(device.get("modules", [])) for device in [*servers, *nodes]
        ),
    )
    log.info(
        "Collector finished: network_devices=%d, servers=%d, nodes=%d, "
        "cables=%d, vlans=%d, prefixes=%d, module_types=%d, modules=%d",
        summary.network_devices,
        summary.servers,
        summary.nodes,
        summary.cables,
        summary.vlans,
        summary.prefixes,
        summary.module_types,
        summary.modules,
    )
    return summary
