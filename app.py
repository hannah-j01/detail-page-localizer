"""상세페이지 현지화 도우미 — Streamlit 화면.

실행: .venv\\Scripts\\streamlit run app.py
흐름: ① 입력(텍스트·이미지·PDF·URL) → ② 추출 확인 → ③ 전략 선택 → ④ 생성 결과 검수 → ⑤ 내보내기
"""
import os
import re
import time

import streamlit as st
from dotenv import load_dotenv

from core import llm, media, pipeline, url_import
from core.config_loader import load_all, marketplaces_for, risk_rules_for
from core.edits import COL_BACK, COL_LOCALIZED, COL_ORIGINAL, apply_guide_edits, is_edited, table_rows, with_guides
from core.export import INCLUDED, compute_metrics, final_texts, to_json, to_markdown
from core.preview import render_preview
from core.review import ORIGINAL_ITEM_ID, full_review
from core.schemas import FIELD_LABELS, ProductInput

load_dotenv()
st.set_page_config(page_title="상세페이지 현지화 도우미", page_icon="🌏", layout="wide")

CFG = load_all()
STEPS = ["① 상품 정보 입력", "② 추출 결과 확인", "③ 마케팅 전략 선택", "④ 생성 결과 검수", "⑤ 내보내기"]
STAGES = ["extraction", "strategy", "page"]           # 단계별 결과가 저장되는 session_state 키
INPUT_KEYS = ["product", "media", "url_page", "url_images", "url_skipped", "up_images", "up_pdf", "url_input"]
SEVERITY_ICON = {"높음": "🔴", "중간": "🟠", "낮음": "🟡"}
REQUIRED = ["name", "features", "description_ko", "target_customer", "goal"]
OPTIONAL = ["price", "specs", "reviews", "certifications", "brand_tone", "competitors", "sales_data", "page_text"]
LONG_FIELDS = {"features", "description_ko", "specs", "reviews", "certifications", "brand_tone", "competitors", "sales_data", "page_text"}
ss = st.session_state


# ── 공통 도구 ────────────────────────────────────────────────
def get_secret(name: str, default: str = "") -> str:
    """환경 변수(.env) → Streamlit secrets 순서로 읽는다. 키를 코드에 쓰지 않기 위함."""
    value = os.environ.get(name)
    if value:
        return value
    try:
        value = st.secrets.get(name, default)
    except Exception:  # secrets 파일이 없으면 오류가 난다
        return default
    if value:
        os.environ[name] = str(value)   # anthropic SDK 가 환경 변수에서 키를 읽도록
    return str(value)


def reset_from(stage: str) -> None:
    """해당 단계와 그 뒤 단계의 결과를 지운다(입력을 바꾸면 이후 결과는 무효)."""
    for key in STAGES[STAGES.index(stage):]:
        ss.pop(key, None)
    for key in [k for k in ss.keys() if k.startswith(("dec_", "txt_", "gdec_", "gedit_", "ginst_", "gkeep_"))]:
        del ss[key]


def reset_all() -> None:
    reset_from("extraction")
    for key in INPUT_KEYS + [k for k in ss.keys() if k.startswith("urlimg_")]:
        ss.pop(key, None)


def run_ai(label: str, fn, *args, mode: str):
    """AI 단계를 실행한다. 실제 호출이면 횟수 제한과 비용 기록을 처리한다."""
    if mode == "live":
        limit = int(get_secret("LIVE_CALL_LIMIT", "9"))
        if ss.get("live_calls", 0) >= limit:
            st.error(f"이 세션의 AI 호출 한도({limit}회)에 도달했습니다. 비용 보호를 위한 제한입니다.")
            return None
    with st.spinner(label):
        try:
            result, usage = fn(*args, mode)
        except llm.LLMError as e:
            st.error(str(e))
            return None
    if usage:
        ss.live_calls = ss.get("live_calls", 0) + 1
        ss.setdefault("usages", []).append(usage)
    return result


def collect_decisions(page) -> dict:
    decisions = {}
    for item in page.items:
        text = ss.get(f"txt_{item.id}", item.localized)
        status = ss.get(f"dec_{item.id}", "미검토")
        if status == "수락" and text.strip() != item.localized.strip():
            status = "편집"
        decisions[item.id] = {"status": status, "text": text}
    return decisions


