"""Local-only resource checks performed before DeepDoc parser construction."""

import os
from io import BytesIO

from ...core.offline_assets import (
    require_deepdoc_local_assets,
    require_tiktoken_encoding,
)
from .base import validate_office_file_format


def configure_local_deepdoc() -> None:
    """Disable the vendor's ModelScope and NLTK fallback for every format."""
    os.environ["DEEPDOC_OFFLINE"] = "1"
    os.environ["DEEPDOC_MODEL_PROVIDER"] = "local"


def require_local_parser_assets(ext: str) -> None:
    """Verify models only for PDF, but the tokenizer for every local parser."""
    if ext == ".pdf":
        require_deepdoc_local_assets()
    # DeepDoc's tokenizer calls tiktoken directly even with DEEPDOC_OFFLINE.
    require_tiktoken_encoding("cl100k_base")


def validate_local_office_input(file_path: str | BytesIO, ext: str) -> None:
    """Validate file-backed Office inputs; converted byte streams are exempt."""
    if ext in [".docx", ".xlsx", ".pptx"] and not isinstance(file_path, BytesIO):
        validate_office_file_format(file_path, ext, strict=True, parser_name="deepdoc")
