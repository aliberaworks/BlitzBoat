"""
makuri - LINE通知モジュール（レイヤー別）

Layer 1: 荒れ警報（ビジネス向け・シンプル）
Layer 2: まくり成功シナリオ + 買い目
Layer 3: 裏スジ（まくり失敗 → 5号艇軸）+ 買い目
"""
import json
import os
import requests

import config

_BOAT_LABEL = ["①白", "②黒", "③赤", "④青", "⑤黄", "⑥緑"]


def _send(text: str) -> bool:
    token = config.LINE_CHANNEL_ACCESS_TOKEN
    uid   = config.LINE_USER_ID
    if not token or token in ("", "your_token_here"):
        print(f"[LINE skip]\n{text}")
        return False
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    payload = {"to": uid, "messages": [{"type": "text", "text": text}]}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=10)
        ok = r.status_code == 200
        print(f"  [LINE] {'OK' if ok else 'NG ' + str(r.status_code)}")
        return ok
    except Exception as e:
        print(f"  [LINE] error: {e}")
        return False


# ══════════════════════════════════════════════════════════════
# Layer 1: 荒れ警報（ビジネス向け）
# ══════════════════════════════════════════════════════════════
def format_layer1(races: list[dict], ymd: str) -> str:
    """
    races: [{"venue_name":str, "race_no":int, "race_time":str,
              "arare_prob":float, "b1_type":str}, ...]
    """
    lines = [
        f"🌪️ 荒れ警報  {ymd}",
        f"1号艇が凹む可能性の高いレース TOP{len(races)}",
        "",
    ]
    for rank, r in enumerate(races, 1):
        ap = r.get("arare_prob", 0)
        stars = "★" * min(5, max(1, int((ap - 0.50) / 0.05)))
        stars += "☆" * (5 - len(stars))
        b1t = r.get("b1_type", "")
        lines.append(
            f"{rank}. {r['venue_name']} {r['race_no']}R ⏰{r.get('race_time','--:--')}  "
            f"{stars} {int(ap*100)}%  1号艇:{b1t}"
        )
    lines += ["", "⚠ 荒れ確定ではなく可能性が高いレースです"]
    return "\n".join(lines)


def send_layer1(races: list[dict], ymd: str) -> bool:
    if not races:
        return False
    text = format_layer1(races, ymd)
    return _send(text)


# ══════════════════════════════════════════════════════════════
# Layer 2: まくり成功シナリオ
# ══════════════════════════════════════════════════════════════
def format_layer2(notify_list: list[dict], now_str: str) -> str:
    """
    notify_list: [{
      "venue_name": str, "race_no": int, "race_time": str,
      "scenario_result": dict (from scenario.score_race),
      "combos": list (from combo.get_combos_for_scenario),
      "odds_map": dict (任意),
    }]
    """
    lines = [f"⚡ まくり予測  {now_str}", "━━━━━━━━━━━"]

    for n in notify_list:
        sr  = n["scenario_result"]
        ap  = sr.get("arare_prob", 0)
        b1t = sr.get("b1_type", "")
        s3  = sr.get("boat3") or {}
        s4  = sr.get("boat4") or {}
        bt  = sr.get("buy_target")

        lines += [
            "",
            f"📍 {n['venue_name']} {n['race_no']}R  ⏰{n.get('race_time','--:--')}",
            f"  荒れ確率: {int(ap*100)}%  1号艇: {b1t}",
            f"  {sr.get('scenario','')}",
        ]
        if s3.get("p_try", 0) >= config.MAKKURI_TRY_THRESH:
            lines.append(f"  ③まくり試行{s3['p_try']*100:.0f}% → 成功{s3['p_success']*100:.0f}% / 失敗{s3['p_fail']*100:.0f}%")
        if s4.get("p_try", 0) >= config.MAKKURI_TRY_THRESH:
            lines.append(f"  ④まくり試行{s4['p_try']*100:.0f}% → 成功{s4['p_success']*100:.0f}% / 失敗{s4['p_fail']*100:.0f}%")

        combos = n.get("combos", [])
        # 展示ST表示（「自分で見て判断」用）
        exhibit_sts = n.get("exhibit_sts", {})
        if exhibit_sts:
            ex_parts = []
            for b in range(1, 7):
                ex = exhibit_sts.get(b) or exhibit_sts.get(str(b))
                if ex is not None:
                    ex_parts.append(f"{_BOAT_LABEL[b-1]}{float(ex):.2f}")
            if ex_parts:
                lines.append(f"  📊 展示ST: {' '.join(ex_parts)}")

        if combos:
            lines.append(f"  ── 買い目 {len(combos)}点 ──")
            for i, c in enumerate(combos, 1):
                r1, r2, r3 = c["r1"], c["r2"], c["r3"]
                b1 = _BOAT_LABEL[r1 - 1]
                b2 = _BOAT_LABEL[r2 - 1]
                b3 = _BOAT_LABEL[r3 - 1]
                odds_key = (r1, r2, r3)
                odds = n.get("odds_map", {}).get(odds_key, "---")
                odds_str = f"  {odds:.0f}倍" if isinstance(odds, float) else ""
                lines.append(f"  {i}. {b1}-{b2}-{b3}  {c['pct']*100:.1f}%{odds_str}")

    lines += ["", "━━━━━━━━━━━", "※実績統計確率×決まり手分布。EVは不使用。"]
    return "\n".join(lines)


