"""형태소 분석 서비스 - promptmaker_first.py(팀원 A 코드) 래퍼.

konlpy는 Java(JVM)에 의존해 설치 환경에 따라 import가 실패할 수 있다.
분석이 불가능한 환경에서도 서버 전체가 죽지 않도록 지연 import + 예외 격리.
"""
from typing import Dict, List, Optional

from core.config import settings

_analyzer = None
_load_error: Optional[str] = None


def _get_analyzer():
    """promptmaker_first 모듈을 최초 호출 시점에 한 번만 로드."""
    global _analyzer, _load_error
    if _analyzer is not None or _load_error is not None:
        return _analyzer

    if not settings.ENABLE_MORPH_ANALYSIS:
        _load_error = "ENABLE_MORPH_ANALYSIS=False"
        return None

    try:
        import promptmaker_first  # konlpy/JVM 초기화 발생

        _analyzer = promptmaker_first
    except Exception as exc:                     # ImportError, JVMNotFound 등
        _load_error = str(exc)
        print(f"[MORPH] 형태소 분석기를 사용할 수 없습니다: {exc}")
    return _analyzer


def is_available() -> bool:
    return _get_analyzer() is not None


def analyze(text: str) -> List[Dict]:
    """한 문단을 문장 단위로 쪼개 분석 결과 리스트로 반환.

    Returns:
        [{'noun_objects': [imagine], 'relations': [Relation]}, ...]
        위험어가 감지된 문장은 건너뛴다.
    """
    analyzer = _get_analyzer()
    if analyzer is None or not text.strip():
        return []

    results: List[Dict] = []
    try:
        split_result = analyzer.split_sentences_by_connective(text)
    except Exception as exc:
        print(f"[MORPH] 문장 분리 실패: {exc}")
        return []

    for item in split_result:
        # split_sentences_by_connective는 (sentence, tokens) 튜플을 반환한다.
        if isinstance(item, tuple):
            sentence, tokens = item[0], item[1]
        else:
            sentence, tokens = item, None

        try:
            result = analyzer.extract_nouns_adjectives_verbs(sentence, tokens)
        except Exception as exc:
            print(f"[MORPH] 분석 실패 ('{sentence[:20]}...'): {exc}")
            continue

        if not isinstance(result, dict):
            continue
        if result.get("error") == "DANGER_WORD_DETECTED":
            print("[MORPH] 위험어 감지 - 해당 문장 저장 생략")
            continue
        if "noun_objects" in result:
            results.append(result)

    return results


def mask_pii(text: str) -> str:
    """전화번호/주민번호 마스킹. 분석기가 없으면 원문 그대로."""
    analyzer = _get_analyzer()
    if analyzer is None:
        return text
    try:
        return analyzer.mask_pii(text)
    except Exception:
        return text
