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
