from __future__ import annotations

import asyncio
from pathlib import Path

from netbox_inventory_toolkit.discover import probes
from netbox_inventory_toolkit.discover.models import DiscoveryOptions


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


class FakeSession:
    def __init__(self) -> None:
        self.verify = True
        self.auth = None
        self.headers: dict[str, str] = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url: str, timeout: int = 30) -> FakeResponse:
        payloads = {
            "https://192.0.2.20/redfish/v1": {
                "Systems": {"@odata.id": "/redfish/v1/Systems"}
            },
            "https://192.0.2.20/redfish/v1/Systems": {
                "Members": [{"@odata.id": "/redfish/v1/Systems/1"}]
            },
            "https://192.0.2.20/redfish/v1/Systems/1": {
                "HostName": "server-01",
                "Manufacturer": "Dell Inc.",
                "Model": "PowerEdge R250",
                "SerialNumber": "ABC123",
                "UUID": "uuid-1",
            },
        }
        return FakeResponse(200, payloads.get(url, {}))


def test_redfish_probe_extracts_identity(monkeypatch) -> None:
    monkeypatch.setattr(probes.requests, "Session", FakeSession)
    options = DiscoveryOptions(inventory=Path("."))
    result = probes._probe_redfish("192.0.2.20", options)

    assert result.reachable
    assert result.identity["name"] == "server-01"
    assert result.identity["manufacturer"] == "Dell Inc."
    assert result.identity["serial"] == "ABC123"
    assert result.identity["system_uuid"] == "uuid-1"


def test_snmp_probe_maps_identity(monkeypatch) -> None:
    async def fake_probe(*args, **kwargs):
        return {
            "ip": "192.0.2.21",
            "sys": {"name": "edge-switch", "descr": "Generic switch"},
            "model": "GS1920-24",
            "serial": "SW123",
        }

    monkeypatch.setattr(probes, "probe_snmp", fake_probe)
    options = DiscoveryOptions(inventory=Path("."))
    result = asyncio.run(probes._probe_snmp_target("192.0.2.21", options))

    assert result.reachable
    assert result.identity["name"] == "edge-switch"
    assert result.identity["model"] == "GS1920-24"
    assert result.identity["serial"] == "SW123"


def test_ipmi_probe_maps_identity(monkeypatch) -> None:
    def fake_probe(*args, **kwargs):
        return {
            "system": {
                "Manufacturer": "Dell Inc.",
                "Model": "PowerEdge R515",
                "SerialNumber": "JVCJGZ1",
            }
        }

    monkeypatch.setattr(probes, "probe_ipmi", fake_probe)
    options = DiscoveryOptions(inventory=Path("."))
    result = probes._probe_ipmi_target("192.0.2.22", options)

    assert result.reachable
    assert result.identity["manufacturer"] == "Dell Inc."
    assert result.identity["model"] == "PowerEdge R515"
    assert result.identity["serial"] == "JVCJGZ1"
