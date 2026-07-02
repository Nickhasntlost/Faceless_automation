import sys
import json
import google.auth
from googleapiclient.discovery import build
import google.oauth2.credentials

def main(video_id):
    token_file = 'credentials/token.json'
    with open(token_file, 'r') as f:
        creds_data = json.load(f)
    
    creds = google.oauth2.credentials.Credentials(
        token=creds_data.get('token'),
        refresh_token=creds_data.get('refresh_token'),
        token_uri=creds_data.get('token_uri'),
        client_id=creds_data.get('client_id'),
        client_secret=creds_data.get('client_secret'),
        scopes=creds_data.get('scopes')
    )
    
    youtube = build("youtube", "v3", credentials=creds)
    request = youtube.videos().list(
        part="snippet,status",
        id=video_id
    )
    response = request.execute()
    if not response.get('items'):
        print("Video not found.")
        return
    
    video = response['items'][0]
    status = video.get('status', {})
    
    print("Video ID:", video_id)
    print("Self-Declared Synthetic Media:", status.get('selfDeclaredMadeWithAlteredOrSyntheticMedia'))
    print("Privacy Status:", status.get('privacyStatus'))

if __name__ == '__main__':
    if len(sys.argv) > 1:
        main(sys.argv[1])
    else:
        print("Usage: python check_yt.py <video_id>")
