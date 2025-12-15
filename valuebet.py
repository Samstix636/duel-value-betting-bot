import os
import json
import logging
import time
import asyncio
from datetime import datetime, timedelta
from attr import dataclass
import websocket
import requests
import pytz
import pygsheets
import dotenv
import signal
from typing import Optional, Dict, Any
import sys
import random
import winsound
import threading
from duel_client import DuelClient
dotenv.load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    filename="OddsFinder.log")      
logger = logging.getLogger("OddsFinder")  


def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)

def play_notification_sound(sound_path: str = "alarm.wav", async_play: bool = True) -> None:
    """
    Play a .wav file using the built-in Windows sound API.

    Args:
        sound_path: Relative or absolute path to the .wav file.
        async_play: True → return immediately; False → block until playback finishes.
    """
    # if not os.path.exists(sound_path):
    #     logger.error("Notification sound not found: %s", sound_path)
    #     return

    flags = winsound.SND_FILENAME
    if async_play:
        flags |= winsound.SND_ASYNC

    try:
        winsound.PlaySound(resource_path(sound_path), flags)
    except RuntimeError as exc:
        logger.error("Failed to play notification sound (%s): %s", sound_path, exc)


class OddsFinder:
    def __init__(self, api_key: str, input_data: dict, worksheet, sent_bets: list = None, duel_client: Optional[DuelClient] = None):
        self.api_key = api_key
        self.input_data = input_data
        self.worksheet = worksheet
        self.duel_client = duel_client
        self.bookmakers = ["Duel", "Pinnacle"]
        self.markets = ["Spread", "ML", "Totals", "Totals HT", "3-Way Result",
                       "Asian Handicap HT", "Team Total Home", "Team Total Away", 
                       "Team Total Home HT", "Team Total Away HT", "ML HT", "Spread HT",
                       "Totals (Games)", "Spread (Games)", "Totals 1st Set (Games)", "Spread 1st Set (Games)", "ML 1st Set"]
        self.odds_store = []
        self.value_events = sent_bets or []
        self.is_running = True
        self.ws = None
        self.backoff = 1  # Track backoff for reset on successful connection

    def on_open(self, ws):
        logger.info("WebSocket connected successfully")
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] WebSocket connected")
        # Reset backoff on successful connection
        self.backoff = 1

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
        # logger.info(f"Handling event message: {data}")
        
        
        event_id = data.get("id")
        bookie = data.get("bookie")

        if bookie not in self.bookmakers:
            return

        # logger.info(data)

        if data.get("type") == "deleted":
            logger.info("-----------------Event deleted")
            logger.info(data)
            return
        
        if data.get("type") not in ("created", "updated"):
            return

        # logger.info(data)

        for market in data.get("markets", []):
            # logger.info(market)
            market_name = market.get("name")
            market_name = transpose_duel_market_name(market_name)

            updated_at = market.get("updatedAt")
            # if updated_at is None:
            #     continue
        
            if market_name not in self.markets:
                continue

            for entry in market.get("odds", []):
                hdp = entry.get("hdp")
                if hdp is not None:
                    if len(entry.items()) < 3:
                        continue

                for key, value in entry.items():
                    if key not in ("home", "away", "draw", "over", "under", 'yes','no'):
                        continue

                    try:
                        float(value)
                    except:
                        continue

                    if bookie.lower() == "duel":
                        if float(value) < self.input_data['min_prematch_odd'] or float(value) > self.input_data['max_prematch_odd']:
                            continue
                        if not is_within_one_minute(updated_at, 360):
                            continue

                    if bookie.lower() == "pinnacle": #If pinnacle odds are above the max_prematch_odd, it can't give a value bet anyways, so skip
                        if float(value) > self.input_data['max_prematch_odd']:
                            continue 
                        if not is_within_one_minute(updated_at, 10):
                            continue
                    
                    if "Spread" in market_name and key == "away":
                        hdp = -1 * float(hdp)


                    record = {
                        "event_id": event_id,
                        "bookie": bookie, #different for Pinnacle and Duel
                        "market": market_name,
                        "selection": key,
                        "price": float(value), #different for Pinnacle and Duel
                        "hdp": hdp,
                        "updated_at": updated_at
                    }

                    if hdp is None:
                        uid = f"{event_id}-{key}-{market_name.replace(' ', '_')}"
                    else:
                        uid = f"{event_id}-{key}-{market_name.replace(' ', '_')}-{hdp}"

                    record["uid"] = uid
                    self.process_bets(record)
        if len(self.odds_store) > 200000:
            logger.info(f"Odds store is too large ({len(self.odds_store)}), removing old records")
            for record in self.odds_store:
                if not is_less_than_24_hours_away(record['updated_at']):
                    self.odds_store.remove(record)
            logger.info(f"Odds store is now {len(self.odds_store)}")

    def process_bets(self, record):
        # print('Processing odds updates...')
        # Check if we've already found a value bet for this event
        if int(record['event_id']) in self.value_events:
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
            # logger.info("Odds store:")
            # if len(self.odds_store) > 5:
            #     logger.info(self.odds_store[-5:])
            # else:
            #     logger.info(self.odds_store)
            self.compare_odds(record)

    def compare_odds(self, record):
        # logger.info(f"No of events processed: {len(self.odds_store)}")
        event_id = record.get("event_id")
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
        if float(value) < self.input_data['min_value_percentage']:
        # if float(value) < 1:
            # print(f"Skipping game. {value}% is below minimum value {self.input_data['min_value_percentage']}%")
            return None

        sport, league, home, away, start_time = self.fetch_event_details(event_id) #This is the farthest place we can pull this API
        logger.info(f"[{sport}] Value bet found for Duel with value: {value}%")
        # print(f"[{sport}] Value bet found for Duel with value: {value}%")
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
        found_valuebet_at = datetime.now(pytz.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        if duel_entry['sport'].lower() == 'handball' and duel_entry['market'] == 'ML':
            logger.info(f"Skipping Handball ML market")
            return None
        
        if int(event_id) in self.value_events:
            logger.info(f"Event {event_id} already processed")
            return None

        if is_less_than_24_hours_away(start_time): #Ensures only events starting within the next 24 hours are considered.
            if should_process_event(sport, start_time): # Filters out events that are too close to kickoff:
                # For tennis, skip if it's starting in <45 min. For football, skip if it's starting in <2 min

                # Fetch latest odds for this event from API
                try:
                    api_odds_data = get_event_odds(event_id)
                    duel_url = api_odds_data['urls']['Duel']
                    duel_event_id = duel_url.split('-')[-1]
                    logger.info(json.dumps(api_odds_data, indent=2))

                    duel_latest_odds = get_odds_from_data(
                        api_odds_data,
                        "Duel",
                        duel_entry.get("market"),
                        duel_entry.get("selection"),
                        hdp_line=duel_entry.get("hdp")
                    )
                    pinnacle_latest_odds = get_odds_from_data(
                        api_odds_data,
                        "Pinnacle",
                        pinnacle_entry.get("market"),
                        pinnacle_entry.get("selection"),
                        hdp_line=pinnacle_entry.get("hdp")
                    )

                    # Only recalculate if both are found and valid (not None)
                    if duel_latest_odds is not None and pinnacle_latest_odds is not None:
                        value = calculate_value(duel_latest_odds, pinnacle_latest_odds)
                        # Refresh the odds as well for further processing
                        duel_entry["price"] = duel_latest_odds
                        pinnacle_entry["price"] = pinnacle_latest_odds
                        logger.info(f"Recalculated value with latest API odds: {value}% (Duel: {duel_latest_odds}, Pinnacle: {pinnacle_latest_odds})")
                    else:
                        logger.warning("Unable to fetch both Duel and Pinnacle odds from API for event_id {} (duel: {}, pinnacle: {})".format(
                            event_id, duel_latest_odds, pinnacle_latest_odds
                        ))
                        return None

                except Exception as e:
                    logger.error(f"Error fetching latest odds from API for event_id {event_id}: {e}", exc_info=True)
                    return None

                if value < self.input_data['min_value_percentage']:
                    logger.info(f"Skipping game. Latest value is {value}%; below minimum value {self.input_data['min_value_percentage']}%")
                    return None

                # duel value bet
                duel_value_bet = {
                    "home_name": home,
                    "away_name": away,
                    'event_id': event_id,
                    'uuid': duel_entry.get("uid"),
                    "league": duel_entry.get("league"),
                    "market_name": duel_entry.get("market"),
                    "selection": duel_entry.get("selection"),
                    "pinnacle_odds": pinnacle_entry.get("price"),
                    "duel_odds": duel_entry.get("price"),
                    'value': value,
                    "sport": duel_entry.get("sport"),
                    "hdp": duel_entry.get("hdp"),
                    'start_time': start_time,
                    "duel_odds_updated_at": duel_entry.get("updated_at"),
                    "found_valuebet_at": found_valuebet_at
                }
                

                # Pinnacle value bet
                pinnacle_value_bet = {
                    "home_name": home,
                    "away_name": away,
                    'event_id': event_id,
                    'uuid': pinnacle_entry.get("uid"),
                    "league": pinnacle_entry.get("league"),
                    "market_name": pinnacle_entry.get("market"),
                    "selection": pinnacle_entry.get("selection"),
                    "odds": pinnacle_entry.get("price"),
                    "sportsbook": "pinnacle",
                    "sport": pinnacle_entry.get("sport"),
                    "hdp": pinnacle_entry.get("hdp"),
                    'pinnacle_odds_updated_at': pinnacle_entry.get("updated_at")
                }

                # print at once so it doesn't mix up
                logger.info(f"[{sport}] Value bet pair found\n"
                            f"----- VALUE BET SIDE (duel) -----\n"
                            f"{json.dumps(duel_value_bet, indent=2, ensure_ascii=False)}\n"
                            f"----- CORRESPONDING PINNACLE SIDE -----\n"
                            f"{json.dumps(pinnacle_value_bet, indent=2, ensure_ascii=False)}\n"
                            f"------------------------------------------")
                print('>>> Value bet pair found')

                logger.info(f">>> Fetching latest odds for value event {event_id}")
                
                # Use DuelClient to place bet
                if not self.duel_client:
                    logger.error("DuelClient not initialized, cannot place bet")
                    return None
                
                settled_odds = None
                balance = None
                try:
                    bet_response = self.duel_client.place_bet_sync(
                        duel_event_id=duel_event_id,
                        sport=sport,
                        market_name=duel_entry.get("market"),
                        selection=duel_entry.get("selection"),
                        hdp=duel_entry.get("hdp"),
                        odds=duel_entry.get("price")
                    )
                    time.sleep(5)
                    
                    if bet_response.get('error') == []:
                        logger.info(f"Bet placed successfully for event")
                        settled_odds, balance = self.duel_client.get_bet_odds(duel_event_id)
                    elif bet_response.get('error') == "expired_token":
                        logger.error(f"Token expired, attempting to refresh token")
                        # Try to refresh token
                        self.duel_client.get_auth_token(force_refresh=True)
                        play_notification_sound("alarm.wav", async_play=False)
                        # Retry placing bet with new token
                        bet_response = self.duel_client.place_bet(
                            duel_event_id=duel_event_id,
                            sport=sport,
                            market_name=duel_entry.get("market"),
                            selection=duel_entry.get("selection"),
                            hdp=duel_entry.get("hdp"),
                            odds=duel_entry.get("price")
                        )
                        if bet_response.get('error') == []:
                            settled_odds, balance = self.duel_client.get_bet_odds(duel_event_id)
                    else:
                        logger.error(f"Error placing bet: {bet_response}")
                        for record in self.odds_store:
                            if record['uid'] == duel_entry.get("uid"):
                                self.odds_store.remove(record)
                        
                        return None
                except Exception as e:
                    logger.error(f"Error placing bet: {e}", exc_info=True)
                    return None
                    
                
                # Save to Google Sheets
                alert_data = [
                    duel_value_bet['sport'],
                    duel_value_bet['league'],
                    duel_value_bet['home_name'],
                    duel_value_bet['away_name'],
                    duel_value_bet['event_id'],
                    'Duel',
                    duel_value_bet['market_name'],
                    duel_value_bet['selection'],
                    duel_value_bet['hdp'],
                    duel_value_bet['duel_odds'],
                    duel_value_bet['pinnacle_odds'],
                    duel_value_bet['value'],
                    duel_value_bet['duel_odds_updated_at'],
                    duel_value_bet['found_valuebet_at'],
                    settled_odds,
                    balance
                ]
                self.value_events.append(int(event_id))
                update_log_to_sheet([alert_data], self.worksheet)

                
                
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
        # logger.error(f"WebSocket Error: {error}")
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] WebSocket Error: {error}")

    def on_close(self, ws, close_status_code, close_msg):
        logger.info(f"WebSocket closed (code: {close_status_code}, msg: {close_msg})")
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] WebSocket closed")

    def build_ws_url(self) -> str:
        return f"wss://api.odds-api.io/v3/ws?apiKey={self.api_key}&status=prematch"
        
    def start(self):
        """Start WebSocket connection with automatic reconnection"""
        max_backoff = 60
        reconnect_count = 0
        
        print("=" * 80)
        print("Starting OddsFinder WebSocket Monitor")
        print("Press Ctrl+C to stop gracefully")
        print("=" * 80)
        
        while self.is_running:
            try:
                logger.info(f"Attempting WebSocket connection (attempt {reconnect_count + 1})...")
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Connecting to WebSocket...")
                
                self.ws = websocket.WebSocketApp(
                    self.build_ws_url(),
                    on_open=self.on_open,
                    on_message=self.on_message,
                    on_error=self.on_error,
                    on_close=self.on_close
                )
                
                # The connection will be established in on_open callback
                # on_open will reset self.backoff on successful connection
                self.ws.run_forever(
                    ping_interval=30,
                    ping_timeout=10
                )
                
                # If we get here, connection was closed
                if not self.is_running:
                    logger.info("Shutting down gracefully...")
                    break
                    
                reconnect_count += 1
                logger.warning(f"WebSocket disconnected. Reconnecting in {self.backoff} seconds...")
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Reconnecting in {self.backoff} seconds...")
                time.sleep(self.backoff)
                
                # Exponential backoff with max limit
                self.backoff = min(self.backoff * 2, max_backoff)
                
            except KeyboardInterrupt:
                logger.info("Received interrupt signal, shutting down...")
                print("\n[Ctrl+C] Shutting down gracefully...")
                self.stop()
                break
            except Exception as e:
                logger.error(f"Unexpected error in WebSocket connection: {e}", exc_info=True)
                # print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Error: {e}. Retrying in {self.backoff} seconds...")
                time.sleep(self.backoff)
                self.backoff = min(self.backoff * 2, max_backoff)
                reconnect_count += 1
    
    def stop(self):
        """Stop the WebSocket connection gracefully"""
        self.is_running = False
        if self.ws:
            try:
                self.ws.close()
            except:
                pass
        logger.info("OddsFinder stopped")



