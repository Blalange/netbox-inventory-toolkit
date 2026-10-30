"""IPMI source adapter for BMCs without Redfish support."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

from netbox_inventory_toolkit.inventory.util import InventoryError

from ..hardware import normalize_manufacturer
from ..log import get_logger
from .base import Source, SourceState

log = get_logger(__name__)

IPMI_BINARY = os.environ.get("IPMI_TOOL", "ipmitool")


@dataclass(frozen=True)
class IpmiOptions:
    binary: str = "ipmitool"
    interface: str = "lanplus"
    privilege: str = "USER"
    cipher: str = ""
    timeout: float = 30.0

    @classmethod
    def from_env(cls) -> IpmiOptions:
        return cls(
            binary=os.environ.get("IPMI_TOOL", "ipmitool"),
            interface=os.environ.get("IPMI_INTERFACE", "lanplus"),
            privilege=os.environ.get("IPMI_PRIVILEGE", "USER"),
            cipher=os.environ.get("IPMI_CIPHER", ""),
            timeout=float(os.environ.get("IPMI_TIMEOUT", "30")),
        )


def _options(value: IpmiOptions | None = None) -> IpmiOptions:
    return value or IpmiOptions.from_env()


def _sensor_status(status: str) -> str:
    value = status.strip().lower()
    if value == "ok":
        return "active"
    if value in {"ns", "disabled", "not readable"}:
        return "inventory"
    return "failed"


def _fru_devices(text: str) -> list[dict[str, str]]:
    """Split ``ipmitool fru print`` output into per-FRU field mappings."""
    devices: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in text.splitlines():
        if line.strip().startswith("FRU Device Description"):
            if current:
                devices.append(current)
            current = {}
            key, _, value = line.partition(":")
            current[key.strip()] = value.strip()
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key and value and key not in current:
            current[key] = value
    if current:
        devices.append(current)
    return devices


def parse_fru(text: str) -> dict[str, str]:
    """Extract the system identity fields from ``ipmitool fru print``."""
    devices = _fru_devices(text)
    fields = devices[0] if devices else {}
    product_name = fields.get("Product Name", "").strip()
    board_product = fields.get("Board Product", "").strip()
    return {
        "Manufacturer": fields.get("Product Manufacturer")
        or fields.get("Board Mfg")
        or "",
        "Model": product_name if len(product_name) > 2 else board_product,
        "SerialNumber": fields.get("Product Serial") or fields.get("Board Serial") or "",
        "SKU": fields.get("Product Serial") or fields.get("Board Serial") or "",
    }


def parse_fru_psus(text: str, *, manufacturer: str = "") -> list[dict[str, Any]]:
    """Extract power-supply FRUs from ``ipmitool fru print`` output."""
    vendor = manufacturer or "generic"
    result: list[dict[str, Any]] = []
    for index, fields in enumerate(_fru_devices(text), 1):
        description = fields.get("FRU Device Description", "")
        product = fields.get("Board Product", "").strip()
        haystack = f"{description} {product}".lower()
        if not (
            re.match(r"^(?:PS|PSU|POWER\s*SUPPLY)\s*\d+\b", description, re.IGNORECASE)
            or "pwr spl" in haystack
            or "power supply" in haystack
        ):
            continue
        number = re.search(r"(\d+)", description)
        bay = f"PSU {number.group(1)}" if number else f"PSU {index}"
        wattage = re.search(r"(\d+)\s*W\b", product, re.IGNORECASE)
        board_mfg = fields.get("Board Mfg", "")
        result.append(
            {
                "bay": bay,
                "manufacturer": (
                    normalize_manufacturer(board_mfg) if board_mfg else vendor
                ),
                "model": product or "Power Supply",
                "serial": fields.get("Board Serial", ""),
                "wattage": int(wattage.group(1)) if wattage else 0,
                "status": "active",
            }
        )
    return result


def parse_sdr(
    text: str,
    *,
    manufacturer: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Extract fan and power-supply modules from ``ipmitool sdr`` output."""
    vendor = manufacturer or "generic"
    fans: dict[str, dict[str, Any]] = {}
    psus: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 2:
            continue
        name, value = parts[0], parts[1]
        status = _sensor_status(parts[2] if len(parts) > 2 else "")

        rpm_match = re.search(r"(\d+)\s*RPM", value, re.IGNORECASE)
        if rpm_match and "fan" in name.lower():
            bay = re.sub(r"\s+RPM$", "", name, flags=re.IGNORECASE).strip() or name
            fans[bay] = {
                "bay": bay,
                "name": name,
                "rpm": int(rpm_match.group(1)),
                "status": status,
                "manufacturer": vendor,
            }
            continue

        psu_match = re.match(
            r"^(?:PSU|PS|PWR|POWER\s*SUPPLY)\s*(\d+)(?:\s+(.*))?$",
            name,
            re.IGNORECASE,
        )
        if psu_match:
            bay = f"PSU {psu_match.group(1)}"
            existing = psus.get(bay)
            if existing is None or (existing["status"] == "active" and status == "failed"):
                psus[bay] = {
                    "bay": bay,
                    "manufacturer": vendor,
                    "model": "Power Supply",
                    "serial": "",
                    "wattage": 0,
                    "status": status,
                }
    return list(fans.values()), list(psus.values())


