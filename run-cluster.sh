#!/bin/bash
# AI Digest — Clustering pipeline
# Runs after daily fetch, analyzes clusters, sends to Telegram
set -e

cd /home/apps_maker/ai-digest
DATE=$(date +%Y-%m-%d)

export MINIMAX_API_KEY="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJodHRwOi8vbWluaW1heC5jaGF0IiwiaWF0IjoxNzUwNzI4MDA3LCJleHAiOjE3NTEyODM0MDcsInJvbGUiOiJhcGkiLCJwZXJtaXNzaW9uIjpbIm11dGVkIl19.r1LvZ1mZP7V3Xp-8RqGsc5N-3gAb4BHC3w1v1gY3R2A"

.venv/bin/python3 clusterize.py \
    --date "$DATE" \
    --fetch-hours 24 \
    2>&1
