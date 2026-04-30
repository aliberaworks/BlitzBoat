"""
発走前LINE通知スクリプト（GitHub Actions専用・ステートレス）

発走30〜60分前のレースのオッズを取得してEV計算し、EV≥0.5の買い目をLINEに送る。
GitHub Actions が30分ごとに実行するため、各レースは自然に1回だけ通知される。

使用:
  python notify_prerace.py              # 今日
  python notify_prerace.py --date 20260501
  python notify_prerace.py --min 20 --max 65   # 窓を変更
"""
import argparse
import json
import os
import sys
from datetime import date, datetime, timezone, timedelta
from itertools import permutations

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path = [_HERE] + [p for p in sys.path if p != _HERE]

import importlib.util as _ilu
import config

_spec = _ilu.spec_from_file_location("scraper", os.path.join(_HERE, "scraper.py"))
_mod  = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
scrape_odds_3t = _mod.scrape_odds_3t

try:
    from line_bot import send_ev_notification, send_line_message
except Exception:
    def send_ev_notification(*a, **kw): return False
    def send_line_message(*a, **kw):    return False

JST      = timezone(timedelta(hours=9))
KIMARITE = ["逃げ", "差し", "まくり", "まくり差し", "抜き", "恵まれ"]
BOATS    = list(range(1, 7))
EV_THRESH = 0.5


def _minutes_until(race_time_str: str, now: datetime) -> float:
    if not race_time_str:
        return float("inf")
    try:
        h, m = map(int, race_time_str.split(":"))
        race_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        diff = (race_dt - now).total_seconds() / 60
        if diff < -600:
            diff += 24 * 60
        return diff
    except Exception:
        return float("inf")


def _compute_ev(boat_prob: dict, odds_3t: dict, meta: dict) -> list:
    trifecta_all = meta.get("trifecta_stats_all", meta.get("trifecta_stats", {}))
    km_by_boat   = meta.get("km_by_boat", {})
    rows = []
    for r1 in BOATS:
        p_r1    = boat_prob.get(r1, 0.0)
        km_cond = km_by_boat.get(str(r1), {})
        all_c   = list(permutations([b for b in BOATS if b != r1], 2))
        p_cond  = {c: 0.0 for c in all_c}
        for km in KIMARITE:
            p_km = km_cond.get(km, 0.0)
            if p_km <= 0:
                continue
            tri_data = trifecta_all.get(f"{r1}_{km}", [])
            km_map: dict = {}
            km_known = 0.0
            for ent in tri_data:
                try:
                    ps = ent["combo"].split("-")
                    c  = (int(float(ps[0])), int(float(ps[1])))
                    km_map[c] = ent["pct"]
                    km_known += ent["pct"]
                except Exception:
                    pass
            unknowns = [c for c in all_c if c not in km_map]
            unif = max(0.0, 1.0 - km_known) / len(unknowns) if unknowns else 0.0
            for c in unknowns:
                km_map[c] = unif
            for c in all_c:
                p_cond[c] += p_km * km_map.get(c, unif)
        for r2, r3 in all_c:
            ov = odds_3t.get((r1, r2, r3))
            if ov is None or ov <= 0:
                continue
            p_combo = p_r1 * p_cond.get((r2, r3), 1.0 / len(all_c))
            rows.append({
                "r1": r1, "r2": r2, "r3": r3,
                "p_r1": p_r1, "p_combo": p_combo,
                "odds": ov, "ev": p_combo * ov - 1.0,
            })
    return sorted(rows, key=lambda x: x["ev"], reverse=True)


def run(hd: str, win_min: int = 30, win_max: int = 60):
    batch_path   = os.path.join(config.DATA_DIR, f"today_{hd}.json")
    meta_path    = os.path.join(config.DATA_DIR, "model_meta.json")
    prerace_path = os.path.join(config.DATA_DIR, f"prerace_{hd}.json")

    if not os.path.exists(batch_path):
        print(f"[SKIP] 朝バッチなし: {batch_path}")
        return

    with open(batch_path, encoding="utf-8") as f:
        batch = json.load(f)
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)

    # prerace_json があれば読み込む（展示タイム・コース変更情報）
    prerace: dict = {}
    if os.path.exists(prerace_path):
        try:
            with open(prerace_path, encoding="utf-8") as f:
                prerace = json.load(f)
        except Exception:
            pass

    now = datetime.now(JST).replace(tzinfo=None)
    preds = batch.get("predictions", [])

    targets = []
    for p in preds:
        mins = _minutes_until(p.get("race_time", ""), now)
        if win_min <= mins < win_max:
            targets.append((mins, p))

    if not targets:
        print(f"対象レースなし (窓: {win_min}〜{win_max}分前)")
        return

    print(f"対象: {len(targets)}レース (窓: {win_min}〜{win_max}分前)")
    notified = 0

    for mins, p in sorted(targets):
        ck = f"{p['jcd']}_{p['race_no']}"
        print(f"  {p['venue_name']} {p['race_no']}R  ({mins:.0f}分前) オッズ取得中...")
        try:
            odds = scrape_odds_3t(p["jcd"], hd, p["race_no"])
            if not odds:
                print(f"    オッズ取得失敗")
                continue

            boat_prob    = {int(k): v for k, v in p["boat_prob"].items()}
            ev_rows      = _compute_ev(boat_prob, odds, meta)
            top_ev       = [r for r in ev_rows if r["ev"] >= EV_THRESH]
            pr_entry     = prerace.get(ck, {})
            course_changes = pr_entry.get("course_changes") or None

            if course_changes:
                print(f"    ⚠️ コース変更: {course_changes}")

            if not top_ev:
                print(f"    EV≥{EV_THRESH}の買い目なし")
                continue

            sent = send_ev_notification(
                p["venue_name"], p["race_no"],
                p.get("race_time", ""),
                ev_rows, EV_THRESH,
                course_changes=course_changes,
            )
            if sent:
                notified += 1
                print(f"    📲 LINE送信 (EV≥{EV_THRESH}: {len(top_ev)}件)")
            else:
                print(f"    LINE未設定 (EV≥{EV_THRESH}: {len(top_ev)}件)")

        except Exception as e:
            print(f"    [ERR] {e}")

    print(f"\n完了: {notified}/{len(targets)}レースをLINE通知")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None)
    parser.add_argument("--min",  type=int, default=30, help="通知する最小分数前 (default: 30)")
    parser.add_argument("--max",  type=int, default=60, help="通知する最大分数前 (default: 60)")
    args = parser.parse_args()

    hd = args.date or date.today().strftime("%Y%m%d")
    print(f"\n=== 発走前通知  {hd}  窓: {args.min}〜{args.max}分前 ===")
    run(hd, win_min=args.min, win_max=args.max)
