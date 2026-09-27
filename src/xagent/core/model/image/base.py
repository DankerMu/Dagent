from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable, List, Optional


def image_edit_size(
    request_kwargs: dict[str, Any], normalize: Callable[[str], str]
) -> str:
    """Consume edit size controls using the same precedence as generation."""
    size = normalize(
        resolve_requested_size(
            request_kwargs.pop("size", None),
            resolution=request_kwargs.pop("resolution", None),
            width=request_kwargs.pop("width", None),
            height=request_kwargs.pop("height", None),
        )
    )
    request_kwargs.pop("aspect_ratio", None)
    return size


# NOT a claim that these providers edit across their lineup -- xinference defaults
# to stable-diffusion-2-1 and raises unless the backend exposes image_to_image, and
# openai's advertised DALL-E 3 cannot serve images.edit. It is the default both web
# call sites already applied to their NULL rows, kept so this change stays about
# agreement between the two paths rather than about widening or narrowing access.
_EDIT_CAPABLE_PROVIDERS = ("openai", "openai-compatible", "xinference")


def default_image_abilities(provider: str, model_name: str) -> List[str]:
    """Abilities for an image model whose row declares none.

    Both image-model construction paths use this provider-level default for
    rows without explicitly configured abilities. Explicit abilities remain
    authoritative; a model name alone no longer grants editing.
    """
    normalized = provider.strip().lower()
    if normalized in _EDIT_CAPABLE_PROVIDERS:
        return ["generate", "edit"]
    return ["generate"]


class InvalidImageResponseError(RuntimeError):
    """A provider returned a billed 200 whose body carries no usable image.

    Subclasses RuntimeError so existing `except RuntimeError` callers and the
    documented `Raises: RuntimeError` contract of every provider method are
    unchanged; the distinct type exists so the retry policy can tell it apart.

    Retrying this is strictly harmful: the provider already billed the response
    and the metering row was already written (recorded before validation
    precisely because the charge is real), so each retry buys another charge and
    another billing row for a request whose outcome will not change -- a
    safety-blocked prompt is refused just as deterministically on attempt ten.
    Transport and status failures stay plain/typed errors so they remain
    retryable, where per-attempt accounting is correct because each attempt
    really was a separate billed call.
    """


def invalid_response_from(
    error: BaseException, context: str
) -> InvalidImageResponseError:
    """Reclassify a body-walking failure as an already-billed invalid response.

    Explicitly raising the typed error at every structural check is not enough:
    walking a malformed body also fails *implicitly*. ``content[0]`` on a list
    of nulls, ``candidates[0].get(...)`` on a list of strings, or a ``parts``
    entry that is not a dict raise ``TypeError``/``AttributeError``/
    ``KeyError``/``IndexError`` -- and those land in the blanket handler, get
    rewrapped as a plain ``RuntimeError``, and are retried, re-billing and
    re-recording a call whose body will be exactly as malformed next time.

    Enumerating every shape a provider could send is a losing game, so the
    classification is positional instead: anything that fails while walking a
    200 body, after usage was already recorded, is an invalid response.

    The limit worth knowing: callers reach this through a fixed
    ``(TypeError, AttributeError, KeyError, IndexError)`` tuple, so parsing code
    that raises anything else stays retryable and re-bills. Widen the tuple at
    the call site when a provider's parser can fail another way.
    """
    return InvalidImageResponseError(f"{context}: {type(error).__name__}: {error}")


def retry_image_call(e: Exception) -> bool:
    """Retry anything except an already-billed response that cannot improve.

    The only excluded case is a billed response whose body cannot improve
    on retry; all other errors retain the existing retry policy.

    The excluded case is ``InvalidImageResponseError``: a 200 the provider
    already billed, whose body carries no usable image. Its metering row is
    already written (recorded before validation precisely because the charge is
    real), and the outcome does not change on a second attempt -- a
    safety-blocked prompt is refused just as deterministically on attempt ten.
    Retrying it multiplied both the provider bill and the recorded quantity by
    the retry count.

    Every retryable failure here is a genuinely separate billed call, so the
    per-attempt accounting the providers do stays correct.
    """
    return not isinstance(e, InvalidImageResponseError)


