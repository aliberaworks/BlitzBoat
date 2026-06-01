"""
makuri - 発走前通知
GitHub Actions が30分ごとに実行し、発走30〜60分前のレースに対して
シナリオ・買い目をLINE通知する（レイヤー別）。

実行:
  python prerace.py
  python prerace.py --date 20260601 --min 30 --max 60
"""
import argparse, json, os, sys
from datetime import date, datetime, timezone, timedelta

import config, scenario, combo, notify

sys.path.insert(0, config.BLITZBOAT_DIR)
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("scraper", os.path.join(config.BLITZBOAT_DIR, "scraper.py"))
_scr  = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_scr)
scrape_odds_3t        = _scr.scrape_odds_3t
scrape_beforeinfo     = _scr.scrape_beforeinfo
detect_course_changes = _scr.detect_course_changes

JST = timezone(timedelta(hours=9))


def _minutes_until(race_time_str, now):
    if not race_time_str:
        return float("inf")
    try:
        h, m = map(int, race_time_str.split(":"))
        rd = now.replace(hour=h, minute=m, second=0, microsecond=0)
        diff = (rd - now).total_seconds() / 60
        if diff < -600: diff += 24 * 60
        return diff
    except Exception:
        return float("inf")


def run(hd: str, win_min=30, win_max=60):
    batch_path   = os.path.join(config.DATA_DIR, f"today_{hd}.json")
    prerace_path = os.path.join(config.DATA_DIR, f"prerace_{hd}.json")

    if not os.path.exists(batch_path):
        print(f"[SKIP] 朝バッチなし: {batch_path}")
        return

    with open(batch_path, encoding="utf-8") as f:
        batch = json.load(f)

    prerace = {}
    if os.path.exists(prerace_path):
        try:
            with open(prerace_path, encoding="utf-8") as f:
                prerace = json.load(f)
        except Exception:
            pass

    now = datetime.now(JST).replace(tzinfo=None)
    preds = batch.get("predictions", [])

    # ── 対象レース絞り込み ──────────────────────────────────────
    targets = []
    for p in preds:
        mins = _minutes_until(p.get("race_time", ""), now)
        if not (win_min <= mins < win_max): continue
        if p.get("layer", 0) == 0: continue          # 非荒れはスキップ
        ck = f"{p['jcd']}_{p['race_no']}"
        if prerace.get(ck, {}).get("notified"): continue  # 通知済みスキップ
        targets.append((mins, p))

    if not targets:
        print(f"対象なし ({win_min}-{win_max}分前 / Layer1以上)")
        return

    print(f"対象: {len(targets)}レース")

    l1_races, l2_list, l3_list = [], [], []

    for mins, p in sorted(targets, key=lambda x: x[0]):
        ck = f"{p['jcd']}_{p['race_no']}"
        layer = p.get("layer", 1)
        print(f"  {p['venue_name']} {p['race_no']}R ({mins:.0f}分前 / Layer{layer})")

        # オッズ・展示ST取得
        odds_map = {}
        exhibit_sts = {}
        course_changes = []
        try:
            raw_odds = scrape_odds_3t(p["jcd"], hd, p["race_no"])
            if raw_odds:
                odds_map = raw_odds
                prerace.setdefault(ck, {})["odds"] = {
                    f"{k[0]}-{k[1]}-{k[2]}": v for k, v in raw_odds.items()
                }
        except Exception as e:
            print(f"    [odds err] {e}")

        if mins <= 35:
            try:
                exhibit = scrape_beforeinfo(p["jcd"], hd, p["race_no"])
                if exhibit:
                    exhibit_sts = {e["boat"]: e.get("exhibit_st") for e in exhibit
                                   if e.get("exhibit_st") is not None}
                    course_changes = detect_course_changes(exhibit)
                    prerace.setdefault(ck, {})["exhibit"] = exhibit_sts
            except Exception as e:
                print(f"    [exhibit err] {e}")

        # 展示ST込みでシナリオ再計算
        boat_data_raw = p.get("boat_data", {})
        boat_data = {}
        for k, v in boat_data_raw.items():
            try:
                boat_data[int(k)] = v
            except Exception:
                pass

        if exhibit_sts and boat_data:
            sr = scenario.score_race(
                p["jcd"], boat_data,
                exhibit_sts={int(k): v for k, v in exhibit_sts.items()}
            )
        else:
            sr = {
                "arare_prob": p.get("arare_prob", 0),
                "b1_type":    p.get("b1_type", ""),
                "boat3":      p.get("boat3"),
                "boat4":      p.get("boat4"),
                "scenario":   p.get("scenario", ""),
                "layer":      layer,
                "buy_target": p.get("buy_target"),
            }

        combos = combo.get_combos_for_scenario(sr)

        item = {
            "venue_name":      p["venue_name"],
            "race_no":         p["race_no"],
            "race_time":       p.get("race_time", ""),
            "scenario_result": sr,
            "combos":          combos,
            "odds_map":        odds_map,
            "exhibit_sts":     exhibit_sts,   # 展示ST（LINEで表示）
            "course_changes":  course_changes,
        }

        notify_layer = sr.get("layer", 1)
        if notify_layer >= 1:
            l1_races.append({
                "venue_name": p["venue_name"],
                "race_no":    p["race_no"],
                "race_time":  p.get("race_time", ""),
                "arare_prob": sr["arare_prob"],
                "b1_type":    sr["b1_type"],
            })
        if notify_layer == 2: l2_list.append(item)
        if notify_layer == 3: l3_list.append(item)

    # ── 送信 ────────────────────────────────────────────────────
    now_str = now.strftime("%H:%M")
    sent_any = False

    l1_today = int(prerace.get("l1_sent_today", 0))
    l2_today = int(prerace.get("l2_sent_today", 0))
    l3_today = int(prerace.get("l3_sent_today", 0))

    if l2_list and l2_today < config.MAX_DAILY_NOTIFY_L2:
        if notify.send_layer2(l2_list, now_str):
            l2_today += 1
            prerace["l2_sent_today"] = l2_today
            for item in l2_list:
                ck = f"{item.get('jcd', '')}"  # 簡易マーク
            # 通知済みフラグ
            for item in l2_list:
                for k in list(prerace.keys()):
                    if item["venue_name"] in k:
                        prerace[k]["notified"] = True
            sent_any = True

    if l3_list and l3_today < config.MAX_DAILY_NOTIFY_L3:
        if notify.send_layer3(l3_list, now_str):
            l3_today += 1
            prerace["l3_sent_today"] = l3_today
            sent_any = True

    if l1_races and not sent_any and l1_today < config.MAX_DAILY_NOTIFY_L1:
        if notify.send_layer1(l1_races, hd):
            l1_today += 1
            prerace["l1_sent_today"] = l1_today

    # ── prerace保存 ─────────────────────────────────────────────
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(prerace_path, "w", encoding="utf-8") as f:
        json.dump(prerace, f, ensure_ascii=False)
    print(f"\n保存: {prerace_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None)
    parser.add_argument("--min",  type=int, default=30)
    parser.add_argument("--max",  type=int, default=60)
    args = parser.parse_args()
    hd = args.date or date.today().strftime("%Y%m%d")
    print(f"\n=== makuri 発走前通知  {hd}  窓:{args.min}-{args.max}分前 ===")
    run(hd, win_min=args.min, win_max=args.max)
