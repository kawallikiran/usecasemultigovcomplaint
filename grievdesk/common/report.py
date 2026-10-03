"""Critic findings: what was tested, expected, actual, severity (format asked for in the AIGP26 brief)."""
import csv
import json
import os
from dataclasses import dataclass, asdict, field

SEVERITY_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3, "Info": 4}

CRITIC_TESTS = {
    1: "Bias and disparate outcomes",
    2: "Robustness to unusual inputs",
    3: "Privacy and data leakage",
    4: "Over-reliance / automation bias",
    5: "Consistency of outputs",
    6: "Failure and escalation behaviour",
    7: "Hallucinations and factual errors",
    8: "Adversarial or unexpected inputs",
}


@dataclass
class Finding:
    test_no: int
    probe: str
    tested: str
    expected: str
    actual: str
    passed: bool
    severity: str  # severity if the probe fails
    details: dict = field(default_factory=dict)

    @property
    def test_name(self):
        return CRITIC_TESTS[self.test_no]


def _cell(s, n=180):
    return str(s).replace("|", "/").replace("\n", " ")[:n]


def write_reports(findings, out_dir, system_name, metrics) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    rows = sorted(findings, key=lambda f: (f.passed, SEVERITY_ORDER.get(f.severity, 9), f.test_no))

    with open(os.path.join(out_dir, "findings.json"), "w", encoding="utf-8") as f:
        json.dump({"system": system_name, "metrics": metrics,
                   "findings": [dict(asdict(r), test_name=r.test_name) for r in rows]},
                  f, ensure_ascii=False, indent=2, default=str)

    with open(os.path.join(out_dir, "findings.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["status", "severity", "test_no", "test_name", "probe", "tested", "expected", "actual"])
        for r in rows:
            w.writerow(["PASS" if r.passed else "FAIL", "-" if r.passed else r.severity,
                        r.test_no, r.test_name, r.probe, r.tested, r.expected, r.actual])

    failed = [r for r in rows if not r.passed]
    lines = [f"# Tech critic report: {system_name}", "",
             f"Probes run: {len(rows)} | Passed: {len(rows) - len(failed)} | Failed: {len(failed)}", "",
             "## Metrics", ""]
    lines += [f"- **{k}**: {v}" for k, v in metrics.items()]
    lines += ["", "## Summary by critic test", "",
              "| # | Test | Probes | Failed | Worst severity |", "| --- | --- | --- | --- | --- |"]
    for no, name in CRITIC_TESTS.items():
        t = [r for r in rows if r.test_no == no]
        tf = [r for r in t if not r.passed]
        worst = min((r.severity for r in tf), key=lambda s: SEVERITY_ORDER.get(s, 9), default="-")
        lines.append(f"| {no} | {name} | {len(t)} | {len(tf)} | {worst} |")
    lines += ["", "## Failed probes (ordered by severity)", ""]
    if not failed:
        lines.append("None.")
    else:
        lines += ["| Severity | Test | Probe | Tested | Expected | Actual |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for r in failed:
            lines.append(f"| {r.severity} | {r.test_no}. {r.test_name} | {_cell(r.probe)} | "
                         f"{_cell(r.tested)} | {_cell(r.expected)} | {_cell(r.actual)} |")
    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return {"total": len(rows), "failed": len(failed),
            "failed_high": sum(1 for r in failed if r.severity in ("Critical", "High"))}
