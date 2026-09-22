"""python -m production.cli render CLAUDE-D01
python -m production.cli regen-shot CLAUDE-D01 shot-02
python -m production.cli qa CLAUDE-D01
"""
from __future__ import annotations

import argparse
import sys

from production.pipeline import load_job, mark_shot_for_regen, regen_shot, render
from production.qa.compliance import run_compliance_qa
from production.qa.technical import run_technical_qa


def cmd_render(args: argparse.Namespace) -> int:
    try:
        mp4_path = render(args.job_id)
    except RuntimeError as e:
        print(f"RENDER FAILED: {e}", file=sys.stderr)
        return 1
    print(f"OK: {mp4_path}")
    return 0


def cmd_regen_shot(args: argparse.Namespace) -> int:
    mark_shot_for_regen(args.job_id, args.shot_id)
    regen_shot(args.job_id, args.shot_id)
    job = load_job(args.job_id)
    shot = job.shot(args.shot_id)
    print(f"{args.shot_id}: status={shot.status} provider={shot.assigned_provider} path={shot.output_path}")
    return 0 if shot.status == "success" else 1


def cmd_qa(args: argparse.Namespace) -> int:
    from pathlib import Path
    job = load_job(args.job_id)
    compliance = run_compliance_qa(job)
    print("Compliance QA:", "PASS" if compliance.passed else "FAIL")
    for f in compliance.findings:
        print(f"  [{'ok' if f.passed else 'FAIL'}] {f.check} {f.detail}")
    if job.output.mp4_path:
        tech = run_technical_qa(Path(job.output.mp4_path), job.qa.technical)
        print("Technical QA:", "PASS" if tech.passed else "FAIL")
        for f in tech.findings:
            print(f"  [{'ok' if f.passed else 'FAIL'}] {f.check} {f.detail}")
    else:
        print("Technical QA: skipped (no rendered mp4 yet)")
    return 0 if (compliance.passed and (not job.output.mp4_path or tech.passed)) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m production.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p_render = sub.add_parser("render", help="run the full pipeline for a job")
    p_render.add_argument("job_id")
    p_render.set_defaults(func=cmd_render)

    p_regen = sub.add_parser("regen-shot", help="regenerate a single NG shot")
    p_regen.add_argument("job_id")
    p_regen.add_argument("shot_id")
    p_regen.set_defaults(func=cmd_regen_shot)

    p_qa = sub.add_parser("qa", help="run QA checks for a job without rendering")
    p_qa.add_argument("job_id")
    p_qa.set_defaults(func=cmd_qa)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
