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
    def __init__(self, api_key: str, bookmakers: List[str], min_ev: float, interval_seconds: int):
        self.api_key = api_key
        self.bookmakers = bookmakers
        self.min_ev = min_ev
        self.interval_seconds = interval_seconds
        self.is_running = False
        self.seen_bets = []
        self.markets = []

    def set_markets_for_sport(self, sport: str):
        # Set target markets based on sport
        if sport == "tennis":
            self.markets = ["ML", "Totals", "Spread"]
        elif sport == "football":
            self.markets = ["ML", "Spread", "Totals", "Totals HT"]
        elif sport == "basketball":
            self.markets = ["ML", "Spread", "Totals", "Team Totals"]
        else:
            self.markets = ["ML"]

    def is_target_market(self, market_name: str) -> bool:
        """Check if the market is one we're interested in"""
        if not market_name:
            return False
        
        market_lower = market_name.lower().strip()
        
        # Check for exact or partial matches (case-insensitive)
        return any(target.lower() in market_lower for target in self.markets)

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

    def save_valuebet_to_csv(self, bet_data, filename="valuebet-swagger.csv"):
        file_exists = os.path.exists(filename)

        with open(filename, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=bet_data.keys())

            # Write header only if file is new
            if not file_exists:
                writer.writeheader()

            writer.writerow(bet_data)

    async def process_and_poll(self):
        try:
            all_bets = await self.fetch_valuebets_from_all_bookmakers()

            # Filter bets by sport, EV, AND market (sport-specific)
            filtered = []
            for bet in all_bets:
                ev = bet.get('expectedValue', 0)
                sport = bet.get('event', {}).get('sport', '').lower()
                market_name = bet.get('market', {}).get('name', '')
                
                # Check EV threshold
                if ev < self.min_ev:
                    continue
                
                # Check sport 
                if sport not in ('football', 'tennis'):
                    continue
                
                # Set markets based on sport and check if this bet's market is valid
                self.set_markets_for_sport(sport)
                if self.is_target_market(market_name):
                    filtered.append(bet)
            
            # Log filtered out markets for debugging
            filtered_out = {}
            for bet in all_bets:
                ev = bet.get('expectedValue', 0)
                sport = bet.get('event', {}).get('sport', '').lower()
                market_name = bet.get('market', {}).get('name', 'Unknown')
                
                if (ev >= self.min_ev 
                    and sport in ('football', 'tennis', 'basketball')):
                    self.set_markets_for_sport(sport)
                    if not self.is_target_market(market_name):
                        if sport not in filtered_out:
                            filtered_out[sport] = set()
                        filtered_out[sport].add(market_name)
            
            if filtered_out:
                for sport, markets in filtered_out.items():
                    logging.debug(f"Filtered out {sport} markets: {markets}")
            
            for bet in filtered:
                logging.info(f"----------------------------{bet}--------------------------")

                betside = bet.get("betSide") or bet.get("betside")

                # Map betside to bookmakerOdds key
                odds_key_map = {"home": "home", "away": "away", "draw": "draw", "over": "over", "under": "under"}
                selected_key = odds_key_map.get(betside)

                # Safely pick selected odds
                selected_odds = None
                if selected_key:
                    selected_odds = bet.get("bookmakerOdds", {}).get(selected_key)

                value_bet_data = {
                    "id": bet.get("id"),
                    "ev": round(bet.get("expectedValue", 0), 2),
                    "market_name": bet.get("market", {}).get("name", "Unknown"),
                    "bookmaker": bet.get("bookmaker", "Unknown"),
                    "betside": betside,
                    "selected_odds": selected_odds,
                    "event_id": bet.get("eventId"),
                    "event_home": bet.get("event", {}).get("home", "Unknown"),
                    "event_away": bet.get("event", {}).get("away", "Unknown"),
                    "event_sport": bet.get("event", {}).get("sport", "Unknown"),
                    "event_league": bet.get("event", {}).get("league", "Unknown"),
                }

                key = f"{value_bet_data['id']}-{value_bet_data['event_id']}-{value_bet_data['market_name']}"
                
                duplicate_found = False
                odds_update = False

                for seen_bet in self.seen_bets:
                    seen_bet_key = f"{seen_bet['id']}-{seen_bet['event_id']}-{seen_bet['market_name']}"
                    same_id = key == seen_bet_key

                    if same_id:
                        duplicate_found = True
                        if seen_bet['selected_odds'] != value_bet_data['selected_odds']:
                            odds_update = True
                            seen_bet['selected_odds'] = value_bet_data['selected_odds']
                            break
                        else:
                            return # exit

                    
                if not duplicate_found:
                    self.seen_bets.append(value_bet_data)
                    logging.info(f"🔔Value bet found @ {bet.get('bookmaker', 'Unknown')}\n{json.dumps(value_bet_data, indent=4)}\n")
                    
                    # Save to CSV
                    # Note that if it was a previous value bet data with updated ods, it won't be able to update the CSV with the new odds.
                    self.save_valuebet_to_csv(value_bet_data)

                if duplicate_found and odds_update:
                    logging.info(f"[@ {value_bet_data['bookmaker']}] Duplicate found and odds updated \n{json.dumps(value_bet_data, indent=4)}\n ")


        except Exception as e:
            logging.error(f"Error during polling: {e}")
            return []

    async def start(self):
        """Start the monitoring loop"""
        if self.is_running:
            logging.warning("Monitor already running")
            return

        logging.info(f"Starting monitor: {', '.join(self.bookmakers)}")
        logging.info(f"Min EV: {self.min_ev * 100:.2f}% | Interval: {self.interval_seconds}s")
        logging.info("Markets will be set dynamically based on sport")
        
        self.is_running = True
        await self.process_and_poll() 
        
        while self.is_running:
            # logging.info("Polling cycle triggered")
            await asyncio.sleep(self.interval_seconds)
            await self.process_and_poll()

    def stop(self):
        """Stop monitoring"""
        self.is_running = False
        logging.info("Monitor stopped")


async def main():
    api_key = os.getenv('ODDS_API_KEY')
    if not api_key:
        raise ValueError("Missing ODDS_API_KEY in environment variables")
    
    monitor = ValueBetMonitor(api_key=api_key,
                              bookmakers=['Duel'],
                              min_ev=0.01,  # 1%. Best practice as stated is threshold of 3-5%
                              interval_seconds=5)

    try:
        await monitor.start()
    except KeyboardInterrupt:
        monitor.stop()
        logging.info("Stopped by user")


if __name__ == "__main__":
    asyncio.run(main())