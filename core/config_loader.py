"""config/ 폴더의 YAML 설정을 읽는다.

국가·쇼핑몰·카테고리·전략·위험 표현 규칙을 코드가 아닌 파일로 관리한다.
새 국가나 쇼핑몰은 해당 폴더에 YAML 파일을 하나 추가하면 된다.
"""
from functools import lru_cache
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
KINDS = ("countries", "marketplaces", "categories", "strategies", "risk_rules")


@lru_cache
def load_all() -> dict[str, dict[str, dict]]:
    """{종류: {id: 설정}} 형태로 모든 설정을 읽는다."""
    result: dict[str, dict[str, dict]] = {}
    for kind in KINDS:
        items = {}
        for path in sorted((CONFIG_DIR / kind).glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if data["id"] in items:
                raise ValueError(f"설정 id 중복: {kind}/{data['id']}")
            items[data["id"]] = data
        result[kind] = items
    return result


def marketplaces_for(country_id: str) -> dict[str, dict]:
    return {k: v for k, v in load_all()["marketplaces"].items() if v["country"] == country_id}


def risk_rules_for(country_id: str, category_id: str) -> list[dict]:
    """국가·카테고리에 해당하는 위험 표현 규칙 목록.

    'any' 는 모든 국가/카테고리에 적용한다는 뜻이다.
    각 규칙에는 검사 대상(target: localized 또는 original)과 규칙 묶음 이름(ruleset)을 붙인다.
    """
    rules = []
    for ruleset in load_all()["risk_rules"].values():
        applies = ruleset["applies_to"]
        if applies["country"] in ("any", country_id) and applies["category"] in ("any", category_id):
            for rule in ruleset["rules"]:
                rules.append({**rule, "target": ruleset.get("target", "localized"), "ruleset": ruleset["name"]})
    return rules


def context_for(country_id: str, marketplace_id: str, category_id: str) -> dict:
    """AI 프롬프트에 넣을 설정 묶음."""
    cfg = load_all()
    return {
        "country": cfg["countries"][country_id],
        "marketplace": cfg["marketplaces"][marketplace_id],
        "category": cfg["categories"][category_id],
        "strategies": list(cfg["strategies"].values()),
        "risk_rules": risk_rules_for(country_id, category_id),
    }
