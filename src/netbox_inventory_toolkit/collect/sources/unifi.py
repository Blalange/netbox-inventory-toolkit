"""UniFi API client and normalized UniFi observations."""

from __future__ import annotations

import ipaddress
from collections import Counter
from typing import Any

import requests

from netbox_inventory_toolkit.inventory.util import InventoryError, normalize_mac, slugify

from ..config import default_tag, require_site
from ..log import get_logger
from ..process import read_cluster_secret
from .base import Source, SourceState, UnifiData

log = get_logger(__name__)


def unifi_get(
    base: str,
    username: str,
    password: str,
    *,
    site: str = "default",
    verify_tls: bool = True,
    timeout: float = 30.0,
) -> tuple[list[dict], list[dict], list[dict]]:
    session = requests.Session()
    session.verify = verify_tls
    if not verify_tls:
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    session.headers.update({"User-Agent": "netbox-inventory-toolkit/0.1"})
    log.debug("UniFi: logging in to %s (site %s)", base, site)
    login = session.post(
        f"{base}/api/auth/login",
        json={"username": username, "password": password, "remember": True},
        timeout=timeout,
    )
    if login.status_code != 200:
        raise InventoryError(
            f"UniFi login failed: HTTP {login.status_code}: {login.text[:300]}"
        )

    site_path = f"{base}/proxy/network/api/s/{site}"
    devices_response = session.get(
        f"{site_path}/stat/device", timeout=timeout * 2
    )
    devices_response.raise_for_status()
    networks_response = session.get(
        f"{site_path}/rest/networkconf", timeout=timeout * 2
    )
    networks_response.raise_for_status()
    clients_response = session.get(
        f"{site_path}/stat/sta", timeout=timeout * 2
    )
    clients_response.raise_for_status()
    devices = devices_response.json().get("data") or []
    networks = networks_response.json().get("data") or []
    clients = clients_response.json().get("data") or []
    log.debug(
        "UniFi API: devices=%d, networks=%d, clients=%d",
        len(devices),
        len(networks),
        len(clients),
    )
    return devices, networks, clients

def unifi_device_name(device: dict, counts: Counter[str]) -> str:
    mac = str(device.get("mac") or "").replace(":", "")
    raw = (device.get("name") or device.get("hostname") or "").strip()
    if not raw:
        raw = f"UniFi device {mac[-4:]}"
    if counts[raw] > 1:
        return f"{raw} ({mac[-4:].lower()})"
    return raw

def unifi_status(device: dict) -> str:
    state = device.get("state")
    if state == 1:
        return "active"
    if state == 2:
        return "inventory"
    return "offline"

def interface_type(port: dict) -> str:
    media = str(port.get("media") or "").upper()
    speed = int(port.get("speed") or 0)
    if "SFP+" in media or "10G" in media:
        return "10gbase-x-sfpp"
    if media == "SFP":
        return "1000base-x-sfp"
    if media in {"2P5GE", "2.5GE"} or speed == 2500:
        return "2.5gbase-t"
    if media == "10GE" or speed >= 10000:
        return "10gbase-t"
    return "1000base-t"

def port_description(port: dict) -> str:
    up = port.get("up")
    if up is True:
        state = "Up"
    elif up is False:
        state = "Down"
    else:
        state = "Unknown"
    speed = int(port.get("speed") or 0)
    speed_text = f"{speed / 1000:g} Gbps" if speed else "no link"
    poe = " PoE" if port.get("port_poe") else ""
    return f"{state}, {speed_text}{poe}"

