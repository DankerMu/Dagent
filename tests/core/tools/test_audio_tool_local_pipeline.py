"""Local ASR/TTS behavior at the tool boundary, with model transport substituted."""

import json
from functools import partial
from pathlib import Path
from typing import Any

import httpx

from xagent.core.model.asr.base import ASRResult, BaseASR
from xagent.core.model.tts.base import BaseTTS, TTSResult
from xagent.core.tools.adapters.vibe.audio_tool import create_audio_tool
from xagent.core.tools.core.audio_tool import AudioToolCore
from xagent.core.workspace import TaskWorkspace


class LocalASR(BaseASR):
    def __init__(self, answer: str | ASRResult = "hello") -> None:
        self.answer = answer
        self.calls: list[str] = []

    @property
    def abilities(self) -> list[str]:
        return ["asr"]

    async def transcribe(self, audio: str | bytes, **kwargs: Any) -> str | ASRResult:
        self.calls.append(str(audio))
        return self.answer


class LocalTTS(BaseTTS):
    def __init__(self, *, cloning: bool = False, audio: bytes = b"wav-bytes") -> None:
        self.cloning = cloning
        self.audio = audio
        self.calls: list[dict[str, Any]] = []

    @property
    def abilities(self) -> list[str]:
        return ["tts", "voice_cloning"] if self.cloning else ["tts"]

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        language: str | None = None,
        format: str | None = None,
        sample_rate: int | None = None,
        **kwargs: Any,
    ) -> bytes | TTSResult:
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
            audio=self.audio, format=format or "wav", sample_rate=sample_rate
        )


def _workspace(tmp_path: Path) -> TaskWorkspace:
    return TaskWorkspace("task_local_audio", base_dir=str(tmp_path))


async def test_local_asr_workspace_file_id_produces_registered_transcription(tmp_path):
    workspace = _workspace(tmp_path)
    source = workspace.output_dir / "speech.wav"
    source.write_bytes(b"local speech")
    file_id = workspace.register_file(str(source))
    asr = LocalASR("A local transcript")
    tools = create_audio_tool(asr_models={"offline": asr}, workspace=workspace)
    transcribe = next(t for t in tools if t.name == "transcribe_audio")

    result = await transcribe.run_json_async({"file_path_or_id": file_id})

    assert result["success"] is True
    assert asr.calls == [str(source.resolve())]
    saved = json.loads(Path(result["transcription_path"]).read_text())
    assert saved["text"] == "A local transcript"
    assert result["file_id"] == workspace.get_file_id_from_path(
        result["transcription_path"]
    )


async def test_local_asr_without_model_or_with_invalid_workspace_path_reports_failure(
    tmp_path,
):
    workspace = _workspace(tmp_path)
    no_model = await AudioToolCore(workspace=workspace).transcribe_audio("speech.wav")
    asr = LocalASR()
    invalid_path = await AudioToolCore(
        asr_models={"offline": asr}, workspace=workspace
    ).transcribe_audio("../../outside.wav")

    assert no_model["success"] is False
    assert no_model["error"] == "No available ASR models configured"
    assert invalid_path["success"] is False
    assert asr.calls == []


async def test_local_tts_saves_audio_and_explicit_model_controls_selection(tmp_path):
    workspace = _workspace(tmp_path)
    default = LocalTTS(audio=b"default")
    chosen = LocalTTS(audio=b"chosen")
    tool = AudioToolCore(
        tts_models={"default": default, "offline": chosen},
        default_tts_model=default,
        workspace=workspace,
    )

    result = await tool.synthesize_speech(
        "hello", voice="Ada", audio_format="wav", sample_rate=24000, model_id="offline"
    )

    assert result["success"] is True
    assert result["model_used"] == "offline"
    assert Path(result["audio_path"]).read_bytes() == b"chosen"
    assert result["file_id"] == workspace.get_file_id_from_path(result["audio_path"])
    assert default.calls == []
    assert chosen.calls[0]["voice"] == "Ada"
    assert chosen.calls[0]["sample_rate"] == 24000


