"""Compose the resource mixins into the NetBox reconciler."""

from __future__ import annotations

from netbox_inventory_toolkit.inventory.schema import MANAGED_TAG_NAME
from netbox_inventory_toolkit.sync.context import SyncContext
from netbox_inventory_toolkit.sync.devices import DevicesMixin
from netbox_inventory_toolkit.sync.images import ImagesMixin
from netbox_inventory_toolkit.sync.meta import MetaMixin
from netbox_inventory_toolkit.sync.network import NetworkMixin
from netbox_inventory_toolkit.sync.prune import PruneMixin
from netbox_inventory_toolkit.sync.templates import TemplatesMixin


class Reconciler(
    MetaMixin,
    ImagesMixin,
    TemplatesMixin,
    NetworkMixin,
    DevicesMixin,
    PruneMixin,
    SyncContext,
):
    def run(self) -> None:
        self.ensure_tag(MANAGED_TAG_NAME)
        self.sync_sites()
        self.sync_manufacturers()
        self.sync_platforms()
        self.sync_device_roles()
        self.sync_owner_groups()
        self.sync_owners()
        self.sync_contact_roles()
        self.sync_contacts()
        self.sync_module_bay_types()
        self.sync_device_types()
        self.sync_module_types()
        self.sync_racks()
        self.sync_vlans()
        self.sync_prefixes()
        self.sync_devices()
        self.sync_contact_assignments()
        self.sync_standalone_ips()
        self.sync_primary_ips()
        self.sync_cables()
        self.prune_all()
        self.summary()
