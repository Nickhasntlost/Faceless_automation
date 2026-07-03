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
    pauses = []
    
    current_tts_time = 0.0
    word_idx = 0
    
    for scene in script.planned_scenes:
        for b in getattr(scene, 'rhythm_plan', []):
            target_cut_time = current_tts_time + b.speech_target
            
            if b.silence_target <= 0:
                current_tts_time = target_cut_time
                continue
                
            best_idx = word_idx
            best_diff = float('inf')
            
            for i in range(word_idx, len(voice.timings)):
                diff = abs(voice.timings[i].end_seconds - target_cut_time)
                if diff < best_diff:
                    best_diff = diff
                    best_idx = i
                elif diff > best_diff:
                    # diff is increasing, we passed the closest word
                    break
                    
            if best_idx < len(voice.timings):
                word_end = voice.timings[best_idx].end_seconds
                if best_idx + 1 < len(voice.timings):
                    next_word_start = voice.timings[best_idx+1].start_seconds
                    actual_cut_time = word_end + (next_word_start - word_end) / 2.0
                else:
                    actual_cut_time = word_end + 0.1
                pauses.append((actual_cut_time, b.silence_target))
                current_tts_time = actual_cut_time
                word_idx = best_idx + 1

    # Remove duplicate cut times if any (should be rare, but just in case)
    # If there are duplicates, we merge their silence durations.
    merged_pauses = {}
    for t, dur in pauses:
        merged_pauses[t] = merged_pauses.get(t, 0) + dur
    pauses = sorted(list(merged_pauses.items()), key=lambda x: x[0])
    
    adjusted_timings = []
    for t in voice.timings:
        shift = sum(dur for (timestamp, dur) in pauses if timestamp <= t.start_seconds + 0.01)
        adjusted_timings.append(WordTiming(
            word=t.word,
            start_seconds=t.start_seconds + shift,
            end_seconds=t.end_seconds + shift,
            mark_name=t.mark_name
        ))
    
    if not pauses:
        shutil.copy(audio_path, output_path)
        return adjusted_timings

    filter_parts = []
    inputs_count = 0
    last_t = 0.0
    for idx, (t, dur) in enumerate(pauses):
        filter_parts.append(f"[0:a]atrim={last_t}:{t},asetpts=PTS-STARTPTS[a{idx}]")
        filter_parts.append(f"anullsrc=d={dur}:r=48000:cl=stereo[s{idx}]")
        inputs_count += 2
        last_t = t
        
    filter_parts.append(f"[0:a]atrim={last_t}:9999,asetpts=PTS-STARTPTS[a{len(pauses)}]")
    inputs_count += 1
    
    concat_inputs = "".join(f"[a{i}][s{i}]" for i in range(len(pauses))) + f"[a{len(pauses)}]"
    filter_parts.append(f"{concat_inputs}concat=n={inputs_count}:v=0:a=1[outa]")
    filter_parts.append(f"anoisesrc=c=brown:a=0.005:r=48000[noise_mono]")
    filter_parts.append(f"[noise_mono]aformat=channel_layouts=stereo[noise]")
    filter_parts.append(f"[outa][noise]amix=inputs=2:duration=first:normalize=0[final_audio]")
    
    cmd = [
        "ffmpeg", "-y",
        "-i", str(audio_path),
        "-filter_complex", "; ".join(filter_parts),
        "-map", "[final_audio]",
        str(output_path)
    ]
    _run_ffmpeg(cmd)
    
    return adjusted_timings


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
        "Style: Word,Arial Black,52,&H00FFFFFF,&H000000FF,&H00000000,&H96000000,1,0,0,0,100,100,0,0,1,4,0,2,40,40,120,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    
    # Group words into 2-3 word phrases for readability, but NEVER cross long pauses
    MAX_GROUP_SIZE = 3
    MAX_GAP = 0.4  # seconds
    
    i = 0
    while i < len(timings):
        group = [timings[i]]
        
        # Look ahead to see if we should include next words
        j = i + 1
        while j < len(timings) and len(group) < MAX_GROUP_SIZE:
            gap = timings[j].start_seconds - timings[j-1].end_seconds
            if gap > MAX_GAP:
                break
            group.append(timings[j])
            j += 1
            
        start = _seconds_to_ass_time(group[0].start_seconds)
        end = _seconds_to_ass_time(max(group[-1].end_seconds, group[0].start_seconds + 0.3))
        text = " ".join(t.word.upper() for t in group)
        lines.append(f"Dialogue: 0,{start},{end},Word,,0,0,0,,{text}")
        
        i = j
    
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

    first_word = voice.timings[0].word if voice.timings else script.hook.split()[0]
    sample_time = voice.timings[0].start_seconds if voice.timings else 0.5
    frame_path = verification_dir / "caption_check.jpg"
    extract_frame(final_path, sample_time, frame_path)
    ok, detail = verify_captions_in_frame(frame_path, first_word)
    logger.info("Caption verification: %s", detail)
    return final_path, ok, detail
