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

            # Filter bets
            filtered = [bet for bet in all_bets if bet.get('expectedValue', 0) >= self.min_ev and bet.get('event', {}).get('sport', '').lower() in ('football', 'tennis')]
            
            for bet in filtered:
                value_bet_data = {
                    'id': bet.get('id', None),
                    'ev': round(bet.get('expectedValue', 0), 2),
                    'market_name': bet.get('market', {}).get('name', 'Unknown'),
                    'bookmaker': bet.get('bookmaker', 'Unknown'),
                    'bookmaker_odds_home': bet.get('bookmakerOdds', {}).get('home', None),
                    'bookmaker_odds_away': bet.get('bookmakerOdds', {}).get('away', None),
                    'bookmaker_odds_draw': bet.get('bookmakerOdds', {}).get('draw', None),
                    'event_id': bet.get('eventId', None),
                    'event_home': bet.get('event', {}).get('home', 'Unknown'),
                    'event_away': bet.get('event', {}).get('away', 'Unknown'),
                    'event_sport': bet.get('event', {}).get('sport', 'Unknown'),
                    'event_league': bet.get('event', {}).get('league', 'Unknown')
                }
                
                key = f"{value_bet_data['id']}-{value_bet_data['event_id']}-{value_bet_data['market_name']}"
                
                duplicate_found = False
                odds_update = False

                for seen_bet in self.seen_bets:
                    seen_bet_key = f"{seen_bet['id']}-{seen_bet['event_id']}-{seen_bet['market_name']}"
                    same_id = key == seen_bet_key

                    if same_id:
                        duplicate_found = True
                        for side in ['home', 'away', 'draw']:
                            odds_field = f'bookmaker_odds_{side}'
                            if seen_bet[odds_field] != value_bet_data[odds_field]:
                                seen_bet[odds_field] = value_bet_data[odds_field]
                                odds_update = True
                        if odds_update:
                            logging.info(f"[@ {value_bet_data['bookmaker']}] Duplicate found and odds updated \n{json.dumps(value_bet_data, indent=4)}\n ")
                        break  # stop looping, we found the duplicate
                    
                if not duplicate_found:
                    self.seen_bets.append(value_bet_data)
                    logging.info(f"🔔Value bet found @ {bet.get('bookmaker', 'Unknown')}\n{json.dumps(value_bet_data, indent=4)}\n")
                    
                    # Save to CSV
                    # Note that if it was a previous value bet data with updated ods, it won't be able to update the CSV with the new odds.
                    self.save_valuebet_to_csv(value_bet_data)

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
                              min_ev=0.05,  # 5%. Best practice as stated is threshold of 3-5%
                              interval_seconds=5)

    try:
        await monitor.start()
    except KeyboardInterrupt:
        monitor.stop()
        logging.info("Stopped by user")


if __name__ == "__main__":
    asyncio.run(main())