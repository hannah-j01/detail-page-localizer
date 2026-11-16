"""상품 하나를 처리하는 3단계 AI 흐름: 추출(텍스트+이미지) → 전략 추천 → 상세페이지 생성.

mode="demo" 이면 demo/ 폴더에 저장된 결과를 불러온다(API 비용 0원).
mode="live" 이면 Claude API 를 호출한다.
"""
import json
from pathlib import Path

import yaml

from core import llm
from core.config_loader import context_for, load_all
from core.media import MediaItem, build_media, open_image
from core.schemas import (
    FIELD_LABELS, Extraction, GeneratedPage, ProductInput, ReviewFlag, Revision, Strategy, StrategyResult,
)

ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = ROOT / "prompts"
DEMO_DIR = ROOT / "demo"


# ── 공통 도구 ────────────────────────────────────────────────
def _dump(obj) -> str:
    return yaml.safe_dump(obj, allow_unicode=True, sort_keys=False)


def _render(name: str, **values: str) -> str:
    text = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def _system() -> str:
    return (PROMPTS_DIR / "system.md").read_text(encoding="utf-8")


def _product_text(product: ProductInput) -> str:
    cfg = load_all()
    names = {
        "category": cfg["categories"][product.category]["name"],
        "country": cfg["countries"][product.country]["name"],
        "marketplace": cfg["marketplaces"][product.marketplace]["name"],
    }
    lines = []
    for field, label in FIELD_LABELS.items():
        value = names.get(field) or getattr(product, field).strip() or "(입력 없음)"
        lines.append(f"### {label}\n{value}")
    return "\n\n".join(lines)


def _context_text(product: ProductInput) -> str:
    ctx = context_for(product.country, product.marketplace, product.category)
    return _dump({k: ctx[k] for k in ("country", "marketplace", "category")})


def load_demo(name: str, model: type):
    return model.model_validate_json((DEMO_DIR / f"{name}.json").read_text(encoding="utf-8"))


def load_demo_input() -> ProductInput:
    return load_demo("sample_input", ProductInput)


def load_demo_media() -> list[MediaItem]:
    """데모용 상세페이지 이미지(가상 브랜드)를 실제 업로드와 같은 방식으로 조각낸다."""
    data = (DEMO_DIR / "detail_page.jpg").read_bytes()
    return build_media([("데모 상세페이지.jpg", open_image(data))])


