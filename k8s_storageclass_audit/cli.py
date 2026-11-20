"""Command line entrypoint."""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from . import __version__
from .checks import ALL_CHECK_IDS, run
from .loader import InputError, load
from .model import SEVERITIES, SEVERITY_RANK, Finding
from .render import FORMATS, render

EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_INPUT = 2


def _split(values: Sequence[str] | None) -> list[str]:
    """Flatten repeated and comma-separated option values."""
    out: list[str] = []
    for value in values or []:
        out.extend(part.strip() for part in value.split(",") if part.strip())
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="k8s-storageclass-audit",
        description=(
            "Audit Kubernetes StorageClasses offline from kubectl JSON: default-class problems, "
            "deprecated provisioners, destructive reclaim policies, binding-mode and expansion traps."
        ),
    )
    parser.add_argument(
        "--input",
        "-i",
        action="append",
        metavar="PATH",
        help="file, directory or - for stdin; repeatable (default: -)",
    )
    parser.add_argument("--format", "-f", choices=sorted(FORMATS), default="table", help="output format")
    parser.add_argument(
        "--severity",
        choices=SEVERITIES,
        default="low",
        help="only report findings at this severity or above (default: low)",
    )
    parser.add_argument("--only", action="append", metavar="IDS", help="report only these check ids")
    parser.add_argument("--skip", action="append", metavar="IDS", help="never report these check ids")
    parser.add_argument(
        "--namespace",
        "-n",
        action="append",
        metavar="NS",
        help="restrict namespaced findings to these namespaces",
    )
    parser.add_argument(
        "--ignore-namespace",
        action="append",
        metavar="NS",
        help="drop namespaced findings from these namespaces",
    )
    parser.add_argument(
        "--fail-on",
        choices=(*SEVERITIES, "none"),
        default="medium",
        help="exit 1 when a finding at this severity or above is reported (default: medium)",
    )
    parser.add_argument("--list-checks", action="store_true", help="print the check ids and exit")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def select_checks(only: list[str], skip: list[str]) -> set[str]:
    """Resolve --only/--skip into the set of check ids to run.

    An unknown id is an input error rather than a silent no-op: a typo in CI
    would otherwise turn a gate into a pass.
    """
    known = set(ALL_CHECK_IDS)
    unknown = sorted({c for c in (*only, *skip) if c not in known})
    if unknown:
        raise InputError(f"unknown check id(s): {', '.join(unknown)}")
    selected = set(only) if only else set(known)
    return selected - set(skip)


def filter_findings(
    findings: list[Finding],
    severity_floor: str,
    namespaces: list[str],
    ignored: list[str],
) -> list[Finding]:
    floor = SEVERITY_RANK[severity_floor]
    keep: list[Finding] = []
    for finding in findings:
        if SEVERITY_RANK.get(finding.severity, 0) < floor:
            continue
        if finding.namespace is not None:
            if namespaces and finding.namespace not in namespaces:
                continue
            if finding.namespace in ignored:
                continue
        keep.append(finding)
    return keep


def exit_code(findings: list[Finding], fail_on: str) -> int:
    if fail_on == "none":
        return EXIT_CLEAN
    floor = SEVERITY_RANK[fail_on]
    worst = max((SEVERITY_RANK.get(f.severity, 0) for f in findings), default=0)
    return EXIT_FINDINGS if worst >= floor else EXIT_CLEAN


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list_checks:
        for check_id in ALL_CHECK_IDS:
            print(check_id)
        return EXIT_CLEAN
    try:
        enabled = select_checks(_split(args.only), _split(args.skip))
        cluster = load(args.input or ["-"])
    except InputError as exc:
        print(f"k8s-storageclass-audit: {exc}", file=sys.stderr)
        return EXIT_INPUT
    findings = filter_findings(
        run(cluster, enabled),
        args.severity,
        _split(args.namespace),
        _split(args.ignore_namespace),
    )
    sys.stdout.write(render(findings, args.format))
    return exit_code(findings, args.fail_on)


if __name__ == "__main__":
    raise SystemExit(main())
