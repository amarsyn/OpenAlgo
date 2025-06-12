# =======================
# Import Dependencies
# =======================
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timedelta
import time
import sys
import signal
from openalgo import api
import requests
import os
import threading
from collections import defaultdict
import csv
import statistics
# ========== Global Config ==========
interval = "1minute"  # or "5minute"
cooldown_seconds = 180  # 3 minutes cooldown

# Determine candle granularity
candle_interval_minutes = 1
if interval == "5minute":
    candle_interval_minutes = 5

cooldown_candles = cooldown_seconds // (candle_interval_minutes * 60)

max_bars = 48  # limit future bars to ~4 hours of 5-min candles
positions = defaultdict(lambda: None)
last_trade_time = defaultdict(lambda: datetime.min)

# ==============================
# Setup and Configuration
# ==============================
os.makedirs("logs", exist_ok=True)

api_key = '78b9f1597a7f903d3bfc76ad91274a7cc7536c2efc4508a8276d85fbc840d7d2'
strategy_name = "WMA Dynamic Strategy"
symbols =["DRREDDY","AUBANK"]
exchange = "NSE"
product = "MIS"
quantity = 5
mode = "live"  # or "backtest" or Analyze
start_time = "09:15"
end_time = "15:10"
sl_pct = 2.0  
target_pct = 5.0
sl_atr_multiplier = 2.0  
target_atr_multiplier = 5.0  
trailing_sl_pct = 1.0
trailing_trigger_pct = 0.8
# Example: Trigger trail after +1.5×ATR, trail at 1×ATR
trailing_trigger_atr_mult = 1.5
trailing_sl_atr_mult = 1.0

# Set backtest range and fixed date for historical evaluation
backtest_start_date = datetime(2025, 3, 1)
backtest_end_date = datetime(2025, 6, 2)

LOG_FILE = f"logs/WMA_dynamic_{datetime.now().strftime('%Y-%m-%d')}.txt"
TRADE_LOG = f"logs/WMA_dynamic_{datetime.now().strftime('%Y-%m-%d')}.csv"

TELEGRAM_ENABLED = True
BOT_TOKEN = "7891610241:AAHcNW6faW2lZGrxeSaOZJ3lSggI-ehl-pg"
CHAT_ID = "627470225"

client = api(api_key=api_key)

# =====================
# Utility Functions
# =====================
def send_telegram(message):
    if TELEGRAM_ENABLED:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": message}
        requests.post(url, data=payload)

def log_message(msg):
    if mode.lower() in ["backtest", "analyze"]:
        timestamp = backtest_end_date.strftime('%Y-%m-%d') + " (BT)"
    else:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f"[{timestamp}] WMA_Dynamic {msg}")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] WMA_Dynamic {msg}\n")

def log_trade(symbol, entry_price, exit_price, direction, reason):
    try:
        profit_pct = ((exit_price - entry_price) / entry_price) * 100 if direction == "bullish" else ((entry_price - exit_price) / entry_price) * 100
        profit_pct = round(profit_pct, 2)
    except Exception as e:
        log_message(f"[ERROR] Failed to calculate profit: {e}")
        profit_pct = 0.0

    with open("trade_log.csv", "a") as f:
        f.write(f"{datetime.now()},{symbol},{entry_price},{exit_price},{profit_pct:.2f},{reason}\n")

def log_backtest(symbol, entry_price, exit_price, direction, result, reason, entry_time, mfe, mae):
    with open(f"logs/backtest_{backtest_end_date.strftime('%Y-%m-%d')}.csv", "a") as f:
        f.write(f"{entry_time},{symbol},{direction},{entry_price},{exit_price},{result:.2f},{reason},{mfe:.2f},{mae:.2f}\n")

# =======================
# MACD Crossover Utility
# =======================
def recent_macd_bullish_cross(df, lookback=3):
    macd = df['macd']
    macd_signal = df['macd_signal']
    for i in range(-lookback - 1, -1):
        if macd.iloc[i - 1] < macd_signal.iloc[i - 1] and macd.iloc[i] > macd_signal.iloc[i]:
            return True
    return False

def recent_macd_bearish_cross(df, lookback=3):
    macd = df['macd']
    macd_signal = df['macd_signal']
    for i in range(-lookback - 1, -1):
        if macd.iloc[i - 1] > macd_signal.iloc[i - 1] and macd.iloc[i] < macd_signal.iloc[i]:
            return True
    return False

