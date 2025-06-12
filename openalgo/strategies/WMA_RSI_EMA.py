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
last_trade_time = defaultdict(lambda: datetime.min)
cooldown_seconds = 180  # 3 minutes cooldown
max_bars = 48  # limit future bars to ~4 hours of 5-min candles
positions = defaultdict(lambda: None)

# ==============================
# Setup and Configuration
# ==============================
os.makedirs("logs", exist_ok=True)

api_key = '78b9f1597a7f903d3bfc76ad91274a7cc7536c2efc4508a8276d85fbc840d7d2'
strategy_name = "WMA_RSI_EMA_Strategy"
symbols = ["TRENT"]
exchange = "NSE"
product = "MIS"
quantity = 10
mode = "backtest"
start_time = "09:15"
end_time = "18:10"
sl_pct = 2.0  
target_pct = 5.0
sl_atr_multiplier = 2.0  
target_atr_multiplier = 5.0  
trailing_sl_pct = 1.0
trailing_trigger_pct = 0.8
executed_trades = []

# Set backtest range and fixed date for historical evaluation
backtest_start_date = datetime(2025, 3, 1)
backtest_end_date = datetime(2025, 6, 2)

LOG_FILE = f"logs/WMA_RSI_EMA_{datetime.now().strftime('%Y-%m-%d')}.txt"
TRADE_LOG = f"logs/WMA_RSI_EMA_{datetime.now().strftime('%Y-%m-%d')}.csv"

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
    print(f"[{timestamp}] WMA_RSI_EMA {msg}")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] WMA_RSI_EMA {msg}\n")

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

# =====================
# Data Fetching with EMA
# =====================
def fetch_data(symbol):
    if mode.lower() in ["backtest", "analyze"]:
        start_date = backtest_start_date
        end_date = backtest_end_date
    else:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=1)

    result = client.history(
        symbol=symbol,
        exchange=exchange,
        interval="5m",
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_date.strftime("%Y-%m-%d")
    )

    if isinstance(result, dict) and "data" in result:
        df = pd.DataFrame(result["data"])
    elif isinstance(result, pd.DataFrame):
        df = result
    else:
        log_message(f"No valid data returned for {symbol}")
        return None

    if df.empty:
        log_message(f"Empty DataFrame for {symbol}")
        return None

    df.index = pd.to_datetime(df.index)
    df['wma'] = ta.wma(df['close'], length=20)
    df['ema'] = ta.ema(df['close'], length=20)
    df['rsi'] = ta.rsi(df['close'], length=14)
    df['vol_ma'] = df['volume'].rolling(20).mean()
    df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
    df['ema13'] = ta.ema(df['close'], length=13)
    df['ema62'] = ta.ema(df['close'], length=62)
    # requests.get(url, params=params, timeout=10)  # 10 seconds max wait
    return df

# =====================
# EMA Crossover + Pullback Detection
# =====================
def detect_ema_crossover_pullback(df, symbol=None, index_label=None):
    if len(df) < 65:
        return None

    last = df.iloc[-1]
    prev = df.iloc[-2]
    ema13 = df['ema13']
    ema62 = df['ema62']

    if symbol and index_label:
        log_message(f"[BT] {symbol} @ {index_label} checking for entry...")
        log_message(f"[BT Debug] 13EMA[-3]: {ema13.iloc[-3]:.2f}, 62EMA[-3]: {ema62.iloc[-3]:.2f}")
        log_message(f"[BT Debug] 13EMA[-2]: {ema13.iloc[-2]:.2f}, 62EMA[-2]: {ema62.iloc[-2]:.2f}")

    # ✅ Relaxed bullish condition: 13EMA > 62EMA (no need for crossover)
    if ema13.iloc[-2] > ema62.iloc[-2]:
        close_near_ema62 = abs(last['close'] - last['ema62']) <= 0.5 * last['atr']  # relaxed from 0.2
        bullish_engulf = last['close'] > prev['open'] and last['open'] < prev['close']

        if close_near_ema62:
            reason = "EMA Bullish"
            if bullish_engulf:
                reason += " + Engulfing"
            log_message(f"✅ Valid Bullish Pullback Setup ({reason})")
            return "bullish"

    # ✅ Relaxed bearish condition: 13EMA < 62EMA
    if ema13.iloc[-2] < ema62.iloc[-2]:
        close_near_ema62_bear = abs(last['close'] - last['ema62']) <= 0.5 * last['atr']
        bearish_engulf = last['close'] < prev['open'] and last['open'] > prev['close']

        if close_near_ema62_bear:
            reason = "EMA Bearish"
            if bearish_engulf:
                reason += " + Engulfing"
            log_message(f"✅ Valid Bearish Pullback Setup ({reason})")
            return "bearish"

    log_message("Rejected — No Valid Setup")
    return None

