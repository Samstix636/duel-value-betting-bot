import websocket
import json
import time
import logging
import threading
from pprint import pprint
from helper import american_to_decimal, est_to_utc, get_sport_from_league, normalize_league
from dotenv import load_dotenv
import os
load_dotenv()

import logging
# Create logs directory
os.makedirs("logs", exist_ok=True)
logger = logging.getLogger("Boltodds")
logger.setLevel(logging.INFO)

handler = logging.FileHandler("logs/boltodds.log")
formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
handler.setFormatter(formatter)

if not logger.handlers:
    logger.addHandler(handler)

logger.propagate = True

boltodds_api_key = os.getenv("boltodds_api_key")
    
class BoltOddsStreamClient:
    def __init__(self, uri):
        self.uri = uri
        self.ws = None
        self.subscribed = False
        self.should_reconnect = True
        self.handlers = {
            "line_update": self.handle_line_update,
        }
        self.boltoddsevent = []
        self.lock = threading.Lock()  
        self.thread = None

    def start_threaded(self):
        """Start the WebSocket in a background thread"""
        logger.info("Starting BoltOdds stream in background thread")
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
            self.uri,
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
        """Called when the WebSocket connection is established"""
        logger.info("WebSocket connection opened")
        
    def on_message(self, ws, message):
        """Called when a message is received from the server"""
        try:
            data = json.loads(message)
            
            if not self.subscribed:
                logger.info(f"Ack message: {message}")
                self.send_subscription(ws)
                return
            
            if data.get('action') == 'ping':
                return
            
            action = data.get('action')
            handler = self.handlers.get(action)
            if handler:
                handler(data)
                
        except json.JSONDecodeError as e:
            logger.info(f"Error parsing JSON: {e}")
        except Exception as e:
            logger.info(f"Error processing message: {e}")
    
    def on_error(self, ws, error):
        """Called when an error occurs"""
        logger.info(f"WebSocket error: {error}")
    
    def on_close(self, ws, close_status_code, close_msg):
        """Called when the WebSocket connection is closed"""
        logger.info(f"Connection closed — status: {close_status_code}, reason: {close_msg}")
        self.subscribed = False
        
        if self.should_reconnect:
            logger.info("Reconnecting...")
            time.sleep(5)
            self.connect()

    def send_subscription(self, ws):
        subscribe_message = {
            "action": "subscribe",
            "filters": {
                "sportsbooks": ["pinnacle"],
                "markets": ["Moneyline", "Spread", "1st Half Spread", "1st Half Moneyline", 
                           "Total Goals", "1st Half Asian Spread", "1st Half Total Goals", 
                           "3 Way", "Asian Spread", 'Total', '1st Half Total', 
                           '1st Half Total Points', 'Total Points']
            }
        }
        ws.send(json.dumps(subscribe_message))
        self.subscribed = True

    def handle_line_update(self, data):
        """Handle line_update action"""
        inner_data = data["data"]
        info = inner_data.get("info", {})
        outcomes = inner_data.get("outcomes", {})
        when_utc = est_to_utc(info.get("when"))

        for _, outcome_data in outcomes.items():
            bolt_league = inner_data.get('sport')
            clean_bolt_league = normalize_league(bolt_league) 
            sport = get_sport_from_league(clean_bolt_league)

            logger.info(
                        "Parsed sport data | bolt_league=%s | clean_bolt_league=%s | sport=%s",
                        bolt_league,
                        clean_bolt_league,
                        sport,
                    )

            id = f"{sport}|{inner_data.get('home_team')}|{inner_data.get('away_team')}|{when_utc}"
            american_odds = outcome_data.get("odds")

            record = {
                "id": id.lower(),
                "sport": sport,
                "league": inner_data.get("sport"),
                "sportsbook": "Pinnacle",
                "home_team": inner_data.get("home_team"),
                "away_team": inner_data.get("away_team"),
                "when_utc": when_utc,
                "odds_decimal": american_to_decimal(american_odds),
                "market": outcome_data.get("outcome_name"),
                "outcome_line": outcome_data.get("outcome_line"),
                "outcome_over_under": outcome_data.get("outcome_over_under"),
                "outcome_target": outcome_data.get("outcome_target")
            }

            with self.lock:
                key = record.get("id", 0)
                duplicate_found = False

                for event in self.boltoddsevent:
                    if key == event.get("id", 0):
                        duplicate_found = True
                        break
                    
                if not duplicate_found:
                    self.boltoddsevent.append(record)
                    # logger.info(f"-----------------Line updated. Added new event: {record}")
            
    def return_all_events(self):
        """Thread-safe method to get all events"""
        with self.lock:
            # logger.info(f'Bolt odds events - {self.boltoddsevent}')
            return list(self.boltoddsevent)  
    
    def get_event_by_id(self, event_id):
        """Thread-safe method to get specific event"""
        with self.lock:
            for event in self.boltoddsevent:
                if event.get("id") == event_id:
                    return dict(event)  
            return None

    