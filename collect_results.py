"""
レース結果収集スクリプト
today_{hd}.json の予測と実際の結果を照合してROIを計算。
data/results_{hd}.json に保存、data/accuracy_log.json に累積追記。

使用:
  python collect_results.py              # 今日
  python collect_results.py --date 20260501
  python collect_results.py --ev-thresh 0.3  # EV閾値変更
"""
import argparse
import json
import os
import sys
import time
from datetime import date
from itertools import permutations

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path = [_HERE] + [p for p in sys.path if p != _HERE]

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import importlib.util as _ilu
import config

_spec = _ilu.spec_from_file_location("scraper", os.path.join(_HERE, "scraper.py"))
_mod  = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
scrape_race_result = _mod.scrape_race_result
scrape_odds_3t     = _mod.scrape_odds_3t

KIMARITE = ["逃げ", "差し", "まくり", "まくり差し", "抜き", "恵まれ"]
BOATS    = list(range(1, 7))
EV_THRESH_DEFAULT = 0.5


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
                "combo": f"{r1}-{r2}-{r3}",
                "p_combo": p_combo,
                "odds": ov,
                "ev": p_combo * ov - 1.0,
            })
    return sorted(rows, key=lambda x: x["ev"], reverse=True)


def _parse_odds_dict(raw: dict) -> dict:
    """prerace_json の {"1-2-3": 18.9} → {(1,2,3): 18.9}"""
    result = {}
    for k, v in raw.items():
        try:
            parts = k.split("-")
            result[(int(parts[0]), int(parts[1]), int(parts[2]))] = float(v)
        except Exception:
            pass
    return result


