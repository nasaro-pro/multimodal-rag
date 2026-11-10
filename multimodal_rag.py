"""
멀티모달 RAG — 한 파일 버전
=============================
PDF 의 텍스트·표·이미지(그래프/사진)를 모두 근거로 써서 질문에 답합니다.

    PDF ─▶ ① 추출 ─▶ ② 요약(LLM/VLM) ─▶ ③ 요약으로 검색 ─▶ 원본(이미지 포함)으로 답변
           (unstructured)  (gpt-4o-mini)    (MultiVectorRetriever)

사용법 (이 폴더에서, 가상환경 켠 상태)
    python multimodal_rag.py                                  # 예제 PDF + 기본 질문
    python multimodal_rag.py -q "질문"                         # 질문 지정
    python multimodal_rag.py --questions examples/questions.txt  # 여러 질문 → report.md
    python multimodal_rag.py --chat                           # 대화 모드
    python multimodal_rag.py --pdf 내파일.pdf --lang kor eng    # 다른 PDF (한국어 문서면 kor 추가)
    python multimodal_rag.py --rebuild                        # 캐시 지우고 처음부터

빠르게 돌리기 위한 장치
    - 추출/요약 결과를 PDF별 캐시(pkl)로 저장 → 두 번째 실행부터 API 요약 비용·시간 0
    - 텍스트·표·이미지 요약을 병렬 처리 (--concurrency)
    - 로고·아이콘 같은 작은 이미지는 건너뜀 (--min-kb)
    - 작업 폴더를 PDF별(파일명+내용 해시)로 분리 → 다른 PDF 이미지가 섞이지 않음
"""

import argparse
import base64
import hashlib
import os
import pickle
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

# 윈도우 콘솔·파이프에서 한글이 깨지지 않도록 입출력을 모두 UTF-8 로
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("UNSTRUCTURED_DISABLE_NOTICE", "1")

APP_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = APP_DIR / "output"
DEFAULT_PDF = APP_DIR / "examples" / "reliance.pdf"
# 텍스트에는 없고 사진을 봐야만 답할 수 있는 질문이어야 Before/After 차이가 납니다
DEFAULT_QUESTION = "보고서 사진 속 사람들은 각각 무엇을 하고 있고, 어떤 사업(에너지·리테일·디지털·친환경)을 보여주는 장면이야?"

LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")


# ──────────────────────────────────────────────────────────
# 0. 환경 준비
# ──────────────────────────────────────────────────────────
def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def is_real_key(key):
    return bool(key) and key.startswith("sk-") and not key.startswith("sk-your")


def load_env():
    """이 폴더의 .env 를 읽습니다. 거기에 실제 키가 없으면 상위 폴더의 .env 를 씁니다."""
    from dotenv import dotenv_values, load_dotenv

    for candidate in (APP_DIR / ".env", APP_DIR.parent / ".env"):
        if candidate.exists() and is_real_key(dotenv_values(candidate).get("OPENAI_API_KEY")):
            load_dotenv(candidate)
            break

    if not is_real_key(os.getenv("OPENAI_API_KEY", "")):
        raise SystemExit(
            "OPENAI_API_KEY 가 없습니다.\n"
            f"  → {APP_DIR / '.env'} 파일을 열어 실제 키를 넣으세요 (.env.example 참고)."
        )


def add_ocr_tools_to_path():
    """.env 의 POPPLER_PATH / TESSERACT_PATH, 윈도우 기본 설치 경로를 PATH 에 추가합니다."""
    extra = [os.getenv("POPPLER_PATH"), os.getenv("TESSERACT_PATH")]
    if os.name == "nt":
        extra.append(r"C:\Program Files\Tesseract-OCR")
    for p in extra:
        if p and Path(p).is_dir() and p not in os.environ["PATH"]:
            os.environ["PATH"] = p + os.pathsep + os.environ["PATH"]


