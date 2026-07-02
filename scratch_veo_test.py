import os, time
from google import genai
from google.genai import types

os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = r'C:\Users\User\Desktop\Youtube\friday-500814-894523a587fe.json'
client = genai.Client(vertexai=True, project='friday-500814', location='us-central1')

print('=== Veo Final Verification ===')
try:
    operation = client.models.generate_videos(
        model='veo-3.1-lite-generate-001',
        prompt='a red ball rolling on a wooden table, cinematic lighting, vertical 9:16',
        config=types.GenerateVideosConfig(
            aspect_ratio='9:16',
            duration_seconds=8,
            resolution='720p',
        ),
    )
    print(f'Operation started: {operation.name}')

    elapsed = 0
    while not operation.done:
        time.sleep(10)
        elapsed += 10
        operation = client.operations.get(operation=operation)
        print(f'  Polling... elapsed={elapsed}s, done={operation.done}')
        if elapsed > 300:
            print('  TIMEOUT')
            break

    if operation.error:
        print(f'[ERROR] {operation.error}')
    elif operation.result is not None:
        generated = operation.result.generated_videos[0]
        # In Vertex AI, video_bytes is already populated — no download call needed
        video_bytes = generated.video.video_bytes
        if video_bytes:
            out_path = 'test_veo_verified.mp4'
            with open(out_path, 'wb') as f:
                f.write(video_bytes)
            size_kb = len(video_bytes) / 1024
            print(f'[SUCCESS] Video saved to {out_path}: {size_kb:.1f} KB ({len(video_bytes)} bytes)')
        else:
            print('[FAILED] video_bytes is empty/None')
    else:
        print('[FAILED] result is None, no error')

except Exception:
    import traceback
    traceback.print_exc()
