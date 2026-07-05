from __future__ import annotations

import logging
from typing import Tuple

from src.models import CharacterBible, MotionRules, PlannedScene, StyleGuide

logger = logging.getLogger("shorts_pipeline.prompt_linter")

def lint_veo_prompt(prompt: str, scene: PlannedScene, char_bible: CharacterBible, motion_rules: MotionRules, style: StyleGuide) -> Tuple[bool, str]:
    """
    Checks the generated Veo prompt against strict rules.
    Returns (is_valid, error_reason).
    """
    prompt_lower = prompt.lower()
    
    # Check cinematic words
    cinematic_words = ["cinematic", "film", "movie", "blockbuster", "camera lens", "dof", "depth of field", "dramatic lighting"]
    for word in cinematic_words:
        if word in prompt_lower:
            return False, f"Prompt contains forbidden cinematic wording: '{word}'"
            
    # Check forbidden styles
    # The visual_prompt_builder appends them as "--no neg1, neg2, neg3"
    # We should only fail if the forbidden word appears BEFORE the "--no" flag
    prompt_before_no = prompt_lower.split("--no")[0] if "--no" in prompt_lower else prompt_lower
    for neg in style.negative_prompts:
        if neg.lower() in prompt_before_no:
            return False, f"Prompt contains forbidden style: '{neg}'"
            
    # Check character exists
    if scene.character and scene.character not in char_bible.characters:
        return False, f"Character '{scene.character}' does not exist in Character Bible."
        
    # Check valid motion
    if scene.motion and scene.motion not in motion_rules.motions:
        return False, f"Motion '{scene.motion}' is not a valid motion preset."
        
    # Length check (Veo prompts should be descriptive but not overly long)
    # Only lint the core prompt, excluding boilerplate
    core_prompt = prompt_before_no
    if "| negative:" in core_prompt:
        core_prompt = core_prompt.split("| negative:")[0]
        
    if len(core_prompt.split()) > 150:
        return False, f"Core visual prompt is too long ({len(core_prompt.split())} words, max 150)."
        
    # Basic repeated adjectives check
    words = prompt_lower.replace(",", "").replace(".", "").split()
    word_counts = {}
    for w in words:
        if len(w) > 4: # Only check significant words
            word_counts[w] = word_counts.get(w, 0) + 1
            if word_counts[w] > 3:
                return False, f"Repeated adjective/word detected: '{w}' used {word_counts[w]} times."

    return True, ""
