from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict

from src.models import CharacterBible, MotionRules, PlannedScene, RhythmBeat, RhythmConfig, Storyboard

logger = logging.getLogger('shorts_pipeline.scene_planner')


def _validate_scenes(
    scenes: list[PlannedScene],
    expected_beats: list[str] | None = None,
    expected_narrations: list[str] | None = None,
) -> list[str]:
    warnings = []
    for i, scene in enumerate(scenes):
        if expected_narrations and i < len(expected_narrations) and scene.narration != expected_narrations[i]:
            warnings.append(f'Scene {scene.index} narration differs from its timestamp segment.')
        if expected_beats and i < len(expected_beats) and scene.emotional_beat != expected_beats[i]:
            warnings.append(
                f'Scene {scene.index} emotional_beat {scene.emotional_beat!r} '
                f'does not match expected {expected_beats[i]!r}.'
            )
        if i > 0 and scene.motion == scenes[i - 1].motion:
            warnings.append(f'Scene {scene.index} repeats motion {scene.motion!r} from previous scene.')
        if scene.speech_ratio > 0.75:
            warnings.append(f'Scene {scene.index} speech ratio {scene.speech_ratio} exceeds 0.75.')
        if scene.visual_ratio < 0.25:
            warnings.append(f'Scene {scene.index} visual ratio {scene.visual_ratio} is below 0.25.')
        visual_beats = sum(1 for beat in scene.rhythm_plan if beat.silence_target > 0)
        if visual_beats < 2:
            warnings.append(f'Scene {scene.index} has only {visual_beats} visual beats (needs >= 2).')
        for beat in scene.rhythm_plan:
            if beat.speech_target > 3.0:
                warnings.append(
                    f'Scene {scene.index} beat {beat.name!r} has speech > 3.0s ({beat.speech_target}s).'
                )
    return warnings


def _require_timestamp_segments(storyboard: Storyboard):
    segments = [item.timestamp_segment for item in storyboard.items]
    if any(segment is None for segment in segments):
        raise ValueError(
            'ScenePlanner requires timestamped narration. Run ScriptWriter and TimestampPlanner first.'
        )
    return segments


def _mock_scenes(storyboard: Storyboard, char_bible: CharacterBible) -> list[PlannedScene]:
    segments = _require_timestamp_segments(storyboard)
    actions = ['looking at camera', 'typing fast', 'pointing up', 'walking left', 'surprised jump', 'looping back']
    backgrounds = ['solid blue', 'server room', 'city street', 'neon grid', 'space', 'solid blue']
    motions = ['zoom_in', 'slide', 'bounce', 'pop', 'shake', 'static_wide']
    cameras = ['front facing', 'side angle', 'low angle', 'close up', 'wide angle', 'front facing']
    expressions = ['curious', 'thinking', 'surprised', 'confident', 'celebrating', 'knowing']
    focuses = [
        'character closeup with emotion',
        'wide environment establishing shot',
        'data visualization overlay',
        'action sequence with motion',
        'dramatic reveal with lighting change',
        'character in environment context',
    ]
    topic = storyboard.creative_plan.topic.lower()
    allowed_characters = ['robot']
    for domain, characters in char_bible.topic_mapping.items():
        if domain in topic:
            allowed_characters = characters
            break
    chosen_character = allowed_characters[0]

    scenes = []
    for i, (item, segment) in enumerate(zip(storyboard.items, segments)):
        idx = i % len(actions)
        scenes.append(PlannedScene(
            index=item.scene_index,
            narration=segment.narration,
            purpose=item.purpose,
            emotion=item.energy_level,
            character=chosen_character,
            expression=expressions[idx],
            action=actions[idx],
            background=backgrounds[idx],
            motion=motions[idx],
            camera=cameras[idx],
            transition='cut',
            sfx='whoosh',
            duration=segment.duration,
            emotional_beat=item.emotional_beat,
            retention_trigger=item.retention_trigger,
            rhythm_plan=[
                RhythmBeat(name='HOOK', speech_target=2.0, silence_target=0.5),
                RhythmBeat(name='BUILD', speech_target=1.8, silence_target=0.5),
                RhythmBeat(name='PAYOFF', speech_target=2.2, silence_target=1.0),
            ],
            speech_ratio=0.75,
            visual_ratio=0.25,
            visual_focus=focuses[idx],
        ))
    return scenes


