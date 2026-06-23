#!/bin/bash
# AI Digest — Clustering pipeline
set -e

cd /home/apps_maker/ai-digest
DATE=$(date +%Y-%m-%d)

.venv/bin/python3 clusterize.py \
    --date "$DATE" \
    --fetch-hours 24 \
    2>&1