# =====================
# Data Fetching
# =====================
def fetch_data(symbol):
    try:
        if mode.lower() in ["backtest", "analyze"]:
            start_date = backtest_start_date
            end_date = backtest_end_date
        else:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=3)  # or 5 if you're using 5-min candles

        result = client.history(
            symbol=symbol,
            exchange=exchange,
            interval="5m",
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d")
        )

        # === Validate response ===
        if isinstance(result, dict) and "data" in result:
            data = result["data"]
        elif isinstance(result, pd.DataFrame):
            data = result
        else:
            log_message(f"{symbol}: ❌ Invalid or empty history response")
            return None

        df = pd.DataFrame(data)
        if df.empty:
            log_message(f"{symbol}: ❌ Empty DataFrame received")
            return None

        # === Ensure required columns ===
        required_cols = {'open', 'high', 'low', 'close', 'volume'}
        if not required_cols.issubset(df.columns):
            log_message(f"{symbol}: ❌ Missing required OHLCV columns: {required_cols - set(df.columns)}")
            return None

        # === Timestamp Indexing ===
        if 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df.set_index('timestamp', inplace=True)
        elif df.index.name != 'timestamp':
            df.index = pd.to_datetime(df.index)

        # === Add indicators (with NaN checks) ===
        df['wma'] = ta.wma(df['close'], length=20)
        df['rsi'] = ta.rsi(df['close'], length=14)
        df['vol_ma'] = df['volume'].rolling(20).mean()

        macd_df = ta.macd(df['close'])
        if macd_df is not None and 'MACD_12_26_9' in macd_df.columns:
            df['macd'] = macd_df['MACD_12_26_9']
            df['macd_signal'] = macd_df['MACDs_12_26_9']

            if df[['macd', 'macd_signal']].iloc[-5:].isnull().all().any():
                log_message(f"{symbol}: ⚠️ MACD values are NaN in recent candles.")
                return None
        else:
            log_message(f"{symbol}: ⚠️ MACD calculation failed")
            return None

        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)

        return df

    except Exception as e:
        log_message(f"{symbol}: ❌ Exception in fetch_data — {e}")
        return None

# =====================
# Trend Detection
# =====================
def detect_trend(df, symbol=None, rsi_bull=51, rsi_bear=49, macd_thresh=0.15, min_atr_pct=0.001):
    if df is None or len(df) < 2:
        log_message(f"Skipping {symbol if symbol else 'UNKNOWN'} due to invalid or insufficient data.")
        return None

    # Check if required columns have nulls
    required_cols = ['wma', 'rsi', 'macd', 'macd_signal', 'volume', 'vol_ma', 'atr']
    if df[required_cols].iloc[-2:].isnull().values.any():
        log_message("Trend detection skipped: Required indicator values are missing (NaN).")
        return None

    last = df.iloc[-1]
    prev = df.iloc[-2]

    log_message(
        f"Checking trend — {symbol} | Close: {last['close']}, WMA: {last['wma']}, Prev_WMA: {prev['wma']}, "
        f"RSI: {last['rsi']}, MACD: {last['macd']}, Signal: {last['macd_signal']}, "
        f"Vol: {last['volume']}, Vol_MA: {last['vol_ma']}, ATR: {last['atr']}"
    )

    # ✅ ATR filter
    min_atr = df['close'].mean() * min_atr_pct
    if last['atr'] < min_atr:
        log_message(f"{symbol}: Filtered out due to low ATR: {last['atr']} < {min_atr}")
        return None

    # ✅ Volume filters
    if last['volume'] < last['vol_ma'] * 0.75:
        log_message(f"{symbol}: Filtered due to weak current volume: {last['volume']} < 75% of MA {last['vol_ma']}")
        return None
    if last['volume'] < last['vol_ma']:
        log_message(f"{symbol}: ⚠️ Very low average volume, skipping for safety.")
        return None

    macd_delta = last['macd'] - last['macd_signal']
    wma_slope = last['wma'] - prev['wma']

    # ✅ Bullish conditions
    if last['rsi'] >= rsi_bull and macd_delta > macd_thresh and wma_slope > 0:
        log_message(f"{symbol}: ✅ Valid Bullish Trend Detected.")
        return "bullish"

    # ✅ Bearish conditions
    elif last['rsi'] <= rsi_bear and macd_delta < -macd_thresh and wma_slope < 0:
        log_message(f"{symbol}: ✅ Valid Bearish Trend Detected.")
        return "bearish"

    # 🚫 No trend matched
    log_message(f"{symbol}: No valid trend detected — Conditions not met:")
    log_message(f"  RSI: {last['rsi']} (Need ≥{rsi_bull} or ≤{rsi_bear})")
    log_message(f"  MACD Δ: {macd_delta} (Need > {macd_thresh} or < -{macd_thresh})")
    log_message(f"  WMA Slope: {'up' if wma_slope > 0 else 'down' if wma_slope < 0 else 'flat'}")
    return None

