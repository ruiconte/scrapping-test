"""Orchestrates one autonomous discovery/qualification session.

Graph-like exploration: seed keywords -> search results enqueued -> each
candidate is extracted + Stage-1 qualified -> a promising candidate's
related accounts are enqueued with a HIGHER priority (a good profile is a
stronger seed); a rejected candidate contributes no new candidates at all
(its branch is implicitly deprioritized by simply not being explored).
"""
from __future__ import annotations
from instagram.outreach import open_dm_with_draft
from playwright.sync_api import Page

from browser.browser import InstagramSession, SecurityStopError
from config import (
    ANALYSIS_VERSION,
    FOLLOWERS_ACCEPTABLE_MAX,
    FOLLOWERS_ACCEPTABLE_MIN,
    MAX_COMMENTS_PER_POST,
    MAX_POSTS_STAGE2,
    MAX_PROFILES_PER_SESSION,
    MAX_RELATED_PER_PROFILE,
    PAUSE_FLAG,
)
from instagram.comment_sampler import sample_comments
from instagram.discovery import search_accounts
from instagram.post_extractor import extract_recent_posts
from instagram.profile_extractor import extract_profile
from intelligence.qualification import run_stage1, run_stage2
from storage import database as db
from utils.logging import get_logger
from utils.normalization import extract_hashtags
from instagram.outreach import get_message_for_profile

log = get_logger()

_PROFILE_FIELD_KEYS = (
    "display_name", "bio", "followers", "following", "post_count",
    "external_url", "account_category",
)


def seed_queue_from_keywords(page, keywords: list[str], max_results_per_keyword: int = 10) -> int:
    added = 0
    for kw in keywords:
        try:
            usernames = search_accounts(page, kw, max_results=max_results_per_keyword)
        except Exception as exc:
            log.info(f"[ERROR] Search failed for '{kw}': {exc}")
            continue
        for u in usernames:
            if db.enqueue(u, priority=1.0, discovery_source="search", discovery_keyword=kw):
                added += 1
    return added


def _followers_in_acceptable_range(followers) -> bool:
    if followers is None:
        return True  # never reject purely for missing data
    return FOLLOWERS_ACCEPTABLE_MIN <= followers <= FOLLOWERS_ACCEPTABLE_MAX


def run_stage2_for_prospect(page, username: str, profile: dict) -> None:
    prospect = db.get_prospect(username)
    if not prospect:
        return

    posts = extract_recent_posts(page, username, max_posts=MAX_POSTS_STAGE2)
    if not posts:
        log.info(f"[DEEP] @{username} → no posts available, skipping deep analysis")
        return

    # Comments sampled from only a couple of posts — aggregate audience
    # signal, not a full scrape (data minimization).
    for p in posts[:2]:
        p["sample_comments"] = sample_comments(page, p["post_url"], max_comments=MAX_COMMENTS_PER_POST)

    post_ids = db.save_posts(prospect["id"], posts)
    for post_id, p in zip(post_ids, posts):
        if p.get("sample_comments"):
            db.save_comments(post_id, p["sample_comments"])

    result, data_hash = run_stage2(profile, posts)
    db.save_stage2_result(username, result.model_dump(mode="json"), data_hash, ANALYSIS_VERSION)
    if result.recommended_action.value == "REJECT":
        log.info(f"[SKIP] @{username} → rejected after deep analysis")
    else:
        log.info(f"[KEEP] @{username} → {result.recommended_action.value}")


