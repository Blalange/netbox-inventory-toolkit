# Inventory schema

The inventory is plain YAML with two layers:

- `manual/` is maintained by hand and wins when the same natural key appears in
  both layers.
- `generated/` is written by the collector from live sources. Do not edit it by
  hand.

Every file declares its layer:

```yaml
layer: manual
```

## Root sections

| Key | Purpose |
|---|---|
| `layer` | `manual` or `generated`; required |
| `settings` | Collector configuration; ignored by NetBox |
| `defaults` | Defaults inherited by every device |
| `sites` | Required for devices, racks, VLANs and prefixes |
| `manufacturers` | Referenced by device types and platforms |
| `platforms` | Referenced by devices |
| `device_roles` | Referenced by devices |
| `owner_groups` | Optional groups for owners |
| `owners` | Referenced by devices via `owner` |
| `contact_roles` | Referenced by device contacts |
| `contacts` | Referenced by device contacts |
| `device_types` | Referenced by devices; may declare `dtl`, `images` and templates |
| `module_bay_types` | Referenced by module types and device module bays |
| `module_types` | Referenced by device modules; may declare `dtl` and templates |
| `racks` | Rack definitions plus declarative `layout` |
| `vlans` | Site-scoped or global |
| `prefixes` | Optionally attached to a site and VLAN |
| `devices` | Nested interfaces, module bays, modules, IPs, contacts and uplinks |
| `ip_addresses` | Standalone/unassigned IPs |
| `cables` | One declaration per physical cable |

Unknown root sections are rejected by the language server/sync validation.

## Settings

```yaml
settings:
  site: example               # required when the collector creates devices
  default_tag: production
  rack: Rack 1
  talos_platform: talos-linux
  roles:
    control_plane: control-plane
    worker: worker
  compute_roles:
    - server
  snmp:
    port_names: auto           # auto | port-number | ifname
    auto_unifi: true           # poll active UniFi devices for SNMP enrichment
```

`unifi_models` (UniFi model code -> device-type slug) and
`unifi_vlan_names` (VLAN ID -> corrected name) are also supported when the
UniFi source is used.

## Devices

A device needs `site`, `role` and `device_type`:

```yaml
devices:
  - name: server-01
    site: example
    role: server
    device_type: generic-server
    status: active
    serial: ABC123
    interfaces:
      - name: eno1
        type: 1000base-t
        ip_addresses:
          - address: 192.0.2.20/24
            dns_name: server-01
            primary: true
    module_bays:
      - name: Drive 1
        position: 1
        bay_type: drive-3-5
    modules:
      - bay: Drive 1
        module_type: generic-12tb-sas-hdd
```

Source-specific locators are optional:

| Field | Source |
|---|---|
| `unifi_ip`, `unifi_mac`, `unifi_serial` | UniFi |
| `talos_node` | Talos |
| `redfish_ip`, `oob_ip` | Redfish |
| `ipmi_ip` | IPMI |
| `snmp_ip`, `snmp_interfaces`, `snmp_cables` | SNMP |

The manual device is the identity registry. The collector matches observations
by hardware identifiers first (service tag, system UUID, serial, MAC), then by
locators (Talos node name, UniFi/Redfish/IPMI/SNMP address).

## Racks

Racks use a top-to-bottom `layout` instead of per-device positions:

```yaml
racks:
  - name: Rack 1
    site: example
    u_height: 42
    layout:
      - gap: 1
      - device: edge-switch
        face: rear
      - gap: 2
      - device: server-01
```

`gap: N` inserts empty units; `face: front|rear` marks rear-mounted devices.

## Provenance

Every generated object may carry a `sources` list. The layer merge unions the
lists and the sync job applies them as NetBox tags:

`manual`, `unifi`, `talos`, `redfish`, `ipmi`, `snmp`, `dtl`.

A `gitops` tag is applied to every taggable object managed by the sync. With
`--prune`, objects that still carry `gitops` but are no longer present in the
inventory are deleted. Objects created by hand in NetBox are never touched.

## Natural keys

- slugs for sites, manufacturers, platforms, roles, contact roles, module bay
  types and device types
- names for owner groups, owners and contacts
- `(site, name)` for racks
- `(site, vid)` for VLANs
- the prefix for prefixes
- the device name for devices
- `(device, name)` for interfaces and module bays
- `(device, bay)` for modules
- `(device, contact, role)` for contact assignments
- the address for IPs
- the two terminations for cables

Duplicate keys inside one layer are an error; a manual object merges into the
generated object with the same key.

## Not modelled

VRFs, tenants, contact groups, virtual machines, wireless, circuits, custom
fields and multi-termination cables. Add those in the NetBox UI if needed; they
will not carry the `gitops` tag and will not be pruned.
