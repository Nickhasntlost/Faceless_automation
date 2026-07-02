from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PipelinePaths:
    root: Path
    run_id: str
    run_dir: Path
    script_path: Path
    voice_audio_path: Path
    voice_timing_path: Path
    clips_dir: Path
    assembly_dir: Path
    thumbnail_path: Path
    final_video_path: Path
    quality_report_path: Path
    verification_dir: Path


def make_run_paths(root: Path, output_dir: str, run_id: str) -> PipelinePaths:
    run_dir = root / output_dir / run_id
    assembly_dir = run_dir / "assembly"
    return PipelinePaths(
        root=root,
        run_id=run_id,
        run_dir=run_dir,
        script_path=run_dir / "script.json",
        voice_audio_path=run_dir / "narration.mp3",
        voice_timing_path=run_dir / "word_timings.json",
        clips_dir=run_dir / "clips",
        assembly_dir=assembly_dir,
        thumbnail_path=run_dir / "thumbnail.png",
        final_video_path=assembly_dir / "final_short.mp4",
        quality_report_path=run_dir / "quality_report.json",
        verification_dir=run_dir / "verification",
    )


@dataclass
class PipelineConfig:
    scene_count_min: int
    scene_count_max: int
    clip_duration_seconds: int
    video_resolution: str
    aspect_ratio: str
    budget_threshold_usd: float
    total_credit_usd: float
    budget_buffer_usd: float
    budget_store_path: str
    output_dir: str
    upload_privacy_status: str
    youtube_category_id: str
    tts_voice_name: str
    tts_language_code: str
    api_timeout_seconds: int
    veo_poll_interval_seconds: int
    veo_max_poll_seconds: int
    retry_backoff_seconds: int
    thumbnail_enabled: bool
    thumbnail_model: str
    mock_mode: bool


@dataclass
class PricingConfig:
    verified_at: str
    script_model_id: str
    script_input_usd_per_1m_tokens: float
    script_output_usd_per_1m_tokens: float
    veo_model_id: str
    veo_usd_per_second: dict[str, float]
    thumbnail_model_id: str
    thumbnail_usd_per_1k_image: float
    tts_usd_per_1m_characters: float


@dataclass
class ChannelIdentity:
    niche: str
    persona: str
    tone: str
    audience: str
    content_rules: list[str]
    banned_topics: list[str]
    default_hashtags: list[str]


@dataclass
class Scene:
    index: int
    narration: str
    visual_prompt: str
    emotional_beat: str = ""


@dataclass
class ScriptPackage:
    title: str
    description: str
    tags: list[str]
    hook: str
    body: str
    loop_ending: str
    scenes: list[Scene]
    full_narration: str
    color_palette: str = ""
    estimated_script_tokens: int = 0
    validation_warnings: list[str] = field(default_factory=list)


@dataclass
class WordTiming:
    word: str
    start_seconds: float
    end_seconds: float
    mark_name: str


@dataclass
class VoiceResult:
    audio_path: Path
    timings: list[WordTiming]
    character_count: int
    estimated_cost_usd: float


@dataclass
class SceneClipResult:
    scene_index: int
    clip_path: Path | None
    success: bool
    error: str | None = None
    estimated_cost_usd: float = 0.0


@dataclass
class Degradation:
    subsystem: str
    reason: str


@dataclass
class QualityReport:
    verdict: str
    run_id: str
    degradations: list[Degradation] = field(default_factory=list)
    cost_usd: float = 0.0
    cost_breakdown: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    fatal_error: str | None = None
    incomplete: bool = False
    completed_stages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "run_id": self.run_id,
            "degradations": [d.__dict__ for d in self.degradations],
            "cost_usd": round(self.cost_usd, 4),
            "cost_breakdown": {k: round(v, 4) for k, v in self.cost_breakdown.items()},
            "notes": self.notes,
            "fatal_error": self.fatal_error,
            "incomplete": self.incomplete,
            "completed_stages": self.completed_stages,
        }