# =======================
# Trailing SL Logic
# =======================
def monitor_position(symbol, direction, entry_price, sl, target):
    in_position = True
    trail_triggered = False
    trail_target = None
    exit_price = None
    reason = "Unknown"

    try:
        while in_position:
            time.sleep(60)  # check every minute
            log_message(f"Processing {symbol}...")
            df = fetch_data(symbol)
            if df is None or len(df) < 2:
                log_message(f"Skipping {symbol} due to insufficient data.")
                continue

            current_price = df['close'].iloc[-1]
            atr = df['atr'].iloc[-1]  # ensure ATR is available

            # ✅ Bullish logic
            if direction == "bullish":
                if current_price <= sl:
                    exit_price = current_price
                    reason = "Stop Loss Hit"
                    exit_position(symbol, direction)
                    in_position = False

                elif current_price >= target:
                    exit_price = current_price
                    reason = "Target Hit"
                    exit_position(symbol, direction)
                    in_position = False

                elif not trail_triggered and current_price >= entry_price + (atr * trailing_trigger_atr_mult):
                    trail_triggered = True
                    trail_target = current_price - atr * trailing_sl_atr_mult
                    log_message(f"{symbol}: ATR-based trailing activated at {trail_target:.2f}")

                elif trail_triggered and current_price <= trail_target:
                    exit_price = current_price
                    reason = "Trailing Stop Hit"
                    exit_position(symbol, direction)
                    in_position = False

                elif trail_triggered:
                    trail_target = max(trail_target, current_price - atr * trailing_sl_atr_mult)

            # ✅ Bearish logic
            elif direction == "bearish":
                if current_price >= sl:
                    exit_price = current_price
                    reason = "Stop Loss Hit"
                    exit_position(symbol, direction)
                    in_position = False

                elif current_price <= target:
                    exit_price = current_price
                    reason = "Target Hit"
                    exit_position(symbol, direction)
                    in_position = False

                elif not trail_triggered and current_price <= entry_price - (atr * trailing_trigger_atr_mult):
                    trail_triggered = True
                    trail_target = current_price + atr * trailing_sl_atr_mult
                    log_message(f"{symbol}: ATR-based trailing activated at {trail_target:.2f}")

                elif trail_triggered and current_price >= trail_target:
                    exit_price = current_price
                    reason = "Trailing Stop Hit"
                    exit_position(symbol, direction)
                    in_position = False

                elif trail_triggered:
                    trail_target = min(trail_target, current_price + atr * trailing_sl_atr_mult)

            # ✅ Force exit before market close
            if datetime.now().strftime("%H:%M") > end_time:
                exit_price = current_price
                reason = "Auto Exit — Market Close"
                exit_position(symbol, direction)
                in_position = False

    except Exception as e:
        log_message(f"[ERROR] monitor_position failed for {symbol}: {e}")
        reason = f"Monitor Error: {e}"
        exit_price = df['close'].iloc[-1] if 'df' in locals() and not df.empty else entry_price
        positions[symbol] = None

    # Always log exit
    log_trade(symbol, entry_price, exit_price, direction, reason)
    send_telegram(f"{symbol} → {reason} at {exit_price:.2f}")
    positions[symbol] = None

# =====================
# Order Placement
# =====================
def place_exit_order(symbol, action, price, trigger_price=None, reason="SL"):
    """
    Unified function to place exit orders (SL, Target, Trail).
    All orders are placed as MARKET to avoid hanging orders.
    """
    try:
        log_message(f"[EXIT] Attempting to exit {symbol} due to {reason}")
        order_type = "MARKET"
        response = client.placeorder(
            strategy=strategy_name,
            symbol=symbol,
            action=action,
            exchange=exchange,
            price_type=order_type,
            product=product,
            quantity=quantity,
            price=0,
            trigger_price="0"
        )

        log_message(f"📦 {reason} Order | {symbol} | {action} | MARKET | Response: {response}")
        send_telegram(f"{reason} EXIT for {symbol}: {action} @ MARKET")

        # ✅ Prevent rapid re-entry after exit (SL/TP/Trail)
        positions[symbol] = None
        last_trade_time[symbol] = datetime.now()

        return response

    except Exception as e:
        log_message(f"[ERROR] Failed to place {reason} order for {symbol}: {e}")
        return None

