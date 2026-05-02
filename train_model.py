"""
予想AIモデル訓練スクリプト

特徴量: 全6艇の avg_st, motor_2rate, national_rate, local_rate + 会場 + 艇間相対ランク
ターゲット: winning_boat（1-6）と kimarite（6種）を別々に訓練

出力:
  data/model_boat.pkl      -- 勝利艇予測モデル
  data/model_km.pkl        -- 決まり手予測モデル
  data/model_meta.json     -- 特徴量定義・評価結果・確率テーブル

実行:
  python train_model.py
"""

import csv
import json
import os
import pickle

import numpy as np
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder
from sklearn.metrics import accuracy_score, top_k_accuracy_score

import config

INPUT_CSV     = os.path.join(config.DATA_DIR, "prediction_data_graded_enriched.csv")
MODEL_BOAT    = os.path.join(config.DATA_DIR, "model_boat.pkl")
MODEL_KM      = os.path.join(config.DATA_DIR, "model_km.pkl")
MODEL_META    = os.path.join(config.DATA_DIR, "model_meta.json")

BOATS    = list(range(1, 7))
KIMARITE = ["逃げ", "差し", "まくり", "まくり差し", "抜き", "恵まれ"]

BASE_COLS    = ["avg_st", "motor_2rate", "national_rate", "local_rate"]
BUBBLE_COLS  = ["qualify", "bubble", "points_before"]  # 各艇の節内特徴量
VENUE_CODES  = sorted(config.VENUE_CODES.keys())
GRADE_MAP    = {"A1": 4, "A2": 3, "B1": 2, "B2": 1}


# ── データ読み込み・前処理 ────────────────────────────────────────────────────

def load_data(path: str):
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    print(f"読み込み: {len(rows):,} 行")
    return rows


def _f(v):
    try:
        return float(v) if v and v != "" else None
    except ValueError:
        return None


def build_features(rows: list[dict]) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str]]:
    """
    特徴量行列 X と ターゲット y_boat, y_km を構築する。

    特徴量:
      - 各艇の絶対値: avg_st, motor_2rate, national_rate, local_rate (6艇 × 4 = 24)
      - 各艇の艇間ランク: avg_st_rank, motor_rank, national_rank (6 × 3 = 18)
      - 会場ワンホット (24)
      - 節内日数: day_from_start (1)
      - 各艇の節内特徴量: qualify, bubble, points_before (6 × 3 = 18)
      - グレード特徴量: b{i}_grade_num (6), a1_count (1), a1_in_boat1 (1)
    合計 93次元
    """
    feat_names = []

    # 絶対値特徴量名
    for col in BASE_COLS:
        for i in BOATS:
            feat_names.append(f"b{i}_{col}")

    # ランク特徴量名
    rank_cols = ["avg_st", "motor_2rate", "national_rate"]
    for col in rank_cols:
        for i in BOATS:
            feat_names.append(f"b{i}_{col}_rank")

    # 会場ワンホット
    for jcd in VENUE_CODES:
        feat_names.append(f"venue_{jcd}")

    # 節内特徴量
    feat_names.append("day_from_start")
    feat_names.append("is_final_day")
    feat_names.append("series_progress")
    for col in BUBBLE_COLS:
        for i in BOATS:
            feat_names.append(f"b{i}_{col}")

    # グレード特徴量
    for i in BOATS:
        feat_names.append(f"b{i}_grade_num")
    feat_names.append("a1_count")
    feat_names.append("a1_in_boat1")

    X_list, y_boat_list, y_km_list = [], [], []
    skipped = 0

    for r in rows:
        # ターゲット
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
            sorted_vals = sorted(enumerate(vals, start=1), key=lambda x: x[1], reverse=not ascending)
            ranks = {boat: rank + 1 for rank, (boat, _) in enumerate(sorted_vals)}
            for i in BOATS:
                rank_vals[f"b{i}_{col}_rank"] = float(ranks[i])

        # 会場ワンホット
        jcd = r.get("venue_code", "")
        venue_vec = {f"venue_{v}": 1.0 if v == jcd else 0.0 for v in VENUE_CODES}

        # 節内特徴量（欠損は 0 で補完）
        day      = _f(r.get("day_from_start"))    or 0.0
        is_final = _f(r.get("is_final_day"))       or 0.0
        series_p = _f(r.get("series_progress"))    or 0.0
        bubble_vals = {}
        for col in BUBBLE_COLS:
            for i in BOATS:
                v = _f(r.get(f"b{i}_{col}"))
                bubble_vals[f"b{i}_{col}"] = v if v is not None else 0.0

        # グレード特徴量（欠損は 0）
        grade_nums = {}
        for i in BOATS:
            g = r.get(f"b{i}_grade", "")
            grade_nums[i] = float(GRADE_MAP.get(g, 0))
        a1_count     = float(sum(1 for i in BOATS if grade_nums[i] == 4.0))
        a1_in_boat1  = float(grade_nums[1] == 4.0)

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
        y_boat_list.append(wb_int)
        y_km_list.append(km)

    print(f"有効サンプル: {len(X_list):,} / スキップ: {skipped:,}")
    return (
        np.array(X_list, dtype=np.float32),
        np.array(y_boat_list, dtype=np.int32),
        np.array(y_km_list),
        feat_names,
    )


