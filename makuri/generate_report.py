"""
makuri - 朝の予想レポート生成スクリプト
boatrace.jp をスクレイプ → まくり予測 → Obsidian Vault に Markdown 保存
LINE: 荒れ警報 Top5 のみ1日1通

実行:
  python generate_report.py            # 今日分
  python generate_report.py --date 20260606
"""
import argparse, json, os, sys, io
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import config, scenario, combo

sys.path.insert(0, config.BLITZBOAT_DIR)
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location("scraper", os.path.join(config.BLITZBOAT_DIR, "scraper.py"))
_scr  = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_scr)
scrape_today_venues = _scr.scrape_today_venues
scrape_racelist     = _scr.scrape_racelist
scrape_race_times   = _scr.scrape_race_times

# Obsidian レポート保存先
OBSIDIAN_REPORT_DIR = os.path.join(
    os.path.dirname(config.BASE_DIR), "レポート"
)
MAX_WORKERS = 8
BOAT_LABEL  = ["①白", "②黒", "③赤", "④青", "⑤黄", "⑥緑"]
GRADE_COLOR = {"A1": "🔴", "A2": "🟠", "B1": "🔵", "B2": "⚪"}

# 逃げ確定の閾値
NIGE_ARARE_MAX   = 0.40   # 荒れ確率がこれ以下
NIGE_B1_RATE_MIN = 5.5    # 1号艇の全国勝率がこれ以上


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
                    "name":          e.get("name", ""),
                }
            sr    = scenario.score_race(jcd, boat_data)
            cmbs  = combo.get_combos_for_scenario(sr)
            results.append({
                "jcd": jcd, "venue_name": venue_name,
                "race_no": rno, "race_time": race_times.get(rno, ""),
                "boat_data": boat_data,
                "scenario_result": sr,
                "combos": cmbs,
            })
        except Exception as e:
            print(f"  [WARN] {venue_name} {rno}R: {e}", file=sys.stderr)
    return results


def is_nige_kakutei(r: dict) -> bool:
    """逃げ確定レース判定"""
    sr = r["scenario_result"]
    ap = sr.get("arare_prob", 1.0)
    if ap > NIGE_ARARE_MAX:
        return False
    b1 = r["boat_data"].get(1, {})
    if b1.get("national_rate", 0) < NIGE_B1_RATE_MIN:
        return False
    if b1.get("grade", "") not in ("A1", "A2"):
        return False
    # 外艇（3〜6）に強い選手がいないか
    max_outer = max(
        r["boat_data"].get(b, {}).get("national_rate", 0)
        for b in range(3, 7)
    )
    if max_outer > b1.get("national_rate", 0):
        return False
    return True


def render_boat_table(boat_data: dict) -> str:
    rows = ["| 艇 | 選手 | Gr | 全国 | 当地 | ST | モーター |",
            "|:--:|------|:--:|----:|----:|----:|-------:|"]
    for b in range(1, 7):
        bd   = boat_data.get(b, {})
        gr   = bd.get("grade", "")
        icon = GRADE_COLOR.get(gr, "⚪")
        name = bd.get("name", "-")
        nat  = bd.get("national_rate", 0)
        loc  = bd.get("local_rate", 0)
        st   = bd.get("avg_st", 0)
        mot  = bd.get("motor_2rate", 0)
        rows.append(f"| {BOAT_LABEL[b-1]} | {name} | {icon}{gr} | {nat:.2f} | {loc:.2f} | {st:.2f} | {mot:.1f}% |")
    return "\n".join(rows)


def render_race_block(r: dict, show_detail: bool = True) -> list[str]:
    """1レース分のMarkdownブロックを返す"""
    sr    = r["scenario_result"]
    arare = sr.get("arare_prob", 0)
    b1t   = sr.get("b1_type", "")
    s3    = sr.get("boat3") or {}
    s4    = sr.get("boat4") or {}
    scen  = sr.get("scenario", "")
    cmbs  = r.get("combos", [])
    rtime = r.get("race_time", "--:--")

    lines = [f"#### {r['venue_name']} {r['race_no']}R  {rtime}", ""]
    if show_detail:
        lines.append(render_boat_table(r["boat_data"]))
        lines.append("")
    lines.append(f"荒れ確率 **{arare*100:.0f}%**　1号艇: {b1t}")
    if s3.get("p_try", 0) >= config.MAKKURI_TRY_THRESH:
        lines.append(f"③まくり: 試行{s3['p_try']*100:.0f}% 成功{s3['p_success']*100:.0f}% 失敗{s3['p_fail']*100:.0f}%")
    if s4.get("p_try", 0) >= config.MAKKURI_TRY_THRESH:
        lines.append(f"④まくり: 試行{s4['p_try']*100:.0f}% 成功{s4['p_success']*100:.0f}% 失敗{s4['p_fail']*100:.0f}%")
    lines.append(f"シナリオ: {scen}")
    if cmbs:
        lines.append("")
        lines.append("**買い目**")
        for i, c in enumerate(cmbs, 1):
            b1=BOAT_LABEL[c["r1"]-1]; b2=BOAT_LABEL[c["r2"]-1]; b3=BOAT_LABEL[c["r3"]-1]
            lines.append(f"{i}. {b1}-{b2}-{b3}　{c['pct']*100:.1f}%")
    lines.append("")
    return lines