def plan_scenes(
    storyboard: Storyboard,
    char_bible: CharacterBible,
    motion_rules: MotionRules,
    rhythm_config: RhythmConfig,
    model_id: str,
    mock: bool = False,
    diversity_rules=None,
) -> tuple[list[PlannedScene], list[str]]:
    segments = _require_timestamp_segments(storyboard)
    expected_beats = [item.emotional_beat for item in storyboard.items]
    expected_narrations = [segment.narration for segment in segments]
    if mock:
        logger.info('Generating mock visual plans around supplied narration')
        scenes = _mock_scenes(storyboard, char_bible)
        return scenes, _validate_scenes(scenes, expected_beats, expected_narrations)

    from google import genai

    client = genai.Client(
        vertexai=True,
        project=os.environ['GOOGLE_CLOUD_PROJECT'],
        location=os.environ.get('VERTEX_LOCATION', 'us-central1'),
    )
    available_characters = list(char_bible.characters.keys())
    available_motions = list(motion_rules.motions.keys())
    scene_rhythm_context = []
    for item in storyboard.items:
        template_name = item.rhythm_template
        beat_sequence = rhythm_config.templates.get(template_name, rhythm_config.templates.get('default', []))
        beats = []
        for beat_name in beat_sequence:
            beat_config = rhythm_config.beats.get(beat_name)
            if beat_config:
                beats.append({
                    'name': beat_name,
                    'speech_target': beat_config.speech,
                    'silence_target': beat_config.silence,
                })
        scene_rhythm_context.append({
            'scene_index': item.scene_index,
            'rhythm_template': template_name,
            'required_beats': beats,
        })

    prompt = f'''
You are the Scene Planner. The spoken story and timestamp split are final and immutable.
Plan visuals for each supplied segment. Never write, rewrite, paraphrase, or return narration.

For every segment choose: character, expression, action, background, motion, camera,
animation rhythm, transition, sound effect, visual focus, and emphasis words.

Rules:
- Character mapping: {char_bible.topic_mapping}; available characters: {available_characters}.
- Available motions: {available_motions}.
- Motion rules: {json.dumps(motion_rules.rules)}.
- Diversity rules: {asdict(diversity_rules) if diversity_rules else 'Vary every scene'}.
- Emotional beat and scene index must exactly match the storyboard.
- Use exactly the required rhythm beats. Speech ratio <= 0.75; visual ratio >= 0.25.
- No rhythm beat may have more than 3.0 seconds of speech.

Storyboard with immutable timestamp segments:
{json.dumps([asdict(item) for item in storyboard.items], indent=2, default=lambda o: o.__dict__ if hasattr(o, '__dict__') else str(o))}

Required rhythm timelines:
{json.dumps(scene_rhythm_context, indent=2)}

Return JSON only as {{'scenes': [{{
  'index': 1,
  'purpose': 'scene purpose',
  'emotion': 'character emotion',
  'character': 'character name',
  'expression': 'facial expression',
  'action': 'visible action',
  'background': 'environment',
  'motion': 'motion preset',
  'camera': 'camera framing',
  'transition': 'transition to next',
  'sfx': 'sound effect',
  'emotional_beat': 'exact supplied beat',
  'retention_trigger': 'trigger type',
  'visual_focus': 'camera focus',
  'speech_ratio': 0.70,
  'visual_ratio': 0.30,
  'rhythm_plan': [{{'name': 'HOOK', 'speech_target': 2.2, 'silence_target': 0.2}}],
  'emphasis_words': ['word1']
}}]}}.
Do not include a narration field.
'''
    response = client.models.generate_content(
        model=model_id,
        contents=prompt,
        config={'response_mime_type': 'application/json'},
    )
    try:
        data = json.loads(response.text or '{}')
        raw_scenes = data.get('scenes', []) if isinstance(data, dict) else data
        if len(raw_scenes) != len(segments):
            raise ValueError(f'Expected {len(segments)} visual scenes, received {len(raw_scenes)}')
        scenes = []
        for position, (raw_scene, segment) in enumerate(zip(raw_scenes, segments)):
            raw_scene.pop('narration', None)
            raw_scene.pop('pause_points', None)
            raw_scene['index'] = storyboard.items[position].scene_index
            raw_scene['narration'] = segment.narration
            raw_scene['duration'] = segment.duration
            raw_scene['rhythm_plan'] = [RhythmBeat(**beat) for beat in raw_scene.get('rhythm_plan', [])]
            raw_scene['emphasis_words'] = raw_scene.get('emphasis_words') or []
            scenes.append(PlannedScene(**raw_scene))
        warnings = _validate_scenes(scenes, expected_beats, expected_narrations)
        logger.info('Planned %d visual scenes with %d warnings', len(scenes), len(warnings))
        return scenes, warnings
    except Exception as exc:
        logger.error('Failed to parse visual scene plan: %s', exc)
        logger.error('Raw response: %s', response.text)
        raise
