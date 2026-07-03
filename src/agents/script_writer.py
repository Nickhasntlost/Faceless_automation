from __future__ import annotations

import json
import logging
import os
import re

from src.models import ChannelIdentity, CreativePlan, FullScript

logger = logging.getLogger('shorts_pipeline.script_writer')

MIN_DURATION_SECONDS = 25.0
MAX_DURATION_SECONDS = 45.0
WORDS_PER_SECOND = 2.5


def estimate_spoken_duration(text: str) -> float:
    words = re.findall(r'''\b[\w']+\b''', text)
    return round(len(words) / WORDS_PER_SECOND, 1)


def _validate_script(script: FullScript) -> None:
    if not script.full_script.strip():
        raise ValueError('ScriptWriter returned an empty full_script')
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
        f'People notice the loud result. '
        f'But the real story begins with one quiet change. '
        f'That change creates pressure. '
        f'Watch the quiet shift, and you can spot what comes next. '
        f'Because that hidden beginning is what everyone misses.'
    )
    result = FullScript(
        title=plan.topic,
        hook=hook,
        full_script=full_script,
        estimated_duration=estimate_spoken_duration(full_script),
        story_template=plan.story_template,
        hook_style=plan.hook_style,
    )
    _validate_script(result)
    return result


def generate_script(
    plan: CreativePlan,
    identity: ChannelIdentity,
    model_id: str,
    mock: bool = False,
) -> FullScript:
    if mock:
        logger.info('Generating mock story-first script')
        return _mock_script(plan)

    from google import genai

    client = genai.Client(
        vertexai=True,
        project=os.environ['GOOGLE_CLOUD_PROJECT'],
        location=os.environ.get('VERTEX_LOCATION', 'us-central1'),
    )
    plan_dict = plan.__dict__.copy()
    if 'scene_count' in plan_dict:
        del plan_dict['scene_count']

    prompt = f'''
You are a master storyteller and Script Writer for a highly engaging YouTube Shorts channel.
Your objective is NOT to explain a topic. Your objective is to make someone watch until the final second.

Write ONE complete spoken story before anyone thinks about scenes or visuals.

Creative direction:
{json.dumps(plan_dict, indent=2)}

Channel voice:
- Persona: {identity.persona}
- Tone: {identity.tone}
- Audience: {identity.audience}
- Rules: {json.dumps(identity.content_rules)}

Core Storytelling Rules:
1. Write for speech. Never sound like Wikipedia, a documentary, or a textbook. Avoid corporate or AI-generated wording (e.g. "Furthermore", "In conclusion", "This fact", "It is important to note").
2. Do not dump information. Reveal it gradually. Each sentence must make the viewer want the next sentence. Delay answers to create suspense.
3. Vary sentence length intentionally. Mix very short phrases ("Wait.", "Seriously.") with conversational sentences. DO NOT produce scripts where every sentence has the same length.
4. Assume visuals will help tell the story. Don't over-explain what can be seen.
5. Write like you are enthusiastically explaining something fascinating to one friend.

Story Structure (Flow naturally, do NOT use these as headings):
Hook -> Curiosity -> Escalation -> Reveal -> Payoff -> Loop

Mandatory Storytelling Checklist (Internally verify these before returning):
- The first 3 seconds create immediate curiosity.
- Every sentence makes the viewer want to hear the next sentence.
- No sentence sounds like Wikipedia, a textbook, or a news article.
- Do not state facts back-to-back. Every fact must build suspense or answer a previous question.
- At least one "wait..." moment exists where information is intentionally delayed.
- The script contains at least one emotional shift (surprise, disbelief, excitement, concern, relief, etc.).
- At least one sentence is extremely short (1-3 words).
- Sentence lengths vary naturally. Do not produce a repetitive rhythm.
- The ending makes the opening feel more meaningful or encourages a rewatch.

Examples of Good vs Bad pacing:
Bad: "AI consumes a lot of electricity. It also requires water. Data centers are expensive to operate."
Good: "Most people think AI lives in the cloud. But here's the weird part... The cloud isn't a cloud. It's a giant warehouse. And every question you ask... makes those servers heat up."

Requirements:
- 25 to 40 seconds when spoken (Strict limit: 60 to 85 words MAXIMUM).
- One continuous story with natural pacing.
- Do not think in scenes. Do not output timestamps. Do not describe visuals.
- The hook field must appear verbatim at the start of full_script.

Return JSON only:
{{
  "title": "string",
  "hook": "string",
  "full_script": "string",
  "estimated_duration": 46.3,
  "story_template": "{plan.story_template}",
  "hook_style": "{plan.hook_style}"
}}
'''
    response = client.models.generate_content(
        model=model_id,
        contents=prompt,
        config={'response_mime_type': 'application/json'},
    )
    payload = json.loads(response.text or '{}')
    payload['estimated_duration'] = estimate_spoken_duration(payload.get('full_script', ''))
    result = FullScript(**payload)
    _validate_script(result)
    logger.info('Generated %.1fs continuous script', result.estimated_duration)
    return result
