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
)
from src.models import ScriptPackage, make_run_paths
from src.agents.creative_director import generate_creative_plan
from src.agents.script_writer import generate_script, WORDS_PER_SECOND
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
        dry_run: bool = False,
        deterministic: bool = False,
    ) -> None:
        self.mock = mock
        self.skip_upload = skip_upload
        self.simulate_timeout = simulate_timeout
        self.simulate_budget_breach = simulate_budget_breach
        self.simulate_interrupt = simulate_interrupt
        self.dry_run = dry_run
        self.deterministic = deterministic


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
        
        # Script writer now returns a fully structured ScriptPackage with
        # triple-hook scenes, visual_prompts, loop_type, and comment_trigger.
        # No need for timestamp_planner → retention_director → storyboard_generator
        # → scene_planner → visual_prompt_builder chain — the prompt handles it upstream.
        script = generate_script(plan, identity, pricing.script_model_id, mock=mock, deterministic=simulation.deterministic)
        
        full_narration = script.full_narration
        warnings = list(script.validation_warnings)
        
        # Serialize script output
        import json
        def json_default(o):
            return o.__dict__ if hasattr(o, '__dict__') else str(o)
            
        script_dict = {
            "title": script.title,
            "description": script.description,
            "tags": script.tags,
            "hook": script.hook,
            "body": script.body,
            "loop_ending": script.loop_ending,
            "color_palette": script.color_palette,
            "loop_type": script.loop_type,
            "comment_trigger": script.comment_trigger,
            "psychology_hook": script.psychology_hook,
            "scenes": [
                {
                    "index": s.index,
                    "narration": s.narration,
                    "visual_prompt": s.visual_prompt,
                    "emotional_beat": s.emotional_beat,
                    "hook_type": s.hook_type,
                }
                for s in script.scenes
            ],
            "full_narration": script.full_narration,
            "validation_warnings": script.validation_warnings,
        }

        with open(paths.run_dir / "creative_plan.json", "w", encoding="utf-8") as f:
            json.dump(plan.__dict__, f, indent=2, default=json_default)
        with open(paths.run_dir / "script.json", "w", encoding="utf-8") as f:
            json.dump(script_dict, f, indent=2, default=json_default)

        gate.mark_stage_complete("script")
        gate.record_cost("script", budget.estimate_script_cost(800, 1200))
        for w in warnings:
            gate.note(f"Script validation: {w}")

        # --- METRIC-BASED ENGAGEMENT SCORING ---
        # 1. Hook Score = 40% Curiosity + 20% Length + 20% Novelty + 20% Clarity
        hook_words = script.hook.split()
        clean_words = [w.lower().replace("'", "").strip(".,?!:;") for w in hook_words[:3]]
        curiosity_score = 10.0 if any(w in ["why", "how", "what", "whats", "is", "are", "do", "does", "did", "can", "could", "will", "would", "nobody", "this", "heres", "scientists"] for w in clean_words) else 5.0
        length_score = 10.0 if len(hook_words) <= 6 else max(0.0, 10.0 - (len(hook_words) - 6))
        novelty_score = 10.0 if plan.hook_style in ["Contradiction", "Question", "Surprise", "Curiosity"] else 6.0
        clarity_score = 5.0 if ("," in script.hook or ";" in script.hook) else 10.0

        hook_score = round(curiosity_score * 0.4 + length_score * 0.2 + novelty_score * 0.2 + clarity_score * 0.2, 1)
        hook_expl = f"Curiosity: {curiosity_score}, Length: {length_score}, Novelty: {novelty_score}, Clarity: {clarity_score}"
        gate.set_engagement_score("hook", hook_score, hook_expl)

        # 2. Story Score — check for triple hook structure
        hook_types = {s.hook_type for s in script.scenes}
        has_triple_hook = {"primary", "secondary", "tertiary"} <= hook_types
        story_score = 10.0 if has_triple_hook else 7.0
        story_expl = f"Triple hook: {'yes' if has_triple_hook else 'no'}, Loop type: {script.loop_type}"
        gate.set_engagement_score("story", story_score, story_expl)

        # 3. Visual Variety Score — check visual prompt diversity across scenes
        visual_prompts = [s.visual_prompt for s in script.scenes]
        # Simple check: count how many scenes share the exact same primary subject
        unique_subjects = len(set(vp[:80] for vp in visual_prompts))  # first 80 chars as proxy
        visual_variety = round(min(10.0, unique_subjects / len(visual_prompts) * 10.0), 1) if visual_prompts else 5.0
        gate.set_engagement_score("visual_variety", visual_variety, f"Unique visual prefixes: {unique_subjects}/{len(visual_prompts)}")

        # 4. Consistency & Originality
        gate.set_engagement_score("consistency", 10.0, "Structured scenes from unified prompt")
        gate.set_engagement_score("originality", 9.5 if plan.hook_style else 7.0, f"Hook style: {plan.hook_style}")

        # 5. Loop and Comment Trigger
        gate.set_engagement_score("loop_engineering", 10.0 if script.loop_type else 5.0, f"Loop type: {script.loop_type}")
        gate.set_engagement_score("comment_trigger", 10.0 if script.comment_trigger else 3.0, f"Trigger: {script.comment_trigger[:50] if script.comment_trigger else 'missing'}")

        # 6. Pacing — word density
        total_words = len(full_narration.split())
        estimated_duration = total_words / WORDS_PER_SECOND
        info_density = total_words / max(estimated_duration, 1.0)
        info_density_score = round(10.0 if info_density <= 2.8 else max(0.0, 10.0 - (info_density - 2.8) * 5.0), 1)
        gate.set_engagement_score("pacing_score", info_density_score, f"Words/sec: {info_density:.2f}")

        if hook_score < 8.0:
            gate.degrade("hook", f"Hook score is {hook_score} (needs 8.0+)")
        if warnings:
            gate.degrade("validation", f"Found {len(warnings)} warnings during script validation.")

        # Compute scene-boundary pause requests for TTS.
        # Insert a brief pause at the end of each scene's narration.
        import re
        pause_requests: dict[int, str] = {}
        word_cursor = 0
        for scene in script.scenes:
            seg_word_count = len(re.findall(r"\b[\w']+\b", scene.narration))
            word_cursor += seg_word_count
            # Add a natural breath pause between scenes (not after the last one)
            if scene.index < len(script.scenes):
                pause_requests[word_cursor - 1] = "medium"

        voice = synthesize_voice(
            script,
            pipeline_config,
            pricing,
            budget,
            paths.voice_audio_path,
            paths.voice_timing_path,
            mock=mock,
            pause_requests=pause_requests,
            dry_run=simulation.dry_run,
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
            dry_run=simulation.dry_run,
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
        if not successful_clips and not simulation.dry_run:
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
        ) if not simulation.dry_run else (paths.assembly_dir / "dry_run_final.mp4", True, "Captions skipped in dry run")
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

        if simulation.skip_upload or mock or simulation.dry_run:
            gate.note("Upload skipped (mock mode or --skip-upload or --dry-run)")
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

        gate.note("=== Pipeline Cost Report ===")
        for k, v in gate.cost_breakdown.items():
            gate.note(f"- {k}: ${v:.4f}")
        gate.note(f"Total Run Cost: ${sum(gate.cost_breakdown.values()):.4f}")
        gate.note(f"Budget Cumulative Spend: ${budget.cumulative_spend_usd:.4f}")
        gate.note("==========================")
        
        # Generate Dashboard
        dashboard_lines = [
            f"# Run Summary (ID: {run_id})",
            f"",
            f"**Topic:** {script.title}",
            f"**Hook:** {script.hook}",
            f"**Script Score:** {gate.engagement_scores.get('script_metrics', 0.0)} / 10",
            f"**Estimated Cost:** ${sum(gate.cost_breakdown.values()):.4f}",
            f"",
            f"## Warnings",
        ]
        if warnings:
            for w in warnings:
                dashboard_lines.append(f"- {w}")
        else:
            dashboard_lines.append("None")
            
        dashboard_lines.append("")
        dashboard_lines.append(f"## Quality Verdict: {gate.finalize().verdict}")
        
        with open(paths.run_dir / "dashboard.md", "w", encoding="utf-8") as df:
            df.write("\\n".join(dashboard_lines))
            
    except KeyboardInterrupt:
        gate.set_incomplete("Pipeline interrupted before natural completion")
        raise
    except Exception as exc:
        gate.set_fatal(str(exc))
    finally:
        gate.write_report(paths.quality_report_path)

    return paths.quality_report_path
