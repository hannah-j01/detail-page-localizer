"""숫자+단위 대조 테스트."""
from core import pipeline
from core.numbers import Quantity, convert_number_words, find_unknown, quantities, source_quantities
from core.review import number_review
from core.schemas import Extraction


def q(value, unit=None):
    return Quantity(value, unit)


def test_units_across_languages_are_the_same():
    assert quantities("제조일로부터 36개월") == quantities("製造日から36ヶ月") == quantities("36 months") == {q("36", "month")}
    assert quantities("200ml") == quantities("２００ｍｌ") == quantities("200 ミリリットル") == {q("200", "ml")}
    assert quantities("pH 5.5 약산성") == quantities("pH5.5") == {q("5.5", "ph")}
    assert quantities("1,980円") == quantities("¥1980") == quantities("1,980엔") == {q("1980", "jpy")}


def test_longest_unit_and_latin_boundary():
    assert quantities("36개월") == {q("36", "month")}          # '개'(piece)가 아니라 '개월'
    assert quantities("200ml") == {q("200", "ml")}             # 'l'(리터)가 아니라 'ml'
    assert quantities("5 lovely days") == {q("5")}             # 'l' 뒤에 영문자가 이어지면 단위 아님
    assert quantities("후기 5) 용량") == {q("5")}


def test_korean_number_words_only_before_counters():
    assert convert_number_words("벌써 두 병째예요") == "벌써 2병째예요"
    assert convert_number_words("세안 후 사용") == "세안 후 사용"
    assert convert_number_words("모두 병에 담아") == "모두 병에 담아"
    assert q("2", "bottle") in source_quantities(["벌써 두 병째예요"])


def test_unit_mismatch_is_reported_with_the_other_unit():
    known = source_quantities(["1) 좋아요", "5) 용량 대비 가격", "용량 200ml"])
    unknown = find_unknown("5%配合 / 200ml / 200g", known)
    labels = {x.label(): [k.label() for k in others] for x, others in unknown}
    assert labels == {"5%": ["5"], "200g": ["200ml"]}


def test_bare_number_matches_any_unit_and_ignored_numbers():
    known = source_quantities(["어성초 77%"])
    assert find_unknown("ドクダミ77", known) == []                     # 단위 없는 숫자는 값만 비교
    assert find_unknown("STEP 3 / まずは1本お試し / 1枚目", known) == []  # 순서 번호·수량 1은 제외
    assert [x.label() for x, _ in find_unknown("3本セット", known)] == ["3병"]


def test_review_flags_wrong_unit_in_page():
    product = pipeline.load_demo_input()
    extraction, _ = pipeline.extract(product, [], "demo")
    strategy, _ = pipeline.recommend(product, extraction, "demo")
    page, _ = pipeline.generate(product, extraction, strategy.strategies, "demo")
    edited = {"S2": "ドクダミエキス5%配合", "S5": "内容量200g", "S10": "使用期限：製造日から36日"}
    flags = {(f.item_id, f.expression): f.risk for f in number_review(page, extraction, product, edited)}
    assert ("S2", "5%") in flags and "5(단위 없음)" in flags[("S2", "5%")]   # 후기 번호 '5)'로 통과하면 안 됨
    assert ("S5", "200g") in flags and "200ml" in flags[("S5", "200g")]
    assert ("S10", "36일") in flags and "36개월" in flags[("S10", "36일")]


def test_empty_extraction_still_uses_input_text():
    product = pipeline.load_demo_input()
    empty = Extraction(product_summary="", facts=[], questions=[], images=[])
    page, _ = pipeline.generate(product, empty, [], "demo")
    flags = {f.expression for f in number_review(page, empty, product)}
    assert "36개월" in flags            # 사용기한은 이미지에서만 확인된 정보 → 이미지 분석이 없으면 근거 없음
    assert "200ml" not in flags and "77%" not in flags
