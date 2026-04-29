"""
3連単バックテスト（実際の払戻金使用版）

race_results.csv の pay_3t（実際の3連単払戻金）を使い、
真の回収率を計算する。

戦略:
  A: 1着固定流し   … 予測1位艇1着固定、2-3着は全20点
  B: 厳選5点       … 1着固定 + trifecta_stats 上位5点
  C: 信頼度フィルタ … confidence>閾値の時だけ戦略Bを実行

実行:
  python backtest_trifecta.py
  python backtest_trifecta.py --conf 0.08
"""

import argparse
import csv
import json
import os
import pickle
from collections import defaultdict
from itertools import permutations

import numpy as np

import config

MODEL_BOAT   = os.path.join(config.DATA_DIR, "model_boat.pkl")
MODEL_KM     = os.path.join(config.DATA_DIR, "model_km.pkl")
MODEL_META   = os.path.join(config.DATA_DIR, "model_meta.json")
INPUT_CSV    = os.path.join(config.DATA_DIR, "prediction_data_graded_enriched.csv")
RESULTS_CSV  = os.path.join(config.DATA_DIR, "race_results.csv")

BOATS        = list(range(1, 7))
BASE_COLS    = ["avg_st", "motor_2rate", "national_rate", "local_rate"]
BUBBLE_COLS  = ["qualify", "bubble", "points_before"]
GRADE_MAP    = {"A1": 4, "A2": 3, "B1": 2, "B2": 1}
UNIFORM      = 1.0 / 6
TICKET_YEN   = 100


# ── 払戻データ読み込み ────────────────────────────────────────────────────────