def get_event_odds(event_id):
    """
    Fetch event odds from the API.
    
    Args:
        event_id: The event ID to fetch odds for
    
    Returns:
        Dictionary containing event and bookmaker odds data
    """
    url = "https://api.odds-api.io/v3/odds"
    params = {
        "apiKey": os.getenv("ODDS_API_KEY"),
        "eventId": event_id,
        "bookmakers": 'Pinnacle,Duel',
        # 'sport': 'Handball',
        # 'since': int((datetime.now(pytz.UTC) - timedelta(seconds=30)).timestamp())
    }
    response = requests.get(url, params=params)
    # response.raise_for_status()
    if response.status_code != 200:
        logger.error(f"Error fetching latest odds from API for event_id {event_id}: {response.text}")
        return []
    return response.json()


def get_odds_from_data(
    data: Dict[str, Any],
    bookmaker_name: str,
    market_name: str,
    selection: str,
    hdp_line: Optional[float] = None
) -> Optional[float]:
    """
    Extract odds from the API response data structure.
    
    Args:
        data: The API response dictionary containing event and bookmaker data
        bookmaker_name: Name of the bookmaker (e.g., 'Duel', 'Pinnacle')
        market_name: Name of the market (e.g., 'ML', 'Totals')
        selection: The selection to get odds for (e.g., 'home', 'away', 'draw', 'over', 'under')
        hdp_line: Optional handicap/totals line (required for markets like Totals, Spread)
    
    Returns:
        The odds value as a float, or None if not found
    
    Example:
        >>> data = get_event_odds("61957400")
        >>> odds = get_odds_from_data(data, 'Duel', 'ML', 'home')
        >>> print(odds)  # 1.80
        >>> odds = get_odds_from_data(data, 'Duel', 'Totals', 'over', hdp_line=59.5)
        >>> print(odds)  # 1.78
    """
    try:
        # Check if bookmakers key exists
        if 'bookmakers' not in data:
            return None
        
        # Get the bookmaker data
        bookmaker_data = data['bookmakers'].get(bookmaker_name)
        if not bookmaker_data:
            return None
        
        # Find the market with matching name
        market = None
        for m in bookmaker_data:
            if m.get('name') == market_name:
                market = m
                break
        
        if not market:
            return None
        
        # Get the odds list
        odds_list = market.get('odds', [])
        if not odds_list:
            return None
        
        # Find the appropriate odds entry
        odds_entry = None
        
        if hdp_line is not None:
            # For markets with lines (Totals, Spread, etc.), find matching hdp
            for entry in odds_list:
                if entry.get('hdp') == hdp_line:
                    odds_entry = entry
                    break
        else:
            # For markets without lines (ML), use the first entry
            odds_entry = odds_list[0] if odds_list else None
        
        if not odds_entry:
            return None
        
        # Get the odds value for the selection
        odds_value = odds_entry.get(selection)
        if odds_value is None:
            return None
        
        # Convert to float and return
        return float(odds_value)
    
    except (KeyError, ValueError, TypeError) as e:
        print(f"Error extracting odds: {e}")
        return None

