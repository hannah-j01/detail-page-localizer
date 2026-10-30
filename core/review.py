"""AI 없이 코드로 하는 검수(비용 0원).

1. 규칙 검수: config/risk_rules 의 위험 표현 패턴을 찾는다.
   - 현지화 문구(텍스트 항목 + 이미지 지시서 교체 문구)
   - 원본(한국어 입력 텍스트 + 이미지에서 읽은 한국어 문구)
2. 근거 검수: 각 문구가 실제로 존재하는 '확인된 사실'에 연결되어 있는지 확인한다.
3. 숫자 검수: 결과물에 나온 '숫자+단위'가 입력 자료에 같은 값·같은 단위로 있는지 확인한다(core/numbers.py).
   번역 과정의 숫자 오류(함량·용량·기간)는 일반 문구 오류보다 피해가 크기 때문이다.

사용자가 문구를 직접 고친 뒤에도 다시 돌릴 수 있도록 final_texts 로 최종 문구를 받는다.
"""
import re

from core.numbers import find_unknown, source_quantities
from core.schemas import Extraction, GeneratedPage, ProductInput, ReviewFlag

ORIGINAL_ITEM_ID = "원본"


def _find(pattern: str, text: str) -> list[str]:
    return [m.group(0) for m in re.finditer(pattern, text, flags=re.IGNORECASE)]


def localized_targets(
    page: GeneratedPage, final_texts: dict[str, str] | None = None,
) -> list[tuple[str, str]]:
    """(항목 ID, 현지화 문구) 목록. 텍스트 항목은 사용자가 고친 문구를 우선한다."""
    final_texts = final_texts or {}
    targets = [(i.id, "\n".join([final_texts.get(i.id, i.localized), *i.variants])) for i in page.items]
    for guide in page.image_guides:
        targets.append((guide.image_id, "\n".join(r.localized for r in guide.text_replacements)))
    return targets


def original_targets(product: ProductInput, extraction: Extraction) -> list[tuple[str, str]]:
    targets = [(ORIGINAL_ITEM_ID, "\n".join([product.features, product.description_ko, product.page_text]))]
    for image in extraction.images:
        targets.append((f"{ORIGINAL_ITEM_ID} {image.image_id}", "\n".join(image.texts_ko)))
    return targets


def rule_review(
    page: GeneratedPage,
    rules: list[dict],
    product: ProductInput,
    final_texts: dict[str, str] | None = None,
    extraction: Extraction | None = None,
) -> list[ReviewFlag]:
    flags: list[ReviewFlag] = []
    seen: set[tuple[str, str]] = set()
    targets = {
        "localized": localized_targets(page, final_texts),
        "original": original_targets(product, extraction or Extraction(product_summary="", facts=[], questions=[], images=[])),
    }
    for rule in rules:
        for item_id, text in targets[rule["target"]]:
            for match in _find(rule["pattern"], text):
                if (item_id, match) in seen:
                    continue
                seen.add((item_id, match))
                flags.append(ReviewFlag(
                    source="규칙", item_id=item_id, expression=match,
                    risk=f"{rule['reason']} ({rule['ruleset']})",
                    suggestion=rule["suggestion"], severity=rule["severity"],
                ))
    return flags


def evidence_review(page: GeneratedPage, extraction: Extraction) -> list[ReviewFlag]:
    facts = {f.id: f for f in extraction.facts}
    flags: list[ReviewFlag] = []
    linked = [(i.id, i.fact_ids, i.is_ai_suggestion, i.localized) for i in page.items]
    linked += [(g.image_id, g.fact_ids, False, g.reason) for g in page.image_guides if g.action != "삭제"]
    for item_id, fact_ids, is_ai_suggestion, text in linked:
        missing = [fid for fid in fact_ids if fid not in facts]
        if missing:
            flags.append(ReviewFlag(
                source="근거", item_id=item_id, expression=", ".join(missing),
                risk="존재하지 않는 사실 ID를 근거로 들고 있다. AI가 근거를 지어냈을 수 있다.",
                suggestion="문구의 근거를 직접 확인하고, 근거가 없으면 삭제한다",
                severity="높음",
            ))
        if not is_ai_suggestion and not fact_ids:
            flags.append(ReviewFlag(
                source="근거", item_id=item_id, expression=text[:30],
                risk="사실로 표시되었지만 연결된 근거가 없다.",
                suggestion="근거 사실을 연결하거나 'AI 제안'으로 취급한다",
                severity="중간",
            ))
        for fid in fact_ids:
            fact = facts.get(fid)
            if fact and fact.needs_evidence:
                flags.append(ReviewFlag(
                    source="근거", item_id=item_id, expression=f"{fid}: {fact.content}",
                    risk="증빙 자료가 확인되지 않은 주장을 근거로 사용했다.",
                    suggestion="증빙 자료를 확인한 뒤 사용하거나 문구에서 제외한다",
                    severity="중간",
                ))
    return flags


def number_review(
    page: GeneratedPage, extraction: Extraction, product: ProductInput, final_texts: dict[str, str] | None = None,
) -> list[ReviewFlag]:
    """현지화 문구의 '숫자+단위'가 입력 자료(텍스트·이미지 문구·사실)에 없으면 표시한다.

    단위가 붙은 숫자는 값과 단위가 모두 같아야 통과한다(예: 입력의 '5)'로 결과물의 '5%'가 통과하지 않음).
    """
    sources = [*product.model_dump().values(), *(f.content for f in extraction.facts)]
    sources += [t for image in extraction.images for t in image.texts_ko]
    known = source_quantities(sources)
    flags = []
    for item_id, text in localized_targets(page, final_texts):
        for q, same_value in find_unknown(text, known):
            if same_value:
                others = ", ".join(sorted(k.label() if k.unit else f"{k.value}(단위 없음)" for k in same_value))
                risk = f"입력 자료에는 같은 숫자가 다른 단위로만 있다({others}). 단위를 잘못 옮겼거나 다른 맥락의 숫자일 수 있다."
            else:
                risk = "입력 자료 어디에도 없는 숫자다. 번역 중 생긴 오류이거나 근거 없는 수치일 수 있다."
            flags.append(ReviewFlag(
                source="숫자", item_id=item_id, expression=q.label(), risk=risk,
                suggestion="원본의 함량·용량·기간과 대조해 고치거나 삭제한다",
                severity="높음",
            ))
    return flags


def full_review(
    page: GeneratedPage,
    extraction: Extraction,
    rules: list[dict],
    product: ProductInput,
    final_texts: dict[str, str] | None = None,
) -> list[ReviewFlag]:
    """AI 자체 점검 + 규칙 검수 + 근거 검수 + 숫자 검수를 합친다."""
    ai_flags = [ReviewFlag(source="AI", **f.model_dump()) for f in page.risk_flags]
    return (
        ai_flags
        + rule_review(page, rules, product, final_texts, extraction)
        + evidence_review(page, extraction)
        + number_review(page, extraction, product, final_texts)
    )
