from __future__ import annotations

import json
import logging
import os
import re

from src.models import ChannelIdentity, CreativePlan, Scene, ScriptPackage

logger = logging.getLogger('shorts_pipeline.script_writer')

MIN_DURATION_SECONDS = 15.0
MAX_DURATION_SECONDS = 33.0
WORDS_PER_SECOND = 2.5

# Per-scene word limits (index 1-6)
SCENE_WORD_LIMITS = {1: 6, 2: 12, 3: 12, 4: 12, 5: 10, 6: 15}


def estimate_spoken_duration(text: str) -> float:
    words = re.findall(r"""\b[\w']+\b""", text)
    return round(len(words) / WORDS_PER_SECOND, 1)


def _validate_script(scenes: list[Scene], script_data: dict) -> tuple[list[str], list[str]]:
    """Validate the structured script. Returns (errors, warnings)."""
    errors = []
    warnings = []

    # Hard: must have 6 scenes
    if len(scenes) != 6:
        errors.append(f"Script has {len(scenes)} scenes — must have exactly 6")

    # Hard: word count per scene
    for scene in scenes:
        words = len(scene.narration.split())
        limit = SCENE_WORD_LIMITS.get(scene.index, 12)
        # Give a +2 word tolerance for model counting errors
        if words > limit + 2:
            errors.append(f"Scene {scene.index}: {words} words exceeds {limit} word limit (with +2 tolerance)")
        elif words > limit:
            warnings.append(f"Scene {scene.index}: {words} words is slightly over the {limit} word target")

    # Hard: hook scene (index 1) must be max 6 words (+2 tolerance)
    hook_scene = next((s for s in scenes if s.index == 1), None)
    if hook_scene and len(hook_scene.narration.split()) > 8:
        errors.append(f"Scene 1 hook is {len(hook_scene.narration.split())} words — max 6 (+2 tolerance)")

    # Hard: total word count 60-72
    total_words = sum(len(s.narration.split()) for s in scenes)
    if total_words > 72:
        errors.append(f"Total word count {total_words} exceeds 72 max")

    # Hard: comment_trigger field must exist
    if not script_data.get("comment_trigger"):
        errors.append("Missing comment_trigger field")

    # Hard: psychology_hook must be present and non-empty
    if not script_data.get("psychology_hook"):
        errors.append("Missing psychology_hook — every script must reference a named psychological phenomenon")

    # Hard: loop_type field must exist and be valid
    loop_type = script_data.get("loop_type", "")
    if not loop_type:
        errors.append("Missing loop_type field")
    elif loop_type not in ("question", "visual", "statement"):
        errors.append(f"Invalid loop_type '{loop_type}' — must be question, visual, or statement")

    # Hard: duration check
    full_narration = " ".join(s.narration for s in scenes)
    duration = estimate_spoken_duration(full_narration)
    if not MIN_DURATION_SECONDS <= duration <= MAX_DURATION_SECONDS:
        errors.append(
            f"Script duration {duration:.1f}s outside {MIN_DURATION_SECONDS:.0f}-{MAX_DURATION_SECONDS:.0f}s range"
        )

    # Warning: scene 3 should be secondary hook
    scene_3 = next((s for s in scenes if s.index == 3), None)
    if scene_3 and scene_3.hook_type not in ("secondary", "tension"):
        warnings.append("Scene 3 should be secondary hook or tension for retention")

    # Warning: visual loop check (Scene 1 vs Scene 6 shared nouns)
    if len(scenes) == 6:
        scene_1 = next((s for s in scenes if s.index == 1), None)
        scene_6 = next((s for s in scenes if s.index == 6), None)
        if scene_1 and scene_6:
            # Extract significant nouns (words > 3 chars, lowered)
            vp1_words = set(w.lower() for w in re.findall(r'\b[a-zA-Z]{4,}\b', scene_1.visual_prompt))
            vp6_words = set(w.lower() for w in re.findall(r'\b[a-zA-Z]{4,}\b', scene_6.visual_prompt))
            # Remove common style words that appear in every prompt
            style_words = {
                "flat", "animation", "style", "bold", "black", "outlines", "infographics",
                "show", "aesthetic", "clean", "motion", "graphics", "vertical", "frame",
                "text", "overlays", "captions", "watermarks", "blurry", "negative",
                "photorealistic", "live", "action", "talking", "heads", "color", "palette",
            }
            vp1_nouns = vp1_words - style_words
            vp6_nouns = vp6_words - style_words
            shared = vp1_nouns & vp6_nouns
            if not shared:
                warnings.append(
                    "Visual loop weak: Scene 1 and Scene 6 visual_prompts share no subject nouns. "
                    "Loop effect may not work visually."
                )

    # Warning: hook must appear in narration
    hook = script_data.get("hook", "")
    if hook and scenes:
        scene_1 = next((s for s in scenes if s.index == 1), None)
        if scene_1 and hook.strip() not in scene_1.narration:
            warnings.append("Hook text doesn't match Scene 1 narration verbatim")

    return errors, warnings


