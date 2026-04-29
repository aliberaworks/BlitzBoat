"""
ボートレース予想AI - Streamlit UI
実行: python -m streamlit run app_streamlit.py
"""

import json
import os
import sys
import pickle
from datetime import date, timedelta

# BlitzBoat ディレクトリを sys.path の先頭に固定（boatrace-tool/scripts/scraper.py 混入防止）
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path = [_HERE] + [p for p in sys.path if p != _HERE]

import numpy as np
import pandas as pd
import streamlit as st

import config
from bubble_today import (
    compute_bubble_status, save_bubble_today,
    load_bubble_today, get_race_bubble_today,
)
import importlib.util as _ilu
_scraper_spec = _ilu.spec_from_file_location("scraper", os.path.join(_HERE, "scraper.py"))
_scraper_mod  = _ilu.module_from_spec(_scraper_spec)
_scraper_spec.loader.exec_module(_scraper_mod)
scrape_beforeinfo  = _scraper_mod.scrape_beforeinfo
scrape_race_result = _scraper_mod.scrape_race_result
scrape_racelist    = _scraper_mod.scrape_racelist
scrape_odds_3t     = _scraper_mod.scrape_odds_3t

st.set_page_config(page_title="ボートレース予想AI", page_icon="🚤", layout="wide")

BOATS       = list(range(1, 7))
KIMARITE    = ["逃げ", "差し", "まくり", "まくり差し", "抜き", "恵まれ"]
BASE_COLS   = ["avg_st", "motor_2rate", "national_rate", "local_rate"]
BUBBLE_COLS = ["qualify", "bubble", "points_before"]
VENUE_CODES = sorted(config.VENUE_CODES.keys())
GRADE_MAP   = {"A1": 4, "A2": 3, "B1": 2, "B2": 1}

# 1=白 2=黒 3=赤 4=青 5=黄 6=緑（公式カラー）
BOAT_COLORS = ["#FFFFFF", "#1A1A1A", "#E53935", "#1976D2", "#FDD835", "#43A047"]
BOAT_TEXT   = ["black",   "white",   "white",  "white",   "black",   "white"  ]

MODEL_BOAT = os.path.join(config.DATA_DIR, "model_boat.pkl")
MODEL_KM   = os.path.join(config.DATA_DIR, "model_km.pkl")
MODEL_META = os.path.join(config.DATA_DIR, "model_meta.json")

BUBBLE_CSV = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "boatrace-tool", "scripts", "bubble_analysis.csv"
))

KM_COLORS = {
    "逃げ": "#1565c0", "差し": "#c62828", "まくり": "#1b5e20",
    "まくり差し": "#f57f17", "抜き": "#4a148c", "恵まれ": "#795548",
}


# ── モデルロード ──────────────────────────────────────────────────────────────
@st.cache_resource
def load_models():
    with open(MODEL_BOAT, "rb") as f:
        clf_boat = pickle.load(f)
    with open(MODEL_KM, "rb") as f:
        clf_km = pickle.load(f)
    with open(MODEL_META, encoding="utf-8") as f:
        meta = json.load(f)
    return clf_boat, clf_km, meta


# ── 特徴量構築 ────────────────────────────────────────────────────────────────
def build_feature_vector(
    venue: str,
    boat_data: dict,
    day_from_start: int = 0,
    total_days: int = 0,
    race_bubble: dict | None = None,
) -> np.ndarray:
    abs_vals = {f"b{i}_{col}": boat_data[i].get(col, 0.0) for col in BASE_COLS for i in BOATS}

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

    is_final    = float(day_from_start > 0 and total_days > 0 and day_from_start == total_days)
    series_prog = float(day_from_start / total_days) if total_days > 0 else 0.0

    bs = race_bubble or {}
    bubble_vals = {}
    for col in BUBBLE_COLS:
        for i in BOATS:
            bubble_vals[f"b{i}_{col}"] = float((bs.get(i) or {}).get(col, 0))

    grade_nums  = {i: float(GRADE_MAP.get(boat_data[i].get("grade", ""), 0)) for i in BOATS}
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