def generate_report(hd: str) -> tuple[str, list]:
    """レポートMarkdownと全予測リストを返す"""
    ymd = f"{hd[:4]}-{hd[4:6]}-{hd[6:]}"
    print(f"\n=== makuri 予想レポート {ymd} ===\n")

    venues_raw = scrape_today_venues(hd)
    if not venues_raw:
        return f"# {ymd} 予想レポート\n\n開催情報を取得できませんでした。\n", []
    venues = {v["jcd"]: (v["name"], v.get("races", 12)) for v in venues_raw}
    print(f"  {len(venues)}会場")

    all_results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {
            ex.submit(predict_venue, jcd, name, hd, n): jcd
            for jcd, (name, n) in venues.items()
        }
        for fut in as_completed(futures):
            jcd = futures[fut]
            name, _ = venues[jcd]
            try:
                rows = fut.result()
                all_results.extend(rows)
                print(f"  {name}: {len(rows)}R完了")
            except Exception as e:
                print(f"  [ERR] {name}: {e}", file=sys.stderr)

    all_results.sort(key=lambda x: x["scenario_result"].get("arare_prob", 0), reverse=True)

    # 分類
    makkuri  = [r for r in all_results if r["scenario_result"].get("layer",0)==2]
    ura_suji = [r for r in all_results if r["scenario_result"].get("layer",0)==3]
    arare_l1 = [r for r in all_results if r["scenario_result"].get("layer",0)==1]
    nige     = [r for r in all_results if is_nige_kakutei(r)]

    l0 = sum(1 for r in all_results if r["scenario_result"].get("layer",0)==0)

    print(f"\n  まくり成功: {len(makkuri)}件  裏スジ: {len(ura_suji)}件  荒れ警報: {len(arare_l1)}件  逃げ確定: {len(nige)}件")

    # ── Markdown 生成 ─────────────────────────────────────────
    md = []
    md += [
        f"# {ymd} ボートレース予想レポート",
        "",
        f"> 生成: {datetime.now().strftime('%H:%M')}　全{len(all_results)}レース / {len(venues)}会場",
        "",
        "## サマリー",
        "",
        "| 区分 | 件数 | 内容 |",
        "|------|:----:|------|",
        f"| ⚡ まくり成功シナリオ | **{len(makkuri)}** | 3・4号艇まくり高確率 + 買い目4点 |",
        f"| 🔀 裏スジシナリオ | **{len(ura_suji)}** | まくり失敗→5号艇軸 |",
        f"| 🌪️ 荒れ警報 | **{len(arare_l1)}** | 1号艇が凹む可能性大 |",
        f"| 🏃 逃げ確定 | **{len(nige)}** | 1号艇A1以上・外弱・荒れ確率低 |",
        f"| ⬜ スキップ | {l0} | 非荒れ |",
        "",
        "---",
        "",
    ]

    # ── ⚡ まくり成功シナリオ ──────────────────────────────────
    if makkuri:
        md.append("## ⚡ まくり成功シナリオ（買い目あり）")
        md.append("")
        md.append("> Walk-Forward ROI: 3号艇+466〜+496% / 4号艇+545〜+572%（8.5年検証）")
        md.append("> 展示タイムを確認して仕掛けそうならGO")
        md.append("")
        for r in makkuri:
            md += render_race_block(r, show_detail=True)
        md.append("---")
        md.append("")
    else:
        md += ["## ⚡ まくり成功シナリオ", "", "本日は該当レースなし（p_try閾値未達）", "", "---", ""]

    # ── 🏃 逃げ確定レース ─────────────────────────────────────
    md.append("## 🏃 逃げ確定レース（鉄板狙い）")
    md.append("")
    md.append("> 1号艇A1以上・外弱・荒れ確率40%以下")
    md.append("> 低配当だが的中率高。1-2-3・1-3-2・1-2-4を中心に")
    md.append("")
    if nige:
        nige_sorted = sorted(nige, key=lambda x: (
            x["boat_data"].get(1,{}).get("national_rate",0) -
            max(x["boat_data"].get(b,{}).get("national_rate",0) for b in range(2,7))
        ), reverse=True)
        md.append("| 会場 | R | 時刻 | 荒れ確率 | 1号艇 | 全国勝率 | モーター | Gr |")
        md.append("|------|:--:|-----:|-------:|------|-------:|-------:|:--:|")
        for r in nige_sorted[:20]:
            b1   = r["boat_data"].get(1, {})
            ap   = r["scenario_result"].get("arare_prob", 0)
            name = b1.get("name", "-")
            nat  = b1.get("national_rate", 0)
            mot  = b1.get("motor_2rate", 0)
            gr   = b1.get("grade", "")
            icon = GRADE_COLOR.get(gr, "⚪")
            md.append(f"| {r['venue_name']} | {r['race_no']} | {r.get('race_time','--:--')} | {ap*100:.0f}% | {name} | {nat:.2f} | {mot:.1f}% | {icon}{gr} |")
        md.append("")
    else:
        md.append("本日は逃げ確定レースなし")
        md.append("")
    md.append("---")
    md.append("")

    # ── 🌪️ 荒れ警報（全件） ──────────────────────────────────
    md.append("## 🌪️ 荒れ警報レース（全件）")
    md.append("")
    arare_all = [r for r in all_results if r["scenario_result"].get("arare_prob",0) >= config.ARARE_THRESH_L1]
    if arare_all:
        md.append("| 会場 | R | 時刻 | 荒れ確率 | 1号艇タイプ | シナリオ |")
        md.append("|------|:--:|-----:|-------:|------------|---------|")
        for r in arare_all:
            sr   = r["scenario_result"]
            ap   = sr.get("arare_prob", 0)
            b1t  = sr.get("b1_type", "")
            scen = sr.get("scenario", "")
            lyr  = sr.get("layer", 0)
            icon = {0:"⬜", 1:"🌪️", 2:"⚡", 3:"🔀"}.get(lyr, "")
            stars= "★" * min(5, max(1, int((ap-0.50)/0.05)))
            md.append(f"| {r['venue_name']} | {r['race_no']} | {r.get('race_time','--:--')} | {ap*100:.0f}% {stars} | {b1t} | {icon} {scen[:20]} |")
        md.append("")
    else:
        md.append("本日は荒れ警報レースなし")
        md.append("")

    # ── 🔀 裏スジ ────────────────────────────────────────────
    if ura_suji:
        md.append("## 🔀 裏スジシナリオ")
        md.append("")
        for r in ura_suji:
            md += render_race_block(r, show_detail=True)

    md += ["---", "", f"*makuri system / 8.5年分データ / Walk-Forward検証済*"]
    return "\n".join(md), all_results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date",     default=None)
    parser.add_argument("--no-line",  action="store_true", help="LINE通知をスキップ")
    args = parser.parse_args()
    hd = args.date or date.today().strftime("%Y%m%d")

    md, all_results = generate_report(hd)

    # Obsidianに保存
    os.makedirs(OBSIDIAN_REPORT_DIR, exist_ok=True)
    ymd      = f"{hd[:4]}-{hd[4:6]}-{hd[6:]}"
    out_path = os.path.join(OBSIDIAN_REPORT_DIR, f"{ymd}.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"\n保存: {out_path}")

    # LINE: 荒れ警報 Top5 のみ（1日1通）
    if not args.no_line:
        try:
            from notify import send_layer1
            arare_top5 = [
                {"venue_name": r["venue_name"], "race_no": r["race_no"],
                 "race_time": r.get("race_time",""), "arare_prob": r["scenario_result"].get("arare_prob",0),
                 "b1_type": r["scenario_result"].get("b1_type","")}
                for r in all_results
                if r["scenario_result"].get("arare_prob",0) >= config.ARARE_THRESH_L1
            ][:5]
            if arare_top5:
                send_layer1(arare_top5, hd)
                print(f"  LINE送信: 荒れ警報Top{len(arare_top5)}件")
        except Exception as e:
            print(f"  LINE送信エラー: {e}", file=sys.stderr)

    print("完了")


if __name__ == "__main__":
    main()
