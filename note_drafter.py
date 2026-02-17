"""
BlitzBoat — note.com 下書き自動保存モジュール
Playwright で note.com にログインし、予測記事を下書き保存する。
祐希が朝起きて確認・公開ボタンを押すだけの状態にする。
"""
import os
from datetime import datetime

import config


# ═══════════════════════════════════════════
#  1. 記事コンテンツ生成
# ═══════════════════════════════════════════

def _build_article_content(
    chance_races: list[dict],
    venue_stats_summary: dict,
    date_str: str,
) -> tuple[str, str]:
    """
    note 記事のタイトルとマークダウン本文を生成。
    Returns: (title, body_markdown)
    """
    date_display = f"{date_str[:4]}/{date_str[4:6]}/{date_str[6:]}"
    n = len(chance_races)

    title = f"【BlitzBoat】{date_display} 本日のチャンスレース {n}件"

    body = f"## {date_display} 統計アラート\n\n"
    body += f"統計分析に基づくチャンスレースを **{n}件** 検出しました。\n\n"
    body += "---\n\n"

    # 各チャンスレース
    for i, cr in enumerate(chance_races):
        venue_name = cr.get("venue_name", "")
        race_no = cr.get("race_no", 0)
        boat1 = cr.get("boat1", {})
        prob = cr.get("boat1_win_prob", 0.0)
        cond1 = cr.get("cond1", {})
        cond2 = cr.get("cond2", {})

        tier = "Tier 1" if prob <= 0.40 else "Tier 2"

        body += f"### {tier}: {venue_name} {race_no}R\n\n"
        body += f"| 項目 | 値 |\n"
        body += f"|------|----|\n"
        body += f"| 1号艇 | {boat1.get('name', '')} |\n"
        body += f"| 全国勝率 | {boat1.get('national_rate', 0):.2f} |\n"
        body += f"| 当地勝率 | {boat1.get('local_rate', 0):.2f} |\n"
        body += f"| 1号艇勝率推定 | **{prob*100:.0f}%** |\n"
        body += f"\n"

        if cond1:
            body += f"- Cond1 (1号艇弱体): {cond1.get('reason', '')}\n"
        if cond2:
            body += f"- Cond2 (ST凹み): {cond2.get('reason', '')}\n"

        # 推奨舟券
        tickets = cr.get("tickets", [])
        if tickets:
            body += f"\n**推奨舟券 (予算 ¥{config.TOTAL_BUDGET:,})**\n\n"
            body += f"| 出目 | 確率 | 金額 |\n"
            body += f"|------|------|------|\n"
            for t in tickets[:8]:
                prob_pct = t["prob"] * 100
                body += f"| {t['trifecta']} | {prob_pct:.1f}% | ¥{t['amount']:,} |\n"

        body += "\n---\n\n"

    # アフィリエイトリンク
    affiliate_url = config.AFFILIATE_URL
    if affiliate_url:
        body += f"\n## 📱 ボートレースを始める\n\n"
        body += f"[こちらから無料登録]({affiliate_url})\n\n"

    # フッター
    body += (
        "\n---\n\n"
        "*この記事は BlitzBoat 統計エンジンにより自動生成されています。"
        "投資は自己責任でお願いします。*\n"
    )

    return title, body


# ═══════════════════════════════════════════
#  2. note.com 下書き保存 (Playwright)
# ═══════════════════════════════════════════

