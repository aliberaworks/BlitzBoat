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
