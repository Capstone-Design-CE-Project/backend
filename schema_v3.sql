-- ============================================================
-- 기억의 조각 - 치매 어르신 회상 치료 상담 앱 DB 스키마 v3
-- PostgreSQL 16+
--
-- v2 대비 변경점
--   + counselor          상담사 계정 (상담사 앱 로그인)
--   + users 확장         나이/성별/연락처/메모/인지단계/고유번호/PIN 로그인
--   + session 확장       상태·제목·요약·키워드·참조번호·녹음시간
--   + qa_log 확장        화자 라벨, 발화 시각
--   + album 확장         제목, 장면 설명, 선택 여부
--   + appointment        상담 일정(캘린더)
--   + notification       상담사 알림
--   + client_setting     어르신 앱 접근성 설정
-- ============================================================

-- ── 상담사 ───────────────────────────────────────────────────
CREATE TABLE counselor (
    id                BIGSERIAL    PRIMARY KEY,
    login_id          VARCHAR(50)  NOT NULL UNIQUE,
    password_hash     VARCHAR(255) NOT NULL,
    name              VARCHAR(50)  NOT NULL,
    email             VARCHAR(120),
    phone             VARCHAR(20),
    profile_image_url TEXT,
    is_active         BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMP    NOT NULL DEFAULT NOW()
);

-- ── 내담자(어르신) ───────────────────────────────────────────
CREATE TABLE users (
    id                BIGSERIAL    PRIMARY KEY,
    counselor_id      BIGINT       REFERENCES counselor(id),
    name              VARCHAR(50)  NOT NULL,
    age               INT,
    gender            CHAR(1)      CHECK (gender IN ('M', 'F')),
    phone             VARCHAR(20),
    phone_digits      VARCHAR(20),             -- 숫자만 남긴 전화번호(앱 로그인 조회용)
    memo              TEXT,                    -- 상담 메모 및 특이사항
    cognitive_stage   VARCHAR(50),             -- '경도 인지 장애' 등
    member_code       VARCHAR(20)  UNIQUE,     -- 고유 번호 #MEM-7290
    login_id          VARCHAR(50)  UNIQUE,     -- 어르신 앱 ID
    pin_hash          VARCHAR(255),            -- 4~6자리 PIN 해시
    profile_image_url TEXT,
    last_visit_at     TIMESTAMP,
    is_active         BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMP    NOT NULL DEFAULT NOW()
);

-- ── 상담 세션 ────────────────────────────────────────────────
CREATE TABLE session (
    id               BIGSERIAL    PRIMARY KEY,
    user_id          BIGINT       NOT NULL REFERENCES users(id),
    counselor_id     BIGINT       REFERENCES counselor(id),
    title            VARCHAR(120),             -- '고향집 마당'
    status           VARCHAR(20)  NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending','recording','transcribed',
                                       'summarized','image_ready','completed','failed')),
    raw_filename     VARCHAR(255),
    audio_path       TEXT,
    duration_seconds INT          DEFAULT 0,
    transcript       TEXT,                     -- STT 전문
    summary          TEXT,                     -- AI 요약(상담사 수정 가능)
    keywords         TEXT,                     -- 핵심 기억 키워드(콤마 구분)
    analysis_json    TEXT,                     -- 형태소/관계 분석 원본(JSON)
    reference_code   VARCHAR(30)  UNIQUE,      -- 참조 번호 #CC-8821-XP
    started_at       TIMESTAMP,
    ended_at         TIMESTAMP,
    created_at       TIMESTAMP    NOT NULL DEFAULT NOW()
);

-- ── 대화 원문 ────────────────────────────────────────────────
CREATE TABLE qa_log (
    id            BIGSERIAL   PRIMARY KEY,
    session_id    BIGINT      NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    type          CHAR(1)     NOT NULL CHECK (type IN ('Q', 'A')),  -- Q=상담사
    text          TEXT        NOT NULL,
    order_index   INT         NOT NULL,
    speaker_label VARCHAR(10),          -- CLOVA 화자 라벨 원본
    spoken_at_ms  INT,                  -- 발화 시작 시각(ms)
    UNIQUE (session_id, order_index, type)
);

