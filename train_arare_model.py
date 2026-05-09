"""
荒れ専用二値分類モデルの訓練

is_arare = (winning_boat != 1) を予測する。
特徴量は train_model.py と同一の93次元。

実行:
  python train_arare_model.py

出力:
  data/model_arare.pkl
  data/model_arare_meta.json
"""

import csv
import json
import os
import pickle

import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import (
    accuracy_score, brier_score_loss, classification_report, roc_auc_score,
)

import config

INPUT_CSV        = os.path.join(config.DATA_DIR, "prediction_data_graded_enriched.csv")
MODEL_ARARE      = os.path.join(config.DATA_DIR, "model_arare.pkl")
MODEL_ARARE_META = os.path.join(config.DATA_DIR, "model_arare_meta.json")

BOATS       = list(range(1, 7))
KIMARITE    = ["逃げ", "差し", "まくり", "まくり差し", "抜き", "恵まれ"]
BASE_COLS   = ["avg_st", "motor_2rate", "national_rate", "local_rate"]
BUBBLE_COLS = ["qualify", "bubble", "points_before"]
VENUE_CODES = sorted(config.VENUE_CODES.keys())
GRADE_MAP   = {"A1": 4, "A2": 3, "B1": 2, "B2": 1}


def _f(v):
    try:
        return float(v) if v and v != "" else None
    except ValueError:
        return None


def load_and_build(path: str):
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    print(f"読み込み: {len(rows):,} 行")

    rank_cols = ["avg_st", "motor_2rate", "national_rate"]
    X_list, y_list = [], []
    skipped = 0

    for r in rows:
        wb = r.get("winning_boat", "")
        km = r.get("kimarite", "").strip()
        if not wb or wb == "0" or km not in KIMARITE:
            skipped += 1
            continue
        try:
            wb_int = int(float(wb))
        except ValueError:
            skipped += 1
            continue
        if wb_int not in BOATS:
            skipped += 1
            continue

        # 絶対値特徴量
        abs_vals = {}
        for col in BASE_COLS:
            for i in BOATS:
                v = _f(r.get(f"b{i}_{col}"))
                abs_vals[f"b{i}_{col}"] = v if v is not None else 0.0

        # ランク特徴量
        rank_vals = {}
        for col in rank_cols:
            vals = [abs_vals[f"b{i}_{col}"] for i in BOATS]
            ascending = (col == "avg_st")
            sp = sorted(enumerate(vals, 1), key=lambda x: x[1], reverse=not ascending)
            ranks = {b: rk + 1 for rk, (b, _) in enumerate(sp)}
            for i in BOATS:
                rank_vals[f"b{i}_{col}_rank"] = float(ranks[i])

        # 会場ワンホット
        jcd = r.get("venue_code", "")
        venue_vec = {f"venue_{v}": 1.0 if v == jcd else 0.0 for v in VENUE_CODES}

        # 節内特徴量
        day      = _f(r.get("day_from_start"))  or 0.0
        is_final = _f(r.get("is_final_day"))     or 0.0
        series_p = _f(r.get("series_progress"))  or 0.0
        bubble_vals = {}
        for col in BUBBLE_COLS:
            for i in BOATS:
                v = _f(r.get(f"b{i}_{col}"))
                bubble_vals[f"b{i}_{col}"] = v if v is not None else 0.0

        # グレード特徴量
        grade_nums = {}
        for i in BOATS:
            g = r.get(f"b{i}_grade", "")
            grade_nums[i] = float(GRADE_MAP.get(g, 0))
        a1_count    = float(sum(1 for i in BOATS if grade_nums[i] == 4.0))
        a1_in_boat1 = float(grade_nums[1] == 4.0)

        row_feat = (
            [abs_vals[f"b{i}_{col}"] for col in BASE_COLS for i in BOATS]
            + [rank_vals[f"b{i}_{col}_rank"] for col in rank_cols for i in BOATS]
            + [venue_vec[f"venue_{v}"] for v in VENUE_CODES]
            + [day, is_final, series_p]
            + [bubble_vals[f"b{i}_{col}"] for col in BUBBLE_COLS for i in BOATS]
            + [grade_nums[i] for i in BOATS]
            + [a1_count, a1_in_boat1]
        )

        X_list.append(row_feat)
        y_list.append(1 if wb_int != 1 else 0)

    print(f"有効サンプル: {len(X_list):,} / スキップ: {skipped:,}")
    arare_count = sum(y_list)
    print(f"荒れ(1号艇負け): {arare_count:,} ({arare_count/len(y_list)*100:.1f}%) / "
          f"非荒れ: {len(y_list)-arare_count:,} ({(len(y_list)-arare_count)/len(y_list)*100:.1f}%)")
    return np.array(X_list, dtype=np.float32), np.array(y_list, dtype=np.int32)


