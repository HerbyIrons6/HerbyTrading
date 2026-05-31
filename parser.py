import aiohttp
import asyncio

ITEMS_URL = "https://api.skinport.com/v1/items"
HISTORY_URL = "https://api.skinport.com/v1/sales/history"


async def fetch_current_listings() -> dict:
    """Запрашивает текущее состояние рынка (листинги)."""
    params = {
        "app_id": 730,
        "currency": "USD",
        "tradable": 0
    }
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
            print(f"[!] Ошибка при парсинге /items: {e}")
            return {}


async def fetch_sales_history(item_names: str = None) -> dict:
    """Запрашивает агрегированную историю продаж."""
    params = {
        "app_id": 730,
        "currency": "USD"
    }
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
            print(f"[!] Ошибка при парсинге /sales/history: {e}")
            return {}