-- ── 형태소 분석 결과 ─────────────────────────────────────────
CREATE TABLE noun (
    id           BIGSERIAL    PRIMARY KEY,
    session_id   BIGINT       NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    value        VARCHAR(100) NOT NULL,
    target_noun  VARCHAR(100),
    is_base_noun BOOLEAN      NOT NULL DEFAULT FALSE
);

CREATE TABLE adjective (
    id      BIGSERIAL    PRIMARY KEY,
    noun_id BIGINT       NOT NULL REFERENCES noun(id) ON DELETE CASCADE,
    value   VARCHAR(100) NOT NULL
);

CREATE TABLE verb (
    id           BIGSERIAL    PRIMARY KEY,
    noun_id      BIGINT       NOT NULL REFERENCES noun(id) ON DELETE CASCADE,
    value        VARCHAR(100) NOT NULL,
    polarity_bit INT          NOT NULL DEFAULT 1   -- 1=긍정, 0=부정
);

-- ── 회상 이미지 앨범 ─────────────────────────────────────────
CREATE TABLE album (
    id          BIGSERIAL   PRIMARY KEY,
    session_id  BIGINT      NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    title       VARCHAR(120),            -- '평화로운 해변'
    image_url   TEXT        NOT NULL,
    prompt      TEXT,
    scene       TEXT,                    -- 한국어 장면 설명
    is_selected BOOLEAN     NOT NULL DEFAULT FALSE,   -- '이대로 저장'된 이미지
    created_at  TIMESTAMP   NOT NULL DEFAULT NOW()
);

-- ── 상담 일정 ────────────────────────────────────────────────
CREATE TABLE appointment (
    id               BIGSERIAL   PRIMARY KEY,
    counselor_id     BIGINT      NOT NULL REFERENCES counselor(id),
    client_id        BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    scheduled_at     TIMESTAMP   NOT NULL,
    duration_minutes INT         NOT NULL DEFAULT 60,
    purpose          VARCHAR(120),
    place            VARCHAR(120),
    memo             TEXT,
    status           VARCHAR(20) NOT NULL DEFAULT 'scheduled'
                     CHECK (status IN ('scheduled', 'done', 'canceled')),
    created_at       TIMESTAMP   NOT NULL DEFAULT NOW()
);

-- ── 상담사 알림 ──────────────────────────────────────────────
CREATE TABLE notification (
    id              BIGSERIAL    PRIMARY KEY,
    counselor_id    BIGINT       NOT NULL REFERENCES counselor(id) ON DELETE CASCADE,
    type            VARCHAR(30)  NOT NULL DEFAULT 'info',
    title           VARCHAR(120) NOT NULL,
    body            TEXT,
    link_session_id BIGINT       REFERENCES session(id) ON DELETE SET NULL,
    is_read         BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMP    NOT NULL DEFAULT NOW()
);

-- ── 어르신 앱 접근성 설정 ────────────────────────────────────
CREATE TABLE client_setting (
    user_id         BIGINT      PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    font_scale      VARCHAR(10) NOT NULL DEFAULT 'large'
                    CHECK (font_scale IN ('medium', 'large', 'xlarge')),
    high_contrast   BOOLEAN     NOT NULL DEFAULT FALSE,
    haptic_feedback BOOLEAN     NOT NULL DEFAULT TRUE,
    show_help_tips  BOOLEAN     NOT NULL DEFAULT TRUE,
    updated_at      TIMESTAMP   NOT NULL DEFAULT NOW()
);

-- ── 인덱스 ───────────────────────────────────────────────────
CREATE INDEX idx_users_counselor    ON users(counselor_id);
CREATE INDEX idx_users_phone_digits ON users(phone_digits);
CREATE INDEX idx_session_user       ON session(user_id);
CREATE INDEX idx_session_counselor  ON session(counselor_id, created_at DESC);
CREATE INDEX idx_qa_session         ON qa_log(session_id, order_index);
CREATE INDEX idx_noun_session       ON noun(session_id);
CREATE INDEX idx_adj_noun           ON adjective(noun_id);
CREATE INDEX idx_verb_noun          ON verb(noun_id);
CREATE INDEX idx_album_session      ON album(session_id);
CREATE INDEX idx_appointment_time   ON appointment(counselor_id, scheduled_at);
CREATE INDEX idx_notification_read  ON notification(counselor_id, is_read);

