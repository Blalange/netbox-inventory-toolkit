from __future__ import annotations

from typing import Any

from netbox_inventory_toolkit.collect.sources import redfish


def test_read_redfish_discovers_standard_collections(monkeypatch) -> None:
    responses: dict[str, dict[str, Any]] = {
        "/redfish/v1": {
            "Systems": {"@odata.id": "/redfish/v1/Systems"},
            "Chassis": {"@odata.id": "/redfish/v1/Chassis"},
        },
        "/redfish/v1/Systems": {
            "Members": [{"@odata.id": "/redfish/v1/Systems/1"}]
        },
        "/redfish/v1/Systems/1": {
            "SerialNumber": "SERIAL1",
            "SKU": "SERIAL1",
            "UUID": "uuid-1",
            "Processors": {"@odata.id": "/redfish/v1/Systems/1/Processors"},
            "Memory": {"@odata.id": "/redfish/v1/Systems/1/Memory"},
            "Storage": {"@odata.id": "/redfish/v1/Systems/1/Storage"},
        },
        "/redfish/v1/Systems/1/Processors": {
            "Members": [{"@odata.id": "/redfish/v1/Systems/1/Processors/1"}]
        },
        "/redfish/v1/Systems/1/Processors/1": {
            "Manufacturer": "Intel",
            "Model": "Xeon",
            "TotalCores": 8,
            "OperatingSpeedMHz": 2800,
        },
        "/redfish/v1/Systems/1/Memory": {
            "Members": [{"@odata.id": "/redfish/v1/Systems/1/Memory/1"}]
        },
        "/redfish/v1/Systems/1/Memory/1": {
            "CapacityMiB": 32768,
            "Manufacturer": "Samsung",
            "PartNumber": "DIMM-1",
            "DeviceLocator": "DIMM A1",
            "MemoryDeviceType": "DDR4",
        },
        "/redfish/v1/Systems/1/Storage": {
            "Members": [{"@odata.id": "/redfish/v1/Systems/1/Storage/1"}]
        },
        "/redfish/v1/Systems/1/Storage/1": {
            "Drives": [{"@odata.id": "/redfish/v1/Systems/1/Storage/1/Drives/1"}]
        },
        "/redfish/v1/Systems/1/Storage/1/Drives/1": {
            "Model": "Disk",
            "SerialNumber": "DISK1",
            "CapacityBytes": 1_000_000_000_000,
            "MediaType": "SSD",
        },
        "/redfish/v1/Chassis": {
            "Members": [{"@odata.id": "/redfish/v1/Chassis/1"}]
        },
        "/redfish/v1/Chassis/1/Thermal": {
            "Fans": [{"Name": "Fan 1", "Reading": 4000}]
        },
        "/redfish/v1/Chassis/1/Power": {
            "PowerSupplies": [{"Model": "PSU", "PowerCapacityWatts": 750}]
        },
    }

    def fake_get(_session, _base, path):
        return responses.get(path)

    monkeypatch.setattr(redfish, "_redfish_get", fake_get)

    result = redfish.read_redfish("https://bmc.example", "user", "pass")

    assert result is not None
    assert result["system"]["SerialNumber"] == "SERIAL1"
    assert result["cpu"][0]["cores"] == 8
    assert result["memory"][0]["size_gb"] == 32
    assert result["disks"][0]["type"] == "SSD"
    assert result["fans"][0]["bay"] == "Fan 1"
    assert result["psus"][0]["wattage"] == 750
