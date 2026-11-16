"""글자 수 한도(형식 검수)와 자기검수의 처리. API 는 부르지 않는다."""
from typing import get_args

import pytest

from core import pipeline, refine
from core.config_loader import load_all, risk_rules_for
from core.review import UNVERIFIED_TAG, format_limits, format_review, full_review, text_length
from core.schemas import GeneratedPage, PageItem, Revision, Section

CFG = load_all()


def page_with_title(title: str, variants=(), language="en") -> GeneratedPage:
    item = PageItem(id="S1", section="상품명", original_ko="", localized=title, back_translation_ko="",
                    reason="", fact_ids=["F1"], strategy_ids=[], is_ai_suggestion=False, variants=list(variants))
    return GeneratedPage(language=language, items=[item], image_guides=[], risk_flags=[], missing_info=[])


def test_limit_config_is_well_formed():
    for mid, market in CFG["marketplaces"].items():
        for lim in format_limits(market):
            assert lim["section"] in get_args(Section), f"{mid}: {lim}"
            assert isinstance(lim["max_chars"], int) and lim["max_chars"] > 0
            assert lim.get("unit", "chars") in ("chars", "fullwidth")
    assert any(l["section"] == "상품명" for l in format_limits(CFG["marketplaces"]["amazon_us"]))
    assert format_limits(CFG["marketplaces"]["qoo10_jp"]) == []          # 한도를 모르는 쇼핑몰은 검사하지 않는다


def test_text_length_units():
    assert text_length("  abc def ") == 7
    assert text_length("abc def", counts_spaces=False) == 6
    assert text_length("ドクダミ77", unit="fullwidth") == 4 + 1            # 전각 4 + 반각 2×0.5
    assert text_length("é") == 1                                           # 결합 문자는 NFC 로 합쳐 센다


def test_title_over_unverified_limit_is_low_severity_warning():
    amazon = CFG["marketplaces"]["amazon_us"]
    long_title = "GYEOLDAM Heartleaf 77% Calming Toner for Sensitive Skin, Fragrance Free, pH 5.5, 200ml / 6.76 fl oz"
    assert text_length(long_title) > 75
    flags = format_review(page_with_title(long_title), amazon)
    assert len(flags) == 1
    f = flags[0]
    assert f.source == "형식" and f.severity == "낮음" and f.risk.startswith(UNVERIFIED_TAG)
    assert f.expression.endswith("/ 한도 75자") and "verified" in f.suggestion
    assert format_review(page_with_title("GYEOLDAM Heartleaf 77% Calming Toner 200ml"), amazon) == []


def test_variants_are_measured_separately():
    amazon = CFG["marketplaces"]["amazon_us"]
    short, long = "GYEOLDAM Toner 200ml", "x" * 76
    flags = format_review(page_with_title(short, variants=[short, long]), amazon)
    assert [f.expression.split(" ")[0] for f in flags] == ["대안2"]


def test_verified_limit_is_high_severity_and_fed_back_to_ai(monkeypatch):
    market = {"name": "테스트몰", "format_rules": [
        {"rule": "상품명 글자 수 제한", "value": "20자", "verified": True, "source": "공식",
         "limit": {"section": "상품명", "max_chars": 20}}]}
    flags = format_review(page_with_title("x" * 21), market)
    assert flags[0].severity == "높음" and "확인됨" in flags[0].risk and refine.fixable(flags[0])
    unverified = format_review(page_with_title("x" * 76), CFG["marketplaces"]["amazon_us"])[0]
    assert not refine.fixable(unverified)


def test_unverified_limit_is_not_sent_to_self_review_but_blocks_auto_accept(monkeypatch):
    product = pipeline.load_demo_input().model_copy(update={"country": "us", "marketplace": "amazon_us"})
    extraction, _ = pipeline.extract(product, [], "demo")
    strategy, _ = pipeline.recommend(product, extraction, "demo")
    base, _ = pipeline.generate(product, extraction, strategy.strategies, "demo")
    long_title = "GYEOLDAM Heartleaf 77% Calming Toner for Sensitive Skin, Fragrance Free, pH 5.5, 200ml / 6.76 fl oz"
    page = base.model_copy(update={
        "items": [i.model_copy(update={"localized": long_title, "variants": []}) if i.id == "S1" else i for i in base.items],
        "risk_flags": [f for f in base.risk_flags if f.item_id != "S1"],
    })
    sent = []

    def fake_call(system, content, model):
        if model is Revision:
            sent.append(content)
            return Revision(items=[], image_guides=[], remaining_flags=[], notes=[]), None
        return page, None

    monkeypatch.setattr(pipeline.llm, "call_structured", fake_call)
    monkeypatch.setenv("POLISH_PASS", "0")
    result, _ = refine.generate_with_review(product, extraction, strategy.strategies, "live")
    assert all(long_title not in c for c in sent)                      # 미확인 한도 때문에 AI 를 부르지 않는다
    flags = full_review(result.page, extraction, risk_rules_for("us", "skincare"), product)
    auto_items, _ = refine.auto_decisions(result.page, flags)
    assert "S1" not in auto_items                                      # 대신 사람이 본다
    assert any(f.source == "형식" and f.item_id == "S1" for f in flags)


def test_demo_marketplace_without_limit_is_unaffected():
    product = pipeline.load_demo_input()
    extraction, _ = pipeline.extract(product, [], "demo")
    strategy, _ = pipeline.recommend(product, extraction, "demo")
    page, _ = pipeline.generate(product, extraction, strategy.strategies, "demo")
    assert [f for f in full_review(page, extraction, risk_rules_for("jp", "skincare"), product) if f.source == "형식"] == []
