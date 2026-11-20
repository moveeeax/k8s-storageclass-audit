"""The cluster model every check reads.

The loader flattens whatever ``kubectl get ... -o json`` produced into these
dataclasses, so no check ever touches raw Kubernetes JSON.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SEVERITIES = ("low", "medium", "high")
SEVERITY_RANK = {name: rank for rank, name in enumerate(SEVERITIES, start=1)}

DEFAULT_CLASS_ANNOTATIONS = (
    "storageclass.kubernetes.io/is-default-class",
    "storageclass.beta.kubernetes.io/is-default-class",
)

ZONE_LABELS = (
    "topology.kubernetes.io/zone",
    "failure-domain.beta.kubernetes.io/zone",
)

#: In-tree provisioners removed from modern kubelets, with their CSI successor.
#: An empty value means the driver was deleted without a drop-in replacement.
IN_TREE_REPLACEMENTS = {
    "kubernetes.io/aws-ebs": "ebs.csi.aws.com",
    "kubernetes.io/gce-pd": "pd.csi.storage.gke.io",
    "kubernetes.io/azure-disk": "disk.csi.azure.com",
    "kubernetes.io/azure-file": "file.csi.azure.com",
    "kubernetes.io/cinder": "cinder.csi.openstack.org",
    "kubernetes.io/vsphere-volume": "csi.vsphere.vmware.com",
    "kubernetes.io/portworx-volume": "pxd.portworx.com",
    "kubernetes.io/rbd": "rbd.csi.ceph.com",
    "kubernetes.io/glusterfs": "",
    "kubernetes.io/scaleio": "",
    "kubernetes.io/storageos": "",
    "kubernetes.io/quobyte": "",
    "kubernetes.io/flocker": "",
}

#: Provisioners that create a volume pinned to one zone. Binding such a class
#: Immediate without allowedTopologies is how a pod ends up unschedulable.
ZONAL_PROVISIONERS = frozenset(
    {
        "ebs.csi.aws.com",
        "pd.csi.storage.gke.io",
        "disk.csi.azure.com",
        "cinder.csi.openstack.org",
        "csi.vsphere.vmware.com",
        "kubernetes.io/aws-ebs",
        "kubernetes.io/gce-pd",
        "kubernetes.io/azure-disk",
        "kubernetes.io/cinder",
    }
)

#: Label values that mark a claim as holding data worth keeping, used when a
#: claim is not owned by a StatefulSet but clearly backs one.
DATA_LABEL_KEYS = ("app.kubernetes.io/component", "component", "role", "tier")
DATA_LABEL_VALUES = frozenset(
    {"database", "db", "data", "datastore", "postgres", "postgresql", "mysql", "mariadb", "redis", "etcd", "kafka"}
)


@dataclass(frozen=True)
class Finding:
    check: str
    severity: str
    resource: str
    message: str
    namespace: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "check": self.check,
            "severity": self.severity,
            "namespace": self.namespace,
            "resource": self.resource,
            "message": self.message,
        }


@dataclass
class StorageClass:
    name: str
    provisioner: str = ""
    reclaim_policy: str = "Delete"
    binding_mode: str = "Immediate"
    allow_expansion: bool = False
    is_default: bool = False
    allowed_topologies: bool = False
    parameters: dict[str, str] = field(default_factory=dict)

    @property
    def waits_for_consumer(self) -> bool:
        return self.binding_mode == "WaitForFirstConsumer"


@dataclass
class Claim:
    """A PersistentVolumeClaim, or a StatefulSet volumeClaimTemplate posing as one."""

    name: str
    namespace: str
    requested_class: str | None = None
    effective_class: str | None = None
    labels: dict[str, str] = field(default_factory=dict)
    owner: str | None = None

    @property
    def explicit_class(self) -> bool:
        return self.requested_class is not None

    @property
    def holds_data(self) -> bool:
        if self.owner is not None:
            return True
        for key in DATA_LABEL_KEYS:
            value = self.labels.get(key)
            if value and value.lower() in DATA_LABEL_VALUES:
                return True
        return False

    @property
    def ref(self) -> str:
        if self.owner is not None:
            return f"{self.owner}:{self.name}"
        return self.name


@dataclass
class Volume:
    name: str
    storage_class: str | None = None
    zones: frozenset[str] = frozenset()


@dataclass
class Cluster:
    classes: dict[str, StorageClass] = field(default_factory=dict)
    claims: list[Claim] = field(default_factory=list)
    volumes: list[Volume] = field(default_factory=list)
    malformed: list[Finding] = field(default_factory=list)

    @property
    def defaults(self) -> list[StorageClass]:
        return [sc for sc in self.classes.values() if sc.is_default]

    @property
    def zones(self) -> frozenset[str]:
        found: set[str] = set()
        for volume in self.volumes:
            found |= set(volume.zones)
        return frozenset(found)

    def claims_naming(self, class_name: str) -> list[Claim]:
        return [c for c in self.claims if c.effective_class == class_name or c.requested_class == class_name]