def decode_billed_body(decode: Callable[[], Any], context: str) -> Any:
    """Decode an already-billed 200 body, classifying failure as invalid.

    The billing boundary starts at the *first* read of a billed response, not at
    the first structural check. Decoding is the earliest such read, and it can
    fail on its own: an edge proxy returning HTML with a 200, a truncated body,
    a bad Content-Encoding. Those raise ``JSONDecodeError``/``UnicodeDecodeError``
    /``aiohttp.ContentTypeError`` rather than the walk errors
    :func:`invalid_response_from` was written for.

    Leaving decode outside the boundary is what made a plain ``RuntimeError``
    escape to the retry predicate: the provider had already charged for the
    response, no usage row had been written yet, and the call was retried up to
    ``max_retries`` times -- billed N times, metered zero. That is strictly worse
    than the multiplied-metering bug this boundary was introduced to fix, because
    the spend leaves no trace at all.

    So the rule is positional and complete: **everything from the first read of a
    billed body onwards is an invalid response, never a retryable failure.** A
    transport or status failure is a different thing and stays retryable, because
    each attempt there really is a separate billed call.

    ``decode`` is a thunk rather than an already-decoded value so the failure
    happens *inside* this function. Passing ``response.json()`` directly would
    evaluate it in the caller's frame, outside the protection -- the same
    argument-evaluation trap ``record_image_usage`` documents.
    """
    try:
        return decode()
    except Exception as error:  # noqa: BLE001 - positional classification
        raise invalid_response_from(error, context) from error


def call_billed_endpoint(
    call: Callable[[], Any],
    context: str,
    also_billed: tuple[type[BaseException], ...] = (),
) -> Any:
    """Invoke a client whose own body decode sits inside the billing boundary.

    For a client that hands back parsed JSON rather than a response object, the
    decode happens inside the client and cannot be wrapped separately. The
    xinference client is the case: it raises on a non-200, then calls
    ``response.json()`` on everything else. A 200 it cannot decode is therefore
    an already-billed response, but it surfaces as an ordinary exception from the
    call itself, indistinguishable by position from a transport failure.

    Split by type, which is exact here rather than a guess. Every stdlib/requests
    decode failure -- ``json.JSONDecodeError``,
    ``requests.exceptions.JSONDecodeError``, ``UnicodeDecodeError`` -- is a
    ``ValueError``; no transport failure is (``Timeout`` and ``ConnectionError``
    derive from ``RequestException`` and ``OSError``, never ``ValueError``). So a
    ``ValueError`` out of a client that already accepted the status is a billed
    body that will not parse, and anything else is a genuinely separate,
    genuinely retryable call.

    ``also_billed`` names further exception types a specific SDK raises for the
    same situation, since not every client expresses it as a ``ValueError`` --
    the OpenAI SDK signals a 200 whose body fails validation with
    ``APIResponseValidationError``, which derives from its own ``APIError``.
    Passed in by the provider rather than imported here, so this module keeps no
    dependency on any vendor SDK.
    """
    try:
        return call()
    except ValueError as error:
        raise invalid_response_from(error, context) from error
    except also_billed as error:
        raise invalid_response_from(error, context) from error


async def call_billed_endpoint_async(
    call: Callable[[], Awaitable[Any]],
    context: str,
    also_billed: tuple[type[BaseException], ...] = (),
) -> Any:
    """Async counterpart of :func:`call_billed_endpoint`, same contract."""
    try:
        return await call()
    except ValueError as error:
        raise invalid_response_from(error, context) from error
    except also_billed as error:
        raise invalid_response_from(error, context) from error


async def decode_billed_body_async(
    decode: Callable[[], Awaitable[Any]], context: str
) -> Any:
    """Async counterpart of :func:`decode_billed_body`, same contract.

    Separate rather than a sync helper awaiting its result, because the failure
    has to happen inside the boundary: ``await response.json()`` raises during
    the await, so handing this function an already-created coroutine would leave
    the decode unprotected in exactly the way it was before.
    """
    try:
        return await decode()
    except Exception as error:  # noqa: BLE001 - positional classification
        raise invalid_response_from(error, context) from error


