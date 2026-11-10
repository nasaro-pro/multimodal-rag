# 멀티모달 RAG

PDF 안의 **텍스트 · 표 · 이미지(그래프/사진)** 를 모두 근거로 써서 질문에 답하는 RAG 입니다.
텍스트만 쓰는 RAG 는 그래프나 사진에만 있는 정보를 놓치지만, 이 프로젝트는 이미지까지 검색해서 모델에게 보여줍니다.

```
                ┌─ 텍스트 ─┐                 ┌─ 요약 ─▶ Chroma (검색용)  ┐
PDF ─▶ 추출 ──┼─ 표 ─────┼─▶ LLM/VLM 요약 ─┤                           ├─ doc_id 로 연결
 (unstructured) └─ 이미지 ─┘   (gpt-4o-mini)  └─ 원본 ─▶ InMemoryStore    ┘
                                                        │
질문 ─▶ 요약으로 검색 ─▶ 원본(이미지 포함) 꺼내기 ─▶ 멀티모달 LLM ─▶ 답변
```

핵심 아이디어는 **"요약으로 찾고, 원본으로 답한다"** 입니다. 이미지는 그대로는 검색이 안 되므로 요약문을 만들어 검색에 쓰고, 답할 때는 원본 이미지를 모델에 보여줍니다.

---

## 폴더 구성

```
multimodal_rag_app/
├─ multimodal_rag.py      # 본체 (추출 → 요약 → 검색기 → 체인 → Before/After)
├─ check_env.py           # 설치 점검
├─ requirements.txt       # 파이썬 패키지 (버전 고정)
├─ setup.bat / setup.ps1  # 윈도우 설치
├─ setup.sh               # mac / Linux 설치
├─ .env.example           # API 키 템플릿
├─ examples/
│  ├─ reliance.pdf        # 예제 PDF (Reliance Industries 연차보고서 2021-22 개요, 3쪽)
│  ├─ questions.txt       # 예제 질문 5건
│  └─ sample_report.md    # 예제 실행 결과
└─ output/                # 실행하면 생김 (PDF별 이미지·캐시·리포트)
```

---

## 1. OS 프로그램 설치 (최초 1회)

pip 로 설치되지 않는 프로그램 2개가 필요합니다.

