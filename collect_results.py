"""
荒れ特化 結果収集・ROI追跡スクリプト

設計方針:
  - arare_prob >= ARARE_THRESH のレースのみ対象
  - P(combo) × live_odds_multiplier によるEV計算で買い目を選別
  - EV >= EV_THRESH かつ高EV順に最大 MAX_BETS 点に絞る
  - 実際の払戻金（3連単払戻）でROI計算
  - 累積成績を accuracy_log.json に保存、LINE日次サマリーを送信

P(combo) の計算式:
  P(r1,r2,r3) = arare_prob × P(r1 | arare) × Σ_km[P(km|arare) × P(r2-r3 | r1,km)]
  arare_stats.json の winning_boat_dist / kimarite_dist / trifecta_by_winner_km を使用

EV:
  EV = P(r1,r2,r3) × odds_multiplier - 1
  odds_multiplier は scrape_odds_3t が返す値（例: 50.4 → 5040円/100円賭け）

ROI:
  実際の払戻はレース後の scrape_race_result → payouts["3連単"]（円/100円賭け）
  ROI = (Σ払戻円) / (Σ賭け金円) - 1

実行:
  python collect_results.py               # 今日
  python collect_results.py --date 20260501
  python collect_results.py --arare-thresh 0.60 --ev-thresh 0.3 --max-bets 3
  python collect_results.py --quiet
"""

import argparse
import json
import os
import sys
import time
from datetime import date

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

BOATS            = list(range(1, 7))
KIMARITE         = ["逃げ", "差し", "まくり", "まくり差し", "抜き", "恵まれ"]
BET_UNIT         = 100    # 1点あたり(円)
MAX_BETS_DEFAULT = 3      # 1レースあたり最大購入点数（バックテスト最適値）
ARARE_THRESH_DEF = 0.60   # arare_prob 閾値（これ以上のレースのみ対象）
EV_THRESH_DEF    = 0.3    # EV閾値（30%期待利益以上のみ購入）
SCRAPE_INTERVAL  = 1.5    # スクレイプ間隔(秒)

# 荒れ確率帯定義  (下限, 上限(exclusive), ラベル, dictキー)
ARARE_TIERS = [
    (0.55, 0.65, "55-65%", "t55_65"),
    (0.65, 0.75, "65-75%", "t65_75"),
    (0.75, 1.01, "75%+",   "t75up"),
]


def _empty_tier() -> dict:
    return {"races": 0, "arare_actual": 0,
            "ev_bets": 0, "ev_hits": 0, "invest_yen": 0, "return_yen": 0}


def _tier_key(arare_prob: float) -> str | None:
    for lo, hi, _, key in ARARE_TIERS:
        if lo <= arare_prob < hi:
            return key
    return None

ARARE_STATS_PATH = os.path.join(config.DATA_DIR, "arare_stats.json")


# ────────────────────────────────────────────
#  arare_stats ローダー
# ────────────────────────────────────────────

def _load_arare_stats() -> dict:
    if not os.path.exists(ARARE_STATS_PATH):
        return {}
    with open(ARARE_STATS_PATH, encoding="utf-8") as f:
        return json.load(f)


def _get_venue_stats(arare_data: dict, venue_name: str) -> dict:
    """会場別統計を優先、なければグローバル統計。"""
    by_venue = arare_data.get("by_venue", {})
    venue_s  = by_venue.get(venue_name, {})
    if venue_s and venue_s.get("n", 0) >= 30:
        return venue_s
    return arare_data.get("global", {})


# ────────────────────────────────────────────
#  P(combo) 計算
# ────────────────────────────────────────────