def build_unifi(
    devices: list[dict],
    networks: list[dict],
    manual: dict[str, Any],
    *,
    site: str | None = None,
    tag: str | None = None,
    models: dict[str, str] | None = None,
    vlan_names: dict[int, str] | None = None,
) -> tuple[list[dict], list[dict], list[dict], list[dict], dict[str, str]]:
    raw_names = [
        (d.get("name") or d.get("hostname") or f"UniFi device {str(d.get('mac') or '')[-4:]}").strip()
        for d in devices
    ]
    counts = Counter(raw_names)
    manual_by_serial = {
        str(device.get("unifi_serial")).strip().upper(): device
        for device in manual.get("devices") or []
        if device.get("unifi_serial")
    }
    manual_by_mac = {
        normalize_mac(device["unifi_mac"]): device
        for device in manual.get("devices") or []
        if device.get("unifi_mac")
    }
    names: dict[str, str] = {}
    for device in devices:
        mac = str(device.get("mac")).upper()
        serial = str(device.get("serial") or "").strip().upper()
        manual_device = manual_by_serial.get(serial)
        if manual_device is None and mac:
            manual_device = manual_by_mac.get(normalize_mac(mac))
        if manual_device and manual_device.get("name"):
            names[mac] = str(manual_device["name"])
        else:
            names[mac] = unifi_device_name(device, counts)
    by_mac = {str(d.get("mac")).upper(): d for d in devices}
    model_map = models if models is not None else (manual.get("unifi_models") or {})
    device_types = manual.get("device_types_by_slug") or {}
    site_slug = site or require_site(manual)
    tag_name = tag or default_tag(manual)

    unifi_devices: list[dict] = []
    cables: list[dict] = []
    cable_endpoints: set[tuple[str, str]] = set()

    for d in devices:
        mac = str(d.get("mac") or "").upper()
        name = names[mac]
        model = str(d.get("model") or "")
        serial = str(d.get("serial") or "").strip().upper()
        manual_device = manual_by_serial.get(serial)
        if manual_device is None and mac:
            manual_device = manual_by_mac.get(normalize_mac(mac))
        device_site = str((manual_device or {}).get("site") or site_slug)
        slug = model_map.get(model) or f"ubiquiti-{slugify(model or 'unifi-device')}"
        device_type = device_types.get(slug) or {}
        model_name = device_type.get("model") or model or "UniFi device"
        role = {"uap": "access-point", "usw": "switch", "udm": "gateway"}.get(
            str(d.get("type")), "switch"
        )
        description = (
            f"UniFi {model_name} ({model}), firmware {d.get('version') or 'unknown'}, "
            f"MAC {d.get('mac')}"
        )
        interfaces: list[dict] = []
        ports = d.get("port_table") or []
        port_by_idx: dict[int, dict] = {}
        log.debug(
            "UniFi device %s: model=%s, type=%s, state=%s, ports=%d",
            name,
            model,
            d.get("type"),
            d.get("state"),
            len(ports),
        )

        for port in ports:
            try:
                idx = int(port.get("port_idx"))
            except (TypeError, ValueError):
                continue
            port_by_idx[idx] = port
            interfaces.append(
                {
                    "name": f"Port {idx}",
                    "type": interface_type(port),
                    "enabled": True,
                    "description": port_description(port),
                    "mtu": None,
                    "mode": "",
                    "untagged_vlan": None,
                    "tagged_vlans": [],
                }
            )

        if not ports:
            interfaces.append(
                {
                    "name": "Port 1",
                    "type": "1000base-t",
                    "enabled": True,
                    "description": "Uplink Ethernet port",
                }
            )

        management_ips: list[dict] = []
        if role == "gateway":
            lan_ip = d.get("lan_ip")
            if lan_ip:
                management_ips.append({"address": f"{lan_ip}/24", "primary": True})
            for network in networks:
                subnet = network.get("ip_subnet")
                if subnet and network.get("purpose") in {"corporate", "remote-user-vpn"}:
                    address = str(ipaddress.ip_interface(subnet))
                    if not any(item["address"] == address for item in management_ips):
                        management_ips.append({"address": address})
            interfaces.insert(
                0,
                {
                    "name": "lan",
                    "type": "virtual",
                    "mgmt_only": False,
                    "enabled": True,
                    "description": "LAN and VPN gateway addresses",
                    "mac_address": d.get("mac"),
                    "ip_addresses": management_ips,
                },
            )
            wan = d.get("wan1") or {}
            if wan.get("ip") and wan.get("netmask") and 9 in port_by_idx:
                wan_address = str(ipaddress.ip_interface(f"{wan['ip']}/{wan['netmask']}"))
                for interface in interfaces:
                    if interface["name"] == "Port 9":
                        interface["description"] = "WAN uplink"
                        interface["ip_addresses"] = [{"address": wan_address, "description": "WAN"}]
                        break
        elif role == "switch":
            mgmt_ip = d.get("ip") or (d.get("config_network") or {}).get("ip")
            if mgmt_ip and interfaces:
                uplink = d.get("uplink") or {}
                local_port = uplink.get("port_idx")
                target_name = f"Port {int(local_port)}" if local_port else interfaces[0]["name"]
                target = next(
                    (interface for interface in interfaces if interface["name"] == target_name),
                    interfaces[0],
                )
                target.setdefault("mac_address", d.get("mac"))
                target.setdefault("ip_addresses", []).append(
                    {"address": f"{mgmt_ip}/24", "dns_name": slugify(name), "primary": True}
                )
        else:
            mgmt_ip = d.get("ip") or (d.get("config_network") or {}).get("ip")
            if mgmt_ip and interfaces:
                interfaces[0].setdefault("mac_address", d.get("mac"))
                interfaces[0].setdefault("ip_addresses", []).append(
                    {"address": f"{mgmt_ip}/24", "dns_name": slugify(name), "primary": True}
                )
            for radio in d.get("radio_table") or []:
                radio_name = str(radio.get("name") or "wifi")
                band = {"ng": "2.4 GHz", "na": "5 GHz", "6e": "6 GHz"}.get(
                    str(radio.get("radio")), str(radio.get("radio"))
                )
                interfaces.append(
                    {
                        "name": radio_name,
                        "type": "ieee802.11ax",
                        "enabled": True,
                        "description": f"{band} radio",
                    }
                )

        unifi_devices.append(
            {
                "name": name,
                "site": device_site,
                "role": role,
                "device_type": slug,
                "platform": "unifi",
                "status": unifi_status(d),
                "serial": d.get("serial") or "",
                "description": description,
                "tags": [tag_name],
                "sources": ["unifi"],
                "interfaces": interfaces,
            }
        )

    for d in sorted(devices, key=lambda item: (item.get("state") != 1, str(item.get("mac")))):
        mac = str(d.get("mac") or "").upper()
        child_name = names[mac]
        uplink = d.get("uplink") or {}
        parent_mac = str(uplink.get("uplink_mac") or "").upper()
        parent = by_mac.get(parent_mac)
        if not parent:
            continue
        parent_name = names[parent_mac]
        local_idx = uplink.get("port_idx")
        remote_idx = uplink.get("uplink_remote_port")
        if local_idx is None or remote_idx is None:
            continue
        local_if = f"Port {int(local_idx)}"
        parent_if = f"Port {int(remote_idx)}"
        if (parent_name, parent_if) in cable_endpoints or (child_name, local_if) in cable_endpoints:
            continue
        cable_endpoints.add((parent_name, parent_if))
        cable_endpoints.add((child_name, local_if))
        speed = int(uplink.get("speed") or 0)
        cables.append(
            {
                "a_device": child_name,
                "a_interface": local_if,
                "b_device": parent_name,
                "b_interface": parent_if,
                "type": "cat6a" if speed > 1000 else "cat6",
                "status": "connected",
                "description": f"UniFi uplink to {parent_name} {parent_if}",
                "sources": ["unifi"],
            }
        )

    # VLAN names can be corrected globally or per UniFi profile.
    vlan_name_map = (
        vlan_names
        if vlan_names is not None
        else {
            int(key): str(value)
            for key, value in (manual.get("unifi_vlan_names") or {}).items()
        }
    )
    vlans, prefixes = build_networks(
        networks,
        next((d for d in devices if d.get("type") == "udm"), None),
        vlan_name_map,
        site_slug,
    )
    log.debug(
        "UniFi build: devices=%d, cables=%d, vlans=%d, prefixes=%d",
        len(unifi_devices),
        len(cables),
        len(vlans),
        len(prefixes),
    )
    return unifi_devices, cables, vlans, prefixes, names

