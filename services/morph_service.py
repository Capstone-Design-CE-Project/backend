"""형태소/관계 분석 서비스 - counseling_analysis_interface 래퍼.

분석 엔진 교체 (v3.2)
  이전: promptmaker_first.extract_nouns_adjectives_verbs() 를 발화 하나씩 호출
  현재: counseling_analysis_interface.analyze() 에 상담 전체 Q/A 를 한 번에 전달

전체를 한 번에 넘기는 이유는, 새 엔진이 질문과 답변을 이어서 해석하기
때문이다. "Q:감나무는 어떤 색이었나요 / A:주황색이었어" 처럼 답변에만 있는
형용사를 질문 쪽 명사에 붙여 준다. 발화를 따로 넘기면 이 연결이 끊긴다.

엔진은 입력 형식을 엄격하게 검증한다 (짝수 개, Q/A 교대, 'Q:'/'A:' 접두사).
반면 CLOVA 화자 분리 결과는 같은 화자가 연달아 말하는 구간이 흔해 그대로는
통과하지 못한다. build_conversation() 이 그 간극을 메운다.

konlpy 는 Java(JVM)에 의존해 환경에 따라 import 가 실패할 수 있으므로
지연 import + 예외 격리를 유지한다.
"""
import re
import threading
from typing import Dict, List, Optional, Sequence

from core.config import settings

_interface = None
_engine = None
_load_error: Optional[str] = None

# 상담사 발화가 없을 때 Q/A 쌍을 맞추기 위해 끼워 넣는 중립 질문
_FILLER_QUESTION = "그때 이야기를 들려주세요."

# 엔진을 못 쓸 때 쓰는 최소 PII 마스킹 (엔진 mask_pii 와 동일 규칙)
_PHONE_RE = re.compile(r"01[016789]-?\d{3,4}-?\d{4}")
_RRN_RE = re.compile(r"\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])-?[1-4]\d{6}")


def _load():
    """분석기를 최초 호출 시점에 한 번만 로드."""
    global _interface, _engine, _load_error
    if _interface is not None or _load_error is not None:
        return _interface

    if not settings.ENABLE_MORPH_ANALYSIS:
        _load_error = "ENABLE_MORPH_ANALYSIS=False"
        return None

    try:
        import counseling_analysis_interface as interface   # konlpy/JVM 초기화
        import prompt_maker_succeed_3_span_experiment as engine

        engine.DEBUG_MORPHS = False
        _interface, _engine = interface, engine
    except Exception as exc:                    # ImportError, JVMNotFound 등
        _load_error = str(exc)
        print(f"[MORPH] 형태소 분석기를 사용할 수 없습니다: {exc}")
    return _interface


def is_available() -> bool:
    return _load() is not None


def _detach_jvm_thread() -> None:
    """워커 스레드를 JVM 에서 분리한다.

    konlpy 는 JPype 로 JVM 을 쓰는데, FastAPI 는 동기 핸들러를 스레드풀에서
    실행한다. 분석을 마친 워커 스레드가 JVM 에 붙은 채로 남으면 JPype 의
    종료 처리가 그 스레드를 기다려 프로세스가 끝나지 않는다
    (uvicorn 종료·pytest 종료가 멈춘다). 메인 스레드는 건드리지 않는다.
    """
    if threading.current_thread() is threading.main_thread():
        return
    try:
        import jpype

        if jpype.isJVMStarted() and jpype.java.lang.Thread.isAttached():
            jpype.detachThreadFromJVM()
    except Exception:
        pass


def _reset_engine_state() -> None:
    """상담 간 전역 상태가 섞이지 않도록 초기화.

    엔진은 추론한 사람 이름을 전역 집합에 쌓아 둔다. 서버는 한 프로세스에서
    여러 상담을 처리하므로, 분석 직전에 비워 이전 상담의 이름이 섞이지 않게 한다.
    """
    if _engine is None:
        return
    for name in ("INFERRED_PERSON_NAMES", "nouns"):
        container = getattr(_engine, name, None)
        if isinstance(container, (set, list, dict)):
            container.clear()


# ── 입력 정규화 ───────────────────────────────────────────────
def build_conversation(turns: Sequence[Dict]) -> List[Dict[str, str]]:
    """qa_log 행들을 엔진이 요구하는 엄격한 Q/A 목록으로 변환.

    Args:
        turns: [{"type": "Q"|"A", "text": "..."}, ...]  (order_index 순서)

    Returns:
        [{"text": "Q:..."}, {"text": "A:..."}, ...]
        짝수 개이고 Q로 시작하며 Q/A가 교대한다. 만들 수 없으면 빈 리스트.

    규칙
        - 같은 화자가 연달아 말하면 한 턴으로 합친다
        - 첫 발화가 어르신(A)이면 중립 질문을 앞에 끼운다
        - 마지막이 상담사(Q)로 끝나면 (답이 없으므로) 버린다
    """
    merged: List[Dict[str, str]] = []
    for turn in turns:
        qa_type = "Q" if str(turn.get("type", "A")).upper() == "Q" else "A"
        text = (turn.get("text") or "").strip()
        if not text:
            continue
        if merged and merged[-1]["type"] == qa_type:
            merged[-1]["text"] += " " + text
        else:
            merged.append({"type": qa_type, "text": text})

    if not merged:
        return []

    if merged[0]["type"] == "A":
        merged.insert(0, {"type": "Q", "text": _FILLER_QUESTION})
    if merged[-1]["type"] == "Q":
        merged.pop()
    if not merged:
        return []

    return [{"text": f"{t['type']}:{t['text']}"} for t in merged]


# ── 분석 ──────────────────────────────────────────────────────
EMPTY_RESULT: Dict[str, List] = {"nouns": [], "relations": []}


def analyze_conversation(turns: Sequence[Dict]) -> Dict[str, List]:
    """상담 전체를 분석해 {"nouns": [...], "relations": [...]} 반환.

    분석기가 없거나 입력이 부족하면 빈 결과를 돌려주고 예외는 던지지 않는다.
    """
    interface = _load()
    if interface is None:
        return dict(EMPTY_RESULT)

    conversation = build_conversation(turns)
    if not conversation:
        print("[MORPH] Q/A 쌍을 만들 수 없어 분석을 건너뜁니다.")
        return dict(EMPTY_RESULT)

    _reset_engine_state()
    try:
        result = interface.analyze(conversation)
    except interface.CounselingInputError as exc:
        print(f"[MORPH] 입력 형식 오류 - 분석 건너뜀: {exc}")
        return dict(EMPTY_RESULT)
    except Exception as exc:
        print(f"[MORPH] 분석 실패: {exc}")
        return dict(EMPTY_RESULT)
    finally:
        _detach_jvm_thread()

    nouns = result.get("nouns") or []
    relations = result.get("relations") or []
    print(f"[MORPH] 분석 완료: 명사 {len(nouns)}개, 관계 {len(relations)}개")
    return {"nouns": nouns, "relations": relations}


def analyze_text(text: str, question: Optional[str] = None) -> Dict[str, List]:
    """발화 한 건만 분석 (실시간 텍스트 추가용)."""
    turns = [{"type": "Q", "text": question or _FILLER_QUESTION},
             {"type": "A", "text": text}]
    return analyze_conversation(turns)


def mask_pii(text: str) -> str:
    """전화번호/주민번호 마스킹. 엔진이 없어도 동작한다."""
    if not text:
        return text
    interface = _load()
    if _engine is not None:
        try:
            return _engine.mask_pii(text)
        except Exception:
            pass
    text = _PHONE_RE.sub("010-****-****", text)
    return _RRN_RE.sub("******-*******", text)
