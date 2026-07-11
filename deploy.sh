#!/bin/bash
set -e

echo "========================================"
echo "  Friday Shorts Pipeline — Deploy"
echo "========================================"

echo ""
echo "Step 1: Building Docker image..."
gcloud builds submit --config cloudbuild.yaml . --project friday-500814

echo ""
echo "Step 2: Updating Cloud Run job..."
gcloud run jobs update shorts-pipeline \
    --image gcr.io/friday-500814/shorts-pipeline:latest \
    --region us-central1 \
    --project friday-500814

echo ""
echo "========================================"
echo "  Deploy complete!"
echo "  To run manually:"
echo "  gcloud run jobs execute shorts-pipeline --region us-central1"
echo "========================================"
