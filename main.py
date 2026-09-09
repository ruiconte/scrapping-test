"""CLI entry point for the Fableya prospector.

Built incrementally phase by phase. Run `python main.py <command>`.
"""
from __future__ import annotations

import argparse
import sys

from utils.logging import get_logger

log = get_logger()


def cmd_login(_args):
    from browser.browser import InstagramSession, SecurityStopError

    session = InstagramSession()
    session.start()
    try:
        ok = session.ensure_logged_in()
        if ok:
            log.info("[SESSION] Success. Session persisted at browser_profile/.")
        else:
            log.info("[SESSION] Login not confirmed within the wait window.")
    except SecurityStopError as exc:
        log.info(f"[ERROR] {exc}")
    finally:
        session.stop()


def cmd_search(args):
    from browser.browser import InstagramSession, SecurityStopError
    from instagram.discovery import search_accounts

    session = InstagramSession()
    session.start()
    try:
        if not session.ensure_logged_in():
            log.info("[ERROR] Not logged in, aborting search.")
            return
        usernames = search_accounts(session.page, args.query, max_results=args.limit)
        print("\n--- RESULTS ---")
        for u in usernames:
            print(f"https://www.instagram.com/{u}/")
    except SecurityStopError as exc:
        log.info(f"[ERROR] {exc}")
    finally:
        session.stop()


def cmd_profile(args):
    import json

    from browser.browser import InstagramSession, SecurityStopError
    from instagram.profile_extractor import extract_profile

    session = InstagramSession()
    session.start()
    try:
        if not session.ensure_logged_in():
            log.info("[ERROR] Not logged in, aborting.")
            return
        data = extract_profile(session.page, args.username)
        print("\n--- EXTRACTED PROFILE ---")
        print(json.dumps(data, indent=2, ensure_ascii=False))
    except SecurityStopError as exc:
        log.info(f"[ERROR] {exc}")
    finally:
        session.stop()


def cmd_gemini_test(args):
    from instagram.profile_extractor import extract_profile
    from intelligence.qualification import run_stage1
    from browser.browser import InstagramSession, SecurityStopError

    session = InstagramSession()
    session.start()
    try:
        if not session.ensure_logged_in():
            log.info("[ERROR] Not logged in, aborting.")
            return
        profile = extract_profile(session.page, args.username)
    except SecurityStopError as exc:
        log.info(f"[ERROR] {exc}")
        return
    finally:
        session.stop()

    if profile.get("status") != "OK":
        log.info(f"[ERROR] Profile not usable (status={profile.get('status')})")
        return

    result, data_hash = run_stage1(profile)
    print("\n--- GEMINI STAGE 1 RESULT ---")
    print(f"score: {result.preliminary_relevance_score}")
    print(f"decision: {result.decision.value}")
    print(f"reason: {result.reason}")
    print(f"data_hash: {data_hash}")


def cmd_posts(args):
    import json

    from browser.browser import InstagramSession, SecurityStopError
    from instagram.post_extractor import extract_recent_posts

    session = InstagramSession()
    session.start()
    try:
        if not session.ensure_logged_in():
            log.info("[ERROR] Not logged in, aborting.")
            return
        posts = extract_recent_posts(session.page, args.username, max_posts=args.limit)
        print("\n--- EXTRACTED POSTS ---")
        print(json.dumps(posts, indent=2, ensure_ascii=False))
    except SecurityStopError as exc:
        log.info(f"[ERROR] {exc}")
    finally:
        session.stop()


def cmd_comments(args):
    from browser.browser import InstagramSession, SecurityStopError
    from instagram.comment_sampler import sample_comments

    session = InstagramSession()
    session.start()
    try:
        if not session.ensure_logged_in():
            log.info("[ERROR] Not logged in, aborting.")
            return
        samples = sample_comments(session.page, args.post_url, max_comments=args.limit)
        print("\n--- SAMPLED COMMENTS (text only, no usernames) ---")
        for c in samples:
            print(f"- {c}")
    except SecurityStopError as exc:
        log.info(f"[ERROR] {exc}")
    finally:
        session.stop()


def cmd_qualify(args):
    from browser.browser import InstagramSession, SecurityStopError
    from instagram.profile_extractor import extract_profile
    from intelligence.qualification import run_stage1
    from storage import database as db

    db.init_db()

    session = InstagramSession()
    session.start()
    try:
        if not session.ensure_logged_in():
            log.info("[ERROR] Not logged in, aborting.")
            return

        _id, created = db.upsert_discovered(
            args.username, f"https://www.instagram.com/{args.username}/", "manual-cli"
        )
        log.info(f"[DB] @{args.username} → {'inserted' if created else 'already known, deduped'} (id={_id})")

        profile = extract_profile(session.page, args.username)
        if profile.get("status") != "OK":
            log.info(f"[SKIP] @{args.username} → status={profile.get('status')}")
            return

        db.update_profile_fields(args.username, {
            k: v for k, v in profile.items()
            if k in ("display_name", "bio", "followers", "following", "post_count",
                      "external_url", "account_category")
        })

        result, data_hash = run_stage1(profile)
        db.save_stage1_result(args.username, result.preliminary_relevance_score, result.decision.value, data_hash)

        row = db.get_prospect(args.username)
        print("\n--- DB ROW AFTER QUALIFY ---")
        for k in ("username", "followers", "preliminary_score", "status", "date_discovered", "date_last_checked"):
            print(f"{k}: {row.get(k)}")
    except SecurityStopError as exc:
        log.info(f"[ERROR] {exc}")
    finally:
        session.stop()


