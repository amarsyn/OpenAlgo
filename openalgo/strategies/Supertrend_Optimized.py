import pandas as pd
import numpy as np
import time
import requests
import os
import signal
import sys
from datetime import datetime, timedelta
import pandas_ta as ta
from openalgo import api

# ================================
# ⚙️ configuration
# ================================
api_key = '78b9f1597a7f903d3bfc76ad91274a7cc7536c2efc4508a8276d85fbc840d7d2'
strategy_name = "supertrend_optimized_merged"
symbols = ["HCLTECH","HEROMOTOCO"]
exchange = "NSE"
product = "MIS"
quantity = 2
atr_period = 10
atr_multiplier = 1.5

mode = "live"
max_trades_per_day = 2

start_time = "09:20"
end_time = "15:15"

stop_loss_pct = 0.4
target_pct = 1.0
trailing_sl_pct = 0.5
trailing_trigger_pct = 0.5
partial_profit_pct = 0.5

telegram_enabled = True
bot_token = "7891610241:AAHcNW6faW2lZGrxeSaOZJ3lSggI-ehl-pg"
chat_id = "627470225"

# ================================
# 📦 state initialization
# ================================
client = api(api_key=api_key, host='http://127.0.0.1:5000')
positions = {sym: 0 for sym in symbols}
entry_prices = {sym: None for sym in symbols}
max_favorable_price = {sym: None for sym in symbols}
partial_booked = {sym: False for sym in symbols}
daily_trade_count = {sym: 0 for sym in symbols}

log_file = f"logs/ST_OA_{datetime.now().strftime('%Y-%m-%d')}.log"

# ================================
# 📝 logging & alerts
# ================================
def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    formatted = f"{timestamp} ST-OA {msg}"
    print(formatted)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(formatted + "\n")

def alert(msg):
    if telegram_enabled:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {"chat_id": chat_id, "text": msg}
        requests.post(url, data=payload)

# ================================
# 📊 indicators
# ================================
def calculate_indicators(df):
    df['vwap'] = (df['close'] * df['volume']).cumsum() / df['volume'].cumsum()
    df['rsi'] = ta.rsi(df['close'], length=14)
    macd = ta.macd(df['close'])
    df['macd'] = macd['MACD_12_26_9']
    df['signal'] = macd['MACDs_12_26_9']
    df['adx'] = ta.adx(df['high'], df['low'], df['close'])['ADX_14']
    return df

def calculate_supertrend(df):
    hl2 = (df['high'] + df['low']) / 2
    atr = ta.atr(df['high'], df['low'], df['close'], length=atr_period)
    upperband = hl2 + (atr_multiplier * atr)
    lowerband = hl2 - (atr_multiplier * atr)
    st = [True] * len(df)
    for i in range(1, len(df)):
        if df['close'].iloc[i] > upperband.iloc[i-1]:
            st[i] = True
        elif df['close'].iloc[i] < lowerband.iloc[i-1]:
            st[i] = False
        else:
            st[i] = st[i-1]

    df['supertrend'] = st
    return df

