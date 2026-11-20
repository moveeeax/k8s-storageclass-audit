"""The checks. Each one is a pure function over a :class:`Cluster`."""

from __future__ import annotations

from typing import Callable

from .model import (
    IN_TREE_REPLACEMENTS,
    ZONAL_PROVISIONERS,
    Cluster,
    Finding,
)

CheckFn = Callable[[Cluster], list[Finding]]

SEVERITY_OF = {
    "NO_DEFAULT_CLASS": "high",
    "MULTIPLE_DEFAULT_CLASSES": "high",
    "DEPRECATED_PROVISIONER": "high",
    "RECLAIM_DELETE_ON_STATEFUL": "high",
    "PVC_MISSING_CLASS": "high",
    "EXPANSION_DISABLED": "medium",
    "IMMEDIATE_BINDING_MULTIZONE": "medium",
    "NO_ALLOWED_TOPOLOGIES": "medium",
    "MALFORMED_RESOURCE": "medium",
    "UNUSED_CLASS": "low",
}

DESCRIPTIONS = {
    "NO_DEFAULT_CLASS": "no StorageClass is marked default while claims rely on one",
    "MULTIPLE_DEFAULT_CLASSES": "more than one StorageClass is marked default",
    "DEPRECATED_PROVISIONER": "class uses an in-tree provisioner removed from modern kubelets",
    "RECLAIM_DELETE_ON_STATEFUL": "class backing stateful data deletes the volume with the claim",
    "PVC_MISSING_CLASS": "claim names a StorageClass that is not in the input",
    "EXPANSION_DISABLED": "class cannot grow a volume in place",
    "IMMEDIATE_BINDING_MULTIZONE": "class binds Immediate in a cluster spanning zones",
    "NO_ALLOWED_TOPOLOGIES": "zonal class has neither allowedTopologies nor WaitForFirstConsumer",
    "MALFORMED_RESOURCE": "resource item could not be parsed and was skipped",
    "UNUSED_CLASS": "class is referenced by nothing and is not the default",
}


def check_no_default_class(cluster: Cluster) -> list[Finding]:
    if cluster.defaults:
        return []
    implicit = [c for c in cluster.claims if not c.explicit_class]
    if not implicit:
        return []
    sample = ", ".join(sorted({f"{c.namespace}/{c.ref}" for c in implicit})[:3])
    return [
        Finding(
            check="NO_DEFAULT_CLASS",
            severity=SEVERITY_OF["NO_DEFAULT_CLASS"],
            resource="cluster",
            message=(
                f"no StorageClass carries the default annotation, but {len(implicit)} claim(s) omit "
                f"storageClassName and will stay Pending ({sample})"
            ),
        )
    ]


def check_multiple_default_classes(cluster: Cluster) -> list[Finding]:
    defaults = cluster.defaults
    if len(defaults) < 2:
        return []
    names = sorted(sc.name for sc in defaults)
    return [
        Finding(
            check="MULTIPLE_DEFAULT_CLASSES",
            severity=SEVERITY_OF["MULTIPLE_DEFAULT_CLASSES"],
            resource="cluster",
            message=(
                f"{len(names)} classes are annotated default ({', '.join(names)}); the API server picks one "
                "non-deterministically for every claim that omits storageClassName"
            ),
        )
    ]


def check_deprecated_provisioner(cluster: Cluster) -> list[Finding]:
    findings: list[Finding] = []
    for name, sc in sorted(cluster.classes.items()):
        if sc.provisioner not in IN_TREE_REPLACEMENTS:
            continue
        replacement = IN_TREE_REPLACEMENTS[sc.provisioner]
        hint = f"use {replacement} instead" if replacement else "no in-tree replacement exists; move to a CSI driver"
        findings.append(
            Finding(
                check="DEPRECATED_PROVISIONER",
                severity=SEVERITY_OF["DEPRECATED_PROVISIONER"],
                resource=f"storageclass/{name}",
                message=f"provisioner {sc.provisioner} is in-tree and no longer served by modern kubelets; {hint}",
            )
        )
    return findings


def check_reclaim_delete_on_stateful(cluster: Cluster) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    for claim in cluster.claims:
        if not claim.holds_data:
            continue
        class_name = claim.effective_class
        if class_name is None:
            continue
        sc = cluster.classes.get(class_name)
        if sc is None or sc.reclaim_policy != "Delete":
            continue
        key = (class_name, f"{claim.namespace}/{claim.ref}")
        if key in seen:
            continue
        seen.add(key)
        holder = claim.owner or f"claim {claim.name}"
        findings.append(
            Finding(
                check="RECLAIM_DELETE_ON_STATEFUL",
                severity=SEVERITY_OF["RECLAIM_DELETE_ON_STATEFUL"],
                resource=f"storageclass/{class_name}",
                namespace=claim.namespace,
                message=(
                    f"reclaimPolicy Delete backs {holder} in namespace {claim.namespace}; deleting the claim or the "
                    "namespace destroys the data"
                ),
            )
        )
    return findings