def _compute_combo_probs(arare_prob: float, stats: dict) -> dict[tuple, float]:
    """
    全 5×4×3=60 通りの荒れ系3連単確率を返す。
    {(r1,r2,r3): probability}
    """
    win_dist = stats.get("winning_boat_dist", {})
    km_dist  = stats.get("kimarite_dist", {})
    tri      = stats.get("trifecta_by_winner_km", {})

    if not win_dist or not km_dist:
        return {}

    result: dict[tuple, float] = {}

    for r1 in BOATS:
        p_r1_arare = win_dist.get(str(r1), 0.0)
        if p_r1_arare <= 0:
            continue

        others = [b for b in BOATS if b != r1]
        all_r2r3 = [(r2, r3) for r2 in others for r3 in others if r3 != r2]
        cond: dict[tuple, float] = {pair: 0.0 for pair in all_r2r3}

        for km, km_prob in km_dist.items():
            if km_prob <= 0:
                continue
            key      = f"{r1}_{km}"
            patterns = tri.get(key, [])

            known: dict[tuple, float] = {}
            known_total = 0.0
            for pat in patterns:
                try:
                    r2s, r3s = pat["r2_r3"].split("-")
                    pair = (int(r2s), int(r3s))
                    if pair in cond:
                        known[pair]  = pat["pct"]
                        known_total += pat["pct"]
                except (ValueError, KeyError):
                    continue

            unknowns = [p for p in all_r2r3 if p not in known]
            unif     = max(0.0, 1.0 - known_total) / len(unknowns) if unknowns else 0.0

            for pair in all_r2r3:
                cond[pair] += km_prob * known.get(pair, unif)

        for (r2, r3), p_r2r3 in cond.items():
            result[(r1, r2, r3)] = arare_prob * p_r1_arare * p_r2r3

    return result


# ────────────────────────────────────────────
#  オッズ変換ユーティリティ
# ────────────────────────────────────────────

def _parse_prerace_odds(raw: dict) -> dict[tuple, float]:
    """prerace_json の {"1-2-3": 50.4} → {(1,2,3): 50.4}"""
    result: dict[tuple, float] = {}
    for k, v in raw.items():
        try:
            parts = k.split("-")
            result[(int(parts[0]), int(parts[1]), int(parts[2]))] = float(v)
        except Exception:
            pass
    return result


# ────────────────────────────────────────────
#  買い目選択
# ────────────────────────────────────────────

def _select_bets(
    combo_probs: dict[tuple, float],
    odds_3t: dict[tuple, float],
    ev_thresh: float,
    max_bets: int,
) -> list[dict]:
    """
    EV >= ev_thresh の買い目を EV降順で最大 max_bets 点返す。
    EV = P(combo) × odds_multiplier - 1

    戻り値:
      [{"combo": (r1,r2,r3), "combo_str": "2-1-3", "prob": ..., "odds": ..., "ev": ...}, ...]
    """
    candidates = []
    for combo, prob in combo_probs.items():
        if prob <= 0:
            continue
        odds = odds_3t.get(combo)
        if odds is None or odds <= 0:
            continue
        ev = prob * odds - 1.0
        if ev < ev_thresh:
            continue
        r1, r2, r3 = combo
        candidates.append({
            "combo":     combo,
            "combo_str": f"{r1}-{r2}-{r3}",
            "prob":      round(prob, 6),
            "odds":      round(odds, 1),
            "ev":        round(ev, 4),
        })

    candidates.sort(key=lambda x: x["ev"], reverse=True)
    return candidates[:max_bets]


# ────────────────────────────────────────────
#  メイン処理
# ────────────────────────────────────────────