def load_payouts() -> dict:
    """
    (date, venue_code, race_no) → {"pay_3t": int, "trifecta": "1-2-3"}
    """
    payouts = {}
    with open(RESULTS_CSV, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            key = (r["date"].strip(), r["venue"].strip().zfill(2), r["race_no"].strip())
            try:
                pay = int(r.get("pay_3t", 0) or 0)
            except ValueError:
                pay = 0
            payouts[key] = {
                "pay_3t":    pay,
                "trifecta":  r.get("trifecta", "").strip(),
            }
    print(f"払戻データ: {len(payouts):,} レース読み込み")
    return payouts


# ── 特徴量構築 ────────────────────────────────────────────────────────────────

def build_feature_vector(r: dict, venue_codes: list) -> np.ndarray:
    def _f(v):
        try:
            return float(v) if v and v != "" else 0.0
        except ValueError:
            return 0.0

    abs_vals = {f"b{i}_{col}": _f(r.get(f"b{i}_{col}")) for col in BASE_COLS for i in BOATS}

    rank_cols = ["avg_st", "motor_2rate", "national_rate"]
    rank_vals = {}
    for col in rank_cols:
        vals = [abs_vals[f"b{i}_{col}"] for i in BOATS]
        ascending = (col == "avg_st")
        sp = sorted(enumerate(vals, 1), key=lambda x: x[1], reverse=not ascending)
        ranks = {b: rk + 1 for rk, (b, _) in enumerate(sp)}
        for i in BOATS:
            rank_vals[f"b{i}_{col}_rank"] = float(ranks[i])

    jcd = r.get("venue_code", "")
    venue_vec = [1.0 if v == jcd else 0.0 for v in venue_codes]

    day      = _f(r.get("day_from_start"))
    is_final = _f(r.get("is_final_day"))
    series_p = _f(r.get("series_progress"))

    bubble_vals = [_f(r.get(f"b{i}_{col}")) for col in BUBBLE_COLS for i in BOATS]

    grade_nums  = [float(GRADE_MAP.get(r.get(f"b{i}_grade", ""), 0)) for i in BOATS]
    a1_count    = float(sum(1 for g in grade_nums if g == 4.0))
    a1_in_boat1 = float(grade_nums[0] == 4.0)

    feat = (
        [abs_vals[f"b{i}_{col}"] for col in BASE_COLS for i in BOATS]
        + [rank_vals[f"b{i}_{col}_rank"] for col in rank_cols for i in BOATS]
        + venue_vec
        + [day, is_final, series_p]
        + bubble_vals
        + grade_nums
        + [a1_count, a1_in_boat1]
    )
    return np.array(feat, dtype=np.float32).reshape(1, -1)


# ── 集計クラス ────────────────────────────────────────────────────────────────

class StrategyStats:
    def __init__(self, name: str):
        self.name   = name
        self.races  = 0
        self.cost   = 0
        self.payout = 0
        self.hits   = 0
        self.hit_pays: list[int] = []   # 的中時の実払戻金

    def bet(self, n_combos: int, hit: bool, pay_3t: int):
        self.races  += 1
        self.cost   += n_combos * TICKET_YEN
        if hit:
            self.hits   += 1
            self.payout += pay_3t
            self.hit_pays.append(pay_3t)

    def report(self):
        if self.races == 0:
            return
        hit_r  = self.hits / self.races
        ret    = self.payout / self.cost * 100 if self.cost > 0 else 0
        avg_odds = (np.mean(self.hit_pays) / TICKET_YEN) if self.hit_pays else 0
        med_odds = (np.median(self.hit_pays) / TICKET_YEN) if self.hit_pays else 0

        print(f"\n  【{self.name}】")
        print(f"  対象レース  : {self.races:,}R  /  的中: {self.hits:,}R  ({hit_r*100:.2f}%)")
        print(f"  総投資      : {self.cost:,}円")
        print(f"  総回収      : {self.payout:,}円")
        print(f"  実際の回収率: {ret:.1f}%  （期待値: {ret-100:+.1f}%）")
        if self.hit_pays:
            print(f"  的中時オッズ: 平均 {avg_odds:.1f}倍  中央値 {med_odds:.1f}倍  "
                  f"最高 {max(self.hit_pays)/TICKET_YEN:.1f}倍")


# ── メイン ───────────────────────────────────────────────────────────────────

def main(conf_threshold: float = 0.08):
    with open(MODEL_BOAT, "rb") as f: clf_boat = pickle.load(f)
    with open(MODEL_KM,   "rb") as f: clf_km   = pickle.load(f)
    with open(MODEL_META, encoding="utf-8") as f: meta = json.load(f)

    venue_codes    = sorted(config.VENUE_CODES.keys())
    km_by_boat     = meta.get("km_by_boat", {})
    trifecta_stats = meta.get("trifecta_stats", {})

    payouts = load_payouts()

    rows = []
    with open(INPUT_CSV, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(r)

    # 払戻データが存在する行だけ抽出
    test = [r for r in rows
            if (r.get("date",""), r.get("venue_code","").zfill(2), r.get("race_no","")) in payouts]
    print(f"払戻データ照合可能: {len(test):,} / {len(rows):,} レース")
    print(f"期間: {test[0]['date'] if test else '?'} 〜 {test[-1]['date'] if test else '?'}")
    print("※ 訓練データ期間を含むため回収率はやや楽観的。傾向把握として参照のこと")

    # バッチ予測（高速化）
    print("特徴量行列を構築中...")
    X_all = np.vstack([build_feature_vector(r, venue_codes) for r in test])
    print("predict_proba バッチ実行中...")
    proba_all = clf_boat.predict_proba(X_all)  # shape: (N, 6)
    print("予測完了")

    # 戦略インスタンス
    s_a_all  = StrategyStats(f"A: 1着流し20点（全レース）")
    s_b_all  = StrategyStats(f"B: 厳選5点（全レース）")
    s_c_conf = StrategyStats(f"C: 厳選5点 (confidence>{conf_threshold:.2f})")

    # 信頼度別（戦略B）
    conf_bins = [0.00, 0.03, 0.05, 0.08, 0.10, 0.12, 0.15, 0.20]
    bin_stats = {b: StrategyStats(f">={b:.2f}") for b in conf_bins}

    skipped = no_payout = 0

    for idx, r in enumerate(test):
        # 結果取得
        try:
            actual_r1 = int(float(r.get("rank1", r.get("winning_boat", 0))))
            actual_r2 = int(float(r.get("rank2", 0)))
            actual_r3 = int(float(r.get("rank3", 0)))
        except (ValueError, TypeError):
            skipped += 1
            continue
        if actual_r1 == 0 or actual_r2 == 0 or actual_r3 == 0:
            skipped += 1
            continue

        # 払戻金取得
        key = (r.get("date", ""), r.get("venue_code", "").zfill(2), r.get("race_no", ""))
        payout_info = payouts.get(key)
        if not payout_info or payout_info["pay_3t"] == 0:
            no_payout += 1
            continue
        pay_3t = payout_info["pay_3t"]

        # バッチ予測結果を取得
        boat_prob = {int(c): float(p) for c, p in zip(clf_boat.classes_, proba_all[idx])}
        top_boat  = max(boat_prob, key=boat_prob.get)
        confidence = boat_prob[top_boat] - UNIFORM

        # trifecta_stats から上位5組み合わせ
        top_km = km_by_boat.get(str(top_boat), {}).get("_modal", "逃げ")
        tri_key = f"{top_boat}_{top_km}"
        top5_combos: list[tuple[int, int]] = []
        existing: set[tuple[int, int]] = set()
        for entry in trifecta_stats.get(tri_key, [])[:5]:
            parts = entry.get("combo", "").split("-")
            if len(parts) == 2:
                try:
                    c = (int(parts[0]), int(parts[1]))
                    top5_combos.append(c)
                    existing.add(c)
                except ValueError:
                    pass
        for r2, r3 in permutations([b for b in BOATS if b != top_boat], 2):
            if len(top5_combos) >= 5:
                break
            c = (r2, r3)
            if c not in existing:
                top5_combos.append(c)
                existing.add(c)

        all20 = list(permutations([b for b in BOATS if b != top_boat], 2))
        actual_combo = (actual_r2, actual_r3)

        # ── 戦略A ────────────────────────────────────────────────────────────
        hit_a = (actual_r1 == top_boat and actual_combo in all20)
        s_a_all.bet(len(all20), hit_a, pay_3t)

        # ── 戦略B（全レース・信頼度別）──────────────────────────────────────
        hit_b = (actual_r1 == top_boat and actual_combo in top5_combos)
        s_b_all.bet(len(top5_combos), hit_b, pay_3t)
        for b in conf_bins:
            if confidence >= b:
                bin_stats[b].bet(len(top5_combos), hit_b, pay_3t)

        # ── 戦略C（信頼度フィルター）────────────────────────────────────────
        if confidence >= conf_threshold:
            s_c_conf.bet(len(top5_combos), hit_b, pay_3t)

    print(f"スキップ: {skipped:,}  払戻なし: {no_payout:,}")

    # ── 結果表示 ─────────────────────────────────────────────────────────────
    print(f"\n{'='*66}")
    print("  3連単バックテスト結果（実際の払戻金使用）")
    print(f"{'='*66}")
    s_a_all.report()
    s_b_all.report()
    s_c_conf.report()

    # ── 信頼度別ブレークダウン ─────────────────────────────────────────────
    print(f"\n{'─'*66}")
    print("  信頼度別 回収率（戦略B・厳選5点）")
    print(f"{'─'*66}")
    print(f"  {'閾値':>6}  {'対象R':>6}  {'的中':>5}  {'的中率':>6}  "
          f"{'回収率':>6}  {'平均的中オッズ':>12}")
    for b in conf_bins:
        s = bin_stats[b]
        if s.races == 0:
            continue
        hr  = s.hits / s.races
        ret = s.payout / s.cost * 100 if s.cost > 0 else 0
        avg_odds = (np.mean(s.hit_pays) / TICKET_YEN) if s.hit_pays else 0
        print(f"  >{b:.2f}  {s.races:>6,}R  {s.hits:>5}  {hr*100:>5.2f}%  "
              f"{ret:>5.1f}%  {avg_odds:>12.1f}倍")

    # ── 結論 ─────────────────────────────────────────────────────────────────
    print(f"\n{'='*66}")
    print("  最大回収率戦略の特定")
    print(f"{'='*66}")
    best_ret   = 0.0
    best_label = ""
    for b in conf_bins:
        s = bin_stats[b]
        if s.races < 100 or s.cost == 0:
            continue
        ret = s.payout / s.cost * 100
        if ret > best_ret:
            best_ret   = ret
            best_label = f"confidence>{b:.2f}"
    if best_label:
        print(f"  最高回収率: {best_ret:.1f}%  @ 戦略B（{best_label}）")
        print(f"  ※ 控除率25%を考慮すると理論上限は75%。これを超えていれば市場に対して優位あり")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--conf", type=float, default=0.08)
    args = parser.parse_args()
    main(conf_threshold=args.conf)
