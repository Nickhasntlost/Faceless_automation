from __future__ import annotations

import logging
from pathlib import Path

from src.builders.visual_prompt_builder import build_veo_prompt
from src.models import CharacterBible, MotionRules, PipelineConfig, PlannedScene, PricingConfig, ScriptPackage, StyleGuide
from src.module3_budget_guard import BudgetGuard
from src.utils.api_client import retry_once, with_timeout

logger = logging.getLogger("shorts_pipeline.thumbnail")


def _overlay_text(image_path: Path, title: str) -> None:
    from PIL import Image, ImageDraw, ImageFont

    image = Image.open(image_path).convert("RGBA")
    w, h = image.size
    new_w = int(h * (9/16))
    left = (w - new_w) // 2
    top = 0
    right = left + new_w
    bottom = h
    image = image.crop((left, top, right, bottom)).resize((720, 1280))
    draw = ImageDraw.Draw(image)

    font_paths = [
        Path(__file__).resolve().parents[1] / "assets" / "fonts" / "DejaVuSans-Bold.ttf",
        Path("C:/Windows/Fonts/arial.ttf"),
    ]
    font = None
    for fp in font_paths:
        if fp.exists():
            try:
                font = ImageFont.truetype(str(fp), 48)
                break
            except OSError:
                pass
    if font is None:
        font = ImageFont.load_default()

    import textwrap
    text = title[:60].upper()
    # Wrap text to fit within ~600px width (approx 20 chars at size 48)
    wrapped_text = "\n".join(textwrap.wrap(text, width=22))
    
    bbox = draw.multiline_textbbox((0, 0), wrapped_text, font=font, align="center")
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (image.width - text_w) // 2
    y = int(image.height * 0.72)
    draw.rectangle((x - 12, y - 8, x + text_w + 12, y + text_h + 8), fill=(0, 0, 0, 180))
    draw.multiline_text((x, y), wrapped_text, fill=(255, 255, 255, 255), font=font, align="center")
    image.convert("RGB").save(image_path)


def _mock_thumbnail(output_path: Path, title: str) -> Path:
    from PIL import Image

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (720, 1280), color=(18, 24, 38))
    image.save(output_path)
    _overlay_text(output_path, title)
    return output_path


def generate_thumbnail(
    script: ScriptPackage,
    pipeline_config: PipelineConfig,
    pricing: PricingConfig,
    budget: BudgetGuard,
    output_path: Path,
    style_guide: StyleGuide,
    char_bible: CharacterBible,
    motion_rules: MotionRules,
    mock: bool = False,
) -> tuple[Path | None, str | None, bool]:
    if not pipeline_config.thumbnail_enabled:
        return None, "Thumbnail generation disabled in pipeline_config.json", True

    projected = budget.estimate_thumbnail_cost()
    label = "thumbnail_generation"

    try:
        budget.assert_can_spend(projected, label)
    except Exception as exc:
        return None, str(exc), False

    if mock:
        path = _mock_thumbnail(output_path, script.title)
        budget.record_spend(projected, label, metadata={"mock": True})
        return path, None, False

    # We need a dummy scene to generate a prompt for the thumbnail
    dummy_scene = PlannedScene(
        index=0,
        narration="",
        purpose="thumbnail",
        emotion="excited",
        character="robot",  # default character
        expression="excited",
        action="looking at camera",
        background="solid or gradient",
        motion="static_wide",
        camera="static wide shot",
        transition="",
        sfx="",
        duration=0,
        emotional_beat="",
        retention_trigger=""
    )
    if script.scenes and hasattr(script, "planned_scenes") and script.planned_scenes:
        first_scene = script.planned_scenes[0]
        dummy_scene.character = first_scene.character
        dummy_scene.background = first_scene.background

    base_prompt = build_veo_prompt(dummy_scene, style_guide, char_bible, motion_rules)
    
    prompt = (
        f"{base_prompt}. "
        f"Topic: {script.title}. "
        f"Aspect ratio: 16:9 horizontal format. "
        f"High contrast, eye-catching composition. "
    )

    @retry_once(label, backoff_seconds=15.0)
    @with_timeout(pipeline_config.api_timeout_seconds, label)
    def _call_image_model() -> None:
        import os
        from google import genai

        client = genai.Client(
            vertexai=True,
            project=os.environ["GOOGLE_CLOUD_PROJECT"],
            location=os.environ.get("VERTEX_LOCATION", "us-central1")
        )
        response = client.models.generate_content(
            model=pipeline_config.thumbnail_model or pricing.thumbnail_model_id,
            contents=prompt,
        )
        for part in response.candidates[0].content.parts:
            if getattr(part, "inline_data", None) is not None:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(part.inline_data.data)
                return
        raise RuntimeError("Thumbnail model returned no image bytes")

    try:
        _call_image_model()
        _overlay_text(output_path, script.title)
        budget.record_spend(projected, label, metadata={"model": pricing.thumbnail_model_id})
        logger.info("Thumbnail saved to %s", output_path)
        return output_path, None, False
    except Exception as exc:
        logger.error("Thumbnail generation failed: %s", exc)
        return None, str(exc), False
