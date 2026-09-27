"""Classify and meter image responses after a provider call has been billed."""

from typing import Any, Callable, Optional

from ..chat.token_context import MediaCallType
from .base import invalid_response_from
from .usage import record_image_usage


def metered_image_result(
    response: Any,
    *,
    usage: Any,
    image_url: Callable[[Any], Optional[str]],
    model_name: str,
    model_id: str,
    call_type: MediaCallType,
    image_count: Any,
    resolution: str,
) -> dict[str, Any]:
    """Record a billed response before reading its potentially malformed image."""
    result = {
        "image_url": None,
        "usage": usage,
        "request_id": getattr(response, "id", None),
    }
    record_image_usage(
        result,
        model_name=model_name,
        model_id=model_id,
        call_type=call_type,
        image_count=image_count,
        resolution=resolution,
    )
    try:
        result["image_url"] = image_url(response)
    except (TypeError, AttributeError, KeyError, IndexError) as error:
        raise invalid_response_from(error, "Invalid response format") from error
    return result
