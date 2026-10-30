"""Collector settings file loading, value resolution and source profiles.

The collector accepts an optional ``settings.yml`` next to the inventory.  It
is deliberately separate from the NetBox inventory: credentials and endpoint
configuration never become NetBox objects.  The file supports literal values
and resolver objects such as ``{from_env: UNIFI_PASS}`` anywhere a scalar is
expected.
"""

from __future__ import annotations

import fnmatch
import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from netbox_inventory_toolkit.inventory.util import InventoryError

from .config import (
    DEFAULT_IPMI_PASS,
    DEFAULT_IPMI_USER,
    DEFAULT_REDFISH_PASS,
    DEFAULT_REDFISH_USER,
    DEFAULT_SNMP_COMMUNITY,
    DEFAULT_SNMP_PORT_NAMES,
    DEFAULT_TAG,
    VALID_SNMP_PORT_NAMES,
)
from .process import read_cluster_secret_value

if TYPE_CHECKING:
    from .context import CollectOptions

SETTINGS_FILENAMES = ("settings.yml", "settings.yaml")
SOURCE_TYPES = ("unifi", "snmp", "talos", "redfish", "ipmi")
_COLLECTOR_KEYS = {
    "sources",
    "snmp",
    "kubectl",
    "kubeconfig",
    "snmp_report",
    "log_level",
    "log_file",
}
_DEFAULTS_KEYS = {
    "site",
    "default_tag",
    "rack",
    "talos_platform",
    "roles",
    "compute_roles",
    "unifi_models",
    "unifi_vlan_names",
}
_RESOLVER_KEYS = {"from_env", "from_file", "from_k8s_secret", "default", "kubectl", "kubeconfig"}


class ConfigError(InventoryError):
    """A user-facing collector settings error."""