def run(
    hd: str,
    arare_thresh: float = ARARE_THRESH_DEF,
    ev_thresh: float    = EV_THRESH_DEF,
    max_bets: int       = MAX_BETS_DEFAULT,
    verbose: bool       = True,
) -> dict:
    batch_path   = os.path.join(config.DATA_DIR, f"today_{hd}.json")
    prerace_path = os.path.join(config.DATA_DIR, f"prerace_{hd}.json")
    results_path = os.path.join(config.DATA_DIR, f"results_{hd}.json")
    log_path     = os.path.join(config.DATA_DIR, "accuracy_log.json")

    if not os.path.exists(batch_path):
        print(f"[SKIP] 朝バッチなし: {batch_path}")
        return {}

    with open(batch_path, encoding="utf-8") as f:
        batch = json.load(f)

    prerace: dict = {}
    if os.path.exists(prerace_path):
        try:
            with open(prerace_path, encoding="utf-8") as f:
                prerace = json.load(f)
        except Exception:
            pass

    arare_data = _load_arare_stats()
    if not arare_data:
        print("[WARN] arare_stats.json なし。python build_arare_stats.py を先に実行してください。")

    preds = batch.get("predictions", [])
    print(f"結果収集: {hd}  全{len(preds)}レース  arare閾値={arare_thresh}  EV閾値={ev_thresh}")

    # カウンタ
    n_arare_candidate = 0   # arare_prob >= thresh
    n_bets_ev         = 0   # EV合格買い目数
    total_bet_yen     = 0
    total_return_yen  = 0
    n_hits            = 0

    # naive(EV filter なし、荒れ上位3点固定) の比較用カウンタ
    n_naive_bets   = 0
    naive_bet_yen  = 0
    naive_ret_yen  = 0
    naive_hits     = 0

    # 荒れ確率帯別カウンタ
    tier_stats: dict[str, dict] = {key: _empty_tier() for _, _, _, key in ARARE_TIERS}

    race_records = []

    for p in preds:
        jcd        = p["jcd"]
        rno        = p["race_no"]
        venue_name = p["venue_name"]
        arare_prob = float(p.get("arare_prob", 0.0))
        ck         = f"{jcd}_{rno}"

        # ── 荒れ候補フィルタ ──
        if arare_prob < arare_thresh:
            continue
        n_arare_candidate += 1

        # ── 実際の結果を取得 ──
        time.sleep(SCRAPE_INTERVAL)
        try:
            res = scrape_race_result(jcd, hd, rno)
        except Exception as e:
            if verbose:
                print(f"  [ERR] {venue_name} {rno}R: {e}")
            continue

        if not res or not res.get("results") or len(res["results"]) < 3:
            if verbose:
                print(f"  [SKIP] {venue_name} {rno}R: 結果未取得（未発走?）")
            continue

        actual_r1    = res["results"][0]["boat"]
        actual_r2    = res["results"][1]["boat"]
        actual_r3    = res["results"][2]["boat"]
        actual_tri   = (actual_r1, actual_r2, actual_r3)
        actual_str   = f"{actual_r1}-{actual_r2}-{actual_r3}"
        kimarite     = res.get("kimarite", "")
        payout_yen   = res.get("payouts", {}).get("3連単", 0)  # 円/100円賭け

        # ── オッズ取得（レース前の倍率）──
        odds_3t: dict[tuple, float] = {}

        pr_entry = prerace.get(ck, {})
        raw_odds = pr_entry.get("odds", {})
        if raw_odds:
            odds_3t = _parse_prerace_odds(raw_odds)

        if not odds_3t:
            # prerace になければ live スクレイプ（レース後は取れない場合あり）
            try:
                time.sleep(SCRAPE_INTERVAL)
                live = scrape_odds_3t(jcd, hd, rno)
                if live:
                    odds_3t = live
                    if verbose:
                        print(f"    [{venue_name} {rno}R] オッズ live 取得: {len(odds_3t)}通り")
            except Exception:
                pass

        # ── P(combo) 計算 ──
        stats       = _get_venue_stats(arare_data, venue_name) if arare_data else {}
        combo_probs = _compute_combo_probs(arare_prob, stats) if stats else {}

        # ── EV フィルタ済み買い目 ──
        ev_bets: list[dict] = []
        if combo_probs and odds_3t:
            ev_bets = _select_bets(combo_probs, odds_3t, ev_thresh, max_bets)

        # ── 結果照合（EV買い目） ──
        ev_records = []
        for bet in ev_bets:
            hit     = (bet["combo"] == actual_tri)
            ret_yen = payout_yen if (hit and payout_yen > 0) else 0
            ev_records.append({
                "combo":   bet["combo_str"],
                "prob":    bet["prob"],
                "odds":    bet["odds"],       # 事前倍率
                "ev":      bet["ev"],
                "hit":     hit,
                "payout":  ret_yen,           # 実際の払戻(円/100円賭け)
            })
            n_bets_ev       += 1
            total_bet_yen   += BET_UNIT
            total_return_yen += ret_yen
            if hit:
                n_hits += 1

        # ── 荒れ確率帯別集計 ──
        tk = _tier_key(arare_prob)
        if tk and tk in tier_stats:
            t = tier_stats[tk]
            t["races"]       += 1
            t["arare_actual"] += (1 if actual_r1 != 1 else 0)
            t["ev_bets"]     += len(ev_records)
            t["ev_hits"]     += sum(1 for b in ev_records if b["hit"])
            t["invest_yen"]  += len(ev_records) * BET_UNIT
            t["return_yen"]  += sum(b["payout"] for b in ev_records)

        # ── naive 比較用（EV filter なし、確率スコア上位3点） ──
        naive_bets_list: list[tuple] = []
        if combo_probs:
            sorted_probs = sorted(combo_probs.items(), key=lambda x: x[1], reverse=True)
            naive_bets_list = [c for c, _ in sorted_probs[:max_bets]]
        naive_hit = (actual_tri in naive_bets_list)
        n_naive_bets   += len(naive_bets_list)
        naive_bet_yen  += len(naive_bets_list) * BET_UNIT
        naive_ret_yen  += (payout_yen if naive_hit and payout_yen > 0 else 0)
        if naive_hit:
            naive_hits += 1

        # ── ログ出力 ──
        if verbose:
            ev_count  = len([b for b in ev_records if b["ev"] >= ev_thresh])
            hit_mark  = "[HIT]" if any(b["hit"] for b in ev_records) else (
                        "[MISS]" if ev_records else "[NO_ODDS]")
            ev_top    = f"  EV={ev_records[0]['ev']:+.2f}({ev_records[0]['combo']})" if ev_records else ""
            print(
                f"  {hit_mark} {venue_name} {rno}R "
                f"arare={arare_prob:.2f} "
                f"→ 実際:{actual_str}({kimarite}) "
                f"払戻:{payout_yen:,}円 "
                f"EV買い目:{ev_count}点{ev_top}"
            )

        race_records.append({
            "jcd":        jcd,
            "venue_name": venue_name,
            "race_no":    rno,
            "race_time":  p.get("race_time", ""),
            "arare_prob": arare_prob,
            "actual_1st": actual_r1,
            "actual_2nd": actual_r2,
            "actual_3rd": actual_r3,
            "trifecta":   actual_str,
            "kimarite":   kimarite,
            "payout_yen": payout_yen,
            "ev_bets":    ev_records,
            "had_odds":   bool(odds_3t),
        })

    # ────────────────────────────────────────
    #  ROI 集計
    # ────────────────────────────────────────
    ev_roi   = (total_return_yen / total_bet_yen   - 1.0) if total_bet_yen   > 0 else None
    naive_roi = (naive_ret_yen   / naive_bet_yen   - 1.0) if naive_bet_yen   > 0 else None

    summary = {
        "n_races_total":      len(preds),
        "n_arare_candidate":  n_arare_candidate,
        "arare_thresh":       arare_thresh,
        "ev_thresh":          ev_thresh,
        "max_bets":           max_bets,
        # EV フィルタ済み
        "ev_bets":            n_bets_ev,
        "ev_hits":            n_hits,
        "ev_invest_yen":      total_bet_yen,
        "ev_return_yen":      total_return_yen,
        "ev_roi":             round(ev_roi,    4) if ev_roi    is not None else None,
        "ev_roi_pct":         round(ev_roi * 100, 1) if ev_roi is not None else None,
        # naive 比較（EV filter なし）
        "naive_bets":         n_naive_bets,
        "naive_hits":         naive_hits,
        "naive_invest_yen":   naive_bet_yen,
        "naive_return_yen":   naive_ret_yen,
        "naive_roi":          round(naive_roi,    4) if naive_roi is not None else None,
        "naive_roi_pct":      round(naive_roi * 100, 1) if naive_roi is not None else None,
        # 荒れ確率帯別集計
        "tier_stats": {
            key: {
                **t,
                "arare_rate": round(t["arare_actual"] / t["races"], 3) if t["races"] > 0 else None,
                "ev_roi":     round(t["return_yen"] / t["invest_yen"] - 1.0, 3)
                              if t["invest_yen"] > 0 else None,
            }
            for key, t in tier_stats.items()
        },
    }

    out = {
        "date":    hd,
        "summary": summary,
        "races":   race_records,
    }

    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    # ── accuracy_log.json 追記 ──
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

    # ── 端末サマリー ──
    print(f"\n=== {hd} 荒れ特化サマリー ===")
    print(f"  荒れ候補R (arare>={arare_thresh}): {n_arare_candidate} / {len(preds)}")
    if n_bets_ev > 0:
        print(f"  EV>{ev_thresh} 買い目: {n_bets_ev}点  的中: {n_hits}点  "
              f"投資: {total_bet_yen:,}円  回収: {total_return_yen:,}円  "
              f"ROI: {ev_roi*100:+.1f}%")
    else:
        print(f"  EV買い目なし（オッズ未取得 or EV<{ev_thresh}）")
    if naive_roi is not None:
        print(f"  [比較] naive(EV filterなし): {n_naive_bets}点  ROI: {naive_roi*100:+.1f}%")
    print(f"  保存: {results_path}")

    # ── LINE 日次サマリー ──
    _send_line_summary(hd, summary, race_records, log)

    return summary


