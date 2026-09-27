"""
Audio tool descriptions

This module contains the description templates for audio processing tools.
Extracted from audio_tool.py for better maintainability.
"""

# Description for transcribe_audio tool
TRANSCRIBE_AUDIO_DESCRIPTION = """
Transcribe audio to text using Speech-to-Text (ASR).

This tool converts spoken language in audio files into written text.
Supports multiple languages and can provide detailed timing information.

Available models (⭐[DEFAULT] marks the configured default model):
{}

**IMPORTANT: Prefer the default model marked with ⭐[DEFAULT]. Only specify model_id if the user explicitly requests a different model.**

Parameters:
- file_path_or_id (required): workspace file ID, audio file path, or URL to transcribe
- language (optional): language code (e.g., 'zh', 'en', 'yue', 'ja', 'ko')
- model_id (optional): specific ASR model to use. Omit to use the default model marked with ⭐[DEFAULT].
- verbose (optional): False returns processed, subtitle-friendly timestamp segments.
  True preserves the ASR provider's raw word/segment timestamps. Default: False

Language support:
- 'zh': Chinese (Mandarin)
- 'en': English
- 'yue': Cantonese
- 'ja': Japanese
- 'ko': Korean
- And more depending on model capabilities

Audio formats: wav, mp3, m4a, flac, ogg, and other common formats

File handling:
- If an earlier tool returned a file_id, pass that exact file_id directly.
- Do not copy a registered workspace file or construct a relative path for it.
- Use a path or URL only when no workspace file_id is available.

Advanced features (if supported by model):
- Speaker diarization: identify different speakers
- Timestamps: get word-level or segment-level timing
- Confidence scores: get transcription confidence
- Raw timestamps: verbose=True preserves the provider's original word/segment timing without processing
- Readable timestamps: verbose=False groups raw timing into subtitle-friendly cues using punctuation, pauses, speaker changes, duration, and text length

Output:
- file_id: File ID for accessing the full transcription JSON file in workspace
- transcription_path: Path to saved transcription JSON file in workspace
- saved_to_workspace: Whether the transcription was saved to workspace
- segments: Raw provider timing when verbose=True; processed readable timing when verbose=False
- language: Detected language code
- model_used: The actual model used for transcription
- text_length: Length of transcribed text
- segment_count: Number of segments

Note: Use read_file(file_id) to get the full transcription text.

JSON Output Format (saved to file specified by file_id):
```json
{{
  "model": "model_name",
  "language": "zh",
  "text": "Full transcribed text here...",
  "segments": [
    {{
      "text": "Segment text",
      "start": 0.0,
      "end": 2.5,
      "speaker": "spk1",
      "confidence": 0.95
    }}
  ],
  "metadata": {{
    "audio_source": "input_audio.mp3",
    "verbose_mode": true,
    "segment_view": "raw",
    "raw_segment_count": 205,
    "total_segments": 205
  }}
}}
```

Use verbose=True for diagnostics or exact provider output. Use the default
verbose=False mode for downstream subtitle generation and normal display.
""".strip()

# Description for synthesize_speech tool
SYNTHESIZE_SPEECH_DESCRIPTION = """
Synthesize speech from text using Text-to-Speech (TTS).

This tool converts written text into natural-sounding speech audio.
Supports multiple voices, languages, and audio formats.

Available models (⭐[DEFAULT] marks the configured default model):
{}

**IMPORTANT: Prefer the default model marked with ⭐[DEFAULT]. Only specify model_id if the user explicitly requests a different model.**

Parameters:
- text (required): text content to synthesize into speech
- voice (optional): provider-specific voice identifier. Never invent or derive this value from language, gender, accent, or style. Omit for the configured default voice.
- language (optional): language code (e.g., 'zh', 'en', 'yue'). Auto-detected from text if not specified.
- audio_format (optional): audio output format (e.g., 'mp3', 'wav', 'pcm'). Default: 'mp3'
- sample_rate (optional): sample rate in Hz when the provider/model supports it.
- reference_audio (optional): reference audio file path or workspace file ID for voice cloning, when supported by the selected model. When provided, it takes precedence over voice.
- model_id (optional): specific TTS model to use. Omit to use the default model marked with ⭐[DEFAULT].

Voice options depend on the model:
- Voice identifiers are provider-specific opaque values, not generic labels such as language plus gender.
- Some models support voice cloning using reference_audio
- Multilingual models can auto-detect language from text

Audio format options:
- mp3: Compressed audio, good for speech (default)
- wav: Uncompressed audio, higher quality
- pcm: Raw audio data

The generated audio file will be automatically saved to workspace.
""".strip()

# Description for synthesize_speech_json tool
SYNTHESIZE_SPEECH_JSON_DESCRIPTION = """
Batch synthesize speech from JSON structure using Text-to-Speech (TTS).

This tool converts multiple text segments into speech audio files in a single call.
Supports flexible JSON format with configurable field mapping, voice cloning, and batch processing.

Available models (⭐[DEFAULT] marks the configured default model):
{}

**IMPORTANT: Prefer the default model marked with ⭐[DEFAULT]. Only specify model_id if the user explicitly requests a different model.**

Parameters:
- json_data (optional): JSON string or dict containing synthesis configuration. Either json_data or file_id must be provided.
- file_id (optional): File ID, file path, or URL to read JSON data from. Either json_data or file_id must be provided.
- segments_field (optional): Field name containing segments array (default: "segments")
- text_field (optional): Field name containing text within each segment (default: "text")
- voice_field (optional): Field name containing voice within each segment (default: "voice")
- reference_field (optional): Field name containing reference audio file path/ID for voice cloning (default: "reference_audio")
- default_voice (optional): Default voice for segments without voice specified
- default_language (optional): Default language code (auto-detect if None)
- audio_format (optional): Output audio format (default: 'mp3')
- sample_rate (optional): Sample rate in Hz (default: model-specific)
- model_id (optional): Specific TTS model to use. Omit to use the default model marked with ⭐[DEFAULT].
- batch_size (optional): Number of syntheses to process in parallel (1-20, default: 5)

JSON Format (nested segment structure):
```json
{{
    "segments": [
        {{"text": "你好世界", "reference_audio": "ref_voice_1"}},
        {{"text": "这是一个测试", "reference_audio": "ref_voice_2"}}
    ],
    "output_format": "mp3",
    "sample_rate": 24000
}}
```

Voice identifiers:
- Never invent voice or default_voice values from language, gender, accent, or style.
- Use an exact provider-specific voice ID only when one is already known; otherwise omit voice/default_voice for the configured default.

Voice Cloning:
- Use reference_audio in each segment to clone voices from reference audio files
- Supports both workspace file IDs and direct file paths (absolute or relative)
- Voice cloning quality depends on the reference audio quality
- Not all models support voice cloning

Batch Processing:
- All segments are processed in parallel for efficiency
- Use batch_size to control parallelism (1-20)
- Progress is shown during synthesis
- Failed segments don't stop the batch

Output:
- success (bool): Whether all syntheses succeeded
- results (list): List of synthesis results, one per segment
- total (int): Total number of segments processed
- successful (int): Number of successful syntheses
- failed (int): Number of failed syntheses
- errors (list): List of error messages for failed segments
- saved_to_workspace (bool): Whether audio files were saved to workspace

Using file_id parameter is recommended for workflows with file chaining.
file_id supports: File ID, file path, or URL.
""".strip()
