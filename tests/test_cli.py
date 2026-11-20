import json

import pytest

from k8s_storageclass_audit.cli import main
from k8s_storageclass_audit.loader import InputError
from k8s_storageclass_audit.model import Finding
from k8s_storageclass_audit.render import render


def test_clean_cluster_exits_zero(fixture_path, capsys):
    assert main(["-i", fixture_path("clean-cluster.json")]) == 0
    assert "No findings." in capsys.readouterr().out


def test_two_defaults_exit_one_under_default_fail_on(fixture_path, capsys):
    assert main(["-i", fixture_path("two-defaults.json")]) == 1
    out = capsys.readouterr().out
    assert out.count("MULTIPLE_DEFAULT_CLASSES") == 1


def test_unreadable_input_exits_two_without_traceback(fixture_path, capsys):
    assert main(["-i", fixture_path("not-kubernetes.json")]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("k8s-storageclass-audit: ")
    assert "Traceback" not in captured.err


def test_json_output_is_machine_readable(fixture_path, capsys):
    main(["-i", fixture_path("in-tree.json"), "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["high"] == 1
    assert payload["summary"]["total"] == 3
    assert all("description" in f for f in payload["findings"])


def test_csv_output_has_a_header(fixture_path, capsys):
    main(["-i", fixture_path("in-tree.json"), "--format", "csv"])
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[0] == "check,severity,namespace,resource,detail"
    assert len(lines) == 4


def test_findings_are_sorted_worst_first(example_path, capsys):
    main(["-i", example_path("broken-cluster.json"), "--format", "csv"])
    severities = [line.split(",")[1] for line in capsys.readouterr().out.strip().splitlines()[1:]]
    assert severities == sorted(severities, key=["high", "medium", "low"].index)


def test_severity_floor_drops_lower_findings(fixture_path, capsys):
    main(["-i", fixture_path("in-tree.json"), "--severity", "high", "--format", "csv"])
    body = capsys.readouterr().out.strip().splitlines()[1:]
    assert [line.split(",")[0] for line in body] == ["DEPRECATED_PROVISIONER"]


def test_fail_on_none_always_exits_zero(fixture_path):
    assert main(["-i", fixture_path("two-defaults.json"), "--fail-on", "none"]) == 0


def test_fail_on_high_ignores_medium(fixture_path):
    assert main(["-i", fixture_path("immediate-multizone.json"), "--fail-on", "high"]) == 0
    assert main(["-i", fixture_path("immediate-multizone.json"), "--fail-on", "medium"]) == 1


def test_only_and_skip_compose_with_severity(fixture_path, capsys):
    args = ["-i", fixture_path("in-tree.json"), "--format", "csv"]
    main([*args, "--only", "DEPRECATED_PROVISIONER,EXPANSION_DISABLED", "--skip", "EXPANSION_DISABLED"])
    body = capsys.readouterr().out.strip().splitlines()[1:]
    assert [line.split(",")[0] for line in body] == ["DEPRECATED_PROVISIONER"]

    main([*args, "--only", "EXPANSION_DISABLED", "--severity", "high"])
    assert capsys.readouterr().out.strip().splitlines()[1:] == []


def test_unknown_check_id_is_an_input_error(fixture_path, capsys):
    assert main(["-i", fixture_path("in-tree.json"), "--only", "NO_SUCH_CHECK"]) == 2
    assert "unknown check id(s): NO_SUCH_CHECK" in capsys.readouterr().err


def test_namespace_scopes_namespaced_findings(fixture_path, capsys):
    args = ["-i", fixture_path("delete-on-stateful.json"), "--format", "csv"]
    main([*args, "--namespace", "shop"])
    body = capsys.readouterr().out.strip().splitlines()[1:]
    assert len(body) == 1 and ",shop," in body[0]

    main([*args, "--ignore-namespace", "shop,streaming"])
    assert capsys.readouterr().out.strip().splitlines()[1:] == []


def test_ignore_namespace_keeps_cluster_wide_findings(fixture_path, capsys):
    main(["-i", fixture_path("two-defaults.json"), "--ignore-namespace", "web", "--format", "csv"])
    body = capsys.readouterr().out.strip().splitlines()[1:]
    assert [line.split(",")[0] for line in body] == ["MULTIPLE_DEFAULT_CLASSES"]


def test_malformed_item_is_reported_and_the_rest_audited(fixture_path, capsys):
    assert main(["-i", fixture_path("malformed.json"), "--format", "csv"]) == 1
    body = capsys.readouterr().out.strip().splitlines()[1:]
    assert len(body) == 1
    assert body[0].startswith("MALFORMED_RESOURCE,medium,,StorageClass in ")


def test_list_checks(capsys):
    assert main(["--list-checks"]) == 0
    printed = capsys.readouterr().out.split()
    assert "MALFORMED_RESOURCE" in printed and "UNUSED_CLASS" in printed


def test_several_inputs_merge(fixture_path, capsys):
    code = main(
        [
            "-i",
            fixture_path("in-tree.json"),
            "-i",
            fixture_path("unused-class.json"),
            "--only",
            "MULTIPLE_DEFAULT_CLASSES",
            "--format",
            "csv",
        ]
    )
    assert code == 1
    assert "standard" in capsys.readouterr().out


def test_render_rejects_an_unknown_format():
    with pytest.raises(KeyError):
        render([Finding("X", "low", "r", "m")], "yaml")


def test_input_error_message_is_one_line(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("[[[", encoding="utf-8")
    with pytest.raises(InputError) as excinfo:
        from k8s_storageclass_audit.loader import load

        load([str(bad)])
    assert "\n" not in str(excinfo.value)
