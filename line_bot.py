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
    cond1 = chance_race.get("cond1", {})
    cond2 = chance_race.get("cond2", {})
    
    lines = [
        "🔥 BlitzBoat 大荒れ警報 🔥",
        "",
        f"📍 {venue_name} {race_no}R",
        f"🚩 1号艇: {boat1.get('name', '不明')}",
        f"📊 全国勝率: {boat1.get('national_rate', 0):.1f}",
        f"📊 当地勝率: {boat1.get('local_rate', 0):.1f}",
        f"⚠️ 1号艇勝率推定: {win_prob*100:.1f}%",
        "",
        f"❌ Cond.1: {cond1.get('reason', '')}",
        f"❌ Cond.2: {cond2.get('reason', '')}",
    ]
    
    if tickets:
        lines.append("")
        lines.append("── 推奨出目 ──")
        for i, t in enumerate(tickets[:5]):
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
    from statistics_engine import get_venue_ranking
    
    tickets = []
    if venue_stats:
        patterns = get_venue_ranking(venue_stats, top_race.get("venue", ""))
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
