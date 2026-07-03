from __future__ import annotations

from src.models import CharacterBible, MotionRules, PlannedScene, StyleGuide


def build_veo_prompt(
    scene: PlannedScene,
    style: StyleGuide,
    char_bible: CharacterBible,
    motion_rules: MotionRules
) -> str:
    character = char_bible.characters.get(scene.character)
    char_desc = character.description if character else scene.character
    char_colors = ", ".join(character.colors) if character else ""
    
    motion = motion_rules.motions.get(scene.motion)
    motion_prompt = motion.prompt_text if motion else scene.motion
    
    neg_prompts = ", ".join(style.negative_prompts)
    
    # Lead with the per-scene unique elements so the model differentiates scenes
    parts = []
    
    # Visual focus first (most important differentiator)
    visual_focus = getattr(scene, 'visual_focus', '')
    if visual_focus:
        parts.append(f"Focus: {visual_focus}")
    
    # Scene-specific elements before character
    parts.extend([
        f"{style.art_style}, camera: {scene.camera}",
        f"background: {scene.background}",
        f"action: {scene.action}",
        f"of {char_desc}, expression: {scene.expression}",
        f"lighting: {style.lighting}",
        f"motion: {motion_prompt}",
        f"Palette: {style.palette}, {char_colors}",
        f"Style rules: {style.character_rules}, {style.animation_rules}, {style.background_rules}"
    ])
    
    prompt = ", ".join(p for p in parts if p)
    if neg_prompts:
        prompt += f" --no {neg_prompts}"
        
    return prompt.strip()
