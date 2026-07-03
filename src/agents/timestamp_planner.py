from __future__ import annotations

import json
import logging
import os
import re

from src.agents.script_writer import estimate_spoken_duration
from src.models import TimestampPlan, TimestampSegment

logger = logging.getLogger('shorts_pipeline.timestamp_planner')


def _normalize(text: str) -> str:
    return re.sub(r'\s+', ' ', text).strip()


def _sentence_chunks(full_script: str) -> list[str]:
    chunks = re.findall(r'.*?(?:[.!?](?=\s|$)|$)', full_script.strip(), flags=re.DOTALL)
    return [chunk.strip() for chunk in chunks if chunk.strip()]


def _natural_split(full_script: str, target_segments: int) -> list[str]:
    sentences = _sentence_chunks(full_script)
    if len(sentences) < target_segments:
        raise ValueError(
            f'TimestampPlanner needs at least {target_segments} complete thoughts; found {len(sentences)}'
        )

    weights = [max(1, len(re.findall(r'''\b[\w']+\b''', sentence))) for sentence in sentences]
    prefix = [0]
    for weight in weights:
        prefix.append(prefix[-1] + weight)
    ideal = prefix[-1] / target_segments

    costs = [[float('inf')] * (target_segments + 1) for _ in range(len(sentences) + 1)]
    previous = [[-1] * (target_segments + 1) for _ in range(len(sentences) + 1)]
    costs[0][0] = 0.0
    for end in range(1, len(sentences) + 1):
        for groups in range(1, min(target_segments, end) + 1):
            for start in range(groups - 1, end):
                group_weight = prefix[end] - prefix[start]
                cost = costs[start][groups - 1] + (group_weight - ideal) ** 2
                if cost < costs[end][groups]:
                    costs[end][groups] = cost
                    previous[end][groups] = start

    boundaries = []
    end = len(sentences)
    groups = target_segments
    while groups:
        start = previous[end][groups]
        boundaries.append((start, end))
        end = start
        groups -= 1
    boundaries.reverse()
    return [' '.join(sentences[start:end]) for start, end in boundaries]


def _build_plan(parts: list[str], total_duration: float) -> TimestampPlan:
    word_counts = [len(re.findall(r'''\b[\w']+\b''', part)) for part in parts]
    total_words = sum(word_counts)
    cursor = 0.0
    segments = []
    for index, (part, count) in enumerate(zip(parts, word_counts), start=1):
        end = total_duration if index == len(parts) else round(cursor + total_duration * count / total_words, 1)
        segments.append(TimestampSegment(index=index, start=round(cursor, 1), end=round(end, 1), narration=part))
        cursor = end
    return TimestampPlan(segments=segments)


def validate_timestamp_plan(full_script: str, plan: TimestampPlan, target_segments: int = 6) -> list[str]:
    errors = []
    if len(plan.segments) != target_segments:
        errors.append(f'Expected {target_segments} timestamp segments, found {len(plan.segments)}')
    if plan.segments and abs(plan.segments[0].start) > 0.01:
        errors.append('First timestamp segment must start at 0.0')
    for position, segment in enumerate(plan.segments):
        if segment.index != position + 1:
            errors.append(f'Segment index {segment.index} is out of sequence')
        if segment.end <= segment.start:
            errors.append(f'Segment {segment.index} has a non-positive duration')
        if position and abs(segment.start - plan.segments[position - 1].end) > 0.11:
            errors.append(f'Gap or overlap before segment {segment.index}')
        if not re.search(r'[.!?]["\']?$', segment.narration.strip()):
            errors.append(f'Segment {segment.index} ends mid-thought')
    reconstructed = _normalize(' '.join(segment.narration for segment in plan.segments))
    if reconstructed != _normalize(full_script):
        errors.append('Timestamp narration does not reconstruct full_script exactly')
    return errors


def plan_timestamps(
    full_script: str,
    model_id: str | None = None,
    mock: bool = False,
    target_segments: int = 6,
) -> TimestampPlan:
    total_duration = estimate_spoken_duration(full_script)
    if mock or not model_id:
        plan = _build_plan(_natural_split(full_script, target_segments), total_duration)
    else:
        from google import genai

        client = genai.Client(
            vertexai=True,
            project=os.environ['GOOGLE_CLOUD_PROJECT'],
            location=os.environ.get('VERTEX_LOCATION', 'us-central1'),
        )
        prompt = f'''
You are a Timestamp Planner. Divide this already-finished spoken story into exactly {target_segments}
natural, contiguous segments. Never rewrite, add, remove, or reorder a word. Split only after a complete
idea, favoring suspense, reveals, questions, and emotional beats. Do not split mechanically by sentence count.
Target roughly 7-8 seconds per segment while preserving story flow.

Full script ({total_duration:.1f} seconds):
{full_script}

Return JSON only as {{'segments': [{{'index': 1, 'start': 0.0, 'end': 7.8, 'narration': 'exact text'}}]}}.
'''
        response = client.models.generate_content(
            model=model_id,
            contents=prompt,
            config={'response_mime_type': 'application/json'},
        )
        try:
            payload = json.loads(response.text or '{}')
            plan = TimestampPlan(segments=[TimestampSegment(**item) for item in payload['segments']])
            if validate_timestamp_plan(full_script, plan, target_segments):
                raise ValueError('Model split did not preserve the script')
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            logger.warning('Falling back to deterministic natural timestamp splitting')
            plan = _build_plan(_natural_split(full_script, target_segments), total_duration)

    errors = validate_timestamp_plan(full_script, plan, target_segments)
    if errors:
        raise ValueError('Invalid timestamp plan: ' + '; '.join(errors))
    logger.info('Planned %d natural timestamp segments', len(plan.segments))
    return plan
