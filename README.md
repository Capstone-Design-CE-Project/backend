# 기억의 조각 — 치매 어르신 회상 치료 상담 앱 백엔드

상담사 앱 / 어르신 앱 두 클라이언트를 하나의 FastAPI 서버로 지원합니다.

```
[상담사]  로그인 → 대시보드 → 내담자 관리 → 상담 일정
          → 녹음 → STT·화자분리 → 형태소·관계 분석
          → AI 요약 → 회상 이미지 생성 → 리포트 저장

[어르신]  전화번호 + PIN 로그인 → 홈 → 앨범 → 요약 보기 → 접근성 설정
```

> 형태소 분석기 자체의 설명은 `readme.md`(팀원 A 작성)를 참고하세요.

---

## 1. 빠른 시작

```bash
pip install -r requirements.txt
cp .env.example .env          # 없어도 동작합니다 (아래 '키 없이 개발하기')

python -m scripts.seed_demo   # 디자인 시안과 동일한 데모 데이터
uvicorn main:app --reload --host 0.0.0.0
```

API 문서: `http://localhost:8000/docs` · 상태 확인: `/health`

### 데모 계정

| 구분 | 아이디 | 비밀번호 |
|------|--------|----------|
| 상담사 | `counselor_lee` | `capstone1234` |
| 어르신 | `010-3333-4444` | PIN `123456` |

### 키 없이 개발하기

| 구성 | 키가 있을 때 | 키가 없을 때 |
|------|--------------|--------------|
| DB | PostgreSQL | `./capstone.db` (SQLite 자동 폴백) |
| STT | CLOVA Speech 장문 인식 | 목(mock) 녹취록 8발화 |
| 요약 | OpenAI `gpt-4o-mini` | 규칙 기반 추출 요약 |
| 이미지 | DALL-E 3 | 플레이스홀더 URL |
| 형태소·관계 분석 | konlpy (JDK 필요) | 건너뜀 (요약 키워드로 대체) |

---

## 2. 파일 구조

```
backend/
├── main.py                  FastAPI 진입점 (라우터, CORS, 헬스체크)
├── database.py              엔진/세션/Base (+ SQLite 폴백)
├── models.py                ORM 모델 10개
├── schemas.py               요청/응답 스키마 (Pydantic v2)
├── promptmaker.py           qa_log·분석결과 저장 + 장면 설명/프롬프트 조합
├── clova_speech.py          CLOVA Speech 장문 인식 + 화자 분리
├── schema_v3.sql            PostgreSQL 스키마 (+ 마이그레이션 구문)
│
├── counseling_analysis_interface.py            분석 인터페이스 (팀원 A)
├── prompt_maker_succeed_3_span_experiment.py   분석 엔진 (팀원 A)
├── promptmaker_first.py                        구 분석기 (미사용, 참고용)
├── analysis_cases/                             분석 테스트 케이스 20턴 × 10건
│
├── core/
│   ├── config.py            설정 (.env 로딩)
│   ├── security.py          bcrypt 해싱 + JWT
│   ├── deps.py              인증 의존성 (상담사/어르신 분리)
│   ├── phone.py             전화번호 정규화
│   └── timeutil.py          시간대 처리
├── services/
│   ├── stt_service.py       STT 래퍼 + 업로드 저장 + 화자 분리
│   ├── morph_service.py     분석기 래퍼 + Q/A 입력 변환
│   ├── summary_service.py   AI 요약 + 규칙 기반 폴백
│   ├── image_service.py     DALL-E 래퍼 + 폴백
│   └── serializers.py       ORM → 응답 변환
├── routers/
│   ├── auth.py  dashboard.py  clients.py
│   ├── appointments.py  sessions.py  client_app.py
│   └── pipeline.py          레거시 호환 (구 Flutter ApiService)
├── scripts/                 seed_demo.py, check_db.py, check_stt_pipeline.py
└── tests/                   test_flow.py, test_analysis.py
```

---

## 3. API — 화면별 대응

로그인 후 받은 토큰을 `Authorization: Bearer <access_token>`으로 보냅니다.
상담사 토큰과 어르신 토큰은 역할이 분리되어 서로의 API를 호출할 수 없습니다(403).

### 3.1 인증

