"""대화 원문 / 형태소 분석 결과 저장 + DALL-E 프롬프트 조합.

기존에는 psycopg2로 직접 SQL을 날렸지만,
models.py / database.py의 SQLAlchemy 세션을 그대로 쓰도록 통일했다.
(드라이버가 두 개로 갈려 트랜잭션이 분리되던 문제 해결)
"""
import json
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


# ── 3. 분석 결과 → noun / adjective / verb ────────────────────
def save_analysis_to_db(session_id: int, result: Dict, db: DBSession) -> int:
    """counseling_analysis_interface.analyze() 결과를 저장하고 명사 수를 반환.

    결과 구조 (v3.2 엔진)
        {"nouns":     [{noun, adjectives, positions, moderators, target,
                        relative_clauses}, ...],
         "relations": [{subjects, verb, object, direct_objects, ...,
                        polarity_bit, display}, ...]}

    nouns  → noun 테이블 (+ adjectives/positions/moderators 는 adjective 로)
    relations → 주어에 해당하는 noun 에 verb 로 연결 (부정은 polarity_bit=0)
    """
    nouns = result.get("nouns") or []
    if not nouns:
        print("[DB] 명사 없음 - 분석 결과 저장 생략")
        return 0

    relations = result.get("relations") or []

    # 같은 상담을 다시 분석하면 중복이 쌓이므로 기존 분석 결과를 비운다.
    _clear_morphs(session_id, db)

    noun_rows: Dict[str, Noun] = {}
    for item in nouns:
        value = str(item.get("noun", "")).strip()
        if not value:
            continue

        row = Noun(
            session_id=session_id,
            value=value[:100],
            target_noun=(str(item["target"])[:100] if item.get("target") else None),
            is_base_noun=False,
        )
        db.add(row)
        db.flush()                       # row.id 확보
        noun_rows[value] = row

        for modifier in _collect_modifiers(item):
            db.add(Adjective(noun_id=row.id, value=modifier[:100]))

    # 관계의 동사를 주어 명사에 붙인다
    for relation in relations:
        verb = str(relation.get("verb", "")).strip()
        if not verb:
            continue
        polarity = int(relation.get("polarity_bit", 1))
        for subject in relation.get("subjects") or []:
            row = noun_rows.get(str(subject).strip())
            if row is not None:
                db.add(Verb(noun_id=row.id, value=verb[:100], polarity_bit=polarity))

    db.commit()
    print(f"[DB] 분석 결과 저장 완료: 명사 {len(noun_rows)}개, 관계 {len(relations)}개")
    return len(noun_rows)


def _collect_modifiers(noun_item: Dict) -> List[str]:
    """형용사 / 위치 / 수식 명사를 adjective 로 넣을 문자열로 평탄화."""
    out: List[str] = []
    out += [str(v).strip() for v in (noun_item.get("adjectives") or [])]
    out += [str(v).strip() for v in (noun_item.get("moderators") or [])]

    for position in noun_item.get("positions") or []:
        if isinstance(position, dict):
            target = str(position.get("target") or "").strip()
            location = str(position.get("location") or "").strip()
            merged = " ".join(part for part in (target, location) if part)
        else:
            merged = str(position).strip()
        if merged:
            out.append(merged)

    return [v for v in out if v]


def _clear_morphs(session_id: int, db: DBSession) -> None:
    """해당 세션의 noun/adjective/verb 만 비운다 (qa_log·album 은 유지)."""
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
        db.flush()


def save_morphs_to_db(session_id: int, result: Dict, db: DBSession) -> int:
    """하위 호환 별칭. 신규 코드는 save_analysis_to_db 를 쓴다."""
    return save_analysis_to_db(session_id, result, db)


# ── 4. 분석 결과 → 한국어 장면 설명 ──────────────────────────
def make_scene_from_analysis(result: Dict) -> str:
    """analyze() 결과로 장면 설명을 만든다 (관계·부정 반영).

    예) "주황색 감이 열린 감나무, 마당 옆 우물, 어머니가 감을 깎는 모습"
    """
    nouns = result.get("nouns") or []
    relations = result.get("relations") or []
    if not nouns and not relations:
        return ""

    parts: List[str] = []
    seen = set()

    def add(text: str) -> None:
        text = text.strip()
        if text and text not in seen:
            seen.add(text)
            parts.append(text)

    for item in nouns:
        value = str(item.get("noun", "")).strip()
        if not value:
            continue
        modifiers = _collect_modifiers(item)
        add((" ".join(modifiers) + " " + value).strip() if modifiers else value)

    for relation in relations:
        verb = str(relation.get("verb", "")).strip()
        if not verb:
            continue
        subjects = [str(v) for v in (relation.get("subjects") or []) if v]
        obj = relation.get("object")
        phrase = " ".join(filter(None, [
            ", ".join(subjects) if subjects else "",
            f"{obj}을(를)" if obj else "",
            verb,
        ]))
        # polarity_bit=0 이면 부정. 반대 장면을 그리지 않도록 표시한다.
        if int(relation.get("polarity_bit", 1)) == 0:
            phrase = f"{phrase} (하지 않음)"
        add(phrase)

    scene = ", ".join(parts)
    print(f"[PROMPT] 장면 설명: {scene[:120]}")
    return scene


def make_scene_description(session_id: int, db: DBSession) -> str:
    """세션의 분석 결과로 장면 설명을 만든다.

    session.analysis_json 이 있으면 그것을(관계 포함) 쓰고,
    없으면 noun/adjective 테이블에서 조합한다(구버전 데이터 대응).
    """
    sess = db.query(CounselSession).filter(CounselSession.id == session_id).first()
    if sess is not None and sess.analysis_json:
        try:
            return make_scene_from_analysis(json.loads(sess.analysis_json))
        except (ValueError, TypeError) as exc:
            print(f"[PROMPT] analysis_json 파싱 실패, 테이블로 대체: {exc}")

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

        verbs = [v for v in noun.verbs if v.value]
        if verbs:
            rendered = [
                v.value if v.polarity_bit else f"{v.value} (하지 않음)" for v in verbs
            ]
            line += " - " + ", ".join(rendered)

        if line in seen:
            continue
        seen.add(line)
        parts.append(line)

    scene = ", ".join(parts)
    print(f"[PROMPT] 장면 설명: {scene[:120]}")
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