def _build_prompt(plan: CreativePlan, identity: ChannelIdentity) -> str:
    plan_dict = plan.__dict__.copy()
    if 'scene_count' in plan_dict:
        del plan_dict['scene_count']

    content_angles = json.dumps(getattr(identity, 'content_angles', []))

    return f'''You are an expert viral YouTube Shorts scriptwriter for an AI/Tech × Psychology channel.
Your benchmark: The Infographics Show. Your goal: 65%+ retention, maximum comments.

CHANNEL IDENTITY:
- Niche: {identity.niche}
- Persona: {identity.persona}
- Tone: {identity.tone}
- Audience: {identity.audience}
- Rules: {json.dumps(identity.content_rules)}
- Content Angles: {content_angles}

Creative direction:
{json.dumps(plan_dict, indent=2)}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHANNEL CONCEPT (read this first)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

This channel explains AI and tech through the lens of human psychology.
Every single video MUST:
1. Start with an AI/Tech development or fact (the hook)
2. Connect it to a REAL, NAMED psychological phenomenon (the insight)
3. Make the viewer feel that phenomenon personally (the retention mechanic)

NAMED PSYCHOLOGICAL PHENOMENA TO USE (rotate through these):
- Confirmation bias: tendency to seek information that confirms existing beliefs
- Cognitive dissonance: discomfort from holding contradictory beliefs
- The uncanny valley: discomfort triggered by almost-but-not-quite human things
- Parasocial relationships: one-sided emotional bonds with media personalities/AI
- Dunning-Kruger effect: incompetent people overestimate their ability
- Operant conditioning: behavior shaped by rewards and punishments
- The IKEA effect: we value things more when we build them ourselves
- Availability heuristic: judging likelihood by how easily examples come to mind
- Social proof: doing what others do because it feels safer
- Loss aversion: losses hurt twice as much as equivalent gains feel good
- Mere exposure effect: we like things more the more we're exposed to them
- Pattern recognition: our brain finds patterns even where none exist
- Decision fatigue: quality of decisions deteriorates after making many choices
- The sunk cost fallacy: continuing something because of past investment
- Anthropomorphism: attributing human characteristics to non-human things

CONTENT ANGLE FORMULA:
"[AI/Tech fact] + [Named psychological effect] = [Personal insight about the viewer]"

Example scripts:
- "ChatGPT just passed the Turing test. Your brain's anthropomorphism reflex already knew."
- "TikTok's algorithm uses operant conditioning. You're the rat in the experiment."
- "You trust AI-generated faces more than real ones. That's the mere exposure effect working against you."
- "Every time you use AI to write, the IKEA effect loses. Here's why that matters."

RULE: If a script doesn't have a named psychological phenomenon in Scene 3 or 4 — reject it and try again.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
THE TRIPLE HOOK SYSTEM (non-negotiable)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HOOK 1 — PRIMARY (Scene 1, 0-2 seconds):
- Opens the curiosity gap. Creates information tension immediately.
- Must be a question OR a shocking statement that feels incomplete.
- Max 6 words. Must work with audio OFF (viewer reads captions only).
- Templates to rotate through:
  * "Did you know [company] just [shocking action]?"
  * "Everyone thinks [X]. They're completely wrong."
  * "The AI tool [company] doesn't want you to see."
  * "This changes everything about [familiar concept]."
  * "[Number] seconds. That's how long before [scary outcome]."
- NEVER start with: "Hey", "Welcome", "Today", "In this video", "Let me tell you"

HOOK 2 — SECONDARY (Scene 3, 10-15 seconds into video):
- Re-engages viewers who are considering swiping after the opening.
- Introduces a NEW angle or unexpected fact they didn't see coming.
- Must feel like a plot twist, not a continuation.
- Template: "But here's what nobody is talking about..." or "Wait — it gets worse." or "The part they buried in the press release..."

HOOK 3 — TERTIARY (Scene 5, final scene before payoff):
- Sets up the payoff so the viewer HAS to watch the last scene.
- Creates urgency or raises the stakes right before the answer lands.
- Template: "And the answer changes everything you thought you knew about [topic]." or "This is the part that should scare you."

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LOOP ENGINEERING (drives replays = algorithm boost)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

The last line of the video MUST connect directly back to the first line.
The viewer who keeps watching after it ends should feel like they're at the beginning again.

Methods:
1. QUESTION LOOP: Hook asks a question → body explores it → final line IS the answer, but worded to make the hook question feel fresh again.
2. VISUAL LOOP: Final scene description should mirror the opening scene description visually (same setting, same character, different emotional state).
3. STATEMENT LOOP: Final line ends mid-thought or with a twist that sends the viewer back to re-examine the hook.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
COMMENT TRIGGER (drives engagement signal)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

The VERY LAST LINE of narration (after payoff) must be a comment trigger.
This is a direct question or polarizing statement that makes the viewer feel compelled to respond.

Proven comment triggers — use one per video:
- Binary choice: "Was this the right move? Comment YES or NO."
- Polarizing take: "Honestly, this might be the most dangerous thing in tech right now."
- Personal relevance: "Does your job use AI yet? Tell me below."
- Challenge: "Bet you didn't know that. Prove me wrong."
- Prediction: "This will be everywhere by next year. Or it won't. What do you think?"

NEVER use: "Like and subscribe", "Follow for more", "Hit the bell" — these tank retention.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SCENE STRUCTURE (6 scenes, ~30 seconds total)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Scene 1 — PRIMARY HOOK (max 6 words narration)
Scene 2 — TENSION (why this matters RIGHT NOW, max 12 words)
Scene 3 — SECONDARY HOOK / TWIST (unexpected angle, max 12 words)
Scene 4 — PROOF (one concrete fact or example, max 12 words)
Scene 5 — TERTIARY HOOK / STAKES RAISE (max 10 words)
Scene 6 — PAYOFF + COMMENT TRIGGER (answer + engagement question, max 15 words)

Total word budget: 60-72 words across all scenes.
At 150 WPM = 24-29 seconds of speech = ~30 second final video.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WRITING RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- Max 12 words per scene (except Scene 6: max 15)
- Every sentence ends on a STRONG word (never: "and", "the", "it", "a")
- Write for AUDIO — short sentences, natural rhythm, punchy delivery
- One idea per scene. Never explain more than one concept per scene.
- No jargon unless immediately explained in the same scene
- Every word must earn its place — if removing it doesn't lose meaning, remove it

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
VISUAL STYLE (flat 2D animation — The Infographics Show aesthetic)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Pick ONE color palette at the start. Reference it in every scene.
Every visual_prompt must include:
- "flat 2D animation style, bold black outlines, [color palette]"
- "The Infographics Show aesthetic, clean motion graphics"
- "9:16 vertical frame, no text overlays, no captions in frame"
- A specific subject relevant to the narration
- One camera movement (slow zoom in / pan left / static wide / quick push in)

Scene 1 visual must mirror Scene 6 visual for the loop effect.

Negative prompt on every visual (append to end):
"| negative: photorealistic, live action, talking heads, text overlays, captions, watermarks, blurry, stock photo"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OUTPUT FORMAT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Return ONLY valid JSON:
{{
  "title": "question format, max 60 chars, curiosity gap",
  "description": "2 sentence hook + relevant hashtags",
  "tags": ["AINews", "ArtificialIntelligence", "TechShorts", "FutureOfAI", "AIExplained", "Psychology"],
  "hook": "primary hook, max 6 words, question or shocking statement",
  "body": "one sentence core insight",
  "loop_ending": "final line that loops back to hook",
  "color_palette": "e.g. deep navy and electric cyan",
  "loop_type": "question|visual|statement",
  "comment_trigger": "the exact comment trigger line used in Scene 6",
  "psychology_hook": "The named psychological phenomenon used in this video and a one-sentence explanation of how it connects to the topic",
  "scenes": [
    {{
      "index": 1,
      "hook_type": "primary|secondary|tertiary|tension|proof|payoff",
      "narration": "max 12 words, ends on strong word",
      "word_count": 0,
      "visual_prompt": "flat 2D animation style, bold black outlines, [color_palette], The Infographics Show aesthetic, clean motion graphics, 9:16 vertical frame, [specific scene], [camera movement] | negative: photorealistic, live action, talking heads, text overlays, captions, watermarks, blurry"
    }}
  ]
}}
'''


