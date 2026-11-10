# 멀티모달 RAG 설치 스크립트 (Windows)
#   실행: setup.bat 더블클릭  또는  powershell -ExecutionPolicy Bypass -File setup.ps1
#   하는 일: 가상환경(.venv) 생성 → 패키지 설치 → .env 준비 → 환경 점검

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Step($msg) { Write-Host ""; Write-Host "==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host ""; Write-Host "[실패] $msg" -ForegroundColor Red; exit 1 }

# 1. 파이썬 찾기 (3.12 → 3.11 → 3.10 순서, 없으면 python)
Step "1/4 파이썬 확인"
$py = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($ver in @("3.12", "3.11", "3.10")) {
        & py "-$ver" -c "import sys" 2>$null
        if ($LASTEXITCODE -eq 0) { $py = @("py", "-$ver"); break }
    }
}
if (-not $py -and (Get-Command python -ErrorAction SilentlyContinue)) { $py = @("python") }
if (-not $py) { Fail "Python 3.10~3.12 가 없습니다. https://www.python.org/downloads/ 에서 3.12 를 설치하세요." }
$pyExe = $py[0]; $pyArgs = @($py | Select-Object -Skip 1)
& $pyExe @pyArgs --version

# 2. 가상환경 만들기
Step "2/4 가상환경(.venv) 만들기"
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (Test-Path $venvPy) {
    Write-Host ".venv 가 이미 있습니다. 그대로 사용합니다."
} else {
    & $pyExe @pyArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { Fail "가상환경 생성 실패" }
}

# 3. 패키지 설치 (uv 가 있으면 훨씬 빠름)
Step "3/4 패키지 설치 (처음에는 torch 등 내려받느라 수 분 걸립니다)"
if (Get-Command uv -ErrorAction SilentlyContinue) {
    & uv pip install --python $venvPy -r requirements.txt
} else {
    & $venvPy -m pip install --upgrade pip
    & $venvPy -m pip install -r requirements.txt
}
if ($LASTEXITCODE -ne 0) { Fail "패키지 설치 실패. 위 에러 메시지를 확인하세요." }

# 4. .env 준비
Step "4/4 .env 준비 및 환경 점검"
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host ".env 를 만들었습니다. 메모장으로 열어 OPENAI_API_KEY 를 입력하세요:" -ForegroundColor Yellow
    Write-Host "   notepad .env"
}

& $venvPy check_env.py
Write-Host ""
Write-Host "설치 끝. 실행 방법:" -ForegroundColor Green
Write-Host "   .venv\Scripts\activate"
Write-Host "   python multimodal_rag.py"