# ────────────────────────────────────────────
#  LINE 日次サマリー送信
# ────────────────────────────────────────────

def _send_line_summary(hd: str, summary: dict, races: list[dict], log: dict) -> None:
    try:
        from line_bot import send_line_message as _snd
    except Exception:
        return

    # 累積成績（当日を除く過去全日）
    past = [v for d, v in log.items()
            if d != hd and v.get("ev_bets", 0) > 0]
    cum_invest  = sum(v.get("ev_invest_yen", 0) for v in past)
    cum_return  = sum(v.get("ev_return_yen", 0) for v in past)
    cum_hits    = sum(v.get("ev_hits", 0) for v in past)
    cum_bets    = sum(v.get("ev_bets", 0) for v in past)
    cum_roi     = (cum_return / cum_invest - 1.0) * 100 if cum_invest > 0 else None

    # 当日の的中レースをピックアップ
    hit_lines = []
    for r in races:
        for b in r.get("ev_bets", []):
            if b.get("hit"):
                hit_lines.append(
                    f"  {r['venue_name']} {r['race_no']}R  "
                    f"{b['combo']} → {b['payout']:,}円"
                )

    ev_roi_pct = summary.get("ev_roi_pct")
    roi_str    = f"{ev_roi_pct:+.1f}%" if ev_roi_pct is not None else "---"

    lines = [
        f"[荒れ特化] {hd} 日次レポート",
        f"荒れ候補: {summary['n_arare_candidate']}R / {summary['n_races_total']}R",
        f"EV買い目: {summary['ev_bets']}点  的中: {summary['ev_hits']}点",
        f"投資: {summary['ev_invest_yen']:,}円  回収: {summary['ev_return_yen']:,}円",
        f"本日ROI: {roi_str}",
        "",
    ]

    if hit_lines:
        lines.append("的中:")
        lines.extend(hit_lines)
        lines.append("")

    if cum_invest > 0:
        lines.append(
            f"累積({len(past)}日) "
            f"投資{cum_invest:,}円 回収{cum_return:,}円 "
            f"ROI{cum_roi:+.1f}% "
            f"的中{cum_hits}/{cum_bets}点"
        )

    # ── 荒れ確率帯別サマリー（累積で積み上げる） ─────────────────────────────
    all_days = [v for _, v in sorted(log.items()) if v.get("tier_stats")]
    if all_days:
        # 全日の tier_stats を合算
        cum_tier: dict[str, dict] = {key: _empty_tier() for _, _, _, key in ARARE_TIERS}
        for day_v in all_days:
            for key, t in day_v.get("tier_stats", {}).items():
                if key in cum_tier:
                    for field in ("races", "arare_actual", "ev_bets", "ev_hits",
                                  "invest_yen", "return_yen"):
                        cum_tier[key][field] += t.get(field, 0)

        lines.append("")
        lines.append("【荒れ確率帯別 累積実績】")
        for lo, hi, label, key in ARARE_TIERS:
            t = cum_tier[key]
            if t["races"] == 0:
                continue
            arare_r  = t["arare_actual"] / t["races"]
            ev_roi_v = (t["return_yen"] / t["invest_yen"] - 1.0) * 100 if t["invest_yen"] > 0 else None
            roi_s    = f"{ev_roi_v:+.1f}%" if ev_roi_v is not None else "---"
            lines.append(
                f"  {label}: {t['races']}R "
                f"荒れ実現{arare_r*100:.0f}%  "
                f"ROI{roi_s}"
            )

    try:
        _snd("\n".join(lines))
        print("[LINE] 日次サマリー送信")
    except Exception as e:
        print(f"[LINE] スキップ: {e}")


