from __future__ import annotations

import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from src.config_loader import load_channel_identity, load_pipeline_config, load_pricing_config
from src.models import make_run_paths
from src.module1_script import generate_script
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


def run_pipeline(root: Path, simulation: SimulationFlags) -> Path:
    _check_ffmpeg()
    pipeline_config = load_pipeline_config(root)
    pricing = load_pricing_config(root)
    identity = load_channel_identity(root)
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

        script = generate_script(
            identity,
            pipeline_config,
            pricing,
            budget,
            paths.script_path,
            mock=mock,
        )
        gate.mark_stage_complete("script")
        gate.record_cost("script", budget.estimate_script_cost(800, 1200))
        for w in script.validation_warnings:
            gate.note(f"Script validation: {w}")

        voice = synthesize_voice(
            script,
            pipeline_config,
            pricing,
            budget,
            paths.voice_audio_path,
            paths.voice_timing_path,
            mock=mock,
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