def calculate_value(slower_odds, sharp_odds):
    value = (float(slower_odds) - float(sharp_odds))*100/(float(sharp_odds))
    value = round(value, 2)
    return value

def transpose_duel_market_name(market_name):
    if market_name == "First Set Winner":
        return 'ML 1st Set'
    else:
        return market_name
def is_less_than_24_hours_away(time_str):
    # Parse the input time string into a datetime object
    time_format = "%Y-%m-%dT%H:%M:%SZ"
    given_time = datetime.strptime(time_str, time_format).replace(tzinfo=pytz.UTC)

    # Get the current time in UTC
    current_time = datetime.now(pytz.UTC)
    
    # Calculate the time difference
    time_difference = given_time - current_time
    
    # Ensure event is in the future and less than 24 hours away
    return timedelta(0) < time_difference <= timedelta(hours=48)

def is_within_one_minute(time_str, minute_val = 2):
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
    return timedelta(0) <= delta <= timedelta(minutes=minute_val)



def should_process_event(sport, time_str):
    time_format = "%Y-%m-%dT%H:%M:%SZ"
    given_time = datetime.strptime(time_str, time_format).replace(tzinfo=pytz.UTC)
    current_time = datetime.now(pytz.UTC)
    time_until_event_start = given_time - current_time

    # Skip events that have already started
    if time_until_event_start <= timedelta(0):
        return False

    if (sport.lower() == "tennis" and time_until_event_start > timedelta(minutes=45)) \
       or (sport.lower() in ["football", "basketball", 'baseball', "american football", "ice hockey", "esports", "handball", "rugby", "volleyball", 'badminton', 'beach soccer', 'beach volleyball', 'table tennis'] and time_until_event_start > timedelta(minutes=2)):
        return True
    
    return False


