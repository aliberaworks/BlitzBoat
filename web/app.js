/**
 * BlitzBoat — Dashboard App Logic
 * 日替わりパスワード認証 + データ表示
 */

// ── 設定 ──
const AUTH_SECRET = 'blitzboat2026';
const DATA_PATH = './data/';

// ── 日替わりパスワード生成 ──
async function generateDailyPassword() {
    const today = new Date();
    const dateStr = today.toISOString().slice(0, 10).replace(/-/g, '');
    const raw = dateStr + AUTH_SECRET;
    
    // SHA-256 ハッシュ
    const encoder = new TextEncoder();
    const data = encoder.encode(raw);
    const hashBuffer = await crypto.subtle.digest('SHA-256', data);
    const hashArray = Array.from(new Uint8Array(hashBuffer));
    const hashHex = hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
    
    return hashHex.slice(0, 8);
}

// ── 認証 ──
async function checkAuth() {
    const stored = sessionStorage.getItem('blitzboat_auth');
    const todayKey = new Date().toISOString().slice(0, 10);
    
    if (stored === todayKey) {
        showDashboard();
        return;
    }
    
    document.getElementById('auth-overlay').classList.remove('hidden');
    document.getElementById('dashboard').classList.add('hidden');
}

document.getElementById('auth-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const input = document.getElementById('auth-password').value.trim();
    const expected = await generateDailyPassword();
    
    if (input === expected) {
        const todayKey = new Date().toISOString().slice(0, 10);
        sessionStorage.setItem('blitzboat_auth', todayKey);
        showDashboard();
    } else {
        const errorEl = document.getElementById('auth-error');
        errorEl.textContent = 'パスワードが正しくありません';
        document.getElementById('auth-password').value = '';
        document.getElementById('auth-password').focus();
        setTimeout(() => { errorEl.textContent = ''; }, 3000);
    }
});

function showDashboard() {
    document.getElementById('auth-overlay').classList.add('hidden');
    document.getElementById('dashboard').classList.remove('hidden');
    initDashboard();
}

// ── Dashboard Init ──
async function initDashboard() {
    // 日付表示
    const now = new Date();
    const dateStr = `${now.getFullYear()}/${String(now.getMonth()+1).padStart(2,'0')}/${String(now.getDate()).padStart(2,'0')}`;
    document.getElementById('header-date').textContent = dateStr;
    
    // データ読み込み
    await loadDailyData();
}

// ── データ読み込み ──
let currentData = null;

async function loadDailyData() {
    const today = new Date();
    const dateStr = `${today.getFullYear()}${String(today.getMonth()+1).padStart(2,'0')}${String(today.getDate()).padStart(2,'0')}`;
    
    // 最新のdaily JSONを試行
    const urls = [
        `${DATA_PATH}daily_${dateStr}.json`,
        `${DATA_PATH}latest.json`,
    ];
    
    for (const url of urls) {
        try {
            const resp = await fetch(url);
            if (resp.ok) {
                currentData = await resp.json();
                renderDashboard(currentData);
                return;
            }
        } catch (e) {
            console.log(`Failed to load ${url}:`, e);
        }
    }
    
    // デモデータ
    renderDashboard(getDemoData());
}

function renderDashboard(data) {
    currentData = data;
    
    const chanceRaces = data.chance_races || [];
    const totalRaces = data.total_races || 0;
    
    // Stats
    document.getElementById('stat-chance').textContent = chanceRaces.length;
    document.getElementById('stat-total').textContent = totalRaces;
    
    if (chanceRaces.length > 0) {
        const lowest = Math.min(...chanceRaces.map(r => r.boat1_win_prob || 1));
        document.getElementById('stat-lowest').textContent = `${(lowest * 100).toFixed(0)}%`;
        
        const totalTickets = chanceRaces.reduce((sum, r) => sum + (r.tickets ? r.tickets.length : 0), 0);
        document.getElementById('stat-tickets').textContent = totalTickets;
        
        // Alert banner
        const banner = document.getElementById('alert-banner');
        banner.classList.remove('hidden');
        document.getElementById('alert-text').textContent = 
            `🔥 本日のチャンスレース: ${chanceRaces.length}件検出! 最低1号艇勝率: ${(lowest * 100).toFixed(0)}%`;
    }
    
    // Chance races
    renderChanceRaces(chanceRaces);
    
    // Venue selector
    renderVenueSelector(data.venue_stats_summary || {});
}

