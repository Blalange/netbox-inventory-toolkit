# NetBox sync

The sync merges `manual/` and `generated/`, expands rack layouts, enriches
device types from the Device Type Library, and reconciles the result into
NetBox.

## Run locally

```sh
# validate only, no NetBox connection
netbox-inventory-toolkit sync --inventory inventory --check --no-dtl

# show the create/update/delete plan
NETBOX_URL=https://netbox.example NETBOX_TOKEN=... \
  netbox-inventory-toolkit sync --inventory inventory --dry-run --prune

# apply
NETBOX_URL=https://netbox.example NETBOX_TOKEN=... \
  netbox-inventory-toolkit sync --inventory inventory --prune
```

`--check` prints every validation problem and exits non-zero if any are found.
`--prune` is required for deletions; without it the sync only creates and
updates.

## Environment

| Variable | Purpose |
|---|---|
| `NETBOX_URL` | NetBox base URL |
| `NETBOX_TOKEN` | API token (v2 tokens supported) |
| `NETBOX_INVENTORY_DIR` | Inventory root (default `inventory`) |
| `NETBOX_IMAGES_DIR` | Device-type image directory (default `<inventory>/images`) |
| `DRY_RUN` | Plan only |
| `PRUNE` | Delete stale `gitops`-tagged objects |
| `NETBOX_WAIT_SECONDS` | Wait for NetBox to become reachable |
| `FORCE_IMAGES` | Re-upload images even when content matches |
| `DTL_ENABLED` | Enable Device Type Library enrichment |
| `DTL_REPO`, `DTL_REF` | DTL repository and ref |
| `DTL_CACHE_DIR` | DTL definition/image cache |
| `DTL_CACHE_TTL_HOURS` | Cache lifetime |
| `DTL_TOKEN` | Optional GitHub token for DTL tree lookups |

## Managed objects

Every taggable object created by the sync carries:

- `gitops`
- one or more provenance tags from the inventory `sources` list

With `PRUNE=true`, objects that still carry `gitops` but are no longer in the
inventory are deleted. Objects created by hand in NetBox do not carry the tag
and are never touched. Owners and owner groups are not taggable and are never
pruned; contacts and contact assignments are taggable and are pruned normally.

## Device Type Library

The sync fetches device and module types from the Apache-2.0
[NetBox Device Type Library](https://github.com/netbox-community/devicetype-library)
at runtime. Definitions and elevation images are cached in `DTL_CACHE_DIR`; no
DTL data is committed to the inventory repository.

Manual values and manually declared components always win. DTL fills gaps and
adds components that are not already present. Set `dtl:` on a device type or
module type when the model/path differs from the DTL entry.

## Device images

`front` and `rear` images are uploaded through the NetBox multipart API. Other
images are stored as `dcim.devicetype` attachments. The sync compares the local
file's SHA-256 digest with the image NetBox is serving, so unchanged images are
not re-uploaded. `--force-images` overrides this.

## Kubernetes CronJob

The release image can be used directly:

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: netbox-sync
spec:
  schedule: "*/10 * * * *"
  concurrencyPolicy: Forbid
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: Never
          containers:
            - name: sync
              image: ghcr.io/blalange/netbox-inventory-toolkit:v0.1.5
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
                - name: DTL_ENABLED
                  value: "true"
                - name: DTL_CACHE_DIR
                  value: /media/dtl-cache
                - name: PRUNE
                  value: "true"
                - name: HOME
                  value: /tmp
              volumeMounts:
                - name: inventory
                  mountPath: /inventory
                - name: images
                  mountPath: /images
                - name: media
                  mountPath: /media
                - name: tmp
                  mountPath: /tmp
          volumes:
            - name: inventory
              configMap:
                name: netbox-inventory-data
            - name: images
              configMap:
                name: netbox-device-type-images
            - name: media
              persistentVolumeClaim:
                claimName: netbox-media
            - name: tmp
              emptyDir: {}
```
