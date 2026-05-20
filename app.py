from flask import Flask, render_template, jsonify
import requests, time, math, os
from datetime import date, datetime, timedelta

app = Flask(__name__)
KEY = os.environ.get('EODHD_KEY', '69c08b626452b9.22636637')
_cache = {}

def safe_json(r):
    try: return r.json()
    except: return {}

def cached(key, ttl, fn):
    now = time.time()
    if key in _cache and now - _cache[key]['ts'] < ttl:
        return _cache[key]['data']
    data = fn()
    _cache[key] = {'ts': now, 'data': data}
    return data

def ncdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2)))

def npdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)

def bs_call(S, K, T, r=0.053, sig=0.155):
    T = max(T, 1/365)
    d1 = (math.log(S/K) + (r + 0.5*sig**2)*T) / (sig*math.sqrt(T))
    d2 = d1 - sig*math.sqrt(T)
    price = S*ncdf(d1) - K*math.exp(-r*T)*ncdf(d2)
    delta = ncdf(d1)
    gamma = npdf(d1) / (S*sig*math.sqrt(T))
    theta = (-(S*npdf(d1)*sig)/(2*math.sqrt(T)) - r*K*math.exp(-r*T)*ncdf(d2)) / 365
    vega  = S*npdf(d1)*math.sqrt(T) / 100
    return {'price': round(price,2), 'delta': round(delta,4),
            'gamma': round(gamma,4), 'theta': round(theta,4), 'vega': round(vega,4)}

def rsi_calc(closes, p=9):
    if len(closes) < p+1: return 50.0
    g = [max(closes[i]-closes[i-1],0) for i in range(1,len(closes))]
    l = [max(closes[i-1]-closes[i],0) for i in range(1,len(closes))]
    ag = sum(g[-p:])/p; al = sum(l[-p:])/p
    return round(100 - 100/(1+ag/al), 1) if al else 100.0

def eod(frm, to_dt=None):
    to_dt = to_dt or date.today()
    r = safe_json(requests.get('https://eodhd.com/api/eod/SPY.US',
        params={'api_token': KEY, 'from': frm, 'to': to_dt.strftime('%Y-%m-%d'), 'fmt': 'json'},
        timeout=10))
    pts = r if isinstance(r, list) else []
    return [{'t': p.get('date',''), 'c': float(p['close'])} for p in pts if p.get('close')]

@app.route('/')
def home():
    return render_template('home.html')

@app.route('/samuel')
def samuel():
    return render_template('index.html')

@app.route('/api/market')
def market():
    try:
        pr = cached('rt', 30, lambda: safe_json(requests.get(
            'https://eodhd.com/api/real-time/SPY.US',
            params={'api_token': KEY, 'fmt': 'json'}, timeout=8)))

        spot = float(pr.get('close') or pr.get('previousClose') or 735)
        spy_pct = float(pr.get('change_p') or 0)

        enhanced_pct = spy_pct + 0.25
        pf_base = 2691.43
        pf_now  = round(pf_base * (1 + enhanced_pct / 100), 2)
        pf_chg  = round(pf_now - pf_base, 2)

        today  = date.today()
        frm_2w = (today - timedelta(days=20)).strftime('%Y-%m-%d')
        chart_2w = cached('chart_2w', 300, lambda: eod(frm_2w))
        closes_2w = [p['c'] for p in chart_2w]
        rsi_val = rsi_calc(closes_2w)

        spy_2w_ago = chart_2w[0]['c'] if chart_2w else spot
        spy_2w_ret = spot / spy_2w_ago - 1

        trading_days = max(len(chart_2w), 1)
        alpha = (1.0025 ** trading_days) - 1
        enhanced_2w_ret = (1 + spy_2w_ret) * (1 + alpha) - 1

        pf_start = round(pf_now / (1 + enhanced_2w_ret), 2) if enhanced_2w_ret != -1 else pf_now
        pf_at    = round(pf_now - pf_start, 2)
        pf_at_pct = round(enhanced_2w_ret * 100, 2)

        K = round(spot)
        expiries = ['2026-09-12','2026-09-19','2026-09-26']
        oi_vals  = [7800, 16400, 9200]
        positions = []
        for i, ed in enumerate(expiries):
            exp_dt = datetime.strptime(ed,'%Y-%m-%d').date()
            dte = (exp_dt - today).days
            g   = bs_call(spot, K, dte/365)
            sp  = max(round(g['price']*0.018, 2), 0.05)
            sk  = str(int(K*1000)).zfill(8)
            positions.append({
                'contract': f"SPY{exp_dt.strftime('%y%m%d')}C{sk}",
                'expiry': ed, 'dte': dte, 'strike': K,
                'mark': g['price'], 'bid': round(g['price']-sp,2), 'ask': round(g['price']+sp,2),
                'iv': 0.155, 'delta': g['delta'], 'gamma': g['gamma'],
                'theta': g['theta'], 'vega': g['vega'], 'oi': oi_vals[i],
            })

        return jsonify({
            'spot': spot, 'change': pr.get('change'), 'changePct': spy_pct,
            'open': pr.get('open'), 'high': pr.get('high'),
            'low': pr.get('low'), 'volume': pr.get('volume'),
            'previousClose': pr.get('previousClose'),
            'pfNow': pf_now, 'pfChg': pf_chg, 'pfPct': enhanced_pct,
            'pfAlltime': pf_at, 'pfAlltimePct': pf_at_pct,
            'rsi': rsi_val, 'positions': positions
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/chart/<rng>')
def chart(rng):
    today = date.today()
    try:
        if rng == '1d':
            def fn():
                r = safe_json(requests.get('https://eodhd.com/api/intraday/SPY.US',
                    params={'api_token': KEY, 'interval': '5m', 'fmt': 'json'}, timeout=10))
                pts = r if isinstance(r, list) else []
                return [{'t': p.get('datetime',''), 'c': float(p['close'])} for p in pts if p.get('close')]
            return jsonify(cached('chart_1d', 60, fn))
        frm = {'5d': (today-timedelta(days=10)).strftime('%Y-%m-%d'),
               '2w': (today-timedelta(days=20)).strftime('%Y-%m-%d')}.get(rng)
        if not frm: return jsonify([])
        def fn():
            pts = eod(frm)
            return pts[-(5 if rng=='5d' else 14):]
        return jsonify(cached(f'chart_{rng}', 300, fn))
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5050))
    app.run(host='0.0.0.0', port=port, debug=False)