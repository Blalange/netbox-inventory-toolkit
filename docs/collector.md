# Collector

The collector reads the manual inventory, runs the enabled sources, and writes:

- `generated/network.yaml` — network devices, VLANs, prefixes and cables
- `generated/compute.yaml` — servers and nodes with components
- `generated/module-types.yaml` — module types discovered across sources

It never edits `manual/`.

Collector endpoints, credentials and multi-source settings live in
`inventory/settings.yml`; see [configuration.md](configuration.md). Legacy
flags and environment variables still work without that file, but are
deprecated and log a warning.

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
`--no-ipmi`. These override `collector.sources` and remain supported.

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

When the UniFi source is enabled, active UniFi devices are also polled for SNMP
enrichment by default. This fills VLAN membership for devices that are generated
from UniFi but have no manual `snmp_ip`. Disable it with:

```yaml
collector:
  snmp:
    auto_unifi: false
```

Uplink entries may set `interface_type` to override the interface type inferred
from the cable `type` (for example `dac-passive` → `10gbase-x-sfpp`).

SNMP v2c communities and timeouts are configured with one or more
`sources.snmp` profiles. A device can select a profile explicitly:

```yaml
- name: old-switch
  snmp_ip: 192.0.2.10
  snmp_credential: legacy
```

Profiles may also select devices with `match` rules and one profile may be the
`default`. Targets are grouped by profile, so every device is polled with the
right community exactly once. `--snmp-report PATH` writes raw observations for
troubleshooting.

## IPMI

Devices with `ipmi_ip` are read with `ipmitool`:

```yaml
- name: server-01
  role: server
  device_type: generic-server
  ipmi_ip: 192.0.2.20
```

Credentials and client options come from `sources.ipmi` profiles. Devices can
select one with `ipmi_credential`; otherwise `match` and `default` rules apply.
Each profile supports `username`, `password`, `binary`, `interface`,
`privilege`, `cipher` and `timeout`. IPMI supplies FRU identity plus fan and PSU
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
fields are used as optional fallbacks. Credentials and TLS settings come from
`sources.redfish` profiles; devices may select one with `redfish_credential`.
`system_path` and `chassis_path` override standard collection discovery.

## Talos / Kubernetes

Each `sources.talos` profile is one cluster. It may set `kubeconfig`,
`talosconfig`, `kubectl`, `talosctl`, `nodes_json` and `config_dir`. `talosctl`
adds hardware discovery (CPU, memory, disks, link MACs). Endpoints are taken
from the Kubernetes `InternalIP`; no hostname pattern is assumed.

Manual devices may set `talos_cluster` to select the profile. This is required
when the same node name exists in more than one cluster. Roles and platform
come from the manual device first, then from:

```yaml
settings:
  talos_platform: talos-linux
  roles:
    control_plane: control-plane
    worker: worker
```

## UniFi

UniFi is optional. Each `sources.unifi` profile is one controller and one
UniFi site (`unifi_site`, default `default`). Give each profile a NetBox `site`.
Multiple profiles can use the same controller URL with different `unifi_site`
values, or point at different controllers.

Credentials may be literal values, environment references, files or
Kubernetes Secrets:

```yaml
sources:
  unifi:
    - name: glemmen
      url: https://unifi.example
      site: glemmen
      username: {from_k8s_secret: monitoring/unpoller-unifi#username}
      password: {from_k8s_secret: monitoring/unpoller-unifi#password}
      snmp_credential: glemmen
```

`defaults.unifi_models` maps controller model codes to device types, and
`defaults.unifi_vlan_names` corrects VLAN names. Both can be overridden per
profile.

## Logging

`-v/--verbose`, `--log-level`, `--log-file`, `COLLECTOR_LOG_LEVEL` and
`COLLECTOR_LOG_FILE` control logging. Logs go to stderr.
