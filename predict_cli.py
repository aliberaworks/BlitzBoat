"""
ボートレース予想AI - コマンドライン予測ツール

実行:
  python predict_cli.py

入力: 会場・各艇の avg_st / motor_2rate / national_rate / local_rate
      (展示STは任意入力 - avg_stの代わりに使われる)
出力: 勝利艇・決まり手の確率分布 + TOP 3連単候補
"""

import json
import os
import pickle

import numpy as np

import config

MODEL_BOAT = os.path.join(config.DATA_DIR, "model_boat.pkl")
MODEL_KM   = os.path.join(config.DATA_DIR, "model_km.pkl")
MODEL_META = os.path.join(config.DATA_DIR, "model_meta.json")

BOATS    = list(range(1, 7))
KIMARITE = ["逃げ", "差し", "まくり", "まくり差し", "抜き", "恵まれ"]
VENUE_CODES = sorted(config.VENUE_CODES.keys())
BASE_COLS = ["avg_st", "motor_2rate", "national_rate", "local_rate"]


def load_models():
    for p in [MODEL_BOAT, MODEL_KM, MODEL_META]:
        if not os.path.exists(p):
            print(f"モデルが見つかりません: {p}")
            print("先に python train_model.py を実行してください。")
            exit(1)
    with open(MODEL_BOAT, "rb") as f:
        clf_boat = pickle.load(f)
    with open(MODEL_KM, "rb") as f:
        clf_km = pickle.load(f)
    with open(MODEL_META, encoding="utf-8") as f:
        meta = json.load(f)
    return clf_boat, clf_km, meta


def prompt(msg: str, default=None, cast=float):
    while True:
        raw = input(msg).strip()
        if raw == "" and default is not None:
            return default
        try:
            return cast(raw)
        except ValueError:
            print("  数値を入力してください。")


def select_venue() -> str:
    print("\n=== 会場選択 ===")
    for jcd, name in sorted(config.VENUE_CODES.items()):
        print(f"  {jcd}: {name}")
    while True:
        v = input("会場コード (例: 04): ").strip().zfill(2)
        if v in config.VENUE_CODES:
            return v
        print("  有効なコードを入力してください。")


def input_boat_data() -> dict:
    """各艇のデータを対話入力する。"""
    print("\n=== 各艇のデータを入力 ===")
    print("  ※ 出走表から転記してください。不明な場合はEnterで0入力。")
    print("  ※ 展示STがある場合は avg_st の代わりに使います。\n")

    data = {}
    for i in BOATS:
        print(f"  --- {i}号艇 ---")
        avg_st      = prompt(f"    avg_st (平均ST, 例 0.17): ", default=0.0)
        motor_2rate = prompt(f"    motor_2rate (モーター2連率%, 例 38.5): ", default=0.0)
        national    = prompt(f"    national_rate (全国勝率, 例 5.23): ", default=0.0)
        local       = prompt(f"    local_rate (当地勝率, 例 4.80): ", default=0.0)
        exhibit_st  = prompt(f"    exhibit_st (展示ST, なければEnter): ", default=None)

        # 展示STがあればavg_stの代わりに使う
        effective_st = exhibit_st if exhibit_st is not None else avg_st
        data[i] = {
            "avg_st": effective_st,
            "motor_2rate": motor_2rate,
            "national_rate": national,
            "local_rate": local,
        }
    return data


