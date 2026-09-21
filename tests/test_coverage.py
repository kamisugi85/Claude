from a8_automation.coverage import compute_field_coverage


def test_counts_only_parseable_numeric_fields():
    catalog = {
        "1": {"program_id": "1", "reward": "1000円", "epc": "12.3", "conversion_rate": "50%", "start_date": "2020年"},
        "2": {"program_id": "2", "reward": "", "epc": None, "conversion_rate": "-"},
    }
    report = compute_field_coverage(catalog)
    assert report["total"] == 2
    assert report["fields"]["reward(報酬額)"]["count"] == 1
    assert report["fields"]["epc"]["count"] == 1
    assert report["fields"]["start_date(新着判定用)"]["count"] == 1


def test_detail_and_conversion_action_and_sns_text_presence():
    catalog = {
        "1": {
            "program_id": "1",
            "detail_url": "u",
            "name": "n",
            "reward": "r",
            "epc": "e",
            "conversion_rate": "c",
            "category": "cat",
            "start_date": "d",
            "checked_at": "t",
            "成果条件": "WEB申込完了",
            "否認条件": "x",
        },
        "2": {"program_id": "2"},
    }
    report = compute_field_coverage(catalog)
    assert report["fields"]["detail_page_fetched"]["count"] == 1
    assert report["fields"]["成果条件(成果地点)"]["count"] == 1
    assert report["fields"]["sns_status(実データに基づく判定)"]["count"] == 1


def test_campaign_info_reported_as_unimplemented():
    report = compute_field_coverage({"1": {"program_id": "1"}})
    assert report["fields"]["campaign_info(キャンペーン/報酬アップ)"]["count"] == 0
    assert "note" in report["fields"]["campaign_info(キャンペーン/報酬アップ)"]


def test_empty_catalog_does_not_divide_by_zero():
    report = compute_field_coverage({})
    assert report["total"] == 0
    assert report["fields"]["epc"]["rate"] == 0.0