# ── 確率バー ─────────────────────────────────────────────────────────────────
def prob_bar(label: str, prob: float, color: str, text_color: str = "white"):
    pct = prob * 100
    w = max(pct, 2)
    st.markdown(
        f'<div style="display:flex;align-items:center;margin-bottom:5px;">'
        f'<div style="width:76px;font-weight:bold;font-size:13px;">{label}</div>'
        f'<div style="flex:1;background:#eee;border-radius:4px;height:26px;">'
        f'<div style="width:{w}%;background:{color};height:100%;border-radius:4px;'
        f'display:flex;align-items:center;padding-left:8px;'
        f'color:{text_color};font-weight:bold;font-size:12px;min-width:42px;">'
        f'{pct:.1f}%</div></div></div>',
        unsafe_allow_html=True,
    )


# ── session_state 初期化 ──────────────────────────────────────────────────────
def init_state():
    defaults = {f"avg_{i}": 0.17 for i in BOATS}
    defaults.update({f"ex_{i}": 0.0 for i in BOATS})
    defaults.update({f"m2r_{i}": 38.0 for i in BOATS})
    defaults.update({f"nat_{i}": 5.0 for i in BOATS})
    defaults.update({f"loc_{i}": 5.0 for i in BOATS})
    defaults.update({f"grade_{i}": "" for i in BOATS})
    defaults["fetch_status"] = ""
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ── bubble_analysis キャッシュ ────────────────────────────────────────────────
@st.cache_data(ttl=3600)
def load_bubble_data() -> dict:
    """bubble_analysis.csv を (jcd, hd, rno, boat) キーのdictでキャッシュ"""
    if not os.path.exists(BUBBLE_CSV):
        return {}
    try:
        df = pd.read_csv(
            BUBBLE_CSV,
            dtype={"jcd": str, "hd": str, "rno": int, "boat": int},
            usecols=["jcd", "hd", "rno", "boat", "day_from_start",
                     "qualify", "bubble", "tournament_rank", "points_before"],
        )
        result = {}
        for row in df.itertuples(index=False):
            key = (str(row.jcd).zfill(2), str(row.hd), int(row.rno), int(row.boat))
            try:
                day = int(row.day_from_start)
            except (ValueError, TypeError):
                day = 0
            try:
                rank = int(float(row.tournament_rank)) if row.tournament_rank == row.tournament_rank else None
            except (ValueError, TypeError):
                rank = None
            result[key] = {
                "day_from_start": day,
                "qualify": str(row.qualify) == "True",
                "bubble": str(row.bubble) == "True",
                "tournament_rank": rank,
                "points_before": row.points_before,
            }
        return result
    except Exception:
        return {}


def get_race_bubble(bubble_data: dict, jcd: str, hd: str, rno: int) -> dict:
    """指定レースの全艇ステータス {boat_no: {...}} を返す"""
    result = {}
    for boat in range(1, 7):
        key = (str(jcd).zfill(2), str(hd), int(rno), boat)
        if key in bubble_data:
            result[boat] = bubble_data[key]
    return result


def bubble_badge_html(status: dict | None) -> str:
    """qualify/bubble/out バッジのHTMLを返す"""
    if not status:
        return '<span style="color:#bbb;font-size:11px;">-</span>'
    rank = status.get("tournament_rank")
    rank_str = f" {rank}位" if rank is not None else ""
    if status["qualify"]:
        return (f'<span style="background:#2e7d32;color:white;padding:1px 6px;'
                f'border-radius:3px;font-size:11px;font-weight:bold;">圏内{rank_str}</span>')
    elif status["bubble"]:
        return (f'<span style="background:#f57f17;color:white;padding:1px 6px;'
                f'border-radius:3px;font-size:11px;font-weight:bold;">バブル{rank_str}</span>')
    else:
        return (f'<span style="background:#757575;color:white;padding:1px 6px;'
                f'border-radius:3px;font-size:11px;">圏外{rank_str}</span>')


