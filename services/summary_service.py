"""상담 요약 생성 - summary-review 화면용.

OpenAI 키가 있으면 GPT로 요약/키워드를 만들고,
없으면 규칙 기반(발화 + 형태소 결과)으로 대체한다.
"""
import json
import re
from typing import Dict, List, Optional

from core.config import settings

_STOPWORDS = {
    "그것", "저것", "이것", "거기", "여기", "저기", "그거", "때문", "정도",
    "생각", "이야기", "얘기", "사람", "우리", "지금", "오늘", "그때", "동안",
}

SYSTEM_PROMPT = (
    "당신은 치매 어르신 회상 치료를 돕는 상담 보조 AI입니다. "
    "상담사가 검토할 초기 요약본을 작성합니다. "
    "어르신이 실제로 말한 내용만 근거로 삼고 추측하지 마세요. "
    "따뜻하지만 임상적으로 담백한 문체를 사용하고, "
    "자해·폭력 등 민감한 표현은 중립적으로 순화하세요."
)

USER_PROMPT = """다음은 상담 녹취록입니다.

[녹취록]
{transcript}

[형태소 분석으로 추출된 기억 요소]
{scene}

아래 JSON 형식으로만 답하세요.
{{
  "title": "기억 주제를 나타내는 8자 이내 제목",
  "summary": "3~5문장 요약. 어르신이 떠올린 장면과 정서 반응을 포함",
  "keywords": ["핵심 기억 키워드", "최대 6개"]
}}"""


def generate_summary(
    transcript: str,
    client_utterances: List[str],
    scene_description: str = "",
) -> Dict:
    """Returns {'title', 'summary', 'keywords', 'generated_by'}."""
    if settings.openai_enabled:
        result = _generate_with_openai(transcript, scene_description)
        if result:
            return result
    return _generate_rule_based(client_utterances or [transcript], scene_description)


def _generate_with_openai(transcript: str, scene: str) -> Optional[Dict]:
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        response = client.chat.completions.create(
            model=settings.OPENAI_TEXT_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": USER_PROMPT.format(
                        transcript=transcript[:6000], scene=scene or "(없음)"
                    ),
                },
            ],
            temperature=0.4,
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content)
        keywords = [str(k).strip() for k in data.get("keywords", []) if str(k).strip()]
        return {
            "title": (data.get("title") or "").strip()[:120] or None,
            "summary": (data.get("summary") or "").strip(),
            "keywords": keywords[:6],
            "generated_by": "openai",
        }
    except Exception as exc:
        print(f"[SUMMARY] OpenAI 요약 실패 - 규칙 기반으로 대체합니다: {exc}")
        return None


def _generate_rule_based(utterances: List[str], scene: str) -> Dict:
    """키 없이도 화면을 채울 수 있는 추출식 요약."""
    sentences: List[str] = []
    for utt in utterances:
        for part in re.split(r"(?<=[.!?。])\s+|\n+", utt or ""):
            part = part.strip()
            if len(part) >= 4:
                sentences.append(part)

    keywords = _extract_keywords(scene, sentences)
    head = sentences[:4]

    if head:
        body = " ".join(head)
        summary = f"내담자는 상담 중 다음과 같은 기억을 떠올렸습니다. {body}"
        if keywords:
            summary += f" 반복적으로 언급된 기억 요소는 {', '.join(keywords[:3])}입니다."
    else:
        summary = "이번 상담에서는 유의미한 발화가 충분히 수집되지 않았습니다."

    title = keywords[0] if keywords else "오늘의 기억"
    return {
        "title": title[:120],
        "summary": summary,
        "keywords": keywords[:6],
        "generated_by": "rule-based",
    }


def _extract_keywords(scene: str, sentences: List[str]) -> List[str]:
    """형태소 결과(scene)를 우선 쓰고, 없으면 빈도 기반으로 뽑는다."""
    keywords: List[str] = []

    for chunk in re.split(r"[,()]", scene or ""):
        chunk = chunk.strip()
        if not chunk:
            continue
        word = chunk.split()[-1]
        if len(word) >= 2 and word not in keywords:
            keywords.append(word)

    if len(keywords) >= 3:
        return keywords

    freq: Dict[str, int] = {}
    for sentence in sentences:
        for word in re.findall(r"[가-힣]{2,}", sentence):
            word = _strip_josa(word)
            if len(word) < 2 or word in _STOPWORDS:
                continue
            freq[word] = freq.get(word, 0) + 1

    for word, _ in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0])):
        if word not in keywords:
            keywords.append(word)
        if len(keywords) >= 6:
            break
    return keywords


# 형태소 분석기를 못 쓰는 환경용 간이 조사 제거
_JOSA = ("에서", "으로", "에게", "부터", "까지", "이", "가", "은", "는",
         "을", "를", "에", "로", "와", "과", "도", "만", "의")


def _strip_josa(word: str) -> str:
    for josa in _JOSA:
        if word.endswith(josa) and len(word) - len(josa) >= 2:
            return word[: -len(josa)]
    return word


def make_daily_tip(name: str) -> str:
    """client-home의 '오늘의 마음 다스림' 문구."""
    tips = [
        "창밖을 천천히 바라보며 깊게 숨을 세 번 쉬어보세요.",
        "오늘 기분 좋았던 순간 하나를 떠올려 보세요.",
        "따뜻한 물 한 잔과 함께 잠시 쉬어가도 좋습니다.",
        "좋아하는 노래를 한 곡 흥얼거려 보세요.",
        "가까운 사람에게 안부 인사를 건네 보세요.",
    ]
    from datetime import date

    index = (date.today().toordinal() + len(name)) % len(tips)
    return tips[index]
