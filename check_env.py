"""
환경 점검 스크립트 — 설치가 제대로 됐는지 한 번에 확인합니다.

    python check_env.py            # 오프라인 점검
    python check_env.py --online   # API 키가 실제로 동작하는지까지 확인 (비용 거의 0)

표준 라이브러리만 사용하므로 패키지 설치가 덜 됐어도 실행됩니다.
"""

import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

APP_DIR = Path(__file__).resolve().parent
problems = []


def row(name, ok, detail="", fix=""):
    print(f"  {name:22s}: {'OK' if ok else '✗'}  {detail}")
    if not ok and fix:
        problems.append(f"{name}: {fix}")


def parse_env(path):
    values = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def is_real_key(key):
    return bool(key) and key.startswith("sk-") and not key.startswith("sk-your")


def read_env():
    """multimodal_rag.py 와 같은 규칙: 이 폴더 .env → (실제 키가 없으면) 상위 폴더 .env"""
    for candidate in (APP_DIR / ".env", APP_DIR.parent / ".env"):
        if candidate.exists():
            values = parse_env(candidate)
            if is_real_key(values.get("OPENAI_API_KEY")):
                for k, v in values.items():
                    os.environ.setdefault(k, v)
                return candidate
    return APP_DIR / ".env" if (APP_DIR / ".env").exists() else None


def main():
    online = "--online" in sys.argv

    print("\n[1] 파이썬")
    v = sys.version_info
    row("Python 버전", (3, 10) <= (v.major, v.minor) <= (3, 12), f"{v.major}.{v.minor}.{v.micro}",
        "Python 3.10~3.12 를 권장합니다 (3.12 에서 검증)")
    in_venv = sys.prefix != sys.base_prefix
    row("가상환경", in_venv, sys.prefix, "가상환경을 켜세요: .venv\\Scripts\\activate (mac: source .venv/bin/activate)")

    print("\n[2] .env")
    env_file = read_env()
    row(".env 파일", env_file is not None, str(env_file or ""), "copy .env.example .env 후 키를 입력하세요")
    key = os.getenv("OPENAI_API_KEY", "")
    key_ok = is_real_key(key)
    row("OPENAI_API_KEY", key_ok, (key[:7] + "..." + key[-4:]) if key_ok else "(없음/예시값)",
        ".env 에 실제 OpenAI 키를 넣으세요")

    print("\n[3] OS 프로그램 (pip 로 설치되지 않음)")
    extra = [os.getenv("POPPLER_PATH"), os.getenv("TESSERACT_PATH")]
    if os.name == "nt":
        extra.append(r"C:\Program Files\Tesseract-OCR")
    for p in extra:
        if p and Path(p).is_dir():
            os.environ["PATH"] = p + os.pathsep + os.environ["PATH"]

    poppler = shutil.which("pdftoppm")
    row("Poppler (pdftoppm)", bool(poppler), poppler or "", "README '1. OS 프로그램 설치' → Poppler")
    tess = shutil.which("tesseract")
    row("Tesseract", bool(tess), tess or "", "README '1. OS 프로그램 설치' → Tesseract")
    if tess:
        out = subprocess.run([tess, "--list-langs"], capture_output=True, text=True)
        langs = set((out.stdout + out.stderr).split())
        row("  언어팩 eng", "eng" in langs, "", "Tesseract 를 다시 설치하세요")
        row("  언어팩 kor (선택)", "kor" in langs, "한국어 PDF 에만 필요",
            "한국어 PDF 를 쓰려면 Tesseract 설치 시 Korean 언어 데이터를 체크하세요")

    print("\n[4] 파이썬 패키지")
    from importlib.metadata import PackageNotFoundError, version

    packages = {  # import 이름: 배포(pip) 이름
        "dotenv": "python-dotenv", "langchain_core": "langchain-core", "langchain_classic": "langchain-classic",
        "langchain_openai": "langchain-openai", "langchain_chroma": "langchain-chroma",
        "langchain_text_splitters": "langchain-text-splitters", "chromadb": "chromadb", "tiktoken": "tiktoken",
        "unstructured": "unstructured", "unstructured_inference": "unstructured-inference",
    }
    for mod, dist in packages.items():
        try:
            importlib.import_module(mod)
            try:
                ver = version(dist)
            except PackageNotFoundError:
                ver = ""
            row(mod, True, ver)
        except Exception as e:  # noqa: BLE001
            row(mod, False, type(e).__name__, "pip install -r requirements.txt")

    if online and key_ok:
        print("\n[5] OpenAI API 연결")
        try:
            from openai import OpenAI

            OpenAI().models.retrieve("gpt-4o-mini")
            row("API 키 인증", True, "gpt-4o-mini 사용 가능")
        except Exception as e:  # noqa: BLE001
            row("API 키 인증", False, f"{type(e).__name__}: {str(e)[:80]}", "키가 맞는지, 결제 등록이 됐는지 확인하세요")

    print()
    if problems:
        print("해결이 필요한 항목:")
        for p in problems:
            print("  -", p)
        sys.exit(1)
    print("모든 점검 통과! → python multimodal_rag.py")


if __name__ == "__main__":
    main()
