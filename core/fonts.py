"""이미지 속 문구 자동 교체에 쓸 글꼴을 찾는다(config/fonts.yaml).

로컬 글꼴(Windows 기본 글꼴) → 없으면 오픈소스 글꼴을 한 번 내려받아 캐시(~/.cache/detail_page_fonts).
내려받기는 사용자가 자동 교체를 눌렀을 때만 일어나고, 실패하면 한국어 오류 메시지를 낸다.
"""
import os
from pathlib import Path

import yaml
from PIL import ImageFont

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = Path(os.environ.get("FONT_CACHE_DIR") or Path.home() / ".cache" / "detail_page_fonts")
DOWNLOAD_TIMEOUT = 30
MAX_FONT_BYTES = 40_000_000


class FontError(Exception):
    """사용자에게 보여 줄 한국어 메시지를 담는 오류."""


def _config() -> dict:
    return yaml.safe_load((ROOT / "config" / "fonts.yaml").read_text(encoding="utf-8"))


def _download(entry: dict) -> Path:
    target = CACHE_DIR / entry["file"]
    if target.exists() and target.stat().st_size > 0:
        return target
    import requests   # 내려받을 때만 필요

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with requests.get(entry["url"], stream=True, timeout=DOWNLOAD_TIMEOUT) as r:
            r.raise_for_status()
            data = r.raw.read(MAX_FONT_BYTES + 1, decode_content=True)
    except Exception as e:
        raise FontError(f"글꼴을 내려받지 못했습니다({entry['file']}). 네트워크를 확인하거나 config/fonts.yaml 에 로컬 글꼴 경로를 추가하세요. ({e})")
    if len(data) > MAX_FONT_BYTES:
        raise FontError(f"글꼴 파일이 너무 큽니다({entry['file']}).")
    target.write_bytes(data)
    return target


def font_path(language: str, bold: bool = False) -> tuple[Path, bool]:
    """(글꼴 파일 경로, 가변 글꼴 여부). 언어 설정이 없으면 영어 글꼴을 쓴다."""
    cfg = _config()
    entry = cfg.get(language) or cfg["en"]
    candidates = entry.get("bold" if bold else "regular", []) + (entry.get("regular", []) if bold else [])
    for c in candidates:
        if Path(c).exists():
            return Path(c), False
    if "download" not in entry:
        raise FontError(f"'{language}' 언어의 글꼴을 찾지 못했습니다. config/fonts.yaml 을 확인하세요.")
    return _download(entry["download"]), True


def load_font(language: str, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path, variable = font_path(language, bold)
    font = ImageFont.truetype(str(path), max(1, int(size)))
    if variable:
        try:
            font.set_variation_by_axes([700 if bold else 400] if "wdth" not in path.name else [100, 700 if bold else 400])
        except Exception:
            pass   # 굵기 조절이 안 되면 기본 굵기로 그린다
    return font
