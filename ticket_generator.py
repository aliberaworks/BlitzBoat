"""
BlitzBoat Ticket Generator
Probability-weighted ticket allocation, total 30,000 yen
"""
import sys
import io

import config

# Windows console UTF-8 support
if sys.platform == 'win32':
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    except Exception:
        pass


def generate_tickets(patterns: list[dict], budget: int = None) -> list[dict]:
    """
    確率分布に基づき推奨舟券を生成。
    合計金額を傾斜配分 (100円単位丸め)。
    
    Args:
        patterns: [{"trifecta": "2-3-4", "prob": 0.08, "kimarite": "まくり"}, ...]
        budget: 合計金額 (default: 30,000円)
        
    Returns: [{"trifecta": "2-3-4", "prob": 0.08, "amount": 4900, "kimarite": "まくり"}, ...]
    """
    if budget is None:
        budget = config.TOTAL_BUDGET
    
    if not patterns:
        return []
    
    # 確率合計
    total_prob = sum(p["prob"] for p in patterns)
    if total_prob == 0:
        return []
    
    # ── 傾斜配分計算 ──
    tickets = []
    for p in patterns:
        ratio = p["prob"] / total_prob
        raw_amount = budget * ratio
        # 100円単位に丸め
        amount = max(config.MIN_BET_UNIT, round(raw_amount / config.MIN_BET_UNIT) * config.MIN_BET_UNIT)
        
        tickets.append({
            "trifecta": p["trifecta"],
            "prob": p["prob"],
            "amount": amount,
            "kimarite": p.get("kimarite", ""),
            "cum_prob": p.get("cum_prob", 0),
        })
    
    # ── 合計金額調整 ──
    current_total = sum(t["amount"] for t in tickets)
    diff = budget - current_total
    
    if diff != 0:
        # 最も確率の高い買い目で調整
        tickets[0]["amount"] += diff
        # 調整後もMIN_BET_UNIT以上を保証
        if tickets[0]["amount"] < config.MIN_BET_UNIT:
            tickets[0]["amount"] = config.MIN_BET_UNIT
    
    # 最終合計を再確認
    final_total = sum(t["amount"] for t in tickets)
    if final_total != budget:
        # 微調整: 最後の買い目で吸収
        tickets[-1]["amount"] += (budget - final_total)
    
    return tickets


def print_tickets(tickets: list[dict], venue_name: str = "", race_no: int = 0):
    """推奨舟券をコンソールに出力"""
    header = f"推奨舟券"
    if venue_name:
        header += f" ({venue_name}"
        if race_no:
            header += f" {race_no}R"
        header += ")"
    
    print(f"\n{'='*60}")
    print(f"  {header}")
    print(f"  Total: Y{config.TOTAL_BUDGET:,}")
    print(f"{'='*60}")
    print(f"  {'Bet':>7} | {'Prob':>7} | {'Amount':>8} | Kimarite")
    print(f"  {'-'*50}")
    
    total_amount = 0
    for t in tickets:
        prob_pct = t["prob"] * 100
        total_amount += t["amount"]
        print(f"  {t['trifecta']:>7} | {prob_pct:>6.2f}% | Y{t['amount']:>7,} | {t['kimarite']}")
    
    print(f"  {'-'*50}")
    print(f"  {'Total':>7} |         | Y{total_amount:>7,}")
    
    if total_amount != config.TOTAL_BUDGET:
        print(f"  * Mismatch: Y{total_amount:,} (target: Y{config.TOTAL_BUDGET:,})")


def format_tickets_for_line(tickets: list[dict], venue_name: str, race_no: int) -> str:
    """LINE通知用のテキストフォーマット"""
    lines = [
        f"🚤 BlitzBoat 推奨舟券",
        f"📍 {venue_name} {race_no}R",
        f"💰 合計: ¥{config.TOTAL_BUDGET:,}",
        "",
    ]
    
    for i, t in enumerate(tickets):
        prob_pct = t["prob"] * 100
        lines.append(f"{i+1}. {t['trifecta']} → ¥{t['amount']:,} ({prob_pct:.1f}%)")
    
    return "\n".join(lines)


if __name__ == "__main__":
    # テスト
    test_patterns = [
        {"trifecta": "2-3-4", "prob": 0.082, "kimarite": "まくり"},
        {"trifecta": "3-2-4", "prob": 0.065, "kimarite": "まくり差し"},
        {"trifecta": "4-2-3", "prob": 0.055, "kimarite": "まくり"},
        {"trifecta": "2-4-3", "prob": 0.048, "kimarite": "まくり"},
        {"trifecta": "3-4-2", "prob": 0.042, "kimarite": "まくり差し"},
        {"trifecta": "4-3-2", "prob": 0.038, "kimarite": "まくり"},
        {"trifecta": "5-2-3", "prob": 0.032, "kimarite": "まくり"},
        {"trifecta": "2-5-3", "prob": 0.028, "kimarite": "まくり"},
    ]
    
    tickets = generate_tickets(test_patterns)
    print_tickets(tickets, "テスト会場", 1)
