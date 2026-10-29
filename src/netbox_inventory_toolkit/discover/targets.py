"""Expand explicit/CIDR/inventory addresses into scan targets."""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable

from .models import DiscoveryError, DiscoveryOptions, InventorySnapshot, ScanTarget

_SOURCE_PRIORITY = {"network": 1, "inventory": 2, "explicit": 3}


def _split_values(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        for item in str(value).split(","):
            item = item.strip()
            if item:
                result.append(item)
    return result


def _parse_excludes(
    values: Iterable[str],
) -> tuple[set[str], list[ipaddress.IPv4Network | ipaddress.IPv6Network]]:
    addresses: set[str] = set()
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for value in _split_values(values):
        try:
            addresses.add(str(ipaddress.ip_address(value)))
            continue
        except ValueError:
            pass
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError as exc:
            raise DiscoveryError(f"invalid exclude address or network: {value!r}") from exc
    return addresses, networks


def expand_targets(
    options: DiscoveryOptions,
    snapshot: InventorySnapshot,
) -> list[ScanTarget]:
    if not options.has_network_scan():
        return []

    excluded_addresses, excluded_networks = _parse_excludes(options.excludes)
    targets: dict[str, ScanTarget] = {}

    def add(address: str, source: str) -> None:
        normalized = str(ipaddress.ip_address(address))
        if normalized in excluded_addresses:
            return
        if any(normalized in network for network in excluded_networks):
            return
        current = targets.get(normalized)
        if current is None or _SOURCE_PRIORITY[source] > _SOURCE_PRIORITY[current.source]:
            targets[normalized] = ScanTarget(address=normalized, source=source)

    for value in _split_values(options.targets):
        try:
            add(value, "explicit")
        except ValueError as exc:
            raise DiscoveryError(f"invalid target address: {value!r}") from exc

    network_hosts = 0
    for value in options.networks:
        try:
            network = ipaddress.ip_network(value, strict=False)
        except ValueError as exc:
            raise DiscoveryError(f"invalid network: {value!r}") from exc
        for host in network.hosts():
            network_hosts += 1
            add(str(host), "network")

    if options.inventory_targets:
        for device in snapshot.devices:
            for address in (
                device.redfish_ip,
                device.ipmi_ip,
                device.snmp_ip,
                device.oob_ip,
            ):
                if address:
                    try:
                        add(address, "inventory")
                    except ValueError:
                        continue

    if len(targets) > options.max_hosts and not options.force:
        raise DiscoveryError(
            f"refusing to scan {len(targets)} hosts (max {options.max_hosts}); "
            "narrow the range or pass --force"
        )
    if network_hosts > 256 and not options.force:
        raise DiscoveryError(
            f"network scan covers {network_hosts} addresses; pass --force or narrow the range"
        )
    return sorted(targets.values(), key=lambda target: target.address)
