"""
BlitzBoat Statistics Engine — 会場別決まり手・出目ランキング
差し除外、97%累積確率フィルタ
"""
import json
import os
from collections import defaultdict

import config
from scraper import load_all_results


def build_venue_stats(results: dict = None) -> dict:
    """
    過去の全レース結果から会場別の決まり手別・出目ランキングを構築。
    差し除外。
    
    Returns: {
        "01": {  # 会場コード
            "name": "桐生",
            "total_races": 500,
            "filtered_races": 120,  # 差し除外後
            "pattern_counts": {"2-3-4": 15, "3-2-4": 12, ...},
            "pattern_kimarite": {"2-3-4": "まくり", ...},
            "patterns": [  # 確率順
                {"trifecta": "2-3-4", "count": 15, "prob": 0.125, "kimarite": "まくり", "cum_prob": 0.125},
                ...
            ]
        },
        ...
    }
    """
    if results is None:
        results = load_all_results()
    
    venue_data = {}
    
    for key, races in results.items():
        for race in races:
            jcd = race.get("venue", "")
            if not jcd:
                continue
            
            if jcd not in venue_data:
                venue_data[jcd] = {
                    "name": config.VENUE_CODES.get(jcd, f"会場{jcd}"),
                    "total_races": 0,
                    "filtered_races": 0,
                    "pattern_counts": defaultdict(int),
                    "pattern_kimarite": {},
                }
            
            venue_data[jcd]["total_races"] += 1
            
            result = race.get("result", {})
            kimarite = result.get("kimarite", "")
            winning_boat = result.get("winning_boat", 0)
            trifecta = result.get("trifecta", "")
            
            if not kimarite or not trifecta or winning_boat == 0:
                continue
            
            # ── 差し除外フィルタ ──
            if kimarite == "差し":
                continue
            
            # ── 対象決まり手×艇番の組み合わせチェック ──
            allowed = config.ALLOWED_KIMARITE_BOATS.get(winning_boat, [])
            if kimarite not in allowed:
                continue
            
            # ── カウント ──
            venue_data[jcd]["filtered_races"] += 1
            venue_data[jcd]["pattern_counts"][trifecta] += 1
            venue_data[jcd]["pattern_kimarite"][trifecta] = kimarite
    
    # ── 確率計算 + 97% 累積フィルタ ──
    for jcd, data in venue_data.items():
        total = data["filtered_races"]
        if total == 0:
            data["patterns"] = []
            continue
        
        # 出目→確率に変換
        patterns = []
        for trifecta, count in data["pattern_counts"].items():
            prob = count / total
            patterns.append({
                "trifecta": trifecta,
                "count": count,
                "prob": round(prob, 6),
                "kimarite": data["pattern_kimarite"].get(trifecta, ""),
            })
        
        # 確率降順ソート
        patterns.sort(key=lambda x: x["prob"], reverse=True)
        
        # 累積確率を計算 + 97%フィルタ
        cum_prob = 0.0
        filtered_patterns = []
        for p in patterns:
            cum_prob += p["prob"]
            p["cum_prob"] = round(cum_prob, 6)
            filtered_patterns.append(p)
            if cum_prob >= config.CUMULATIVE_PROB_CUTOFF:
                break
        
        data["patterns"] = filtered_patterns
        
        # defaultdictをdictに変換 (JSON保存用)
        data["pattern_counts"] = dict(data["pattern_counts"])
    
    return venue_data


def get_venue_ranking(venue_stats: dict, jcd: str) -> list[dict]:
    """
    指定会場の出目ランキングを取得。
    """
    if jcd not in venue_stats:
        return []
    return venue_stats[jcd].get("patterns", [])


def save_venue_stats(venue_stats: dict):
    """会場統計を保存"""
    with open(config.STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(venue_stats, f, ensure_ascii=False, indent=2)


def load_venue_stats() -> dict:
    """保存済み会場統計を読み込む"""
    if os.path.exists(config.STATS_FILE):
        with open(config.STATS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def print_venue_ranking(venue_stats: dict, jcd: str, top_n: int = 30):
    """
    会場別出目ランキングをコンソールに出力。
    """
    data = venue_stats.get(jcd, {})
    name = data.get("name", jcd)
    patterns = data.get("patterns", [])
    total = data.get("filtered_races", 0)
    
    print(f"\n{'='*60}")
    print(f"  {name} Ranking (sashi excluded / 97% cumulative)")
    print(f"  Filtered races: {total}")
    print(f"{'='*60}")
    print(f"  {'Rank':>4} | {'Bet':>7} | {'Prob':>7} | {'Cum':>7} | {'Count':>5} | Kimarite")
    print(f"  {'-'*55}")
    
    for i, p in enumerate(patterns[:top_n]):
        prob_pct = p["prob"] * 100
        cum_pct = p["cum_prob"] * 100
        print(f"  {i+1:>4} | {p['trifecta']:>7} | {prob_pct:>6.2f}% | {cum_pct:>6.1f}% | {p['count']:>5} | {p['kimarite']}")


def generate_full_probability_table(venue_stats: dict) -> list[dict]:
    """
    全会場の確率テーブルを生成。
    """
    table = []
    for jcd in sorted(venue_stats.keys()):
        data = venue_stats[jcd]
        for p in data.get("patterns", []):
            table.append({
                "venue": jcd,
                "venue_name": data.get("name", ""),
                "trifecta": p["trifecta"],
                "prob": p["prob"],
                "cum_prob": p["cum_prob"],
                "count": p["count"],
                "kimarite": p["kimarite"],
            })
    return table


if __name__ == "__main__":
    print("会場統計を構築中...")
    stats = build_venue_stats()
    save_venue_stats(stats)
    
    for jcd in sorted(stats.keys()):
        print_venue_ranking(stats, jcd, top_n=10)
