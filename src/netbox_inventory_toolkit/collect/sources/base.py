"""Optional inventory source contract and shared result types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from ..context import CollectContext


@dataclass
class UnifiData:
    devices: list[dict[str, Any]] = field(default_factory=list)
    vlans: list[dict[str, Any]] = field(default_factory=list)
    prefixes: list[dict[str, Any]] = field(default_factory=list)
    cables: list[dict[str, Any]] = field(default_factory=list)
    names: dict[str, str] = field(default_factory=dict)
    clients: list[dict[str, Any]] = field(default_factory=list)
    raw_devices: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SnmpData:
    observations: list[dict[str, Any]] = field(default_factory=list)
    matches: dict[str, str] = field(default_factory=dict)


@dataclass
class TalosData:
    nodes: list[dict[str, Any]] = field(default_factory=list)
    manual_by_node: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class SourceState:
    unifi: UnifiData | None = None
    snmp: SnmpData | None = None
    talos: TalosData | None = None
    redfish: dict[str, dict[str, Any]] = field(default_factory=dict)
    ipmi: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def bmc_by_name(self) -> dict[str, dict[str, Any]]:
        return {**self.ipmi, **self.redfish}


class Source(ABC):
    name: ClassVar[str]

    @abstractmethod
    def enabled(self, ctx: CollectContext, state: SourceState) -> bool:
        """Return True when this source has enough configuration to run."""

    @abstractmethod
    def collect(self, ctx: CollectContext, state: SourceState) -> None:
        """Collect into *state*. Failures should raise a clear exception."""
