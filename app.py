from fastapi import FastAPI, HTTPException
import requests, pandas as pd, datetime as dt, os

@app.get("/")
def root():
    return {"status": "btcw-api live"}
    
# ---------------------------------------------------------------------
# 1. CONFIG
# ---------------------------------------------------------------------
SOURCE_URL = "https://api.binance.com/api/v3/klines"
SYMBOL     = "BTCUSDT"           # BTC/USD on Binance
EMA_SPAN   = 50
SL_USD     = 50                  # $50 stop on BTC (≈ 0.07 %) – tweak as you like
TP_FACTOR  = 1.5                 # 1.5 × risk
CSV_FILE   = "/app/btc_m1.csv"   # placeholder path for uploaded history

app = FastAPI(title="BTC Wick-Rejection API")

# ---------------------------------------------------------------------
# 2. FETCH 1-MIN CANDLES (last 60 bars)
# ---------------------------------------------------------------------
def fetch_candles():
    r = requests.get(SOURCE_URL,
                     params=dict(symbol=SYMBOL, interval="1m", limit=60),
                     timeout=10)
    r.raise_for_status()
    rows = []
    for k in r.json():
        rows.append(dict(
            Time  = dt.datetime.utcfromtimestamp(k[0]/1000),
            Open  = float(k[1]),
            High  = float(k[2]),
            Low   = float(k[3]),
            Close = float(k[4])
        ))
    return pd.DataFrame(rows)

# ---------------------------------------------------------------------
# 3. STRATEGY
# ---------------------------------------------------------------------
def generate_signal():
    df = fetch_candles()
    df["EMA"] = df["Close"].ewm(span=EMA_SPAN, adjust=False).mean()
    prev, cur = df.iloc[-2], df.iloc[-1]

    body  = abs(prev.Close - prev.Open) + 1e-6
    upper = prev.High - max(prev.Open, prev.Close)
    lower = min(prev.Open, prev.Close) - prev.Low
    trend_up, trend_down = prev.Close > prev.EMA, prev.Close < prev.EMA

    # ---- LONG ----
    if trend_up and lower/body > .5 and prev.Low < prev.EMA <= prev.Close:
        entry = cur.Open
        return dict(direction="BUY",
                    entry=round(entry,2),
                    sl=round(entry - SL_USD,2),
                    tp=round(entry + SL_USD*TP_FACTOR,2),
                    ts=dt.datetime.utcnow().isoformat())
    # ---- SHORT ----
    if trend_down and upper/body > .5 and prev.High > prev.EMA >= prev.Close:
        entry = cur.Open
        return dict(direction="SELL",
                    entry=round(entry,2),
                    sl=round(entry + SL_USD,2),
                    tp=round(entry - SL_USD*TP_FACTOR,2),
                    ts=dt.datetime.utcnow().isoformat())

    return dict(direction="NONE", msg="no valid wick now", ts=dt.datetime.utcnow().isoformat())

@app.get("/signal")
@app.post("/signal")
def signal():
    try:
        return generate_signal()
    except Exception as e:
        raise HTTPException(502, f"data error: {e}")

# ---------------------------------------------------------------------
# 4. OPTIONAL BACK-TEST ENDPOINT
# ---------------------------------------------------------------------
@app.get("/backtest")
def backtest():
    if not os.path.exists(CSV_FILE):
        raise HTTPException(404, "upload a CSV first")
    df = pd.read_csv(CSV_FILE, parse_dates=["Time"])
    df["EMA"] = df["Close"].ewm(span=EMA_SPAN, adjust=False).mean()

    wins = losses = 0
    for i in range(1, len(df)):
        prev, nxt = df.iloc[i-1], df.iloc[i]
        body  = abs(prev.Close - prev.Open)+1e-6
        upper = prev.High - max(prev.Open, prev.Close)
        lower = min(prev.Open, prev.Close) - prev.Low
        trend_up, trend_down = prev.Close > prev.EMA, prev.Close < prev.EMA

        if trend_up and lower/body>.5 and prev.Low<prev.EMA<=prev.Close:
            entry, sl, tp = nxt.Open, prev.Low-SL_USD, nxt.Open+SL_USD*TP_FACTOR
            wins  += nxt.Low<=tp
            losses+= nxt.Low>=sl
        elif trend_down and upper/body>.5 and prev.High>prev.EMA>=prev.Close:
            entry, sl, tp = nxt.Open, prev.High+SL_USD, nxt.Open-SL_USD*TP_FACTOR
            wins  += nxt.High>=tp
            losses+= nxt.High<=sl
    total = wins+losses
    return dict(trades=total, wins=wins, losses=losses,
                win_rate=round(wins/total*100,2) if total else 0)
