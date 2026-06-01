"""
makuri - 全艇データ付き歴史データ収集スクリプト

既存の fetch_historical_csv.py（1号艇のみ）を拡張し、
全6艇の motor_2rate, national_rate, local_rate, avg_st, grade を取得。

出力: data/race_results_full.csv
  - まくりモデルの再学習に使用

使い方:
  python fetch_history.py                    # 過去365日
  python fetch_history.py --days 1825        # 過去5年
  python fetch_history.py --start 2020-01-01 # 2020年から今日まで
  python fetch_history.py --start 2006-01-01 # 20年分（12〜20時間）

中断・再開対応: data/progress_history_full.json
"""
import argparse, csv, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

import config

sys.path.insert(0, config.BLITZBOAT_DIR)
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("scraper", os.path.join(config.BLITZBOAT_DIR, "scraper.py"))
_scr  = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_scr)
scrape_racelist    = _scr.scrape_racelist
scrape_race_result = _scr.scrape_race_result
scrape_today_venues= _scr.scrape_today_venues

MAX_WORKERS    = 6
REQUEST_DELAY  = 0.8
PROGRESS_FILE  = os.path.join(config.DATA_DIR, "progress_history_full.json")
OUTPUT_CSV     = os.path.join(config.DATA_DIR, "race_results_full.csv")

FIELDNAMES = (
    ["date","venue","venue_name","race_no"]
    + [f"b{i}_{c}" for i in range(1,7)
       for c in ["grade","national_rate","local_rate","avg_st","motor_no","motor_2rate"]]
    + ["st1","st2","st3","st4","st5","st6"]
    + ["winning_boat","kimarite","rank1","rank2","rank3","rank4","rank5","rank6",
       "trifecta","pay_3t"]
)


def load_progress() -> set:
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, encoding="utf-8") as f:
            return set(json.load(f).get("done", []))
    return set()

def save_progress(done: set):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump({"done": sorted(done)}, f)


def scrape_one_day(hd: str, jcd: str, venue_name: str) -> list[dict]:
    rows = []
    for rno in range(1, 13):
        try:
            entries = scrape_racelist(jcd, hd, rno)
            if not entries or len(entries) < 6:
                continue
            result  = scrape_race_result(jcd, hd, rno)
            if not result:
                continue

            row = {"date": hd, "venue": jcd, "venue_name": venue_name, "race_no": rno}

            # 全艇データ
            for e in entries:
                b = e["boat"]
                row[f"b{b}_grade"]         = e.get("grade", "")
                row[f"b{b}_national_rate"] = e.get("national_rate", "")
                row[f"b{b}_local_rate"]    = e.get("local_rate", "")
                row[f"b{b}_avg_st"]        = e.get("avg_st", "")
                row[f"b{b}_motor_no"]      = e.get("motor_no", "")
                row[f"b{b}_motor_2rate"]   = e.get("motor_2rate", "")

            # ST: [{"boat":1, "st":0.25}, ...] → {艇番: ST}
            sts_raw = result.get("start_times", [])
            sts = {str(e["boat"]): e.get("st", "") for e in sts_raw if isinstance(e, dict)}
            for b in range(1, 7):
                row[f"st{b}"] = sts.get(str(b), "")

            # 着順: [{"rank":1, "boat":3}, ...] → {着順: 艇番}
            results_raw = result.get("results", [])
            rank_to_boat = {str(e.get("rank","")): e.get("boat","") for e in results_raw if isinstance(e, dict)}
            # rank{b} = b号艇の着順
            boat_to_rank = {str(v): k for k, v in rank_to_boat.items()}
            for b in range(1, 7):
                row[f"rank{b}"] = boat_to_rank.get(str(b), "")

            row["winning_boat"] = result.get("winning_boat", "")
            row["kimarite"]     = result.get("kimarite", "")
            row["trifecta"]     = result.get("trifecta", "")
            row["pay_3t"]       = result.get("payouts", {}).get("3連単", "")

            rows.append(row)
            time.sleep(REQUEST_DELAY / 2)

        except Exception as e:
            print(f"    [WARN] {venue_name} {rno}R: {e}", file=sys.stderr)
    return rows


def run(start_date: date, end_date: date):
    done = load_progress()
    print(f"期間: {start_date} 〜 {end_date}（{(end_date - start_date).days + 1}日）")
    print(f"収集済みスキップ: {len(done)}件")

    # 出力ファイルの準備（追記モード）
    os.makedirs(config.DATA_DIR, exist_ok=True)
    write_header = not os.path.exists(OUTPUT_CSV) or os.path.getsize(OUTPUT_CSV) == 0

    total_races = 0
    cur = start_date
    while cur <= end_date:
        hd = cur.strftime("%Y%m%d")
        cur += timedelta(days=1)

        venues = scrape_today_venues(hd)
        if not venues:
            continue

        pending = [(hd, v["jcd"], v.get("name","")) for v in venues
                   if f"{v['jcd']}_{hd}" not in done]
        if not pending:
            continue

        day_rows = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = {ex.submit(scrape_one_day, hd, jcd, name): (jcd, name)
                       for hd, jcd, name in pending}
            for fut in as_completed(futures):
                jcd, name = futures[fut]
                try:
                    rows = fut.result()
                    day_rows.extend(rows)
                    done.add(f"{jcd}_{hd}")
                except Exception as e:
                    print(f"  [ERR] {name} {hd}: {e}", file=sys.stderr)

        if day_rows:
            with open(OUTPUT_CSV, "a", encoding="utf-8", newline="") as fp:
                w = csv.DictWriter(fp, fieldnames=FIELDNAMES, extrasaction="ignore")
                if write_header:
                    w.writeheader()
                    write_header = False
                w.writerows(day_rows)
            total_races += len(day_rows)

        save_progress(done)
        print(f"  {hd}: {len(day_rows)}R  累計{total_races:,}R  残{(end_date - cur).days}日")

    print(f"\n完了: {OUTPUT_CSV}  ({total_races:,}レース)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=None, help="開始日 YYYY-MM-DD")
    parser.add_argument("--end",   default=None, help="終了日 YYYY-MM-DD (デフォルト: 今日)")
    parser.add_argument("--days",  type=int, default=365, help="過去N日 (--start未指定時)")
    args = parser.parse_args()

    today = date.today()
    if args.start:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
    else:
        start = today - timedelta(days=args.days)
    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else today

    print(f"\n=== makuri 歴史データ収集（全艇版）===")
    run(start, end)
