from __future__ import annotations

import json
import logging
import re
from src.models import ChannelIdentity, PipelineConfig, PricingConfig, Scene, ScriptPackage
from src.module3_budget_guard import BudgetGuard
from src.utils.api_client import retry_once, with_timeout
from src.utils.encoding import write_json

logger = logging.getLogger("shorts_pipeline.script")


def _build_prompt(identity: ChannelIdentity, scene_min: int, scene_max: int) -> str:
    return f"""You are a viral YouTube Shorts scriptwriter specializing in AI/Tech content.
Your benchmark is high-retention storytelling — punchy, question-led, animated explainer style.

Channel identity:
- Niche: {identity.niche}
- Persona: {identity.persona}
- Tone: {identity.tone}
- Audience: {identity.audience}
- Rules: {json.dumps(identity.content_rules)}
- Banned: {json.dumps(identity.banned_topics)}

SCRIPT RULES (non-negotiable):
1. HOOK = a question. Max 6 words. Must create instant curiosity. Never a statement.
   Good: "Did AI just replace your doctor?"
   Bad: "AI is changing healthcare forever."

2. NARRATION = short, punchy sentences. Max 10 words per sentence. Max 2 sentences per scene.
   Write like a copywriter, not a narrator.
   Every sentence must end on a strong word — never a filler word like "and", "the", "it".
   Each scene narration must be speakable in under 6 seconds.

3. PACING = script to the cut. Each scene ends on a beat.
   Scene 1: Hook (question that stops the scroll)
   Scene 2: Tension (why this matters RIGHT NOW)
   Scene 3: Surprise (the thing they didn't expect)
   Scene 4: Proof (one concrete fact or example)
   Scene 5: Payoff (the answer to the hook question)
   Scene 6 (optional): Loop (line that connects back to the hook)

4. VISUAL STYLE = minimalist vector art. Every visual_prompt must include:
   - "minimalist vector art, [color_palette], clean motion graphics"
   - "vertical portrait orientation"
   - A specific subject/scene relevant to the narration
   - Camera/movement instruction (slow zoom in, pan left, static wide shot, etc.)
   
   VISUAL CONSISTENCY: Pick ONE color palette in scene 1 and reference it in every subsequent scene.
   Example palette anchor: "warm orange and deep blue color palette" — repeat this exact phrase in scenes 2-6.
   
   NEVER generate: live action footage, photorealistic renders, real people, news footage, stock photo look.

5. NEGATIVE PROMPT: End every visual_prompt with this exact string:
   "| negative: channel logos, text, typography, words, branding, watermarks, photorealistic, live action, talking heads"

Return ONLY valid JSON:
{{
  "title": "string, question format, <= 60 chars",
  "description": "2 sentence summary + hashtags",
  "tags": ["tag1", "tag2"],
  "hook": "string, question, max 6 words",
  "body": "string, main insight one sentence",
  "loop_ending": "string that loops back to hook question",
  "color_palette": "string, e.g. 'warm orange and deep blue'",
  "scenes": [
    {{
      "index": 1,
      "emotional_beat": "hook|tension|surprise|proof|payoff|loop",
      "narration": "max 2 sentences, max 10 words each, ends on strong word",
      "word_count": 0,
      "visual_prompt": "minimalist vector art, [color_palette], clean motion graphics, vertical portrait orientation, [specific scene description], [camera movement] | negative: channel logos, text, typography, words, branding, watermarks, photorealistic, live action, talking heads"
    }}
  ]
}}

Provide between {scene_min} and {scene_max} scenes.
"""


def _parse_script_payload(payload: dict, identity: ChannelIdentity) -> ScriptPackage:
    scenes = [
        Scene(
            index=int(item["index"]),
            narration=str(item["narration"]).strip(),
            visual_prompt=str(item.get("visual_prompt", "")).strip(),
            emotional_beat=str(item.get("emotional_beat", item.get("hook_type", ""))).strip(),
            hook_type=str(item.get("hook_type", "")).strip(),
        )
        for item in payload.get("scenes", [])
    ]
    scenes.sort(key=lambda s: s.index)
    full_narration = " ".join(scene.narration for scene in scenes)
    tags = payload.get("tags") or identity.default_hashtags
    return ScriptPackage(
        title=str(payload.get("title", "")).strip(),
        description=str(payload.get("description", "")).strip(),
        tags=[str(t).strip() for t in tags],
        hook=str(payload.get("hook", "")).strip(),
        body=str(payload.get("body", "")).strip(),
        loop_ending=str(payload.get("loop_ending", "")).strip(),
        scenes=scenes,
        full_narration=full_narration,
        color_palette=str(payload.get("color_palette", "")).strip(),
        loop_type=str(payload.get("loop_type", "")).strip(),
        comment_trigger=str(payload.get("comment_trigger", "")).strip(),
    )


