"""이미지 조각내기, PDF 변환, URL 가져오기 테스트(네트워크·API 비용 없음)."""
import io

import numpy as np
import pytest
from PIL import Image, ImageDraw

from core import media, url_import


def striped_page(height: int, line_every: int = 120) -> Image.Image:
    """흰 바탕에 일정 간격으로 굵은 '글자 줄'이 있는 긴 이미지."""
    img = Image.new("RGB", (860, height), "white")
    draw = ImageDraw.Draw(img)
    for y in range(40, height - 40, line_every):
        draw.rectangle((100, y, 760, y + 40), fill="black")
    return img


def test_long_image_is_cut_between_text_lines():
    img = striped_page(6000)
    pieces = media.slice_image(img)
    assert len(pieces) >= 4
    assert sum(p.height for p in pieces) == 6000
    gray = np.asarray(img.convert("L"))
    top = 0
    for piece in pieces[:-1]:
        top += piece.height
        assert gray[top].std() == 0          # 자른 줄은 비어 있어야 한다(글자 위가 아님)


def test_short_image_is_not_cut_and_wide_image_is_resized():
    assert len(media.slice_image(striped_page(1200))) == 1
    wide = media.slice_image(Image.new("RGB", (2000, 1000), "white"))
    assert wide[0].width == media.MAX_WIDTH


def test_build_media_ids_labels_and_limit():
    items = media.build_media([("a.jpg", striped_page(3500)), ("b.png", striped_page(800))])
    assert [i.id for i in items] == [f"I{n}" for n in range(1, len(items) + 1)]
    assert items[-1].label == "b.png" and "(1/" in items[0].label
    with pytest.raises(media.MediaError):
        media.build_media([("huge.jpg", striped_page(40000))])


def test_transparent_png_gets_white_background():
    buffer = io.BytesIO()
    Image.new("RGBA", (500, 500), (0, 0, 0, 0)).save(buffer, "PNG")
    assert media.open_image(buffer.getvalue()).getpixel((10, 10)) == (255, 255, 255)


def test_pdf_pages_become_images():
    buffer = io.BytesIO()
    striped_page(1200).save(buffer, "PDF")
    pages = media.pdf_to_images(buffer.getvalue())
    assert len(pages) == 1 and abs(pages[0].width - media.PDF_RENDER_WIDTH) <= 2   # 렌더링 반올림 오차


def test_private_addresses_are_blocked():
    for url in ("http://127.0.0.1/", "http://localhost:8501/", "http://192.168.0.1/admin", "file:///etc/passwd",
                "http://169.254.169.254/latest/meta-data/"):
        with pytest.raises(url_import.UrlImportError):
            url_import._check_host(url)


def test_html_parser_collects_text_and_lazy_images():
    parser = url_import._PageParser()
    parser.feed("""
        <html><head><title>상품</title><script>var x = '숨김';</script></head>
        <body><p>어성초 77% 토너</p><p>어성초 77% 토너</p>
        <img src="/blank.gif" data-src="/detail1.jpg"><img srcset="/s.jpg 1x, /detail2.jpg 2x">
        <img src="data:image/png;base64,AAA"></body></html>""")
    assert parser.title == "상품"
    assert url_import._clean_text(parser.texts) == "어성초 77% 토너"
    assert parser.images == ["/detail1.jpg", "/detail2.jpg"]
