from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

from src.models import PipelineConfig, PricingConfig, SceneClipResult, ScriptPackage
from src.module3_budget_guard import BudgetGuard
from src.utils.api_client import retry_once, with_timeout

logger = logging.getLogger("shorts_pipeline.video")


def _mock_clip(scene_index: int, clip_path: Path, duration: int) -> None:
    clip_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=0x101820:s=720x1280:d={duration}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={440 + scene_index * 20}:duration={duration}",
        "-shortest",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(clip_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _augment_veo_prompt(visual_prompt: str, scene_index: int, total_scenes: int) -> str:
    """Enforce style consistency and technical requirements on every Veo prompt."""
    style_anchor = "flat 2D animation style, bold black outlines, The Infographics Show aesthetic"
    technical = "9:16 vertical orientation, 720p, smooth motion"

    if "| negative:" in visual_prompt:
        positive, negative = visual_prompt.split("| negative:", 1)
    else:
        positive = visual_prompt
        negative = "photorealistic, live action, talking heads, text overlays, captions, watermarks, blurry"

    if style_anchor.split(",")[0] not in positive:
        positive = f"{style_anchor}, {positive.strip()}"

    augmented = f"{positive.strip()}, {technical} | negative: {negative.strip()}"
    return augmented


def _generate_single_clip(
    scene,
    clip_path: Path,
    pipeline_config: PipelineConfig,
    pricing: PricingConfig,
    budget: BudgetGuard,
    mock: bool,
    total_scenes: int = 1,
) -> SceneClipResult:
    projected = budget.estimate_veo_cost(pipeline_config.clip_duration_seconds, pipeline_config.video_resolution)
    label = f"veo_scene_{scene.index}"

    try:
        budget.assert_can_spend(projected, label)
    except Exception as exc:
        return SceneClipResult(scene_index=scene.index, clip_path=None, success=False, error=str(exc))

    if mock:
        _mock_clip(scene.index, clip_path, pipeline_config.clip_duration_seconds)
        budget.record_spend(projected, label, metadata={"mock": True, "scene": scene.index})
        return SceneClipResult(
            scene_index=scene.index,
            clip_path=clip_path,
            success=True,
            estimated_cost_usd=projected,
        )

    @retry_once(label, backoff_seconds=pipeline_config.retry_backoff_seconds)
    @with_timeout(pipeline_config.veo_max_poll_seconds, label)
    def _call_veo() -> None:
        import os
        from google import genai
        from google.genai import types

        client = genai.Client(
            vertexai=True,
            project=os.environ["GOOGLE_CLOUD_PROJECT"],
            location=os.environ.get("VERTEX_LOCATION", "us-central1")
        )
        operation = client.models.generate_videos(
            model=pricing.veo_model_id,
            prompt=_augment_veo_prompt(scene.visual_prompt, scene.index, total_scenes),
            config=types.GenerateVideosConfig(
                aspect_ratio=pipeline_config.aspect_ratio,
                duration_seconds=pipeline_config.clip_duration_seconds,
                resolution=pipeline_config.video_resolution,
            ),
        )
        elapsed = 0
        while not operation.done:
            if elapsed >= pipeline_config.veo_max_poll_seconds:
                raise TimeoutError(f"{label} polling exceeded {pipeline_config.veo_max_poll_seconds}s")
            time.sleep(pipeline_config.veo_poll_interval_seconds)
            elapsed += pipeline_config.veo_poll_interval_seconds
            operation = client.operations.get(operation=operation)

        generated = operation.result.generated_videos[0]
        clip_path.parent.mkdir(parents=True, exist_ok=True)
        video_bytes = generated.video.video_bytes
        clip_path.write_bytes(video_bytes)

    try:
        _call_veo()
        budget.record_spend(
            projected,
            label,
            metadata={"scene": scene.index, "duration": pipeline_config.clip_duration_seconds},
        )
        return SceneClipResult(
            scene_index=scene.index,
            clip_path=clip_path,
            success=True,
            estimated_cost_usd=projected,
        )
    except Exception as exc:
        logger.error("Scene %d generation failed: %s", scene.index, exc)
        return SceneClipResult(
            scene_index=scene.index,
            clip_path=None,
            success=False,
            error=str(exc),
            estimated_cost_usd=0.0,
        )


def generate_scene_clips(
    script: ScriptPackage,
    clips_dir: Path,
    pipeline_config: PipelineConfig,
    pricing: PricingConfig,
    budget: BudgetGuard,
    mock: bool = False,
    dry_run: bool = False,
) -> list[SceneClipResult]:
    import json
    
    veo_prompts = []
    results: list[SceneClipResult] = []
    
    for scene in script.scenes:
        augmented = _augment_veo_prompt(scene.visual_prompt, scene.index, len(script.scenes))
        veo_prompts.append({
            "scene": scene.index,
            "core_prompt": scene.visual_prompt,
            "veo_prompt": augmented
        })
        
    clips_dir.parent.mkdir(parents=True, exist_ok=True)
    veo_prompts_file = clips_dir.parent / "video" / "veo_prompts.json"
    veo_prompts_file.parent.mkdir(parents=True, exist_ok=True)
    with open(veo_prompts_file, "w", encoding="utf-8") as f:
        json.dump(veo_prompts, f, indent=2)
        
    if dry_run:
        logger.info("Dry run enabled: Skipping Veo clip generation")
        for scene in script.scenes:
            results.append(SceneClipResult(
                scene_index=scene.index,
                clip_path=clips_dir / f"scene_{scene.index:02d}.mp4",
                success=True,
                estimated_cost_usd=0.0,
            ))
        return results

    for scene in script.scenes:
        clip_path = clips_dir / f"scene_{scene.index:02d}.mp4"
        logger.info("Generating clip for scene %d", scene.index)
        result = _generate_single_clip(scene, clip_path, pipeline_config, pricing, budget, mock, len(script.scenes))
        results.append(result)
    return results
