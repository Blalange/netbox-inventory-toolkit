# Collector

The collector reads the manual inventory, runs the enabled sources, and writes:

- `generated/network.yaml` — network devices, VLANs, prefixes and cables
- `generated/compute.yaml` — servers and nodes with components
- `generated/module-types.yaml` — module types discovered across sources

It never edits `manual/`.

## Source selection

```sh
netbox-inventory-toolkit collect --inventory inventory --sources auto
netbox-inventory-toolkit collect --inventory inventory --sources snmp,ipmi
netbox-inventory-toolkit collect --inventory inventory --sources none
```

- `auto` enables a source only when it is configured.
- `all` attempts every source and skips unconfigured ones with a warning.
- `none` writes empty generated files (useful for manual-only inventories and
  pruning stale generated objects).
- A comma-separated list selects those sources explicitly. Selecting an
  unconfigured source is an error.

Per-source flags: `--no-unifi`, `--no-snmp`, `--no-talos`, `--no-redfish`,
`--no-ipmi`.

## SNMP

SNMP targets are the manual devices with `snmp_ip`:

```yaml
- name: edge-switch
  role: switch
  device_type: generic-switch
  snmp_ip: 192.0.2.10
  snmp_interfaces: true
  snmp_cables: true
```

- `snmp_interfaces: true` generates the interface list from the SNMP ifTable.
- `snmp_cables: true` discovers cables from LLDP and the forwarding table.
- Without `snmp_interfaces`, SNMP only fills missing MAC/VLAN fields on existing
  interfaces.

Interface naming is controlled by `settings.snmp.port_names`:

- `auto` — prefer `Port N`, otherwise the interface name (default)
- `port-number` — only `Port N`
- `ifname` — use `ifName`/`ifAlias`/`ifDescr`

SNMP v2c defaults to community `public`. Set `SNMP_COMMUNITY`,
`--snmp-community`, `SNMP_TIMEOUT` and `SNMP_RETRIES` as needed.
`--snmp-report PATH` writes raw observations for troubleshooting.

## IPMI

Devices with `ipmi_ip` are read with `ipmitool`:

```yaml
- name: server-01
  role: server
  device_type: generic-server
  ipmi_ip: 192.0.2.20
```

Credentials: `IPMI_USER` / `IPMI_PASS` (defaults `root` / `calvin`).
`IPMI_TOOL`, `IPMI_INTERFACE`, `IPMI_PRIVILEGE`, `IPMI_CIPHER` and
`IPMI_TIMEOUT` control the client. IPMI supplies FRU identity plus fan and PSU
sensors; CPU, memory and disks still come from Talos or the manual layer.

## Redfish

Devices with `redfish_ip` are read over Redfish:

```yaml
- name: server-02
  role: server
  device_type: generic-server
  redfish_ip: 192.0.2.30
  oob_ip: 192.0.2.30
```

The adapter discovers the first system and chassis from the Redfish root, then
reads processors, memory, storage, thermal and power collections. Dell OEM
fields are used as optional fallbacks. Credentials: `REDFISH_USER` /
`REDFISH_PASS` (defaults `readonly` / `readonly`). `REDFISH_VERIFY=true` enables
TLS verification; `REDFISH_SYSTEM_PATH` and `REDFISH_CHASSIS_PATH` override
discovery.

## Talos / Kubernetes

Talos is enabled when `kubectl` is available or `--nodes-json` is provided.
`--talosctl` adds hardware discovery (CPU, memory, disks, link MACs).
Endpoints are taken from the Kubernetes `InternalIP`; no hostname pattern is
assumed. `--talos-config-dir` optionally points at Talos node YAML files for
bond/interface details.

Roles and platform come from the manual device first, then from:

```yaml
settings:
  talos_platform: talos-linux
  roles:
    control_plane: control-plane
    worker: worker
```

## UniFi

UniFi is optional. It is enabled when `UNIFI_URL` and credentials are present:

```sh
UNIFI_URL=https://unifi.example \
UNIFI_USER=... UNIFI_PASS=... \
netbox-inventory-toolkit collect --inventory inventory --sources unifi,snmp
```

Credentials can also be read from a Kubernetes Secret with
`--unifi-secret NAMESPACE/NAME` (keys `username` and `password`).

`unifi_models` maps controller model codes to device types, and
`unifi_vlan_names` corrects VLAN names. Both live in the manual inventory.

## Logging

`-v/--verbose`, `--log-level`, `--log-file`, `COLLECTOR_LOG_LEVEL` and
`COLLECTOR_LOG_FILE` control logging. Logs go to stderr.
