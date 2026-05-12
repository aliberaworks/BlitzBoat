"""
ScenarioBoat 朝バッチ
GitHub Actions: 毎朝6時JST

1. 本日の全レースを取得（BlitzBoatのスクレイパー流用）
2. シナリオ確率を予測
3. scenario_today_YYYYMMDD.json に保存
"""
import argparse, json, os, pickle, sys
from datetime import date, datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_BB   = os.path.dirname(_HERE)   # BlitzBoat/
sys.path = [_HERE, _BB] + [p for p in sys.path if p not in (_HERE, _BB)]

import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("scraper", os.path.join(_BB, "scraper.py"))
_scraper = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_scraper)
scrape_today_venues = _scraper.scrape_today_venues
scrape_racelist     = _scraper.scrape_racelist
scrape_race_times   = _scraper.scrape_race_times

try:
    from bubble_today import compute_bubble_status, get_race_bubble_today
except ImportError:
    def compute_bubble_status(*a, **kw): return None
    def get_race_bubble_today(*a, **kw): return None

import config
from features import build_row_vector

JST = timezone(timedelta(hours=9))
GRADE_MAP = config.GRADE_MAP


def load_model():
    with open(config.MODEL_PKL, "rb") as f:
        d = pickle.load(f)
    return d["model"], d["label_encoder"]


def predict_scenario_probs(model, le, race_vec: np.ndarray) -> dict:
    proba = model.predict_proba(race_vec.reshape(1, -1))[0]
    return {cls: float(p) for cls, p in zip(le.classes_, proba)}


def build_race_dict(venue_info: dict, race_no: int, hd: str) -> dict | None:
    jcd = venue_info["jcd"]
    rows = scrape_racelist(jcd, hd, race_no)
    if not rows:
        return None

    race = {
        "jcd":        jcd,
        "venue_code": jcd,
        "venue_name": venue_info["name"],
        "race_no":    race_no,
        "hd":         hd,
    }
    for row in rows:
        b = int(row.get("boat", 0))
        if b not in range(1, 7):
            continue
        race[f"b{b}_avg_st"]        = float(row.get("avg_st", 0) or 0)
        race[f"b{b}_motor_2rate"]   = float(row.get("motor_2rate", 0) or 0)
        race[f"b{b}_national_rate"] = float(row.get("national_rate", 0) or 0)
        race[f"b{b}_local_rate"]    = float(row.get("local_rate", 0) or 0)
        race[f"b{b}_grade"]         = str(row.get("grade", "B1")).strip()

    # 節内情報（bubble_today）
    bubble = get_race_bubble_today(jcd, hd, race_no)
    if bubble:
        race["day_from_start"]  = bubble.get("day_from_start", 0)
        race["is_final_day"]    = float(bubble.get("is_final_day", 0))
        race["series_progress"] = bubble.get("series_progress", 0.0)
        for b in range(1, 7):
            bd = bubble.get(f"boat_{b}", {})
            race[f"b{b}_qualify"]       = float(bd.get("qualify", 0))
            race[f"b{b}_bubble"]        = float(bd.get("bubble", 0))
            race[f"b{b}_points_before"] = float(bd.get("points_before", 0))
    else:
        race["day_from_start"]  = 0
        race["is_final_day"]    = 0.0
        race["series_progress"] = 0.0
        for b in range(1, 7):
            race[f"b{b}_qualify"]       = 0.0
            race[f"b{b}_bubble"]        = 0.0
            race[f"b{b}_points_before"] = 0.0

    return race


def main(hd: str):
    print(f"\n=== ScenarioBoat 朝バッチ {hd} ===")
    model, le = load_model()

    venues = scrape_today_venues(hd)
    if not venues:
        print("[SKIP] 本日の開催なし")
        return

    print(f"開催: {len(venues)}会場")

    # レース時刻取得
    race_times: dict[str, dict[int, str]] = {}
    for v in venues:
        times = scrape_race_times(v["jcd"], hd)
        race_times[v["jcd"]] = times or {}

    # 全レース予測
    predictions = []
    tasks = [(v, rno) for v in venues for rno in range(1, 13)]

    def process(args):
        v, rno = args
        race = build_race_dict(v, rno, hd)
        if race is None:
            return None
        vec = build_row_vector(race)
        if vec is None:
            return None
        sc_probs = predict_scenario_probs(model, le, vec)
        race["scenario_probs"] = sc_probs
        race["race_time"] = race_times.get(v["jcd"], {}).get(rno, "")
        top_sc = max(sc_probs, key=sc_probs.get)
        race["top_scenario"] = top_sc
        race["top_prob"]     = round(sc_probs[top_sc], 3)
        return race

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(process, t): t for t in tasks}
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                predictions.append(r)

    predictions.sort(key=lambda x: (x["jcd"], x["race_no"]))
    print(f"予測完了: {len(predictions)}レース")

    # シナリオ分布サマリー
    from collections import Counter
    top_sc_counts = Counter(r["top_scenario"] for r in predictions)
    print("\nシナリオ分布（最高確率）:")
    for sc, n in top_sc_counts.most_common():
        print(f"  {sc:10s}: {n}レース")

    # 保存
    os.makedirs(config.DATA_DIR, exist_ok=True)
    out = {"hd": hd, "predictions": predictions}
    path = config.today_json(hd)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"\n保存: {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None)
    args = parser.parse_args()
    hd = args.date or date.today().strftime("%Y%m%d")
    main(hd)
