import json
import logging
import os
import feedparser
from typing import List, Dict, Any
from src.models import ChannelIdentity

logger = logging.getLogger("shorts_pipeline.trend_scorer")

class TrendCollector:
    def __init__(self):
        self.sources = [
            # Google Trends RSS
            "https://trends.google.com/trends/trendingsearches/daily/rss?geo=US",
            # TechCrunch
            "https://techcrunch.com/feed/",
            # Wired
            "https://www.wired.com/feed/rss",
            # Arxiv AI
            "https://export.arxiv.org/rss/cs.AI"
        ]

    def fetch_trends(self) -> List[str]:
        raw_topics = []
        
        # 1. RSS Feeds (Google Trends, Tech sites)
        for url in self.sources:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:10]:  # Top 10 per source
                    title = entry.title
                    raw_topics.append(title)
            except Exception as e:
                logger.warning(f"Failed to fetch from {url}: {e}")

        # 2. Reddit (Using unauthenticated JSON endpoints to avoid praw setup complexity for now)
        subreddits = ["technology", "science", "Futurology", "space"]
        import urllib.request
        for sub in subreddits:
            url = f"https://www.reddit.com/r/{sub}/hot.json?limit=5"
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36 (ViralShortsGenerator/1.0)'})
                with urllib.request.urlopen(req, timeout=5) as response:
                    data = json.loads(response.read().decode())
                    for post in data['data']['children']:
                        if not post['data']['stickied']:
                            raw_topics.append(post['data']['title'])
            except Exception as e:
                logger.warning(f"Failed to fetch Reddit {sub}: {e}")

        return list(set(raw_topics))

    def pre_filter(self, topics: List[str], identity: ChannelIdentity) -> List[str]:
        """Simple rule-based pre-filter to narrow down to Top 10 candidates"""
        filtered = []
        niche_keywords = set([word.lower() for word in identity.niche.split()])
        
        for topic in topics:
            lower_topic = topic.lower()
            
            # Reject banned topics
            if any(banned.lower() in lower_topic for banned in identity.banned_topics):
                continue
                
            # Score simply by length and keyword presence as a rough heuristic
            score = 0
            if any(kw in lower_topic for kw in niche_keywords):
                score += 5
            
            # Prefer topics that aren't too short or too long
            word_count = len(topic.split())
            if 3 <= word_count <= 15:
                score += 3
                
            filtered.append((score, topic))
            
        # Sort by heuristic score and take top 10
        filtered.sort(key=lambda x: x[0], reverse=True)
        return [t[1] for t in filtered[:10]]


class TrendScorer:
    def __init__(self, model_id: str):
        self.model_id = model_id
        
    def score_trends(self, candidates: List[str], identity: ChannelIdentity) -> str:
        from google import genai
        
        client = genai.Client(
            vertexai=True,
            project=os.environ["GOOGLE_CLOUD_PROJECT"],
            location=os.environ.get("VERTEX_LOCATION", "us-central1"),
        )
        
        prompt = f"""
You are the Trend Scorer for an animated explainer channel.
Your goal is to evaluate the following 10 trending topics and select the absolute best one for a new 45-second animated Short.

Channel Identity:
- Niche: {identity.niche}
- Persona: {identity.persona}
- Audience: {identity.audience}

Evaluate each candidate from 1-10 on these 5 dimensions:
1. Relevance to niche
2. Search growth / Popularity
3. Evergreen potential
4. Visual potential (Can we animate this well?)
5. Story potential (Can this become a compelling 45-second story with a hook and curiosity gap?)

Candidates:
{json.dumps(candidates, indent=2)}

Return a JSON object with:
{{
    "evaluations": [
        {{
            "topic": "...",
            "relevance_score": 8,
            "search_growth_score": 9,
            "evergreen_score": 5,
            "visual_score": 7,
            "story_score": 9,
            "total_score": 38,
            "reasoning": "..."
        }}
    ],
    "best_topic": "The exact string of the chosen topic with the highest total score"
}}
"""
        try:
            response = client.models.generate_content(
                model=self.model_id,
                contents=prompt,
                config={"response_mime_type": "application/json"}
            )
            data = json.loads(response.text or "{}")
            best = data.get("best_topic")
            
            if best:
                logger.info(f"TrendScorer selected: {best}")
                return best
            else:
                return candidates[0]
        except Exception as e:
            logger.error(f"Failed to score trends: {e}")
            return candidates[0]

def get_best_trending_topic(identity: ChannelIdentity, model_id: str, manual_hint: str = None) -> str:
    """
    Implements Hybrid mode: If manual_hint is provided, we could search specifically for that.
    For now, it collects trends, filters them, and scores them.
    """
    logger.info("Collecting trends from sources...")
    collector = TrendCollector()
    raw_topics = collector.fetch_trends()
    
    if manual_hint:
        # In hybrid mode, just pass the manual hint as one of the candidates, highly scored
        candidates = collector.pre_filter(raw_topics, identity)
        if manual_hint not in candidates:
            candidates = [manual_hint] + candidates[:9]
    else:
        candidates = collector.pre_filter(raw_topics, identity)
        
    if not candidates:
        logger.warning("No trend candidates found. Using fallback.")
        return "Latest breakthrough in AI and technology"
        
    logger.info(f"Scoring {len(candidates)} trend candidates...")
    scorer = TrendScorer(model_id=model_id)
    return scorer.score_trends(candidates, identity)
