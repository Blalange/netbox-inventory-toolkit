"""Talos and Kubernetes source adapters."""

from __future__ import annotations

import ipaddress
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml

from ..log import get_logger
from ..process import kubectl_command, run
from .base import Source, SourceState, TalosData

log = get_logger(__name__)


def read_nodes(kubectl: str, kubeconfig: str | None, nodes_json: str | None) -> list[dict]:
    try:
        if nodes_json:
            log.debug("Talos: reading node list from %s", nodes_json)
            nodes = json.loads(Path(nodes_json).read_text(encoding="utf-8-sig"))["items"]
        else:
            if shutil.which(kubectl) is None:
                log.warning(
                    "Talos: %r was not found; skipping Kubernetes node discovery", kubectl
                )
                return []
            log.debug("Talos: reading node list from kubectl")
            raw = run(kubectl_command(kubectl, kubeconfig, "get", "nodes", "-o", "json"))
            nodes = json.loads(raw)["items"]
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as exc:
        log.warning("Talos: could not read the Kubernetes node list: %s", exc)
        return []
    log.debug("Talos: node list contains %d node(s)", len(nodes))
    return nodes

def talosctl_command(
    talosctl: str,
    talosconfig: str | None,
    node: str,
    *args: str,
    endpoint: str | None = None,
) -> list[str]:
    cmd = [talosctl]
    if talosconfig:
        cmd.extend(["--talosconfig", talosconfig])
    if endpoint:
        cmd.extend(["--endpoints", endpoint])
    cmd.extend(["-n", node, *args])
    return cmd

def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False

def talos_table(
    talosctl: str,
    talosconfig: str | None,
    node: str,
    resource: str,
    *,
    endpoint: str | None = None,
) -> list[dict]:
    """Run ``talosctl get <resource> -o json`` and return the documents.

    Talos prints a stream of pretty-printed JSON documents (not one array), so
    decode the stream incrementally. Returns an empty list when the node is
    unreachable or the resource is not registered, so a missing node never
    breaks the whole inventory refresh.
    """
    try:
        raw = run(
            talosctl_command(
                talosctl, talosconfig, node, "get", resource, "-o", "json", endpoint=endpoint
            )
        )
    except (subprocess.CalledProcessError, OSError) as exc:
        log.warning("talosctl get %s on %s failed: %s", resource, node, exc)
        return []
    decoder = json.JSONDecoder()
    items: list[dict] = []
    index = 0
    while index < len(raw):
        while index < len(raw) and raw[index].isspace():
            index += 1
        if index >= len(raw):
            break
        try:
            payload, index = decoder.raw_decode(raw, index)
        except json.JSONDecodeError:
            next_object = raw.find("{", index + 1)
            if next_object == -1:
                break
            index = next_object
            continue
        if isinstance(payload, dict):
            items.append(payload)
        elif isinstance(payload, list):
            items.extend(item for item in payload if isinstance(item, dict))
    log.debug("talosctl get %s on %s returned %d item(s)", resource, node, len(items))
    return items

def disk_serial(spec: dict) -> str:
    serial = str(spec.get("serial") or "").strip()
    if serial:
        return serial
    for link in spec.get("symlinks") or []:
        name = Path(str(link)).name
        if name.startswith("ata-") and "_" in name:
            candidate = name.rsplit("_", 1)[-1]
            if candidate:
                return candidate
    return ""

def node_disks(
    talosctl: str,
    talosconfig: str | None,
    node: str,
    *,
    endpoint: str | None = None,
) -> tuple[list[dict], str | None]:
    """Return the physical disks and the system disk name for one node."""
    disks = []
    for item in talos_table(talosctl, talosconfig, node, "disks", endpoint=endpoint):
        spec = item.get("spec") or {}
        name = str(
            spec.get("dev_path")
            or spec.get("devPath")
            or item.get("metadata", {}).get("id")
            or ""
        ).rsplit("/", 1)[-1]
        transport = str(spec.get("transport") or "")
        if (
            not name
            or name.startswith("loop")
            or spec.get("cdrom")
            or transport == "iscsi"
            or str(spec.get("model") or "").upper() == "VIRTUAL-DISK"
        ):
            continue
        model = str(spec.get("model") or "").strip()
        if not model:
            # nvme/sata devices may expose the model through "device".
            model = str(spec.get("device") or "").strip()
        disks.append(
            {
                "name": name,
                "model": model,
                "serial": disk_serial(spec),
                "vendor": str(spec.get("modalias") or "").strip(),
                "wwid": str(spec.get("wwid") or ""),
                "size": int(spec.get("size") or 0),
                "transport": transport,
                "rotational": bool(spec.get("rotational")),
                "readonly": bool(spec.get("readonly")),
            }
        )

    system_disk = None
    for item in talos_table(talosctl, talosconfig, node, "systemdisks", endpoint=endpoint):
        spec = item.get("spec") or {}
        system_disk = str(
            spec.get("devPath")
            or spec.get("dev_path")
            or spec.get("diskID")
            or ""
        ).rsplit("/", 1)[-1] or None
        break
    return disks, system_disk

def node_link_macs(
    talosctl: str,
    talosconfig: str | None,
    node: str,
    *,
    endpoint: str | None = None,
) -> dict[str, str]:
    macs: dict[str, str] = {}
    for item in talos_table(talosctl, talosconfig, node, "links", endpoint=endpoint):
        spec = item.get("spec") or {}
        name = str(spec.get("name") or item.get("metadata", {}).get("id") or "")
        mac = str(spec.get("hardwareAddr") or spec.get("hardware_addr") or "")
        if name and mac and mac != "00:00:00:00:00:00":
            macs[name] = mac
    return macs

