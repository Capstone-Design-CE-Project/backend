"""대화 원문 / 형태소 분석 결과 저장 + DALL-E 프롬프트 조합.

기존에는 psycopg2로 직접 SQL을 날렸지만,
models.py / database.py의 SQLAlchemy 세션을 그대로 쓰도록 통일했다.
(드라이버가 두 개로 갈려 트랜잭션이 분리되던 문제 해결)
"""
from typing import Dict, List, Optional

from sqlalchemy.orm import Session as DBSession

from models import Adjective, Album, CounselSession, Noun, QaLog, Verb

COUNSELOR_SPEAKER_LABEL = "1"   # CLOVA 화자 1 = 상담사


# ── 1. 화자 분리 세그먼트 → qa_log ────────────────────────────
def save_segments_to_db(
    session_id: int, segments: List[Dict], db: DBSession
) -> int:
    """CLOVA 화자 분리 결과를 qa_log에 저장하고 저장 건수를 반환.

    화자 1 → 상담사 → type='Q' / 그 외 → 어르신 → type='A'
    """
    if not segments:
        print("[DB] 저장할 세그먼트 없음")
        return 0

    start_index = _next_order_index(session_id, db)
    saved = 0

    for offset, seg in enumerate(segments):
        text = (seg.get("text") or "").strip()
        if not text:
            continue

        speaker = str(seg.get("speaker", COUNSELOR_SPEAKER_LABEL))
        qa_type = "Q" if speaker == COUNSELOR_SPEAKER_LABEL else "A"

        db.add(
            QaLog(
                session_id=session_id,
                type=qa_type,
                text=text,
                order_index=start_index + offset,
                speaker_label=speaker,
                spoken_at_ms=seg.get("start"),
            )
        )
        saved += 1

    db.commit()
    print(f"[DB] qa_log {saved}건 저장 완료 (session_id={session_id})")
    return saved


