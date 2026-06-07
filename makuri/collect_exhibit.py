"""
展示ST 収集スクリプト（当日分・日次蓄積用）

レース時間帯に複数回実行することで、今日の全レースの展示STを
exhibit_st_history.csv に蓄積する。

boatrace.jp の beforeinfo は当日レース中のみ取得可能。
レース終了後はデータが消えるため、当日のうちに収集する必要がある。

実行タイミング（GitHub Actions: JST 08:30 / 11:30 / 15:30）:
  - 08:30: 午前レース（R1〜R4）の展示ST取得
  - 11:30: 中盤レース（R5〜R8）の展示ST取得
  - 15:30: 後半レース（R9〜R12）の展示ST取得

使い方:
  python collect_exhibit.py
  python collect_exhibit.py --date 20260608
"""
import argparse, csv, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import config

sys.path.insert(0, config.BLITZBOAT_DIR)
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("scraper", os.path.join(config.BLITZBOAT_DIR, "scraper.py"))
_scr  = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_scr)
scrape_today_venues = _scr.scrape_today_venues
scrape_beforeinfo   = _scr.scrape_beforeinfo

OUTPUT_CSV = os.path.join(config.DATA_DIR, "exhibit_st_history.csv")
FIELDS = [
    "date", "venue", "race_no",
    "b1_exhibit_st", "b2_exhibit_st", "b3_exhibit_st",
    "b4_exhibit_st", "b5_exhibit_st", "b6_exhibit_st",
]
MAX_WORKERS = 6


def load_existing_keys() -> set:
    keys = set()
    if not os.path.exists(OUTPUT_CSV):
        return keys
    with open(OUTPUT_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            keys.add(f"{row['date']}_{row['venue']}_{row['race_no']}")
    return keys


def scrape_race_exhibit(hd: str, jcd: str, rno: int) -> dict | None:
    beforeinfo = scrape_beforeinfo(str(jcd).zfill(2), hd, rno)
    if not beforeinfo:
        return None
    row = {
        "date": hd, "venue": str(jcd).zfill(2), "race_no": rno,
        **{f"b{b}_exhibit_st": "" for b in range(1, 7)},
    }
    for entry in beforeinfo:
        b  = entry.get("boat")
        st = entry.get("exhibit_st")
        if b and st is not None and 1 <= b <= 6:
            row[f"b{b}_exhibit_st"] = st
    if any(row[f"b{b}_exhibit_st"] != "" for b in range(1, 7)):
        return row
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="日付 YYYYMMDD")
    args = parser.parse_args()
    hd   = args.date or date.today().strftime("%Y%m%d")

    print(f"=== 展示ST収集 {hd} ===")

    venues_raw = scrape_today_venues(hd)
    if not venues_raw:
        print("[ERROR] 開催会場を取得できませんでした")
        sys.exit(1)

    existing = load_existing_keys()
    print(f"  {len(venues_raw)}会場  既存レコード: {len(existing)}件")

    tasks = []
    for v in venues_raw:
        jcd     = v["jcd"]
        n_races = v.get("races", 12)
        for rno in range(1, n_races + 1):
            key = f"{hd}_{str(jcd).zfill(2)}_{rno}"
            if key not in existing:
                tasks.append((jcd, rno))

    if not tasks:
        print("  収集対象なし（全件取得済み）")
        return

    print(f"  収集対象: {len(tasks)}レース")

    os.makedirs(config.DATA_DIR, exist_ok=True)
    write_header = not os.path.exists(OUTPUT_CSV)
    written = 0

    with open(OUTPUT_CSV, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if write_header:
            w.writeheader()

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futs = {
                ex.submit(scrape_race_exhibit, hd, jcd, rno): (jcd, rno)
                for jcd, rno in tasks
            }
            for fut in as_completed(futs):
                jcd, rno = futs[fut]
                result = fut.result()
                if result:
                    key = f"{hd}_{str(jcd).zfill(2)}_{rno}"
                    if key not in existing:
                        w.writerow(result)
                        existing.add(key)
                        written += 1
                        print(f"  保存: 会場{jcd} R{rno}", flush=True)

    print(f"\n完了: {written}件を新規保存  (累計: {len(existing)}件)")
    print(f"  出力: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
