"""
ScenarioBoat 結果収集
GitHub Actions: 毎晩22:00 JST

発走前通知で保存したオッズと朝バッチのシナリオ予測を照合し、
EV成績・シナリオ精度を追跡する。
"""
import argparse, json, os, sys, time
from collections import defaultdict
from datetime import date

_HERE = os.path.dirname(os.path.abspath(__file__))
_BB   = os.path.dirname(_HERE)
sys.path = [_HERE, _BB] + [p for p in sys.path if p not in (_HERE, _BB)]

import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("scraper", os.path.join(_BB, "scraper.py"))
_scraper = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_scraper)
scrape_race_result = _scraper.scrape_race_result

import config

BET_UNIT        = 100
SCRAPE_INTERVAL = 1.5


def classify_actual(result: dict) -> str:
    """実際の結果からシナリオを近似的に逆引きする"""
    if not result or len(result.get("results", [])) < 3:
        return "unknown"
    r1 = result["results"][0]["boat"]
    r2 = result["results"][1]["boat"]
    r3 = result["results"][2]["boat"]
    km = result.get("kimarite", "")
    top3 = {r1, r2, r3}

    if km in config.MAKKURI_KM:
        if r1 == 2:         return "2mak"
        elif r1 == 3:       return "3mak"
        elif r1 == 4:       return "4mak"
        elif r1 in {5, 6}:  return "56mak"

    if r1 == 1 and km == "逃げ": return "1nige"
    if r1 == 2 and km == "差し": return "sashi"

    b3_fail = 3 not in top3
    b4_fail = 4 not in top3
    if b3_fail and b4_fail: return "34fail"
    elif b3_fail:            return "3fail"
    elif b4_fail:            return "4fail"

    return "other"


def compute_ev_bets(sc_probs: dict, trifecta_dists: dict, raw_odds: dict) -> list[dict]:
    """notify_prerace と同一ロジックで EV 買い目を再現する"""
    all_tri_prob: dict[str, float] = defaultdict(float)
    for sc in config.SCENARIOS:
        p_sc = sc_probs.get(sc, 0.0)
        if p_sc < 0.005:
            continue
        for tri, p in trifecta_dists.get(sc, {}).items():
            all_tri_prob[tri] += p_sc * p

    rows = []
    for tri, prob in all_tri_prob.items():
        odds_val = raw_odds.get(tri)
        if odds_val is None or odds_val <= 0:
            continue
        ev = prob * odds_val - 1.0
        if ev < config.EV_THRESH:
            continue
        rows.append({"trifecta": tri, "prob": round(prob, 5),
                     "odds": odds_val, "ev": round(ev, 4)})

    rows.sort(key=lambda x: x["ev"], reverse=True)
    return rows[:config.MAX_BETS_PER_RACE]


