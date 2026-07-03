from __future__ import annotations

import logging

from src.models import CreativePlan, RetentionPlan, Storyboard, StoryboardItem, TimestampPlan

logger = logging.getLogger('shorts_pipeline.storyboard_generator')


def generate_storyboard(
    plan: CreativePlan,
    retention: RetentionPlan,
    timestamp_plan: TimestampPlan | None = None,
) -> Storyboard:
    items = []
    template_goals = {
        'Mystery': ['Introduce the mystery', 'Deepen the enigma', 'Reveal a clue', 'Explain the clue', 'Solve the mystery', 'Loop back'],
        'Comparison': ['Introduce the competitors', 'Show the differences', 'Highlight surprising advantage', 'Explain the winner', 'Deliver final verdict', 'Loop back'],
        'Timeline': ['Show the beginning', 'Show first major shift', 'Show accelerating change', 'Show modern day', 'Predict the future', 'Loop back'],
        'Myth vs Fact': ['State the common myth', 'Show why it is believed', 'Bust the myth with fact', 'Explain the real science', 'Deliver the truth', 'Loop back'],
        'Before/After': ['Show the Before', 'Introduce the catalyst', 'Show the transition', 'Explain the change', 'Show the After', 'Loop back'],
        'Problem -> Solution': ['Introduce the problem', 'Show the consequences', 'Reveal the solution', 'Explain how it works', 'Show the successful result', 'Loop back'],
        'Countdown': ['Start the countdown', 'Build the tension', 'Reveal the surprising item', 'Explain the significance', 'Hit number one', 'Loop back'],
        'Journey': ['Start the journey', 'Face the first obstacle', 'Overcome and learn', 'Reach the climax', 'Show the transformation', 'Loop back'],
    }
    progression = template_goals.get(plan.story_template, template_goals['Problem -> Solution'])
    scene_count = len(timestamp_plan.segments) if timestamp_plan else plan.scene_count

    for i in range(scene_count):
        idx = i + 1
        hook_intensity = 'high' if idx == 1 else ('peak' if idx in retention.surprise_at else 'medium')
        curiosity_level = 'peak' if idx < scene_count else 'resolving'
        energy_level = retention.energy_curve[i] if i < len(retention.energy_curve) else 'medium'
        roles = ['Hook', 'Setup', 'Escalation', 'Reveal', 'Explanation', 'Loop'] if scene_count >= 6 else ['Hook', 'Setup', 'Reveal', 'Explanation', 'Loop']
        scene_type = roles[i] if i < len(roles) else 'Escalation'
        step_goal = progression[i] if i < len(progression) else 'Continue the story'
        segment = timestamp_plan.segments[i] if timestamp_plan else None

        items.append(StoryboardItem(
            scene_index=idx,
            purpose=f'Narrative step: {step_goal}',
            narration_goal='',
            visual_goal='Support the supplied timestamp segment visually',
            transition_goal='fast pace' if idx in retention.speed_up_at else 'smooth',
            hook_intensity=hook_intensity,
            curiosity_level=curiosity_level,
            energy_level=energy_level,
            scene_type=scene_type,
            retention_trigger=retention.retention_hooks.get(idx, 'visual_shift'),
            emotional_beat=plan.emotional_arc[i] if i < len(plan.emotional_arc) else 'payoff',
            pause_duration=retention.scene_pauses[i] if i < len(retention.scene_pauses) else 0.5,
            rhythm_template=retention.rhythm_template,
            timestamp_segment=segment,
        ))

    logger.info('Generated visual-only storyboard with %d items using template %s', len(items), plan.story_template)
    return Storyboard(items=items, creative_plan=plan, retention_plan=retention)