def node_link(node_name: str, talos_dir: Path | None) -> tuple[str, str] | None:
    if talos_dir is None:
        return None
    path = talos_dir / f"{node_name}.yaml"
    if not path.is_file():
        return None
    for document in yaml.safe_load_all(path.read_text(encoding="utf-8")):
        if not isinstance(document, dict):
            continue
        if document.get("kind") not in {"LinkConfig", "BondConfig"}:
            continue
        addresses = document.get("addresses") or []
        name = document.get("name")
        if name and addresses:
            address = addresses[0].get("address")
            if address:
                return str(name), str(address)
    return None

def node_bond_members(node_name: str, talos_dir: Path | None) -> list[str]:
    if talos_dir is None:
        return []
    path = talos_dir / f"{node_name}.yaml"
    if not path.is_file():
        return []
    for document in yaml.safe_load_all(path.read_text(encoding="utf-8")):
        if isinstance(document, dict) and document.get("kind") == "BondConfig":
            return [str(item) for item in document.get("links") or []]
    return []

def cpu_cores(value: str | None) -> float:
    if not value:
        return 0.0
    if value.endswith("m"):
        return float(value[:-1]) / 1000
    return float(value)

def memory_gib(value: str | None) -> float:
    if not value:
        return 0.0
    units = {"Ki": 1 / 1024 / 1024, "Mi": 1 / 1024, "Gi": 1, "Ti": 1024}
    for unit, factor in units.items():
        if value.endswith(unit):
            return float(value[: -len(unit)]) * factor
    return float(value) / 1024 / 1024


def node_system_info(
    talosctl: str,
    talosconfig: str | None,
    node: str,
    *,
    endpoint: str | None = None,
) -> dict[str, Any]:
    """Return the SMBIOS/system-information resource for one Talos node."""
    for item in talos_table(talosctl, talosconfig, node, "systeminformation", endpoint=endpoint):
        spec = item.get("spec") or {}
        if spec:
            return spec
    return {}

def _node_internal_ip(node: dict[str, Any]) -> str:
    for address in (node.get("status") or {}).get("addresses") or []:
        if address.get("type") == "InternalIP":
            return str(address.get("address") or "")
    return ""


def resolve_talos_devices(
    resolver: Any,
    nodes: list[dict[str, Any]],
    *,
    talosctl: str | None,
    talosconfig: str | None,
    cluster: str = "",
) -> dict[str, dict[str, Any]]:
    manual_by_node: dict[str, dict[str, Any]] = {}
    for node in nodes:
        name = str((node.get("metadata") or {}).get("name") or "")
        if not name:
            continue
        endpoint = _node_internal_ip(node) or None
        system_info = (
            node_system_info(talosctl, talosconfig, name, endpoint=endpoint)
            if talosctl
            else {}
        )
        serial = str(system_info.get("serialnumber") or "").strip()
        uuid = str(system_info.get("uuid") or "").strip()
        manual_device = None
        if cluster:
            composite_refs: dict[str, set[str]] = {
                "talos_cluster_node": {f"{cluster}/{name}"}
            }
            if serial:
                composite_refs["service_tag"] = {serial}
            if uuid:
                composite_refs["system_uuid"] = {uuid}
            manual_device = resolver.resolve(
                composite_refs, source="talos", label=f"{cluster}/{name}"
            )
        if manual_device is None:
            refs: dict[str, set[str]] = {
                "name": {name},
                "talos_node": {name},
            }
            if serial:
                refs["service_tag"] = {serial}
            if uuid:
                refs["system_uuid"] = {uuid}
            manual_device = resolver.resolve(refs, source="talos", label=name)
        if manual_device is None:
            log.warning(
                "Talos node %s%s has no manual device entry",
                f"{cluster}/" if cluster else "",
                name,
            )
            continue
        manual_by_node[name] = manual_device
        resolver.add_observation_claims(
            manual_device,
            {
                "service_tag": {serial},
                "system_uuid": {uuid},
            },
        )
    return manual_by_node


class TalosSource(Source):
    name = "talos"

    def enabled(self, ctx: Any, state: SourceState) -> bool:
        config = ctx.config
        if config is None:
            return False
        return any(
            profile.enabled
            and (profile.nodes_json or shutil.which(profile.kubectl))
            for profile in config.talos
        )

    def collect(self, ctx: Any, state: SourceState) -> None:
        config = ctx.config
        if config is None:
            return
        errors: list[Exception] = []
        for profile in config.talos:
            if not profile.enabled:
                continue
            try:
                nodes = read_nodes(
                    profile.kubectl, profile.kubeconfig, profile.nodes_json
                )
                if not nodes and not profile.nodes_json and shutil.which(
                    profile.kubectl
                ) is None:
                    raise OSError(
                        f"{profile.kubectl!r} was not found for Talos profile "
                        f"{profile.name!r}"
                    )
                manual_by_node = resolve_talos_devices(
                    ctx.resolver,
                    nodes,
                    talosctl=profile.talosctl,
                    talosconfig=profile.talosconfig,
                    cluster=profile.name,
                )
            except Exception as exc:  # noqa: BLE001 - one cluster must not block others
                log.warning("Talos profile %s failed: %s", profile.name, exc)
                errors.append(exc)
                continue
            state.talos.append(
                TalosData(
                    source=profile.name,
                    site=profile.site,
                    config_dir=Path(profile.config_dir) if profile.config_dir else None,
                    talosctl=profile.talosctl,
                    talosconfig=profile.talosconfig,
                    nodes=nodes,
                    manual_by_node=manual_by_node,
                )
            )
        if errors and not state.talos:
            raise errors[-1]