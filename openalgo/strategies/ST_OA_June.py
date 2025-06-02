"""
OpenAlgo Supertrend Strategy Example

A complete example showing how to implement a Supertrend-based trading strategy
using OpenAlgo's technical indicators and trading API.
"""

from openalgo import api 
import pandas_ta as ta
import pandas as pd
import numpy as np
import time
import requests
import sys
import signal
import os
from datetime import datetime, timedelta

LOG_FILE = f"logs/ST_OA_{datetime.now().strftime('%Y-%m-%d')}.txt"
TRADE_LOG = f"logs/ST_OA_{datetime.now().strftime('%Y-%m-%d')}.csv"

TELEGRAM_ENABLED = True
BOT_TOKEN = "7891610241:AAHcNW6faW2lZGrxeSaOZJ3lSggI-ehl-pg"
CHAT_ID = "627470225"

# =====================
# Utility Functions
# =====================
def send_telegram(message):
    if TELEGRAM_ENABLED:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": message}
        requests.post(url, data=payload)

def log_message(msg):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')
    print(f"[{timestamp}] ST_OA {msg}")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] ST_OA {msg}\n")
    # if TELEGRAM_ENABLED:
    #     send_telegram(f"[{timestamp}] ST_OA {msg}")

def log_trade(symbol, entry_price, exit_price, profit_pct, reason):
    with open(TRADE_LOG, "a") as f:
        f.write(f"{datetime.now()},{symbol},{entry_price},{exit_price},{profit_pct:.2f},{reason}\n")

"""
OpenAlgo Supertrend Strategy Example

A complete example showing how to implement a Supertrend-based trading strategy
using OpenAlgo's technical indicators and trading API.
"""


