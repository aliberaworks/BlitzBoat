"""
BlitzBoat — X (Twitter) 自動投稿モジュール
Playwright で X にログインし、Tier 1 サマリー画像を投稿する。
GitHub Actions 上で headless Chromium として動作。
"""
import os
import re
import sys
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

import config


# ═══════════════════════════════════════════
#  1. サマリー画像生成 (Pillow)
# ═══════════════════════════════════════════

# カラーパレット (ダークテーマ)
BG_COLOR = (15, 17, 26)
CARD_BG = (26, 28, 42)
ACCENT = (59, 130, 246)      # Blue-500
ACCENT_GLOW = (99, 160, 255) # Lighter blue
TEXT_WHITE = (240, 240, 250)
TEXT_GRAY = (160, 165, 180)
TIER1_RED = (239, 68, 68)
TIER2_YELLOW = (234, 179, 8)
GOLD = (255, 215, 0)
DIVIDER = (50, 55, 75)

IMG_W, IMG_H = 1200, 675  # 16:9


def _get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """フォント取得 (フォールバック付き)"""
    font_paths = [
        "C:/Windows/Fonts/meiryo.ttc",
        "C:/Windows/Fonts/msgothic.ttc",
        "C:/Windows/Fonts/YuGothR.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    ]
    if bold:
        bold_paths = [
            "C:/Windows/Fonts/meiryob.ttc",
            "C:/Windows/Fonts/YuGothB.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        ]
        font_paths = bold_paths + font_paths

    for fp in font_paths:
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                continue
    return ImageFont.load_default()


def generate_summary_image(
    chance_races: list[dict],
    date_str: str = "",
) -> str:
    """
    Tier 1 チャンスレースのサマリー画像を生成。
    Returns: 画像ファイルパス
    """
    if not date_str:
        date_str = datetime.now().strftime("%Y/%m/%d")
    else:
        # YYYYMMDD → YYYY/MM/DD
        date_str = f"{date_str[:4]}/{date_str[4:6]}/{date_str[6:]}"

    img = Image.new("RGB", (IMG_W, IMG_H), BG_COLOR)
    draw = ImageDraw.Draw(img)

    font_title = _get_font(42, bold=True)
    font_sub = _get_font(24)
    font_body = _get_font(22)
    font_small = _get_font(18)
    font_big = _get_font(56, bold=True)

    y = 30

    # ── ヘッダー背景 ──
    draw.rectangle([(0, 0), (IMG_W, 110)], fill=(20, 22, 36))
    draw.line([(0, 110), (IMG_W, 110)], fill=ACCENT, width=3)

    # ── タイトル ──
    draw.text((40, 20), "BlitzBoat", fill=ACCENT_GLOW, font=font_title)
    draw.text((330, 30), f"Statistical Alert  |  {date_str}", fill=TEXT_GRAY, font=font_sub)

    # ── ロゴアクセント ──
    draw.ellipse([(IMG_W - 90, 25), (IMG_W - 30, 85)], fill=ACCENT, outline=ACCENT_GLOW)
    draw.text((IMG_W - 78, 37), "BB", fill=TEXT_WHITE, font=font_sub)

    y = 130

    if not chance_races:
        draw.text((IMG_W // 2 - 200, IMG_H // 2 - 30),
                  "本日のチャンスレースはありません",
                  fill=TEXT_GRAY, font=font_sub)
    else:
        # ── チャンスレース数 ──
        n = len(chance_races)
        draw.text((40, y), "Tier 1 Chance Race", fill=TIER1_RED, font=font_sub)
        draw.text((320, y - 8), str(n), fill=GOLD, font=font_big)
        draw.text((370, y + 8), "件検出", fill=TEXT_GRAY, font=font_sub)
        y += 80

        draw.line([(30, y), (IMG_W - 30, y)], fill=DIVIDER, width=1)
        y += 20

        # ── 各レース情報 (最大4件) ──
        for i, cr in enumerate(chance_races[:4]):
            venue_name = cr.get("venue_name", "")
            race_no = cr.get("race_no", 0)
            boat1 = cr.get("boat1", {})
            prob = cr.get("boat1_win_prob", 0.0)

            # カード背景
            card_y = y
            draw.rounded_rectangle(
                [(30, card_y), (IMG_W - 30, card_y + 90)],
                radius=10, fill=CARD_BG
            )

            # レースラベル
            label_color = TIER1_RED if prob <= 0.40 else TIER2_YELLOW
            draw.rounded_rectangle(
                [(50, card_y + 15), (130, card_y + 55)],
                radius=6, fill=label_color
            )
            draw.text((55, card_y + 20), f"{race_no}R", fill=TEXT_WHITE, font=font_sub)

            # 会場名
            draw.text((150, card_y + 18), venue_name, fill=TEXT_WHITE, font=font_body)

            # 1号艇情報
            b1_name = boat1.get("name", "")
            b1_rate = boat1.get("national_rate", 0.0)
            info_text = f"1号艇: {b1_name}  勝率{b1_rate:.2f}"
            draw.text((150, card_y + 50), info_text, fill=TEXT_GRAY, font=font_small)

            # 勝率
            prob_text = f"{prob*100:.0f}%"
            draw.text((IMG_W - 180, card_y + 22), prob_text, fill=label_color, font=font_title)

            y = card_y + 105

    # ── フッター ──
    draw.rectangle([(0, IMG_H - 50), (IMG_W, IMG_H)], fill=(20, 22, 36))
    draw.line([(0, IMG_H - 50), (IMG_W, IMG_H - 50)], fill=DIVIDER, width=1)
    draw.text((40, IMG_H - 40), "#競艇 #ボートレース #BlitzBoat",
              fill=TEXT_GRAY, font=font_small)
    draw.text((IMG_W - 300, IMG_H - 40),
              "Powered by Statistical Analysis",
              fill=(80, 85, 100), font=font_small)

    # 保存
    os.makedirs(config.ASSETS_DIR, exist_ok=True)
    date_flat = date_str.replace("/", "")
    output_path = os.path.join(config.ASSETS_DIR, f"summary_{date_flat}.png")
    img.save(output_path, "PNG", quality=95)
    print(f"  [X_POSTER] Summary image: {output_path}")
    return output_path


# ═══════════════════════════════════════════
#  2. X (Twitter) 自動投稿 (Playwright)
# ═══════════════════════════════════════════

def post_to_x(
    image_path: str,
    chance_races: list[dict],
    date_str: str = "",
) -> bool:
    """
    Playwright で X にログインし、サマリー画像付きツイートを投稿。
    
    必要な環境変数:
    - X_USERNAME: X ユーザー名 or メールアドレス
    - X_PASSWORD: X パスワード
    
    Returns: 成功したら True
    """
    username = config.X_USERNAME
    password = config.X_PASSWORD

    if not username or not password:
        print("  [X_POSTER] SKIP: X_USERNAME or X_PASSWORD not set")
        return False

    if not os.path.exists(image_path):
        print(f"  [X_POSTER] SKIP: Image not found: {image_path}")
        return False

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  [X_POSTER] SKIP: playwright not installed")
        return False

    if not date_str:
        date_str = datetime.now().strftime("%Y/%m/%d")

    n = len(chance_races)
    tweet_text = (
        f"【BlitzBoat 統計アラート】{date_str}\n\n"
        f"本日のTier 1チャンスレース: {n}件検出\n"
    )
    # 上位2件のレース情報を追加
    for cr in chance_races[:2]:
        venue = cr.get("venue_name", "")
        rno = cr.get("race_no", 0)
        prob = cr.get("boat1_win_prob", 0.0)
        tweet_text += f"  {venue} {rno}R (1号艇勝率 {prob*100:.0f}%)\n"

    tweet_text += "\n#競艇 #ボートレース #BlitzBoat"

    print(f"  [X_POSTER] Posting to X...")
    print(f"  [X_POSTER] Text: {tweet_text[:80]}...")

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

            # ── X ログイン ──
            page.goto("https://x.com/i/flow/login", wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(3000)

            # ユーザー名入力
            username_input = page.locator('input[autocomplete="username"]')
            username_input.wait_for(timeout=10000)
            username_input.fill(username)
            page.locator('text="次へ"').or_(page.locator('text="Next"')).click()
            page.wait_for_timeout(2000)

            # パスワード入力
            password_input = page.locator('input[type="password"]')
            password_input.wait_for(timeout=10000)
            password_input.fill(password)
            page.locator('text="ログイン"').or_(page.locator('text="Log in"')).click()
            page.wait_for_timeout(5000)

            # ── ツイート投稿 ──
            page.goto("https://x.com/compose/post", wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(3000)

            # テキスト入力
            editor = page.locator('[data-testid="tweetTextarea_0"]')
            editor.wait_for(timeout=10000)
            editor.click()
            page.keyboard.type(tweet_text, delay=20)
            page.wait_for_timeout(1000)

            # 画像添付
            file_input = page.locator('input[type="file"][accept*="image"]')
            file_input.set_input_files(image_path)
            page.wait_for_timeout(3000)

            # 投稿ボタン
            post_btn = page.locator('[data-testid="tweetButton"]')
            post_btn.wait_for(timeout=10000)
            post_btn.click()
            page.wait_for_timeout(5000)

            print("  [X_POSTER] Tweet posted successfully!")
            success = True

            browser.close()

    except Exception as e:
        print(f"  [X_POSTER] Error: {e}")

    return success


# ═══════════════════════════════════════════
#  3. メインエントリポイント
# ═══════════════════════════════════════════

def run_x_post(chance_races: list[dict], date_str: str = "") -> bool:
    """
    サマリー画像を生成し、X に投稿する。
    Returns: 成功したら True
    """
    if not chance_races:
        print("  [X_POSTER] No chance races to post")
        return False

    # 1. 画像生成
    image_path = generate_summary_image(chance_races, date_str)

    # 2. X 投稿
    return post_to_x(image_path, chance_races, date_str)


if __name__ == "__main__":
    # テスト用: デモデータで画像生成
    demo_races = [
        {
            "venue_name": "桐生",
            "race_no": 3,
            "boat1": {"name": "テスト選手A", "national_rate": 3.85},
            "boat1_win_prob": 0.32,
        },
        {
            "venue_name": "住之江",
            "race_no": 7,
            "boat1": {"name": "テスト選手B", "national_rate": 4.12},
            "boat1_win_prob": 0.38,
        },
    ]
    img = generate_summary_image(demo_races, "20260218")
    print(f"Demo image saved: {img}")
