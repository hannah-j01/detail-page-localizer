"""Streamlit 화면을 데모 모드로 끝까지 자동 실행해 오류가 없는지 확인한다(API 키 불필요)."""
import os
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

os.environ.pop("ANTHROPIC_API_KEY", None)       # 데모 모드로 강제
APP = Path(__file__).resolve().parent.parent / "app.py"


def click(at: AppTest, label: str) -> AppTest:
    button = next(b for b in at.button if b.label == label)
    return button.click().run()


@pytest.fixture(scope="module")
def reviewed() -> AppTest:
    at = AppTest.from_file(str(APP), default_timeout=120).run()
    assert not at.exception
    at = click(at, "상품 정보 분석하기")
    assert not at.exception and "extraction" in at.session_state
    at = click(at, "마케팅 전략 추천 받기")
    assert not at.exception and "strategy" in at.session_state
    at = click(at, "현지화 상세페이지 생성")
    assert not at.exception and "page" in at.session_state
    return at


def test_review_screen_renders_items_and_preview(reviewed: AppTest):
    at = reviewed
    page = at.session_state["page"]
    assert all(at.text_area(key=f"txt_{i.id}") for i in page.items)
    html = [e for e in at.get("html")]
    assert html and 'class="dp"' in html[0].value and page.items[0].localized.split("\n")[0] in html[0].value


def test_editing_text_updates_preview_and_marks_edited(reviewed: AppTest):
    at = reviewed
    at.text_area(key="txt_S2").set_value("テスト用に直接修正した文").run()
    at.radio(key="dec_S2").set_value("수락").run()
    assert not at.exception
    html = at.get("html")[0].value
    assert "テスト用に直接修正した文" in html and 'class="badge">직접 수정' in html
    at.radio(key="dec_S3").set_value("거절").run()
    html = at.get("html")[0].value
    assert next(i for i in at.session_state["page"].items if i.id == "S3").localized not in html


def test_accept_all_enables_export_with_final_preview(reviewed: AppTest):
    at = click(reviewed, "검토하지 않은 항목 모두 수락")
    assert not at.exception
    assert any(b.label == "Markdown 파일 다운로드" for b in at.get("download_button")) or \
        any("Markdown" in e.value for e in at.markdown)  # download_button 미지원 버전 대비
    htmls = [e.value for e in at.get("html")]
    assert len(htmls) >= 2 and 'class="badge"' not in htmls[-1]      # 최종 미리보기에는 검수 표시가 없다
