import os
import json
import logging
import time
from datetime import datetime, timedelta
import websocket
import requests
import pytz
# import dotenv
# dotenv.load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),          
        logging.FileHandler("OddsFinder.log")       
    ])
logger = logging.getLogger("OddsFinder")

MIN_VALUE = 1.0 
MIN_BET_ODDS = 1.2
MAX_BET_ODDS = 3.0  


class OddsFinder:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.bookmakers = ["Duel", "Pinnacle"]
        self.markets = ["Spread", "ML", "Totals", "Totals HT", "Asian Handicap", 
                       "Asian Handicap HT", "Team Total home", "Team Total away", 
                       "Team Total home HT", "Team Total away HT", "ML HT", "Spread HT",
                       "Totals (Games)", "Spread (Games)"]
        self.odds_store = []
        self.value_events = []

    def on_open(self, ws):
        logger.info("WebSocket connected")

    def on_message(self, ws, message):
        try:
            # logger.info(f"Raw message received: {message[:25]}")
            lines = message.strip().split('\n')
            # logger.info(f"Split into {len(lines)} lines")
            for line in lines:
                if not line.strip():
                    continue

                try:
                    data = json.loads(line)
                    # logger.info(f"Parsed JSON: {data}")  

                except json.JSONDecodeError:
                    logger.error(f"Failed to parse: {line[:100]}")
                    continue

                event_id = data.get("id")
                if not event_id:
                    logger.debug(f"No event_id found in data. Keys present: {list(data.keys())}")
                    continue

                self.handle_event_message(data)

        except Exception as e:
            logger.error(f"on_message error: {e}", exc_info=True)

    def handle_event_message(self, data):
        event_id = data.get("id")
        bookie = data.get("bookie")

        if bookie not in self.bookmakers:
            return
        
        if data.get("type") not in ("created", "updated"):
            return

        for market in data.get("markets", []):
            market_name = market.get("name")
            
            if market_name not in self.markets:
                continue

            for entry in market.get("odds", []):
                hdp = entry.get("hdp")

                for key, value in entry.items():
                    if key not in ("home", "away", "draw", "over", "under"):
                        continue

                    if bookie.lower() == "duel":
                        if float(value) < MIN_BET_ODDS or float(value) > MAX_BET_ODDS:
                            return 

                    record = {
                        "event_id": event_id,
                        "bookie": bookie, #different for Pinnacle and Duel
                        "market": market_name,
                        "selection": key,
                        "price": float(value), #different for Pinnacle and Duel
                        "hdp": hdp,
                    }

                    if hdp is None:
                        uid = f"{event_id}-{key}-{market_name}"
                    else:
                        uid = f"{event_id}-{key}-{market_name}-{hdp}"

                    record["uid"] = uid
                    self.process_bets(record)

    def process_bets(self, record):
        if record['event_id'] in self.value_events:
            # logger.info(f"Skipping event {record['event_id']} - value bet already found")
            return

        duplicate = False
        odds_update = False

        for stored_record in self.odds_store:
                same_id = (stored_record["uid"] == record["uid"] and stored_record["bookie"]  == record["bookie"]) 

                if same_id:
                    duplicate = True
                    if stored_record["price"] != record["price"]:
                        odds_update = True
                        stored_record["price"] = record["price"]
                        break
                    else:
                        return

        if duplicate and odds_update:
            self.compare_odds(record)
        elif not duplicate:
            self.odds_store.append(record)
            self.compare_odds(record)

    def compare_odds(self, record):
        event_id = record.get("event_id")
        selection = record.get("selection")
        market_name = record.get("market")
        # logger.info(f"{event_id}-{selection}-{market_name}")

        related_entries = [entry for entry in self.odds_store if entry.get("uid") == record.get("uid")]
        sportsbooks_data = {entry["bookie"]: entry for entry in related_entries}

        # logger.info(self.odds_store[:5])

        # logger.info(f"event_id={event_id}, selection={selection}, market={market_name}")
        # logger.info(f"related_entries={json.dumps(related_entries, indent=2)}")
        # logger.info(f"sportsbooks_data keys={list(sportsbooks_data.keys())}")
        if not all(sb in sportsbooks_data for sb in ["Duel", "Pinnacle"]):
            return None

        logger.info('==================================================================')
        logger.info('Found both Duel and Pinnacle odds pair....')
        duel_odds = sportsbooks_data["Duel"]["price"]
        pinnacle_odds = sportsbooks_data["Pinnacle"]["price"]
        value = calculate_value(duel_odds, pinnacle_odds) #value returned in percentage

        #feat: Implement minimum_value constant, so if value is below the Min_Value, skip it
        if float(value) < MIN_VALUE:
            logger.info(f"Skipping game. {value} is below minimum value {MIN_VALUE})")
            return None

        sport, league, home, away, start_time = self.fetch_event_details(event_id) #This is the farthest place we can pull this API
        logger.info(f"[{sport}] Value bet found for Duel with value: {value}%")
        duel_entry = sportsbooks_data["Duel"]
        pinnacle_entry = sportsbooks_data["Pinnacle"]

        # Add API details to both entries
        duel_entry.update({
            "sport": sport,
            "league": league,
            "home": home,
            "away": away,
            "start_time": start_time
        })

        pinnacle_entry.update({
            "sport": sport,
            "league": league,
            "home": home,
            "away": away,
            "start_time": start_time
        })
        
        home = duel_entry.get("home")
        away = duel_entry.get("away")
        start_time = duel_entry.get("start_time")

        if is_less_than_24_hours_away(start_time): #Ensures only events starting within the next 24 hours are considered.
            if should_process_event(sport, start_time): # Filters out events that are too close to kickoff:
                # For tennis, skip if it's starting in <45 min. For football, skip if it's starting in <2 min

                # duel value bet
                duel_value_bet = {
                    "home_name": home,
                    "away_name": away,
                    "league": duel_entry.get("league"),
                    "market_name": duel_entry.get("market"),
                    "selection": duel_entry.get("selection"),
                    "odds": duel_entry.get("price"),
                    "sportsbook": "duel",
                    "sport": duel_entry.get("sport"),
                    "hdp": duel_entry.get("hdp"),
                }

                # Pinnacle value bet
                pinnacle_value_bet = {
                    "home_name": home,
                    "away_name": away,
                    "league": pinnacle_entry.get("league"),
                    "market_name": pinnacle_entry.get("market"),
                    "selection": pinnacle_entry.get("selection"),
                    "odds": pinnacle_entry.get("price"),
                    "sportsbook": "pinnacle",
                    "sport": pinnacle_entry.get("sport"),
                    "hdp": pinnacle_entry.get("hdp")
                }

                # print at once so it doesn't mix up
                logger.info(f"[{sport}] Value bet pair found\n"
                            f"----- VALUE BET SIDE (duel) -----\n"
                            f"{json.dumps(duel_value_bet, indent=2, ensure_ascii=False)}\n"
                            f"----- CORRESPONDING PINNACLE SIDE -----\n"
                            f"{json.dumps(pinnacle_value_bet, indent=2, ensure_ascii=False)}\n"
                            f"------------------------------------------")
                
                self.value_events.append(event_id)
                return duel_value_bet #not returning Pinnacle data
            else:
                logger.info(f"[{sport}] kickout too soon @ {start_time}")
        else:
            logger.info(f"[{sport}] Event isn't starting in the next 24 hours @ {start_time}")

    def fetch_event_details(self, event_id: str, max_retries: int = 5):
        event_url = f"https://api.odds-api.io/v3/events/{event_id}"
        params = {"apiKey": self.api_key}
        
        retry_delay = 1  # start with 1 second
        for attempt in range(1, max_retries + 1):
            try:
                response = requests.get(
                    event_url, 
                    params=params, 
                    timeout=5
                )
                
                if response.status_code == 429:
                    logger.warning(f"Rate limit hit for event {event_id}, retrying in {retry_delay}s (attempt {attempt}/{max_retries})")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # exponential backoff
                    continue

                response.raise_for_status()
                event = response.json()

                sport = event.get('sport', {}).get('name', 'Unknown')
                league = event.get('league', {}).get('name', 'Unknown')
                home = event.get('home', None)
                away = event.get('away', None)
                date = event.get('date', None)

                return sport, league, home, away, date

            except requests.RequestException as e:
                logger.error(f"API request error for event {event_id}: {e}")
                if attempt == max_retries:
                    return "Unknown", "Unknown", "Unknown", "Unknown", None
                time.sleep(retry_delay)
                retry_delay *= 2
            except Exception as e:
                logger.error(f"Error fetching event {event_id}: {e}", exc_info=True)
                if attempt == max_retries:
                    return "Unknown", "Unknown", "Unknown", "Unknown", None
                time.sleep(retry_delay)
                retry_delay *= 2

        # If we exit the loop without returning, retries failed
        logger.error(f"Max retries reached for event {event_id}")
        return "Unknown", "Unknown", "Unknown", "Unknown", None


    def on_error(self, ws, error):
        logger.error(f"WebSocket Error: {error}")

    def on_close(self, ws, close_status_code, close_msg):
        logger.info("WebSocket closed")

    def build_ws_url(self) -> str:
        return f"wss://api.odds-api.io/v3/ws?apiKey={self.api_key}&sport=football,tennis&status=prematch"
        
    def start(self):
        backoff = 1
        while True:
            try:
                ws = websocket.WebSocketApp(
                    self.build_ws_url(),
                    on_open=self.on_open,
                    on_message=self.on_message,
                    on_error=self.on_error,
                    on_close=self.on_close
                )
                ws.run_forever(ping_interval=30, ping_timeout=10)
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
            except Exception as e:
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)


