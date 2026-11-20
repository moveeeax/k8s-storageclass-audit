# k8s-storageclass-audit

[![ci](https://github.com/moveeeax/k8s-storageclass-audit/actions/workflows/ci.yml/badge.svg)](https://github.com/moveeeax/k8s-storageclass-audit/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)](https://www.python.org/)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Audit Kubernetes StorageClasses from plain `kubectl` JSON, with no API server and no kubeconfig.

`kubectl get sc` shows a provisioner string and a reclaim policy column. It does not show which
claims landed on which class, whether two classes both claim to be the default, or what happens to
a Postgres volume when its namespace is torn down. All of that is derivable from static JSON of four
resource types, which makes it a CI check instead of an incident.

## Checks

| id | severity | what it catches |
| --- | --- | --- |
| `NO_DEFAULT_CLASS` | high | no class is annotated default while claims omit `storageClassName` |
| `MULTIPLE_DEFAULT_CLASSES` | high | more than one default, so the API server picks one non-deterministically |
| `DEPRECATED_PROVISIONER` | high | an in-tree `kubernetes.io/*` provisioner, with its CSI successor named |
| `RECLAIM_DELETE_ON_STATEFUL` | high | `reclaimPolicy: Delete` under a StatefulSet template or a data-labelled claim |
| `PVC_MISSING_CLASS` | high | a claim naming a class that is not in the input |
| `EXPANSION_DISABLED` | medium | `allowVolumeExpansion` absent or false |
| `IMMEDIATE_BINDING_MULTIZONE` | medium | `Immediate` binding while volumes already span zones |
| `NO_ALLOWED_TOPOLOGIES` | medium | a zonal provisioner with neither `allowedTopologies` nor `WaitForFirstConsumer` |
| `MALFORMED_RESOURCE` | medium | a resource item with no `metadata.name` |
| `UNUSED_CLASS` | low | a non-default class nothing references |

## Install

```bash
pip install git+https://github.com/moveeeax/k8s-storageclass-audit
```

Python 3.11+, standard library only.

## Usage

```bash
kubectl get storageclasses,persistentvolumeclaims,persistentvolumes,statefulsets -A -o json > cluster.json
k8s-storageclass-audit --input cluster.json
```

Against the bundled example:

```console
$ k8s-storageclass-audit --input examples/broken-cluster.json
SEVERITY  CHECK                        NAMESPACE   RESOURCE                 DETAIL
--------  ---------------------------  ----------  -----------------------  ------
high      DEPRECATED_PROVISIONER       -           storageclass/standard    provisioner kubernetes.io/aws-ebs is in-tree and no longer served by modern kubelets; use ebs.csi.aws.com instead
high      PVC_MISSING_CLASS            monitoring  prometheus-data          names StorageClass fast-ssd, which is not present in the input
high      RECLAIM_DELETE_ON_STATEFUL   data        storageclass/standard    reclaimPolicy Delete backs StatefulSet/postgres in namespace data; deleting the claim or the namespace destroys the data
medium    EXPANSION_DISABLED           -           storageclass/standard    allowVolumeExpansion is not true, so a full volume can only be grown by backup and restore
medium    IMMEDIATE_BINDING_MULTIZONE  -           storageclass/standard    volumeBindingMode Immediate provisions before the scheduler runs, and volumes already span 2 zones (eu-west-1a, eu-west-1c); a topology-constrained pod can deadlock
medium    NO_ALLOWED_TOPOLOGIES        -           storageclass/standard    zonal provisioner kubernetes.io/aws-ebs has no allowedTopologies and does not wait for a consumer, so the zone is chosen without knowing where the pod will run
low       UNUSED_CLASS                 -           storageclass/io1-legacy  no claim, StatefulSet template or volume references this class and it is not the default

7 finding(s): 3 high, 3 medium, 1 low
$ echo $?
1
```

Other shapes of the same call:

```bash
# a directory of per-resource dumps, machine output, gate only on high
k8s-storageclass-audit --input dump/ --format json --fail-on high

# two checks, platform namespaces excluded
k8s-storageclass-audit -i cluster.json --only MULTIPLE_DEFAULT_CLASSES,DEPRECATED_PROVISIONER --ignore-namespace kube-system

# straight from kubectl
kubectl get sc,pvc,pv,sts -A -o json | k8s-storageclass-audit --format csv
```

### Options

- `--input/-i PATH` — file, directory or `-` for stdin; repeatable. Default `-`.
- `--format/-f table|json|csv` — default `table`.
- `--severity low|medium|high` — report floor, default `low`.
- `--only IDS` / `--skip IDS` — comma-separated check ids; an unknown id exits 2 rather than silently passing.
- `--namespace/-n NS` / `--ignore-namespace NS` — scope namespaced findings; cluster-wide findings are always kept.
- `--fail-on high|medium|low|none` — exit-code floor, default `medium`.
- `--list-checks` — print the ids.

Exit codes: `0` nothing at or above `--fail-on`, `1` something was found, `2` unreadable or non-Kubernetes input.

## How it works

The loader flattens whatever `kubectl` produced — a single bundle, several files, a directory, stdin,
a bare object, nested `List` wrappers, or a concatenated stream of objects — into one model: classes
keyed by name with their resolved default flag, binding mode and expansion flag; claims with their
effective class; volumes grouped by class with the zones from both their labels and their
`nodeAffinity`; and StatefulSet `volumeClaimTemplates` treated as claims that remember their owner.

Each check is then a pure function over that model, which is why they are easy to test and why a
broken item never stops the audit: a resource without `metadata.name` becomes a `MALFORMED_RESOURCE`
finding and everything else is still checked.

Effective class resolution follows the API server: an explicit `storageClassName` wins, then the
single default class, then nothing. `storageClassName: ""` is an explicit opt-out and is never
reported as a missing class. When two classes claim to be default, no claim is resolved to either —
the ambiguity itself is the finding.

## Development

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
```

## License

MIT
