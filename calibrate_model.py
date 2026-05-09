"""
確率キャリブレーション
RandomForestの確率を艇番ごとにIsotonic Regressionで補正し
data/calibration_boat.pkl に保存する
"""
import json, os, pickle
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

os.chdir(os.path.dirname(os.path.abspath(__file__)))
import config

BOATS       = list(range(1, 7))
BASE_COLS   = ["avg_st", "motor_2rate", "national_rate", "local_rate"]
VENUE_CODES = sorted(config.VENUE_CODES.keys())
BUBBLE_COLS = ["qualify", "bubble", "points_before"]
GRADE_MAP   = {"A1": 4, "A2": 3, "B1": 2, "B2": 1}

def build_X(df):
    rows = []
    for _, r in df.iterrows():
        venue = str(r["venue_code"]).zfill(2)
        boat_data = {i: {
            "avg_st":        r[f"b{i}_avg_st"],
            "motor_2rate":   r[f"b{i}_motor_2rate"],
            "national_rate": r[f"b{i}_national_rate"],
            "local_rate":    r[f"b{i}_local_rate"],
            "grade":         r.get(f"b{i}_grade", ""),
        } for i in BOATS}
        abs_vals = {f"b{i}_{c}": boat_data[i].get(c, 0.0) for c in BASE_COLS for i in BOATS}
        rank_cols = ["avg_st", "motor_2rate", "national_rate"]
        rank_vals = {}
        for col in rank_cols:
            vals = [abs_vals[f"b{i}_{col}"] for i in BOATS]
            asc = (col == "avg_st")
            sp = sorted(enumerate(vals, 1), key=lambda x: x[1], reverse=not asc)
            rks = {b: rk + 1 for rk, (b, _) in enumerate(sp)}
            for i in BOATS:
                rank_vals[f"b{i}_{col}_rank"] = float(rks[i])
        venue_vec = {f"venue_{v}": 1.0 if v == venue else 0.0 for v in VENUE_CODES}
        dfs = float(r.get("day_from_start", 0))
        td  = float(r.get("total_days", 0))
        is_final    = float(dfs > 0 and td > 0 and dfs == td)
        series_prog = float(dfs / td) if td > 0 else 0.0
        bubble_vals = {}
        for col in BUBBLE_COLS:
            for i in BOATS:
                bubble_vals[f"b{i}_{col}"] = float(r.get(f"b{i}_{col}", 0))
        grade_nums  = {i: float(GRADE_MAP.get(boat_data[i].get("grade", ""), 0)) for i in BOATS}
        a1_count    = float(sum(1 for i in BOATS if grade_nums[i] == 4.0))
        a1_in_boat1 = float(grade_nums[1] == 4.0)
        feat = (
            [abs_vals[f"b{i}_{c}"] for c in BASE_COLS for i in BOATS]
            + [rank_vals[f"b{i}_{c}_rank"] for c in rank_cols for i in BOATS]
            + [venue_vec[f"venue_{v}"] for v in VENUE_CODES]
            + [dfs, is_final, series_prog]
            + [bubble_vals[f"b{i}_{c}"] for c in BUBBLE_COLS for i in BOATS]
            + [grade_nums[i] for i in BOATS]
            + [a1_count, a1_in_boat1]
        )
        rows.append(feat)
    return np.array(rows, dtype=np.float32)

# ── データ・モデルロード ───────────────────────────────────────────────────────
print("ロード中...")
with open("data/model_boat.pkl", "rb") as f:
    clf = pickle.load(f)

df = pd.read_csv("data/prediction_data_graded_enriched.csv", dtype={"venue_code": str})
df["venue_code"] = df["venue_code"].str.zfill(2)
df["date"] = df["date"].astype(str)

# キャリブレーション用データ: 2026年1月以降
cal_df = df[df["date"] >= "20260101"].copy()
print(f"キャリブレーションデータ: {len(cal_df):,} レース")

# ── 予測 ───────────────────────────────────────────────────────────────────────
print("予測中...")
X = build_X(cal_df)
proba = clf.predict_proba(X)
classes = [int(c) for c in clf.classes_]
winners = cal_df["winning_boat"].astype(int).values

# ── 艇ごとにIsotonic Regressionでキャリブレーション ─────────────────────────
print("\n艇番別キャリブレーション:")
print(f"{'艇':>4}  {'補正前平均':>9}  {'補正後平均':>9}  {'実際勝率':>9}")
print("-" * 45)

calibrators = {}
for ci, boat in enumerate(classes):
    raw_probs = proba[:, ci]
    actuals   = (winners == boat).astype(float)

    ir = IsotonicRegression(increasing=True, out_of_bounds="clip")
    ir.fit(raw_probs, actuals)
    calibrators[boat] = ir

    cal_probs = ir.predict(raw_probs)
    print(f"{boat}号艇  {raw_probs.mean()*100:8.2f}%   {cal_probs.mean()*100:8.2f}%   {actuals.mean()*100:8.2f}%")

# ── 正規化後のキャリブレーション精度確認 ─────────────────────────────────────
print("\n【正規化後】確率ビン別 実際勝率との比較（6号艇）:")
cal_all = np.zeros_like(proba)
for ci, boat in enumerate(classes):
    cal_all[:, ci] = calibrators[boat].predict(proba[:, ci])
row_sums = cal_all.sum(axis=1, keepdims=True)
row_sums = np.where(row_sums == 0, 1, row_sums)
cal_norm = cal_all / row_sums

bins = [0, 0.05, 0.10, 0.15, 0.20, 0.30, 1.01]
labels = ["<5%", "5-10%", "10-15%", "15-20%", "20-30%", "30%+"]
for boat_idx, boat in enumerate([1, 6]):
    ci = classes.index(boat)
    probs_norm = cal_norm[:, ci]
    actual_win = (winners == boat).astype(float)
    print(f"\n  {boat}号艇（補正後）:")
    print(f"  {'ビン':>8}  {'補正後確率':>9}  {'実際勝率':>9}  {'件数':>6}")
    for i, label in enumerate(labels):
        mask = (probs_norm >= bins[i]) & (probs_norm < bins[i+1])
        n = mask.sum()
        if n == 0:
            continue
        print(f"  {label:>8}  {probs_norm[mask].mean()*100:8.1f}%   {actual_win[mask].mean()*100:8.1f}%   {n:>6}")

# ── 保存 ───────────────────────────────────────────────────────────────────────
out_path = os.path.join(config.DATA_DIR, "calibration_boat.pkl")
with open(out_path, "wb") as f:
    pickle.dump({"calibrators": calibrators, "classes": classes}, f)
print(f"\n保存: {out_path}")
print("次: app_streamlit.py でキャリブレーションを有効化します")
