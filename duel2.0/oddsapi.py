import os
import json
import logging
import time
import threading
from datetime import datetime, timedelta
import websocket
import requests
from dotenv import load_dotenv
from helper import is_less_than_24_hours_away, transpose_duel_market_name, is_within_minute, is_event_started

load_dotenv()
# Create logs directory
os.makedirs("logs", exist_ok=True)
logger = logging.getLogger("OddsAPI")
logger.setLevel(logging.INFO)

# Prevent propagation to parent loggers
logger.propagate = False

# Clear any existing handlers to ensure clean setup
logger.handlers.clear()

# Dedicated file for this module
handler = logging.FileHandler("logs/oddsapi.log")
formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
handler.setFormatter(formatter)
logger.addHandler(handler)

# MIN_VALUE = 1.0 
# MIN_BET_ODDS = 1.2
# MAX_BET_ODDS = 3.0  


class OddsAPIStreamClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.bookmakers = ["Duel"]
        self.markets = ["Spread","3Way", "ML", "Totals", "ML_HT", "Totals_HT", "Asian_Handicap", 
                       "Asian_Handicap_HT"]
        self.alloddsapievent: list[dict] = []
        self.upcoming_event_ids = []
        self.oddsapievent: list[dict] = []
        self.should_reconnect = True
        self.lock = threading.Lock()  
        self.thread = None 
        self.sports = ['football', 'basketball', 'baseball', 'ice-hockey', 'american-football', 'volleyball', 'esports']
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 3

    def get_upcoming_event_ids(self):
        """Fetch all events and return IDs of those in the next 24 hours"""
        # Fetch all events from all sports
        from datetime import datetime, timedelta, timezone
        self.alloddsapievent.clear()
        now = datetime.now(timezone.utc)
        from_time = (now + timedelta(minutes=10)).isoformat(timespec='seconds').replace("+00:00", "Z")
        to_time = (now + timedelta(hours=48)).isoformat(timespec='seconds').replace("+00:00", "Z")
        for sport in self.sports:
            url = f"https://api.odds-api.io/v3/events?apiKey={self.api_key}&sport={sport}&status=pending&bookmaker=Duel"
            logger.info(f"OddsAPI upcoming URL: {url}")
            response = requests.get(url, timeout=30)
            # logger.info(f"OddsAPI Response: {response.json()}")
            response.raise_for_status()

            events = response.json()
            if isinstance(events, list):
                self.alloddsapievent.extend(events)
            time.sleep(2)
        # logger.info(self.alloddsapievent)
        # logger.info("___________________________")
        logger.info(f"Number of All OddsAPI events: {len(self.alloddsapievent)}")
        
        # Filter for events in next 24 hours and collect IDs
        for event in self.alloddsapievent:
            date_str = event.get("date")
            # logger.info(event.get("date"))
            if date_str and is_less_than_24_hours_away(date_str):
                self.upcoming_event_ids.append(event['id'])

    def update_event(self, event_id):
        url = f"https://api.odds-api.io/v3/events/{event_id}?apiKey={self.api_key}"
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        event = response.json()
        self.alloddsapievent.append(event)
        return event
        # logger.info(self.upcoming_event_ids)

    def start_periodic_refresh(self, interval_hours=2):
        """Start a background thread that refreshes events list"""
        def refresh_loop():
            while self.should_reconnect:
                try:
                    self.get_upcoming_event_ids()
                    # logger.info(f"Next refresh in {interval_hours} hours")
                except Exception as e:
                    logger.error(f"Error in periodic refresh: {e}", exc_info=True)
                
                # Sleep in smaller intervals to allow graceful shutdown
                for _ in range(interval_hours * 60):  # Check every minute
                    if not self.should_reconnect:
                        break
                    time.sleep(60)
        
        self.refresh_thread = threading.Thread(target=refresh_loop, daemon=True)
        self.refresh_thread.start()
        logger.info(f"Started periodic refresh thread (every {interval_hours} hours)")
        
    def build_ws_url(self) -> str:
        markets = f"{','.join(self.markets)}"
        sports = f"{','.join(self.sports)}"
        url = f"wss://api.odds-api.io/v3/ws?apiKey={self.api_key}&status=prematch&sports={sports}&markets={markets}"
        logger.info(f"OddsAPI WebSocket URL: {url}")
        return url
    
    def start_threaded(self):
        """Start the WebSocket in a background thread"""
        logger.info("Starting OddsAPI stream in background thread")
        self.thread = threading.Thread(target=self.start, daemon=True)
        self.thread.start()
        return self.thread
        
    def start(self):
        """Start the WebSocket client"""
        logger.info("WebSocket connection is starting")
        self.connect()

    def connect(self):
        """Connect to the WebSocket server"""
        logger.info("Connecting to WebSocket server")
        self.ws = websocket.WebSocketApp(
            self.build_ws_url(),
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close
        )
        self.ws.run_forever()

    def stop(self):
        """Stop the WebSocket client"""
        self.should_reconnect = False
        if self.ws:
            self.ws.close()

    def on_open(self, ws):
        logger.info("WebSocket connection opened")
        # Reset reconnect attempts on successful connection
        self.reconnect_attempts = 0

    def on_message(self, ws, message):
        try:
            lines = message.strip().split('\n')
            for line in lines:
                if not line.strip():
                    continue

                try:
                    data = json.loads(line)
                    # logger.info(data)
                except json.JSONDecodeError:
                    logger.error(f"Failed to parse: {line[:100]}")
                    continue

                event_id = data.get("id")
                sportsbook = data.get("bookie")

                # logger.info(event_id)
                if sportsbook not in self.bookmakers:
                    return
                # if event_id not in self.upcoming_event_ids:
                #     # logger.info(f"event id {event_id} for bookmaker {data.get("bookie")} not in the next 24 hours")
                #     continue
                if data.get("type") == "deleted":
                    logger.info("-----------------Event deleted")
                    logger.info(data)
                    continue
                
                if data.get("type") not in ("created", "updated"):
                    continue
                
                if not event_id:
                    logger.debug(f"No event_id found in data. Keys present: {list(data.keys())}")
                    continue

                self.handle_event_message(data)

        except Exception as e:
            logger.error(f"on_message error: {e}", exc_info=True)

    def handle_event_message(self, data):
        event_id = data.get("id")
        # logger.info(f"OddsAPI data: {data}")
        # Find the full event data from alloddsapievent
        event_data = next(
        (event for event in self.alloddsapievent if str(event.get("id")) == str(event_id)),
        None
    )

        if not event_data:
            # logger.info(f"Event {data} not found in alloddsapievent")
            event_data = self.update_event(event_id)
            if not event_data:
                logger.info(f"Event {event_id} not found in alloddsapievent")
                return
            
        
        sport = event_data.get("sport", {}).get("slug")
        league = event_data.get("league", {}).get("slug")
        home = event_data.get("home")
        away = event_data.get("away")
        date = event_data.get("date")
        if not is_less_than_24_hours_away(date) or is_event_started(date):
            return

        if len(self.oddsapievent) > 200000:
            for event in self.oddsapievent:
                if is_event_started(event.get("when_utc")):
                    self.oddsapievent.remove(event)
                    logger.info(f"Removed started event: {event}")
        # logger.info(event_data)

        for market in data.get("markets", []):
            market_name = market.get("name")
            market_name = transpose_duel_market_name(market_name, sport)

            updated_at = market.get("updatedAt")
            
            # if market_name not in self.markets:
            #     continue

            for entry in market.get("odds", []):
                hdp = entry.get("hdp")
                # if hdp is not None:
                #     if len(entry.items()) < 3:
                #         continue

                for key, value in entry.items():
                    if key not in ("home", "away", "draw", "over", "under"):

                        continue

                    try:
                        float(value)
                    except:
                        continue

                    if not is_within_minute(updated_at, 360):
                            continue

                    if "Spread" in market_name and key == "away":
                        hdp = -1 * float(hdp)

                    # if float(value) < MIN_BET_ODDS or float(value) > MAX_BET_ODDS:
                    #     continue 

                    record = {
                        "oddsapi_event_id": event_id,  # Original OddsAPI numeric ID for API lookups
                        "event_id": f"{sport}|{home}|{away}|{date}".lower(),
                        "line_id": f"{sport}|{home}|{away}|{date}|{market_name}|{key}|{hdp}".lower(),
                        "sportsbook": "Duel", 
                        "market": market_name,
                        "selection": key,
                        "odds_decimal": float(value), 
                        "hdp": hdp,
                        "sport": sport,
                        "league": league,
                        "home_team": home,
                        "away_team": away,
                        "when_utc": date,
                        "updated_at": updated_at,
                        "bo_matched_id": None
                    }

                    self.process_bets(record)

    def process_bets(self, record):
        with self.lock:
            # Check if event already exists
            for stored_record in self.oddsapievent:
                if stored_record.get("line_id") == record["line_id"]:
                    # logger.info(f'Updating OA duplicate record: {record}')
                    
                    if stored_record.get("odds_decimal") != record["odds_decimal"]:
                        stored_record["odds_decimal"] = record["odds_decimal"]
                    # Duplicate → skip append & log
                    return    

        # Append new event & log (thread-safe)
        with self.lock:
            self.oddsapievent.append(record)
            logger.info(f"Added new event: {record}")
            # logger.info(f"Added new event: {record}")
            # logger.info(f"Here is self.oddsapievent list {self.oddsapievent}")
        # if len(self.oddsapievent) > 50:
        #     logger.info("Odds API events")
            # for event in self.oddsapievent[:20]:
            #     logger.info(event)
            #     logger.info("--------------------------------")

    def return_all_events(self):
        """Thread-safe method to get all events"""
        with self.lock:
            # logger.info(f'Odds API events - {self.oddsapievent}')
            return list(self.oddsapievent)  

    def update_bo_matched_id(self, event_id, bo_matched_id):
        with self.lock:
            for event in self.oddsapievent:
                if event.get("event_id") == event_id:
                    event["bo_matched_id"] = bo_matched_id
                    
    
    # def get_event_by_id(self, event_id):
    #     """Thread-safe method to get specific event"""
    #     with self.lock:
    #         for event in self.oddsapievent:
    #             if event.get("event_id") == event_id:
    #                 return dict(event)  
    #         return None

    def on_error(self, ws, error):
        logger.error(f"WebSocket Error: {error}")

    def on_close(self, ws, close_status_code, close_msg):
        logger.info(f"WebSocket closed — status: {close_status_code}, reason: {close_msg}")
        
        if not self.should_reconnect:
            logger.info("Reconnection disabled, not attempting to reconnect")
            return
        
        if self.reconnect_attempts < self.max_reconnect_attempts:
            self.reconnect_attempts += 1
            logger.info(f"Attempting to reconnect ({self.reconnect_attempts}/{self.max_reconnect_attempts}) in 30 seconds...")
            time.sleep(30)
            try:
                self.connect()
            except Exception as e:
                logger.error(f"Reconnection attempt {self.reconnect_attempts} failed: {e}", exc_info=True)
                if self.reconnect_attempts < self.max_reconnect_attempts:
                    # Will retry on next on_close call
                    pass
        else:
            logger.error(f"Max reconnection attempts ({self.max_reconnect_attempts}) reached. Stopping reconnection attempts.")
            self.should_reconnect = False