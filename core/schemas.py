"""서비스 전체에서 쓰는 데이터 구조.

AI 응답은 아래 모델 형식으로만 받는다(구조화 출력).
형식이 어긋나면 Pydantic이 오류를 내므로 잘못된 응답이 화면까지 오지 않는다.
"""
from typing import Literal

from pydantic import BaseModel, Field


# ── 사용자 입력 ─────────────────────────────────────────────
class ProductInput(BaseModel):
    name: str
    category: str                 # config/categories 의 id
    features: str                 # 주요 특징과 장점
    description_ko: str           # 기존 한국어 설명/상세페이지 문구
    country: str                  # config/countries 의 id
    marketplace: str              # config/marketplaces 의 id
    target_customer: str
    goal: str                     # 상세페이지 개선 목적
    price: str = ""
    specs: str = ""               # 소재·성분·규격·사용법
    reviews: str = ""
    certifications: str = ""      # 인증·효능 증빙 자료 설명
    brand_tone: str = ""
    source_url: str = ""          # 기존 상세페이지 URL
    page_text: str = ""           # URL 에서 가져온 페이지 텍스트(사용자가 정리 가능)


# 입력 칸의 한국어 이름. 화면과 AI 프롬프트에서 함께 쓴다.
FIELD_LABELS = {
    "name": "상품명",
    "category": "상품 카테고리",
    "features": "주요 특징과 장점",
    "description_ko": "기존 한국어 상품 설명/상세페이지 문구",
    "country": "판매 대상 국가",
    "marketplace": "판매할 쇼핑몰",
    "target_customer": "주요 타깃 고객",
    "goal": "상세페이지 개선 목적",
    "price": "가격",
    "specs": "소재·성분·규격·사용법",
    "reviews": "보유 고객 후기",
    "certifications": "인증·효능 증빙 자료",
    "brand_tone": "유지하고 싶은 말투·표현",
    "source_url": "기존 상세페이지 URL",
    "page_text": "URL 에서 가져온 페이지 텍스트",
}


# ── 단계 ① 상품 정보 추출 ────────────────────────────────────
FactType = Literal[
    "기본정보", "기능·특징", "고객 효익", "소재·성분·규격",
    "사용법", "주의사항", "핵심 메시지", "인증·효능 주장", "후기",
]


class Fact(BaseModel):
    id: str = Field(description="F1, F2 ... 형식의 고유 ID")
    type: FactType
    content: str = Field(description="입력에서 확인된 사실(한국어). 추측 금지")
    source: str = Field(description="이 사실이 나온 입력 위치. 예: '상세 설명', '후기'")
    needs_evidence: bool = Field(
        description="효능·인증·순위처럼 증빙이 필요한데 증빙 자료가 입력되지 않았으면 true"
    )


class Question(BaseModel):
    id: str = Field(description="Q1, Q2 ... 형식")
    question: str
    reason: str = Field(description="이 정보가 왜 필요한지")
    priority: Literal["높음", "중간", "낮음"]


ImageRole = Literal[
    "메인·첫인상", "성분·특징", "효능·주장", "사용법", "후기", "제품 정보", "브랜드 스토리", "기타",
]


class ImageAnalysis(BaseModel):
    image_id: str = Field(description="입력 이미지에 붙은 ID 그대로. 예: I1")
    role: ImageRole
    key_message: str = Field(description="이 이미지가 전달하는 핵심 메시지(한국어)")
    texts_ko: list[str] = Field(description="이미지 안의 한국어 문구를 원문 그대로 옮긴 목록")
    visual_elements: str = Field(description="제품 사진, 아이콘, 배지, 인증 마크 등 시각 요소 설명")
    issues: list[str] = Field(description="현지 고객이 이해하기 어렵거나 근거 확인이 필요한 부분. 없으면 빈 배열")


class Extraction(BaseModel):
    product_summary: str
    facts: list[Fact]
    questions: list[Question]
    images: list[ImageAnalysis] = Field(description="입력 이미지별 분석. 이미지가 없으면 빈 배열")


# ── 단계 ② 마케팅 전략 추천 ──────────────────────────────────
class Strategy(BaseModel):
    strategy_id: str = Field(description="config/strategies 에 있는 전략 id")
    title: str
    reason: str
    evidence_fact_ids: list[str]
    target_sections: list[str]
    expected_effect: str
    metrics: list[str]
    to_confirm: list[str]
    priority: int = Field(description="1이 가장 높음")


class StrategyResult(BaseModel):
    positioning: str = Field(description="이 시장에서의 한 줄 포지셔닝 가설")
    strategies: list[Strategy]
    caveats: list[str] = Field(description="이 추천의 한계와 가정")


# ── 단계 ③ 현지화 상세페이지 생성 ─────────────────────────────
Section = Literal[
    "상품명", "핵심 소구점", "상품 요약", "상세 설명", "사용법",
    "제품 사양", "FAQ", "이미지 문구", "검색 키워드", "CTA",
]


class PageItem(BaseModel):
    id: str = Field(description="S1, S2 ... 형식")
    section: Section
    original_ko: str = Field(description="대응하는 기존 한국어 문구. 새로 추가한 항목이면 빈 문자열")
    localized: str = Field(description="현지 언어로 작성한 최종 문구")
    back_translation_ko: str = Field(description="localized 를 한국어로 다시 옮긴 문장(검토용)")
    reason: str = Field(description="이렇게 바꾼 이유")
    fact_ids: list[str] = Field(description="근거가 된 사실 ID")
    strategy_ids: list[str]
    is_ai_suggestion: bool = Field(description="사실이 아니라 AI의 마케팅 제안이면 true")
    variants: list[str] = Field(description="A/B 테스트용 대안 문구(현지 언어). 없으면 빈 배열")


class RiskFlag(BaseModel):
    item_id: str
    expression: str
    risk: str
    suggestion: str
    severity: Literal["높음", "중간", "낮음"]


class TextReplacement(BaseModel):
    original_ko: str = Field(description="이미지 안의 기존 한국어 문구. 새로 넣는 문구면 빈 문자열")
    localized: str = Field(description="교체할 현지 언어 문구. 문구를 지워야 하면 빈 문자열")
    back_translation_ko: str


class ImageGuide(BaseModel):
    image_id: str = Field(description="기존 이미지 ID(I1 …). 새로 만들 이미지는 NEW1, NEW2 …")
    action: Literal["문구만 교체", "구성 수정", "삭제", "새로 추가"]
    recommended_order: int = Field(description="현지 상세페이지에서의 추천 순서. 삭제할 이미지는 0")
    text_replacements: list[TextReplacement]
    edit_instructions: list[str] = Field(description="디자이너에게 줄 레이아웃·가독성 수정 지시")
    keep_unchanged: list[str] = Field(description="바꾸면 안 되는 요소(제품 외형, 패키지, 인증 표시, 사양 수치 등)")
    reason: str
    fact_ids: list[str]


class GeneratedPage(BaseModel):
    language: str
    items: list[PageItem]
    image_guides: list[ImageGuide] = Field(description="입력 이미지가 있을 때 이미지별 수정 지시서. 없으면 빈 배열")
    risk_flags: list[RiskFlag] = Field(description="생성 직후 스스로 점검한 위험 표현")
    missing_info: list[str]


# ── 검수 결과(내부용, AI 응답 형식 아님) ───────────────────────
class ReviewFlag(BaseModel):
    source: Literal["AI", "규칙", "근거", "숫자"]
    item_id: str
    expression: str
    risk: str
    suggestion: str
    severity: Literal["높음", "중간", "낮음"]