def run(hd: str, ev_thresh: float = EV_THRESH_DEFAULT, verbose: bool = True) -> dict:
    batch_path   = os.path.join(config.DATA_DIR, f"today_{hd}.json")
    prerace_path = os.path.join(config.DATA_DIR, f"prerace_{hd}.json")
    results_path = os.path.join(config.DATA_DIR, f"results_{hd}.json")
    meta_path    = os.path.join(config.DATA_DIR, "model_meta.json")
    log_path     = os.path.join(config.DATA_DIR, "accuracy_log.json")

    if not os.path.exists(batch_path):
        print(f"[SKIP] 朝バッチなし: {batch_path}")
        return {}

    with open(batch_path, encoding="utf-8") as f:
        batch = json.load(f)

    meta: dict = {}
    if os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)

    prerace: dict = {}
    if os.path.exists(prerace_path):
        try:
            with open(prerace_path, encoding="utf-8") as f:
                prerace = json.load(f)
        except Exception:
            pass

    preds = batch.get("predictions", [])
    race_records = []
    ev_total_bets = 0
    ev_total_hits = 0
    ev_total_return = 0.0

    print(f"結果収集: {hd}  対象{len(preds)}レース")

    for p in preds:
        jcd = p["jcd"]
        rno = p["race_no"]
        ck  = f"{jcd}_{rno}"

        time.sleep(config.REQUEST_DELAY)
        try:
            result = scrape_race_result(jcd, hd, rno)
        except Exception as e:
            if verbose:
                print(f"  [ERR] {p['venue_name']} {rno}R: {e}")
            continue

        if not result or not result.get("results") or len(result["results"]) < 3:
            if verbose:
                print(f"  [SKIP] {p['venue_name']} {rno}R: 結果なし（未発走?）")
            continue

        r1 = result["results"][0]["boat"]
        r2 = result["results"][1]["boat"]
        r3 = result["results"][2]["boat"]
        trifecta = f"{r1}-{r2}-{r3}"
        kimarite  = result.get("kimarite", "")

        pred_boat = p["top_boat"]
        boat_prob = {int(k): v for k, v in p["boat_prob"].items()}

        hit_win  = (r1 == pred_boat)
        hit_top3 = (pred_boat in [r1, r2, r3])

        # EV買い目の照合
        ev_bets = []
        pr_entry = prerace.get(ck, {})
        raw_odds  = pr_entry.get("odds", {})
        # prerace_json にオッズがなければ live 取得（ブラウザ未起動時のフォールバック）
        if not raw_odds and meta:
            try:
                live = scrape_odds_3t(jcd, hd, rno)
                if live:
                    raw_odds = {f"{k[0]}-{k[1]}-{k[2]}": v for k, v in live.items()}
                    if verbose:
                        print(f"    オッズ live 取得: {len(raw_odds)}通り")
            except Exception:
                pass
        if raw_odds and meta:
            odds_3t    = _parse_odds_dict(raw_odds)
            # 展示ST補正済み確率があれば使う
            if "boat_prob_adjusted" in pr_entry:
                bp_for_ev = {int(k): v for k, v in pr_entry["boat_prob_adjusted"].items()}
            else:
                bp_for_ev = boat_prob
            ev_rows = _compute_ev(bp_for_ev, odds_3t, meta)
            for row in ev_rows:
                if row["ev"] < ev_thresh:
                    break
                hit     = (row["combo"] == trifecta)
                ret     = row["odds"] if hit else 0.0
                ev_bets.append({
                    "combo": row["combo"],
                    "ev":    round(row["ev"], 4),
                    "odds":  row["odds"],
                    "hit":   hit,
                    "return": ret,
                })
                ev_total_bets   += 1
                ev_total_hits   += int(hit)
                ev_total_return += ret

        race_records.append({
            "jcd":        jcd,
            "venue_name": p["venue_name"],
            "race_no":    rno,
            "race_time":  p.get("race_time", ""),
            "pred_boat":  pred_boat,
            "pred_km":    p.get("top_km", ""),
            "pred_prob":  round(boat_prob.get(pred_boat, 0.0), 4),
            "confidence": p.get("confidence", 0.0),
            "actual_1st": r1,
            "actual_2nd": r2,
            "actual_3rd": r3,
            "trifecta":   trifecta,
            "kimarite":   kimarite,
            "hit_win":    hit_win,
            "hit_top3":   hit_top3,
            "ev_bets":    ev_bets,
        })

        if verbose:
            mark = "✅" if hit_win else ("△" if hit_top3 else "❌")
            ev_info = f"  EV買い目{len(ev_bets)}件" if ev_bets else ""
            print(f"  {mark} {p['venue_name']} {rno}R: 予測{pred_boat}号艇 → 実際{r1}-{r2}-{r3} ({kimarite}){ev_info}")

    # 日次サマリー
    n = len(race_records)
    win_acc  = sum(1 for r in race_records if r["hit_win"]) / n if n else 0.0
    top3_acc = sum(1 for r in race_records if r["hit_top3"]) / n if n else 0.0
    ev_roi   = (ev_total_return / ev_total_bets) - 1.0 if ev_total_bets > 0 else None

    summary = {
        "n_races":        n,
        "win_accuracy":   round(win_acc, 4),
        "top3_accuracy":  round(top3_acc, 4),
        "ev_bets_count":  ev_total_bets,
        "ev_hits":        ev_total_hits,
        "ev_roi":         round(ev_roi, 4) if ev_roi is not None else None,
        "ev_total_return": round(ev_total_return, 2),
    }

    out = {
        "date":      hd,
        "summary":   summary,
        "races":     race_records,
    }

    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n保存: {results_path}")

    # accuracy_log.json に追記
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

    print(f"\n=== {hd} サマリー ===")
    print(f"  レース数: {n}")
    print(f"  1着的中率: {win_acc*100:.1f}%")
    print(f"  3着内的中率: {top3_acc*100:.1f}%")
    if ev_total_bets > 0:
        print(f"  EV≥{ev_thresh} 買い目: {ev_total_bets}件  的中: {ev_total_hits}件")
        print(f"  EV_ROI: {ev_roi*100:+.1f}%  (回収: {ev_total_return:.1f} / {ev_total_bets}賭け)")
    else:
        print(f"  EV買い目なし（オッズ未取得 or EV<{ev_thresh}）")

    # LINE通知（詳細日次まとめ）
    try:
        from line_bot import send_line_message as _snd, format_daily_summary as _fmt
        text = _fmt(hd, race_records, ev_total_bets, ev_total_hits, ev_total_return,
                    ev_thresh, log)
        _snd(text)
        print("[LINE] 日次まとめ送信")
    except Exception as e:
        print(f"[LINE] スキップ: {e}")

    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="レース結果収集・ROI計算")
    parser.add_argument("--date",      default=None,       help="日付 YYYYMMDD (default: 今日)")
    parser.add_argument("--ev-thresh", type=float, default=EV_THRESH_DEFAULT, help="EV閾値 (default: 0.5)")
    parser.add_argument("--quiet",     action="store_true", help="詳細ログを抑制")
    args = parser.parse_args()

    hd = args.date or date.today().strftime("%Y%m%d")
    print(f"\n=== 結果収集  {hd} ===")
    run(hd, ev_thresh=args.ev_thresh, verbose=not args.quiet)