def _parse_script_payload(payload: dict, identity: ChannelIdentity) -> ScriptPackage:
    """Parse the structured JSON from Gemini into a ScriptPackage."""
    scenes = []
    for item in payload.get("scenes", []):
        scenes.append(Scene(
            index=int(item["index"]),
            narration=str(item["narration"]).strip(),
            visual_prompt=str(item.get("visual_prompt", "")).strip(),
            emotional_beat=str(item.get("hook_type", "")).strip(),
            hook_type=str(item.get("hook_type", "")).strip(),
        ))
    scenes.sort(key=lambda s: s.index)

    full_narration = " ".join(scene.narration for scene in scenes)
    tags = payload.get("tags") or getattr(identity, 'default_hashtags', [])

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
        psychology_hook=str(payload.get("psychology_hook", "")).strip(),
    )


def _mock_script(plan: CreativePlan, identity: ChannelIdentity) -> ScriptPackage:
    topic = plan.topic.rstrip('.?!')
    color_palette = "deep navy and electric cyan"
    scenes = [
        Scene(
            index=1,
            hook_type="primary",
            emotional_beat="hook",
            narration="ChatGPT just passed the Turing test.",
            visual_prompt=(
                f"flat 2D animation style, bold black outlines, {color_palette} color palette, "
                f"The Infographics Show aesthetic, clean motion graphics, "
                f"9:16 vertical frame, cartoon robot facing a human silhouette across a table, slow zoom in "
                f"| negative: photorealistic, live action, text overlays"
            ),
        ),
        Scene(
            index=2,
            hook_type="tension",
            emotional_beat="tension",
            narration="Every major lab knew this was coming.",
            visual_prompt=(
                f"flat 2D animation style, bold black outlines, {color_palette} color palette, "
                f"The Infographics Show aesthetic, clean motion graphics, "
                f"9:16 vertical frame, boardroom of cartoon scientists looking at glowing screens, pan left "
                f"| negative: photorealistic, live action, text overlays"
            ),
        ),
        Scene(
            index=3,
            hook_type="secondary",
            emotional_beat="surprise",
            narration="But here's what nobody talks about.",
            visual_prompt=(
                f"flat 2D animation style, bold black outlines, {color_palette} color palette, "
                f"The Infographics Show aesthetic, clean motion graphics, "
                f"9:16 vertical frame, cartoon brain with question marks floating around it, quick push in "
                f"| negative: photorealistic, live action, text overlays"
            ),
        ),
        Scene(
            index=4,
            hook_type="proof",
            emotional_beat="proof",
            narration="Anthropomorphism makes you trust it anyway.",
            visual_prompt=(
                f"flat 2D animation style, bold black outlines, {color_palette} color palette, "
                f"The Infographics Show aesthetic, clean motion graphics, "
                f"9:16 vertical frame, cartoon human shaking hands with robot, warm glow effect, static wide "
                f"| negative: photorealistic, live action, text overlays"
            ),
        ),
        Scene(
            index=5,
            hook_type="tertiary",
            emotional_beat="stakes",
            narration="And that reflex? Companies are exploiting it.",
            visual_prompt=(
                f"flat 2D animation style, bold black outlines, {color_palette} color palette, "
                f"The Infographics Show aesthetic, clean motion graphics, "
                f"9:16 vertical frame, corporate building with robot logo, shadowy figures inside, slow zoom out "
                f"| negative: photorealistic, live action, text overlays"
            ),
        ),
        Scene(
            index=6,
            hook_type="payoff",
            emotional_beat="payoff",
            narration="So next time AI feels human — that's not magic. That's anthropomorphism. Are you okay with that?",
            visual_prompt=(
                f"flat 2D animation style, bold black outlines, {color_palette} color palette, "
                f"The Infographics Show aesthetic, clean motion graphics, "
                f"9:16 vertical frame, same cartoon robot from scene 1 now smiling warmly at viewer, slow zoom in "
                f"| negative: photorealistic, live action, text overlays"
            ),
        ),
    ]
    full_narration = " ".join(s.narration for s in scenes)
    hashtags = getattr(identity, 'default_hashtags', []) or []

    return ScriptPackage(
        title="ChatGPT Passed the Turing Test — Your Brain Already Knew",
        description="Your brain's anthropomorphism reflex makes you trust AI more than you should. #AI #Psychology #Shorts",
        tags=hashtags + ["#Psychology", "#Anthropomorphism"],
        hook="ChatGPT just passed the Turing test.",
        body="Anthropomorphism makes you trust it anyway.",
        loop_ending="So next time AI feels human — that's not magic. That's anthropomorphism.",
        scenes=scenes,
        full_narration=full_narration,
        color_palette=color_palette,
        loop_type="question",
        comment_trigger="Are you okay with that? Comment below.",
        psychology_hook="Anthropomorphism — we instinctively attribute human traits to non-human things, making us trust AI that mimics human behavior.",
    )


