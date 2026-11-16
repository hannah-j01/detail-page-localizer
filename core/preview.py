"""검수 중인 결과를 실제 상세페이지 모양의 HTML 로 그린다.

화면(app.py)과 내보내기 모두 이 모듈을 쓴다. Streamlit 과 분리된 순수 함수라서 테스트할 수 있다.
- decisions 는 {항목 ID: {"status": ..., "text": 최종 문구}} (core/export.py 와 같은 형태)
- guide_decisions 는 {이미지 ID: "수락"|"편집"|"거절"|"미검토"}
- images 는 {이미지 ID: JPEG bytes}. 없는 이미지는 자리 표시 상자로 그린다.
- include_unreviewed=True 면 거절만 빼고 모두 보여 준다(검수 중). False 면 수락·편집만 넣는다(내보내기).
- image_order 는 {이미지 ID: 순서}. 화면에서 바꾼 순서이며 0 은 상세페이지에서 뺀다는 뜻. 없으면 AI 추천 순서.
- replaced 는 디자이너가 완성한 교체 이미지를 올린 ID 집합. 이 이미지에는 '바꿀 문구' 표를 붙이지 않는다.
"""
import base64
import html
import re

from core.export import INCLUDED
from core.schemas import GeneratedPage, ImageGuide, ProductInput

# 실제 상세페이지에서 위에서 아래로 놓이는 순서. "이미지" 는 항목이 아니라 이미지 지시서 블록의 위치다.
SECTION_ORDER = ["상품명", "핵심 소구점", "상품 요약", "이미지", "이미지 문구", "상세 설명", "사용법", "제품 사양", "FAQ", "CTA", "검색 키워드"]

# 현지 고객이 보게 될 소제목. 없는 언어는 한국어 섹션 이름을 그대로 쓴다.
HEADINGS = {
    "ja": {"핵심 소구점": "特長", "이미지": "商品画像", "상세 설명": "商品詳細", "사용법": "ご使用方法",
           "제품 사양": "商品仕様", "FAQ": "よくあるご質問", "검색 키워드": "検索キーワード"},
    "zh": {"핵심 소구점": "产品亮点", "이미지": "商品图片", "상세 설명": "商品详情", "사용법": "使用方法",
           "제품 사양": "产品规格", "FAQ": "常见问题", "검색 키워드": "搜索关键词"},
    "en": {"핵심 소구점": "Highlights", "이미지": "Product Images", "상세 설명": "Product Details", "사용법": "How to Use",
           "제품 사양": "Specifications", "FAQ": "FAQ", "검색 키워드": "Search Keywords"},
}
STATUS_LABEL = {"미검토": "미검토", "편집": "직접 수정"}

CSS = """
.dp{max-width:720px;margin:0 auto;padding:28px 24px 40px;background:#fff;color:#1f2328;
  font-family:-apple-system,"Segoe UI","Hiragino Sans","Noto Sans JP","Noto Sans SC","Noto Sans KR",sans-serif;
  font-size:15px;line-height:1.7;border:1px solid #e5e7eb;border-radius:12px}
.dp *{box-sizing:border-box}
.dp h1{font-size:22px;line-height:1.4;margin:0 0 14px;font-weight:700}
.dp h2{font-size:16px;margin:30px 0 10px;padding-bottom:6px;border-bottom:2px solid #1f2328;font-weight:700}
.dp p{margin:0 0 10px}
.dp .lead{font-size:16px;color:#374151;padding:14px 16px;background:#f6f8fa;border-radius:8px}
.dp ul.points{list-style:none;padding:0;margin:0 0 6px}
.dp ul.points li{position:relative;padding-left:26px;margin-bottom:8px}
.dp ul.points li::before{content:"✓";position:absolute;left:0;top:0;color:#0a7c42;font-weight:700}
.dp table.spec{width:100%;border-collapse:collapse;margin-bottom:8px}
.dp table.spec th,.dp table.spec td{padding:8px 10px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}
.dp table.spec th{width:34%;background:#f6f8fa;font-weight:600}
.dp .faq{margin-bottom:12px}
.dp .faq .q{font-weight:700}
.dp .faq .a{color:#374151}
.dp .cta{display:block;margin:26px 0 8px;padding:14px 18px;background:#1f2328;color:#fff;text-align:center;
  border-radius:8px;font-weight:700;font-size:16px}
.dp .tags span{display:inline-block;margin:0 6px 6px 0;padding:2px 10px;background:#eef2f7;border-radius:999px;font-size:13px;color:#374151}
.dp .hint{font-size:12px;color:#6b7280}
.dp figure{margin:0 0 22px}
.dp figure img{width:100%;display:block;border-radius:6px;border:1px solid #e5e7eb}
.dp figure .new{padding:36px 18px;border:2px dashed #9ca3af;border-radius:6px;text-align:center;color:#6b7280;background:#fafafa}
.dp figcaption{margin-top:6px;font-size:12.5px;color:#4b5563}
.dp figcaption .act{display:inline-block;padding:1px 8px;border-radius:4px;background:#fff3cd;color:#7a5a00;font-weight:600;margin-right:6px}
.dp figcaption .act.done{background:#dcfce7;color:#166534}
.dp table.rep{width:100%;border-collapse:collapse;margin-top:6px;font-size:12.5px}
.dp table.rep td{padding:4px 6px;border-bottom:1px solid #eee;vertical-align:top}
.dp table.rep td.ko{color:#9ca3af;text-decoration:line-through;width:45%}
.dp table.rep td.arrow{width:20px;color:#9ca3af;text-align:center}
.dp .item{position:relative}
.dp .item.unreviewed{outline:1px dashed #d1d5db;outline-offset:4px;border-radius:4px}
.dp .item.edited{outline:1px solid #86efac;outline-offset:4px;border-radius:4px}
.dp .badge{position:absolute;right:-4px;top:-14px;font-size:10px;padding:0 6px;border-radius:3px;background:#f3f4f6;color:#6b7280}
.dp .item.edited .badge{background:#dcfce7;color:#166534}
"""