def cancel_open_orders(symbol):
    try:
        cancel_response = client.cancel_orders(symbol=symbol, exchange=exchange)
        log_message(f"Cancelled open orders for {symbol}: {cancel_response}")
    except Exception as e:
        log_message(f"[ERROR] Failed to cancel open orders for {symbol}: {e}")

def exit_position(symbol, direction):
    cancel_open_orders(symbol)  # ✅ Cancel SL/Target
    action = "SELL" if direction == "bullish" else "BUY"
    try:
        response = client.placeorder(
            strategy=strategy_name,
            symbol=symbol,
            action=action,
            exchange=exchange,
            price_type="MARKET",
            product=product,
            quantity=quantity
        )
        log_message(f"Exit Order Placed for {symbol}. Order ID: {response['orderid']}")
        send_telegram(f"✅ Exit Order for {symbol}, ID: {response['orderid']}")
    except Exception as e:
        positions[symbol] = None
        log_message(f"Exit failed for {symbol}: {str(e)}")
        send_telegram(f"❌ Exit failed for {symbol}: {str(e)}")

# =====================
# ATR-Based SL and Dynamic Target Booking
# =====================
def calculate_sl_target(entry_price, atr, direction):
    """Returns dynamic stop-loss and target based on ATR multipliers."""
    direction = direction.strip().lower()  # Normalize input

    if not direction or direction.strip().lower() not in ["bullish", "bearish"]:
        raise ValueError(f"Invalid direction passed to SL calculator: {direction}")

    if direction == "bullish":
        sl = entry_price - sl_atr_multiplier * atr
        target = entry_price + target_atr_multiplier * atr
        return sl, target
    elif direction == "bearish":
        sl = entry_price + sl_atr_multiplier * atr
        target = entry_price - target_atr_multiplier * atr
        return sl, target
    else:
        log_message(f"❌ SL Calculator error: Unexpected direction '{direction}'")
        raise ValueError("Invalid direction passed to SL calculator")

# =====================
# Simulate Trade Execution
# =====================
def simulate_trade(df_future, direction, entry, sl, target, max_bars=48):
    if df_future.empty or len(df_future) < 1:
        return entry, "No future data", 0, 0

    # Calculate MFE and MAE once
    if direction == "bullish":
        mfe = df_future['close'].max() - entry
        mae = entry - df_future['close'].min()
    else:
        mfe = entry - df_future['close'].min()
        mae = df_future['close'].max() - entry

    trailing_trigger = target_pct * 0.01
    trailing_sl = trailing_sl_pct * 0.01
    triggered = False
    trail_target = None

    for row in df_future.iloc[:max_bars].itertuples():
        price = row.close

        if direction == "bullish":
            if not triggered and price >= entry * (1 + trailing_trigger):
                triggered = True
                trail_target = price * (1 - trailing_sl)
            elif triggered:
                trail_target = max(trail_target, price * (1 - trailing_sl))

            if price <= sl:
                return price, "Stop Loss", mfe, mae
            if triggered and price <= trail_target:
                return price, "Trailing Exit", mfe, mae
            if price >= target:
                return price, "Target Hit", mfe, mae

        else:  # bearish
            if not triggered and price <= entry * (1 - trailing_trigger):
                triggered = True
                trail_target = price * (1 + trailing_sl)
            elif triggered:
                trail_target = min(trail_target, price * (1 + trailing_sl))

            if price >= sl:
                return price, "Stop Loss", mfe, mae
            if triggered and price >= trail_target:
                return price, "Trailing Exit", mfe, mae
            if price <= target:
                return price, "Target Hit", mfe, mae

    return df_future.iloc[:max_bars].iloc[-1].close, "Exit at end", mfe, mae