# ────────────────────────────────────────────
#  累積成績表示ユーティリティ
# ────────────────────────────────────────────

def show_cumulative(log_path: str | None = None) -> None:
    """accuracy_log.json の累積ROIを表示する。"""
    path = log_path or os.path.join(config.DATA_DIR, "accuracy_log.json")
    if not os.path.exists(path):
        print("accuracy_log.json が見つかりません。")
        return

    with open(path, encoding="utf-8") as f:
        log = json.load(f)

    print("\n=== 累積成績 ===")
    print(f"  {'日付':>10}  {'EV買い目':>8}  {'的中':>4}  {'投資(円)':>10}  {'回収(円)':>10}  {'ROI':>8}")
    print("  " + "-" * 62)

    cum_invest = 0
    cum_return = 0
    cum_bets   = 0
    cum_hits   = 0

    for d in sorted(log.keys()):
        v = log[d]
        invest = v.get("ev_invest_yen", 0)
        ret    = v.get("ev_return_yen", 0)
        bets   = v.get("ev_bets", 0)
        hits   = v.get("ev_hits", 0)
        if bets == 0:
            continue
        roi = (ret / invest - 1.0) * 100 if invest > 0 else None
        roi_s = f"{roi:+.1f}%" if roi is not None else "---"
        print(f"  {d:>10}  {bets:>8}  {hits:>4}  {invest:>10,}  {ret:>10,}  {roi_s:>8}")
        cum_invest += invest
        cum_return += ret
        cum_bets   += bets
        cum_hits   += hits

    if cum_invest > 0:
        cum_roi = (cum_return / cum_invest - 1.0) * 100
        print("  " + "-" * 62)
        print(f"  {'累計':>10}  {cum_bets:>8}  {cum_hits:>4}  "
              f"{cum_invest:>10,}  {cum_return:>10,}  {cum_roi:>+7.1f}%")

    # ── 荒れ確率帯別累積 ──────────────────────────────────────────────────────
    cum_tier: dict[str, dict] = {key: _empty_tier() for _, _, _, key in ARARE_TIERS}
    for d, v in log.items():
        for key, t in v.get("tier_stats", {}).items():
            if key in cum_tier:
                for field in ("races", "arare_actual", "ev_bets", "ev_hits",
                              "invest_yen", "return_yen"):
                    cum_tier[key][field] += t.get(field, 0)

    if any(t["races"] > 0 for t in cum_tier.values()):
        print(f"\n{'─'*62}")
        print(f"  {'確率帯':>10}  {'R数':>5}  {'荒れ実現率':>10}  {'EV買い目':>8}  {'的中':>4}  {'ROI':>8}")
        print(f"  {'─'*60}")
        for lo, hi, label, key in ARARE_TIERS:
            t = cum_tier[key]
            if t["races"] == 0:
                continue
            arare_r = t["arare_actual"] / t["races"]
            roi_v   = (t["return_yen"] / t["invest_yen"] - 1.0) * 100 if t["invest_yen"] > 0 else None
            roi_s   = f"{roi_v:+.1f}%" if roi_v is not None else "---"
            print(f"  {label:>10}  {t['races']:>5}  {arare_r*100:>9.1f}%  "
                  f"{t['ev_bets']:>8}  {t['ev_hits']:>4}  {roi_s:>8}")