async def test_local_tts_reference_audio_requires_capability_before_model_call(
    tmp_path,
):
    workspace = _workspace(tmp_path)
    source = workspace.output_dir / "reference.wav"
    source.write_bytes(b"reference")
    file_id = workspace.register_file(str(source))
    incapable = LocalTTS()
    cloning = LocalTTS(cloning=True)
    tool = AudioToolCore(
        tts_models={"incapable": incapable, "cloning": cloning},
        workspace=workspace,
    )

    rejected = await tool.synthesize_speech(
        "test", reference_audio=file_id, model_id="incapable"
    )
    accepted = await tool.synthesize_speech(
        "test", reference_audio=file_id, model_id="cloning"
    )

    assert rejected["success"] is False
    assert "does not support reference_audio" in rejected["error"]
    assert incapable.calls == []
    assert accepted["success"] is True
    assert cloning.calls[0]["reference_audio"] == str(source.resolve())


async def test_local_tts_rejects_nested_provider_options_before_model_call():
    tts = LocalTTS()
    tool = AudioToolCore(tts_models={"offline": tts})

    result = await tool.synthesize_speech(
        "hello", model_id="offline", provider_options={"format": "wav"}
    )

    assert result["success"] is False
    assert "standard TTS parameters: provider_options" in result["error"]
    assert tts.calls == []


async def test_local_tts_batch_file_input_overrides_inline_json_and_applies_voice_precedence(
    tmp_path,
):
    workspace = _workspace(tmp_path)
    source = workspace.output_dir / "script.json"
    source.write_text(
        json.dumps(
            {
                "default_voice": "narrator",
                "segments": [
                    {"text": "first", "voice": "character"},
                    {"text": "second"},
                ],
            }
        )
    )
    file_id = workspace.register_file(str(source))
    tts = LocalTTS()
    tool = AudioToolCore(tts_models={"offline": tts}, workspace=workspace)

    result = await tool.synthesize_speech_json(
        file_id=file_id,
        json_data={"segments": [{"text": "ignored"}]},
        model_id="offline",
    )

    assert result["success"] is True
    assert result["successful"] == 2
    assert [call["text"] for call in tts.calls] == ["first", "second"]
    assert [call["voice"] for call in tts.calls] == ["character", "narrator"]
    assert [Path(item["audio_path"]).read_bytes() for item in result["results"]] == [
        b"wav-bytes",
        b"wav-bytes",
    ]


async def test_local_tts_batch_reports_individual_failed_segment_without_losing_success(
    tmp_path,
):
    workspace = _workspace(tmp_path)
    tts = LocalTTS()
    tool = AudioToolCore(tts_models={"offline": tts}, workspace=workspace)

    result = await tool.synthesize_speech_json(
        json_data={
            "segments": [
                {"text": "first"},
                {"voice": "missing text"},
                {"text": "last"},
            ]
        },
        model_id="offline",
        batch_size=2,
    )

    assert result["success"] is False
    assert result["successful"] == 2
    assert result["failed"] == 1
    assert result["errors"] == ["Segment 1: No text found in field 'text'"]
    assert [call["text"] for call in tts.calls] == ["first", "last"]
    assert all(
        Path(item["audio_path"]).exists()
        for item in result["results"]
        if item["success"]
    )


async def test_local_tts_batch_rejects_malformed_rate_and_missing_model_before_synthesis():
    tts = LocalTTS()
    tool = AudioToolCore(tts_models={"offline": tts})
    malformed = await tool.synthesize_speech_json(
        json_data={"segments": [{"text": "hello"}], "sample_rate": "not-a-number"}
    )
    missing = await AudioToolCore().synthesize_speech_json(
        json_data={"segments": [{"text": "hello"}]}
    )

    assert malformed["success"] is False
    assert malformed["error"] == "sample_rate must be an integer"
    assert missing["success"] is False
    assert missing["error"] == "No available TTS models configured"
    assert tts.calls == []


async def test_lan_json_script_url_supersedes_inline_segments(monkeypatch, tmp_path):
    workspace = _workspace(tmp_path)
    tts = LocalTTS()
    tool = AudioToolCore(tts_models={"offline": tts}, workspace=workspace)
    observed_urls = []

    def respond(request: httpx.Request) -> httpx.Response:
        observed_urls.append(str(request.url))
        return httpx.Response(200, json={"segments": [{"text": "from LAN"}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", partial(original, transport=httpx.MockTransport(respond))
    )

    result = await tool.synthesize_speech_json(
        file_id="http://lan.invalid/script.json",
        json_data={"segments": [{"text": "stale"}]},
        model_id="offline",
    )

    assert result["success"] is True
    assert observed_urls == ["http://lan.invalid/script.json"]
    assert [call["text"] for call in tts.calls] == ["from LAN"]
    assert Path(result["results"][0]["audio_path"]).read_bytes() == b"wav-bytes"
