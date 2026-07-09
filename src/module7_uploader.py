from __future__ import annotations

import logging
from pathlib import Path

from src.models import PipelineConfig, ScriptPackage
from src.utils.api_client import with_timeout

logger = logging.getLogger("shorts_pipeline.uploader")


def _get_youtube_service(client_secrets: Path, token_path: Path):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    scopes = ["https://www.googleapis.com/auth/youtube.upload"]
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), scopes)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), scopes)
            creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds)


def upload_video(
    script: ScriptPackage,
    video_path: Path,
    thumbnail_path: Path | None,
    pipeline_config: PipelineConfig,
    credentials_dir: Path,
    mock: bool = False,
) -> tuple[str | None, str | None]:
    if mock:
        logger.warning("YouTube upload skipped in mock mode")
        return "mock-video-id", None

    client_secrets = credentials_dir / "client_secrets.json"
    token_path = credentials_dir / "token.json"
    if not client_secrets.exists():
        return None, f"Missing OAuth client secrets at {client_secrets}"

    @with_timeout(max(pipeline_config.api_timeout_seconds, 300), "youtube_upload")
    def _upload() -> str:
        youtube = _get_youtube_service(client_secrets, token_path)
        # Build description with psychology hook and comment trigger
        description = script.description
        if hasattr(script, 'psychology_hook') and script.psychology_hook:
            description = f"{description}\n\n🧠 Psychology concept: {script.psychology_hook}"
        if hasattr(script, 'comment_trigger') and script.comment_trigger:
            description = f"{description}\n\n{script.comment_trigger}"

        body = {
            "snippet": {
                "title": script.title[:100],
                "description": description[:5000],
                "tags": script.tags[:500],
                "categoryId": pipeline_config.youtube_category_id,
            },
            "status": {
                "privacyStatus": pipeline_config.upload_privacy_status,
                "selfDeclaredMadeForKids": False,
                "containsSyntheticMedia": True,
            },
        }
        from googleapiclient.http import MediaFileUpload

        media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            _, response = request.next_chunk()
        video_id = response["id"]
        if thumbnail_path and thumbnail_path.exists():
            youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(thumbnail_path))).execute()
        return video_id

    try:
        video_id = _upload()
        logger.info("Uploaded video %s with containsSyntheticMedia=true", video_id)
        return video_id, None
    except Exception as exc:
        logger.error("YouTube upload failed: %s", exc)
        return None, str(exc)
