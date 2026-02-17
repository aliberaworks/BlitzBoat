"""
BlitzBoat Scraper — boatrace.jp データ収集モジュール
中断再開対応 (progress.json)、レート制限、リトライ付き
"""
import json
import time
import re
import os
from datetime import datetime, timedelta
from typing import Optional

import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
import warnings

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

import config

# ── セッション初期化 ──
_session = requests.Session()
_session.headers.update({
    "User-Agent": config.USER_AGENT,
    "Accept-Language": "ja,en;q=0.9",
})


def _fetch(url: str) -> Optional[BeautifulSoup]:
    """URL取得 → BeautifulSoup。リトライ付き。"""
    for attempt in range(config.MAX_RETRIES):
        try:
            time.sleep(config.REQUEST_DELAY)
            resp = _session.get(url, timeout=config.REQUEST_TIMEOUT)
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or "utf-8"
            return BeautifulSoup(resp.text, "html.parser")
        except Exception as e:
            wait = 2 ** attempt * 2
            print(f"  [RETRY {attempt+1}] {url} -> {e}, wait {wait}s")
            time.sleep(wait)
    print(f"  [FAIL] {url}")
    return None


def _parse_concatenated_rates(text: str) -> list[float]:
    """
    Boatrace.jpの連結された勝率文字列をパース。
    例: '4.1116.6738.10' → [4.11, 16.67, 38.10]
    例: '5.2537.5037.50' → [5.25, 37.50, 37.50]
    例: '3621.8837.50' → [36, 21.88, 37.50] (モーター: No+2連+3連)
    
    数値は常にX.XX (小数点以下2桁) の形式。
    """
    values = re.findall(r'(\d+\.\d{2})', text)
    return [float(v) for v in values]


# ═══════════════════════════════════════════
#  1. 当日開催会場一覧
# ═══════════════════════════════════════════
def scrape_today_venues(hd: str) -> list[dict]:
    """
    開催中の会場とレース数を取得。
    Returns: [{"jcd": "01", "name": "桐生", "races": 12}, ...]
    """
    url = f"https://boatrace.jp/owpc/pc/race/index?hd={hd}"
    soup = _fetch(url)
    if not soup:
        return []

    venues = []
    links = soup.find_all("a", href=re.compile(r"raceindex\?jcd=\d+&hd="))
    seen = set()
    for link in links:
        href = link.get("href", "")
        m = re.search(r"jcd=(\d+)", href)
        if m:
            jcd = m.group(1)
            if jcd not in seen:
                seen.add(jcd)
                name = config.VENUE_CODES.get(jcd, f"Venue{jcd}")
                venues.append({"jcd": jcd, "name": name, "races": 12})
    return venues