# ── 단계 ① 상품 정보 추출 ────────────────────────────────────
def extract(product: ProductInput, media: list[MediaItem], mode: str) -> tuple[Extraction, dict | None]:
    if mode == "demo":
        return load_demo("extract", Extraction), None
    category = load_all()["categories"][product.category]
    prompt = _render("extract", category=_dump(category), product=_product_text(product))
    if not media:
        return llm.call_structured(_system(), prompt, Extraction)
    content = []
    for item in media:
        content.append({"type": "text", "text": f"[이미지 {item.id}] 출처: {item.label}"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": item.b64()}})
    content.append({"type": "text", "text": prompt})
    return llm.call_structured(_system(), content, Extraction)


# ── 단계 ② 마케팅 전략 추천 ──────────────────────────────────
def recommend(product: ProductInput, extraction: Extraction, mode: str) -> tuple[StrategyResult, dict | None]:
    if mode == "demo":
        return load_demo("strategy", StrategyResult), None
    prompt = _render(
        "strategy",
        context=_context_text(product),
        strategies=_dump(list(load_all()["strategies"].values())),
        product=_product_text(product),
        facts=_dump([f.model_dump() for f in extraction.facts]),
    )
    return llm.call_structured(_system(), prompt, StrategyResult)


# ── 단계 ③ 현지화 상세페이지 생성 ─────────────────────────────
def generate(
    product: ProductInput, extraction: Extraction, chosen: list[Strategy], mode: str,
) -> tuple[GeneratedPage, dict | None]:
    if mode == "demo":
        return load_demo("generate", GeneratedPage), None
    ctx = context_for(product.country, product.marketplace, product.category)
    strategy_configs = load_all()["strategies"]
    chosen_text = _dump([
        {**s.model_dump(), "config": strategy_configs.get(s.strategy_id, {})} for s in chosen
    ])
    rules = [r for r in ctx["risk_rules"] if r["target"] == "localized"]
    prompt = _render(
        "generate",
        language=ctx["country"]["language"],
        language_name=ctx["country"]["language_name"],
        context=_context_text(product),
        risk_rules=_dump(rules),
        strategies=chosen_text,
        product=_product_text(product),
        facts=_dump([f.model_dump() for f in extraction.facts]),
        # 이미지는 다시 보내지 않고 ①의 분석 결과(텍스트)만 넘긴다 → 이미지 토큰 비용을 한 번만 낸다
        images=_dump([i.model_dump() for i in extraction.images]) if extraction.images else "(이미지 없음)",
    )
    return llm.call_structured(_system(), prompt, GeneratedPage)


# ── 단계 ③-1 현지어 다듬기: 현지 카피라이터 시각으로 전체 문구를 손질 ──
def polish(product: ProductInput, extraction: Extraction, page: GeneratedPage) -> tuple[Revision, dict | None]:
    """번역투·경어·쇼핑몰 관행을 고친다. 사실·숫자는 바꾸지 않게 하고, 결과는 다시 코드 검수를 거친다(core/refine.py)."""
    ctx = context_for(product.country, product.marketplace, product.category)
    rules = [r for r in ctx["risk_rules"] if r["target"] == "localized"]
    guides = [g.model_dump() for g in page.image_guides if g.text_replacements]
    prompt = _render(
        "polish",
        marketplace_name=ctx["marketplace"]["name"],
        language_name=ctx["country"]["language_name"],
        items=_dump([i.model_dump() for i in page.items]),
        image_guides=_dump(guides) if guides else "(없음)",
        context=_context_text(product),
        risk_rules=_dump(rules),
        product=_product_text(product),
        facts=_dump([f.model_dump() for f in extraction.facts]),
    )
    return llm.call_structured(_system(), prompt, Revision)


# ── 단계 ③-2 자기검수: 검수에 걸린 항목만 다시 쓰기 ─────────────
def revise(
    product: ProductInput, extraction: Extraction, page: GeneratedPage, flags: list[ReviewFlag],
) -> tuple[Revision, dict | None]:
    """검수에 걸린 문구·이미지 지시서와 그 표시만 보내 다시 쓰게 한다(이미지는 보내지 않는다).

    생성 호출보다 훨씬 짧은 요청이다. 데모 모드에서는 호출하지 않는다(core/refine.py 가 건너뛴다).
    """
    ctx = context_for(product.country, product.marketplace, product.category)
    by_id: dict[str, list[dict]] = {}
    for f in flags:
        by_id.setdefault(f.item_id, []).append(f.model_dump())
    items = [{**i.model_dump(), "검수_표시": by_id[i.id]} for i in page.items if i.id in by_id]
    guides = [{**g.model_dump(), "검수_표시": by_id[g.image_id]} for g in page.image_guides if g.image_id in by_id]
    rules = [r for r in ctx["risk_rules"] if r["target"] == "localized"]
    prompt = _render(
        "revise",
        language_name=ctx["country"]["language_name"],
        items=_dump(items) if items else "(없음)",
        image_guides=_dump(guides) if guides else "(없음)",
        context=_context_text(product),
        risk_rules=_dump(rules),
        facts=_dump([f.model_dump() for f in extraction.facts]),
    )
    return llm.call_structured(_system(), prompt, Revision)


def save_as_demo(name: str, data) -> None:
    """실제 API 결과를 데모용 파일로 저장할 때 쓴다(개발용)."""
    (DEMO_DIR / f"{name}.json").write_text(
        json.dumps(data.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8",
    )
