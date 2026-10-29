#!/usr/bin/env bash
# Antifake — inicia no Linux/macOS sem Docker
set -e
cd "$(dirname "$0")"
command -v tesseract >/dev/null || echo "⚠️  Instale o Tesseract (OCR): sudo apt install tesseract-ocr tesseract-ocr-por  |  brew install tesseract tesseract-lang"
command -v ffmpeg >/dev/null    || echo "⚠️  Instale o ffmpeg (vídeo): sudo apt install ffmpeg  |  brew install ffmpeg"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r backend/requirements.txt
[ -f backend/.env ] || cp backend/.env.example backend/.env
set -a; . backend/.env; set +a
echo "✅ Antifake rodando em http://localhost:8000"
cd backend && exec uvicorn app:app --host 0.0.0.0 --port "${PORT:-8000}"
