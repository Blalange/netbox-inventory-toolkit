"""NetBox inventory toolkit."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("netbox-inventory-toolkit")
except PackageNotFoundError:  # running from a source tree without installed metadata
    __version__ = "0.2.1"