def _validate_script(scenes: list[Scene]) -> list[str]:
    """Returns list of warnings, empty = pass."""
    warnings = []
    for scene in scenes:
        word_count = len(scene.narration.split())
        if word_count > 20:  # 2 sentences × 10 words max
            warnings.append(f"Scene {scene.index}: narration too long ({word_count} words, max 20)")
        sentences = [s.strip() for s in re.split(r'[.!?]+', scene.narration) if s.strip()]
        for s in sentences:
            if len(s.split()) > 10:
                warnings.append(f"Scene {scene.index}: sentence exceeds 10 words: '{s}'")
        if not any(scene.narration.endswith(p) for p in ['.', '!', '?']):
            warnings.append(f"Scene {scene.index}: narration doesn't end with punctuation")
    return warnings


def _mock_script(identity: ChannelIdentity, scene_count: int) -> ScriptPackage:
    color_palette = "warm orange and deep blue"
    beats = ["hook", "tension", "surprise", "proof", "payoff", "loop"]
    mock_narrations = [
        "Did AI just break the rules?",
        "Your entire workflow is now obsolete.",
        "One tool replaced six departments overnight.",
        "Google confirmed it last Tuesday.",
        "The builders who adapt will dominate.",
        "So... did AI replace you yet?",
    ]
    scenes = []
    for idx in range(1, scene_count + 1):
        beat = beats[idx - 1] if idx <= len(beats) else "payoff"
        narration = mock_narrations[idx - 1] if idx <= len(mock_narrations) else f"Scene {idx} reveals more."
        scenes.append(
            Scene(
                index=idx,
                narration=narration,
                visual_prompt=(
                    f"minimalist vector art, {color_palette} color palette, "
                    f"clean motion graphics, vertical portrait orientation, "
                    f"futuristic AI interface scene {idx}, "
                    f"slow zoom in "
                    f"| negative: channel logos, text, typography, words, branding, watermarks, "
                    f"photorealistic, live action, talking heads"
                ),
                emotional_beat=beat,
            )
        )
    payload = {
        "title": "Did AI just replace your entire workflow?",
        "description": "One shift every builder should know about. #AI #Tech #Shorts",
        "tags": identity.default_hashtags + ["#Automation"],
        "hook": "Did AI just replace your workflow?",
        "body": "Teams are replacing manual content pipelines with budget-guarded automation.",
        "loop_ending": "So... did AI replace your workflow yet?",
        "color_palette": color_palette,
        "loop_type": "question",
        "comment_trigger": "Does your job use AI yet? Tell me below.",
        "scenes": [
            {
                "index": s.index,
                "narration": s.narration,
                "visual_prompt": s.visual_prompt,
                "emotional_beat": s.emotional_beat,
                "hook_type": s.emotional_beat,
            }
            for s in scenes
        ],
    }
    return _parse_script_payload(payload, identity)


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def _script_to_dict(script: ScriptPackage) -> dict:
    return {
        "title": script.title,
        "description": script.description,
        "tags": script.tags,
        "hook": script.hook,
        "body": script.body,
        "loop_ending": script.loop_ending,
        "color_palette": script.color_palette,
        "loop_type": script.loop_type,
        "comment_trigger": script.comment_trigger,
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


def generate_script(
    identity: ChannelIdentity,
    pipeline_config: PipelineConfig,
    pricing: PricingConfig,
    budget: BudgetGuard,
    output_path,
    mock: bool = False,
) -> ScriptPackage:
    if mock:
        logger.warning("Script generator running in mock mode")
        script = _mock_script(identity, pipeline_config.scene_count_min)
        warnings = _validate_script(script.scenes)
        script.validation_warnings = warnings
        for w in warnings:
            logger.warning("Script validation: %s", w)
        write_json(output_path, _script_to_dict(script))
        return script

    prompt = _build_prompt(identity, pipeline_config.scene_count_min, pipeline_config.scene_count_max)
    estimated_input_tokens = max(500, len(prompt) // 4)
    estimated_output_tokens = 1200
    projected_cost = budget.estimate_script_cost(estimated_input_tokens, estimated_output_tokens)
    budget.assert_can_spend(projected_cost, "gemini_script_generation")

    @retry_once("gemini_script_generation", backoff_seconds=15.0)
    @with_timeout(pipeline_config.api_timeout_seconds, "gemini_script_generation")
    def _call_gemini() -> ScriptPackage:
        import os
        from google import genai

        client = genai.Client(
            vertexai=True,
            project=os.environ["GOOGLE_CLOUD_PROJECT"],
            location=os.environ.get("VERTEX_LOCATION", "us-central1")
        )
        response = client.models.generate_content(
            model=pricing.script_model_id,
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        payload = _extract_json(response.text or "{}")
        return _parse_script_payload(payload, identity)

    script = _call_gemini()
    warnings = _validate_script(script.scenes)
    script.validation_warnings = warnings
    for w in warnings:
        logger.warning("Script validation: %s", w)
    budget.record_spend(
        projected_cost,
        "gemini_script_generation",
        metadata={"model": pricing.script_model_id},
    )
    write_json(output_path, _script_to_dict(script))
    logger.info("Script generated with %d scenes", len(script.scenes))
    return script
