"""
荒れ特化 3連単 バックテスト

- 末尾20%をテストデータとして使用
- arare_statsは前80%（訓練データ）のみで再構築（ルックアヘッドバイアス回避）
- model_arare.pkl でarare_probを計算し、閾値以上のレースのみ購入
- arare_stats.trifecta_by_winner_km の上位N点を購入
- 閾値(4) × 購入点数(4) × バイアスなし/あり のマトリクスで回収率を出力

実行:
  python backtest_arare.py

出力:
  data/backtest_arare_result.json
"""

import csv
import json
import os
import pickle
from collections import defaultdict

import numpy as np

import config

INPUT_CSV   = os.path.join(config.DATA_DIR, "prediction_data_graded_enriched.csv")
MODEL_ARARE = os.path.join(config.DATA_DIR, "model_arare.pkl")
OUTPUT_JSON = os.path.join(config.DATA_DIR, "backtest_arare_result.json")

BOATS       = list(range(1, 7))
KIMARITE    = ["逃げ", "差し", "まくり", "まくり差し", "抜き", "恵まれ"]
BASE_COLS   = ["avg_st", "motor_2rate", "national_rate", "local_rate"]
BUBBLE_COLS = ["qualify", "bubble", "points_before"]
VENUE_CODES = sorted(config.VENUE_CODES.keys())
GRADE_MAP   = {"A1": 4, "A2": 3, "B1": 2, "B2": 1}
BET_UNIT    = 100        # 1点あたり (円)
MAX_PRE     = 20         # 事前計算する最大点数


def _f(v):
    try:
        return float(v) if v and v != "" else None
    except ValueError:
        return None


def extract_features(r: dict) -> list[float]:
    """1行から93次元特徴量ベクトルを返す（pre-race dataのみ使用）。"""
    rank_cols = ["avg_st", "motor_2rate", "national_rate"]

    abs_vals = {}
    for col in BASE_COLS:
        for i in BOATS:
            v = _f(r.get(f"b{i}_{col}"))
            abs_vals[f"b{i}_{col}"] = v if v is not None else 0.0

    rank_vals = {}
    for col in rank_cols:
        vals = [abs_vals[f"b{i}_{col}"] for i in BOATS]
        sp = sorted(enumerate(vals, 1), key=lambda x: x[1], reverse=(col != "avg_st"))
        ranks = {b: rk + 1 for rk, (b, _) in enumerate(sp)}
        for i in BOATS:
            rank_vals[f"b{i}_{col}_rank"] = float(ranks[i])

    jcd = r.get("venue_code", "")
    venue_vec = {f"venue_{v}": 1.0 if v == jcd else 0.0 for v in VENUE_CODES}

    day      = _f(r.get("day_from_start"))  or 0.0
    is_final = _f(r.get("is_final_day"))     or 0.0
    series_p = _f(r.get("series_progress"))  or 0.0

    bubble_vals = {}
    for col in BUBBLE_COLS:
        for i in BOATS:
            v = _f(r.get(f"b{i}_{col}"))
            bubble_vals[f"b{i}_{col}"] = v if v is not None else 0.0

    grade_nums = {i: float(GRADE_MAP.get(r.get(f"b{i}_grade", ""), 0)) for i in BOATS}
    a1_count    = float(sum(1 for i in BOATS if grade_nums[i] == 4.0))
    a1_in_boat1 = float(grade_nums[1] == 4.0)

    return (
        [abs_vals[f"b{i}_{col}"] for col in BASE_COLS for i in BOATS]
        + [rank_vals[f"b{i}_{col}_rank"] for col in rank_cols for i in BOATS]
        + [venue_vec[f"venue_{v}"] for v in VENUE_CODES]
        + [day, is_final, series_p]
        + [bubble_vals[f"b{i}_{col}"] for col in BUBBLE_COLS for i in BOATS]
        + [grade_nums[i] for i in BOATS]
        + [a1_count, a1_in_boat1]
    )


