import pandas as pd
import numpy as np
from openalgo import api
import requests
import os
import sys
import signal
from datetime import datetime

# ================================
# 📁 Setup and Configuration
# ================================
# Ensure logs directory exists
os.makedirs("logs", exist_ok=True)

# 🔧 Test if logging works (check file permission/path issues)
with open("test_log.txt", "a") as f:
    f.write("Log test\n")

# Config
api_key = '78b9f1597a7f903d3bfc76ad91274a7cc7536c2efc4508a8276d85fbc840d7d2'
symbol = "MAXHEALTH"
exchange = "NSE"
interval = "5m"
start_date = "2025-01-01"
end_date = "2025-05-20"
quantity = 5
rsi_period = 7
bollinger_period = 14
bollinger_std = 2

# Telegram Alert Setup
TELEGRAM_ENABLED = True
BOT_TOKEN = "7891610241:AAHcNW6faW2lZGrxeSaOZJ3lSggI-ehl-pg"
CHAT_ID = "627470225"
LOG_FILE = f"logs/MR_{datetime.now().strftime('%Y-%m-%d')}.txt"
TRADE_LOG = f"logs/MR_{datetime.now().strftime('%Y-%m-%d')}.csv"

# ================================
# 🔌 Initialize API
# ================================
try:
    client = api(api_key=api_key, host="http://127.0.0.1:5000")
except Exception as e:
    print(f"❌ API initialization failed: {e}")
    exit(1)

# ================================
# 📢 Alerting and Logging
# ================================
def send_telegram(message):
    if TELEGRAM_ENABLED:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            payload = {"chat_id": CHAT_ID, "text": message}
            response = requests.post(url, data=payload)
            print(f"📩 Telegram response: {response.text}")
        except Exception as e:
            print(f"❌ Telegram alert failed: {e}")

