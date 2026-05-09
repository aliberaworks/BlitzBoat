"""
発走前LINE通知スクリプト（GitHub Actions専用・ステートレス）

発走30〜60分前のレースのオッズを取得してEV計算し、EV≥0.5の買い目をLINEに送る。
GitHub Actions が30分ごとに実行するため、各レースは自然に1回だけ通知される。

使用:
  python notify_prerace.py              # 今日
  python notify_prerace.py --date 20260501
  python notify_prerace.py --min 20 --max 65   # 窓を変更
"""
import argparse
import json
import os
import sys
from datetime import date, datetime, timezone, timedelta
from itertools import permutations

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path = [_HERE] + [p for p in sys.path if p != _HERE]

import importlib.util as _ilu
import config

_spec = _ilu.spec_from_file_location("scraper", os.path.join(_HERE, "scraper.py"))
_mod  = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
scrape_odds_3t     = _mod.scrape_odds_3t
scrape_race_result = _mod.scrape_race_result

try:
    from line_bot import send_line_message
except Exception:
    def send_line_message(*a, **kw): return False

JST      = timezone(timedelta(hours=9))
KIMARITE = ["逃げ", "差し", "まくり", "まくり差し", "抜き", "恵まれ"]
BOATS    = list(range(1, 7))
EV_THRESH            = 0.3   # 荒れ特化後はEV閾値を下げる（荒れレース限定なので）
ARARE_PROB_THRESH    = 0.55  # 荒れ確率がこの値以上のレースのみEV通知する
MAX_DAILY_NOTIFY     = 4     # 1日のLINE送信上限（月200通制限: 4×26日=104 + 朝26 + 集計26 = 156通）


_BOAT_LABEL = ["①白", "②黒", "③赤", "④青", "⑤黄", "⑥緑"]


def _format_consolidated(notify_list: list, now: datetime, ev_thresh: float, sent_today: int) -> str:
    """
    複数レースをまとめた1通のLINEメッセージを生成。
    notify_list: [{"race": p, "ev_rows": [...], "arare_prob": float, "course_changes": list|None}, ...]
    """
    t = now.strftime("%H:%M")
    lines = [f"🌪 荒れEV警報  {t}", ""]

    for n in notify_list:
        p          = n["race"]
        arare_pct  = int(n["arare_prob"] * 100)
        cc         = n.get("course_changes") or []
        ev_rows    = n["ev_rows"]
        top_bets   = [r for r in ev_rows if r["ev"] >= ev_thresh][:3]

        lines.append(f"📍 {p['venue_name']} {p['race_no']}R ⏰{p.get('race_time','--:--')} 荒れ{arare_pct}%")

        if cc:
            for c in cc:
                icon = "⚠前づけ" if c.get("type") == "前づけ" else "↩後づけ"
                lines.append(f"  {icon}: {_BOAT_LABEL[c['boat']-1]}→{c['course']}コース")

        for r in top_bets:
            b1   = _BOAT_LABEL[r["r1"] - 1]
            b2   = _BOAT_LABEL[r["r2"] - 1]
            b3   = _BOAT_LABEL[r["r3"] - 1]
            star = "★" if r["ev"] >= 1.0 else "☆"
            lines.append(f"  {star} {b1}-{b2}-{b3} {r['odds']:.0f}倍 EV{r['ev']:+.2f}")

        total = len([r for r in ev_rows if r["ev"] >= ev_thresh])
        lines.append(f"  (EV≥{ev_thresh}: {total}点)")
        lines.append("")

    lines.append(f"本日{sent_today + 1}回目 / 上限{MAX_DAILY_NOTIFY}回")
    lines.append("※統計確率×市場オッズ−1の参考値")
    return "\n".join(lines)


