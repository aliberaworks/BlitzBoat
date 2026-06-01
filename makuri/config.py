"""
makuri - 設定ファイル
"""
import os

# ── LINE ─────────────────────────────────────────────────────────
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_USER_ID              = os.environ.get("LINE_USER_ID", "")

# ── パス ─────────────────────────────────────────────────────────
BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
DATA_DIR         = os.path.join(BASE_DIR, "data")
BLITZBOAT_DIR    = os.path.join(BASE_DIR, "..", "BlitzBoat")

# BlitzBoatのモデル・データを参照
PLAYER_STATS_JSON      = os.path.join(BLITZBOAT_DIR, "data", "player_stats.json")
MODEL_ARARE_PKL        = os.path.join(BLITZBOAT_DIR, "data", "model_arare.pkl")
MODEL_ARARE_META_JSON  = os.path.join(BLITZBOAT_DIR, "data", "model_arare_meta.json")
MODEL_MAKKURI_3_PKL    = os.path.join(BLITZBOAT_DIR, "data", "model_makkuri_3.pkl")
MODEL_MAKKURI_4_PKL    = os.path.join(BLITZBOAT_DIR, "data", "model_makkuri_4.pkl")
MODEL_MAKKURI_META_JSON= os.path.join(BLITZBOAT_DIR, "data", "model_makkuri_meta.json")
MODEL_META_JSON        = os.path.join(BLITZBOAT_DIR, "data", "model_meta.json")

# makuri独自データ
TRIFECTA_STATS_JSON    = os.path.join(DATA_DIR, "trifecta_stats.json")
URA_SUJI_STATS_JSON    = os.path.join(DATA_DIR, "ura_suji_stats.json")
ACCURACY_LOG_JSON      = os.path.join(DATA_DIR, "accuracy_log.json")

# ── 閾値（通知レイヤー別） ──────────────────────────────────────
# Layer 1: 荒れ警報（ビジネス向け）
ARARE_THRESH_L1        = 0.60   # arare_prob >= これでLayer1通知

# Layer 2: まくり成功シナリオ
MAKKURI_TRY_THRESH     = 0.30   # p_try >= これでまくり試行と判定
MAKKURI_SUCCESS_THRESH = 0.55   # p_success >= これで成功シナリオ優先

# Layer 3: 裏スジ（まくり失敗シナリオ）
MAKKURI_FAIL_THRESH    = 0.40   # p_fail >= これで裏スジ通知

# ── 買い目点数 ────────────────────────────────────────────────────
# 分析結果より: まくり成功シナリオは4点が期待利益最大
MAKKURI_SUCCESS_COMBOS = 4   # まくり成功の買い目点数
MAKKURI_FAIL_COMBOS    = 4   # 裏スジの買い目点数（独自テーブル使用）

# ── LINE通知制限 ──────────────────────────────────────────────────
MAX_DAILY_NOTIFY_L1    = 3   # 荒れ警報の1日最大送信数
MAX_DAILY_NOTIFY_L2    = 4   # まくり予測の1日最大送信数
MAX_DAILY_NOTIFY_L3    = 2   # 裏スジの1日最大送信数

# ── 会場コード ────────────────────────────────────────────────────
VENUE_CODES = {
    "01":"桐生","02":"戸田","03":"江戸川","04":"平和島","05":"多摩川",
    "06":"浜名湖","07":"蒲郡","08":"常滑","09":"津","10":"三国",
    "11":"琵琶湖","12":"住之江","13":"尼崎","14":"鳴門","15":"丸亀",
    "16":"児島","17":"宮島","18":"徳山","19":"下関","20":"若松",
    "21":"芦屋","22":"福岡","23":"唐津","24":"大村",
}

USER_AGENT      = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
REQUEST_DELAY   = 0.5
REQUEST_TIMEOUT = 15
MAX_RETRIES     = 3
