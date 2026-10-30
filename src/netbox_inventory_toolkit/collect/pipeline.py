"""Collector pipeline: run optional sources, merge them and render YAML."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from netbox_inventory_toolkit.inventory.keys import cable_key
from netbox_inventory_toolkit.inventory.util import InventoryError

from .build import build_nodes, build_servers
from .config import require_site
from .context import ALL_SOURCES, CollectContext, CollectOptions
from .identity import DeviceResolver
from .log import configure_logging, get_logger
from .manual import load_manual
from .render import write_yaml
from .settings import load_config
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


def selected_sources(
    options: CollectOptions,
    config: Any = None,
) -> set[str]:
    mode = (
        options.sources_mode
        or (getattr(config, "sources_mode", None) if config is not None else None)
        or os.environ.get("COLLECTOR_SOURCES")
        or "auto"
    )
    if mode == "none":
        return set()
    if mode in {"auto", "all"}:
        names = set(ALL_SOURCES)
    else:
        names = {name.strip() for name in mode.split(",") if name.strip()}
        unknown = names - set(ALL_SOURCES)
        if unknown:
            raise InventoryError(
                f"unknown source(s): {', '.join(sorted(unknown))}; "
                f"choose from {', '.join(ALL_SOURCES)} or auto/all/none"
            )
    disabled = set(options.disabled_sources)
    if config is not None:
        disabled.update(getattr(config, "disabled_sources", set()) or set())
    return names - disabled


def _switches_by_site(devices: list[dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for device in devices:
        if str(device.get("role") or "") != "switch":
            continue
        site = str(device.get("site") or "")
        if site and site not in result:
            result[site] = str(device.get("name") or "")
    return result


def _merge_devices(groups: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    for group in groups:
        for device in group:
            name = str(device.get("name") or "")
            if not name:
                continue
            if name in seen:
                raise InventoryError(
                    f"generated device name {name!r} appears in multiple sources "
                    f"({seen[name]!r} and another source); use unique names or "
                    "separate sites"
                )
            seen[name] = str(device.get("sources") or "")
            result.append(device)
    return result


def _merge_vlans(groups: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    by_key: dict[tuple[str, int], dict[str, Any]] = {}
    for group in groups:
        for vlan in group:
            if vlan.get("vid") is None:
                continue
            key = (str(vlan.get("site") or ""), int(vlan["vid"]))
            current = by_key.get(key)
            if current is None:
                by_key[key] = vlan
                result.append(vlan)
                continue
            current["sources"] = sorted(
                {*(current.get("sources") or []), *(vlan.get("sources") or [])}
            )
            if not current.get("name") and vlan.get("name"):
                current["name"] = vlan["name"]
            if not current.get("description") and vlan.get("description"):
                current["description"] = vlan["description"]
    return result


def _merge_prefixes(groups: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    by_prefix: dict[str, dict[str, Any]] = {}
    for group in groups:
        for prefix in group:
            value = str(prefix.get("prefix") or "")
            if not value:
                continue
            current = by_prefix.get(value)
            if current is None:
                by_prefix[value] = prefix
                result.append(prefix)
                continue
            current_site = str(current.get("site") or "")
            new_site = str(prefix.get("site") or "")
            if current_site and new_site and current_site != new_site:
                raise InventoryError(
                    f"prefix {value} is generated for both {current_site!r} and "
                    f"{new_site!r}; NetBox prefixes are global, so keep one site "
                    "or exclude the duplicate network"
                )
            current["sources"] = sorted(
                {*(current.get("sources") or []), *(prefix.get("sources") or [])}
            )
            if not current.get("description") and prefix.get("description"):
                current["description"] = prefix["description"]
    return result


def _merge_cables(groups: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    by_key: dict[Any, dict[str, Any]] = {}
    for group in groups:
        for cable in group:
            key = cable_key(cable)
            current = by_key.get(key)
            if current is None:
                by_key[key] = cable
                result.append(cable)
                continue
            current["sources"] = sorted(
                {*(current.get("sources") or []), *(cable.get("sources") or [])}
            )
            if not current.get("description") and cable.get("description"):
                current["description"] = cable["description"]
    return result


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


def _duplicate_check(devices: list[dict[str, Any]], label: str) -> None:
    seen: set[str] = set()
    for device in devices:
        name = str(device.get("name") or "")
        if not name:
            continue
        if name in seen:
            raise InventoryError(
                f"{label} contains duplicate generated device name {name!r}; "
                "assign distinct names or use talos_cluster/unifi site separation"
            )
        seen.add(name)


def collect(options: CollectOptions) -> CollectSummary:
    manual = load_manual(options.manual_path)
    config = load_config(options, manual)
    configure_logging(
        options.log_level or config.log_level,
        options.log_file or config.log_file,
    )
    config.apply_to_manual(manual)
    require_site(manual)
    resolver = DeviceResolver(manual)
    ctx = CollectContext(options=options, manual=manual, resolver=resolver, config=config)
    selected = selected_sources(options, config)
    explicit = (options.sources_mode or config.sources_mode or "auto") not in {
        "auto",
        "all",
        "none",
    }
    log.info("Collector sources: %s", ", ".join(sorted(selected)) or "none")
    state = _run_sources(ctx, selected, explicit)

    unifi_groups = state.unifi
    network_devices = _merge_devices([group.devices for group in unifi_groups])
    vlans = _merge_vlans([group.vlans for group in unifi_groups])
    prefixes = _merge_prefixes([group.prefixes for group in unifi_groups])
    cables = _merge_cables([group.cables for group in unifi_groups])
    clients: list[dict[str, Any]] = []
    for group in unifi_groups:
        for client in group.clients:
            clients.append({**client, "_site": group.site})

    matches: dict[str, str] = {}
    if state.snmp and state.snmp.observations:
        matches = merge_snmp_observations(
            network_devices,
            vlans,
            state.snmp.observations,
            resolver,
            manual,
            cables=cables,
            port_names=config.port_names,
            default_site=config.defaults.site,
        )
        state.snmp.matches = matches
        if options.snmp_report or config.snmp_report:
            write_snmp_report(
                Path(options.snmp_report or config.snmp_report),
                state.snmp.observations,
                matches,
            )

    module_types: dict[str, dict[str, Any]] = {}
    has_compute_source = bool(
        unifi_groups
        or state.bmc_by_name
        or any(group.nodes for group in state.talos)
    )
    servers = (
        build_servers(
            clients,
            manual,
            "",
            default_site=config.defaults.site,
            switches_by_site=_switches_by_site(network_devices),
            module_types=module_types,
            bmc_by_name=state.bmc_by_name,
        )
        if has_compute_source
        else []
    )
    nodes: list[dict[str, Any]] = []
    for group in state.talos:
        nodes.extend(
            build_nodes(
                group.nodes,
                group.config_dir,
                manual,
                default_site=group.site or config.defaults.site,
                cluster=group.source,
                talosctl=group.talosctl,
                talosconfig=group.talosconfig,
                module_types=module_types,
                bmc_by_name=state.bmc_by_name,
                manual_by_node=group.manual_by_node,
            )
        )
    _duplicate_check([*servers, *nodes], "compute inventory")

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
