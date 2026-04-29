from sqlalchemy.orm import Session as DBSession
from models import QaLog, Noun, Adjective, Verb
from dotenv import load_dotenv

load_dotenv()


# ── 1. STT 결과 → qa_log 저장 ─────────────────────────────────

def save_stt_result(
    session_id: int,
    text: str,
    order_index: int,
    qa_type: str,
    db: DBSession
) -> QaLog:
    """
    CLOVA STT 변환 텍스트를 qa_log 테이블에 저장

    Args:
        session_id:  상담 세션 ID
        text:        STT 변환된 텍스트
        order_index: Q&A 순서 번호
        qa_type:     'Q' 또는 'A'
        db:          DB 세션
    """
    qa = QaLog(
        session_id=session_id,
        type=qa_type,
        text=text,
        order_index=order_index
    )
    db.add(qa)
    db.commit()
    print(f"[DB] qa_log 저장 완료 (type={qa_type}): {text[:30]}...")
    return qa


# ── 2. 형태소 분석 결과 → DB 저장 ─────────────────────────────

def save_morphs_to_db(
    session_id: int,
    result: dict,
    db: DBSession
):
    """
    extract_nouns_adjectives_verbs() 결과를
    noun / adjective / verb 테이블에 저장

    현재 버전 result 형태:
    {
        'noun_objects': [imagine 객체들],
        'relations':    [Relation 객체들]
    }
    또는 명사가 없을 때:
    {
        'adjectives': [...],
        'verbs':      [...],
        'position':   [...]
    }
    """
    # 명사가 없는 경우 저장 생략
    if 'noun_objects' not in result:
        print("[DB] 명사 없음 - 저장 생략")
        return

    noun_objects = result.get('noun_objects', [])
    relations    = result.get('relations',    [])

    for noun_obj in noun_objects:

        # relations에서 이 명사가 목적어인 경우 주어를 target으로 저장
        target = None
        for rel in relations:
            if rel.obj and rel.obj.noun == noun_obj.noun:
                target = rel.subject.noun
                break

        # noun 저장
        noun = Noun(
            session_id=session_id,
            value=noun_obj.noun,
            target_noun=target,
            is_base_noun=False
        )
        db.add(noun)
        db.flush()  # INSERT 후 id 먼저 확보

        # adjective 저장
        for adj in noun_obj.adjectives:
            if adj.strip():
                db.add(Adjective(noun_id=noun.id, value=adj))

        # position 정보도 adjective로 저장 (위치 수식어)
        for pos in noun_obj.position:
            if pos.strip():
                db.add(Adjective(noun_id=noun.id, value=pos))

        # moderators → 수식 명사도 adjective로 저장
        for mod in noun_obj.moderators:
            if isinstance(mod, str) and mod.strip():
                db.add(Adjective(noun_id=noun.id, value=mod))

    db.commit()
    print(f"[DB] 형태소 분석 결과 저장 완료: {len(noun_objects)}개 명사")


# ── 3. DB에서 꺼내서 장면 설명 조합 ───────────────────────────

def make_scene_description(session_id: int, db: DBSession) -> str:
    """
    noun / adjective 테이블에서 데이터를 꺼내
    DALL-E 프롬프트용 한국어 장면 설명 문장 생성

    Returns:
        예) "남색 등대, 흰색 배 (등대 근처에), 바닷가"
    """
    nouns = db.query(Noun).filter(
        Noun.session_id == session_id
    ).all()

    if not nouns:
        print("[PROMPT] 명사 없음 - 장면 설명 생성 불가")
        return ""

    parts = []
    for noun in nouns:
        adjs = [a.value for a in noun.adjectives]

        line = ""

        # 형용사 + 명사
        if adjs:
            line += " ".join(adjs) + " "
        line += noun.value

        # target 관계
        if noun.target_noun:
            line += f" ({noun.target_noun} 근처에)"

        parts.append(line)

    scene = ", ".join(parts)
    print(f"[PROMPT] 장면 설명: {scene}")
    return scene


# ── 4. 장면 설명 → DALL-E 영문 프롬프트 ──────────────────────

def make_dalle_prompt(scene_description: str) -> str:
    """
    한국어 장면 설명을 DALL-E 3 영문 프롬프트로 변환

    Args:
        scene_description: make_scene_description()의 결과

    Returns:
        DALL-E에 넘길 영문 프롬프트
    """
    if not scene_description:
        return (
            "A warm Korean watercolor illustration of a peaceful countryside. "
            "Soft and gentle style, reminiscent of an elderly Korean person's memories."
        )

    return (
        f"A warm Korean watercolor illustration: {scene_description}. "
        f"Soft and gentle style, "
        f"reminiscent of an elderly Korean person's precious memories. "
        f"No violence or negative elements, "
        f"peaceful and emotional atmosphere, traditional Korean scenery."
    )
