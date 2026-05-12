"""
ScenarioBoat 発走前通知
GitHub Actions: 30分ごと 8:00〜21:30 JST

発走30〜60分前のレースに対してEVを計算しLINEに送る。
EV = Σ_s P(scenario_s) × P(trifecta|scenario_s) × live_odds - 1
"""
import argparse, json, os, sys
from datetime import date, datetime, timezone, timedelta
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_BB   = os.path.dirname(_HERE)
sys.path = [_HERE, _BB] + [p for p in sys.path if p not in (_HERE, _BB)]

import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("scraper", os.path.join(_BB, "scraper.py"))
_scraper = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_scraper)
scrape_odds_3t = _scraper.scrape_odds_3t

try:
    _bb_cfg = _ilu.spec_from_file_location("bb_config", os.path.join(_BB, "config.py"))
    _bb_cfg_mod = _ilu.module_from_spec(_bb_cfg); _bb_cfg.loader.exec_module(_bb_cfg_mod)
    LINE_TOKEN = _bb_cfg_mod.LINE_CHANNEL_ACCESS_TOKEN
    LINE_UID   = _bb_cfg_mod.LINE_USER_ID
except Exception:
    LINE_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    LINE_UID   = os.environ.get("LINE_USER_ID", "")

try:
    from line_bot import send_line_message
except Exception:
    def send_line_message(*a, **kw): return False

import config

JST = timezone(timedelta(hours=9))
BOAT_LABEL = ["①白", "②黒", "③赤", "④青", "⑤黄", "⑥緑"]


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


def load_stats() -> dict:
    with open(config.STATS_JSON, encoding="utf-8") as f:
        return json.load(f)


def compute_ev(scenario_probs: dict, trifecta_dists: dict, live_odds: dict) -> list[dict]:
    """
    EV(trifecta) = Σ_s P(s) × P(tri|s) × live_odds(tri) - 1
    """
    all_tri_prob = defaultdict(float)
    for sc in config.SCENARIOS:
        p_sc = scenario_probs.get(sc, 0.0)
        if p_sc < 0.005:
            continue
        for tri, p in trifecta_dists.get(sc, {}).items():
            all_tri_prob[tri] += p_sc * p

    rows = []
    for tri, prob in all_tri_prob.items():
        try:
            parts = tri.split("-")
            key = (int(parts[0]), int(parts[1]), int(parts[2]))
        except Exception:
            continue
        odds_val = live_odds.get(key)
        if odds_val is None or odds_val <= 0:
            continue
        ev = prob * odds_val - 1.0
        rows.append({
            "trifecta": tri, "r1": key[0], "r2": key[1], "r3": key[2],
            "prob": round(prob, 5), "odds": odds_val,
            "ev": round(ev, 4),
        })
    return sorted(rows, key=lambda x: x["ev"], reverse=True)


def format_message(notify_list: list, now: datetime, sent_today: int) -> str:
    t = now.strftime("%H:%M")
    lines = [f"🌊 ScenarioBoat EV速報  {t}", "━━━━━━━━━━━"]

    lines.append("▼ シナリオ予報")
    for rank, n in enumerate(notify_list, 1):
        p  = n["race"]
        sc = n["top_scenario"]
        prob = int(n["top_prob"] * 100)
        lines.append(f"{rank}. {p['venue_name']} {p['race_no']}R ⏰{p.get('race_time','')}  [{sc}] {prob}%")

    lines.append("━━━━━━━━━━━")
    lines.append(f"▼ 買い目 EV≥{config.EV_THRESH}")
    for n in notify_list:
        p    = n["race"]
        bets = n["ev_rows"][:config.MAX_BETS_PER_RACE]
        lines.append(f"")
        lines.append(f"📍 {p['venue_name']} {p['race_no']}R ⏰{p.get('race_time','')}")
        lines.append(f"   シナリオ: {n['top_scenario']}({int(n['top_prob']*100)}%)")
        for i, b in enumerate(bets, 1):
            b1 = BOAT_LABEL[b["r1"]-1]
            b2 = BOAT_LABEL[b["r2"]-1]
            b3 = BOAT_LABEL[b["r3"]-1]
            star = "★" if b["ev"] >= 1.0 else "☆"
            lines.append(f"  {i}. {star}{b1}-{b2}-{b3} {b['odds']:.0f}倍 EV{b['ev']:+.2f}")

    lines += ["", "━━━━━━━━━━━",
              f"本日{sent_today+1}回目 / 上限{config.MAX_DAILY_NOTIFY}回",
              "※シナリオ確率×統計分布×市場オッズ"]
    return "\n".join(lines)