def _run(
    address: str,
    username: str,
    password: str,
    *args: str,
    timeout: float | None = None,
    options: IpmiOptions | None = None,
) -> str | None:
    options = _options(options)
    binary = shutil.which(options.binary)
    if not binary:
        log.warning(
            "IPMI %s: %s is not installed; install ipmitool or set IPMI_TOOL",
            address,
            options.binary,
        )
        return None
    command = [
        binary,
        "-I",
        options.interface,
        "-H",
        address,
        "-U",
        username,
        "-E",
        "-L",
        options.privilege,
        "-N",
        "3",
        "-R",
        "1",
    ]
    if options.cipher:
        command.extend(["-C", options.cipher])
    command.extend(args)
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout if timeout is not None else options.timeout,
            env={**os.environ, "IPMI_PASSWORD": password},
        )
    except subprocess.TimeoutExpired:
        log.warning(
            "IPMI %s: ipmitool timed out after %.0fs",
            address,
            timeout if timeout is not None else options.timeout,
        )
        return None
    except OSError as exc:
        log.warning("IPMI %s: cannot run ipmitool: %s", address, exc)
        return None
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        message = detail[-1] if detail else f"exit {result.returncode}"
        if "unable to establish" in message.lower():
            message += (
                "; check that IPMI over LAN is enabled and the account has IPMI"
                " privilege"
            )
        log.warning(
            "IPMI %s: ipmitool %s failed: %s",
            address,
            " ".join(args),
            message,
        )
        return None
    return result.stdout


def probe_ipmi(
    address: str,
    username: str,
    password: str,
    *,
    timeout: float | None = None,
    options: IpmiOptions | None = None,
) -> dict[str, Any] | None:
    """Read only FRU identity from an IPMI BMC."""
    options = _options(options)
    if not shutil.which(options.binary):
        return None
    fru_text = _run(
        address,
        username,
        password,
        "fru",
        "print",
        timeout=timeout,
        options=options,
    )
    if fru_text is None:
        return None
    system = parse_fru(fru_text)
    if not any(system.values()):
        return None
    return {"system": system}


