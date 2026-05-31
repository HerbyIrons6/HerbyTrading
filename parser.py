import aiohttp
import asyncio

# Базовые эндпоинты Skinport
ITEMS_URL = "https://api.skinport.com/v1/items"
HISTORY_URL = "https://api.skinport.com/v1/sales/history"


async def fetch_current_listings() -> dict:
    """
    Запрашивает текущее состояние рынка (листинги).
    Возвращает { "Скин": {"min_price": 10.5, "quantity": 100} }
    """
    params = {
        "app_id": 730,
        "currency": "USD",
        "tradable": 0
    }
    # Строгое требование документации Skinport
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
    """
    Запрашивает агрегированную историю продаж.
    item_names: строка с названиями через запятую (опционально).
    """
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

                # Перепаковываем список в словарь для быстрого поиска
                history_data = {
                    item["market_hash_name"]: item
                    for item in raw_data if "market_hash_name" in item
                }
                return history_data

        except Exception as e:
            print(f"[!] Ошибка при парсинге /sales/history: {e}")
            return {}


# --- Блок тестирования ---
if __name__ == "__main__":
    async def test_run():
        print("⏳ Запрашиваем текущие листинги...")
        listings = await fetch_current_listings()
        print(f"✅ Загружено листингов: {len(listings)}")

        test_skin = "AK-47 | Redline (Field-Tested)"

        if test_skin in listings:
            print(f"\nТекущий рынок для {test_skin}:")
            print(f"Минимальная цена: ${listings[test_skin]['min_price']}")
            print(f"Количество лотов: {listings[test_skin]['quantity']}")

        print("\n⏳ Запрашиваем историю продаж...")
        # Запрашиваем хистори только для одного предмета для экономии трафика
        history = await fetch_sales_history(item_names=test_skin)

        if test_skin in history:
            stats = history[test_skin]
            last_24h = stats.get('last_24_hours', {})
            last_7d = stats.get('last_7_days', {})

            print(f"\nСтатистика продаж для {test_skin}:")
            print(f"Объем торгов за 24ч: {last_24h.get('volume')} шт.")
            print(f"Медианная цена (24ч): ${last_24h.get('median')}")
            print(f"Медианная цена (7дн): ${last_7d.get('median')}")


    asyncio.run(test_run())