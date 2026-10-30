"""화면에서 사용자가 고친 이미지 지시서를 반영한다.

Streamlit 표 편집기(st.data_editor)는 '원본 대비 변경분'을 아래 형태로 기억한다.
    {"edited_rows": {행 번호: {열 이름: 새 값}}, "added_rows": [{열 이름: 값}], "deleted_rows": [행 번호]}
이 모듈은 화면과 분리된 순수 함수라서 테스트할 수 있다.
"""
from core.schemas import GeneratedPage, ImageGuide, TextReplacement

COL_ORIGINAL, COL_LOCALIZED, COL_BACK = "기존 문구", "바꿀 문구", "역번역"
EDITED_BACK = "(직접 수정 — 역번역 없음)"
ADDED_BACK = "(직접 추가 — 역번역 없음)"


def table_rows(guide: ImageGuide) -> list[dict]:
    """표 편집기에 보여 줄 원본 행. 바꿀 문구가 빈 칸이면 이미지에서 그 문구를 지운다는 뜻."""
    return [{COL_ORIGINAL: r.original_ko, COL_LOCALIZED: r.localized, COL_BACK: r.back_translation_ko}
            for r in guide.text_replacements]


def _lines(text: str) -> list[str]:
    return [line.strip().lstrip("-•").strip() for line in text.splitlines() if line.strip().lstrip("-•").strip()]


def apply_guide_edits(
    guide: ImageGuide,
    table_state: dict | None = None,
    instructions_text: str | None = None,
    keep_text: str | None = None,
) -> ImageGuide:
    """원본 지시서에 화면의 변경분을 반영한 새 지시서. 값이 None 이면 해당 부분은 원본 그대로."""
    table_state = table_state or {}
    rows = [r.model_copy() for r in guide.text_replacements]
    for index, change in table_state.get("edited_rows", {}).items():
        row = rows[int(index)]
        original = change.get(COL_ORIGINAL, row.original_ko) or ""
        localized = change.get(COL_LOCALIZED, row.localized) or ""
        if (original, localized) != (row.original_ko, row.localized):
            rows[int(index)] = TextReplacement(original_ko=original, localized=localized, back_translation_ko=EDITED_BACK)
    deleted = {int(i) for i in table_state.get("deleted_rows", [])}
    rows = [r for i, r in enumerate(rows) if i not in deleted]
    for added in table_state.get("added_rows", []):
        original, localized = added.get(COL_ORIGINAL) or "", added.get(COL_LOCALIZED) or ""
        if original or localized:
            rows.append(TextReplacement(original_ko=original, localized=localized, back_translation_ko=ADDED_BACK))

    update = {"text_replacements": rows}
    if instructions_text is not None:
        update["edit_instructions"] = _lines(instructions_text)
    if keep_text is not None:
        update["keep_unchanged"] = _lines(keep_text)
    return guide.model_copy(update=update)


def is_edited(original: ImageGuide, edited: ImageGuide) -> bool:
    return original.model_dump() != edited.model_dump()


def with_guides(page: GeneratedPage, guides: list[ImageGuide]) -> GeneratedPage:
    return page.model_copy(update={"image_guides": guides})
