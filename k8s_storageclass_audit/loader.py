"""Turn mixed ``kubectl get -o json`` output into a :class:`Cluster`.

Accepts a single bundle, several files, a directory, stdin, a bare object, a
``List`` wrapper, nested ``List`` wrappers and a concatenated stream of JSON
objects. Anything that is not one of the four resource kinds we care about is
ignored; a recognised item that is structurally broken becomes a finding.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Iterable, Iterator

from .model import (
    DEFAULT_CLASS_ANNOTATIONS,
    ZONE_LABELS,
    Claim,
    Cluster,
    Finding,
    StorageClass,
    Volume,
)

WANTED_KINDS = frozenset({"StorageClass", "PersistentVolumeClaim", "PersistentVolume", "StatefulSet"})


class InputError(Exception):
    """Raised when the input cannot be read or holds no Kubernetes resources."""


def _decode_stream(text: str) -> list[Any]:
    """Parse one JSON document, or a stream of them written back to back."""
    stripped = text.strip()
    if not stripped:
        return []
    try:
        return [json.loads(stripped)]
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    docs: list[Any] = []
    index = 0
    length = len(stripped)
    while index < length:
        while index < length and stripped[index] in " \t\r\n":
            index += 1
        if index >= length:
            break
        try:
            doc, end = decoder.raw_decode(stripped, index)
        except json.JSONDecodeError as exc:
            raise InputError(f"input is not valid JSON: {exc.msg} at position {exc.pos}") from exc
        docs.append(doc)
        index = end
    return docs


def _iter_items(doc: Any) -> Iterator[dict[str, Any]]:
    """Walk a document yielding leaf resource objects, unwrapping any List."""
    if isinstance(doc, list):
        for entry in doc:
            yield from _iter_items(entry)
        return
    if not isinstance(doc, dict):
        return
    items = doc.get("items")
    if isinstance(items, list):
        for entry in items:
            yield from _iter_items(entry)
        return
    yield doc


def _read_paths(paths: Iterable[str]) -> list[tuple[str, str]]:
    blobs: list[tuple[str, str]] = []
    for raw in paths:
        if raw == "-":
            blobs.append(("<stdin>", sys.stdin.read()))
            continue
        path = Path(raw)
        if path.is_dir():
            files = sorted(p for p in path.rglob("*.json") if p.is_file())
            if not files:
                raise InputError(f"no .json files under {path}")
            for found in files:
                blobs.append((str(found), found.read_text(encoding="utf-8")))
            continue
        try:
            blobs.append((str(path), path.read_text(encoding="utf-8")))
        except OSError as exc:
            raise InputError(f"cannot read {path}: {exc.strerror}") from exc
    return blobs


def _metadata(item: dict[str, Any]) -> dict[str, Any] | None:
    meta = item.get("metadata")
    if not isinstance(meta, dict):
        return None
    name = meta.get("name")
    if not isinstance(name, str) or not name:
        return None
    return meta


def _str_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items()}


def _is_default(meta: dict[str, Any]) -> bool:
    annotations = _str_map(meta.get("annotations"))
    return any(annotations.get(key, "").lower() == "true" for key in DEFAULT_CLASS_ANNOTATIONS)


def _load_class(item: dict[str, Any], meta: dict[str, Any]) -> StorageClass:
    topologies = item.get("allowedTopologies")
    binding = item.get("volumeBindingMode")
    return StorageClass(
        name=str(meta["name"]),
        provisioner=str(item.get("provisioner") or ""),
        reclaim_policy=str(item.get("reclaimPolicy") or "Delete"),
        binding_mode=str(binding) if isinstance(binding, str) and binding else "Immediate",
        allow_expansion=item.get("allowVolumeExpansion") is True,
        is_default=_is_default(meta),
        allowed_topologies=bool(isinstance(topologies, list) and topologies),
        parameters=_str_map(item.get("parameters")),
    )


def _claim_class(spec: dict[str, Any], meta: dict[str, Any]) -> str | None:
    """The class a claim asked for, or ``None`` when it asked for the default.

    ``storageClassName: ""`` is an explicit opt-out of the default, and is
    reported as the sentinel empty string rather than ``None``.
    """
    name = spec.get("storageClassName")
    if isinstance(name, str):
        return name
    annotated = _str_map(meta.get("annotations")).get("volume.beta.kubernetes.io/storage-class")
    if annotated:
        return annotated
    return None


def _load_claim(item: dict[str, Any], meta: dict[str, Any]) -> Claim:
    spec = item.get("spec") if isinstance(item.get("spec"), dict) else {}
    return Claim(
        name=str(meta["name"]),
        namespace=str(meta.get("namespace") or "default"),
        requested_class=_claim_class(spec, meta),
        labels=_str_map(meta.get("labels")),
    )


def _pv_zones(item: dict[str, Any], meta: dict[str, Any]) -> frozenset[str]:
    zones: set[str] = set()
    labels = _str_map(meta.get("labels"))
    for key in ZONE_LABELS:
        value = labels.get(key)
        if value:
            zones.add(value)
    spec = item.get("spec") if isinstance(item.get("spec"), dict) else {}
    affinity = spec.get("nodeAffinity")
    if isinstance(affinity, dict):
        required = affinity.get("required")
        if isinstance(required, dict):
            for term in required.get("nodeSelectorTerms") or []:
                if not isinstance(term, dict):
                    continue
                for expr in term.get("matchExpressions") or []:
                    if not isinstance(expr, dict):
                        continue
                    if expr.get("key") in ZONE_LABELS:
                        zones.update(str(v) for v in expr.get("values") or [])
    return frozenset(zones)


def _load_volume(item: dict[str, Any], meta: dict[str, Any]) -> Volume:
    spec = item.get("spec") if isinstance(item.get("spec"), dict) else {}
    class_name = spec.get("storageClassName")
    return Volume(
        name=str(meta["name"]),
        storage_class=str(class_name) if isinstance(class_name, str) and class_name else None,
        zones=_pv_zones(item, meta),
    )


def _load_statefulset_claims(item: dict[str, Any], meta: dict[str, Any]) -> list[Claim]:
    spec = item.get("spec") if isinstance(item.get("spec"), dict) else {}
    namespace = str(meta.get("namespace") or "default")
    owner = f"StatefulSet/{meta['name']}"
    claims: list[Claim] = []
    for template in spec.get("volumeClaimTemplates") or []:
        if not isinstance(template, dict):
            continue
        tmeta = template.get("metadata") if isinstance(template.get("metadata"), dict) else {}
        tspec = template.get("spec") if isinstance(template.get("spec"), dict) else {}
        claims.append(
            Claim(
                name=str(tmeta.get("name") or "volumeClaimTemplate"),
                namespace=namespace,
                requested_class=_claim_class(tspec, tmeta),
                labels=_str_map(tmeta.get("labels")),
                owner=owner,
            )
        )
    return claims


def _resolve_effective_classes(cluster: Cluster) -> None:
    defaults = cluster.defaults
    fallback = defaults[0].name if len(defaults) == 1 else None
    for claim in cluster.claims:
        requested = claim.requested_class
        if requested is None:
            claim.effective_class = fallback
        elif requested == "":
            claim.effective_class = None
        else:
            claim.effective_class = requested


def load(paths: Iterable[str]) -> Cluster:
    """Build a cluster model from files, directories or stdin."""
    cluster = Cluster()
    recognised = 0
    for origin, text in _read_paths(paths):
        try:
            docs = _decode_stream(text)
        except InputError as exc:
            raise InputError(f"{origin}: {exc}") from exc
        for doc in docs:
            for item in _iter_items(doc):
                kind = item.get("kind")
                if kind not in WANTED_KINDS:
                    continue
                recognised += 1
                meta = _metadata(item)
                if meta is None:
                    cluster.malformed.append(
                        Finding(
                            check="MALFORMED_RESOURCE",
                            severity="medium",
                            resource=f"{kind} in {origin}",
                            message=f"{kind} item has no metadata.name and was skipped",
                        )
                    )
                    continue
                if kind == "StorageClass":
                    loaded = _load_class(item, meta)
                    cluster.classes.setdefault(loaded.name, loaded)
                elif kind == "PersistentVolumeClaim":
                    cluster.claims.append(_load_claim(item, meta))
                elif kind == "PersistentVolume":
                    cluster.volumes.append(_load_volume(item, meta))
                else:
                    cluster.claims.extend(_load_statefulset_claims(item, meta))
    if recognised == 0:
        raise InputError(
            "no StorageClass, PersistentVolumeClaim, PersistentVolume or StatefulSet found in the input"
        )
    _resolve_effective_classes(cluster)
    return cluster
