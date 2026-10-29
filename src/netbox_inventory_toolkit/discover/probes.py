"""Best-effort Redfish, SNMP and IPMI network probes."""

from __future__ import annotations

import asyncio
import importlib.util
import shutil
from typing import Any

import requests

from netbox_inventory_toolkit.collect.sources.ipmi import IPMI_BINARY, probe_ipmi
from netbox_inventory_toolkit.collect.sources.snmp import probe_snmp

from .models import DiscoveryOptions, ProbeResult, ScanTarget

USER_AGENT = "netbox-inventory-toolkit/0.2"


def available_protocols(options: DiscoveryOptions) -> tuple[list[str], list[str]]:
    """Return protocol names that can run and human-readable warnings."""
    available: list[str] = []
    warnings: list[str] = []
    for protocol in ("redfish", "snmp", "ipmi"):
        if protocol not in options.protocols:
            continue
        if protocol == "snmp" and importlib.util.find_spec("pysnmp") is None:
            warnings.append("SNMP skipped: install the 'snmp' extra (pysnmp)")
            continue
        if protocol == "ipmi":
            binary = shutil.which(options.ipmi_binary or IPMI_BINARY)
            if binary is None:
                warnings.append(
                    f"IPMI skipped: {options.ipmi_binary or IPMI_BINARY} was not found"
                )
                continue
        available.append(protocol)
    return available, warnings


def _redfish_get(
    session: requests.Session, base: str, path: str, *, timeout: float = 30
) -> dict[str, Any] | None:
    try:
        response = session.get(f"{base}{path}", timeout=timeout)
    except requests.RequestException:
        return None
    if response.status_code != 200:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _first_member(collection: dict[str, Any] | None) -> str:
    for member in (collection or {}).get("Members", []) or []:
        if isinstance(member, dict) and member.get("@odata.id"):
            return str(member["@odata.id"])
    return ""


def _probe_redfish(address: str, options: DiscoveryOptions) -> ProbeResult:
    base = f"https://{address}"
    with requests.Session() as session:
        session.verify = options.redfish_verify
        session.headers.update({"Accept": "application/json", "User-Agent": USER_AGENT})
        if options.redfish_auth and options.redfish_user and options.redfish_pass:
            session.auth = (options.redfish_user, options.redfish_pass)
        if not options.redfish_verify:
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        try:
            response = session.get(f"{base}/redfish/v1", timeout=options.timeout)
        except requests.RequestException as exc:
            return ProbeResult(protocol="redfish", address=address, error=str(exc))
        if response.status_code in {401, 403}:
            return ProbeResult(
                protocol="redfish",
                address=address,
                reachable=True,
                auth_required=True,
                error="authentication required",
            )
        if response.status_code != 200:
            return ProbeResult(
                protocol="redfish",
                address=address,
                error=f"HTTP {response.status_code}",
            )
        try:
            root = response.json()
        except ValueError:
            return ProbeResult(
                protocol="redfish",
                address=address,
                reachable=True,
                error="invalid Redfish JSON",
            )
        if not isinstance(root, dict):
            return ProbeResult(
                protocol="redfish",
                address=address,
                reachable=True,
                error="invalid Redfish response",
            )

        systems_url = str(((root.get("Systems") or {}).get("@odata.id")) or "")
        collection = _redfish_get(
            session,
            base,
            systems_url or "/redfish/v1/Systems",
            timeout=options.timeout,
        )
        system_url = _first_member(collection)
        system = (
            _redfish_get(session, base, system_url, timeout=options.timeout)
            if system_url
            else None
        )
        if not system:
            return ProbeResult(
                protocol="redfish",
                address=address,
                reachable=True,
                auth_required=bool(systems_url),
                error="no readable system resource"
                if systems_url
                else "no Systems collection",
            )
        identity = {
            "name": str(system.get("HostName") or system.get("Name") or ""),
            "manufacturer": str(system.get("Manufacturer") or ""),
            "model": str(system.get("Model") or ""),
            "serial": str(system.get("SerialNumber") or system.get("SKU") or ""),
            "system_uuid": str(system.get("UUID") or ""),
            "description": str(system.get("Description") or system.get("Model") or ""),
            "power_state": str(system.get("PowerState") or ""),
        }
        return ProbeResult(
            protocol="redfish",
            address=address,
            reachable=True,
            identity={key: value for key, value in identity.items() if value},
        )


async def _probe_snmp_target(address: str, options: DiscoveryOptions) -> ProbeResult:
    data = await probe_snmp(
        address,
        community=options.snmp_community,
        timeout=options.snmp_timeout,
        retries=options.snmp_retries,
    )
    if not data:
        return ProbeResult(
            protocol="snmp",
            address=address,
            error="no SNMP response",
        )
    system = data.get("sys") or {}
    identity = {
        "name": str(system.get("name") or ""),
        "description": str(system.get("descr") or ""),
        "model": str(data.get("model") or ""),
        "serial": str(data.get("serial") or ""),
    }
    return ProbeResult(
        protocol="snmp",
        address=address,
        reachable=True,
        identity={key: value for key, value in identity.items() if value},
    )


def _probe_ipmi_target(address: str, options: DiscoveryOptions) -> ProbeResult:
    data = probe_ipmi(
        address,
        options.ipmi_user or "",
        options.ipmi_pass or "",
        timeout=options.ipmi_timeout,
    )
    if not data:
        return ProbeResult(protocol="ipmi", address=address, error="no IPMI response")
    system = data.get("system") or {}
    identity = {
        "name": "",
        "manufacturer": str(system.get("Manufacturer") or ""),
        "model": str(system.get("Model") or ""),
        "serial": str(system.get("SerialNumber") or system.get("SKU") or ""),
        "description": str(system.get("Model") or ""),
    }
    return ProbeResult(
        protocol="ipmi",
        address=address,
        reachable=True,
        identity={key: value for key, value in identity.items() if value},
    )


async def probe_targets(
    targets: list[ScanTarget],
    options: DiscoveryOptions,
) -> tuple[list[ProbeResult], list[str]]:
    protocols, warnings = available_protocols(options)
    if not protocols:
        return [], warnings

    semaphore = asyncio.Semaphore(max(1, options.concurrency))

    async def run(target: ScanTarget, protocol: str) -> ProbeResult | None:
        if protocol == "ipmi" and target.source == "network" and not options.ipmi_cidr_scan:
            return None
        async with semaphore:
            if protocol == "redfish":
                return await asyncio.to_thread(_probe_redfish, target.address, options)
            if protocol == "snmp":
                return await _probe_snmp_target(target.address, options)
            return await asyncio.to_thread(_probe_ipmi_target, target.address, options)

    tasks = [
        run(target, protocol)
        for target in targets
        for protocol in protocols
    ]
    results = await asyncio.gather(*tasks)
    return [result for result in results if result is not None], warnings