# =====================
# Trend Detection (MACD removed)
# =====================
def detect_trend(df, rsi_bull=51, rsi_bear=49, min_atr_pct=0.001):
    last = df.iloc[-1]
    prev = df.iloc[-2]

    log_message(
        f"Checking trend — Close: {last['close']}, WMA: {last['wma']}, EMA: {last['ema']}, Prev_WMA: {prev['wma']}, "
        f"RSI: {last['rsi']}, Vol: {last['volume']}, Vol_MA: {last['vol_ma']}, ATR: {last['atr']}"
    )

    min_atr = df['close'].mean() * min_atr_pct
    if last['atr'] < min_atr:
        log_message(f"Filtered out due to low ATR: {last['atr']} < {min_atr}")
        return None

    if last['volume'] < last['vol_ma'] * 0.75:
        log_message(f"Filtered due to weak current volume: {last['volume']} < 75% of MA {last['vol_ma']}")
        return None
    if last['volume'] < last['vol_ma']:
        log_message("⚠️ Very low average volume, skipping for safety.")
        return None

    wma_slope = last['wma'] - prev['wma']

    if last['rsi'] >= rsi_bull and wma_slope > 0:
        log_message("Valid Bullish Trend Detected.")
        return "bullish"
    elif last['rsi'] <= rsi_bear and wma_slope < 0:
        log_message("Valid Bearish Trend Detected.")
        return "bearish"

    log_message("No valid trend detected — Conditions not met:")
    log_message(f"  RSI: {last['rsi']} (Need ≥{rsi_bull} or ≤{rsi_bear})")
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
            df = fetch_data(symbol)
            if df is None or len(df) < 2:
                continue

            current_price = df['close'].iloc[-1]

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
                elif not trail_triggered and current_price >= entry_price * (1 + trailing_trigger_pct * 0.01):
                    trail_triggered = True
                    trail_target = current_price * (1 - trailing_sl_pct * 0.01)
                    log_message(f"{symbol}: Trailing target activated at {trail_target:.2f}")
                elif trail_triggered and current_price <= trail_target:
                    exit_price = current_price
                    reason = "Trailing Target Hit"
                    exit_position(symbol, direction)
                    in_position = False
                elif trail_triggered:
                    trail_target = max(trail_target, current_price * (1 - trailing_sl_pct * 0.01))

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
                elif not trail_triggered and current_price <= entry_price * (1 - trailing_trigger_pct * 0.01):
                    trail_triggered = True
                    trail_target = current_price * (1 + trailing_sl_pct * 0.01)
                    log_message(f"{symbol}: Trailing target activated at {trail_target:.2f}")
                elif trail_triggered and current_price >= trail_target:
                    exit_price = current_price
                    reason = "Trailing Target Hit"
                    exit_position(symbol, direction)
                    in_position = False
                elif trail_triggered:
                    trail_target = min(trail_target, current_price * (1 + trailing_sl_pct * 0.01))

            # ✅ Force exit before market close
            if datetime.now().strftime("%H:%M") > end_time:
                exit_price = current_price
                reason = "Auto Exit — Market Close"
                exit_position(symbol, direction)
                in_position = False

    except Exception as e:
        log_message(f"[ERROR] monitor_position failed for {symbol}: {e}")
        reason = f"Monitor Error: {e}"
        exit_price = df['close'].iloc[-1] if isinstance(df, pd.DataFrame) and not df.empty else entry_price
        positions[symbol] = None

    # Always log exit
    log_trade(symbol, entry_price, exit_price, direction, reason)
    send_telegram(f"{symbol} → {reason} at {exit_price:.2f}")

    # ✅ Append to executed trades for live summary
    executed_trades.append({
        "symbol": symbol,
        "entry": entry_price,
        "exit": exit_price,
        "direction": direction,
        "reason": reason,
        "return_pct": ((exit_price - entry_price) / entry_price * 100) if direction == "bullish"
                      else ((entry_price - exit_price) / entry_price * 100)
    })

    positions[symbol] = None