function renderChanceRaces(races) {
    const container = document.getElementById('chance-races');
    
    if (!races.length) {
        container.innerHTML = '<div class="empty-state">チャンスレース該当なし</div>';
        return;
    }
    
    container.innerHTML = races.map((race, i) => {
        const boat1 = race.boat1 || {};
        const winProb = ((race.boat1_win_prob || 0) * 100).toFixed(0);
        const cond1 = race.cond1 || {};
        const cond2 = race.cond2 || {};
        
        return `
            <div class="race-card" onclick="selectRace(${i})">
                <div class="race-card-header">
                    <span class="race-venue">${race.venue_name || ''} ${race.race_no || ''}R</span>
                    <span class="race-prob">1号艇勝率: ${winProb}%</span>
                </div>
                <div class="race-detail">
                    <strong>1号艇:</strong> ${boat1.name || '不明'}<br>
                    <strong>全国勝率:</strong> ${(boat1.national_rate || 0).toFixed(2)} / 
                    <strong>当地勝率:</strong> ${(boat1.local_rate || 0).toFixed(2)}<br>
                    <strong>❌ Cond.1:</strong> ${cond1.reason || ''}<br>
                    <strong>❌ Cond.2:</strong> ${cond2.reason || ''}
                </div>
            </div>
        `;
    }).join('');
}

function selectRace(index) {
    if (!currentData || !currentData.chance_races) return;
    const race = currentData.chance_races[index];
    if (!race) return;
    
    // Tickets
    if (race.tickets) {
        renderTickets(race.tickets, race.venue_name, race.race_no);
    }
    
    // Venue ranking
    const venueSelect = document.getElementById('venue-select');
    if (venueSelect) {
        venueSelect.value = race.venue || '';
        renderVenueRanking(race.venue);
    }
}

function renderVenueSelector(statsMap) {
    const select = document.getElementById('venue-select');
    const venues = Object.keys(statsMap).sort();
    
    select.innerHTML = '<option value="">会場を選択</option>' + 
        venues.map(jcd => {
            const name = statsMap[jcd]?.name || jcd;
            return `<option value="${jcd}">${name}</option>`;
        }).join('');
    
    select.addEventListener('change', () => {
        renderVenueRanking(select.value);
    });
}

