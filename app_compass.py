"""
ボートレース 3連単コンパス（公開版）
1着・決まり手を選ぶと、統計的な2・3着分布とリアルタイムオッズ・期待値を表示。
"""
import json, os, sys
from datetime import date, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path = [_HERE] + [p for p in sys.path if p != _HERE]

import streamlit as st
import importlib.util as _ilu

import config

_spec = _ilu.spec_from_file_location("scraper", os.path.join(_HERE, "scraper.py"))
_mod  = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_mod)
scrape_today_venues = _mod.scrape_today_venues
scrape_race_times   = _mod.scrape_race_times
scrape_racelist     = _mod.scrape_racelist
scrape_odds_3t      = _mod.scrape_odds_3t

st.set_page_config(
    page_title="3連単コンパス | ボートレース統計",
    page_icon="🧭", layout="wide",
)

BOATS  = list(range(1, 7))
KIMARITE = ["逃げ", "差し", "まくり", "まくり差し", "抜き", "恵まれ"]
BOAT_COLORS = ["#FFFFFF","#1A1A1A","#E53935","#1976D2","#FDD835","#43A047"]
BOAT_TEXT   = ["black","white","white","white","black","white"]
BOAT_LABEL  = ["①白","②黒","③赤","④青","⑤黄","⑥緑"]

# ── スケジュール取得 ──────────────────────────────────────────────────────────
@st.cache_data(ttl=600)
def get_schedule(hd: str):
    venues = scrape_today_venues(hd)
    schedule = {}
    for v in (venues or []):
        jcd  = v["jcd"]
        name = v.get("name", config.VENUE_CODES.get(jcd, jcd))
        times = scrape_race_times(jcd, hd)
        schedule[jcd] = {"name": name, "races": v.get("races", 12), "times": times}
    return schedule

@st.cache_data(ttl=600)
def find_next_race_date(from_hd: str) -> str:
    for delta in range(1, 8):
        d = (date.fromisoformat(f"{from_hd[:4]}-{from_hd[4:6]}-{from_hd[6:]}") +
             timedelta(days=delta)).strftime("%Y%m%d")
        if scrape_today_venues(d):
            return d
    return ""

@st.cache_data(ttl=60)
def get_odds(jcd: str, hd: str, rno: int):
    return scrape_odds_3t(jcd, hd, rno)

@st.cache_data(ttl=300)
def get_racelist(jcd: str, hd: str, rno: int):
    return scrape_racelist(jcd, hd, rno)

@st.cache_data
def load_meta():
    with open(os.path.join(config.DATA_DIR, "model_meta.json"), encoding="utf-8") as f:
        return json.load(f)

# ── サイドバー：スケジュール ──────────────────────────────────────────────────
hd = date.today().strftime("%Y%m%d")
hd_disp = f"{hd[:4]}/{hd[4:6]}/{hd[6:]}"

st.sidebar.markdown(f"## 🗓 {hd_disp} 開催情報")

with st.sidebar:
    with st.spinner("開催情報を取得中..."):
        schedule = get_schedule(hd)

if not schedule:
    st.sidebar.warning("本日の開催なし")
    next_d = find_next_race_date(hd)
    if next_d:
        st.sidebar.info(f"次回開催: {next_d[:4]}/{next_d[4:6]}/{next_d[6:]}")
    selected_jcd = None
else:
    for jcd, info in schedule.items():
        times_list = [t for t in info["times"].values() if t]
        next_t = min(times_list) if times_list else "—"
        st.sidebar.markdown(
            f"**{info['name']}**　{info['races']}R　最終 {next_t}"
        )
    st.sidebar.markdown("---")

    venue_options = {info["name"]: jcd for jcd, info in schedule.items()}
    selected_venue_name = st.sidebar.selectbox("会場を選ぶ", list(venue_options.keys()))
    selected_jcd = venue_options[selected_venue_name]
    selected_info = schedule[selected_jcd]

    race_options = {}
    for rno in range(1, selected_info["races"] + 1):
        t = selected_info["times"].get(rno, "")
        label = f"{rno}R　{t}" if t else f"{rno}R"
        race_options[label] = rno
    selected_race_label = st.sidebar.selectbox("レースを選ぶ", list(race_options.keys()))
    selected_rno = race_options[selected_race_label]

    st.sidebar.markdown("---")
    st.sidebar.markdown("### オッズ取得")
    fetch_odds = st.sidebar.button("🔄 リアルタイムオッズ取得")

# ── メインエリア ──────────────────────────────────────────────────────────────
st.title("🧭 3連単コンパス")
st.caption("1着と決まり手を選ぶと、統計的な2・3着分布とリアルタイムオッズ・期待値を表示します。")
st.caption("※ 本ツールは過去データに基づく統計情報の提供です。1着・決まり手はご自身の予想でお選びください。")

