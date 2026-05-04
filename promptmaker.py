import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()


# ── DB 연결 ────────────────────────────────────────────────────
def get_conn():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD")
    )


# ── 1. 화자 분리된 세그먼트 → qa_log 저장 ─────────────────────
def save_segments_to_db(session_id: int, segments: list):
    """
    CLOVA STT 화자 분리 결과를 qa_log 테이블에 저장

    화자 1 → 상담사 → type = 'Q'
    화자 2 → 어르신 → type = 'A'

    Args:
        session_id: 상담 세션 ID
        segments:   clova_speech.transcribe()의 segments 결과
                    [{"speaker": "1", "text": "..."}, ...]
    """
    if not segments:
        print("[DB] 저장할 세그먼트 없음")
        return

    conn = get_conn()
    cur  = conn.cursor()

    for i, seg in enumerate(segments):
        speaker = seg.get("speaker", "1")
        text    = seg.get("text", "").strip()

        if not text:
            continue

        # 화자 1 = 상담사(Q), 화자 2 = 어르신(A)
        qa_type = "Q" if speaker == "1" else "A"

        cur.execute("""
            INSERT INTO qa_log (session_id, type, text, order_index)
            VALUES (%s, %s, %s, %s)
        """, (session_id, qa_type, text, i + 1))

        print(f"[DB] qa_log 저장 (type={qa_type}, 화자={speaker}): {text[:30]}...")

    conn.commit()
    cur.close()
    conn.close()
    print(f"[DB] 전체 {len(segments)}개 발화 저장 완료")


# ── 2. 전문만 저장할 때 (화자 분리 없이 원문 보존용) ───────────
def save_full_text_to_db(session_id: int, full_text: str, order_index: int):
    """
    화자 분리 없이 전체 텍스트를 qa_log에 저장
    (백업용 또는 화자 분리 실패 시 사용)
    """
    conn = get_conn()
    cur  = conn.cursor()

    cur.execute("""
        INSERT INTO qa_log (session_id, type, text, order_index)
        VALUES (%s, %s, %s, %s)
    """, (session_id, "A", full_text, order_index))

    conn.commit()
    cur.close()
    conn.close()
    print(f"[DB] 전문 저장 완료: {full_text[:30]}...")


# ── 3. 형태소 분석 결과 → noun/adjective/verb 저장 ────────────
def save_morphs_to_db(session_id: int, result: dict):
    """
    extract_nouns_adjectives_verbs() 결과를
    noun / adjective / verb 테이블에 저장

    어르신(화자 2) 발화에서 추출된 결과만 저장
    """
    if 'noun_objects' not in result:
        print("[DB] 명사 없음 - 형태소 저장 생략")
        return

    noun_objects = result.get('noun_objects', [])
    relations    = result.get('relations',    [])

    conn = get_conn()
    cur  = conn.cursor()

    for noun_obj in noun_objects:

        # relations에서 이 명사가 목적어인 경우 주어를 target으로 저장
        target = None
        for rel in relations:
            if rel.obj and rel.obj.noun == noun_obj.noun:
                target = rel.subject.noun
                break

        # noun 저장 후 id 받기
        cur.execute("""
            INSERT INTO noun (session_id, value, target_noun, is_base_noun)
            VALUES (%s, %s, %s, %s)
            RETURNING id
        """, (session_id, noun_obj.noun, target, False))

        noun_id = cur.fetchone()[0]

        # adjective 저장
        for adj in noun_obj.adjectives:
            if adj.strip():
                cur.execute("""
                    INSERT INTO adjective (noun_id, value)
                    VALUES (%s, %s)
                """, (noun_id, adj))

        # position(위치 정보)도 adjective로 저장
        for pos in noun_obj.position:
            if pos.strip():
                cur.execute("""
                    INSERT INTO adjective (noun_id, value)
                    VALUES (%s, %s)
                """, (noun_id, pos))

        # moderators(수식 명사)도 adjective로 저장
        for mod in noun_obj.moderators:
            if isinstance(mod, str) and mod.strip():
                cur.execute("""
                    INSERT INTO adjective (noun_id, value)
                    VALUES (%s, %s)
                """, (noun_id, mod))

    conn.commit()
    cur.close()
    conn.close()
    print(f"[DB] 형태소 분석 결과 저장 완료: {len(noun_objects)}개 명사")


# ── 4. DB에서 꺼내서 장면 설명 조합 ───────────────────────────
def make_scene_description(session_id: int) -> str:
    """
    noun / adjective 테이블에서 데이터를 꺼내
    DALL-E 프롬프트용 한국어 장면 설명 생성

    Returns:
        예) "남색 등대, 흰색 배 (등대 근처에), 바닷가"
    """
    conn = get_conn()
    cur  = conn.cursor()

    cur.execute("""
        SELECT
            n.id,
            n.value,
            n.target_noun,
            ARRAY_AGG(a.value) FILTER (WHERE a.value IS NOT NULL) AS adjectives
        FROM noun n
        LEFT JOIN adjective a ON a.noun_id = n.id
        WHERE n.session_id = %s
        GROUP BY n.id, n.value, n.target_noun
        ORDER BY n.id
    """, (session_id,))

    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        print("[PROMPT] 명사 없음 - 장면 설명 생성 불가")
        return ""

    parts = []
    for row in rows:
        _, value, target_noun, adjectives = row

        line = ""
        if adjectives:
            line += " ".join(adjectives) + " "
        line += value
        if target_noun:
            line += f" ({target_noun} 근처에)"

        parts.append(line)

    scene = ", ".join(parts)
    print(f"[PROMPT] 장면 설명: {scene}")
    return scene


# ── 5. 장면 설명 → DALL-E 영문 프롬프트 ──────────────────────
def make_dalle_prompt(scene_description: str) -> str:
    """한국어 장면 설명 → DALL-E 3 영문 프롬프트 변환"""
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
def clear_session_data(session_id: int):
    """테스트용 - 해당 세션 데이터 전체 초기화"""
    conn = get_conn()
    cur  = conn.cursor()

    cur.execute("DELETE FROM adjective WHERE noun_id IN (SELECT id FROM noun WHERE session_id = %s)", (session_id,))
    cur.execute("DELETE FROM verb       WHERE noun_id IN (SELECT id FROM noun WHERE session_id = %s)", (session_id,))
    cur.execute("DELETE FROM noun       WHERE session_id = %s", (session_id,))
    cur.execute("DELETE FROM qa_log     WHERE session_id = %s", (session_id,))
    cur.execute("DELETE FROM album      WHERE session_id = %s", (session_id,))

    conn.commit()
    cur.close()
    conn.close()
    print(f"[DB] session_id={session_id} 데이터 초기화 완료")