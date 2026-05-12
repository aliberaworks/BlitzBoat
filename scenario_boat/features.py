"""特徴量構築（訓練・推論共通）"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

import numpy as np


def _f(v, default=0.0):
    try:
        return float(v) if v and str(v).strip() != "" else default
    except (ValueError, TypeError):
        return default


def feature_names() -> list[str]:
    names = []
    rank_cols = ["avg_st", "motor_2rate", "national_rate"]
    for col in config.BASE_COLS:
        for i in config.BOATS:
            names.append(f"b{i}_{col}")
    for col in rank_cols:
        for i in config.BOATS:
            names.append(f"b{i}_{col}_rank")
    names += ["day_from_start", "is_final_day", "series_progress"]
    for col in config.BUBBLE_COLS:
        for i in config.BOATS:
            names.append(f"b{i}_{col}")
    for i in config.BOATS:
        names.append(f"b{i}_grade_num")
    names += ["a1_count", "a1_in_boat1"]
    names += ["b3_vs1_motor_diff", "b3_vs1_st_diff", "b3_vs1_rate_diff"]
    names += ["b4_vs1_motor_diff", "b4_vs1_st_diff", "b4_vs1_rate_diff"]
    names += ["b3_vs2_motor_diff", "b3_vs2_st_diff"]
    names += ["b4_vs3_motor_diff", "b4_vs3_st_diff"]
    for jcd in config.VENUE_CODES:
        names.append(f"venue_{jcd}")
    return names


def build_row_vector(race: dict) -> np.ndarray | None:
    """
    1レース分の特徴量ベクトルを構築する。
    raceは以下の形式のdict:
      b{i}_avg_st, b{i}_motor_2rate, b{i}_national_rate, b{i}_local_rate,
      b{i}_grade (文字列), b{i}_qualify, b{i}_bubble, b{i}_points_before,
      day_from_start, is_final_day, series_progress,
      venue_code (2桁文字列)
    """
    rank_cols = ["avg_st", "motor_2rate", "national_rate"]
    vec = []

    # 絶対値
    abs_vals = {}
    for col in config.BASE_COLS:
        for i in config.BOATS:
            v = _f(race.get(f"b{i}_{col}"))
            abs_vals[f"b{i}_{col}"] = v
            vec.append(v)

    # 艇間ランク
    for col in rank_cols:
        vals = [abs_vals[f"b{i}_{col}"] for i in config.BOATS]
        ascending = (col == "avg_st")
        sorted_vals = sorted(enumerate(vals, 1), key=lambda x: x[1], reverse=not ascending)
        ranks = {b: r+1 for r, (b, _) in enumerate(sorted_vals)}
        for i in config.BOATS:
            vec.append(float(ranks[i]))

    # 節内
    vec.append(_f(race.get("day_from_start")))
    vec.append(_f(race.get("is_final_day")))
    vec.append(_f(race.get("series_progress")))
    for col in config.BUBBLE_COLS:
        for i in config.BOATS:
            vec.append(_f(race.get(f"b{i}_{col}")))

    # グレード
    a1_cnt = 0
    a1_boat1 = 0.0
    for i in config.BOATS:
        g = str(race.get(f"b{i}_grade", "B1")).strip()
        gn = float(config.GRADE_MAP.get(g, 2))
        vec.append(gn)
        if gn == 4:
            a1_cnt += 1
            if i == 1:
                a1_boat1 = 1.0
    vec.append(float(a1_cnt))
    vec.append(a1_boat1)

    # 差分特徴量
    def motor(b): return abs_vals[f"b{b}_motor_2rate"]
    def st(b):    return abs_vals[f"b{b}_avg_st"]
    def rate(b):  return abs_vals[f"b{b}_national_rate"]
    vec += [motor(3)-motor(1), st(1)-st(3), rate(3)-rate(1)]
    vec += [motor(4)-motor(1), st(1)-st(4), rate(4)-rate(1)]
    vec += [motor(3)-motor(2), st(2)-st(3)]
    vec += [motor(4)-motor(3), st(3)-st(4)]

    # 会場
    vc = str(race.get("venue_code", "")).zfill(2)
    for jcd in config.VENUE_CODES:
        vec.append(1.0 if vc == jcd else 0.0)

    expected = len(feature_names())
    if len(vec) != expected:
        return None
    return np.array(vec, dtype=np.float32)
