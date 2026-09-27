"""Local workbook ingestion preserves sheet ownership and exposes corrupt files."""

import pytest
from openpyxl import Workbook

from xagent.providers.pdf_parser.deepdoc import DeepDocParser


@pytest.fixture(autouse=True)
def isolate_parser_environment(monkeypatch, tmp_path):
    monkeypatch.delenv("XAGENT_DEEPDOC_XINFERENCE_URL", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "models"))


@pytest.mark.asyncio
async def test_two_sheet_workbook_keeps_titles_and_headers_with_their_sheets(tmp_path):
    path = tmp_path / "workbook.xlsx"
    book = Workbook()
    first = book.active
    first.title = "Finance"
    first.append(["Quarterly Revenue"])
    first.append(["Item", "Amount"])
    first.append(["Local", 42])
    second = book.create_sheet("Operations")
    second.append(["Backlog Review"])
    second.append(["Task", "Owner"])
    second.append(["Ingest", "Alice"])
    book.save(path)
    book.close()

    parsed = await DeepDocParser().parse(str(path))
    assert [
        (segment.text, segment.metadata["sheet_name"])
        for segment in parsed.text_segments
    ] == [
        ("[Finance] Quarterly Revenue", "Finance"),
        ("[Finance] Item | Amount", "Finance"),
        ("Item: Local | Amount: 42", "Finance"),
        ("[Operations] Backlog Review", "Operations"),
        ("[Operations] Task | Owner", "Operations"),
        ("Task: Ingest | Owner: Alice", "Operations"),
    ]


@pytest.mark.asyncio
async def test_corrupt_workbook_reports_local_parser_error(tmp_path):
    path = tmp_path / "corrupt.xlsx"
    path.write_bytes(b"not a workbook")

    with pytest.raises(ValueError, match="Failed to parse spreadsheet rows"):
        await DeepDocParser().parse(str(path))
