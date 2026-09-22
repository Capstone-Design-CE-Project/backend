-- ============================================================
-- 치매 어르신 상담 앱 - DB 스키마 v2
-- PostgreSQL 16
-- 현재 단계: 클로바 없이 .json 파일만 사용
-- ============================================================

-- ── 어르신 유저 ───────────────────────────────────────────────
CREATE TABLE users (
    id          BIGSERIAL    PRIMARY KEY,
    name        VARCHAR(50)  NOT NULL,
    created_at  TIMESTAMP    NOT NULL DEFAULT NOW()
);

-- ── 상담 세션 (json 파일 하나 = 세션 하나) ─────────────────────
CREATE TABLE session (
    id              BIGSERIAL   PRIMARY KEY,
    user_id         BIGINT      NOT NULL REFERENCES users(id),
    raw_filename    VARCHAR(255),            -- 원본 json 파일명
    created_at      TIMESTAMP   NOT NULL DEFAULT NOW()
);

-- ── 대화 원문 전체 로그 ────────────────────────────────────────
-- Q&A 원문을 순서대로 전부 보존
CREATE TABLE qa_log (
    id          BIGSERIAL   PRIMARY KEY,
    session_id  BIGINT      NOT NULL REFERENCES session(id),
    type        CHAR(1)     NOT NULL CHECK (type IN ('Q', 'A')),
    text        TEXT        NOT NULL,        -- 원문 텍스트 그대로
    order_index INT         NOT NULL,        -- Q&A 순서 번호 (1부터)
    UNIQUE (session_id, order_index, type)
);

-- ── 명사 (imagine 객체 하나 = 1행) ───────────────────────────
CREATE TABLE noun (
    id              BIGSERIAL    PRIMARY KEY,
    session_id      BIGINT       NOT NULL REFERENCES session(id),
    value           VARCHAR(100) NOT NULL,   -- 명사값 (예: "등대")
    target_noun     VARCHAR(100),            -- 대상 명사 (예: 배→등대), 없으면 NULL
    is_base_noun    BOOLEAN      NOT NULL DEFAULT FALSE  -- 세팅 기준 명사 여부
);

-- ── 형용사 ───────────────────────────────────────────────────
CREATE TABLE adjective (
    id          BIGSERIAL    PRIMARY KEY,
    noun_id     BIGINT       NOT NULL REFERENCES noun(id),
    value       VARCHAR(100) NOT NULL        -- 형용사값 (예: "남색")
);

-- ── 동사 ─────────────────────────────────────────────────────
CREATE TABLE verb (
    id          BIGSERIAL    PRIMARY KEY,
    noun_id     BIGINT       NOT NULL REFERENCES noun(id),
    value       VARCHAR(100) NOT NULL        -- 동사값 (예: "묶이")
);

-- ── 생성된 이미지 앨범 ─────────────────────────────────────────
CREATE TABLE album (
    id          BIGSERIAL   PRIMARY KEY,
    session_id  BIGINT      NOT NULL REFERENCES session(id),
    image_url   TEXT        NOT NULL,        -- DALL-E 생성 이미지 URL
    prompt      TEXT,                        -- 실제 사용된 프롬프트
    created_at  TIMESTAMP   NOT NULL DEFAULT NOW()
);

-- ── 인덱스 ───────────────────────────────────────────────────
CREATE INDEX idx_session_user    ON session(user_id);
CREATE INDEX idx_qa_session      ON qa_log(session_id, order_index);
CREATE INDEX idx_noun_session    ON noun(session_id);
CREATE INDEX idx_adj_noun        ON adjective(noun_id);
CREATE INDEX idx_verb_noun       ON verb(noun_id);
CREATE INDEX idx_album_session   ON album(session_id);

-- ── 코멘트 ───────────────────────────────────────────────────
COMMENT ON TABLE session    IS '상담 세션 - json 파일 하나당 1개';
COMMENT ON TABLE qa_log     IS 'Q&A 원문 전체 보존';
COMMENT ON TABLE noun       IS '추출된 명사 - imagine 객체 하나당 1행';
COMMENT ON TABLE adjective  IS '명사에 달린 형용사';
COMMENT ON TABLE verb       IS '명사에 달린 동사';
COMMENT ON TABLE album      IS 'DALL-E 생성 이미지';

-- ============================================================
-- 예시 데이터 삽입
-- json 예시 기준
-- ============================================================

INSERT INTO users (name) VALUES ('이순자');

INSERT INTO session (user_id, raw_filename)
VALUES (1, 'test.json');

-- 대화 원문 전체
INSERT INTO qa_log (session_id, type, text, order_index) VALUES
(1, 'Q', '지금 어떤 풍경이 떠오르나요?',  1),
(1, 'A', '바닷가가 보여요',                1),
(1, 'Q', '바닷가에는 뭐가 있나요?',        2),
(1, 'A', '등대와 배가 보입니다.',           2),
(1, 'Q', '등대는 어떤 색깔인가요?',         3),
(1, 'A', '남색입니다.',                     3),
(1, 'Q', '배는 어떤색인가요?',              4),
(1, 'A', '흰색입니다.',                     4),
(1, 'Q', '배는 어디에 있나요?',             5),
(1, 'A', '등대 옆에 묶여있습니다.',         5);

-- 명사
INSERT INTO noun (session_id, value, target_noun, is_base_noun) VALUES
(1, '바닷가', NULL,   TRUE),
(1, '등대',   NULL,   FALSE),
(1, '배',     '등대', FALSE);

-- 형용사
INSERT INTO adjective (noun_id, value) VALUES
(2, '남색'),
(3, '흰색');

-- 동사
INSERT INTO verb (noun_id, value) VALUES
(3, '묶이');