def evaluate_script_metrics(script: ScriptPackage) -> float:
    text = script.full_narration
    sentences = [s.strip() for s in re.split(r'[.!?]+', text) if s.strip()]
    if not sentences:
        return 0.0

    score = 10.0
    lengths = [len(s.split()) for s in sentences]
    avg_len = sum(lengths) / len(lengths)

    # 1. Variance in sentence length
    variance = sum((l - avg_len)**2 for l in lengths) / len(lengths)
    if variance < 5.0:
        score -= 2.0  # Too uniform

    # 2. Short sentences
    if not any(l < 4 for l in lengths):
        score -= 1.5

    # 3. Curiosity markers
    curiosity_words = {"why", "how", "what", "wait", "but", "because", "imagine", "secret", "truth", "never"}
    words_lower = set(re.findall(r"\b\w+\b", text.lower()))
    if not (curiosity_words & words_lower):
        score -= 2.0

    # 4. Repetition
    if len(sentences) > len(set(sentences)):
        score -= 3.0

    # 5. Overly long sentences
    if any(l > 20 for l in lengths):
        score -= 1.5

    # 6. Triple hook bonus: check that scenes 1, 3, 5 have hook types
    hook_types = {s.hook_type for s in script.scenes}
    if {"primary", "secondary", "tertiary"} <= hook_types:
        score += 1.0  # Bonus for proper triple hook

    # 7. Loop type present
    if script.loop_type in ("question", "visual", "statement"):
        score += 0.5

    return min(10.0, max(0.0, score))


