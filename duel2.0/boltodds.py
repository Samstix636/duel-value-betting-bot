import websocket
import json
import time
import logging
import threading
from pprint import pprint
from helper import american_to_decimal, est_to_utc, get_sport_from_league, normalize_league, is_less_than_24_hours_away, is_event_started
from dotenv import load_dotenv
import os
load_dotenv()

import logging
# Create logs directory
os.makedirs("logs", exist_ok=True)
logger = logging.getLogger("Boltodds")
logger.setLevel(logging.INFO)

# Prevent propagation to parent loggers
logger.propagate = False

# Clear any existing handlers to ensure clean setup
logger.handlers.clear()

handler = logging.FileHandler("logs/boltodds.log")
formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
handler.setFormatter(formatter)
logger.addHandler(handler)

boltodds_api_key = os.getenv("boltodds_api_key")
    
class BoltOddsStreamClient:
    def __init__(self, uri):
        self.uri = uri
        self.ws = None
        self.subscribed = False
        self.should_reconnect = True
        self.handlers = {
            "line_update": self.handle_line_update,
            "game_update": self.handle_line_update,
            "initial_state": self.handle_line_update,
        }
        self.boltoddsevent = []
        self.lock = threading.Lock()  
        self.thread = None
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 3

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
        # Reset reconnect attempts on successful connection
        self.reconnect_attempts = 0
        
    def on_message(self, ws, message):
        """Called when a message is received from the server"""
        try:
            data_response = json.loads(message)
            if type(data_response) == list:
                for data in data_response:
                    if not self.subscribed:
                        logger.info(f"Ack message: {message}")
                        self.send_subscription(ws)
                        return
                    
                    if data.get('action') == 'ping':
                        return

                    action = data.get('action')
                    
                    # logger.info(f"Action: {action}")
                    handler = self.handlers.get(action)
                    if handler:
                        handler(data)
            elif type(data_response) == dict:
                data = data_response
                if not self.subscribed:
                    logger.info(f"Ack message: {message}")
                    self.send_subscription(ws)
                    return

                    
                if data.get('action') == 'ping':
                    return

                action = data.get('action')
                    

                
        except json.JSONDecodeError as e:
            logger.info(f"Error parsing JSON: {e}")
        except Exception as e:
            logger.info(f"Error processing message: {e}")
            logger.info(f"Error Data: {data_response}")
    
    def on_error(self, ws, error):
        """Called when an error occurs"""
        logger.info(f"WebSocket error: {error}")
    
    def on_close(self, ws, close_status_code, close_msg):
        """Called when the WebSocket connection is closed"""
        logger.info(f"Connection closed — status: {close_status_code}, reason: {close_msg}")
        self.subscribed = False
        
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

    def send_subscription(self, ws):
        subscribe_message = {
            "action": "subscribe",
            "filters": {
                "sportsbooks": ["pinnacle"],
                "markets": ["Moneyline", "Spread", "1st Half Spread", "1st Half Moneyline", 
                           "Total Goals", "1st Half Asian Spread", "1st Half Total Goals", 
                           "3 Way", "Asian Spread", 'Total', '1st Half Total', 
                           '1st Half Total Points', 'Total Points', 'Asian Handicap']
            }
        }
        ws.send(json.dumps(subscribe_message))
        self.subscribed = True

    def handle_line_update(self, data):
        """Handle line_update action"""
        # logger.info(f"BoltOdds data: {data}")
        inner_data = data["data"]
        info = inner_data.get("info", {})
        outcomes = inner_data.get("outcomes", {})
        when_utc = est_to_utc(info.get("when"))
        if not is_less_than_24_hours_away(when_utc) or is_event_started(when_utc):
            return

        if len(self.boltoddsevent) > 200000:
            for event in self.boltoddsevent:
                if is_event_started(event.get("when_utc")):
                    self.boltoddsevent.remove(event)
                    
            
        

        for _, outcome_data in outcomes.items():
            bolt_league = inner_data.get('sport')
            clean_bolt_league = normalize_league(bolt_league) 
            sport = get_sport_from_league(clean_bolt_league)

            # logger.info(
            #             "Parsed sport data | bolt_league=%s | clean_bolt_league=%s | sport=%s",
            #             bolt_league,
            #             clean_bolt_league,
            #             sport,
            #         )

            id = f"{sport}|{inner_data.get('home_team')}|{inner_data.get('away_team')}|{when_utc}"
            line_id = f"{sport}|{inner_data.get('home_team')}|{inner_data.get('away_team')}|{when_utc}|{outcome_data.get('outcome_name')}|{outcome_data.get('outcome_line')}|{outcome_data.get('outcome_over_under')}|{outcome_data.get('outcome_target')}"
            american_odds = outcome_data.get("odds")

            if any(x in outcome_data.get('outcome_name') for x in ['Moneyline', '3 Way']):
                # logger.info(f"Moneyline data: {outcome_data}")
                hdp = None
                if outcome_data['outcome_target'] == inner_data.get('home_team'):
                    selection = 'home'
                elif outcome_data['outcome_target'] == inner_data.get('away_team'):
                    selection = 'away'
                else:
                    selection = 'draw'
            elif any(x in outcome_data.get('outcome_name') for x in ['Spread', 'Asian Handicap']):
                # logger.info(f"Spread/Asian Handicap data: {outcome_data}")
                hdp = outcome_data['outcome_line']
                if outcome_data['outcome_target'] == inner_data.get('home_team'):
                    selection = 'home'
                elif outcome_data['outcome_target'] == inner_data.get('away_team'):
                    selection = 'away'
            elif any(x == outcome_data.get('outcome_name') for x in ['Total', "Totals",'Total Points', 'Total Goals', "1st Half Total", "1st Half Total Points", "1st Half Total Goals"]):
                # logger.info(f"Totals data: {outcome_data}")
                hdp = outcome_data['outcome_line']
                if outcome_data['outcome_over_under'] == 'O':
                    selection = 'over'
                elif outcome_data['outcome_over_under'] == 'U':
                    selection = 'under'
            else:
                logger.info(f"Unknown market name: {outcome_data}")
                continue
                

            record = {
                "event_id": id.lower(),
                "line_id": line_id.lower(),
                "sport": sport,
                "league": inner_data.get("sport"),
                "sportsbook": "Pinnacle",
                "home_team": inner_data.get("home_team"),
                "away_team": inner_data.get("away_team"),
                "when_utc": when_utc,
                "odds_decimal": american_to_decimal(american_odds),
                "market": outcome_data.get("outcome_name"),
                "hdp": hdp,
                "selection": selection
                # "outcome_over_under": outcome_data.get("outcome_over_under"),
                # "outcome_target": outcome_data.get("outcome_target")
            }
            

            with self.lock:
                key = record.get("line_id", 0)
                duplicate_found = False


                #if duplicate is found, update the odds_decimal
                for event in self.boltoddsevent:
                    if key == event.get("line_id", 0):
                        event["odds_decimal"] = record.get("odds_decimal")
                        # logger.info(f"----------------- Updating Duplicate event")
                        duplicate_found = True
                        break
                    
                if not duplicate_found:
                    self.boltoddsevent.append(record)
                    logger.info(f"-----------------Updating BoltOdds event: {record}")

                    
            
    def return_all_events(self):
        """Thread-safe method to get all events"""
        with self.lock:
            # logger.info(f'Bolt odds events - {self.boltoddsevent}')
            return list(self.boltoddsevent)  
    
    def get_event_by_id(self, event_id):
        """Thread-safe method to get specific event"""
        with self.lock:
            for event in self.boltoddsevent:
                if event.get("event_id") == event_id:
                    return dict(event)  
            return None

    