# ═══════════════════════════════════════════
#  2. 出走表スクレイピング
# ═══════════════════════════════════════════
def scrape_racelist(jcd: str, hd: str, rno: int) -> list[dict]:
    """
    出走表から各艇の選手情報・勝率・モーター情報を取得。
    
    HTML構造:
    - 各艇は tbody.is-fs12 内に4行で構成
    - Row0: Cell0=艇番, Cell2=選手情報, Cell3=F/L+ST,
            Cell4=全国(勝率+2連+3連), Cell5=当地(勝率+2連+3連),
            Cell6=モーター(No+2連+3連), Cell7=ボート(No+2連+3連)
    - 連結文字列例: '4.1116.6738.10' = 勝率4.11, 2連率16.67, 3連率38.10
    
    Returns: 6要素のリスト (1~6号艇)
    """
    url = config.URL_RACELIST.format(jcd=jcd, hd=hd, rno=rno)
    soup = _fetch(url)
    if not soup:
        return []

    entries = []
    
    # 各艇は tbody.is-fs12 に格納されている
    tbodies = soup.find_all("tbody", class_="is-fs12")
    
    for boat_idx, tbody in enumerate(tbodies):
        if boat_idx >= 6:
            break
        
        boat_num = boat_idx + 1
        rows = tbody.find_all("tr")
        if not rows:
            continue
        
        # メイン行 (Row 0) からデータ抽出
        main_row = rows[0]
        cells = main_row.find_all("td")
        
        # 選手名と登録番号
        name_link = tbody.find("a", href=re.compile(r"racersearch/profile"))
        name = ""
        toban = ""
        if name_link:
            name = name_link.get_text(strip=True)
            toban_match = re.search(r"toban=(\d+)", name_link.get("href", ""))
            toban = toban_match.group(1) if toban_match else ""
        
        # is-lineH2 クラスのセルにデータが格納されている
        data_cells = main_row.find_all("td", class_="is-lineH2")
        
        national_rate = 0.0
        local_rate = 0.0
        motor_no = ""
        motor_2rate = 0.0
        avg_st = 0.0
        
        # data_cells[0] = F/L + ST (例: 'F0L00.16')
        # data_cells[1] = 全国 (例: '4.1116.6738.10')
        # data_cells[2] = 当地 (例: '5.2537.5037.50')
        # data_cells[3] = モーター (例: '3621.8837.50')
        # data_cells[4] = ボート (例: '7334.0052.00')
        
        if len(data_cells) >= 1:
            # ST情報をパース
            st_text = data_cells[0].get_text(strip=True)
            st_vals = re.findall(r'(0\.\d{2})', st_text)
            if st_vals:
                avg_st = float(st_vals[0])
        
        if len(data_cells) >= 2:
            # 全国勝率
            national_text = data_cells[1].get_text(strip=True)
            national_vals = _parse_concatenated_rates(national_text)
            if national_vals:
                national_rate = national_vals[0]  # 最初の値が勝率
        
        if len(data_cells) >= 3:
            # 当地勝率
            local_text = data_cells[2].get_text(strip=True)
            local_vals = _parse_concatenated_rates(local_text)
            if local_vals:
                local_rate = local_vals[0]  # 最初の値が勝率
        
        if len(data_cells) >= 4:
            # モーター情報: '3621.8837.50' = No:36 + 2連:21.88 + 3連:37.50
            motor_text = data_cells[3].get_text(strip=True)
            # 最初の小数点の位置を探す
            dot_pos = motor_text.find('.')
            if dot_pos > 0:
                # 小数点の前2桁が2連率の整数部分、その前がモーター番号
                # 例: '3621.88' → dot at 4, 整数部'21'の前='36'
                # モーター番号は通常2桁 (01-99)
                prefix = motor_text[:dot_pos]
                if len(prefix) >= 4:
                    motor_no = prefix[:-2]  # 最後の2桁は2連率の整数部分
                    rate_part = motor_text[len(motor_no):]
                    motor_vals = _parse_concatenated_rates(rate_part)
                elif len(prefix) >= 3:
                    motor_no = prefix[:-1]  # 1桁の2連率
                    rate_part = motor_text[len(motor_no):]
                    motor_vals = _parse_concatenated_rates(rate_part)
                else:
                    motor_no = prefix
                    motor_vals = _parse_concatenated_rates(motor_text)
            else:
                motor_no_match = re.match(r'^(\d{2,3})', motor_text)
                if motor_no_match:
                    motor_no = motor_no_match.group(1)
                motor_vals = _parse_concatenated_rates(motor_text)
            if motor_vals:
                motor_2rate = motor_vals[0]
        
        entries.append({
            "boat": boat_num,
            "name": name,
            "toban": toban,
            "national_rate": national_rate,
            "local_rate": local_rate,
            "motor_no": motor_no,
            "motor_2rate": motor_2rate,
            "avg_st": avg_st,
        })

    return entries


