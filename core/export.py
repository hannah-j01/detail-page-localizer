"""검수를 마친 결과를 Markdown / JSON 으로 내보낸다.

decisions 는 {항목 ID: {"status": "수락"|"편집"|"거절"|"미검토", "text": 최종 문구}} 형태다.
guide_decisions 는 {이미지 ID: "수락"|"편집"|"거절"|"미검토"} 형태다.
이미지 지시서를 직접 고친 경우 page 에는 고친 지시서가 들어 있어야 한다(core/edits.py).
'수락'과 '편집' 항목만 최종 결과에 들어간다.
"""
import json
from collections import Counter

from core.review import ORIGINAL_ITEM_ID
from core.schemas import Extraction, GeneratedPage, ProductInput, ReviewFlag, StrategyResult

INCLUDED = ("수락", "편집")


def final_texts(page: GeneratedPage, decisions: dict) -> dict[str, str]:
    """화면에서 고친 문구까지 반영한 항목별 최종 문구(거절 항목 포함, 검수용)."""
    return {item.id: decisions.get(item.id, {}).get("text", item.localized) for item in page.items}


def compute_metrics(
    page: GeneratedPage, decisions: dict, guide_decisions: dict, strategy: StrategyResult,
    chosen_ids: list[str], elapsed_sec: float | None, usages: list[dict],
) -> dict:
    counts = Counter(decisions.get(i.id, {}).get("status", "미검토") for i in page.items)
    guide_counts = Counter(guide_decisions.get(g.image_id, "미검토") for g in page.image_guides)
    total = len(page.items)
    costs = [u["cost_usd"] for u in usages if u.get("cost_usd") is not None]
    return {
        "문구_총개수": total,
        "그대로_수락": counts["수락"],
        "직접_수정": counts["편집"],
        "거절": counts["거절"],
        "미검토": counts["미검토"],
        "그대로_수락_비율": round(counts["수락"] / total, 3) if total else 0,
        "이미지_지시서_수": len(page.image_guides),
        "이미지_지시서_수락": guide_counts["수락"],
        "이미지_지시서_직접_수정": guide_counts["편집"],
        "전략_추천수": len(strategy.strategies),
        "전략_채택수": len(chosen_ids),
        "작업_시간_초": round(elapsed_sec) if elapsed_sec is not None else None,
        "AI_호출_비용_USD": round(sum(costs), 4) if costs else 0,
    }


def _guide_lines(page: GeneratedPage, guide_decisions: dict) -> list[str]:
    guides = [g for g in page.image_guides if guide_decisions.get(g.image_id) in INCLUDED]
    if not guides:
        return []
    lines = ["---", "", "# 이미지 수정 지시서", "",
             "> 디자이너 전달용. '바꾸면 안 되는 요소'는 AI 이미지 편집 도구를 쓸 때 특히 확인하세요.", ""]
    for g in sorted(guides, key=lambda g: (g.recommended_order == 0, g.recommended_order)):
        order = "삭제" if g.action == "삭제" else f"{g.recommended_order}번째"
        edited = " · 직접 수정" if guide_decisions.get(g.image_id) == "편집" else ""
        lines += [f"## {g.image_id} — {g.action} ({order}{edited})", "", f"이유: {g.reason}", ""]
        if g.text_replacements:
            lines += ["| 기존 문구 | 바꿀 문구 | 역번역 |", "|---|---|---|"]
            for r in g.text_replacements:
                cells = [r.original_ko or "(새 문구)", r.localized or "(삭제)", r.back_translation_ko]
                lines.append("| " + " | ".join(c.replace("|", "／").replace("\n", " ") for c in cells) + " |")
            lines.append("")
        if g.edit_instructions:
            lines += ["수정 지시:"] + [f"- {x}" for x in g.edit_instructions] + [""]
        if g.keep_unchanged:
            lines += ["바꾸면 안 되는 요소:"] + [f"- {x}" for x in g.keep_unchanged] + [""]
    return lines


def to_markdown(
    product: ProductInput, page: GeneratedPage, decisions: dict, guide_decisions: dict,
    flags: list[ReviewFlag], extraction: Extraction, metrics: dict,
) -> str:
    lines = [f"# {product.name} — 현지화 상세페이지 ({page.language})", ""]
    lines += ["> 이 문서는 AI 초안을 사람이 검토한 결과입니다. 위험 표현 검수는 법률 검토를 대신하지 않습니다.", ""]

    current_section = None
    for item in page.items:
        decision = decisions.get(item.id, {})
        if decision.get("status") not in INCLUDED:
            continue
        if item.section != current_section:
            current_section = item.section
            lines += [f"## {item.section}", ""]
        lines += [decision.get("text", item.localized), ""]
        if item.variants:
            lines += ["테스트용 대안:"] + [f"- {v}" for v in item.variants] + [""]

    lines += _guide_lines(page, guide_decisions)

    included = {i for i, d in decisions.items() if d.get("status") in INCLUDED}
    included |= {g for g, status in guide_decisions.items() if status in INCLUDED}
    open_flags = [f for f in flags if f.item_id.startswith(ORIGINAL_ITEM_ID) or f.item_id in included]
    if open_flags:
        lines += ["---", "", "## 게시 전 확인할 위험 표현", ""]
        lines += [f"- [{f.severity}] ({f.source}) {f.item_id}: `{f.expression}` — {f.risk}" for f in open_flags]
        lines += [""]

    if page.missing_info or extraction.questions:
        lines += ["## 추가로 확인할 정보", ""]
        lines += [f"- {m}" for m in page.missing_info]
        lines += [f"- {q.id}. {q.question}" for q in extraction.questions]
        lines += [""]

    lines += ["## 작업 지표", ""] + [f"- {k}: {v}" for k, v in metrics.items()]
    return "\n".join(lines)


def to_json(
    product: ProductInput, extraction: Extraction, strategy: StrategyResult, chosen_ids: list[str],
    page: GeneratedPage, decisions: dict, guide_decisions: dict, flags: list[ReviewFlag], metrics: dict,
    media_labels: dict[str, str] | None = None,
) -> str:
    data = {
        "input": product.model_dump(),
        "media": media_labels or {},          # 이미지 ID → 출처 (이미지 파일 자체는 저장하지 않음)
        "extraction": extraction.model_dump(),
        "strategy": strategy.model_dump(),
        "chosen_strategy_ids": chosen_ids,
        "page": page.model_dump(),
        "decisions": decisions,
        "image_guide_decisions": guide_decisions,
        "review_flags": [f.model_dump() for f in flags],
        "metrics": metrics,
    }
    return json.dumps(data, ensure_ascii=False, indent=2)
