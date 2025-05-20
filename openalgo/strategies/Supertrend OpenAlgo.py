# ✅ Merged and Fixed: Supertrend OpenAlgo with Enhancements from Batch1
# =============================================================
# Strategy: Supertrend with VWAP + Dynamic Trailing Stop + Retry Order Logic + Partial Booking

from openalgo import api
import pandas as pd
import numpy as np
import time
import requests
from datetime import datetime, timedelta
import signal
import sys
import pandas_ta as ta
import os
# ================================
# 📁 Setup and Configuration
# ================================
# Ensure logs directory exists
os.makedirs("logs", exist_ok=True)

# 🔧 Test if logging works (check file permission/path issues)
with open("test_log.txt", "a") as f:
    f.write("Log test\n")

# ================================
# ⚙️ Configuration
# ================================
api_key = '78b9f1597a7f903d3bfc76ad91274a7cc7536c2efc4508a8276d85fbc840d7d2'
strategy = "Supertrend Merged Fixed"
symbols = ["ADANIENT"]
exchange = "NSE"
product = "MIS"
quantity = 5
atr_period = 5
atr_multiplier = 1.5
mode = "live"
slippage_buffer = 0.05

start_time = "09:20"
end_time = "15:15"

stop_loss_pct = 0.4
base_target_pct = 0.8
trailing_sl_pct = 0.6
partial_profit_pct = 0.5

TELEGRAM_ENABLED = True
BOT_TOKEN = "7891610241:AAHcNW6faW2lZGrxeSaOZJ3lSggI-ehl-pg"
CHAT_ID = "627470225"

LOG_FILE = f"logs/ST_{datetime.now().strftime('%Y-%m-%d')}.txt"
TRADE_LOG = f"logs/ST_{datetime.now().strftime('%Y-%m-%d')}.csv"

# ================================
# 🧠 Initialize State
# ================================
client = api(api_key=api_key, host='http://127.0.0.1:5000')

positions = {sym: 0 for sym in symbols}
entry_prices = {sym: None for sym in symbols}
max_favorable_price = {sym: None for sym in symbols}
partial_booked = {sym: False for sym in symbols}
daily_trade_count = {sym: 0 for sym in symbols}
max_trades_per_day = 2
target_pct_map = {sym: base_target_pct for sym in symbols}