def save_note_draft(
    chance_races: list[dict],
    venue_stats_summary: dict = None,
    date_str: str = "",
) -> bool:
    """
    Playwright で note.com にログインし、記事を下書き保存する。
    
    必要な環境変数:
    - NOTE_EMAIL: note.com ログインメールアドレス
    - NOTE_PASSWORD: note.com パスワード
    
    Returns: 成功したら True
    """
    email = config.NOTE_EMAIL
    password = config.NOTE_PASSWORD

    if not email or not password:
        print("  [NOTE] SKIP: NOTE_EMAIL or NOTE_PASSWORD not set")
        return False

    if not chance_races:
        print("  [NOTE] SKIP: No chance races")
        return False

    if not date_str:
        date_str = datetime.now().strftime("%Y%m%d")

    if venue_stats_summary is None:
        venue_stats_summary = {}

    title, body = _build_article_content(chance_races, venue_stats_summary, date_str)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  [NOTE] SKIP: playwright not installed")
        return False

    print(f"  [NOTE] Saving draft to note.com...")
    print(f"  [NOTE] Title: {title}")

    success = False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()

            # ── note ログイン ──
            page.goto("https://note.com/login", wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(3000)

            # メールアドレス入力
            email_input = page.locator('input[name="login"]').or_(
                page.locator('input[type="email"]')
            ).or_(
                page.locator('input[placeholder*="メール"]')
            )
            email_input.wait_for(timeout=10000)
            email_input.fill(email)

            # パスワード入力
            pwd_input = page.locator('input[name="password"]').or_(
                page.locator('input[type="password"]')
            )
            pwd_input.wait_for(timeout=10000)
            pwd_input.fill(password)

            # ログインボタン
            login_btn = page.locator('button:has-text("ログイン")').or_(
                page.locator('button[type="submit"]')
            )
            login_btn.click()
            page.wait_for_timeout(5000)

            # ── 新規記事作成 ──
            page.goto("https://note.com/notes/new", wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(3000)

            # タイトル入力
            title_input = page.locator('textarea[placeholder*="タイトル"]').or_(
                page.locator('[class*="title"] textarea')
            ).or_(
                page.locator('textarea').first
            )
            title_input.wait_for(timeout=10000)
            title_input.fill(title)
            page.wait_for_timeout(1000)

            # 本文入力 (エディタエリア)
            editor = page.locator('[contenteditable="true"]').or_(
                page.locator('[class*="editor"]')
            ).or_(
                page.locator('[role="textbox"]')
            )
            editor.wait_for(timeout=10000)
            editor.click()

            # マークダウンの各行をペースト
            for line in body.split("\n"):
                page.keyboard.type(line, delay=5)
                page.keyboard.press("Enter")
            page.wait_for_timeout(2000)

            # ── 下書き保存 ──
            # note.com は自動保存されるが、明示的に下書き保存も可能
            save_btn = page.locator('button:has-text("下書き保存")').or_(
                page.locator('button:has-text("下書き")')
            )
            try:
                save_btn.wait_for(timeout=5000)
                save_btn.click()
                page.wait_for_timeout(3000)
                print("  [NOTE] Draft saved via button!")
            except Exception:
                # 自動保存に依存
                print("  [NOTE] Auto-saved as draft (no explicit save button found)")

            success = True
            browser.close()

    except Exception as e:
        print(f"  [NOTE] Error: {e}")

    return success


# ═══════════════════════════════════════════
#  3. メインエントリポイント
# ═══════════════════════════════════════════

def run_note_draft(
    chance_races: list[dict],
    venue_stats_summary: dict = None,
    date_str: str = "",
) -> bool:
    """note 下書き保存のエントリポイント"""
    return save_note_draft(chance_races, venue_stats_summary, date_str)


if __name__ == "__main__":
    # テスト用: コンテンツ生成のみ
    demo = [
        {
            "venue_name": "桐生",
            "race_no": 3,
            "boat1": {"name": "テスト選手", "national_rate": 3.85, "local_rate": 2.50},
            "boat1_win_prob": 0.32,
            "cond1": {"reason": "全国勝率3.85 < 4.5"},
            "cond2": {"reason": "ST偏差0.201 > 0.18"},
            "tickets": [
                {"trifecta": "2-3-4", "prob": 0.082, "amount": 12100, "kimarite": "makuri"},
            ],
        },
    ]
    title, body = _build_article_content(demo, {}, "20260218")
    print(f"Title: {title}\n")
    print(body)
