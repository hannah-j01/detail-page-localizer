"""이미지 속 한국어 문구를 현지 언어 문구로 자동 교체한다(Pillow, API 비용 0원).

할 수 있는 것: 단색 배경(흰 바탕, 색 띠, 색 원 안) 위의 글자.
  1. 글자 위치(상자)를 받는다 — 실제 모드는 AI 가 찾은 비율 좌표, 데모 모드는 그릴 때 기록한 좌표.
  2. 상자 주변 픽셀로 배경색을 재고, 배경과 다른 픽셀(잉크)만 골라 글자 영역을 정확히 잡는다.
  3. 배경 테두리가 단색이 아니면(사진·그라데이션) 건너뛰고 디자이너 작업으로 남긴다.
  4. 글자 영역을 배경색으로 지우고, 같은 색·같은 높이·같은 정렬로 현지어 글자를 그린다.
     현지어가 더 길면 좌우의 빈 배경까지 쓰고, 그래도 넘치면 글자를 줄이거나(원래의 55%까지) 두 줄로 나눈다.
할 수 없는 것: 사진 위 글자, 그라데이션·패턴 배경, 글자에 윤곽선·그림자 효과가 있는 경우, 새로 넣는 문구(위치가 없음).
제품 사진·인증 마크·도형은 건드리지 않는다(글자 상자 안만 바꾼다).
"""
import io
import re
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw

from core import fonts
from core.media import MediaItem, _encode, open_image
from core.schemas import ImageGuide, TextBox

INK_DISTANCE = 60      # 배경색과 RGB 거리가 이보다 크면 글자(잉크)로 본다
SAME_BG = 30           # 배경이 "이어진다"고 볼 최대 색 차이. JPEG 잡음(≈25)은 넘기고 연한 초록↔흰색(≈43)은 구분한다
FLAT_STD = 14.0        # 배경 테두리 픽셀의 표준편차가 이보다 크면 단색 배경이 아니다
MIN_SCALE = 0.55       # 글자를 원래 높이의 이 비율까지만 줄인다
BOLD_INK_FRACTION = 0.30
ERASE_PAD = 6          # 글자 주변의 JPEG 번짐까지 함께 지운다
AUTO_ACTIONS = ("문구만 교체", "구성 수정")


@dataclass
class Box:
    text_ko: str
    x0: int
    y0: int
    x1: int
    y1: int
    lines: int = 1          # 여러 줄로 그려진 문구를 합친 상자면 줄 수

    @property
    def w(self) -> int:
        return self.x1 - self.x0

    @property
    def h(self) -> int:
        return self.y1 - self.y0


@dataclass
class Replacement:
    original_ko: str
    localized: str
    status: str             # "교체" | "삭제" | "건너뜀: …"
    note: str = ""


@dataclass
class OverlayResult:
    image_id: str
    jpeg: bytes | None      # 바뀐 게 없으면 None
    replacements: list[Replacement] = field(default_factory=list)

    @property
    def done(self) -> list[Replacement]:
        return [r for r in self.replacements if not r.status.startswith("건너뜀")]

    @property
    def skipped(self) -> list[Replacement]:
        return [r for r in self.replacements if r.status.startswith("건너뜀")]


# ── 문구 ↔ 상자 짝짓기 ──────────────────────────────────────
def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _union(boxes: list[Box]) -> Box:
    return Box("".join(b.text_ko for b in boxes), min(b.x0 for b in boxes), min(b.y0 for b in boxes),
               max(b.x1 for b in boxes), max(b.y1 for b in boxes), lines=len(boxes))


def _below(a: Box, b: Box) -> bool:
    """b 가 a 바로 아래 줄인가(세로로 이어지고 가로로 겹침)."""
    gap = b.y0 - a.y1
    overlap = min(a.x1, b.x1) - max(a.x0, b.x0)
    return -a.h * 0.3 <= gap <= a.h * 1.2 and overlap > 0


