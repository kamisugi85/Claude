from __future__ import annotations

from typing import List, Optional

from .utils import iso_now, read_json, write_json


def load_crawl_state(path: str) -> dict:
    return read_json(path, default={"last_completed_page": 0, "total_pages": None})


def save_crawl_state(path: str, last_completed_page: int, total_pages: Optional[int]) -> None:
    write_json(
        path,
        {
            "last_completed_page": last_completed_page,
            "total_pages": total_pages,
            "updated_at": iso_now(),
        },
    )


def plan_pages(state: dict, pages_per_run: int) -> List[int]:
    """Which page numbers to crawl this run, continuing from last_completed_page
    and wrapping back to page 1 once total_pages is known and reached, so a full
    catalog sweep completes over several runs and then starts re-checking from
    the top (picking up reward/condition changes on already-seen programs too).
    """
    total_pages = state.get("total_pages")
    start = state.get("last_completed_page", 0) + 1

    if not total_pages:
        return list(range(start, start + pages_per_run))

    pages = []
    p = start
    for _ in range(pages_per_run):
        if p > total_pages:
            p = 1
        pages.append(p)
        p += 1
    return pages
