"""
makuri - 朝バッチ
本日の全レースを予測し today_YYYYMMDD.json に保存する。

実行:
  python morning.py
  python morning.py --date 20260601
"""
import argparse, csv, json, os, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import config, scenario

# BlitzBoatのスクレイパーを参照
sys.path.insert(0, config.BLITZBOAT_DIR)
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("scraper", os.path.join(config.BLITZBOAT_DIR, "scraper.py"))
_scr  = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_scr)
scrape_today_venues = _scr.scrape_today_venues
scrape_racelist     = _scr.scrape_racelist
scrape_race_times   = _scr.scrape_race_times
scrape_beforeinfo   = _scr.scrape_beforeinfo

MAX_WORKERS = 8

EXHIBIT_CSV = os.path.join(config.DATA_DIR, "exhibit_st_history.csv")
EXHIBIT_FIELDS = [
    "date", "venue", "race_no",
    "b1_exhibit_st", "b2_exhibit_st", "b3_exhibit_st",
    "b4_exhibit_st", "b5_exhibit_st", "b6_exhibit_st",
]


def save_exhibit_st(hd: str, jcd: str, rno: int, exhibit_sts: dict):
    """展示STをCSVに追記保存する（重複チェックなし・append）"""
    if not exhibit_sts:
        return
    write_header = not os.path.exists(EXHIBIT_CSV)
    with open(EXHIBIT_CSV, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=EXHIBIT_FIELDS)
        if write_header:
            w.writeheader()
        row = {"date": hd, "venue": jcd, "race_no": rno}
        for b in range(1, 7):
            row[f"b{b}_exhibit_st"] = exhibit_sts.get(b, "")
        w.writerow(row)


def predict_venue(jcd, venue_name, hd, n_races):
    race_times = scrape_race_times(jcd, hd)
    results = []
    for rno in range(1, n_races + 1):
        try:
            entries = scrape_racelist(jcd, hd, rno)
            if not entries or len(entries) < 6:
                continue

            boat_data = {}
            for e in entries:
                b = e["boat"]
                boat_data[b] = {
                    "avg_st":        float(e.get("avg_st") or 0.17),
                    "motor_2rate":   float(e.get("motor_2rate") or 38.0),
                    "national_rate": float(e.get("national_rate") or 5.0),
                    "local_rate":    float(e.get("local_rate") or 5.0),
                    "grade":         e.get("grade", ""),
                    "reg_no":        e.get("toban", ""),
                }

            # 展示ST取得（取れた分だけ使う・取れなければ exhibit_diff=0 で動く）
            beforeinfo = scrape_beforeinfo(jcd, hd, rno)
            exhibit_sts = {}
            if beforeinfo:
                for entry in beforeinfo:
                    b  = entry.get("boat")
                    st = entry.get("exhibit_st")
                    if b and st is not None and 1 <= b <= 6:
                        exhibit_sts[b] = st
                if exhibit_sts:
                    save_exhibit_st(hd, jcd, rno, exhibit_sts)

            sr = scenario.score_race(jcd, boat_data, exhibit_sts=exhibit_sts if exhibit_sts else None)
            results.append({
                "jcd":         jcd,
                "venue_name":  venue_name,
                "race_no":     rno,
                "race_time":   race_times.get(rno, ""),
                "boat_data":   {str(k): v for k, v in boat_data.items()},
                "arare_prob":  sr["arare_prob"],
                "b1_type":     sr["b1_type"],
                "boat3":       sr["boat3"],
                "boat4":       sr["boat4"],
                "scenario":    sr["scenario"],
                "layer":       sr["layer"],
                "buy_target":  sr["buy_target"],
            })
        except Exception as e:
            print(f"  [WARN] {venue_name} {rno}R: {e}", file=sys.stderr)
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None)
    args  = parser.parse_args()
    hd    = args.date or date.today().strftime("%Y%m%d")
    print(f"\n=== makuri 朝バッチ  {hd} ===\n")

    venues_raw = scrape_today_venues(hd)
    if not venues_raw:
        print("[ERROR] 開催会場を取得できませんでした")
        sys.exit(1)
    venues = {v["jcd"]: (v["name"], v.get("races", 12)) for v in venues_raw}
    print(f"  {len(venues)}会場 / 最大{sum(r for _, r in venues.values())}レース\n")

    all_results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {
            ex.submit(predict_venue, jcd, name, hd, n_races): jcd
            for jcd, (name, n_races) in venues.items()
        }
        for fut in as_completed(futures):
            jcd = futures[fut]
            name, _ = venues[jcd]
            try:
                rows = fut.result()
                all_results.extend(rows)
                print(f"  {name}  {len(rows)}R完了")
            except Exception as e:
                print(f"  [ERR] {name}: {e}", file=sys.stderr)

    # 荒れ確率降順ソート
    all_results.sort(key=lambda x: x["arare_prob"], reverse=True)

    out_path = os.path.join(config.DATA_DIR, f"today_{hd}.json")
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "date": hd,
            "predictions": all_results,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n保存: {out_path}")

    # Layer別サマリー表示
    l0 = sum(1 for r in all_results if r["layer"] == 0)
    l1 = sum(1 for r in all_results if r["layer"] == 1)
    l2 = sum(1 for r in all_results if r["layer"] == 2)
    l3 = sum(1 for r in all_results if r["layer"] == 3)
    print(f"\n  Layer0(非荒れ): {l0}R  Layer1(荒れ警報): {l1}R  "
          f"Layer2(まくり成功): {l2}R  Layer3(裏スジ): {l3}R")

    # Layer1以上の荒れ警報をLINEに送る
    from notify import send_layer1
    arare_races = [
        r for r in all_results if r["arare_prob"] >= config.ARARE_THRESH_L1
    ][:10]
    if arare_races:
        send_layer1(arare_races, hd)


if __name__ == "__main__":
    main()