def build_networks(
    networks: list[dict],
    udm: dict | None,
    vlan_names: dict[int, str],
    site_slug: str,
) -> tuple[list[dict], list[dict]]:
    vlans: dict[int, dict] = {}
    prefixes: dict[str, dict] = {}
    for network in networks:
        vlan_id = network.get("vlan")
        name = str(network.get("name") or "").strip()
        if vlan_id is not None and int(vlan_id) in vlan_names:
            name = vlan_names[int(vlan_id)]
        purpose = network.get("purpose")
        if vlan_id is not None:
            vlans[int(vlan_id)] = {
                "vid": int(vlan_id),
                "name": name,
                "site": site_slug,
                "status": "active",
                "description": f"UniFi network: {name}",
                "sources": ["unifi"],
            }
        elif name == "Default":
            vlans[1] = {
                "vid": 1,
                "name": "Default",
                "site": site_slug,
                "status": "active",
                "description": "UniFi Default network (native VLAN).",
                "sources": ["unifi"],
            }
        subnet = network.get("ip_subnet")
        if not subnet:
            continue
        try:
            prefix = str(ipaddress.ip_interface(subnet).network)
        except ValueError:
            continue
        if prefix in prefixes:
            continue
        entry: dict[str, Any] = {
            "prefix": prefix,
            "site": site_slug,
            "status": "active",
            "description": f"UniFi {name} ({purpose or 'network'})",
            "sources": ["unifi"],
        }
        if name == "Default":
            entry["vlan"] = 1
        elif vlan_id is not None:
            entry["vlan"] = int(vlan_id)
        prefixes[prefix] = entry

    if udm:
        wan = udm.get("wan1") or {}
        if wan.get("ip") and wan.get("netmask"):
            try:
                prefix = str(ipaddress.ip_interface(f"{wan['ip']}/{wan['netmask']}").network)
                prefixes.setdefault(
                    prefix,
                    {
                        "prefix": prefix,
                        "site": site_slug,
                        "status": "active",
                        "description": "WAN (UDM Pro)",
                        "sources": ["unifi"],
                    },
                )
            except ValueError:
                pass

    return list(vlans.values()), list(prefixes.values())