if not schedule or selected_jcd is None:
    st.info("本日の開催情報がありません。")
    st.stop()

meta = load_meta()
trifecta_stats = meta.get("trifecta_stats_all", {})
km_by_boat     = meta.get("km_by_boat", {})

# 出走表取得
entries = get_racelist(selected_jcd, hd, selected_rno)
boat_names = {}
if entries:
    for e in entries:
        boat_names[e["boat"]] = e.get("player_name", "")

# ── セレクター：1着・決まり手 ─────────────────────────────────────────────────
col_b, col_k = st.columns([2, 3])
with col_b:
    st.markdown("#### 1着予想")
    boat_cols = st.columns(6)
    if "sel_boat" not in st.session_state:
        st.session_state.sel_boat = 1
    for i, b in enumerate(BOATS):
        with boat_cols[i]:
            name = boat_names.get(b, "")
            label = f"{BOAT_LABEL[b-1]}\n{name}" if name else BOAT_LABEL[b-1]
            if st.button(label, key=f"boat_{b}",
                         use_container_width=True,
                         type="primary" if st.session_state.sel_boat == b else "secondary"):
                st.session_state.sel_boat = b
                st.rerun()

with col_k:
    st.markdown("#### 決まり手")
    km_cols = st.columns(3)
    if "sel_km" not in st.session_state:
        st.session_state.sel_km = "逃げ"
    km_flat = KIMARITE
    for i, km in enumerate(km_flat):
        with km_cols[i % 3]:
            if st.button(km, key=f"km_{km}",
                         use_container_width=True,
                         type="primary" if st.session_state.sel_km == km else "secondary"):
                st.session_state.sel_km = km
                st.rerun()

r1  = st.session_state.sel_boat
km  = st.session_state.sel_km
key = f"{r1}_{km}"

# 統計上の発生率
p_km = km_by_boat.get(str(r1), {}).get(km, 0.0)
tri_data = trifecta_stats.get(key, [])

st.markdown("---")

# オッズ取得
odds_dict = {}
if "odds_cache" not in st.session_state:
    st.session_state.odds_cache = {}
cache_key = f"{selected_jcd}_{hd}_{selected_rno}"

if fetch_odds:
    with st.spinner("オッズ取得中..."):
        odds_dict = get_odds(selected_jcd, hd, selected_rno)
        st.session_state.odds_cache[cache_key] = odds_dict
        st.success(f"{len(odds_dict)}件取得")
elif cache_key in st.session_state.odds_cache:
    odds_dict = st.session_state.odds_cache[cache_key]

has_odds = len(odds_dict) > 0

# ── コンパス表示 ──────────────────────────────────────────────────────────────
st.markdown(
    f"### {selected_venue_name} {selected_rno}R　"
    f"1着: **{BOAT_LABEL[r1-1]}**　決まり手: **{km}**　"
    f"（この組み合わせの発生率: {p_km*100:.1f}%）"
)

if not tri_data:
    st.warning("この組み合わせの統計データがありません。")
    st.stop()

# 確率マップ構築
stat_map = {}
known = 0.0
for entry in tri_data:
    parts = entry["combo"].split("-")
    r2, r3 = int(float(parts[0])), int(float(parts[1]))
    stat_map[(r2, r3)] = entry["pct"]
    known += entry["pct"]
others = [b for b in BOATS if b != r1]
all_combos = [(r2, r3) for r2 in others for r3 in others if r2 != r3]
remain = max(0.0, 1.0 - known)
unif   = remain / max(len([c for c in all_combos if c not in stat_map]), 1)
for c in all_combos:
    if c not in stat_map:
        stat_map[c] = unif

# テーブル構築
rows_html = []
sorted_r2 = sorted(others)
sorted_r3_for = {r2: sorted([b for b in others if b != r2]) for r2 in sorted_r2}

# ヘッダー行
header_cells = ["<th style='background:#222;color:#fff;padding:8px;min-width:80px;'>2着↓/3着→</th>"]
for r3_ref in sorted(others):
    c = BOAT_COLORS[r3_ref-1]; tc = BOAT_TEXT[r3_ref-1]
    header_cells.append(
        f"<th style='background:{c};color:{tc};padding:8px;text-align:center;'>"
        f"{BOAT_LABEL[r3_ref-1]}</th>"
    )
rows_html.append("<tr>" + "".join(header_cells) + "</tr>")

