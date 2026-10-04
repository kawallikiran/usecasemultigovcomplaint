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


def resolve_ai(cfg, profile=None):
    """For AI test sets: pick the AI service (named profile, AIGP_AI_PROFILE, or the first ready one),
    apply it, and give it its own report folder. Returns (cfg, profile_name or None, message)."""
    from .common.ai_profiles import load_profiles, profile_status, apply_profile
    profiles = load_profiles(ROOT)
    wanted = profile or os.environ.get("AIGP_AI_PROFILE")
    names = [wanted] if wanted else list(profiles)
    for n in names:
        if n in profiles:
            ok, msg = profile_status(profiles[n], cfg)
            if ok:
                c = apply_profile(cfg, profiles[n])
                c["output_dir"] = f"{cfg['output_dir']}_{n}"
                return c, n, msg
            if wanted:
                return cfg, None, f"AI service '{n}' not ready: {msg}"
    return cfg, None, (f"Unknown AI service '{wanted}'." if wanted and wanted not in profiles
                       else "No AI service is ready (see config/ai_providers.yaml and .env.example).")


def run_suite(cfg_path, quiet=False, profile=None):
    cfg = load_config(cfg_path)
    if cfg.get("engine") == "ai":
        cfg, used, msg = resolve_ai(cfg, profile)
        if not used:
            if not quiet:
                print(f"\n=== skipped {os.path.basename(cfg_path)}: {msg}")
            return {"total": 0, "failed": 0, "failed_high": 0, "skipped": msg}, 0
    sysm, kit = build(cfg)
    findings, metrics = run_critic(kit, cfg)
    summary = write_reports(findings, cfg["output_dir"], kit.system_name, metrics)
    if not quiet:
        print(f"\n=== {kit.system_name} | {os.path.basename(cfg_path)} | engine {sysm.engine_label} ===")
        print(f"accuracy {metrics['baseline_accuracy']} | probes {summary['total']} | failed {summary['failed']}")
        for f in sorted([f for f in findings if not f.passed], key=lambda f: f.severity):
            print(f"  FAIL [{f.severity}] {f.test_no}. {f.test_name} :: {f.probe} -> {f.actual[:100]}")
        print(f"reports: {cfg['output_dir']}")
    gate = cfg.get("critic", {}).get("fail_on_high", False)
    return summary, (1 if gate and summary["failed_high"] else 0)


EW_CONFIG = os.path.join(ROOT, "config", "early_warning.yaml")


def early_warning_cmd(cmd, quiet=False):
    from . import early_warning as ew
    from .ew_datagen import generate
    from .system import GrievanceRouter
    cfg = load_config(EW_CONFIG)
    if cmd == "ew-generate" or not os.path.exists(cfg["dataset"]):
        n = generate(cfg)
        if not quiet:
            print(f"Generated {n} synthetic complaints -> {cfg['dataset']}")
        if cmd == "ew-generate":
            return 0
    rows, items, groups, alerts, findings = ew.run(cfg, GrievanceRouter(cfg))
    ew.write_outputs(cfg, items, groups, alerts, findings)
    if not quiet:
        print(f"\n=== Early-warning layer | {len(items)} complaints | {len(alerts)} alert(s) ===")
        for al in alerts:
            print("  " + " | ".join(ew.alert_card(al, cfg)[0]))
        for f in findings:
            if not f.passed:
                print(f"  CHECK FAILED [{f.severity}] {f.probe} -> {f.actual[:100]}")
        print(f"reports: {cfg['output_dir']}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="grievdesk")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--config", required=True)
    r.add_argument("--ai-profile", help="AI service from config/ai_providers.yaml (for AI test sets)")
    al = sub.add_parser("all")
    al.add_argument("--ai-profile")
    sub.add_parser("ai-status", help="list AI services and whether each is ready")
    sub.add_parser("early-warning", help="group complaints into district alerts and run the early-warning checks")
    sub.add_parser("ew-generate", help="rebuild the synthetic early-warning data (500 complaints, 62 villages)")
    d = sub.add_parser("demo")
    d.add_argument("--text", default="No water from the handpump for a week.")
    d.add_argument("--config", default=os.path.join(ROOT, "config", "grievance.yaml"))
    a = ap.parse_args(argv)
    if a.cmd == "run":
        return run_suite(a.config, profile=a.ai_profile)[1]
    if a.cmd in ("early-warning", "ew-generate"):
        return early_warning_cmd(a.cmd)
    if a.cmd == "ai-status":
        from .common.ai_profiles import all_status
        for name, label, ok, msg in all_status(ROOT):
            print(f"{'READY ' if ok else 'not ready'}  {name:18} {label}  ({msg})")
        return 0
    if a.cmd == "demo":
        from .system import GrievanceRouter
        print(json.dumps(GrievanceRouter(load_config(a.config)).process({"id": "DEMO", "text": a.text}),
                         indent=2, ensure_ascii=False))
        return 0
    cfgs = [p for p in sorted(glob.glob(os.path.join(ROOT, "config", "*.yaml")))
            if os.path.basename(p) not in ("ai_providers.yaml", "early_warning.yaml")]
    code = max(run_suite(p, profile=a.ai_profile)[1] for p in cfgs)
    early_warning_cmd("early-warning")
    return code


if __name__ == "__main__":
    sys.exit(main())