def edited_guide(g):
    """화면에서 고친 내용(표 편집 변경분, 지시 문장)을 반영한 지시서."""
    instructions = ss.get(f"ginst_{g.image_id}")
    keep = ss.get(f"gkeep_{g.image_id}")
    return apply_guide_edits(
        g, ss.get(f"gedit_{g.image_id}"),
        None if instructions in (None, "\n".join(g.edit_instructions)) else instructions,
        None if keep in (None, "\n".join(g.keep_unchanged)) else keep,
    )


def current_page(page):
    """AI 결과에 사용자의 이미지 지시서 수정을 반영한 페이지(검수·내보내기에 사용)."""
    return with_guides(page, [edited_guide(g) for g in page.image_guides])


def collect_guide_decisions(page) -> dict:
    decisions = {}
    for g in page.image_guides:
        status = ss.get(f"gdec_{g.image_id}", "미검토")
        if status == "수락" and is_edited(g, edited_guide(g)):
            status = "편집"
        decisions[g.image_id] = status
    return decisions


def media_by_id() -> dict:
    return {m.id: m for m in ss.get("media", [])}


def flag_line(f) -> str:
    return f"{SEVERITY_ICON[f.severity]} **[{f.source}]** `{f.expression}` — {f.risk}  \n→ {f.suggestion}"


# ── 사이드바 ─────────────────────────────────────────────────
def sidebar() -> str:
    st.sidebar.title("🌏 상세페이지 현지화 도우미")
    st.sidebar.caption("한국 상품 정보와 기존 상세페이지(이미지·PDF·URL)를 해외 쇼핑몰용 상세페이지 초안으로 바꾸고, 근거와 위험 표현을 함께 검수합니다.")

    mode = "demo"
    get_secret("CLAUDE_MODEL")   # Streamlit secrets 에 적은 모델명을 환경 변수로 옮겨 llm.current_model() 이 읽게 한다
    if get_secret("ANTHROPIC_API_KEY"):
        choice = st.sidebar.radio("실행 모드", ["데모 (저장된 결과)", "실제 AI 호출"])
        if choice == "실제 AI 호출":
            password = get_secret("APP_PASSWORD")
            if password and st.sidebar.text_input("접근 비밀번호", type="password") != password:
                st.sidebar.warning("비밀번호를 입력해야 실제 AI를 사용할 수 있습니다. 지금은 데모 모드입니다.")
            else:
                mode = "live"
    else:
        st.sidebar.info("데모 모드로 실행 중입니다. 미리 만들어 둔 결과를 보여 주므로 비용이 들지 않습니다.")

    if ss.get("active_mode") != mode:      # 모드가 바뀌면 처음부터 다시
        reset_all()
        ss.active_mode = mode

    if mode == "live":
        limit = get_secret("LIVE_CALL_LIMIT", "9")
        st.sidebar.caption(f"모델: `{llm.current_model()}` · AI 호출 {ss.get('live_calls', 0)}/{limit}회")
        costs = [u["cost_usd"] for u in ss.get("usages", []) if u.get("cost_usd") is not None]
        if costs:
            st.sidebar.caption(f"이번 세션 예상 비용: ${sum(costs):.3f}")

    st.sidebar.divider()
    done = {0: "extraction" in ss, 1: "strategy" in ss, 2: "page" in ss, 3: "page" in ss, 4: False}
    for i, name in enumerate(STEPS):
        st.sidebar.markdown(f"{'✅' if done[i] else '⬜'} {name}")

    st.sidebar.divider()
    if st.sidebar.button("처음부터 다시"):
        reset_all()
        st.rerun()
    st.sidebar.caption(
        "데모 상품 '결담(GYEOLDAM)'은 가상 브랜드입니다. 데모 결과는 실제 API 호출 결과가 아니라, "
        "같은 프롬프트 규칙으로 미리 작성해 둔 예시입니다. 위험 표현 검수는 법률 검토를 대신하지 않습니다."
    )
    return mode


