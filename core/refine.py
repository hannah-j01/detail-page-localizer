"""사람 손을 줄이는 두 장치: AI 자기검수 루프와 자동 승인 규칙.

1. 자기검수 루프(실제 AI 모드)
   생성 → 코드 검수(규칙·근거·숫자) + AI 자체 점검 → 걸린 항목만 AI 에게 다시 쓰게 함 → 다시 검수 → 최대 REFINE_ROUNDS 회.
   - 걸린 항목만 보내므로 생성 호출보다 훨씬 짧고, 이미지는 보내지 않는다.
   - 이전 회차에 보냈는데 표시가 그대로인 항목(AI 가 '사람 확인 필요'로 남긴 것)은 다시 보내지 않는다.
     새로 보낼 항목이 없으면 횟수가 남아도 멈춘다(헛호출 방지).
   - 데모 모드에서는 저장된 결과를 그대로 쓰므로 건너뛴다.

2. 자동 승인 규칙
   모든 검수를 통과한 항목은 처음부터 '수락' 상태로 두고, 사람은 '검수 필요' 항목만 본다.
   끝까지 사람이 보는 것: 검수 표시가 남은 항목, 이미지를 빼는 '삭제' 지시서.
"""
import os
from dataclasses import dataclass, field

from core import pipeline
from core.config_loader import risk_rules_for
from core.review import ORIGINAL_ITEM_ID, full_review
from core.schemas import Extraction, GeneratedPage, ProductInput, ReviewFlag, Revision, Strategy

DEFAULT_ROUNDS = 2


def refine_rounds() -> int:
    """환경 변수 REFINE_ROUNDS(기본 2). 0 이면 자기검수를 끈다."""
    try:
        return max(0, int(os.environ.get("REFINE_ROUNDS", DEFAULT_ROUNDS)))
    except ValueError:
        return DEFAULT_ROUNDS


def result_flags(flags: list[ReviewFlag]) -> list[ReviewFlag]:
    """원본(입력 자료)에 대한 표시를 뺀, 결과물에 대한 표시만."""
    return [f for f in flags if not f.item_id.startswith(ORIGINAL_ITEM_ID)]


def auto_decisions(page: GeneratedPage, flags: list[ReviewFlag]) -> tuple[set[str], set[str]]:
    """검수를 모두 통과해 자동 수락할 (문구 ID 집합, 이미지 지시서 ID 집합).

    - 위험·근거·숫자·AI 표시가 하나라도 있으면 사람이 본다.
    - '삭제' 지시서는 이미지를 빼는 결정이므로 사람이 본다.
    - AI 제안(사실 아님) 문구도 표시가 없으면 자동 수락한다. 사실 주장이 아니므로 위험은 규칙 검수가 맡는다.
    """
    flagged = {f.item_id for f in result_flags(flags)}
    items = {i.id for i in page.items if i.id not in flagged}
    guides = {g.image_id for g in page.image_guides if g.image_id not in flagged and g.action != "삭제"}
    return items, guides


@dataclass
class RefineRound:
    round: int
    flagged_before: list[str]     # 이 회차에 검수에 걸려 있던 항목 ID
    sent: list[str]               # 이 회차에 실제로 다시 쓰게 한 항목 ID
    flagged_after: list[str]      # 고친 뒤에도 검수에 걸린 항목 ID
    notes: list[str]              # AI 가 적은 변경 기록(한국어)


@dataclass
class RefineResult:
    page: GeneratedPage
    rounds: list[RefineRound] = field(default_factory=list)
    skipped: str = ""             # 자기검수를 건너뛴 이유. 비어 있으면 실행함


def merge(page: GeneratedPage, revision: Revision) -> GeneratedPage:
    """고쳐 받은 항목을 같은 ID 자리에 넣는다. 그 항목의 AI 자체 점검 표시는 새로 받은 remaining_flags 로 바꾼다.

    목록에 없던 ID(AI 가 새로 만든 항목)는 무시한다.
    """
    items = {i.id: i for i in revision.items}
    guides = {g.image_id: g for g in revision.image_guides}
    known = {i.id for i in page.items} | {g.image_id for g in page.image_guides}
    touched = (set(items) | set(guides)) & known
    return page.model_copy(update={
        "items": [items.get(i.id, i) for i in page.items],
        "image_guides": [guides.get(g.image_id, g) for g in page.image_guides],
        "risk_flags": [f for f in page.risk_flags if f.item_id not in touched]
        + [f for f in revision.remaining_flags if f.item_id in touched],
    })


def _signatures(flags: list[ReviewFlag]) -> dict[str, frozenset]:
    """항목 ID → 표시 묶음. 이전 회차와 같으면 AI 가 고칠 수 없다고 본 항목이다."""
    out: dict[str, set] = {}
    for f in flags:
        out.setdefault(f.item_id, set()).add((f.source, f.expression, f.risk))
    return {k: frozenset(v) for k, v in out.items()}


def generate_with_review(
    product: ProductInput, extraction: Extraction, chosen: list[Strategy], mode: str,
) -> tuple[RefineResult, list[dict]]:
    """생성 뒤 자기검수 루프까지 돈다. (결과, 호출별 사용량 목록) 을 돌려준다."""
    page, usage = pipeline.generate(product, extraction, chosen, mode)
    usages = [usage] if usage else []
    result = RefineResult(page=page)
    rounds = refine_rounds()
    if mode == "demo":
        result.skipped = "데모 모드에서는 저장된 결과를 그대로 보여 주므로 자기검수를 건너뜁니다."
        return result, usages
    if rounds == 0:
        result.skipped = "REFINE_ROUNDS=0 으로 자기검수가 꺼져 있습니다."
        return result, usages

    rules = risk_rules_for(product.country, product.category)
    already_sent: dict[str, frozenset] = {}
    for n in range(1, rounds + 1):
        flags = result_flags(full_review(result.page, extraction, rules, product))
        signatures = _signatures(flags)
        to_send = sorted(k for k, sig in signatures.items() if already_sent.get(k) != sig)
        if not to_send:
            break
        revision, usage = pipeline.revise(product, extraction, result.page, [f for f in flags if f.item_id in to_send])
        if usage:
            usages.append(usage)
        already_sent.update({k: signatures[k] for k in to_send})
        result.page = merge(result.page, revision)
        after = result_flags(full_review(result.page, extraction, rules, product))
        result.rounds.append(RefineRound(
            round=n, flagged_before=sorted(signatures), sent=to_send,
            flagged_after=sorted({f.item_id for f in after}), notes=list(revision.notes),
        ))
    return result, usages