# ═══════════════════════════════════════════
#  3. 直前情報スクレイピング (展示ST)
# ═══════════════════════════════════════════
def scrape_beforeinfo(jcd: str, hd: str, rno: int) -> list[dict]:
    """
    直前情報ページから展示STを取得。
    Returns: [{"boat": 1, "exhibit_st": 0.15}, ...]
    """
    url = config.URL_BEFOREINFO.format(jcd=jcd, hd=hd, rno=rno)
    soup = _fetch(url)
    if not soup:
        return []

    results = []
    # 展示タイムのテーブルを探す
    # boatrace.jpでは直前情報ページに展示タイムとスタート展示の表がある
    tables = soup.find_all("table")

    for table in tables:
        rows = table.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if not cells:
                continue

            # STの値を探す (0.XX形式)
            for cell in cells:
                text = cell.get_text(strip=True)
                st_match = re.match(r'^(0\.\d{2})$', text)
                if st_match:
                    boat = len(results) + 1
                    if boat <= 6:
                        results.append({
                            "boat": boat,
                            "exhibit_st": float(st_match.group(1)),
                        })

    # スタート展示テーブルからSTを取得する別パターン
    if len(results) < 6:
        results = []
        all_st = re.findall(r'(?<!\d)(0\.\d{2})(?!\d)', soup.get_text())
        # STは通常0.10~0.30の範囲
        st_values = [float(v) for v in all_st if 0.05 <= float(v) <= 0.40]
        for i, st in enumerate(st_values[:6]):
            results.append({
                "boat": i + 1,
                "exhibit_st": st,
            })

    return results


# ═══════════════════════════════════════════
#  4. レース結果スクレイピング
# ═══════════════════════════════════════════

# 着順表示用: 全角数字→半角
_ZENKAKU_MAP = {"１": 1, "２": 2, "３": 3, "４": 4, "５": 5, "６": 6}


