import pytest

from k8s_storageclass_audit.checks import ALL_CHECK_IDS, SEVERITY_OF, run
from k8s_storageclass_audit.loader import load


def ids(findings):
    return sorted(f.check for f in findings)


CASES = [
    ("clean-cluster.json", []),
    ("two-defaults.json", ["MULTIPLE_DEFAULT_CLASSES"]),
    ("no-default.json", ["NO_DEFAULT_CLASS"]),
    ("in-tree.json", ["DEPRECATED_PROVISIONER", "EXPANSION_DISABLED", "NO_ALLOWED_TOPOLOGIES"]),
    ("delete-on-stateful.json", ["RECLAIM_DELETE_ON_STATEFUL", "RECLAIM_DELETE_ON_STATEFUL"]),
    ("immediate-multizone.json", ["IMMEDIATE_BINDING_MULTIZONE", "NO_ALLOWED_TOPOLOGIES"]),
    ("unused-class.json", ["UNUSED_CLASS"]),
    ("missing-class.json", ["PVC_MISSING_CLASS"]),
    ("malformed.json", ["MALFORMED_RESOURCE"]),
]


@pytest.mark.parametrize("fixture,expected", CASES)
def test_fixture_findings(fixture_path, fixture, expected):
    findings = run(load([fixture_path(fixture)]))
    assert ids(findings) == sorted(expected)


def test_clean_cluster_is_silent_even_across_zones(fixture_path):
    """Two zones plus WaitForFirstConsumer everywhere is correct, not a finding."""
    cluster = load([fixture_path("clean-cluster.json")])
    assert len(cluster.zones) == 2
    assert run(cluster) == []


def test_multiple_defaults_names_every_candidate(fixture_path):
    finding = run(load([fixture_path("two-defaults.json")]))[0]
    assert finding.severity == "high"
    assert "gp2" in finding.message and "gp3" in finding.message
    assert finding.namespace is None


def test_no_default_is_quiet_when_every_claim_is_explicit(fixture_path, tmp_path):
    source = open(fixture_path("no-default.json")).read().replace(
        '{"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "1Gi"}}}',
        '{"storageClassName": "slow", "accessModes": ["ReadWriteOnce"]}',
    )
    path = tmp_path / "explicit.json"
    path.write_text(source, encoding="utf-8")
    assert "NO_DEFAULT_CLASS" not in ids(run(load([str(path)])))


def test_deprecated_provisioner_suggests_the_csi_driver(fixture_path):
    finding = next(f for f in run(load([fixture_path("in-tree.json")])) if f.check == "DEPRECATED_PROVISIONER")
    assert "ebs.csi.aws.com" in finding.message
    assert finding.resource == "storageclass/standard"


def test_reclaim_delete_names_the_statefulset(fixture_path):
    findings = [f for f in run(load([fixture_path("delete-on-stateful.json")])) if f.check == "RECLAIM_DELETE_ON_STATEFUL"]
    owners = {f.namespace: f.message for f in findings}
    assert "StatefulSet/kafka" in owners["streaming"]
    assert "mysql-data" in owners["shop"]
    assert all(f.resource == "storageclass/gp3" for f in findings)


def test_immediate_binding_needs_more_than_one_zone(fixture_path, tmp_path):
    source = open(fixture_path("immediate-multizone.json")).read().replace("eu-central-1c", "eu-central-1a")
    path = tmp_path / "single-zone.json"
    path.write_text(source, encoding="utf-8")
    assert "IMMEDIATE_BINDING_MULTIZONE" not in ids(run(load([str(path)])))


def test_default_class_is_never_reported_unused(fixture_path):
    findings = run(load([fixture_path("unused-class.json")]))
    assert [f.resource for f in findings] == ["storageclass/io2-legacy"]


def test_class_used_only_by_a_volume_is_not_unused(fixture_path, tmp_path):
    source = open(fixture_path("unused-class.json")).read().replace(
        '  ]\n}',
        '''    ,{
      "apiVersion": "v1",
      "kind": "PersistentVolume",
      "metadata": {"name": "pv-legacy"},
      "spec": {"storageClassName": "io2-legacy"}
    }
  ]
}''',
    )
    path = tmp_path / "used-by-pv.json"
    path.write_text(source, encoding="utf-8")
    assert run(load([str(path)])) == []


def test_opted_out_claim_is_not_reported_missing(fixture_path):
    findings = run(load([fixture_path("missing-class.json")]))
    assert [f.resource for f in findings] == ["ghost"]


def test_every_check_id_has_a_severity():
    assert set(SEVERITY_OF) == set(ALL_CHECK_IDS)
    assert set(SEVERITY_OF.values()) <= {"low", "medium", "high"}


def test_enabled_subset_runs_only_those_checks(fixture_path):
    cluster = load([fixture_path("in-tree.json")])
    assert ids(run(cluster, {"DEPRECATED_PROVISIONER"})) == ["DEPRECATED_PROVISIONER"]
    assert run(cluster, set()) == []