# ── ① 상품 정보 입력: 상세페이지 자료 ─────────────────────────
def url_section() -> None:
    st.caption(
        "셀러 본인이 운영하는 상품 페이지 주소를 넣으세요. 자사몰(카페24·아임웹 등)은 대부분 가져올 수 있지만, "
        "쿠팡·네이버 스마트스토어처럼 자동 접속을 막거나 자바스크립트로 화면을 그리는 쇼핑몰은 가져올 수 없습니다."
    )
    c1, c2 = st.columns([5, 1])
    url = c1.text_input("상세페이지 URL", key="url_input", placeholder="https://", label_visibility="collapsed")
    if c2.button("가져오기", disabled=not url.strip()):
        with st.spinner("페이지와 상세 이미지를 가져오는 중입니다…"):
            try:
                page = url_import.import_page(url)
                images, skipped = url_import.fetch_images(page.image_urls)
            except url_import.UrlImportError as e:
                st.error(str(e))
                return
        for key in [k for k in ss.keys() if k.startswith("urlimg_")]:
            del ss[key]
        ss.url_page, ss.url_images, ss.url_skipped = page, images, skipped
        for i in range(len(images)):
            ss[f"urlimg_{i}"] = True

    page = ss.get("url_page")
    if not page:
        return
    st.success(
        f"'{page.title or page.url}'에서 텍스트 {len(page.text):,}자, 상세 이미지 {len(ss.url_images)}장을 가져왔습니다"
        f" (작은 아이콘·중복·실패 {ss.url_skipped}개 제외). 텍스트는 아래 'URL 에서 가져온 페이지 텍스트' 칸에 들어갑니다."
    )
    for warning in page.warnings:
        st.warning(warning)
    if ss.url_images:
        st.caption("분석에 포함할 이미지를 고르세요. 배너·다른 상품 이미지를 빼면 정확도가 오르고 비용이 줄어듭니다.")
        cols = st.columns(4)
        for i, img in enumerate(ss.url_images):
            with cols[i % 4]:
                st.image(img.data, width="stretch")
                st.checkbox(f"포함 ({img.width}×{img.height})", key=f"urlimg_{i}")


def media_section(mode: str) -> None:
    st.subheader("기존 상세페이지 자료 (선택)")
    if mode == "demo":
        st.info(
            "데모에서는 가상 브랜드의 상세페이지 이미지 1장(세로 5,200px)을 씁니다. "
            "글자가 잘리지 않도록 빈 줄을 찾아 여러 조각으로 나눈 뒤 분석합니다."
        )
        st.image(str(pipeline.DEMO_DIR / "detail_page.jpg"), width=220, caption="데모 상세페이지(가상 브랜드)")
        return
    tab_img, tab_pdf, tab_url = st.tabs(["이미지 업로드", "PDF 업로드", "URL로 가져오기"])
    with tab_img:
        st.file_uploader("상품 사진·상세페이지 이미지 (여러 장 가능)", type=["jpg", "jpeg", "png", "webp", "gif"],
                         accept_multiple_files=True, key="up_images")
        st.caption("세로로 긴 상세페이지 이미지도 그대로 올리면 됩니다. 글자 사이 빈 줄에서 자동으로 나눕니다.")
    with tab_pdf:
        st.file_uploader(f"상세페이지 PDF ({media.MAX_PDF_PAGES}쪽 이하)", type=["pdf"], key="up_pdf")
        st.caption("URL로 가져올 수 없는 쇼핑몰은 브라우저에서 Ctrl+P → 'PDF로 저장'으로 만든 파일을 올리세요.")
    with tab_url:
        url_section()


def collect_media(mode: str) -> list | None:
    """업로드·PDF·URL 이미지를 모아 조각낸다. 오류가 있으면 화면에 알리고 None."""
    if mode == "demo":
        return pipeline.load_demo_media()
    try:
        sources = [(f.name, media.open_image(f.getvalue())) for f in ss.get("up_images") or []]
        pdf = ss.get("up_pdf")
        if pdf:
            sources += [(f"{pdf.name} {n}쪽", img) for n, img in enumerate(media.pdf_to_images(pdf.getvalue()), 1)]
        sources += [(f"URL 이미지 {i + 1}", media.open_image(img.data))
                    for i, img in enumerate(ss.get("url_images", [])) if ss.get(f"urlimg_{i}")]
        return media.build_media(sources)
    except media.MediaError as e:
        st.error(str(e))
        return None