def scrape_race_result(jcd: str, hd: str, rno: int) -> dict:
    """
    レース結果ページから着順・決まり手・ST・3連単を取得。
    
    HTML構造 (boatrace.jp/owpc/pc/race/raceresult):
    - Table[is-w495] #1: 着順表 (着, 枠, 選手名, タイム)
    - Table[is-w495] #2: スタート情報 (各艇ST)
    - Table[is-w495] #3: 払戻金 (3連単, 3連複, 2連単, 2連複, 拡連複, 単勝, 複勝)
    - Table[is-w243]: 決まり手
    
    Returns: {
        "results": [{"rank": 1, "boat": 6}, ...],
        "kimarite": "まくり差し",
        "winning_boat": 6,
        "trifecta": "6-4-2",
        "start_times": [{"boat": 1, "st": 0.25}, ...],
    }
    """
    url = config.URL_RACE_RESULT.format(jcd=jcd, hd=hd, rno=rno)
    soup = _fetch(url)
    if not soup:
        return {}

    result_data = {
        "results": [],
        "kimarite": "",
        "winning_boat": 0,
        "start_times": [],
        "trifecta": "",
    }

    # ── 全テーブルを分類 ──
    tables_w495 = soup.find_all("table", class_="is-w495")
    tables_w243 = soup.find_all("table", class_="is-w243")

    # ── 1. 決まり手 (is-w243 テーブル) ──
    for table in tables_w243:
        text = table.get_text(strip=True)
        if "決まり手" in text:
            rows = table.find_all("tr")
            if len(rows) >= 2:
                km_text = rows[1].get_text(strip=True)
                # まくり差しを先にチェック (まくりとの誤判定防止)
                for km in ["まくり差し", "まくり", "差し", "逃げ", "抜き", "恵まれ"]:
                    if km in km_text:
                        result_data["kimarite"] = km
                        break

    # ── 2. 着順 + 3連単 + ST (is-w495 テーブル) ──
    for table in tables_w495:
        rows = table.find_all("tr")
        if not rows:
            continue
        header_text = rows[0].get_text(strip=True)

        # --- 着順表: ヘッダーに「着」を含む ---
        if "着" in header_text and "枠" in header_text:
            finish_order = []
            for row in rows[1:]:
                cells = row.find_all("td")
                if len(cells) < 2:
                    continue
                rank_text = cells[0].get_text(strip=True)
                boat_text = cells[1].get_text(strip=True)
                
                # 全角数字 → 着順
                rank = _ZENKAKU_MAP.get(rank_text, 0)
                if rank == 0:
                    try:
                        rank = int(rank_text)
                    except ValueError:
                        continue
                
                # 枠番 (半角数字)
                try:
                    boat = int(boat_text)
                except ValueError:
                    continue
                
                if 1 <= rank <= 6 and 1 <= boat <= 6:
                    finish_order.append({"rank": rank, "boat": boat})
            
            if len(finish_order) >= 3:
                # rank順にソート
                finish_order.sort(key=lambda x: x["rank"])
                result_data["results"] = finish_order
                result_data["winning_boat"] = finish_order[0]["boat"]
                # 着順表から3連単を構築 (フォールバック)
                if not result_data["trifecta"]:
                    result_data["trifecta"] = (
                        f"{finish_order[0]['boat']}-"
                        f"{finish_order[1]['boat']}-"
                        f"{finish_order[2]['boat']}"
                    )

        # --- 払戻金表: ヘッダーに「勝式」「払戻金」を含む ---
        elif "勝式" in header_text or "払戻金" in header_text:
            for row in rows[1:]:
                cells = row.find_all("td")
                if len(cells) < 3:
                    continue
                bet_type = cells[0].get_text(strip=True)
                combo = cells[1].get_text(strip=True)
                
                # 3連単の出目を取得 (最も正確)
                if "3連単" in bet_type:
                    # combo = "6-4-2" or "6−4−2" (全角ハイフン)
                    trifecta = combo.replace("−", "-").replace("ー", "-")
                    if re.match(r'^[1-6]-[1-6]-[1-6]$', trifecta):
                        result_data["trifecta"] = trifecta
                        boats = trifecta.split("-")
                        result_data["winning_boat"] = int(boats[0])

        # --- スタート情報表 ---
        elif "スタート" in header_text or "コース" in header_text:
            for row in rows[1:]:
                text = row.get_text(strip=True)
                # パターン: "X.YY" = 艇番.ST (例: 1.25 = 1号艇 ST 0.25)
                # 実際は "コース番.ST値の小数表記"
                m = re.match(r'^(\d)\.([\d]{2})$', text)
                if m:
                    boat = int(m.group(1))
                    st_val = float(f"0.{m.group(2)}")
                    if 1 <= boat <= 6 and 0.01 <= st_val <= 0.50:
                        result_data["start_times"].append({
                            "boat": boat,
                            "st": st_val,
                        })

    return result_data


# ═══════════════════════════════════════════
#  5. Progress管理 (中断再開対応)
# ═══════════════════════════════════════════
def load_progress() -> dict:
    """progress.jsonを読み込む"""
    if os.path.exists(config.PROGRESS_FILE):
        with open(config.PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"completed": [], "last_date": "", "total_fetched": 0}


