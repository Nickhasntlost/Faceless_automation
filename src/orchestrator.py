from __future__ import annotations

import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from src.config_loader import (
    load_channel_identity,
    load_character_bible,
    load_motion_rules,
    load_pipeline_config,
    load_pricing_config,
    load_style_guide,
    load_rhythm_config,
    load_visual_diversity_rules,
)
from src.models import Scene, ScriptPackage, make_run_paths
from src.agents.creative_director import generate_creative_plan
from src.agents.script_writer import generate_script
from src.agents.timestamp_planner import plan_timestamps
from src.agents.retention_director import generate_retention_plan
from src.agents.storyboard_generator import generate_storyboard
from src.agents.scene_planner import plan_scenes
from src.builders.visual_prompt_builder import build_veo_prompt
from src.agents.prompt_linter import lint_veo_prompt
from src.module2_voice import synthesize_voice
from src.module3_budget_guard import BudgetGuard
from src.module4_video import generate_scene_clips
from src.module5_assembly import assemble_final_video
from src.module6_thumbnail import generate_thumbnail
from src.module7_uploader import upload_video
from src.module8_quality_gate import QualityGate
from src.utils.logging_utils import setup_logging

logger = logging.getLogger("shorts_pipeline.orchestrator")


class SimulationFlags:
    def __init__(
        self,
        mock: bool = False,
        skip_upload: bool = False,
        simulate_timeout: bool = False,
        simulate_budget_breach: bool = False,
        simulate_interrupt: bool = False,
    ) -> None:
        self.mock = mock
        self.skip_upload = skip_upload
        self.simulate_timeout = simulate_timeout
        self.simulate_budget_breach = simulate_budget_breach
        self.simulate_interrupt = simulate_interrupt