# ────────────────────────────────────────────
#  エントリポイント
# ────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="荒れ特化 結果収集・ROI追跡")
    parser.add_argument("--date",         default=None,              help="日付 YYYYMMDD (default: 今日)")
    parser.add_argument("--arare-thresh", type=float, default=ARARE_THRESH_DEF, help=f"荒れ閾値 (default: {ARARE_THRESH_DEF})")
    parser.add_argument("--ev-thresh",    type=float, default=EV_THRESH_DEF,    help=f"EV閾値 (default: {EV_THRESH_DEF})")
    parser.add_argument("--max-bets",     type=int,   default=MAX_BETS_DEFAULT, help=f"最大購入点数 (default: {MAX_BETS_DEFAULT})")
    parser.add_argument("--quiet",        action="store_true", help="詳細ログを抑制")
    parser.add_argument("--summary",      action="store_true", help="累積成績を表示して終了")
    args = parser.parse_args()

    if args.summary:
        show_cumulative()
        sys.exit(0)

    hd = args.date or date.today().strftime("%Y%m%d")
    print(f"\n=== 荒れ特化 結果収集  {hd} ===")
    run(
        hd,
        arare_thresh = args.arare_thresh,
        ev_thresh    = args.ev_thresh,
        max_bets     = args.max_bets,
        verbose      = not args.quiet,
    )