# =====================
# Order Placement
# =====================
def place_entry_order(symbol, direction, entry_price):
    action = "BUY" if direction == "bullish" else "SELL"
    try:
        response = client.placeorder(
            strategy=strategy_name,
            symbol=symbol,
            action=action,
            exchange=exchange,
            price_type="MARKET",
            product=product,
            quantity=quantity,
            price=0,
            trigger_price="0"
        )
        log_message(f"📥 ENTRY Order | {symbol} | {action} @ MARKET | Response: {response}")
        send_telegram(f"📥 ENTRY for {symbol} @ MARKET | ID: {response['orderid']}")
        return response["orderid"], entry_price  # assuming entry at LTP
    except Exception as e:
        log_message(f"[ERROR] Failed to place entry order for {symbol}: {e}")
        send_telegram(f"❌ Entry Failed for {symbol}: {e}")
        return None, None

def place_exit_order(symbol, action, price, trigger_price=None, reason="SL"):
    """
    Unified function to place exit orders (SL, Target, Trail).
    All orders are placed as MARKET to avoid hanging orders.
    """
    try:
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
    if direction == "bullish":
        sl = entry_price - sl_atr_multiplier * atr
        target = entry_price + target_atr_multiplier * atr
    else:
        sl = entry_price + sl_atr_multiplier * atr
        target = entry_price - target_atr_multiplier * atr
    return sl, target


# =====================
# Simulate Trade Execution
# =====================
def simulate_trade(df, direction, entry_price, sl, target):
    mfe = mae = 0
    exit_price = None
    reason = None

    for i, row in df.iterrows():
        high = row['high']
        low = row['low']
        close = row['close']

        # Calculate MFE and MAE continuously
        if direction == "bullish":
            mfe = max(mfe, high - entry_price)
            mae = max(mae, entry_price - low)

            if low <= sl:
                exit_price = sl
                reason = "Stop Loss"
                break
            elif high >= target:
                exit_price = target
                reason = "Target Hit"
                break

        elif direction == "bearish":
            mfe = max(mfe, entry_price - low)
            mae = max(mae, high - entry_price)

            if high >= sl:
                exit_price = sl
                reason = "Stop Loss"
                break
            elif low <= target:
                exit_price = target
                reason = "Target Hit"
                break

    # If no exit triggered, use last close
    if exit_price is None:
        exit_price = df.iloc[-1]['close']
        reason = "Timed Exit"

    # Validate exit price
    if exit_price <= 0 or not isinstance(exit_price, (int, float)):
        raise ValueError(f"Invalid exit price: {exit_price} for {direction} trade.")

    return exit_price, reason, mfe, mae

