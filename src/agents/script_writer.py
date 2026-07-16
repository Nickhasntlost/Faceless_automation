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
    # Scene 1 = hook, second-to-last scene = tiny explanation (needs more room),
    # last scene = emotional ending, everything else = story moment.
    for scene in scenes:
        if scene.index == 1:
            limit = 7
        elif scene.index == len(scenes) - 1:
            limit = 25  # tiny explanation scene
        elif scene.index == len(scenes):
            limit = 18  # emotional ending
        else:
            limit = 13  # story moment
        words = len(scene.narration.split())
        if words > limit:
            errors.append(f"Scene {scene.index}: {words} words exceeds {limit} word hard limit")

    # HARD: total word budget
    total_words = sum(len(s.narration.split()) for s in scenes)
    if total_words < 40:
        errors.append(f"Total word count {total_words} is too low — minimum 40 words (target 50-70)")
    if total_words > 75:
        errors.append(f"Total word count {total_words} exceeds 75 — video will be too long. Target 50-70 words.")
    
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
    
    # WARNING: emotional progression — explanation must arrive last, not mid-story
    psych_terms = ["heuristic", "bias", "effect", "syndrome", "psychology",
                   "psychologists", "cognitive", "research", "studies"]

    scene_3 = next((s for s in scenes if s.index == 3), None)
    if scene_3:
        if any(term in scene_3.narration.lower() for term in psych_terms):
            warnings.append("Scene 3 contains psychology explanation — explanation should appear in Scene 4 only. Keep Scene 3 as pure storytelling.")

    scene_2 = next((s for s in scenes if s.index == 2), None)
    if scene_2:
        if any(term in scene_2.narration.lower() for term in psych_terms):
            warnings.append("Scene 2 should be pure storytelling — no psychology terms yet")

    # WARNING: ending too weak
    if scenes:
        last_scene = scenes[-1]
        weak_endings = ["think about that", "pretty interesting", "pretty amazing",
                        "worth protecting", "worth considering", "what do you think"]
        if any(w in last_scene.narration.lower() for w in weak_endings):
            warnings.append("Ending is too generic — needs a specific haunting image or unanswered question that connects back to the opening story moment")

    # WARNING: check for performance anti-patterns
    for scene in scenes:
        narration = scene.narration

        # Bad: starts with a fact/noun (not a performance hook)
        first_word = narration.split()[0].lower() if narration else ""
        boring_starters = ["ai", "the", "this", "research", "studies", "humans", "people", "when", "because"]
        if first_word in boring_starters:
            warnings.append(f"Scene {scene.index}: starts with '{first_word}' — consider opening with a performance hook instead")

        # Bad: no short sentence (all sentences similar length)
        sentences = [s.strip() for s in re.split(r'[.!?]+', narration) if s.strip()]
        short_sentences = [s for s in sentences if len(s.split()) <= 5]
        if len(sentences) > 1 and not short_sentences:
            warnings.append(f"Scene {scene.index}: no short punchy sentences — vary sentence length for better delivery")

        # Bad: missing performance note
        if not getattr(scene, 'performance_note', ''):
            warnings.append(f"Scene {scene.index}: missing performance_note — narrator needs delivery guidance")

    return errors, warnings


