"""파라미터 파생 규칙(service/policy/param_derive.py) — 성질로: 남긴 값은 모두 원문에 있다(설계 #9 완료 기준) · 남김 + 버림 = 입력 ·
남긴 것끼리 중복 없음 · 최대 12개 · LLM이 무엇을 내든 파싱이 죽지 않는다."""
import importlib.util
import random
from pathlib import Path

_spec = importlib.util.spec_from_file_location("param_derive", Path(__file__).resolve().parent.parent / "service" / "policy" / "param_derive.py")
pd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pd)

BODY = "온라인스토어 주문 30,000원 이상 무료배송. 미만이면 배송비 3,000원. 반품은 수령 후 7일 이내."


def test_kept_values_are_in_text_and_partition_holds():
    rng = random.Random(1)
    pool = ["30,000", "30000", "3,000", "7", "7일", "5,000", "10%", "", "999", "30 000"]
    for _ in range(200):
        params = [{"name": rng.choice(["기준", "배송비", "기한"]), "condition": rng.choice([None, "온라인", ""]), "value": rng.choice(pool)}
                  for _ in range(rng.randint(0, 20))]
        keep, drop = pd.validate_params(params, BODY)
        assert len(keep) + len(drop) == len(params)
        assert len(keep) <= pd.MAX_PARAMS
        norm = pd._norm(BODY)
        assert all(pd._norm(p["value"]) and pd._norm(p["value"]) in norm for p in keep)   # 만든 값 0
        keys = [(p["name"], p.get("condition") or "", pd._norm(p["value"])) for p in keep]
        assert len(keys) == len(set(keys))


def test_comma_and_space_insensitive_but_invented_dropped():
    keep, drop = pd.validate_params([{"name": "기준", "value": "30000"}, {"name": "기준2", "value": "50,000"}], BODY)
    assert [p["value"] for p in keep] == ["30000"] and [p["value"] for p in drop] == ["50,000"]


def test_parse_never_raises():
    for text in ["", "엉망", '{"params": "x"}', '{"params": [1, {"name": "a"}, {"name": "b", "value": "3"}]}',
                 '앞 {"params": [{"name": "기준", "value": "30,000", "unit": "원", "condition": "온라인"}]} 뒤', "{bad json"]:
        out = pd.parse_params(text)
        assert isinstance(out, list) and all(p["name"] and p["value"] for p in out)
    assert pd.parse_params('{"params": [{"name": "기준", "value": "30,000", "unit": "원"}]}')[0]["unit"] == "원"


def test_number_boundaries_respected():
    """리뷰 L2: 정규화 후 부분 문자열로 비교하면 "3"이 "30,000" 안에서, "13"이 "1 3"에서 통과했다 — 숫자 경계를 지킨다."""
    body = "주문 30,000원 이상 무료. 수령 후 7일 이내. 1 4 등급"
    ok = {"30000", "30,000", "7", "7일", "30,000원"}
    bad = {"3", "300", "0", "14", "000", "70", "1 4 5"}   # "14"는 "1 4"에서 안 됨(숫자가 붙지 않음)
    for v in ok:
        assert pd.value_in_text(v, body), v
    for v in bad:
        assert not pd.value_in_text(v, body), v
    # 목록 값(쉼표로 나열) — 숫자 사이가 아닌 쉼표는 그대로 통과(첫 판 규칙이 899개 중 31개를 잘못 버렸다)
    lst = "등급: Welcome, Green, Gold · 금액: 5,000원, 10,000원 · 30,000 원"
    for v in ("Welcome, Green, Gold", "Welcome,Green,Gold", "5,000원, 10,000원", "5000원, 10000원", "30,000원", "30000 원"):
        assert pd.value_in_text(v, lst), v
    assert not pd.value_in_text("Welcome Green", lst)   # 쉼표를 빼면 다른 글자
    # 값 안 숫자 사이 공백은 공백끼리("10:30 1회") — 실제 파생 값에서 나온 모양(재검사 2건)
    t2 = "운영 10:30 1회, 기간 D-30 ~ D-1 15시"
    for v in ("10:30 1회", "D-30 ~ D-1 15시", "10:30"):
        assert pd.value_in_text(v, t2), v
    assert not pd.value_in_text("10:301회", t2) and not pd.value_in_text("D-115시", t2)
    assert not pd.value_in_text("", body) and not pd.value_in_text("   ", body)


