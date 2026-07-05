from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

from src.models import ScriptPackage, VoiceResult, WordTiming
import shutil

logger = logging.getLogger("shorts_pipeline.assembly")

def assemble_audio_timeline(
    audio_path: Path, 
    script: ScriptPackage, 
    voice: VoiceResult, 
    output_path: Path
) -> list[WordTiming]:
    shutil.copy(audio_path, output_path)
    return voice.timings


def _seconds_to_ass_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:01d}:{minutes:02d}:{secs:05.2f}"


def build_ass_subtitles(timings: list[WordTiming], ass_path: Path, script: ScriptPackage | None = None) -> None:
    # Gather emphasis words from script
    emphasis_words_set = set()
    if script:
        for scene in script.planned_scenes:
            for w in getattr(scene, 'emphasis_words', []):
                emphasis_words_set.add(w.lower().strip(".,!?"))
                
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 720",
        "PlayResY: 1280",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,Arial Black,56,&H00FFFFFF,&H000000FF,&H00000000,&H90000000,1,0,0,0,100,100,0,0,4,3,0,2,40,40,80,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    
    # Phrase boundary logic
    def is_boundary(w1: WordTiming, w2: WordTiming) -> bool:
        if any(p in w1.word for p in ['.', ',', '?', '!', '—', ':']):
            return True
        if w2.word.lower() in ['and', 'but', 'or', 'because', 'so', 'then']:
            return True
        if w2.start_seconds - w1.end_seconds > 0.35:
            return True
        return False

    # Group into phrases (3-4 words per chunk)
    phrases = []
    current_phrase = []
    
    for i, t in enumerate(timings):
        current_phrase.append(t)
        if i == len(timings) - 1 or len(current_phrase) >= 4 or is_boundary(t, timings[i+1]):
            phrases.append(current_phrase)
            current_phrase = []
            
    # Generate ASS events
    for phrase in phrases:
        phrase_start = _seconds_to_ass_time(phrase[0].start_seconds)
        phrase_end = _seconds_to_ass_time(max(phrase[-1].end_seconds, phrase[0].start_seconds + 0.3))
        
        # Base event: full phrase in white, layer 0
        plain_text = " ".join(w.word.upper() for w in phrase)
        lines.append(f"Dialogue: 0,{phrase_start},{phrase_end},Default,,0,0,0,,{plain_text}")
        
        # Highlight events: one per word, layer 1
        for i, word in enumerate(phrase):
            active_start = word.start_seconds
            active_end = phrase[i+1].start_seconds if i < len(phrase) - 1 else max(word.end_seconds, active_start + 0.1)
                
            ass_start = _seconds_to_ass_time(active_start)
            ass_end = _seconds_to_ass_time(active_end)
            
            styled_words = []
            for w in phrase:
                text = w.word.upper()
                if w == word:
                    styled_words.append(f"{{\\c&H00FFFF&}}{text}{{\\r}}")
                else:
                    styled_words.append(text)
                    
            event_text = " ".join(styled_words)
            lines.append(f"Dialogue: 1,{ass_start},{ass_end},Default,,0,0,0,,{event_text}")
    
    ass_path.parent.mkdir(parents=True, exist_ok=True)
    ass_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_ffmpeg(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "ffmpeg failed")


def concat_clips(clip_paths: list[Path], output_path: Path) -> None:
    list_file = output_path.parent / "concat_list.txt"
    list_file.write_text(
        "\n".join(f"file '{path.resolve().as_posix()}'" for path in clip_paths) + "\n",
        encoding="utf-8",
    )
    _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c",
            "copy",
            str(output_path),
        ]
    )


def mix_narration(video_path: Path, audio_path: Path, output_path: Path) -> None:
    _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            str(output_path),
        ]
    )


def burn_captions(video_path: Path, ass_path: Path, output_path: Path) -> None:
    ass_posix = ass_path.resolve().as_posix().replace(":", r"\:")
    _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vf",
            f"ass='{ass_posix}'",
            "-c:a",
            "copy",
            str(output_path),
        ]
    )


def extract_frame(video_path: Path, timestamp_seconds: float, frame_path: Path) -> None:
    frame_path.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-ss",
            str(timestamp_seconds),
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            str(frame_path),
        ]
    )


def verify_captions_in_frame(frame_path: Path, reference_word: str) -> tuple[bool, str]:
    if not frame_path.exists():
        return False, f"Verification frame missing: {frame_path}"
    image = Image.open(frame_path).convert("L")
    width, height = image.size
    lower = np.array(image.crop((0, int(height * 0.65), width, height)))
    upper = np.array(image.crop((0, 0, width, int(height * 0.35))))
    lower_var = float(lower.var())
    upper_var = float(upper.var())
    edge_boost = lower_var / max(upper_var, 1.0)
    if edge_boost < 1.15:
        return False, (
            f"Caption region variance ratio too low ({edge_boost:.2f}); "
            f"expected visible text near '{reference_word}'"
        )
    return True, f"Caption region shows elevated contrast (ratio {edge_boost:.2f})"


