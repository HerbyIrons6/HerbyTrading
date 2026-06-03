import numpy as np
import aiosqlite
import logging
from dataclasses import dataclass

DB_NAME = "trading_bot.db"


@dataclass
class Signal:
    skin: str
    current_price: float
    median_price: float
    z_score: float
    drain_percent: float
    volume_ratio: float
    base_score: int
    net_profit: float
    profit_percent: float


async def get_historical_data(hash_name: str, limit: int = 4320):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('''
                                  SELECT min_price, quantity, volume_24h
                                  FROM Price_History
                                  WHERE hash_name = ?
                                  ORDER BY timestamp DESC
                                      LIMIT ?
                                  ''', (hash_name, limit))
        return await cursor.fetchall()


def calculate_mad(data: np.ndarray):
    median = np.median(data)
    devs = np.abs(data - median)
    mad = np.median(devs)
    return median, mad


async def run_analysis(hash_name: str) -> Signal | None:
    rows = await get_historical_data(hash_name)
    if len(rows) < 10:
        return None

    prices = np.array([r[0] for r in rows if r[0] > 0])
    quantities = np.array([r[1] for r in rows])
    volumes = np.array([r[2] for r in rows])

    if len(prices) < 10:
        return None

    current_price = prices[0]
    current_qty = quantities[0]
    current_vol = volumes[0]

    vol_med = np.median(volumes)
    if current_vol == 0 or current_vol < (vol_med * 0.5):
        return None

    q1 = np.percentile(prices, 25)
    q3 = np.percentile(prices, 75)
    iqr = q3 - q1
    lower_bound = q1 - 1.5 * iqr
    upper_bound = q3 + 1.5 * iqr

    filtered_prices = prices[(prices >= lower_bound) & (prices <= upper_bound)]
    if len(filtered_prices) < 10:
        filtered_prices = prices

    median_price, mad = calculate_mad(filtered_prices)
    mad_floor = median_price * 0.001
    mad = max(mad, mad_floor)

    z_robust = (0.6745 * (median_price - current_price)) / mad

    recent_qty_max = np.max(quantities[:96]) if len(quantities) > 96 else np.max(quantities)
    drain = 0.0
    if recent_qty_max > 0:
        drain = ((recent_qty_max - current_qty) / recent_qty_max) * 100

    vol_ratio = 1.0
    if vol_med > 0:
        vol_ratio = current_vol / vol_med

    drop_percent = ((median_price - current_price) / median_price) * 100
    if drop_percent < 1.5:
        return None

    score = 0
    if z_robust >= 3.0:
        score += 1
    if drain >= 20.0:
        score += 1
    if vol_ratio >= 2.5:
        score += 1

    # --- РАСЧЕТ ЧИСТОЙ ПРИБЫЛИ С УЧЕТОМ 15% КОМИССИИ ---
    safe_return = median_price * 0.85
    net_profit = safe_return - current_price
    profit_percent = (net_profit / current_price) * 100 if current_price > 0 else 0

    if score >= 2:
        return Signal(
            skin=hash_name,
            current_price=round(current_price, 2),
            median_price=round(median_price, 2),
            z_score=round(z_robust, 2),
            drain_percent=round(drain, 2),
            volume_ratio=round(vol_ratio, 2),
            base_score=score,
            net_profit=round(net_profit, 2),
            profit_percent=round(profit_percent, 2)
        )
    return None


def get_trade_advice(signal: Signal | None, spread_percent: float | None) -> str:
    """Генерирует финальный торговый совет с учетом спреда и комиссии."""
    if not signal:
        return "🔴 <b>СКИП</b> (Предмет торгуется в норме, нет аномалий цены)"

    score = signal.base_score
    is_pump = False

    if spread_percent is not None:
        if spread_percent > 15.0:
            is_pump = True
        elif spread_percent <= 5.0:
            score += 1

    if is_pump:
        return "🔴 <b>СКИП</b> (Опасный спред стакана! Высокий риск манипуляции/неликвида)"

    if signal.net_profit <= 0:
        loss = abs(signal.net_profit)
        return f"🟡 <b>СКИП</b> (Скидка есть, но 15% комиссии съест всю прибыль. Убыток: -${loss})"

    if score >= 4:
        return f"🟢 <b>ПОКУПАТЬ</b> (Надежный сигнал. Чистая прибыль: <b>+${signal.net_profit}</b> / +{signal.profit_percent}%)"
    elif score == 3:
        return f"🟠 <b>МОЖНО РАССМОТРЕТЬ</b> (Средний риск. Чистая прибыль: <b>+${signal.net_profit}</b>)"
    else:
        return "🔴 <b>НЕ ПОКУПАТЬ</b> (Слабый сигнал для входа)"