-- ── 코멘트 ───────────────────────────────────────────────────
COMMENT ON TABLE counselor      IS '상담사 계정';
COMMENT ON TABLE users          IS '내담자(어르신)';
COMMENT ON TABLE session        IS '상담 세션 - 녹음 1회 단위';
COMMENT ON TABLE qa_log         IS '화자 분리된 Q&A 원문';
COMMENT ON TABLE noun           IS '추출된 명사 - imagine 객체 하나당 1행';
COMMENT ON TABLE album          IS 'DALL-E 회상 이미지';
COMMENT ON TABLE appointment    IS '상담 일정(캘린더)';
COMMENT ON TABLE notification   IS '상담사 알림';
COMMENT ON TABLE client_setting IS '어르신 앱 접근성 설정';

-- ============================================================
-- v2 → v3 마이그레이션 (기존 데이터가 있을 때)
-- ============================================================
-- ALTER TABLE users   ADD COLUMN counselor_id BIGINT REFERENCES counselor(id);
-- ALTER TABLE users   ADD COLUMN age INT;
-- ALTER TABLE users   ADD COLUMN gender CHAR(1);
-- ALTER TABLE users   ADD COLUMN phone VARCHAR(20);
-- ALTER TABLE users   ADD COLUMN phone_digits VARCHAR(20);
-- CREATE INDEX idx_users_phone_digits ON users(phone_digits);
-- UPDATE users SET phone_digits = regexp_replace(phone, '[^0-9]', '', 'g') WHERE phone IS NOT NULL;
-- ALTER TABLE users   ADD COLUMN memo TEXT;
-- ALTER TABLE users   ADD COLUMN cognitive_stage VARCHAR(50);
-- ALTER TABLE users   ADD COLUMN member_code VARCHAR(20) UNIQUE;
-- ALTER TABLE users   ADD COLUMN login_id VARCHAR(50) UNIQUE;
-- ALTER TABLE users   ADD COLUMN pin_hash VARCHAR(255);
-- ALTER TABLE users   ADD COLUMN profile_image_url TEXT;
-- ALTER TABLE users   ADD COLUMN last_visit_at TIMESTAMP;
-- ALTER TABLE users   ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT TRUE;
-- ALTER TABLE session ADD COLUMN counselor_id BIGINT REFERENCES counselor(id);
-- ALTER TABLE session ADD COLUMN title VARCHAR(120);
-- ALTER TABLE session ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'completed';
-- ALTER TABLE session ADD COLUMN audio_path TEXT;
-- ALTER TABLE session ADD COLUMN duration_seconds INT DEFAULT 0;
-- ALTER TABLE session ADD COLUMN transcript TEXT;
-- ALTER TABLE session ADD COLUMN summary TEXT;
-- ALTER TABLE session ADD COLUMN keywords TEXT;
-- v3.2 (분석 엔진 교체)
-- ALTER TABLE session ADD COLUMN analysis_json TEXT;
-- ALTER TABLE verb    ADD COLUMN polarity_bit INT NOT NULL DEFAULT 1;
-- ALTER TABLE session ADD COLUMN reference_code VARCHAR(30) UNIQUE;
-- ALTER TABLE session ADD COLUMN started_at TIMESTAMP;
-- ALTER TABLE session ADD COLUMN ended_at TIMESTAMP;
-- ALTER TABLE qa_log  ADD COLUMN speaker_label VARCHAR(10);
-- ALTER TABLE qa_log  ADD COLUMN spoken_at_ms INT;
-- ALTER TABLE album   ADD COLUMN title VARCHAR(120);
-- ALTER TABLE album   ADD COLUMN scene TEXT;
-- ALTER TABLE album   ADD COLUMN is_selected BOOLEAN NOT NULL DEFAULT FALSE;
