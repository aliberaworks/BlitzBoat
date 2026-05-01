"""
BlitzBoat LINE Bot — チャンスレース通知
LINE Messaging API Push Message
"""
import json
import requests as http_requests

import config


def send_line_message(text: str) -> bool:
    """
    LINE Messaging API でプッシュメッセージを送信。
    
    Args:
        text: 送信するテキスト
        
    Returns: 成功/失敗
    """
    token = config.LINE_CHANNEL_ACCESS_TOKEN
    user_id = config.LINE_USER_ID
    
    if not token or token == "your_token_here":
        print("  [LINE] トークン未設定。メッセージ送信をスキップ。")
        print(f"  [LINE] 送信予定内容:\n{text}")
        return False
    
    if not user_id or user_id == "your_user_id_here":
        print("  [LINE] ユーザーID未設定。メッセージ送信をスキップ。")
        return False
    
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    payload = {
        "to": user_id,
        "messages": [
            {"type": "text", "text": text}
        ],
    }
    
    try:
        resp = http_requests.post(url, headers=headers, json=payload, timeout=10)
        if resp.status_code == 200:
            print("  [LINE] メッセージ送信成功")
            return True
        else:
            print(f"  [LINE] 送信失敗: {resp.status_code} {resp.text}")
            return False
    except Exception as e:
        print(f"  [LINE] エラー: {e}")
        return False


def format_chance_race_message(chance_race: dict, tickets: list[dict] = None) -> str:
    """
    チャンスレースのLINE通知用テキストを生成。
    """
    boat1 = chance_race.get("boat1", {})
    venue_name = chance_race.get("venue_name", "")
    race_no = chance_race.get("race_no", 0)
    win_prob = chance_race.get("boat1_win_prob", 0)
    
    tier = chance_race.get("tier", 0)
    rate_gap = chance_race.get("rate_gap", 0.0)
    dent_prob = chance_race.get("dent_probability", 0.0)
    tier_reason = chance_race.get("tier_reason", "")
    
    lines = [
        f"🔥 BlitzBoat Tier {tier} Alert 🔥",
        "",
        f"📍 {venue_name} {race_no}R",
        f"🚩 1号艇: {boat1.get('name', '不明')}",
        f"📊 全国勝率: {boat1.get('national_rate', 0):.2f}",
        f"📉 当地ギャップ: {rate_gap:.2f}",
        f"⚠️ 1号艇推定勝率: {win_prob*100:.1f}%",
        f"🌪️ 凹み確率: {dent_prob*100:.0f}%",
        "",
        f"📝 判定: {tier_reason}",
    ]
    
    if tickets:
        lines.append("")
        lines.append(f"── 推奨出目 (¥{config.TOTAL_BUDGET:,}配分) ──")
        for i, t in enumerate(tickets[:8]): # 少し多めに表示
            prob_pct = t["prob"] * 100
            lines.append(f"{i+1}. {t['trifecta']} ({prob_pct:.1f}%)")
    
    lines.extend([
        "",
        "▶ 詳細は作戦盤をチェック!",
        "🔗 https://blitzboat.vercel.app",
    ])
    
    return "\n".join(lines)


def notify_chance_races(chance_races: list[dict], venue_stats: dict = None):
    """
    チャンスレースリストからLINE通知を送信。
    最も凹みそうなレースを優先的に通知。
    """
    if not chance_races:
        print("  [LINE] チャンスレースなし。通知をスキップ。")
        return
    
    # 最も凹みそうなレース (boat1_win_prob最低)
    top_race = chance_races[0]
    
    from ticket_generator import generate_tickets
    from statistics_engine import get_filtered_ranking
    
    tickets = []
    if venue_stats:
        dent_prob = top_race.get("dent_probability", 0.0)
        patterns = get_filtered_ranking(venue_stats, top_race.get("venue", ""), dent_prob)
        if patterns:
            tickets = generate_tickets(patterns)
    
    message = format_chance_race_message(top_race, tickets)
    send_line_message(message)
    
    # 残りのチャンスレースもサマリー通知
    if len(chance_races) > 1:
        summary_lines = [
            f"📋 本日のチャンスレース: 全{len(chance_races)}件",
            "",
        ]
        for i, cr in enumerate(chance_races[:10]):
            win_pct = cr.get("boat1_win_prob", 0) * 100
            summary_lines.append(
                f"{i+1}. {cr['venue_name']} {cr['race_no']}R (1号艇勝率: {win_pct:.0f}%)"
            )
        
        send_line_message("\n".join(summary_lines))


