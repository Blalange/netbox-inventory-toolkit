# Editor support

The language server understands both inventory layers and runs the same
validation rule engine as `sync --check`.

## Features

- diagnostics for field types, unknown fields, unknown references, duplicate
  natural keys, rack layout arithmetic and overlaps, cable/uplink endpoints,
  module compatibility and missing image files
- go-to-definition for sites, roles, device types, module types, module bay
  types, manufacturers, platforms, owners, contacts, racks, VLANs, devices,
  module bays and interfaces
- hover details for definitions and missing references
- key and value completion, including slugs, names, VLAN IDs, rack devices,
  cable endpoints, image paths, booleans, tags and NetBox choice values
- workspace-wide rename of a definition and all of its references

## Run the server

```sh
netbox-inventory-toolkit lsp --inventory inventory
```

The server speaks LSP over stdio. Use the VS Code extension in
`editors/vscode/netbox-inventory-toolkit/` for the client.

## VS Code

Build and install the extension:

```sh
cd editors/vscode/netbox-inventory-toolkit
npm ci
npm run package
code --install-extension netbox-inventory-toolkit-0.2.0.vsix
```

Settings:

| Setting | Default |
|---|---|
| `netboxInventory.server.command` | `uvx` |
| `netboxInventory.server.args` | `--python 3.13 --from "netbox-inventory-toolkit[lsp]==0.2.0" netbox-inventory-toolkit lsp` |
| `netboxInventory.server.cwd` | workspace root |
| `netboxInventory.inventory` | `inventory` |

For a local checkout, override the command:

```json
{
  "netboxInventory.server.command": "uv",
  "netboxInventory.server.args": ["run", "--extra", "lsp", "netbox-inventory-toolkit", "lsp"],
  "netboxInventory.inventory": "inventory"
}
```

The extension activates for YAML files under any `manual/` or `generated/`
directory. It does not use a JSON Schema; completion comes from the server's
field catalog.
