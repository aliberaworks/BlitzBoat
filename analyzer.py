"""
BlitzBoat Analyzer — チャンスレース判定エンジン
scipy.stats による統計解析モデル
"""
import numpy as np
from scipy import stats

import config


def is_boat1_weak(national_rate: float, local_rate: float) -> tuple[bool, str]:
    """
    Condition 1: 1号艇が弱い（負ける）条件を判定。
    - 全国勝率 < 4.5
    - OR 全国勝率 - 当地勝率 > 1.5
    
    Returns: (True/False, 理由テキスト)
    """
    reasons = []
    
    if national_rate < config.NATIONAL_RATE_THRESHOLD:
        reasons.append(f"全国勝率 {national_rate:.2f} < {config.NATIONAL_RATE_THRESHOLD}")
    
    rate_diff = national_rate - local_rate
    if rate_diff > config.RATE_DIFF_THRESHOLD:
        reasons.append(f"全国-当地 = {rate_diff:.2f} > {config.RATE_DIFF_THRESHOLD}")
    
    triggered = len(reasons) > 0
    return triggered, " / ".join(reasons) if reasons else "条件未達"


def is_st_slow(motor_st_history: list[float]) -> tuple[bool, dict]:
    """
    Condition 2: 1号艇のSTが遅い（凹む）条件を判定。
    モーターの過去6ヶ月のST履歴から平均+標準偏差 > 0.18 を評価。
    
    Args:
        motor_st_history: モーターの過去STリスト
        
    Returns: (True/False, 統計情報dict)
    """
    if not motor_st_history or len(motor_st_history) < 2:
        return False, {"reason": "データ不足"}
    
    arr = np.array(motor_st_history)
    avg = float(np.mean(arr))
    std = float(np.std(arr, ddof=1))  # 不偏標準偏差
    combined = avg + std
    
    # scipy.stats で正規分布フィッティング
    mu, sigma = stats.norm.fit(arr)
    
    # P(ST > 0.18) の確率を計算
    prob_slow = 1 - stats.norm.cdf(config.ST_SLOW_THRESHOLD, loc=mu, scale=sigma)
    
    # 95%信頼区間
    ci = stats.norm.interval(0.95, loc=mu, scale=sigma)
    
    info = {
        "avg_st": round(avg, 4),
        "std_st": round(std, 4),
        "combined": round(combined, 4),
        "mu": round(mu, 4),
        "sigma": round(sigma, 4),
        "prob_slow": round(prob_slow, 4),
        "ci_95": (round(ci[0], 4), round(ci[1], 4)),
        "n_samples": len(motor_st_history),
    }
    
    triggered = combined > config.ST_SLOW_THRESHOLD
    if triggered:
        info["reason"] = f"avg({avg:.4f}) + std({std:.4f}) = {combined:.4f} > {config.ST_SLOW_THRESHOLD}"
    else:
        info["reason"] = "条件未達"
    
    return triggered, info


def evaluate_boat1_win_probability(national_rate: float, local_rate: float,
                                     motor_st_info: dict) -> float:
    """
    1号艇の勝率推定 (0.0 ~ 1.0)。
    全国勝率ベースに、当地補正とST補正を適用。
    """
    # ベース勝率: 全国勝率から推計 (勝率8.0のトップ選手で約60%、4.0の選手で約30%)
    base_prob = min(national_rate / 13.0, 0.70)
    
    # 当地補正
    rate_diff = national_rate - local_rate
    if rate_diff > 0:
        # 当地が弱い → 勝率低下
        local_penalty = rate_diff * 0.03
        base_prob -= local_penalty
    elif rate_diff < 0:
        # 当地が強い → 勝率上昇
        local_bonus = abs(rate_diff) * 0.02
        base_prob += local_bonus
    
    # ST補正
    if motor_st_info.get("prob_slow", 0) > 0.5:
        # STが遅い確率が50%以上 → 大幅ペナルティ
        st_penalty = motor_st_info["prob_slow"] * 0.15
        base_prob -= st_penalty
    
    # 1号艇の構造的有利 (イン有利)
    base_prob += 0.15
    
    return max(min(base_prob, 0.95), 0.05)


def identify_chance_races(races: list[dict]) -> list[dict]:
    """
    レースリストからチャンスレースを特定。
    Condition 1 AND Condition 2 が両方成立するレースを抽出。
    
    Args:
        races: scraper.pyで取得したレースデータのリスト
        
    Returns: チャンスレース情報リスト
    """
    chance_races = []
    
    for race in races:
        entries = race.get("entries", [])
        if not entries:
            continue
        
        # 1号艇データ
        boat1 = None
        for e in entries:
            if e.get("boat") == 1:
                boat1 = e
                break
        
        if not boat1:
            continue
        
        national_rate = boat1.get("national_rate", 0)
        local_rate = boat1.get("local_rate", 0)
        
        # Condition 1: 弱い1号艇
        cond1, cond1_reason = is_boat1_weak(national_rate, local_rate)
        
        # Condition 2: ST凹み
        # STデータを収集 (過去のST履歴が必要)
        st_info_list = race.get("st_info", [])
        boat1_st_history = []
        for st in st_info_list:
            if st.get("boat") == 1:
                boat1_st_history.append(st.get("exhibit_st", 0))
        
        # ヒストリカルSTデータが利用可能な場合
        motor_st_history = race.get("motor_st_history", [])
        if not motor_st_history and boat1_st_history:
            motor_st_history = boat1_st_history
        
        if motor_st_history:
            cond2, cond2_info = is_st_slow(motor_st_history)
        else:
            # STデータ不足 → 展示STのみで簡易判定
            if boat1_st_history and boat1_st_history[0] > config.ST_SLOW_THRESHOLD:
                cond2 = True
                cond2_info = {
                    "avg_st": boat1_st_history[0],
                    "reason": f"展示ST {boat1_st_history[0]} > {config.ST_SLOW_THRESHOLD}",
                }
            else:
                cond2 = False
                cond2_info = {"reason": "STデータ不足"}
        
        if cond1 and cond2:
            # 勝率推定
            win_prob = evaluate_boat1_win_probability(
                national_rate, local_rate, cond2_info
            )
            
            chance_races.append({
                "date": race.get("date", ""),
                "venue": race.get("venue", ""),
                "venue_name": race.get("venue_name", ""),
                "race_no": race.get("race_no", 0),
                "boat1": boat1,
                "cond1": {"triggered": True, "reason": cond1_reason},
                "cond2": {"triggered": True, **cond2_info},
                "boat1_win_prob": round(win_prob, 4),
                "entries": entries,
            })
    
    # 1号艇勝率が低い順にソート (凹みが大きい順)
    chance_races.sort(key=lambda x: x["boat1_win_prob"])
    
    return chance_races


if __name__ == "__main__":
    # テスト
    print("=== Condition 1 テスト ===")
    print(is_boat1_weak(3.8, 2.1))   # → True
    print(is_boat1_weak(5.5, 3.0))   # → True (diff > 1.5)
    print(is_boat1_weak(6.0, 5.0))   # → False
    
    print("\n=== Condition 2 テスト ===")
    st_history = [0.16, 0.18, 0.20, 0.17, 0.19, 0.21, 0.18]
    print(is_st_slow(st_history))
