"""Tests for audio tool speech recognition and TTS options."""

from __future__ import annotations

import json
from typing import Any, Optional, Union

import pytest

from xagent.core.model.asr.base import ASRResult, ASRSegment, BaseASR
from xagent.core.model.tts.base import BaseTTS, TTSResult
from xagent.core.tools.adapters.vibe.audio_tool import AudioTool
from xagent.core.tools.core.audio_tool import AudioToolCore
from xagent.core.workspace import TaskWorkspace


class FakeASR(BaseASR):
    async def transcribe(
        self,
        audio: Union[str, bytes],
        language: Optional[str] = None,
        format: Optional[str] = None,
        verbose: bool = False,
        **kwargs: Any,
    ) -> Union[str, ASRResult]:
        _ = audio, format, kwargs
        if not verbose:
            return "Hello world"
        return ASRResult(
            text="Hello world",
            segments=[
                ASRSegment(
                    text="Hello",
                    start=0.1,
                    end=0.5,
                    confidence=0.99,
                ),
                ASRSegment(
                    text="world",
                    start=0.55,
                    end=1.0,
                    confidence=0.98,
                ),
            ],
            language=language or "eng",
        )

    @property
    def abilities(self) -> list[str]:
        return ["asr", "timestamps"]


class FakeTTS(BaseTTS):
    def __init__(
        self,
        *,
        provider_name: str = "fake",
        abilities: Optional[list[str]] = None,
    ) -> None:
        self._provider_name = provider_name
        self._abilities = abilities or ["tts", "text_to_speech"]
        self.calls: list[dict[str, Any]] = []
        self.model_name = "fake-tts"

    async def synthesize(
        self,
        text: str,
        voice: Optional[str] = None,
        language: Optional[str] = None,
        format: Optional[str] = None,
        sample_rate: Optional[int] = None,
        **kwargs: Any,
    ) -> Union[bytes, TTSResult]:
        self.calls.append(
            {
                "text": text,
                "voice": voice,
                "language": language,
                "format": format,
                "sample_rate": sample_rate,
                **kwargs,
            }
        )
        return TTSResult(
            audio=b"fake-audio",
            format=format or "mp3",
            sample_rate=sample_rate,
            language=language,
        )

    @property
    def abilities(self) -> list[str]:
        return self._abilities

    @property
    def provider_name(self) -> str:
        return self._provider_name


class ClosableTTS(FakeTTS):
    def __init__(self) -> None:
        super().__init__()
        self.close_count = 0

    async def aclose(self) -> None:
        self.close_count += 1


def test_audio_path_resolution_uses_workspace_file_id(tmp_path) -> None:
    workspace = TaskWorkspace("task_audio", base_dir=str(tmp_path))
    audio_path = workspace.output_dir / "voice.mp3"
    audio_path.write_bytes(b"audio")
    file_id = workspace.register_file(str(audio_path))
    tool = AudioToolCore(workspace=workspace)

    assert tool._resolve_audio_path(file_id) == str(audio_path.resolve())


def test_audio_path_resolution_does_not_fall_back_to_process_cwd(
    tmp_path, monkeypatch
) -> None:
    workspace = TaskWorkspace("task_audio", base_dir=str(tmp_path / "uploads"))
    process_cwd = tmp_path / "repo"
    misplaced_audio = process_cwd / "output" / "voice.mp3"
    misplaced_audio.parent.mkdir(parents=True)
    misplaced_audio.write_bytes(b"audio")
    monkeypatch.chdir(process_cwd)
    tool = AudioToolCore(workspace=workspace)

    with pytest.raises(FileNotFoundError, match="not found in workspace"):
        tool._resolve_audio_path("output/voice.mp3")


async def test_transcribe_audio_verbose_preserves_provider_timestamps(
    tmp_path,
) -> None:
    workspace = TaskWorkspace("task_audio", base_dir=str(tmp_path))
    audio_path = workspace.output_dir / "voice.mp3"
    audio_path.write_bytes(b"audio")
    file_id = workspace.register_file(str(audio_path))
    tool = AudioToolCore(asr_models={"fake": FakeASR()}, workspace=workspace)

    result = await tool.transcribe_audio(
        audio_file_path=file_id,
        model_id="fake",
        verbose=True,
    )

    assert result["success"] is True
    assert result["segment_count"] == 2
    assert result["segments"] == [
        {
            "text": "Hello",
            "start": 0.1,
            "end": 0.5,
            "speaker": None,
            "confidence": 0.99,
        },
        {
            "text": "world",
            "start": 0.55,
            "end": 1.0,
            "speaker": None,
            "confidence": 0.98,
        },
    ]

    with open(result["transcription_path"], encoding="utf-8") as f:
        transcription = json.load(f)
    assert transcription["segments"] == result["segments"]
    assert transcription["metadata"]["segment_view"] == "raw"
    assert transcription["metadata"]["raw_segment_count"] == 2
    assert transcription["metadata"]["total_segments"] == 2
    assert transcription["metadata"]["segments_merged"] is False