# ── 訓練 ─────────────────────────────────────────────────────────────────────

def train_boat_model(X_train, y_train):
    print("\n[1/2] 勝利艇モデル訓練中...")
    clf = RandomForestClassifier(
        n_estimators=300,
        max_depth=12,
        min_samples_leaf=20,
        class_weight={1: 0.6, 2: 1.4, 3: 1.6, 4: 1.8, 5: 2.5, 6: 3.0},
        n_jobs=-1,
        random_state=42,
    )
    clf.fit(X_train, y_train)
    return clf


def train_km_model(X_train, y_train):
    print("[2/2] 決まり手モデル訓練中...")
    clf = RandomForestClassifier(
        n_estimators=300,
        max_depth=10,
        min_samples_leaf=20,
        n_jobs=-1,
        random_state=42,
    )
    clf.fit(X_train, y_train)
    return clf


# ── 評価 ─────────────────────────────────────────────────────────────────────

def evaluate(name: str, clf, X_test, y_test, classes):
    y_pred = clf.predict(X_test)
    acc = accuracy_score(y_test, y_pred)

    # top-3 accuracy
    try:
        proba = clf.predict_proba(X_test)
        top3 = top_k_accuracy_score(y_test, proba, k=3, labels=clf.classes_)
    except Exception:
        top3 = None

    # ベースライン: 最多クラス予測
    from collections import Counter
    most_common = Counter(y_test).most_common(1)[0][0]
    baseline = np.mean(y_test == most_common)

    print(f"\n=== {name} 評価 ===")
    print(f"  テスト精度  : {acc*100:.1f}%")
    if top3:
        print(f"  Top-3 精度  : {top3*100:.1f}%")
    print(f"  ベースライン: {baseline*100:.1f}% (最多クラス '{most_common}' を常に予測)")

    # クラス別正解率
    print(f"  クラス別:")
    for cls in sorted(set(y_test)):
        mask = y_test == cls
        cls_acc = np.mean(y_pred[mask] == cls)
        n = mask.sum()
        print(f"    {cls}: {cls_acc*100:.1f}% (n={n:,})")

    return {"accuracy": round(acc, 4), "top3_accuracy": round(top3, 4) if top3 else None, "baseline": round(baseline, 4)}


# ── 特徴量重要度 ─────────────────────────────────────────────────────────────

def show_feature_importance(clf, feat_names: list[str], top_n: int = 15):
    imp = clf.feature_importances_
    idx = np.argsort(imp)[::-1][:top_n]
    print(f"\n  重要特徴量 Top{top_n}:")
    result = []
    for rank, i in enumerate(idx, 1):
        print(f"    {rank:2}. {feat_names[i]:<30} {imp[i]:.4f}")
        result.append({"feature": feat_names[i], "importance": round(float(imp[i]), 4)})
    return result


# ── メイン ───────────────────────────────────────────────────────────────────

