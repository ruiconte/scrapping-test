"""Sample a SMALL number of public comments from one post.

Data minimization (see spec section 9/22): we only care about aggregate
audience signals (do parents engage? is the content resonating with a
parenting audience?), never about individual commenters. So we collect
ONLY the comment text — never the commenter's username, profile, or any
other identifying detail — and cap the sample size hard.
"""
from __future__ import annotations

import re

from playwright.sync_api import Page

from config import MAX_COMMENTS_PER_POST
from utils.logging import get_logger

log = get_logger()

_RELATIVE_TIME_RE = re.compile(r"^\d+[hdwmy]$", re.IGNORECASE)
_NON_COMMENT_LINES = {"reply", "see translation", "répondre", "voir la traduction"}


def sample_comments(page: Page, post_url: str, max_comments: int = MAX_COMMENTS_PER_POST) -> list[str]:
    """Assumes the page may or may not already be on post_url; navigates if needed."""
    if page.url.rstrip("/") != post_url.rstrip("/"):
        page.goto(post_url, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

    try:
        body_text = page.locator("body").inner_text(timeout=4000)
    except Exception:
        return []

    lines = body_text.split("\n")

    comments_section_idx = next(
        (i for i, l in enumerate(lines) if l.strip().lower() in ("for you", "most relevant", "les plus pertinents")),
        None,
    )
    if comments_section_idx is None:
        return []

    samples: list[str] = []
    for i in range(comments_section_idx, len(lines) - 1):
        if not _RELATIVE_TIME_RE.match(lines[i].strip()):
            continue
        candidate = lines[i + 1].strip()
        low = candidate.lower()
        if not candidate or low in _NON_COMMENT_LINES or _RELATIVE_TIME_RE.match(candidate):
            continue
        if re.match(r"^view all|^voir les", low):
            continue
        samples.append(candidate)
        if len(samples) >= max_comments:
            break

    log.info(f"[COMMENTS] Sampled {len(samples)} comment(s) from {post_url} (usernames not collected)")
    return samples
