"""
BlitzBoat YouTube Shorts Generator
ffmpeg + Pillow で「大荒れ警報」動画を自動生成
"""
import os
import subprocess
import json
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

import config


def _get_font(size: int):
    """フォントを取得 (日本語対応)"""
    # Windows日本語フォントパス
    font_paths = [
        "C:/Windows/Fonts/meiryo.ttc",
        "C:/Windows/Fonts/msgothic.ttc",
        "C:/Windows/Fonts/YuGothM.ttc",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for path in font_paths:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def generate_alert_image(
    chance_race: dict,
    output_path: str = None,
) -> str:
    """
    「大荒れ警報」画像を生成。
    1080x1920 (9:16 縦) YouTube Shorts向け。
    
    Returns: 出力画像パス
    """
    if output_path is None:
        output_path = os.path.join(config.ASSETS_DIR, "alert_image.png")
    
    W, H = 1080, 1920
    img = Image.new("RGB", (W, H), "#0D0D1A")
    draw = ImageDraw.Draw(img)
    
    # ── 背景グラデーション効果 (上から赤→暗い) ──
    for y in range(H):
        r = int(max(0, 180 - y * 0.12))
        g = int(max(0, 30 - y * 0.02))
        b = int(max(0, 40 - y * 0.02))
        draw.line([(0, y), (W, y)], fill=(r, g, b))
    
    # ── フォント ──
    font_title = _get_font(72)
    font_large = _get_font(56)
    font_medium = _get_font(42)
    font_small = _get_font(32)
    font_tiny = _get_font(24)
    
    # ── 上部: 警報マーク ──
    y = 120
    draw.text((W // 2, y), "⚠️ 大荒れ警報 ⚠️", fill="#FF4444",
              font=font_title, anchor="mt")
    
    y += 100
    draw.text((W // 2, y), "BlitzBoat AI 分析", fill="#FFD700",
              font=font_medium, anchor="mt")
    
    # ── 日付 ──
    y += 80
    today = datetime.now().strftime("%Y年%m月%d日")
    draw.text((W // 2, y), today, fill="#AAAAAA",
              font=font_small, anchor="mt")
    
    # ── レース情報 ──
    boat1 = chance_race.get("boat1", {})
    venue_name = chance_race.get("venue_name", "")
    race_no = chance_race.get("race_no", 0)
    win_prob = chance_race.get("boat1_win_prob", 0)
    
    y += 120
    # 区切り線
    draw.line([(100, y), (W - 100, y)], fill="#FF4444", width=3)
    
    y += 60
    draw.text((W // 2, y), f"{venue_name} {race_no}R", fill="#FFFFFF",
              font=font_large, anchor="mt")
    
    y += 100
    draw.text((W // 2, y), "1号艇 崩壊予測", fill="#FF6B6B",
              font=font_medium, anchor="mt")
    
    y += 80
    draw.text((W // 2, y), f"勝率推定: {win_prob*100:.0f}%", fill="#FF4444",
              font=font_large, anchor="mt")
    
    # ── 条件表示 ──
    y += 120
    national = boat1.get("national_rate", 0)
    local = boat1.get("local_rate", 0)
    
    draw.text((120, y), f"全国勝率: {national:.2f}", fill="#CCCCCC",
              font=font_medium)
    y += 60
    draw.text((120, y), f"当地勝率: {local:.2f}", fill="#CCCCCC",
              font=font_medium)
    
    y += 80
    cond2 = chance_race.get("cond2", {})
    avg_st = cond2.get("avg_st", 0)
    if avg_st:
        draw.text((120, y), f"モーターST: {avg_st:.3f}s", fill="#FF8888",
                  font=font_medium)
    
    # ── 下部: CTA ──
    y += 200
    draw.line([(100, y), (W - 100, y)], fill="#FF4444", width=3)
    
    y += 60
    draw.text((W // 2, y), "📲 LINE登録で", fill="#00FF88",
              font=font_medium, anchor="mt")
    y += 60
    draw.text((W // 2, y), "毎朝の予想を無料配信!", fill="#00FF88",
              font=font_medium, anchor="mt")
    
    y += 100
    draw.text((W // 2, y), "🔗 プロフィールのリンクから", fill="#AAAAAA",
              font=font_small, anchor="mt")
    
    # ── BlitzBoat ロゴ ──
    draw.text((W // 2, H - 80), "Powered by BlitzBoat", fill="#666666",
              font=font_tiny, anchor="mt")
    
    img.save(output_path, "PNG")
    print(f"  [Shorts] 画像生成: {output_path}")
    return output_path


def generate_shorts_video(
    chance_race: dict,
    bgm_path: str = None,
    output_path: str = None,
    duration: int = 15,
) -> str:
    """
    ffmpegで画像+BGMからYouTube Shorts用動画を生成。
    
    Args:
        chance_race: チャンスレースデータ
        bgm_path: BGM音声ファイルパス (なければ無音)
        output_path: 出力MP4パス
        duration: 動画秒数 (default: 15)
        
    Returns: 出力動画パス
    """
    if output_path is None:
        today = datetime.now().strftime("%Y%m%d")
        venue = chance_race.get("venue_name", "unknown")
        race_no = chance_race.get("race_no", 0)
        output_path = os.path.join(config.ASSETS_DIR, f"shorts_{today}_{venue}_{race_no}R.mp4")
    
    # 画像生成
    image_path = generate_alert_image(chance_race)
    
    # ffmpegコマンド構築
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", image_path,
        "-t", str(duration),
        "-vf", "scale=1080:1920,format=yuv420p",
        "-r", "30",
    ]
    
    if bgm_path and os.path.exists(bgm_path):
        cmd.extend(["-i", bgm_path, "-shortest"])
        cmd.extend(["-c:a", "aac", "-b:a", "128k"])
    else:
        # 無音トラック
        cmd.extend(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"])
        cmd.extend(["-shortest", "-c:a", "aac"])
    
    cmd.extend([
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        output_path,
    ])
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            print(f"  [Shorts] 動画生成成功: {output_path}")
            return output_path
        else:
            print(f"  [Shorts] ffmpeg エラー: {result.stderr[:500]}")
            # ffmpegがない場合は画像のみ保存
            print(f"  [Shorts] 画像のみ保存: {image_path}")
            return image_path
    except FileNotFoundError:
        print("  [Shorts] ffmpegが見つかりません。画像のみ生成。")
        return image_path
    except subprocess.TimeoutExpired:
        print("  [Shorts] ffmpegタイムアウト。画像のみ保存。")
        return image_path


if __name__ == "__main__":
    test_race = {
        "venue_name": "桐生",
        "race_no": 5,
        "boat1_win_prob": 0.22,
        "boat1": {
            "name": "テスト選手",
            "national_rate": 3.8,
            "local_rate": 2.1,
        },
        "cond2": {"avg_st": 0.195},
    }
    
    generate_alert_image(test_race)
    print("画像生成完了")
