"""이미지 지시서 직접 수정 테스트."""
from core import pipeline
from core.config_loader import risk_rules_for
from core.edits import ADDED_BACK, EDITED_BACK, apply_guide_edits, is_edited, table_rows, with_guides
from core.export import compute_metrics, to_markdown
from core.review import number_review, rule_review

RULES = risk_rules_for("jp", "skincare")


def demo():
    product = pipeline.load_demo_input()
    extraction, _ = pipeline.extract(product, pipeline.load_demo_media(), "demo")
    strategy, _ = pipeline.recommend(product, extraction, "demo")
    page, _ = pipeline.generate(product, extraction, strategy.strategies, "demo")
    return product, extraction, strategy, page


def guide(page, image_id):
    return next(g for g in page.image_guides if g.image_id == image_id)


def test_no_changes_means_not_edited():
    _, _, _, page = demo()
    g = guide(page, "I3")
    assert not is_edited(g, apply_guide_edits(g, {"edited_rows": {}, "added_rows": [], "deleted_rows": []}))
    assert len(table_rows(g)) == len(g.text_replacements)


def test_edit_add_delete_rows_and_instructions():
    _, _, _, page = demo()
    g = guide(page, "I3")                        # 7행: 0='꿀피부 만들기 꿀팁!' … 4='★★★★★ 실제 구매 후기'
    state = {
        "edited_rows": {0: {"바꿀 문구": "ご使用方法"}, "1": {"기존 문구": "STEP 1 화장솜에 듬뿍 적셔요!"}},
        "deleted_rows": [4],
        "added_rows": [{"바꿀 문구": "※韓国での販売時のレビューです"}, {"기존 문구": None, "바꿀 문구": None}],
    }
    edited = apply_guide_edits(g, state, "- 별점 삭제\n\n후기 주석 추가", None)
    rows = edited.text_replacements
    assert rows[0].localized == "ご使用方法" and rows[0].back_translation_ko == EDITED_BACK
    assert rows[1].original_ko.endswith("!") and rows[1].localized == g.text_replacements[1].localized
    assert all("★" not in r.original_ko for r in rows)                  # 삭제한 행
    assert rows[-1].localized == "※韓国での販売時のレビューです" and rows[-1].back_translation_ko == ADDED_BACK
    assert len(rows) == len(g.text_replacements) - 1 + 1                # 빈 추가 행은 무시
    assert edited.edit_instructions == ["별점 삭제", "후기 주석 추가"]
    assert edited.keep_unchanged == g.keep_unchanged
    assert is_edited(g, edited)


def test_edited_guide_text_is_rechecked():
    product, extraction, _, page = demo()
    g = guide(page, "I2")
    edited = apply_guide_edits(g, {"edited_rows": {5: {"바꿀 문구": "3日で肌荒れが治る"}}})
    new_page = with_guides(page, [edited if x.image_id == "I2" else x for x in page.image_guides])
    found = {f.expression for f in rule_review(new_page, RULES, product, None, extraction) if f.item_id == "I2"}
    assert {"治る", "3日で"} <= found
    edited = apply_guide_edits(g, {"edited_rows": {0: {"바꿀 문구": "パンテノール3.5%"}}})
    new_page = with_guides(page, [edited if x.image_id == "I2" else x for x in page.image_guides])
    assert ("I2", "3.5%") in {(f.item_id, f.expression) for f in number_review(new_page, extraction, product)}


def test_edited_guide_is_exported_and_counted():
    product, extraction, strategy, page = demo()
    g = guide(page, "I4")
    edited = apply_guide_edits(g, None, "全成分の行を追加する", None)
    new_page = with_guides(page, [edited if x.image_id == "I4" else x for x in page.image_guides])
    guide_decisions = {x.image_id: "거절" for x in page.image_guides} | {"I4": "편집"}
    metrics = compute_metrics(new_page, {}, guide_decisions, strategy, [], None, [])
    md = to_markdown(product, new_page, {}, guide_decisions, [], extraction, metrics)
    assert "## I4 — 구성 수정 (5번째 · 직접 수정)" in md
    assert "- 全成分の行を追加する" in md
    assert metrics["이미지_지시서_직접_수정"] == 1