function renderVenueRanking(jcd) {
    const container = document.getElementById('ranking-table');
    
    if (!jcd || !currentData?.venue_stats_summary?.[jcd]) {
        container.innerHTML = '<div class="empty-state">データなし</div>';
        return;
    }
    
    const patterns = currentData.venue_stats_summary[jcd].top_patterns || [];
    
    if (!patterns.length) {
        container.innerHTML = '<div class="empty-state">ランキングデータなし</div>';
        return;
    }
    
    const maxProb = Math.max(...patterns.map(p => p.prob));
    
    container.innerHTML = `
        <table class="ranking-table">
            <thead>
                <tr>
                    <th>順位</th>
                    <th>出目</th>
                    <th>確率</th>
                    <th>累積</th>
                    <th>回数</th>
                    <th>決まり手</th>
                    <th>分布</th>
                </tr>
            </thead>
            <tbody>
                ${patterns.map((p, i) => `
                    <tr>
                        <td class="col-rank">${i + 1}</td>
                        <td class="col-trifecta">${p.trifecta}</td>
                        <td class="col-prob">${(p.prob * 100).toFixed(2)}%</td>
                        <td class="col-cum">${(p.cum_prob * 100).toFixed(1)}%</td>
                        <td>${p.count}</td>
                        <td class="col-kimarite">${p.kimarite}</td>
                        <td>
                            <div class="prob-bar">
                                <div class="prob-bar-fill" style="width: ${(p.prob / maxProb * 100).toFixed(0)}%"></div>
                            </div>
                        </td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

function renderTickets(tickets, venueName, raceNo) {
    const container = document.getElementById('ticket-list');
    
    if (!tickets || !tickets.length) {
        container.innerHTML = '<div class="empty-state">推奨舟券なし</div>';
        return;
    }
    
    const total = tickets.reduce((s, t) => s + t.amount, 0);
    
    container.innerHTML = 
        tickets.map((t, i) => `
            <div class="ticket-card">
                <span class="ticket-rank">${i + 1}</span>
                <span class="ticket-trifecta">${t.trifecta}</span>
                <span class="ticket-kimarite">${t.kimarite}</span>
                <span class="ticket-prob">${(t.prob * 100).toFixed(1)}%</span>
                <span class="ticket-amount">¥${t.amount.toLocaleString()}</span>
            </div>
        `).join('') +
        `<div class="ticket-total">
            <span class="ticket-total-label">合計</span>
            <span class="ticket-total-amount">¥${total.toLocaleString()}</span>
        </div>`;
}

// ── Demo Data ──
function getDemoData() {
    return {
        date: new Date().toISOString().slice(0, 10).replace(/-/g, ''),
        total_races: 144,
        chance_races: [
            {
                venue: '01', venue_name: '桐生', race_no: 5,
                boat1_win_prob: 0.22,
                boat1: { name: 'サンプル選手A', national_rate: 3.82, local_rate: 2.15, motor_no: '45' },
                cond1: { triggered: true, reason: '全国勝率 3.82 < 4.5' },
                cond2: { triggered: true, reason: 'avg(0.190) + std(0.015) = 0.205 > 0.18' },
                tickets: [
                    { trifecta: '2-3-4', prob: 0.082, amount: 6300, kimarite: 'まくり' },
                    { trifecta: '3-2-4', prob: 0.065, amount: 5000, kimarite: 'まくり差し' },
                    { trifecta: '4-2-3', prob: 0.055, amount: 4200, kimarite: 'まくり' },
                    { trifecta: '2-4-3', prob: 0.048, amount: 3700, kimarite: 'まくり' },
                    { trifecta: '3-4-2', prob: 0.042, amount: 3200, kimarite: 'まくり差し' },
                    { trifecta: '4-3-2', prob: 0.038, amount: 2900, kimarite: 'まくり' },
                    { trifecta: '5-2-3', prob: 0.032, amount: 2500, kimarite: 'まくり' },
                    { trifecta: '2-5-3', prob: 0.028, amount: 2200, kimarite: 'まくり' },
                ],
            },
            {
                venue: '22', venue_name: '福岡', race_no: 8,
                boat1_win_prob: 0.28,
                boat1: { name: 'サンプル選手B', national_rate: 4.15, local_rate: 2.30, motor_no: '12' },
                cond1: { triggered: true, reason: '全国勝率 4.15 < 4.5' },
                cond2: { triggered: true, reason: 'avg(0.185) + std(0.012) = 0.197 > 0.18' },
                tickets: [],
            },
        ],
        venue_stats_summary: {
            '01': {
                name: '桐生',
                total_races: 2160,
                filtered_races: 432,
                top_patterns: [
                    { trifecta: '2-3-4', prob: 0.082, cum_prob: 0.082, count: 35, kimarite: 'まくり' },
                    { trifecta: '3-2-4', prob: 0.065, cum_prob: 0.147, count: 28, kimarite: 'まくり差し' },
                    { trifecta: '4-2-3', prob: 0.055, cum_prob: 0.202, count: 24, kimarite: 'まくり' },
                    { trifecta: '2-4-3', prob: 0.048, cum_prob: 0.250, count: 21, kimarite: 'まくり' },
                    { trifecta: '3-4-2', prob: 0.042, cum_prob: 0.292, count: 18, kimarite: 'まくり差し' },
                    { trifecta: '4-3-2', prob: 0.038, cum_prob: 0.330, count: 16, kimarite: 'まくり' },
                    { trifecta: '5-2-3', prob: 0.032, cum_prob: 0.362, count: 14, kimarite: 'まくり' },
                    { trifecta: '2-5-3', prob: 0.028, cum_prob: 0.390, count: 12, kimarite: 'まくり' },
                    { trifecta: '3-5-2', prob: 0.025, cum_prob: 0.415, count: 11, kimarite: 'まくり差し' },
                    { trifecta: '4-5-2', prob: 0.022, cum_prob: 0.437, count: 10, kimarite: 'まくり' },
                ],
            },
            '22': {
                name: '福岡',
                total_races: 2100,
                filtered_races: 410,
                top_patterns: [
                    { trifecta: '3-2-4', prob: 0.075, cum_prob: 0.075, count: 31, kimarite: 'まくり差し' },
                    { trifecta: '2-3-4', prob: 0.068, cum_prob: 0.143, count: 28, kimarite: 'まくり' },
                    { trifecta: '4-3-2', prob: 0.052, cum_prob: 0.195, count: 21, kimarite: 'まくり' },
                ],
            },
        },
    };
}

// ── Init ──
checkAuth();