# =====================
# Main Strategy Loop with Reversal Support
# =====================
def run_strategy():
    log_message("Amar's WMA Dynamic Strategy started in LIVE mode.")
    try:
        while True:
            try:
                now = datetime.now()
                current_time = now.strftime("%H:%M")

                if current_time < start_time or current_time > end_time:
                    log_message("Outside trading window. Sleeping...")
                    time.sleep(60)
                    continue

                for symbol in symbols:
                    log_message(f"Processing {symbol}...")

                    if (datetime.now() - last_trade_time[symbol]).total_seconds() < cooldown_seconds:
                        log_message(f"⏳ Skipping {symbol} — cooldown in effect")
                        continue

                    df = fetch_data(symbol)
                    if df is None or len(df) < 30:
                        continue

                    trend = detect_trend(df)
                    if not trend:
                        continue

                    direction = detect_ema_crossover_pullback(df[-30:], symbol=symbol, index_label=df.index[-1])
                    if direction != trend:
                        log_message(f"⚠️ Skipping {symbol} — Trend: {trend}, Signal: {direction}")
                        continue

                    if positions.get(symbol):
                        log_message(f"🔒 Already in position for {symbol}, skipping...")
                        continue

                    log_message(f"📊 Trend detected for {symbol}: {direction.upper()}")

                    if positions[symbol] == direction:
                        log_message(f"Already in {direction.upper()} trade for {symbol}, skipping...")
                        continue

                    if positions[symbol] and positions[symbol] != direction:
                        log_message(f"🔁 Reversing position in {symbol} from {positions[symbol]} to {direction}")
                        exit_position(symbol, positions[symbol])
                        time.sleep(2)

                    entry_price = df.iloc[-1]['close']
                    atr = df.iloc[-1]['atr']

                    sl, target = calculate_sl_target(entry_price, atr, direction)

                    log_message(f"{direction.upper()} Signal -> {symbol} @ {entry_price:.2f} | SL: {sl:.2f}, Target: {target:.2f}")
                    send_telegram(f"{direction.upper()} Signal -> {symbol} @ {entry_price:.2f} | SL: {sl:.2f}, Target: {target:.2f}")

                    order_id, ltp = place_entry_order(symbol, direction, entry_price)
                    if order_id:
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

    finally:
        summarize_live_trades(executed_trades)

def summarize_live_trades(executed_trades):
    if not executed_trades:
        log_message("No live trades to summarize.")
        return

    df = pd.DataFrame(executed_trades)
    df['return_pct'] = df.apply(
        lambda row: ((row['exit'] - row['entry']) / row['entry'] * 100)
        if row['direction'] == 'bullish' else ((row['entry'] - row['exit']) / row['entry'] * 100), axis=1
    )
    df['outcome'] = df['return_pct'].apply(lambda x: '✅ Win' if x > 0 else '❌ Loss')

    total = len(df)
    wins = len(df[df['return_pct'] > 0])
    losses = len(df[df['return_pct'] <= 0])
    avg_return = df['return_pct'].mean()
    win_rate = (wins / total) * 100 if total > 0 else 0

    # log_message("\nTrade Execution Details:")
    # log_message("Symbol\tEntry Time\tDirection\tEntry\tExit\tReturn %\tReason\tOutcome")
    # for _, row in df.iterrows():
    #     log_message(f"{row['symbol']}\t{row['entry_time']}\t{row['direction']}\t{row['entry']:.2f}\t{row['exit']:.2f}\t{row['return_pct']:+.2f}%\t{row['reason']}\t{row['outcome']}")
    log_message(f"Live Strategy Summary — Trades: {total}, Wins: {wins}, Losses: {losses}, Win Rate: {win_rate:.1f}%, Avg Return: {avg_return:.2f}%")