def _esc(text: str) -> str:
    return html.escape(text or "", quote=True)


def _br(text: str) -> str:
    return "<br>".join(_esc(line) for line in (text or "").splitlines())


def _heading(language: str, section: str) -> str:
    return HEADINGS.get((language or "")[:2], {}).get(section, section)


def _wrap(status: str, inner: str, include_unreviewed: bool) -> str:
    """검수 중에는 미검토·직접 수정 항목에 작은 표시를 붙인다."""
    label = STATUS_LABEL.get(status) if include_unreviewed else None
    if not label:
        return inner
    cls = "unreviewed" if status == "미검토" else "edited"
    return f'<div class="item {cls}"><span class="badge">{label}</span>{inner}</div>'


def _spec_rows(text: str) -> str:
    rows = []
    for line in (text or "").splitlines():
        line = line.strip().lstrip("-•・ ").strip()
        if not line:
            continue
        m = re.match(r"^(.{1,24}?)\s*[:：]\s*(.+)$", line)
        if m:
            rows.append(f"<tr><th>{_esc(m.group(1))}</th><td>{_esc(m.group(2))}</td></tr>")
        else:
            rows.append(f'<tr><td colspan="2">{_esc(line)}</td></tr>')
    return f'<table class="spec">{"".join(rows)}</table>' if rows else ""


def _faq(text: str) -> str:
    q, a = [], []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if re.match(r"^(Q|質問|问|問)\s*[.．:：]", s, re.I):
            q.append(re.sub(r"^(Q|質問|问|問)\s*[.．:：]\s*", "", s, flags=re.I))
        elif re.match(r"^(A|回答|答)\s*[.．:：]", s, re.I):
            a.append(re.sub(r"^(A|回答|答)\s*[.．:：]\s*", "", s, flags=re.I))
        else:
            (a if q else q).append(s)
    if not q and not a:
        return ""
    return (f'<div class="faq"><div class="q">Q. {_esc(" ".join(q))}</div>'
            f'<div class="a">A. {_esc(" ".join(a))}</div></div>')


def _tags(text: str) -> str:
    words = [w.strip() for w in re.split(r"[,，、\n/]", text or "") if w.strip()]
    return '<div class="tags">' + "".join(f"<span>{_esc(w)}</span>" for w in words) + "</div>" if words else ""


def final_guides(
    page: GeneratedPage, guide_decisions: dict, include_unreviewed: bool, image_order: dict[str, int] | None = None,
) -> list[ImageGuide]:
    """상세페이지에 실제로 들어갈 이미지 지시서를 순서대로. 삭제 권장·순서 0·거절(또는 미검토) 은 뺀다."""
    image_order = image_order or {}
    chosen = []
    for g in page.image_guides:
        order = image_order.get(g.image_id, g.recommended_order)
        if g.image_id not in image_order and g.action == "삭제":
            continue
        if order <= 0 or not _visible(guide_decisions.get(g.image_id, "미검토"), include_unreviewed):
            continue
        chosen.append((order, g))
    return [g for _, g in sorted(chosen, key=lambda x: x[0])]


def final_images(
    page: GeneratedPage, guide_decisions: dict, images: dict[str, bytes], *,
    include_unreviewed: bool = False, image_order: dict[str, int] | None = None,
) -> list[tuple[str, bytes]]:
    """내보낼 이미지 파일 목록 [(파일 이름, JPEG)] — 순서가 파일 이름 앞에 붙는다. 아직 파일이 없는 새 이미지는 뺀다."""
    guides = [g for g in final_guides(page, guide_decisions, include_unreviewed, image_order) if g.image_id in images]
    return [(f"{n:02d}_{g.image_id}.jpg", images[g.image_id]) for n, g in enumerate(guides, 1)]


