@echo off
REM Antifake — inicia no Windows sem Docker
cd /d %~dp0
where tesseract >nul 2>nul || echo [aviso] Instale o Tesseract: https://github.com/UB-Mannheim/tesseract/wiki (marque o idioma Portuguese)
where ffmpeg >nul 2>nul || echo [aviso] Instale o ffmpeg: winget install ffmpeg
if not exist .venv python -m venv .venv
call .venv\Scripts\activate
pip install -q -r backend\requirements.txt
if not exist backend\.env copy backend\.env.example backend\.env
echo Antifake rodando em http://localhost:8000
cd backend
uvicorn app:app --host 0.0.0.0 --port 8000
