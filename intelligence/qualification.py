"""Orchestrates Stage 1 / Stage 2 Gemini qualification, with hash-based
caching so we never re-send identical profile data to Gemini twice."""
from __future__ import annotations

import json

from config import BUSINESS_CONTEXT
from intelligence.gemini_client import generate_structured
from intelligence.prompts import STAGE1_INSTRUCTION, STAGE2_INSTRUCTION
from intelligence.schemas import Stage1Result, Stage2Result
from utils.logging import get_logger
from utils.normalization import stable_hash

log = get_logger()


def build_stage1_payload(profile: dict) -> dict:
    """Only include fields that are actually available (no invented data)."""
    fields = {}
    for key in (
        "username", "display_name", "bio", "followers", "following",
        "post_count", "account_category", "external_url",
    ):
        val = profile.get(key)
        if val is not None:
            fields[key] = val
    return {"business_context": BUSINESS_CONTEXT, "profile": fields}


def run_stage1(profile: dict) -> tuple[Stage1Result, str]:
    payload = build_stage1_payload(profile)
    data_hash = stable_hash(payload)
    payload_json = json.dumps(payload, ensure_ascii=False)

    log.info(f"[FAST] Analyzing @{profile.get('username')}...")
    result = generate_structured(STAGE1_INSTRUCTION, payload_json, Stage1Result)
    log.info(
        f"[FAST] @{profile.get('username')} → {result.preliminary_relevance_score}/100 → {result.decision.value}"
    )
    return result, data_hash


def build_stage2_payload(profile: dict, posts: list[dict]) -> dict:
    fields = build_stage1_payload(profile)
    trimmed_posts = []
    for p in posts:
        post_fields = {}
        for key in ("caption", "hashtags", "date", "likes", "comments_count"):
            val = p.get(key)
            if val is not None:
                post_fields[key] = val
        if p.get("sample_comments"):
            post_fields["sample_comments"] = p["sample_comments"]
        if post_fields:
            trimmed_posts.append(post_fields)
    if trimmed_posts:
        fields["profile"]["recent_posts"] = trimmed_posts
    return fields


def run_stage2(profile: dict, posts: list[dict]) -> tuple[Stage2Result, str]:
    payload = build_stage2_payload(profile, posts)
    data_hash = stable_hash(payload)
    payload_json = json.dumps(payload, ensure_ascii=False)

    log.info(f"[DEEP] Analyzing @{profile.get('username')}...")
    result = generate_structured(STAGE2_INSTRUCTION, payload_json, Stage2Result)
    log.info(f"[DEEP] @{profile.get('username')} → {result.relevance_score}/100 → {result.recommended_action.value}")
    return result, data_hash