# =====================
# Main Strategy Loop with Reversal Support
# =====================
def run_strategy():
    print("🔁 OpenAlgo Python Bot is running.")
    log_message("Amar's WMA Dynamic Strategy started in LIVE mode.")
    
    while True:
        try:
            now = datetime.now()
            current_time = now.strftime("%H:%M")

            if current_time < start_time or current_time > end_time:
                log_message("Outside trading window. Sleeping...")
                time.sleep(60)
                continue

            for symbol in symbols:
                positions.setdefault(symbol, None)
                last_trade_time.setdefault(symbol, datetime.min)
                log_message(f"Processing {symbol}...")

                df = fetch_data(symbol)
                if df is None or len(df) < 30:
                    log_message(f"Skipping {symbol} due to invalid or insufficient data.")
                    continue

                # Cooldown logic
                if (datetime.now() - last_trade_time.get(symbol, datetime.min)).total_seconds() < cooldown_seconds:
                    log_message(f"⏳ Skipping {symbol} — cooldown in effect")
                    continue

                log_message(f"{symbol}: df shape = {df.shape}, using last 30 rows.")
                direction = detect_trend(df[-30:], symbol)
                if not direction:
                    log_message(f"No valid trend detected for {symbol}.")
                    continue

                log_message(f"📊 Trend detected for {symbol}: {direction.upper()}")

                # Already in same direction
                if positions.get(symbol) == direction:
                    log_message(f"Already in {direction.upper()} trade for {symbol}, skipping...")
                    continue

                # === Reversal validation ===
                if positions.get(symbol) and positions.get(symbol) != direction:
                    rsi = df.iloc[-1]['rsi']
                    macd = df.iloc[-1]['macd']
                    signal = df.iloc[-1]['macd_signal']
                    wma = df.iloc[-1]['wma']
                    prev_wma = df.iloc[-2]['wma']
                    wma_slope_up = wma > prev_wma
                    macd_delta = macd - signal

                    reverse_allowed = (
                        (direction == "bullish" and rsi > 50 and macd_delta > 0.15 and wma_slope_up) or
                        (direction == "bearish" and rsi < 50 and macd_delta < -0.15 and not wma_slope_up)
                    )

                    if reverse_allowed:
                        log_message(f"🔁 Strong reversal detected in {symbol} from {positions.get(symbol)} to {direction}")
                        exit_position(symbol, positions.get(symbol))
                        time.sleep(2)
                    else:
                        log_message(f"↩️ Reversal signal weak — Ignoring {positions.get(symbol)} to {direction} for {symbol}")
                        continue

                if pd.isnull(df.iloc[-1][['close', 'atr']]).any():
                    log_message(f"{symbol}: Skipping due to NaN in latest candle's 'close' or 'atr'")
                    continue

                entry_price = df.iloc[-1]['close']
                atr = df.iloc[-1]['atr']
                if direction not in ["bullish", "bearish"]:
                    log_message(f"{symbol}: Invalid direction from trend detection.")
                    continue

                try:
                    sl, target = calculate_sl_target(entry_price, atr, direction)
                    log_message(f"{direction.upper()} Signal -> {symbol} @ {entry_price:.2f} | SL: {sl:.2f}, Target: {target:.2f}")
                except Exception as calc_error:
                    log_message(f"{symbol}: SL calc failed — {calc_error}")
                    continue

                send_telegram(f"{direction.upper()} Signal -> {symbol} @ {entry_price:.2f} | SL: {sl:.2f}, Target: {target:.2f}")

                response = place_exit_order(symbol, "BUY" if direction == "bullish" else "SELL", entry_price, reason="Entry")
                if response and response.get("status") == "success":
                    last_trade_time[symbol] = datetime.now()
                    positions[symbol] = direction

                    thread = threading.Thread(
                        target=monitor_position,
                        args=(symbol, direction, entry_price, sl, target),
                        daemon=True
                    )
                    thread.start()
                else:
                    log_message(f"Skipping {symbol} due to order failure.")

            time.sleep(120)

        except Exception as e:
            log_message(f"Unexpected error: {str(e)}")
            send_telegram(f"Strategy Error: {str(e)}")
            time.sleep(30)

