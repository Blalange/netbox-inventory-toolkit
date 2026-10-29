# netbox-inventory-toolkit

Collect a declarative YAML inventory from optional live sources, validate it,
and reconcile it into [NetBox](https://netbox.dev).

The toolkit is deliberately source-agnostic. UniFi, SNMP, Talos, Redfish and
IPMI are optional adapters; an inventory that only uses SNMP and IPMI works
without UniFi, and a manual-only inventory works without any live source.

## What it includes

- **Collector** — merges live observations into a generated inventory layer.
- **Sync** — reconciles the manual + generated layers into NetBox over the REST
  API, with optional Device Type Library enrichment and image uploads.
- **Language server** — diagnostics, completion, go-to-definition, hover and
  rename for inventory YAML.
- **VS Code extension** — a thin client for the language server.

## Install

Use `uv` to run a pinned revision without installing anything globally:

```sh
uvx --from git+https://github.com/Blalange/netbox-inventory-toolkit@v0.1.0 \
  netbox-inventory-toolkit --help
```

Or install it as a package:

```sh
pip install "netbox-inventory-toolkit[all]"
```

## Quick start: SNMP + IPMI only

The repository contains a minimal example inventory with one SNMP switch and
one IPMI server:

```sh
netbox-inventory-toolkit check --inventory examples/minimal --no-dtl
netbox-inventory-toolkit collect --inventory examples/minimal --sources snmp,ipmi
netbox-inventory-toolkit sync --inventory examples/minimal --check --no-dtl
```

The collector reads `snmp_ip` and `ipmi_ip` from manual device entries. No
UniFi controller or Kubernetes cluster is required.

## Commands

```text
netbox-inventory-toolkit collect --inventory inventory [--sources auto|all|none|list]
netbox-inventory-toolkit sync --inventory inventory --check
netbox-inventory-toolkit sync --inventory inventory --dry-run --prune
netbox-inventory-toolkit lsp --inventory inventory
```

`--sources auto` enables a source only when its configuration and credentials
are present. Explicitly selecting a source that is not configured is an error;
`auto` and `all` skip it with a warning.

## Optional sources

| Source | Configuration |
|---|---|
| UniFi | `UNIFI_URL`, `UNIFI_USER`, `UNIFI_PASS` or `--unifi-secret NAMESPACE/NAME` |
| SNMP | manual devices with `snmp_ip`; optional `--snmp-community` |
| Talos | a working `kubectl`, `--nodes-json`, and optional `--talosctl` |
| Redfish | manual devices with `redfish_ip`; `REDFISH_USER` / `REDFISH_PASS` |
| IPMI | manual devices with `ipmi_ip`; `ipmitool` and `IPMI_USER` / `IPMI_PASS` |

Optional Python extras:

- `snmp` adds `pysnmp`.
- `lsp` adds `pygls` and `lsprotocol`.
- `all` adds both.

## Inventory model

The inventory root contains:

```text
inventory/
├── manual/       # hand-maintained objects; wins over generated values
├── generated/    # collector output; do not edit
└── images/       # device-type images referenced from manual device types
```

See [docs/inventory-schema.md](docs/inventory-schema.md) for the schema,
natural keys and provenance tags.

## Container image

The release workflow publishes:

```text
ghcr.io/blalange/netbox-inventory-toolkit:v0.1.0
```

The image entrypoint is `netbox-inventory-toolkit` and the default command is
`sync`. Mount the inventory at `/inventory`, images at `/images`, and a writable
cache/media volume at `/media`:

```yaml
containers:
  - name: sync
    image: ghcr.io/blalange/netbox-inventory-toolkit:v0.1.0
    command: ["netbox-inventory-toolkit", "sync"]
    env:
      - name: NETBOX_URL
        value: http://netbox.netbox.svc.cluster.local
      - name: NETBOX_TOKEN
        valueFrom:
          secretKeyRef:
            name: netbox-secrets
            key: api_token_full
      - name: NETBOX_INVENTORY_DIR
        value: /inventory
      - name: NETBOX_IMAGES_DIR
        value: /images
      - name: DTL_CACHE_DIR
        value: /media/dtl-cache
```

See [docs/sync.md](docs/sync.md) for the full CronJob example and options.

## Development

```sh
uv sync --extra all --group dev
uv run ruff check .
uv run pytest
```

Package the VS Code extension:

```sh
cd editors/vscode/netbox-inventory-toolkit
npm ci
npm run package
```

## License

MIT
