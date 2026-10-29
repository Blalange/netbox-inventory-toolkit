# Discovery

`netbox-inventory-toolkit discover` suggests manual inventory entries by
inspecting the existing inventory and, optionally, probing network devices.

It never edits existing manual files. New entries and suggested updates are
printed or written to a separate file for review.

## Inventory-only discovery

```sh
netbox-inventory-toolkit discover --inventory inventory
```

This scans `manual/` and `generated/` and reports:

- generated-only devices that have no manual entry, with a suggested manual
  entry
- manual devices without any source locators (`redfish_ip`, `ipmi_ip`,
  `snmp_ip`, `oob_ip`, `unifi_ip`, `talos_node`)

No network traffic is generated.

## Network discovery

Network probes are opt-in:

```sh
# Explicit targets
netbox-inventory-toolkit discover --inventory inventory \
  --targets 192.168.4.130,192.168.4.2,192.168.4.60 \
  --protocols redfish,snmp,ipmi

# CIDR scan
netbox-inventory-toolkit discover --inventory inventory \
  --network 192.168.4.0/24 \
  --protocols redfish,snmp

# Probe only addresses already present in the inventory
netbox-inventory-toolkit discover --inventory inventory --scan-inventory

# Exclude addresses or networks
netbox-inventory-toolkit discover --inventory inventory \
  --network 192.168.4.0/24 \
  --exclude 192.168.4.1,192.168.4.3
```

By default, an explicit/CIDR scan also includes inventory locators. Use
`--no-inventory-targets` to scan only the addresses you passed.

### Protocols

- **Redfish** probes `https://<ip>/redfish/v1` and reads the first system's
  identity. Credentials are not sent unless `--redfish-auth` is set.
  `REDFISH_VERIFY=true` enables certificate verification.
- **SNMP** reads `sysDescr`, `sysName`, `sysObjectID` and ENTITY-MIB serial and
  model. It requires the `snmp` extra (`pysnmp`).
- **IPMI** reads FRU identity with `ipmitool`. Broad CIDR IPMI scans are
  disabled by default; pass `--ipmi-cidr-scan` to include them.

### Safety limits

- `--max-hosts` defaults to 1024.
- CIDR scans larger than 256 addresses require `--force`.
- `--concurrency` defaults to 32.
- `--timeout` controls Redfish; `--snmp-timeout` and `--ipmi-timeout` control
  the other protocols.
- Credentials are never logged.

## Matching

Probes are matched against the manual inventory by:

- service tag / serial
- system UUID
- MAC address
- SNMP name
- Redfish/IPMI/SNMP locator address
- exact device name

Results are grouped into:

| Status | Meaning |
|---|---|
| `existing` | already configured and matched |
| `update` | existing device is missing locators or identity fields |
| `new` | no manual entry; a suggestion is provided |
| `conflict` | probe identity matches multiple devices, or an existing field differs |
| `unreachable` | an inventory/explicit address did not answer |

## Output

### Table (default)

```sh
netbox-inventory-toolkit discover --inventory inventory
```

### YAML

```sh
netbox-inventory-toolkit discover --inventory inventory --format yaml
```

The YAML output is a valid `layer: manual` document containing new device
suggestions. Suggested additions to existing devices are printed as comments.

### JSON

```sh
netbox-inventory-toolkit discover --inventory inventory --format json
```

### Write new entries

```sh
netbox-inventory-toolkit discover --inventory inventory \
  --write-new inventory/manual/discovered.yaml
```

`--write-new`:

- refuses to overwrite an existing file
- refuses names that already exist in the manual layer
- writes only entries with `name`, `site`, `role` and `device_type`

Review the file, then add it to the inventory Kustomization if the inventory is
deployed from Git.

## Incomplete suggestions

If a new device cannot be assigned a role, device type or site, the suggestion
is reported but not written by `--write-new`. Supply defaults explicitly:

```sh
netbox-inventory-toolkit discover --inventory inventory \
  --network 192.168.4.0/24 \
  --default-role server \
  --default-device-type dell-poweredge-r250
```

## Credentials

| Protocol | Variables / flags |
|---|---|
| Redfish | `REDFISH_USER`, `REDFISH_PASS`, `--redfish-auth`, `--redfish-verify` |
| SNMP | `SNMP_COMMUNITY`, `--snmp-community` |
| IPMI | `IPMI_USER`, `IPMI_PASS`, `--ipmitool`, `--ipmi-cidr-scan` |
