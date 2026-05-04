# test_clova.py
# CLOVA STT → 화자 분리 → DB 저장 전체 테스트

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import clova_speech
import promptmaker

# ── 설정 ──────────────────────────────────────────────────────
SESSION_ID  = 1
file_path   = r"C:\Users\HyunSuk\Documents\capstone.m4a" # 본인 파일 경로

promptmaker.clear_session_data(SESSION_ID)  # 이 줄 추가


# ── 1. CLOVA STT 변환 (화자 분리 포함) ────────────────────────
print("=" * 50)
print("1. CLOVA STT 변환 시작")
print("=" * 50)

stt_result = clova_speech.transcribe(file_path)

full_text = stt_result.get("full_text", "")
segments  = stt_result.get("segments",  [])

if not full_text:
    print("STT 변환 실패 - 종료")
    exit()

print(f"\n전체 텍스트: {full_text}")
print(f"분리된 발화 수: {len(segments)}개")

# ── 2. 화자 분리 결과 → qa_log 저장 ──────────────────────────
print("\n" + "=" * 50)
print("2. qa_log 저장 (화자 분리)")
print("=" * 50)

promptmaker.save_segments_to_db(SESSION_ID, segments)

# ── 3. 어르신(화자 2) 발화만 형태소 분석 → noun/adj/verb 저장
# ── 3. 어르신(화자 2) 발화만 형태소 분석 → DB 저장 ────────────
print("\n" + "=" * 50)
print("3. 형태소 분석 → DB 저장 (어르신 발화만)")
print("=" * 50)

from promptmaker_first import extract_nouns_adjectives_verbs, split_sentences_by_connective

for seg in segments:
    speaker = seg.get("speaker", "1")
    text    = seg.get("text", "")

    if speaker == "2":  # 어르신 발화만
        print(f"\n어르신 발화 분석: {text}")

        # split_sentences_by_connective가 (text, tokens) 튜플 리스트 반환
        split_result = split_sentences_by_connective(text)

        for sentence, tokens in split_result:   # ← 튜플 언패킹
            result = extract_nouns_adjectives_verbs(sentence, tokens)  # ← tokens 전달

            if isinstance(result, dict) and result.get("error") == "DANGER_WORD_DETECTED":
                print(f"[주의] 위험어 감지 - 저장 생략")
                continue

            if isinstance(result, dict) and 'noun_objects' in result:
                print(f"저장할 명사 수: {len(result['noun_objects'])}개")
                promptmaker.save_morphs_to_db(SESSION_ID, result)
            else:
                print(f"명사 없음 - 저장 생략: {result}")

# ── 4. 결과 확인 안내 ─────────────────────────────────────────
print("\n" + "=" * 50)
print("4. 완료! pgAdmin에서 아래 쿼리로 확인하세요.")
print("=" * 50)
print("""
-- 화자 분리 결과 확인
SELECT type, text, order_index
FROM qa_log
WHERE session_id = 1
ORDER BY order_index;

-- 추출된 명사 확인
SELECT * FROM noun WHERE session_id = 1 ORDER BY id;

-- 형용사 확인
SELECT n.value AS 명사, a.value AS 형용사
FROM noun n
JOIN adjective a ON a.noun_id = n.id
WHERE n.session_id = 1;
""")