def unifi_ip(device: dict[str, Any]) -> str:
    """Return the management address UniFi reports for a device."""
    return str(
        device.get("lan_ip")
        or device.get("ip")
        or (device.get("config_network") or {}).get("ip")
        or ""
    ).strip()


def register_unifi_devices(
    resolver: Any,
    devices: list[dict[str, Any]],
    names: dict[str, str],
) -> None:
    for device in devices:
        mac = str(device.get("mac") or "").upper()
        name = names.get(mac)
        if not name:
            continue
        resolver.register_generated_device(
            name,
            {
                "snmp_mac": {mac},
                "snmp_serial": {str(device.get("serial") or "")},
                "snmp_ip": {unifi_ip(device)},
                "snmp_name": {name},
            },
        )


class UnifiSource(Source):
    name = "unifi"

    def enabled(self, ctx: Any, state: SourceState) -> bool:
        config = ctx.config
        return bool(config and [profile for profile in config.unifi if profile.enabled])

    def collect(self, ctx: Any, state: SourceState) -> None:
        config = ctx.config
        if config is None:
            return
        errors: list[Exception] = []
        for profile in config.unifi:
            if not profile.enabled:
                continue
            username = profile.username
            password = profile.password
            if profile.secret:
                namespace, _, secret_name = profile.secret.partition("/")
                if not namespace or not secret_name:
                    raise InventoryError(
                        "UniFi secret must be NAMESPACE/NAME, "
                        "for example monitoring/unifi"
                    )
                username, password = read_cluster_secret(
                    config.kubectl,
                    config.kubeconfig,
                    namespace,
                    secret_name,
                )
            if not username or not password:
                raise InventoryError(
                    f"UniFi profile {profile.name!r} has no credentials"
                )
            try:
                devices, networks, clients = unifi_get(
                    profile.url.rstrip("/"),
                    username,
                    password,
                    site=profile.unifi_site,
                    verify_tls=profile.verify_tls,
                    timeout=profile.timeout,
                )
                built_devices, cables, vlans, prefixes, names = build_unifi(
                    devices,
                    networks,
                    ctx.manual,
                    site=profile.site,
                    models={**ctx.manual.get("unifi_models", {}), **profile.models},
                    vlan_names={
                        **ctx.manual.get("unifi_vlan_names", {}),
                        **{str(key): value for key, value in profile.vlan_names.items()},
                    },
                )
            except Exception as exc:  # noqa: BLE001 - one controller must not block others
                log.warning("UniFi profile %s failed: %s", profile.name, exc)
                errors.append(exc)
                continue
            register_unifi_devices(ctx.resolver, devices, names)
            state.unifi.append(
                UnifiData(
                    source=profile.name,
                    site=profile.site,
                    snmp_credential=profile.snmp_credential,
                    devices=built_devices,
                    vlans=vlans,
                    prefixes=prefixes,
                    cables=cables,
                    names=names,
                    clients=clients,
                    raw_devices=devices,
                )
            )
        if errors and not state.unifi:
            raise InventoryError(f"all UniFi profiles failed: {errors[-1]}")