def build_feature_vector(venue: str, boat_data: dict, feat_names: list[str]) -> np.ndarray:
    """入力データを特徴量ベクトルに変換する。"""
    abs_vals = {}
    for col in BASE_COLS:
        for i in BOATS:
            abs_vals[f"b{i}_{col}"] = boat_data[i].get(col, 0.0)

    rank_cols = ["avg_st", "motor_2rate", "national_rate"]
    rank_vals = {}
    for col in rank_cols:
        vals = [abs_vals[f"b{i}_{col}"] for i in BOATS]
        ascending = (col == "avg_st")
        sorted_vals = sorted(enumerate(vals, start=1), key=lambda x: x[1], reverse=not ascending)
        ranks = {boat: rank + 1 for rank, (boat, _) in enumerate(sorted_vals)}
        for i in BOATS:
            rank_vals[f"b{i}_{col}_rank"] = float(ranks[i])

    venue_vec = {f"venue_{v}": 1.0 if v == venue else 0.0 for v in VENUE_CODES}

    feat = (
        [abs_vals[f"b{i}_{col}"] for col in BASE_COLS for i in BOATS]
        + [rank_vals[f"b{i}_{col}_rank"] for col in rank_cols for i in BOATS]
        + [venue_vec[f"venue_{v}"] for v in VENUE_CODES]
    )
    return np.array(feat, dtype=np.float32).reshape(1, -1)


def display_results(venue: str, boat_data: dict, clf_boat, clf_km, feat_names: list[str]):
    X = build_feature_vector(venue, boat_data, feat_names)

    boat_proba = clf_boat.predict_proba(X)[0]
    boat_classes = clf_boat.classes_
    km_proba = clf_km.predict_proba(X)[0]
    km_classes = clf_km.classes_

    boat_prob = {int(c): float(p) for c, p in zip(boat_classes, boat_proba)}
    km_prob   = {c: float(p) for c, p in zip(km_classes, km_proba)}

    bar = lambda p, w=20: "█" * int(p * w) + "░" * (w - int(p * w))

    print(f"\n{'='*50}")
    print(f"  会場: {config.VENUE_CODES.get(venue, venue)}")
    print(f"{'='*50}")

    print("\n【勝利艇 確率分布】")
    for b in sorted(boat_prob, key=boat_prob.get, reverse=True):
        p = boat_prob[b]
        print(f"  {b}号艇  {p*100:5.1f}%  {bar(p)}")

    print("\n【決まり手 確率分布】")
    for km in sorted(km_prob, key=km_prob.get, reverse=True):
        p = km_prob[km]
        print(f"  {km:<6}  {p*100:5.1f}%  {bar(p)}")

    # TOP 3連単候補（勝利艇 × 決まり手 の確率積）
    print("\n【TOP 3連単候補（勝利艇×決まり手 確率）】")
    combos = []
    for b in BOATS:
        for km in KIMARITE:
            p = boat_prob.get(b, 0) * km_prob.get(km, 0)
            combos.append((b, km, p))
    combos.sort(key=lambda x: x[2], reverse=True)

    print(f"  {'艇':>3}  {'決まり手':<8}  {'確率':>6}  ヒント")
    hints = {
        (1, "逃げ"):    "定番 / 配当低め",
        (2, "差し"):    "スジ舟券",
        (2, "まくり"):  "荒れ注意",
        (3, "まくり差し"): "中穴",
    }
    for b, km, p in combos[:10]:
        hint = hints.get((b, km), "")
        print(f"  {b}号艇  {km:<8}  {p*100:5.1f}%  {hint}")

    print(f"\n  ※ 3連単コンパス（https://boatrace.arcatua.com）で")
    print(f"     上位の勝利艇×決まり手を選ぶと2着・3着候補が確率順に表示されます。")


def main():
    print("=== ボートレース予想AI (v1) ===")
    print("  ※ 使用特徴量: avg_st / motor_2rate / national_rate / local_rate")
    print("  ※ 展示STを入力すると avg_st の代わりに使用します。\n")

    clf_boat, clf_km, meta = load_models()
    feat_names = meta["feature_names"]

    while True:
        venue = select_venue()
        boat_data = input_boat_data()
        display_results(venue, boat_data, clf_boat, clf_km, feat_names)

        again = input("\n別のレースを予測しますか？ (y/N): ").strip().lower()
        if again != "y":
            break

    print("\n終了。")


if __name__ == "__main__":
    main()
