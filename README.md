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

Use `uv` to run a pinned release without installing anything globally:

```sh
uvx --python 3.13 --from "netbox-inventory-toolkit[all]==0.3.2" \
  netbox-inventory-toolkit --help
```

Or install it as a package from [PyPI](https://pypi.org/project/netbox-inventory-toolkit/):

```sh
pip install "netbox-inventory-toolkit[all]"
```

Tagged releases are published to PyPI from the `release.yml` workflow using
PyPI trusted publishing. The trusted publisher is configured for this
repository, workflow `release.yml`, and environment `pypi`.

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

For anything beyond a single credential, create `inventory/settings.yml`:

```yaml
version: 1
defaults:
  site: example
sources:
  snmp:
    - name: default
      community: {from_env: SNMP_COMMUNITY, default: public}
      default: true
  ipmi:
    - name: default
      username: {from_env: IPMI_USER, default: root}
      password: {from_env: IPMI_PASS, default: calvin}
      default: true
```

See [docs/configuration.md](docs/configuration.md) for multiple UniFi
controllers, Talos clusters, SNMP communities and Redfish/IPMI credentials.

## Commands

```text
netbox-inventory-toolkit discover --inventory inventory
netbox-inventory-toolkit discover --inventory inventory --network 192.0.2.1/24 --protocols redfish,snmp
netbox-inventory-toolkit config check --inventory inventory
netbox-inventory-toolkit config show --inventory inventory
netbox-inventory-toolkit collect --inventory inventory [--sources auto|all|none|list]
netbox-inventory-toolkit sync --inventory inventory --check
netbox-inventory-toolkit sync --inventory inventory --dry-run --prune
netbox-inventory-toolkit lsp --inventory inventory
```

`--sources auto` enables a source only when its configuration and credentials
are present. Explicitly selecting a source that is not configured is an error;
`auto` and `all` skip it with a warning.

`discover` scans the existing manual/generated inventory and can optionally
probe Redfish, SNMP and IPMI endpoints. It prints suggested manual entries and
never edits existing files. See [docs/discovery.md](docs/discovery.md).

## Optional sources

| Source | Configuration |
|---|---|
| UniFi | one or more `sources.unifi` profiles (`url`, credentials, `site`) |
| SNMP | one or more `sources.snmp` profiles (`community` plus optional `match`) |
| Talos | one or more `sources.talos` profiles (`kubeconfig`, `talosconfig`, tools) |
| Redfish | one or more `sources.redfish` credential profiles |
| IPMI | one or more `sources.ipmi` credential profiles |

Old flags and environment variables are still accepted when no
`settings.yml` exists, but they are deprecated and log a warning.

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
ghcr.io/blalange/netbox-inventory-toolkit:v0.3.2
```

The release workflow tries to make the GHCR package public. If it remains
private, either change the package visibility in the GitHub package settings or
add an `imagePullSecrets` entry with a token that has `read:packages`.

The image entrypoint is `netbox-inventory-toolkit` and the default command is
`sync`. Mount the inventory at `/inventory`, images at `/images`, and a writable
cache/media volume at `/media`:

```yaml
containers:
  - name: sync
    image: ghcr.io/blalange/netbox-inventory-toolkit:v0.3.2
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