def update_log_to_sheet(alert_list, worksheet):
    """Update Google Sheet with value bet alerts"""
    try:
        rows = worksheet.get_all_values(
            include_tailing_empty=True, 
            include_tailing_empty_rows=False, 
            returnas='matrix'
        )
        row_index = len(rows) + 1
        no_of_alerts = len(alert_list)
        worksheet.update_values(f"A{row_index}:P{row_index+no_of_alerts}", alert_list)
        logger.info(f"Successfully saved {no_of_alerts} value bet(s) to Google Sheet")
    except Exception as e:
        logger.error(f"Error updating Google Sheet: {e}", exc_info=True)


# place_bet and get_bet_odds functions have been moved to DuelClient class

def main():
    # Setup signal handler for graceful shutdown
    duel_client = None
    finder = None
    
    def signal_handler(sig, frame):
        logger.info("Received interrupt signal")
        if finder:
            finder.stop()
        if duel_client:
            duel_client.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Initialize Google Sheets
    try:
        gc = pygsheets.authorize(service_file='google_client.json')
        sht1 = gc.open_by_key('1hhb-Gr-Rh1DniTBISgVYStec59E8s0YEYm-Xl-MZ3vA')
        global wks1, wks2
        wks1 = sht1.worksheet_by_title('Input')
        wks2 = sht1.worksheet_by_title('valuebet_system_2')
        
        # Get user defined inputs
        [[min_prematch_odd, max_prematch_odd, min_live_odd, max_live_odd, kelly_fraction, min_value_percentage]] = wks1.get_values('A2', 'F2')
        
        # Load previously sent bets to avoid duplicates
        temp_rows = wks2.get_all_values(
            include_tailing_empty=True, 
            include_tailing_empty_rows=False, 
            returnas='matrix'
        )
        sent_bets = []
        if len(temp_rows) > 1:  # Skip header row
            for row in temp_rows[1:]:
                if len(row) > 4 and row[4]:  # Check if event_id exists
                    try:
                        sent_bets.append(int(row[4]))
                    except (ValueError, IndexError):
                        continue
        
        # Build input_data dictionary
        input_data = {
            'min_prematch_odd': float(min_prematch_odd),
            'max_prematch_odd': float(max_prematch_odd),
            'min_live_odd': float(min_live_odd),
            'max_live_odd': float(max_live_odd),
            'kelly_fraction': float(kelly_fraction),
            'min_value_percentage': float(min_value_percentage)
        }
        
        logger.info(f"Loaded input data: {input_data}")
        logger.info(f"Loaded {len(sent_bets)} previously sent bets")
        
    except Exception as e:
        logger.error(f"Error initializing Google Sheets: {e}", exc_info=True)
        raise
    
    # Get API key
    api_key = os.getenv("ODDS_API_KEY")
    if not api_key:
        raise ValueError("Missing ODDS_API_KEY in environment variables")
    
    # Initialize DuelClient (async)
    try:
        logger.info("Initializing DuelClient...")
        duel_client = DuelClient(headless=False)  # Set to True for headless mode
        # Store the event loop for async operations
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        duel_client._loop = loop
        # Run initialization in the event loop
        loop.run_until_complete(duel_client.initialize())
        logger.info("DuelClient initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing DuelClient: {e}", exc_info=True)
        logger.warning("Continuing without DuelClient - betting will not be automated")
        duel_client = None
        return
    
    # Initialize and start OddsFinder in a separate thread
    print("Initializing Valuebet Finder...")
    finder = OddsFinder(
        api_key=api_key,
        input_data=input_data,
        worksheet=wks2,
        sent_bets=sent_bets,
        duel_client=duel_client
    )
    
    # Function to run OddsFinder in a separate thread
    def run_odds_finder():
        try:
            logger.info("Starting OddsFinder in background thread...")
            finder.start()
        except Exception as e:
            logger.error(f"Fatal error in OddsFinder thread: {e}", exc_info=True)
        finally:
            if finder:
                finder.stop()
    
    # Start OddsFinder in a daemon thread
    finder_thread = threading.Thread(target=run_odds_finder, daemon=True)
    finder_thread.start()
    logger.info("OddsFinder started in background thread")
    
    # Keep main thread alive to maintain DuelClient and token refresh
    try:
        logger.info("Main thread running to maintain DuelClient and token refresh...")
        logger.info("Press Ctrl+C to stop")
        
        # Run async event loop in main thread
        async def run_main_loop():
            while True:
                await asyncio.sleep(1)
                
                # Check if token refresh is needed
                if duel_client:
                    await duel_client.refresh_token_if_needed()
                
                # Check if finder thread is still alive
                if not finder_thread.is_alive():
                    logger.warning("OddsFinder thread has stopped")
                    break
        
        # Run the async main loop
        loop.run_until_complete(run_main_loop())
        
    except KeyboardInterrupt:
        logger.info("Received interrupt signal in main thread")
    except Exception as e:
        logger.error(f"Error in main thread: {e}", exc_info=True)
    finally:
        if finder:
            finder.stop()
        if duel_client and loop:
            loop.run_until_complete(duel_client.stop())
            loop.close()
        logger.info("Shutting down...")


if __name__ == "__main__":
    main()

    