| 화면 | 메서드 | 경로 |
|------|--------|------|
| 상담사 로그인 | POST | `/auth/counselor/login` |
| 내담자 로그인 | POST | `/auth/client/login` (전화번호 가능, 3.8 참고) |
| 공통 | GET | `/auth/me` |
| 회원가입 | POST | `/auth/counselor/register` |
| 중복 확인 | GET | `/auth/check?login_id=` 또는 `?phone=` |
| 내 정보 수정 | PATCH | `/auth/counselor/me` |
| 비밀번호 변경 | POST | `/auth/counselor/password` |

#### 회원가입 (v3.3)

`POST /auth/counselor/register`

```json
{
  "login_id": "counselor_kim",
  "password": "8자 이상",
  "name": "김상담",
  "phone": "010-1234-5678",
  "email": "kim@example.com",
  "organization": "행복요양병원"
}
```

**가입 범위**는 `.env`의 `OPEN_COUNSELOR_SIGNUP`으로 정한다.

| 값 | 동작 |
|---|---|
| `False` (기본) | 첫 계정만 무인증 생성, 이후에는 기존 상담사 토큰 필요 (초대 방식) |
| `True` | 누구나 가입 가능 (공개 회원가입 페이지용) |

공개 가입을 켜면 만들어진 계정이 내담자를 등록하고 상담 기록을 볼 수 있으므로,
기관 인증·승인 절차를 별도로 두는 것을 권장한다.

**중복 검사**는 아이디와 전화번호 양쪽에 적용된다.

- 아이디: 상담사·내담자를 통틀어 중복 불가 (같은 아이디 공간을 쓴다)
- 전화번호: 표기가 달라도 같은 번호면 중복 (`+82 10-1234-5678` = `01012345678`)
  어르신이 전화번호로 로그인하므로, 중복을 허용하면 로그인이 409로 막힌다
- 내담자 등록(`POST /clients`)·수정에도 같은 검사가 걸린다

회원가입 화면에서는 제출 전에 `GET /auth/check`로 미리 확인할 수 있다(인증 불필요).

```
GET /auth/check?login_id=counselor_kim
  → {"field":"login_id","value":"counselor_kim","available":false,
     "reason":"이미 사용 중인 아이디입니다."}

GET /auth/check?phone=%2B82%2010-1234-5678
  → {"field":"phone","value":"01012345678","available":false,
     "reason":"이미 등록된 전화번호입니다."}
```

### 3.2 대시보드 / 알림

`GET /dashboard` · `GET /notifications` · `POST /notifications/{id}/read` · `POST /notifications/read-all`

### 3.3 내담자 관리

`GET|POST /clients` · `GET|PATCH|DELETE /clients/{id}` · `GET /clients/{id}/sessions`

#### 내담자 추가 (상담사 로그인 후)

```json
POST /clients
{
  "name": "김순자",
  "gender": "F",            // 'M' | 'F'
  "age": 80,
  "phone": "010-1234-5678",
  "pin": "246810",          // 6자리 숫자
  "memo": "고향 이야기에 반응이 좋음",
  "cognitive_stage": "경도 인지 장애"
}
```

`name` 외에는 모두 선택이며, 고유 번호(`#MEM-xxxx`)는 서버가 발급합니다.

**검증 규칙** — 앱 로그인 화면과 맞춰 두어, 등록은 됐는데 어르신이
로그인하지 못하는 계정이 생기지 않게 합니다.

| 항목 | 규칙 | 위반 시 |
|---|---|---|
| `pin` | **6자리 숫자**(`^\d{6}$`). 앱이 6자리만 입력받는다 | 422 |
| `pin` + 연락 수단 | `pin`을 주면 `phone` 또는 `login_id` 중 하나는 필요 | 422 |
| `phone` | 상담사·내담자를 통틀어 중복 불가 (표기 차이 무시) | 409 |
| `login_id` | 상담사·내담자를 통틀어 중복 불가 | 409 |

`pin`을 주지 않으면 `AUTO_ISSUE_CLIENT_PIN` 설정에 따라 기본 PIN이 발급됩니다(3.8 참고).

### 3.4 상담 일정

`GET /appointments/calendar?year=&month=` · `GET /appointments/upcoming` ·
`GET|POST /appointments` · `PATCH|DELETE /appointments/{id}`