def run(hd: str, verbose: bool = True) -> dict:
    today_path   = config.today_json(hd)
    prerace_path = os.path.join(config.DATA_DIR, f"scenario_prerace_{hd}.json")
    results_path = config.results_json(hd)
    log_path     = config.ACCURACY_LOG

    if not os.path.exists(today_path):
        print(f"[SKIP] 朝バッチなし: {today_path}")
        return {}

    with open(today_path, encoding="utf-8") as f:
        today = json.load(f)

    prerace: dict = {}
    if os.path.exists(prerace_path):
        with open(prerace_path, encoding="utf-8") as f:
            prerace = json.load(f)

    with open(config.STATS_JSON, encoding="utf-8") as f:
        stats = json.load(f)
    trifecta_dists = {sc: d["trifecta_dist"] for sc, d in stats["scenarios"].items()}

    preds = today.get("predictions", [])
    print(f"結果収集: {hd}  全{len(preds)}レース")

    ev_bets_total = 0
    ev_hits_total = 0
    invest_total  = 0
    return_total  = 0
    sc_correct    = 0
    sc_total      = 0
    race_records  = []

    for p in preds:
        jcd   = p["jcd"]
        rno   = p["race_no"]
        venue = p["venue_name"]
        ck    = f"{jcd}_{rno}"

        time.sleep(SCRAPE_INTERVAL)
        try:
            res = scrape_race_result(jcd, hd, rno)
        except Exception as e:
            if verbose:
                print(f"  [ERR] {venue} {rno}R: {e}")
            continue

        if not res or len(res.get("results", [])) < 3:
            if verbose:
                print(f"  [SKIP] {venue} {rno}R: 結果未取得")
            continue

        r1  = res["results"][0]["boat"]
        r2  = res["results"][1]["boat"]
        r3  = res["results"][2]["boat"]
        km  = res.get("kimarite", "")
        pay = res.get("payouts", {}).get("3連単", 0)
        actual_tri = f"{r1}-{r2}-{r3}"

        actual_sc = classify_actual(res)
        top_sc    = p.get("top_scenario", "")
        sc_total += 1
        sc_match  = (actual_sc == top_sc)
        if sc_match:
            sc_correct += 1

        # EV買い目の再現と照合
        raw_odds = prerace.get(ck, {}).get("odds", {})
        ev_records = []
        if raw_odds:
            sc_probs = p.get("scenario_probs", {})
            bets = compute_ev_bets(sc_probs, trifecta_dists, raw_odds)
            for bet in bets:
                hit     = (bet["trifecta"] == actual_tri)
                ret_yen = pay if (hit and pay > 0) else 0
                ev_records.append({
                    "trifecta": bet["trifecta"],
                    "ev":       bet["ev"],
                    "odds":     bet["odds"],
                    "hit":      hit,
                    "payout":   ret_yen,
                })
                ev_bets_total += 1
                invest_total  += BET_UNIT
                return_total  += ret_yen
                if hit:
                    ev_hits_total += 1

        if verbose:
            sc_mark  = "[SC✓]" if sc_match else "[SC✗]"
            hit_mark = " [HIT]" if any(b["hit"] for b in ev_records) else ""
            print(f"  {sc_mark}{hit_mark} {venue} {rno}R: "
                  f"予測={top_sc} 実際={actual_sc} "
                  f"→ {actual_tri}({km}) 払:{pay:,}円 "
                  f"EV買い目:{len(ev_records)}点")

        race_records.append({
            "jcd":              jcd,
            "venue_name":       venue,
            "race_no":          rno,
            "race_time":        p.get("race_time", ""),
            "top_scenario":     top_sc,
            "top_prob":         p.get("top_prob", 0),
            "actual_scenario":  actual_sc,
            "sc_match":         sc_match,
            "actual_tri":       actual_tri,
            "kimarite":         km,
            "payout":           pay,
            "ev_bets":          ev_records,
        })

    ev_roi = (return_total / invest_total - 1.0) if invest_total > 0 else None
    sc_acc = (sc_correct / sc_total)              if sc_total > 0     else None

    summary = {
        "n_races":    len(race_records),
        "sc_total":   sc_total,
        "sc_correct": sc_correct,
        "sc_acc":     round(sc_acc, 4) if sc_acc is not None else None,
        "ev_bets":    ev_bets_total,
        "ev_hits":    ev_hits_total,
        "invest_yen": invest_total,
        "return_yen": return_total,
        "ev_roi":     round(ev_roi, 4) if ev_roi is not None else None,
    }

    out = {"date": hd, "summary": summary, "races": race_records}
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    # accuracy_log 更新
    log: dict = {}
    if os.path.exists(log_path):
        try:
            with open(log_path, encoding="utf-8") as f:
                log = json.load(f)
        except Exception:
            pass
    log[hd] = summary
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)

    print(f"\n=== {hd} ScenarioBoat サマリー ===")
    if sc_acc is not None:
        print(f"  シナリオ精度: {sc_correct}/{sc_total} ({sc_acc*100:.1f}%)")
    if invest_total > 0:
        print(f"  EV買い目: {ev_bets_total}点  的中: {ev_hits_total}点")
        print(f"  投資: {invest_total:,}円  回収: {return_total:,}円  ROI: {ev_roi*100:+.1f}%")
    else:
        print("  EV買い目なし（発走前通知未実施 or EV<閾値）")
    print(f"  保存: {results_path}")

    _send_line_summary(hd, summary, race_records, log)
    return summary


def _send_line_summary(hd: str, summary: dict, races: list, log: dict) -> None:
    try:
        from line_bot import send_line_message as _snd
    except Exception:
        return

    past = [v for d, v in log.items() if d != hd and v.get("sc_total", 0) > 0]
    cum_sc_ok   = sum(v.get("sc_correct", 0) for v in past)
    cum_sc_all  = sum(v.get("sc_total",   0) for v in past)
    cum_invest  = sum(v.get("invest_yen", 0) for v in past)
    cum_return  = sum(v.get("return_yen", 0) for v in past)
    cum_hits    = sum(v.get("ev_hits",    0) for v in past)
    cum_bets    = sum(v.get("ev_bets",    0) for v in past)

    sc_acc  = summary.get("sc_acc")
    ev_roi  = summary.get("ev_roi")
    acc_str = f"{sc_acc*100:.1f}%" if sc_acc is not None else "---"
    roi_str = f"{ev_roi*100:+.1f}%" if ev_roi is not None else "---"

    lines = [
        f"[ScenarioBoat] {hd} 日次レポート",
        f"シナリオ精度: {summary['sc_correct']}/{summary['sc_total']} ({acc_str})",
        f"EV買い目: {summary['ev_bets']}点  的中: {summary['ev_hits']}点",
        f"投資: {summary['invest_yen']:,}円  回収: {summary['return_yen']:,}円",
        f"本日ROI: {roi_str}",
    ]

    hit_lines = [
        f"  {r['venue_name']} {r['race_no']}R  {b['trifecta']} → {b['payout']:,}円"
        for r in races for b in r.get("ev_bets", []) if b.get("hit")
    ]
    if hit_lines:
        lines += ["", "的中:"] + hit_lines

    if cum_sc_all > 0:
        cum_acc = cum_sc_ok / cum_sc_all
        cum_roi = (cum_return / cum_invest - 1.0) * 100 if cum_invest > 0 else None
        roi_s   = f"{cum_roi:+.1f}%" if cum_roi is not None else "---"
        lines += [
            "",
            f"累積({len(past)}日) SC精度{cum_acc*100:.1f}%  "
            f"投資{cum_invest:,}円 回収{cum_return:,}円 ROI{roi_s}  "
            f"EV的中{cum_hits}/{cum_bets}点"
        ]

    try:
        _snd("\n".join(lines))
        print("[LINE] 日次サマリー送信")
    except Exception as e:
        print(f"[LINE] スキップ: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date",  default=None)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    hd = args.date or date.today().strftime("%Y%m%d")
    print(f"\n=== ScenarioBoat 結果収集  {hd} ===")
    run(hd, verbose=not args.quiet)
