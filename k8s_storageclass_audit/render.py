"""Output formats: a padded table, machine JSON and CSV."""

from __future__ import annotations

import csv
import io
import json

from .checks import DESCRIPTIONS
from .model import SEVERITY_RANK, Finding

HEADERS = ("SEVERITY", "CHECK", "NAMESPACE", "RESOURCE", "DETAIL")


def sort_key(finding: Finding) -> tuple[int, str, str, str]:
    return (
        -SEVERITY_RANK.get(finding.severity, 0),
        finding.check,
        finding.namespace or "",
        finding.resource,
    )


def summarise(findings: list[Finding]) -> dict[str, int]:
    counts = {"total": len(findings), "high": 0, "medium": 0, "low": 0}
    for finding in findings:
        if finding.severity in counts:
            counts[finding.severity] += 1
    return counts


def _table(findings: list[Finding]) -> str:
    if not findings:
        return "No findings.\n"
    rows = [
        (
            finding.severity,
            finding.check,
            finding.namespace or "-",
            finding.resource,
            finding.message,
        )
        for finding in findings
    ]
    widths = [len(h) for h in HEADERS]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    # The detail column is last and free-form, so it is never padded.
    widths[-1] = len(HEADERS[-1])
    lines = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(HEADERS)).rstrip()]
    lines.append("  ".join("-" * widths[i] for i in range(len(HEADERS))))
    for row in rows:
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
    counts = summarise(findings)
    lines.append("")
    lines.append(
        f"{counts['total']} finding(s): {counts['high']} high, {counts['medium']} medium, {counts['low']} low"
    )
    return "\n".join(lines) + "\n"


def _json(findings: list[Finding]) -> str:
    payload = {
        "summary": summarise(findings),
        "findings": [
            {**finding.as_dict(), "description": DESCRIPTIONS.get(finding.check, "")} for finding in findings
        ],
    }
    return json.dumps(payload, indent=2) + "\n"


def _csv(findings: list[Finding]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["check", "severity", "namespace", "resource", "detail"])
    for finding in findings:
        writer.writerow(
            [finding.check, finding.severity, finding.namespace or "", finding.resource, finding.message]
        )
    return buffer.getvalue()


FORMATS = {"table": _table, "json": _json, "csv": _csv}


def render(findings: list[Finding], fmt: str) -> str:
    return FORMATS[fmt](sorted(findings, key=sort_key))
