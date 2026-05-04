# 치매 어르신 상담 이미지 생성 시스템

## 구조
```
STT (CLOVA Speech)
    ↓
형태소 분석 (extract.py - 팀원 A)
    ↓
PostgreSQL 저장 (promptmaker.py)
    ↓
DALL-E 이미지 생성
    ↓
album 테이블 저장
```

## 파일 구조
```
capstone/
├── .env                  ← API 키 (깃허브에 올리지 말 것!)
├── .env.example          ← .env 양식
├── main.py               ← FastAPI 진입점
├── database.py           ← DB 연결 설정
├── models.py             ← 테이블 구조
├── clova_speech.py       ← CLOVA STT
├── promptmaker.py        ← DB 저장 + 프롬프트 조합
├── extract.py            ← 형태소 분석 (팀원 A)
├── requirements.txt      ← 라이브러리 목록
└── routers/
      └── pipeline.py     ← API 엔드포인트
```

## 설치 및 실행

```bash
# 1. 라이브러리 설치
pip install -r requirements.txt

# 2. .env 파일 설정
cp .env.example .env
# .env 파일에 API 키, DB 비밀번호 입력

# 3. 서버 실행
uvicorn main:app --reload

# 4. API 문서 확인
# http://localhost:8000/docs
```

## API 사용 순서

```
1. 세션 생성
   POST /pipeline/session?user_id=1&filename=test.mp3

2. 음성 파일 업로드 (STT + DB 저장)
   POST /pipeline/stt/{session_id}
   Body: form-data, file=음성파일

3. 이미지 생성
   POST /pipeline/generate/{session_id}

또는 한 번에 실행
   POST /pipeline/run/{session_id}
   Body: form-data, file=음성파일

4. 앨범 조회
   GET /pipeline/album/{session_id}
```

## .gitignore 필수 설정
```
.env
__pycache__/
*.pyc
/tmp/
```

## 업데이트 내역

### v2.0 - 화자 분리 및 DB 연동 개선

#### 1. CLOVA Speech API 단문 → 장문 인식 전환
- 기존 단문 API는 60초, 10MB 제한으로 실제 상담 녹음에 적합하지 않아 장문 인식 API로 전환
- 음성 파일과 설정값(JSON)을 multipart/form-data 형식으로 전송하는 방식으로 변경
- timeout=300 (5분) 설정 추가 - 장문 인식 처리 시간 대응

#### 2. 화자 분리 (Diarization) 기능 추가
- CLOVA Speech diarization 옵션 활성화
- speakerCountMin/Max = 2 설정 (상담사 + 어르신)
- 화자 1 (label: '1') → 상담사 → qa_log type = 'Q'
- 화자 2 (label: '2') → 어르신 → qa_log type = 'A'
- 화자 분리 실패 시 전문을 어르신 발화(A)로 저장하는 fallback 처리

#### 3. DB 연동 방식 변경 (SQLAlchemy → psycopg2)
- models.py / database.py 기반 SQLAlchemy ORM에서
  psycopg2 직접 SQL 방식으로 변경
- 이미 schema_v2.sql로 테이블이 생성되어 있어
  별도 ORM 모델 정의 불필요 판단
- RETURNING id 구문으로 noun 저장 후 즉시 id 확보,
  adjective/verb FK 연결에 활용

#### 4. 형태소 분석 연동 방식 수정
- promptmaker_first.py의 split_sentences_by_connective()가
  (text, tokens) 튜플 리스트를 반환하도록 업데이트됨에 따라
  test_clova.py 호출 방식 수정
```python
  # 변경 전
  sentences = split_sentences_by_connective(text)
  for sentence in sentences:
      result = extract_nouns_adjectives_verbs(sentence)

  # 변경 후
  split_result = split_sentences_by_connective(text)
  for sentence, tokens in split_result:
      result = extract_nouns_adjectives_verbs(sentence, tokens)
```
- 어르신(화자 2) 발화만 형태소 분석 후 DB 저장
- 위험어 감지(DANGER_WORD_DETECTED) 시 저장 생략 처리 추가

#### 5. 테스트용 세션 초기화 함수 추가
- promptmaker.py에 clear_session_data() 함수 추가
- 테스트 실행 전 해당 세션 데이터 전체 삭제 후 재저장
```python
  promptmaker.clear_session_data(SESSION_ID)
```
- 삭제 순서: adjective → verb → noun → qa_log → album
  (FK 제약 조건으로 자식 테이블부터 삭제 필요)

---

## 파일별 변경 사항

| 파일 | 변경 내용 |
|------|---------|
| clova_speech.py | 장문 API 전환, 화자 분리 추가, segments 반환 |
| promptmaker.py | psycopg2로 전환, save_segments_to_db() 추가, clear_session_data() 추가 |
| test_clova.py | 튜플 언패킹 방식으로 수정, 초기화 함수 호출 추가 |
| promptmaker_first.py | 팀원 A - split_sentences_by_connective() 튜플 반환으로 변경 |

---

## 현재 동작 흐름
음성 파일 (.mp3)
↓
CLOVA Speech 장문 인식 API
↓ 화자 분리 (Diarization)
화자 1(상담사) → qa_log (type='Q')
화자 2(어르신) → qa_log (type='A')
↓ 어르신 발화만
형태소 분석 (promptmaker_first.py)
split_sentences_by_connective() → (text, tokens) 튜플
extract_nouns_adjectives_verbs(sentence, tokens)
↓
noun / adjective / verb 테이블 저장
↓ (향후)
DALL-E 이미지 생성 → album 테이블 저장

---

## DB 확인 쿼리

```sql
-- 화자별 원문 확인
SELECT
    order_index AS 순서,
    CASE type
        WHEN 'Q' THEN '상담사'
        WHEN 'A' THEN '어르신'
    END AS 화자,
    text AS 원문
FROM qa_log
WHERE session_id = 1
ORDER BY order_index;

-- 명사별 형용사, 동사 한눈에 보기
SELECT
    n.value                                AS 명사,
    n.target_noun                          AS 대상,
    STRING_AGG(DISTINCT a.value, ', ')     AS 형용사,
    STRING_AGG(DISTINCT v.value, ', ')     AS 동사
FROM noun n
LEFT JOIN adjective a ON a.noun_id = n.id
LEFT JOIN verb      v ON v.noun_id = n.id
WHERE n.session_id = 1
GROUP BY n.id, n.value, n.target_noun
ORDER BY n.id;
```

---

## 트러블슈팅

| 오류 | 원인 | 해결 |
|------|------|------|
| UniqueViolation | 같은 session_id로 중복 저장 | clear_session_data() 먼저 호출 |
| Response ended prematurely | 장문 파일 처리 시간 초과 | timeout=300 설정 |
| ERROR_INVALID_SECRET | .env의 Secret Key 오류 | 콘솔에서 실제 발급된 키 확인 |
| 400 file format incorrect | m4a 포맷 미지원 | mp3로 변환 후 사용 |
| AttributeError: tuple has no attribute strip | split 반환값 변경 | for sentence, tokens in split_result 로 수정 |
| ModuleNotFoundError: extract | extract.py 없음 | promptmaker_first.py를 extract.py로 이름 변경 |

---

## 제작 환경
- Python 3.13
- PostgreSQL 17
- CLOVA Speech API (장문 인식)