def assemble_final_video(
    clip_paths: list[Path],
    voice: VoiceResult,
    script: ScriptPackage,
    assembly_dir: Path,
    verification_dir: Path,
    mock_audio: bool = False,
) -> tuple[Path, bool, str]:
    assembly_dir.mkdir(parents=True, exist_ok=True)
    verification_dir.mkdir(parents=True, exist_ok=True)

    concat_path = assembly_dir / "concatenated.mp4"
    padded_audio_path = assembly_dir / "padded_narration.mp3"
    mixed_path = assembly_dir / "mixed.mp4"
    ass_path = assembly_dir / "captions.ass"
    final_path = assembly_dir / "final_short.mp4"

    adjusted_timings = voice.timings
    if voice.audio_path.exists() and voice.audio_path.read_bytes() != b"MOCK_MP3":
        adjusted_timings = assemble_audio_timeline(voice.audio_path, script, voice, padded_audio_path)
        
    # Calculate scene durations from adjusted timings to sync video perfectly
    import re
    scene_boundaries = [0]
    word_cursor = 0
    for s in script.planned_scenes:
        word_count = len(re.findall(r"\b[\w']+\b", s.narration))
        word_cursor += word_count
        scene_boundaries.append(word_cursor)
        
    scene_durations = []
    if not adjusted_timings:
        scene_durations = [8.0] * len(script.planned_scenes)
    else:
        last_time = 0.0
        for i in range(len(script.planned_scenes)):
            end_idx = scene_boundaries[i+1]
            if end_idx < len(adjusted_timings):
                end_time = adjusted_timings[end_idx].start_seconds
            else:
                end_time = adjusted_timings[-1].end_seconds
            
            duration = end_time - last_time
            scene_durations.append(duration)
            last_time = end_time

    # Trim clips to match audio duration perfectly
    trimmed_clips = []
    for i, clip_path in enumerate(clip_paths):
        dur = scene_durations[i] if i < len(scene_durations) else 8.0
        if dur <= 0:
            continue
        trimmed_path = clip_path.parent / f"trimmed_{clip_path.name}"
        _run_ffmpeg([
            "ffmpeg", "-y",
            "-i", str(clip_path),
            "-t", f"{dur:.3f}",
            "-c", "copy",
            str(trimmed_path)
        ])
        trimmed_clips.append(trimmed_path)

    build_ass_subtitles(adjusted_timings, ass_path)
    concat_clips(trimmed_clips, concat_path)

    if mock_audio or not voice.audio_path.exists() or voice.audio_path.read_bytes() == b"MOCK_MP3":
        _run_ffmpeg(["ffmpeg", "-y", "-i", str(concat_path), "-c", "copy", str(mixed_path)])
    else:
        mix_narration(concat_path, padded_audio_path, mixed_path)

    burn_captions(mixed_path, ass_path, final_path)

    # Background music mix
    import random
    import shutil
    music_dir = assembly_dir.parent.parent.parent / "assets" / "music"
    tracks = list(music_dir.glob("*.mp3")) + list(music_dir.glob("*.wav")) if music_dir.exists() else []
    
    if tracks:
        track = random.choice(tracks)
        final_video_duration = sum(scene_durations)
        temp_music_path = assembly_dir / "final_with_music.mp4"
        _run_ffmpeg([
            "ffmpeg", "-y",
            "-i", str(final_path),
            "-stream_loop", "-1",
            "-i", str(track),
            "-t", str(final_video_duration),
            "-filter_complex",
            f"[1:a]volume=0.10,afade=t=out:st={max(0, final_video_duration - 2)}:d=2[music];[0:a][music]amix=inputs=2:duration=first[aout]",
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            str(temp_music_path)
        ])
        shutil.move(str(temp_music_path), str(final_path))
        logger.info(f"Mixed background music: {track.name}")
    else:
        logger.info("No music tracks found in assets/music/, skipping background music.")

    first_word = voice.timings[0].word if voice.timings else script.hook.split()[0]
    sample_time = voice.timings[0].start_seconds if voice.timings else 0.5
    frame_path = verification_dir / "caption_check.jpg"
    extract_frame(final_path, sample_time, frame_path)
    ok, detail = verify_captions_in_frame(frame_path, first_word)
    logger.info("Caption verification: %s", detail)
    return final_path, ok, detail
