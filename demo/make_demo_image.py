"""데모용 가상 상세페이지 이미지(demo/detail_page.jpg)와 글자 위치(demo/text_boxes.json)를 만든다.

한국 상세페이지에서 흔한 구성(긴 세로 이미지, 근거 없는 배지, 유행어)을 일부러 넣었다.
'결담(GYEOLDAM)'은 가상 브랜드다. Windows 의 맑은 고딕 글꼴이 필요하다.
글자 위치는 데모 모드의 '이미지 속 문구 자동 교체'가 쓴다(실제 모드에서는 AI 가 위치를 찾는다).
실행: .venv\\Scripts\\python demo\\make_demo_image.py
"""
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BOXES: list[dict] = []          # 그린 글자마다 {"text", "box": [x0, y0, x1, y1]} (전체 이미지 좌표)
_section_top = 0                # 지금 그리는 구역의 시작 y (전체 이미지 기준)


def text(draw, xy, s, f, fill):
    """draw.text 대신 쓰는 함수: 글자를 그리고 위치를 기록한다."""
    draw.text(xy, s, font=f, fill=fill)
    x0, y0, x1, y1 = draw.textbbox(xy, s, font=f)
    BOXES.append({"text": s, "box": [int(x0), int(y0) + _section_top, int(x1), int(y1) + _section_top]})

FONT = "C:/Windows/Fonts/malgun.ttf"
FONT_BOLD = "C:/Windows/Fonts/malgunbd.ttf"
WIDTH = 860
GREEN, DARK, PINK, GRAY = (226, 239, 228), (38, 64, 48), (250, 232, 234), (243, 243, 243)


def font(size, bold=False):
    return ImageFont.truetype(FONT_BOLD if bold else FONT, size)


def center(draw, y, s, size, bold=False, fill=DARK):
    f = font(size, bold)
    w = draw.textlength(s, font=f)
    text(draw, ((WIDTH - w) / 2, y), s, f, fill)


def bottle(draw, x, y, scale=1.0):
    s = lambda v: int(v * scale)
    draw.rounded_rectangle((x + s(35), y, x + s(105), y + s(70)), radius=s(8), fill=(60, 90, 70))
    draw.rounded_rectangle((x, y + s(70), x + s(140), y + s(400)), radius=s(24), fill=(250, 252, 250), outline=(140, 170, 150), width=3)
    text(draw, (x + s(22), y + s(190)), "GYEOLDAM", font(s(18), True), DARK)
    text(draw, (x + s(34), y + s(220)), "어성초 77", font(s(18)), DARK)


def section(height, color):
    global _section_top
    _section_top = sum(p.height for p in _parts)
    img = Image.new("RGB", (WIDTH, height), color)
    return img, ImageDraw.Draw(img)


_parts: list[Image.Image] = []


def build() -> Image.Image:
    BOXES.clear()
    _parts.clear()
    parts = _parts

    img, d = section(1000, GREEN)                       # 1. 메인
    center(d, 110, "예민한 피부를 위한", 44)
    center(d, 175, "데일리 진정 토너", 64, True)
    bottle(d, 360, 330)
    center(d, 820, "결담 어성초 77 진정 토너 200ml", 34, True)
    parts.append(img)

    img, d = section(900, "white")                      # 2. 성분
    center(d, 90, "어성초 추출물", 40)
    center(d, 150, "77%", 120, True, fill=(46, 125, 70))
    for i, label in enumerate(["판테놀", "히알루론산", "pH 5.5 약산성"]):
        cx = 150 + i * 280
        d.ellipse((cx - 105, 400, cx + 105, 610), fill=GREEN)
        w = d.textlength(label, font=font(28, True))
        text(d, (cx - w / 2, 487), label, font(28, True), DARK)
    center(d, 700, "무향료 · 무색소", 40, True)
    parts.append(img)

    img, d = section(900, PINK)                         # 3. 근거 없는 주장
    center(d, 110, "트러블 싹!", 80, True, fill=(190, 60, 80))
    center(d, 240, "3일이면 진정 끝", 56, True, fill=(190, 60, 80))
    for i, label in enumerate([("민감피부", "판매 1위"), ("피부과", "테스트 완료")]):
        cx = 280 + i * 300
        d.ellipse((cx - 120, 430, cx + 120, 670), fill=(190, 60, 80))
        for j, line in enumerate(label):
            w = d.textlength(line, font=font(32, True))
            text(d, (cx - w / 2, 505 + j * 48), line, font(32, True), "white")
    parts.append(img)

    img, d = section(900, "white")                      # 4. 사용법 + 유행어
    center(d, 90, "꿀피부 만들기 꿀팁!", 48, True)
    center(d, 220, "STEP 1  화장솜에 듬뿍 적셔요", 36)
    center(d, 320, "STEP 2  피부결 따라 닦아내요", 36)
    d.line((180, 440, 680, 440), fill=(200, 200, 200), width=2)
    center(d, 500, "손으로 톡톡 두드려 흡수해도 OK", 36)
    bottle(d, 395, 600, 0.55)
    parts.append(img)

    img, d = section(800, GRAY)                         # 5. 후기
    center(d, 90, "★★★★★ 실제 구매 후기", 44, True)
    for i, review in enumerate(["“향이 하나도 없어서 좋아요”", "“닦토로 아낌없이 써요”", "“벌써 두 병째예요”"]):
        d.rounded_rectangle((110, 230 + i * 160, 750, 350 + i * 160), radius=20, fill="white")
        center(d, 265 + i * 160, review, 34)
    parts.append(img)

    img, d = section(700, "white")                      # 6. 제품 정보
    center(d, 80, "제품 정보", 44, True)
    rows = [("제품명", "결담 어성초 77 진정 토너"), ("용량", "200ml"), ("제조국", "대한민국"),
            ("사용기한", "제조일로부터 36개월"), ("주의사항", "이상이 있을 경우 사용 중지")]
    for i, (key, value) in enumerate(rows):
        y = 200 + i * 90
        d.line((100, y + 70, 760, y + 70), fill=(220, 220, 220), width=2)
        text(d, (120, y + 15), key, font(30, True), DARK)
        text(d, (330, y + 15), value, font(30), DARK)
    parts.append(img)

    page = Image.new("RGB", (WIDTH, sum(p.height for p in parts)), "white")
    y = 0
    for p in parts:
        page.paste(p, (0, y))
        y += p.height
    return page


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    build().save(here / "detail_page.jpg", quality=90)
    (here / "text_boxes.json").write_text(json.dumps(BOXES, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"저장: {here / 'detail_page.jpg'}, 글자 {len(BOXES)}개 위치 → {here / 'text_boxes.json'}")