# ================================
# 📈 strategy execution
# ================================
def run_strategy():
    while True:
        now = datetime.now().strftime("%H:%M")
        if now < start_time or now > end_time:
            time.sleep(60)
            continue

        for symbol in symbols:
            try:
                df = client.history(symbol=symbol, exchange=exchange, interval="5m",
                                    start_date=(datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d'),
                                    end_date=datetime.now().strftime('%Y-%m-%d'))
                if df.empty:
                    continue

                df = calculate_indicators(df)
                df = calculate_supertrend(df)
                close = df['close'].iloc[-1]
                vwap = df['vwap'].iloc[-1]
                rsi = df['rsi'].iloc[-1]
                macd = df['macd'].iloc[-1]
                signal_val = df['signal'].iloc[-1]
                adx = df['adx'].iloc[-1]
                st = df['supertrend'].iloc[-1]

                entry = entry_prices[symbol]
                position = positions[symbol]
                direction = 1 if position > 0 else -1 if position < 0 else 0

                log(f"{symbol} | LTP: {close:.2f} | Trend: {'UP' if st else 'DOWN'} | VWAP: {vwap:.2f} | RSI: {rsi:.2f} | MACD: {macd:.2f} | Signal: {signal_val:.2f} | ADX: {adx:.2f}")

                long_signal = st and close > vwap and rsi > 50 and macd > signal_val and adx > 15
                short_signal = not st and close < vwap and rsi < 50 and macd < signal_val and adx > 15

                # Debugging signal evaluations
                log(f"Signal Check | Long: {long_signal} | Short: {short_signal}")                

                # trend reversal handling with immediate re-entry
                if position > 0 and short_signal:   # Long to Short
                    client.placesmartorder(strategy=strategy_name, symbol=symbol, action="SELL", exchange=exchange, price_type="MARKET", product=product, quantity=abs(position), position_size=0)
                    alert(f"🔁 Reversing LONG to SHORT: {symbol} @ {close:.2f}")
                    log(f"Reversed LONG to SHORT: {symbol} @ {close:.2f}")
                    positions[symbol] = 0
                    entry_prices[symbol] = None
                    max_favorable_price[symbol] = None
                    partial_booked[symbol] = False

                    client.placesmartorder(strategy=strategy_name, symbol=symbol, action="SELL", exchange=exchange, price_type="MARKET", product=product, quantity=quantity, position_size=-quantity)
                    positions[symbol] = -quantity
                    entry_prices[symbol] = close
                    max_favorable_price[symbol] = close
                    partial_booked[symbol] = False
                    daily_trade_count[symbol] += 1
                    alert(f"🔻 SHORT Entry (Reversal): {symbol} @ {close:.2f}")
                    log(f"SHORT Entry (Reversal): {symbol} @ {close:.2f}")

                elif position < 0 and long_signal:    # Short to Long
                    client.placesmartorder(strategy=strategy_name, symbol=symbol, action="BUY", exchange=exchange, price_type="MARKET", product=product, quantity=abs(position), position_size=0)
                    alert(f"🔁 Reversing SHORT to LONG: {symbol} @ {close:.2f}")
                    log(f"Reversed SHORT to LONG: {symbol} @ {close:.2f}")
                    positions[symbol] = 0
                    entry_prices[symbol] = None
                    max_favorable_price[symbol] = None
                    partial_booked[symbol] = False

                    client.placesmartorder(strategy=strategy_name, symbol=symbol, action="BUY", exchange=exchange, price_type="MARKET", product=product, quantity=quantity, position_size=quantity)
                    positions[symbol] = quantity
                    entry_prices[symbol] = close
                    max_favorable_price[symbol] = close
                    partial_booked[symbol] = False
                    daily_trade_count[symbol] += 1
                    alert(f"🚀 LONG Entry (Reversal): {symbol} @ {close:.2f}")
                    log(f"LONG Entry (Reversal): {symbol} @ {close:.2f}")

                # rest of the strategy logic remains unchanged...
                # fresh entry
                if position == 0 and daily_trade_count[symbol] < max_trades_per_day:
                    if long_signal:
                        client.placesmartorder(strategy=strategy_name, symbol=symbol, action="BUY",
                                               exchange=exchange, price_type="MARKET", product=product,
                                               quantity=quantity, position_size=quantity)
                        positions[symbol] = quantity
                        entry_prices[symbol] = close
                        max_favorable_price[symbol] = close
                        partial_booked[symbol] = False
                        daily_trade_count[symbol] += 1
                        alert(f"🚀 LONG {symbol} at {close:.2f}")
                        log(f"LONG Entry: {symbol} @ {close:.2f}")
                    elif short_signal:
                        client.placesmartorder(strategy=strategy_name, symbol=symbol, action="SELL",
                                               exchange=exchange, price_type="MARKET", product=product,
                                               quantity=quantity, position_size=-quantity)
                        positions[symbol] = -quantity
                        entry_prices[symbol] = close
                        max_favorable_price[symbol] = close
                        partial_booked[symbol] = False
                        daily_trade_count[symbol] += 1
                        alert(f"🔻 SHORT {symbol} at {close:.2f}")
                        log(f"SHORT Entry: {symbol} @ {close:.2f}")

                # position management
                elif position != 0:
                    peak = max_favorable_price[symbol]
                    max_favorable_price[symbol] = max(peak, close) if direction > 0 else min(peak, close)
                    drop = abs((close - max_favorable_price[symbol]) / max_favorable_price[symbol]) * 100
                    pnl = (close - entry_prices[symbol]) / entry_prices[symbol] * 100 * direction

                    target_hit = pnl >= target_pct
                    sl_hit = pnl <= -stop_loss_pct
                    trail_exit = pnl >= trailing_trigger_pct and drop >= trailing_sl_pct

                    if not partial_booked[symbol] and pnl >= partial_profit_pct:
                        partial_booked[symbol] = True
                        half_qty = quantity // 2
                        action = "SELL" if direction > 0 else "BUY"
                        client.placesmartorder(strategy=strategy_name, symbol=symbol, action=action,
                                               exchange=exchange, price_type="MARKET", product=product,
                                               quantity=half_qty, position_size=direction * half_qty)
                        alert(f"💰 Partial Profit Booked: {symbol} at {close:.2f}")
                        log(f"Partial Exit: {symbol} @ {close:.2f}")
                        positions[symbol] = direction * half_qty

                    if target_hit or sl_hit or trail_exit:
                        reason = "Target" if target_hit else "Stop Loss" if sl_hit else "Trailing SL"
                        action = "SELL" if direction > 0 else "BUY"
                        client.placesmartorder(strategy=strategy_name, symbol=symbol, action=action,
                                               exchange=exchange, price_type="MARKET", product=product,
                                               quantity=abs(positions[symbol]), position_size=0)
                        alert(f"❌ Exit {reason}: {symbol} at {close:.2f} | PnL: {pnl:.2f}%")
                        log(f"Exit {reason}: {symbol} @ {close:.2f} | PnL: {pnl:.2f}%")
                        positions[symbol] = 0
                        entry_prices[symbol] = None
                        max_favorable_price[symbol] = None

            except Exception as e:
                log(f"Error processing {symbol}: {str(e)}")

        time.sleep(30)

# ================================
# 🚪 exit handling
# ================================
def handle_exit(signum, frame):
    log("Supertrend Optimized - Graceful exit requested.")
    sys.exit(0)

signal.signal(signal.SIGINT, handle_exit)
signal.signal(signal.SIGTERM, handle_exit)

# ================================
# ▶️ main
# ================================
if __name__ == "__main__":
    os.makedirs("logs", exist_ok=True)
    log("✅ Supertrend Optimized Strategy Started")
    alert("✅ Supertrend Optimized Strategy Started")
    run_strategy()