@dataclass(frozen=True)
class MatchRule:
    """Conditions used to select a credential profile for a target."""

    sites: tuple[str, ...] = ()
    devices: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    cidrs: tuple[Any, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not (self.sites or self.devices or self.tags or self.roles or self.cidrs)

    def matches(
        self,
        *,
        name: str = "",
        site: str = "",
        address: str = "",
        tags: tuple[str, ...] | list[str] = (),
        role: str = "",
    ) -> bool:
        if self.sites and site not in self.sites:
            return False
        if self.devices and not any(
            fnmatch.fnmatchcase(name.lower(), pattern.lower())
            for pattern in self.devices
        ):
            return False
        if self.tags and not set(self.tags).intersection(str(tag) for tag in tags):
            return False
        if self.roles and role not in self.roles:
            return False
        if self.cidrs:
            if not address:
                return False
            try:
                parsed = ipaddress.ip_address(address)
            except ValueError:
                return False
            if not any(parsed in network for network in self.cidrs):
                return False
        return True


@dataclass(frozen=True)
class SnmpProfile:
    name: str
    community: str
    timeout: float = 2.0
    retries: int = 1
    match: MatchRule = field(default_factory=MatchRule)
    default: bool = False
    enabled: bool = True


@dataclass(frozen=True)
class RedfishProfile:
    name: str
    username: str
    password: str
    verify_tls: bool = False
    timeout: float = 30.0
    system_path: str | None = None
    chassis_path: str | None = None
    match: MatchRule = field(default_factory=MatchRule)
    default: bool = False
    enabled: bool = True


@dataclass(frozen=True)
class IpmiProfile:
    name: str
    username: str
    password: str
    binary: str = "ipmitool"
    interface: str = "lanplus"
    privilege: str = "USER"
    cipher: str = ""
    timeout: float = 30.0
    match: MatchRule = field(default_factory=MatchRule)
    default: bool = False
    enabled: bool = True


@dataclass(frozen=True)
class UnifiProfile:
    name: str
    url: str
    site: str
    unifi_site: str = "default"
    username: str = ""
    password: str = ""
    verify_tls: bool = True
    timeout: float = 30.0
    snmp_credential: str | None = None
    models: dict[str, str] = field(default_factory=dict)
    vlan_names: dict[int, str] = field(default_factory=dict)
    secret: str | None = None
    enabled: bool = True


@dataclass(frozen=True)
class TalosProfile:
    name: str
    site: str
    kubectl: str = "kubectl"
    kubeconfig: str | None = None
    nodes_json: str | None = None
    talosctl: str | None = None
    talosconfig: str | None = None
    config_dir: str | None = None
    enabled: bool = True


@dataclass
class Defaults:
    site: str = ""
    default_tag: str = DEFAULT_TAG
    rack: str = ""
    talos_platform: str = ""
    roles: dict[str, str] = field(default_factory=dict)
    compute_roles: list[str] = field(default_factory=list)
    unifi_models: dict[str, str] = field(default_factory=dict)
    unifi_vlan_names: dict[int, str] = field(default_factory=dict)


@dataclass
class CollectorConfig:
    version: int = 1
    settings_path: Path | None = None
    sources_mode: str | None = None
    disabled_sources: set[str] = field(default_factory=set)
    port_names: str = DEFAULT_SNMP_PORT_NAMES
    auto_unifi: bool = True
    kubectl: str = "kubectl"
    kubeconfig: str | None = None
    snmp_report: Path | None = None
    log_level: str = "INFO"
    log_file: Path | None = None
    defaults: Defaults = field(default_factory=Defaults)
    unifi: list[UnifiProfile] = field(default_factory=list)
    snmp: list[SnmpProfile] = field(default_factory=list)
    talos: list[TalosProfile] = field(default_factory=list)
    redfish: list[RedfishProfile] = field(default_factory=list)
    ipmi: list[IpmiProfile] = field(default_factory=list)

    def apply_to_manual(self, manual: dict[str, Any]) -> None:
        """Overlay effective collector defaults on the loaded manual layer."""
        settings = manual.setdefault("settings", {})
        if self.defaults.site:
            settings["site"] = self.defaults.site
        if self.defaults.default_tag:
            settings["default_tag"] = self.defaults.default_tag
        if self.defaults.rack:
            settings["rack"] = self.defaults.rack
        if self.defaults.talos_platform:
            settings["talos_platform"] = self.defaults.talos_platform
        if self.defaults.roles:
            settings["roles"] = dict(self.defaults.roles)
        if self.defaults.compute_roles:
            settings["compute_roles"] = list(self.defaults.compute_roles)
        snmp = settings.setdefault("snmp", {})
        if not isinstance(snmp, dict):
            snmp = {}
            settings["snmp"] = snmp
        snmp["port_names"] = self.port_names
        snmp["auto_unifi"] = self.auto_unifi
        manual.setdefault("unifi_models", {}).update(self.defaults.unifi_models)
        manual.setdefault("unifi_vlan_names", {}).update(
            self.defaults.unifi_vlan_names
        )

    def unifi_profile(self, name: str) -> UnifiProfile | None:
        return next((profile for profile in self.unifi if profile.name == name), None)

    def snmp_profile_for(
        self,
        *,
        device: dict[str, Any] | None = None,
        name: str = "",
        site: str = "",
        address: str = "",
        tags: tuple[str, ...] | list[str] = (),
        role: str = "",
        explicit: str | None = None,
    ) -> SnmpProfile:
        return _select_profile(
            self.snmp,
            explicit=explicit
            or str((device or {}).get("snmp_credential") or "").strip()
            or None,
            name=name or str((device or {}).get("name") or ""),
            site=site or str((device or {}).get("site") or ""),
            address=address,
            tags=tags or tuple(str(tag) for tag in (device or {}).get("tags") or []),
            role=role or str((device or {}).get("role") or ""),
            kind="SNMP",
        )

    def redfish_profile_for(
        self,
        *,
        device: dict[str, Any] | None = None,
        name: str = "",
        site: str = "",
        address: str = "",
        tags: tuple[str, ...] | list[str] = (),
        role: str = "",
        explicit: str | None = None,
    ) -> RedfishProfile:
        return _select_profile(
            self.redfish,
            explicit=explicit
            or str((device or {}).get("redfish_credential") or "").strip()
            or None,
            name=name or str((device or {}).get("name") or ""),
            site=site or str((device or {}).get("site") or ""),
            address=address,
            tags=tags or tuple(str(tag) for tag in (device or {}).get("tags") or []),
            role=role or str((device or {}).get("role") or ""),
            kind="Redfish",
        )

    def ipmi_profile_for(
        self,
        *,
        device: dict[str, Any] | None = None,
        name: str = "",
        site: str = "",
        address: str = "",
        tags: tuple[str, ...] | list[str] = (),
        role: str = "",
        explicit: str | None = None,
    ) -> IpmiProfile:
        return _select_profile(
            self.ipmi,
            explicit=explicit
            or str((device or {}).get("ipmi_credential") or "").strip()
            or None,
            name=name or str((device or {}).get("name") or ""),
            site=site or str((device or {}).get("site") or ""),
            address=address,
            tags=tags or tuple(str(tag) for tag in (device or {}).get("tags") or []),
            role=role or str((device or {}).get("role") or ""),
            kind="IPMI",
        )

    def validate_references(self) -> None:
        snmp_names = {profile.name for profile in self.snmp if profile.enabled}
        for profile in self.unifi:
            if profile.snmp_credential and profile.snmp_credential not in snmp_names:
                raise ConfigError(
                    f"sources.unifi[{profile.name}].snmp_credential refers to unknown "
                    f"SNMP profile {profile.snmp_credential!r}"
                )

    def validate_inventory(self, manual: dict[str, Any]) -> tuple[list[str], list[str]]:
        """Return warnings and errors for inventory/profile mismatches."""
        errors: list[str] = []
        warnings: list[str] = []
        for device in manual.get("devices") or []:
            name = str(device.get("name") or "")
            if device.get("snmp_ip"):
                try:
                    self.snmp_profile_for(
                        device=device, address=str(device["snmp_ip"])
                    )
                except InventoryError as exc:
                    errors.append(f"device {name!r}: {exc}")
            if device.get("redfish_ip"):
                try:
                    self.redfish_profile_for(
                        device=device, address=str(device["redfish_ip"])
                    )
                except InventoryError as exc:
                    errors.append(f"device {name!r}: {exc}")
            if device.get("ipmi_ip"):
                try:
                    self.ipmi_profile_for(
                        device=device, address=str(device["ipmi_ip"])
                    )
                except InventoryError as exc:
                    errors.append(f"device {name!r}: {exc}")
            cluster = str(device.get("talos_cluster") or "").strip()
            if cluster and cluster not in {profile.name for profile in self.talos}:
                errors.append(
                    f"device {name!r}: talos_cluster refers to unknown profile {cluster!r}"
                )
            if (
                len([profile for profile in self.talos if profile.enabled]) > 1
                and device.get("talos_node")
                and not cluster
            ):
                warnings.append(
                    f"device {name!r} has talos_node but no talos_cluster; "
                    "set talos_cluster when node names can repeat across clusters"
                )
        return warnings, errors

    def as_dict(self, *, redact: bool = True) -> dict[str, Any]:
        """Render the effective configuration for ``config show``."""
        secret = "<redacted>" if redact else None

        def clean(value: str) -> str:
            return secret if redact else value

        return {
            "version": self.version,
            "collector": {
                "sources": self.sources_mode or "auto",
                "snmp": {
                    "port_names": self.port_names,
                    "auto_unifi": self.auto_unifi,
                },
                "kubectl": self.kubectl,
                "kubeconfig": self.kubeconfig,
                "snmp_report": str(self.snmp_report) if self.snmp_report else None,
                "log_level": self.log_level,
                "log_file": str(self.log_file) if self.log_file else None,
            },
            "defaults": {
                "site": self.defaults.site,
                "default_tag": self.defaults.default_tag,
                "rack": self.defaults.rack,
                "talos_platform": self.defaults.talos_platform,
                "roles": dict(self.defaults.roles),
                "compute_roles": list(self.defaults.compute_roles),
                "unifi_models": dict(self.defaults.unifi_models),
                "unifi_vlan_names": {
                    str(key): value
                    for key, value in self.defaults.unifi_vlan_names.items()
                },
            },
            "sources": {
                "unifi": [
                    {
                        "name": profile.name,
                        "url": profile.url,
                        "site": profile.site,
                        "unifi_site": profile.unifi_site,
                        "username": profile.username,
                        "password": clean(profile.password),
                        "verify_tls": profile.verify_tls,
                        "timeout": profile.timeout,
                        "snmp_credential": profile.snmp_credential,
                        "models": dict(profile.models),
                        "vlan_names": {
                            str(key): value
                            for key, value in profile.vlan_names.items()
                        },
                        "enabled": profile.enabled,
                    }
                    for profile in self.unifi
                ],
                "snmp": [
                    {
                        "name": profile.name,
                        "community": clean(profile.community),
                        "timeout": profile.timeout,
                        "retries": profile.retries,
                        "match": _match_dict(profile.match),
                        "default": profile.default,
                        "enabled": profile.enabled,
                    }
                    for profile in self.snmp
                ],
                "redfish": [
                    {
                        "name": profile.name,
                        "username": profile.username,
                        "password": clean(profile.password),
                        "verify_tls": profile.verify_tls,
                        "timeout": profile.timeout,
                        "system_path": profile.system_path,
                        "chassis_path": profile.chassis_path,
                        "match": _match_dict(profile.match),
                        "default": profile.default,
                        "enabled": profile.enabled,
                    }
                    for profile in self.redfish
                ],
                "ipmi": [
                    {
                        "name": profile.name,
                        "username": profile.username,
                        "password": clean(profile.password),
                        "binary": profile.binary,
                        "interface": profile.interface,
                        "privilege": profile.privilege,
                        "cipher": profile.cipher,
                        "timeout": profile.timeout,
                        "match": _match_dict(profile.match),
                        "default": profile.default,
                        "enabled": profile.enabled,
                    }
                    for profile in self.ipmi
                ],
                "talos": [
                    {
                        "name": profile.name,
                        "site": profile.site,
                        "kubectl": profile.kubectl,
                        "kubeconfig": profile.kubeconfig,
                        "nodes_json": profile.nodes_json,
                        "talosctl": profile.talosctl,
                        "talosconfig": profile.talosconfig,
                        "config_dir": profile.config_dir,
                        "enabled": profile.enabled,
                    }
                    for profile in self.talos
                ],
            },
        }


def _match_dict(match: MatchRule) -> dict[str, Any]:
    return {
        "sites": list(match.sites),
        "devices": list(match.devices),
        "tags": list(match.tags),
        "roles": list(match.roles),
        "cidrs": [str(cidr) for cidr in match.cidrs],
    }


def _select_profile(
    profiles: list[Any],
    *,
    explicit: str | None,
    name: str,
    site: str,
    address: str,
    tags: tuple[str, ...] | list[str],
    role: str,
    kind: str,
) -> Any:
    enabled = [profile for profile in profiles if profile.enabled]
    if not enabled:
        raise ConfigError(f"no enabled {kind} credential profiles are configured")
    if explicit:
        profile = next(
            (candidate for candidate in enabled if candidate.name == explicit), None
        )
        if profile is None:
            raise ConfigError(
                f"{kind} credential profile {explicit!r} is not configured or is disabled"
            )
        return profile
    for profile in enabled:
        if (
            not profile.match.is_empty
            and profile.match.matches(
                name=name,
                site=site,
                address=address,
                tags=tags,
                role=role,
            )
        ):
            return profile
    defaults = [profile for profile in enabled if profile.default]
    if defaults:
        return defaults[0]
    if len(enabled) == 1:
        return enabled[0]
    raise ConfigError(
        f"no {kind} credential profile matches device {name or address!r} "
        f"(site={site or '-'}, address={address or '-'}); "
        f"set {kind.lower()}_credential on the device, add a match rule, "
        "or mark one profile as default"
    )


def find_settings_file(
    inventory: Path,
    explicit: str | Path | None = None,
) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise ConfigError(f"collector settings file {path} does not exist")
        return path.resolve()
    candidates = [Path(inventory) / name for name in SETTINGS_FILENAMES]
    existing = [path for path in candidates if path.is_file()]
    if len(existing) > 1:
        raise ConfigError(
            f"both {existing[0]} and {existing[1]} exist; keep only one settings file"
        )
    return existing[0].resolve() if existing else None


def load_config(
    options: CollectOptions,
    manual: dict[str, Any] | None = None,
) -> CollectorConfig:
    """Load settings.yml or synthesize the legacy flag/environment config."""
    manual = manual or {}
    explicit = getattr(options, "settings_path", None)
    path = find_settings_file(options.inventory, explicit)
    if path is None:
        return legacy_config(options, manual)
    return load_settings_file(path, manual)


def legacy_config(
    options: CollectOptions,
    manual: dict[str, Any] | None = None,
) -> CollectorConfig:
    """Build an equivalent config from the deprecated flags and environment."""
    manual = manual or {}
    settings = _manual_settings(manual)
    defaults = _defaults_from_manual(manual)
    port_names = str(
        (settings.get("snmp") or {}).get("port_names")
        or DEFAULT_SNMP_PORT_NAMES
    )
    if port_names not in VALID_SNMP_PORT_NAMES:
        port_names = DEFAULT_SNMP_PORT_NAMES
    auto_unifi = bool((settings.get("snmp") or {}).get("auto_unifi", True))
    sources_mode = options.sources_mode or os.environ.get("COLLECTOR_SOURCES") or "auto"

    unifi: list[UnifiProfile] = []
    if options.unifi_url and (
        options.unifi_secret or (options.unifi_user and options.unifi_pass)
    ):
        unifi.append(
            UnifiProfile(
                name="default",
                url=options.unifi_url,
                site=defaults.site,
                username=options.unifi_user or "",
                password=options.unifi_pass or "",
                secret=options.unifi_secret,
                snmp_credential="default",
            )
        )

    talos: list[TalosProfile] = [
        TalosProfile(
            name="default",
            site=defaults.site,
            kubectl=options.kubectl or os.environ.get("KUBECTL", "kubectl"),
            kubeconfig=options.kubeconfig,
            nodes_json=options.nodes_json,
            talosctl=options.talosctl,
            talosconfig=options.talosconfig,
            config_dir=str(options.talos_config_dir)
            if options.talos_config_dir
            else None,
        )
    ]

    return CollectorConfig(
        version=1,
        settings_path=None,
        sources_mode=sources_mode,
        disabled_sources=set(options.disabled_sources),
        port_names=port_names,
        auto_unifi=auto_unifi,
        kubectl=options.kubectl or os.environ.get("KUBECTL", "kubectl"),
        kubeconfig=options.kubeconfig,
        snmp_report=options.snmp_report
        or (Path(os.environ["SNMP_REPORT"]) if os.environ.get("SNMP_REPORT") else None),
        log_level=options.log_level or "INFO",
        log_file=options.log_file,
        defaults=defaults,
        unifi=unifi,
        snmp=[
            SnmpProfile(
                name="default",
                community=options.snmp_community or DEFAULT_SNMP_COMMUNITY,
                timeout=options.snmp_timeout,
                retries=options.snmp_retries,
                default=True,
            )
        ],
        talos=talos,
        redfish=[
            RedfishProfile(
                name="default",
                username=options.redfish_user or DEFAULT_REDFISH_USER,
                password=options.redfish_pass or DEFAULT_REDFISH_PASS,
                verify_tls=options.redfish_verify,
                default=True,
            )
        ],
        ipmi=[
            IpmiProfile(
                name="default",
                username=options.ipmi_user or DEFAULT_IPMI_USER,
                password=options.ipmi_pass or DEFAULT_IPMI_PASS,
                binary=os.environ.get("IPMI_TOOL", "ipmitool"),
                interface=os.environ.get("IPMI_INTERFACE", "lanplus"),
                privilege=os.environ.get("IPMI_PRIVILEGE", "USER"),
                cipher=os.environ.get("IPMI_CIPHER", ""),
                timeout=float(os.environ.get("IPMI_TIMEOUT", "30")),
                default=True,
            )
        ],
    )


def load_settings_file(path: Path, manual: dict[str, Any] | None = None) -> CollectorConfig:
    manual = manual or {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read collector settings {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"cannot parse collector settings {path}: {exc}") from exc
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: the settings file must contain a YAML mapping")

    _check_keys(raw, {"version", "collector", "defaults", "sources"}, str(path))
    version = _as_int(raw.get("version"), "version", default=1)
    if version != 1:
        raise ConfigError(f"unsupported settings version {version!r}; expected 1")

    collector_raw = _as_mapping(raw.get("collector"), "collector", default={})
    _check_keys(collector_raw, _COLLECTOR_KEYS, "collector")
    base = path.parent
    # Resolve kubectl/kubeconfig first so from_k8s_secret can use them.
    pre_kubeconfig = _optional_str(
        _resolve_scalar(
            collector_raw.get("kubeconfig"), base, "collector.kubeconfig"
        )
    )
    if pre_kubeconfig:
        pre_kubeconfig = str(_resolve_path(pre_kubeconfig, base))
    pre_kubectl = _optional_str(
        _resolve_scalar(collector_raw.get("kubectl"), base, "collector.kubectl")
    )
    if pre_kubectl and ("/" in pre_kubectl or "\\" in pre_kubectl):
        pre_kubectl = str(_resolve_path(pre_kubectl, base))
    pre_resolver = _ValueResolver(
        base,
        kubectl=pre_kubectl or os.environ.get("KUBECTL", "kubectl"),
        kubeconfig=pre_kubeconfig,
    )
    resolved = _resolve_tree(raw, pre_resolver, "settings")
    collector_raw = _as_mapping(resolved.get("collector"), "collector", default={})
    defaults_raw = _as_mapping(resolved.get("defaults"), "defaults", default={})
    sources_raw = _as_mapping(resolved.get("sources"), "sources", default={})
    _check_keys(sources_raw, set(SOURCE_TYPES), "sources")

    defaults = _parse_defaults(defaults_raw, manual)
    collector_snmp = _as_mapping(collector_raw.get("snmp"), "collector.snmp", default={})
    _check_keys(
        collector_snmp,
        {"port_names", "auto_unifi"},
        "collector.snmp",
    )
    manual_settings = _manual_settings(manual)
    manual_snmp = (
        manual_settings.get("snmp")
        if isinstance(manual_settings.get("snmp"), dict)
        else {}
    )
    port_names = str(
        collector_snmp.get("port_names")
        or manual_snmp.get("port_names")
        or DEFAULT_SNMP_PORT_NAMES
    )
    if port_names not in VALID_SNMP_PORT_NAMES:
        raise ConfigError(
            f"collector.snmp.port_names must be one of "
            f"{sorted(VALID_SNMP_PORT_NAMES)}, got {port_names!r}"
        )
    auto_unifi = _as_bool(
        collector_snmp.get("auto_unifi"),
        "collector.snmp.auto_unifi",
        default=bool(manual_snmp.get("auto_unifi", True)),
    )
    sources_mode = _optional_str(collector_raw.get("sources")) or "auto"
    _validate_sources_mode(sources_mode)
    log_level = str(
        collector_raw.get("log_level")
        or os.environ.get("COLLECTOR_LOG_LEVEL")
        or "INFO"
    ).upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ConfigError(
            f"collector.log_level must be one of DEBUG, INFO, WARNING, ERROR, "
            f"CRITICAL, got {log_level!r}"
        )

    kubectl = (
        _optional_tool(collector_raw.get("kubectl"), base, "collector.kubectl")
        or os.environ.get("KUBECTL", "kubectl")
    )
    kubeconfig = _optional_path(
        collector_raw.get("kubeconfig"), base, "collector.kubeconfig"
    )

    unifi = _parse_unifi(
        _as_list(sources_raw.get("unifi"), "sources.unifi"),
        defaults,
        base,
    )
    snmp = _parse_snmp(_as_list(sources_raw.get("snmp"), "sources.snmp"))
    redfish = _parse_redfish(
        _as_list(sources_raw.get("redfish"), "sources.redfish")
    )
    ipmi = _parse_ipmi(_as_list(sources_raw.get("ipmi"), "sources.ipmi"))
    talos = _parse_talos(
        _as_list(sources_raw.get("talos"), "sources.talos"),
        defaults,
        base,
        default_kubectl=kubectl,
        default_kubeconfig=kubeconfig,
    )
    config = CollectorConfig(
        version=version,
        settings_path=path,
        sources_mode=sources_mode,
        port_names=port_names,
        auto_unifi=auto_unifi,
        kubectl=kubectl,
        kubeconfig=kubeconfig,
        snmp_report=_path_or_none(collector_raw.get("snmp_report"), base),
        log_level=log_level,
        log_file=_path_or_none(
            collector_raw.get("log_file") or os.environ.get("COLLECTOR_LOG_FILE"),
            base,
        ),
        defaults=defaults,
        unifi=unifi,
        snmp=snmp,
        talos=talos,
        redfish=redfish,
        ipmi=ipmi,
    )
    config.validate_references()
    return config


@dataclass
class _ValueResolver:
    base: Path
    kubectl: str
    kubeconfig: str | None

    def resolve(self, value: dict[str, Any], path: str) -> Any:
        sources = [
            key for key in ("from_env", "from_file", "from_k8s_secret") if key in value
        ]
        if len(sources) > 1:
            raise ConfigError(f"{path}: use only one of {', '.join(sources)}")
        source = sources[0]
        default = value.get("default")
        try:
            if source == "from_env":
                name = _as_str(value["from_env"], f"{path}.from_env")
                resolved = os.environ.get(name)
                if resolved is None:
                    if "default" in value:
                        return _resolve_tree(default, self, f"{path}.default")
                    raise ConfigError(
                        f"{path}: environment variable {name!r} is not set"
                    )
                return resolved
            if source == "from_file":
                raw_path = _as_str(value["from_file"], f"{path}.from_file")
                file_path = _resolve_path(raw_path, self.base)
                try:
                    return file_path.read_text(encoding="utf-8").rstrip("\r\n")
                except OSError as exc:
                    if "default" in value:
                        return _resolve_tree(default, self, f"{path}.default")
                    raise ConfigError(f"{path}: cannot read {file_path}") from exc
            if source == "from_k8s_secret":
                reference = _as_str(
                    value["from_k8s_secret"], f"{path}.from_k8s_secret"
                )
                namespace, name, key = _parse_k8s_secret_reference(reference, path)
                kubectl = str(
                    _resolve_tree(value.get("kubectl"), self, f"{path}.kubectl")
                    or self.kubectl
                )
                kubeconfig = _optional_str(
                    _resolve_tree(
                        value.get("kubeconfig"), self, f"{path}.kubeconfig"
                    )
                    or self.kubeconfig
                )
                try:
                    return read_cluster_secret_value(
                        kubectl, kubeconfig, namespace, name, key
                    )
                except InventoryError:
                    if "default" in value:
                        return _resolve_tree(default, self, f"{path}.default")
                    raise
        except ConfigError:
            raise
        except Exception as exc:  # noqa: BLE001 - wrap resolver failures with a path
            if "default" in value:
                return _resolve_tree(default, self, f"{path}.default")
            raise ConfigError(f"{path}: {exc}") from exc
        raise ConfigError(f"{path}: invalid resolver object")


def _resolve_tree(value: Any, resolver: _ValueResolver, path: str) -> Any:
    if isinstance(value, dict):
        if any(key in value for key in ("from_env", "from_file", "from_k8s_secret")):
            _check_keys(value, _RESOLVER_KEYS, path)
            return resolver.resolve(value, path)
        return {
            str(key): _resolve_tree(item, resolver, f"{path}.{key}")
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _resolve_tree(item, resolver, f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    return value


def _resolve_scalar(value: Any, base: Path, path: str) -> Any:
    if value is None:
        return None
    resolver = _ValueResolver(
        base,
        kubectl=os.environ.get("KUBECTL", "kubectl"),
        kubeconfig=os.environ.get("KUBECONFIG"),
    )
    result = _resolve_tree(value, resolver, path)
    return result


def _parse_k8s_secret_reference(value: str, path: str) -> tuple[str, str, str]:
    if "#" not in value:
        raise ConfigError(
            f"{path}.from_k8s_secret must use NAMESPACE/NAME#KEY, got {value!r}"
        )
    reference, key = value.rsplit("#", 1)
    namespace, separator, name = reference.partition("/")
    if not separator or not namespace or not name or not key:
        raise ConfigError(
            f"{path}.from_k8s_secret must use NAMESPACE/NAME#KEY, got {value!r}"
        )
    return namespace, name, key


def _parse_defaults(raw: dict[str, Any], manual: dict[str, Any]) -> Defaults:
    base = _defaults_from_manual(manual)
    settings = _manual_settings(manual)
    site = _optional_str(raw.get("site")) or base.site
    default_tag = (
        _optional_str(raw.get("default_tag"))
        or base.default_tag
        or DEFAULT_TAG
    )
    rack = _optional_str(raw.get("rack")) or base.rack
    talos_platform = _optional_str(raw.get("talos_platform")) or base.talos_platform
    roles = _string_map(raw.get("roles"), "defaults.roles") if "roles" in raw else base.roles
    if "compute_roles" in raw:
        compute_roles = _string_list(raw.get("compute_roles"), "defaults.compute_roles")
    else:
        compute_roles = base.compute_roles
    unifi_models = dict(base.unifi_models)
    if "unifi_models" in raw:
        unifi_models.update(_string_map(raw.get("unifi_models"), "defaults.unifi_models"))
    unifi_vlan_names = dict(base.unifi_vlan_names)
    if "unifi_vlan_names" in raw:
        unifi_vlan_names.update(
            _vlan_name_map(raw.get("unifi_vlan_names"), "defaults.unifi_vlan_names")
        )
    return Defaults(
        site=site or str(settings.get("site") or ""),
        default_tag=default_tag,
        rack=rack,
        talos_platform=talos_platform,
        roles=roles,
        compute_roles=compute_roles,
        unifi_models=unifi_models,
        unifi_vlan_names=unifi_vlan_names,
    )


def _defaults_from_manual(manual: dict[str, Any]) -> Defaults:
    settings = _manual_settings(manual)
    roles = settings.get("roles") if isinstance(settings.get("roles"), dict) else {}
    compute_roles = (
        settings.get("compute_roles")
        if isinstance(settings.get("compute_roles"), list)
        else []
    )
    return Defaults(
        site=str(settings.get("site") or ""),
        default_tag=str(settings.get("default_tag") or DEFAULT_TAG),
        rack=str(settings.get("rack") or ""),
        talos_platform=str(settings.get("talos_platform") or ""),
        roles={str(key): str(value) for key, value in roles.items()},
        compute_roles=[str(item) for item in compute_roles],
        unifi_models={
            str(key): str(value)
            for key, value in (manual.get("unifi_models") or {}).items()
        },
        unifi_vlan_names=_vlan_name_map(
            manual.get("unifi_vlan_names") or {}, "manual.unifi_vlan_names"
        ),
    )


def _parse_unifi(
    items: list[Any],
    defaults: Defaults,
    base: Path,
) -> list[UnifiProfile]:
    result: list[UnifiProfile] = []
    for index, item in enumerate(items):
        path = f"sources.unifi[{index}]"
        raw = _as_mapping(item, path)
        _check_keys(
            raw,
            {
                "name",
                "url",
                "site",
                "unifi_site",
                "username",
                "password",
                "verify_tls",
                "timeout",
                "snmp_credential",
                "models",
                "vlan_names",
                "enabled",
            },
            path,
        )
        name = _required_str(raw.get("name"), f"{path}.name")
        result.append(
            UnifiProfile(
                name=name,
                url=_required_str(raw.get("url"), f"{path}.url").rstrip("/"),
                site=_optional_str(raw.get("site")) or defaults.site,
                unifi_site=_optional_str(raw.get("unifi_site")) or "default",
                username=_optional_str(raw.get("username")) or "",
                password=_optional_str(raw.get("password")) or "",
                verify_tls=_as_bool(raw.get("verify_tls"), f"{path}.verify_tls", default=True),
                timeout=_as_float(raw.get("timeout"), f"{path}.timeout", default=30.0),
                snmp_credential=_optional_str(raw.get("snmp_credential")),
                models=_string_map(raw.get("models"), f"{path}.models"),
                vlan_names=_vlan_name_map(
                    raw.get("vlan_names"), f"{path}.vlan_names"
                ),
                enabled=_as_bool(raw.get("enabled"), f"{path}.enabled", default=True),
            )
        )
    _validate_names(result, "sources.unifi")
    for profile in result:
        if not profile.enabled:
            continue
        if not profile.site:
            raise ConfigError(
                f"sources.unifi[{profile.name}].site is required when defaults.site is not set"
            )
        if not profile.username or not profile.password:
            raise ConfigError(
                f"sources.unifi[{profile.name}] requires username and password "
                "(or from_env/from_file/from_k8s_secret values)"
            )
    return result


def _parse_snmp(items: list[Any]) -> list[SnmpProfile]:
    result: list[SnmpProfile] = []
    for index, item in enumerate(items):
        path = f"sources.snmp[{index}]"
        raw = _as_mapping(item, path)
        _check_keys(
            raw,
            {"name", "community", "timeout", "retries", "match", "default", "enabled"},
            path,
        )
        result.append(
            SnmpProfile(
                name=_required_str(raw.get("name"), f"{path}.name"),
                community=_required_str(
                    raw.get("community"), f"{path}.community"
                ),
                timeout=_as_float(raw.get("timeout"), f"{path}.timeout", default=2.0),
                retries=_as_int(raw.get("retries"), f"{path}.retries", default=1),
                match=_parse_match(raw.get("match"), f"{path}.match"),
                default=_as_bool(raw.get("default"), f"{path}.default", default=False),
                enabled=_as_bool(raw.get("enabled"), f"{path}.enabled", default=True),
            )
        )
    _validate_names(result, "sources.snmp")
    _validate_single_default(result, "sources.snmp")
    return result


def _parse_redfish(items: list[Any]) -> list[RedfishProfile]:
    result: list[RedfishProfile] = []
    for index, item in enumerate(items):
        path = f"sources.redfish[{index}]"
        raw = _as_mapping(item, path)
        _check_keys(
            raw,
            {
                "name",
                "username",
                "password",
                "verify_tls",
                "timeout",
                "system_path",
                "chassis_path",
                "match",
                "default",
                "enabled",
            },
            path,
        )
        result.append(
            RedfishProfile(
                name=_required_str(raw.get("name"), f"{path}.name"),
                username=_required_str(raw.get("username"), f"{path}.username"),
                password=_required_str(raw.get("password"), f"{path}.password"),
                verify_tls=_as_bool(
                    raw.get("verify_tls"), f"{path}.verify_tls", default=False
                ),
                timeout=_as_float(raw.get("timeout"), f"{path}.timeout", default=30.0),
                system_path=_optional_str(raw.get("system_path")),
                chassis_path=_optional_str(raw.get("chassis_path")),
                match=_parse_match(raw.get("match"), f"{path}.match"),
                default=_as_bool(raw.get("default"), f"{path}.default", default=False),
                enabled=_as_bool(raw.get("enabled"), f"{path}.enabled", default=True),
            )
        )
    _validate_names(result, "sources.redfish")
    _validate_single_default(result, "sources.redfish")
    return result


def _parse_ipmi(items: list[Any]) -> list[IpmiProfile]:
    result: list[IpmiProfile] = []
    for index, item in enumerate(items):
        path = f"sources.ipmi[{index}]"
        raw = _as_mapping(item, path)
        _check_keys(
            raw,
            {
                "name",
                "username",
                "password",
                "binary",
                "interface",
                "privilege",
                "cipher",
                "timeout",
                "match",
                "default",
                "enabled",
            },
            path,
        )
        result.append(
            IpmiProfile(
                name=_required_str(raw.get("name"), f"{path}.name"),
                username=_required_str(raw.get("username"), f"{path}.username"),
                password=_required_str(raw.get("password"), f"{path}.password"),
                binary=_optional_str(raw.get("binary")) or "ipmitool",
                interface=_optional_str(raw.get("interface")) or "lanplus",
                privilege=_optional_str(raw.get("privilege")) or "USER",
                cipher=_optional_str(raw.get("cipher")) or "",
                timeout=_as_float(raw.get("timeout"), f"{path}.timeout", default=30.0),
                match=_parse_match(raw.get("match"), f"{path}.match"),
                default=_as_bool(raw.get("default"), f"{path}.default", default=False),
                enabled=_as_bool(raw.get("enabled"), f"{path}.enabled", default=True),
            )
        )
    _validate_names(result, "sources.ipmi")
    _validate_single_default(result, "sources.ipmi")
    return result


def _parse_talos(
    items: list[Any],
    defaults: Defaults,
    base: Path,
    *,
    default_kubectl: str,
    default_kubeconfig: str | None,
) -> list[TalosProfile]:
    result: list[TalosProfile] = []
    for index, item in enumerate(items):
        path = f"sources.talos[{index}]"
        raw = _as_mapping(item, path)
        _check_keys(
            raw,
            {
                "name",
                "site",
                "kubectl",
                "kubeconfig",
                "nodes_json",
                "talosctl",
                "talosconfig",
                "config_dir",
                "enabled",
            },
            path,
        )
        result.append(
            TalosProfile(
                name=_required_str(raw.get("name"), f"{path}.name"),
                site=_optional_str(raw.get("site")) or defaults.site,
                kubectl=_optional_str(raw.get("kubectl")) or default_kubectl,
                kubeconfig=_optional_path(raw.get("kubeconfig"), base, f"{path}.kubeconfig")
                or default_kubeconfig,
                nodes_json=_optional_path(raw.get("nodes_json"), base, f"{path}.nodes_json"),
                talosctl=_optional_tool(raw.get("talosctl"), base, f"{path}.talosctl"),
                talosconfig=_optional_path(
                    raw.get("talosconfig"), base, f"{path}.talosconfig"
                ),
                config_dir=_optional_path(
                    raw.get("config_dir"), base, f"{path}.config_dir"
                ),
                enabled=_as_bool(raw.get("enabled"), f"{path}.enabled", default=True),
            )
        )
    _validate_names(result, "sources.talos")
    for profile in result:
        if profile.enabled and not profile.site:
            raise ConfigError(
                f"sources.talos[{profile.name}].site is required when defaults.site is not set"
            )
    return result


def _parse_match(value: Any, path: str) -> MatchRule:
    if value is None:
        return MatchRule()
    raw = _as_mapping(value, path)
    _check_keys(raw, {"sites", "devices", "tags", "roles", "cidrs"}, path)
    cidrs: list[Any] = []
    for index, item in enumerate(_as_list(raw.get("cidrs"), f"{path}.cidrs")):
        try:
            cidrs.append(ipaddress.ip_network(str(item), strict=False))
        except ValueError as exc:
            raise ConfigError(f"{path}.cidrs[{index}]: {exc}") from exc
    return MatchRule(
        sites=tuple(_string_list(raw.get("sites"), f"{path}.sites")),
        devices=tuple(_string_list(raw.get("devices"), f"{path}.devices")),
        tags=tuple(_string_list(raw.get("tags"), f"{path}.tags")),
        roles=tuple(_string_list(raw.get("roles"), f"{path}.roles")),
        cidrs=tuple(cidrs),
    )


def _validate_names(profiles: list[Any], path: str) -> None:
    seen: set[str] = set()
    for profile in profiles:
        if profile.name in seen:
            raise ConfigError(f"{path}: duplicate profile name {profile.name!r}")
        seen.add(profile.name)


def _validate_single_default(profiles: list[Any], path: str) -> None:
    enabled = [profile for profile in profiles if profile.enabled]
    defaults = [profile.name for profile in enabled if profile.default]
    if len(defaults) > 1:
        raise ConfigError(
            f"{path}: only one profile may set default: true, got {defaults}"
        )
    if len(enabled) > 1:
        for profile in enabled:
            if profile.match.is_empty and not profile.default:
                raise ConfigError(
                    f"{path}[{profile.name}] needs a match block or default: true "
                    "when multiple profiles are enabled"
                )


def _validate_sources_mode(value: str) -> None:
    if value in {"auto", "all", "none"}:
        return
    names = {item.strip() for item in value.split(",") if item.strip()}
    unknown = names - set(SOURCE_TYPES)
    if unknown:
        raise ConfigError(
            f"collector.sources contains unknown source(s): {', '.join(sorted(unknown))}"
        )
    if not names:
        raise ConfigError("collector.sources must be auto, all, none or a source list")


def _manual_settings(manual: dict[str, Any]) -> dict[str, Any]:
    value = manual.get("settings")
    return value if isinstance(value, dict) else {}


def _resolve_path(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path


def _optional_path(value: Any, base: Path, path: str) -> str | None:
    text = _optional_str(value)
    if not text:
        return None
    return str(_resolve_path(text, base))


def _optional_tool(value: Any, base: Path, path: str) -> str | None:
    text = _optional_str(value)
    if not text:
        return None
    if "/" in text or "\\" in text or text.startswith("."):
        return str(_resolve_path(text, base))
    return text


def _path_or_none(value: Any, base: Path) -> Path | None:
    text = _optional_str(value)
    return _resolve_path(text, base) if text else None


def _check_keys(value: dict[str, Any], allowed: set[str], path: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ConfigError(
            f"{path}: unknown key(s): {', '.join(sorted(str(key) for key in unknown))}"
        )


def _as_mapping(value: Any, path: str, *, default: Any = None) -> dict[str, Any]:
    if value is None and default is not None:
        return dict(default)
    if not isinstance(value, dict):
        raise ConfigError(f"{path} must be a mapping")
    return value


def _as_list(value: Any, path: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError(f"{path} must be a list")
    return value


def _as_str(value: Any, path: str) -> str:
    if isinstance(value, (dict, list)):
        raise ConfigError(f"{path} must be a scalar")
    return str(value)


def _required_str(value: Any, path: str) -> str:
    text = _optional_str(value)
    if not text:
        raise ConfigError(f"{path} is required")
    return text


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return None
    text = str(value).strip()
    return text or None


def _as_bool(value: Any, path: str, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{path} must be a boolean, got {value!r}")


def _as_int(value: Any, path: str, *, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{path} must be an integer, got {value!r}") from exc


def _as_float(value: Any, path: str, *, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{path} must be a number, got {value!r}") from exc


def _string_map(value: Any, path: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{path} must be a mapping")
    return {str(key): str(item) for key, item in value.items()}


def _vlan_name_map(value: Any, path: str) -> dict[int, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{path} must be a mapping")
    result: dict[int, str] = {}
    for key, item in value.items():
        try:
            result[int(key)] = str(item)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{path}: invalid VLAN ID {key!r}") from exc
    return result


def _string_list(value: Any, path: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError(f"{path} must be a list")
    return [str(item) for item in value]