# =====================
# run_backtest() Function
# =====================
def run_backtest():
    log_message("Starting WMA_RSI_EMA_ Backtest...")
    log_message(f"Backtest window: {backtest_start_date} to {backtest_end_date}")

    with open(f"logs/backtest_{backtest_end_date.strftime('%Y-%m-%d')}.csv", "w") as f:
        f.write("entry_time,symbol,direction,entry,exit,return_pct,reason,mfe,mae\n")

    try:
        for symbol in symbols:
            log_message(f"[BT] Processing {symbol}...")
            try:
                df = fetch_data(symbol)
                if df is None or len(df) < 80:
                    continue

                for i in range(30, len(df) - 1):
                    sub_df = df.iloc[:i+1]
                    direction = detect_ema_crossover_pullback(sub_df, symbol=symbol, index_label=sub_df.index[-1])

                    if not direction:
                        continue

                    entry_price = sub_df.iloc[-1]['close']
                    atr = sub_df.iloc[-1]['atr']

                    if direction == "bullish":
                        sl = entry_price - 1.25 * atr
                        target = entry_price + 2.5 * atr
                    else:
                        sl = entry_price + 1.25 * atr
                        target = entry_price - 2.5 * atr

                    df_future = df.iloc[i+1:]
                    if df_future.empty:
                        continue

                    try:
                        exit_price, reason, mfe, mae = simulate_trade(df_future, direction, entry_price, sl, target)

                        # Detect anomalies
                        if exit_price <= 1.0:
                            log_message(f"[WARN] Abnormal exit price for {symbol} @ {sub_df.index[-1]}: {exit_price:.2f}")

                        return_pct = ((exit_price - entry_price) / entry_price * 100) if direction == "bullish" else ((entry_price - exit_price) / entry_price * 100)

                        entry_time = sub_df.index[-1]
                        log_backtest(
                            symbol,
                            entry_price,
                            exit_price,
                            direction,
                            return_pct,
                            reason,
                            entry_time,
                            mfe,
                            mae
                        )
                    except Exception as e:
                        log_message(f"[BT] Error during simulation at index {i} for {symbol}: {e}")

            except Exception as e:
                log_message(f"[BT] Fatal Error fetching {symbol}: {e}")
                continue

    finally:
        log_message("")  # For readability
        summarize_backtest()

def summarize_backtest():
    try:
        log_file = f"logs/backtest_{backtest_end_date.strftime('%Y-%m-%d')}.csv"
        if not os.path.exists(log_file):
            log_message("No backtest log file found.")
            return

        df = pd.read_csv(log_file, parse_dates=['entry_time'])
        # No need to recalculate return_pct — it's now directly logged
        df['outcome'] = df['return_pct'].apply(lambda x: '✅ Win' if x > 0 else '❌ Loss')
        df['outcome'] = df['return_pct'].apply(lambda x: '✅ Win' if x > 0 else '❌ Loss')

        total = len(df)
        wins = len(df[df['return_pct'] > 0])
        losses = len(df[df['return_pct'] <= 0])
        avg_return = df['return_pct'].mean()
        win_rate = (wins / total) * 100 if total > 0 else 0

        # 🧾 Detailed Trade Execution Table
        # log_message("\nTrade Execution Details:")
        # log_message("Symbol\tEntry Time\tDirection\tEntry\tExit\tReturn %\tReason\tMFE\tMAE\tOutcome")
        # for _, row in df.iterrows():
        #     log_message(f"{row['symbol']}\t{row['entry_time']}\t{row['direction']}\t{row['entry']:.2f}\t{row['result']:.2f}\t{row['return_pct']:+.2f}%\t{row['reason']}\t{row['mfe']:.2f}\t{row['mae']:.2f}\t{row['outcome']}")

        log_message(f"Backtest Summary — Trades: {total}, Wins: {wins}, Losses: {losses}, Win Rate: {win_rate:.1f}%, Avg Return: {avg_return:.2f}%")
    
    except Exception as e:
        log_message(f"Error as in: {e}")

# =====================
# Graceful Exit
# =====================
def graceful_exit(sig, frame):
    log_message("WMA_RSI_EMA_Graceful shutdown requested.")
    summarize_live_trades()  # ✅ <-- Add this line
    send_telegram("WMA_RSI_EMA_Strategy stopped gracefully.")
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
