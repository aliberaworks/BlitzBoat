"""ScenarioBoat 設定（BlitzBoat/scenario_boat/ 配置版）"""
import os

SCENARIO_DIR = os.path.dirname(os.path.abspath(__file__))
BLITZBOAT_DIR = os.path.dirname(SCENARIO_DIR)   # BlitzBoat/
DATA_DIR = os.path.join(SCENARIO_DIR, "data")

# モデル・統計ファイル
MODEL_PKL   = os.path.join(DATA_DIR, "model_scenario.pkl")
STATS_JSON  = os.path.join(DATA_DIR, "scenario_stats.json")
META_JSON   = os.path.join(DATA_DIR, "model_scenario_meta.json")

# 日次データ（GitHubActionsがコミットする）
def today_json(hd: str):  return os.path.join(DATA_DIR, f"scenario_today_{hd}.json")
def results_json(hd: str):return os.path.join(DATA_DIR, f"scenario_results_{hd}.json")
ACCURACY_LOG = os.path.join(DATA_DIR, "scenario_accuracy_log.json")

# シナリオ定義
SCENARIOS = [
    "3mak", "4mak", "2mak", "56mak",
    "3fail", "4fail", "34fail",
    "1nige", "sashi", "other",
]
MAKKURI_KM = {"まくり", "まくり差し"}

# 買い目設定
EV_THRESH        = 0.3   # EV閾値
MAX_BETS_PER_RACE = 3    # 1レースの最大買い目数
MAX_DAILY_NOTIFY  = 4    # 1日のLINE送信上限

# まくり試行プロキシ（ラベル生成・スコアリング共通）
ATTEMPT_ST_RANK   = 2
ATTEMPT_MOTOR_MIN = 25.0
ATTEMPT_AVG_ST    = 0.185
FAIL_RANK_MIN     = 4

BOATS    = list(range(1, 7))
BASE_COLS   = ["avg_st", "motor_2rate", "national_rate", "local_rate"]
BUBBLE_COLS = ["qualify", "bubble", "points_before"]
GRADE_MAP   = {"A1": 4, "A2": 3, "B1": 2, "B2": 1}
VENUE_CODES = [f"{i:02d}" for i in range(1, 25)]
