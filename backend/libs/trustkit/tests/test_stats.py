"""trustkit.eval.stats(2026-10-13, 앱 tests/에서 옮김 10/19) — 오차 범위·같은 문항 차이·채점기-사람 카파. 손 계산과 맞는지."""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from trustkit.eval import stats as S  # noqa: E402


def test_iid_estimate_matches_hand_calc():
    v = [1.0] * 8 + [0.0] * 2            # 80%, 표본 표준편차 = sqrt(0.8·0.2·10/9)
    e = S.estimate(v)
    assert math.isclose(e["mean"], 0.8)
    assert math.isclose(e["se"], math.sqrt(0.16 * 10 / 9 / 10))
    assert e["crit"] == S.t95(9)                      # 작은 표본은 t 값(1.96이면 구간이 좁게 나와 과신)
    assert math.isclose(e["lo"], 0.8 - e["crit"] * e["se"]) and math.isclose(e["hi"], 0.8 + e["crit"] * e["se"])


def test_clustering_widens_interval_when_errors_cluster():
    """같은 원문 묶음끼리 함께 틀리면 독립 가정보다 오차가 크다 — 묶지 않으면 과신한다."""
    v = [1, 1, 1, 1, 0, 0, 0, 0] * 2
    together = ["a", "a", "a", "a", "b", "b", "b", "b"] * 2   # 틀림이 한 묶음에 몰림
    spread = [str(i) for i in range(len(v))]
    assert S.clustered_se(v, together) > S.clustered_se(v, spread)


def test_paired_diff_flags_only_clear_differences():
    a = {str(i): 1.0 for i in range(40)}
    b = {str(i): (0.0 if i < 20 else 1.0) for i in range(40)}      # 같은 문항 40개 중 20개가 좋아짐
    big = S.paired_diff(a, b, lambda q: q)
    assert big["significant"] and big["mean"] == 0.5
    c = {str(i): (1.0 if i != 0 else 0.0) for i in range(40)}       # 1개만 차이
    small = S.paired_diff(c, a, lambda q: q)
    assert not small["significant"]


def test_kappa_hand_values_and_dominant_label_trap():
    perfect = S.cohen_kappa([("정답", "정답"), ("오답", "오답"), ("부분", "부분")])
    assert perfect["agree"] == 1.0 and math.isclose(perfect["kappa"], 1.0)
    # 원 일치 90%인데 한 라벨이 몰려 우연 일치가 높으면 카파는 낮다(원 일치율만 보면 속는다)
    pairs = [("정답", "정답")] * 18 + [("정답", "오답")] + [("오답", "정답")]
    k = S.cohen_kappa(pairs)
    assert math.isclose(k["agree"], 0.9) and k["kappa"] < 0.0
    assert S.kappa_band(0.85).startswith("우수") and S.kappa_band(0.65).startswith("운영") and S.kappa_band(0.3).startswith("참고")


def test_uncomputable_when_one_cluster_or_one_item():
    """묶음이 1개거나 문항이 1개면 오차를 잴 수 없다 — 0으로 두면 구간 폭 0·"의미 있는 차이"로 잘못 나왔다(리뷰)."""
    assert S.estimate([1.0, 0.0, 1.0], ["a", "a", "a"])["lo"] is None
    assert S.estimate([1.0])["lo"] is None
    one = S.paired_diff({"q": 1.0}, {"q": 0.0}, lambda q: q)
    assert one["lo"] is None and one["significant"] is False


def test_t_critical_values():
    assert S.t95(8) == 2.306 and S.t95(1) == 12.706 and S.t95(22) == 2.086 and 1.96 < S.t95(100) < 2.0

