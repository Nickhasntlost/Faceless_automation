import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.models import ScriptPackage, PipelineConfig
from src.module7_uploader import upload_video
from src.utils.encoding import read_json

from src.config_loader import load_pipeline_config

def main():
    run_dir = Path("output/20260629T061230Z")
    
    print(f"Loading script from {run_dir / 'script.json'}")
    script_data = read_json(run_dir / "script.json")
    script = ScriptPackage(**script_data)
    
    video_path = run_dir / "assembly" / "final_short.mp4"
    thumb_path = Path("thumbnail_test.png")
    
    pipeline_config = load_pipeline_config(ROOT)
    pipeline_config.upload_privacy_status = "unlisted"
    
    credentials_dir = Path("credentials")
    credentials_dir.mkdir(exist_ok=True)
    
    client_secrets = credentials_dir / "client_secrets.json"
    if not client_secrets.exists():
        print(f"ERROR: {client_secrets} not found.")
        print("Please place your Desktop-app OAuth client secrets file there and run again.")
        return

    print(f"\nUploading video: {video_path}")
    print(f"Using thumbnail: {thumb_path}")
    print("Initiating YouTube upload. Watch for the browser consent window...\n")
    
    video_id, error = upload_video(script, video_path, thumb_path, pipeline_config, credentials_dir, mock=False)
    
    if error:
        print(f"\nUpload failed: {error}")
    else:
        print(f"\nUpload SUCCESS!")
        print(f"Video URL: https://youtu.be/{video_id}")
        print("Please check YouTube Studio to confirm the 'Altered or synthetic content' disclosure flag is set to TRUE.")

if __name__ == "__main__":
    main()