def _check_ffmpeg() -> None:
    try:
        subprocess.run(["ffmpeg", "-version"], check=True, capture_output=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("ffmpeg is required but was not found on PATH") from exc


def run_pipeline(root: Path, simulation: SimulationFlags, topic: str = None, mode: str = "trend") -> Path:
    _check_ffmpeg()
    pipeline_config = load_pipeline_config(root)
    pricing = load_pricing_config(root)
    identity = load_channel_identity(root)
    style_guide = load_style_guide(root)
    char_bible = load_character_bible(root)
    motion_rules = load_motion_rules(root)
    diversity_rules = load_visual_diversity_rules(root)
    mock = simulation.mock or pipeline_config.mock_mode

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    paths = make_run_paths(root, pipeline_config.output_dir, run_id)
    setup_logging(paths.run_dir)

    budget_store = root / pipeline_config.budget_store_path
    if mock:
        budget_store = paths.run_dir / "budget_counter_mock.json"
    budget = BudgetGuard(
        store_path=budget_store,
        threshold_usd=pipeline_config.budget_threshold_usd,
        total_credit_usd=pipeline_config.total_credit_usd,
        pricing=pricing,
    )

    gate = QualityGate(run_id)

    if simulation.simulate_budget_breach:
        gate.set_fatal("Simulated budget breach before paid calls")
        gate.degrade("budget_guard", "Forced budget breach simulation")
        gate.write_report(paths.quality_report_path)
        return paths.quality_report_path

    if budget.remaining_before_threshold() <= 0:
        gate.set_fatal(
            f"Budget threshold already reached (${budget.cumulative_spend_usd:.2f} / "
            f"${pipeline_config.budget_threshold_usd:.2f})"
        )
        gate.write_report(paths.quality_report_path)
        return paths.quality_report_path

    billing_note = budget.cross_check_billing()
    if billing_note:
        gate.note(billing_note)

    try:
        if simulation.simulate_interrupt:
            raise KeyboardInterrupt("Simulated manual interrupt")

        if simulation.simulate_timeout:
            from src.utils.api_client import APITimeoutError, with_timeout

            @with_timeout(0.001, "simulated_timeout")
            def _timeout_probe():
                import time

                time.sleep(1)

            try:
                _timeout_probe()
            except APITimeoutError as exc:
                gate.set_fatal(str(exc))
                gate.degrade("reliability", "Simulated API timeout")
                gate.write_report(paths.quality_report_path)
                return paths.quality_report_path

        from src.agents.trend_scorer import get_best_trending_topic
        
        final_topic = topic
        if mode == "trend":
            final_topic = get_best_trending_topic(identity, pricing.script_model_id)
            gate.note(f"Trend mode selected topic: {final_topic}")
        elif mode == "hybrid":
            final_topic = get_best_trending_topic(identity, pricing.script_model_id, manual_hint=topic)
            gate.note(f"Hybrid mode selected topic: {final_topic}")
        elif mode == "manual" and not topic:
            final_topic = "Latest breakthrough in technology"
            
        plan = generate_creative_plan(final_topic, identity, pricing.script_model_id, mock=mock)
        full_script = generate_script(plan, identity, pricing.script_model_id, mock=mock)
        timestamp_plan = plan_timestamps(
            full_script.full_script,
            pricing.script_model_id,
            mock=mock,
            target_segments=6,
        )
        if not gate.validate_story_flow(full_script.full_script, timestamp_plan):
            gate.write_report(paths.quality_report_path)
            return paths.quality_report_path

        rhythm_config = load_rhythm_config(root)
        retention = generate_retention_plan(plan, rhythm_config)
        storyboard = generate_storyboard(plan, retention, timestamp_plan)
        planned_scenes, warnings = plan_scenes(storyboard, char_bible, motion_rules, rhythm_config, pricing.script_model_id, mock=mock, diversity_rules=diversity_rules)

        scenes = []
        for s in planned_scenes:
            v_prompt = build_veo_prompt(s, style_guide, char_bible, motion_rules)
            
            # Run the Prompt Linter
            is_valid, error_reason = lint_veo_prompt(v_prompt, s, char_bible, motion_rules, style_guide)
            if not is_valid:
                warnings.append(f"Prompt Linter failed for Scene {s.index}: {error_reason}")
                
            scenes.append(Scene(
                index=s.index,
                narration=s.narration,
                visual_prompt=v_prompt,
                emotional_beat=s.emotional_beat,
            ))
            
        full_narration = " ".join(s.narration for s in scenes)
        
        script = ScriptPackage(
            title=plan.topic,
            description=plan.core_message,
            tags=identity.default_hashtags,
            hook=plan.hook,
            body=plan.core_message,
            loop_ending=plan.core_message if plan.ending_type == "loop" else "",
            scenes=scenes,
            full_narration=full_narration,
            color_palette=style_guide.palette,
            validation_warnings=warnings,
            planned_scenes=planned_scenes
        )

        import json
        def json_default(o):
            return o.__dict__ if hasattr(o, '__dict__') else str(o)
            
        with open(paths.run_dir / "creative_plan.json", "w", encoding="utf-8") as f:
            json.dump(plan.__dict__, f, indent=2, default=json_default)
        with open(paths.run_dir / "storyboard.json", "w", encoding="utf-8") as f:
            json.dump({"items": [item.__dict__ for item in storyboard.items]}, f, indent=2, default=json_default)
        
        script_dict = script.__dict__.copy()
        script_dict["scenes"] = [s.__dict__ for s in script.scenes]
        script_dict["planned_scenes"] = []
        for s in script.planned_scenes:
            sd = s.__dict__.copy()
            if hasattr(s, "pause_points"):
                sd["pause_points"] = [p.__dict__ for p in s.pause_points]
            if hasattr(s, "rhythm_plan"):
                sd["rhythm_plan"] = [b.__dict__ for b in s.rhythm_plan]
            script_dict["planned_scenes"].append(sd)
        with open(paths.run_dir / "script.json", "w", encoding="utf-8") as f:
            json.dump(script_dict, f, indent=2, default=json_default)

        gate.mark_stage_complete("script")
        gate.record_cost("script", budget.estimate_script_cost(800, 1200))
        for w in warnings:
            gate.note(f"Script validation: {w}")

        # --- METRIC-BASED ENGAGEMENT SCORING ---
        # 1. Hook Score = 40% Curiosity + 20% Length + 20% Novelty + 20% Clarity
        hook_words = plan.hook.split()
        curiosity_score = 10.0 if any(w.lower() in ["why", "how", "what", "is", "are", "do", "does", "did", "can", "could", "will", "would", "nobody", "this", "here's", "scientists"] for w in hook_words[:3]) else 5.0
        length_score = 10.0 if 5 <= len(hook_words) <= 12 else max(0.0, 10.0 - abs(len(hook_words) - 8))
        novelty_score = 10.0 if plan.hook_style in ["Contradiction", "Question", "Surprise"] else 6.0
        clarity_score = 5.0 if ("," in plan.hook or ";" in plan.hook) else 10.0
        
        hook_score = round(curiosity_score * 0.4 + length_score * 0.2 + novelty_score * 0.2 + clarity_score * 0.2, 1)
        hook_expl = f"Curiosity: {curiosity_score}, Length: {length_score}, Novelty: {novelty_score}, Clarity: {clarity_score}"
        gate.set_engagement_score("hook", hook_score, hook_expl)
        
        # 2. Story Score
        story_score = 10.0 if len(planned_scenes) == plan.scene_count else 5.0
        story_expl = "Scene count matched plan." if story_score == 10.0 else "Scene count mismatch."
        gate.set_engagement_score("story", story_score, story_expl)
        
        # 3. Motion & Visual Variety Score
        motion_repeats = sum(1 for i in range(1, len(planned_scenes)) if planned_scenes[i].motion == planned_scenes[i-1].motion)
        motion_score = round(max(0.0, 10.0 - (motion_repeats * 2.5)), 1)
        camera_repeats = sum(1 for i in range(1, len(planned_scenes)) if planned_scenes[i].camera == planned_scenes[i-1].camera)
        visual_variety = round(max(0.0, 10.0 - (camera_repeats * 2.0)), 1)
        
        gate.set_engagement_score("motion", motion_score, f"Repeated motions: {motion_repeats}")
        gate.set_engagement_score("visual_variety", visual_variety, f"Repeated cameras: {camera_repeats}")
        
        # 4. Consistency & Originality
        gate.set_engagement_score("consistency", 10.0 if "Character" not in str(warnings) else 5.0, "No character warnings." if "Character" not in str(warnings) else "Character warnings found.")
        gate.set_engagement_score("originality", 9.5 if plan.hook_style else 7.0, f"Hook style: {plan.hook_style}")
        gate.set_engagement_score("retention", 10.0 if retention.surprise_at else 5.0, f"Surprises at scenes: {retention.surprise_at}")
        
        # 5. Rhythm & Breathing
        total_video_duration = sum(s.duration for s in planned_scenes)
        if total_video_duration > 0:
            word_count = len(full_narration.split())
            total_pause = sum(b.silence_target for s in planned_scenes for b in getattr(s, 'rhythm_plan', []))
            
            info_density = word_count / total_video_duration
            info_density_score = round(10.0 if info_density <= 2.5 else max(0.0, 10.0 - (info_density - 2.5)*5.0), 1)
            silence_ratio = total_pause / total_video_duration
            silence_ratio_score = round(10.0 if silence_ratio >= 0.15 else max(0.0, silence_ratio * 66.6), 1)
            
            gate.set_engagement_score("information_density", info_density_score, f"Words/sec: {info_density:.2f}")
            gate.set_engagement_score("silence_ratio", silence_ratio_score, f"Silence ratio: {silence_ratio:.2f}")
            gate.set_engagement_score("pacing_score", info_density_score, f"Derived from info density")
            gate.set_engagement_score("breathing_score", silence_ratio_score, f"Derived from silence ratio")
            
            if info_density_score < 5.0:
                gate.degrade("pacing", "Information density too high (>2.5 words/sec)")
            if silence_ratio_score < 3.0 and not mock:
                gate.degrade("breathing", "Inadequate silences generated")
        
        if hook_score < 8.0:
            gate.degrade("hook", f"Hook score is {hook_score} (needs 8.0+)")
        if motion_score < 8.0:
            gate.degrade("motion", f"Motion score is {motion_score} (needs 8.0+)")
        if warnings:
            gate.degrade("validation", f"Found {len(warnings)} warnings during scene planning/linting.")

        # Compute scene narration boundaries (word indices where each scene starts)
        # so the TTS can insert <break> tags at scene transitions
        import re
        scene_boundaries: list[int] = []
        word_cursor = 0
        for s in scenes:
            if word_cursor > 0:
                scene_boundaries.append(word_cursor)
            scene_word_count = len(re.findall(r"\b[\w']+\b", s.narration))
            word_cursor += scene_word_count

        voice = synthesize_voice(
            script,
            pipeline_config,
            pricing,
            budget,
            paths.voice_audio_path,
            paths.voice_timing_path,
            mock=mock,
            scene_boundaries=scene_boundaries,
        )
        gate.mark_stage_complete("voice")
        gate.record_cost("voice", voice.estimated_cost_usd)

        clip_results = generate_scene_clips(
            script,
            paths.clips_dir,
            pipeline_config,
            pricing,
            budget,
            mock=mock,
        )
        failed_scenes = [r for r in clip_results if not r.success]
        for result in clip_results:
            if result.success:
                gate.record_cost(f"veo_scene_{result.scene_index}", result.estimated_cost_usd)
        if failed_scenes:
            for failed in failed_scenes:
                gate.degrade(
                    "video_generation",
                    f"Scene {failed.scene_index} failed: {failed.error}",
                )
        else:
            gate.mark_stage_complete("video_generation")

        successful_clips = [r.clip_path for r in clip_results if r.success and r.clip_path]
        if not successful_clips:
            gate.set_fatal("No video clips were generated")
            gate.write_report(paths.quality_report_path)
            return paths.quality_report_path

        final_video, captions_ok, caption_detail = assemble_final_video(
            successful_clips,
            voice,
            script,
            paths.assembly_dir,
            paths.verification_dir,
            mock_audio=mock,
        )
        paths.final_video_path = final_video
        gate.note(f"Final video at {final_video}")
        if captions_ok:
            gate.mark_stage_complete("captions")
            gate.note(caption_detail)
        else:
            gate.degrade("captions", caption_detail)

        thumb_path, thumb_error, thumb_skipped = generate_thumbnail(
            script,
            pipeline_config,
            pricing,
            budget,
            paths.thumbnail_path,
            style_guide,
            char_bible,
            motion_rules,
            mock=mock,
        )
        if thumb_skipped:
            gate.note(thumb_error or "Thumbnail skipped by configuration")
        elif thumb_error:
            gate.degrade("thumbnail", thumb_error)
        elif thumb_path:
            gate.mark_stage_complete("thumbnail")
            gate.record_cost("thumbnail", budget.estimate_thumbnail_cost())

        if simulation.skip_upload or mock:
            gate.note("Upload skipped (mock mode or --skip-upload)")
        else:
            video_id, upload_error = upload_video(
                script,
                final_video,
                thumb_path,
                pipeline_config,
                root / "credentials",
                mock=False,
            )
            if upload_error:
                gate.degrade("youtube_upload", upload_error)
            else:
                gate.mark_stage_complete("youtube_upload")
                gate.note(f"Uploaded video id={video_id} with containsSyntheticMedia=true")

        gate.note(
            f"Run cost estimate: ${sum(gate.cost_breakdown.values()):.4f}; "
            f"budget cumulative: ${budget.cumulative_spend_usd:.4f}"
        )
    except KeyboardInterrupt:
        gate.set_incomplete("Pipeline interrupted before natural completion")
        raise
    except Exception as exc:
        gate.set_fatal(str(exc))
    finally:
        gate.write_report(paths.quality_report_path)

    return paths.quality_report_path
