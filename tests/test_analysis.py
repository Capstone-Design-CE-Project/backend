"""분석 엔진 연동 테스트.

실제 분석기(konlpy/JVM)는 느리고 Java 설치에 의존하므로, 여기서는
 - STT 출력 → 엔진 입력 형식 변환(build_conversation)
 - 엔진 출력 → DB 저장 / 장면 설명(save_analysis_to_db, make_scene_*)
처럼 엔진 없이도 검증 가능한 경계면만 다룬다.
"""
import promptmaker
from models import Noun, Verb
from services import morph_service


# ── STT 출력 → 엔진 입력 ──────────────────────────────────────
def test_build_conversation_merges_consecutive_same_speaker():
    """CLOVA 화자 분리는 같은 화자가 연달아 말하는 구간을 그대로 내보낸다.

    엔진은 Q/A 교대를 요구하므로 연속 발화는 한 턴으로 합쳐야 한다.
    """
    turns = [
        {"type": "Q", "text": "안녕하세요"},
        {"type": "Q", "text": "오늘 어떠셨어요"},
        {"type": "A", "text": "좋았어요"},
        {"type": "A", "text": "날씨도 맑았고"},
    ]
    assert morph_service.build_conversation(turns) == [
        {"text": "Q:안녕하세요 오늘 어떠셨어요"},
        {"text": "A:좋았어요 날씨도 맑았고"},
    ]


def test_build_conversation_prepends_question_when_starting_with_answer():
    turns = [{"type": "A", "text": "고향이 생각나요"}, {"type": "Q", "text": "어디신가요"},
             {"type": "A", "text": "충청도요"}]
    result = morph_service.build_conversation(turns)
    assert len(result) % 2 == 0
    assert result[0]["text"].startswith("Q:")
    assert result[1]["text"] == "A:고향이 생각나요"


def test_build_conversation_drops_trailing_question():
    """답이 없는 마지막 질문은 버린다 (짝수 개 유지)."""
    turns = [{"type": "Q", "text": "안녕"}, {"type": "A", "text": "네"},
             {"type": "Q", "text": "또 뭐가 있나요"}]
    assert morph_service.build_conversation(turns) == [
        {"text": "Q:안녕"}, {"text": "A:네"},
    ]


def test_build_conversation_handles_empty_and_blank():
    assert morph_service.build_conversation([]) == []
    assert morph_service.build_conversation([{"type": "A", "text": "   "}]) == []


def test_analyze_conversation_returns_empty_when_disabled(monkeypatch):
    """분석기를 못 쓰는 환경에서도 예외 없이 빈 결과를 준다."""
    result = morph_service.analyze_conversation(
        [{"type": "Q", "text": "안녕"}, {"type": "A", "text": "네"}]
    )
    assert result == {"nouns": [], "relations": []}


# ── 엔진 출력 → DB / 장면 설명 ────────────────────────────────
SAMPLE = {
    "nouns": [
        {"noun": "감나무", "adjectives": ["커다랗다"],
         "positions": [{"target": "마당", "location": "옆"}],
         "moderators": [], "target": None, "relative_clauses": []},
        {"noun": "밥", "adjectives": [], "positions": [],
         "moderators": [], "target": None, "relative_clauses": []},
        {"noun": "나", "adjectives": [], "positions": [],
         "moderators": [], "target": None, "relative_clauses": []},
    ],
    "relations": [
        {"subjects": ["나"], "verb": "먹다", "object": "밥", "direct_objects": ["밥"],
         "indirect_objects": [], "object_complements": [], "context": [],
         "reason_clauses": [], "polarity_bit": 0, "negation": "안",
         "display": "(나 [안]먹다 밥)"},
    ],
}


def _make_session(client, auth):
    cid = client.post("/clients", headers=auth, json={"name": "분석테스트"}).json()["id"]
    return client.post("/sessions", headers=auth, json={"client_id": cid}).json()["id"]


def test_save_analysis_stores_nouns_adjectives_and_polarity(client, auth):
    from database import SessionLocal

    session_id = _make_session(client, auth)
    db = SessionLocal()
    try:
        saved = promptmaker.save_analysis_to_db(session_id, SAMPLE, db)
        assert saved == 3

        nouns = {n.value: n for n in
                 db.query(Noun).filter(Noun.session_id == session_id).all()}
        assert set(nouns) == {"감나무", "밥", "나"}

        # 형용사 + 위치가 함께 adjective 로 들어간다
        assert {a.value for a in nouns["감나무"].adjectives} == {"커다랗다", "마당 옆"}

        # 부정 관계는 polarity_bit=0 으로 보존된다
        verbs = [(v.value, v.polarity_bit) for v in nouns["나"].verbs]
        assert verbs == [("먹다", 0)]
    finally:
        db.close()


def test_save_analysis_is_idempotent(client, auth):
    """같은 세션을 다시 분석해도 명사가 중복으로 쌓이지 않는다."""
    from database import SessionLocal

    session_id = _make_session(client, auth)
    db = SessionLocal()
    try:
        promptmaker.save_analysis_to_db(session_id, SAMPLE, db)
        promptmaker.save_analysis_to_db(session_id, SAMPLE, db)
        assert db.query(Noun).filter(Noun.session_id == session_id).count() == 3
        assert db.query(Verb).join(Noun).filter(
            Noun.session_id == session_id).count() == 1
    finally:
        db.close()


def test_scene_description_marks_negation():
    """부정문을 긍정 장면으로 그리지 않도록 표시가 남아야 한다."""
    scene = promptmaker.make_scene_from_analysis(SAMPLE)
    assert "커다랗다 마당 옆 감나무" in scene   # 형용사 → 수식어 → 위치 순
    assert "(하지 않음)" in scene


def test_scene_description_empty_for_empty_analysis():
    assert promptmaker.make_scene_from_analysis({"nouns": [], "relations": []}) == ""
