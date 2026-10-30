"""API 비용 없이 돌리는 테스트. 실행: .venv\\Scripts\\python -m pytest -q"""
import json

from core import pipeline
from core.config_loader import load_all, risk_rules_for
from core.export import compute_metrics, final_texts, to_json, to_markdown
from core.review import ORIGINAL_ITEM_ID, evidence_review, full_review, number_review, rule_review
from core.schemas import Extraction, GeneratedPage, StrategyResult

RULES = risk_rules_for("jp", "skincare")


def demo():
    product = pipeline.load_demo_input()
    media = pipeline.load_demo_media()
    extraction, _ = pipeline.extract(product, media, "demo")
    strategy, _ = pipeline.recommend(product, extraction, "demo")
    page, _ = pipeline.generate(product, extraction, strategy.strategies, "demo")
    return product, extraction, strategy, page


# ── 설정 ────────────────────────────────────────────────────
def test_config_references_are_consistent():
    cfg = load_all()
    for market in cfg["marketplaces"].values():
        assert market["country"] in cfg["countries"]
    for ruleset in cfg["risk_rules"].values():
        assert ruleset["applies_to"]["country"] in (*cfg["countries"], "any")


def test_demo_strategies_exist_in_config():
    _, _, strategy, page = demo()
    known = load_all()["strategies"]
    assert all(s.strategy_id in known for s in strategy.strategies)
    assert all(sid in known for item in page.items for sid in item.strategy_ids)


# ── 데모 데이터가 원칙을 지키는지 ─────────────────────────────
def test_demo_images_match_analysis():
    _, extraction, _, page = demo()
    media_ids = {m.id for m in pipeline.load_demo_media()}
    assert {i.image_id for i in extraction.images} == media_ids
    existing_guides = {g.image_id for g in page.image_guides if not g.image_id.startswith("NEW")}
    assert existing_guides == media_ids          # 모든 기존 이미지에 지시서가 있어야 한다


def test_demo_uses_only_existing_facts_and_no_unproven_claims():
    _, extraction, _, page = demo()
    assert evidence_review(page, extraction) == []


def test_demo_localized_text_has_no_rule_or_number_flags():
    product, extraction, _, page = demo()
    flags = rule_review(page, RULES, product, None, extraction) + number_review(page, extraction, product)
    assert [f for f in flags if not f.item_id.startswith(ORIGINAL_ITEM_ID)] == []


def test_original_risks_in_text_and_images_are_detected():
    product, extraction, _, page = demo()
    found = {(f.item_id, f.expression) for f in rule_review(page, RULES, product, None, extraction)}
    assert {("원본", "1위"), ("원본", "싹 사라"), ("원본", "피부과 테스트")} <= found
    assert {("원본 I2", "트러블 싹"), ("원본 I2", "3일이면"), ("원본 I2", "1위")} <= found


# ── 검수 ────────────────────────────────────────────────────
def test_user_edit_is_rechecked():
    product, extraction, _, page = demo()
    edited = {"S2": "ドクダミエキス配合。ニキビが消える最高の化粧水"}
    flags = rule_review(page, RULES, product, edited, extraction)
    found = {f.expression for f in flags if f.item_id == "S2"}
    assert {"ニキビが消える", "最高"} <= found


def test_wrong_number_is_flagged_including_fullwidth():
    product, extraction, _, page = demo()
    edited = {"S2": "ドクダミエキスを７０%配合", "S5": "3日で実感"}
    flags = number_review(page, extraction, product, edited)
    assert ("S2", "70%") in {(f.item_id, f.expression) for f in flags}
    # '3'은 원본 이미지에 있는 숫자라 숫자 검수는 통과하지만, 기간 효과 규칙에 걸려야 한다
    assert any(f.item_id == "S5" for f in rule_review(page, RULES, product, edited, extraction))


def test_image_guide_text_is_checked():
    product, extraction, _, page = demo()
    page = page.model_copy(deep=True)
    page.image_guides[1].text_replacements[4].localized = "肌荒れが治る！"
    flags = rule_review(page, RULES, product, None, extraction)
    assert any(f.item_id == "I2" and "治る" in f.expression for f in flags)


def test_invented_fact_id_and_unproven_claim_are_flagged():
    _, extraction, _, page = demo()
    page = page.model_copy(deep=True)
    page.items[0].fact_ids = ["F999"]            # 존재하지 않는 근거
    page.items[1].fact_ids = ["F13"]             # 증빙 없는 '판매 1위'
    page.image_guides[0].fact_ids = ["F20"]      # 증빙 없는 '3일이면 진정 끝'
    flags = evidence_review(page, extraction)
    assert any(f.item_id == "S1" and f.severity == "높음" for f in flags)
    assert any(f.item_id == "S2" and "F13" in f.expression for f in flags)
    assert any(f.item_id == "I1" and "F20" in f.expression for f in flags)


def test_us_rules_do_not_apply_to_japan():
    assert all("미국" not in r["ruleset"] for r in RULES)


# ── 내보내기 ─────────────────────────────────────────────────
def test_export_includes_only_accepted_and_edited():
    product, extraction, strategy, page = demo()
    decisions = {i.id: {"status": "거절", "text": i.localized} for i in page.items}
    decisions["S1"] = {"status": "수락", "text": page.items[0].localized}
    decisions["S2"] = {"status": "편집", "text": "直接修正した文言"}
    guide_decisions = {g.image_id: "거절" for g in page.image_guides}
    guide_decisions["I2"] = "수락"
    flags = full_review(page, extraction, RULES, product, final_texts(page, decisions))
    metrics = compute_metrics(page, decisions, guide_decisions, strategy, ["review_trust"], 120.0, [])

    md = to_markdown(product, page, decisions, guide_decisions, flags, extraction, metrics)
    assert page.items[0].localized in md
    assert "直接修正した文言" in md
    assert page.items[2].localized not in md        # 거절한 항목은 빠진다
    assert "I2 — 구성 수정" in md and "I3 —" not in md
    assert metrics["그대로_수락"] == 1 and metrics["직접_수정"] == 1 and metrics["이미지_지시서_수락"] == 1

    data = json.loads(to_json(product, extraction, strategy, ["review_trust"], page, decisions,
                              guide_decisions, flags, metrics))
    assert data["decisions"]["S2"]["status"] == "편집"


# ── 실제 AI 모드 요청 형식(API 를 부르지 않고 가로채서 확인) ─────────
def test_live_requests_send_images_once(monkeypatch):
    product, extraction, strategy, page = demo()
    media = pipeline.load_demo_media()
    calls = []
    monkeypatch.setattr(pipeline.llm, "call_structured",
                        lambda system, content, model: calls.append(content) or (None, None))

    pipeline.extract(product, media, "live")
    blocks = calls[0]
    assert [b["type"] for b in blocks[:2]] == ["text", "image"]
    assert blocks[0]["text"].startswith("[이미지 I1]")
    assert sum(b["type"] == "image" for b in blocks) == len(media)
    assert blocks[-1]["type"] == "text" and "{{" not in blocks[-1]["text"]

    pipeline.generate(product, extraction, strategy.strategies, "live")
    prompt = calls[1]
    assert isinstance(prompt, str)                       # 생성 단계는 이미지를 다시 보내지 않는다
    assert "3일이면 진정 끝" in prompt and "{{" not in prompt


# ── AI 응답 형식 ─────────────────────────────────────────────
def test_schemas_produce_json_schema():
    for model in (Extraction, StrategyResult, GeneratedPage):
        assert model.model_json_schema()["type"] == "object"
