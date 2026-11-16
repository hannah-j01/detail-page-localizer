"""Claude API 호출을 한곳에 모은 모듈.

- API 키는 코드에 쓰지 않고 환경 변수(ANTHROPIC_API_KEY)에서만 읽는다.
- 응답은 Pydantic 모델 형식으로 받는다(구조화 출력).
- 호출마다 토큰 사용량과 예상 비용을 돌려준다(성공 지표: 상품당 AI 비용).
"""
import os

import anthropic
from pydantic import BaseModel

DEFAULT_MODEL = "claude-opus-5-5"

# 100만 토큰당 달러 가격(입력, 출력). 2026-11 기준 공식 가격(Haiku 는 10만 토큰 이하 프롬프트 기준). 가격이 바뀌면 여기만 고친다.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}


class LLMError(Exception):
    """사용자에게 보여 줄 한국어 메시지를 담는 오류."""


def current_model() -> str:
    return os.environ.get("CLAUDE_MODEL") or DEFAULT_MODEL


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    if model not in PRICES:
        return None
    price_in, price_out = PRICES[model]
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


def call_structured(system: str, content: str | list, output_model: type[BaseModel]) -> tuple[BaseModel, dict]:
    """content 는 텍스트 하나 또는 [이미지 블록…, 텍스트 블록] 목록."""
    model = current_model()
    client = anthropic.Anthropic()
    try:
        # 이미지 지시서까지 만들면 응답이 길어지므로 스트리밍으로 받는다(긴 응답의 시간 초과 방지).
        with client.beta.messages.stream(
            model=model,
            max_tokens=32000,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_format=output_model,
            # Opus 5.5 는 기본 effort 가 medium 이다. 긴 상세페이지 생성이므로 high 로 명시한다.
            output_config={"effort": "high"},
            # 안전 분류기가 요청을 거절하면 서버가 다른 모델로 자동 재시도한다.
            # fallbacks 를 지원하지 않는 모델(예: Haiku)에서는 무시되고 원래 모델만 사용된다.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            response = stream.get_final_message()
    except anthropic.AuthenticationError:
        raise LLMError("API 키가 올바르지 않습니다. .env 의 ANTHROPIC_API_KEY 를 확인하세요.")
    except anthropic.PermissionDeniedError:
        raise LLMError("이 API 키로는 해당 모델을 사용할 수 없습니다.")
    except anthropic.NotFoundError:
        raise LLMError(f"모델을 찾을 수 없습니다: {model}")
    except anthropic.RateLimitError:
        raise LLMError("요청 한도를 초과했습니다. 잠시 후 다시 시도하세요.")
    except anthropic.BadRequestError as e:
        raise LLMError(f"요청 형식 오류: {e.message}")
    except anthropic.APIStatusError as e:
        raise LLMError(f"Claude API 오류({e.status_code}). 잠시 후 다시 시도하세요.")
    except anthropic.APIConnectionError:
        raise LLMError("네트워크 연결을 확인하세요.")

    if response.stop_reason == "refusal":
        raise LLMError("AI가 이 요청에 대한 응답을 거절했습니다. 입력 내용을 확인하세요.")
    if response.stop_reason == "max_tokens":
        raise LLMError("응답이 너무 길어 중간에 잘렸습니다. 입력을 줄여 다시 시도하세요.")
    if response.parsed_output is None:
        raise LLMError("AI 응답을 정해진 형식으로 읽지 못했습니다. 다시 시도하세요.")

    usage = {
        "model": response.model,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    usage["cost_usd"] = estimate_cost(response.model, usage["input_tokens"], usage["output_tokens"])
    return response.parsed_output, usage
