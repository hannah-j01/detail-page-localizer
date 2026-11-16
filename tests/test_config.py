"""설정 파일(국가·쇼핑몰·전략·위험 표현·단위)이 서로 맞고 코드가 기대하는 형식인지 확인한다."""
import re
from typing import get_args

from core import pipeline
from core.config_loader import context_for, load_all, marketplaces_for, risk_rules_for
from core.numbers import Quantity, quantities
from core.review import rule_review
from core.schemas import Extraction, GeneratedPage, PageItem, ProductInput, Section

CFG = load_all()
EMPTY = Extraction(product_summary="", facts=[], questions=[], images=[])


def test_every_country_has_a_marketplace_and_language():
    for cid, country in CFG["countries"].items():
        assert marketplaces_for(cid), f"{cid} 에 쇼핑몰이 없다"
        assert country["language"] and country["language_name"] and country["writing_style"]
        assert all({"text", "verified"} <= set(h) for h in country["hypotheses"])


def test_marketplace_rules_have_required_keys():
    for mid, market in CFG["marketplaces"].items():
        for rule in market["format_rules"]:
            assert {"rule", "value", "verified", "source"} <= set(rule), f"{mid}: {rule}"
        assert market["reviewed_at"]


def test_strategy_sections_match_page_sections():
    valid = set(get_args(Section))
    for sid, strategy in CFG["strategies"].items():
        assert set(strategy["sections"]) <= valid, f"{sid}: {set(strategy['sections']) - valid}"
        assert strategy["required_evidence"] and strategy["risks"] and strategy["metrics"]


def test_risk_rule_patterns_compile_and_have_severity():
    for rid, ruleset in CFG["risk_rules"].items():
        assert ruleset["references"]
        for rule in ruleset["rules"]:
            re.compile(rule["pattern"])
            assert rule["severity"] in ("높음", "중간", "낮음"), f"{rid}: {rule['pattern']}"
            assert rule["reason"] and rule["suggestion"]


def test_prompts_render_for_every_country_marketplace_category(monkeypatch):
    """모든 국가·쇼핑몰·카테고리 조합으로 세 단계 프롬프트가 빈 자리({{ }}) 없이 만들어진다."""
    captured = []
    monkeypatch.setattr(pipeline.llm, "call_structured",
                        lambda system, content, model: captured.append(content) or (None, None))
    base = pipeline.load_demo_input()
    extraction, _ = pipeline.extract(base, [], "demo")
    strategy, _ = pipeline.recommend(base, extraction, "demo")
    for cid in CFG["countries"]:
        for mid in marketplaces_for(cid):
            for cat in CFG["categories"]:
                product = base.model_copy(update={
                    "country": cid, "marketplace": mid, "category": cat,
                    "competitors": "A사 토너 150ml 1,500엔", "sales_data": "클릭률 2.1%",
                })
                ctx = context_for(cid, mid, cat)
                assert ctx["country"]["id"] == cid and ctx["marketplace"]["country"] == cid
                pipeline.extract(product, [], "live")
                pipeline.recommend(product, extraction, "live")
                pipeline.generate(product, extraction, strategy.strategies, "live")
    assert captured and all("{{" not in c for c in captured)
    assert any("경쟁 상품 정보\nA사 토너" in c for c in captured)          # 새 입력 칸이 프롬프트에 들어간다
    assert any("国家级" in c for c in captured)                           # 중국 규칙이 생성 프롬프트에 들어간다


def _page(text: str, language: str) -> GeneratedPage:
    item = PageItem(id="S1", section="상품 요약", original_ko="", localized=text, back_translation_ko="",
                    reason="", fact_ids=[], strategy_ids=[], is_ai_suggestion=True, variants=[])
    return GeneratedPage(language=language, items=[item], image_guides=[], risk_flags=[], missing_info=[])


def test_chinese_rules_flag_medical_and_superlative_terms():
    product = pipeline.load_demo_input().model_copy(update={"country": "cn", "marketplace": "tmall_global"})
    rules = risk_rules_for("cn", "skincare")
    assert all("일본" not in r["ruleset"] and "미국" not in r["ruleset"] for r in rules)
    page = _page("韩国药妆品牌，7天见效，全网销量第一的最佳爽肤水，美白祛痘", "zh")
    found = {f.expression for f in rule_review(page, rules, product, None, EMPTY) if f.item_id == "S1"}
    assert {"药妆", "7天见效", "销量第一", "最佳", "美白", "祛痘"} <= found
    clean = _page("鱼腥草提取物77%，弱酸性pH5.5，不含香精，温和舒缓", "zh")
    assert [f for f in rule_review(clean, rules, product, None, EMPTY) if f.item_id == "S1"] == []


def test_chinese_units_match_korean_and_japanese():
    assert quantities("200毫升") == quantities("200ml") == {Quantity("200", "ml")}
    assert quantities("36个月") == quantities("36개월") == {Quantity("36", "month")}
    assert quantities("7天") == quantities("7日") == {Quantity("7", "day")}
    assert quantities("3瓶装") == {Quantity("3", "bottle")}
    assert quantities("售价99元") == {Quantity("99", "cny")}
    assert quantities("满意度提升30%") == {Quantity("30", "percent")}   # '升' 이 단위로 잡히지 않는다


def test_new_optional_fields_default_empty():
    product = ProductInput(**pipeline.load_demo_input().model_dump())
    assert product.competitors == "" and product.sales_data == ""
    assert {"competitors", "sales_data"} <= set(product.model_dump())