def check_ocr_tools(languages):
    """추출 전에 Poppler·Tesseract·OCR 언어팩을 확인해 알기 쉬운 에러를 냅니다."""
    missing = [name for name, cmd in (("Poppler", "pdftoppm"), ("Tesseract", "tesseract")) if not shutil.which(cmd)]
    if missing:
        raise SystemExit(
            f"{', '.join(missing)} 를 찾을 수 없습니다. README 의 '1. OS 프로그램 설치' 를 보고 설치하거나,\n"
            "  .env 에 POPPLER_PATH / TESSERACT_PATH 를 적으세요. (python check_env.py 로 진단)"
        )
    out = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True)
    installed = set((out.stdout + out.stderr).split())
    lacking = [lang for lang in languages if lang not in installed]
    if lacking:
        raise SystemExit(
            f"Tesseract 언어팩이 없습니다: {lacking}\n"
            "  → Tesseract 재설치 시 'Additional language data' 에서 Korean 을 체크하거나 --lang eng 로 실행하세요."
        )


def work_dir_for(pdf_path):
    """PDF 마다 고유한 작업 폴더 (파일명 + 내용 해시 8자리)."""
    digest = hashlib.md5(pdf_path.read_bytes()).hexdigest()[:8]
    return OUTPUT_DIR / f"{pdf_path.stem}_{digest}"


def load_cache(path):
    if path.exists():
        with open(path, "rb") as f:
            return pickle.load(f)
    return None


