import asyncio
import aiohttp
import os
from datetime import datetime
from typing import List, Dict
import json
import logging
import csv

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='[%(asctime)s] %(levelname)s: %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S')

class ValueBetMonitor:
    def __init__(self, api_key: str, bookmakers, min_ev, interval_seconds: int):
        self.api_key = api_key
        self.bookmakers = bookmakers
        self.min_ev = min_ev
        self.interval_seconds = interval_seconds
        self.is_running = False
        self.seen_bets = []
        self.markets = ["Spread", "ML", "Totals", "Totals HT", "Asian Handicap", "Asian Handicap HT", "Team Total home", "Team Total away", "Team Total home HT", "Team Total away HT", "ML HT", "Spread HT"]


    def is_target_market(self, market_name: str) -> bool:
        if not market_name:
            return False

        market_lower = market_name.lower().strip()
        target_markets = [m.lower().strip() for m in self.markets]

        return market_lower in target_markets

    async def fetch_valuebets_from_all_bookmakers(self) -> List[Dict]:
        # Fetch value bets from all bookmakers in parallel
        async with aiohttp.ClientSession() as session:
            tasks = [self.fetch_valuebets(session, bookmaker) for bookmaker in self.bookmakers]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            bets = []
            for result in results:
                if isinstance(result, list):
                    bets.extend(result)
                elif isinstance(result, Exception):
                    logging.error(f"Error: {result}")
            return bets

    async def fetch_valuebets(self, session: aiohttp.ClientSession, bookmaker: str) -> List[Dict]:
        # Connect to provided endpoint
        try:
            url = f"https://api.odds-api.io/v3/value-bets?apiKey={self.api_key}&bookmaker={bookmaker}&includeEventDetails=true"
            async with session.get(url) as response:
                return await response.json() if response.status == 200 else []
        except Exception as e:
            logging.error(f"Error fetching {bookmaker}: {e}")
            return []

    def save_valuebet_to_csv(self, bet_data, filename="valuebet-endpoint.csv"):
        file_exists = os.path.exists(filename)

        with open(filename, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=bet_data.keys())

            # Write header only if file is new
            if not file_exists:
                writer.writeheader()

            writer.writerow(bet_data)

    async def process_and_poll(self):
        try:
            # logging.info("Polling for value bets...") #pulse

            all_bets = await self.fetch_valuebets_from_all_bookmakers()

            # if all_bets:
            #     with open("allbets.csv", "w", newline="", encoding="utf-8") as f:
            #         writer = csv.DictWriter(f, fieldnames=all_bets[0].keys())
            #         writer.writeheader()
            #         writer.writerows(all_bets)

            # Filter bets by sport, EV, and target market
            filtered = []

            for bet in all_bets:
                temp_ev = bet.get('expectedValue', 0)
                ev = round(temp_ev - 100, 2)

                sport = bet.get('event', {}).get('sport', '').lower()
                market_name = bet.get('market', {}).get('name', '')  

                # Check EV threshold
                if ev < self.min_ev:
                    logging.info(f"Skipped: EV {ev}% below minimum {self.min_ev}%")
                    continue
                
                if sport.lower() not in ('football', 'tennis'):
                    logging.info(f"Skipped: Sport '{sport}' not in target sports")
                    continue

                if not self.is_target_market(market_name):
                    logging.info(f"Skipped: Market '{market_name}' not in target markets")
                    continue

                totals_markets = ("totals", "totals ht", "team total home", "team total away", "team total home ht", "team total away ht")
                if market_name.lower() in totals_markets:
                    odds = bet["bookmakerOdds"]
                    odds["over"] = odds.pop("home")
                    odds["under"] = odds.pop("away")

                    betside = bet.get("betSide") or bet.get("betside")
                    if betside == "home":
                        bet["betSide"] = "over"
                    elif betside == "away":
                        bet["betSide"] = "under"

                filtered.append(bet)
            
            # if not filtered:
            #     logging.info("Poll complete - No new value bets found")  # Completion pulse
            #     return
        
            for bet in filtered:
                # logging.info(f'------------{bet}-------')
                betside = bet.get("betSide") or bet.get("betside")

                # Map betside to bookmakerOdds key
                odds_key_map = {"home": "home", "away": "away", "draw": "draw", "over": "over", "under": "under"}
                selected_key = odds_key_map.get(betside)

                odds = None
                if selected_key:
                    odds = bet.get("bookmakerOdds", {}).get(selected_key)

                temp_ev = bet.get('expectedValue', 0)
                ev = round(temp_ev - 100, 2)

                value_bet_data = {
                    "sport": bet.get("event", {}).get("sport", "Unknown"),
                    "league": bet.get("event", {}).get("league", "Unknown"),
                    "home": bet.get("event", {}).get("home", "Unknown"),
                    "away": bet.get("event", {}).get("away", "Unknown"),
                    "market_name": bet.get("market", {}).get("name", "Unknown"),
                    "odds": odds,
                    "ev": ev,
                    "betside": betside,
                }

                hdp_markets = ("spread", "asian handicap", "spread ht", "asian handicap ht")

                if bet.get("market", {}).get("name", "Unknown").lower() in hdp_markets:  # Added ()
                    value_bet_data['hdp'] = bet.get('bookmakerOdds', {}).get('hdp')
                else:
                    value_bet_data['hdp'] = None
                    

                key = bet.get("eventId", 0)
                duplicate_found = False

                for seen_bet in self.seen_bets:
                    seen_bet_key = seen_bet.get("event_id", 0)
                    same_id = key == seen_bet_key

                    if same_id:
                        duplicate_found = True
                    
                if not duplicate_found:
                    logging.info(f"🔔Value bet found @ {bet.get('bookmaker', 'Unknown')}\n{json.dumps(value_bet_data, indent=4)}\n")
                    value_bet_data['event_id'] = bet.get("eventId", 0)
                    self.seen_bets.append(value_bet_data)
                    
                    # Save to CSV
                    value_bet_data['id'] = bet.get("id")
                    value_bet_data['bookmaker'] = bet.get("bookmaker", "Unknown"),
                    self.save_valuebet_to_csv(value_bet_data)

        except Exception as e:
            logging.error(f"Error during polling: {e}")
            import traceback
            logging.error(traceback.format_exc())

    async def start(self):
        # Start the monitoring loop
        if self.is_running:
            logging.warning("Monitor already running")
            return

        logging.info(f"Starting monitor: {', '.join(self.bookmakers)}")
        logging.info(f"Target sports: Football, Tennis")
        logging.info("Markets will be set dynamically based on sport\n")
        
        self.is_running = True
        await self.process_and_poll() 
        
        while self.is_running:
            await asyncio.sleep(self.interval_seconds)
            await self.process_and_poll()

    def stop(self):
        # Stop monitoring
        self.is_running = False
        logging.info("Monitor stopped")


async def main():
    api_key = os.getenv('ODDS_API_KEY')
    if not api_key:
        raise ValueError("Missing ODDS_API_KEY in environment variables")
    
    monitor = ValueBetMonitor(api_key=api_key,
                              bookmakers=['Duel'],
                              min_ev = 1,  # 1%.
                              interval_seconds=5)

    try:
        await monitor.start()
    except KeyboardInterrupt:
        monitor.stop()
        logging.info("Stopped by user")


if __name__ == "__main__":
    asyncio.run(main())