def _load_arare_stats() -> dict:
    path = os.path.join(config.DATA_DIR, "arare_stats.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _compute_ev_arare(
    boat_prob: dict,
    arare_prob: float,
    odds_3t: dict,
    arare_stats: dict,
    venue_name: str,
) -> list:
    """
    荒れ確率 × 荒れ時出目統計でEVを計算する。

    P(r1,r2,r3) = p_r1 × Σ_km[P(km) × P(r2-r3 | r1,km)]
    p_r1 = arare_prob × win_dist[r1] + (1-arare_prob) × boat_prob[1]  (r1=1のみ)

    collect_results._compute_combo_probs と同ロジック（全決まり手積分、無効エントリ自動除外）。
    """
    venue_stats  = arare_stats.get("by_venue", {}).get(venue_name, {})
    global_stats = arare_stats.get("global", {})
    # 会場別サンプルが30以上あれば会場別、それ以外はグローバル
    stats = venue_stats if venue_stats and venue_stats.get("n", 0) >= 30 else global_stats

    win_dist = stats.get("winning_boat_dist", {})
    km_dist  = stats.get("kimarite_dist", {})
    tri      = stats.get("trifecta_by_winner_km", {})

    rows = []
    for r1 in BOATS:
        p_r1_arare   = float(win_dist.get(str(r1), 0.0))
        p_r1_noarare = float(boat_prob.get(r1, 0.0)) if r1 == 1 else 0.0
        p_r1 = arare_prob * p_r1_arare + (1 - arare_prob) * p_r1_noarare

        if p_r1 < 0.005:
            continue

        others   = [b for b in BOATS if b != r1]
        all_r2r3 = [(r2, r3) for r2 in others for r3 in others if r3 != r2]
        cond: dict = {pair: 0.0 for pair in all_r2r3}

        for km, km_prob in km_dist.items():
            if km_prob <= 0:
                continue
            key      = f"{r1}_{km}"
            patterns = tri.get(key, [])

            known: dict = {}
            known_total = 0.0
            for pat in patterns:
                try:
                    r2s, r3s = pat["r2_r3"].split("-")
                    pair = (int(r2s), int(r3s))
                    if pair in cond:          # 無効エントリ（同艇重複）を自然に除外
                        known[pair]   = pat["pct"]
                        known_total  += pat["pct"]
                except (ValueError, KeyError):
                    continue

            unknowns = [p for p in all_r2r3 if p not in known]
            unif     = max(0.0, 1.0 - known_total) / len(unknowns) if unknowns else 0.0

            for pair in all_r2r3:
                cond[pair] += km_prob * known.get(pair, unif)

        for (r2, r3), p_r2r3 in cond.items():
            p_combo = p_r1 * p_r2r3
            ov = odds_3t.get((r1, r2, r3))
            if ov is None or ov <= 0:
                continue
            rows.append({
                "r1": r1, "r2": r2, "r3": r3,
                "p_r1": p_r1, "p_combo": round(p_combo, 5),
                "odds": ov, "ev": round(p_combo * ov - 1.0, 4),
            })

    return sorted(rows, key=lambda x: x["ev"], reverse=True)


def _parse_odds_dict(raw: dict) -> dict:
    result = {}
    for k, v in raw.items():
        try:
            parts = k.split("-")
            result[(int(parts[0]), int(parts[1]), int(parts[2]))] = float(v)
        except Exception:
            pass
    return result