def test_kept_values_pass_the_same_rule_as_ingest_check():
    """파생이 남긴 값은 적재 검수(policy_param_in_text)도 통과 — 둘이 같은 함수(리뷰 L3: SQL position은 쉼표 차이를 오탐)."""
    keep, _ = pd.validate_params([{"name": "기준", "value": "30000"}, {"name": "배송비", "value": "3,000"}], BODY)
    assert keep and all(pd.value_in_text(p["value"], BODY) for p in keep)


def test_reviewer_probe_cases():
    """재리뷰가 찾은 경계: 소수점·전각 숫자·목록 쉼표·쉼표 뒤 공백·앞쉼표·문장부호만 값."""
    assert not pd.value_in_text("3", "할인 0.3% · 기간 3.5일")          # 소수점으로 붙은 숫자 조각
    assert pd.value_in_text("0.3%", "할인 0.3% · 기간 3.5일") and pd.value_in_text("3.5일", "할인 0.3% · 기간 3.5일")
    assert not pd.value_in_text("１", "１０개") and not pd.value_in_text("１０", "１００")   # 전각 숫자도 경계
    assert not pd.value_in_text("12", "후보 1,2 중")                     # 목록 쉼표는 천 단위가 아님
    assert pd.value_in_text("1,200", "1200원") and pd.value_in_text("1200", "1,200원")
    assert not pd.value_in_text("000", "30, 000원")                       # 쉼표 뒤 공백으로 붙은 조각
    assert not pd.value_in_text(",000", "30,000원")                       # 앞 쉼표 떼고 "000" — 조각
    for v in (",", "-", " , ", "~"):
        assert not pd.value_in_text(v, "가, 나 - 다 ~ 라")                # 글자·숫자 없는 값은 값이 아님


def test_comma_runs_are_linear():
    """재리뷰 M2: 쉼표가 이어진 값에서 패턴이 겹쳐 시간이 지수로 늘던 것 — 길게 해도 금방 끝나야."""
    import time
    body = ", " * 5000
    t0 = time.monotonic()
    assert not pd.value_in_text("," * 40 + "x", body)
    assert time.monotonic() - t0 < 1.0


def test_comma_inside_value_is_thousands_only_at_3_digit_groups():
    """리뷰 M2: 값 쪽 쉼표 — 천 단위(앞 1~3자리·뒤 정확히 3자리)만 있어도 없어도, 그 밖(목록 "1,2"·"3,0000")은 쉼표 그대로."""
    assert not pd.value_in_text("1,2", "12개") and not pd.value_in_text("1,5", "15일")
    assert pd.value_in_text("1,2", "후보 1,2 중") and pd.value_in_text("1,2,3", "1,2,3등급")
    assert not pd.value_in_text("3,0000", "30,000원")
    for v, body in (("1,200", "1200원"), ("12,345", "12345원"), ("1,234,567", "1234567원"), ("1,234,567", "1,234,567원")):
        assert pd.value_in_text(v, body), (v, body)
    assert not pd.value_in_text("1,234", "11,234원")   # 앞 숫자에 붙은 조각


def test_value_in_text_never_accepts_value_absent_from_text():
    """성질: 원문에서 숫자·쉼표만 지운/더한 무작위 변형 값은, 원문에 같은 숫자열이 없으면 받지 않는다(받으면 숫자 열이 같아야 한다)."""
    import random
    import re
    rnd = random.Random(26)
    for _ in range(400):
        digits = "".join(rnd.choice("0123456789") for _ in range(rnd.randint(1, 7)))
        body = f"기준 {digits}원"
        cand = list(rnd.choice([digits, digits[:-1] or "9", digits + "0"]))
        for _ in range(rnd.randint(0, 2)):
            cand.insert(rnd.randint(1, max(1, len(cand))), ",")
        v = "".join(cand).strip(",")
        if pd.value_in_text(v, body):
            assert re.sub(r"\D", "", v) == digits, (v, body)

