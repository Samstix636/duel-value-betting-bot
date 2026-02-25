import time
import os
import asyncio
import threading
from zlib import MAX_WBITS
import requests
import signal
import sys
from datetime import datetime
from typing import Optional, Dict, Any
from dotenv import load_dotenv
import pygsheets
from oddsapi import OddsAPIStreamClient
from boltodds import BoltOddsStreamClient
from duel_client import DuelClient
from helper import events_match, calculate_value, map_market_name, deduplicate_by_key
import logging
import pytz
load_dotenv()

import logging
import os

# Create logs directory
os.makedirs("logs", exist_ok=True)

# Root logger
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

todays_date = datetime.now().strftime("%Y-%m-%d")

# --- Application log (main decisions) ---
app_handler = logging.FileHandler(f"logs/main_{todays_date}.log")
app_handler.setFormatter(formatter)
app_handler.setLevel(logging.INFO)

# Attach to root
root_logger.addHandler(app_handler)
# root_logger.addHandler(error_handler)

# Main logger
logger = logging.getLogger("MainLog")


def get_event_odds(event_id: str, api_key: str) -> Dict[str, Any]:
    """
    Fetch event odds from the API to get Duel URL.
    
    Args:
        event_id: The OddsAPI event ID to fetch odds for
        api_key: The odds-api.io API key
    
    Returns:
        Dictionary containing event and bookmaker odds data
    """
    url = "https://api.odds-api.io/v3/odds"
    params = {
        "apiKey": api_key,
        "eventId": event_id,
        "bookmakers": 'Duel',
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code != 200:
            logger.error(f"Error fetching latest odds from API for event_id {event_id}: {response.text}")
            return {}
        return response.json()
    except Exception as e:
        logger.error(f"Error fetching event odds: {e}", exc_info=True)
        return {}


def get_duel_event_id(api_odds_data: Dict[str, Any]) -> Optional[str]:
    """
    Extract the Duel event ID from the API odds data.
    
    Args:
        api_odds_data: The API response containing URLs
    
    Returns:
        The Duel event ID, or None if not found
    """
    try:
        urls = api_odds_data.get('urls', {})
        duel_url = urls.get('Duel')
        if duel_url:
            # URL format: https://duel.com/sports/...-12345678
            duel_event_id = duel_url.split('-')[-1]
            return duel_event_id
        return None
    except Exception as e:
        logger.error(f"Error extracting Duel event ID: {e}", exc_info=True)
        return None


class ValueBetFinder:
    def __init__(self, input_data: dict, sent_bets: list, duel_client: Optional[DuelClient] = None):
        logger.info("Initializing ValueBetFinder...")
        self.input_data = input_data
        self.duel_client = duel_client
        
        # Load API keys from environment
        odds_api_key = os.getenv("odds_api_key")
        boltodds_api_key = os.getenv("boltodds_api_key")
        
        # Store API key for fetching Duel event IDs
        self.api_key = odds_api_key
        
        # Validate keys exist
        if not odds_api_key:
            raise ValueError("odds_api_key not found in .env file")
        if not boltodds_api_key:
            raise ValueError("boltodds_api_key not found in .env file")
        
        logger.info("API keys loaded successfully")
        self.found_value_bets = sent_bets or []
        
        # Initialize both stream clients
        self.oddsapi_stream = OddsAPIStreamClient(odds_api_key)
        self.bolt_stream = BoltOddsStreamClient(f"wss://spro.agency/api?key={boltodds_api_key}")
                
        logger.info("Stream clients initialized")
        print('>>> OA Stream initialized')
        print('>>> BO Stream Initialized')
        
    def start(self):
        """Start both streams in background threads"""
        logger.info("-"*60)
        logger.info("Starting Value Bet Finder") 
        logger.info("-"*60)

        logger.info("Performing initial fetch of upcoming events...")
        # try:
        #     self.oddsapi_stream.get_upcoming_event_ids()
        #     logger.info("Initial events fetch complete")
            
        # except Exception as e:
        #     logger.error(f"Failed to fetch initial events: {e}", exc_info=True)
        #     return
        
        # Start periodic refresh (every 2 hours)
        self.oddsapi_stream.start_periodic_refresh(interval_hours=1)
            
        # Start OddsAPI stream in Thread 1
        logger.info("Starting OddsAPI stream...")
        try:
            odds_thread = self.oddsapi_stream.start_threaded()
            logger.info(f"OddsAPI thread started: {odds_thread.is_alive()}")
        except Exception as e:
            logger.error(f"Failed to start OddsAPI stream: {e}", exc_info=True)
            return
        
        # Start BoltOdds stream in Thread 2
        logger.info("Starting BoltOdds stream...")
        try:
            bolt_thread = self.bolt_stream.start_threaded()
            logger.info(f"BoltOdds thread started: {bolt_thread.is_alive()}")
        except Exception as e:
            logger.error(f"Failed to start BoltOdds stream: {e}", exc_info=True)
            return
        
        # Give streams time to connect and populate data
        logger.info("Waiting for streams to initialize (10 seconds)...")
        time.sleep(10)
        
        logger.info("Starting main matching loop on main thread...")
        logger.info("="*60)
        
        # Start the matching loop on the MAIN thread
        try:
            self.run_matcher()
        except KeyboardInterrupt:
            logger.info("\nStopping streams...")
            self.oddsapi_stream.stop()
            self.bolt_stream.stop()
            logger.info("Shutdown complete")
        except Exception as e:
            logger.error(f"Fatal error in main loop: {e}", exc_info=True)
            self.oddsapi_stream.stop()
            self.bolt_stream.stop()
    
    def run_matcher(self):
        """Main loop: runs on MAIN thread, matches events and finds valuebets"""
        iteration = 0
        start_time = time.time()
        runtime_hours = 3
        runtime_seconds = runtime_hours * 3590
        print("------------------------------- Running matcher function --------------------------------------")
        logger.info(f"Matcher will run for {runtime_hours} hours")
        while True:
            
            try:
                # Check if 6 hours have elapsed
                iteration += 1
                
                # Get all events from both sources (thread-safe)
                oddsapi_events = self.oddsapi_stream.return_all_events()
                bolt_events = self.bolt_stream.return_all_events()
                
                logger.info(f"\n--- Iteration {iteration} ---")
                logger.info(f"OddsAPI events: {len(oddsapi_events)}")
                logger.info(f"BoltOdds events: {len(bolt_events)}")
                # if len(bolt_events) > 10:
                #     for event in bolt_events[:10]:
                #         logger.info(event)
                #         logger.info("--------------------------------")
                # if len(oddsapi_events) > 50:
                #     for event in oddsapi_events[0:50]:
                #         logger.info(f"OddsAPI event: {event}")
                #         logger.info("--------------------------------")
            
                # if oddsapi_events:
                #     logger.info(f"Sample OddsAPI event: {oddsapi_events[0]}")
                # if bolt_events:
                #     logger.info(f"Sample BoltOdds event: {bolt_events[0]}")
                # return
                
                # Try to match events
                matches_found = 0
                unique_oa_events = deduplicate_by_key(oddsapi_events, 'event_id')
                # logger.info(f"Number of unique OddsAPI events: {len(unique_oa_events)}")
                
                unique_bolt_events = deduplicate_by_key(bolt_events, 'event_id')
                # logger.info(f"Number of unique BoltOdds events: {len(unique_bolt_events)}")
                # continue
                for oddsapi_event in unique_oa_events:
                    # logger.info(f">>>Looking for match for OddsAPI event: {oddsapi_event}")
                    oddsapi_event_slug = oddsapi_event.get("event_id")
                    # logger.info(f"Found value bets: {self.found_value_bets}")
                    if oddsapi_event_slug in self.found_value_bets:
                        logger.info(f"Skipping event {oddsapi_event_slug} because it's already in the found value bets list")
                        continue
                    oddsapi_sport_slug = oddsapi_event.get("sport")
                    oddsapi_bo_matched_id = oddsapi_event.get("bo_matched_id")
                    #Check if we've already matched this event with a BoltOdds event before and get boltodds event directly, if not, try to search for bolt event match from bolt_events list
                    if oddsapi_bo_matched_id is not None:
                        bolt_event = next((event for event in unique_bolt_events if event.get("event_id") == oddsapi_bo_matched_id), None)
                        # logger.info(f">>>Found matching BoltOdds event: {bolt_event}")
                        if bolt_event is not None:
                            self.compare_odds(oddsapi_events, oddsapi_event.get("event_id"), bolt_events, bolt_event.get("event_id"))
                            continue
                    else:
                        for bolt_event in unique_bolt_events:
                            bolt_event_slug = bolt_event.get("event_id")

                            if oddsapi_event_slug is None or bolt_event_slug is None:
                                continue

                            
                            if events_match(oddsapi_event_slug, bolt_event_slug, oddsapi_sport_slug, threshold=70):
                                matches_found += 1
                                # print('New match found!')
                                # logger.info(f">>> Found new bolt events match: {oddsapi_event} vs {bolt_event}")
                                self.oddsapi_stream.update_bo_matched_id(oddsapi_event.get("event_id"), bolt_event.get("event_id"))

                                # Compare odds for this matched event
                                self.compare_odds(oddsapi_events, oddsapi_event.get("event_id"), bolt_events, bolt_event.get("event_id"))
                        
                
                if matches_found > 0:
                    logger.info(f"\nTotal matches found: {matches_found}")
                else:
                    logger.info("No matches found this iteration")
                
                time.sleep(2)
            except Exception as e:
                logger.error(f"Error in matcher loop: {e}", exc_info=True)
                time.sleep(5)
    
    def compare_odds(self, oddsapi_markets, oa_event_id, bolt_markets, bolt_event_id):
        """Compare odds between matched events to find valuebets"""
    

        
        oa_target_markets = [x for x in oddsapi_markets if x.get("event_id") == oa_event_id]
        # logger.info(f"Number of OddsAPI target markets: {len(oa_target_markets)}")
        # logger.info(f"--------------------- oddsapi_markets_to_compare --------------------------")
        # for m in oa_target_markets:
        #     logger.info(m)
        #     logger.info("--------------------------------")
        bolt_target_markets = [x for x in bolt_markets if x.get("event_id") == bolt_event_id]
        # logger.info(f"Number of BoltOdds target markets: {len(bolt_target_markets)}")

        unique_oa_target_markets = deduplicate_by_key(oa_target_markets, 'market')
        # logger.info(f"Number of unique OddsAPI target markets: {len(unique_oa_target_markets)}")
        # logger.info(f">>> List of unique OddsAPI target markets to compare:")
        # for m in unique_oa_target_markets:
        #     logger.info(m)
        #     logger.info("--------------------------------")
        unique_bolt_target_markets = deduplicate_by_key(bolt_target_markets, 'market')
        
        for oddsapi_market in unique_oa_target_markets:
            if oddsapi_market.get("event_id") in self.found_value_bets:
                break
            # logger.info(f">>> Comparing OddsAPI market: {oddsapi_market}")
            market_matched = False
            for bolt_market in unique_bolt_target_markets:
                oddsapi_market_name = oddsapi_market.get('market')
                bolt_market_name = bolt_market.get('market')
                # logger.info(f"mapping market names: {oddsapi_market_name} vs {bolt_market_name}")
                # Map market names 
                mapped_odds_market_name = map_market_name(oddsapi_market_name)
                mapped_bolt_market_name = map_market_name(bolt_market_name)
                # logger.info(f"mapped market names: {mapped_odds_market_name} vs {mapped_bolt_market_name}")
                
                
                # Only compare if markets match
                if mapped_odds_market_name and mapped_bolt_market_name:
                    if mapped_odds_market_name.lower() == mapped_bolt_market_name.lower():
                        # logger.info(f">>> Market match found for: {oddsapi_market} vs {bolt_market}")
                        market_matched = True
                        oa_target_market_lines = [x for x in oa_target_markets if x.get('market') == oddsapi_market_name]
                        bolt_target_market_lines = [x for x in bolt_target_markets if x.get('market') == bolt_market_name]
                        # logger.info(f">>> Number of OddsAPI target market lines: {len(oa_target_market_lines)}")
                        # logger.info("--------------------- oa_target_market_lines --------------------------")
                        # for m in oa_target_market_lines:
                        #     logger.info(m)
                        #     logger.info("--------------------------------")
                        # logger.info("--------------------- bolt_target_market_lines --------------------------")
                        # for m in bolt_target_market_lines:
                        #     logger.info(m)
                        #     logger.info("--------------------------------")
                        
                        for oa_market_line in oa_target_market_lines:
                            if oa_market_line.get("event_id") in self.found_value_bets:
                                break
                            # logger.info(f">>> Comparing OddsAPI market line: {oa_market_line}")
                            line_match_found = False
                            for bolt_market_line in bolt_target_market_lines:
                                if (any(x in mapped_odds_market_name for x in ["ML", "1x2"]) and oa_market_line.get('selection') == bolt_market_line.get('selection')) or \
                                ('Spread' in mapped_odds_market_name and oa_market_line.get('selection').lower() == bolt_market_line.get('selection').lower() and float(oa_market_line['hdp']) == float(bolt_market_line['hdp'])) or \
                                ('Total' in mapped_odds_market_name and oa_market_line.get('selection').lower() == bolt_market_line.get('selection').lower() and float(oa_market_line['hdp']) == float(bolt_market_line['hdp'])):
                                    
                                    line_match_found = True
                                    oddsapi_price = oa_market_line.get('odds_decimal')
                                    bolt_price = bolt_market_line.get('odds_decimal')
                                    if oddsapi_price < self.input_data['min_prematch_odd'] or oddsapi_price > self.input_data['max_prematch_odd']:
                                        # logger.info(f"Price {oddsapi_price} is not in the min/max range")
                                        continue

                                    # print(f"Odds: oddsapi_price={oddsapi_price} vs bolt_price={bolt_price}")
                                    # Calculate value
                                    if oddsapi_price and bolt_price:
                                        value = calculate_value(oddsapi_price, bolt_price) #value returned in percentage
                                        # logger.info(f"Value: {value}")
                                    else:
                                        logger.info(f"OddsAPI or Bolt price not found.")
                                        logger.info(f"OddsAPI market line: {oa_market_line}")
                                        logger.info(f"Bolt market line: {bolt_market_line}")
                                        continue
                                        
                                    

                                    
                                    # 

                                    if float(value) >= self.input_data['min_value_percentage']:
                                        # logger.info(f"Value bet pair founnd")
                                        print('>>> Found Value Bet!!!')
                                        self.found_value_bets.append(oa_market_line.get("event_id"))
                                        # logger.info(f"Skipping game. {value} is below minimum value {MIN_VALUE})")
                                    else:
                                        continue
                                    logger.info(f">>> Market line match found for: {oa_market_line} vs {bolt_market_line}")
                                    
                                    logger.info(f"Value: {value}")
                                    logger.info(f"Mapped markets: oddsapi_market={mapped_odds_market_name} vs bolt_market={mapped_bolt_market_name}")
                                    
                                    logger.info(f"[Value bet pair found\n"
                                                f"----- VALUE BET SIDE (Duel) -----\n"
                                                f"{oa_market_line}\n"
                                                f"----- CORRESPONDING PINNACLE SIDE -----\n"
                                                f"{bolt_market_line}\n"
                                                f"------------------------------------------")

                                    print(f"Value: {value}")
                                    
                                    print(f"[Value bet pair found\n"
                                                f"----- VALUE BET SIDE (Duel) -----\n"
                                                f"{oa_market_line}\n"
                                                f"----- CORRESPONDING PINNACLE SIDE -----\n"
                                                f"{bolt_market_line}\n"
                                                f"------------------------------------------------------------------------------")

                                    found_valuebet_at = datetime.now(pytz.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

                                    duel_value_bet = {
                                        "home_name": oa_market_line.get("home_team"),
                                        "away_name": oa_market_line.get("away_team"),
                                        'event_id': oa_market_line.get("event_id"),
                                        'oddsapi_event_id': oa_market_line.get("oddsapi_event_id"),
                                        'bo_matched_event_id': oa_market_line.get("bo_matched_id"),
                                        "league": oa_market_line.get("league"),
                                        "market_name": oa_market_line.get("market"),
                                        "selection": oa_market_line.get("selection"),
                                        "pinnacle_odds": bolt_market_line.get("odds_decimal"),
                                        "duel_odds": oa_market_line.get("odds_decimal"),
                                        'value': value,
                                        "sport": oa_market_line.get("sport"),
                                        "hdp": oa_market_line.get("hdp"),
                                        'start_time': oa_market_line.get("when_utc"),
                                        "duel_odds_updated_at": oa_market_line.get("updated_at"),
                                        "found_valuebet_at": found_valuebet_at
                                    }

                                    # Attempt to place bet via DuelClient
                                    settled_odds = None
                                    balance = None
                                    duel_event_id = None
                                    bet_placed = False
                                    
                                    if self.duel_client:
                                        try:
                                            # Fetch Duel event ID from API
                                            oddsapi_id = oa_market_line.get("oddsapi_event_id")
                                            if oddsapi_id:
                                                api_odds_data = get_event_odds(oddsapi_id, self.api_key)
                                                logger.info(f"Latest oddsAPI data: {api_odds_data}")
                                                duel_event_id = get_duel_event_id(api_odds_data)


                                                duel_latest_odds = get_odds_from_data(
                                                            api_odds_data,
                                                            "Duel",
                                                            oa_market_line.get("market"),
                                                            oa_market_line.get("selection"),
                                                            hdp_line=oa_market_line.get("hdp")
                                                        )
                                                logger.info(f"Duel latest odds: {duel_latest_odds}")
                                                pinnacle_latest_odds = bolt_market_line.get("odds_decimal")
                                                if duel_latest_odds is not None and pinnacle_latest_odds is not None:
                                                    value = calculate_value(duel_latest_odds, pinnacle_latest_odds)
                                                    if float(value) >= self.input_data['min_value_percentage']:
                                                        self.found_value_bets.append(oa_market_line.get("event_id"))
                                                    else:
                                                        logger.info(f"No longer a value bet. Duel latest odds: {duel_latest_odds}")
                                                        print(f"No longer a value bet. Duel latest odds: {duel_latest_odds}")
                                                        return
                                                    # Refresh the odds as well for further processing
                                                    oa_market_line["price"] = duel_latest_odds
                                                    logger.info(f"Recalculated value with latest API odds: {value}% (Duel: {duel_latest_odds}, Pinnacle: {pinnacle_latest_odds})")
                                                else:
                                                    print("Unable to find Duel Latest Odds")
                                                    logger.warning("Unable to fetch both Duel and Pinnacle odds from API for event_id {} (duel: {}, pinnacle: {})".format(
                                                        duel_event_id, duel_latest_odds, pinnacle_latest_odds
                                                    ))
                                                    return
                                                
                                                if duel_event_id:
                                                    logger.info(f"Placing bet on Duel event ID: {duel_event_id}")

                                                    


                                                    
                                                    
                                                    # Place bet via DuelClient
                                                    bet_response = self.duel_client.place_bet_sync(
                                                        duel_event_id=duel_event_id,
                                                        sport=oa_market_line.get("sport"),
                                                        market_name=oa_market_line.get("market"),
                                                        selection=oa_market_line.get("selection"),
                                                        hdp=oa_market_line.get("hdp"),
                                                        odds=oa_market_line.get("odds_decimal") 
                                                    )
                                                    
                                                    time.sleep(5)

                                                    if bet_response == "Max stake limit reached":
                                                        self.oddsapi_stream.stop()
                                                        self.bolt_stream.stop()
                                                        input("Max Stake Limit Reached. Hit Enter to Exit Program")
                                                        os._exit(0)
                                                    
                                                    # Check response and get settled odds
                                                    if bet_response.get('error') == []:
                                                        logger.info(f"Bet placed successfully for event {duel_event_id}")
                                                        settled_odds, balance = self.duel_client.get_bet_odds(duel_event_id)
                                                        bet_placed = True
                                                    elif bet_response.get('error')[0]['message'] == "expired_token":
                                                        logger.error("Token expired, please refresh token")
                                                        input("Token seems to have expired. Refresh and restart the program.")
                                                    elif 'Max stake limit' in bet_response.get('error')[0]['message']:
                                                        logger.error("Max stake limit reached. Exiting program.")
                                                        # input("Max Stake Limit Reached. Hit Enter to Continue")
                                                        
                                                    else:
                                                        logger.error(f"Error placing bet: {bet_response}")
                                                        print(f"Error placing bet: {bet_response}")

                                                else:
                                                    logger.warning(f"Could not extract Duel event ID for OddsAPI event {oddsapi_id}")
                                            else:
                                                logger.warning("No OddsAPI event ID available for bet placement")
                                        except Exception as e:
                                            logger.error(f"Error placing bet: {e}", exc_info=True)
                                    else:
                                        logger.warning("DuelClient not initialized, skipping bet placement")

                                    # Save to Google Sheets
                                    alert_data = [
                                        duel_value_bet['sport'],
                                        duel_value_bet['league'],
                                        duel_value_bet['home_name'],
                                        duel_value_bet['away_name'],
                                        duel_value_bet['event_id'],
                                        duel_value_bet['bo_matched_event_id'],
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
                                        balance,
                                        bet_placed
                                    ]
                                    
                                    update_log_to_sheet([alert_data], wks2)
                                    return
                                if line_match_found:
                                    break
                if market_matched:
                    break


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
                if str(entry.get('hdp')).replace('-', '') == str(hdp_line).replace('-', ''):
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
        worksheet.update_values(f"A{row_index}:R{row_index+no_of_alerts}", alert_list)
        logger.info(f"Successfully saved {no_of_alerts} value bet(s) to Google Sheet")
    except Exception as e:
        logger.error(f"Error updating Google Sheet: {e}", exc_info=True)
                                
        
        
def main():
    """Main entry point with DuelClient integration and async event loop."""
    duel_client = None
    finder = None
    loop = None
    finder_thread = None
    
    def signal_handler(sig, frame):
        logger.info("Received interrupt signal")
        if finder:
            finder.oddsapi_stream.stop()
            finder.bolt_stream.stop()
        if duel_client and loop:
            loop.run_until_complete(duel_client.stop())
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
                        sent_bets.append(str(row[4]))
                    except (ValueError, IndexError):
                        continue
        
        # Get user defined inputs
        [[min_prematch_odd, max_prematch_odd, min_live_odd, max_live_odd, kelly_fraction, min_value_percentage]] = wks1.get_values('A2', 'F2')

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
    
    # Initialize DuelClient (async)
    try:
        logger.info("Initializing DuelClient...")
        duel_client = DuelClient(accounts_file='accounts.txt')
        
        # Create and store event loop for async operations
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        duel_client._loop = loop
        
        # Run initialization in the event loop (browser, login, token extraction)
        loop.run_until_complete(duel_client.initialize())
        logger.info("DuelClient initialized successfully")
        
    except Exception as e:
        logger.error(f"Error initializing DuelClient: {e}", exc_info=True)
        logger.warning("Continuing without DuelClient - betting will not be automated")
        duel_client = None
    
    # Initialize ValueBetFinder with DuelClient
    print("Initializing Valuebet Finder...")
    finder = ValueBetFinder(
        input_data=input_data, 
        sent_bets=sent_bets,
        duel_client=duel_client
    )
    
    # Function to run ValueBetFinder in a separate thread
    def run_value_bet_finder():
        try:
            logger.info("Starting ValueBetFinder in background thread...")
            finder.start()
        except Exception as e:
            logger.error(f"Fatal error in ValueBetFinder thread: {e}", exc_info=True)
        finally:
            if finder:
                finder.oddsapi_stream.stop()
                finder.bolt_stream.stop()
    
    # Start ValueBetFinder in a daemon thread
    finder_thread = threading.Thread(target=run_value_bet_finder, daemon=True)
    finder_thread.start()
    logger.info("ValueBetFinder started in background thread")
    
    # Keep main thread alive to maintain DuelClient and token refresh
    try:
        logger.info("Main thread running to maintain DuelClient and token refresh...")
        logger.info("Press Ctrl+C to stop")
        
        # Run async event loop in main thread for token refresh
        async def run_main_loop():
            while True:
                await asyncio.sleep(1)
                
                # Check if token refresh is needed
                if duel_client:
                    await duel_client.refresh_token_if_needed()
                
                # Check if finder thread is still alive
                if not finder_thread.is_alive():
                    logger.warning("ValueBetFinder thread has stopped")
                    break
        
        # Run the async main loop
        if loop:
            loop.run_until_complete(run_main_loop())
        else:
            # If no DuelClient, just keep main thread alive
            while finder_thread.is_alive():
                time.sleep(1)
        
    except KeyboardInterrupt:
        logger.info("Received interrupt signal in main thread")
    except Exception as e:
        logger.error(f"Error in main thread: {e}", exc_info=True)
    finally:
        if finder:
            finder.oddsapi_stream.stop()
            finder.bolt_stream.stop()
        if duel_client and loop:
            loop.run_until_complete(duel_client.stop())
            loop.close()
        logger.info("Shutting down...")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Failed to start: {e}")
        logger.error(f"Failed to start application: {e}", exc_info=True)