| 프로그램 | 역할 | Windows | macOS |
| --- | --- | --- | --- |
| **Poppler** | PDF → 이미지 변환 | [poppler-windows 릴리스](https://github.com/oschwartz10612/poppler-windows/releases) zip 을 받아 압축을 풀고 `Library\bin` 폴더를 PATH 에 추가 | `brew install poppler` |
| **Tesseract** | 글자 인식(OCR) | [UB Mannheim 설치 파일](https://github.com/UB-Mannheim/tesseract/wiki) 실행. 한국어 PDF 를 쓸 거면 설치 중 *Additional language data* 에서 **Korean** 체크 | `brew install tesseract tesseract-lang` |

> Ubuntu: `sudo apt install -y poppler-utils tesseract-ocr tesseract-ocr-kor`

PATH 를 건드리기 싫다면 `.env` 에 `POPPLER_PATH`, `TESSERACT_PATH` 로 bin 폴더를 적어도 됩니다.
Tesseract 를 기본 위치(`C:\Program Files\Tesseract-OCR`)에 설치했다면 자동으로 찾습니다.

## 2. 설치

**Windows** — `setup.bat` 을 더블클릭하거나 터미널에서:

```powershell
cd multimodal_rag_app
.\setup.bat
```

**macOS / Linux**

```bash
cd multimodal_rag_app
bash setup.sh
```

설치 스크립트가 하는 일: `.venv` 생성 → `requirements.txt` 설치 → `.env` 생성 → `check_env.py` 실행.
처음에는 PDF 레이아웃 모델용 torch 등을 받느라 몇 분 걸립니다 (`uv` 가 깔려 있으면 자동으로 써서 훨씬 빠릅니다).

<details>
<summary>수동 설치</summary>

```powershell
python -m venv .venv
.venv\Scripts\activate            # mac: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env            # mac: cp .env.example .env
```
</details>

## 3. API 키 넣기

`.env` 를 열어 OpenAI 키를 넣습니다.

```
OPENAI_API_KEY=sk-proj-xxxxxxxx
```

이 폴더의 `.env` 에 키가 없으면 상위 폴더의 `.env` 를 대신 씁니다 (수업 프로젝트 안에서 쓸 때 편하도록).

점검:

```powershell
.venv\Scripts\activate
python check_env.py            # 모두 OK 면 준비 완료
python check_env.py --online   # 키가 실제로 동작하는지까지 확인
```

## 4. 실행

```powershell
python multimodal_rag.py                                   # 예제 PDF + 기본 질문 (Before/After 출력)
python multimodal_rag.py -q "CSR 기여액은 얼마야?"           # 질문 하나
python multimodal_rag.py --questions examples/questions.txt  # 질문 여러 개 → report.md
python multimodal_rag.py --chat                            # 대화 모드
python multimodal_rag.py --pdf C:\docs\내보고서.pdf --lang kor eng   # 내 PDF (한국어면 kor 추가)
```

| 옵션 | 설명 | 기본값 |
| --- | --- | --- |
| `--pdf` | 분석할 PDF | `examples/reliance.pdf` |
| `-q`, `--question` | 질문 하나 | 사진 관련 예제 질문 |
| `--questions` | 질문 목록 파일 (한 줄에 하나, `#` 주석) | — |
| `--chat` | 질문을 계속 입력받기 (빈 줄/`q` 로 종료) | — |
| `--lang` | OCR 언어 | `eng` |
| `--min-kb` | 이보다 작은 이미지(로고·아이콘)는 요약 생략 | `10` |
| `--concurrency` | 동시 API 요청 수 | `5` |
| `--no-compare` | 텍스트 전용 RAG(Before) 비교 생략 | — |
| `--rebuild` | 이 PDF 의 캐시를 지우고 처음부터 | — |

### 실행 시간 (examples/reliance.pdf, 3쪽 기준)

| | 시간 |
| --- | --- |
| 첫 실행 준비 (추출 약 60초 + 이미지 22장 요약 약 20초) | 약 1분 20초 |
| 두 번째부터 준비 (캐시 사용) | 약 5초 |
| 질문 1건 답변 (Before + After) | 약 5~10초 |

설치 직후 깨끗한 환경에서 `--questions examples/questions.txt` 로 5건을 돌리면 총 2분 정도 걸립니다.

PDF 별 결과는 `output/<파일명>_<해시>/` 에 저장됩니다.

```
output/reliance_xxxxxxxx/
├─ figures/         # 추출된 이미지
├─ extracted.pkl    # 추출 캐시
├─ summaries.pkl    # 요약 캐시 (다시 돌려도 API 비용 없음)
└─ report.md        # 질의응답 요약표 + 질문별 Before/After (README 에 그대로 붙이기)
```

---

## 좋은 질문 고르기

Before/After 차이가 드러나려면 **그래프·표·사진을 봐야만 답할 수 있는 질문**이어야 합니다.

| | 예시 |
| --- | --- |
| 좋음 | "그래프에서 2023년 이후 추세를 설명해줘", "사진 속 사람들은 무엇을 하고 있어?" |
| 나쁨 | "이 문서는 무엇에 관한 거야?" (본문만으로 답할 수 있음) |

## 비용

`gpt-4o-mini` + `text-embedding-3-small` 기준, 예제 PDF 첫 실행은 수십 원 수준입니다.
요약은 캐시되므로 같은 PDF 를 다시 돌리면 질문 답변 비용만 듭니다.
이미지가 많은 PDF 는 `--min-kb` 를 올리면 요약할 이미지 수가 줄어듭니다.

## 자주 나는 에러

| 증상 | 원인 | 해결 |
| --- | --- | --- |
| `OPENAI_API_KEY 가 없습니다` | `.env` 미작성 / 예시값 그대로 | `.env` 에 실제 키 입력 |
| `Poppler, Tesseract 를 찾을 수 없습니다` | OS 프로그램 미설치 / PATH 누락 | 1단계 설치 후 **터미널 재시작**, 또는 `.env` 에 경로 지정 |
| `Tesseract 언어팩이 없습니다: ['kor']` | 한국어 데이터 미설치 | Tesseract 재설치 시 Korean 체크, 또는 `--lang eng` |
| `RateLimitError` | 동시 요청 과다 | `--concurrency 2` |
| 검색 근거에 이미지가 0건 | 질문이 이미지 요약과 동떨어짐 | 그래프/사진에 있을 법한 단어로 질문 |
| 첫 실행이 오래 멈춘 듯함 | 레이아웃 모델(YOLOX) 최초 다운로드 | 정상. 한 번만 받습니다 |
| PDF 를 바꿨는데 예전 결과가 나옴 | — | 캐시는 파일 내용 해시로 구분됩니다. 강제로 다시 하려면 `--rebuild` |

## 수업 노트북과의 관계

| 노트북 | 이 파일의 함수 |
| --- | --- |
| 01 데이터 추출 | `extract_pdf`, `split_texts` |
| 02 요약 생성 | `summarize_texts_and_tables`, `summarize_images` |
| 03 최종 체인 | `build_retriever`, `img_prompt_func`, `build_chains`, `ask` |

노트북 대비 개선점
- 이미지 요약을 **병렬 처리** (노트북은 한 장씩 순서대로)
- 작업 폴더를 **PDF 별로 분리** (다른 PDF 의 이미지가 섞이지 않음)
- 검색된 이미지를 **전부** 모델에 전달하고, 이미지/텍스트 구분을 정규식 대신 메타데이터로 처리
- 이미지 요약 프롬프트에 "축 이름·수치·추세 / 사진 속 행동" 지시 추가
- 실행 결과를 `report.md` 로 자동 저장