def send_layer2(notify_list: list[dict], now_str: str) -> bool:
    if not notify_list:
        return False
    text = format_layer2(notify_list, now_str)
    return _send(text)


# ══════════════════════════════════════════════════════════════
# Layer 3: 裏スジ
# ══════════════════════════════════════════════════════════════
def format_layer3(notify_list: list[dict], now_str: str) -> str:
    lines = [f"🔀 裏スジ警報  {now_str}", "━━━━━━━━━━━",
             "まくり失敗→5号艇チャンス", ""]

    for n in notify_list:
        sr   = n["scenario_result"]
        b1t  = sr.get("b1_type", "")
        bt   = sr.get("buy_target", 5)
        s    = sr.get("scenario", "")
        fail_boat = 4 if "4号艇" in s else 3

        lines += [
            f"📍 {n['venue_name']} {n['race_no']}R  ⏰{n.get('race_time','--:--')}",
            f"  1号艇:{b1t}  {fail_boat}号艇まくり失敗シナリオ",
        ]
        combos = n.get("combos", [])
        if combos:
            lines.append(f"  ── 裏スジ買い目 {len(combos)}点 ──")
            for i, c in enumerate(combos, 1):
                r1, r2, r3 = c["r1"], c["r2"], c["r3"]
                b1 = _BOAT_LABEL[r1 - 1]
                b2 = _BOAT_LABEL[r2 - 1]
                b3 = _BOAT_LABEL[r3 - 1]
                odds_key = (r1, r2, r3)
                odds = n.get("odds_map", {}).get(odds_key, "---")
                odds_str = f"  {odds:.0f}倍" if isinstance(odds, float) else ""
                lines.append(f"  {i}. {b1}-{b2}-{b3}  {c['pct']*100:.1f}%{odds_str}")
        lines.append("")

    lines += ["━━━━━━━━━━━",
              f"まくり失敗時の5号艇勝率: 約10-11%（ベース比1.7-1.8x）",
              "※確率は低いが高配当（平均17,000-21,000円）"]
    return "\n".join(lines)


def send_layer3(notify_list: list[dict], now_str: str) -> bool:
    if not notify_list:
        return False
    text = format_layer3(notify_list, now_str)
    return _send(text)


# ══════════════════════════════════════════════════════════════
# 日次サマリー
# ══════════════════════════════════════════════════════════════
def format_daily_summary(log: dict, ymd: str) -> str:
    entry = log.get(ymd, {})
    if not entry:
        return f"📊 {ymd} データなし"

    lines = [f"📊 {ymd[:4]}/{ymd[4:6]}/{ymd[6:]} 日次まとめ", ""]

    for layer, label in [(2, "Layer2 まくり成功"), (3, "Layer3 裏スジ")]:
        lk = f"layer{layer}"
        d  = entry.get(lk, {})
        bets  = d.get("bets", 0)
        hits  = d.get("hits", 0)
        ret   = d.get("return_yen", 0)
        invest= d.get("invest_yen", 0)
        if bets == 0:
            lines.append(f"  {label}: 買い目なし")
            continue
        roi = (ret / invest - 1) * 100 if invest > 0 else 0
        lines.append(f"  {label}:  {bets}点/{hits}的中  "
                     f"ROI {roi:+.1f}%  回収{ret:,}円/投資{invest:,}円")

    # 累計
    all_days = [(d, v) for d, v in log.items() if isinstance(v, dict)]
    if len(all_days) >= 2:
        tot_bet = tot_hit = tot_ret = tot_inv = 0
        for _, v in all_days:
            for lk in ["layer2", "layer3"]:
                d_ = v.get(lk, {})
                tot_bet += d_.get("bets", 0)
                tot_hit += d_.get("hits", 0)
                tot_ret += d_.get("return_yen", 0)
                tot_inv += d_.get("invest_yen", 0)
        if tot_inv > 0:
            cum_roi = (tot_ret / tot_inv - 1) * 100
            lines += ["", f"  累計({len(all_days)}日): {tot_bet}点/{tot_hit}的中  "
                          f"ROI {cum_roi:+.1f}%"]
    return "\n".join(lines)


def send_daily_summary(log: dict, ymd: str) -> bool:
    text = format_daily_summary(log, ymd)
    return _send(text)
