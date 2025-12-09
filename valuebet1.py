import asyncio
import aiohttp
import os
from datetime import datetime, timedelta
import random
import pytz
from typing import List, Dict
import json
import logging
import csv
from pprint import pprint
import dotenv
import requests
import pygsheets
dotenv.load_dotenv()
# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='[%(asctime)s] %(levelname)s: %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S',
                    filename='valuebet_1.log')

class ValueBetMonitor:
    def __init__(self, api_key: str, bookmakers, min_ev, interval_seconds: int, input_data: dict, worksheet, sent_bets):
        self.api_key = api_key
        self.bookmakers = bookmakers
        self.min_ev = min_ev
        self.interval_seconds = interval_seconds
        self.input_data = input_data
        self.worksheet = worksheet
        self.is_running = True
        self.sport_list = ("football", "basketball", 'baseball', "american football", "ice hockey", "esports", "handball", "rugby", "volleyball", 'badminton', 'beach soccer', 'beach volleyball', 'table tennis')
        self.seen_bets = sent_bets or []
        self.markets = ["Spread", "ML", "Totals", "Totals HT", "Asian Handicap", "Asian Handicap HT", "Team Total Home",
                        "Team Total Away", "Team Total Home HT", "Team Total Away HT", "ML HT", "Spread HT", "Totals (Games)", "Spread (Games)", "Totals 1st Set (Games)", "Spread 1st Set (Games)", "ML 1st Set"]

        print('================================================================================')
        print(f"Processing Valuebets for following markets: {self.markets}")
        print('================================================================================')



    def is_target_market(self, market_name: str) -> bool:
        if not market_name:
            return False

        market_lower = market_name.lower()
        target_markets = [m.lower() for m in self.markets]

        return market_lower in target_markets

    def fetch_valuebets_from_all_bookmakers(self) -> List[Dict]:
        # Fetch value bets from all bookmakers synchronously
        
        bets = []
        for bookmaker in self.bookmakers:
            try:
                url = f"https://api.odds-api.io/v3/value-bets?apiKey={self.api_key}&bookmaker={bookmaker}&includeEventDetails=true"
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, list):
                        bets.extend(data)
                    else:
                        bets.append(data)
                else:
                    logging.error(f"Failed to fetch for bookmaker {bookmaker}: {response.status_code}")
            except Exception as e:
                logging.error(f"Error: {e}")
        return bets

    # async def fetch_valuebets(self, session: aiohttp.ClientSession, bookmaker: str) -> List[Dict]:
    #     # Connect to provided endpoint
    #     try:
    #         url = f"https://api.odds-api.io/v3/value-bets?apiKey={self.api_key}&bookmaker={bookmaker}&includeEventDetails=true"
    #         async with session.get(url) as response:
    #             return await response.json() if response.status == 200 else []
    #     except Exception as e:
    #         logging.error(f"Error fetching {bookmaker}: {e}")
    #         return []

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
            logging.info("Polling for value bets...")
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Polling for value bets...")

            all_bets = self.fetch_valuebets_from_all_bookmakers()
            logging.info(f"Fetched {len(all_bets)} bets from API")
            # print("--------------------------------")
            # pprint(all_bets)

            # if all_bets:
            #     with open("allbets.csv", "a", newline="", encoding="utf-8") as f:
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
                event_start_time = bet.get('event', {}).get('date', '')
                
                if sport.lower() not in self.sport_list:
                    # logging.info(f"Skipped: Sport '{sport}' not in target sports")
                    continue

                if not self.is_target_market(market_name):
                    # logging.info(f"Skipped: Market '{market_name}' not in target markets")
                    continue

                # Check EV threshold
                if ev < self.input_data['min_value_percentage']:
                # if ev < 0:
                    logging.info(f"Skipped: EV {ev}% below minimum {self.input_data['min_value_percentage']}%")
                    print(f"Skipped: EV {ev}% below minimum {self.input_data['min_value_percentage']}%")
                    continue

                

                if not is_less_than_24_hours_away(event_start_time):
                    logging.info(f"Skipped: Event start time {event_start_time} is more than 24 hours away")
                    continue

                if not should_process_event(sport, event_start_time):
                    logging.info(f"Skipped: Event start time {event_start_time} is not in target time range")
                    continue

                totals_markets = ("totals", "totals ht", "team total home", "team total away", "team total home ht", "team total away ht")
                if market_name.lower() in totals_markets:
                    odds = bet["bookmakerOdds"]
                    odds["over"] = odds.pop("home")
                    odds["under"] = odds.pop("away")
                    reference_odds = bet['market']
                    reference_odds["over"] = reference_odds.pop("home")
                    reference_odds["under"] = reference_odds.pop("away")

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

                odds = 1.0
                reference_odds = 1.0
                if selected_key:
                    try:
                        odds = float(bet.get("bookmakerOdds", {}).get(selected_key))
                    except:
                        print('Bookmaker Odds Error here:')
                        print(bet)
                        odds = 1.0
                    try:
                        reference_odds = float(bet.get("market", {}).get(selected_key))
                    except:
                        print('Market Error here:')
                        print(bet)
                        reference_odds = 1.0

                if self.input_data['min_prematch_odd'] > odds or odds > self.input_data['max_prematch_odd']:
                    logging.info(f"Skipped: Odds {odds} is not in the range {self.input_data['min_prematch_odd']} to {self.input_data['max_prematch_odd']}")
                    print(f"Skipped: Odds {odds} is not in the range {self.input_data['min_prematch_odd']} to {self.input_data['max_prematch_odd']}")
                    continue

                temp_ev = bet.get('expectedValue', 0)
                ev = round(temp_ev - 100, 2)
                updated_at = bet.get("expectedValueUpdatedAt")
                if not is_within_one_minute(updated_at):
                    # logging.info(f"Skipped: Expected value updated at {updated_at} is not within the last 1 minute")
                    continue

                value_bet_data = {
                    "sport": bet.get("event", {}).get("sport", "Unknown"),
                    "league": bet.get("event", {}).get("league", "Unknown"),
                    "home": bet.get("event", {}).get("home", "Unknown"),
                    "away": bet.get("event", {}).get("away", "Unknown"),
                    "market_name": bet.get("market", {}).get("name", "Unknown"),
                    "odds": odds,
                    "sharp_odds": reference_odds,
                    "ev": ev,
                    "betside": betside,
                    "event_id": bet.get("eventId", 0),
                    "updated_at": updated_at,
                    "found_valuebet_at": datetime.now(pytz.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                }
                logging.info(value_bet_data)

                hdp_markets = ("spread", "asian handicap", "spread ht", "asian handicap ht", "team total home", "team total away", "team total home ht", "team total away ht", 'totals', 'totals ht')

                if bet.get("market", {}).get("name", "Unknown").lower() in hdp_markets:
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
                    value_bet_data['id'] = bet.get("id")
                    value_bet_data['bookmaker'] = bet.get("bookmaker", "Unknown")
                    # logging.info(f"🔔Value bet found @ {bet.get('bookmaker', 'Unknown')}\n{json.dumps(value_bet_data, indent=4)}\n")
                    value_info = f'''🔔Value bet found @ {value_bet_data['sport']} | {value_bet_data['league']} | {value_bet_data['home']} vs {value_bet_data['away']} 
                    Bookmaker: {value_bet_data['bookmaker']} | Market: {value_bet_data['market_name']} | Selection: {value_bet_data['betside']} | Line: {value_bet_data['hdp']} | Odds: {value_bet_data['odds']} | EV: {value_bet_data['ev']}%                     
                    '''
                    print("---------------------------------------------------------------------------------------------------------------------")
                    print(value_info)
                    
                    self.seen_bets.append(value_bet_data)
                    logging.info(bet)
                    
                    # Save to CSV
                    # self.save_valuebet_to_csv(value_bet_data)
                    alert_data = [value_bet_data['sport'], value_bet_data['league'], value_bet_data['home'], value_bet_data['away'], value_bet_data['event_id'], value_bet_data['bookmaker'], value_bet_data['market_name'], 
                                        value_bet_data['betside'], value_bet_data['hdp'], value_bet_data['odds'], value_bet_data['sharp_odds'], value_bet_data['ev'], value_bet_data['updated_at'], value_bet_data['found_valuebet_at']]
                    update_log_to_sheet([alert_data], self.worksheet)

        except Exception as e:
            error_msg = f"Error during polling: {e}"
            logging.error(error_msg, exc_info=True)
            print(f"ERROR: {error_msg}")
            import traceback
            traceback.print_exc()
            

    # async def start(self):
    #     # Start the monitoring loo
    #     while True:
    #         await asyncio.sleep(self.interval_seconds)
    #         await self.process_and_poll()

    def stop(self):
        # Stop monitoring
        self.is_running = False
        logging.info("Monitor stopped")




async def main(input_data: dict, worksheet, sent_bets):
    api_key = os.getenv('ODDS_API_KEY')
    if not api_key:
        raise ValueError("Missing ODDS_API_KEY in environment variables")
    
    monitor = ValueBetMonitor(api_key=api_key,
                              bookmakers=['Duel'],
                              min_ev=1,  # 1%.
                              interval_seconds=7,
                              input_data=input_data,
                              worksheet=worksheet,
                              sent_bets=sent_bets)
    
    print(f"Starting ValueBetMonitor - polling every {monitor.interval_seconds} seconds...")
    print("Press Ctrl+C to stop")
    print("=" * 80)
    
    while monitor.is_running:
        for _ in range(100):
            try:
                await monitor.process_and_poll()
            except KeyboardInterrupt:
                print("\nReceived interrupt signal, stopping monitor...")
                monitor.stop()
                break
            except Exception as e:
                error_msg = f"Unexpected error in main loop: {e}"
                logging.error(error_msg, exc_info=True)
                print(f"ERROR: {error_msg}")
            
            await asyncio.sleep(monitor.interval_seconds)
        await asyncio.sleep(120)
    


def is_within_one_minute(time_str):
    """Returns True if the given UTC time string is within the last minute from now, else False."""
    

    # Accepts both with and without milliseconds
    time_formats = ["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"]
    given_time = None
    for fmt in time_formats:
        try:
            given_time = datetime.strptime(time_str, fmt).replace(tzinfo=pytz.UTC)
            break
        except ValueError:
            continue
    if given_time is None:
        raise ValueError(f"Time string {time_str} not in recognized ISO format")
    now = datetime.now(pytz.UTC)
    delta = now - given_time
    return timedelta(0) <= delta <= timedelta(minutes=2)

def is_less_than_24_hours_away(time_str):
    # Parse the input time string into a datetime object
    time_format = "%Y-%m-%dT%H:%M:%SZ"
    given_time = datetime.strptime(time_str, time_format).replace(tzinfo=pytz.UTC)

    # Get the current time in UTC
    current_time = datetime.now(pytz.UTC)
    
    # Calculate the time difference
    time_difference = given_time - current_time
    
    # Check if the time difference is less than 24 hours
    return time_difference < timedelta(hours=48)


def should_process_event(sport, time_str):
    time_format = "%Y-%m-%dT%H:%M:%SZ"
    given_time = datetime.strptime(time_str, time_format).replace(tzinfo=pytz.UTC)
    current_time = datetime.now(pytz.UTC)
    time_until_event_start = given_time - current_time

    if (
        (sport == "tennis" and time_until_event_start > timedelta(minutes=45)) \
        or (sport.lower() in ("football", "basketball", 'baseball', "american football", "ice hockey", "esports", "handball", "rugby", "volleyball", 'badminton', 'beach soccer', 'beach volleyball', 'table tennis') and time_until_event_start > timedelta(minutes=2))):
        return True  # process event
    return False  # skip event


def update_log_to_sheet(alert_list, worksheet):
    rows = worksheet.get_all_values(include_tailing_empty=True, include_tailing_empty_rows=False, returnas='matrix')
    row_index = len(rows)+1
    no_of_alerts = len(alert_list)
    worksheet.update_values(f"A{row_index}:N{row_index+no_of_alerts}", alert_list) 


if __name__ == "__main__":
    gc = pygsheets.authorize(service_file='google_client.json')
    sht1 = gc.open_by_key('1hhb-Gr-Rh1DniTBISgVYStec59E8s0YEYm-Xl-MZ3vA')
    wks1 = sht1.worksheet_by_title('Input')
    wks2 = sht1.worksheet_by_title('valuebet_system_1')
    
    #Get user defined inputs
    [[min_prematch_odd, max_prematch_odd, min_live_odd, max_live_odd, kelly_fraction, min_value_percentage]] = wks1.get_values('A2','F2')

    temp_rows = wks2.get_all_values(include_tailing_empty=True, include_tailing_empty_rows=False, returnas='matrix')
    sent_bets = [{'event_id': int(row[4])} for row in temp_rows[1:]]
    
    input_data = {}
    input_data['min_prematch_odd'] = float(min_prematch_odd)
    input_data['max_prematch_odd'] = float(max_prematch_odd)
    input_data['min_live_odd'] = float(min_live_odd)
    input_data['max_live_odd'] = float(max_live_odd)
    input_data['kelly_fraction'] = float(kelly_fraction)
    input_data['min_value_percentage'] = float(min_value_percentage)

    # pprint(input_data)
    asyncio.run(main(input_data, wks2, sent_bets))