def check_expansion_disabled(cluster: Cluster) -> list[Finding]:
    findings: list[Finding] = []
    for name, sc in sorted(cluster.classes.items()):
        if sc.allow_expansion:
            continue
        findings.append(
            Finding(
                check="EXPANSION_DISABLED",
                severity=SEVERITY_OF["EXPANSION_DISABLED"],
                resource=f"storageclass/{name}",
                message=(
                    "allowVolumeExpansion is not true, so a full volume can only be grown by backup and restore"
                ),
            )
        )
    return findings


def check_immediate_binding_multizone(cluster: Cluster) -> list[Finding]:
    zones = cluster.zones
    if len(zones) < 2:
        return []
    findings: list[Finding] = []
    for name, sc in sorted(cluster.classes.items()):
        if sc.waits_for_consumer:
            continue
        findings.append(
            Finding(
                check="IMMEDIATE_BINDING_MULTIZONE",
                severity=SEVERITY_OF["IMMEDIATE_BINDING_MULTIZONE"],
                resource=f"storageclass/{name}",
                message=(
                    f"volumeBindingMode {sc.binding_mode} provisions before the scheduler runs, and volumes already "
                    f"span {len(zones)} zones ({', '.join(sorted(zones))}); a topology-constrained pod can deadlock"
                ),
            )
        )
    return findings


def check_no_allowed_topologies(cluster: Cluster) -> list[Finding]:
    findings: list[Finding] = []
    for name, sc in sorted(cluster.classes.items()):
        if sc.provisioner not in ZONAL_PROVISIONERS:
            continue
        if sc.allowed_topologies or sc.waits_for_consumer:
            continue
        findings.append(
            Finding(
                check="NO_ALLOWED_TOPOLOGIES",
                severity=SEVERITY_OF["NO_ALLOWED_TOPOLOGIES"],
                resource=f"storageclass/{name}",
                message=(
                    f"zonal provisioner {sc.provisioner} has no allowedTopologies and does not wait for a consumer, "
                    "so the zone is chosen without knowing where the pod will run"
                ),
            )
        )
    return findings


def check_unused_class(cluster: Cluster) -> list[Finding]:
    findings: list[Finding] = []
    volume_classes = {v.storage_class for v in cluster.volumes if v.storage_class}
    for name, sc in sorted(cluster.classes.items()):
        if sc.is_default:
            continue
        if cluster.claims_naming(name) or name in volume_classes:
            continue
        findings.append(
            Finding(
                check="UNUSED_CLASS",
                severity=SEVERITY_OF["UNUSED_CLASS"],
                resource=f"storageclass/{name}",
                message="no claim, StatefulSet template or volume references this class and it is not the default",
            )
        )
    return findings


def check_pvc_missing_class(cluster: Cluster) -> list[Finding]:
    findings: list[Finding] = []
    for claim in cluster.claims:
        requested = claim.requested_class
        if not requested or requested in cluster.classes:
            continue
        findings.append(
            Finding(
                check="PVC_MISSING_CLASS",
                severity=SEVERITY_OF["PVC_MISSING_CLASS"],
                resource=claim.ref,
                namespace=claim.namespace,
                message=f"names StorageClass {requested}, which is not present in the input",
            )
        )
    return findings


CHECKS: dict[str, CheckFn] = {
    "NO_DEFAULT_CLASS": check_no_default_class,
    "MULTIPLE_DEFAULT_CLASSES": check_multiple_default_classes,
    "DEPRECATED_PROVISIONER": check_deprecated_provisioner,
    "RECLAIM_DELETE_ON_STATEFUL": check_reclaim_delete_on_stateful,
    "EXPANSION_DISABLED": check_expansion_disabled,
    "IMMEDIATE_BINDING_MULTIZONE": check_immediate_binding_multizone,
    "NO_ALLOWED_TOPOLOGIES": check_no_allowed_topologies,
    "UNUSED_CLASS": check_unused_class,
    "PVC_MISSING_CLASS": check_pvc_missing_class,
}

#: MALFORMED_RESOURCE is produced by the loader, not by a check function, but it
#: still has to be selectable by --only/--skip.
ALL_CHECK_IDS = tuple(list(CHECKS) + ["MALFORMED_RESOURCE"])


def run(cluster: Cluster, enabled: set[str] | None = None) -> list[Finding]:
    """Run the enabled checks and return findings sorted worst-first."""
    selected = set(ALL_CHECK_IDS) if enabled is None else enabled
    findings: list[Finding] = []
    if "MALFORMED_RESOURCE" in selected:
        findings.extend(cluster.malformed)
    for check_id, fn in CHECKS.items():
        if check_id in selected:
            findings.extend(fn(cluster))
    return findings
