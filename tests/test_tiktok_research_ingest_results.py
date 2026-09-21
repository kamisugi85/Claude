import json

from tiktok_research.ingest_results import dedupe_posts, ingest, load_work_results, validate_post_result


def make_post(**overrides):
    post = {
        "program_id": "s000001",
        "search_query": "保育士 辞めたい",
        "video_url": "https://www.tiktok.com/@x/video/1",
        "video_id": "1",
        "view_count": 1000,
        "like_count": 50,
        "comment_count": 5,
    }
    post.update(overrides)
    return post


def test_validate_post_result_flags_missing_required_keys():
    incomplete = {"program_id": "s000001"}
    missing = validate_post_result(incomplete)
    assert "search_query" in missing
    assert "video_url" in missing
    assert "program_id" not in missing


def test_dedupe_posts_by_video_id():
    posts = [make_post(video_id="1"), make_post(video_id="1"), make_post(video_id="2")]
    deduped = dedupe_posts(posts)
    assert len(deduped) == 2


def test_load_work_results_rejects_records_missing_required_keys(tmp_path):
    result_path = tmp_path / "work_result.json"
    result_path.write_text(json.dumps({
        "job_id": "job-1",
        "posts": [make_post(), {"program_id": "s000001"}],
    }), encoding="utf-8")

    valid, rejected = load_work_results([str(result_path)])
    assert len(valid) == 1
    assert len(rejected) == 1
    assert "missing_required_keys" in rejected[0]["reason"]


def test_load_work_results_reports_missing_files_without_raising():
    valid, rejected = load_work_results(["/nonexistent/path.json"])
    assert valid == []
    assert rejected[0]["reason"] == "file_not_found_or_unreadable"


def test_ingest_writes_deduped_output_and_reports_counts(tmp_path):
    result_path = tmp_path / "work_result.json"
    result_path.write_text(json.dumps({
        "job_id": "job-1",
        "posts": [make_post(video_id="1"), make_post(video_id="1")],
    }), encoding="utf-8")
    output_path = str(tmp_path / "observed.json")

    result = ingest([str(result_path)], output_path)

    assert result["ingested_count"] == 1
    assert result["duplicate_count"] == 1
    saved = json.load(open(output_path, encoding="utf-8"))
    assert saved["count"] == 1
