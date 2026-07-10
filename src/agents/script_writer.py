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


def _validate_script(scenes: list, script_data: dict, config: dict) -> tuple[list, list]:
    errors = []
    warnings = []
    
    scene_min = config.get("scene_count_min", 4)
    scene_max = config.get("scene_count_max", 5)
    
    # HARD: scene count
    if len(scenes) < scene_min:
        errors.append(f"Too few scenes: {len(scenes)} — minimum is {scene_min}")
    if len(scenes) > scene_max:
        errors.append(f"Too many scenes: {len(scenes)} — maximum is {scene_max}. Never generate 6 scenes.")
    
    # HARD: hook scene word count
    hook = next((s for s in scenes if s.index == 1), None)
    if hook:
        hook_words = len(hook.narration.split())
        if hook_words > 7:  # Prompt asks for 5, enforce 7 as hard limit
            errors.append(f"Scene 1 hook is {hook_words} words — max is 5 (strict)")
    
    # HARD: per-scene word count
    for scene in scenes:
        limit = 18 if scene.index == len(scenes) else 13
        if scene.index == 1:
            limit = 7
        words = len(scene.narration.split())
        if words > limit:
            errors.append(f"Scene {scene.index}: {words} words exceeds {limit} word hard limit")
    
    # HARD: total word budget
    total_words = sum(len(s.narration.split()) for s in scenes)
    if total_words < 40:
        errors.append(f"Total word count {total_words} is too low — minimum 40 words (target 50-65)")
    if total_words > 70:
        errors.append(f"Total word count {total_words} exceeds 70 — video will be too long. Target 50-65 words.")
    
    # HARD: required fields
    if not script_data.get("comment_trigger"):
        errors.append("Missing comment_trigger field")
    if not script_data.get("loop_type"):
        errors.append("Missing loop_type field")
    if not script_data.get("psychology_hook"):
        errors.append("Missing psychology_hook — must reference a named psychological phenomenon")
    
    # WARNING: loop visual check
    if len(scenes) >= 2:
        scene1_words = set(scenes[0].visual_prompt.lower().split())
        scene_last_words = set(scenes[-1].visual_prompt.lower().split())
        common = scene1_words & scene_last_words - {"a", "the", "of", "in", "on", "with", "and", "flat", "vector", "animation", "style", "bold", "black", "outlines", "clean", "motion", "graphics", "vertical", "portrait", "orientation", "negative"}
        if len(common) < 2:
            warnings.append("Scene 1 and final scene share no common subject — loop effect may be broken")
    
    # WARNING: scene 3 hook type (for 5-scene scripts)
    if len(scenes) == 5:
        scene_3 = next((s for s in scenes if s.index == 3), None)
        if scene_3 and scene_3.hook_type not in ["secondary", "tension"]:
            warnings.append("Scene 3 should be secondary hook or tension for optimal retention")
    
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
TIMING & SCENE RULES (hard limits — violations cause retry)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

TARGET VIDEO LENGTH: 21-28 seconds. Never shorter. Never longer.

SCENE COUNT: Generate exactly 4 OR 5 scenes. Never 3. Never 6. Never 7.
- 4 scenes × 6 seconds = 24 seconds
- 5 scenes × 6 seconds = 30 seconds
Both are acceptable. Choose based on how much the topic needs.
Simple topics = 4 scenes. Complex topics = 5 scenes.

WORD BUDGET:
- Total script: 50-65 words across ALL scenes combined
- At 150 WPM, 50 words = 20 seconds, 65 words = 26 seconds
- This is the ONLY way to hit the 21-28 second target
- Scene 1 (hook): max 5 words (WE WILL COUNT THEM AND FAIL IF MORE)
- Scenes 2-4: max 10 words each
- Scene 5 (payoff, if used): max 15 words including comment trigger

SPEAKING PACE TARGET: 150-170 words per minute
- Write short, punchy sentences that naturally speak fast
- No long explanatory sentences — one idea, one sentence, done
- If a sentence takes more than 4 seconds to say out loud, it's too long

TIMING CHECK (do this before outputting JSON):
1. Count total words across all scene narrations
2. Divide by 150 (words per minute) × 60 = speaking seconds
3. If result is outside 20-28 seconds → rewrite until it fits
4. Never pad with filler words to hit a minimum
5. Never cut important content to hit a maximum — restructure instead

SCENE STRUCTURE FOR 4 SCENES:
Scene 1 — PRIMARY HOOK (max 5 words, question)
Scene 2 — TENSION + SECONDARY HOOK (max 10 words)
Scene 3 — PROOF + PSYCHOLOGY CONCEPT (max 10 words)
Scene 4 — PAYOFF + COMMENT TRIGGER (max 15 words)

