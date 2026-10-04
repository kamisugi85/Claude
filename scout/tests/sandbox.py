"""Isolated scout/ copy for the tests: the code only, with a fresh Vault under a test-only key.

The live Vault, state/ (index, backlog, runs.jsonl, daily_metrics.jsonl), data/ and out/ are never copied,
so a test gives the same result in a fresh clone, in the 05:00 / 17:00 routines and after any extra run
(rescreen etc.). The profile is synthetic (no personal data); a test adds the jobs it needs itself.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_KEY = "scout-tests-only-not-the-vault-key"
NISA_RE = "NISA[^。？?\\n]{0,20}(経験|利用|使って|やって|されて|有無)|(経験|利用)[^。？?\\n]{0,20}NISA"
PROFILE = {
    "birth_year": 1993,
    "professional": {"experience": ["法人営業"], "qualifications": ["簿記2級", "FP2級"]},
    "personal_experience": ["NISA", "iDeCo"],
    "unconfirmed_skills": ["Webライター経験", "SEO経験"],
    "never_infer": ["Webライター経験"],
    "keywords_strong": ["NISA", "iDeCo", "簿記", "FP", "財務", "市場調査"],
    "keywords_title_only": ["副業", "投資", "お金"],
    "confirmed_facts": [{"fact": "NISAの利用経験がある", "source": "テスト用", "scope": "全案件で再利用",
                         "reuse": True, "question_re": NISA_RE, "profile_ref": "personal_experience[0]"}],
}


def make(profile=None):
    """A temporary scout/ with code only and an empty Vault (test key, synthetic profile)."""
    os.environ["SCOUT_VAULT_KEY"] = TEST_KEY  # subprocesses inherit it; the real key is never used
    tmp = tempfile.mkdtemp()
    shutil.copytree(SRC, tmp, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("tests", "data", "out", "state", "__pycache__"))
    os.makedirs(os.path.join(tmp, "state"))
    p = os.path.join(tmp, "profile.json")
    json.dump(PROFILE if profile is None else profile, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    subprocess.run([sys.executable, "pipeline.py", "init-vault", "--profile", p], cwd=tmp, check=True,
                   capture_output=True)
    return tmp
