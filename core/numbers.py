"""텍스트에서 '숫자 + 단위'를 읽어 언어와 관계없이 비교할 수 있는 형태로 만든다.

예) '36개월', '36ヶ月', '36 months' → Quantity("36", "month")
    'pH 5.5', 'pH5.5'                → Quantity("5.5", "ph")
    '두 병째'(입력 자료)              → Quantity("2", "bottle")
단위 목록은 config/units.yaml 에서 관리한다.
"""
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

UNITS_FILE = Path(__file__).resolve().parent.parent / "config" / "units.yaml"
NUMBER = re.compile(r"\d+(?:\.\d+)?")


@dataclass(frozen=True)
class Quantity:
    value: str
    unit: str | None          # None 이면 단위 없는 숫자

    def label(self) -> str:
        if not self.unit:
            return self.value
        spec = _config()["units"][self.unit]
        return f"{spec['label']} {self.value}" if "suffix" not in spec else self.value + spec["label"]


@lru_cache
def _config() -> dict:
    return yaml.safe_load(UNITS_FILE.read_text(encoding="utf-8"))


@lru_cache
def _aliases(kind: str) -> list[tuple[str, str]]:
    """(표기, 단위) 목록. 긴 표기부터 검사해야 '개월'이 '개'보다 먼저 잡힌다."""
    pairs = [(alias.lower(), unit) for unit, spec in _config()["units"].items() for alias in spec.get(kind, [])]
    return sorted(pairs, key=lambda p: -len(p[0]))


@lru_cache
def _number_word_pattern() -> re.Pattern:
    words = sorted(_config()["korean_number_words"], key=len, reverse=True)
    counters = sorted({a for a, _ in _aliases("suffix") if re.fullmatch(r"[가-힣]+", a)}, key=len, reverse=True)
    return re.compile(rf"(?<![가-힣])({'|'.join(words)})\s?(?=({'|'.join(counters)}))")


def _is_latin(alias: str) -> bool:
    return alias[-1].isascii() and alias[-1].isalpha()


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)          # 전각 '７０％' → '70%', '㎖' → 'ml'
    return re.sub(r"(?<=\d),(?=\d{3})", "", text)       # '1,980' → '1980'


def convert_number_words(text: str) -> str:
    """입력 자료의 한국어 숫자 단어를 숫자로 바꾼다. '두 병째' → '2병째'."""
    words = _config()["korean_number_words"]
    return _number_word_pattern().sub(lambda m: str(words[m.group(1)]), text)


def _suffix_unit(after: str) -> str | None:
    rest = after[1:] if after.startswith(" ") else after
    lowered = rest.lower()
    for alias, unit in _aliases("suffix"):
        if lowered.startswith(alias):
            following = rest[len(alias):len(alias) + 1]
            if not (_is_latin(alias) and following.isascii() and following.isalpha()):
                return unit
    return None


def _prefix_unit(before: str) -> str | None:
    rest = before[:-1] if before.endswith(" ") else before
    lowered = rest.lower()
    for alias, unit in _aliases("prefix"):
        if lowered.endswith(alias):
            preceding = rest[-len(alias) - 1:-len(alias)]
            if not (_is_latin(alias) and preceding.isascii() and preceding.isalpha()):
                return unit
    return None


def quantities(text: str) -> set[Quantity]:
    text = normalize(text)
    found = set()
    for m in NUMBER.finditer(text):
        unit = _suffix_unit(text[m.end():]) or _prefix_unit(text[:m.start()])
        found.add(Quantity(m.group(), unit))
    return found


def source_quantities(texts: list[str]) -> set[Quantity]:
    return quantities(convert_number_words("\n".join(texts)))


def is_ignored(q: Quantity) -> bool:
    ignore = _config()["ignore"]
    return q.unit in ignore["units"] or (q.value == "1" and q.unit in ignore["value_one_units"])


def find_unknown(text: str, known: set[Quantity]) -> list[tuple[Quantity, list[Quantity]]]:
    """입력 자료(known)에 없는 숫자와, 같은 값이지만 단위가 다른 입력 자료 숫자 목록."""
    results = []
    for q in sorted(quantities(text), key=lambda q: (q.value, q.unit or "")):
        if is_ignored(q):
            continue
        same_value = [k for k in known if k.value == q.value]
        matched = q in known if q.unit else bool(same_value)
        if not matched:
            results.append((q, same_value))
    return results
