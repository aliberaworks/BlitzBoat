"""
レース前自動更新スクリプト
発走60分以内のレースのオッズ・展示タイムを取得し data/prerace_YYYYMMDD.json に保存。

使用:
  python prerace_updater.py                  # 今日、発走60分以内
  python prerace_updater.py --window 90      # 90分以内
  python prerace_updater.py --force          # キャッシュ無視で再取得
  python prerace_updater.py --date 20260501  # 日付指定
"""
import argparse
import json
import os
import sys
from datetime import date, datetime, timezone, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path = [_HERE] + [p for p in sys.path if p != _HERE]

import importlib.util as _ilu
import config

_spec = _ilu.spec_from_file_location("scraper", os.path.join(_HERE, "scraper.py"))
_mod  = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
scrape_odds_3t    = _mod.scrape_odds_3t
scrape_beforeinfo = _mod.scrape_beforeinfo

try:
    from line_bot import send_ev_notification
    _LINE_AVAILABLE = True
except Exception:
    _LINE_AVAILABLE = False
    def send_ev_notification(*a, **kw): return False

JST = timezone(timedelta(hours=9))
ODDS_TTL   = 900   # 15分
EXHIBIT_TTL = 900  # 15分
EXHIBIT_WINDOW = 35  # 展示タイムは発走35分前から
EV_THRESH  = 0.5   # LINE通知する最低EV
EV_NOTIFY_WINDOW = 55  # 発走何分前にEV通知するか
KIMARITE = ["逃げ", "差し", "まくり", "まくり差し", "抜き", "恵まれ"]


def _load_meta():
    """model_meta.json を返す（なければ空dict）"""
    import json as _json
    path = os.path.join(config.DATA_DIR, "model_meta.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return _json.load(f)


def _compute_ev(boat_prob: dict, odds_3t: dict, meta: dict) -> list:
    """全3連単コンボのEVを計算して [{r1,r2,r3,p_r1,p_combo,odds,ev}] で返す"""
    from itertools import permutations as _perm
    BOATS = list(range(1, 7))
    trifecta_all = meta.get("trifecta_stats_all", meta.get("trifecta_stats", {}))
    km_by_boat   = meta.get("km_by_boat", {})
    rows = []
    for r1 in BOATS:
        p_r1    = boat_prob.get(r1, 0.0)
        km_cond = km_by_boat.get(str(r1), {})
        all_c   = list(_perm([b for b in BOATS if b != r1], 2))
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


def _minutes_until(race_time_str: str, now: datetime) -> float:
    """XX:XX 形式の時刻まで何分か。負は過去。"""
    if not race_time_str:
        return float("inf")
    try:
        h, m = map(int, race_time_str.split(":"))
        race_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        diff = (race_dt - now).total_seconds() / 60
        # 23:59→00:01 のまたぎを補正
        if diff < -600:
            diff += 24 * 60
        return diff
    except Exception:
        return float("inf")


def run(hd: str, window_min: int = 60, force: bool = False, verbose: bool = True) -> int:
    """
    対象日 hd のレースのうち、発走 window_min 分以内のものを更新する。
    戻り値: 更新したレース数
    """
    batch_path   = os.path.join(config.DATA_DIR, f"today_{hd}.json")
    prerace_path = os.path.join(config.DATA_DIR, f"prerace_{hd}.json")

    if not os.path.exists(batch_path):
        if verbose:
            print(f"[SKIP] 朝バッチデータなし: {batch_path}")
        return 0

    with open(batch_path, encoding="utf-8") as f:
        batch = json.load(f)

    existing: dict = {}
    if os.path.exists(prerace_path):
        try:
            with open(prerace_path, encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = {}

    meta     = _load_meta()
    now_jst  = datetime.now(JST)
    now_naive = now_jst.replace(tzinfo=None)
    now_ts   = now_jst.timestamp()
    preds    = batch.get("predictions", [])
    updated  = 0

    for p in preds:
        ck   = f"{p['jcd']}_{p['race_no']}"
        mins = _minutes_until(p.get("race_time", ""), now_naive)

        # 対象外（過去レース or まだ遠い）
        if mins < -10 or mins > window_min:
            continue

        entry = existing.get(ck, {})

        # オッズ取得
        odds_age = now_ts - entry.get("odds_ts", 0)
        if force or odds_age > ODDS_TTL:
            try:
                odds = scrape_odds_3t(p["jcd"], hd, p["race_no"])
                if odds:
                    entry["odds"] = {
                        f"{k[0]}-{k[1]}-{k[2]}": v for k, v in odds.items()
                    }
                    entry["odds_ts"] = now_ts
                    updated += 1
                    if verbose:
                        print(f"  オッズ: {p['venue_name']} {p['race_no']}R  "
                              f"({mins:.0f}分前, {len(odds)}通り)")

                    # EV計算 → LINE通知（発走 EV_NOTIFY_WINDOW 分前かつ未通知）
                    if (mins <= EV_NOTIFY_WINDOW
                            and not entry.get("line_notified")
                            and meta):
                        boat_prob = {int(k): v for k, v in p["boat_prob"].items()}
                        ev_rows   = _compute_ev(boat_prob, odds, meta)
                        top_ev    = [r for r in ev_rows if r["ev"] >= EV_THRESH]
                        if top_ev:
                            sent = send_ev_notification(
                                p["venue_name"], p["race_no"],
                                p.get("race_time", ""),
                                ev_rows, EV_THRESH,
                            )
                            if sent:
                                entry["line_notified"] = now_ts
                                if verbose:
                                    print(f"  📲 LINE送信: {p['venue_name']} {p['race_no']}R "
                                          f"(EV≥{EV_THRESH}: {len(top_ev)}件)")
            except Exception as e:
                if verbose:
                    print(f"  [ERR] オッズ {p['venue_name']} {p['race_no']}R: {e}")

        # 展示タイム（発走 EXHIBIT_WINDOW 分前から）
        if mins <= EXHIBIT_WINDOW:
            exhibit_age = now_ts - entry.get("exhibit_ts", 0)
            if force or exhibit_age > EXHIBIT_TTL:
                try:
                    exhibit = scrape_beforeinfo(p["jcd"], hd, p["race_no"])
                    if exhibit:
                        entry["exhibit"] = {
                            str(e["boat"]): e.get("exhibit_st")
                            for e in exhibit
                            if e.get("exhibit_st") is not None
                        }
                        entry["exhibit_ts"] = now_ts
                        updated += 1
                        if verbose:
                            print(f"  展示ST: {p['venue_name']} {p['race_no']}R")
                except Exception as e:
                    if verbose:
                        print(f"  [ERR] 展示ST {p['venue_name']} {p['race_no']}R: {e}")

        existing[ck] = entry

    if updated > 0:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        with open(prerace_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        if verbose:
            print(f"保存: {prerace_path}  (更新 {updated}件)")
    elif verbose:
        print(f"更新なし (window={window_min}分, 対象レースなし or キャッシュ有効)")

    return updated


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="レース前オッズ・展示タイム自動取得")
    parser.add_argument("--date",   default=None,  help="日付 YYYYMMDD (デフォルト: 今日)")
    parser.add_argument("--window", type=int, default=60, help="発走何分前まで対象にするか")
    parser.add_argument("--force",  action="store_true", help="キャッシュ無視で再取得")
    args = parser.parse_args()

    hd = args.date or date.today().strftime("%Y%m%d")
    print(f"\n=== レース前更新  {hd}  (window={args.window}分) ===")
    run(hd, window_min=args.window, force=args.force)
