from __future__ import annotations

import json
import logging
import os

from src.models import (
    CharacterBible,
    MotionRules,
    PlannedScene,
    PausePoint,
    Storyboard,
    RhythmBeat,
    RhythmConfig,
)

logger = logging.getLogger("shorts_pipeline.scene_planner")


def _validate_scenes(scenes: list[PlannedScene], expected_beats: list[str] = None) -> list[str]:
    warnings = []
    for i, scene in enumerate(scenes):
        sentences = [s.strip() for s in scene.narration.replace("?", ".").replace("!", ".").split(".") if s.strip()]
        if len(sentences) > 2:
            warnings.append(f"Scene {scene.index} has {len(sentences)} sentences (>2 limit).")
        for s in sentences:
            words = s.split()
            if len(words) > 10:
                warnings.append(f"Scene {scene.index} has a sentence with {len(words)} words (>10 limit).")
        
        if expected_beats and i < len(expected_beats):
            if scene.emotional_beat != expected_beats[i]:
                warnings.append(f"Scene {scene.index} emotional_beat '{scene.emotional_beat}' does not match expected '{expected_beats[i]}'.")
                
        if i > 0 and scene.motion == scenes[i-1].motion:
            warnings.append(f"Scene {scene.index} repeats motion '{scene.motion}' from previous scene.")
            
        # Validate rhythms
        if getattr(scene, 'speech_ratio', 0) > 0.75:
            warnings.append(f"Scene {scene.index} speech ratio {scene.speech_ratio} exceeds 0.75.")
        if getattr(scene, 'visual_ratio', 0) < 0.25:
            warnings.append(f"Scene {scene.index} visual ratio {scene.visual_ratio} is below 0.25.")
        
        visual_beats = sum(1 for b in scene.rhythm_plan if b.silence_target > 0)
        if visual_beats < 2:
            warnings.append(f"Scene {scene.index} has only {visual_beats} visual beats (needs >= 2).")
            
        for b in scene.rhythm_plan:
            if b.speech_target > 3.0:
                warnings.append(f"Scene {scene.index} beat '{b.name}' has speech > 3.0s ({b.speech_target}s).")
            
    return warnings


