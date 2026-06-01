"""
makuri - 結果収集・ROI追跡
毎日22:00 JSTに GitHub Actions が実行し、
当日の買い目の結果を accuracy_log.json に累積する。

実行:
  python collect.py
  python collect.py --date 20260601
"""
import argparse, json, os, sys, time
from datetime import date

import config, combo, scenario

sys.path.insert(0, config.BLITZBOAT_DIR)
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("scraper", os.path.join(config.BLITZBOAT_DIR, "scraper.py"))
_scr  = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_scr)
scrape_race_result = _scr.scrape_race_result

BET_UNIT = 100


def run(hd: str):
    batch_path   = os.path.join(config.DATA_DIR, f"today_{hd}.json")
    prerace_path = os.path.join(config.DATA_DIR, f"prerace_{hd}.json")
    log_path     = config.ACCURACY_LOG_JSON

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

    log = {}
    if os.path.exists(log_path):
        try:
            with open(log_path, encoding="utf-8") as f:
                log = json.load(f)
        except Exception:
            pass

    preds = batch.get("predictions", [])
    day_stats = {
        "layer2": {"bets": 0, "hits": 0, "invest_yen": 0, "return_yen": 0},
        "layer3": {"bets": 0, "hits": 0, "invest_yen": 0, "return_yen": 0},
    }

    for p in preds:
        layer = p.get("layer", 0)
        if layer not in (2, 3): continue

        ck = f"{p['jcd']}_{p['race_no']}"
        ck_data = prerace.get(ck, {})
        # 通知済みのレースのみ集計（未通知は買ってないので除外）
        if not ck_data.get("notified") and not ck_data.get("odds"):
            continue

        try:
            result = scrape_race_result(p["jcd"], hd, p["race_no"])
            time.sleep(0.5)
        except Exception as e:
            print(f"  [ERR] {p['venue_name']} {p['race_no']}R: {e}")
            continue
        if not result:
            continue

        actual_tri = result.get("trifecta", "")
        pay_3t     = result.get("payouts", {}).get("3連単", 0)

        # 買い目再計算
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

        lk = f"layer{layer}"
        d  = day_stats[lk]
        d["bets"]       += len(combos)
        d["invest_yen"] += len(combos) * BET_UNIT

        for c in combos:
            if c["combo"] == actual_tri and pay_3t > 0:
                d["hits"]       += 1
                d["return_yen"] += int(pay_3t)
                print(f"  ✅ {p['venue_name']} {p['race_no']}R  {actual_tri}  "
                      f"{pay_3t:,}円 (Layer{layer})")
                break
        else:
            print(f"  ❌ {p['venue_name']} {p['race_no']}R  "
                  f"実際: {actual_tri}  Layer{layer}")

    log[hd] = day_stats
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
    print(f"\n保存: {log_path}")

    # Layer別サマリー
    for lk, label in [("layer2", "まくり成功"), ("layer3", "裏スジ")]:
        d = day_stats[lk]
        if d["bets"] == 0:
            print(f"  {label}: 買い目なし")
            continue
        roi = (d["return_yen"] / d["invest_yen"] - 1) * 100 if d["invest_yen"] else 0
        print(f"  {label}: {d['bets']}点/{d['hits']}的中  "
              f"ROI {roi:+.1f}%  投資{d['invest_yen']:,}円  回収{d['return_yen']:,}円")

    # LINE日次サマリー
    from notify import send_daily_summary
    send_daily_summary(log, hd)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None)
    args = parser.parse_args()
    hd = args.date or date.today().strftime("%Y%m%d")
    print(f"\n=== makuri 結果収集  {hd} ===\n")
    run(hd)