def _build_prompt(plan: CreativePlan, identity: ChannelIdentity) -> str:
    plan_dict = plan.__dict__.copy()
    if 'scene_count' in plan_dict:
        del plan_dict['scene_count']

    content_angles = json.dumps(getattr(identity, 'content_angles', []))

    return f'''You are a short-form storyteller. You make viewers feel a human moment in under 30 seconds.
The psychology is always the twist — never the subject.
Your benchmark: emotional micro-fiction. Think: the moment before a realization, not the realization itself.
Your goal: the viewer feels something before they understand anything. 65%+ retention through emotion, not information.

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
"[Human moment the viewer has lived] + [The thing happening underneath it] + [The name for it — revealed last]"

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

STRONGER HOOK TEMPLATES (prefer these over generic questions):

IMMEDIATE MENTAL IMAGE:
"What if AI made you forget your grandmother?"
"Your grandmother told you stories. AI might erase them."

PERSONAL PROVOCATION:
"AI might be replacing something you can't get back."
"Something is quietly disappearing from your family. And you don't know it yet."

SPECIFIC SCENARIO:
"Imagine asking AI for advice... instead of calling your dad."
"What happens when Google becomes more familiar than your parents' voices?"

AVOID:
Generic questions like "Is AI stealing something from your family?"
(good but safe — push for more specific/visual hooks)

The strongest hooks create an IMMEDIATE mental image in the first 2 seconds.
The viewer should picture something specific, not think about an abstract concept.

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

COMMENT TRIGGER RULE:
The comment trigger must flow NATURALLY from the emotional ending.
It should feel like the narrator is genuinely asking, not prompting engagement.

WRONG: "Is your family's wisdom worth protecting? Tell me below."
(Generic, preachy, disconnected from the story)

RIGHT: "Will you remember her face... or just the AI's answer? Tell me."
(Specific, haunting, directly connected to the story's emotional core)

The comment trigger should reference something SPECIFIC from the story,
not a generic concept.

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
- Total script: 50-70 words across ALL scenes combined
- At 150 WPM, 50 words = 20 seconds, 70 words = 28 seconds
- This is the ONLY way to hit the 21-28 second target
- Scene 1 (hook): max 7 words (WE WILL COUNT THEM AND FAIL IF MORE)
- Scenes 2-3 (story moments): max 13 words each
- Scene 4 (tiny explanation): max 22 words — this scene carries the concept, give it room
- Scene 5 (emotional ending, if used): max 18 words including comment trigger

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

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SCENE STRUCTURE — FEEL FIRST, UNDERSTAND SECOND
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

MANDATORY STRUCTURE FOR 5 SCENES:

Scene 1 — HOOK (emotional question or shocking statement)
  - Must create personal stakes immediately
  - Never start with a fact or statistic
  - Must make the viewer feel "this is about ME"
  - Max 7 words

Scene 2 — STORY MOMENT 1 (pure storytelling, zero explanation)
  - Paint a specific, human picture
  - Use "Imagine this." or "Picture this." to open
  - No psychology terms, no AI jargon
  - The viewer must see a human moment, not a concept
  - Example: "Your grandmother tells a story. Nobody writes it down.
    Years later... it's gone."

Scene 3 — STORY MOMENT 2 (escalate the emotion, still no explanation)
  - Build on scene 2, increase emotional stakes
  - Something unexpected or slightly painful
  - Still no explanation — keep them feeling, not thinking
  - Use contrast: what they expect vs what actually happens
  - Example: "The story disappears. Then the details disappear.
    Then... the sound of her voice."

Scene 4 — TINY EXPLANATION (revised delivery pattern)

NEVER say "Psychologists call this X" as a standalone sentence.
Always build to the concept name with hesitation and suspense:

WRONG:
"Psychologists call this the availability heuristic."

RIGHT (delayed reveal):
"You don't forget because you wanted to."
"You forget... because your brain keeps choosing what it sees most often."
"Psychologists actually have a name for this..."
"It's called... the availability heuristic."

The concept name should feel like a REVEAL, not a label.
The "..." before the concept name is mandatory — it creates the micro-pause
that makes the naming feel like a discovery.

Pattern to follow:
[Why it happens in human terms]
→ [What the brain is doing]
→ "Psychologists actually have a name for this..."
→ "It's called... [CONCEPT NAME]."

Scene 5 — EMOTIONAL ENDING (leave a scar, not a lesson)
  - Do NOT summarize what you just said
  - Do NOT use "Think about that" — too weak
  - Leave an image or question that haunts them
  - Must connect back to the human moment from Scene 2
  - Examples of strong endings:
    "One day... the last person who remembers that story will be gone.
     What happens then?"
    "You'll remember the AI's answer. Will you remember her face?"
    "The story is still there. Is it in your head... or just in the cloud?"
  - End with a question that has no easy answer

For 4-scene scripts, compress Scene 2 and Scene 3 above into a single
STORY scene, then follow with the TINY EXPLANATION and EMOTIONAL ENDING
scenes as described.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EMOTIONAL PROGRESSION RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

RULE 1 — EARN YOUR EMOTION
Emotional claims must be built to, not stated.
WRONG: "AI doesn't just replace the story. It replaces HER."
  (Too fast — not earned yet)
RIGHT: Build through scenes 2 and 3 until the viewer feels the loss.
  Then in scene 3: "AI starts becoming easier to remember...
  than the people who taught you."
  (Same message, but now it lands because they feel it first)

RULE 2 — SHOW THEN NAME
Never introduce a psychology concept cold.
Always show the concept in human terms first, then name it.
Pattern: [Human experience] → [What it means] → [Name of concept]
Never: [Name of concept] → [What it means] → [Human experience]

RULE 3 — SOFTEN STRONG CLAIMS
Rhetorical claims that sound like facts create distrust.
WRONG: "AI replaces HER." (stated as fact)
RIGHT: "AI starts becoming easier to remember than the people
who taught you." (concern/observation)
Keep emotional impact. Reduce factual overstatement.

RULE 4 — THE ENDING MUST LEAVE A SCAR
The last line of the video is the most remembered.
It should create a lingering image or unanswered question.
Test: if someone could only remember ONE line from your video,
would this be worth remembering?
If no — rewrite it.

RULE 5 — NO TED TALK MOMENTS
The moment you hear "That's the availability heuristic" in isolation,
the viewer switches from feeling to thinking.
Never let a scene feel like a psychology textbook entry.
The explanation must be woven into the story, not dropped on top of it.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PERFORMANCE SCRIPT RULES (most important section)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

You are NOT writing sentences. You are writing SPOKEN MOMENTS.
Every line must implicitly tell the narrator how to deliver it.
The goal: sound like someone telling an exciting story to a friend,
not reading an article aloud.

RULE 1 — NEVER LET A THOUGHT DIE
Bad: "AI has changed how families remember stories."
     [thought dies, silence, next sentence starts]

Good: "AI has changed how families remember stories..."
      "...and nobody warned you it was happening."

The "..." means the thought CONTINUES. The narrator bridges to
the next line instead of stopping. Use "..." between connected
thoughts to create run-on momentum.

RULE 2 — VARY SENTENCE ENERGY DELIBERATELY
Every scene must have a different energy level:
- Short punchy line = HIGH energy (reader speeds up naturally)
- "But then..." = PAUSE + anticipation
- "Nobody noticed." = LOW, quiet, almost whispered
- "Until NOW." = SPIKE, emphasis
- "Think about that." = SLOW DOWN, let it land

Never write 3 sentences in a row at the same energy level.
Energy pattern should look like: HIGH → low → HIGH HIGH → pause → SPIKE

RULE 3 — WRITE DISCOVERY MOMENTS, NOT EXPLANATIONS
Bad: "The availability heuristic makes you overestimate AI reliability."
[explains immediately, no tension]

Good: "But here's the strange part..."
      "The more you use AI..."
      "The MORE you trust it."
      "Even when it's wrong."

The good version creates tension BEFORE the explanation.
The viewer leans in because they don't have the answer yet.

RULE 4 — USE THESE PERFORMANCE STRUCTURES (rotate through them)

SETUP → PAUSE → REVEAL:
"Something happened in 2023."
"Nobody talked about it."
"But it changed everything."

HYPOTHETICAL MOMENT:
"Imagine this."
"Your grandmother tells a story."
"Nobody writes it down."
"Years later... it's gone."

CALLBACK QUESTION:
"Remember what I said about [X]?"
"That's exactly what's happening here."

WHISPER MOMENT (short, heavy):
"That's the part they don't mention."
or
"Nobody tells you this."
or
"Think about that."

RUSH MOMENT (fast, no pauses, urgency):
"It's happening right now. In your phone. In your home. Today."

RULE 5 — SENTENCE LENGTH VARIATION IS MANDATORY
Every scene must have at least one sentence under 5 words.
Every scene must have a mix of short and longer sentences.

Examples of good short punchy lines:
"That's the catch."
"Nobody warned you."
"Until it was gone."
"But here's the thing."
"Think about that."
"It already started."

RULE 6 — NEVER START A SCENE WITH A FACT
Bad scene opening: "The availability heuristic affects memory recall."
Good scene opening: "Here's what nobody tells you."
                   "Something strange happens when you use AI daily."
                   "But wait."
                   "Imagine this for a second."

Always open a scene with a performance hook, then deliver the fact.

RULE 7 — THE NARRATOR MUST SOUND LIKE THEY'RE DISCOVERING THIS TOO
Write lines that sound like genuine surprise or realization:
"And this is where it gets weird."
"I didn't expect this either."
"But then something changed."
"Wait — it gets worse."
"Here's the part that surprised me."

These lines cost zero extra words but completely change delivery energy.

RULE 8 — SOUND SLIGHTLY IMPERFECT (most human-sounding rule)

Real creators don't tell stories perfectly. They:
- Interrupt themselves mid-thought
- Correct themselves
- Build to a word, then pause before saying it
- Start a sentence, then change direction

Use these patterns deliberately:

SELF-INTERRUPT:
"And that's when — actually, wait."
"But here's the thing — no, here's the REAL thing."
"I thought it was about memory. It's not. It's about something else."

MID-SENTENCE REDIRECT:
"The story disappears. Then the details. And then..."
"First the story goes. Then the little details. And eventually..."
"You don't forget because you wanted to. You forget because..."

BUILD TO THE WORD:
"Then... the sound of her voice." (pause BEFORE the emotional word)
"And eventually... this is the scary part... you can't even remember her voice."
"Psychologists actually have a name for this... it's called..."

THINKING ALOUD:
"And this is where it gets strange."
"I didn't expect this part."
"Here's what I didn't realize until recently."
"Actually — this is the part nobody talks about."

These cost zero extra words but completely change how human the delivery sounds.
TTS naturally pauses at "..." and changes tone at em dashes — use both liberally.

RULE 9 — FRAME AS TENDENCY, NOT CERTAINTY

When making psychological claims, frame them as possibilities or tendencies,
not established facts. This preserves emotional impact while avoiding overstatement.

WRONG (overstates causality):
"AI replaces her."
"AI makes you forget."
"Using AI destroys family memory."

RIGHT (frames as tendency):
"AI starts becoming easier to remember than the people who taught you."
"The stories we revisit are the ones we keep. The ones we don't... fade."
"If AI becomes your first answer for everything, your own memories get used less."

The emotional impact is identical. The accuracy is higher.
Avoid: "replaces", "erases", "destroys", "steals" as definitive verbs.
Use: "starts to replace", "might erase", "can slowly replace", "gets used less".

Every scene's JSON object must include a "performance_note" field describing
the delivery style for that scene, e.g. "urgent whisper", "fast rush",
"slow reveal", "shocked reaction", "thoughtful pause", "direct address".

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
VISUAL STYLE (minimalist vector art)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

There is NO single global color palette. Each scene has its own color mood
that matches its emotional beat — never repeat the same palette twice.

Every visual_prompt must include:
- "minimalist vector art, clean motion graphics"
- The scene's specific color mood (see PER-SCENE COLOR MOOD below)
- "vertical portrait orientation"
- A specific subject relevant to the narration
- One camera movement (slow zoom in / pan left / static wide / quick push in)

PER-SCENE COLOR MOOD (hard requirement — never the same twice):
Scene 1 (hook): bright white/clean studio lighting, light background,
  high contrast — shocking, clean
Scene 2 (story_1): warm sepia/golden tones, soft amber light,
  family warmth feeling — human, emotional, nostalgic
Scene 3 (story_2): muted warm tones fading to grey,
  desaturated, melancholy — loss, fading, quiet
Scene 4 (explanation): cool blue-white data visualization feel,
  clean white background, clinical — analytical, clear
Scene 5 (emotional_ending): same subject as scene 1 but darker,
  slightly desaturated — resolution, weight, finality

For 4-scene scripts, use moods 1, 2-or-3 (whichever fits the single story
scene), 4, and 5 in that order.

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
"| negative: channel logos, text, typography, words, branding, watermarks, photorealistic, live action, talking heads, neon, cyan, teal, electric blue, dark navy background, glowing, neon glow"

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
  "loop_type": "question|visual|statement",
  "comment_trigger": "the exact comment trigger line used in Scene 6",
  "psychology_hook": "The named psychological phenomenon used in this video and a one-sentence explanation of how it connects to the topic",
  "scenes": [
    {{
      "index": 1,
      "hook_type": "primary|secondary|tertiary|tension|proof|payoff",
      "performance_note": "delivery style for this scene, e.g. urgent whisper, fast rush, slow reveal, shocked reaction, thoughtful pause, direct address",
      "narration": "max 12 words, ends on strong word",
      "word_count": 0,
      "visual_prompt": "minimalist vector art, [per-scene color mood], clean motion graphics, vertical portrait orientation, [specific scene], [camera movement] | negative: channel logos, text, typography, words, branding, watermarks, photorealistic, live action, talking heads, neon, cyan, teal, electric blue, dark navy background, glowing, neon glow"
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
            performance_note=str(item.get("performance_note", "")).strip(),
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
        loop_type=str(payload.get("loop_type", "")).strip(),
        comment_trigger=str(payload.get("comment_trigger", "")).strip(),
        psychology_hook=str(payload.get("psychology_hook", "")).strip(),
    )


NEGATIVE_SUFFIX = (
    "| negative: channel logos, text, typography, words, branding, watermarks, "
    "photorealistic, live action, talking heads, neon, cyan, teal, electric blue, "
    "dark navy background, glowing, neon glow"
)


def _mock_script(plan: CreativePlan, identity: ChannelIdentity) -> ScriptPackage:
    scenes = [
        Scene(
            index=1,
            hook_type="primary",
            emotional_beat="hook",
            performance_note="urgent whisper, speeds up",
            narration="Is AI stealing something from your family?",
            visual_prompt=(
                f"minimalist vector art, bright white clean studio lighting, light background, high contrast, "
                f"clean motion graphics, vertical portrait orientation, "
                f"cartoon person looking at glowing screen with question mark above head, slow zoom in "
                f"{NEGATIVE_SUFFIX}"
            ),
        ),
        Scene(
            index=2,
            hook_type="tension",
            emotional_beat="story_1",
            performance_note="slow, human, pause on every line",
            narration="Imagine this. Your grandmother tells a story. Nobody writes it down. Years later... it's gone.",
            visual_prompt=(
                f"minimalist vector art, warm sepia and golden tones, soft amber light, nostalgic family warmth, "
                f"clean motion graphics, vertical portrait orientation, "
                f"grandmother telling a story to a child by lamplight, pan left "
                f"{NEGATIVE_SUFFIX}"
            ),
        ),
        Scene(
            index=3,
            hook_type="secondary",
            emotional_beat="story_2",
            performance_note="build slowly, get quieter not louder",
            narration="The story disappears. Then the details disappear. Then... the sound of her voice. One day... you can't remember it anymore.",
            visual_prompt=(
                f"minimalist vector art, muted warm tones fading to grey, desaturated, melancholy, "
                f"clean motion graphics, vertical portrait orientation, "
                f"cartoon brain shrinking as robot arm takes over tasks, wide shot "
                f"{NEGATIVE_SUFFIX}"
            ),
        ),
        Scene(
            index=4,
            hook_type="proof",
            emotional_beat="explanation",
            performance_note="matter-of-fact, like a discovery not a lecture",
            narration="The more you ask AI... the easier its answers become to remember. Her stories don't get repeated. Psychologists call this the availability heuristic.",
            visual_prompt=(
                f"minimalist vector art, cool blue-white data visualization feel, clean white background, clinical, "
                f"clean motion graphics, vertical portrait orientation, "
                f"crowd of silhouettes scrolling on phones under a fading memory, static wide "
                f"{NEGATIVE_SUFFIX}"
            ),
        ),
        Scene(
            index=5,
            hook_type="payoff",
            emotional_beat="emotional_ending",
            performance_note="slow, haunting, leave space after last word",
            narration="One day... the last person who remembers that story will be gone. Will you remember her face... or just the AI's answer?",
            visual_prompt=(
                f"minimalist vector art, bright white studio lighting but darker and slightly desaturated, weighty resolution, "
                f"clean motion graphics, vertical portrait orientation, "
                f"same cartoon person from scene 1 now staring blankly at screen, slow zoom out "
                f"{NEGATIVE_SUFFIX}"
            ),
        ),
    ]
    full_narration = " ".join(s.narration for s in scenes)
    hashtags = getattr(identity, "default_hashtags", []) or []

    return ScriptPackage(
        title="Is AI Stealing Your Family's Memories?",
        description="The psychology of the availability heuristic — and what it means for your family's stories.",
        tags=hashtags + ["#Psychology", "#AvailabilityHeuristic"],
        hook="Is AI stealing something from your family?",
        body="Psychologists call this the availability heuristic.",
        loop_ending="Will you remember her face... or just the AI's answer?",
        scenes=scenes,
        full_narration=full_narration,
        loop_type="question",
        comment_trigger="Will you remember her face... or just the AI's answer? Tell me.",
        psychology_hook="Availability heuristic — judging likelihood by how easily examples come to mind, making AI-stored memories feel more \"real\" than the ones fading from your own mind.",
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