def match_boxes(texts: list[str], boxes: list[Box]) -> dict[str, list[Box]]:
    """교체할 원문 → 이미지 안의 상자(들). 공백 차이는 무시하고, 여러 줄로 그려진 문구는 아래로 이어지는 상자를 합쳐 맞춘다."""
    ordered = sorted(boxes, key=lambda b: (b.y0, b.x0))
    result: dict[str, list[Box]] = {}
    for text in texts:
        key = _norm(text)
        if not key:
            continue
        found = [b for b in ordered if _norm(b.text_ko) == key]
        if not found:
            for start in ordered:
                chain = [start]
                while len(chain) < 4 and _norm("".join(b.text_ko for b in chain)) != key:
                    nxt = [b for b in ordered if b not in chain and _below(chain[-1], b)]
                    if not nxt or not key.startswith(_norm("".join(b.text_ko for b in chain))):
                        break
                    chain.append(min(nxt, key=lambda b: b.y0))
                if _norm("".join(b.text_ko for b in chain)) == key:
                    found = [_union(chain)]
                    break
        if found:
            result[text] = found
    return result


# ── 픽셀 분석 ───────────────────────────────────────────────
@dataclass
class Region:
    bg: tuple[int, int, int]
    flat: bool
    tight: Box
    ink: tuple[int, int, int]
    bold: bool


def _ring(arr: np.ndarray, x0: int, y0: int, x1: int, y1: int, width: int = 2) -> np.ndarray:
    h, w = arr.shape[:2]
    X0, Y0, X1, Y1 = max(0, x0 - width), max(0, y0 - width), min(w, x1 + width), min(h, y1 + width)
    outer = arr[Y0:Y1, X0:X1].reshape(-1, 3)
    inner = arr[max(0, y0):min(h, y1), max(0, x0):min(w, x1)].reshape(-1, 3)
    if len(outer) <= len(inner):
        return outer
    # 바깥 사각형에서 안쪽 사각형을 뺀 테두리 픽셀
    mask = np.ones((Y1 - Y0, X1 - X0), dtype=bool)
    mask[max(0, y0) - Y0:min(h, y1) - Y0, max(0, x0) - X0:min(w, x1) - X0] = False
    return arr[Y0:Y1, X0:X1][mask]


def analyze(arr: np.ndarray, box: Box) -> Region | None:
    """상자 주변 배경색, 단색 여부, 잉크로 잡은 정확한 글자 영역, 글자색, 굵기 추정."""
    h, w = arr.shape[:2]
    pad = max(4, int(box.h * 0.12))
    x0, y0, x1, y1 = max(0, box.x0 - pad), max(0, box.y0 - pad), min(w, box.x1 + pad), min(h, box.y1 + pad)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    ring = _ring(arr, x0, y0, x1, y1).astype(np.float32)
    if len(ring) == 0:
        return None
    bg = np.median(ring, axis=0)
    flat = float(ring.std(axis=0).max()) <= FLAT_STD
    region = arr[y0:y1, x0:x1].astype(np.float32)
    dist = np.sqrt(((region - bg) ** 2).sum(axis=2))
    ink = dist > INK_DISTANCE
    if ink.sum() < 5:
        return None
    ys, xs = np.where(ink)
    tight = Box(box.text_ko, x0 + int(xs.min()), y0 + int(ys.min()), x0 + int(xs.max()) + 1, y0 + int(ys.max()) + 1, box.lines)
    ink_color = np.median(region[ink], axis=0)
    fraction = ink.sum() / max(1, tight.w * tight.h)
    return Region(tuple(int(v) for v in bg), flat, tight, tuple(int(v) for v in ink_color), fraction > BOLD_INK_FRACTION)


def _free_span(arr: np.ndarray, bg: tuple, y0: int, y1: int, x0: int, x1: int) -> tuple[int, int]:
    """[y0,y1) 줄 띠에서 상자 좌우로 배경색만 이어지는 범위(글자를 더 길게 쓸 수 있는 폭)."""
    h, w = arr.shape[:2]
    band = arr[max(0, y0):min(h, y1)].astype(np.float32)
    dist = np.sqrt(((band - np.array(bg, dtype=np.float32)) ** 2).sum(axis=2)).max(axis=0)   # 열마다 최대 거리
    margin = max(4, int(w * 0.02))
    left = x0
    while left - 1 >= margin and dist[left - 1] <= SAME_BG:
        left -= 1
    right = x1
    while right < w - margin and dist[right] <= SAME_BG:
        right += 1
    return left, right