async def test_transcribe_audio_default_returns_processed_segments(tmp_path) -> None:
    workspace = TaskWorkspace("task_audio", base_dir=str(tmp_path))
    audio_path = workspace.output_dir / "voice.mp3"
    audio_path.write_bytes(b"audio")
    file_id = workspace.register_file(str(audio_path))
    tool = AudioToolCore(asr_models={"fake": FakeASR()}, workspace=workspace)

    result = await tool.transcribe_audio(
        audio_file_path=file_id,
        model_id="fake",
    )

    assert result["success"] is True
    assert result["segments"] == [
        {
            "text": "Hello world",
            "start": 0.1,
            "end": 1.0,
            "speaker": None,
            "confidence": pytest.approx(0.985),
        }
    ]

    with open(result["transcription_path"], encoding="utf-8") as f:
        transcription = json.load(f)
    assert transcription["metadata"]["segment_view"] == "processed"
    assert transcription["metadata"]["raw_segment_count"] == 2
    assert transcription["metadata"]["total_segments"] == 1
    assert transcription["metadata"]["segments_merged"] is True


def test_aggregate_segments_uses_sentence_pause_and_speaker_boundaries() -> None:
    tool = AudioToolCore()
    segments = [
        {"text": "Hello", "start": 0.0, "end": 0.2, "speaker": "a"},
        {"text": "world", "start": 0.25, "end": 0.5, "speaker": "a"},
        {"text": ".", "start": 0.5, "end": 0.55, "speaker": "a"},
        {"text": "Next", "start": 0.6, "end": 0.9, "speaker": "a"},
        {"text": "pause", "start": 1.5, "end": 1.8, "speaker": "a"},
        {"text": "Speaker", "start": 1.85, "end": 2.1, "speaker": "b"},
    ]

    aggregated = tool._aggregate_segments(segments)

    assert [segment["text"] for segment in aggregated] == [
        "Hello world.",
        "Next",
        "pause",
        "Speaker",
    ]


@pytest.mark.parametrize(
    "invalid_segment",
    [
        {"text": "Missing start", "end": 1.0},
        {"text": "Null end", "start": 0.6, "end": None},
    ],
)
def test_aggregate_segments_rejects_missing_timestamps(
    invalid_segment: dict[str, Any],
) -> None:
    tool = AudioToolCore()

    with pytest.raises(
        ValueError,
        match="Segment start or end time is missing or null",
    ):
        tool._aggregate_segments(
            [
                {"text": "Valid", "start": 0.0, "end": 0.5},
                invalid_segment,
            ]
        )


async def test_aclose_closes_unique_configured_model_clients() -> None:
    tts = ClosableTTS()
    tool = AudioToolCore(
        tts_models={"fake": tts},
        default_tts_model=tts,
    )

    await tool.aclose()

    assert tts.close_count == 1


async def test_audio_function_tool_teardown_closes_owner_once_per_task() -> None:
    tts = ClosableTTS()
    audio_tool = AudioTool(tts_models={"fake": tts})

    for tool in audio_tool.get_tools():
        await tool.teardown(task_id="task-1")

    assert tts.close_count == 1

    for tool in audio_tool.get_tools():
        await tool.teardown(task_id="task-2")

    assert tts.close_count == 2


async def test_synthesize_speech_rejects_unsupported_reference_audio() -> None:
    tts = FakeTTS(provider_name="localtts")
    tool = AudioToolCore(tts_models={"localtts": tts})

    result = await tool.synthesize_speech(
        text="Hello",
        reference_audio="ref.wav",
        model_id="localtts",
    )

    assert result["success"] is False
    assert "does not support reference_audio voice cloning" in result["error"]
    assert tts.calls == []


def test_transcribe_audio_schema_accepts_workspace_file_path_or_id() -> None:
    tool = AudioTool()
    transcribe_tool = next(
        candidate
        for candidate in tool.get_tools()
        if candidate.name == "transcribe_audio"
    )

    schema = transcribe_tool.args_type().model_json_schema()

    assert "file_path_or_id" in schema["properties"]
    assert "file_path_or_id" in schema["required"]
    assert "audio_file_path" not in schema["properties"]
    assert "exact file_id" in transcribe_tool.description
    assert "only when no workspace file_id" in transcribe_tool.description


@pytest.mark.parametrize(
    ("unsupported", "at_root"),
    [
        ("default_voice_settings", True),
        ("default_provider_options", True),
        ("voice_settings", False),
        ("provider_options", False),
    ],
)
async def test_speech_json_rejects_retired_voice_options(
    unsupported: str, at_root: bool
) -> None:
    tts = FakeTTS()
    tool = AudioToolCore(tts_models={"fake": tts})
    segment: dict[str, Any] = {"text": "Hello"}
    data: dict[str, Any] = {"segments": [segment]}
    (data if at_root else segment)[unsupported] = {"style": 0.5}

    result = await tool.synthesize_speech_json(json_data=data, model_id="fake")

    assert result["success"] is False
    if at_root:
        assert result["error"] == f"Unsupported TTS option: {unsupported}"
    else:
        assert result["errors"] == [f"Segment 0: Unsupported TTS option: {unsupported}"]
    assert tts.calls == []


async def test_synthesize_speech_json_rejects_non_object_json() -> None:
    tool = AudioToolCore(tts_models={"fake": FakeTTS()})

    result = await tool.synthesize_speech_json(json_data=[])

    assert result == {
        "success": False,
        "error": "JSON data must be an object",
        "results": [],
        "total": 0,
        "successful": 0,
        "failed": 0,
        "errors": ["JSON data must be an object"],
    }
