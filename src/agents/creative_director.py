from __future__ import annotations

import json
import logging
import os
from typing import Optional

from src.models import ChannelIdentity, CreativePlan

logger = logging.getLogger("shorts_pipeline.creative_director")


import random
from pathlib import Path

def _mock_creative_plan(topic: str) -> CreativePlan:
    mock_dir = Path("mock_data")
    if not mock_dir.exists():
        # Fallback if no mock data exists
        return CreativePlan(
            topic=topic or "The future of AI agents",
            story_template="Problem -> Solution",
            viral_angle=f"What they aren't telling you about {topic or 'AI'}",
            curiosity_gap=f"Why {topic or 'AI'} is completely misunderstood",
            hook=f"Is {topic or 'AI'} changing everything today?",
            hook_style="Question",
            core_message=f"{topic or 'AI'} is a tool, not a replacement for human creativity.",
            emotional_arc=["hook", "tension", "surprise", "proof", "payoff", "loop"],
            emotion_timeline=["curiosity", "fear", "relief", "trust", "empowerment", "curiosity"],
            target_audience="Professionals worried about " + (topic or "AI"),
            ending_type="loop",
            cta_style="question",
            visual_identity="robot + earth",
            scene_count=6,
            style_id="flat_2d"
        )
    
    mock_files = list(mock_dir.glob("*.json"))
    if not mock_files:
        raise FileNotFoundError("mock_data directory is empty")
        
    chosen_file = random.choice(mock_files)
    with open(chosen_file, "r", encoding="utf-8") as f:
        plans = json.load(f)
        
    chosen_plan = random.choice(plans)
    if topic:
        chosen_plan["topic"] = topic
        
    return CreativePlan(**chosen_plan)


def generate_creative_plan(
    topic: Optional[str],
    identity: ChannelIdentity,
    model_id: str,
    mock: bool = False,
) -> CreativePlan:
    if mock:
        logger.info("Generating mock creative plan")
        return _mock_creative_plan(topic or "Mock Topic")

    from google import genai

    client = genai.Client(
        vertexai=True,
        project=os.environ["GOOGLE_CLOUD_PROJECT"],
        location=os.environ.get("VERTEX_LOCATION", "us-central1")
    )
    
    prompt = f"""
You are the Creative Director for a YouTube Shorts channel.
Niche: {identity.niche}
Persona: {identity.persona}
Tone: {identity.tone}
Audience: {identity.audience}
Rules: {json.dumps(identity.content_rules)}
Banned: {json.dumps(identity.banned_topics)}

Generate a strategic creative plan for a new short.
Topic: {topic if topic else "Pick a highly engaging topic in the niche"}

CRITICAL RULES:
1. Every field MUST be strictly semantically aligned with the ONE topic.
2. No template leakage. No unrelated generic outputs. The entire plan MUST revolve around the single topic.
3. Choose a `story_template` from: Mystery, Comparison, Timeline, Myth vs Fact, Before/After, Problem -> Solution, Countdown, Journey.

Output JSON exactly matching this schema:
{{
    "topic": "string",
    "story_template": "string (chosen from the list)",
    "viral_angle": "string",
    "curiosity_gap": "string",
    "hook": "string",
    "hook_style": "string (e.g. Question, Contradiction, Surprise, Curiosity)",
    "core_message": "string",
    "emotional_arc": ["hook", "tension", "surprise", "proof", "payoff", "loop"],
    "emotion_timeline": ["list of 5-6 viewer emotions"],
    "target_audience": "string",
    "ending_type": "string (loop | cliffhanger | callback | question)",
    "cta_style": "string (question | challenge | tease)",
    "visual_identity": "string (e.g. robot + neon interface)",
    "scene_count": 5 or 6 (integer),
    "style_id": "flat_2d"
}}
"""
    
    response = client.models.generate_content(
        model=model_id,
        contents=prompt,
        config={"response_mime_type": "application/json"}
    )
    
    try:
        data = json.loads(response.text)
        plan = CreativePlan(**data)
        logger.info("Creative plan generated for topic: %s", plan.topic)
        return plan
    except Exception as e:
        logger.error("Failed to parse CreativePlan from Gemini: %s", e)
        logger.error("Raw response: %s", response.text)
        raise