def _rows_free(arr: np.ndarray, bg: tuple, y0: int, y1: int, x0: int, x1: int) -> bool:
    h, w = arr.shape[:2]
    if y0 < 0 or y1 > h:
        return False
    band = arr[y0:y1, max(0, x0):min(w, x1)].astype(np.float32)
    return bool((np.sqrt(((band - np.array(bg, dtype=np.float32)) ** 2).sum(axis=2)) <= SAME_BG).all())


# ── 글자 그리기 ─────────────────────────────────────────────
def _split(text: str, k: int) -> list[str]:
    """문구를 k 줄로 나눈다. 띄어쓰기가 있으면 거기서, 없으면(일본어·중국어) 구두점 뒤 또는 글자 수로 나눈다."""
    if k <= 1 or len(text) < 2:
        return [text]
    words = text.split(" ")
    if len(words) >= k:
        lines, per = [], len(text) / k
        cur = ""
        for word in words:
            if cur and len(cur) + 1 + len(word) > per * 1.15 and len(lines) < k - 1:
                lines.append(cur)
                cur = word
            else:
                cur = f"{cur} {word}".strip()
        lines.append(cur)
        return lines
    step = len(text) / k
    cuts = []
    for i in range(1, k):
        target = round(step * i)
        near = [j for j in range(max(1, target - 3), min(len(text), target + 3)) if text[j - 1] in "、。・，．,.!?！？）)」』"]
        cuts.append(near[0] if near else target)
    bounds = [0, *cuts, len(text)]
    return [text[a:b] for a, b in zip(bounds, bounds[1:])]


def _measure(font, lines: list[str]) -> tuple[int, int, int]:
    """(가장 긴 줄 폭, 한 줄 높이, 위쪽 여백). 높이는 글꼴 자체 높이라 줄마다 같다."""
    widths, top, bottom = [], 0, 0
    for line in lines:
        l, t, r, b = font.getbbox(line or " ")
        widths.append(r - l)
        top, bottom = t, b
    return max(widths), bottom - top, top


def _left_bearing(font, line: str) -> int:
    return font.getbbox(line or " ")[0]


def _fit(language: str, text: str, line_h: int, max_lines: int, avail_w: int, bold: bool):
    """글자 높이를 원래 줄 높이에 맞추고, 폭이 넘치면 줄 수를 늘리거나 크기를 줄인다. (font, lines, w, h, top, scale)"""
    size = max(6, line_h)
    font = fonts.load_font(language, size, bold)
    _, h, _ = _measure(font, [text])
    size = max(6, round(size * line_h / max(1, h)))        # 실제 글자 높이가 원래 줄 높이와 같도록 보정
    best = None
    for n_lines in range(1, max_lines + 1):
        lines = _split(text, n_lines)
        scale = 1.0
        while scale >= MIN_SCALE:
            font = fonts.load_font(language, round(size * scale), bold)
            w, h, top = _measure(font, lines)
            if w <= avail_w:
                return font, lines, w, h, top, scale
            if best is None or w < best[2]:
                best = (font, lines, w, h, top, scale)
            scale -= 0.08
    return best