# ── EV推薦買い目通知 ──────────────────────────────────────────────────────────

_BOAT_LABEL = ["①白", "②黒", "③赤", "④青", "⑤黄", "⑥緑"]


def format_ev_notification(
    venue_name: str,
    race_no: int,
    race_time: str,
    ev_rows: list,
    ev_thresh: float = 0.5,
    course_changes: list | None = None,
) -> str:
    """EV推薦買い目のLINE通知テキストを生成"""
    targets = [r for r in ev_rows if r["ev"] >= ev_thresh]
    if not targets:
        return ""

    lines = [
        f"🎯 EV推薦買い目",
        f"📍 {venue_name} {race_no}R　⏰ {race_time}",
    ]

    # 前づけ・後ろ付けアラート
    if course_changes:
        lines.append("")
        for c in course_changes:
            icon = "⚠️ 前づけ" if c["type"] == "前づけ" else "↩️ 後ろ付け"
            lines.append(
                f"{icon}: {_BOAT_LABEL[c['boat']-1]} → {c['course']}コース発走"
            )

    lines.append("")
    for i, row in enumerate(targets[:6], 1):
        r1, r2, r3 = row["r1"], row["r2"], row["r3"]
        b1 = _BOAT_LABEL[r1 - 1]
        b2 = _BOAT_LABEL[r2 - 1]
        b3 = _BOAT_LABEL[r3 - 1]
        ev   = row["ev"]
        odds = row["odds"]
        star = "★" if ev >= 1.0 else "☆"
        lines.append(f"{star} {b1}-{b2}-{b3}  {odds:.1f}倍  EV{ev:+.2f}")

    lines.extend([
        "",
        f"EV≥{ev_thresh:.1f}の買い目: {len(targets)}通り",
        "※統計確率×市場オッズ−1の参考値です",
    ])
    return "\n".join(lines)


def format_race_result_notification(
    venue_name: str,
    race_no: int,
    race_time: str,
    trifecta: str,
    kimarite: str,
    ev_bets: list,
    today_summary: dict | None = None,
    cumulative: dict | None = None,
) -> str:
    """
    レース結果のLINE通知テキストを生成。

    ev_bets: [{"combo":"1-2-3","ev":0.8,"odds":12.5,"hit":True,"return":12.5}, ...]
    today_summary: {"bets":12,"hits":3,"return":45.0}
    cumulative: {"days":5,"bets":60,"hits":14,"return":210.0}
    """
    hit_bets  = [b for b in ev_bets if b.get("hit")]
    miss_bets = [b for b in ev_bets if not b.get("hit")]
    total_ret = sum(b.get("return", 0) for b in ev_bets)

    lines = [f"🏁 {venue_name} {race_no}R 結果  {race_time}"]
    lines.append(f"{'✅' if hit_bets else '❌'}  {trifecta}（{kimarite}）")

    if ev_bets:
        lines.append("")
        lines.append("── EV買い目 ──")
        for b in ev_bets[:6]:
            r1, r2, r3 = b["combo"].split("-")
            b1 = _BOAT_LABEL[int(r1) - 1]
            b2 = _BOAT_LABEL[int(r2) - 1]
            b3 = _BOAT_LABEL[int(r3) - 1]
            mark = "✅" if b.get("hit") else "❌"
            ret_str = f" → 回収 {b['return']:.1f}倍" if b.get("hit") else ""
            lines.append(f"{mark} {b1}-{b2}-{b3}  {b['odds']:.1f}倍  EV{b['ev']:+.2f}{ret_str}")

        if total_ret > 0:
            lines.append(f"💰 {len(ev_bets)}件中{len(hit_bets)}件的中  回収{total_ret:.1f}倍")
        else:
            lines.append(f"💸 {len(ev_bets)}件全外れ")

    if today_summary and today_summary.get("bets", 0) > 0:
        lines.append("")
        td = today_summary
        roi = (td["return"] / td["bets"] - 1) * 100 if td["bets"] > 0 else 0
        lines.append(
            f"📊 本日累計  {td['bets']}件/{td['hits']}的中  "
            f"ROI {roi:+.1f}%"
        )

    if cumulative and cumulative.get("bets", 0) > 0:
        cu = cumulative
        cu_roi = (cu["return"] / cu["bets"] - 1) * 100 if cu["bets"] > 0 else 0
        lines.append(
            f"📈 累計({cu.get('days',0)}日)  {cu['bets']}件/{cu['hits']}的中  "
            f"ROI {cu_roi:+.1f}%"
        )

    return "\n".join(lines)