def read_ipmi(
    address: str,
    username: str,
    password: str,
    *,
    options: IpmiOptions | None = None,
) -> dict[str, Any] | None:
    """Read FRU identity and fan/PSU sensors from an IPMI BMC."""
    options = _options(options)
    if not shutil.which(options.binary):
        log.warning(
            "IPMI %s: %s is not installed; install ipmitool or set IPMI_TOOL",
            address,
            options.binary,
        )
        return None
    log.debug("IPMI: reading %s", address)
    fru_text = _run(
        address, username, password, "fru", "print", options=options
    )
    sdr_text = _run(address, username, password, "sdr", options=options)
    if fru_text is None and sdr_text is None:
        return None

    system = parse_fru(fru_text or "")
    vendor = (
        normalize_manufacturer(system.get("Manufacturer"))
        if system.get("Manufacturer")
        else ""
    )
    fans, sensor_psus = parse_sdr(sdr_text or "", manufacturer=vendor)
    psu_by_bay = {psu["bay"]: psu for psu in sensor_psus}
    for psu in parse_fru_psus(fru_text or "", manufacturer=vendor):
        psu_by_bay[psu["bay"]] = psu
    psus = list(psu_by_bay.values())
    if not any(system.values()) and not fans and not psus:
        log.warning("IPMI %s returned no usable data", address)
        return None
    log.debug(
        "IPMI %s: model=%s serial=%s fans=%d psus=%d",
        address,
        system.get("Model"),
        system.get("SerialNumber"),
        len(fans),
        len(psus),
    )
    return {
        "system": system,
        "cpu": [],
        "memory": [],
        "disks": [],
        "fans": fans,
        "psus": psus,
    }

def collect_ipmi(
    manual: dict[str, Any],
    resolver: Any,
    *,
    username: str = "",
    password: str = "",
    options: IpmiOptions | None = None,
    profile_for: Any = None,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for device in manual.get("devices") or []:
        address = str(device.get("ipmi_ip") or "").strip()
        if not address:
            continue
        if profile_for is not None:
            profile = profile_for(device, address)
            if profile is None:
                continue
            data = read_ipmi(
                address,
                profile.username,
                profile.password,
                options=IpmiOptions(
                    binary=profile.binary,
                    interface=profile.interface,
                    privilege=profile.privilege,
                    cipher=profile.cipher,
                    timeout=profile.timeout,
                ),
            )
        elif options is None:
            data = read_ipmi(address, username, password)
        else:
            data = read_ipmi(address, username, password, options=options)
        if not data:
            continue
        system = data.get("system") or {}
        serial = str(system.get("SerialNumber") or system.get("SKU") or "").strip()
        refs: dict[str, set[str]] = {"ipmi_ip": {address}}
        if serial:
            refs["service_tag"] = {serial}
        resolved = resolver.resolve(refs, source="ipmi", label=address)
        if resolved is None:
            log.warning("IPMI %s is not mapped to a manual device", address)
            continue
        name = str(resolved.get("name") or "")
        if not name:
            continue
        if serial:
            resolver.add_observation_claims(resolved, {"service_tag": {serial}})
        if name in result:
            raise InventoryError(f"multiple IPMI BMCs resolve to {name!r}")
        result[name] = {**data, "source": "ipmi"}
    return result


class IpmiSource(Source):
    name = "ipmi"

    def enabled(self, ctx: Any, state: SourceState) -> bool:
        config = ctx.config
        if config is None:
            return False
        profiles = [profile for profile in config.ipmi if profile.enabled]
        return bool(
            profiles
            and any(shutil.which(profile.binary) for profile in profiles)
            and any(
                device.get("ipmi_ip")
                for device in ctx.manual.get("devices") or []
            )
        )

    def collect(self, ctx: Any, state: SourceState) -> None:
        config = ctx.config
        if config is None:
            return

        def profile_for(device: dict[str, Any], address: str) -> Any:
            try:
                return config.ipmi_profile_for(device=device, address=address)
            except InventoryError as exc:
                log.warning(
                    "IPMI %s (%s): %s", device.get("name") or address, address, exc
                )
                return None

        state.ipmi = collect_ipmi(
            ctx.manual,
            ctx.resolver,
            profile_for=profile_for,
        )
