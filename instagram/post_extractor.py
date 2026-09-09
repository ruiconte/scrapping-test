"""Extract recent posts (captions, hashtags, dates, engagement) for a
profile. Used only for Stage 2 (deep) qualification, on a small number of
promising candidates — never for every discovered account.
"""
from __future__ import annotations

import re
from typing import Optional

from playwright.sync_api import Page

from utils.logging import get_logger
from utils.normalization import extract_hashtags, parse_count, profile_url

log = get_logger()

_RELATIVE_TIME_RE = re.compile(r"^\d+[hdwmy]$", re.IGNORECASE)
_LIKE_LINE_RE = re.compile(r"^[\d.,\s]+\s*(likes?|j'?aime)$", re.IGNORECASE)
_COMMENTS_SUMMARY_RE = re.compile(r"(view all|voir les)\s+([\d.,\s]+)\s*(comments?|commentaires?)", re.IGNORECASE)


def get_recent_post_urls(page: Page, username: str, max_posts: int = 8) -> list[str]:
    page.goto(profile_url(username), wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    hrefs = page.eval_on_selector_all(
        "a[href*='/p/'], a[href*='/reel/']",
        "els => els.map(e => e.getAttribute('href'))",
    )
    seen, urls = set(), []
    for h in hrefs:
        if h and h not in seen:
            seen.add(h)
            urls.append(f"https://www.instagram.com{h}")
        if len(urls) >= max_posts:
            break
    log.info(f"[POSTS] @{username} → found {len(urls)} recent post(s) to inspect")
    return urls


def extract_post(page: Page, post_url: str, username: str) -> Optional[dict]:
    page.goto(post_url, wait_until="domcontentloaded")
    page.wait_for_timeout(2000)

    try:
        body_text = page.locator("body").inner_text(timeout=4000)
    except Exception:
        return None

    lines = [l for l in body_text.split("\n")]

    # Find the post's own caption block: second occurrence of the username,
    # followed by a non-breaking-space spacer line, followed by a relative
    # time marker ("8w", "2d"...). The caption runs until "See translation"
    # (or a hard cap) so we don't accidentally swallow the comment thread.
    caption = None
    start_idx = None
    username_hits = [i for i, l in enumerate(lines) if l.strip() == username]
    for idx in username_hits[1:] or username_hits:
        if idx + 2 < len(lines) and _RELATIVE_TIME_RE.match(lines[idx + 2].strip()):
            start_idx = idx + 3
            break

    likes = None
    comments_count = None
    caption_end_idx = start_idx
    if start_idx is not None:
        caption_lines = []
        for offset, line in enumerate(lines[start_idx:start_idx + 15]):
            stripped = line.strip()
            if stripped.lower() == "see translation":
                caption_end_idx = start_idx + offset
                break
            caption_lines.append(line)
        else:
            caption_end_idx = start_idx + len(caption_lines)
        caption = "\n".join(caption_lines).strip() or None

    # Engagement counts (post-level like/comment totals, if the creator
    # hasn't hidden them) only ever appear in the narrow zone between the
    # caption and the start of the comment thread. Searching the whole page
    # would pick up an individual COMMENT's own like count instead, which
    # would misrepresent it as the post's total.
    comments_section_idx = next(
        (i for i, l in enumerate(lines) if l.strip().lower() in ("for you", "most relevant", "les plus pertinents")),
        len(lines),
    )
    if caption_end_idx is not None:
        for line in lines[caption_end_idx:comments_section_idx]:
            stripped = line.strip()
            if _LIKE_LINE_RE.match(stripped) and likes is None:
                likes = parse_count(stripped)
            m = _COMMENTS_SUMMARY_RE.search(stripped)
            if m and comments_count is None:
                comments_count = parse_count(m.group(2))

    date = None
    try:
        times = page.eval_on_selector_all("time", "els => els.map(e => e.getAttribute('datetime'))")
        if times:
            date = times[0]
    except Exception:
        pass

    return {
        "post_url": post_url,
        "caption": caption,
        "hashtags": extract_hashtags(caption) if caption else [],
        "date": date,
        "likes": likes,
        "comments_count": comments_count,
    }


def extract_recent_posts(page: Page, username: str, max_posts: int = 8) -> list[dict]:
    urls = get_recent_post_urls(page, username, max_posts=max_posts)
    posts = []
    for url in urls:
        data = extract_post(page, url, username)
        if data and data.get("caption"):
            posts.append(data)
    log.info(f"[POSTS] @{username} → extracted {len(posts)} post(s) with usable captions")
    return posts