class SupertrendStrategy:
    """
    A simple Supertrend-based trading strategy
    
    Strategy Rules:
    - Buy when price closes above Supertrend and RSI < 70
    - Sell when price closes below Supertrend or RSI > 80
    - Additional filter: ATR-based position sizing
    """
    
    def __init__(self, api_key, host="http://127.0.0.1:5000"):
        """Initialize the strategy"""
        self.client = api(api_key=api_key, host=host)
        self.api_key = api_key
        self.host = host  # <-- add this line
        self.position = 0  # 0: No position, 1: Long, -1: Short
        self.entry_price = 0
        
        # Strategy parameters
        self.st_period = 10
        self.st_multiplier = 3.0
        self.rsi_period = 14
        self.atr_period = 14
        self.rsi_overbought = 80
        self.rsi_oversold = 20
        
        # Risk management
        self.max_position_size = 10
        self.risk_per_trade = 0.02  # 2% risk per trade

    def get_ltp(self, symbol, exchange):
        try:
            url = f"{self.client.host}/ltp"
            response = requests.post(url, json={"symbol": symbol, "exchange": exchange})
            if response.status_code == 200:
                return response.json().get("last_price")
            else:
                log_message(f"❌ Failed to fetch LTP: HTTP {response.status_code}")
                return None
        except Exception as e:
            log_message(f"❌ Failed to fetch LTP: {e}")
            return None
    
    def fetch_data(self, symbol, exchange, interval="5m", days=30):
        """
        Fetch historical data and calculate indicators
        """
        try:
            from datetime import datetime, timedelta
            
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days)
            
            # Get historical data
            df = self.client.history(
                symbol=symbol,
                exchange=exchange,
                interval=interval,
                start_date=start_date.strftime("%Y-%m-%d"),
                end_date=end_date.strftime("%Y-%m-%d")
            )
            
            if isinstance(df, pd.DataFrame) and not df.empty:
                return self.calculate_indicators(df)
            else:
                log_message(f"No data received for {symbol}")
                send_telegram(f"No data received for {symbol}")
                return None
                
        except Exception as e:
            log_message(f"Error fetching data: {e}")
            send_telegram(f"Error fetching data: {e}")
            return None
    
    def calculate_indicators(self, df):
        """
        Calculate all required technical indicators
        """
        # Supertrend
        st_df = ta.supertrend(df['high'], df['low'], df['close'], length=self.st_period, multiplier=self.st_multiplier)
        df['supertrend'] = st_df[f'SUPERT_{self.st_period}_{self.st_multiplier}']
        df['trend'] = st_df[f'SUPERTd_{self.st_period}_{self.st_multiplier}']
        
        # RSI
        df['rsi'] = ta.rsi(df['close'], self.rsi_period)
        
        # ATR for position sizing
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], self.atr_period)
        
        # Moving averages for additional confirmation
        df['ema_20'] = ta.ema(df['close'], 20)
        df['ema_50'] = ta.ema(df['close'], 50)
        
        # Price position relative to Supertrend
        df['above_st'] = df['close'] > df['supertrend']
        df['below_st'] = df['close'] < df['supertrend']
        
        # Generate signals
        df['buy_signal'] = (
            (df['trend'] == -1) &  # Bullish Supertrend
            (df['rsi'] < 70) &     # Not overbought
            (df['close'] > df['ema_20'])  # Above short-term EMA
        )
        
        df['sell_signal'] = (
            (df['trend'] == 1) |   # Bearish Supertrend
            (df['rsi'] > self.rsi_overbought)  # Overbought
        )
        
        return df

    def update_trailing_sl_and_target(self, action, current_price, atr):
        if action == "BUY":
            self.trailing_sl = max(self.trailing_sl, current_price - 1.5 * atr)
        else:
            self.trailing_sl = min(self.trailing_sl, current_price + 1.5 * atr)
    
    def calculate_position_size(self, current_price, atr, account_balance=100000):
        """
        Calculate position size based on ATR and risk management
        """
        # Risk per trade based on ATR
        stop_loss_distance = atr * 2  # 2x ATR stop loss
        
        if stop_loss_distance == 0:
            return 1  # Minimum position size
        
        # Calculate position size based on risk
        risk_amount = account_balance * self.risk_per_trade
        position_size = int(risk_amount / stop_loss_distance)
        
        # Limit position size
        return min(position_size, self.max_position_size)
    
    def execute_trade(self, symbol, exchange, action, quantity, strategy_name="Supertrend"):
        """
        Enhanced trade execution logic with SL, targets, trailing SL, reversal exits, P&L tracking,
        partial profit booking, and continuous SL/target monitoring.
        """
        try:
            ltp_data = self.get_ltp(symbol, exchange)
            if not ltp_data:
                log_message("❌ No LTP data available")
                return
            price = ltp_data['last_price']
            atr_data = self.client.history(
                symbol=symbol,
                exchange=exchange,
                interval="5m",
                start_date=(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
                end_date=datetime.now().strftime("%Y-%m-%d")
            )

            stop_loss = price - 2 * atr if action == "BUY" else price + 2 * atr
            target = price + 3 * atr if action == "BUY" else price - 3 * atr
            partial_target = price + 1.5 * atr if action == "BUY" else price - 1.5 * atr
            partial_qty = quantity // 2
            remaining_qty = quantity - partial_qty

            response = self.client.placeorder(
                symbol=symbol,
                action=action,
                exchange=exchange,
                price_type="MARKET",
                product="MIS",
                quantity=quantity,
                strategy=strategy_name
            )

            if response.get('status') == 'success':
                self.entry_price = price
                self.stop_loss = stop_loss
                self.target_price = target
                self.partial_target = partial_target
                self.trailing_sl = stop_loss
                self.current_position = quantity if action == "BUY" else -quantity
                self.partial_booked = False

                log_message(f"✅ {action} order placed: {quantity} shares of {symbol} at ₹{price:.2f}")
                send_telegram(f"✅ {action} order placed: {quantity} shares of {symbol} at ₹{price:.2f}")

                # Start monitoring for SL, target, trailing SL, partial booking
                while True:
                    time.sleep(15)
                    current_price = self.get_ltp(symbol, exchange)

                    # Update trailing SL
                    if action == "BUY":
                        self.trailing_sl = max(self.trailing_sl, current_price - 1.5 * atr)
                    else:
                        self.trailing_sl = min(self.trailing_sl, current_price + 1.5 * atr)

                    # Partial booking check
                    if not self.partial_booked and (
                        (action == "BUY" and current_price >= partial_target) or
                        (action == "SELL" and current_price <= partial_target)):
                        exit_action = "SELL" if action == "BUY" else "BUY"
                        self.client.placeorder(
                            symbol=symbol,
                            action=exit_action,
                            exchange=exchange,
                            price_type="MARKET",
                            product="MIS",
                            quantity=partial_qty,
                            strategy=f"{strategy_name}_Partial"
                        )
                        self.partial_booked = True
                        log_message(f"📈 Partial target hit for {symbol}: {partial_qty} shares at ₹{current_price:.2f}")
                        send_telegram(f"📈 Partial target hit for {symbol}: {partial_qty} shares at ₹{current_price:.2f}")

                    # Exit condition check
                    if ((action == "BUY" and (current_price <= self.trailing_sl or current_price >= target)) or
                        (action == "SELL" and (current_price >= self.trailing_sl or current_price <= target))):

                        exit_action = "SELL" if action == "BUY" else "BUY"
                        self.client.placeorder(
                            symbol=symbol,
                            action=exit_action,
                            exchange=exchange,
                            price_type="MARKET",
                            product="MIS",
                            quantity=remaining_qty,
                            strategy=f"{strategy_name}_Exit"
                        )
                        log_message(f"🏁 Final exit {symbol}: remaining {remaining_qty} shares at ₹{current_price:.2f}")
                        send_telegram(f"🏁 Final exit {symbol}: remaining {remaining_qty} shares at ₹{current_price:.2f}")
                        break

                return response

            else:
                msg = response.get('message', 'Unknown error')
                log_message(f"❌ Order failed: {msg}")
                send_telegram(f"❌ Order failed: {msg}")
                return None

        except Exception as e:
            log_message(f"❌ Trade execution error: {e}")
            send_telegram(f"❌ Trade execution error: {e}")
            return None
    
    def run_strategy(self, symbol, exchange="NSE", interval="5m"):
        """
        Run the strategy for a given symbol
        """
        print(f"🚀 Starting Supertrend Strategy for {symbol}")
        print(f"Parameters: ST({self.st_period}, {self.st_multiplier}), RSI({self.rsi_period})")
        print("-" * 60)
        
        # Fetch and analyze data
        df = self.fetch_data(symbol, exchange, interval)
        
        if df is None or len(df) < 50:
            log_message(f"❌ Insufficient data for analysis")
            send_telegram(f"❌ Insufficient data for analysis")
            return
        
        # Get latest values
        latest = df.iloc[-1]
        prev = df.iloc[-2]
        
        current_price = latest['close']
        supertrend = latest['supertrend']
        trend = latest['trend']
        rsi = latest['rsi']
        atr = latest['atr']
        
        # Display current market state
        print(f"📊 Market Analysis for {symbol}:")
        print(f"   Current Price: ₹{current_price:.2f}")
        print(f"   Supertrend: ₹{supertrend:.2f}")
        print(f"   Trend: {'🟢 BULLISH' if trend == -1 else '🔴 BEARISH'}")
        print(f"   RSI: {rsi:.2f}")
        print(f"   ATR: {atr:.2f}")
        
        # Check for signals
        buy_signal = latest['buy_signal']
        sell_signal = latest['sell_signal']
        
        # Position management
        if buy_signal and self.position <= 0:
            # Enter long position
            quantity = self.calculate_position_size(current_price, atr)
            
            if self.position < 0:
                # Close short position first
                self.execute_trade(symbol, exchange, "BUY", abs(self.position), "Supertrend_Cover")
            
            # Open long position
            result = self.execute_trade(symbol, exchange, "BUY", quantity, "Supertrend_Long")
            if result:
                self.position = quantity
                self.entry_price = current_price
                log_message(f"📈 LONG position opened: {quantity} shares at ₹{current_price:.2f}")
                send_telegram(f"📈 LONG position opened: {quantity} shares at ₹{current_price:.2f}")
                
        elif sell_signal and self.position >= 0:
            # Enter short position or close long
            if self.position > 0:
                # Close long position
                result = self.execute_trade(symbol, exchange, "SELL", self.position, "Supertrend_Exit")
                if result:
                    profit_loss = (current_price - self.entry_price) * self.position
                    log_message(f"📉 LONG position closed. P&L: ₹{profit_loss:.2f}")
                    send_telegram(f"📉 LONG position closed. P&L: ₹{profit_loss:.2f}")
                    self.position = 0
                    
            # Optional: Enter short position (uncomment if short selling is allowed)
            """
            quantity = self.calculate_position_size(current_price, atr)
            result = self.execute_trade(symbol, exchange, "SELL", quantity, "Supertrend_Short")
            if result:
                self.position = -quantity
                self.entry_price = current_price
                print(f"📉 SHORT position opened: {quantity} shares at ₹{current_price:.2f}")
                log_message(f"No data received for {symbol}")
                send_telegram(f"No data received for {symbol}")
            """
        
        # Display current position
        if self.position > 0:
            unrealized_pnl = (current_price - self.entry_price) * self.position
            print(f"💼 Current Position: LONG {self.position} shares")
            print(f"   Entry Price: ₹{self.entry_price:.2f}")
            print(f"   Unrealized P&L: ₹{unrealized_pnl:.2f}")
        elif self.position < 0:
            unrealized_pnl = (self.entry_price - current_price) * abs(self.position)
            print(f"💼 Current Position: SHORT {abs(self.position)} shares")
            print(f"   Entry Price: ₹{self.entry_price:.2f}")
            print(f"   Unrealized P&L: ₹{unrealized_pnl:.2f}")
        else:
            print("💼 Current Position: FLAT (No position)")
    
    def backtest_strategy(self, symbol, exchange="NSE", interval="5m", days=90):
        """
        Backtest the strategy on historical data
        """
        print(f"📊 Backtesting Supertrend Strategy for {symbol}")
        print("-" * 60)
        
        df = self.fetch_data(symbol, exchange, interval, days)
        
        if df is None or len(df) < 100:
            log_message(f"❌ Insufficient data for backtesting")
            send_telegram(f"❌ Insufficient data for backtesting")
            return
        
        # Initialize backtest variables
        position = 0
        entry_price = 0
        trades = []
        equity_curve = [5000]  # Starting capital
        current_equity = 5000
        
        for i in range(50, len(df)):  # Start after indicators are valid
            row = df.iloc[i]
            prev_row = df.iloc[i-1]
            
            current_price = row['close']
            
            # Entry signals
            if row['buy_signal'] and position <= 0:
                if position < 0:
                    # Close short position
                    pnl = (entry_price - current_price) * abs(position)
                    current_equity += pnl
                    trades.append({
                        'type': 'SHORT_EXIT',
                        'price': current_price,
                        'quantity': abs(position),
                        'pnl': pnl,
                        'date': row.name
                    })
                
                # Open long position
                position = 100  # Fixed size for backtesting
                entry_price = current_price
                trades.append({
                    'type': 'LONG_ENTRY',
                    'price': current_price,
                    'quantity': position,
                    'pnl': 0,
                    'date': row.name
                })
                
            elif row['sell_signal'] and position >= 0:
                if position > 0:
                    # Close long position
                    pnl = (current_price - entry_price) * position
                    current_equity += pnl
                    trades.append({
                        'type': 'LONG_EXIT',
                        'price': current_price,
                        'quantity': position,
                        'pnl': pnl,
                        'date': row.name
                    })
                    position = 0
            
            # Update equity curve
            if position > 0:
                unrealized_pnl = (current_price - entry_price) * position
                equity_curve.append(current_equity + unrealized_pnl)
            elif position < 0:
                unrealized_pnl = (entry_price - current_price) * abs(position)
                equity_curve.append(current_equity + unrealized_pnl)
            else:
                equity_curve.append(current_equity)
        
        # Calculate performance metrics
        total_trades = len([t for t in trades if 'EXIT' in t['type']])
        winning_trades = len([t for t in trades if 'EXIT' in t['type'] and t['pnl'] > 0])
        total_pnl = sum([t['pnl'] for t in trades if 'EXIT' in t['type']])
        
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
        total_return = (current_equity - 100000) / 100000 * 100
        
        print(f"📈 Backtest Results:")
        print(f"   Total Trades: {total_trades}")
        print(f"   Winning Trades: {winning_trades}")
        print(f"   Win Rate: {win_rate:.1f}%")
        print(f"   Total P&L: ₹{total_pnl:.2f}")
        print(f"   Total Return: {total_return:.2f}%")
        print(f"   Final Equity: ₹{current_equity:.2f}")
        
        return trades, equity_curve


def main():
    """
    Main function to run the Supertrend strategy
    """
    # Configuration
    API_KEY = "78b9f1597a7f903d3bfc76ad91274a7cc7536c2efc4508a8276d85fbc840d7d2"  # Replace with your actual API key
    symbols = ["ADANIPORTS", "M&M", "HINDUNILVR", "TATACONSUM", "ADANIENT", "SBIN", "NESTLEIND", "SBILIFE", "ASIANPAINT", "AXISBANK"]
    EXCHANGE = "NSE"
    
    print("OpenAlgo Supertrend Strategy")
    print("============================\n")
    
    # Initialize strategy
    strategy = SupertrendStrategy(api_key=API_KEY)
    
    # Run backtest first
    log_message(f"1. Running Backtest...")
    send_telegram(f"1. Running Backtest...")

    for symbol in symbols:
        strategy.backtest_strategy(symbol, EXCHANGE, days=60)
    
    print("\n" + "="*60 + "\n")
    
    # Run live strategy (single execution)
    log_message(f"2. Running Live Analysis...")
    send_telegram(f"2. Running Live Analysis...")

    for symbol in symbols:
        strategy.run_strategy(symbol, EXCHANGE)
    
    print("\n" + "="*60 + "\n")
    
    # For continuous monitoring, uncomment the following:
    print("3. Starting Continuous Monitoring...")
    while True:
        try:
            for symbol in symbols:
                strategy.run_strategy(symbol, EXCHANGE)
                time.sleep(2)  # Small delay between symbols
            log_message(f"⏰ Waiting 3 minutes for next analysis...")
            send_telegram(f"⏰ Waiting 3 minutes for next analysis...")
            time.sleep(180)  # Wait 3 minutes
        except KeyboardInterrupt:
            log_message(f"🛑 Strategy stopped by user")
            send_telegram(f"🛑 Strategy stopped by user")
            break
        except Exception as e:
            log_message(f"❌ Error in strategy loop: {e}")
            send_telegram(f"❌ Error in strategy loop: {e}")
            time.sleep(60)  # Wait 1 minute before retrying


if __name__ == "__main__":
    main()

# =====================
# Graceful Exit
# =====================
def graceful_exit(sig, frame):
    log_message("Graceful shutdown requested.")
    send_telegram("Strategy stopped gracefully.")
    sys.exit(0)

signal.signal(signal.SIGINT, graceful_exit)
signal.signal(signal.SIGTERM, graceful_exit)
