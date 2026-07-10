"""
module9_drive.py — Save pipeline outputs to Google Drive
Uses Application Default Credentials (the Cloud Run job's service account,
or GOOGLE_APPLICATION_CREDENTIALS locally).
"""
from __future__ import annotations

import os
import logging
from pathlib import Path

logger = logging.getLogger("shorts_pipeline.drive")

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
DRIVE_FOLDER_NAME = "Friday_Shorts_Output"
# Files uploaded by the service account live in its own Drive space unless shared —
# share the output folder with this address so it shows up in the owner's My Drive.
SHARE_WITH_EMAIL = os.environ.get("DRIVE_SHARE_WITH_EMAIL", "fridayhastarted@gmail.com")


def get_drive_service():
    from googleapiclient.discovery import build

    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if creds_path:
        from google.oauth2 import service_account

        creds = service_account.Credentials.from_service_account_file(creds_path, scopes=SCOPES)
    else:
        import google.auth

        creds, _ = google.auth.default(scopes=SCOPES)
    return build("drive", "v3", credentials=creds)


def get_or_create_folder(service, folder_name: str) -> str:
    """Get the Friday_Shorts_Output folder ID, create if missing."""
    query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    results = service.files().list(q=query, fields="files(id, name)").execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"]
    metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    folder = service.files().create(body=metadata, fields="id").execute()
    folder_id = folder["id"]
    logger.info(f"Created Drive folder: {folder_name}")

    if SHARE_WITH_EMAIL:
        try:
            service.permissions().create(
                fileId=folder_id,
                body={"type": "user", "role": "writer", "emailAddress": SHARE_WITH_EMAIL},
                sendNotificationEmail=False,
            ).execute()
            logger.info(f"Shared Drive folder with {SHARE_WITH_EMAIL}")
        except Exception as e:
            logger.warning(f"Failed to share Drive folder with {SHARE_WITH_EMAIL}: {e}")

    return folder_id


def upload_run_outputs(run_id: str, video_path: Path, report_path: Path) -> dict:
    """Upload video and quality report to Google Drive. Non-fatal on failure."""
    try:
        from googleapiclient.http import MediaFileUpload

        service = get_drive_service()
        folder_id = get_or_create_folder(service, DRIVE_FOLDER_NAME)

        uploaded = {}

        if video_path.exists():
            media = MediaFileUpload(str(video_path), mimetype="video/mp4", resumable=True)
            file_metadata = {"name": f"{run_id}_final_short.mp4", "parents": [folder_id]}
            video_file = service.files().create(
                body=file_metadata, media_body=media, fields="id, webViewLink"
            ).execute()
            uploaded["video_link"] = video_file.get("webViewLink")
            logger.info(f"Video uploaded to Drive: {video_file.get('webViewLink')}")

        if report_path.exists():
            media = MediaFileUpload(str(report_path), mimetype="application/json")
            file_metadata = {"name": f"{run_id}_quality_report.json", "parents": [folder_id]}
            report_file = service.files().create(
                body=file_metadata, media_body=media, fields="id"
            ).execute()
            uploaded["report_id"] = report_file.get("id")
            logger.info("Quality report uploaded to Drive")

        return uploaded

    except Exception as e:
        logger.warning(f"Drive upload failed (non-fatal): {e}")
        return {}