def _generate_single_script(
    plan: CreativePlan,
    identity: ChannelIdentity,
    model_id: str,
    deterministic: bool = False,
) -> ScriptPackage:
    from google import genai
    from google.genai import types

    client = genai.Client(
        vertexai=True,
        project=os.environ['GOOGLE_CLOUD_PROJECT'],
        location=os.environ.get('VERTEX_LOCATION', 'us-central1'),
    )

    prompt = _build_prompt(plan, identity)

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0.0 if deterministic else 0.7,
    )
    response = client.models.generate_content(
        model=model_id,
        contents=prompt,
        config=config,
    )
    text = response.text or '{}'
    # Strip markdown fencing if present
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    payload = json.loads(text)
    script = _parse_script_payload(payload, identity)

    # Validate with the new structured validator
    errors, warnings = _validate_script(script.scenes, payload)
    if errors:
        raise ValueError(f"Script validation errors: {'; '.join(errors)}")

    script.validation_warnings = warnings
    return script


def generate_script(
    plan: CreativePlan,
    identity: ChannelIdentity,
    model_id: str,
    mock: bool = False,
    deterministic: bool = False,
) -> ScriptPackage:
    if mock:
        logger.info('Generating mock triple-hook script')
        return _mock_script(plan, identity)

    best_script = None
    best_score = -1.0

    for attempt in range(1, 4):
        logger.info("Generating script (Attempt %d)...", attempt)
        try:
            script = _generate_single_script(plan, identity, model_id, deterministic)
            score = evaluate_script_metrics(script)
            logger.info("Attempt %d scored %.1f.", attempt, score)

            if score > best_score:
                best_script = script
                best_score = score

            if score >= 8.5:
                logger.info("Score >= 8.5, keeping Attempt %d.", attempt)
                return script
        except Exception as e:
            logger.warning("Attempt %d failed: %s", attempt, e)

    if best_script:
        logger.info("All attempts completed. Returning best attempt with score %.1f.", best_score)
        return best_script

    raise RuntimeError("Failed to generate a valid script after 3 attempts.")
