"""Model adapter"""

from typing import Any, Callable, Optional, Sequence, Union, cast

from langchain.tools import BaseTool
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_openai import ChatOpenAI

from ...model import ChatModelConfig, ModelConfig
from ...retry import (
    ExponentialBackoff,
    RetryStrategy,
    chat_retry_budget,
    create_retry_wrapper,
)
from ..providers import (
    canonical_provider_name,
    provider_compatibility_for_provider,
    require_explicit_base_url,
)
from .error import retry_on


class ChatModelRetryWrapper(Runnable):
    def __init__(
        self,
        model: BaseChatModel,
        strategy: RetryStrategy,
        max_retries: int = 10,
        default_extra_body: Optional[dict[str, Any]] = None,
    ):
        # One budget for this wrapper and every wrapper it derives, so the
        # bound does not depend on which of bind_tools/with_structured_output
        # a caller happens to go through.
        self.budget = chat_retry_budget()
        self._retry_wrapper = create_retry_wrapper(
            model,
            Runnable,  # type: ignore[type-abstract]
            retry_methods={"invoke", "ainvoke"},
            strategy=strategy,
            max_retries=max_retries,
            retry_on=retry_on,
            budget=self.budget,
        )
        self.model = model
        self.strategy = strategy
        self.max_retries = max_retries
        self.default_extra_body = default_extra_body

    def invoke(
        self,
        input: Any,
        config: Optional[RunnableConfig] = None,
        **kwargs: Any,
    ) -> Any:
        return self._retry_wrapper.invoke(input, config, **kwargs)

    async def ainvoke(
        self,
        input: Any,
        config: Optional[RunnableConfig] = None,
        **kwargs: Any,
    ) -> Any:
        return await self._retry_wrapper.ainvoke(input, config, **kwargs)

    def bind_tools(
        self,
        tools: Sequence[
            Union[dict[str, Any], type, Callable, BaseTool]  # noqa: UP006
        ],
        *,
        tool_choice: Optional[Union[str]] = None,
        **kwargs: Any,
    ) -> Runnable:
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice

        model: Any = self.model
        if self.default_extra_body:
            model = model.bind(extra_body=self.default_extra_body)
        bound_model = model.bind_tools(tools, **kwargs)
        return cast(
            Runnable,
            create_retry_wrapper(
                bound_model,
                Runnable,
                retry_methods={"invoke", "ainvoke"},
                strategy=self.strategy,
                max_retries=self.max_retries,
                retry_on=retry_on,
                budget=self.budget,
            ),
        )

    def with_structured_output(
        self,
        schema: Union[dict, type],  # noqa: UP006
        *,
        include_raw: bool = False,
        **kwargs: Any,
    ) -> Runnable:  # noqa: UP006
        model: Any = self.model
        if self.default_extra_body:
            model = model.bind(extra_body=self.default_extra_body)
        structured_model = model.with_structured_output(
            schema,
            include_raw=include_raw,
            **kwargs,
        )
        return cast(
            Runnable,
            create_retry_wrapper(
                structured_model,
                Runnable,
                retry_methods={"invoke", "ainvoke"},
                strategy=self.strategy,
                max_retries=self.max_retries,
                retry_on=retry_on,
                budget=self.budget,
            ),
        )


def create_base_chat_model(
    model: ModelConfig, temperature: float | None
) -> BaseChatModel:
    """
    Adapts a custom LLM instance to its corresponding LangChain Chat Model class
    """

    if not isinstance(model, ChatModelConfig):
        raise TypeError(f"Unsupported Chat model type: {type(model).__name__}")

    temp = temperature if temperature is not None else model.default_temperature

    provider = canonical_provider_name(model.model_provider)
    compatibility = provider_compatibility_for_provider(provider)

    if (
        provider in {"openai", "openai-compatible", "xinference"}
        or compatibility == "openai_compatible"
    ):
        return ChatOpenAI(
            model=model.model_name,
            temperature=temp,
            max_tokens=model.default_max_tokens,
            api_key=model.api_key or "not-needed",
            base_url=require_explicit_base_url(provider, model.base_url),
            timeout=model.timeout,
            # Retry policy lives in ``ChatModelRetryWrapper`` only; see
            # ``OpenAICompatibleLLM._ensure_client``.
            max_retries=0,
        )
    raise ValueError(f"Unsupported LLM provider: {model.model_provider}")


def create_base_chat_model_with_retry(
    model: ModelConfig, temperature: float | None
) -> ChatModelRetryWrapper:
    chat_model = create_base_chat_model(model, temperature)
    strategy = ExponentialBackoff()
    return ChatModelRetryWrapper(
        chat_model,
        strategy,
        max_retries=model.max_retries,
        default_extra_body=None,
    )