def _mock_scenes(storyboard: Storyboard, char_bible: CharacterBible) -> list[PlannedScene]:
    scenes = []
    # Generate some diversity for mock
    actions = ["looking at camera", "typing fast", "pointing up", "walking left", "surprised jump", "looping back"]
    bgs = ["solid blue", "server room", "city street", "neon grid", "space", "solid blue"]
    motions = ["zoom_in", "slide", "bounce", "pop", "shake", "static_wide"]
    cams = ["front facing", "side angle", "low angle", "close up", "wide angle", "front facing"]
    exprs = ["neutral", "thinking", "surprised", "confident", "celebrating", "neutral"]
    focuses = ["character closeup with emotion", "wide environment establishing shot", "data visualization overlay", "action sequence with motion", "dramatic reveal with lighting change", "character in environment context"]
    
    topic = storyboard.creative_plan.topic.lower()
    allowed_chars = ["robot"]
    for domain, chars in char_bible.topic_mapping.items():
        if domain in topic:
            allowed_chars = chars
            break
            
    chosen_char = allowed_chars[0]
    
    for i, item in enumerate(storyboard.items):
        idx = i % len(actions)
        scenes.append(PlannedScene(
            index=item.scene_index,
            narration=f"This is mock narration {item.scene_index}.",
            purpose=item.purpose,
            emotion=item.energy_level,
            character=chosen_char,
            expression=exprs[idx],
            action=actions[idx],
            background=bgs[idx],
            motion=motions[idx],
            camera=cams[idx],
            transition="cut",
            sfx="whoosh",
            duration=8,
            emotional_beat=item.emotional_beat,
            retention_trigger=item.retention_trigger,
            rhythm_plan=[
                RhythmBeat(name="HOOK", speech_target=2.0, silence_target=0.5),
                RhythmBeat(name="BUILD", speech_target=1.8, silence_target=0.5),
                RhythmBeat(name="PAYOFF", speech_target=2.2, silence_target=1.0)
            ],
            speech_ratio=0.75,
            visual_ratio=0.25,
            visual_focus=focuses[idx]
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
    expected_beats = [item.emotional_beat for item in storyboard.items]
    if mock:
        logger.info("Generating mock planned scenes")
        scenes = _mock_scenes(storyboard, char_bible)
        return scenes, _validate_scenes(scenes, expected_beats)

    from google import genai

    client = genai.Client(
        vertexai=True,
        project=os.environ["GOOGLE_CLOUD_PROJECT"],
        location=os.environ.get("VERTEX_LOCATION", "us-central1")
    )

    available_characters = list(char_bible.characters.keys())
    available_motions = list(motion_rules.motions.keys())
    
    # Pre-calculate the rhythm sequence for each scene
    scene_rhythm_context = []
    for item in storyboard.items:
        template_name = getattr(item, "rhythm_template", "default")
        beat_sequence = rhythm_config.templates.get(template_name, rhythm_config.templates.get("default", []))
        
        beats_json = []
        for beat_name in beat_sequence:
            b_cfg = rhythm_config.beats.get(beat_name)
            if b_cfg:
                beats_json.append({"name": beat_name, "speech_target": b_cfg.speech, "silence_target": b_cfg.silence})
        
        scene_rhythm_context.append({
            "scene_index": item.scene_index,
            "rhythm_template": template_name,
            "required_beats": beats_json
        })
    
    sb_json = json.dumps([item.__dict__ for item in storyboard.items], indent=2)
    rc_json = json.dumps(scene_rhythm_context, indent=2)

    prompt = f"""
You are the Scene Planner. Convert the storyboard into concrete, detailed scenes.
You MUST output valid JSON exactly matching the schema.

Constraints:
1. Max 10 words per sentence. Max 2 sentences per scene.
2. Hook must be the first scene narration. Loop ending must be the last scene narration.
3. Character Selection: Use `topic_mapping` fallback logic to choose a character based on the topic. Available mappings: {char_bible.topic_mapping}. Fallback to {available_characters} if topic doesn't match.
4. Character Expression: MUST match the scene's emotional beat. Do not leave neutral.
5. Motion Selection: Choose from {available_motions}. Follow these rules:
{json.dumps(motion_rules.rules)}
6. Visual Diversity: Track camera, motion, and background history across scenes. Strictly enforce: {diversity_rules.__dict__ if diversity_rules else "Vary every scene"}.
7. Emotional Beat: MUST exactly match the beat provided in the storyboard for each scene.
8. Pacing and Pauses: You must write your narration to flow naturally alongside the required Rhythm Beats.
Each scene has a mandatory `required_beats` sequence. You must output exactly these beats in your JSON for that scene.
Speech ratio must be <= 0.75, Visual ratio must be >= 0.25.
No single beat's speech target can exceed 3.0s.

Storyboard:
{sb_json}

Required Rhythm Timelines per Scene:
{rc_json}

Output JSON Schema:
{{
    "scenes": [
        {{
            "index": 1,
            "narration": "exact words spoken",
            "purpose": "scene purpose",
            "emotion": "character emotion",
            "character": "character name",
            "expression": "facial expression",
            "action": "what they are doing",
            "background": "environment description",
            "motion": "motion preset name",
            "camera": "camera framing",
            "transition": "transition to next",
            "sfx": "sound effect",
            "duration": 8,
            "emotional_beat": "beat name",
            "retention_trigger": "trigger type",
            "visual_focus": "what the camera should focus on (e.g. character closeup, environment wide, data overlay, action sequence, dramatic reveal)",
            "speech_ratio": 0.70,
            "visual_ratio": 0.30,
            "rhythm_plan": [
                {{
                    "name": "HOOK",
                    "speech_target": 2.2,
                    "silence_target": 0.2
                }}
            ]
        }}
    ]
}}
"""
    
    response = client.models.generate_content(
        model=model_id,
        contents=prompt,
        config={"response_mime_type": "application/json"}
    )
    
    try:
        data = json.loads(response.text)
        scenes = []
        raw_scenes = data.get("scenes", []) if isinstance(data, dict) else data
        for s in raw_scenes:
            # Convert dicts back to RhythmBeat
            rhythm_plan = [RhythmBeat(**b) for b in s.get("rhythm_plan", [])]
            s["rhythm_plan"] = rhythm_plan
            
            # Remove any pause_points if generated
            s.pop("pause_points", None)
            
            scenes.append(PlannedScene(**s))
            
        warnings = _validate_scenes(scenes, expected_beats)
        logger.info("Planned %d scenes with %d validation warnings", len(scenes), len(warnings))
        return scenes, warnings
    except Exception as e:
        logger.error("Failed to parse PlannedScenes from Gemini: %s", e)
        logger.error("Raw response: %s", response.text)
        raise
