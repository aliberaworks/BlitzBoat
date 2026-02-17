"""
BlitzBoat Main CLI — 全モジュール統合
Usage:
  python main.py --collect              # 過去6ヶ月データ収集 (中断再開OK)
  python main.py --analyze 20260218     # 指定日のチャンスレース分析
  python main.py --daily                # 日次自動実行
  python main.py --stats                # 会場統計再計算
  python main.py --test                 # スクレイパーテスト
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta

import config
from scraper import (
    scrape_racelist,
    scrape_beforeinfo,
    scrape_race_result,
    scrape_today_venues,
    collect_historical_results,
    collect_daily_results,
    load_all_results,
    save_all_results,
)
from analyzer import identify_chance_races, is_boat1_weak, is_st_slow
from statistics_engine import (
    build_venue_stats,
    get_venue_ranking,
    save_venue_stats,
    load_venue_stats,
    print_venue_ranking,
)
from ticket_generator import generate_tickets, print_tickets
from line_bot import notify_chance_races, format_chance_race_message
from shorts_generator import generate_shorts_video
from x_poster import run_x_post, generate_summary_image
from note_drafter import run_note_draft


def cmd_collect(args):
    """過去データ一括収集"""
    days = args.days or config.COLLECTION_DAYS
    print(f"═══════════════════════════════════════════")
    print(f"  BlitzBoat — 過去{days}日間データ収集")
    print(f"  中断しても `--collect` で再開可能")
    print(f"═══════════════════════════════════════════")
    
    stats = collect_historical_results(days)
    
    # 統計を再計算
    print("\n会場統計を再計算中...")
    results = load_all_results()
    venue_stats = build_venue_stats(results)
    save_venue_stats(venue_stats)
    print(f"完了: {len(venue_stats)} 会場の統計を更新")


def cmd_analyze(args):
    """指定日のチャンスレース分析"""
    target_date = args.date or datetime.now().strftime("%Y%m%d")
    
    print(f"═══════════════════════════════════════════")
    print(f"  BlitzBoat — {target_date} チャンスレース分析")
    print(f"═══════════════════════════════════════════")
    
    # 会場統計ロード
    venue_stats = load_venue_stats()
    if not venue_stats:
        print("⚠ 会場統計データが未作成です。先に --collect を実行してください。")
        print("  デモモードとして統計なしで分析を実行します。")
    
    # 当日の開催会場を取得
    venues = scrape_today_venues(target_date)
    if not venues:
        print(f"  {target_date} の開催情報を取得できませんでした。")
        print(f"  全会場を試行します...")
        venues = [{"jcd": jcd, "name": name} for jcd, name in config.VENUE_CODES.items()]
    
    print(f"\n  開催会場: {', '.join(v['name'] for v in venues)}")
    
    # 全レースのデータを収集
    all_races = []
    for venue in venues:
        jcd = venue["jcd"]
        venue_name = venue.get("name", "")
        print(f"\n  [{venue_name}] スキャン中...")
        
        for rno in range(1, 13):
            entries = scrape_racelist(jcd, target_date, rno)
            if not entries:
                continue
            
            st_info = scrape_beforeinfo(jcd, target_date, rno)
            
            race_data = {
                "date": target_date,
                "venue": jcd,
                "venue_name": venue_name,
                "race_no": rno,
                "entries": entries,
                "st_info": st_info,
            }
            all_races.append(race_data)
    
    print(f"\n  取得レース数: {len(all_races)}")
    
    # チャンスレース判定
    chance_races = identify_chance_races(all_races)
    
    if not chance_races:
        print("\n  ❌ チャンスレース該当なし")
        return
    
    print(f"\n  🔥 チャンスレース: {len(chance_races)} 件検出!")
    
    # 各チャンスレースの詳細を表示
    for i, cr in enumerate(chance_races):
        boat1 = cr["boat1"]
        venue_name = cr["venue_name"]
        race_no = cr["race_no"]
        win_prob = cr["boat1_win_prob"]
        
        print(f"\n{'═'*60}")
        print(f"  チャンスレース #{i+1}: {venue_name} {race_no}R")
        print(f"{'═'*60}")
        print(f"  1号艇: {boat1['name']}")
        print(f"  全国勝率: {boat1['national_rate']:.2f}")
        print(f"  当地勝率: {boat1['local_rate']:.2f}")
        print(f"  1号艇勝率推定: {win_prob*100:.1f}%")
        print(f"  {cr['cond1']['reason']}")
        print(f"  {cr['cond2']['reason']}")
        
        # 出目ランキング表示
        jcd = cr["venue"]
        if venue_stats and jcd in venue_stats:
            print_venue_ranking(venue_stats, jcd, top_n=20)
            
            # 推奨舟券
            patterns = get_venue_ranking(venue_stats, jcd)
            if patterns:
                tickets = generate_tickets(patterns)
                print_tickets(tickets, venue_name, race_no)
    
    # 結果をJSON保存
    output_file = os.path.join(config.DAILY_DIR, f"analysis_{target_date}.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump({
            "date": target_date,
            "total_races": len(all_races),
            "chance_races": chance_races,
        }, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  分析結果保存: {output_file}")


def cmd_daily(args):
    """日次自動実行 (GitHub Actions用)"""
    today = datetime.now()
    yesterday = (today - timedelta(days=1)).strftime("%Y%m%d")
    today_str = today.strftime("%Y%m%d")
    
    print(f"═══════════════════════════════════════════")
    print(f"  BlitzBoat — 日次更新 {today_str}")
    print(f"═══════════════════════════════════════════")
    
    # 1. 前日結果を収集
    print(f"\n[1/7] 前日({yesterday})の結果を収集...")
    daily_results = collect_daily_results(yesterday)
    if daily_results:
        all_results = load_all_results()
        for race in daily_results:
            key = f"{race['venue']}_{race['date']}"
            if key not in all_results:
                all_results[key] = []
            all_results[key].append(race)
        save_all_results(all_results)
        print(f"  {len(daily_results)} レースの結果を追加")
    
    # 2. 統計再計算
    print(f"\n[2/7] 会場統計を再計算...")
    results = load_all_results()
    venue_stats = build_venue_stats(results)
    save_venue_stats(venue_stats)
    print(f"  {len(venue_stats)} 会場の統計を更新")
    
    # 3. 本日のチャンスレース分析
    print(f"\n[3/7] 本日({today_str})のレースを分析...")
    venues = scrape_today_venues(today_str)
    all_races = []
    for venue in venues:
        jcd = venue["jcd"]
        venue_name = venue.get("name", "")
        for rno in range(1, 13):
            entries = scrape_racelist(jcd, today_str, rno)
            if not entries:
                continue
            st_info = scrape_beforeinfo(jcd, today_str, rno)
            all_races.append({
                "date": today_str,
                "venue": jcd,
                "venue_name": venue_name,
                "race_no": rno,
                "entries": entries,
                "st_info": st_info,
            })
    
    chance_races = identify_chance_races(all_races)
    print(f"  チャンスレース: {len(chance_races)} 件")
    
    # 4. LINE通知
    print(f"\n[4/7] LINE通知...")
    notify_chance_races(chance_races, venue_stats)
    
    # 5. YouTube Shorts生成
    print(f"\n[5/7] YouTube Shorts生成...")
    if chance_races:
        top_race = chance_races[0]
        video_path = generate_shorts_video(top_race)
        print(f"  動画: {video_path}")
    else:
        print("  チャンスレースなし。動画生成スキップ。")
    
    # 6. X (Twitter) 自動投稿
    print(f"\n[6/7] X自動投稿...")
    if chance_races:
        x_ok = run_x_post(chance_races, today_str)
        if x_ok:
            print("  X投稿完了!")
    else:
        print("  チャンスレースなし。X投稿スキップ。")
    
    # 7. note.com 下書き保存
    print(f"\n[7/7] note下書き保存...")
    if chance_races:
        venue_stats_summary = {
            jcd: {
                "name": data.get("name", ""),
                "total_races": data.get("total_races", 0),
                "top_patterns": data.get("patterns", [])[:5],
            }
            for jcd, data in venue_stats.items()
        }
        note_ok = run_note_draft(chance_races, venue_stats_summary, today_str)
        if note_ok:
            print("  note下書き保存完了! 確認・公開をお願いします。")
    else:
        print("  チャンスレースなし。note下書きスキップ。")
    
    # 分析結果をJSON保存  (Vercel用)
    output_file = os.path.join(config.DAILY_DIR, f"daily_{today_str}.json")
    daily_output = {
        "date": today_str,
        "updated_at": today.isoformat(),
        "total_races": len(all_races),
        "chance_races": chance_races,
        "venue_stats_summary": {
            jcd: {
                "name": data.get("name", ""),
                "total_races": data.get("total_races", 0),
                "filtered_races": data.get("filtered_races", 0),
                "top_patterns": data.get("patterns", [])[:10],
            }
            for jcd, data in venue_stats.items()
        },
    }
    
    # 推奨舟券も追加
    for cr in daily_output["chance_races"]:
        jcd = cr.get("venue", "")
        patterns = get_venue_ranking(venue_stats, jcd)
        if patterns:
            cr["tickets"] = generate_tickets(patterns)
    
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(daily_output, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  日次分析保存: {output_file}")
    print(f"\n✅ 日次更新完了")


def cmd_stats(args):
    """会場統計の再計算"""
    print("会場統計を再計算中...")
    results = load_all_results()
    if not results:
        print("⚠ レースデータがありません。先に --collect を実行してください。")
        return
    
    venue_stats = build_venue_stats(results)
    save_venue_stats(venue_stats)
    
    for jcd in sorted(venue_stats.keys()):
        print_venue_ranking(venue_stats, jcd, top_n=10)


def cmd_test(args):
    """スクレイパー動作テスト"""
    jcd = args.venue or "01"
    hd = args.date or datetime.now().strftime("%Y%m%d")
    rno = args.race or 1
    venue_name = config.VENUE_CODES.get(jcd, jcd)
    
    print(f"═══════════════════════════════════════════")
    print(f"  テスト: {venue_name} {hd} {rno}R")
    print(f"═══════════════════════════════════════════")
    
    print(f"\n[1] 出走表...")
    entries = scrape_racelist(jcd, hd, rno)
    for e in entries:
        print(f"  {e['boat']}号艇: {e['name']} 全国{e['national_rate']} 当地{e['local_rate']} モーター{e['motor_no']}")
    
    print(f"\n[2] 直前情報...")
    st_info = scrape_beforeinfo(jcd, hd, rno)
    for s in st_info:
        print(f"  {s['boat']}号艇: ST {s['exhibit_st']}")
    
    print(f"\n[3] レース結果...")
    result = scrape_race_result(jcd, hd, rno)
    if result:
        print(f"  決まり手: {result.get('kimarite', 'N/A')}")
        print(f"  3連単: {result.get('trifecta', 'N/A')}")
        print(f"  ST: {result.get('start_times', [])}")


def main():
    parser = argparse.ArgumentParser(
        description="BlitzBoat — 統計ベース競艇戦略システム",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")
    
    # --collect
    p_collect = sub.add_parser("collect", help="過去データ一括収集")
    p_collect.add_argument("--days", type=int, default=None, help="収集日数 (default: 180)")
    
    # --analyze
    p_analyze = sub.add_parser("analyze", help="チャンスレース分析")
    p_analyze.add_argument("--date", type=str, default=None, help="対象日 YYYYMMDD")
    
    # --daily
    p_daily = sub.add_parser("daily", help="日次自動実行")
    
    # --stats
    p_stats = sub.add_parser("stats", help="会場統計再計算")
    
    # --test
    p_test = sub.add_parser("test", help="スクレイパーテスト")
    p_test.add_argument("--venue", type=str, default="01", help="会場コード")
    p_test.add_argument("--date", type=str, default=None, help="日付 YYYYMMDD")
    p_test.add_argument("--race", type=int, default=1, help="レース番号")
    
    args = parser.parse_args()
    
    if args.command == "collect":
        cmd_collect(args)
    elif args.command == "analyze":
        cmd_analyze(args)
    elif args.command == "daily":
        cmd_daily(args)
    elif args.command == "stats":
        cmd_stats(args)
    elif args.command == "test":
        cmd_test(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
