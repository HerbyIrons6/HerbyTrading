import aiohttp
import asyncio
import logging
import re
import urllib.parse

# Настройки заголовков для обхода базовых защит
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9'
}


def with_retry(retries=3, base_delay=2.0):
    """
    Декоратор для умного повтора запросов (Exponential Backoff).
    Защищает от банов по IP (429) и временных падений API.
    """

    def decorator(func):
        async def wrapper(*args, **kwargs):
            for attempt in range(retries):
                try:
                    return await func(*args, **kwargs)
                except aiohttp.ClientResponseError as e:
                    if e.status == 429:
                        delay = base_delay * (2 ** attempt)
                        logging.warning(f"⚠️ Rate limit 429 в {func.__name__}. Ждем {delay} сек...")
                        await asyncio.sleep(delay)
                    else:
                        logging.error(f"HTTP Ошибка {e.status} в {func.__name__}: {e.message}")
                        if attempt == retries - 1: return None
                        await asyncio.sleep(base_delay)
                except asyncio.TimeoutError:
                    logging.warning(f"⌛ Таймаут в {func.__name__}. Попытка {attempt + 1}/{retries}")
                    if attempt == retries - 1: return None
                    await asyncio.sleep(base_delay)
                except Exception as e:
                    logging.error(f"Критическая ошибка в {func.__name__}: {e}")
                    if attempt == retries - 1: return None
                    await asyncio.sleep(base_delay)
            return None

        return wrapper

    return decorator


@with_retry(retries=3, base_delay=5.0)
async def fetch_current_listings():
    """Получает минимальные цены и количество со Skinport."""
    url = "https://api.skinport.com/v1/items"
    params = {"app_id": 730, "currency": "USD", "tradable": 0}

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        async with session.get(url, params=params, raise_for_status=True, timeout=15) as response:
            data = await response.json()

            listings = {}
            for item in data:
                listings[item['market_hash_name']] = {
                    "min_price": item.get("min_price", 0.0),
                    "quantity": item.get("quantity", 0)
                }
            return listings


@with_retry(retries=3, base_delay=5.0)
async def fetch_sales_history():
    """Получает объемы торгов и медианные цены со Skinport."""
    url = "https://api.skinport.com/v1/sales/history"
    params = {"app_id": 730, "currency": "USD"}

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        async with session.get(url, params=params, raise_for_status=True, timeout=15) as response:
            data = await response.json()

            history = {}
            for item in data:
                history[item['market_hash_name']] = {
                    "last_24_hours": item.get("last_24_hours", {}),
                    "last_7_days": item.get("last_7_days", {})
                }
            return history


@with_retry(retries=3, base_delay=2.0)
async def fetch_steam_item_nameid(hash_name: str) -> int | None:
    """Парсит скрытый item_nameid со страницы предмета в Steam."""
    encoded_name = urllib.parse.quote(hash_name)
    url = f"https://steamcommunity.com/market/listings/730/{encoded_name}"

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        async with session.get(url, raise_for_status=True, timeout=10) as response:
            html = await response.text()
            match = re.search(r'Market_LoadOrderSpread\(\s*(\d+)\s*\)', html)
            if match:
                return int(match.group(1))
            return None


@with_retry(retries=3, base_delay=2.0)
async def fetch_steam_order_histogram(item_nameid: int) -> dict | None:
    """Получает стакан Steam и считает спред."""
    url = "https://steamcommunity.com/market/itemordershistogram"
    params = {
        "country": "US",
        "language": "english",
        "currency": 1,
        "item_nameid": item_nameid,
        "two_factor": 0
    }

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        async with session.get(url, params=params, raise_for_status=True, timeout=10) as response:
            data = await response.json()

            if data.get('success') != 1:
                return None

            sell_orders = data.get('sell_order_graph', [])
            buy_orders = data.get('buy_order_graph', [])

            if not sell_orders or not buy_orders:
                return None

            lowest_sell = float(sell_orders[0][0])
            highest_buy = float(buy_orders[0][0])

            spread = lowest_sell - highest_buy
            spread_percent = (spread / lowest_sell) * 100 if lowest_sell > 0 else 0

            return {
                "lowest_sell": lowest_sell,
                "highest_buy": highest_buy,
                "spread_percent": round(spread_percent, 2)
            }