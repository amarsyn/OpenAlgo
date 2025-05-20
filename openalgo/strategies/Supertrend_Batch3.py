# Supertrend Batch-3 Corrected Version with Diagnostics and Enhancements
# Supertrend Batch-3 Multi-Stock Strategy
# ----------------------------------------------------
# Description:
# This script is an intraday trend-following strategy that uses Supertrend, RSI, ADX, and VWAP indicators.
# It executes trades on multiple symbols using OpenAlgo API, with support for partial profit booking,
# dynamic trailing stop-loss, daily trade limits, and real-time logging to file and Telegram.
# 
# Enhancements:
# - Buffered entry condition (PrevHigh * 0.995)
# - ATR-based volatility filter
# - Time-based exit if trade stagnates
# ----------------------------------------------------

from openalgo import api
import pandas as pd
import numpy as np
import time
import requests
import os
from datetime import datetime, timedelta
import signal
import sys
import pandas_ta as ta

# ================================
# 📁 Setup and Configuration
# ================================
# Ensure logs directory exists
os.makedirs("logs", exist_ok=True)

# 🔧 Test if logging works (check file permission/path issues)
with open("test_log.txt", "a") as f:
    f.write("Log test\n")

# Configuration and Threshold Parameters
api_key = '78b9f1597a7f903d3bfc76ad91274a7cc7536c2efc4508a8276d85fbc840d7d2'
strategy = "Supertrend Python B3"
symbols = ["BHARATFORG", "EUREKAFORB","DODLA"]
exchange = "NSE"
product = "MIS"
quantity = 5
atr_period = 5
atr_multiplier = 0.8
mode = "live"

stop_loss_pct = 2
target_pct = 4
trailing_sl_pct = 0.75
trailing_trigger_pct = 1

TELEGRAM_ENABLED = True
BOT_TOKEN = "7891610241:AAHcNW6faW2lZGrxeSaOZJ3lSggI-ehl-pg"
CHAT_ID = "627470225"

start_time = "09:20"
end_time = "14:45"

LOG_FILE = f"logs/ST_B3_{datetime.now().strftime('%Y-%m-%d')}.txt"
TRADE_LOG = f"logs/ST_B3_{datetime.now().strftime('%Y-%m-%d')}.csv"

positions = {sym: 0 for sym in symbols}
entry_prices = {sym: None for sym in symbols}
max_favorable_price = {sym: None for sym in symbols}
entry_times = {sym: None for sym in symbols}
trade_counts = {sym: 0 for sym in symbols}
partial_booked = {sym: False for sym in symbols}

client = api(api_key=api_key, host='http://127.0.0.1:5000')

# ----------------------------------------------------
# Function: send_telegram
# Sends real-time alerts to Telegram based on bot token and chat ID
# ----------------------------------------------------
def send_telegram(message):
    if TELEGRAM_ENABLED:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": message}
        response = requests.post(url, data=payload)
        print(f"📩 Telegram response: {response.text}")

# ----------------------------------------------------
# Function: log_message
# Logs messages to both console and a timestamped log file
# ----------------------------------------------------
def log_message(msg):
    print(msg)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now()} - {msg}\n")

# ----------------------------------------------------
# Function: add_indicators
# Adds RSI, VWAP, and ADX to the OHLCV dataframe for trade signal logic
# ----------------------------------------------------
def add_indicators(df):
    df['RSI'] = ta.rsi(df['close'], length=14)
    df['VWAP'] = (df['close'] * df['volume']).cumsum() / df['volume'].cumsum()
    adx = ta.adx(df['high'], df['low'], df['close'])
    df['ADX'] = adx['ADX_14'] if 'ADX_14' in adx.columns else np.nan
    df['ATR'] = ta.atr(df['high'], df['low'], df['close'], length=14)
    return df

# ----------------------------------------------------
# Function: Supertrend
# Calculates the Supertrend indicator based on ATR and appends the trend signal to the dataframe
# ----------------------------------------------------
def Supertrend(df, atr_period, multiplier):
    high, low, close = df['high'], df['low'], df['close']
    tr = pd.concat([high - low, abs(high - close.shift()), abs(low - close.shift())], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/atr_period, min_periods=atr_period).mean()
    hl2 = (high + low) / 2
    upperband = hl2 + multiplier * atr
    lowerband = hl2 - multiplier * atr
    trend = [True]
    for i in range(1, len(close)):
        if close.iloc[i] > upperband.iloc[i-1]:
            trend.append(True)
        elif close.iloc[i] < lowerband.iloc[i-1]:
            trend.append(False)
        else:
            trend.append(trend[-1])
    df['Supertrend'] = pd.Series(trend, index=df.index)
    return df

