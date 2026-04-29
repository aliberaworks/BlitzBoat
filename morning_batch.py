"""
ボートレース朝バッチ - 本日全レース一括予測・信頼度ランキング

実行:
  python morning_batch.py          # 本日の全開催レース
  python morning_batch.py --top 20 # 上位20件だけ表示
  python morning_batch.py --min-conf 0.10  # 信頼度0.10以上だけ表示

出力:
  コンソール: 信頼度ランキング表
  data/today_YYYYMMDD.json: 全予測結果（Streamlit連携用）
"""

import argparse
import json
import os
import pickle
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime

import numpy as np

import importlib.util as _ilu

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path = [_HERE] + [p for p in sys.path if p != _HERE]

import config
from bubble_today import compute_bubble_status, save_bubble_today, get_race_bubble_today

_scraper_spec = _ilu.spec_from_file_location("scraper", os.path.join(_HERE, "scraper.py"))
_scraper_mod  = _ilu.module_from_spec(_scraper_spec)
_scraper_spec.loader.exec_module(_scraper_mod)
scrape_race_times    = _scraper_mod.scrape_race_times
scrape_racelist      = _scraper_mod.scrape_racelist
scrape_today_venues  = _scraper_mod.scrape_today_venues

# ── 定数 ──────────────────────────────────────────────────────────────────────
BOATS       = list(range(1, 7))
KIMARITE    = ["逃げ", "差し", "まくり", "まくり差し", "抜き", "恵まれ"]
BASE_COLS   = ["avg_st", "motor_2rate", "national_rate", "local_rate"]
BUBBLE_COLS = ["qualify", "bubble", "points_before"]
VENUE_CODES = sorted(config.VENUE_CODES.keys())
GRADE_MAP   = {"A1": 4, "A2": 3, "B1": 2, "B2": 1}
UNIFORM    = 1.0 / 6  # ランダムベースライン

MODEL_BOAT = os.path.join(config.DATA_DIR, "model_boat.pkl")
MODEL_KM   = os.path.join(config.DATA_DIR, "model_km.pkl")
MODEL_META = os.path.join(config.DATA_DIR, "model_meta.json")

BOAT_MARK = ["①白", "②黒", "③赤", "④青", "⑤黄", "⑥緑"]
KM_SHORT  = {"逃げ": "逃", "差し": "差", "まくり": "捲", "まくり差し": "捲差", "抜き": "抜", "恵まれ": "恵"}

MAX_WORKERS = 12


# ── モデルロード ─────────────────────────────────────────────────────────────
def load_models():
    for p in [MODEL_BOAT, MODEL_KM, MODEL_META]:
        if not os.path.exists(p):
            print(f"[ERROR] モデルが見つかりません: {p}")
            print("  先に python train_model.py を実行してください。")
            sys.exit(1)
    with open(MODEL_BOAT, "rb") as f:
        clf_boat = pickle.load(f)
    with open(MODEL_KM, "rb") as f:
        clf_km = pickle.load(f)
    with open(MODEL_META, encoding="utf-8") as f:
        meta = json.load(f)
    return clf_boat, clf_km, meta


# ── 特徴量ベクトル構築 ────────────────────────────────────────────────────────
def build_feature_vector(
    venue: str,
    boat_data: dict,
    day_from_start: int = 0,
    total_days: int = 0,
    bubble_status: dict | None = None,
) -> np.ndarray:
    """
    93次元特徴量ベクトルを構築する。
    boat_data[i]: {"avg_st", "motor_2rate", "national_rate", "local_rate", "grade"(任意)}
    bubble_status: {boat_no: {"qualify": 0/1, "bubble": 0/1, "points_before": float}}
    当日データがない場合は 0 埋め。
    """
    abs_vals = {f"b{i}_{col}": boat_data[i].get(col, 0.0)
                for col in BASE_COLS for i in BOATS}

    rank_cols = ["avg_st", "motor_2rate", "national_rate"]
    rank_vals = {}
    for col in rank_cols:
        vals = [abs_vals[f"b{i}_{col}"] for i in BOATS]
        ascending = (col == "avg_st")
        sp = sorted(enumerate(vals, 1), key=lambda x: x[1], reverse=not ascending)
        ranks = {b: r + 1 for r, (b, _) in enumerate(sp)}
        for i in BOATS:
            rank_vals[f"b{i}_{col}_rank"] = float(ranks[i])

    venue_vec = {f"venue_{v}": 1.0 if v == venue else 0.0 for v in VENUE_CODES}

    is_final     = float(day_from_start > 0 and total_days > 0 and day_from_start == total_days)
    series_prog  = float(day_from_start / total_days) if total_days > 0 else 0.0

    bs = bubble_status or {}
    bubble_vals = {}
    for col in BUBBLE_COLS:
        for i in BOATS:
            bubble_vals[f"b{i}_{col}"] = float((bs.get(i) or {}).get(col, 0))

    grade_nums = {i: float(GRADE_MAP.get(boat_data[i].get("grade", ""), 0)) for i in BOATS}
    a1_count    = float(sum(1 for i in BOATS if grade_nums[i] == 4.0))
    a1_in_boat1 = float(grade_nums[1] == 4.0)

    feat = (
        [abs_vals[f"b{i}_{col}"] for col in BASE_COLS for i in BOATS]
        + [rank_vals[f"b{i}_{col}_rank"] for col in rank_cols for i in BOATS]
        + [venue_vec[f"venue_{v}"] for v in VENUE_CODES]
        + [float(day_from_start), is_final, series_prog]
        + [bubble_vals[f"b{i}_{col}"] for col in BUBBLE_COLS for i in BOATS]
        + [grade_nums[i] for i in BOATS]
        + [a1_count, a1_in_boat1]
    )
    return np.array(feat, dtype=np.float32).reshape(1, -1)