# =====================
# run_backtest() Function
# =====================
def run_backtest():
    log_message("Starting WMA Backtest...")
    log_message(f"Backtest window: {backtest_start_date} to {backtest_end_date}")

    file_path = f"logs/backtest_{backtest_end_date.strftime('%Y-%m-%d')}.csv"
    with open(file_path, "w") as f:
        f.write("entry_time,symbol,direction,entry,exit,result,reason,mfe,mae\n")

    for symbol in symbols:
        log_message(f"[BT] Processing {symbol}...")
        df = fetch_data(symbol)
        if df is None or len(df) < 50:
            continue

        position = None
        last_trade_index = -999  # for cooldown tracking

        for i in range(30, len(df) - 1):
            if i - last_trade_index < cooldown_candles:
                continue

            sub_df = df.iloc[:i+1]
            current = df.iloc[i]
            prev = df.iloc[i - 1]

            direction = detect_trend(sub_df)
            if not direction:
                continue

            # === Reversal Filtering (match run_strategy)
            rsi = current['rsi']
            macd = current['macd']
            signal = current['macd_signal']
            wma = current['wma']
            prev_wma = prev['wma']
            wma_slope_up = wma > prev_wma
            macd_delta = macd - signal

            reverse_allowed = False
            if direction == "bullish" and rsi > 50 and macd_delta > 0.15 and wma_slope_up:
                reverse_allowed = True
            elif direction == "bearish" and rsi < 50 and macd_delta < -0.15 and not wma_slope_up:
                reverse_allowed = True

            if position == direction:
                continue  # already in same direction

            if position and position != direction:
                if not reverse_allowed:
                    continue  # skip weak reversal
                else:
                    position = None  # exit for reversal

            entry_price = current['close']
            atr = current['atr']
            sl, target = calculate_sl_target(entry_price, atr, direction)

            df_future = df.iloc[i+1:]
            if df_future.empty:
                continue

            try:
                result, reason, mfe, mae = simulate_trade(df_future, direction, entry_price, sl, target)
                entry_time = df.index[i]
                log_backtest(symbol, entry_price, result, direction,
                             ((result - entry_price) / entry_price) * 100,
                             reason, entry_time, mfe, mae)
                position = direction
                last_trade_index = i

            except Exception as e:
                log_message(f"[BT] Error during simulation at index {i}: {e}")

    summarize_backtest()

def summarize_backtest():
    file_path = f"logs/backtest_{backtest_end_date.strftime('%Y-%m-%d')}.csv"
    try:
        with open(file_path) as f:
            reader = csv.reader(f)
            next(reader)  # skip header
            profits = [float(r[5]) for r in reader if len(r) > 5]
            total = len(profits)
            wins = sum(1 for p in profits if p > 0)
            losses = total - wins
            avg_return = statistics.mean(profits) if profits else 0
            win_rate = wins / total * 100 if total > 0 else 0
            log_message(f"Backtest Summary — Trades: {total}, Wins: {wins}, Losses: {losses}, Win Rate: {win_rate:.1f}%, Avg Return: {avg_return:.2f}%")
    except Exception as e:
        log_message(f"Summary failed: {e}")

# =====================
# Graceful Exit
# =====================
def graceful_exit(sig, frame):
    log_message("WMA_dynamic_Graceful shutdown requested.")
    summarize_live_trades()  # ✅ <-- Add this line
    send_telegram("WMA_dynamic_Strategy stopped gracefully.")
    sys.exit(0)

signal.signal(signal.SIGINT, graceful_exit)
signal.signal(signal.SIGTERM, graceful_exit)

def summarize_live_trades():
    try:
        with open("trade_log.csv") as f:
            reader = csv.reader(f)
            profits = []
            for row in reader:
                if len(row) >= 5:
                    try:
                        profits.append(float(row[4]))
                    except ValueError:
                        continue
            total = len(profits)
            wins = sum(1 for p in profits if p > 0)
            losses = total - wins
            avg_return = statistics.mean(profits) if profits else 0
            win_rate = wins / total * 100 if total > 0 else 0
            log_message(f"Live Summary — Trades: {total}, Wins: {wins}, Losses: {losses}, Win Rate: {win_rate:.1f}%, Avg Return: {avg_return:.2f}%")
            send_telegram(f"📈 Live Summary — Trades: {total}, Wins: {wins}, Losses: {losses}, Win Rate: {win_rate:.1f}%, Avg: {avg_return:.2f}%")
    except Exception as e:
        log_message(f"Live summary failed: {e}")

if __name__ == "__main__":
    try:
        if mode.lower() == "live":
            run_strategy()
        elif mode.lower() in ["analyze", "backtest"]:
            run_backtest()
        else:
            log_message(f"Invalid mode: {mode}")
    except Exception as e:
        log_message(f"Fatal Error: {e}")
        send_telegram(f"🔥 Fatal Error: {e}")
        time.sleep(60)