### 3.5 상담 진행

| 화면 | 메서드 | 경로 |
|------|--------|------|
| 상담 시작 | POST | `/sessions` |
| 녹음 완료 | POST | `/sessions/{id}/recording` (multipart) |
| 실시간 텍스트 확인 | GET | `/sessions/{id}/transcript` |
| 텍스트 직접 추가 | POST | `/sessions/{id}/transcript` |
| 요약 생성 / 수정 | POST \| PATCH | `/sessions/{id}/summary` |
| 이미지 생성 / 목록 | POST \| GET | `/sessions/{id}/image` \| `/images` |
| 완료 저장 | POST | `/sessions/{id}/complete` |
| 상세 / 목록 | GET | `/sessions/{id}` \| `/sessions` |

상태 흐름: `pending → recording → transcribed → summarized → image_ready → completed`

### 3.6 어르신 앱

`GET /me/home` · `GET /me/album` · `GET /me/sessions` ·
`GET /me/sessions/{id}/summary` · `GET|PATCH /me/settings`

### 3.7 레거시 파이프라인

`/pipeline/session` · `/pipeline/stt/{id}` · `/pipeline/generate/{id}` ·
`/pipeline/run/{id}` · `/pipeline/album/{id}` · `/pipeline/session/{id}`

운영에서는 `.env`에 `LEGACY_PIPELINE_ENABLED=False`로 끄는 것을 권장합니다(인증 없음).

### 3.8 내담자 로그인 — 전화번호 허용

어르신 앱은 단말 전화번호를 그대로 `login_id`로 보냅니다. 서버는
① `users.login_id` 완전 일치 → ② 숫자만 남긴 `users.phone_digits` 비교 순으로 찾습니다.

```
'+82 10-9876-5432' / '010-9876-5432' / '01098765432' → 모두 '01098765432'
```

같은 번호로 여러 명이 등록돼 있으면 자동 선택하지 않고 **409**를 반환합니다.

**PIN**: 등록 시 `pin`을 주지 않으면 `AUTO_ISSUE_CLIENT_PIN`(기본 True)에 따라
기본 PIN `123456`가 자동 발급됩니다. 프론트에 PIN 입력 UI가 없는 동안의 임시 조치이므로,
**실제 환자 데이터를 다루기 전에 `.env`에 `AUTO_ISSUE_CLIENT_PIN=False`를 넣고**
상담사가 `PATCH /clients/{id}` `{"pin": "..."}`로 직접 발급해야 합니다.

### 3.9 형태소·관계 분석 (v3.2)

분석 엔진이 교체되었습니다. 기존에는 어르신 발화를 **하나씩** 분석했지만,
지금은 상담 **전체 Q/A를 한 번에** `counseling_analysis_interface.analyze()`에 넘깁니다.
질문의 명사와 답변의 수식어를 이어서 해석하기 위해서입니다.

```
Q:감나무는 어떤 모습이었나요?
A:주황색 감이 주렁주렁 열렸습니다.
  → '주황색'이 '감'에 붙고, 앞 턴의 '감나무'와 연결된다
```

**입력 형식이 엄격합니다** — 짝수 개, `Q:`로 시작, Q/A 교대.
CLOVA 화자 분리는 같은 화자가 연달아 말하는 구간이 흔해 그대로는 통과하지 못하므로
`services/morph_service.build_conversation()`이 변환합니다.

| 입력 상황 | 처리 |
|---|---|
| 같은 화자 연속 발화 | 한 턴으로 합침 |
| 어르신(A) 발화로 시작 | 중립 질문을 앞에 삽입 |
| 상담사(Q) 발화로 끝남 | 마지막 질문 버림 |
| Q/A를 만들 수 없음 | 분석 생략 (빈 결과, 예외 없음) |

결과는 `session.analysis_json`에 원본 그대로 저장하고,
동시에 `noun` / `adjective` / `verb` 테이블에도 정규화해 넣습니다.

- `relations[].polarity_bit` → `verb.polarity_bit` (1=긍정, 0=부정).
  "밥을 안 먹었어"를 먹는 장면으로 그리지 않기 위해 보존합니다.
- 장면 설명은 `analysis_json`이 있으면 관계까지 활용하고, 없으면 테이블에서 조합합니다.

