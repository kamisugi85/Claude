"""Compliance QA: checks the creative/job content, not the pixels - PR
disclosure present when required, CTA present, no banned words in narration
or subtitles, and that no shot prompt asked the video model to render text
itself (that boundary is also enforced at schema-validation time in
ShotJob, this is a second, job-level check so it survives even if a shot
was hand-edited after validation).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import re

from production.schemas.models import ComplianceQAConfig, FORBIDDEN_PROMPT_PATTERNS, ProductionJob


@dataclass
class QAFinding:
    check: str
    passed: bool
    detail: str = ""


@dataclass
class ComplianceQAReport:
    passed: bool
    findings: list[QAFinding] = field(default_factory=list)


def run_compliance_qa(job: ProductionJob) -> ComplianceQAReport:
    cfg: ComplianceQAConfig = job.qa.compliance
    findings: list[QAFinding] = []

    if cfg.require_pr_disclosure:
        ok = job.creative.pr_disclosure.required and bool(job.creative.pr_disclosure.text.strip())
        findings.append(QAFinding("pr_disclosure_present", ok, job.creative.pr_disclosure.text))

    if cfg.require_cta:
        findings.append(QAFinding("cta_present", bool(job.creative.cta.strip()), job.creative.cta))

    full_text = " ".join([job.creative.hook, job.creative.narration_script, job.creative.cta])
    hits = [w for w in cfg.banned_words if w and w.lower() in full_text.lower()]
    findings.append(QAFinding("no_banned_words", not hits, f"hits: {hits}" if hits else ""))

    if cfg.forbid_model_rendered_text:
        offending = []
        for shot in job.shots:
            for pat in FORBIDDEN_PROMPT_PATTERNS:
                if re.search(pat, shot.video_prompt, re.IGNORECASE):
                    offending.append((shot.shot_id, pat))
        findings.append(QAFinding(
            "no_model_rendered_text_requested", not offending,
            f"offending shots: {offending}" if offending else "",
        ))

    passed = all(f.passed for f in findings)
    return ComplianceQAReport(passed=passed, findings=findings)
