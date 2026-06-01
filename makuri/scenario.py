"""
makuri - シナリオエンジン

各レースに対して3レイヤーのシナリオスコアを計算する。

Layer 1: 荒れ警報スコア (arare_prob)
Layer 2: まくり試行スコア (p_try, p_success, p_fail)
Layer 3: 1号艇タイプ × まくり成否の組み合わせ判定

出力例:
{
  "arare_prob": 0.72,
  "b1_type": "まくられ型",
  "boat3": {"p_try": 0.45, "p_success": 0.68, "p_fail": 0.32},
  "boat4": {"p_try": 0.22, "p_success": 0.51, "p_fail": 0.49},
  "scenario": "3号艇まくり成功",
  "layer": 2,
}
"""
import json
import os
import pickle
import sys

import numpy as np

import config

# ── モデルキャッシュ ────────────────────────────────────────────
_models = {}

def _load_models():
    global _models
    if _models:
        return _models
    with open(config.MODEL_ARARE_PKL, "rb") as f:
        _models["arare"] = pickle.load(f)
    with open(config.MODEL_MAKKURI_3_PKL, "rb") as f:
        _models["makkuri_3"] = pickle.load(f)
    with open(config.MODEL_MAKKURI_4_PKL, "rb") as f:
        _models["makkuri_4"] = pickle.load(f)
    with open(config.MODEL_META_JSON, encoding="utf-8") as f:
        _models["meta"] = json.load(f)
    with open(config.MODEL_MAKKURI_META_JSON, encoding="utf-8") as f:
        _models["makkuri_meta"] = json.load(f)
    with open(config.PLAYER_STATS_JSON, encoding="utf-8") as f:
        _models["player_stats"] = json.load(f)
    return _models


# ── 定数 ─────────────────────────────────────────────────────────
VENUE_CODES = sorted(config.VENUE_CODES.keys())
GRADE_MAP   = {"A1": 4, "A2": 3, "B1": 2, "B2": 1}
BOATS       = list(range(1, 7))


def _f(v, d=0.0):
    try: return float(v) if v is not None else d
    except: return d


# ── 荒れモデル特徴量（93次元） ───────────────────────────────────
def _build_arare_features(venue: str, boat_data: dict,
                          day_from_start=0, total_days=0,
                          bubble_status=None) -> np.ndarray:
    BASE_COLS  = ["avg_st", "motor_2rate", "national_rate", "local_rate"]
    RANK_COLS  = ["avg_st", "motor_2rate", "national_rate"]
    BUBBLE_COLS= ["qualify", "bubble", "points_before"]

    abs_vals = {f"b{i}_{col}": boat_data[i].get(col, 0.0)
                for col in BASE_COLS for i in BOATS}
    rank_vals = {}
    for col in RANK_COLS:
        vals = [abs_vals[f"b{i}_{col}"] for i in BOATS]
        sp = sorted(enumerate(vals, 1), key=lambda x: x[1],
                    reverse=(col != "avg_st"))
        ranks = {b: r + 1 for r, (b, _) in enumerate(sp)}
        for i in BOATS:
            rank_vals[f"b{i}_{col}_rank"] = float(ranks[i])

    venue_vec = {f"venue_{v}": 1.0 if v == venue else 0.0 for v in VENUE_CODES}
    is_final  = float(day_from_start > 0 and total_days > 0 and day_from_start == total_days)
    series_p  = float(day_from_start / total_days) if total_days > 0 else 0.0

    bs = bubble_status or {}
    bubble_vals = {}
    for col in BUBBLE_COLS:
        for i in BOATS:
            bubble_vals[f"b{i}_{col}"] = float((bs.get(i) or {}).get(col, 0))

    grade_nums  = {i: float(GRADE_MAP.get(boat_data[i].get("grade", ""), 0)) for i in BOATS}
    a1_count    = float(sum(1 for i in BOATS if grade_nums[i] == 4.0))
    a1_in_boat1 = float(grade_nums[1] == 4.0)

    feat = (
        [abs_vals[f"b{i}_{col}"] for col in BASE_COLS for i in BOATS]
        + [rank_vals[f"b{i}_{col}_rank"] for col in RANK_COLS for i in BOATS]
        + [venue_vec[f"venue_{v}"] for v in VENUE_CODES]
        + [float(day_from_start), is_final, series_p]
        + [bubble_vals[f"b{i}_{col}"] for col in BUBBLE_COLS for i in BOATS]
        + [grade_nums[i] for i in BOATS]
        + [a1_count, a1_in_boat1]
    )
    return np.array(feat, dtype=np.float32).reshape(1, -1)


