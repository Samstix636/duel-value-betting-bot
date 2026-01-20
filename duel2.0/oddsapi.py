import os
import json
import logging
import time
import threading
from datetime import datetime, timedelta
import websocket
import requests
from dotenv import load_dotenv
from helper import is_less_than_24_hours_away

load_dotenv()
# Create logs directory
os.makedirs("logs", exist_ok=True)
logger = logging.getLogger("OddsAPI")
logger.setLevel(logging.INFO)

# Dedicated file for this module
handler = logging.FileHandler("logs/oddsapi.log")
formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
handler.setFormatter(formatter)

# Prevent duplicate handlers
if not logger.handlers:
    logger.addHandler(handler)

# Let messages also flow to app.log and errors.log
logger.propagate = True

MIN_VALUE = 1.0 
MIN_BET_ODDS = 1.2
MAX_BET_ODDS = 3.0  


class OddsAPIStreamClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.bookmakers = ["Duel"]
        self.markets = ["Spread", "ML", "Totals", "Totals HT", "Asian Handicap", 
                       "Asian Handicap HT", "Team Total home", "Team Total away", 
                       "Team Total home HT", "Team Total away HT", "ML HT", "Spread HT",
                       "Totals (Games)", "Spread (Games)"]
        self.alloddsapievent: list[dict] = []
        self.upcoming_event_ids = []
        self.oddsapievent: list[dict] = []
        self.should_reconnect = True
        self.lock = threading.Lock()  
        self.thread = None 
        self.sports = ['football', 'basketball', 'handball', 'volleyball', 'tennis', 'ice-hockey', 'american-football']

    def get_upcoming_event_ids(self):
        """Fetch all events and return IDs of those in the next 24 hours"""
        # Fetch all events from all sports
        for sport in self.sports:
            url = f"https://api.odds-api.io/v3/events?apiKey={self.api_key}&sport={sport}"
            response = requests.get(url)
            response.raise_for_status()

            events = response.json()
            if isinstance(events, list):
                self.alloddsapievent.extend(events)
        # logger.info(self.alloddsapievent)
        # logger.info("___________________________")
        
        # Filter for events in next 24 hours and collect IDs
        for event in self.alloddsapievent:
            date_str = event.get("date")
            if date_str and is_less_than_24_hours_away(date_str):
                self.upcoming_event_ids.append(event['id'])
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
        return f"wss://api.odds-api.io/v3/ws?apiKey={self.api_key}"
    
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
                if sportsbook not in self.bookmakers:
                    return
                if event_id not in self.upcoming_event_ids:
                    # logger.info(f"event id {event_id} for bookmaker {data.get("bookie")} not in the next 24 hours")
                    continue
                if data.get("type") not in ("created", "updated"):
                    return
                
                if not event_id:
                    logger.debug(f"No event_id found in data. Keys present: {list(data.keys())}")
                    continue

                self.handle_event_message(data)

        except Exception as e:
            logger.error(f"on_message error: {e}", exc_info=True)

    def handle_event_message(self, data):
        event_id = data.get("id")
        # Find the full event data from alloddsapievent
        event_data = next(
        (event for event in self.alloddsapievent if str(event.get("id")) == str(event_id)),
        None
    )

        if not event_data:
            logger.info(f"Event ID {event_id} not found in alloddsapievent")
            return
        
        sport = event_data.get("sport", {}).get("slug")
        league = event_data.get("league", {}).get("slug")
        home = event_data.get("home")
        away = event_data.get("away")
        date = event_data.get("date")
        # logger.info(event_data)

        for market in data.get("markets", []):
            market_name = market.get("name")
            
            if market_name not in self.markets:
                continue

            for entry in market.get("odds", []):
                hdp = entry.get("hdp")

                for key, value in entry.items():
                    if key not in ("home", "away", "draw", "over", "under"):
                        continue

                    if float(value) < MIN_BET_ODDS or float(value) > MAX_BET_ODDS:
                        return 

                    record = {
                        "id": f"{league}|{home}|{away}|{date}".lower(),
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
                    }

                    self.process_bets(record)

    def process_bets(self, record):
        with self.lock:
            # Check if event already exists
            for stored_record in self.oddsapievent:
                if stored_record.get("id") == record["id"]:
                    
                    if stored_record.get("odds_decimal") != record["odds_decimal"]:
                        stored_record["odds_decimal"] = record["odds_decimal"]
                    # Duplicate → skip append & log
                    return    

        # Append new event & log (thread-safe)
        with self.lock:
            self.oddsapievent.append(record)
            # logger.info(f"Added new event: {record}")
            # logger.info(f"Here is self.oddsapievent list {self.oddsapievent}")

    def return_all_events(self):
        """Thread-safe method to get all events"""
        with self.lock:
            # logger.info(f'Odds API events - {self.oddsapievent}')
            return list(self.oddsapievent)  
    
    def get_event_by_id(self, event_id):
        """Thread-safe method to get specific event"""
        with self.lock:
            for event in self.oddsapievent:
                if event.get("id") == event_id:
                    return dict(event)  
            return None

    def on_error(self, ws, error):
        logger.error(f"WebSocket Error: {error}")

    def on_close(self, ws, close_status_code, close_msg):
        logger.info("WebSocket closed")