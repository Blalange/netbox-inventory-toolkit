"""Command line entry point for the NetBox inventory toolkit."""

from __future__ import annotations

import sys

from . import __version__

USAGE = """\
usage: netbox-inventory-toolkit <command> [options]

commands:
  collect   collect live source data into the generated inventory layer
  sync      reconcile the inventory into NetBox
  check     validate the inventory without contacting NetBox
  lsp       run the inventory language server (stdio)
  version   print the package version
"""


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help", "help"}:
        print(USAGE, end="")
        return 0
    if args[0] in {"--version", "version"}:
        print(f"netbox-inventory-toolkit {__version__}")
        return 0

    command, rest = args[0], args[1:]
    if command == "collect":
        from .collect.cli import main as collect_main

        return collect_main(rest)
    if command in {"sync", "check"}:
        from .sync.cli import main as sync_main

        return sync_main([*rest, "--check"] if command == "check" else rest)
    if command == "lsp":
        from .lsp import main as lsp_main

        return lsp_main(rest)

    print(f"error: unknown command {command!r}\n\n{USAGE}", file=sys.stderr, end="")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
