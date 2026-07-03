from __future__ import annotations

import logging

from src.models import CreativePlan, RetentionPlan, Storyboard, StoryboardItem

logger = logging.getLogger("shorts_pipeline.storyboard_generator")


def generate_storyboard(plan: CreativePlan, retention: RetentionPlan) -> Storyboard:
    items = []
    
    # Define dynamic progression goals based on the story template
    template_goals = {
        "Mystery": ["Introduce the mystery", "Deepen the enigma", "Reveal a clue", "Explain the clue", "Solve the mystery", "Loop back"],
        "Comparison": ["Introduce the competitors", "Show the differences", "Highlight surprising advantage", "Explain the winner", "Deliver final verdict", "Loop back"],
        "Timeline": ["Show the beginning", "Show first major shift", "Show accelerating change", "Show modern day", "Predict the future", "Loop back"],
        "Myth vs Fact": ["State the common myth", "Show why it's believed", "Bust the myth with fact", "Explain the real science", "Deliver the truth", "Loop back"],
        "Before/After": ["Show the 'Before'", "Introduce the catalyst", "Show the transition", "Explain the change", "Show the 'After'", "Loop back"],
        "Problem -> Solution": ["Introduce the problem", "Show the consequences", "Reveal the solution", "Explain how it works", "Show the successful result", "Loop back"],
        "Countdown": ["Start the countdown", "Build the tension", "Reveal the surprising item", "Explain the significance", "Hit number one", "Loop back"],
        "Journey": ["Start the journey", "Face the first obstacle", "Overcome and learn", "Reach the climax", "Show the transformation", "Loop back"]
    }
    
    progression = template_goals.get(plan.story_template, template_goals["Problem -> Solution"])
    
    for i in range(plan.scene_count):
        idx = i + 1
        
        hook_intensity = "high" if idx == 1 else ("peak" if idx in retention.surprise_at else "medium")
        curiosity_level = "peak" if idx < plan.scene_count else "resolving"
        energy_level = retention.energy_curve[i] if i < len(retention.energy_curve) else "medium"
        
        # Explicit Scene Roles
        roles_6 = ["Hook", "Setup", "Escalation", "Reveal", "Explanation", "Loop"]
        roles_5 = ["Hook", "Setup", "Reveal", "Explanation", "Loop"]
        roles = roles_6 if plan.scene_count >= 6 else roles_5
        scene_type = roles[i] if i < len(roles) else "Escalation"
            
        retention_trigger = retention.retention_hooks.get(idx, "visual_shift")
        
        arc_beat = plan.emotional_arc[i] if i < len(plan.emotional_arc) else 'payoff'
        
        step_goal = progression[i] if i < len(progression) else "Continue the story"
        
        items.append(StoryboardItem(
            scene_index=idx,
            purpose=f"Narrative step: {step_goal}",
            narration_goal=f"Engage viewer with {step_goal}",
            visual_goal=f"Visually represent: {step_goal}",
            transition_goal="fast pace" if idx in retention.speed_up_at else "smooth",
            hook_intensity=hook_intensity,
            curiosity_level=curiosity_level,
            energy_level=energy_level,
            scene_type=scene_type,
            retention_trigger=retention_trigger,
            emotional_beat=arc_beat,
            pause_duration=retention.scene_pauses[i] if i < len(retention.scene_pauses) else 0.5,
            rhythm_template=retention.rhythm_template
        ))
        
    logger.info("Generated storyboard with %d items using template '%s'", len(items), plan.story_template)
    return Storyboard(items=items, creative_plan=plan, retention_plan=retention)