def _load_today_summary(hd: str) -> dict:
    """当日の累計EV成績を results_{hd}.json から集計して返す"""
    path = os.path.join(config.DATA_DIR, f"results_{hd}.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        bets = [b for r in data.get("races", []) for b in r.get("ev_bets", [])]
        return {
            "bets":   len(bets),
            "hits":   sum(1 for b in bets if b.get("hit")),
            "return": sum(b.get("return", 0.0) for b in bets),
        }
    except Exception:
        return {}


def _load_cumulative(hd: str) -> dict:
    """accuracy_log.json から累計成績を返す（当日除く）"""
    path = os.path.join(config.DATA_DIR, "accuracy_log.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            log = json.load(f)
        days = [s for d, s in log.items() if d != hd and s.get("ev_bets_count", 0) > 0]
        if not days:
            return {}
        return {
            "days":   len(days),
            "bets":   sum(s["ev_bets_count"] for s in days),
            "hits":   sum(s.get("ev_hits", 0) for s in days),
            "return": sum(s.get("ev_total_return", 0.0) for s in days),
        }
    except Exception:
        return {}


def _minutes_until(race_time_str: str, now: datetime) -> float:
    if not race_time_str:
        return float("inf")
    try:
        h, m = map(int, race_time_str.split(":"))
        race_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        diff = (race_dt - now).total_seconds() / 60
        if diff < -600:
            diff += 24 * 60
        return diff
    except Exception:
        return float("inf")


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
            if p_combo < 0.005:
                continue
            rows.append({
                "r1": r1, "r2": r2, "r3": r3,
                "p_r1": p_r1, "p_combo": p_combo,
                "odds": ov, "ev": p_combo * ov - 1.0,
            })
    return sorted(rows, key=lambda x: x["ev"], reverse=True)


def run(hd: str, win_min: int = 30, win_max: int = 60):
    batch_path   = os.path.join(config.DATA_DIR, f"today_{hd}.json")
    meta_path    = os.path.join(config.DATA_DIR, "model_meta.json")
    prerace_path = os.path.join(config.DATA_DIR, f"prerace_{hd}.json")

    if not os.path.exists(batch_path):
        print(f"[SKIP] 朝バッチなし: {batch_path}")
        return

    with open(batch_path, encoding="utf-8") as f:
        batch = json.load(f)
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)

    # prerace_json があれば読み込む（展示タイム・コース変更情報）
    prerace: dict = {}
    if os.path.exists(prerace_path):
        try:
            with open(prerace_path, encoding="utf-8") as f:
                prerace = json.load(f)
        except Exception:
            pass

    now = datetime.now(JST).replace(tzinfo=None)
    preds = batch.get("predictions", [])
    arare_stats = _load_arare_stats()
    use_arare_ev = bool(arare_stats)
    if use_arare_ev:
        print("荒れ統計テーブル読み込み完了 → arare EVモードで動作")
    else:
        print("[INFO] arare_stats.json なし → 従来EVモードで動作")

    # LINE資格情報の確認
    _tok = config.LINE_CHANNEL_ACCESS_TOKEN
    _uid = config.LINE_USER_ID
    if _tok and _tok not in ("", "your_token_here"):
        print(f"[LINE] トークン設定済み (先頭8文字: {_tok[:8]}...)")
    else:
        print("[LINE] ⚠️ トークン未設定 — GitHub Actions secrets に LINE_CHANNEL_ACCESS_TOKEN を設定してください")
    if _uid and _uid not in ("", "your_user_id_here"):
        print(f"[LINE] ユーザーID設定済み ({_uid[:6]}...)")
    else:
        print("[LINE] ⚠️ ユーザーID未設定 — GitHub Actions secrets に LINE_USER_ID を設定してください")

    targets = []
    for p in preds:
        mins = _minutes_until(p.get("race_time", ""), now)
        if not (win_min <= mins < win_max):
            continue
        # 荒れ候補フィルタ（arare_probがあれば使い、なければarare_scoreを100で割る）
        arare_prob = p.get("arare_prob", p.get("arare_score", 0) / 100)
        if arare_prob < ARARE_PROB_THRESH:
            continue
        targets.append((mins, p))

    if not targets:
        print(f"対象レースなし (窓: {win_min}〜{win_max}分前, 荒れ確率≥{ARARE_PROB_THRESH})")
        return

    # 本日の送信回数を prerace JSON から読む
    line_sent_today = int(prerace.get("line_sent_today", 0))
    print(f"対象: {len(targets)}レース (窓: {win_min}〜{win_max}分前, 荒れ候補のみ) / 本日LINE送信済: {line_sent_today}回")

    # ── オッズ取得 + EV計算（全レース）──────────────────────────────────────
    notify_list = []   # LINE送信対象レースをここに積む

    for mins, p in sorted(targets, key=lambda x: x[0]):
        ck         = f"{p['jcd']}_{p['race_no']}"
        arare_prob = p.get("arare_prob", 0.5)
        print(f"  {p['venue_name']} {p['race_no']}R  ({mins:.0f}分前 / 荒れ{arare_prob*100:.0f}%) オッズ取得中...")
        try:
            odds = scrape_odds_3t(p["jcd"], hd, p["race_no"])
            if not odds:
                print(f"    オッズ取得失敗")
                continue

            # オッズを prerace_json に保存（collect_results.py が後で使う）
            if ck not in prerace:
                prerace[ck] = {}
            prerace[ck]["odds"] = {f"{k[0]}-{k[1]}-{k[2]}": v for k, v in odds.items()}

            boat_prob = {int(k): v for k, v in p["boat_prob"].items()}
            if use_arare_ev:
                ev_rows = _compute_ev_arare(boat_prob, arare_prob, odds, arare_stats, p["venue_name"])
            else:
                ev_rows = _compute_ev(boat_prob, odds, meta)

            top_ev = [r for r in ev_rows if r["ev"] >= EV_THRESH]
            course_changes = prerace.get(ck, {}).get("course_changes") or None

            # 診断ログ
            if ev_rows:
                top3 = ev_rows[:3]
                print(f"    EV上位3: " + " / ".join(
                    f"{r['r1']}-{r['r2']}-{r['r3']} EV{r['ev']:+.3f}({r['odds']}x)" for r in top3
                ))

            if top_ev:
                notify_list.append({
                    "race":           p,
                    "ev_rows":        ev_rows,
                    "arare_prob":     arare_prob,
                    "course_changes": course_changes,
                })
                print(f"    → 通知候補 (EV≥{EV_THRESH}: {len(top_ev)}点)")
            else:
                top_ev_val = ev_rows[0]["ev"] if ev_rows else None
                print(f"    EV≥{EV_THRESH}の買い目なし" + (f" (最高EV={top_ev_val:+.3f})" if top_ev_val is not None else ""))

        except Exception as e:
            print(f"    [ERR] {e}")

    # prerace JSON を保存（オッズ記録のため）
    try:
        with open(prerace_path, "w", encoding="utf-8") as _pf:
            json.dump(prerace, _pf, ensure_ascii=False)
    except Exception:
        pass

    # ── まとめて1通のLINEに送信（月200通制限対応）────────────────────────────
    if not notify_list:
        print(f"\n完了: 通知対象レースなし")
        return

    if line_sent_today >= MAX_DAILY_NOTIFY:
        print(f"\n本日のLINE上限({MAX_DAILY_NOTIFY}回)に達しています。スキップ。")
        skipped = ", ".join(f"{n['race']['venue_name']} {n['race']['race_no']}R" for n in notify_list)
        print(f"  候補: {skipped}")
        return

    text = _format_consolidated(notify_list, now, EV_THRESH, line_sent_today)
    if send_line_message(text):
        line_sent_today += 1
        prerace["line_sent_today"] = line_sent_today
        try:
            with open(prerace_path, "w", encoding="utf-8") as _pf:
                json.dump(prerace, _pf, ensure_ascii=False)
        except Exception:
            pass
        print(f"\n📲 LINE送信完了: {len(notify_list)}レースまとめ / 本日{line_sent_today}回目(上限{MAX_DAILY_NOTIFY})")
    else:
        print(f"\nLINE送信失敗")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None)
    parser.add_argument("--min",  type=int, default=30, help="通知する最小分数前 (default: 30)")
    parser.add_argument("--max",  type=int, default=60, help="通知する最大分数前 (default: 60)")
    args = parser.parse_args()

    hd = args.date or date.today().strftime("%Y%m%d")
    print(f"\n=== 発走前通知  {hd}  窓: {args.min}〜{args.max}分前 ===")
    run(hd, win_min=args.min, win_max=args.max)
