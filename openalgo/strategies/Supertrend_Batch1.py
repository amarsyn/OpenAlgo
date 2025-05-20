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

# Configuration Section
api_key = '78b9f1597a7f903d3bfc76ad91274a7cc7536c2efc4508a8276d85fbc840d7d2'
strategy = "Supertrend Python Batch-1"
symbols = ["SUNPHARMA", "HINDALCO", "ICICIBANK", "RELIANCE", "AXISBANK", "DRREDDY", "HINDUNILVR", "HDFCLIFE"]
exchange = "NSE"
product = "MIS"
quantity = 5
atr_period = 10
atr_multiplier = 1.2
mode = "live"  # Set to "analyze" for Analyzer Mode - Toggle between live and analyze mode

# Entry Time Filter (24-hr format)
start_time = "09:20"
end_time = "14:30"

# Stop Loss and Target (in %)
stop_loss_pct = 0.5       # Temporarily reduced SL for testing - 1% SL
target_pct = 1            # Temporarily reduced SL for testing - 1.5% profit target
trailing_sl_pct = 0.4     # Temporarily lowered for testing     # Start trailing SL when in profit; trail at 0.5%
trailing_trigger_pct = 0.25  # Activate trailing after 0.25% profit

# Telegram Alert Setup
TELEGRAM_ENABLED = True
BOT_TOKEN = "7891610241:AAHcNW6faW2lZGrxeSaOZJ3lSggI-ehl-pg"
CHAT_ID = "627470225"

# Logging
LOG_FILE = f"logs/ST_B1_{datetime.now().strftime('%Y-%m-%d')}.txt"
TRADE_LOG = f"logs/ST_B1_{datetime.now().strftime('%Y-%m-%d')}.csv"

# NEW: Max favorable price tracking
max_favorable_price = {sym: None for sym in symbols}
partial_booked = {sym: False for sym in symbols}  

def send_telegram(message):
    if TELEGRAM_ENABLED:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": message}
        response = requests.post(url, data=payload)
        print(f"📩 Telegram response: {response.text}")

def log_message(message):
    print(f"📝 Logging: {message}")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now()} - {message}\n")

client = api(api_key=api_key, host='http://127.0.0.1:5000')

def Supertrend(df, atr_period, multiplier):
    high = df['high']
    low = df['low']
    close = df['close']

    price_diffs = [high - low, high - close.shift(), close.shift() - low]
    true_range = pd.concat(price_diffs, axis=1).abs().max(axis=1)
    atr = true_range.ewm(alpha=1/atr_period, min_periods=atr_period).mean()

    hl2 = (high + low) / 2
    final_upperband = hl2 + (multiplier * atr)
    final_lowerband = hl2 - (multiplier * atr)
    supertrend = [True] * len(df)

    for i in range(1, len(df)):
        curr, prev = i, i - 1

        if close.iloc[curr] > final_upperband.iloc[prev]:
            supertrend[curr] = True
        elif close.iloc[curr] < final_lowerband.iloc[prev]:
            supertrend[curr] = False
        else:
            supertrend[curr] = supertrend[prev]
            if supertrend[curr] and final_lowerband.iloc[curr] < final_lowerband.iloc[prev]:
                final_lowerband.iloc[curr] = final_lowerband.iloc[prev]
            if not supertrend[curr] and final_upperband.iloc[curr] > final_upperband.iloc[prev]:
                final_upperband.iloc[curr] = final_upperband.iloc[prev]

        if supertrend[curr]:
            final_upperband.iloc[curr] = np.nan
        else:
            final_lowerband.iloc[curr] = np.nan

    return pd.DataFrame({
        'Supertrend': supertrend,
        'Final_Lowerband': final_lowerband,
        'Final_Upperband': final_upperband
    }, index=df.index)

