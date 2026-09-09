"""Thin wrapper around the Gemini API (google-genai SDK) for structured
prospect qualification calls.

Never logs or prints the API key. Model name is configurable via
GEMINI_MODEL in .env.
"""
from __future__ import annotations

from typing import TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel

from config import GEMINI_API_KEY, GEMINI_MODEL
from utils.logging import get_logger
from utils.retry import retry

log = get_logger()

T = TypeVar("T", bound=BaseModel)

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Add it to your local .env file (see .env.example)."
            )
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


@retry(times=3, delay_seconds=3.0, exceptions=(Exception,))
def generate_structured(system_instruction: str, user_payload: str, response_schema: type[T]) -> T:
    """Send a prompt + structured JSON payload to Gemini and return a
    validated instance of response_schema."""
    client = _get_client()
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=user_payload,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
            response_schema=response_schema,
            temperature=0.2,
        ),
    )
    parsed = response.parsed
    if parsed is None:
        raise ValueError(f"Gemini did not return valid structured output: {response.text[:300]}")
    log.info("[GEMINI] analysis completed")
    return parsed