def _draw(img: Image.Image, arr: np.ndarray, region: Region, text: str, language: str) -> tuple[bool, str]:
    """지운 자리에 현지어 글자를 그린다. (성공 여부, 비고)"""
    draw = ImageDraw.Draw(img)
    t = region.tight
    line_h = max(6, round(t.h / t.lines))
    left, right = _free_span(arr, region.bg, t.y0, t.y1, t.x0, t.x1)
    centered = abs((t.x0 + t.x1) / 2 - img.width / 2) < img.width * 0.02   # 손으로 가운데 맞춘 글자는 오차가 몇 px 이내다
    if centered:                                            # 가운데 정렬은 양쪽 여유 중 작은 쪽을 기준으로 대칭 확보
        room = min((t.x0 + t.x1) / 2 - left, right - (t.x0 + t.x1) / 2)
        avail = int(room * 2)
    else:
        avail = right - t.x0
    # 아래쪽에 빈 줄이 있으면 한 줄 더 쓸 수 있다
    max_lines = t.lines
    if max_lines == 1 and _rows_free(arr, region.bg, t.y1 + 2, t.y1 + 2 + int(line_h * 1.15), left, right):
        max_lines = 2
    font, lines, w, h, top, scale = _fit(language, text, line_h, max_lines, avail, region.bold)
    gap = int(h * 0.15)
    total_h = h * len(lines) + gap * (len(lines) - 1)
    y = (t.y0 + t.y1) / 2 - total_h / 2 if len(lines) <= t.lines else t.y0
    note = ""
    if w > avail:
        note = "글자가 길어 원래 자리보다 넓게 들어갔습니다. 확인하세요"
    elif scale < 0.999:
        note = f"현지어가 길어 글자를 {round(scale * 100)}% 크기로 줄였습니다"
    elif len(lines) > t.lines:
        note = "현지어가 길어 두 줄로 나눴습니다"
    for i, line in enumerate(lines):
        lw, _, _ = _measure(font, [line])
        x = (t.x0 + t.x1) / 2 - lw / 2 if centered else t.x0
        x = min(max(2, x), img.width - lw - 2) - _left_bearing(font, line)
        draw.text((x, y - top + i * (h + gap)), line, font=font, fill=region.ink)
    return True, note


# ── 한 이미지 처리 ──────────────────────────────────────────
def replace_texts(item: MediaItem, guide: ImageGuide, boxes: list[TextBox], language: str) -> OverlayResult:
    img = open_image(item.jpeg)
    W, H = img.size
    px_boxes = [Box(b.text_ko, round(b.x0 * W / 1000), round(b.y0 * H / 1000), round(b.x1 * W / 1000), round(b.y1 * H / 1000))
                for b in boxes if b.image_id == item.id]
    matched = match_boxes([r.original_ko for r in guide.text_replacements if r.original_ko], px_boxes)
    result = OverlayResult(image_id=item.id, jpeg=None)
    changed = False
    for r in guide.text_replacements:
        if not r.original_ko:
            result.replacements.append(Replacement(r.original_ko, r.localized, "건너뜀: 새로 넣는 문구는 자리가 없어 디자이너가 배치해야 합니다"))
            continue
        if r.original_ko not in matched:
            result.replacements.append(Replacement(r.original_ko, r.localized, "건너뜀: 이미지에서 이 문구의 위치를 찾지 못했습니다"))
            continue
        arr = np.asarray(img)
        for box in matched[r.original_ko]:
            region = analyze(arr, box)
            if region is None:
                result.replacements.append(Replacement(r.original_ko, r.localized, "건너뜀: 상자 안에서 글자를 찾지 못했습니다"))
                continue
            if not region.flat:
                result.replacements.append(Replacement(r.original_ko, r.localized, "건너뜀: 배경이 단색이 아니라(사진·그라데이션) 자연스럽게 지울 수 없습니다"))
                continue
            t = region.tight
            ImageDraw.Draw(img).rectangle((t.x0 - ERASE_PAD, t.y0 - ERASE_PAD, t.x1 + ERASE_PAD, t.y1 + ERASE_PAD), fill=region.bg)
            changed = True
            if not r.localized.strip():
                result.replacements.append(Replacement(r.original_ko, r.localized, "삭제"))
                continue
            arr = np.asarray(img)
            _, note = _draw(img, arr, region, r.localized.strip(), language)
            result.replacements.append(Replacement(r.original_ko, r.localized, "교체", note))
    if changed:
        result.jpeg = _encode(img)
    return result


def eligible(guides: list[ImageGuide], media_ids: set[str]) -> list[ImageGuide]:
    """자동 교체를 시도할 지시서: 기존 이미지이고, 문구 교체가 있고, 삭제·새로 추가가 아닌 것."""
    return [g for g in guides if g.image_id in media_ids and g.action in AUTO_ACTIONS
            and any(r.original_ko for r in g.text_replacements)]


def overlay_all(media: list[MediaItem], guides: list[ImageGuide], boxes: list[TextBox], language: str) -> dict[str, OverlayResult]:
    by_id = {m.id: m for m in media}
    return {g.image_id: replace_texts(by_id[g.image_id], g, boxes, language) for g in eligible(guides, set(by_id))}