# Optional RSI and MACD Calculation (disabled by default)
def add_rsi_macd(df):
    # Calculate RSI (14-period)
    delta = df['close'].diff()
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = pd.Series(gain).rolling(window=14).mean()
    avg_loss = pd.Series(loss).rolling(window=14).mean()
    rs = avg_gain / avg_loss
    df['RSI'] = 100 - (100 / (1 + rs))

    # Calculate MACD
    ema_12 = df['close'].ewm(span=12, adjust=False).mean()
    ema_26 = df['close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = ema_12 - ema_26
    df['Signal_Line'] = df['MACD'].ewm(span=9, adjust=False).mean()
    return df

def calculate_vwap(df):
    df['vwap'] = (df['close'] * df['volume']).cumsum() / df['volume'].cumsum()
    return df

def supertrend_strategy():
    positions = {sym: 0 for sym in symbols}
    entry_prices = {sym: None for sym in symbols}
    trailing_sls = {sym: None for sym in symbols}
    trade_count = {sym: 0 for sym in symbols}
    MAX_TRADES_PER_DAY = 2  # or any number you choose
    partial_booked = {sym: False for sym in symbols}
    
    log_message("🧪 Manual test log message after startup")

    while True:
        # 🕘 Reset daily trade count at 09:15
        if datetime.now().strftime("%H:%M") == "09:15":
            for sym in trade_count:
                trade_count[sym] = 0
            log_message("🔁 Trade count reset for all symbols at 09:15")
        now = datetime.now().time()
        if not (datetime.strptime(start_time, "%H:%M").time() <= now <= datetime.strptime(end_time, "%H:%M").time()):
            log_message("Outside trading hours. Waiting...")
            time.sleep(60)
            continue

        for symbol in symbols:
            try:
                end_date = datetime.now().strftime("%Y-%m-%d")
                start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

                df = client.history(
                    symbol=symbol,
                    exchange=exchange,
                    interval="5m",
                    start_date=start_date,
                    end_date=end_date
                )

                if not isinstance(df, pd.DataFrame) or df.empty or not {'close', 'high', 'low', 'open'}.issubset(df.columns):
                    log_message(f"Skipping {symbol} due to data issue: {df}")
                    continue

                df['close'] = df['close'].round(2)
                df = calculate_vwap(df)
                df = add_rsi_macd(df)
                supertrend = Supertrend(df, atr_period, atr_multiplier)
                
                # Long Entry logic
                is_uptrend = supertrend['Supertrend']
                longentry = (
                    is_uptrend.iloc[-2] and not is_uptrend.iloc[-3] and 
                    df['close'].iloc[-1] > df['vwap'].iloc[-1] 
                )

                # Short Entry logic                
                shortentry = (
                    not is_uptrend.iloc[-2] and is_uptrend.iloc[-3] and 
                    df['close'].iloc[-1] < df['vwap'].iloc[-1]
                )
                
                close_price = df['close'].iloc[-1]

                position = positions[symbol]
                entry_price = entry_prices[symbol]
                trailing_sl = trailing_sls[symbol]

                # ✅ Always define profit_pct safely
                profit_pct = 0
                if entry_price and position != 0:
                    if position > 0:
                        profit_pct = (close_price - entry_price) / entry_price * 100
                    elif position < 0:
                        profit_pct = (entry_price - close_price) / entry_price * 100

                # Exit logic
                # Update max favorable price                    
                if position != 0:
                    if position > 0:
                        if max_favorable_price[symbol] is None or close_price > max_favorable_price[symbol]:
                            max_favorable_price[symbol] = close_price
                            profit_pct = (close_price - entry_price) / entry_price * 100
                    elif position < 0:
                        if max_favorable_price[symbol] is None or close_price < max_favorable_price[symbol]:
                            max_favorable_price[symbol] = close_price

                # Long Execution Logic
                if longentry and position <= 0 and trade_count[symbol] < MAX_TRADES_PER_DAY:
                    positions[symbol] = quantity
                    entry_prices[symbol] = close_price
                    trailing_sls[symbol] = None
                    max_favorable_price[symbol] = close_price
                    df['ATR'] = ta.atr(df['high'], df['low'], df['close'], length=10)
                    atr_stop = close_price - (atr_multiplier * df['ATR'].iloc[-1])
                    min_sl = close_price * (1 - 0.2 / 100)
                    trailing_sls[symbol] = max(atr_stop, min_sl)
                    trailing_sls[symbol] = max(trailing_sls[symbol], min_sl)
                    if mode == "live":
                        response = client.placesmartorder(
                            strategy=strategy,
                            symbol=symbol,
                            action="BUY",
                            exchange=exchange,
                            price_type="MARKET",
                            product=product,
                            quantity=quantity,
                            position_size=quantity
                        )
                        print(f"Buy Order Response for {symbol}:", response)
                    send_telegram(f"🚀 Buy executed for {symbol} at {close_price}")
                    log_message(f"Buy executed for {symbol} at {close_price}")
                    trade_count[symbol] += 1

                # Short Execution Logic
                elif shortentry and position >= 0 and trade_count[symbol] < MAX_TRADES_PER_DAY:
                    positions[symbol] = -quantity
                    entry_prices[symbol] = close_price
                    trailing_sls[symbol] = None
                    max_favorable_price[symbol] = close_price
                    if mode == "live":
                        response = client.placesmartorder(
                            strategy=strategy,
                            symbol=symbol,
                            action="SELL",
                            exchange=exchange,
                            price_type="MARKET",
                            product=product,
                            quantity=quantity,
                            position_size=-quantity
                        )
                        print(f"Sell Order Response for {symbol}:", response)
                    send_telegram(f"🔻 Sell executed for {symbol} at {close_price}")
                    log_message(f"Sell executed for {symbol} at {close_price}")
                    trade_count[symbol] += 1

                # Short Exit Logic
                elif position != 0 and entry_price:
                    max_price = max_favorable_price[symbol]
                    current_change = ((close_price - entry_price) / entry_price * 100) * (1 if position > 0 else -1)

                    # Fixed Stop Loss and Target Logic
                    if current_change <= -stop_loss_pct or current_change >= target_pct:
                        reason = "Target Hit" if current_change >= target_pct else "Stop Loss Hit"
                        send_telegram(f"❌ Exit {reason}: {symbol} at {close_price} | PnL: {current_change:.2f}%")
                        log_message(f"Exit {reason}: {symbol} at {close_price} | PnL: {current_change:.2f}%")
                        if mode == "live":
                            action = "SELL" if position > 0 else "BUY"
                            response = client.placesmartorder(
                                strategy=strategy,
                                symbol=symbol,
                                action=action,
                                exchange=exchange,
                                price_type="MARKET",
                                product=product,
                                quantity=abs(position),
                                position_size=0
                            )
                            print(f"{action} Order Response ({reason}) for {symbol}:", response)
                        positions[symbol] = 0
                        entry_prices[symbol] = None
                        trailing_sls[symbol] = None
                        max_favorable_price[symbol] = None
                        continue

                    if max_price:

                        peak_change = ((max_price - entry_price) / entry_price * 100) * (1 if position > 0 else -1)
                        drop_from_peak = peak_change - current_change

                        log_message(f"🔍 {symbol} - Entry={entry_price}, Peak={max_price}, Current={close_price}, PeakChange={peak_change:.2f}%, DropFromPeak={drop_from_peak:.2f}%")

                        if drop_from_peak >= trailing_sl_pct:
                            send_telegram(f"🔁 Dynamic Target Exit: {symbol} at {close_price} | Dropped {drop_from_peak:.2f}% from peak")
                            log_message(f"Dynamic Target Exit: {symbol} at {close_price} | Dropped {drop_from_peak:.2f}% from peak")
                            if mode == "live":
                                action = "SELL" if position > 0 else "BUY"
                                response = client.placesmartorder(
                                    strategy=strategy,
                                    symbol=symbol,
                                    action=action,
                                    exchange=exchange,
                                    price_type="MARKET",
                                    product=product,
                                    quantity=abs(position),
                                    position_size=0
                                )
                                print(f"{action} Order Response (Dynamic Target) for {symbol}:", response)
                            positions[symbol] = 0
                            entry_prices[symbol] = None
                            trailing_sls[symbol] = None
                            max_favorable_price[symbol] = None
                            continue

                    # Adjust trail_pct based on profit
                    if profit_pct > 1.5:
                        trail_pct = 0.2
                    elif profit_pct > 1.0:
                        trail_pct = 0.25
                    elif profit_pct > 0.5:
                        trail_pct = 0.3
                    else:
                        trail_pct = trailing_sl_pct

                    # Partial profit booking
                    if not partial_booked[symbol] and profit_pct >= 0.5:
                        partial_booked[symbol] = True
                        log_message(f"💰 Partial Profit Booked for {symbol} at {close_price}")
                        send_telegram(f"💰 Partial Profit Booked for {symbol} at {close_price}")
                        if mode == "live":
                            # Book half position
                            response = client.placesmartorder(
                                strategy=strategy,
                                symbol=symbol,
                                action="SELL" if position > 0 else "BUY",
                                exchange=exchange,
                                price_type="MARKET",
                                product=product,
                                quantity=quantity // 2,
                                position_size=position // 2
                            )

                log_message(f"Supertrend Cycle: LTP={df['close'].iloc[-1]}, Trend={is_uptrend.iloc[-2]}")
                log_message(f"Cycle: {symbol} | LTP={close_price}, Pos={position}, Buy={longentry}, Sell={shortentry}")

                print("\nStrategy Status:")
                print("-" * 50)
                print(f"Position: {position}")
                print(f"LTP: {df['close'].iloc[-1]}")
                print(f"Supertrend: {supertrend['Supertrend'].iloc[-2]}")
                print(f"LowerBand: {supertrend['Final_Lowerband'].iloc[-2]:.2f}")
                print(f"UpperBand: {supertrend['Final_Upperband'].iloc[-2]:.2f}")
                print(f"VWAP: {df['vwap'].iloc[-1]:.2f}")
                print(f"Buy Signal: {longentry}")
                print(f"Sell Signal: {shortentry}")
                print("-" * 50)

            except Exception as e:
                print(f"Error in strategy: {str(e)}")
                log_message(f"Error for {symbol}: {str(e)}")
                continue
        # Exit on Supertrend reversal
        if (position > 0 and not is_uptrend.iloc[-1]) or (position < 0 and is_uptrend.iloc[-1]):
            reason = "Supertrend Reversal"
            send_telegram(f"🔁 Exit {reason}: {symbol} at {close_price}")
            log_message(f"Exit {reason}: {symbol} at {close_price}")
            # Close position logic
            positions[symbol] = 0
            entry_prices[symbol] = None
            trailing_sls[symbol] = None
            max_favorable_price[symbol] = None

            # Log to CSV
            reason = "Supertrend Reversal"  # or "Target Hit", or "Supertrend Reversal"
            with open("trade_log.csv", "a") as log_file:
                log_file.write(f"{datetime.now()},{symbol},{entry_price},{close_price},{(close_price - entry_price)/entry_price * 100:.2f},{profit_pct:.2f},{reason},Trailing SL\n")

            continue

        time.sleep(15)

if __name__ == "__main__":
    print("Starting Amar's Supertrend Batch-1 Multi-Stock Strategy...")
    send_telegram(f"✅ Amar's Supertrend Batch-1 Multi-Stock strategy started in {mode.upper()} mode.")
    log_message(f"Amar's ST B-1 Strategy started in {mode.upper()} mode.")
    def graceful_exit(signum, frame):
        print("Graceful shutdown requested... Exiting strategy.")
        send_telegram("🛑 Supertrend Batch-1 Strategy stopped gracefully.")
        log_message("Graceful shutdown invoked.")
        sys.exit(0)

    signal.signal(signal.SIGINT, graceful_exit)   # Ctrl+C
    signal.signal(signal.SIGTERM, graceful_exit)  # kill <pid>
    supertrend_strategy()