def calculate_value(slower_odds, sharp_odds):
    value = (float(slower_odds) - float(sharp_odds))*100/(float(sharp_odds))
    value = round(value, 2)
    return value


def is_less_than_24_hours_away(time_str):
    # Parse the input time string into a datetime object
    time_format = "%Y-%m-%dT%H:%M:%SZ"
    given_time = datetime.strptime(time_str, time_format).replace(tzinfo=pytz.UTC)

    # Get the current time in UTC
    current_time = datetime.now(pytz.UTC)
    
    # Calculate the time difference
    time_difference = given_time - current_time
    
    # Ensure event is in the future and less than 24 hours away
    return timedelta(0) < time_difference <= timedelta(hours=24)


def should_process_event(sport, time_str):
    time_format = "%Y-%m-%dT%H:%M:%SZ"
    given_time = datetime.strptime(time_str, time_format).replace(tzinfo=pytz.UTC)
    current_time = datetime.now(pytz.UTC)
    time_until_event_start = given_time - current_time

    # Skip events that have already started
    if time_until_event_start <= timedelta(0):
        return False

    if (sport.lower() == "tennis" and time_until_event_start > timedelta(minutes=45)) \
       or (sport.lower() == "football" and time_until_event_start > timedelta(minutes=2)):
        return True
    
    return False


def main():
    api_key = os.getenv("ODDS_API_KEY")
    if not api_key:
        raise ValueError("Missing API key.")

    finder = OddsFinder(api_key)
    finder.start()


if __name__ == "__main__":
    main()
    