# ── 1レース予測 ───────────────────────────────────────────────────────────────
def predict_race(venue: str, boat_data: dict, clf_boat, clf_km, km_by_boat: dict,
                 day_from_start: int = 0, total_days: int = 0,
                 bubble_status: dict | None = None) -> dict:
    X = build_feature_vector(venue, boat_data,
                             day_from_start=day_from_start, total_days=total_days,
                             bubble_status=bubble_status)
    boat_prob = {int(c): float(p)
                 for c, p in zip(clf_boat.classes_, clf_boat.predict_proba(X)[0])}
    km_prob   = {c: float(p)
                 for c, p in zip(clf_km.classes_, clf_km.predict_proba(X)[0])}

    top_boat = max(boat_prob, key=boat_prob.get)

    # 決まり手は「勝利艇ごとの条件付き確率」の最頻値を使う
    # （独立モデルは艇番との相関を無視するため不正確）
    conditional = km_by_boat.get(str(top_boat), {})
    top_km = conditional.get("_modal") or max(km_prob, key=km_prob.get)

    confidence = boat_prob[top_boat] - UNIFORM
    km_prob_for_winner = conditional.get(top_km, km_prob.get(top_km, 0.0))

    return {
        "top_boat":    top_boat,
        "top_km":      top_km,
        "boat_prob":   boat_prob,
        "km_prob":     km_prob,
        "confidence":  round(confidence, 4),
        "top_combo_p": round(boat_prob[top_boat] * km_prob_for_winner, 4),
    }


# ── 1会場の全レースをスクレイプ→予測 ─────────────────────────────────────────
def fetch_and_predict_venue(
    jcd: str, venue_name: str, hd: str, n_races: int,
    clf_boat, clf_km, km_by_boat: dict,
    bubble_data: dict | None = None,
) -> list[dict]:
    race_times = scrape_race_times(jcd, hd)

    # bubble_today.json があれば読み込む（なければ 0 埋め）
    day_from_start = 0
    total_days     = 0
    if bubble_data:
        day_from_start = bubble_data.get("day_from_start", 0)
        total_days     = bubble_data.get("total_days", 0)

    results = []
    for rno in range(1, n_races + 1):
        try:
            entries = scrape_racelist(jcd, hd, rno)
            if not entries or len(entries) < 6:
                continue

            boat_data = {}
            for e in entries:
                i = e["boat"]
                boat_data[i] = {
                    "avg_st":        float(e.get("avg_st") or 0.17),
                    "motor_2rate":   float(e.get("motor_2rate") or 38.0),
                    "national_rate": float(e.get("national_rate") or 5.0),
                    "local_rate":    float(e.get("local_rate") or 5.0),
                    "grade":         e.get("grade", ""),
                }

            # bubble ステータスを各艇データに変換
            bubble_status = None
            if bubble_data:
                race_boats = bubble_data.get("races", {}).get(str(rno), {})
                if race_boats:
                    bubble_status = {
                        int(b): {
                            "qualify":        v.get("qualify", 0),
                            "bubble":         v.get("bubble", 0),
                            "points_before":  v.get("points_before", 0.0),
                        }
                        for b, v in race_boats.items()
                    }

            pred = predict_race(jcd, boat_data, clf_boat, clf_km, km_by_boat,
                                day_from_start=day_from_start, total_days=total_days,
                                bubble_status=bubble_status)
            results.append({
                "jcd":        jcd,
                "venue_name": venue_name,
                "race_no":    rno,
                "race_time":  race_times.get(rno, ""),
                "boat_data":  boat_data,
                **pred,
            })
        except Exception as e:
            print(f"  [WARN] {venue_name} {rno}R: {e}", file=sys.stderr)

    return results