ev_rows = []
for r2 in sorted_r2:
    cells = []
    c2 = BOAT_COLORS[r2-1]; tc2 = BOAT_TEXT[r2-1]
    cells.append(
        f"<td style='background:{c2};color:{tc2};font-weight:bold;"
        f"padding:8px;text-align:center;'>{BOAT_LABEL[r2-1]}</td>"
    )
    for r3_ref in sorted(others):
        if r3_ref == r2:
            cells.append("<td style='background:#f0f0f0;'></td>")
            continue
        stat_p = stat_map.get((r2, r3_ref), 0.0)
        odds_val = odds_dict.get((r1, r2, r3_ref))

        stat_str = f"{stat_p*100:.1f}%"

        if odds_val and has_odds:
            ev = stat_p * odds_val - 1.0
            ev_str = f"{ev:+.1f}"
            odds_str = f"{odds_val:.1f}倍"
            if ev >= 1.0:
                bg = "#e8f5e9"; border = "2px solid #2e7d32"
                ev_color = "#1b5e20"; ev_bold = "font-weight:bold;"
                ev_rows.append((ev, r1, r2, r3_ref, stat_p, odds_val))
            elif ev >= 0:
                bg = "#fff9c4"; border = "1px solid #f9a825"
                ev_color = "#e65100"; ev_bold = ""
                ev_rows.append((ev, r1, r2, r3_ref, stat_p, odds_val))
            else:
                bg = "#fff"; border = "1px solid #ddd"
                ev_color = "#999"; ev_bold = ""
            cells.append(
                f"<td style='background:{bg};border:{border};"
                f"padding:6px;text-align:center;font-size:12px;'>"
                f"<div>{stat_str}</div>"
                f"<div style='color:#555;'>{odds_str}</div>"
                f"<div style='color:{ev_color};{ev_bold}'>EV{ev_str}</div>"
                f"</td>"
            )
        else:
            cells.append(
                f"<td style='padding:6px;text-align:center;font-size:13px;"
                f"background:#fff;border:1px solid #eee;'>{stat_str}</td>"
            )
    rows_html.append("<tr>" + "".join(cells) + "</tr>")

table_html = (
    "<div style='overflow-x:auto;'>"
    "<table style='border-collapse:collapse;width:100%;'>"
    + "".join(rows_html) +
    "</table></div>"
)
st.markdown(table_html, unsafe_allow_html=True)

if not has_odds:
    st.caption("「リアルタイムオッズ取得」ボタンを押すとオッズとEV（期待値）が表示されます。")

# ── EV上位 ──────────────────────────────────────────────────────────────────
if ev_rows:
    st.markdown("---")
    st.markdown("#### 期待値プラスの買い目")
    ev_rows.sort(reverse=True)
    cols = st.columns(min(len(ev_rows), 5))
    for i, (ev, b1, b2, b3, stat_p, odds_v) in enumerate(ev_rows[:5]):
        with cols[i]:
            color = "#e8f5e9" if ev >= 1.0 else "#fff9c4"
            border = "#2e7d32" if ev >= 1.0 else "#f9a825"
            st.markdown(
                f"<div style='background:{color};border:2px solid {border};"
                f"border-radius:8px;padding:12px;text-align:center;'>"
                f"<div style='font-size:20px;font-weight:bold;'>"
                f"{BOAT_LABEL[b1-1]}-{BOAT_LABEL[b2-1]}-{BOAT_LABEL[b3-1]}</div>"
                f"<div style='font-size:14px;color:#555;'>{odds_v:.1f}倍</div>"
                f"<div style='font-size:16px;font-weight:bold;color:#1b5e20;'>"
                f"EV {ev:+.2f}</div>"
                f"<div style='font-size:11px;color:#777;'>統計確率 {stat_p*100:.1f}%</div>"
                f"</div>",
                unsafe_allow_html=True
            )

# ── 凡例・注意書き ───────────────────────────────────────────────────────────
st.markdown("---")
with st.expander("📖 見方・注意事項"):
    st.markdown("""
**統計確率 (%)** : 過去の同条件（1着艇×決まり手）のレース結果から算出した2・3着の出現率。

**オッズ** : 取得時点のリアルタイム3連単オッズ（boatrace.jp）。

**EV（期待値）** = 統計確率 × オッズ − 1
- EV **+1.0以上**（緑）: 統計的に有利な買い目
- EV **0〜+1.0**（黄）: やや有利
- EV **マイナス**（グレー）: 統計的に不利

**⚠️ 重要な注意事項**
- 本ツールは**過去の統計情報**の提供のみを目的としています
- 1着予想・決まり手予想はご自身で行ってください
- 統計的優位性があっても必ずしも的中するとは限りません
- 公営競技への参加は適切な資金管理のもとで行ってください

**データ出所**: boatrace.jp 公式サイト
    """)

# ── アフィリエイトリンク ────────────────────────────────────────────────────
st.markdown("---")
col_a, col_b_aff = st.columns([3, 2])
with col_a:
    st.caption("ボートレース公式サイトで投票・レース情報の確認ができます。")
with col_b_aff:
    st.link_button(
        "🎯 ボートレース公式サイト",
        "https://www.boatrace.jp",
        use_container_width=True,
    )