# ── ① 상품 정보 입력 ─────────────────────────────────────────
def step_input(mode: str) -> None:
    st.header(STEPS[0])
    demo = mode == "demo"
    if demo:
        st.info("데모 모드에서는 예시 상품으로 전체 흐름을 체험할 수 있습니다. 입력 칸은 수정할 수 없습니다.")
    base = ss.get("product") or pipeline.load_demo_input()
    if ss.get("url_page") and not demo:
        base = base.model_copy(update={"page_text": ss.url_page.text, "source_url": ss.url_page.url})

    c1, c2, c3 = st.columns(3)
    countries = list(CFG["countries"])
    country = c1.selectbox(
        "판매 대상 국가 *", countries, index=countries.index(base.country),
        format_func=lambda k: CFG["countries"][k]["name"], disabled=demo,
    )
    markets = list(marketplaces_for(country))
    marketplace = c2.selectbox(
        "판매할 쇼핑몰 *", markets, index=markets.index(base.marketplace) if base.marketplace in markets else 0,
        format_func=lambda k: CFG["marketplaces"][k]["name"], disabled=demo,
    )
    categories = list(CFG["categories"])
    category = c3.selectbox(
        "상품 카테고리 *", categories, index=categories.index(base.category),
        format_func=lambda k: CFG["categories"][k]["name"], disabled=demo,
    )

    media_section(mode)

    with st.form("input_form"):
        values = {}
        st.markdown("**필수 정보**")
        for field in REQUIRED:
            widget = st.text_area if field in LONG_FIELDS else st.text_input
            values[field] = widget(f"{FIELD_LABELS[field]} *", getattr(base, field), disabled=demo)
        with st.expander("선택 정보 (입력할수록 결과가 정확해집니다)", expanded=demo):
            for field in OPTIONAL:
                widget = st.text_area if field in LONG_FIELDS else st.text_input
                values[field] = widget(FIELD_LABELS[field], getattr(base, field), disabled=demo)
        submitted = st.form_submit_button("상품 정보 분석하기", type="primary")

    if not submitted:
        return
    missing = [FIELD_LABELS[f] for f in REQUIRED if not values[f].strip()]
    if missing:
        st.error("필수 정보를 입력하세요: " + ", ".join(missing))
        return
    with st.spinner("이미지를 준비하는 중입니다…"):
        items = collect_media(mode)
    if items is None:
        return
    product = ProductInput(country=country, marketplace=marketplace, category=category,
                           source_url=base.source_url, **values)
    reset_from("extraction")
    ss.product, ss.media = product, items
    ss.started_at = time.time()
    label = f"상품 정보와 이미지 {len(items)}조각을 분석하는 중입니다…" if items else "상품 정보를 분석하는 중입니다…"
    result = run_ai(label, pipeline.extract, product, items, mode=mode)
    if result:
        ss.extraction = result