# ── 朝バッチ JSON ロード ──────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def load_batch_json(hd: str) -> dict | None:
    path = os.path.join(config.DATA_DIR, f"today_{hd}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── 朝バッチ ダッシュボードタブ ───────────────────────────────────────────────
def tab_morning_batch():
    st.subheader("朝バッチ 予測ランキング")

    hd_input = st.date_input("対象日", value=date.today(), key="batch_date")
    hd = hd_input.strftime("%Y%m%d")

    col_run, col_info = st.columns([2, 5])
    with col_run:
        if st.button("🔄 朝バッチを今すぐ実行", help="boatrace.jpから全会場スクレイプ（約2分）"):
            st.info("コマンドプロンプトで実行してください:\n`python morning_batch.py`")

    data = load_batch_json(hd)
    if not data:
        st.warning(f"{hd_input.strftime('%m/%d')} の朝バッチデータがありません。先に `python morning_batch.py` を実行してください。")
        return

    preds = data["predictions"]
    total = data["total_races"]
    generated = data.get("generated_at", "")

    st.caption(f"生成: {generated}　|　総レース数: {total}　|　モデル精度 Top3: {data['model_info']['top3_accuracy']*100:.1f}%")

    # フィルター
    fc1, fc2, fc3, fc4 = st.columns([1.5, 1.5, 1.5, 3])
    with fc1:
        top_n = st.slider("表示件数", 10, total, min(30, total), step=5)
    with fc2:
        min_conf = st.slider("最低信頼度 +%", 0, 30, 0) / 100
    with fc3:
        sort_by = st.radio("並び順", ["信頼度順", "時刻順"], horizontal=True)
    with fc4:
        venue_filter = st.multiselect(
            "会場絞り込み",
            sorted({p["venue_name"] for p in preds}),
            default=[],
        )

    filtered = [
        p for p in preds
        if p["confidence"] >= min_conf
        and (not venue_filter or p["venue_name"] in venue_filter)
    ]
    if sort_by == "時刻順":
        filtered.sort(key=lambda p: (p.get("race_time") or "99:99", p["venue_name"], p["race_no"]))
    filtered = filtered[:top_n]

    if not filtered:
        st.info("条件に合うレースがありません。")
        return

    # テーブル描画
    hdr = st.columns([0.5, 1.2, 0.6, 0.8, 1.2, 1.5, 1.5, 2.0])
    for col, label in zip(hdr, ["順位", "会場", "R", "時刻", "本命艇", "決まり手", "本命確率", "信頼度"]):
        col.markdown(f"**{label}**")

    for rank, p in enumerate(filtered, 1):
        b     = p["top_boat"]
        prob  = p["boat_prob"][str(b)]
        conf  = p["confidence"]
        stars = "★" * min(int(conf / 0.05), 5)
        t     = p.get("race_time", "")

        row = st.columns([0.5, 1.2, 0.6, 0.8, 1.2, 1.5, 1.5, 2.0])
        row[0].write(f"{rank}位")
        row[1].write(p["venue_name"])
        row[2].write(f"{p['race_no']}R")
        row[3].write(t)
        row[4].markdown(
            f'<span style="background:{BOAT_COLORS[b-1]};color:{BOAT_TEXT[b-1]};'
            f'padding:2px 8px;border-radius:4px;font-weight:bold;">{b}号艇</span>',
            unsafe_allow_html=True,
        )
        row[5].write(p["top_km"])
        row[6].write(f"{prob*100:.1f}%")
        bar_w = max(int(conf * 100 * 3), 4)
        row[7].markdown(
            f'<div style="background:#1565c0;width:{bar_w}px;height:16px;'
            f'border-radius:3px;display:inline-block;"></div>'
            f'&nbsp;<b>+{conf*100:.1f}%</b>&nbsp;{stars}',
            unsafe_allow_html=True,
        )


# ── 前日比較タブ ──────────────────────────────────────────────────────────────
def tab_review():
    st.subheader("前日予測 vs 実際の結果")

    hd_input = st.date_input("対象日", value=date.today() - timedelta(days=1), key="review_date")
    hd = hd_input.strftime("%Y%m%d")

    data = load_batch_json(hd)
    if not data:
        st.warning(f"{hd_input.strftime('%m/%d')} の朝バッチデータがありません。")
        return

    preds = data["predictions"]
    top_n = st.slider("検証対象（信頼度上位N件）", 10, len(preds), 30, step=5, key="review_top")
    targets = preds[:top_n]

    if not st.button("📊 実際の結果を取得して比較"):
        st.info(f"上位{top_n}件の予測結果を照合します。「実際の結果を取得して比較」を押してください。")
        return

    results = []
    bar = st.progress(0)
    for i, p in enumerate(targets):
        bar.progress((i + 1) / len(targets))
        try:
            res = scrape_race_result(p["jcd"], hd, p["race_no"])
            actual_boat = int(res.get("winning_boat", 0)) if res else 0
            actual_km   = res.get("kimarite", "") if res else ""
            pred_boat   = p["top_boat"]
            hit_boat    = (actual_boat == pred_boat)
            results.append({**p, "actual_boat": actual_boat, "actual_km": actual_km, "hit": hit_boat})
        except Exception:
            results.append({**p, "actual_boat": 0, "actual_km": "", "hit": False})

    bar.empty()

    hits = sum(r["hit"] for r in results)
    st.success(f"的中率（本命艇）: **{hits}/{len(results)} = {hits/len(results)*100:.1f}%**")

    hdr = st.columns([0.5, 1.2, 0.6, 1.2, 1.5, 1.2, 1.5, 0.8])
    for col, label in zip(hdr, ["順位", "会場", "R", "予測艇", "予測決まり手", "実際の艇", "実際の決まり手", "的中"]):
        col.markdown(f"**{label}**")

    for rank, r in enumerate(results, 1):
        b_pred = r["top_boat"]
        b_act  = r["actual_boat"]
        hit    = r["hit"]

        row = st.columns([0.5, 1.2, 0.6, 1.2, 1.5, 1.2, 1.5, 0.8])
        row[0].write(f"{rank}位")
        row[1].write(r["venue_name"])
        row[2].write(f"{r['race_no']}R")
        row[3].markdown(
            f'<span style="background:{BOAT_COLORS[b_pred-1]};color:{BOAT_TEXT[b_pred-1]};'
            f'padding:2px 8px;border-radius:4px;">{b_pred}号艇</span>',
            unsafe_allow_html=True,
        )
        row[4].write(r["top_km"])
        if b_act:
            row[5].markdown(
                f'<span style="background:{BOAT_COLORS[b_act-1]};color:{BOAT_TEXT[b_act-1]};'
                f'padding:2px 8px;border-radius:4px;">{b_act}号艇</span>',
                unsafe_allow_html=True,
            )
        else:
            row[5].write("取得失敗")
        row[6].write(r["actual_km"])
        row[7].markdown("✅" if hit else "❌")


# ── メイン ───────────────────────────────────────────────────────────────────
def main():
    init_state()

    st.title("🚤 ボートレース予想AI")
    st.caption("会場・レース番号を選んで「出走表を取得」→「予測する」だけ")

    if not os.path.exists(MODEL_BOAT):
        st.error("モデルが見つかりません。`python train_model.py` を先に実行してください。")
        return

    clf_boat, clf_km, meta = load_models()
    km_by_boat = meta.get("km_by_boat", {})

    tab1, tab2, tab3 = st.tabs(["🔍 レース予測", "📋 朝バッチ", "📊 前日比較"])

    with tab2:
        tab_morning_batch()

    with tab3:
        tab_review()

    with tab1:
        # ── ① 会場・日付・レース番号 ────────────────────────────────────────────
        st.subheader("① 会場・レース")
        c1, c2, c3 = st.columns([2, 1.2, 1])
        with c1:
            venue = st.selectbox(
                "会場",
                VENUE_CODES,
                format_func=lambda jcd: f"{config.VENUE_CODES[jcd]}（{jcd}）",
            )
        with c2:
            race_date = st.date_input("日付", value=date.today())
        with c3:
            race_no = st.number_input("レース番号", 1, 12, 1, step=1)

        hd = race_date.strftime("%Y%m%d")

        # bubble ステータス取得: 当日JSONがあればそちらを優先、なければ過去CSVを参照
        race_bubble = get_race_bubble_today(venue, hd, int(race_no))
        today_bubble_loaded = race_bubble is not None
        if not race_bubble:
            bubble_data = load_bubble_data()
            race_bubble = get_race_bubble(bubble_data, venue, hd, int(race_no))

        # 節内日数の表示
        if race_bubble:
            day = next(iter(race_bubble.values())).get("day_from_start", 0)
            src = "当日取得済み" if today_bubble_loaded else "過去データ"
            if day > 0:
                st.info(f"📅 節内 **{day}日目**　（{src} — 予選ステータスは各艇カード右端に表示）")
        else:
            st.caption("📅 節内ステータス未取得 — 下の「節内ステータスを取得」で更新できます")

        # ── ② 自動取得ボタン ──────────────────────────────────────────────────
        st.subheader("② データ取得")
        col_a, col_b, col_c, col_d = st.columns([2, 2, 2, 2])

        with col_a:
            if st.button("📋 出走表を自動取得", use_container_width=True):
                with st.spinner("boatrace.jp から出走表を取得中..."):
                    entries = scrape_racelist(venue, hd, int(race_no))
                if entries:
                    for e in entries:
                        i = e["boat"]
                        st.session_state[f"avg_{i}"]   = float(e.get("avg_st") or 0.17)
                        st.session_state[f"m2r_{i}"]   = float(e.get("motor_2rate") or 38.0)
                        st.session_state[f"nat_{i}"]   = float(e.get("national_rate") or 5.0)
                        st.session_state[f"loc_{i}"]   = float(e.get("local_rate") or 5.0)
                        st.session_state[f"grade_{i}"] = e.get("grade", "")
                    st.session_state["fetch_status"] = "✅ 出走表を取得しました"
                    st.rerun()
                else:
                    st.session_state["fetch_status"] = "⚠️ 出走表の取得に失敗しました（開催日・会場を確認してください）"

        with col_b:
            if st.button("🏁 展示STも取得", use_container_width=True):
                with st.spinner("直前情報を取得中..."):
                    exhibit = scrape_beforeinfo(venue, hd, int(race_no))
                if exhibit:
                    for e in exhibit:
                        i = e["boat"]
                        v = e.get("exhibit_st")
                        if v:
                            st.session_state[f"ex_{i}"] = float(v)
                    st.session_state["fetch_status"] = "✅ 展示STを取得しました"
                    st.rerun()
                else:
                    st.session_state["fetch_status"] = "⚠️ 展示STはまだ公開されていません（レース30分前以降に取得できます）"

        with col_d:
            if st.button("📊 節内ステータスを取得", use_container_width=True,
                         help="boatrace.jpから当日の出走表を取得し、節内予選順位を計算（約1分）"):
                with st.spinner(f"{config.VENUE_CODES.get(venue, venue)} の節内ステータスを計算中..."):
                    try:
                        data = compute_bubble_status(venue, hd)
                        if data:
                            save_bubble_today(data)
                            st.session_state["fetch_status"] = (
                                f"✅ 節内ステータスを取得しました（{data['day_from_start']}日目）"
                            )
                        else:
                            st.session_state["fetch_status"] = "⚠️ 節内ステータスの取得に失敗しました"
                    except Exception as e:
                        st.session_state["fetch_status"] = f"⚠️ エラー: {e}"
                st.rerun()

        if st.session_state["fetch_status"]:
            st.info(st.session_state["fetch_status"])

        # ── ③ データ表示・手修正 ──────────────────────────────────────────────
        st.subheader("③ 各艇データ（自動取得後に手修正も可）")

        h0, h1, h2, h3, h4, h5, h6 = st.columns([0.7, 1.1, 1.1, 1.3, 1.3, 1.3, 1.2])
        for col, label in zip(
            [h0, h1, h2, h3, h4, h5, h6],
            ["艇番", "avg_st", "展示ST", "motor2連率%", "全国勝率", "当地勝率", "予選ステータス"],
        ):
            col.markdown(f"**{label}**")

        boat_data = {}
        for i in BOATS:
            c0, c1, c2, c3, c4, c5, c6 = st.columns([0.7, 1.1, 1.1, 1.3, 1.3, 1.3, 1.2])
            c0.markdown(
                f'<div style="background:{BOAT_COLORS[i-1]};color:{BOAT_TEXT[i-1]};'
                f'text-align:center;font-weight:bold;padding:6px;border-radius:4px;margin-top:4px;">'
                f'{i}号艇</div>',
                unsafe_allow_html=True,
            )
            avg = c1.number_input("", 0.0, 0.99, st.session_state[f"avg_{i}"],
                                   0.01, "%.2f", key=f"avg_{i}", label_visibility="collapsed")
            ex  = c2.number_input("", 0.0, 0.99, st.session_state[f"ex_{i}"],
                                   0.01, "%.2f", key=f"ex_{i}", label_visibility="collapsed")
            m2r = c3.number_input("", 0.0, 100.0, st.session_state[f"m2r_{i}"],
                                   0.1,  "%.1f", key=f"m2r_{i}", label_visibility="collapsed")
            nat = c4.number_input("", 0.0, 9.99, st.session_state[f"nat_{i}"],
                                   0.01, "%.2f", key=f"nat_{i}", label_visibility="collapsed")
            loc = c5.number_input("", 0.0, 9.99, st.session_state[f"loc_{i}"],
                                   0.01, "%.2f", key=f"loc_{i}", label_visibility="collapsed")

            # 予選ステータスバッジ
            c6.markdown(
                f'<div style="padding-top:6px;">{bubble_badge_html(race_bubble.get(i))}</div>',
                unsafe_allow_html=True,
            )

            effective_st = ex if ex > 0 else avg
            boat_data[i] = {"avg_st": effective_st, "motor_2rate": m2r,
                            "national_rate": nat, "local_rate": loc,
                            "grade": st.session_state[f"grade_{i}"]}

        # ── ④ 予測 ─────────────────────────────────────────────────────────────
        st.markdown("---")
        if not st.button("🔍 予測する", type="primary", use_container_width=True):
            st.info("「出走表を自動取得」後に「予測する」を押してください。")
            return

        if race_bubble:
            first = next(iter(race_bubble.values()))
            day        = first.get("day_from_start", 0)
            total_days = first.get("total_days", 0)
        else:
            day, total_days = 0, 0
        X = build_feature_vector(venue, boat_data,
                                 day_from_start=day, total_days=total_days,
                                 race_bubble=race_bubble)
        boat_prob = {int(c): float(p) for c, p in zip(clf_boat.classes_, clf_boat.predict_proba(X)[0])}

        # オッズ取得（レース前のみ有効）
        with st.spinner("3連単オッズを取得中..."):
            odds_3t = scrape_odds_3t(venue, race_date.strftime("%Y%m%d"), race_no)

        venue_name = config.VENUE_CODES.get(venue, venue)
        st.success(f"✅  {venue_name}  {race_date.strftime('%m/%d')}  {race_no}R  の予測結果")

        left, right = st.columns(2)
        with left:
            st.subheader("勝利艇 確率分布")
            for b in sorted(boat_prob, key=boat_prob.get, reverse=True):
                prob_bar(f"{b}号艇", boat_prob[b], BOAT_COLORS[b - 1], BOAT_TEXT[b - 1])

        with right:
            st.subheader("決まり手（勝利艇別・実績）")
            top_b = max(boat_prob, key=boat_prob.get)
            km_cond = km_by_boat.get(str(top_b), {})
            for km in KIMARITE:
                p_km = km_cond.get(km, 0.0)
                if p_km > 0:
                    prob_bar(km, p_km, KM_COLORS.get(km, "#555"))
            st.caption(f"※ {top_b}号艇が勝った場合の決まり手実績")

        # TOP 3連単（勝利艇確率 × 条件付き決まり手確率）
        combos = []
        for b in BOATS:
            km_cond = km_by_boat.get(str(b), {})
            top_km = km_cond.get("_modal", "逃げ")
            p_km = km_cond.get(top_km, 0.0)
            p = boat_prob.get(b, 0) * p_km
            combos.append((b, top_km, p))
        combos.sort(key=lambda x: x[2], reverse=True)

        st.subheader("🏆 TOP 買い目候補（本命艇 × 決まり手）")
        hc = st.columns([0.6, 1.2, 1.5, 2.5])
        for h, lbl in zip(hc, ["順位", "勝利艇", "決まり手", "確率"]):
            h.markdown(f"**{lbl}**")

        for rank, (b, km, p) in enumerate(combos, 1):
            cc = st.columns([0.6, 1.2, 1.5, 2.5])
            cc[0].write(f"{rank}位")
            cc[1].markdown(
                f'<span style="background:{BOAT_COLORS[b-1]};color:{BOAT_TEXT[b-1]};'
                f'border:1px solid #ccc;'
                f'padding:2px 10px;border-radius:4px;font-weight:bold;">{b}号艇</span>',
                unsafe_allow_html=True,
            )
            cc[2].write(km)
            bw = max(int(p * 100 * 4), 4)
            cc[3].markdown(
                f'<div style="background:#1565c0;width:{bw}px;height:18px;'
                f'border-radius:3px;display:inline-block;"></div>&nbsp;{p*100:.1f}%',
                unsafe_allow_html=True,
            )

        top_boat, top_km, _ = combos[0]

        # ── 3連単コンパス ──────────────────────────────────────────────────────
        st.markdown("---")
        st.subheader("🎯 3連単コンパス（統計的2・3着候補）")

        tri_col1, tri_col2 = st.columns([1, 2])
        with tri_col1:
            compass_boat = st.selectbox(
                "1着本命艇",
                BOATS,
                index=top_boat - 1,
                format_func=lambda b: f"{b}号艇",
                key="compass_boat",
            )
            compass_km_options = [km for km in KIMARITE
                                  if f"{compass_boat}_{km}" in km_by_boat.get(str(compass_boat), {})]
            compass_km = st.selectbox(
                "決まり手",
                KIMARITE,
                index=KIMARITE.index(km_by_boat.get(str(compass_boat), {}).get("_modal", "逃げ")),
                key="compass_km",
            )

        tri_key = f"{compass_boat}_{compass_km}"
        tri_data = meta.get("trifecta_stats", {}).get(tri_key, [])

        with tri_col2:
            if tri_data:
                st.markdown(f"**{compass_boat}号艇 {compass_km}** で決まった過去{sum(e['count'] for e in tri_data)}件の2・3着分布")
                hdr = st.columns([0.6, 1.5, 1.5, 1.5, 2.5])
                for col, lbl in zip(hdr, ["順位", "1着", "2着", "3着", "頻度"]):
                    col.markdown(f"**{lbl}**")
                for rank, entry in enumerate(tri_data[:8], 1):
                    r2, r3 = entry["combo"].split("-")
                    r2, r3 = int(float(r2)), int(float(r3))
                    row = st.columns([0.6, 1.5, 1.5, 1.5, 2.5])
                    row[0].write(f"{rank}位")
                    for col, b in zip(row[1:4], [compass_boat, r2, r3]):
                        col.markdown(
                            f'<span style="background:{BOAT_COLORS[b-1]};color:{BOAT_TEXT[b-1]};'
                            f'border:1px solid #ccc;padding:2px 8px;border-radius:4px;font-weight:bold;">'
                            f'{b}号艇</span>',
                            unsafe_allow_html=True,
                        )
                    bw = max(int(entry["pct"] * 100 * 5), 4)
                    row[4].markdown(
                        f'<div style="background:#e53935;width:{bw}px;height:16px;border-radius:3px;'
                        f'display:inline-block;"></div>&nbsp;{entry["pct"]*100:.1f}%',
                        unsafe_allow_html=True,
                    )
            else:
                st.info("この組み合わせの統計データが不足しています。")

        st.caption(
            "⚠️ 過去データの統計的傾向に基づく参考情報です。勝敗を保証するものではありません。賭けは自己責任で。"
        )

        # ── EV（期待値）買い目推薦 ────────────────────────────────────────────
        st.markdown("---")
        st.subheader("💰 期待値（EV）推薦買い目")

        if not odds_3t:
            st.info("オッズデータが取得できませんでした（レース開始後 or ページ未公開）。")
        else:
            st.caption(f"オッズ取得済み: {len(odds_3t)}通り  ※EV>0が理論上プラス期待値（控除率25%込み）")

            trifecta_stats_all = meta.get("trifecta_stats", {})
            km_by_boat_all     = meta.get("km_by_boat", {})
            from itertools import permutations as _perm

            ev_rows = []
            for r1 in BOATS:
                p_r1 = boat_prob.get(r1, 0.0)
                km_cond = km_by_boat_all.get(str(r1), {})

                # P(r2,r3|r1) = Σ_km P(km|r1) × P(r2,r3|r1,km)  全決まり手で重み付け平均
                all_combos = list(_perm([b for b in BOATS if b != r1], 2))
                p_cond: dict[tuple, float] = {c: 0.0 for c in all_combos}
                for km in KIMARITE:
                    p_km = km_cond.get(km, 0.0)
                    if p_km <= 0:
                        continue
                    tri_key  = f"{r1}_{km}"
                    tri_data = trifecta_stats_all.get(tri_key, [])
                    # このkmでの既知分布
                    km_cond_map: dict[tuple, float] = {}
                    km_known = 0.0
                    for entry in tri_data:
                        try:
                            parts = entry["combo"].split("-")
                            c = (int(float(parts[0])), int(float(parts[1])))
                            km_cond_map[c] = entry["pct"]
                            km_known += entry["pct"]
                        except (ValueError, KeyError):
                            pass
                    # 残りのコンボに均等配分
                    km_unknown = [c for c in all_combos if c not in km_cond_map]
                    remain = max(0.0, 1.0 - km_known)
                    unif_km = remain / len(km_unknown) if km_unknown else 0.0
                    for c in km_unknown:
                        km_cond_map[c] = unif_km
                    # 重み付け加算
                    for c in all_combos:
                        p_cond[c] += p_km * km_cond_map.get(c, unif_km)

                for r2, r3 in all_combos:
                    odds_val = odds_3t.get((r1, r2, r3))
                    if odds_val is None or odds_val <= 0:
                        continue
                    p_combo = p_r1 * p_cond.get((r2, r3), 1.0 / len(all_combos))
                    ev = p_combo * odds_val - 1.0
                    ev_rows.append({
                        "buy": f"{r1}-{r2}-{r3}",
                        "r1": r1, "r2": r2, "r3": r3,
                        "p_r1": p_r1,
                        "p_combo": p_combo,
                        "odds": odds_val,
                        "ev": ev,
                    })

            ev_rows.sort(key=lambda x: x["ev"], reverse=True)
            positive_ev = [row for row in ev_rows if row["ev"] > 0]

            if positive_ev:
                st.markdown(f"**EV>0の買い目: {len(positive_ev)}通り**（上位10件）")
                hdr = st.columns([1.2, 1.0, 1.0, 1.0, 1.2, 1.5, 1.5])
                for col, lbl in zip(hdr, ["買い目", "1着", "2着", "3着", "1着確率", "市場オッズ", "期待値EV"]):
                    col.markdown(f"**{lbl}**")
                for row in positive_ev[:10]:
                    r1, r2, r3 = row["r1"], row["r2"], row["r3"]
                    cols = st.columns([1.2, 1.0, 1.0, 1.0, 1.2, 1.5, 1.5])
                    cols[0].write(row["buy"])
                    for col, b in zip(cols[1:4], [r1, r2, r3]):
                        col.markdown(
                            f'<span style="background:{BOAT_COLORS[b-1]};color:{BOAT_TEXT[b-1]};'
                            f'padding:2px 7px;border-radius:4px;font-weight:bold;">{b}号艇</span>',
                            unsafe_allow_html=True,
                        )
                    cols[4].write(f"{row['p_r1']*100:.1f}%")
                    cols[5].write(f"{row['odds']:.1f}倍")
                    ev_pct = row["ev"] * 100
                    cols[6].markdown(
                        f'<span style="color:{"#e53935" if ev_pct>10 else "#388e3c"};font-weight:bold;">'
                        f'+{ev_pct:.1f}%</span>',
                        unsafe_allow_html=True,
                    )
            else:
                st.warning("現在のオッズではEV>0の買い目がありません（全買い目が期待値マイナス）。")
                # 上位5件を参考表示
                st.caption("参考: EV上位5件")
                for row in ev_rows[:5]:
                    st.write(f"{row['buy']}  オッズ{row['odds']:.1f}倍  EV{row['ev']*100:+.1f}%")


if __name__ == "__main__":
    main()
