from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

from src.models import ScriptPackage, VoiceResult, WordTiming

logger = logging.getLogger("shorts_pipeline.assembly")


def _seconds_to_ass_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:01d}:{minutes:02d}:{secs:05.2f}"


def build_ass_subtitles(timings: list[WordTiming], ass_path: Path) -> None:
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 720",
        "PlayResY: 1280",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Word,Arial Black,56,&H00FFFFFF,&H000000FF,&H00000000,&H96000000,1,0,0,0,100,100,0,0,1,4,0,2,40,40,120,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for timing in timings:
        start = _seconds_to_ass_time(timing.start_seconds)
        end = _seconds_to_ass_time(max(timing.end_seconds, timing.start_seconds + 0.08))
        text = timing.word.upper()
        lines.append(f"Dialogue: 0,{start},{end},Word,,0,0,0,,{text}")
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
            "-shortest",
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
    mixed_path = assembly_dir / "mixed.mp4"
    ass_path = assembly_dir / "captions.ass"
    final_path = assembly_dir / "final_short.mp4"

    build_ass_subtitles(voice.timings, ass_path)
    concat_clips(clip_paths, concat_path)

    if mock_audio or not voice.audio_path.exists() or voice.audio_path.read_bytes() == b"MOCK_MP3":
        _run_ffmpeg(["ffmpeg", "-y", "-i", str(concat_path), "-c", "copy", str(mixed_path)])
    else:
        mix_narration(concat_path, voice.audio_path, mixed_path)

    burn_captions(mixed_path, ass_path, final_path)

    first_word = voice.timings[0].word if voice.timings else script.hook.split()[0]
    sample_time = voice.timings[0].start_seconds if voice.timings else 0.5
    frame_path = verification_dir / "caption_check.jpg"
    extract_frame(final_path, sample_time, frame_path)
    ok, detail = verify_captions_in_frame(frame_path, first_word)
    logger.info("Caption verification: %s", detail)
    return final_path, ok, detail
