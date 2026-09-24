"""python -m production.cli render CLAUDE-D01
python -m production.cli regen-shot CLAUDE-D01 shot-02
python -m production.cli qa CLAUDE-D01
python -m production.cli ingest-shot CLAUDE-D01 shot-02 /path/to/clip.mp4 --service invideo
"""
from __future__ import annotations

import argparse
import sys

from production.pipeline import (
    accept_shot,
    compute_cost_rollup,
    ingest_shot,
    load_job,
    mark_shot_for_regen,
    reject_shot,
    regen_shot,
    render,
)
from production.providers.registry import get_provider
from production.qa.compliance import run_compliance_qa
from production.qa.technical import run_technical_qa

_QUALITY_WARNINGS = {
    "placeholder_preview": "PLACEHOLDER PREVIEW - NOT publishable TikTok quality.",
    "free_tier_noncommercial_preview": (
        "FREE-TIER PREVIEW - real generated video, but commercial-use clearance "
        "is NOT confirmed (watermarked/personal-use-only unless proven otherwise). "
        "NOT publishable as-is."
    ),
}


def cmd_render(args: argparse.Namespace) -> int:
    try:
        mp4_path = render(args.job_id)
    except RuntimeError as e:
        print(f"RENDER FAILED: {e}", file=sys.stderr)
        return 1
    print(f"OK: {mp4_path}")

    job = load_job(args.job_id)
    warning = _QUALITY_WARNINGS.get(job.output.quality_tier)
    if warning:
        print("\n" + "=" * 70)
        print(f"WARNING: {warning}")
        for note in job.output.quality_notes:
            print(f"  - {note}")
        print("This MP4 exists to prove the pipeline/quality, not to publish.")
        print("=" * 70)
    return 0


def cmd_ingest_shot(args: argparse.Namespace) -> int:
    ingest_shot(
        args.job_id, args.shot_id, args.source_path, args.service,
        license_commercial_clear=args.commercial_clear,
        license_note=args.license_note or "",
    )
    job = load_job(args.job_id)
    shot = job.shot(args.shot_id)
    print(f"{args.shot_id}: ingested from {args.service} -> {shot.output_path}")
    print(f"  license_commercial_clear={shot.license_commercial_clear} ({shot.qa_notes})")
    return 0


def cmd_regen_shot(args: argparse.Namespace) -> int:
    mark_shot_for_regen(args.job_id, args.shot_id)
    regen_shot(args.job_id, args.shot_id)
    job = load_job(args.job_id)
    shot = job.shot(args.shot_id)
    print(f"{args.shot_id}: status={shot.status} provider={shot.assigned_provider} path={shot.output_path}")
    return 0 if shot.status == "success" else 1


def cmd_accept_shot(args: argparse.Namespace) -> int:
    accept_shot(args.job_id, args.shot_id)
    print(f"{args.shot_id}: accepted")
    return 0


def cmd_reject_shot(args: argparse.Namespace) -> int:
    reject_shot(args.job_id, args.shot_id, args.reason)
    print(f"{args.shot_id}: rejected ({args.reason}), marked needs_regen. "
          f"Run regen-shot to retry just this shot.")
    return 0


def cmd_estimate_cost(args: argparse.Namespace) -> int:
    job = load_job(args.job_id)
    shot = job.shot(args.shot_id)
    provider = get_provider(args.provider or job.provider_preferences[0])
    per_attempt = provider.estimate_cost_usd(shot)
    print(f"{args.shot_id} via {provider.name}: estimated_cost_usd per attempt = {per_attempt}")
    for attempts, label in [(1, "1回生成"), (1.5, "1.5回生成(平均)"), (2, "2回生成")]:
        print(f"  {label}: expected accepted-shot cost = {round(per_attempt * attempts, 4)} USD")
    avail = provider.check_availability()
    if not avail.available:
        print(f"\nNOTE: {provider.name} is not currently configured: {avail.reason}")
        for action in avail.required_user_actions:
            print(f"  - {action}")
    return 0


def cmd_cost_report(args: argparse.Namespace) -> int:
    job = load_job(args.job_id)
    rollup = compute_cost_rollup(job)
    for shot in sorted(job.shots, key=lambda s: s.order):
        print(f"{shot.shot_id}: attempts={shot.attempts} total_cost_usd={shot.total_cost_usd} "
              f"first_attempt_cost_usd={shot.first_attempt_cost_usd} "
              f"last_generation_time_sec={shot.last_generation_time_sec} "
              f"accepted={shot.accepted} rejection_reason={shot.rejection_reason}")
    print()
    for k, v in rollup.items():
        print(f"{k}: {v}")
    return 0


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

    p_ingest = sub.add_parser("ingest-shot", help="adopt a human-generated free-tier clip for one shot")
    p_ingest.add_argument("job_id")
    p_ingest.add_argument("shot_id")
    p_ingest.add_argument("source_path", help="local path to the clip downloaded from the free-tier service")
    p_ingest.add_argument("--service", required=True, help="e.g. invideo, dreamina")
    p_ingest.add_argument(
        "--commercial-clear", action="store_true",
        help="only pass this if you have confirmed the service's terms actually allow commercial use "
             "for this output - defaults to False (not confirmed) otherwise",
    )
    p_ingest.add_argument("--license-note", help="free-text note on what you confirmed/didn't confirm")
    p_ingest.set_defaults(func=cmd_ingest_shot)

    p_accept = sub.add_parser("accept-shot", help="record that a generated shot passed human/QA review")
    p_accept.add_argument("job_id")
    p_accept.add_argument("shot_id")
    p_accept.set_defaults(func=cmd_accept_shot)

    p_reject = sub.add_parser("reject-shot", help="record that a generated shot failed review; marks needs_regen")
    p_reject.add_argument("job_id")
    p_reject.add_argument("shot_id")
    p_reject.add_argument("reason", help="why it was rejected, e.g. 'hand distortion in frame 40'")
    p_reject.set_defaults(func=cmd_reject_shot)

    p_estimate = sub.add_parser("estimate-cost", help="estimate cost for a shot before calling any API")
    p_estimate.add_argument("job_id")
    p_estimate.add_argument("shot_id")
    p_estimate.add_argument("--provider", help="defaults to the job's first provider_preferences entry")
    p_estimate.set_defaults(func=cmd_estimate_cost)

    p_cost_report = sub.add_parser("cost-report", help="print per-shot and total cost/time so far")
    p_cost_report.add_argument("job_id")
    p_cost_report.set_defaults(func=cmd_cost_report)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