def _image_block(g: ImageGuide, jpeg: bytes | None, status: str, include_unreviewed: bool, replaced: bool = False) -> str:
    if jpeg:
        src = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
        body = f'<img src="{src}" alt="{_esc(g.image_id)}">'
    else:
        lines = "".join(f"<div>{_esc(x)}</div>" for x in g.edit_instructions[:3])
        body = f'<div class="new"><strong>🆕 {_esc(g.image_id)} 새로 만들 이미지</strong>{lines}</div>'
    reps = [r for r in g.text_replacements if r.original_ko or r.localized]
    rows = "".join(
        f'<tr><td class="ko">{_esc(r.original_ko) or "(새 문구)"}</td><td class="arrow">→</td>'
        f'<td>{_esc(r.localized) or "<em>(삭제)</em>"}</td></tr>' for r in reps
    )
    table = f'<table class="rep">{rows}</table>' if rows else ""
    if replaced:
        caption = f'<figcaption><span class="act done">교체 이미지</span>{_esc(g.image_id)} 자리에 올린 완성 이미지입니다.</figcaption>'
    else:
        caption = f'<figcaption><span class="act">{_esc(g.action)}</span>{_esc(g.reason)}{table}</figcaption>'
    return _wrap(status, f"<figure>{body}{caption}</figure>", include_unreviewed)


def _visible(status: str, include_unreviewed: bool) -> bool:
    return status in INCLUDED or (include_unreviewed and status != "거절")


def render_preview(
    product: ProductInput, page: GeneratedPage, decisions: dict, guide_decisions: dict,
    images: dict[str, bytes] | None = None, *, include_unreviewed: bool = True, full_document: bool = False,
    image_order: dict[str, int] | None = None, replaced: set[str] | None = None,
) -> str:
    """현재 검수 상태를 반영한 상세페이지 HTML 조각(또는 full_document=True 면 완전한 HTML 문서)."""
    images, replaced = images or {}, replaced or set()
    lang = page.language or ""
    by_section: dict[str, list[tuple[str, str]]] = {}
    for item in page.items:
        d = decisions.get(item.id, {})
        status, text = d.get("status", "미검토"), d.get("text", item.localized)
        if _visible(status, include_unreviewed) and text.strip():
            by_section.setdefault(item.section, []).append((status, text))

    guides = final_guides(page, guide_decisions, include_unreviewed, image_order)

    out = [f'<div class="dp" lang="{_esc(lang)}">']
    for section in SECTION_ORDER:
        entries = by_section.get(section, [])
        if section == "이미지":
            if guides:
                out.append(f"<h2>{_esc(_heading(lang, section))}</h2>")
                out += [_image_block(g, images.get(g.image_id), guide_decisions.get(g.image_id, "미검토"),
                                     include_unreviewed, g.image_id in replaced) for g in guides]
            continue
        if not entries:
            continue
        if section == "상품명":
            out += [_wrap(s, f"<h1>{_br(t)}</h1>", include_unreviewed) for s, t in entries]
        elif section == "핵심 소구점":
            out.append(f"<h2>{_esc(_heading(lang, section))}</h2><ul class='points'>")
            out += [_wrap(s, f"<li>{_br(t)}</li>", include_unreviewed) for s, t in entries]
            out.append("</ul>")
        elif section == "상품 요약":
            out += [_wrap(s, f'<p class="lead">{_br(t)}</p>', include_unreviewed) for s, t in entries]
        elif section == "이미지 문구":
            out.append(f"<p class='hint'>이미지에 들어갈 문구</p>")
            out += [_wrap(s, f"<p>{_br(t)}</p>", include_unreviewed) for s, t in entries]
        elif section == "제품 사양":
            out.append(f"<h2>{_esc(_heading(lang, section))}</h2>")
            out += [_wrap(s, _spec_rows(t) or f"<p>{_br(t)}</p>", include_unreviewed) for s, t in entries]
        elif section == "FAQ":
            out.append(f"<h2>{_esc(_heading(lang, section))}</h2>")
            out += [_wrap(s, _faq(t) or f"<p>{_br(t)}</p>", include_unreviewed) for s, t in entries]
        elif section == "CTA":
            out += [_wrap(s, f'<div class="cta">{_br(t)}</div>', include_unreviewed) for s, t in entries]
        elif section == "검색 키워드":
            out.append(f"<h2>{_esc(_heading(lang, section))}</h2>"
                       "<p class='hint'>쇼핑몰 검색용 항목으로, 고객 화면에는 보이지 않습니다.</p>")
            out += [_wrap(s, _tags(t) or f"<p>{_br(t)}</p>", include_unreviewed) for s, t in entries]
        else:  # 상세 설명, 사용법
            out.append(f"<h2>{_esc(_heading(lang, section))}</h2>")
            out += [_wrap(s, f"<p>{_br(t)}</p>", include_unreviewed) for s, t in entries]
    out.append("</div>")
    fragment = f"<style>{CSS}</style>" + "".join(out)

    if not full_document:
        return fragment
    title = _esc(f"{product.name} — {lang}")
    return (f'<!doctype html><html lang="{_esc(lang)}"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title></head>'
            f'<body style="margin:0;padding:24px 12px;background:#f3f4f6">{fragment}</body></html>')
