"""자기검수 루프와 자동 승인 규칙. API 는 부르지 않고 가로채서 확인한다."""
from core import pipeline, refine
from core.config_loader import risk_rules_for
from core.export import compute_metrics
from core.review import full_review
from core.schemas import Revision, RiskFlag

RULES = risk_rules_for("jp", "skincare")


def demo():
    product = pipeline.load_demo_input()
    extraction, _ = pipeline.extract(product, pipeline.load_demo_media(), "demo")
    strategy, _ = pipeline.recommend(product, extraction, "demo")
    page, _ = pipeline.generate(product, extraction, strategy.strategies, "demo")
    return product, extraction, strategy, page


def with_item(page, item_id, **changes):
    return page.model_copy(update={
        "items": [i.model_copy(update=changes) if i.id == item_id else i for i in page.items],
    })


def flagged_ids(product, extraction, page):
    return {f.item_id for f in refine.result_flags(full_review(page, extraction, RULES, product))}


# ── 자동 승인 ───────────────────────────────────────────────
def test_auto_decisions_leave_only_flagged_items_to_human():
    product, extraction, _, page = demo()
    items, guides = refine.auto_decisions(page, full_review(page, extraction, RULES, product))
    flagged = {f.item_id for f in page.risk_flags}            # 데모에는 AI 자체 점검 표시만 있다
    assert flagged == {"S1", "S3", "S4", "S17"}
    assert items == {i.id for i in page.items} - flagged
    assert guides == {g.image_id for g in page.image_guides}  # 지시서는 표시가 없어 모두 자동 수락


def test_delete_guides_and_flagged_guides_need_human():
    product, extraction, _, page = demo()
    page = page.model_copy(update={
        "image_guides": [g.model_copy(update={"action": "삭제", "recommended_order": 0}) if g.image_id == "I3" else g
                         for g in page.image_guides],
        "risk_flags": page.risk_flags + [RiskFlag(item_id="I2", expression="x", risk="r", suggestion="s", severity="낮음")],
    })
    _, guides = refine.auto_decisions(page, full_review(page, extraction, RULES, product))
    assert "I3" not in guides and "I2" not in guides and "I1" in guides


def test_metrics_count_auto_accepted_separately():
    _, _, strategy, page = demo()
    decisions = {i.id: {"status": "수락", "text": i.localized} for i in page.items}
    decisions["S2"] = {"status": "편집", "text": "고친 문구"}            # 자동 수락 후 사람이 고친 항목은 자동 수락이 아니다
    auto = {i.id for i in page.items}
    m = compute_metrics(page, decisions, {}, strategy, [], None, [], auto, set())
    assert m["자동_수락"] == len(page.items) - 1 and m["사람이_검토한_문구"] == 1


# ── 자기검수 루프 ─────────────────────────────────────────────
def test_demo_mode_skips_self_review():
    product, extraction, strategy, _ = demo()
    result, usages = refine.generate_with_review(product, extraction, strategy.strategies, "demo")
    assert result.skipped and result.rounds == [] and usages == []


def test_self_review_fixes_number_error_and_does_not_resend_unfixable_items(monkeypatch):
    product, extraction, strategy, page = demo()
    good_s2 = next(i for i in page.items if i.id == "S2")
    broken = with_item(page, "S2", localized=good_s2.localized + " 容量99ml")      # 입력에 없는 숫자
    assert "S2" in flagged_ids(product, extraction, broken)

    calls = []

    def fake_call(system, content, model):
        calls.append((content, model))
        if model is Revision:
            assert "99ml" in content and "S2" in content and "{{" not in content
            assert "S10" not in content.split("## 검수에 걸린 이미지 지시서")[0]   # 깨끗한 항목은 보내지 않는다
            return Revision(items=[good_s2.model_copy(update={"reason": good_s2.reason + " 자기검수에서 수정: 없는 숫자 삭제"})],
                            image_guides=[], remaining_flags=[], notes=["S2: 입력에 없는 99ml 삭제"]), {"cost_usd": 0.01}
        return broken, {"cost_usd": 0.1}

    monkeypatch.setattr(pipeline.llm, "call_structured", fake_call)
    monkeypatch.setenv("REFINE_ROUNDS", "3")
    result, usages = refine.generate_with_review(product, extraction, strategy.strategies, "live")

    assert [m for _, m in calls].count(Revision) == 1        # 2회차는 새로 보낼 항목이 없어 호출하지 않는다
    assert len(usages) == 2 and not result.skipped
    assert len(result.rounds) == 1
    r = result.rounds[0]
    assert "S2" in r.sent and r.notes == ["S2: 입력에 없는 99ml 삭제"]
    assert "S2" not in r.flagged_after and set(r.flagged_after) == {"S1", "S3", "S4", "S17"}
    assert next(i for i in result.page.items if i.id == "S2").localized == good_s2.localized


def test_merge_replaces_touched_items_and_their_ai_flags_only():
    _, _, _, page = demo()
    s1 = next(i for i in page.items if i.id == "S1")
    new_flag = RiskFlag(item_id="S1", expression="e", risk="사람 확인 필요", suggestion="s", severity="낮음")
    stray = RiskFlag(item_id="S99", expression="e", risk="r", suggestion="s", severity="낮음")
    revised = s1.model_copy(update={"localized": "新しい商品名"})
    ghost = s1.model_copy(update={"id": "S99"})
    merged = refine.merge(page, Revision(items=[revised, ghost], image_guides=[], remaining_flags=[new_flag, stray], notes=[]))
    assert [i.id for i in merged.items] == [i.id for i in page.items]
    assert merged.items[0].localized == "新しい商品名"
    assert [f for f in merged.risk_flags if f.item_id == "S1"] == [new_flag]
    assert {f.item_id for f in merged.risk_flags} == {"S1", "S3", "S4", "S17"}   # 손대지 않은 항목의 표시는 유지, S99 는 무시


def test_rounds_limit_is_respected(monkeypatch):
    product, extraction, strategy, page = demo()
    bad = with_item(page, "S2", localized="容量99ml")

    def fake_call(system, content, model):
        if model is Revision:         # 고치지 못하고 그대로 돌려준다 → 표시가 남는다
            return Revision(items=[], image_guides=[], remaining_flags=[], notes=["고치지 못함"]), None
        return bad, None

    monkeypatch.setattr(pipeline.llm, "call_structured", fake_call)
    monkeypatch.setenv("REFINE_ROUNDS", "1")
    result, usages = refine.generate_with_review(product, extraction, strategy.strategies, "live")
    assert len(result.rounds) == 1 and "S2" in result.rounds[0].flagged_after and usages == []

    monkeypatch.setenv("REFINE_ROUNDS", "0")
    result, _ = refine.generate_with_review(product, extraction, strategy.strategies, "live")
    assert result.skipped and result.rounds == []