# ── まくりモデル特徴量（45次元） ─────────────────────────────────
def _build_makkuri_features(venue: str, boat_data: dict,
                             target: int, player_stats: dict,
                             day_from_start=0, total_days=0,
                             exhibit_diff=0.0) -> np.ndarray:
    t = target
    series_p = float(day_from_start / total_days) if total_days > 0 else 0.0

    def ps(boat_no, key, default):
        reg = boat_data[boat_no].get("reg_no", "")
        ps_ = player_stats.get(reg, {})
        if ps_.get("n", 0) < 10:
            ps_ = player_stats.get("_league_avg", {})
        return _f(ps_.get(key, default))

    bt_avg    = _f(boat_data[t].get("avg_st"))
    bt_motor  = _f(boat_data[t].get("motor_2rate"))
    bt_nat    = _f(boat_data[t].get("national_rate"))
    bt_loc    = _f(boat_data[t].get("local_rate"))
    bt_grade  = float(GRADE_MAP.get(boat_data[t].get("grade", ""), 0))
    b1_avg    = _f(boat_data[1].get("avg_st"))
    b1_motor  = _f(boat_data[1].get("motor_2rate"))
    b1_nat    = _f(boat_data[1].get("national_rate"))
    b1_loc    = _f(boat_data[1].get("local_rate"))
    b1_grade  = float(GRADE_MAP.get(boat_data[1].get("grade", ""), 0))

    bt_mak_r  = ps(t, "makkuri_rate",   0.10)
    bt_atk_r  = ps(t, "attack_rate",    0.35)
    b1_nige_r = ps(1, "nige_rate",      0.55)
    b1_mar    = ps(1, "makuraare_rate",  0.10)

    v_str = str(venue).zfill(2)
    venue_vec = [1.0 if v == v_str else 0.0 for v in VENUE_CODES]

    feat = [
        bt_avg, bt_motor, bt_nat, bt_loc, bt_grade,
        b1_avg, b1_motor, b1_nat, b1_loc, b1_grade,
        bt_avg - b1_avg, bt_motor - b1_motor, bt_nat - b1_nat, bt_grade - b1_grade,
        float(day_from_start), series_p, float(exhibit_diff),
        bt_mak_r, bt_atk_r, b1_nige_r, b1_mar,
    ] + venue_vec
    return np.array(feat, dtype=np.float32).reshape(1, -1)


# ── 1号艇タイプ判定 ───────────────────────────────────────────────
def get_b1_type(boat_data: dict, player_stats: dict) -> str:
    reg = boat_data[1].get("reg_no", "")
    ps  = player_stats.get(reg, {})
    if ps.get("n", 0) < 10:
        ps = player_stats.get("_league_avg", {})
    mar = _f(ps.get("makuraare_rate", 0))
    sar = _f(ps.get("sasare_rate", 0))
    if mar >= 0.12 and mar > sar * 1.5:
        return "まくられ型"
    if sar >= 0.10 and sar > mar * 1.5:
        return "差され型"
    if mar < 0.04 and sar < 0.06:
        return "逃げ鉄壁型"
    return "中間型"


