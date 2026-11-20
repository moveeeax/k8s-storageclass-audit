import json

import pytest

from k8s_storageclass_audit.loader import InputError, load


def test_list_wrapper_is_flattened(fixture_path):
    cluster = load([fixture_path("clean-cluster.json")])
    assert sorted(cluster.classes) == ["gp3-default", "gp3-retain"]
    assert len(cluster.volumes) == 2
    # One real PVC plus one claim synthesised from the StatefulSet template.
    assert sorted(c.name for c in cluster.claims) == ["pgdata", "web-assets"]


def test_bare_object_is_accepted(fixture_path):
    cluster = load([fixture_path("in-tree.json")])
    assert cluster.classes["standard"].provisioner == "kubernetes.io/aws-ebs"
    assert cluster.classes["standard"].is_default is True


def test_concatenated_stream_is_accepted(fixture_path):
    cluster = load([fixture_path("stream.json")])
    assert list(cluster.classes) == ["a"]
    assert [c.name for c in cluster.claims] == ["b"]


def test_directory_input_reads_every_json(tmp_path, fixture_path):
    for name in ("in-tree.json", "stream.json"):
        (tmp_path / name).write_text(open(fixture_path(name)).read(), encoding="utf-8")
    cluster = load([str(tmp_path)])
    assert sorted(cluster.classes) == ["a", "standard"]


def test_stdin_input(monkeypatch, fixture_path):
    import io
    import sys

    monkeypatch.setattr(sys, "stdin", io.StringIO(open(fixture_path("in-tree.json")).read()))
    cluster = load(["-"])
    assert "standard" in cluster.classes


def test_default_annotation_beta_and_stable(fixture_path):
    cluster = load([fixture_path("two-defaults.json")])
    assert sorted(sc.name for sc in cluster.defaults) == ["gp2", "gp3"]


def test_effective_class_resolution(fixture_path):
    cluster = load([fixture_path("missing-class.json")])
    by_name = {c.name: c for c in cluster.claims}
    # An explicit empty storageClassName opts out of the default entirely.
    assert by_name["opted-out"].requested_class == ""
    assert by_name["opted-out"].effective_class is None
    assert by_name["ghost"].effective_class == "deleted-tier"


def test_implicit_claim_takes_single_default(fixture_path):
    cluster = load([fixture_path("clean-cluster.json")])
    web = next(c for c in cluster.claims if c.name == "web-assets")
    assert web.requested_class is None
    assert web.effective_class == "gp3-default"


def test_ambiguous_default_leaves_claim_unresolved(fixture_path):
    cluster = load([fixture_path("two-defaults.json")])
    cache = next(c for c in cluster.claims if c.name == "cache")
    assert cache.effective_class is None


def test_zones_come_from_labels_and_node_affinity(fixture_path):
    cluster = load([fixture_path("immediate-multizone.json")])
    assert cluster.zones == frozenset({"eu-central-1a", "eu-central-1c"})


def test_item_without_metadata_becomes_a_finding(fixture_path):
    cluster = load([fixture_path("malformed.json")])
    assert [f.check for f in cluster.malformed] == ["MALFORMED_RESOURCE"]
    # The healthy sibling in the same list is still loaded.
    assert "gp3" in cluster.classes


def test_unknown_kinds_are_ignored_but_absence_is_an_error(fixture_path):
    with pytest.raises(InputError, match="no StorageClass"):
        load([fixture_path("not-kubernetes.json")])


def test_unreadable_file_raises(tmp_path):
    with pytest.raises(InputError, match="cannot read"):
        load([str(tmp_path / "nope.json")])


def test_broken_json_raises(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json at all", encoding="utf-8")
    with pytest.raises(InputError, match="not valid JSON"):
        load([str(bad)])


def test_empty_directory_raises(tmp_path):
    with pytest.raises(InputError, match="no .json files"):
        load([str(tmp_path)])


def test_statefulset_template_claim_is_owned(fixture_path):
    cluster = load([fixture_path("delete-on-stateful.json")])
    logs = next(c for c in cluster.claims if c.name == "logs")
    assert logs.owner == "StatefulSet/kafka"
    assert logs.namespace == "streaming"
    assert logs.holds_data is True


def test_labelled_pvc_counts_as_data(fixture_path):
    cluster = load([fixture_path("delete-on-stateful.json")])
    mysql = next(c for c in cluster.claims if c.name == "mysql-data")
    assert mysql.owner is None
    assert mysql.holds_data is True


def test_nested_list_wrapper(tmp_path):
    doc = {
        "kind": "List",
        "items": [
            {
                "kind": "List",
                "items": [
                    {
                        "kind": "StorageClass",
                        "metadata": {"name": "inner"},
                        "provisioner": "ebs.csi.aws.com",
                    }
                ],
            }
        ],
    }
    path = tmp_path / "nested.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    cluster = load([str(path)])
    assert list(cluster.classes) == ["inner"]