def save_progress(progress: dict):
    """progress.jsonを保存"""
    with open(config.PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def is_completed(progress: dict, jcd: str, hd: str) -> bool:
    """指定の会場・日付が収集済みか"""
    key = f"{jcd}_{hd}"
    return key in progress.get("completed", [])


def mark_completed(progress: dict, jcd: str, hd: str):
    """指定の会場・日付を収集済みにする"""
    key = f"{jcd}_{hd}"
    if key not in progress["completed"]:
        progress["completed"].append(key)
    progress["last_date"] = hd
    progress["total_fetched"] = len(progress["completed"])
    save_progress(progress)


# ═══════════════════════════════════════════
#  6. 過去データ一括収集
# ═══════════════════════════════════════════
def collect_historical_results(days: int = None) -> dict:
    """
    過去N日間の全会場レース結果を収集し、JSONに保存。
    中断しても再開可能。
    Returns: 収集統計
    """
    if days is None:
        days = config.COLLECTION_DAYS

    progress = load_progress()
    all_results = load_all_results()

    today = datetime.now()
    start_date = today - timedelta(days=days)
    stats = {"new": 0, "skipped": 0, "errors": 0}

    for day_offset in range(days):
        target_date = start_date + timedelta(days=day_offset)
        hd = target_date.strftime("%Y%m%d")

        for jcd in config.VENUE_CODES:
            if is_completed(progress, jcd, hd):
                stats["skipped"] += 1
                continue

            venue_name = config.VENUE_CODES[jcd]
            print(f"[{hd}] {venue_name} ({jcd}) ...")

            day_data = []
            has_data = False

            for rno in range(1, 13):
                # レース結果を取得
                result = scrape_race_result(jcd, hd, rno)
                if not result or not result.get("results"):
                    continue

                has_data = True

                # 出走表から勝率情報を取得
                entries = scrape_racelist(jcd, hd, rno)

                # 直前情報からSTを取得
                st_info = scrape_beforeinfo(jcd, hd, rno)

                race_record = {
                    "date": hd,
                    "venue": jcd,
                    "venue_name": venue_name,
                    "race_no": rno,
                    "entries": entries,
                    "st_info": st_info,
                    "result": result,
                }
                day_data.append(race_record)

            if day_data:
                key = f"{jcd}_{hd}"
                all_results[key] = day_data
                stats["new"] += len(day_data)
                save_all_results(all_results)
            elif not has_data:
                # 開催なし → スキップ記録
                pass

            mark_completed(progress, jcd, hd)

    total = stats["new"] + stats["skipped"]
    print(f"\n収集完了: 新規{stats['new']} / スキップ{stats['skipped']} / 合計{total}")
    return stats


# ═══════════════════════════════════════════
#  7. 結果データのロード/セーブ
# ═══════════════════════════════════════════
def load_all_results() -> dict:
    """全レース結果を読み込む"""
    if os.path.exists(config.RESULTS_FILE):
        with open(config.RESULTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_all_results(data: dict):
    """全レース結果を保存"""
    with open(config.RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════
#  8. 単日結果収集 (日次更新用)
# ═══════════════════════════════════════════
def collect_daily_results(hd: str) -> list[dict]:
    """
    指定日の全会場レース結果を収集。
    日次更新(GitHub Actions)で使用。
    """
    venues = scrape_today_venues(hd)
    if not venues:
        # 全会場を試行
        venues = [{"jcd": jcd, "name": name} for jcd, name in config.VENUE_CODES.items()]

    daily_results = []
    for venue in venues:
        jcd = venue["jcd"]
        venue_name = venue.get("name", config.VENUE_CODES.get(jcd, ""))
        print(f"  [{hd}] {venue_name} ...")

        for rno in range(1, 13):
            result = scrape_race_result(jcd, hd, rno)
            if not result or not result.get("results"):
                continue

            entries = scrape_racelist(jcd, hd, rno)
            st_info = scrape_beforeinfo(jcd, hd, rno)

            daily_results.append({
                "date": hd,
                "venue": jcd,
                "venue_name": venue_name,
                "race_no": rno,
                "entries": entries,
                "st_info": st_info,
                "result": result,
            })

    return daily_results


if __name__ == "__main__":
    # テスト: 単一レースの取得
    print("=== テスト: 出走表 ===")
    entries = scrape_racelist("01", "20260217", 1)
    for e in entries:
        print(f"  {e['boat']}号艇: {e['name']} 全国{e['national_rate']} 当地{e['local_rate']}")

    print("\n=== テスト: 直前情報 ===")
    st = scrape_beforeinfo("01", "20260217", 1)
    for s in st:
        print(f"  {s['boat']}号艇: ST {s['exhibit_st']}")

    print("\n=== テスト: レース結果 ===")
    res = scrape_race_result("01", "20260217", 1)
    print(f"  決まり手: {res.get('kimarite', 'N/A')}")
    print(f"  3連単: {res.get('trifecta', 'N/A')}")