# ── メイン: 1レースのシナリオ計算 ─────────────────────────────────
def score_race(venue: str, boat_data: dict,
               day_from_start=0, total_days=0,
               bubble_status=None,
               exhibit_sts: dict | None = None) -> dict:
    """
    boat_data: {1: {"avg_st":0.17, "motor_2rate":38.0, "national_rate":5.0,
                     "local_rate":5.0, "grade":"A1", "reg_no":"1234"}, ...}
    exhibit_sts: {1: 0.15, 2: 0.18, ...}  # 展示ST（任意）

    returns:
    {
      "arare_prob": float,
      "b1_type": str,
      "boat3": {"p_try": float, "p_success": float, "p_fail": float},
      "boat4": {"p_try": float, "p_success": float, "p_fail": float},
      "scenario": str,   # 主シナリオ名
      "layer": int,      # 1=荒れ警報のみ, 2=まくり成功, 3=まくり失敗(裏スジ)
      "buy_target": int, # 1着予測艇番号
    }
    """
    models = _load_models()
    player_stats = models["player_stats"]

    # ── Layer 1: 荒れ確率 ─────────────────────────────────────────
    X_arare = _build_arare_features(
        venue, boat_data, day_from_start, total_days, bubble_status
    )
    arare_clf = models["arare"]
    arare_classes = list(arare_clf.classes_)
    arare_proba = arare_clf.predict_proba(X_arare)[0]
    arare_idx = arare_classes.index(1) if 1 in arare_classes else -1
    arare_prob = float(arare_proba[arare_idx]) if arare_idx >= 0 else 0.5

    b1_type = get_b1_type(boat_data, player_stats)

    result = {
        "arare_prob": round(arare_prob, 4),
        "b1_type":    b1_type,
        "boat3":      None,
        "boat4":      None,
        "scenario":   "荒れ警報のみ",
        "layer":      1,
        "buy_target": None,
    }

    if arare_prob < config.ARARE_THRESH_L1:
        result["scenario"] = "非荒れ（買わない）"
        result["layer"]    = 0
        return result

    # ── Layer 2: まくり試行スコア ─────────────────────────────────
    boat_scores = {}
    for t in [3, 4]:
        key = f"makkuri_{t}"
        clf = models[key]
        classes = list(clf.classes_)

        # 展示ST差（あれば注入）
        ex_diff = 0.0
        if exhibit_sts:
            ex_t = exhibit_sts.get(t)
            ex_1 = exhibit_sts.get(1)
            if ex_t is not None and ex_1 is not None:
                ex_diff = float(ex_t) - float(ex_1)

        X = _build_makkuri_features(
            venue, boat_data, t, player_stats,
            day_from_start, total_days, ex_diff
        )
        proba = clf.predict_proba(X)[0]

        p_none = float(proba[classes.index(0)]) if 0 in classes else 0.0
        p_fail = float(proba[classes.index(1)]) if 1 in classes else 0.0
        p_succ = float(proba[classes.index(2)]) if 2 in classes else 0.0
        p_try  = p_fail + p_succ

        boat_scores[t] = {
            "p_try":     round(p_try, 4),
            "p_success": round(p_succ / p_try, 4) if p_try > 0 else 0.0,
            "p_fail":    round(p_fail / p_try, 4) if p_try > 0 else 0.0,
        }

    result["boat3"] = boat_scores[3]
    result["boat4"] = boat_scores[4]

    # ── シナリオ決定ロジック ───────────────────────────────────────
    s3 = boat_scores[3]
    s4 = boat_scores[4]

    # どちらかでも試行スコアが閾値以上か
    t3_active = s3["p_try"] >= config.MAKKURI_TRY_THRESH
    t4_active = s4["p_try"] >= config.MAKKURI_TRY_THRESH

    if not t3_active and not t4_active:
        result["scenario"] = "荒れ警報のみ（まくり試行なし）"
        result["layer"]    = 1
        return result

    # 1号艇タイプと成功確率でシナリオを決定
    if b1_type == "逃げ鉄壁型":
        # まくり成功確率が0%に近い → 買わない
        result["scenario"] = "逃げ鉄壁型（触らない）"
        result["layer"]    = 0
        return result

    # 最も高い試行スコアの艇を主シナリオとする
    primary = 3 if s3["p_try"] >= s4["p_try"] else 4
    sp = boat_scores[primary]

    if sp["p_success"] >= config.MAKKURI_SUCCESS_THRESH:
        result["scenario"]   = f"{primary}号艇まくり成功シナリオ"
        result["layer"]      = 2
        result["buy_target"] = primary

        # 差され型1号艇ならまくり成功率が低いので警告
        if b1_type == "差され型":
            result["scenario"] += "（差され型注意）"

    else:
        # まくり失敗シナリオ → 裏スジ（5号艇メイン）
        result["scenario"]   = f"{primary}号艇まくり失敗→裏スジ（5号艇軸）"
        result["layer"]      = 3
        result["buy_target"] = 5

    # まくられ型1号艇なら成功確率が高いと補足
    if b1_type == "まくられ型":
        result["scenario"] += "★"

    return result
