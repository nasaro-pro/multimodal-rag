#!/usr/bin/env bash
# 멀티모달 RAG 설치 스크립트 (macOS / Linux)
#   실행: bash setup.sh
#   먼저 OS 프로그램을 설치하세요:
#     macOS : brew install poppler tesseract tesseract-lang
#     Ubuntu: sudo apt install -y poppler-utils tesseract-ocr tesseract-ocr-kor
set -euo pipefail
cd "$(dirname "$0")"

echo "==> 1/4 파이썬 확인"
PY=""
for cand in python3.12 python3.11 python3.10 python3; do
  if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
done
[ -z "$PY" ] && { echo "[실패] Python 3.10~3.12 가 필요합니다."; exit 1; }
"$PY" --version

echo "==> 2/4 가상환경(.venv) 만들기"
[ -x .venv/bin/python ] || "$PY" -m venv .venv

echo "==> 3/4 패키지 설치 (처음에는 수 분 걸립니다)"
if command -v uv >/dev/null 2>&1; then
  uv pip install --python .venv/bin/python -r requirements.txt
else
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
fi

echo "==> 4/4 .env 준비 및 환경 점검"
if [ ! -f .env ]; then
  cp .env.example .env
  echo ".env 를 만들었습니다. 열어서 OPENAI_API_KEY 를 입력하세요."
fi
.venv/bin/python check_env.py || true

echo
echo "설치 끝. 실행 방법:"
echo "   source .venv/bin/activate"
echo "   python multimodal_rag.py"