def main():
    print(f"=== ボートレース予想AI モデル訓練 ===\n")

    rows = load_data(INPUT_CSV)
    X, y_boat, y_km, feat_names = build_features(rows)

    # 訓練/テスト分割（時系列順なので末尾20%をテストに）
    split = int(len(X) * 0.8)
    X_train, X_test = X[:split], X[split:]
    y_boat_train, y_boat_test = y_boat[:split], y_boat[split:]
    y_km_train, y_km_test = y_km[:split], y_km[split:]
    print(f"訓練: {len(X_train):,} / テスト: {len(X_test):,}")

    # 訓練
    clf_boat = train_boat_model(X_train, y_boat_train)
    clf_km   = train_km_model(X_train, y_km_train)

    # 評価
    boat_eval = evaluate("勝利艇", clf_boat, X_test, y_boat_test, BOATS)
    km_eval   = evaluate("決まり手", clf_km, X_test, y_km_test, KIMARITE)

    # 特徴量重要度
    print("\n=== 勝利艇モデル ===")
    boat_imp = show_feature_importance(clf_boat, feat_names)
    print("\n=== 決まり手モデル ===")
    km_imp = show_feature_importance(clf_km, feat_names)

    # 保存
    with open(MODEL_BOAT, "wb") as f:
        pickle.dump(clf_boat, f)
    with open(MODEL_KM, "wb") as f:
        pickle.dump(clf_km, f)

    # 艇番ごとの条件付き決まり手分布 P(kimarite | winning_boat) を全データから計算
    from collections import defaultdict
    km_by_boat_counts: dict = defaultdict(lambda: defaultdict(int))
    for wb, km in zip(y_boat, y_km):
        km_by_boat_counts[int(wb)][km] += 1

    km_by_boat: dict = {}
    for b in BOATS:
        total = sum(km_by_boat_counts[b].values())
        if total == 0:
            continue
        km_by_boat[str(b)] = {
            km: round(km_by_boat_counts[b][km] / total, 4)
            for km in KIMARITE
        }
        modal = max(km_by_boat_counts[b], key=km_by_boat_counts[b].get)
        km_by_boat[str(b)]["_modal"] = modal
        print(f"  {b}号艇 modal_km={modal} ({km_by_boat[str(b)][modal]*100:.1f}%)")

    # 3連単コンパス用: P(rank2, rank3 | rank1, kimarite) を集計
    from collections import defaultdict as _dd
    tri_counts: dict = _dd(lambda: _dd(int))
    for r in rows:
        wb = r.get("winning_boat", "").strip()
        km = r.get("kimarite", "").strip()
        r2 = r.get("rank2", "").strip()
        r3 = r.get("rank3", "").strip()
        if wb and km in KIMARITE and r2 and r3:
            key = f"{wb}_{km}"
            tri_counts[key][f"{r2}-{r3}"] += 1

    trifecta_stats: dict = {}
    for key, combos in tri_counts.items():
        total = sum(combos.values())
        top = sorted(combos.items(), key=lambda x: x[1], reverse=True)[:10]
        trifecta_stats[key] = [
            {"combo": c, "count": n, "pct": round(n / total, 4)}
            for c, n in top
        ]
    print(f"\n3連単コンパス: {len(trifecta_stats)} キー計算完了")

    meta = {
        "feature_names": feat_names,
        "boat_classes": [int(c) for c in clf_boat.classes_],
        "km_classes": list(clf_km.classes_),
        "boat_eval": boat_eval,
        "km_eval": km_eval,
        "boat_importance": boat_imp[:10],
        "km_importance": km_imp[:10],
        "n_train": len(X_train),
        "n_test": len(X_test),
        "km_by_boat": km_by_boat,
        "trifecta_stats": trifecta_stats,
    }
    with open(MODEL_META, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"\n保存完了:")
    print(f"  {MODEL_BOAT}")
    print(f"  {MODEL_KM}")
    print(f"  {MODEL_META}")
    print(f"\n次: python predict_cli.py で予測を試す")


if __name__ == "__main__":
    main()
