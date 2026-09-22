"""회상 이미지 생성 - DALL-E 3 래퍼.

키가 없으면 데모용 플레이스홀더 이미지 URL을 돌려준다
(image-generation-loading → image-review 플로우를 그대로 확인 가능).
"""
import hashlib
from typing import Dict

from core.config import settings


def generate_image(prompt: str) -> Dict:
    """Returns {'image_url', 'engine', 'error'}."""
    if settings.openai_enabled:
        try:
            from openai import OpenAI

            client = OpenAI(api_key=settings.OPENAI_API_KEY)
            response = client.images.generate(
                model=settings.OPENAI_IMAGE_MODEL,
                prompt=prompt[:4000],
                size="1024x1024",
                quality="standard",
                n=1,
            )
            url = response.data[0].url
            print(f"[IMAGE] 생성 완료: {url[:60]}...")
            return {"image_url": url, "engine": settings.OPENAI_IMAGE_MODEL, "error": None}
        except Exception as exc:
            print(f"[IMAGE] DALL-E 생성 실패: {exc}")
            if not settings.USE_MOCK_ON_MISSING_KEY:
                return {"image_url": None, "engine": "none", "error": str(exc)}

    if settings.USE_MOCK_ON_MISSING_KEY:
        seed = hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:12]
        return {
            "image_url": f"https://picsum.photos/seed/{seed}/1024/1024",
            "engine": "mock",
            "error": None,
        }

    return {"image_url": None, "engine": "none", "error": "이미지 생성기를 사용할 수 없습니다."}
