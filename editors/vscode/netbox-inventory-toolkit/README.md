# NetBox inventory toolkit VS Code client

Thin VS Code client for the `netbox-inventory-toolkit lsp` language server.

The server provides:

- semantic diagnostics for the manual and generated inventory layers
- rack layout validation, including incomplete and overlapping layouts
- unknown reference warnings
- Ctrl-click/go-to-definition for slugs, device names, VLANs, contacts and
  module types
- hover details
- key and value completion
- workspace-wide rename of definitions and their references

## Install

Make sure `uv` is available, then build and install the extension:

```sh
cd editors/vscode/netbox-inventory-toolkit
npm install
npm run package
code --install-extension netbox-inventory-toolkit-0.2.1.vsix
```

Reload VS Code after installing. The extension starts the server in the
workspace root and passes the `netboxInventory.inventory` directory (default
`inventory`).

For development, open this folder in VS Code and press `F5` to launch an
Extension Development Host, then open the inventory repository in that window.

## Configuration

| Setting | Default | Purpose |
| --- | --- | --- |
| `netboxInventory.server.command` | `uvx` | Server command |
| `netboxInventory.server.args` | `uvx --python 3.13 --from "netbox-inventory-toolkit[lsp]==0.2.1" netbox-inventory-toolkit lsp` | Server arguments |
| `netboxInventory.server.cwd` | workspace root | Server working directory |
| `netboxInventory.inventory` | `inventory` | Inventory root containing `manual/` and `generated/` |

If you prefer a local virtual environment, set `server.command` to the Python
executable and replace `server.args` with:

```json
["-m", "netbox_inventory_toolkit", "lsp", "--inventory", "inventory"]
```

The extension does not install or use a JSON Schema. Field and value
completion comes from the language server's Python field catalog.