def cmd_discover(args):
    from pipeline import run_discovery_session

    keywords = args.keywords.split(",") if args.keywords else None
    run_discovery_session(max_profiles=args.max_profiles, seed_keywords=keywords)


def cmd_draft_outreach(args):
    from browser.browser import InstagramSession, SecurityStopError
    from instagram.outreach import get_message_for_profile, open_dm_with_draft, send_current_draft
    from storage import database as db

    db.init_db()
    profile = db.get_prospect(args.username)
    if not profile:
        log.info(f"[ERROR] @{args.username} not found in database. Run 'qualify' first.")
        return

    log.info(f"[OUTREACH] @{args.username} → detected language: {profile.get('language') or 'UNKNOWN'}")
    message = get_message_for_profile(profile, args.message)

    session = InstagramSession()
    session.start()
    try:
        if not session.ensure_logged_in():
            log.info("[ERROR] Not logged in, aborting.")
            return
        ok = open_dm_with_draft(session.page, args.username, message)
        if not ok:
            return
        db.save_outreach_message(args.username, message)
        print(f"\n--- MESSAGE READY FOR @{args.username} ---\n{message}\n")
        if args.send:
            reply = input("Type 'send' to actually send this message now, anything else to leave it as a draft: ").strip().lower()
            if reply == "send":
                send_current_draft(session.page, args.username)
                db.mark_outreach_sent(args.username)
                print(f"Sent to @{args.username}.")
            else:
                print("Left as a draft in the composer. Nothing was sent.")
        else:
            print("Draft ready in the DM composer. Review it in the browser and press send yourself\n(or re-run with --send to be prompted to confirm sending from here).")
    except SecurityStopError as exc:
        log.info(f"[ERROR] {exc}")
    finally:
        session.stop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fableya-prospector")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="Open Instagram and verify/establish a persistent login session.")

    p_search = sub.add_parser("search", help="Search Instagram for accounts matching a keyword.")
    p_search.add_argument("query", help="Search keyword, e.g. 'toddler activities'")
    p_search.add_argument("--limit", type=int, default=15)

    p_profile = sub.add_parser("profile", help="Extract Stage-1 data for one profile.")
    p_profile.add_argument("username")

    p_gemini_test = sub.add_parser("gemini-test", help="Extract one profile and run Gemini Stage 1 on it.")
    p_gemini_test.add_argument("username")

    p_posts = sub.add_parser("posts", help="Extract recent posts for one profile.")
    p_posts.add_argument("username")
    p_posts.add_argument("--limit", type=int, default=6)

    p_comments = sub.add_parser("comments", help="Sample comments from one post URL.")
    p_comments.add_argument("post_url")
    p_comments.add_argument("--limit", type=int, default=8)

    p_qualify = sub.add_parser("qualify", help="Discover+extract+Stage1+persist one profile to SQLite.")
    p_qualify.add_argument("username")

    p_discover = sub.add_parser("discover", help="Run an autonomous discovery/qualification session.")
    p_discover.add_argument("--keywords", type=str, default=None, help="Comma-separated seed keywords.")
    p_discover.add_argument("--max-profiles", type=int, default=5)

    p_draft = sub.add_parser("draft-outreach", help="Open a prospect's DM composer with a pre-filled message (you send it).")
    p_draft.add_argument("username")
    p_draft.add_argument("--message", required=True, help="Template, e.g. 'Hi {display_name}, ...'")
    p_draft.add_argument("--send", action="store_true",
                          help="After drafting, show the message and prompt you to type 'send' to confirm sending it now.")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "login":
        cmd_login(args)
    elif args.command == "search":
        cmd_search(args)
    elif args.command == "profile":
        cmd_profile(args)
    elif args.command == "gemini-test":
        cmd_gemini_test(args)
    elif args.command == "posts":
        cmd_posts(args)
    elif args.command == "comments":
        cmd_comments(args)
    elif args.command == "qualify":
        cmd_qualify(args)
    elif args.command == "discover":
        cmd_discover(args)
    elif args.command == "draft-outreach":
        cmd_draft_outreach(args)


if __name__ == "__main__":
    sys.exit(main())