def build_stats(rows: list[dict]) -> dict:
    """荒れレース(winning_boat != 1)からtrifecta統計を構築。"""
    n = 0
    win_counts  = defaultdict(int)
    km_counts   = defaultdict(int)
    tri_buckets: dict = defaultdict(lambda: defaultdict(list))

    for r in rows:
        wb_raw = r.get("winning_boat", "")
        km     = r.get("kimarite", "").strip()
        if not wb_raw or km not in KIMARITE:
            continue
        try:
            wb = int(float(wb_raw))
        except (ValueError, TypeError):
            continue
        if wb not in BOATS or wb == 1:
            continue

        try:
            r2  = int(float(r.get("rank2", 0)))
            r3  = int(float(r.get("rank3", 0)))
            pay = _f(r.get("pay_3t"))
        except (ValueError, TypeError):
            continue
        if not pay or pay <= 0 or r2 == 0 or r3 == 0:
            continue

        n += 1
        win_counts[str(wb)] += 1
        km_counts[km] += 1
        tri_buckets[f"{wb}_{km}"][f"{r2}-{r3}"].append(pay)

    if n < 20:
        return {}

    win_dist = {k: round(v / n, 4) for k, v in win_counts.items()}
    km_total  = sum(km_counts.values())
    km_dist   = {k: round(v / km_total, 4) for k, v in km_counts.items()} if km_total else {}

    trifecta = {}
    for key, combos in tri_buckets.items():
        total = sum(len(v) for v in combos.values())
        pats  = []
        for r2r3, pays in combos.items():
            pats.append({
                "r2_r3":   r2r3,
                "count":   len(pays),
                "pct":     round(len(pays) / total, 4),
                "avg_pay": int(sum(pays) / len(pays)),
            })
        pats.sort(key=lambda x: x["pct"], reverse=True)
        trifecta[key] = pats[:12]

    return {"n": n, "win_dist": win_dist, "km_dist": km_dist, "trifecta": trifecta}


def precompute_bets(stats: dict, max_n: int = MAX_PRE) -> list[tuple[int,int,int]]:
    """
    trifecta統計から 3連単の上位N点を事前計算。
    スコア = P(winner) × P(kimarite) × P(r2-r3 | winner,kimarite)
    """
    win_dist = stats.get("win_dist", {})
    km_dist  = stats.get("km_dist", {})
    tri      = stats.get("trifecta", {})

    scores: dict = defaultdict(float)
    for key, patterns in tri.items():
        parts = key.split("_", 1)
        if len(parts) != 2:
            continue
        wb_str, km_str = parts
        wp = win_dist.get(wb_str, 0.0)
        kp = km_dist.get(km_str, 0.0)
        if wp <= 0 or kp <= 0:
            continue
        wb = int(wb_str)
        for pat in patterns:
            try:
                r2, r3 = map(int, pat["r2_r3"].split("-"))
            except ValueError:
                continue
            scores[(wb, r2, r3)] += wp * kp * pat["pct"]

    return [bet for bet, _ in sorted(scores.items(), key=lambda x: x[1], reverse=True)[:max_n]]


def run_matrix(
    test_items: list[tuple],
    arare_probs: np.ndarray,
    global_bets: list,
    venue_bets: dict,
    thresholds: list[float],
    top_ns: list[int],
) -> dict:
    """バックテストの全閾値×点数組み合わせを一括実行。"""
    results = {}

    for thresh in thresholds:
        for top_n in top_ns:
            rbc = 0
            total_bet = 0
            total_return = 0
            hits = 0

            for i, (_, r) in enumerate(test_items):
                if arare_probs[i] < thresh:
                    continue

                km = r.get("kimarite", "").strip()
                if km not in KIMARITE:
                    continue
                try:
                    actual_r1 = int(float(r.get("winning_boat", 0)))
                    actual_r2 = int(float(r.get("rank2", 0)))
                    actual_r3 = int(float(r.get("rank3", 0)))
                    pay       = _f(r.get("pay_3t"))
                except (ValueError, TypeError):
                    continue
                if actual_r1 not in BOATS or actual_r2 == 0 or actual_r3 == 0 or not pay:
                    continue

                venue = r.get("venue_name", "").strip()
                bets  = venue_bets.get(venue, global_bets)[:top_n]
                if not bets:
                    continue

                rbc += 1
                total_bet += len(bets) * BET_UNIT

                if (actual_r1, actual_r2, actual_r3) in bets:
                    total_return += int(pay)
                    hits += 1

            roi = (total_return / total_bet - 1.0) if total_bet > 0 else -1.0
            results[f"{thresh}_{top_n}"] = {
                "threshold":    thresh,
                "top_n":        top_n,
                "races_bet":    rbc,
                "total_bet":    total_bet,
                "total_return": total_return,
                "roi":          round(roi, 4),
                "hit_count":    hits,
                "hit_rate":     round(hits / rbc, 4) if rbc > 0 else 0.0,
            }

    return results


