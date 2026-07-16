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
    # Semantic tokenization: returns clean alphanumeric words for alignment and character counts,
    # but _build_ssml will now use the raw text to perfectly preserve punctuation.
    return re.findall(r"\b[\w']+\b", text)

def _verify_ssml(ssml: str) -> None:
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(ssml)
    except ET.ParseError as e:
        raise ValueError(f"SSML is not valid XML: {e}")
        
    if root.tag != "speak":
        raise ValueError("SSML root must be <speak>")
        
    for elem in root.iter():
        if elem.tag == "break":
            if "time" not in elem.attrib:
                raise ValueError("<break> tag missing 'time' attribute")
            if not re.match(r"^\d+ms$", elem.attrib["time"]):
                raise ValueError(f"Invalid break time format: {elem.attrib['time']}")
        elif elem.tag == "emphasis":
            if "level" not in elem.attrib:
                raise ValueError("<emphasis> tag missing 'level' attribute")


def _build_ssml(raw_text: str, pause_requests: dict[int, str] = None, emphasis_words: set[str] = None) -> tuple[str, list[str]]:
    """Build SSML with <mark> tags for each word, perfectly preserving original punctuation,
    and injecting <break> tags after specified words.
    
    Args:
        raw_text: The full script with original punctuation.
        pause_requests: Dict mapping word_index to pause_type ("micro", "reaction", "dramatic").
        emphasis_words: Set of lowercase words to wrap in <emphasis> tags.
    Returns:
        (ssml_string, clean_words_list)
    """
    pause_requests = pause_requests or {}
    emphasis_words = emphasis_words or set()
    parts = ["<speak>"]
    clean_words = []
    
    last_end = 0
    word_index = 0
    
    matches = list(re.finditer(r"\b[\w']+\b", raw_text))
    
    for i, match in enumerate(matches):
        word = match.group(0)
        start = match.start()
        end = match.end()
        
        # Add any intervening text (spaces, punctuation) BEFORE the word
        intervening = raw_text[last_end:start]
        if intervening:
            parts.append(xml.sax.saxutils.escape(intervening))
            
        # If the PREVIOUS word requested a semantic pause, insert the break AFTER the punctuation
        # that just followed it (which we just appended as `intervening` text).
        if (word_index - 1) in pause_requests:
            p_type = pause_requests[word_index - 1]
            if p_type == "micro":
                parts.append('<break time="150ms"/>')
            elif p_type == "reaction":
                parts.append('<break time="300ms"/>')
            elif p_type == "dramatic":
                parts.append('<break time="450ms"/>')
                
        # Add the mark tag directly before the word
        parts.append(f'<mark name="w{word_index}"/>')
        
        escaped_word = xml.sax.saxutils.escape(word)
        if word.lower() in emphasis_words:
            parts.append(f'<emphasis level="strong">{escaped_word}</emphasis>')
        else:
            parts.append(escaped_word)
            
        clean_words.append(word)
        last_end = end
        word_index += 1
        
    # Add any trailing punctuation/text after the final word
    trailing = raw_text[last_end:]
    if trailing:
        parts.append(xml.sax.saxutils.escape(trailing))
        
    # If a pause was requested on the very last word
    if (word_index - 1) in pause_requests:
        p_type = pause_requests[word_index - 1]
        if p_type == "micro":
            parts.append('<break time="150ms"/>')
        elif p_type == "reaction":
            parts.append('<break time="300ms"/>')
        elif p_type == "dramatic":
            parts.append('<break time="450ms"/>')
            
    parts.append("</speak>")
    return "".join(parts), clean_words


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
    
    # Build a set of sentence-ending word indices for natural pauses
    sentence_end_indices: set[int] = set()
    raw_text = script.full_narration
    char_idx = 0
    for word_idx, word in enumerate(words):
        pos = raw_text.find(word, char_idx)
        if pos == -1:
            char_idx += len(word)
            continue
        end_pos = pos + len(word)
        # Check if there's a sentence-ending punctuation right after this word
        rest = raw_text[end_pos:end_pos + 3].lstrip()
        if rest and rest[0] in '.!?':
            sentence_end_indices.add(word_idx)
        char_idx = end_pos
    
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
        cursor += duration
        # Add a longer gap after sentence boundaries, short gap otherwise
        if idx in sentence_end_indices:
            cursor += 0.45  # Natural sentence pause
        else:
            cursor += 0.05  # Normal inter-word gap
    
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
    pause_requests: dict[int, str] | None = None,
    dry_run: bool = False,
) -> VoiceResult:
    if mock:
        logger.warning("Voice pipeline running in mock mode")
        return _mock_voice(script, audio_path, timing_path)

    raw_text = script.full_narration
    if not raw_text.strip():
        raise ValueError("Script has no speakable words")
        
    character_count = len(raw_text)
    projected_cost = budget.estimate_elevenlabs_cost(character_count)
    budget.assert_can_spend(projected_cost, "elevenlabs_tts")

    if dry_run:
        logger.info("Dry run enabled: Skipping ElevenLabs TTS")
        # Estimate marks based on words
        words = _tokenize_words(raw_text)
        timings = []
        cursor = 0.0
        for idx, word in enumerate(words):
            duration = 0.25
            timings.append(
                WordTiming(
                    word=word,
                    start_seconds=cursor,
                    end_seconds=cursor + duration,
                    mark_name=f"w{idx}",
                )
            )
            cursor += duration + 0.05
            
        write_json(
            timing_path,
            {
                "words": [t.__dict__ for t in timings],
                "character_count": character_count,
                "mark_count": len(words),
                "word_count": len(words),
            },
        )
        return VoiceResult(
            audio_path=audio_path,
            timings=timings,
            character_count=character_count,
            estimated_cost_usd=0.0,
        )

    @with_timeout(pipeline_config.api_timeout_seconds, "elevenlabs_tts")
    def _call_tts() -> VoiceResult:
        import os
        import requests
        import base64
        
        api_key = os.environ.get("ELEVENLABS_API_KEY")
        voice_id = os.environ.get("ELEVENLABS_VOICE_ID", pipeline_config.tts_voice_name)
        if not api_key or not voice_id:
            raise RuntimeError("ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID must be set in environment")
            
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps"
        headers = {
            "xi-api-key": api_key,
            "Content-Type": "application/json"
        }
        data = {
            "text": raw_text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75
            }
        }
        
        response = requests.post(url, headers=headers, json=data)
        if response.status_code != 200:
            raise RuntimeError(f"ElevenLabs API error {response.status_code}: {response.text}")
            
        payload = response.json()
        audio_bytes = base64.b64decode(payload["audio_base64"])
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(audio_bytes)
        
        alignment = payload.get("alignment", {})
        chars = alignment.get("characters", [])
        starts = alignment.get("character_start_times_seconds", [])
        ends = alignment.get("character_end_times_seconds", [])
        
        timings = []
        current_word = []
        word_start = -1.0
        word_idx = 0
        
        for i, char in enumerate(chars):
            if i >= len(starts) or i >= len(ends):
                break
            if char.isalnum() or char == "'":
                if not current_word:
                    word_start = starts[i]
                current_word.append(char)
            else:
                if current_word:
                    word_str = "".join(current_word)
                    timings.append(WordTiming(
                        word=word_str,
                        start_seconds=word_start,
                        end_seconds=ends[i-1],
                        mark_name=f"w{word_idx}"
                    ))
                    word_idx += 1
                    current_word = []
                    
        if current_word and len(chars) > 0:
            word_str = "".join(current_word)
            timings.append(WordTiming(
                word=word_str,
                start_seconds=word_start,
                end_seconds=ends[-1],
                mark_name=f"w{word_idx}"
            ))
            
        return VoiceResult(
            audio_path=audio_path,
            timings=timings,
            character_count=character_count,
            estimated_cost_usd=projected_cost,
        )

    @with_timeout(pipeline_config.api_timeout_seconds, "google_tts")
    def _call_google_tts() -> VoiceResult:
        import os
        from google.cloud import texttospeech_v1beta1 as texttospeech

        ssml, clean_words = _build_ssml(raw_text, pause_requests=pause_requests)
        _verify_ssml(ssml)

        client = texttospeech.TextToSpeechClient()
        voice_name = os.environ.get("GOOGLE_TTS_VOICE", pipeline_config.tts_voice_name)
        request = texttospeech.SynthesizeSpeechRequest(
            input=texttospeech.SynthesisInput(ssml=ssml),
            voice=texttospeech.VoiceSelectionParams(
                language_code=pipeline_config.tts_language_code,
                name=voice_name,
            ),
            audio_config=texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3,
            ),
            enable_time_pointing=[texttospeech.SynthesizeSpeechRequest.TimepointType.SSML_MARK],
        )
        response = client.synthesize_speech(request=request)

        audio_path.parent.mkdir(parents=True, exist_ok=True)
        audio_path.write_bytes(response.audio_content)

        audio_duration = _estimate_mp3_duration_seconds(audio_path)
        marks = [(tp.mark_name, tp.time_seconds) for tp in response.timepoints]
        timings = _timings_from_marks(clean_words, marks, audio_duration)

        return VoiceResult(
            audio_path=audio_path,
            timings=timings,
            character_count=character_count,
            estimated_cost_usd=budget.estimate_tts_cost(character_count),
        )

    provider = "elevenlabs_tts"
    try:
        result = _call_tts()
    except Exception as exc:
        logger.warning("ElevenLabs TTS failed (%s); falling back to Google TTS", exc)
        result = _call_google_tts()
        provider = "google_tts"

    mark_count = len(result.timings)
    words = _tokenize_words(raw_text)
    if mark_count < max(1, len(words) // 2):
        raise RuntimeError(
            f"TTS timing verification failed: expected ~{len(words)} word marks, got {mark_count}"
        )
    budget.record_spend(
        result.estimated_cost_usd,
        provider,
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
        "Voice synthesized via %s: %d words, %d marks, %.2fs span",
        provider,
        len(words),
        mark_count,
        result.timings[-1].end_seconds if result.timings else 0.0,
    )
    return result