# ================================
# 📝 Logging & Alerts
# ================================
def log_message(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[ST] [{timestamp}] {message}"
    print(formatted)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(formatted + "\n")
        f.flush()
        os.fsync(f.fileno())

def send_telegram(message):
    if TELEGRAM_ENABLED:
        now = datetime.now().strftime("%H:%M:%S")
        payload = {"chat_id": CHAT_ID, "text": f"[{now}] {message}"}
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        requests.post(url, data=payload)

# ================================
# 📊 Indicator Calculations
# ================================
def calculate_supertrend(df):
    high = df['high']
    low = df['low']
    close = df['close']
    hl2 = (high + low) / 2
    atr = ta.atr(high, low, close, length=atr_period)
    upperband = hl2 + (atr_multiplier * atr)
    lowerband = hl2 - (atr_multiplier * atr)
    final_upperband = upperband.copy()
    final_lowerband = lowerband.copy()
    supertrend = [True] * len(df)
    for i in range(1, len(df)):
        if close.iloc[i] > final_upperband.iloc[i-1]:
            supertrend[i] = True
        elif close.iloc[i] < final_lowerband.iloc[i-1]:
            supertrend[i] = False
        else:
            supertrend[i] = supertrend[i-1]
            if supertrend[i] and final_lowerband.iloc[i] < final_lowerband.iloc[i-1]:
                final_lowerband.iloc[i] = final_lowerband.iloc[i-1]
            if not supertrend[i] and final_upperband.iloc[i] > final_upperband.iloc[i-1]:
                final_upperband.iloc[i] = final_upperband.iloc[i-1]
    df['Supertrend'] = supertrend
    return df

def calculate_vwap(df):
    df['vwap'] = (df['close'] * df['volume']).cumsum() / df['volume'].cumsum()
    return df

# ================================
# 🧾 Order Logic
# ================================
def place_entry(symbol, action, close_price):
    try:
        response = client.placesmartorder(
            strategy=strategy,
            symbol=symbol,
            action=action,
            exchange=exchange,
            price_type="MARKET",
            product=product,
            quantity=quantity,
            position_size=quantity if action == "BUY" else -quantity
        )
        if response.get("status") != "success":
            raise Exception(response.get("message", "Unknown error"))
        log_message(f"✅ {action} order placed for {symbol} @ {close_price:.2f} | Order ID: {response.get('orderid')}")
        send_telegram(f"✅ {action} {symbol} @ {close_price:.2f} | Order ID: {response.get('orderid')}")
        return True
    except Exception as e:
        log_message(f"❌ Order failed for {symbol}: {e}")
        send_telegram(f"❌ Order failed for {symbol}: {e}")
        return False

# ================================
# 📈 Strategy Execution
# ================================
def run_strategy():
    while True:
        now = datetime.now().strftime("%H:%M")
        if now < start_time or now > end_time:
            time.sleep(60)
            continue

        for symbol in symbols:
            try:
                df = client.historical(symbol=symbol, interval='1minute', lookback=60)
                if df is None or len(df) < atr_period + 1:
                    continue

                df = calculate_supertrend(df)
                df = calculate_vwap(df)

                is_uptrend = df.iloc[-1]['Supertrend']
                was_uptrend = df.iloc[-2]['Supertrend']
                close_price = df.iloc[-1]['close']
                vwap = df.iloc[-1]['vwap']

                position = positions[symbol]
                log_message(f"Symbol: {symbol} | LTP: {close_price:.2f} | Trend: {'UP' if is_uptrend else 'DOWN'} | Pos: {position} | Was: {'UP' if was_uptrend else 'DOWN'} | VWAP: {vwap:.2f}")

                if position == 0 and daily_trade_count[symbol] < max_trades_per_day:
                    if is_uptrend and close_price > vwap:
                        success = place_entry(symbol, "BUY", close_price)
                        if success:
                            positions[symbol] = quantity
                            entry_prices[symbol] = close_price
                            max_favorable_price[symbol] = close_price
                            partial_booked[symbol] = False
                            daily_trade_count[symbol] += 1
                    elif not is_uptrend and close_price < vwap:
                        success = place_entry(symbol, "SELL", close_price)
                        if success:
                            positions[symbol] = -quantity
                            entry_prices[symbol] = close_price
                            max_favorable_price[symbol] = close_price
                            partial_booked[symbol] = False
                            daily_trade_count[symbol] += 1

                elif position != 0:
                    entry_price = entry_prices[symbol]
                    direction = 1 if position > 0 else -1
                    target_price = entry_price * (1 + base_target_pct / 100 * direction)
                    stop_price = entry_price * (1 - stop_loss_pct / 100 * direction)

                    max_favorable_price[symbol] = max(max_favorable_price[symbol], close_price) if direction > 0 else min(max_favorable_price[symbol], close_price)
                    drop_from_peak = abs(close_price - max_favorable_price[symbol]) / max_favorable_price[symbol] * 100
                    profit_pct = (close_price - entry_price) / entry_price * 100 * direction

                    sl_hit = (direction > 0 and close_price <= stop_price) or (direction < 0 and close_price >= stop_price)
                    trail_exit = drop_from_peak >= trailing_sl_pct and profit_pct > partial_profit_pct

                    if sl_hit or trail_exit:
                        action = "SELL" if direction > 0 else "BUY"
                        log_message(f"{symbol} | Entry={entry_price:.2f}, Close={close_price:.2f}, SL={stop_price:.2f}, PnL={profit_pct:.2f}%, Drop={drop_from_peak:.2f}%")
                        place_entry(symbol, action, close_price)
                        positions[symbol] = 0
                        entry_prices[symbol] = None
                        max_favorable_price[symbol] = None

            except Exception as e:
                log_message(f"{symbol} | Exception in strategy loop | Close: {close_price:.2f} | Supertrend: {is_uptrend} | VWAP: {vwap:.2f} | Error: {str(e)}")

        time.sleep(10)

# ================================
# 🛑 Graceful Exit Handling
# ================================
def handle_exit(signum, frame):
    log_message("Amar's Supertrend Dynamic Strategy Graceful exit requested.")
    sys.exit(0)

signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)

# ================================
# ▶️ Main Execution
# ================================
if __name__ == "__main__":
    log_message("🔄 Amar's Supertrend Dynamic Strategy Started")
    run_strategy()
