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


def test_image_order_override_and_zero_excludes():
    product, page = demo()
    media = {m.id: m.jpeg for m in pipeline.load_demo_media()}
    gd = {g.image_id: "수락" for g in page.image_guides}
    html = render_preview(product, page, decisions_for(page), gd, media, include_unreviewed=False,
                          image_order={"I1": 9, "I3": 0})
    assert 'alt="I3"' not in html                                         # 순서 0 은 뺀다
    assert html.index('alt="I2"') < html.index('alt="I4"') < html.index('alt="I1"')   # I1 을 맨 뒤로


def test_replaced_image_shows_done_badge_without_replacement_table():
    product, page = demo()
    media = {m.id: m.jpeg for m in pipeline.load_demo_media()}
    gd = {g.image_id: "수락" for g in page.image_guides}
    html = render_preview(product, page, decisions_for(page), gd, media, include_unreviewed=False, replaced={"I1"})
    start, end = html.index('alt="I1"'), html.index('alt="I2"')
    block = html[start:end]
    assert "교체 이미지" in block and '<table class="rep">' not in block
    assert '<table class="rep">' in html[end:]                             # 다른 이미지에는 표가 남는다


def test_final_images_are_numbered_in_order_and_skip_missing_files():
    from core.preview import final_images
    _, page = demo()
    media = {m.id: m.jpeg for m in pipeline.load_demo_media()}
    gd = {g.image_id: "수락" for g in page.image_guides}
    files = final_images(page, gd, media, image_order={"I4": 1, "I1": 2, "I2": 3, "I3": 0, "NEW1": 4})
    assert [name for name, _ in files] == ["01_I4.jpg", "02_I1.jpg", "03_I2.jpg"]   # NEW1 은 파일이 없어 빠진다
    assert files[0][1] == media["I4"]


def test_zip_contains_all_files():
    import io
    import zipfile
    from core.export import to_zip
    data = to_zip({"index.html": "<p>한글</p>", "images/01_I1.jpg": b"\xff\xd8\xff"})
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert sorted(zf.namelist()) == ["images/01_I1.jpg", "index.html"]
        assert zf.read("index.html").decode("utf-8") == "<p>한글</p>"
