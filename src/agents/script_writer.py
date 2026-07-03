from __future__ import annotations

import json
import logging
import os
import re

from src.models import ChannelIdentity, CreativePlan, FullScript

logger = logging.getLogger('shorts_pipeline.script_writer')

MIN_DURATION_SECONDS = 25.0
MAX_DURATION_SECONDS = 40.0
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
    prompt = f'''
You are the Script Writer for a professional YouTube Shorts channel.

Write ONE complete spoken story before anyone thinks about scenes or visuals.

Creative direction:
{json.dumps(plan.__dict__, indent=2)}

Channel voice:
- Persona: {identity.persona}
- Tone: {identity.tone}
- Audience: {identity.audience}
- Rules: {json.dumps(identity.content_rules)}

Requirements:
- 25 to 40 seconds when spoken, about 60 to 100 words.
- Conversational and written for speech, not an article.
- One continuous story with natural pacing and varied sentence length.
- Strong beginning, increasing curiosity, clear payoff, loop-friendly ending.
- Every idea must lead naturally to the next; avoid repetition and abrupt jumps.
- Do not think in scenes. Do not output timestamps. Do not describe visuals.
- The hook field must appear verbatim at the start of full_script.

Return JSON only:
{{
  'title': 'string',
  'hook': 'string',
  'full_script': 'string',
  'estimated_duration': 46.3,
  'story_template': '{plan.story_template}',
  'hook_style': '{plan.hook_style}'
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
