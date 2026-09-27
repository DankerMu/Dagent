"""ASR and TTS factories must reject unsupported providers, not substitute cloud clients."""

from types import SimpleNamespace

import pytest

from xagent.core.model.asr.adapter import get_asr_model, get_asr_model_instance
from xagent.core.model.asr.xinference import XinferenceASR
from xagent.core.model.tts.adapter import get_tts_model, get_tts_model_instance
from xagent.core.model.tts.xinference import XinferenceTTS


def _speech_row(provider: str):
    return SimpleNamespace(
        model_provider=provider,
        model_name="local-speech",
        api_key="local-token",  # pragma: allowlist secret - synthetic model row
        base_url="http://speech.lan:9997/",
    )


def test_asr_database_selection_preserves_local_endpoint_and_model_uid():
    asr = get_asr_model_instance(_speech_row("Xinference"))
    assert isinstance(asr, XinferenceASR)
    assert asr.base_url == "http://speech.lan:9997"
    assert asr.model == "local-speech"
    assert asr._model_uid == "local-speech"


def test_tts_database_selection_preserves_local_endpoint_and_model_uid():
    tts = get_tts_model_instance(_speech_row("Xinference"))
    assert isinstance(tts, XinferenceTTS)
    assert tts.base_url == "http://speech.lan:9997"
    assert tts.model == "local-speech"
    assert tts._model_uid == "local-speech"


def test_explicit_speech_selection_accepts_local_provider_with_canonical_spacing():
    asr = get_asr_model(
        provider=" Xinference ", model="whisper-local", base_url="http://speech.lan"
    )
    tts = get_tts_model(
        provider=" Xinference ", model="voice-local", base_url="http://speech.lan"
    )
    assert isinstance(asr, XinferenceASR)
    assert isinstance(tts, XinferenceTTS)
    assert asr.model == "whisper-local"
    assert tts.model == "voice-local"


def test_speech_factories_never_fall_back_to_a_different_provider():
    with pytest.raises(ValueError, match="ASR provider cannot be None"):
        get_asr_model(provider=None)
    with pytest.raises(ValueError, match="Unsupported ASR provider: cloud-asr"):
        get_asr_model(provider="cloud-asr")
    with pytest.raises(ValueError, match="Unsupported TTS provider: cloud-tts"):
        get_tts_model(provider="cloud-tts")
    with pytest.raises(ValueError, match="Unsupported ASR provider: cloud-asr"):
        get_asr_model_instance(_speech_row("cloud-asr"))
    with pytest.raises(ValueError, match="Unsupported TTS provider: cloud-tts"):
        get_tts_model_instance(_speech_row("cloud-tts"))