def main():
    print("=== 荒れ特化 3連単 バックテスト ===\n")

    rows = []
    with open(INPUT_CSV, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    print(f"全データ: {len(rows):,} 行")

    split      = int(len(rows) * 0.8)
    train_rows = rows[:split]
    test_rows  = rows[split:]
    print(f"訓練: {len(train_rows):,} 行  /  テスト: {len(test_rows):,} 行")

    # ---- 訓練データのみでarare_stats構築 ----
    print("\n訓練データから荒れ統計再構築中...")
    global_stats = build_stats(train_rows)
    print(f"  グローバル荒れR(訓練): {global_stats.get('n', 0):,}")

    venue_rows: dict = defaultdict(list)
    for r in train_rows:
        v = r.get("venue_name", "").strip()
        if v:
            venue_rows[v].append(r)

    venue_stats: dict = {}
    for v, vrows in sorted(venue_rows.items()):
        s = build_stats(vrows)
        if s and s.get("n", 0) >= 30:
            venue_stats[v] = s
    print(f"  会場別統計: {len(venue_stats)} 会場 (荒れ30R以上)")

    # ---- ベット候補事前計算 ----
    global_bets = precompute_bets(global_stats, MAX_PRE)
    venue_bets  = {v: precompute_bets(s, MAX_PRE) for v, s in venue_stats.items()}
    print(f"  グローバルベット候補上位{len(global_bets)}点: "
          + ", ".join(f"{r1}-{r2}-{r3}" for r1, r2, r3 in global_bets[:5]) + " ...")

    # ---- テストデータ特徴量抽出 ----
    print("\nテストデータ特徴量抽出中...")
    test_items = [(extract_features(r), r) for r in test_rows]

    # ---- arare_prob計算 ----
    print("model_arare.pkl でarare_prob推論中...")
    with open(MODEL_ARARE, "rb") as f:
        clf_arare = pickle.load(f)
    X = np.array([it[0] for it in test_items], dtype=np.float32)
    arare_probs = clf_arare.predict_proba(X)[:, 1]
    print(f"  テストアイテム: {len(test_items):,}")
    print(f"  arare_prob 平均: {arare_probs.mean():.4f}  最大: {arare_probs.max():.4f}")

    # ---- バックテスト本体 ----
    print("\nバックテスト実行中...")

    thresholds = [0.50, 0.55, 0.60, 0.65]
    top_ns     = [3, 5, 8, 12]

    results = run_matrix(test_items, arare_probs, global_bets, venue_bets, thresholds, top_ns)

    # ---- ベースライン: 閾値なし（全レース購入） ----
    print("ベースライン（閾値なし）計算中...")
    baseline = run_matrix(test_items, arare_probs, global_bets, venue_bets, [0.0], top_ns)
    for k, v in baseline.items():
        results[f"BASE_{k}"] = v

    # ---- 結果表示 ----
    print("\n=== バックテスト結果 ===")
    print("  (arare_statsは訓練データ80%のみで構築、ルックアヘッドバイアスなし)")
    print()
    print(f"  {'閾値':>5}  {'N点':>4}  {'購入R':>7}  {'総投資(円)':>12}  {'総回収(円)':>12}  {'ROI':>8}  {'的中R':>6}  {'的中率':>6}")
    print("  " + "-" * 78)

    for thresh in [0.0] + thresholds:
        for top_n in top_ns:
            key = f"{thresh}_{top_n}" if thresh > 0 else f"BASE_0.0_{top_n}"
            if key not in results:
                continue
            r = results[key]
            label = "全R" if thresh == 0.0 else f"{thresh:.2f}"
            print(
                f"  {label:>5}  {top_n:>4}  "
                f"{r['races_bet']:>7,}  "
                f"{r['total_bet']:>12,}  "
                f"{r['total_return']:>12,}  "
                f"{r['roi']*100:>+7.1f}%  "
                f"{r['hit_count']:>6}  {r['hit_rate']*100:>5.1f}%"
            )
        print()

    # ---- 最良戦略 ----
    model_keys = [k for k in results if not k.startswith("BASE_")]
    best_key   = max(model_keys, key=lambda k: results[k]["roi"])
    best       = results[best_key]
    print(f"最良戦略: 閾値{best['threshold']} × {best['top_n']}点")
    print(f"  ROI: {best['roi']*100:+.1f}%")
    print(f"  的中率: {best['hit_rate']*100:.2f}%  ({best['hit_count']}的中 / {best['races_bet']}レース)")
    print(f"  総投資: {best['total_bet']:,}円  総回収: {best['total_return']:,}円")
    print()
    print("参考: ボートレース3連単の理論控除率 約25.2%(理論ROI = -25.2%)")
    print("      モデルなし・上位5点購入時の実績ROI は 全R行を参照")

    # ---- 配当帯別の的中分析（最良戦略） ----
    print(f"\n=== 配当帯別的中分析（最良戦略: 閾値{best['threshold']} × {best['top_n']}点） ===")
    brackets = [(0, 3000), (3000, 10000), (10000, 30000), (30000, 100000), (100000, 10**9)]
    bracket_hits = defaultdict(int)
    bracket_total_pay = defaultdict(int)
    bracket_bets = defaultdict(int)

    top_n_best = best["top_n"]
    thresh_best = best["threshold"]
    for i, (_, r) in enumerate(test_items):
        if arare_probs[i] < thresh_best:
            continue
        km = r.get("kimarite", "").strip()
        if km not in KIMARITE:
            continue
        try:
            actual_r1 = int(float(r.get("winning_boat", 0)))
            actual_r2 = int(float(r.get("rank2", 0)))
            actual_r3 = int(float(r.get("rank3", 0)))
            pay       = _f(r.get("pay_3t"))
        except (ValueError, TypeError):
            continue
        if actual_r1 not in BOATS or actual_r2 == 0 or actual_r3 == 0 or not pay:
            continue

        venue = r.get("venue_name", "").strip()
        bets  = venue_bets.get(venue, global_bets)[:top_n_best]
        if not bets:
            continue

        bracket_bets["all"] += 1
        actual = (actual_r1, actual_r2, actual_r3)
        if actual in bets:
            for lo, hi in brackets:
                if lo <= pay < hi:
                    label = f"{lo//100}〜{hi//100}百円"
                    bracket_hits[label] += 1
                    bracket_total_pay[label] += int(pay)
                    bracket_bets[label] += 1
                    break

    for lo, hi in brackets:
        label = f"{lo//100}〜{hi//100}百円"
        h = bracket_hits[label]
        p = bracket_total_pay[label]
        n = bracket_bets[label]
        if h > 0:
            print(f"  {label:>14}: {h}的中  合計回収 {p:,}円  平均配当 {p//h:,}円")

    # ---- 保存 ----
    output = {
        "meta": {
            "train_size":    len(train_rows),
            "test_size":     len(test_rows),
            "test_items":    len(test_items),
            "bet_unit_yen":  BET_UNIT,
            "run_date":      "2026-05-08",
        },
        "results":    results,
        "best_key":   best_key,
    }
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n保存: {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