def run(hd: str, win_min: int = 30, win_max: int = 60):
    today_path  = config.today_json(hd)
    prerace_path = os.path.join(config.DATA_DIR, f"scenario_prerace_{hd}.json")

    if not os.path.exists(today_path):
        print(f"[SKIP] 朝バッチなし: {today_path}")
        return

    with open(today_path, encoding="utf-8") as f:
        today = json.load(f)
    stats = load_stats()
    trifecta_dists = {sc: d["trifecta_dist"] for sc, d in stats["scenarios"].items()}

    prerace: dict = {}
    if os.path.exists(prerace_path):
        with open(prerace_path, encoding="utf-8") as f:
            prerace = json.load(f)

    now  = datetime.now(JST).replace(tzinfo=None)
    preds = today.get("predictions", [])

    # 発走時刻フィルタ（30〜60分前）
    targets = []
    for p in preds:
        mins = _minutes_until(p.get("race_time", ""), now)
        if win_min <= mins < win_max:
            targets.append((mins, p))

    if not targets:
        print(f"対象レースなし (窓: {win_min}〜{win_max}分前)")
        return

    line_sent_today = int(prerace.get("line_sent_today", 0))
    print(f"対象: {len(targets)}レース / 本日LINE送信済: {line_sent_today}回")

    notify_list = []
    for mins, p in sorted(targets, key=lambda x: x[0]):
        sc_probs = p.get("scenario_probs", {})
        top_sc   = p.get("top_scenario", "other")
        top_prob = p.get("top_prob", 0.0)

        print(f"  {p['venue_name']} {p['race_no']}R ({mins:.0f}分前) [{top_sc} {top_prob*100:.0f}%] オッズ取得中...")
        try:
            raw_odds = scrape_odds_3t(p["jcd"], hd, p["race_no"])
            if not raw_odds:
                print("    オッズ取得失敗")
                continue

            # オッズを保存（collect_results用）
            ck = f"{p['jcd']}_{p['race_no']}"
            if ck not in prerace:
                prerace[ck] = {}
            prerace[ck]["odds"] = {f"{k[0]}-{k[1]}-{k[2]}": v for k, v in raw_odds.items()}

            ev_rows = compute_ev(sc_probs, trifecta_dists, raw_odds)
            good    = [r for r in ev_rows if r["ev"] >= config.EV_THRESH]

            if ev_rows:
                top3 = ev_rows[:3]
                print("    EV上位3: " + " / ".join(
                    f"{r['trifecta']} EV{r['ev']:+.3f}({r['odds']}x)" for r in top3
                ))

            if good:
                notify_list.append({
                    "race":         p,
                    "ev_rows":      ev_rows,
                    "top_scenario": top_sc,
                    "top_prob":     top_prob,
                })
                print(f"    → 通知候補 (EV≥{config.EV_THRESH}: {len(good)}点)")
            else:
                best_ev = ev_rows[0]["ev"] if ev_rows else None
                print(f"    EV≥{config.EV_THRESH}なし" + (f" (最高{best_ev:+.3f})" if best_ev else ""))

        except Exception as e:
            print(f"    [ERR] {e}")

    # prerace保存
    try:
        with open(prerace_path, "w", encoding="utf-8") as f:
            json.dump(prerace, f, ensure_ascii=False)
    except Exception:
        pass

    if not notify_list:
        print("\n通知対象なし")
        return

    if line_sent_today >= config.MAX_DAILY_NOTIFY:
        print(f"\nLINE上限({config.MAX_DAILY_NOTIFY}回)到達、スキップ")
        return

    notify_list.sort(key=lambda x: x["top_prob"], reverse=True)
    text = format_message(notify_list, now, line_sent_today)

    if send_line_message(text):
        line_sent_today += 1
        prerace["line_sent_today"] = line_sent_today
        with open(prerace_path, "w", encoding="utf-8") as f:
            json.dump(prerace, f, ensure_ascii=False)
        print(f"\n📲 LINE送信完了: {len(notify_list)}レース / 本日{line_sent_today}回目")
    else:
        print("\nLINE送信失敗（月上限または設定ミス）")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None)
    parser.add_argument("--min",  type=int, default=30)
    parser.add_argument("--max",  type=int, default=60)
    args = parser.parse_args()
    hd = args.date or date.today().strftime("%Y%m%d")
    print(f"\n=== ScenarioBoat 発走前通知  {hd}  窓:{args.min}〜{args.max}分前 ===")
    run(hd, win_min=args.min, win_max=args.max)
