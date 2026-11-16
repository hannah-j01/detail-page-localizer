"""상세페이지 미리보기(HTML) 테스트."""
from core import pipeline
from core.preview import HEADINGS, render_preview


def demo():
    product = pipeline.load_demo_input()
    extraction, _ = pipeline.extract(product, pipeline.load_demo_media(), "demo")
    strategy, _ = pipeline.recommend(product, extraction, "demo")
    page, _ = pipeline.generate(product, extraction, strategy.strategies, "demo")
    return product, page


def decisions_for(page, status="수락", **overrides):
    d = {i.id: {"status": status, "text": i.localized} for i in page.items}
    for item_id, change in overrides.items():
        d[item_id] = {**d[item_id], **change}
    return d


def test_review_mode_shows_everything_but_rejected_and_marks_status():
    product, page = demo()
    d = decisions_for(page, "미검토", S1={"status": "거절"}, S2={"status": "편집", "text": "고친 문구 <b>"})
    html = render_preview(product, page, d, {}, include_unreviewed=True)
    assert page.items[0].localized.split("\n")[0] not in html          # 거절한 상품명은 빠진다
    assert "고친 문구 &lt;b&gt;" in html                                 # 직접 고친 문구가 들어가고 HTML 은 이스케이프된다
    assert 'class="badge">직접 수정' in html and 'class="badge">미검토' in html
    assert "<script" not in html


def test_export_mode_includes_only_accepted_and_no_badges():
    product, page = demo()
    d = decisions_for(page, "수락", S3={"status": "미검토"}, S6={"status": "거절"})
    html = render_preview(product, page, d, {g.image_id: "수락" for g in page.image_guides}, include_unreviewed=False)
    assert page.items[0].localized in html
    assert next(i for i in page.items if i.id == "S3").localized not in html
    assert next(i for i in page.items if i.id == "S6").localized not in html
    assert 'class="badge"' not in html


def test_sections_follow_page_order_with_local_headings():
    product, page = demo()
    html = render_preview(product, page, decisions_for(page), {}, include_unreviewed=False)
    h = HEADINGS["ja"]
    positions = [html.index(f"<h1>"), html.index(h["핵심 소구점"]), html.index(h["상세 설명"]),
                 html.index(h["사용법"]), html.index(h["제품 사양"]), html.index(h["FAQ"]),
                 html.index('class="cta"'), html.index(h["검색 키워드"])]
    assert positions == sorted(positions)
    assert '<table class="spec">' in html and '<div class="faq">' in html and '<div class="tags">' in html


def test_images_follow_recommended_order_and_skip_deleted_or_rejected():
    product, page = demo()
    media = {m.id: m.jpeg for m in pipeline.load_demo_media()}
    guide_decisions = {g.image_id: "수락" for g in page.image_guides}
    guide_decisions["I2"] = "거절"
    html = render_preview(product, page, decisions_for(page), guide_decisions, media, include_unreviewed=True)
    shown = [g for g in sorted(page.image_guides, key=lambda g: g.recommended_order)
             if g.action != "삭제" and g.image_id != "I2"]
    positions = [html.index(f'alt="{g.image_id}"') if g.image_id in media else html.index(f"{g.image_id} 새로 만들 이미지")
                 for g in shown]
    assert positions == sorted(positions)
    assert 'alt="I2"' not in html
    assert html.count("data:image/jpeg;base64,") == len([g for g in shown if g.image_id in media])
    assert "→" in html                                                   # 기존 문구 → 바꿀 문구 표


def test_full_document_wraps_fragment():
    product, page = demo()
    doc = render_preview(product, page, decisions_for(page), {}, include_unreviewed=False, full_document=True)
    assert doc.startswith("<!doctype html>") and 'lang="ja"' in doc and product.name in doc
