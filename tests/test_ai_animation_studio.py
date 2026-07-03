import pytest
from pathlib import Path
from src.models import (
    CreativePlan, RetentionPlan, Storyboard, StoryboardItem, PlannedScene,
    StyleGuide, CharacterBible, CharacterProfile, MotionRules, MotionPreset,
    QualityReport, RhythmConfig, RhythmBeat
)
from src.agents.retention_director import generate_retention_plan
from src.agents.storyboard_generator import generate_storyboard
from src.agents.scene_planner import _validate_scenes
from src.builders.visual_prompt_builder import build_veo_prompt
from src.module8_quality_gate import QualityGate
from src.config_loader import load_character_bible, load_motion_rules

def test_creative_plan_structure():
    plan = CreativePlan(
        topic="test", story_template="Mystery", viral_angle="test", curiosity_gap="test", hook="Is this a test?", hook_style="Question",
        core_message="test", emotional_arc=["hook", "tension"], emotion_timeline=["curiosity", "fear"],
        target_audience="testers", ending_type="loop", cta_style="question",
        visual_identity="robot", scene_count=5, style_id="flat_2d"
    )
    assert plan.scene_count == 5
    assert plan.hook == "Is this a test?"

def test_retention_plan_rules():
    plan = CreativePlan(
        topic="test", story_template="Mystery", viral_angle="test", curiosity_gap="test", hook="Is this a test?", hook_style="Question",
        core_message="test", emotional_arc=[], emotion_timeline=[],
        target_audience="testers", ending_type="loop", cta_style="question",
        visual_identity="robot", scene_count=5, style_id="flat_2d"
    )
    rhythm_config = RhythmConfig(
        templates={"Mystery": ["HOOK", "BUILD"]},
        beats={"HOOK": RhythmBeat(name="HOOK", speech_target=2.0, silence_target=0.5), "BUILD": RhythmBeat(name="BUILD", speech_target=2.0, silence_target=0.5)}
    )
    retention = generate_retention_plan(plan, rhythm_config)
    assert retention.surprise_at == [3]
    assert retention.speed_up_at == [2]
    assert retention.zoom_at == [1, 4]
    assert retention.new_object_at == [3, 5]
    assert len(retention.energy_curve) == 5

def test_storyboard_retention_metadata():
    plan = CreativePlan(
        topic="test", story_template="Problem -> Solution", viral_angle="test", curiosity_gap="test", hook="Is this a test?", hook_style="Question",
        core_message="test", emotional_arc=["hook", "tension", "surprise", "proof", "payoff"], 
        emotion_timeline=[], target_audience="testers", ending_type="loop", 
        cta_style="question", visual_identity="robot", scene_count=5, style_id="flat_2d"
    )
    rhythm_config = RhythmConfig(
        templates={"Problem -> Solution": ["HOOK", "BUILD"]},
        beats={"HOOK": RhythmBeat(name="HOOK", speech_target=2.0, silence_target=0.5), "BUILD": RhythmBeat(name="BUILD", speech_target=2.0, silence_target=0.5)}
    )
    retention = generate_retention_plan(plan, rhythm_config)
    storyboard = generate_storyboard(plan, retention)
    assert len(storyboard.items) == 5
    assert storyboard.items[0].hook_intensity == "high"
    assert storyboard.items[2].hook_intensity == "peak" # scene 3

def test_scene_planner_validation():
    valid_rhythm = [
        RhythmBeat(name="HOOK", speech_target=2.0, silence_target=0.5),
        RhythmBeat(name="BUILD", speech_target=2.0, silence_target=0.5)
    ]
    scenes = [
        PlannedScene(
            index=1,
            narration="Short one.",
            purpose="test",
            emotion="test",
            character="robot",
            expression="neutral",
            action="test",
            background="test",
            motion="test",
            camera="test",
            transition="test",
            sfx="test",
            duration=8,
            emotional_beat="test",
            retention_trigger="test",
            rhythm_plan=valid_rhythm,
            speech_ratio=0.5,
            visual_ratio=0.5
        ),
        PlannedScene(
            index=2,
            narration="This sentence is way too long and should definitely trigger the validation warning because it exceeds the ten word limit.",
            purpose="test",
            emotion="test",
            character="robot",
            expression="neutral",
            action="test",
            background="test",
            motion="test",
            camera="test",
            transition="test",
            sfx="test",
            duration=8,
            emotional_beat="test",
            retention_trigger="test",
            rhythm_plan=valid_rhythm,
            speech_ratio=0.5,
            visual_ratio=0.5
        )
    ]
    warnings = _validate_scenes(scenes, expected_beats=[])
    assert len(warnings) > 0
    assert "Scene 2" in warnings[0]

def test_visual_prompt_builder():
    scene = PlannedScene(
        index=1,
        narration="test",
        purpose="test",
        emotion="test",
        character="robot",
        expression="excited",
        action="jumping",
        background="city",
        motion="zoom_in",
        camera="static",
        transition="cut",
        sfx="sfx",
        duration=8,
        emotional_beat="test",
        retention_trigger="test"
    )
    style = StyleGuide("flat 2d", "vibrant", "bright", "simple", "smooth", "simple", "9:16", "720p", ["photo", "real"])
    char_bible = CharacterBible({"robot": CharacterProfile("robot", "cool robot", ["blue"], [], [], [], [])}, [])
    motion_rules = MotionRules({"zoom_in": MotionPreset("zoom_in", "zoom", "slow zoom", "low", [], [])}, [])
    
    prompt = build_veo_prompt(scene, style, char_bible, motion_rules)
    assert "flat 2d, camera: static" in prompt
    assert "of cool robot" in prompt
    assert "expression: excited" in prompt
    assert "motion: slow zoom" in prompt
    assert "--no photo, real" in prompt

def test_engagement_scoring():
    gate = QualityGate("test_run")
    gate.set_engagement_score("hook", 10)
    gate.set_engagement_score("story", 8)
    report = gate.finalize()
    assert report.engagement_scores["hook"] == 10
    assert report.engagement_scores["story"] == 8
