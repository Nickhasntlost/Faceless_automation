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
class PausePoint:  # DEPRECATED: use RhythmBeat instead
    after_phrase: str
    duration: float
    type: str
    visual_action: str
    sfx: str


@dataclass
class RhythmBeat:
    name: str
    speech_target: float
    silence_target: float


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
    planned_scenes: list[PlannedScene] = field(default_factory=list)


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
    engagement_scores: dict[str, float] = field(default_factory=dict)
    score_explanations: dict[str, str] = field(default_factory=dict)

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
            "engagement_scores": self.engagement_scores,
            "score_explanations": self.score_explanations,
        }


@dataclass
class StyleGuide:
    art_style: str
    palette: str
    lighting: str
    background_rules: str
    animation_rules: str
    character_rules: str
    aspect_ratio: str
    resolution: str
    negative_prompts: list[str]


@dataclass
class CharacterProfile:
    name: str
    description: str
    colors: list[str]
    expressions: list[str]
    poses: list[str]
    accessories: list[str]
    use_when: list[str]


@dataclass
class CharacterBible:
    characters: dict[str, CharacterProfile]
    selection_rules: list[str]
    topic_mapping: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class MotionPreset:
    name: str
    description: str
    prompt_text: str
    energy: str
    use_for: list[str]
    beats: list[str]


@dataclass
class MotionRules:
    motions: dict[str, MotionPreset]
    rules: list[str]


@dataclass
class RhythmBeatConfig:
    speech: float
    silence: float


@dataclass
class RhythmConfig:
    beats: dict[str, RhythmBeatConfig]
    templates: dict[str, list[str]]


@dataclass
class CreativePlan:
    topic: str
    story_template: str
    viral_angle: str
    curiosity_gap: str
    hook: str
    hook_style: str
    core_message: str
    emotional_arc: list[str]
    emotion_timeline: list[str]
    target_audience: str
    ending_type: str
    cta_style: str
    visual_identity: str
    scene_count: int
    style_id: str


@dataclass
class VisualDiversityRules:
    camera: dict[str, Any]
    background: dict[str, Any]
    motion: dict[str, Any]
    composition: dict[str, Any]
    expressions: dict[str, Any]


@dataclass
class RetentionPlan:
    surprise_at: list[int]
    speed_up_at: list[int]
    zoom_at: list[int]
    new_object_at: list[int]
    energy_curve: list[str]
    retention_hooks: dict[int, str]
    scene_pauses: list[float]  # DEPRECATED
    rhythm_template: str = "default"


@dataclass
class StoryboardItem:
    scene_index: int
    purpose: str
    narration_goal: str
    visual_goal: str
    transition_goal: str
    hook_intensity: str
    curiosity_level: str
    energy_level: str
    scene_type: str
    retention_trigger: str
    emotional_beat: str
    pause_duration: float  # DEPRECATED
    rhythm_template: str = "default"


@dataclass
class Storyboard:
    items: list[StoryboardItem]
    creative_plan: CreativePlan
    retention_plan: RetentionPlan


@dataclass
class PlannedScene:
    index: int
    narration: str
    purpose: str
    emotion: str
    character: str
    expression: str
    action: str
    background: str
    motion: str
    camera: str
    transition: str
    sfx: str
    duration: int
    emotional_beat: str
    retention_trigger: str
    pause_points: list[PausePoint] = field(default_factory=list)  # DEPRECATED
    rhythm_plan: list[RhythmBeat] = field(default_factory=list)
    speech_ratio: float = 0.0
    visual_ratio: float = 0.0
    visual_focus: str = ""  # e.g. "character_closeup", "environment", "data_visualization"

    def __post_init__(self):
        # Migration layer: If legacy data provides pause_points but no rhythm_plan, map it
        if self.pause_points and not self.rhythm_plan:
            # We'll map them generically. In practice you might want to split them logically.
            for p in self.pause_points:
                self.rhythm_plan.append(
                    RhythmBeat(
                        name=f"LEGACY_{p.type.upper()}",
                        speech_target=1.0, 
                        silence_target=p.duration
                    )
                )
        # If rhythm_plan exists, we just ignore pause_points
