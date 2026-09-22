from production.qa.compliance import run_compliance_qa
from production.schemas.models import ProductionJob


def test_claude_d01_passes_compliance_qa():
    job = ProductionJob.load("production/jobs/CLAUDE-D01.json")
    report = run_compliance_qa(job)
    assert report.passed, [f for f in report.findings if not f.passed]


def test_compliance_qa_fails_without_pr_disclosure():
    job = ProductionJob.load("production/jobs/CLAUDE-D01.json")
    job.creative.pr_disclosure.required = False
    job.creative.pr_disclosure.text = ""
    report = run_compliance_qa(job)
    assert not report.passed
    failing = {f.check for f in report.findings if not f.passed}
    assert "pr_disclosure_present" in failing


def test_compliance_qa_fails_on_banned_word():
    job = ProductionJob.load("production/jobs/CLAUDE-D01.json")
    job.creative.cta += " 絶対儲かる"
    report = run_compliance_qa(job)
    assert not report.passed
    failing = {f.check for f in report.findings if not f.passed}
    assert "no_banned_words" in failing