# ── ② 추출 결과 확인 ─────────────────────────────────────────
def step_extraction(mode: str) -> None:
    ex = ss.extraction
    st.header(STEPS[1])
    st.write(ex.product_summary)

    st.subheader("확인된 사실")
    st.caption("입력에서 직접 확인된 내용만 담았습니다. ⚠ 표시는 증빙 자료가 없어 상세페이지 근거로 쓰지 않는 주장입니다.")
    st.dataframe(
        [{"ID": f.id, "유형": f.type, "내용": f.content, "출처": f.source,
          "증빙": "⚠ 증빙 필요" if f.needs_evidence else ""} for f in ex.facts],
        hide_index=True, width="stretch",
    )

    if ex.images:
        thumbs = media_by_id()
        tokens = sum(m.est_tokens for m in ss.get("media", []))
        with st.expander(f"이미지 분석 ({len(ex.images)}조각 · 이미지 입력 약 {tokens:,} 토큰)", expanded=True):
            for a in ex.images:
                c1, c2 = st.columns([1, 3])
                if a.image_id in thumbs:
                    c1.image(thumbs[a.image_id].jpeg, caption=f"{a.image_id} · {thumbs[a.image_id].label}")
                c2.markdown(f"**{a.image_id} · {a.role}** — {a.key_message}")
                c2.markdown("**이미지 속 문구** " + " / ".join(f"`{t}`" for t in a.texts_ko))
                c2.caption(f"시각 요소: {a.visual_elements}")
                for issue in a.issues:
                    c2.markdown(f"⚠ {issue}")
                st.divider()

    st.subheader(f"추가로 확인이 필요한 정보 ({len(ex.questions)}건)")
    for q in ex.questions:
        st.markdown(f"{SEVERITY_ICON[q.priority]} **{q.id}. {q.question}**  \n{q.reason}")

    with st.expander("질문에 답하고 다시 분석하기"):
        extra = st.text_area("추가 정보", placeholder="예: Q1 전성분 — 정제수, 어성초추출물, …", disabled=mode == "demo")
        if mode == "demo":
            st.caption("데모 모드에서는 사용할 수 없습니다.")
        if st.button("보완해서 다시 분석", disabled=mode == "demo" or not extra.strip()):
            product = ss.product.model_copy(update={"specs": f"{ss.product.specs}\n[추가 정보]\n{extra}"})
            reset_from("extraction")
            ss.product = product
            result = run_ai("보완한 정보로 다시 분석하는 중입니다…", pipeline.extract, product, ss.get("media", []), mode=mode)
            if result:
                ss.extraction = result
                st.rerun()

    if st.button("마케팅 전략 추천 받기", type="primary"):
        reset_from("strategy")
        result = run_ai("전략을 추천하는 중입니다…", pipeline.recommend, ss.product, ex, mode=mode)
        if result:
            ss.strategy = result


# ── ③ 마케팅 전략 선택 ───────────────────────────────────────
def step_strategy(mode: str) -> None:
    sr, product, ex = ss.strategy, ss.product, ss.extraction
    st.header(STEPS[2])
    st.markdown(f"**포지셔닝 가설** — {sr.positioning}")

    with st.expander("이 추천에 사용된 국가·쇼핑몰 설정 (가설과 미확인 규칙 포함)"):
        country = CFG["countries"][product.country]
        market = CFG["marketplaces"][product.marketplace]
        st.markdown(f"**{country['name']} 소비자 가설**")
        for h in country["hypotheses"] + market["buyer_context"]:
            st.markdown(f"- {h['text']} {'' if h['verified'] else '`미검증 가설`'}")
        st.markdown(f"**{market['name']} 등록 형식**")
        for r in market["format_rules"]:
            st.markdown(f"- {r['rule']}: {r['value']} {'`확인됨`' if r['verified'] else '`미확인`'} · {r['source']}")
        st.caption(f"설정 검토일: {market['reviewed_at']}")

    facts = {f.id: f for f in ex.facts}
    chosen = []
    for s in sorted(sr.strategies, key=lambda s: s.priority):
        name = CFG["strategies"].get(s.strategy_id, {}).get("name", s.strategy_id)
        with st.container(border=True):
            if st.checkbox(f"**{s.priority}. {s.title}** · {name}", value=True, key=f"strat_{s.strategy_id}"):
                chosen.append(s)
            st.markdown(f"**추천 이유** — {s.reason}")
            evidence = [f"`{fid}` {facts[fid].content}" if fid in facts else f"`{fid}` ⚠ 존재하지 않는 ID"
                        for fid in s.evidence_fact_ids]
            st.markdown("**근거 사실**  \n" + "  \n".join(evidence))
            c1, c2, c3, c4 = st.columns(4)
            c1.markdown("**적용 영역**  \n" + ", ".join(s.target_sections))
            c2.markdown(f"**예상 효과**  \n{s.expected_effect}")
            c3.markdown("**검증 지표**  \n" + ", ".join(s.metrics))
            c4.markdown("**추가 확인**  \n" + (", ".join(s.to_confirm) or "없음"))
    if sr.caveats:
        st.warning("**추천의 한계**\n\n" + "\n".join(f"- {c}" for c in sr.caveats))

    if mode == "demo":
        st.caption("데모 모드에서는 전략 선택과 관계없이 저장된 결과를 보여 줍니다.")
    if st.button("현지화 상세페이지 생성", type="primary", disabled=not chosen):
        reset_from("page")
        ss.chosen_ids = [s.strategy_id for s in chosen]
        result = run_ai("상세페이지와 이미지 수정 지시서를 만드는 중입니다… (1~3분 걸릴 수 있습니다)",
                        pipeline.generate, product, ex, chosen, mode=mode)
        if result:
            ss.page = result


