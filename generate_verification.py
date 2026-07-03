import argparse
import sys
from pathlib import Path
from src.orchestrator import run_pipeline, SimulationFlags

def main():
    root = Path(__file__).parent
    
    topics = [
        "artificial general intelligence",
        "deep sea exploration",
        "quantum computing",
        "the fall of rome",
        "exoplanets"
    ]
    
    sim_flags = SimulationFlags(mock=True, skip_upload=True)
    
    for i, topic in enumerate(topics):
        print(f"Generating video {i+1}/5 for topic: {topic}")
        report_path = run_pipeline(root, sim_flags, topic)
        print(f"Report saved to: {report_path}\n")
        
if __name__ == "__main__":
    main()