def image_url_from_item(image_item: Any) -> Optional[str]:
    """A URL or inline data URI from one image entry, or None when it carries neither.

    Shared so the object-shaped providers cannot disagree about what "has a URL"
    means. They did: a truthy check reads ``url=None`` as absent and moves on to
    ``b64_json``, while ``hasattr`` reads it as present and returns the string
    ``"None"`` -- which the caller then tries to download. Truthiness is the
    correct reading; the presence of an attribute set to None says nothing about
    whether the provider returned an image.
    """
    if getattr(image_item, "url", None):
        return str(image_item.url)
    if getattr(image_item, "b64_json", None):
        return f"data:image/png;base64,{image_item.b64_json}"
    return None


def resolve_requested_size(
    size: Any,
    *,
    resolution: Any = None,
    width: Any = None,
    height: Any = None,
    default: str = "1024*1024",
) -> str:
    """The size a request actually asked for, as "WxH".

    ``generate_image`` applies ``resolution > width+height > size``;
    ``edit_image`` must use the same precedence. Callers normalize size
    separators to their configured endpoint's expected format.
    """
    if isinstance(resolution, str) and resolution:
        return resolution
    if width and height:
        return f"{width}x{height}"
    if isinstance(size, str) and size:
        return size
    # A non-string size is dropped rather than stringified: an int 2048 would
    # become the aggregate key "2048", which joins no price table and is
    # indistinguishable from a real tier. width+height above is the typed way
    # to say the same thing.
    return default


def resolve_generation_size(
    size: str,
    *,
    resolution: Optional[str],
    width: Optional[int],
    height: Optional[int],
    aspect_ratio: Optional[str],
    separator: str,
    provider: str,
    logger: Any,
) -> str:
    """Resolve mutually exclusive generation size controls without changing precedence."""
    if aspect_ratio:
        logger.warning(
            "aspect_ratio parameter '%s' is not directly supported by "
            "%s API, using size '%s' instead",
            aspect_ratio,
            provider,
            size,
        )
        return size
    if resolution:
        return resolution.replace("x", separator)
    if width and height:
        return f"{width}{separator}{height}"
    return size


class BaseImageModel(ABC):
    """
    Abstract base class for image generation models.
    """

    @property
    @abstractmethod
    def abilities(self) -> List[str]:
        """
        Get the list of abilities supported by this image model implementation.
        Possible abilities: ["generate", "edit"]

        Returns:
            List[str]: List of supported abilities
        """
        pass

    @property
    def supports_transparent_background(self) -> bool:
        """Whether the provider can return an image with an alpha channel.

        False by default because support for an alpha channel is specific
        to the selected image model. Callers reject unsupported requests
        rather than returning opaque images.
        """
        return False

    def has_ability(self, ability: str) -> bool:
        """
        Check if this image model implementation supports a specific ability.

        Args:
            ability: The ability to check

        Returns:
            bool: True if the ability is supported, False otherwise
        """
        return ability in self.abilities

    @abstractmethod
    async def generate_image(
        self,
        prompt: str,
        size: str = "1024*1024",
        negative_prompt: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Generate an image from a text prompt.

        Args:
            prompt: Text prompt for image generation
            size: Image size in format "width*height" (e.g., "1024*1024")
            negative_prompt: Negative prompt for image generation
            **kwargs: Additional parameters specific to the model

        Returns:
            dict with image generation result containing:
            - image_url: URL of the generated image
            - usage: Image generation usage statistics
            - request_id: Request identifier
        """
        pass

    @abstractmethod
    async def edit_image(
        self,
        image_url: str | list[str],
        prompt: str,
        negative_prompt: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Edit an image using a text prompt.

        Args:
            image_url: URL of the source image to edit (or list of URLs)
            prompt: Text prompt describing the desired edits
            negative_prompt: Negative prompt for image generation
            **kwargs: Additional parameters specific to the model

        Returns:
            dict with image editing result containing:
            - image_url: URL of the edited image
            - usage: Image generation usage statistics
            - request_id: Request identifier
        """
        pass
