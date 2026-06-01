"""
makuri - 出目生成エンジン

シナリオに応じて買い目（3連単コンボ）を確率順に生成する。

Layer 2（まくり成功）: trifecta_stats["X_まくり"] の上位N点
Layer 3（裏スジ）:     ura_suji_stats["fail_X_win_Y"] の上位N点
"""
import json
import os

import config

_tri_cache   = None
_ura_cache   = None


def _load_trifecta_stats():
    global _tri_cache
    if _tri_cache is not None:
        return _tri_cache
    # まずmakuri/data/trifecta_stats.jsonを試す
    if os.path.exists(config.TRIFECTA_STATS_JSON):
        with open(config.TRIFECTA_STATS_JSON, encoding="utf-8") as f:
            _tri_cache = json.load(f)
    else:
        # BlitzBoatのmodel_meta.jsonから抽出
        with open(config.MODEL_META_JSON, encoding="utf-8") as f:
            meta = json.load(f)
        _tri_cache = meta.get("trifecta_stats_all", meta.get("trifecta_stats", {}))
    return _tri_cache


def _load_ura_suji_stats():
    global _ura_cache
    if _ura_cache is not None:
        return _ura_cache
    if os.path.exists(config.URA_SUJI_STATS_JSON):
        with open(config.URA_SUJI_STATS_JSON, encoding="utf-8") as f:
            _ura_cache = json.load(f)
    else:
        _ura_cache = {}
    return _ura_cache


def get_makkuri_combos(target_boat: int, n: int = None) -> list[dict]:
    """
    まくり成功シナリオの買い目を返す。
    target_boat: 1着予測艇（3 or 4）
    n: 点数（デフォルト: config.MAKKURI_SUCCESS_COMBOS）

    Returns: [{"combo": "3-2-1", "pct": 0.227, "rank": 1}, ...]
    """
    if n is None:
        n = config.MAKKURI_SUCCESS_COMBOS
    tri = _load_trifecta_stats()

    # まくり・まくり差し両方のパターンを統合
    patterns = {}
    for km_key in ["まくり", "まくり差し"]:
        key = f"{target_boat}_{km_key}"
        for pat in tri.get(key, []):
            try:
                r2s, r3s = pat["combo"].split("-")
                r2, r3 = int(float(r2s)), int(float(r3s))
                combo = f"{target_boat}-{r2}-{r3}"
                pct = float(pat["pct"])
                if combo not in patterns:
                    patterns[combo] = {"combo": combo, "pct": 0.0, "r2": r2, "r3": r3}
                patterns[combo]["pct"] += pct  # まくり・まくり差しの平均をとる
            except Exception:
                continue

    # 確率降順ソート
    sorted_combos = sorted(patterns.values(), key=lambda x: x["pct"], reverse=True)
    result = []
    for rank, c in enumerate(sorted_combos[:n], 1):
        result.append({
            "combo": c["combo"],
            "r1":    target_boat,
            "r2":    c["r2"],
            "r3":    c["r3"],
            "pct":   round(c["pct"], 4),
            "rank":  rank,
            "type":  "まくり成功",
        })
    return result


def get_ura_suji_combos(fail_boat: int, n: int = None) -> list[dict]:
    """
    裏スジシナリオの買い目を返す。
    fail_boat: まくりを失敗した艇（3 or 4）
    n: 点数

    Returns: [{"combo": "5-3-1", "pct": 0.15, "rank": 1}, ...]
    """
    if n is None:
        n = config.MAKKURI_FAIL_COMBOS
    ura = _load_ura_suji_stats()

    # ura_suji_stats.json の構造: {"fail_3": [...], "fail_4": [...]}
    key = f"fail_{fail_boat}"
    raw_patterns = ura.get(key, [])

    if not raw_patterns:
        # フォールバック: trifecta_statsの5号艇まくり差しを使う
        tri = _load_trifecta_stats()
        raw_patterns = []
        for pat in tri.get("5_まくり差し", [])[:n]:
            try:
                r2s, r3s = pat["combo"].split("-")
                raw_patterns.append({
                    "combo": f"5-{int(float(r2s))}-{int(float(r3s))}",
                    "pct": float(pat["pct"]),
                })
            except Exception:
                continue

    result = []
    for rank, pat in enumerate(raw_patterns[:n], 1):
        parts = pat["combo"].split("-")
        if len(parts) != 3:
            continue
        r1, r2, r3 = int(parts[0]), int(parts[1]), int(parts[2])
        result.append({
            "combo": pat["combo"],
            "r1": r1, "r2": r2, "r3": r3,
            "pct":  round(pat.get("pct", 0.0), 4),
            "rank": rank,
            "type": "裏スジ",
        })
    return result


def get_combos_for_scenario(scenario_result: dict) -> list[dict]:
    """
    scenario.score_race() の結果からまとめて買い目を生成する。

    Returns: [{"combo": "4-2-1", "pct": ..., "type": "まくり成功" or "裏スジ"}, ...]
    """
    layer = scenario_result.get("layer", 0)
    if layer == 0:
        return []

    if layer == 1:
        # 荒れ警報のみ → 買い目なし（通知のみ）
        return []

    buy_target = scenario_result.get("buy_target")
    if buy_target is None:
        return []

    if layer == 2:
        return get_makkuri_combos(buy_target)
    elif layer == 3:
        # 裏スジ: fail_boat = まくりを試みた側（3 or 4）
        # scenario名から特定
        scenario = scenario_result.get("scenario", "")
        fail_boat = 4 if "4号艇" in scenario else 3
        return get_ura_suji_combos(fail_boat)
    return []