def threshold_analysis(clf, X_test, y_test, baseline_rate: float) -> list[dict]:
    proba = clf.predict_proba(X_test)[:, 1]
    results = []
    total = len(y_test)
    for thresh in [0.45, 0.50, 0.55, 0.60, 0.65, 0.70]:
        mask = proba >= thresh
        n = mask.sum()
        if n == 0:
            results.append({"threshold": thresh, "n": 0, "coverage_pct": 0.0,
                            "arare_rate": 0.0, "lift": 0.0})
            continue
        arare_rate = y_test[mask].mean()
        lift = arare_rate / baseline_rate if baseline_rate > 0 else 0.0
        results.append({
            "threshold":    thresh,
            "n":            int(n),
            "coverage_pct": round(n / total * 100, 1),
            "arare_rate":   round(float(arare_rate), 4),
            "lift":         round(float(lift), 3),
        })
    return results


def main():
    print("=== 荒れ専用モデル訓練 ===\n")

    X, y = load_and_build(INPUT_CSV)
    baseline_arare_rate = float(y.mean())

    # 時系列分割（末尾20%をテスト）
    split = int(len(X) * 0.8)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]
    print(f"\n訓練: {len(X_train):,} / テスト: {len(X_test):,}")

    print("\nGradientBoosting訓練中...")
    base_clf = GradientBoostingClassifier(
        n_estimators=300,
        learning_rate=0.05,
        max_depth=5,
        min_samples_leaf=30,
        subsample=0.8,
        random_state=42,
    )
    base_clf.fit(X_train, y_train)

    print("キャリブレーション中（isotonic, cv=3）...")
    clf = CalibratedClassifierCV(base_clf, method="isotonic", cv=3)
    clf.fit(X_train, y_train)

    # 評価
    y_pred  = clf.predict(X_test)
    y_proba = clf.predict_proba(X_test)[:, 1]

    acc    = accuracy_score(y_test, y_pred)
    auc    = roc_auc_score(y_test, y_proba)
    brier  = brier_score_loss(y_test, y_proba)

    print(f"\n=== テスト評価 ===")
    print(f"  Accuracy  : {acc*100:.2f}%")
    print(f"  AUC-ROC   : {auc:.4f}")
    print(f"  Brier     : {brier:.4f}  (0に近いほど良い)")
    print(f"  ベースライン荒れ率: {baseline_arare_rate*100:.1f}%")
    print()
    print(classification_report(y_test, y_pred, target_names=["非荒れ(1着)", "荒れ(2〜6着)"]))

    # 閾値別分析
    ta = threshold_analysis(clf, X_test, y_test, baseline_arare_rate)
    print("=== 閾値別分析 ===")
    print(f"  {'閾値':>5}  {'対象R':>6}  {'カバレッジ':>8}  {'荒れ的中率':>9}  {'lift':>5}")
    for t in ta:
        print(f"  {t['threshold']:.2f}  {t['n']:>6}  {t['coverage_pct']:>7.1f}%  "
              f"{t['arare_rate']*100:>8.1f}%  {t['lift']:>5.3f}x")

    # 保存
    with open(MODEL_ARARE, "wb") as f:
        pickle.dump(clf, f)

    meta = {
        "accuracy":             round(acc, 4),
        "auc_roc":              round(auc, 4),
        "brier":                round(brier, 4),
        "baseline_arare_rate":  round(baseline_arare_rate, 4),
        "n_train":              len(X_train),
        "n_test":               len(X_test),
        "threshold_analysis":   ta,
        "recommended_threshold": 0.55,
    }
    with open(MODEL_ARARE_META, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"\n保存:")
    print(f"  {MODEL_ARARE}")
    print(f"  {MODEL_ARARE_META}")
    print(f"\n次: python build_arare_stats.py")


if __name__ == "__main__":
    main()