def process_one_candidate(page, item: dict) -> str:
    """Returns the outcome: 'SKIPPED' | 'REJECTED' | 'KEPT'."""
    username = item["username"]
    db.upsert_discovered(
        username,
        f"https://www.instagram.com/{username}/",
        item.get("discovery_source", ""),
        item.get("discovery_keyword"),
        item.get("discovered_from_username"),
    )

    profile = extract_profile(page, username)
    if profile.get("status") != "OK":
        log.info(f"[SKIP] @{username} → {profile.get('status')}")
        db.mark_queue_status(username, "SKIPPED")
        return "SKIPPED"

    db.update_profile_fields(username, {k: v for k, v in profile.items() if k in _PROFILE_FIELD_KEYS})

    result, data_hash = run_stage1(profile)
    db.save_stage1_result(username, result.preliminary_relevance_score, result.decision.value, data_hash)
    db.mark_queue_status(username, "DONE")

    if result.decision.value == "REJECT":
        log.info(f"[SKIP] @{username} → unrelated account")
        return "REJECTED"

    log.info(f"[KEEP] @{username} → {result.decision.value}")

    if result.decision.value == "DEEP_ANALYZE":
        try:
            run_stage2_for_prospect(page, username, profile)
        except Exception as exc:
            log.info(f"[ERROR] Stage-2 deep analysis failed for @{username}: {exc}")

    if result.decision.value == "DEEP_ANALYZE" and _followers_in_acceptable_range(profile.get("followers")):
        # Graph growth: a strongly relevant profile's OWN hashtags become new
        # search seeds, discovering accounts in the same niche. (We tried
        # scraping a profile page's outbound links as "related accounts",
        # but that surfaces persistent page chrome rather than genuine
        # related content — see spawned cleanup task for a proper fix.)
        hashtags = extract_hashtags(profile.get("bio") or "")[:2]
        boost = 2.0 if result.preliminary_relevance_score >= 80 else 1.5
        new_related = 0
        for tag in hashtags:
            try:
                candidates = search_accounts(page, tag, max_results=MAX_RELATED_PER_PROFILE)
            except Exception as exc:
                log.info(f"[ERROR] Hashtag search failed for #{tag} (from @{username}): {exc}")
                continue
            for c in candidates:
                if db.enqueue(c, priority=boost, discovery_source="related-hashtag",
                               discovery_keyword=tag, discovered_from_username=username):
                    new_related += 1
        if new_related:
            log.info(f"[RELATED] @{username} → discovered {new_related} new candidates")

    return "KEPT"


def run_discovery_session(
    max_profiles: int = MAX_PROFILES_PER_SESSION,
    seed_keywords: list[str] | None = None,
) -> dict:
    db.init_db()
    recovered = db.recover_stale_processing()
    if recovered:
        log.info(f"[SESSION] Recovered {recovered} candidate(s) stuck from a previous unclean shutdown.")

    session_id = db.start_session_stats()
    session = InstagramSession()
    session.start()
    stats = {"processed": 0, "kept": 0, "rejected": 0, "skipped": 0, "errors": 0}
    try:
        if not session.ensure_logged_in():
            log.info("[ERROR] Not logged in, aborting session.")
            return stats

        if seed_keywords:
            added = seed_queue_from_keywords(session.page, seed_keywords)
            log.info(f"[SESSION] Seeded {added} new candidate(s) from {len(seed_keywords)} keyword(s).")

        paused_logged = False
        while stats["processed"] < max_profiles:
            if PAUSE_FLAG.exists():
                if not paused_logged:
                    log.info("[SESSION] Paused. Waiting to resume...")
                    paused_logged = True
                time.sleep(2)
                continue
            paused_logged = False

            item = db.pop_next_from_queue()
            if not item:
                log.info("[SESSION] Discovery queue is empty.")
                break
            try:
                outcome = process_one_candidate(session.page, item)
                stats[outcome.lower()] = stats.get(outcome.lower(), 0) + 1
            except SecurityStopError:
                raise
            except Exception as exc:
                log.info(f"[ERROR] Failed processing @{item['username']}: {exc}")
                db.mark_queue_status(item["username"], "SKIPPED")
                stats["errors"] += 1
            stats["processed"] += 1
    except SecurityStopError as exc:
        log.info(f"[SESSION] STOPPED — security block detected: {exc}")
    finally:
        session.stop()
        db.finish_session_stats(session_id, stats)

    log.info(
        f"[SESSION] Done. processed={stats['processed']} kept={stats.get('kept', 0)} "
        f"rejected={stats.get('rejected', 0)} skipped={stats.get('skipped', 0)} errors={stats['errors']}"
    )
    pepito = page.url
    toto = pepito.rstrip("/").split("/")[-1]
    return toto
    open_dm_with_draft( Page, toto, get_message_for_profile())

    
    return stats