# ── 2. 단일 발화 저장 (화자 분리 실패 시 폴백) ────────────────
def save_stt_result(
    session_id: int,
    text: str,
    db: DBSession,
    order_index: Optional[int] = None,
    qa_type: str = "A",
) -> QaLog:
    """전문 한 덩어리를 qa_log에 저장."""
    if order_index is None:
        order_index = _next_order_index(session_id, db)

    log = QaLog(
        session_id=session_id,
        type=qa_type,
        text=text,
        order_index=order_index,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    print(f"[DB] 전문 저장 완료: {text[:30]}...")
    return log


def _next_order_index(session_id: int, db: DBSession) -> int:
    last = (
        db.query(QaLog.order_index)
        .filter(QaLog.session_id == session_id)
        .order_by(QaLog.order_index.desc())
        .first()
    )
    return (last[0] + 1) if last else 1


# ── 3. 형태소 분석 결과 → noun / adjective / verb ─────────────
def save_morphs_to_db(session_id: int, result: Dict, db: DBSession) -> int:
    """extract_nouns_adjectives_verbs() 결과를 저장하고 명사 수를 반환."""
    noun_objects = result.get("noun_objects") or []
    if not noun_objects:
        print("[DB] 명사 없음 - 형태소 저장 생략")
        return 0

    relations = result.get("relations") or []

    for noun_obj in noun_objects:
        # relations에서 이 명사가 목적어면 주어를 target_noun으로 기록
        target = None
        for rel in relations:
            obj = getattr(rel, "obj", None)
            subject = getattr(rel, "subject", None)
            if obj is not None and subject is not None and obj.noun == noun_obj.noun:
                target = subject.noun
                break

        noun_row = Noun(
            session_id=session_id,
            value=noun_obj.noun,
            target_noun=target,
            is_base_noun=False,
        )
        db.add(noun_row)
        db.flush()               # noun_row.id 확보

        # 형용사 / 위치 / 수식 명사를 모두 adjective로 저장
        modifiers = []
        modifiers += list(getattr(noun_obj, "adjectives", []) or [])
        modifiers += list(getattr(noun_obj, "position", []) or [])
        modifiers += list(getattr(noun_obj, "moderators", []) or [])

        for mod in modifiers:
            value = _stringify(mod)
            if value:
                db.add(Adjective(noun_id=noun_row.id, value=value[:100]))

        # 동사는 관계에서 추출
        for rel in relations:
            subject = getattr(rel, "subject", None)
            verb = getattr(rel, "verb", None)
            if subject is not None and verb and subject.noun == noun_obj.noun:
                db.add(Verb(noun_id=noun_row.id, value=str(verb)[:100]))

    db.commit()
    print(f"[DB] 형태소 분석 결과 저장 완료: {len(noun_objects)}개 명사")
    return len(noun_objects)


def _stringify(value) -> str:
    """position은 (위치, 대상) 튜플로 들어올 수 있어 문자열로 평탄화."""
    if isinstance(value, (tuple, list)):
        return " ".join(str(v).strip() for v in value if str(v).strip())
    return str(value).strip() if value is not None else ""


# ── 4. DB → 한국어 장면 설명 ──────────────────────────────────
def make_scene_description(session_id: int, db: DBSession) -> str:
    """noun/adjective를 조합해 장면 설명 문자열 생성.

    예) "남색 등대, 흰색 배 (등대 근처에), 바닷가"
    """
    nouns = (
        db.query(Noun)
        .filter(Noun.session_id == session_id)
        .order_by(Noun.id)
        .all()
    )
    if not nouns:
        print("[PROMPT] 명사 없음 - 장면 설명 생성 불가")
        return ""

    seen = set()
    parts = []
    for noun in nouns:
        adjectives = [a.value for a in noun.adjectives if a.value]
        line = (" ".join(adjectives) + " " if adjectives else "") + noun.value
        if noun.target_noun:
            line += f" ({noun.target_noun} 근처에)"
        if line in seen:
            continue
        seen.add(line)
        parts.append(line)

    scene = ", ".join(parts)
    print(f"[PROMPT] 장면 설명: {scene}")
    return scene


def make_scene_from_keywords(keywords: List[str]) -> str:
    """형태소 분석을 못 쓰는 환경에서 요약 키워드로 장면 설명 대체."""
    return ", ".join(k for k in keywords if k)


# ── 5. 장면 설명 → DALL-E 영문 프롬프트 ──────────────────────
BASE_STYLE = (
    "Soft and gentle watercolor style, warm nostalgic Korean scenery, "
    "reminiscent of an elderly Korean person's precious memories, "
    "peaceful and emotional atmosphere, no text, no people's faces in close-up, "
    "no violence or distressing elements."
)


def make_dalle_prompt(scene_description: str, extra: Optional[str] = None) -> str:
    """한국어 장면 설명 → DALL-E 3 영문 프롬프트."""
    if not scene_description:
        prompt = (
            "A warm Korean watercolor illustration of a peaceful countryside "
            "in the late afternoon light. " + BASE_STYLE
        )
    else:
        prompt = (
            f"A warm Korean watercolor illustration depicting: {scene_description}. "
            + BASE_STYLE
        )
    if extra:
        prompt += f" Additional direction: {extra.strip()}"
    return prompt


# ── 6. 테스트용 세션 데이터 초기화 ────────────────────────────
def clear_session_data(session_id: int, db: DBSession) -> None:
    """해당 세션의 분석/기록 데이터 전체 삭제 (세션 행 자체는 유지)."""
    noun_ids = [
        n.id for n in db.query(Noun.id).filter(Noun.session_id == session_id).all()
    ]
    if noun_ids:
        db.query(Adjective).filter(Adjective.noun_id.in_(noun_ids)).delete(
            synchronize_session=False
        )
        db.query(Verb).filter(Verb.noun_id.in_(noun_ids)).delete(
            synchronize_session=False
        )
    db.query(Noun).filter(Noun.session_id == session_id).delete(
        synchronize_session=False
    )
    db.query(QaLog).filter(QaLog.session_id == session_id).delete(
        synchronize_session=False
    )
    db.query(Album).filter(Album.session_id == session_id).delete(
        synchronize_session=False
    )
    db.commit()
    print(f"[DB] session_id={session_id} 데이터 초기화 완료")


def get_client_utterances(session_id: int, db: DBSession) -> List[str]:
    """어르신(A) 발화만 순서대로 반환."""
    rows = (
        db.query(QaLog)
        .filter(QaLog.session_id == session_id, QaLog.type == "A")
        .order_by(QaLog.order_index)
        .all()
    )
    return [r.text for r in rows]


def get_session_or_none(session_id: int, db: DBSession):
    return db.query(CounselSession).filter(CounselSession.id == session_id).first()
