from __future__ import annotations

import logging

from src.models import CreativePlan, RetentionPlan, RhythmConfig

logger = logging.getLogger("shorts_pipeline.retention_director")


def generate_retention_plan(plan: CreativePlan, rhythm_config: RhythmConfig) -> RetentionPlan:
    scene_count = plan.scene_count
    
    surprise_at = [3] if scene_count >= 3 else [scene_count]
    speed_up_at = [2] if scene_count >= 2 else []
    zoom_at = [1, 4] if scene_count >= 4 else [1]
    new_object_at = [3, 5] if scene_count >= 5 else [3]
    
    energy_curve = ["medium", "high", "peak", "high", "medium", "low"]
    
    retention_hooks = {}
    if scene_count >= 2:
        retention_hooks[2] = "pattern interrupt"
    if scene_count >= 4:
        retention_hooks[4] = "stat reveal"
        
    scene_pauses = []
    for i in range(scene_count):
        energy = energy_curve[i]
        # High-energy scenes need almost no pause. Reveal scenes need longer pauses.
        if i + 1 in surprise_at or i + 1 in new_object_at:
            scene_pauses.append(0.6)  # Reveal pause
        elif energy == "peak":
            scene_pauses.append(0.0)
        elif energy == "high":
            scene_pauses.append(0.2)
        elif energy == "medium":
            scene_pauses.append(0.4)
        else:
            scene_pauses.append(0.5)
            
    rhythm_template_name = plan.story_template
    if rhythm_template_name not in rhythm_config.templates:
        rhythm_template_name = "default"
    
    logger.info("Generated retention plan for %d scenes using rhythm template '%s'", scene_count, rhythm_template_name)
    
    return RetentionPlan(
        surprise_at=surprise_at,
        speed_up_at=speed_up_at,
        zoom_at=zoom_at,
        new_object_at=new_object_at,
        energy_curve=energy_curve[:scene_count],
        retention_hooks=retention_hooks,
        scene_pauses=scene_pauses,
        rhythm_template=rhythm_template_name
    )
