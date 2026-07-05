from __future__ import annotations

import json
import logging
import os
import re

from src.models import ChannelIdentity, CreativePlan, FullScript

logger = logging.getLogger('shorts_pipeline.script_writer')

MIN_DURATION_SECONDS = 15.0
MAX_DURATION_SECONDS = 33.0
WORDS_PER_SECOND = 2.5


def estimate_spoken_duration(text: str) -> float:
    words = re.findall(r'''\b[\w']+\b''', text)
    return round(len(words) / WORDS_PER_SECOND, 1)


def _validate_script(script: FullScript) -> None:
    if not script.full_script.strip():
        raise ValueError('ScriptWriter returned an empty full_script')
        
    words = re.findall(r"\b[\w']+\b", script.full_script)
    word_count = len(words)
    
    if word_count > 72:
        raise ValueError(f"Script exceeds word limit: {word_count} words (max 72, 12 words/scene).")
        
    if not MIN_DURATION_SECONDS <= script.estimated_duration <= MAX_DURATION_SECONDS:
        raise ValueError(
            f'Script duration must be {MIN_DURATION_SECONDS:.0f}-{MAX_DURATION_SECONDS:.0f} seconds; '
            f'estimated {script.estimated_duration:.1f}'
        )
    if script.hook.strip() not in script.full_script:
        raise ValueError('ScriptWriter hook must appear verbatim in full_script')


def _mock_script(plan: CreativePlan) -> FullScript:
    topic = plan.topic.rstrip('.?!')
    hook = f'What if the strangest thing about {topic} is the part almost everyone misses?'
    full_script = (
        f'{hook} At first, the story seems simple enough to dismiss. '
        f'People notice the loud result, but they rarely pay attention to the subtle origins. '
        f'But the real story begins with one quiet, barely perceptible change. '
        f'That change creates pressure beneath the surface, building up over time. '
        f'Watch the quiet shift closely, and you can spot what comes next before anyone else does. '
        f'Because that hidden beginning is exactly what everyone misses, and it changes everything.'
    )
    result = FullScript(
        title=plan.topic,
        hook=hook,
        full_script=full_script,
        estimated_duration=estimate_spoken_duration(full_script),
        story_template=plan.story_template,
        hook_style=plan.hook_style,
    )
    # Don't validate mock scripts for word counts
    return result


def evaluate_script_metrics(script: FullScript) -> float:
    text = script.full_script
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
        
    return max(0.0, score)


def _generate_single_script(
    plan: CreativePlan,
    identity: ChannelIdentity,
    model_id: str,
    deterministic: bool = False,
) -> FullScript:
    from google import genai
    from google.genai import types

    client = genai.Client(
        vertexai=True,
        project=os.environ['GOOGLE_CLOUD_PROJECT'],
        location=os.environ.get('VERTEX_LOCATION', 'us-central1'),
    )
    plan_dict = plan.__dict__.copy()
    if 'scene_count' in plan_dict:
        del plan_dict['scene_count']

    prompt = f'''
You are a master storyteller for YouTube Shorts.
Your only goal is to make the viewer watch until the final second.

Creative direction:
{json.dumps(plan_dict, indent=2)}

Channel voice:
- Persona: {identity.persona}
- Tone: {identity.tone}
- Audience: {identity.audience}
- Rules: {json.dumps(identity.content_rules)}

10 Unbreakable Storytelling Rules:
1. Write for speech. Never sound like Wikipedia or a textbook.
2. No corporate or AI wording (e.g. "Furthermore", "In conclusion", "It is important to note").
3. Reveal information gradually. Delay answers to create suspense.
4. Intentionally vary sentence length. Mix very short phrases ("Wait.") with longer conversational ones.
5. Include at least one dramatic pause or delay ("But here's the weird part...").
6. Assume visuals will help. Don't over-explain what can be seen.
7. The first 3 seconds must create immediate curiosity.
8. Every sentence must make the viewer want the next sentence.
9. End on a loop or a reveal that makes the start more meaningful.
10. Strict length: 50 to 70 words maximum (approx 12 words per scene). NEVER exceed 72 words total!

Do not think in scenes. Write ONE continuous script.
The hook field must appear verbatim at the start of full_script.

Return JSON only:
{{
  "title": "string",
  "hook": "string",
  "full_script": "string",
  "estimated_duration": 28.5,
  "story_template": "{plan.story_template}",
  "hook_style": "{plan.hook_style}"
}}
'''
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0.0 if deterministic else 0.7,
    )
    response = client.models.generate_content(
        model=model_id,
        contents=prompt,
        config=config,
    )
    payload = json.loads(response.text or '{}')
    payload['estimated_duration'] = estimate_spoken_duration(payload.get('full_script', ''))
    result = FullScript(**payload)
    _validate_script(result)
    return result


def generate_script(
    plan: CreativePlan,
    identity: ChannelIdentity,
    model_id: str,
    mock: bool = False,
    deterministic: bool = False,
) -> FullScript:
    if mock:
        logger.info('Generating mock story-first script')
        return _mock_script(plan)

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
        logger.info("All attempts failed to hit 8.5 target. Returning best attempt with score %.1f.", best_score)
        return best_script
        
    raise RuntimeError("Failed to generate a valid script after 3 attempts.")