def format_daily_summary(
    hd: str,
    race_records: list,
    ev_bets_count: int,
    ev_hits: int,
    ev_total_return: float,
    ev_thresh: float,
    log: dict,
) -> str:
    """1日まとめのLINE通知テキストを生成"""
    n        = len(race_records)
    win_hits = sum(1 for r in race_records if r.get("hit_win"))
    ev_roi   = (ev_total_return / ev_bets_count - 1) * 100 if ev_bets_count > 0 else None

    ymd = f"{hd[:4]}/{hd[4:6]}/{hd[6:]}"
    lines = [f"📊 {ymd} 1日まとめ", ""]

    lines.append(f"🔍 モデル精度")
    lines.append(f"  1着的中: {win_hits}/{n}R ({win_hits/n*100:.1f}%)" if n else "  データなし")

    if ev_bets_count > 0:
        lines.append("")
        lines.append(f"💰 EV≥{ev_thresh} 買い目")
        lines.append(f"  {ev_bets_count}件中 {ev_hits}件的中")
        if ev_roi is not None:
            lines.append(f"  本日ROI: {ev_roi:+.1f}%")
            lines.append(f"  回収: {ev_total_return:.1f}倍 / {ev_bets_count}賭け")

        # 高配当の的中があれば表示
        best_hits = sorted(
            [b for r in race_records for b in r.get("ev_bets", []) if b.get("hit")],
            key=lambda x: x.get("return", 0), reverse=True
        )[:3]
        if best_hits:
            lines.append("")
            lines.append("🏆 本日の的中")
            for b in best_hits:
                lines.append(f"  {b['combo']}  {b['return']:.1f}倍回収")
    else:
        lines.append("")
        lines.append("💰 EV買い目なし（オッズ未取得 or 低EV）")

    # 累計
    all_days = [s for s in log.values() if s.get("ev_bets_count", 0) > 0]
    if len(all_days) >= 2:
        total_bets   = sum(s["ev_bets_count"] for s in all_days)
        total_hits   = sum(s.get("ev_hits", 0) for s in all_days)
        total_return = sum(s.get("ev_total_return", 0.0) for s in all_days)
        cum_roi      = (total_return / total_bets - 1) * 100 if total_bets > 0 else 0
        lines.append("")
        lines.append(f"📈 累計（{len(all_days)}日間）")
        lines.append(f"  {total_bets}件/{total_hits}的中  ROI {cum_roi:+.1f}%")

    return "\n".join(lines)


def send_ev_notification(
    venue_name: str,
    race_no: int,
    race_time: str,
    ev_rows: list,
    ev_thresh: float = 0.5,
    course_changes: list | None = None,
) -> bool:
    """EV推薦買い目をLINEに送信。送信した場合 True を返す。"""
    text = format_ev_notification(
        venue_name, race_no, race_time, ev_rows, ev_thresh, course_changes
    )
    if not text:
        return False
    return send_line_message(text)
