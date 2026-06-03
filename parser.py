import aiohttp
import logging
import re
import urllib.parse

ITEMS_URL = "https://api.skinport.com/v1/items"
HISTORY_URL = "https://api.skinport.com/v1/sales/history"
STEAM_MARKET_URL = "https://steamcommunity.com/market/listings/730/"
STEAM_RENDER_URL = "https://steamcommunity.com/market/listings/730/{}/render"
STEAM_HISTOGRAM_URL = "https://steamcommunity.com/market/itemordershistogram"


async def fetch_current_listings() -> dict:
    params = {"app_id": 730, "currency": "USD", "tradable": 0}
    headers = {"Accept-Encoding": "br"}

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(ITEMS_URL, params=params, headers=headers) as response:
                response.raise_for_status()
                raw_data = await response.json()

                parsed_data = {}
                for item in raw_data:
                    name = item.get("market_hash_name")
                    if not name:
                        continue
                    parsed_data[name] = {
                        "min_price": item.get("min_price"),
                        "quantity": item.get("quantity", 0)
                    }
                return parsed_data
        except Exception as e:
            logging.error(f"Ошибка при запросе /items: {e}")
            return {}


async def fetch_sales_history(item_names: str = None) -> dict:
    params = {"app_id": 730, "currency": "USD"}
    if item_names:
        params["market_hash_name"] = item_names

    headers = {"Accept-Encoding": "br"}

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(HISTORY_URL, params=params, headers=headers) as response:
                response.raise_for_status()
                raw_data = await response.json()

                history_data = {
                    item["market_hash_name"]: item
                    for item in raw_data if "market_hash_name" in item
                }
                return history_data
        except Exception as e:
            logging.error(f"Ошибка при запросе /sales/history: {e}")
            return {}


async def fetch_steam_item_nameid(hash_name: str) -> int | None:
    """
    Получает item_nameid из Steam.
    Использует "грубый" regex-бульдозер для обхода сложной структуры React SSR.
    """
    encoded_name = urllib.parse.quote(hash_name)

    # 1. Сначала пробуем быстрый AJAX /render
    render_url = STEAM_RENDER_URL.format(encoded_name)
    render_params = {"currency": 1, "language": "english", "country": "US", "start": 0, "count": 1}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "en-US,en;q=0.9",
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(render_url, params=render_params, headers=headers) as response:
                if response.status == 429:
                    logging.warning(f"Steam Rate Limit (429) при запросе /render для {hash_name}.")
                elif response.status == 200:
                    # Защита от ошибки Expecting value
                    if "application/json" in response.headers.get("Content-Type", ""):
                        data = await response.json()
                        if "item_nameid" in data:
                            return int(data["item_nameid"])
        except Exception as e:
            logging.debug(f"Сбой /render для {hash_name}: {e}")

    # 2. Если /render не сработал или вернул HTML, качаем всю страницу и используем грубый поиск
    url = f"{STEAM_MARKET_URL}{encoded_name}"
    html_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=html_headers) as response:
                if response.status == 429:
                    logging.warning(f"Steam Rate Limit (429) при скачивании HTML для {hash_name}.")
                    return None

                response.raise_for_status()
                html = await response.text()

                # Грубый Regex: ищет item_nameid игнорируя слэши, кавычки и пробелы
                patterns = [
                    r'Market_LoadOrderSpread\(\s*(\d+)\s*\)',
                    r'item_nameid[\\"\']*\s*:\s*[\\"\']*(\d+)',
                    r'nameid[\\"\']*\s*:\s*[\\"\']*(\d+)'
                ]

                for pattern in patterns:
                    match = re.search(pattern, html)
                    if match:
                        nameid = int(match.group(1))
                        logging.info(f"Успех! ID {nameid} для {hash_name} вырезан грубым парсингом.")
                        return nameid

                logging.warning(f"Даже грубый поиск не нашел ID для {hash_name}.")
                return None
        except Exception as e:
            logging.error(f"Ошибка при загрузке HTML для {hash_name}: {e}")
            return None


async def fetch_steam_order_histogram(item_nameid: int) -> dict | None:
    if not item_nameid:
        return None

    params = {"country": "US", "language": "english", "currency": 1, "item_nameid": item_nameid, "two_factor": 0}
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://steamcommunity.com/market/"}

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(STEAM_HISTOGRAM_URL, params=params, headers=headers) as response:
                response.raise_for_status()
                data = await response.json()

                if data.get("success") == 1:
                    lowest_sell = data.get("sell_order_graph", [[0]])[0][0]
                    highest_buy = data.get("buy_order_graph", [[0]])[0][0]

                    return {
                        "lowest_sell": float(lowest_sell),
                        "highest_buy": float(highest_buy),
                        "spread_percent": round(((lowest_sell - highest_buy) / highest_buy) * 100,
                                                2) if highest_buy > 0 else 0
                    }
                return None
        except Exception as e:
            logging.error(f"Ошибка при запросе стакана Steam для ID {item_nameid}: {e}")
            return None