# ----------------------------------------------------
# Function: within_trading_hours
# Checks if the current time is within the defined intraday trading window
# ----------------------------------------------------
def within_trading_hours():
    now = datetime.now().time()
    return datetime.strptime(start_time, "%H:%M").time() <= now <= datetime.strptime(end_time, "%H:%M").time()

# ----------------------------------------------------
# Function: place_exit
# Executes a market order to exit an open position, resets internal tracking state
# ----------------------------------------------------
def place_exit(symbol, side):
    action = "SELL" if side == "long" else "BUY"
    response = client.placeorder(strategy=strategy, symbol=symbol, action=action,
                                 exchange=exchange, price_type="MARKET",
                                 product=product, quantity=abs(positions[symbol]))
    log_message(f"🔁 EXIT {side.upper()} {symbol} at market: {response}")
    send_telegram(f"EXIT {side.upper()} {symbol}")
    positions[symbol] = 0
    entry_prices[symbol] = None
    max_favorable_price[symbol] = None
    entry_times[symbol] = None
    partial_booked[symbol] = False

# ----------------------------------------------------
# Function: supertrend_strategy
# Core logic that runs the strategy loop: signal evaluation, trade execution,
# trailing SL handling, partial booking, and exit logic
# ----------------------------------------------------
def supertrend_strategy():
    while True:
        log_message("🔁 Strategy loop running...")

        # Skip execution if outside defined trading hours
        if not within_trading_hours():
            log_message("⏳ Outside trading hours. Waiting...")
            time.sleep(60)
            continue
            
        # Iterate over each stock symbol defined
        for symbol in symbols:
            try:
                df = client.history(symbol=symbol, exchange=exchange, interval="5m",
                                    start_date=(datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d'),
                                    end_date=datetime.now().strftime('%Y-%m-%d'))
                
                # Skip if data is missing or incomplete
                if df.empty or not {'open', 'high', 'low', 'close', 'volume'}.issubset(df.columns):
                    log_message(f"⚠️ No valid data for {symbol}, skipping...")
                    continue

                 # Log the latest close and calculate indicators
                log_message(f"📊 Data fetched for {symbol}, latest close={df['close'].iloc[-1]}")
                df = Supertrend(df, atr_period, atr_multiplier)
                df = add_indicators(df)
                current = df.iloc[-1]['close']

                # Extract latest indicator values for trade signal logic
                st, rsi, adx, vwap, atr = df.iloc[-1][['Supertrend', 'RSI', 'ADX', 'VWAP', 'ATR']]
                prev_high, prev_low = df.iloc[-2][['high', 'low']]
                log_message(f"🧪 {symbol} ST={st}, RSI={rsi:.2f}, ADX={adx:.2f}, VWAP={vwap:.2f}, Close={current:.2f}, PrevHigh={prev_high:.2f}, PrevLow={prev_low:.2f}")

                # Define entry signal criteria with buffered PrevHigh/Low and ATR filter
                volatility_ok = (atr / current) > 0.0015  # e.g., 0.3% volatility
                long_signal = all([st, rsi > 50, adx > 15, current > vwap, current >= prev_high * 0.995, volatility_ok])
                short_signal = all([not st, rsi < 50, adx > 15, current < vwap, current <= prev_low * 1.005, volatility_ok])

                log_message(f"🔍 Signal Check {symbol}: LONG={long_signal}, SHORT={short_signal}, Volatility_OK={volatility_ok:.2f}, ATR={atr:.2f}")
                
                # Exit position if reverse signal occurs
                if positions[symbol] > 0 and short_signal:
                    place_exit(symbol, "long")
                elif positions[symbol] < 0 and long_signal:
                    place_exit(symbol, "short")

                # Enter new position if none open and trade limit not exceeded
                if positions[symbol] == 0 and trade_counts[symbol] < 2:
                    if long_signal:
                        res = client.placeorder(strategy=strategy, symbol=symbol, action="BUY",
                                                exchange=exchange, price_type="MARKET",
                                                product=product, quantity=quantity)
                        positions[symbol] = quantity
                        entry_prices[symbol] = current
                        max_favorable_price[symbol] = current
                        entry_times[symbol] = datetime.now()
                        partial_booked[symbol] = False
                        trade_counts[symbol] += 1
                        log_message(f"🚀 LONG {symbol}: {res}")
                        send_telegram(f"LONG {symbol}")
                    elif short_signal:
                        res = client.placeorder(strategy=strategy, symbol=symbol, action="SELL",
                                                exchange=exchange, price_type="MARKET",
                                                product=product, quantity=quantity)
                        positions[symbol] = -quantity
                        entry_prices[symbol] = current
                        max_favorable_price[symbol] = current
                        entry_times[symbol] = datetime.now()
                        partial_booked[symbol] = False
                        trade_counts[symbol] += 1
                        log_message(f"🔻 SHORT {symbol}: {res}")
                        send_telegram(f"SHORT {symbol}")

                # If position is open, manage SL, target, trailing SL, and partial exits
                if positions[symbol] != 0:
                    entry = entry_prices[symbol]
                    max_fav = max_favorable_price[symbol]
                    if max_fav is None:
                        max_fav = current
                    max_favorable_price[symbol] = max(max_fav, current) if positions[symbol] > 0 else min(max_fav, current)

                    # Set static SL and target
                    tgt = entry * (1 + target_pct / 100) if positions[symbol] > 0 else entry * (1 - target_pct / 100)
                    sl = entry * (1 - stop_loss_pct / 100) if positions[symbol] > 0 else entry * (1 + stop_loss_pct / 100)

                    # Adjust SL to trailing once price moves in favor
                    trail_trigger = entry * (1 + trailing_trigger_pct / 100) if positions[symbol] > 0 else entry * (1 - trailing_trigger_pct / 100)
                    if (positions[symbol] > 0 and max_fav > trail_trigger) or (positions[symbol] < 0 and max_fav < trail_trigger):
                        trail_sl = max_fav * (1 - trailing_sl_pct / 100) if positions[symbol] > 0 else max_fav * (1 + trailing_sl_pct / 100)
                        sl = max(sl, trail_sl) if positions[symbol] > 0 else min(sl, trail_sl)

                    log_message(f"⏳ Monitoring {symbol} | Entry: {entry:.2f} | LTP: {current:.2f} | Target: {tgt:.2f} | SL: {sl:.2f}")

                    # Book partial profit if price moves 0.5% in your favor
                    partial_target = entry * (1 + 0.5 / 100) if positions[symbol] > 0 else entry * (1 - 0.5 / 100)
                    if not partial_booked[symbol] and ((positions[symbol] > 0 and current >= partial_target) or (positions[symbol] < 0 and current <= partial_target)):
                        res = client.placeorder(strategy=strategy, symbol=symbol, action="SELL" if positions[symbol] > 0 else "BUY",
                                                exchange=exchange, price_type="MARKET", product=product, quantity=quantity//2)
                        log_message(f"💰 PARTIAL EXIT for {symbol}: {res}")
                        send_telegram(f"PARTIAL EXIT {symbol}")
                        positions[symbol] = positions[symbol] // 2
                        partial_booked[symbol] = True

                    # Exit if price hits final SL or target
                    if (positions[symbol] > 0 and (current >= tgt or current <= sl)) or \
                       (positions[symbol] < 0 and (current <= tgt or current >= sl)):
                        place_exit(symbol, "long" if positions[symbol] > 0 else "short")

                    # Time-based exit after 15 minutes if price stagnant
                    if entry_times[symbol] and datetime.now() - entry_times[symbol] > timedelta(minutes=15):
                        if abs(current - entry) < entry * 0.002:  # <0.2% movement
                            log_message(f"⏳ TIME-BASED EXIT {symbol}: price stagnant since entry")
                            place_exit(symbol, "long" if positions[symbol] > 0 else "short")

            except Exception as e:
              print(f"Error in strategy: {str(e)}")  
              log_message(f"❌ Error: {symbol} -> {e}")
        time.sleep(30)

# ----------------------------------------------------
# Main block: starts the strategy, handles shutdown gracefully
# ----------------------------------------------------
if __name__ == "__main__":
    print("Starting Amar's Supertrend Batch-3 Multi-Stock Strategy...")
    send_telegram(f"✅ Amar's Supertrend Batch-3 Multi-Stock strategy started in {mode.upper()} mode.")
    log_message(f"Amar's Supertrend Batch-3 Strategy started in {mode.upper()} mode.")
    def graceful_exit(signum, frame):
        print("Graceful shutdown requested... Exiting strategy.")
        send_telegram("🛑 Supertrend Batch-3 Strategy stopped gracefully.")
        log_message("Graceful shutdown invoked.")
        sys.exit(0)

    signal.signal(signal.SIGINT, graceful_exit)
    signal.signal(signal.SIGTERM, graceful_exit)
    supertrend_strategy()