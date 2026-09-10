-- ────────────────────────────────────────────────────────────────────
-- v2.71 마이그레이션 — 정책 RDB(policy_param) 검색에 한국어 조사/어미 규칙 기반 제거 적용
--
-- 배경: to_tsvector('simple', ...)엔 한국어 형태소 분석이 없어 "담을"/"담기"처럼 같은
-- 어간도 조사·어미가 다르면 전혀 다른 lexeme으로 취급돼 매칭이 실패한다. 89문항 골든셋
-- 실측(backend/scripts/bench_suffix_stripping.py, 2026-09-10)으로 규칙 기반 제거의
-- 효과를 확인한 뒤 프로덕션에 적용한다 — 하이브리드(RDB+벡터) 전체 hit@10 75.3%→79.8%.
--
-- policy_param/policy_item 원문 컬럼은 그대로 두고(저장 컬럼 추가 없음), search.py의
-- 쿼리 시점에 이 함수로 실시간 변환한다 — 건수가 389건으로 작아(2026-09-10 기준)
-- 인덱스 없는 함수 호출도 성능에 문제없다.
--
-- 접미사 목록은 backend/service/policy/korean_text.py의 _SUFFIXES와 동일한 집합이어야
-- 한다 — 이 함수는 "일치하는 접미사 중 가장 긴 것"을 제거하는 방식이라(배열 순서 무관),
-- 동일 집합이기만 하면 두 구현이 항상 같은 결과를 낸다.
--
-- 운영 적용 (init/ 디렉토리는 빈 pgdata에서만 자동 실행되므로 수동 실행):
--   docker exec -i ops-postgres psql -U ops -d opsdb < init/06-policy-strip-ko.sql
-- ────────────────────────────────────────────────────────────────────

BEGIN;

CREATE OR REPLACE FUNCTION policy_strip_ko_word(word text) RETURNS text AS $$
DECLARE
    suf text;
    best_len int := 0;
    suffixes text[] := ARRAY[
        '까지는','에서는','으로는','한테는','에게서','으로써',
        '이라도','에서','으로','부터','한테','에게','까지','처럼','보다',
        '이랑','랑','하고','이며',
        '습니다','입니다','겠습니다','습니까',
        '으며','면서','니까','아서','어서','인데','은데',
        '어요','아요','네요','군요',
        '았고','었고',
        '은','는','이','가','을','를','의','도','만','에','로','와','과',
        '고','게','지','며','니','자','다','죠','기','아','어'
    ];
BEGIN
    IF word IS NULL OR word = '' THEN
        RETURN word;
    END IF;
    FOREACH suf IN ARRAY suffixes LOOP
        IF word LIKE '%' || suf AND char_length(word) - char_length(suf) >= 2 THEN
            IF char_length(suf) > best_len THEN
                best_len := char_length(suf);
            END IF;
        END IF;
    END LOOP;
    IF best_len > 0 THEN
        RETURN left(word, char_length(word) - best_len);
    END IF;
    RETURN word;
END;
$$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION policy_strip_ko(input text) RETURNS text AS $$
DECLARE
    w text;
    result text[] := ARRAY[]::text[];
BEGIN
    IF input IS NULL THEN
        RETURN input;
    END IF;
    FOREACH w IN ARRAY regexp_split_to_array(btrim(input), '\s+') LOOP
        result := array_append(result, policy_strip_ko_word(w));
    END LOOP;
    RETURN array_to_string(result, ' ');
END;
$$ LANGUAGE plpgsql IMMUTABLE;

COMMIT;
