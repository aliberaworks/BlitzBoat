"""
makuri - 統計テーブル生成
prediction_data_player.csv から makuri 用の統計ファイルを生成する。

生成物:
  data/trifecta_stats.json  - model_meta.json から抽出（まくり成功出目）
  data/ura_suji_stats.json  - まくり失敗時の実際の出目分布（裏スジ）

実行:
  python build_stats.py
"""
import csv, json, os, sys
from collections import defaultdict

import config

sys.path.insert(0, config.BLITZBOAT_DIR)

MOTOR_MIN   = 35.0
ST_RANK_MAX = 1
FAIL_MIN    = 5
MAKKURI_KM  = {"まくり", "まくり差し"}


def _f(v, d=0.0):
    try: return float(v) if v and str(v).strip() else d
    except: return d

def _i(v, d=0):
    try: return int(float(v)) if v and str(v).strip() else d
    except: return d


def classify(r, t):
    wb = _i(r.get("winning_boat"))
    km = r.get("kimarite","").strip()
    if wb == t and km in MAKKURI_KM: return "success"
    mot = _f(r.get(f"b{t}_motor_2rate"))
    if mot < MOTOR_MIN: return "none"
    sts = {}
    for b in range(1,7):
        v = _f(r.get(f"st{b}"), 0.0)
        if v > 0.05: sts[b] = v
    if t not in sts: return "none"
    ranked = sorted(sts.items(), key=lambda x:x[1])
    st_rank = {b:k+1 for k,(b,_) in enumerate(ranked)}
    if st_rank.get(t,99) > ST_RANK_MAX: return "none"
    final = _i(r.get(f"rank{t}"))
    if final == 0: return "none"
    if final >= FAIL_MIN: return "fail"
    return "partial"


def main():
    os.makedirs(config.DATA_DIR, exist_ok=True)

    # ── 1. trifecta_stats を model_meta.json から抽出 ────────────
    print("trifecta_stats 抽出中...")
    with open(config.MODEL_META_JSON, encoding="utf-8") as f:
        meta = json.load(f)
    tri_stats = meta.get("trifecta_stats_all", meta.get("trifecta_stats", {}))
    with open(config.TRIFECTA_STATS_JSON, "w", encoding="utf-8") as f:
        json.dump(tri_stats, f, ensure_ascii=False, indent=2)
    print(f"  保存: {config.TRIFECTA_STATS_JSON}  ({len(tri_stats)}キー)")

    # ── 2. ura_suji_stats: まくり失敗時の出目分布 ─────────────────
    print("\nura_suji_stats 計算中...")
    csv_path = os.path.join(config.BLITZBOAT_DIR, "data", "prediction_data_player.csv")
    if not os.path.exists(csv_path):
        csv_path = os.path.join(config.BLITZBOAT_DIR, "data", "prediction_data_graded_enriched.csv")
    print(f"  データ: {csv_path}")

    with open(csv_path, encoding="utf-8-sig") as fp:
        rows = list(csv.DictReader(fp))
    print(f"  読み込み: {len(rows):,}件")

    # 全体ベースラインの出目カウント
    base_tri = defaultdict(int)
    base_n   = 0
    for r in rows:
        wb = _i(r.get("winning_boat"))
        tri = r.get("trifecta", "").strip()
        if wb == 0 or not tri: continue
        base_tri[tri] += 1
        base_n += 1

    ura_stats = {}
    for t in [3, 4]:
        fail_tris = defaultdict(list)
        fail_n    = 0
        for r in rows:
            wb  = _i(r.get("winning_boat"))
            tri = r.get("trifecta", "").strip()
            pay = _f(r.get("pay_3t"))
            cat = classify(r, t)
            if cat != "fail": continue
            if wb == 0 or not tri: continue
            fail_n += 1
            fail_tris[tri].append(pay)

        # 出目ごとの出現率・平均配当・ベース比を計算
        patterns = []
        for tri, pays in fail_tris.items():
            cnt = len(pays)
            pct = cnt / fail_n if fail_n else 0
            base_pct = base_tri.get(tri, 0) / base_n if base_n else 0
            lift = pct / base_pct if base_pct > 0 else 0
            avg_pay = sum(p for p in pays if p > 0) / len([p for p in pays if p > 0]) if any(p > 0 for p in pays) else 0
            if pct >= 0.005:  # 0.5%以上の出目のみ
                parts = tri.split("-")
                patterns.append({
                    "combo":   tri,
                    "pct":     round(pct, 4),
                    "count":   cnt,
                    "lift":    round(lift, 3),
                    "avg_pay": int(avg_pay),
                })

        # リフト順にソート（ベース比が高い = 裏スジとして有効な出目）
        patterns.sort(key=lambda x: x["lift"], reverse=True)
        ura_stats[f"fail_{t}"] = patterns[:20]

        print(f"  {t}号艇失敗: 総{fail_n}件 / 有効出目{len(patterns)}パターン")
        print(f"    上位5件（リフト順）:")
        for pat in patterns[:5]:
            print(f"      {pat['combo']:>8}  出現率{pat['pct']*100:.2f}%  "
                  f"リフト×{pat['lift']:.2f}  平均配当{pat['avg_pay']:,}円")

    with open(config.URA_SUJI_STATS_JSON, "w", encoding="utf-8") as f:
        json.dump(ura_stats, f, ensure_ascii=False, indent=2)
    print(f"\n保存: {config.URA_SUJI_STATS_JSON}")
    print("完了")


if __name__ == "__main__":
    main()
