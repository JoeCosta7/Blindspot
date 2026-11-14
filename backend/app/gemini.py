"""The one place that talks to Gemini: a prompt in, a validated Pydantic
object out. Callers own the timeout, the cache and what to do on failure."""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

from app.config import Settings

T = TypeVar("T", bound=BaseModel)


async def generate(prompt: str, schema: type[T], settings: Settings) -> T:
    """One structured-output call. Raises on any SDK, network or parsing failure."""
    # Imported here so the app starts, and tests run, without the SDK configured.
    from google import genai

    client = genai.Client(api_key=settings.gemini_api_key)
    config: dict[str, Any] = {"response_mime_type": "application/json", "response_schema": schema}
    if settings.gemini_thinking_level:
        config["thinking_config"] = {"thinking_level": settings.gemini_thinking_level}
    resp = await client.aio.models.generate_content(
        model=settings.gemini_model, contents=prompt, config=config
    )
    if isinstance(resp.parsed, schema):
        return resp.parsed
    return schema.model_validate_json(resp.text or "")


def describe_error(exc: BaseException) -> str:
    """A short reason for a status message. The SDK's API errors carry an HTTP
    code and a status such as UNAVAILABLE; anything else is named by its type."""
    code = getattr(exc, "code", None)
    status = getattr(exc, "status", None)
    if isinstance(code, int) and status:
        return f"HTTP {code} {status}"
    return type(exc).__name__
