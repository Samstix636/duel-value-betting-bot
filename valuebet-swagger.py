import asyncio
import aiohttp
import os
from datetime import datetime
from typing import List, Dict


class ValueBetMonitor:
    def __init__(self, api_key: str, bookmakers: List[str], min_ev: float, interval_seconds: int):
        self.api_key = api_key
        self.bookmakers = bookmakers
        self.min_ev = min_ev
        self.interval_seconds = interval_seconds
        self.value_bets = []
        self.is_running = False

    async def fetch_valuebets_from_all_bookmakers(self) -> List[Dict]:
        """Fetch value bets from all bookmakers in parallel"""
        async with aiohttp.ClientSession() as session:
            tasks = [self.fetch_valuebets(session, bookmaker) for bookmaker in self.bookmakers]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Flatten and filter out errors
            bets = []
            for result in results:
                if isinstance(result, list):
                    bets.extend(result)
                elif isinstance(result, Exception):
                    print(f"Error: {result}")
            return bets

    async def fetch_valuebets(self, session: aiohttp.ClientSession, bookmaker: str) -> List[Dict]:
        """Fetch from single bookmaker"""
        try:
            url = f"https://api.odds-api.io/v3/value-bets?apiKey={self.api_key}&bookmaker={bookmaker}&includeEventDetails=true"
            async with session.get(url) as response:
                return await response.json() if response.status == 200 else []
        except Exception as e:
            print(f"Error fetching {bookmaker}: {e}")
            return []

    def process_bets(self, bets: List[Dict]) -> List[Dict]:
        """Deduplicate, filter by EV & sport, and sort"""
        
        # Deduplicate
        seen_bets = set()
        unique = []
        for bet in bets:
            key = (bet.get("id"), bet.get("eventId"), bet.get("market", {}).get("name"))
            if key not in seen_bets:
                seen_bets.add(key)
                unique.append(bet)

        # Filter and sort
        filtered = [
            bet for bet in unique
            if bet.get('expectedValue', 0) >= self.min_ev
            and bet.get('event', {}).get('sport', '').lower() in ('football', 'tennis')
        ]
        return sorted(filtered, key=lambda x: x.get('expectedValue', 0), reverse=True)

    async def poll(self):
        """Single polling iteration"""
        try:
            all_bets = await self.fetch_valuebets_from_all_bookmakers()
            self.value_bets = self.process_bets(all_bets)
            
            timestamp = datetime.now().isoformat()
            print(f"[{timestamp}] Found {len(self.value_bets)} value bets")
            
            # Notify for each bet
            for bet in self.value_bets:
                ev = bet.get('expectedValue', 0)
                bookmaker = bet.get('bookmaker', 'Unknown')
                event = bet.get('event', {})
                market = bet.get('market', {})
                selection = bet.get('selection', 'Unknown')
                
                print(f"🔔 {bookmaker} - EV: {ev:.2f}%")
                print(f"📊 {event} | {market} | {selection}")
            
            return self.value_bets
        except Exception as e:
            print(f"Error during polling: {e}")
            return []

    async def start(self):
        """Start the monitoring loop"""
        if self.is_running:
            print("Monitor already running")
            return

        print(f"Starting monitor: {', '.join(self.bookmakers)}")
        print(f"Min EV: {self.min_ev * 100:.2f}% | Interval: {self.interval_seconds}s")
        
        self.is_running = True
        await self.poll()  # Initial fetch
        
        while self.is_running:
            await asyncio.sleep(self.interval_seconds)
            await self.poll()

    def stop(self):
        """Stop monitoring"""
        self.is_running = False
        print("Monitor stopped")


async def main():
    api_key = os.getenv('ODDS_API_KEY')
    if not api_key:
        raise ValueError("Missing ODDS_API_KEY in environment variables")
    
    monitor = ValueBetMonitor(
        api_key=api_key,
        bookmakers=['Duel', 'Pinnacle'],
        min_ev=0.005,  # 0.5%
        interval_seconds=5
    )
    
    try:
        await monitor.start()
    except KeyboardInterrupt:
        monitor.stop()
        print("Stopped by user")


if __name__ == "__main__":
    asyncio.run(main())