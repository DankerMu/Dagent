from typing import Optional, overload

from ....model import ChatModelConfig, ModelConfig
from ....retry import chat_retry_budget, create_retry_wrapper
from ...providers import (
    canonical_provider_name,
    provider_compatibility_for_provider,
    require_explicit_base_url,
)
from ..error import retry_on
from .base import BaseLLM
from .openai import OpenAILLM
from .xinference import XinferenceLLM


@overload
def attach_chat_retry_wrapper(
    llm: BaseLLM, max_retries: Optional[int] = ...
) -> BaseLLM: ...


@overload
def attach_chat_retry_wrapper(llm: None, max_retries: Optional[int] = ...) -> None: ...


def attach_chat_retry_wrapper(
    llm: Optional[BaseLLM], max_retries: Optional[int] = None
) -> Optional[BaseLLM]:
    """Install the shared retry layer on a chat model.

    The env and default factories are all ``Optional``-returning, and this
    sits on their ``return`` statements: pass a missing model through rather
    than handing callers a retry proxy wrapped around nothing.
    """
    if llm is None:
        return None
    if max_retries is None:
        max_retries = int(ModelConfig.model_fields["max_retries"].default)
    return create_retry_wrapper(
        llm,
        BaseLLM,  # type: ignore[type-abstract]
        retry_methods={"chat", "vision_chat", "stream_chat"},
        max_retries=max_retries,
        retry_on=retry_on,
        budget=chat_retry_budget(),
    )


def create_base_llm(model: ModelConfig) -> BaseLLM:
    """Create a chat model from its configured provider endpoint."""
    if not isinstance(model, ChatModelConfig):
        raise TypeError(f"Invalid model type: {type(model).__name__}")

    provider = canonical_provider_name(model.model_provider)
    compatibility = provider_compatibility_for_provider(provider)
    llm: BaseLLM

    if (
        provider in {"openai", "openai-compatible"}
        or compatibility == "openai_compatible"
    ):
        llm = OpenAILLM(
            model_name=model.model_name,
            api_key=model.api_key,
            base_url=require_explicit_base_url(provider, model.base_url),
            default_temperature=model.default_temperature,
            default_max_tokens=model.default_max_tokens,
            timeout=model.timeout,
            abilities=model.abilities,
        )
    elif provider == "xinference":
        llm = XinferenceLLM(
            model_name=model.model_name,
            base_url=require_explicit_base_url(provider, model.base_url),
            api_key=model.api_key,
            default_temperature=model.default_temperature,
            default_max_tokens=model.default_max_tokens,
            timeout=model.timeout,
            abilities=model.abilities,
        )
    else:
        raise ValueError(f"Unsupported LLM provider: {model.model_provider}")

    llm.context_window = model.context_window
    # Stamp the unique model id so token-usage details can disambiguate models
    # that share a model_name (e.g. a platform model vs a user's own).
    llm._model_id = model.id
    return attach_chat_retry_wrapper(llm, model.max_retries)
