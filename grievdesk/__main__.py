"""Command line for the multilingual grievance chatbot.

  python -m grievdesk run --config config/grievance.yaml   # 8 tech-critic tests on one set
  python -m grievdesk all                                  # every config in config/
  python -m grievdesk demo --text "Gaon mein 3 din se bijli nahi hai"
"""
import argparse
import glob
import json
import os
import sys

from .common.audit import AuditLog
from .common.config import load_config
from .common.report import write_reports
from .critic.runner import run_critic

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


def build(cfg):
    from .system import GrievanceRouter
    from .kit import GrievanceKit
    os.makedirs(cfg["output_dir"], exist_ok=True)
    audit_path = os.path.join(cfg["output_dir"], "audit_log.jsonl")
    if os.path.exists(audit_path):
        os.remove(audit_path)
    sysm = GrievanceRouter(cfg, AuditLog(audit_path))
    return sysm, GrievanceKit(sysm, cfg)


def run_suite(cfg_path, quiet=False):
    cfg = load_config(cfg_path)
    if cfg.get("engine") == "ai":
        from .common.ai_client import ai_status
        ready, msg = ai_status(cfg)
        if not ready:
            if not quiet:
                print(f"\n=== skipped {os.path.basename(cfg_path)}: {msg}")
            return {"total": 0, "failed": 0, "failed_high": 0, "skipped": msg}, 0
    sysm, kit = build(cfg)
    findings, metrics = run_critic(kit, cfg)
    summary = write_reports(findings, cfg["output_dir"], kit.system_name, metrics)
    if not quiet:
        print(f"\n=== {kit.system_name} | {os.path.basename(cfg_path)} ===")
        print(f"accuracy {metrics['baseline_accuracy']} | probes {summary['total']} | failed {summary['failed']}")
        for f in sorted([f for f in findings if not f.passed], key=lambda f: f.severity):
            print(f"  FAIL [{f.severity}] {f.test_no}. {f.test_name} :: {f.probe} -> {f.actual[:100]}")
        print(f"reports: {cfg['output_dir']}")
    gate = cfg.get("critic", {}).get("fail_on_high", False)
    return summary, (1 if gate and summary["failed_high"] else 0)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="grievdesk")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run").add_argument("--config", required=True)
    sub.add_parser("all")
    d = sub.add_parser("demo")
    d.add_argument("--text", default="No water from the handpump for a week.")
    d.add_argument("--config", default=os.path.join(ROOT, "config", "grievance.yaml"))
    a = ap.parse_args(argv)
    if a.cmd == "run":
        return run_suite(a.config)[1]
    if a.cmd == "demo":
        from .system import GrievanceRouter
        print(json.dumps(GrievanceRouter(load_config(a.config)).process({"id": "DEMO", "text": a.text}),
                         indent=2, ensure_ascii=False))
        return 0
    return max(run_suite(p)[1] for p in sorted(glob.glob(os.path.join(ROOT, "config", "*.yaml"))))


if __name__ == "__main__":
    sys.exit(main())