# ── ④ 생성 결과 검수 ─────────────────────────────────────────
LAYOUTS = ["편집 + 미리보기", "편집만", "미리보기만"]
PANEL_HEIGHT = 860          # 나란히 보기에서 편집 칸과 미리보기 칸의 높이(각각 따로 스크롤)


def text_height(text: str) -> int:
    """문구 길이에 맞춘 입력칸 높이. 원본 문구 기준이라 편집 중에 칸 크기가 흔들리지 않는다."""
    lines = sum(max(1, len(line) // 48 + 1) for line in (text.splitlines() or [""]))
    return min(240, max(68, 30 + 24 * lines))


def item_card(item, facts: dict, flags: list) -> None:
    with st.container(border=True):
        st.markdown(f"**{item.id}** · {'🧪 AI 제안 (사실 아님)' if item.is_ai_suggestion else '📌 사실 기반'}")
        st.text_area(f"{item.id} 현지화 문구", key=f"txt_{item.id}", height=text_height(item.localized),
                     label_visibility="collapsed")
        st.caption(f"역번역: {item.back_translation_ko}")
        for f in flags:
            st.markdown(flag_line(f))
        st.radio("결정", ["미검토", "수락", "거절"], key=f"dec_{item.id}", horizontal=True,
                 label_visibility="collapsed")
        with st.expander("원본 · 수정 이유 · 근거 · 대안"):
            st.markdown(f"**원본 (한국어)**  \n{item.original_ko or '— 새로 추가한 항목'}")
            st.markdown(f"**수정 이유** — {item.reason}")
            if item.fact_ids:
                st.markdown("**근거**  \n" + "  \n".join(
                    f"`{fid}` {facts[fid].content}" if fid in facts else f"`{fid}` ⚠ 존재하지 않는 ID" for fid in item.fact_ids
                ))
            if item.variants:
                st.markdown("**테스트용 대안**  \n" + "  \n".join(f"- {v}" for v in item.variants))


def guide_card(g, thumbs: dict, flags: list) -> None:
    """g 는 AI 원본 지시서. 표 편집기에는 원본을 넘기고, 변경분은 Streamlit 이 따로 기억한다."""
    ss.setdefault(f"ginst_{g.image_id}", "\n".join(g.edit_instructions))
    ss.setdefault(f"gkeep_{g.image_id}", "\n".join(g.keep_unchanged))
    with st.container(border=True):
        c1, c2 = st.columns([1, 3])
        if g.image_id in thumbs:
            c1.image(thumbs[g.image_id].jpeg, caption=f"{g.image_id} (현재)")
        else:
            c1.markdown(f"**{g.image_id}**  \n🆕 새로 만들 이미지")
        order = "삭제 권장" if g.action == "삭제" else f"{g.recommended_order}번째"
        c2.markdown(f"**{g.image_id} · {g.action}** ({order})")
        c2.caption(g.reason)
        if is_edited(g, edited_guide(g)):
            c2.caption("✏️ 직접 수정됨 — 고친 행의 역번역은 결과 파일에 '역번역 없음'으로 표시됩니다.")
        c2.radio("결정", ["미검토", "수락", "거절"], key=f"gdec_{g.image_id}", horizontal=True,
                 label_visibility="collapsed")
        for f in flags:
            st.markdown(flag_line(f))
        with st.expander(f"지시서 편집 — 바꿀 문구 {len(g.text_replacements)}건 · 수정 지시 · 보존 요소"):
            st.data_editor(
                table_rows(g), key=f"gedit_{g.image_id}", num_rows="dynamic", hide_index=True, width="stretch",
                column_config={
                    COL_ORIGINAL: st.column_config.TextColumn(COL_ORIGINAL, help="AI가 이미지 글자를 잘못 읽었다면 고치세요. 새 문구면 빈 칸"),
                    COL_LOCALIZED: st.column_config.TextColumn(COL_LOCALIZED, help="빈 칸이면 이미지에서 이 문구를 지운다는 뜻"),
                    COL_BACK: st.column_config.TextColumn(COL_BACK, disabled=True),
                },
            )
            st.caption("칸을 눌러 바로 고칠 수 있습니다. '바꿀 문구'가 빈 칸이면 삭제, 표 맨 아래 빈 행에서 새 문구를 추가합니다.")
            left, right = st.columns(2)
            left.text_area("수정 지시 (한 줄에 하나)", key=f"ginst_{g.image_id}", height=140)
            right.text_area("🔒 바꾸면 안 되는 요소 (한 줄에 하나)", key=f"gkeep_{g.image_id}", height=140)


def preview_panel(page, product, decisions: dict, guide_decisions: dict) -> None:
    st.caption(
        "현지 고객이 보게 될 모양입니다. 거절한 항목은 빠지고, 점선 테두리는 미검토, 초록 테두리는 직접 수정한 항목입니다. "
        "이미지는 아직 원본이며, 그 아래 표가 이미지에서 바꿀 문구입니다."
    )
    st.html(render_preview(product, page, decisions, guide_decisions,
                           {m.id: m.jpeg for m in ss.get("media", [])}, include_unreviewed=True))


def step_review() -> None:
    page, product, ex = ss.page, ss.product, ss.extraction
    st.header(STEPS[3])
    st.caption(
        "문구를 고치거나 수락·거절을 고르면 미리보기에 바로 반영됩니다. "
        "직접 고친 뒤 수락하면 '직접 수정'으로 기록되고, 위험 표현·숫자 검사도 고친 내용으로 다시 실행됩니다."
    )

    for item in page.items:      # 위젯 기본값을 먼저 넣어 둔다
        ss.setdefault(f"txt_{item.id}", item.localized)
        ss.setdefault(f"dec_{item.id}", "미검토")
    for g in page.image_guides:
        ss.setdefault(f"gdec_{g.image_id}", "미검토")

    decisions, guide_decisions = collect_decisions(page), collect_guide_decisions(page)
    edited_page = current_page(page)
    rules = risk_rules_for(product.country, product.category)
    flags = full_review(edited_page, ex, rules, product, final_texts(page, decisions))
    by_item: dict[str, list] = {}
    for f in flags:
        by_item.setdefault(f.item_id, []).append(f)
    original_flags = [f for f in flags if f.item_id.startswith(ORIGINAL_ITEM_ID)]

    statuses = [d["status"] for d in decisions.values()] + list(guide_decisions.values())
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("문구 · 이미지 지시서", f"{len(page.items)} · {len(page.image_guides)}")
    c2.metric("미검토", statuses.count("미검토"))
    c3.metric("결과물 위험 표시", len(flags) - len(original_flags))
    c4.metric("원본의 위험 표현", len(original_flags))
    c5.metric("AI 제안(사실 아님)", sum(i.is_ai_suggestion for i in page.items))

    if original_flags:
        with st.expander(f"원본(텍스트·이미지)에서 발견된 위험 표현 ({len(original_flags)}건)"):
            for f in original_flags:
                st.markdown(f"**{f.item_id}** · " + flag_line(f))
    if page.missing_info:
        with st.expander(f"사용하지 않은 주장과 빠진 정보 ({len(page.missing_info)}건)"):
            st.markdown("\n".join(f"- {m}" for m in page.missing_info))

    left, right = st.columns([3, 2])
    layout = left.segmented_control("보기 방식", LAYOUTS, default=LAYOUTS[0], key="review_layout") or LAYOUTS[0]
    if right.button("검토하지 않은 항목 모두 수락"):
        for key in [k for k in ss.keys() if k.startswith(("dec_", "gdec_"))]:
            if ss[key] == "미검토":
                ss[key] = "수락"
        st.rerun()

    if layout == LAYOUTS[0]:
        edit_col, prev_col = st.columns([1.1, 0.9], gap="medium")
        edit_box, prev_box = edit_col.container(height=PANEL_HEIGHT), prev_col.container(height=PANEL_HEIGHT)
    elif layout == LAYOUTS[1]:
        edit_box, prev_box = st.container(), None
    else:
        edit_box, prev_box = None, st.container()

    if edit_box is not None:
        with edit_box:
            facts = {f.id: f for f in ex.facts}
            section = None
            for item in page.items:
                if item.section != section:
                    section = item.section
                    st.subheader(section)
                item_card(item, facts, by_item.get(item.id, []))
            if page.image_guides:
                st.subheader("이미지 수정 지시서")
                st.caption(
                    "디자이너나 이미지 편집 도구에 넘길 지시서입니다. 추천 순서대로 정렬했습니다. "
                    "'바꾸면 안 되는 요소'는 이미지 편집 중 제품 외형·인증 표시·수치가 왜곡되지 않았는지 확인하는 목록입니다."
                )
                thumbs = media_by_id()
                for g in sorted(page.image_guides, key=lambda g: (g.recommended_order == 0, g.recommended_order)):
                    guide_card(g, thumbs, by_item.get(g.image_id, []))
    if prev_box is not None:
        with prev_box:
            preview_panel(edited_page, product, decisions, guide_decisions)


# ── ⑤ 내보내기 ──────────────────────────────────────────────
def step_export() -> None:
    page, product, ex, sr = ss.page, ss.product, ss.extraction, ss.strategy
    st.header(STEPS[4])
    decisions = collect_decisions(page)
    guide_decisions = collect_guide_decisions(page)
    page = current_page(page)            # 이후 결과물에는 사용자가 고친 이미지 지시서가 들어간다
    rules = risk_rules_for(product.country, product.category)
    flags = full_review(page, ex, rules, product, final_texts(page, decisions))
    elapsed = time.time() - ss.started_at if "started_at" in ss else None
    metrics = compute_metrics(page, decisions, guide_decisions, sr, ss.get("chosen_ids", []), elapsed,
                              ss.get("usages", []))

    accepted = any(status in INCLUDED for status in [d["status"] for d in decisions.values()] + list(guide_decisions.values()))
    if not accepted:
        st.info("수락한 항목이 아직 없습니다. ④에서 문구나 이미지 지시서를 수락하면 여기에서 내보낼 수 있습니다.")
        return
    unreviewed = metrics["미검토"] + list(guide_decisions.values()).count("미검토")
    if unreviewed:
        st.warning(f"아직 검토하지 않은 항목이 {unreviewed}개 있습니다. 미검토 항목은 결과에 포함되지 않습니다.")

    md = to_markdown(product, page, decisions, guide_decisions, flags, ex, metrics)
    labels = {m.id: m.label for m in ss.get("media", [])}
    js = to_json(product, ex, sr, ss.get("chosen_ids", []), page, decisions, guide_decisions, flags, metrics, labels)
    stem = re.sub(r'[\\/:*?"<>|\s]+', "_", product.name)
    c1, c2 = st.columns(2)
    c1.download_button("Markdown 파일 다운로드", md, f"{stem}_{page.language}.md", "text/markdown", type="primary")
    c2.download_button("JSON 파일 다운로드", js, f"{stem}_{page.language}.json", "application/json")
    st.caption("업로드한 이미지 파일 자체는 저장하지 않습니다.")
    with st.expander("최종 미리보기 (수락·직접 수정한 항목만)", expanded=True):
        st.html(render_preview(product, page, decisions, guide_decisions,
                               {m.id: m.jpeg for m in ss.get("media", [])}, include_unreviewed=False))
    with st.expander("Markdown 원문 (오른쪽 위 복사 버튼으로 전체 복사)"):
        st.code(md, language="markdown")

    st.subheader("작업 지표")
    st.dataframe([{"지표": k, "값": v} for k, v in metrics.items()], hide_index=True)


# ── 화면 구성 ────────────────────────────────────────────────
mode = sidebar()
step_input(mode)
if "extraction" in ss:
    st.divider()
    step_extraction(mode)
if "strategy" in ss:
    st.divider()
    step_strategy(mode)
if "page" in ss:
    st.divider()
    step_review()
    st.divider()
    step_export()
