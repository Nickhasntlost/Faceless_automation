from __future__ import annotations

import logging
import re
import subprocess
import xml.sax.saxutils
from pathlib import Path

from src.models import PipelineConfig, PricingConfig, ScriptPackage, VoiceResult, WordTiming
from src.module3_budget_guard import BudgetGuard
from src.utils.api_client import with_timeout
from src.utils.encoding import write_json

logger = logging.getLogger("shorts_pipeline.voice")


def _tokenize_words(text: str) -> list[str]:
    return re.findall(r"\b[\w']+\b", text)


def _build_ssml(words: list[str]) -> str:
    parts = ["<speak>"]
    for idx, word in enumerate(words):
        escaped = xml.sax.saxutils.escape(word)
        parts.append(f'<mark name="w{idx}"/>{escaped}')
        if idx != len(words) - 1:
            parts.append(" ")
    parts.append("</speak>")
    return "".join(parts)


def _timings_from_marks(words: list[str], marks: list[tuple[str, float]], audio_duration: float) -> list[WordTiming]:
    mark_map = {name: time for name, time in marks}
    timings: list[WordTiming] = []
    for idx, word in enumerate(words):
        mark_name = f"w{idx}"
        start = float(mark_map.get(mark_name, 0.0))
        if idx + 1 < len(words):
            next_mark = f"w{idx + 1}"
            end = float(mark_map.get(next_mark, start + 0.35))
        else:
            end = max(start + 0.25, audio_duration)
        timings.append(WordTiming(word=word, start_seconds=start, end_seconds=end, mark_name=mark_name))
    return timings


def _estimate_mp3_duration_seconds(path: Path) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return float(result.stdout.strip())
    except Exception:
        return max(1.0, path.stat().st_size / 16000)


def _mock_voice(script: ScriptPackage, audio_path: Path, timing_path: Path) -> VoiceResult:
    words = _tokenize_words(script.full_narration)
    timings: list[WordTiming] = []
    cursor = 0.0
    for idx, word in enumerate(words):
        duration = max(0.18, min(0.45, len(word) * 0.04))
        timings.append(
            WordTiming(
                word=word,
                start_seconds=cursor,
                end_seconds=cursor + duration,
                mark_name=f"w{idx}",
            )
        )
        cursor += duration + 0.05
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"MOCK_MP3")
    write_json(
        timing_path,
        {
            "words": [t.__dict__ for t in timings],
            "character_count": len(script.full_narration),
            "mark_count": len(timings),
            "word_count": len(words),
        },
    )
    return VoiceResult(
        audio_path=audio_path,
        timings=timings,
        character_count=len(script.full_narration),
        estimated_cost_usd=0.0,
    )


def synthesize_voice(
    script: ScriptPackage,
    pipeline_config: PipelineConfig,
    pricing: PricingConfig,
    budget: BudgetGuard,
    audio_path: Path,
    timing_path: Path,
    mock: bool = False,
) -> VoiceResult:
    words = _tokenize_words(script.full_narration)
    if not words:
        raise ValueError("Script has no speakable words")

    if mock:
        logger.warning("Voice pipeline running in mock mode")
        return _mock_voice(script, audio_path, timing_path)

    ssml = _build_ssml(words)
    character_count = len(ssml)
    projected_cost = budget.estimate_tts_cost(character_count)
    budget.assert_can_spend(projected_cost, "cloud_tts_studio")

    @with_timeout(pipeline_config.api_timeout_seconds, "cloud_tts_studio")
    def _call_tts() -> VoiceResult:
        from google.cloud import texttospeech_v1beta1 as texttospeech

        client = texttospeech.TextToSpeechClient(transport="rest")
        synthesis_input = texttospeech.SynthesisInput(ssml=ssml)
        voice = texttospeech.VoiceSelectionParams(
            language_code=pipeline_config.tts_language_code,
            name=pipeline_config.tts_voice_name,
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=1.05,
        )
        request = texttospeech.SynthesizeSpeechRequest(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config,
            enable_time_pointing=[texttospeech.SynthesizeSpeechRequest.TimepointType.SSML_MARK],
        )
        response = client.synthesize_speech(request=request)
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(response.audio_content)
        marks = [(tp.mark_name, tp.time_seconds) for tp in response.timepoints]
        duration = _estimate_mp3_duration_seconds(audio_path)
        timings = _timings_from_marks(words, marks, duration)
        return VoiceResult(
            audio_path=audio_path,
            timings=timings,
            character_count=character_count,
            estimated_cost_usd=projected_cost,
        )

    result = _call_tts()
    mark_count = len([t for t in result.timings if t.mark_name.startswith("w")])
    if mark_count < max(1, len(words) // 2):
        raise RuntimeError(
            f"TTS timing verification failed: expected ~{len(words)} word marks, got {mark_count}"
        )
    budget.record_spend(
        projected_cost,
        "cloud_tts_studio",
        metadata={"characters": character_count, "word_count": len(words), "mark_count": mark_count},
    )
    write_json(
        timing_path,
        {
            "words": [t.__dict__ for t in result.timings],
            "character_count": result.character_count,
            "mark_count": mark_count,
            "word_count": len(words),
        },
    )
    logger.info(
        "Voice synthesized: %d words, %d marks, %.2fs span",
        len(words),
        mark_count,
        result.timings[-1].end_seconds if result.timings else 0.0,
    )
    return result
