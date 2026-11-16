"""이미지 속 한국어 문구 자동 교체(core/overlay.py). API 는 부르지 않는다."""
import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from core import overlay, pipeline
from core.media import MediaItem, _encode
from core.schemas import ImageGuide, TextBox, TextReplacement

MALGUN = Path("C:/Windows/Fonts/malgun.ttf")


def demo():
    product = pipeline.load_demo_input()
    media = pipeline.load_demo_media()
    extraction, _ = pipeline.extract(product, media, "demo")
    strategy, _ = pipeline.recommend(product, extraction, "demo")
    page, _ = pipeline.generate(product, extraction, strategy.strategies, "demo")
    return product, media, extraction, page


def demo_overlay():
    _, media, _, page = demo()
    guides = overlay.eligible(page.image_guides, {m.id for m in media})
    targets = {g.image_id: [r.original_ko for r in g.text_replacements if r.original_ko] for g in guides}
    located, usage = pipeline.locate(media, targets, "demo")
    assert usage is None
    return media, page, located, overlay.overlay_all(media, page.image_guides, located.boxes, page.language)


def region_std(jpeg: bytes, box: tuple) -> float:
    arr = np.asarray(Image.open(io.BytesIO(jpeg)).convert("RGB").crop(box)).astype(np.float32)
    return float(arr.reshape(-1, 3).std(axis=0).max())


# ── 데모 이미지 ─────────────────────────────────────────────
def test_demo_boxes_are_mapped_into_pieces():
    media, page, located, _ = demo_overlay()
    ids = {b.image_id for b in located.boxes}
    assert ids == {"I1", "I2", "I3", "I4"}
    assert all(0 <= v <= 1000 for b in located.boxes for v in (b.x0, b.y0, b.x1, b.y1))
    assert any(b.text_ko == "예민한 피부를 위한" and b.image_id == "I1" for b in located.boxes)


def test_demo_overlay_replaces_every_located_text():
    _, page, _, results = demo_overlay()
    assert set(results) == {"I1", "I2", "I3", "I4"}          # NEW1(새로 추가)은 대상이 아니다
    for r in results.values():
        assert r.jpeg
        assert not any("위치를 찾지 못했습니다" in x.status for x in r.replacements)
    statuses = [x.status for x in results["I2"].replacements]
    assert statuses.count("교체") == 4 and statuses.count("삭제") == 4
    skipped = results["I4"].skipped
    assert len(skipped) == 1 and skipped[0].localized.startswith("全成分")   # 새로 넣는 문구는 자리가 없다


def test_deleted_claim_leaves_flat_background():
    media, _, located, results = demo_overlay()
    item = next(m for m in media if m.id == "I2")
    box = next(b for b in located.boxes if b.image_id == "I2" and b.text_ko == "트러블 싹!")
    px = (box.x0 * item.width // 1000, box.y0 * item.height // 1000, box.x1 * item.width // 1000, box.y1 * item.height // 1000)
    assert region_std(item.jpeg, px) > 40                 # 원본에는 빨간 글자가 있다
    assert region_std(results["I2"].jpeg, px) < 6         # 지운 뒤에는 분홍 배경만 남는다


def test_two_line_badge_is_matched_as_one_text():
    boxes = [overlay.Box("민감피부", 200, 500, 300, 540), overlay.Box("피부과", 500, 500, 600, 540),
             overlay.Box("판매 1위", 205, 548, 295, 588), overlay.Box("테스트 완료", 480, 548, 620, 588)]
    matched = overlay.match_boxes(["민감피부 판매 1위", "피부과 테스트 완료", "없는 문구"], boxes)
    assert set(matched) == {"민감피부 판매 1위", "피부과 테스트 완료"}
    union = matched["민감피부 판매 1위"][0]
    assert (union.x0, union.y0, union.x1, union.y1, union.lines) == (200, 500, 300, 588, 2)


# ── 합성 이미지: 한계 확인 ───────────────────────────────────
@pytest.mark.skipif(not MALGUN.exists(), reason="맑은 고딕 글꼴이 있는 Windows 에서만")
def test_text_on_photo_like_background_is_skipped():
    img = Image.new("RGB", (600, 200))
    px = img.load()
    for x in range(600):                                   # 가로 그라데이션(사진처럼 단색이 아닌 배경)
        for y in range(200):
            px[x, y] = (x * 255 // 600, 120, 255 - x * 255 // 600)
    draw = ImageDraw.Draw(img)
    draw.text((150, 70), "자연 유래 성분", font=ImageFont.truetype(str(MALGUN), 40), fill="white")
    item = MediaItem("I1", "합성", _encode(img), 600, 200)
    guide = ImageGuide(image_id="I1", action="문구만 교체", recommended_order=1,
                       text_replacements=[TextReplacement(original_ko="자연 유래 성분", localized="自然由来成分", back_translation_ko="자연 유래 성분")],
                       edit_instructions=[], keep_unchanged=[], reason="", fact_ids=[])
    result = overlay.replace_texts(item, guide, [TextBox(image_id="I1", text_ko="자연 유래 성분", x0=240, y0=330, x1=760, y1=680)], "ja")
    assert result.jpeg is None and result.skipped and "단색이 아니라" in result.skipped[0].status


@pytest.mark.skipif(not MALGUN.exists(), reason="맑은 고딕 글꼴이 있는 Windows 에서만")
def test_rough_ai_box_is_tightened_to_the_ink():
    """실제 모드에서 AI 가 준 상자가 넉넉해도 잉크 영역만 지우고, 글자색·배경색을 그대로 쓴다."""
    img = Image.new("RGB", (600, 200), (250, 232, 234))
    ImageDraw.Draw(img).text((200, 80), "무향료", font=ImageFont.truetype(str(MALGUN), 36), fill=(190, 60, 80))
    item = MediaItem("I1", "합성", _encode(img), 600, 200)
    guide = ImageGuide(image_id="I1", action="문구만 교체", recommended_order=1,
                       text_replacements=[TextReplacement(original_ko="무향료", localized="無香料", back_translation_ko="무향료")],
                       edit_instructions=[], keep_unchanged=[], reason="", fact_ids=[])
    loose = TextBox(image_id="I1", text_ko="무향료", x0=250, y0=250, x1=600, y1=800)      # 실제 글자보다 훨씬 큰 상자
    result = overlay.replace_texts(item, guide, [loose], "ja")
    assert result.done and result.done[0].status == "교체"
    out = np.asarray(Image.open(io.BytesIO(result.jpeg)).convert("RGB")).astype(int)
    assert abs(out[10, 10] - np.array([250, 232, 234])).max() < 8         # 배경은 그대로
    reds = ((out[:, :, 0] > 150) & (out[:, :, 1] < 110)).sum()
    assert reds > 100                                                      # 같은 색으로 글자가 다시 그려졌다


# ── 실제 모드 요청 형식 ──────────────────────────────────────
def test_locate_live_request_sends_only_target_images(monkeypatch):
    _, media, _, _ = demo()
    calls = []
    monkeypatch.setattr(pipeline.llm, "call_structured", lambda system, content, model: calls.append(content) or (None, None))
    pipeline.locate(media, {"I2": ["판테놀"], "I4": ["용량"]}, "live")
    blocks = calls[0]
    assert sum(b["type"] == "image" for b in blocks) == 2
    assert blocks[0]["text"].startswith("[이미지 I2]")
    assert "판테놀" in blocks[-1]["text"] and "{{" not in blocks[-1]["text"]
