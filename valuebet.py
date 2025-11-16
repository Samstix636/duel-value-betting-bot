import os
import json
import asyncio
import logging
from datetime import datetime
import websocket

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    )
logger = logging.getLogger("ValueBetFinder")


def on_open(ws):
    logger.info(f"[{ws.sport}] WebSocket connected")


def on_message(ws, message):
    try:
        data = json.loads(message)

        markets = data.get("markets", [])
        market_parts = [format_market(m) for m in markets if format_market(m)]

        combined = " | ".join(market_parts)

        logger.info(
            f"[{ws.sport}] "
            f"Type={data.get('type')} | "
            f"Match={data.get('id')} | "
            f"Bookie={data.get('bookie')} | "
            f"{combined}"
        )

    except Exception as e:
        logger.error(f"[{ws.sport}] Message error: {e}")

def format_market(m):
    name = m.get("name")
    odds_list = m.get("odds", [])
    if not odds_list:
        return ""

    o = odds_list[0]  # always first odds object

    # ML market
    if "home" in o and "away" in o:
        return f"{name}(home={o.get('home')}, draw={o.get('draw')}, away={o.get('away')}, max={o.get('max')})"

    # Totals market
    if "over" in o and "under" in o:
        return f"{name}(hdp={o.get('hdp')}, over={o.get('over')}, under={o.get('under')}, max={o.get('max')})"

    # Fallback for any other market type
    clean_fields = ", ".join(f"{k}={v}" for k, v in o.items())
    return f"{name}({clean_fields})"


def on_error(ws, error):
    logger.error(f"[{ws.sport}] WebSocket Error: {error}")


def on_close(ws, close_status_code, close_msg):
    logger.info(f"[{ws.sport}] WebSocket closed")


class ValueBetFinder:
    def __init__(self, api_key: str, sport: str):
        self.api_key = api_key
        self.sport = sport
        self.bookmakers = ["Duel", "Pinnacle"]

    def build_ws_url(self) -> str:
        return f"wss://api.odds-api.io/v3/ws?apiKey={self.api_key}&sport={self.sport}"

    def create_ws(self) -> websocket.WebSocketApp:
        ws = websocket.WebSocketApp(
            self.build_ws_url(),
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close
        )
        ws.sport = self.sport
        return ws

    async def start(self):
        ws = self.create_ws()
        await asyncio.to_thread(ws.run_forever)


async def main():
    api_key = os.getenv("ODDS_API_KEY")
    if not api_key:
        raise ValueError("Missing API key.")

    sports = ["football"]
    tasks = []

    for sport in sports:
        finder = ValueBetFinder(api_key, sport)
        tasks.append(asyncio.create_task(finder.start()))

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
