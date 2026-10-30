from __future__ import annotations

from netbox_inventory_toolkit.collect.build import (
    _manual_uplink_interface_type,
    build_servers,
)


def redfish_payload() -> dict:
    return {
        "source": "redfish",
        "system": {"SKU": "ABC1234", "UUID": "uuid-1"},
        "cpu": [
            {
                "bay": "CPU 1",
                "manufacturer": "intel",
                "model": "Intel Xeon E-2314",
                "cores": 4,
                "speed": 2.8,
                "architecture": "x86_64",
                "serial": "",
            }
        ],
        "memory": [
            {
                "bay": "DIMM A1",
                "manufacturer": "micron",
                "model": "TEST-DIMM",
                "size_gb": 16,
                "class": "DDR4",
                "ecc": True,
                "data_rate": 3200,
                "serial": "S1",
            }
        ],
        "disks": [
            {
                "bay": "Drive 1",
                "manufacturer": "seagate",
                "model": "TEST-DISK",
                "size_gb": 1000,
                "type": "HD",
                "speed": 7200,
                "serial": "D1",
            }
        ],
        "fans": [
            {"bay": "Fan 1", "name": "Fan 1", "rpm": 3600, "status": "active"}
        ],
        "psus": [],
    }


def test_description_uses_manual_uplink_switch() -> None:
    manual = {
        "settings": {"site": "glemmen", "default_tag": "production"},
        "devices": [
            {
                "name": "server110",
                "role": "server",
                "device_type": "dell-poweredge-r250",
                "unifi_ip": "192.0.2.10",
                "unifi_mac": "00:11:22:33:44:55",
                "uplinks": [
                    {
                        "interface": "Port 1",
                        "switch": "Correct Switch",
                        "port": 7,
                        "type": "cat6",
                    }
                ],
            }
        ],
    }
    clients = [
        {
            "is_wired": True,
            "ip": "192.0.2.10",
            "mac": "00:11:22:33:44:55",
            "sw_port": 7,
            "name": "server110",
        }
    ]
    servers = build_servers(clients, manual, "Wrong Switch")
    assert "Correct Switch Port 7" in servers[0]["description"]
    assert "Wrong Switch" not in servers[0]["description"]


def test_dac_uplink_maps_to_sfp_interface() -> None:
    device = {
        "uplinks": [
            {"interface": "enp4s0f0", "switch": "switch", "port": 26, "type": "dac-passive"}
        ]
    }
    assert _manual_uplink_interface_type(device, "enp4s0f0") == "10gbase-x-sfpp"


def test_manual_only_server_gets_bmc_modules() -> None:
    manual = {
        "settings": {"site": "glemmen", "default_tag": "production"},
        "devices": [
            {
                "name": "server110",
                "role": "server",
                "device_type": "dell-poweredge-r250",
                "redfish_ip": "192.168.4.99",
            }
        ],
    }
    servers = build_servers(
        [],
        manual,
        "USW Pro 24 PoE",
        module_types={},
        bmc_by_name={"server110": redfish_payload()},
    )
    assert len(servers) == 1
    server = servers[0]
    assert server["name"] == "server110"
    assert server["sources"] == ["manual", "redfish"]
    assert len(server["module_bays"]) == 4
    assert len(server["modules"]) == 4
    assert "interfaces" not in server


def test_build_nodes_uses_cluster_specific_manual_device() -> None:
    from netbox_inventory_toolkit.collect.build import build_nodes

    manual = {
        "settings": {"site": "example", "roles": {"worker": "worker"}},
        "devices": [
            {
                "name": "cluster-a-node1",
                "site": "site-a",
                "role": "worker",
                "device_type": "generic-server",
                "talos_cluster": "cluster-a",
                "talos_node": "node1",
            },
            {
                "name": "cluster-b-node1",
                "site": "site-b",
                "role": "worker",
                "device_type": "generic-server",
                "talos_cluster": "cluster-b",
                "talos_node": "node1",
            },
        ],
    }
    node = {
        "metadata": {"name": "node1", "labels": {}},
        "status": {
            "addresses": [{"type": "InternalIP", "address": "192.0.2.10"}],
            "capacity": {"cpu": "4", "memory": "8Gi"},
            "conditions": [{"type": "Ready", "status": "True"}],
        },
    }

    result = build_nodes(
        [node],
        None,
        manual,
        default_site="example",
        cluster="cluster-b",
        manual_by_node={"node1": manual["devices"][1]},
    )

    assert result[0]["name"] == "cluster-b-node1"
    assert result[0]["site"] == "site-b"
