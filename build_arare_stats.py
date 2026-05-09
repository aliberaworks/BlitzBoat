"""
荒れ時（winning_boat != 1）の出目統計EVテーブル構築

prediction_data_graded_enriched.csv から荒れレースを抽出し、
グローバル + 会場別の出目傾向を集計してJSONに保存する。

実行:
  python build_arare_stats.py

出力:
  data/arare_stats.json
"""

import csv
import json
import os
from collections import defaultdict

import config

INPUT_CSV   = os.path.join(config.DATA_DIR, "prediction_data_graded_enriched.csv")
OUTPUT_JSON = os.path.join(config.DATA_DIR, "arare_stats.json")

BOATS    = list(range(1, 7))
KIMARITE = ["逃げ", "差し", "まくり", "まくり差し", "抜き", "恵まれ"]


def _f(v):
    try:
        return float(v) if v and v != "" else None
    except ValueError:
        return None


def _build_stats(races: list[dict]) -> dict:
    """荒れレースリストから統計を計算する。"""
    n = len(races)
    if n == 0:
        return {}

    # 勝利艇分布
    win_counts = defaultdict(int)
    for r in races:
        wb = r.get("wb")
        if wb:
            win_counts[str(wb)] += 1
    win_dist = {k: round(v / n, 4) for k, v in win_counts.items()}

    # 決まり手分布
    km_counts = defaultdict(int)
    for r in races:
        km = r.get("km")
        if km in KIMARITE:
            km_counts[km] += 1
    km_total = sum(km_counts.values())
    km_dist = {k: round(v / km_total, 4) for k, v in km_counts.items()} if km_total else {}

    # 3連単パターン: {f"{winner}_{kimarite}": {f"{r2}-{r3}": [pay, ...]} }
    tri_buckets: dict = defaultdict(lambda: defaultdict(list))
    for r in races:
        wb = r.get("wb")
        km = r.get("km")
        r2 = r.get("r2")
        r3 = r.get("r3")
        pay = r.get("pay")
        if wb and km in KIMARITE and r2 and r3 and pay:
            key = f"{wb}_{km}"
            tri_buckets[key][f"{r2}-{r3}"].append(pay)

    # 上位12パターンを出現率・平均配当で整形
    trifecta_by_winner_km: dict = {}
    for key, combos in tri_buckets.items():
        total_for_key = sum(len(v) for v in combos.values())
        patterns = []
        for r2r3, pays in combos.items():
            patterns.append({
                "r2_r3":    r2r3,
                "count":    len(pays),
                "pct":      round(len(pays) / total_for_key, 4),
                "avg_pay":  int(sum(pays) / len(pays)),
            })
        patterns.sort(key=lambda x: x["pct"], reverse=True)
        trifecta_by_winner_km[key] = patterns[:12]

    return {
        "n":                      n,
        "winning_boat_dist":      win_dist,
        "kimarite_dist":          km_dist,
        "trifecta_by_winner_km":  trifecta_by_winner_km,
    }


def main():
    print("=== 荒れ統計テーブル構築 ===\n")

    rows = []
    with open(INPUT_CSV, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    print(f"読み込み: {len(rows):,} 行")

    total_races = 0
    arare_rows = []
    all_venue_races: dict = defaultdict(list)

    for r in rows:
        wb_raw = r.get("winning_boat", "")
        km     = r.get("kimarite", "").strip()
        r2_raw = r.get("rank2", "")
        r3_raw = r.get("rank3", "")
        pay_raw = r.get("pay_3t", "")
        venue  = r.get("venue_name", "").strip()

        if not wb_raw or km not in KIMARITE:
            continue
        try:
            wb = int(float(wb_raw))
        except (ValueError, TypeError):
            continue
        if wb not in BOATS:
            continue

        total_races += 1

        if wb == 1:
            continue  # 荒れではない

        try:
            r2  = int(float(r2_raw))
            r3  = int(float(r3_raw))
            pay = _f(pay_raw)
        except (ValueError, TypeError):
            continue
        if pay is None or pay <= 0:
            continue

        # 物理的に不可能なデータ（同艇番が1着かつ2/3着）を除外
        if r2 == wb or r3 == wb or r2 == r3:
            continue
        entry = {"wb": wb, "km": km, "r2": r2, "r3": r3, "pay": pay}
        arare_rows.append(entry)
        if venue:
            all_venue_races[venue].append(entry)

    arare_rate = len(arare_rows) / total_races if total_races else 0
    print(f"総レース: {total_races:,}")
    print(f"荒れレース: {len(arare_rows):,} ({arare_rate*100:.1f}%)")
    print(f"集計会場数: {len(all_venue_races)}")

    print("\nグローバル統計計算中...")
    global_stats = _build_stats(arare_rows)

    print("会場別統計計算中...")
    by_venue = {}
    for venue, venue_arare in sorted(all_venue_races.items()):
        by_venue[venue] = _build_stats(venue_arare)
        print(f"  {venue}: 荒れ{venue_arare.__len__()}R")

    output = {
        "meta": {
            "total_races": total_races,
            "arare_races": len(arare_rows),
            "arare_rate":  round(arare_rate, 4),
        },
        "global":   global_stats,
        "by_venue": by_venue,
    }

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n保存: {OUTPUT_JSON}")
    print(f"次: python morning_batch.py  (arare_probが自動計算される)")


if __name__ == "__main__":
    main()