# ── メイン ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="朝バッチ: 本日全レース予測")
    parser.add_argument("--top",      type=int,   default=168, help="上位N件表示 (default: 全件)")
    parser.add_argument("--min-conf", type=float, default=0.0, help="最低信頼度フィルタ (default: 0.0)")
    parser.add_argument("--date",     type=str,   default=None, help="日付 YYYYMMDD (default: 今日)")
    args = parser.parse_args()

    hd = args.date or date.today().strftime("%Y%m%d")
    print(f"\n=== ボートレース朝バッチ  {hd[:4]}/{hd[4:6]}/{hd[6:]} ===\n")

    # モデルロード
    clf_boat, clf_km, meta = load_models()
    km_by_boat = meta.get("km_by_boat", {})
    print(f"モデル読み込み完了 (精度: boat {meta['boat_eval']['accuracy']*100:.1f}% / Top3 {meta['boat_eval']['top3_accuracy']*100:.1f}%)")

    # 本日の開催会場取得
    print("本日の開催会場を取得中...")
    venues_raw = scrape_today_venues(hd)
    if not venues_raw:
        print("[ERROR] 開催会場を取得できませんでした。日付・ネット接続を確認してください。")
        sys.exit(1)

    venues = {v["jcd"]: (v["name"], v.get("races", 12)) for v in venues_raw}
    print(f"  → {len(venues)} 会場 / 最大 {sum(r for _, r in venues.values())} レース\n")

    # 節内ステータスを事前取得（--skip-bubble で省略可）
    bubble_map: dict[str, dict] = {}
    try:
        print("節内ステータスを取得中（各会場の出走表をスクレイプ）...")
        for jcd, (name, _) in venues.items():
            try:
                from bubble_today import compute_bubble_status, save_bubble_today, load_bubble_today
                # キャッシュがあれば再利用
                cached = load_bubble_today(jcd, hd)
                if cached:
                    bubble_map[jcd] = cached
                    print(f"  [{name}] キャッシュ利用 ({cached['day_from_start']}日目)")
                else:
                    data = compute_bubble_status(jcd, hd)
                    if data:
                        save_bubble_today(data)
                        bubble_map[jcd] = data
            except Exception as be:
                print(f"  [{name}] bubble取得スキップ: {be}", file=sys.stderr)
        print(f"  bubble取得完了: {len(bubble_map)}/{len(venues)} 会場\n")
    except Exception as e:
        print(f"  [WARN] bubble取得に失敗、0埋めで継続: {e}", file=sys.stderr)

    # 並列スクレイプ + 予測
    all_results = []
    done = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {
            ex.submit(fetch_and_predict_venue, jcd, name, hd, n_races, clf_boat, clf_km, km_by_boat,
                      bubble_map.get(jcd)): jcd
            for jcd, (name, n_races) in venues.items()
        }
        for fut in as_completed(futures):
            jcd = futures[fut]
            name, _ = venues[jcd]
            try:
                rows = fut.result()
                all_results.extend(rows)
                done += 1
                print(f"  [{done:2}/{len(venues)}] {name}  {len(rows)}R 完了")
            except Exception as e:
                print(f"  [ERROR] {name}: {e}", file=sys.stderr)

    # 信頼度降順ソート
    all_results.sort(key=lambda x: x["confidence"], reverse=True)

    # フィルタ
    filtered = [r for r in all_results if r["confidence"] >= args.min_conf]

    # ── コンソール出力 ──────────────────────────────────────────────────────
    top_n = filtered[:args.top]
    total = len(all_results)

    print(f"\n{'─'*76}")
    print(f"  予測完了: {total} レース  |  表示: 上位{len(top_n)}件")
    print(f"{'─'*76}")
    print(f"  {'順位':>3}  {'会場':6}  {'R':>2}  {'時刻':>5}  {'本命艇':4}  {'決まり手':6}  {'信頼度':>6}  {'本命確率':>6}")
    print(f"{'─'*76}")

    for rank, r in enumerate(top_n, 1):
        b    = r["top_boat"]
        km   = r["top_km"]
        conf = r["confidence"]
        prob = r["boat_prob"][b]
        t    = r.get("race_time", "")

        stars = "★" * min(int(conf / 0.05), 5) + "☆" * (5 - min(int(conf / 0.05), 5))

        print(
            f"  {rank:3}位  {r['venue_name']:6}  {r['race_no']:2}R  "
            f"{t:>5}  {BOAT_MARK[b-1]}号艇  {KM_SHORT.get(km, km):4}  "
            f"{stars}  {prob*100:5.1f}%"
        )

    print(f"{'─'*76}")
    print(f"  信頼度 = 本命艇確率 - 1/6 (均等ベースライン 16.7%)")
    print(f"  ★5: 25%+  ★4: 20%+  ★3: 15%+  ★2: 10%+  ★1: 5%+")

    # ── JSON保存 ──────────────────────────────────────────────────────────────
    out_path = os.path.join(config.DATA_DIR, f"today_{hd}.json")
    save_data = {
        "date":         hd,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_races":  total,
        "model_info":   meta["boat_eval"],
        "predictions":  all_results,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)

    print(f"\n保存: {out_path}")
    print(f"次: python morning_batch.py --top 10 --min-conf 0.08  (絞り込み例)")


if __name__ == "__main__":
    main()
