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

JST = timezone(timedelta(hours=9))
ODDS_TTL   = 900   # 15分
EXHIBIT_TTL = 900  # 15分
EXHIBIT_WINDOW = 35  # 展示タイムは発走35分前から


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
