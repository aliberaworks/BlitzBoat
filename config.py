"""
BlitzBoat Configuration
会場コード、URLテンプレート、分析閾値
"""
import os
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # GitHub Actions / Vercel では env vars が直接注入されるため不要

# ── 会場コードマッピング ──
VENUE_CODES = {
    "01": "桐生", "02": "戸田", "03": "江戸川", "04": "平和島",
    "05": "多摩川", "06": "浜名湖", "07": "蒲郡", "08": "常滑",
    "09": "津",   "10": "三国", "11": "びわこ", "12": "住之江",
    "13": "尼崎", "14": "鳴門", "15": "丸亀",   "16": "児島",
    "17": "宮島", "18": "徳山", "19": "下関",   "20": "若松",
    "21": "芦屋", "22": "福岡", "23": "唐津",   "24": "大村",
}

# ── URLテンプレート ──
BASE_URL = "https://boatrace.jp/owpc/pc/race"
URL_RACE_INDEX   = f"{BASE_URL}/raceindex?jcd={{jcd}}&hd={{hd}}"
URL_RACELIST     = f"{BASE_URL}/racelist?rno={{rno}}&jcd={{jcd}}&hd={{hd}}"
URL_BEFOREINFO   = f"{BASE_URL}/beforeinfo?rno={{rno}}&jcd={{jcd}}&hd={{hd}}"
URL_RACE_RESULT  = f"{BASE_URL}/raceresult?rno={{rno}}&jcd={{jcd}}&hd={{hd}}"
URL_ODDS_3T      = f"{BASE_URL}/odds3t?rno={{rno}}&jcd={{jcd}}&hd={{hd}}"

# ── 分析閾値 ──
# ── 分析閾値 ──
# Condition 1: 1号艇負け判定 (Tier分類に使用)
NATIONAL_RATE_THRESHOLD = 4.5        # 全国勝率 < この値
RATE_DIFF_THRESHOLD     = 1.5        # 全国勝率 - 当地勝率 >= この値 (Tier 3)
BOAT1_WIN_RATE_TIER1    = 0.40       # Tier 1: 勝率40%以下 (大幅に低い)
BOAT1_WIN_RATE_TIER2    = 0.50       # Tier 2: 勝率50%以下 (低い)

# Condition 2: ST凹み判定
ST_SLOW_THRESHOLD = 0.18             # motor_avg_st + st_std > この値

# ── 累積確率フィルタ ──
CUMULATIVE_PROB_CUTOFF = 0.97        # 上位97%まで選定

# ── 資金配分 ──
TOTAL_BUDGET     = 10_000            # 合計金額（Kelly基準の元手）
MIN_BET_UNIT     = 100               # 最小単位

# ── スクレイピング設定 ──
REQUEST_DELAY    = 1.5               # リクエスト間隔(秒)
MAX_RETRIES      = 3                 # 最大リトライ回数
REQUEST_TIMEOUT  = 15                # タイムアウト(秒)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# ── データディレクトリ ──
DATA_DIR       = os.path.join(os.path.dirname(__file__), "data")
PROGRESS_FILE  = os.path.join(DATA_DIR, "progress.json")
RESULTS_FILE   = os.path.join(DATA_DIR, "race_results.json")
STATS_FILE     = os.path.join(DATA_DIR, "venue_stats.json")
DAILY_DIR      = os.path.join(DATA_DIR, "daily")
ASSETS_DIR     = os.path.join(os.path.dirname(__file__), "assets")

# ── LINE設定 ──
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_USER_ID              = os.getenv("LINE_USER_ID", "")

# ── Vercel認証 ──
AUTH_SECRET = os.getenv("AUTH_SECRET", "blitzboat2026")

# ── X (Twitter) 自動投稿 ──
X_USERNAME = os.getenv("X_USERNAME", "")
X_PASSWORD = os.getenv("X_PASSWORD", "")

# ── Gumroad API & Passwords ──
GUMROAD_ACCESS_TOKEN = os.getenv("GUMROAD_ACCESS_TOKEN", "")
GUMROAD_PRODUCT_ID   = os.getenv("GUMROAD_PRODUCT_ID", "")

# ── Email Notification (SMTP) ──
OWNER_EMAIL = os.getenv("OWNER_EMAIL", "")
SMTP_HOST   = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT   = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER   = os.getenv("SMTP_USER", "")
SMTP_PASS   = os.getenv("SMTP_PASS", "")

# ── アフィリエイト ──
AFFILIATE_URL = os.getenv("AFFILIATE_URL", "")

# 対象決まり手 (基本定義)
# 実際のフィルタリングは statistics_engine.py で動的に行う
ALLOWED_KIMARITE = [
    "まくり",      # 2号艇+
    "まくり差し",   # 3号艇+
    "差し",        # 状況により除外
]

# 対象決まり手×艇番の組み合わせ
ALLOWED_KIMARITE_BOATS = {
    2: ["まくり", "差し"],
    3: ["まくり", "まくり差し", "差し"],
    4: ["まくり", "まくり差し", "差し"],
    5: ["まくり", "まくり差し", "差し"],
    6: ["まくり", "まくり差し", "差し"],
}

# 過去データ収集期間(日数)
COLLECTION_DAYS = 180  # 約6ヶ月

# ── ディレクトリ自動作成 ──
for d in [DATA_DIR, DAILY_DIR, ASSETS_DIR]:
    os.makedirs(d, exist_ok=True)