def save_cache(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump(data, f)


# ──────────────────────────────────────────────────────────
# 1. 추출
# ──────────────────────────────────────────────────────────
def extract_pdf(pdf_path, figures_dir, languages):
    """PDF → 텍스트 블록, 표 블록 (이미지는 figures_dir 에 파일로 저장)."""
    from unstructured.partition.pdf import partition_pdf  # 무거워서 필요할 때만 import

    figures_dir.mkdir(parents=True, exist_ok=True)
    elements = partition_pdf(
        filename=str(pdf_path),
        strategy="hi_res",                 # 레이아웃 모델로 표·그림 정밀 추출
        extract_images_in_pdf=True,        # 그림을 파일로 저장
        infer_table_structure=True,        # 표 행·열 구조 추론
        languages=languages,
        chunking_strategy="by_title",
        max_characters=4000,
        new_after_n_chars=3800,
        combine_text_under_n_chars=2000,
        extract_image_block_output_dir=str(figures_dir),
    )

    texts, tables = [], []
    for el in elements:
        kind = type(el).__name__
        if kind == "Table":
            # 표는 HTML 구조가 있으면 그쪽이 LLM 이 읽기 더 좋습니다
            html = getattr(el.metadata, "text_as_html", None)
            tables.append(html or str(el))
        elif kind == "CompositeElement":
            texts.append(str(el))
    return texts, tables


def split_texts(texts):
    """텍스트를 2000토큰 단위(200 겹침)로 다시 자릅니다."""
    from langchain_text_splitters import CharacterTextSplitter

    splitter = CharacterTextSplitter.from_tiktoken_encoder(chunk_size=2000, chunk_overlap=200)
    return splitter.split_text(" ".join(texts)) if texts else []


# ──────────────────────────────────────────────────────────
# 2. 요약
# ──────────────────────────────────────────────────────────
TEXT_PROMPT = """당신은 표와 텍스트를 요약하여 검색에 활용할 수 있도록 돕는 도우미입니다.
이 요약본들은 임베딩되어 원본 텍스트나 표 요소를 검색하는 데 사용될 것입니다.
주어진 표나 텍스트의 내용을 검색에 최적화된 간결한 요약으로 한국어로 작성해주세요.
수치, 연도, 항목 이름은 빠짐없이 포함하세요.
요약할 표 또는 텍스트: {element}"""

IMAGE_PROMPT = """You are an assistant tasked with summarizing images for retrieval.
These summaries will be embedded and used to retrieve the raw image.
Provide a concise summary of the image that is well optimized for retrieval.
If it is a chart or table, include the title, axis names, every number you can read, units, years and the trend.
If it is a photo, describe who is doing what and which business or topic it represents.
The summary should be written in Korean (Hangul)."""


def summarize_texts_and_tables(texts, tables, concurrency):
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_openai import ChatOpenAI

    model = ChatOpenAI(temperature=0, model=LLM_MODEL)
    chain = {"element": lambda x: x} | ChatPromptTemplate.from_template(TEXT_PROMPT) | model | StrOutputParser()
    cfg = {"max_concurrency": concurrency}
    return (chain.batch(texts, cfg) if texts else []), (chain.batch(tables, cfg) if tables else [])


def mime_of(path):
    return "image/png" if Path(path).suffix.lower() == ".png" else "image/jpeg"


def summarize_images(figures_dir, min_kb, concurrency):
    """figures_dir 안의 이미지를 병렬로 요약합니다. 작은 이미지(로고 등)는 건너뜁니다."""
    from langchain_core.messages import HumanMessage
    from langchain_core.runnables import RunnableLambda
    from langchain_openai import ChatOpenAI

    if not figures_dir.exists():
        return [], []
    files = sorted(
        f for f in figures_dir.iterdir()
        if f.suffix.lower() in IMAGE_SUFFIXES and f.stat().st_size >= min_kb * 1024
    )
    if not files:
        return [], []

    vlm = ChatOpenAI(temperature=0, model=LLM_MODEL, max_tokens=512)

    def one(path):
        b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
        msg = vlm.invoke([HumanMessage(content=[
            {"type": "text", "text": IMAGE_PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:{mime_of(path)};base64,{b64}"}},
        ])])
        return {"file": path.name, "mime": mime_of(path), "b64": b64, "summary": msg.content}

    results = RunnableLambda(one).batch(files, {"max_concurrency": concurrency})
    images = [{"file": r["file"], "mime": r["mime"], "b64": r["b64"]} for r in results]
    return images, [r["summary"] for r in results]


def prepare_data(pdf_path, languages, min_kb, concurrency, rebuild):
    work_dir = work_dir_for(pdf_path)
    figures_dir = work_dir / "figures"
    extracted_pkl = work_dir / "extracted.pkl"
    summaries_pkl = work_dir / "summaries.pkl"

    if rebuild and work_dir.exists():
        shutil.rmtree(work_dir)

    extracted = load_cache(extracted_pkl)
    if extracted is None:
        check_ocr_tools(languages)
        log(f"① 추출 시작: {pdf_path.name} (hi_res, 첫 실행은 레이아웃 모델 다운로드로 더 걸립니다)")
        t = time.time()
        texts, tables = extract_pdf(pdf_path, figures_dir, languages)
        extracted = {"texts": texts, "tables": tables, "texts_2k": split_texts(texts)}
        save_cache(extracted_pkl, extracted)
        log(f"① 추출 완료 {time.time() - t:.1f}초")
    else:
        log("① 추출: 캐시 사용")

    summaries = load_cache(summaries_pkl)
    if summaries is None:
        log("② 요약 시작 (텍스트·표·이미지 병렬)")
        t = time.time()
        text_s, table_s = summarize_texts_and_tables(extracted["texts_2k"], extracted["tables"], concurrency)
        images, image_s = summarize_images(figures_dir, min_kb, concurrency)
        summaries = {"text_summaries": text_s, "table_summaries": table_s, "images": images, "image_summaries": image_s}
        save_cache(summaries_pkl, summaries)
        log(f"② 요약 완료 {time.time() - t:.1f}초")
    else:
        log("② 요약: 캐시 사용")

    data = {**extracted, **summaries, "work_dir": work_dir}
    log(f"   색인 대상 — 텍스트 {len(data['texts_2k'])}건 / 표 {len(data['tables'])}건 / 이미지 {len(data['images'])}건")
    if not (data["texts_2k"] or data["tables"] or data["images"]):
        raise SystemExit("PDF 에서 아무것도 추출하지 못했습니다. 스캔본이면 --lang 설정을, 암호화 여부를 확인하세요.")
    return data


# ──────────────────────────────────────────────────────────
# 3. 다중 벡터 검색기 + 최종 체인
# ──────────────────────────────────────────────────────────
ANSWER_INSTRUCTION = (
    "아래 자료(이미지와 텍스트)를 근거로 질문에 한국어로 답하세요.\n"
    "그래프나 표의 수치는 그대로 인용하세요. 자료에 없는 내용은 모른다고 답하세요.\n\n"
)


def build_retriever(data):
    """요약은 Chroma(검색용), 원본은 InMemoryStore(전달용)에 같은 doc_id 로 저장."""
    from langchain_chroma import Chroma
    from langchain_classic.retrievers.multi_vector import MultiVectorRetriever
    from langchain_core.documents import Document
    from langchain_core.stores import InMemoryStore
    from langchain_openai import OpenAIEmbeddings

    id_key = "doc_id"
    vectorstore = Chroma(
        collection_name=f"mm_rag_{uuid.uuid4().hex[:8]}",
        embedding_function=OpenAIEmbeddings(model=EMBED_MODEL),
    )
    retriever = MultiVectorRetriever(vectorstore=vectorstore, docstore=InMemoryStore(), id_key=id_key)

    def add(summaries, originals):
        if not summaries:
            return
        ids = [str(uuid.uuid4()) for _ in originals]
        retriever.vectorstore.add_documents(
            [Document(page_content=s, metadata={id_key: ids[i]}) for i, s in enumerate(summaries)]
        )
        retriever.docstore.mset(list(zip(ids, originals)))

    # 원본에 type 꼬리표를 붙여 두면 정규식 없이 이미지/텍스트를 구분할 수 있습니다
    add(data["text_summaries"], [Document(page_content=t, metadata={"type": "text"}) for t in data["texts_2k"]])
    add(data["table_summaries"], [Document(page_content=t, metadata={"type": "table"}) for t in data["tables"]])
    add(data["image_summaries"], [
        Document(page_content=img["b64"], metadata={"type": "image", "mime": img["mime"], "file": img["file"]})
        for img in data["images"]
    ])
    return retriever


def split_image_text_types(docs):
    return {
        "images": [d for d in docs if d.metadata.get("type") == "image"],
        "texts": [d.page_content for d in docs if d.metadata.get("type") != "image"],
    }


def img_prompt_func(data_dict):
    """이미지는 전부 먼저, 텍스트·질문은 마지막에 한 번만 넣습니다."""
    from langchain_core.messages import HumanMessage

    content = [
        {"type": "image_url", "image_url": {"url": f"data:{d.metadata['mime']};base64,{d.page_content}"}}
        for d in data_dict["context"]["images"]
    ]
    content.append({
        "type": "text",
        "text": ANSWER_INSTRUCTION
        + f"질문: {data_dict['question']}\n\n참고 텍스트:\n" + "\n".join(data_dict["context"]["texts"]),
    })
    return [HumanMessage(content=content)]


def build_chains(retriever, texts_2k):
    from langchain_chroma import Chroma
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.runnables import RunnableLambda, RunnablePassthrough
    from langchain_openai import ChatOpenAI, OpenAIEmbeddings

    model = ChatOpenAI(temperature=0, model=LLM_MODEL, max_tokens=1024)

    multimodal_chain = (
        {"context": retriever | RunnableLambda(split_image_text_types), "question": RunnablePassthrough()}
        | RunnableLambda(img_prompt_func)
        | model
        | StrOutputParser()
    )

    # 비교용: 이미지·표를 뺀 텍스트 전용 RAG (나머지 조건 동일)
    text_store = Chroma(
        collection_name=f"text_only_{uuid.uuid4().hex[:8]}",
        embedding_function=OpenAIEmbeddings(model=EMBED_MODEL),
    )
    if texts_2k:
        text_store.add_texts(texts_2k)
    text_only_chain = (
        {
            "context": text_store.as_retriever(search_kwargs={"k": 4})
            | RunnableLambda(lambda docs: "\n".join(d.page_content for d in docs)),
            "question": RunnablePassthrough(),
        }
        | ChatPromptTemplate.from_template(ANSWER_INSTRUCTION + "질문: {question}\n\n참고 텍스트:\n{context}")
        | model
        | StrOutputParser()
    )
    return multimodal_chain, text_only_chain


# ──────────────────────────────────────────────────────────
# 4. 질의응답 + 리포트
# ──────────────────────────────────────────────────────────
def ask(question, chains, retriever, compare):
    multimodal_chain, text_only_chain = chains
    found = split_image_text_types(retriever.invoke(question))
    evidence = (f"이미지 {len(found['images'])}건 "
                f"({', '.join(d.metadata['file'] for d in found['images']) or '-'}) / 텍스트·표 {len(found['texts'])}건")

    bar = "=" * 70
    print(f"\n{bar}\n질문: {question}\n검색 근거: {evidence}\n{bar}")

    before = None
    if compare:
        before = text_only_chain.invoke(question)
        print("\n[BEFORE] 텍스트 전용 RAG\n" + "-" * 70 + f"\n{before}")

    after = multimodal_chain.invoke(question)
    print("\n[AFTER] 멀티모달 RAG\n" + "-" * 70 + f"\n{after}\n")
    return {"question": question, "evidence": evidence, "before": before, "after": after}


def write_report(results, pdf_path, data):
    """README 에 그대로 붙일 수 있는 마크다운 리포트를 만듭니다."""
    # PDF 에서 추출한 텍스트에 섞인 ` (예: ₹ 기호가 깨진 것) 가 마크다운 코드 표시로 해석되지 않도록
    md = lambda s: (s or "").replace("`", "\\`")
    one_line = lambda s: md(s).replace("\n", " ").replace("|", "\\|")
    lines = [
        "# 멀티모달 RAG 실행 결과",
        "",
        f"- PDF: `{pdf_path.name}`",
        f"- 실행 시각: {datetime.now():%Y-%m-%d %H:%M}",
        f"- 모델: `{LLM_MODEL}` / 임베딩: `{EMBED_MODEL}`",
        f"- 색인: 텍스트 {len(data['texts_2k'])}건 / 표 {len(data['tables'])}건 / 이미지 {len(data['images'])}건",
        "",
        "## 질의응답 요약",
        "",
        "| # | 질문 | 근거가 된 원본 | 멀티모달 답변 (앞부분) |",
        "| --- | --- | --- | --- |",
    ]
    for i, r in enumerate(results, 1):
        lines.append(f"| {i} | {one_line(r['question'])} | {one_line(r['evidence'])} | {one_line(r['after'])[:120]}… |")

    lines += ["", "## 질문별 Before / After", ""]
    for i, r in enumerate(results, 1):
        lines += [f"### Q{i}. {r['question']}", "", f"**검색 근거:** {r['evidence']}", ""]
        if r["before"] is not None:
            lines += ["**BEFORE — 텍스트 전용 RAG**", "", md(r["before"]), ""]
        lines += ["**AFTER — 멀티모달 RAG**", "", md(r["after"]), ""]

    path = data["work_dir"] / "report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    log(f"리포트 저장: {path}")


def read_questions(path):
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [q.strip() for q in lines if q.strip() and not q.strip().startswith("#")]


def main():
    parser = argparse.ArgumentParser(description="멀티모달 RAG (텍스트·표·이미지)")
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF, help="분석할 PDF 경로 (기본: examples/reliance.pdf)")
    parser.add_argument("-q", "--question", help="질문 하나")
    parser.add_argument("--questions", type=Path, help="질문 목록 파일 (한 줄에 하나, # 은 주석)")
    parser.add_argument("--chat", action="store_true", help="질문을 계속 입력받는 대화 모드")
    parser.add_argument("--lang", nargs="+", default=["eng"], help="OCR 언어 (한국어 문서: --lang kor eng)")
    parser.add_argument("--min-kb", type=int, default=10, help="이 크기(KB)보다 작은 이미지는 요약하지 않음 (기본 10)")
    parser.add_argument("--concurrency", type=int, default=5, help="동시 API 요청 수 (RateLimitError 시 낮추기)")
    parser.add_argument("--no-compare", action="store_true", help="Before(텍스트 전용) 비교 생략")
    parser.add_argument("--rebuild", action="store_true", help="이 PDF 의 캐시를 지우고 처음부터")
    args = parser.parse_args()

    pdf_path = args.pdf.expanduser().resolve()
    if not pdf_path.exists():
        raise SystemExit(f"PDF 가 없습니다: {pdf_path}")

    load_env()
    add_ocr_tools_to_path()

    start = time.time()
    data = prepare_data(pdf_path, args.lang, args.min_kb, args.concurrency, args.rebuild)

    log("③ 검색기·체인 구성")
    retriever = build_retriever(data)
    chains = build_chains(retriever, data["texts_2k"])
    log(f"준비 완료 (총 {time.time() - start:.1f}초)")

    compare = not args.no_compare
    if args.questions:
        questions = read_questions(args.questions)
    elif args.question:
        questions = [args.question]
    elif args.chat:
        questions = []
    else:
        questions = [DEFAULT_QUESTION]

    results = [ask(q, chains, retriever, compare) for q in questions]
    if results:
        write_report(results, pdf_path, data)

    if args.chat:
        print("대화 모드입니다. 빈 줄이나 q 를 입력하면 종료합니다.")
        while True:
            try:
                q = input("\n질문> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not q or q.lower() == "q":
                break
            ask(q, chains, retriever, compare)


if __name__ == "__main__":
    main()
