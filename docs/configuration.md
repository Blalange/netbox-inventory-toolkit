# Collector configuration

`collect` and `discover` share an optional `settings.yml` in the inventory root.
It is collector configuration, not NetBox inventory: sync, `check` and the
language server ignore it. The file supports multiple source profiles and
resolves secrets from the environment, files or Kubernetes Secrets.

A legacy CLI flag/environment configuration is still accepted when no settings
file exists, but it is deprecated and logs a warning.

## Example

```yaml
version: 1

collector:
  sources: auto
  snmp:
    port_names: auto
    auto_unifi: true
  kubectl: kubectl
  kubeconfig: {from_env: KUBECONFIG}

defaults:
  site: glemmen
  default_tag: production
  rack: Glemmen rack
  talos_platform: talos-linux
  roles:
    control_plane: control-plane
    worker: worker
  compute_roles: [server]
  unifi_models:
    UDMPRO: ubiquiti-udm-pro
  unifi_vlan_names:
    4: Server tilbehør

sources:
  unifi:
    - name: glemmen
      url: https://unifi.glem.men
      site: glemmen
      unifi_site: default
      username: {from_env: UNIFI_USER}
      password: {from_env: UNIFI_PASS}
      verify_tls: true
      snmp_credential: glemmen

  snmp:
    - name: glemmen
      community: {from_env: SNMP_COMMUNITY, default: public}
      timeout: 2
      retries: 1
      match:
        sites: [glemmen]
      default: true

    - name: legacy
      community: {from_env: SNMP_LEGACY_COMMUNITY}
      match:
        devices: ["gs1920-*"]
        cidrs: [192.168.4.0/24]

  redfish:
    - name: idrac9
      username: {from_env: REDFISH_USER, default: readonly}
      password: {from_env: REDFISH_PASS, default: readonly}
      verify_tls: false
      match:
        sites: [glemmen]
      default: true

    - name: idrac6
      username: {from_env: OLD_REDFISH_USER}
      password: {from_env: OLD_REDFISH_PASS}
      match:
        devices: [glemmen50, glemmen60]

  ipmi:
    - name: imm
      username: {from_env: IPMI_USER, default: root}
      password: {from_env: IPMI_PASS, default: calvin}
      match:
        devices: [glemmen50]
      default: true

    - name: idrac6
      username: root
      password: {from_env: OLD_IPMI_PASS}
      match:
        devices: [glemmen60]

  talos:
    - name: glemmen
      site: glemmen
      kubeconfig: talos/generated/kubeconfig
      talosconfig: talos/generated/talosconfig
      talosctl: ./scripts/bin/talosctl
      config_dir: talos/nodes

    - name: bygg
      site: bygg
      kubeconfig: clusters/bygg/kubeconfig
      talosconfig: clusters/bygg/talosconfig
      talosctl: talosctl
      config_dir: clusters/bygg/nodes
```

## Secret and value resolution

Any scalar value may be a resolver object:

```yaml
password: hunter2
password: {from_env: UNIFI_PASS}
password: {from_env: UNIFI_PASS, default: hunter2}
password: {from_file: /run/secrets/unifi_pass}
password: {from_k8s_secret: monitoring/unpoller-unifi#password}
```

- `from_env` fails when the variable is unset unless `default` is present.
- `from_file` paths are relative to `settings.yml`.
- `from_k8s_secret` uses `collector.kubectl` and `collector.kubeconfig`. Use
  `NAMESPACE/NAME#KEY`; it can be overridden with `kubectl` and `kubeconfig`
  keys inside the resolver object.
- File and `from_file` paths are relative to the directory containing
  `settings.yml`; tool paths containing `/` or `\` are resolved the same way.
- Resolved values are converted to the expected boolean, number, path or string.
- Raw values are allowed, but `config show` and logs redact passwords and SNMP
  communities. Prefer environment or Secret references in committed files.

YAML anchors work for shared values.

## Credential selection

For SNMP, Redfish and IPMI:

1. The manual device field `snmp_credential`, `redfish_credential` or
   `ipmi_credential` selects a profile by name.
2. Otherwise the first profile whose `match` matches is used.
3. Otherwise the profile with `default: true` is used.
4. If there is only one enabled profile, it is used.
5. Otherwise the run fails for that device with a clear message.

`match` supports:

```yaml
match:
  sites: [glemmen]
  devices: ["glemmen*", "old-*"]
  tags: [legacy]
  roles: [server]
  cidrs: [192.168.4.0/24]
```

All present conditions must match; values inside one list are alternatives.
When more than one profile is enabled, each profile needs a `match` block or
must be the single `default`; this avoids silently selecting an arbitrary
credential.

## Multiple UniFi controllers and sites

Each item under `sources.unifi` is one controller and one UniFi site
(`unifi_site`, default `default`). `site` is the NetBox site for generated
devices, VLANs and prefixes.

- Use separate profiles for separate controllers.
- Point multiple profiles at the same controller URL with different
  `unifi_site` values to collect multiple UniFi sites.
- `defaults.unifi_models` and `defaults.unifi_vlan_names` can be overridden per
  profile with `models` and `vlan_names`.
- Generated device names must be unique across controllers.

## Multiple Talos/Kubernetes clusters

Each item under `sources.talos` is one cluster with its own `kubeconfig`,
`talosconfig`, `kubectl`, `talosctl`, `nodes_json` and `config_dir`.

Manual devices may set `talos_cluster` to select the cluster. This is required
when the same node name exists in more than one cluster. Without it, the
existing single-cluster name matching applies.

Manual device `site` wins. Otherwise generated nodes use the cluster `site`,
then `defaults.site`.

## Site scoping

Generated network devices, servers, VLANs and node entries are site-scoped.
SNMP and UniFi clients are matched by site first, then by MAC. VLANs are keyed
by `(site, vid)`, so the same VLAN ID can exist in multiple sites.

NetBox prefixes are global. If two source profiles generate the same prefix for
different sites, the collector fails instead of silently picking one.

## Commands

```sh
# validate settings and inventory/profile coverage
netbox-inventory-toolkit config check --inventory inventory

# show the effective config with secrets redacted
netbox-inventory-toolkit config show --inventory inventory

# collect; settings.yml is found automatically
netbox-inventory-toolkit collect --inventory inventory

# discovery uses the same profiles
netbox-inventory-toolkit discover --inventory inventory --scan-inventory
```

Use `--settings PATH` to point at a file outside the inventory root.

## Legacy mode

If no `settings.yml` exists, the old flags and environment variables still
work, but log a deprecation warning. Supplying an auth/tool flag together with
a settings file is an error so there is only one source of truth.