**JVM 주의**: konlpy는 JPype로 JVM을 쓰고, FastAPI는 동기 핸들러를 스레드풀에서
실행합니다. 분석을 마친 워커 스레드가 JVM에 붙은 채 남으면 프로세스가 종료되지 않아
(uvicorn·pytest 종료가 멈춤) `morph_service._detach_jvm_thread()`에서 분리합니다.

JDK가 없으면 `.env`에 `ENABLE_MORPH_ANALYSIS=False`를 넣으세요.
분석만 건너뛰고 나머지는 그대로 동작합니다.

### 3.10 시간대 정책

**DB에는 모든 시각을 서비스 기준 시간대(기본 `Asia/Seoul`)의 naive datetime으로 저장**합니다.
`.env`의 `APP_TIMEZONE`으로 바꿀 수 있습니다.

- **요청**: `scheduled_at`은 오프셋이 있든(`...Z`) 없든 모두 받아 기준 시간대로 변환
- **응답**: 오프셋 없는 기준 시간대 문자열. Dart `DateTime.tryParse()`가 로컬로 읽으므로
  앱이 고른 시각과 화면에 보이는 시각이 일치합니다
- **서버 시각**: `created_at` 등도 OS/DB 시간대와 무관하게 기준 시간대로 기록
- 캘린더 그룹핑·오늘 일정 집계도 같은 기준이라 오전 상담이 전날 칸에 들어가지 않습니다

---

## 4. 프론트엔드 연동 메모

1. `AlbumImage.fromJson`이 읽는 `id`/`session_id`/`image_url`/`created_at`을 모두 내려줍니다
   (기존 `album_id`도 유지).
2. `/pipeline/album/{id}`은 배열, 신규 `/me/album`은 `{total, items}` — 파서를 분리하세요.
3. 신규 엔드포인트는 전부 Bearer 토큰이 필요합니다.
4. 시간대는 앱 수정 없이 동작합니다(`toUtc()` 유지 가능, `.toLocal()` 불필요).
5. **Android `main/AndroidManifest.xml`에 `INTERNET` 권한이 필요합니다.**
   debug/profile manifest에만 있으면 release 빌드에서 통신이 전부 실패합니다.
   평문 HTTP라면 `usesCleartextTraffic`(Android) / ATS 예외(iOS)도 필요합니다.
6. 내담자 메모는 `createClient(memo:)`로 서버에 보내세요. 기기에만 두면 공유되지 않습니다.

---

## 5. 테스트

```bash
pytest -q    # 19건 — 외부 키 없이 동작
```

- `tests/test_flow.py` — 화면 플로우 통합 (로그인 → 녹음 → 요약 → 이미지 → 완료 → 어르신 앱)
- `tests/test_analysis.py` — 분석 입력 변환 / DB 저장 / 부정 표현 보존

수동 점검:

```bash
python -m scripts.check_stt_pipeline ./recording.mp3 1
python -m scripts.check_db 1 "남색 등대 옆에 흰색 배가 묶여있어요"
```

---

## 6. 배포 메모

- `.env`는 커밋하지 마세요(`.gitignore`에 포함).
- `JWT_SECRET` 교체: `python -c "import secrets;print(secrets.token_urlsafe(48))"`
- `CORS_ORIGINS`를 실제 앱 도메인으로 좁히세요.
- `AUTO_ISSUE_CLIENT_PIN=False`, `LEGACY_PIPELINE_ENABLED=False`로 전환하세요.
- DALL-E 이미지 URL은 만료되므로 S3 등에 내려받아 재업로드하는 것을 권장합니다.
- 업로드된 음성은 `UPLOAD_DIR`에 저장되고 `/media/...`로 서빙됩니다.
  민감 정보이므로 운영에서는 정적 서빙을 끄고 접근 제어를 붙이세요.
- 형태소 분석을 쓰려면 서버에 JDK가 필요합니다 (`apt install default-jdk`).

## 제작 환경
- Python 3.11+ / PostgreSQL 16+ (개발 시 SQLite 폴백) / JDK 17+ (형태소 분석)
- CLOVA Speech (장문 인식 + 화자 분리) · OpenAI (요약 + DALL-E 3)