SCENE STRUCTURE FOR 5 SCENES:
Scene 1 — PRIMARY HOOK (max 5 words, question)
Scene 2 — TENSION (max 10 words)
Scene 3 — SECONDARY HOOK / TWIST (max 10 words)
Scene 4 — PROOF + PSYCHOLOGY CONCEPT (max 10 words)
Scene 5 — PAYOFF + COMMENT TRIGGER (max 15 words)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
VISUAL STYLE (minimalist vector art)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Pick ONE color palette at the start. Reference it in every scene.
Every visual_prompt must include:
- "minimalist vector art, [color palette], clean motion graphics"
- "vertical portrait orientation"
- A specific subject relevant to the narration
- One camera movement (slow zoom in / pan left / static wide / quick push in)

VISUAL VARIETY RULE (hard requirement):
Each scene MUST depict a completely different visual subject.
No two scenes can show the same object, character, or setting.

Mandatory variety across 5 scenes — use this rotation:
Scene 1: A PERSON or CHARACTER reacting to something
Scene 2: A DEVICE or INTERFACE (phone, screen, computer)  
Scene 3: A BRAIN or MIND visualization (abstract, symbolic)
Scene 4: A CROWD or SOCIETY scene (multiple people, social setting)
Scene 5: Same CHARACTER as Scene 1 but different emotional state (loop)

The visual_prompt for each scene must open with its subject:
Scene 1: "Cartoon character..." 
Scene 2: "Floating smartphone..."
Scene 3: "Glowing brain..."
Scene 4: "Crowd of silhouettes..."
Scene 5: "Same cartoon character..."

If two scenes open with the same noun — reject and retry.

Negative prompt on every visual (append to end):
"| negative: channel logos, text, typography, words, branding, watermarks, photorealistic, live action, talking heads"

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
      "visual_prompt": "minimalist vector art, [color_palette], clean motion graphics, vertical portrait orientation, [specific scene], [camera movement] | negative: channel logos, text, typography, words, branding, watermarks, photorealistic, live action, talking heads"
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
                f"minimalist vector art, {color_palette} color palette, "
                f"clean motion graphics, vertical portrait orientation, "
                f"cartoon robot facing a human silhouette across a table, slow zoom in "
                f"| negative: channel logos, text, typography, words, branding, watermarks, photorealistic, live action, talking heads"
            ),
        ),
        Scene(
            index=2,
            hook_type="tension",
            emotional_beat="tension",
            narration="Every major lab knew this was coming.",
            visual_prompt=(
                f"minimalist vector art, {color_palette} color palette, "
                f"clean motion graphics, vertical portrait orientation, "
                f"boardroom of cartoon scientists looking at glowing screens, pan left "
                f"| negative: channel logos, text, typography, words, branding, watermarks, photorealistic, live action, talking heads"
            ),
        ),
        Scene(
            index=3,
            hook_type="secondary",
            emotional_beat="surprise",
            narration="But here's what nobody talks about.",
            visual_prompt=(
                f"minimalist vector art, {color_palette} color palette, "
                f"clean motion graphics, vertical portrait orientation, "
                f"cartoon brain with question marks floating around it, quick push in "
                f"| negative: channel logos, text, typography, words, branding, watermarks, photorealistic, live action, talking heads"
            ),
        ),
        Scene(
            index=4,
            hook_type="proof",
            emotional_beat="proof",
            narration="Anthropomorphism makes you trust it anyway.",
            visual_prompt=(
                f"minimalist vector art, {color_palette} color palette, "
                f"clean motion graphics, vertical portrait orientation, "
                f"cartoon human shaking hands with robot, warm glow effect, static wide "
                f"| negative: channel logos, text, typography, words, branding, watermarks, photorealistic, live action, talking heads"
            ),
        ),
        Scene(
            index=5,
            hook_type="tertiary",
            emotional_beat="stakes",
            narration="And that reflex? Companies are exploiting it.",
            visual_prompt=(
                f"minimalist vector art, {color_palette} color palette, "
                f"clean motion graphics, vertical portrait orientation, "
                f"corporate building with robot logo, shadowy figures inside, slow zoom out "
                f"| negative: channel logos, text, typography, words, branding, watermarks, photorealistic, live action, talking heads"
            ),
        ),
        Scene(
            index=6,
            hook_type="payoff",
            emotional_beat="payoff",
            narration="So next time AI feels human — that's not magic. That's anthropomorphism. Are you okay with that?",
            visual_prompt=(
                f"minimalist vector art, {color_palette} color palette, "
                f"clean motion graphics, vertical portrait orientation, "
                f"same cartoon robot from scene 1 now smiling warmly at viewer, slow zoom in "
                f"| negative: channel logos, text, typography, words, branding, watermarks, photorealistic, live action, talking heads"
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
    pipeline_config: dict = None,
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
    errors, warnings = _validate_script(script.scenes, payload, pipeline_config or {})
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
    pipeline_config: dict = None,
) -> ScriptPackage:
    if mock:
        logger.info('Generating mock triple-hook script')
        return _mock_script(plan, identity)

    best_script = None
    best_score = -1.0

    for attempt in range(1, 6):
        logger.info("Generating script (Attempt %d)...", attempt)
        try:
            script = _generate_single_script(plan, identity, model_id, deterministic, pipeline_config)
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

    raise RuntimeError("Failed to generate a valid script after 5 attempts.")
