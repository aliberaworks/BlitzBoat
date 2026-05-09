# BlitzBoat - 引き継ぎ指示書

## このドキュメントの目的
別ターミナル（boatrace-tool）で行った分析を BlitzBoat に統合するための引き継ぎ。
ここを作業ディレクトリとして開始すること。

---

## BlitzBoat の現状

### アーキテクチャ
- `app_streamlit.py` : Streamlit UI（予想画面）
- `train_model.py` : RandomForest/GradientBoosting でモデル訓練
- `scraper.py` : boatrace.jp から前情報・レース結果をスクレイプ
- `data/model_boat.pkl` : 勝利艇予測モデル
- `data/model_km.pkl` : 決まり手予測モデル
- `data/race_results.csv` : 過去レース結果（2025-02-23〜2026-02-22、全24会場）

### モデルの特徴量（現状）
```
BASE_COLS = ["avg_st", "motor_2rate", "national_rate", "local_rate"]
```
6艇分の絶対値 × 4 = 24次元  
艇間相対ランク × 3指標 × 6 = 18次元  
会場 one-hot × 24 = 24次元  
**合計 約66次元。節内の日数・予選順位は含まれていない。**

---

## boatrace-tool で完了した分析

### 生成ファイル（別ディレクトリにある）
```
../boatrace-tool/scripts/players.csv        # 332,424行、全24会場×4,617日分
../boatrace-tool/scripts/bubble_analysis.csv  # 同行数、節内順位付き
```

### players.csv の列
`jcd, venue, hd, rno, boat, player_name, reg_no, grade, avg_st, f_count`

### bubble_analysis.csv の列
`jcd, venue, hd, rno, boat, player_name, reg_no, grade, avg_st, f_count,`  
`day_from_start, tournament_start, points_before, races_before,`  
`tournament_rank, qualify, bubble, position, won, kimarite`

### 結合キー
`(jcd, hd, rno, boat)` が両システムの共通キー。

---

## 分析結果（統合の根拠）

### バブル選手（予選ライン±3位）の日別勝率

| 日 | bubble (±3位) | qualify (圏内) | out (圏外) |
|----|:-------------:|:--------------:|:---------:|
| 2日目 | 17.9% | 21.6% | 12.4% |
| 3日目 | 17.0% | 21.9% | 12.1% |
| **4日目** | **17.3%** | **24.1%** | **10.8%** |
| 5日目 | 19.7% | 24.1% | 9.5% |
| 6日目 | 19.2% | 24.1% | 10.9% |

**主な発見：**
- 4日目に `qualify` vs `out` の勝率格差が最大（13.3pt差）
- `out` の選手は4日目以降に特に消極的になる（諦め仮説）
- バブル選手（±3位）自体に劇的な変化はなかった（仮説は弱支持）

---

## 統合プラン（3段階）

### ① 今すぐ：UIに節情報バッジを表示（モデル不変）
`app_streamlit.py` の各艇カードに以下を追加表示：
- 節内何日目か（`day_from_start`）
- その選手の節内ステータス：`qualify` / `bubble` / `out`

データソース：`bubble_analysis.csv` を `(jcd, hd, rno, boat)` で引く。  
当日データがない場合は `scrape_qualifying.py` を呼んで生成。

**実装場所：** `app_streamlit.py` の `prob_bar` 付近の艇カード描画部分。

### ② 次：特徴量追加して再学習
`train_model.py` に以下を追加：
- `day_from_start`（1〜7）
- `qualify_flag`（0/1）
- `bubble_flag`（0/1）
- `points_before`（節内累積得点）

`prediction_data.csv` に上記カラムを `bubble_analysis.csv` から join して付与し直す。  
その後 `python train_model.py` で再学習。

### ③ 将来：オンライン更新
予想実行前に `scrape_qualifying.py --jcd XX --hd YYYYMMDD --save` を自動実行し、  
当日の節内順位をリアルタイムで特徴量に注入。

---

## 作業開始の指示

まず `①` から着手してください。

1. `app_streamlit.py` を読む
2. `bubble_analysis.csv`（`../boatrace-tool/scripts/bubble_analysis.csv`）を `(jcd, hd, rno, boat)` キーでキャッシュする関数を追加
3. 各艇カードの表示部分に `qualify` / `bubble` / `out` バッジを色付きで追加
   - qualify：緑
   - bubble：黄（±3位、要注目）
   - out：グレー
4. 節内日数（`day_from_start`）をレースヘッダーに表示

---

## スクリプトの場所

| ファイル | パス |
|---------|------|
| 出走表スクレイパー | `../boatrace-tool/scripts/scrape_qualifying.py` |
| バッチスクレイパー | `../boatrace-tool/scripts/batch_scrape.py` |
| players.csv | `../boatrace-tool/scripts/players.csv` |
| bubble_analysis.csv | `../boatrace-tool/scripts/bubble_analysis.csv` |
| race_results.csv | `data/race_results.csv` |
