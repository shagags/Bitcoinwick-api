from fastapi import FastAPI, HTTPException
import requests, pandas as pd, datetime as dt, os

# 1) Create the FastAPI instance before defining routes
app = FastAPI(title="BTC Wick-Rejection API")

# 2) Friendly root route to avoid 404 on "/"
@app.get("/")
def root():
    return {"status": "btcw-api live"}

# 3) Configuration for Binance REST k-line endpoint
SOURCE_URL = "https://data.binance.com/api/v3/klines"   # Must include symbol & interval as params
SYMBOL     = "BTCUSDT"                                 # BTC/USD (in USDT) on Binance
EMA_SPAN   = 50                                        # 50-period EMA
SL_USD     = 50                                        # $50 stop on BTC per trade
TP_FACTOR  = 1.5                                       # 1.5× risk:reward
CSV_FILE   = "/app/btc_m1.csv"                         # Path for uploaded history (optional)

# 4) Fetch last 60 1-min candles from Binance
def fetch_candles(count=60):
    params  = {"symbol": SYMBOL, "interval": "1m", "limit": count}
    headers = {}  # No API key needed for public data
    r = requests.get(SOURCE_URL, params=params, headers=headers, timeout=10)
    r.raise_for_status()  # Raise exception on 4xx/5xx
    rows = []
    for k in r.json():
        rows.append({
            "time":  dt.datetime.utcfromtimestamp(k[0] / 1000),
            "open":  float(k[1]),
            "high":  float(k[2]),
            "low":   float(k[3]),
            "close": float(k[4])
        })
    return pd.DataFrame(rows)

# 5) Core wick-rejection strategy
def generate_signal():
    df = fetch_candles()
    df["ema"] = df["close"].ewm(span=EMA_SPAN, adjust=False).mean()
    prev, cur = df.iloc[-2], df.iloc[-1]

    body  = abs(prev["close"] - prev["open"]) + 1e-6
    upper = prev["high"] - max(prev["open"], prev["close"])
    lower = min(prev["open"], prev["close"]) - prev["low"]
    trend_up   = prev["close"] > prev["ema"]
    trend_down = prev["close"] < prev["ema"]

    # Long signal
    if trend_up and (lower / body) > 0.5 and prev["low"] < prev["ema"] <= prev["close"]:
        entry = cur["open"]
        return {
            "direction": "BUY",
            "entry":     round(entry, 2),
            "sl":        round(entry - SL_USD, 2),
            "tp":        round(entry + SL_USD * TP_FACTOR, 2),
            "timestamp": dt.datetime.utcnow().isoformat()
        }
    # Short signal
    if trend_down and (upper / body) > 0.5 and prev["high"] > prev["ema"] >= prev["close"]:
        entry = cur["open"]
        return {
            "direction": "SELL",
            "entry":     round(entry, 2),
            "sl":        round(entry + SL_USD, 2),
            "tp":        round(entry - SL_USD * TP_FACTOR, 2),
            "timestamp": dt.datetime.utcnow().isoformat()
        }
    return {
        "direction": "NONE",
        "msg":       "no valid wick now",
        "timestamp": dt.datetime.utcnow().isoformat()
    }

# 6) Expose /signal via GET & POST so ChatGPT (which uses POST) and manual GET both work
@app.get("/signal")
@app.post("/signal")
def signal():
    try:
        return generate_signal()
    except Exception as e:
        raise HTTPException(502, f"Error fetching data or generating signal: {e}")

# 7) Optional: backtest endpoint using uploaded CSV
@app.get("/backtest")
def backtest():
    if not os.path.exists(CSV_FILE):
        raise HTTPException(404, "Historical CSV not found; please upload first")
    df = pd.read_csv(CSV_FILE, parse_dates=["time"])
    df["ema"] = df["close"].ewm(span=EMA_SPAN, adjust=False).mean()
    wins = losses = 0
    for i in range(1, len(df)):
        prev, nxt = df.iloc[i - 1], df.iloc[i]
        body  = abs(prev["close"] - prev["open"]) + 1e-6
        upper = prev["high"] - max(prev["open"], prev["close"])
        lower = min(prev["open"], prev["close"]) - prev["low"]
        trend_up   = prev["close"] > prev["ema"]
        trend_down = prev["close"] < prev["ema"]

        if trend_up and (lower / body) > 0.5 and prev["low"] < prev["ema"] <= prev["close"]:
            entry = nxt["open"]
            sl = prev["low"] - SL_USD
            tp = entry + SL_USD * TP_FACTOR
            wins   += nxt["low"] <= tp
            losses += nxt["low"] >= sl
        elif trend_down and (upper / body) > 0.5 and prev["high"] > prev["ema"] >= prev["close"]:
            entry = nxt["open"]
            sl = prev["high"] + SL_USD
            tp = entry - SL_USD * TP_FACTOR
            wins   += nxt["high"] >= tp
            losses += nxt["high"] <= sl

    total = wins + losses
    return {
        "trades":   total,
        "wins":     wins,
        "losses":   losses,
        "win_rate": round(wins / total * 100, 2) if total else 0
    }

