"""이미지·PDF 를 AI 가 읽기 좋은 크기의 조각으로 만든다.

한국 상세페이지는 폭 860px, 높이 1만 px 이상인 긴 이미지가 많다.
그대로 보내면 자동으로 축소되어 글자를 읽을 수 없으므로 세로로 나눈다.
이때 정해진 높이에서 기계적으로 자르면 글자 줄이 반으로 잘리므로,
목표 높이 근처에서 '가장 비어 있는 가로줄'(색 변화가 가장 적은 줄)을 찾아 그곳에서 자른다.
"""
import base64
import io
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageOps

MAX_WIDTH = 1000          # 이보다 넓으면 폭을 줄인다(글자 크기는 충분히 유지됨)
TARGET_HEIGHT = 1400      # 조각 하나의 목표 높이
SEARCH_WINDOW = 300       # 목표 높이 ±300px 안에서 자를 줄을 찾는다
MIN_HEIGHT = 500          # 이보다 짧은 마지막 조각은 앞 조각에 붙인다
MAX_LONG_EDGE = 1568      # Claude 가 축소 없이 읽는 긴 변 기준
MAX_SLICES = 24           # 상품 하나당 최대 조각 수(비용 보호)
MAX_PDF_PAGES = 10
PDF_RENDER_WIDTH = 1000


@dataclass
class MediaItem:
    id: str          # I1, I2 …
    label: str       # 화면에 보일 출처 설명
    jpeg: bytes
    width: int
    height: int

    @property
    def est_tokens(self) -> int:
        # Claude 이미지 토큰 근사치: (가로 × 세로) / 750
        return self.width * self.height // 750

    def b64(self) -> str:
        return base64.standard_b64encode(self.jpeg).decode("ascii")


class MediaError(Exception):
    """사용자에게 보여 줄 한국어 메시지를 담는 오류."""


def open_image(data: bytes) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(data))
        img.seek(0)                      # GIF 등은 첫 프레임만
        img = ImageOps.exif_transpose(img)
    except Exception:
        raise MediaError("이미지 파일을 열 수 없습니다. JPG, PNG, WEBP 파일인지 확인하세요.")
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        background = Image.new("RGB", img.size, "white")
        background.paste(img, mask=img.split()[-1])
        return background
    return img.convert("RGB")


def _cut_rows(gray: np.ndarray) -> list[int]:
    """자를 위치(행 번호) 목록. 각 위치는 목표 높이 근처에서 색 변화가 가장 적은 행이다."""
    height = gray.shape[0]
    row_noise = gray.std(axis=1)
    cuts, start = [], 0
    while height - start > TARGET_HEIGHT + MIN_HEIGHT:
        low = start + TARGET_HEIGHT - SEARCH_WINDOW
        high = min(start + TARGET_HEIGHT + SEARCH_WINDOW, height - MIN_HEIGHT)
        window = row_noise[low:high]
        # 같은 값이면 목표 높이에 가까운 행을 고른다
        distance = np.abs(np.arange(low, high) - (start + TARGET_HEIGHT)) / SEARCH_WINDOW
        cut = low + int(np.argmin(window + distance * 0.5))
        cuts.append(cut)
        start = cut
    return cuts


def _encode(img: Image.Image) -> bytes:
    if max(img.size) > MAX_LONG_EDGE:
        img = img.copy()
        img.thumbnail((MAX_LONG_EDGE, MAX_LONG_EDGE))
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=85, optimize=True)
    return buffer.getvalue()


def to_jpeg(data: bytes) -> bytes:
    """업로드한 교체 이미지(PNG·WEBP 등)를 미리보기·내보내기용 JPEG 로 바꾼다."""
    return _encode(open_image(data))


def normalize_width(img: Image.Image) -> Image.Image:
    if img.width > MAX_WIDTH:
        return img.resize((MAX_WIDTH, round(img.height * MAX_WIDTH / img.width)), Image.LANCZOS)
    return img


def slice_bounds(img: Image.Image) -> list[int]:
    """폭을 맞춘 이미지에서 조각 경계(행 번호) 목록. [0, cut1, cut2, …, height]"""
    img = normalize_width(img)
    gray = np.asarray(img.convert("L"), dtype=np.float32)
    return [0, *_cut_rows(gray), img.height]


def slice_image(img: Image.Image) -> list[Image.Image]:
    img = normalize_width(img)
    bounds = slice_bounds(img)
    return [img.crop((0, top, img.width, bottom)) for top, bottom in zip(bounds, bounds[1:])]


def pdf_to_images(data: bytes) -> list[Image.Image]:
    import pypdfium2 as pdfium   # PDF 를 올릴 때만 필요하므로 여기서 불러온다

    try:
        pdf = pdfium.PdfDocument(data)
    except Exception:
        raise MediaError("PDF 파일을 열 수 없습니다. 암호가 걸려 있거나 손상된 파일일 수 있습니다.")
    if len(pdf) > MAX_PDF_PAGES:
        raise MediaError(f"PDF 는 {MAX_PDF_PAGES}쪽까지만 올릴 수 있습니다(현재 {len(pdf)}쪽). 상세페이지 부분만 저장해 주세요.")
    pages = []
    for page in pdf:
        width_pt, _ = page.get_size()
        pages.append(page.render(scale=PDF_RENDER_WIDTH / width_pt).to_pil().convert("RGB"))
    return pages


def build_media(sources: list[tuple[str, Image.Image]]) -> list[MediaItem]:
    """(출처 이름, 이미지) 목록을 조각내 I1, I2 … ID 를 붙인다."""
    items: list[MediaItem] = []
    for name, img in sources:
        pieces = slice_image(img)
        for n, piece in enumerate(pieces, 1):
            label = name if len(pieces) == 1 else f"{name} ({n}/{len(pieces)})"
            jpeg = _encode(piece)
            w, h = Image.open(io.BytesIO(jpeg)).size
            items.append(MediaItem(f"I{len(items) + 1}", label, jpeg, w, h))
    if len(items) > MAX_SLICES:
        raise MediaError(
            f"이미지 조각이 {len(items)}개로 너무 많습니다(최대 {MAX_SLICES}개). "
            "상세페이지 중 현지화가 필요한 부분만 골라 주세요."
        )
    return items
