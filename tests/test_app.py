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


def show_all(at: AppTest) -> AppTest:
    """'검수 필요 항목만 보기'를 꺼서 자동 수락된 항목의 편집 칸도 보이게 한다."""
    at = at.toggle(key="review_only_pending").set_value(False).run()
    assert not at.exception
    return at


@pytest.fixture(scope="module")
def reviewed() -> AppTest:
    at = AppTest.from_file(str(APP), default_timeout=120).run()
    assert not at.exception
    at = click(at, "단계별로 진행 (분석부터)")
    assert not at.exception and "extraction" in at.session_state
    at = click(at, "마케팅 전략 추천 받기")
    assert not at.exception and "strategy" in at.session_state
    at = click(at, "현지화 상세페이지 생성")
    assert not at.exception and "page" in at.session_state
    return at


def test_review_screen_auto_accepts_clean_items_and_shows_only_pending(reviewed: AppTest):
    at = reviewed
    page = at.session_state["page"]
    assert at.session_state["dec_S1"] == "미검토" and at.session_state["dec_S2"] == "수락"   # S1 은 AI 표시, S2 는 검수 통과
    assert at.session_state["gdec_I1"] == "수락"
    shown = {t.key[4:] for t in at.text_area if t.key and t.key.startswith("txt_")}
    assert shown == {"S1", "S3", "S4", "S17"}            # 검수 필요 항목만 편집 칸이 보인다
    html = at.get("html")[0].value
    assert 'class="dp"' in html and page.items[1].localized.split("\n")[0] in html   # 미리보기에는 자동 수락 항목도 보인다

    at = show_all(at)
    assert all(at.text_area(key=f"txt_{i.id}") for i in page.items)


def test_editing_text_updates_preview_and_marks_edited(reviewed: AppTest):
    at = show_all(reviewed)
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
    labels = [b.label for b in at.get("download_button")]
    assert "상세페이지 HTML" in labels and any(label.startswith("전체 묶음 ZIP") for label in labels)
    htmls = [e.value for e in at.get("html")]
    assert len(htmls) >= 2 and 'class="badge"' not in htmls[-1]      # 최종 미리보기에는 검수 표시가 없다


def test_image_order_controls_update_preview(reviewed: AppTest):
    at = show_all(reviewed)
    at.number_input(key="gord_I1").set_value(0).run()
    assert not at.exception and 'alt="I1"' not in at.get("html")[0].value
    at.number_input(key="gord_I1").set_value(9).run()
    html = at.get("html")[0].value
    assert html.index('alt="I4"') < html.index('alt="I1"')


def test_auto_image_replacement_feeds_preview(reviewed: AppTest):
    at = click(reviewed, "이미지 속 한국어 문구 자동 교체")
    assert not at.exception
    results = at.session_state["auto_images"]
    assert set(results) == {"I1", "I2", "I3", "I4"} and all(r.jpeg for r in results.values())
    html = at.get("html")[0].value
    assert 'alt="I1"' in html
    at.checkbox(key="guse_I1").set_value(False).run()           # 끄면 원본으로 돌아간다
    assert not at.exception


def test_one_click_runs_to_review_with_everything_folded():
    at = AppTest.from_file(str(APP), default_timeout=180).run()
    at = click(at, "한 번에 끝내기 (분석 → 전략 → 생성 → 검수)")
    assert not at.exception
    assert {"extraction", "strategy", "page", "auto_images", "auto_accepted"} <= set(at.session_state)
    assert at.session_state["one_click"] is True
    assert at.session_state["chosen_ids"] == [s.strategy_id for s in at.session_state["strategy"].strategies]
    titles = [e.label for e in at.expander]
    assert any(t.startswith("② 추출 결과 확인 — 자동 진행됨") for t in titles)
    assert any(t.startswith("③ 마케팅 전략 선택 — 추천 전략 모두 채택됨") for t in titles)
    assert any("사람이 볼 항목은 4개" in s.value for s in at.success)
    shown = {t.key[4:] for t in at.text_area if t.key and t.key.startswith("txt_")}
    assert shown == {"S1", "S3", "S4", "S17"}
