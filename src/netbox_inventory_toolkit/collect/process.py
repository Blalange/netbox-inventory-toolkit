"""Subprocess and cluster-secret helpers."""

from __future__ import annotations

import base64
import json
import subprocess

from netbox_inventory_toolkit.inventory.util import InventoryError


def run(cmd: list[str], *, capture: bool = True) -> str:
    result = subprocess.run(
        cmd,
        check=True,
        capture_output=capture,
        text=True,
        encoding="utf-8",
    )
    return result.stdout

def kubectl_command(kubectl: str, kubeconfig: str | None, *args: str) -> list[str]:
    cmd = [kubectl]
    if kubeconfig:
        cmd.extend(["--kubeconfig", kubeconfig])
    cmd.extend(args)
    return cmd

def read_cluster_secret_value(
    kubectl: str,
    kubeconfig: str | None,
    namespace: str,
    name: str,
    key: str,
) -> str:
    raw = run(
        kubectl_command(
            kubectl,
            kubeconfig,
            "-n",
            namespace,
            "get",
            "secret",
            name,
            "-o",
            "json",
        )
    )
    secret = json.loads(raw)
    data = secret.get("data") or {}
    try:
        return base64.b64decode(data[key]).decode()
    except KeyError as exc:
        raise InventoryError(
            f"Secret {namespace}/{name} is missing key {key!r}"
        ) from exc


def read_cluster_secret(
    kubectl: str,
    kubeconfig: str | None,
    namespace: str,
    name: str,
    *,
    user_key: str = "username",
    pass_key: str = "password",
) -> tuple[str, str]:
    return (
        read_cluster_secret_value(kubectl, kubeconfig, namespace, name, user_key),
        read_cluster_secret_value(kubectl, kubeconfig, namespace, name, pass_key),
    )