def log_message(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    full_message = f"MR [{timestamp}] {message}"
    print(full_message)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(full_message + "\n")
    except Exception as e:
        print(f"❌ Failed to write to log file: {e}")

# ================================
# 📊 Indicator Functions
# ================================
def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def compute_bollinger_bands(series, period=20, std_dev=2):
    sma = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    upper_band = sma + (std * std_dev)
    lower_band = sma - (std * std_dev)
    return sma, upper_band, lower_band

# Strategy Logic
def run_mean_reversion_strategy():
    try:
        df = client.history(symbol=symbol, exchange=exchange, interval=interval,
                            start_date=start_date, end_date=end_date)
        if not isinstance(df, pd.DataFrame) or df.empty or 'close' not in df.columns:
            log_message(f"❌ Invalid or empty data for {symbol}, skipping.")
            return
    except requests.exceptions.Timeout:
        log_message("⏳ Request timed out while fetching historical data.")
        return

    print("Fetched DataFrame:", df if isinstance(df, pd.DataFrame) else type(df), df)
    df['rsi'] = compute_rsi(df['close'], rsi_period)
    df['sma'], df['bb_upper'], df['bb_lower'] = compute_bollinger_bands(df['close'], bollinger_period, bollinger_std)

    trades = []
    position = 0
    entry_price = None

    for i in range(bollinger_period, len(df)):
        row = df.iloc[i]
        close = row['close']
        rsi = row['rsi']
        lower_band = row['bb_lower']
        upper_band = row['bb_upper']
        sma = row['sma']
        time = row.name
        ltp = close
        trade_signal = None

        # After calculating RSI, BB, etc.
        if rsi > 70 and close > upper_band:
            trade_signal = "SELL"
        elif rsi < 30 and close < lower_band:
            trade_signal = "BUY"
        else:
            trade_signal = None

        # Now apply SL/Target only if trade_signal is valid
        if trade_signal:
            ltp = close
            if trade_signal == "BUY":
                stop_loss = ltp * 0.99
                target = ltp * 1.02
            else:
                stop_loss = ltp * 1.01
                target = ltp * 0.98

            log_message(f"{symbol} | {trade_signal} @ {ltp:.2f} | Close: {close:.2f}, RSI: {rsi:.2f}, BB Lower: {lower_band:.2f}, BB Upper: {upper_band:.2f} | SL: {stop_loss:.2f} | Target: {target:.2f}")

        if position == 0:
            if close < lower_band and rsi < 35:
                position = quantity
                entry_price = close
                trades.append([symbol, time, "BUY", close, None])
                send_telegram(f"📈 BUY trade_signal for {symbol} at {close:.2f} | RSI: {rsi:.2f}")
                log_message(f"BUY trade_signal for {symbol} at {close:.2f} | RSI: {rsi:.2f}")
            elif close > upper_band and rsi > 65:
                position = -quantity
                entry_price = close
                trades.append([symbol, time, "SELL", close, None])
                send_telegram(f"📉 SELL trade_signal for {symbol} at {close:.2f} | RSI: {rsi:.2f}")
                log_message(f"SELL trade_signal for {symbol} at {close:.2f} | RSI: {rsi:.2f}")

        elif position > 0:
            if close >= sma:
                pnl = ((close - entry_price) / entry_price) * 100
                trades.append([symbol, time, "EXIT", close, pnl])
                send_telegram(f"✅ EXIT long {symbol} at {close:.2f} | PnL: {pnl:.2f}%")
                log_message(f"EXIT long {symbol} at {close:.2f} | PnL: {pnl:.2f}%")
                position = 0
                entry_price = None

        elif position < 0:
            if close <= sma:
                pnl = ((entry_price - close) / entry_price) * 100
                trades.append([symbol, time, "EXIT", close, pnl])
                send_telegram(f"✅ EXIT short {symbol} at {close:.2f} | PnL: {pnl:.2f}%")
                log_message(f"EXIT short {symbol} at {close:.2f} | PnL: {pnl:.2f}%")
                position = 0
                entry_price = None

    df_trades = pd.DataFrame(trades, columns=["Symbol", "Timestamp", "Action", "Price", "PnL%"])
    df_trades.to_csv(TRADE_LOG, index=False)
    print(df_trades)

    if not df_trades[df_trades['Action'] == "EXIT"].empty:
        pnl_data = df_trades[df_trades['Action'] == "EXIT"]
        avg_pnl = pnl_data['PnL%'].astype(float).mean()
        win_rate = (pnl_data['PnL%'].astype(float) > 0).mean() * 100
        print("\n--- Strategy Summary ---")
        print("Total Trades:", len(pnl_data))
        print("Average PnL%:", round(avg_pnl, 2))
        print("Win Rate:", round(win_rate, 2), "%")

# ================================
# 🧹 Exit Handling
# ================================
if __name__ == "__main__":
    def graceful_exit(signum, frame):
        send_telegram("💝 Amar's Mean Reversion Strategy stopped.")
        log_message("Amar's Mean Reversion Strategy Graceful exit requested.")

        try:
            if not os.path.exists(TRADE_LOG):
                log_message("ℹ️ Trade log file not found. Creating new file with headers.")
                with open(TRADE_LOG, "w", encoding="utf-8") as f:
                    f.write("timestamp,symbol,entry_price,exit_price,pnl_pct,reason\n")
                send_telegram("ℹ️ Trade log initialized. No trades to summarize.")
            else:
                df = pd.read_csv(TRADE_LOG)
                if 'timestamp' not in df.columns:
                    log_message("⚠️ 'timestamp' column missing in trade log. Reinitializing with headers.")
                    with open(TRADE_LOG, "w", encoding="utf-8") as f:
                        f.write("timestamp,symbol,entry_price,exit_price,pnl_pct,reason\n")
                    send_telegram("⚠️ Trade log headers were missing. File has been reset.")
                else:
                    df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
                    df_today = df[df['timestamp'].dt.date == datetime.now().date()]

                    if not df_today.empty:
                        df_today['pnl'] = (df_today['exit_price'] - df_today['entry_price']) * quantity * df_today.apply(lambda row: 1 if row['entry_price'] < row['exit_price'] else -1, axis=1)
                        total_pnl = df_today['pnl'].sum()
                        avg_pct = df_today['pnl_pct'].mean()
                        total_trades = len(df_today)

                        summary = f"📊 Summary: {total_trades} trades | Net PnL: ₹{total_pnl:.2f} | Avg Return: {avg_pct:.2f}%"
                        log_message(summary)
                        send_telegram(summary)
                    else:
                        log_message("ℹ️ No trades found for today. Summary not generated.")
                        send_telegram("ℹ️ No trades found for today.")
        except Exception as e:
            log_message(f"⚠️ Failed to generate summary: {e}")

        sys.exit(0)

    signal.signal(signal.SIGINT, graceful_exit)
    signal.signal(signal.SIGTERM, graceful_exit)
    log_message("📊 Starting Mean Reversion BB + RSI Strategy")
    send_telegram("📊 Starting Mean Reversion BB + RSI Strategy")
    run_mean_reversion_strategy()