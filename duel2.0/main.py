import time
import os
from dotenv import load_dotenv
from oddsapi import OddsAPIStreamClient
from boltodds import BoltOddsStreamClient
from helper import events_match, calculate_value, map_market_name
import logging
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

# --- Application log (main decisions) ---
app_handler = logging.FileHandler("logs/main.log")
app_handler.setFormatter(formatter)
app_handler.setLevel(logging.INFO)

# Attach to root
root_logger.addHandler(app_handler)
# root_logger.addHandler(error_handler)

# Main logger
logger = logging.getLogger("MainLog")

MIN_VALUE = 1.0 
class ValueBetFinder:
    def __init__(self):
        logger.info("Initializing ValueBetFinder...")
        
        # Load API keys from environment
        odds_api_key = os.getenv("odds_api_key")
        boltodds_api_key = os.getenv("boltodds_api_key")
        
        # Validate keys exist
        if not odds_api_key:
            raise ValueError("odds_api_key not found in .env file")
        if not boltodds_api_key:
            raise ValueError("boltodds_api_key not found in .env file")
        
        logger.info("API keys loaded successfully")
        
        # Initialize both stream clients
        self.oddsapi_stream = OddsAPIStreamClient(odds_api_key)
        self.bolt_stream = BoltOddsStreamClient(f"wss://spro.agency/api?key={boltodds_api_key}")
                
        logger.info("Stream clients initialized")
        
    def start(self):
        """Start both streams in background threads"""
        logger.info("-"*60)
        logger.info("Starting Value Bet Finder") 
        logger.info("-"*60)

        logger.info("Performing initial fetch of upcoming events...")
        try:
            self.oddsapi_stream.get_upcoming_event_ids()
            logger.info("Initial events fetch complete")
            
        except Exception as e:
            logger.error(f"Failed to fetch initial events: {e}", exc_info=True)
            return
        
        # Start periodic refresh (every 2 hours)
        self.oddsapi_stream.start_periodic_refresh(interval_hours=2)
            
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
        
        while True:
            try:
                iteration += 1
                
                # Get all events from both sources (thread-safe)
                oddsapi_events = self.oddsapi_stream.return_all_events()
                bolt_events = self.bolt_stream.return_all_events()
                
                logger.info(f"\n--- Iteration {iteration} ---")
                logger.info(f"OddsAPI events: {len(oddsapi_events)}")
                logger.info(f"BoltOdds events: {len(bolt_events)}")
                
            
                # if oddsapi_events:
                #     logger.info(f"Sample OddsAPI event: {oddsapi_events[0]}")
                # if bolt_events:
                #     logger.info(f"Sample BoltOdds event: {bolt_events[0]}")
                
                # Try to match events
                matches_found = 0
                
                for oddsapi_event in oddsapi_events:
                    oddsapi_event_slug = oddsapi_event.get("id")
                    oddsapi_sport_slug = oddsapi_event.get("sport")
                    for bolt_event in bolt_events:
                        bolt_event_slug = bolt_event.get("id")

                        if oddsapi_event_slug is None or bolt_event_slug is None:
                            continue

                        sport2, is_match = events_match(oddsapi_event_slug, bolt_event_slug, oddsapi_sport_slug, threshold=70)

                        if is_match:
                            matches_found += 1
                            logger.info(f"\nMATCH FOUND!\n"
                                        f"  OddsAPI ID: {oddsapi_event.get('id')}\n"
                                        f"  BoltOdds ID: {bolt_event.get('id')}\n"
                                        f"  Event: {oddsapi_event.get('home_team')} vs {oddsapi_event.get('away_team')}\n"
                                        f"  League: {oddsapi_event.get('league')}"
                                    )
                            bolt_event['sport'] = sport2


                            # Compare odds for this matched event
                            self.compare_odds(oddsapi_event, bolt_event)
                
                if matches_found > 0:
                    logger.info(f"\nTotal matches found: {matches_found}")
                else:
                    logger.info("No matches found this iteration")
                
                time.sleep(2)
            except Exception as e:
                logger.error(f"Error in matcher loop: {e}", exc_info=True)
                time.sleep(5)
    
    def compare_odds(self, oddsapi_event, bolt_event):
        """Compare odds between matched events to find valuebets"""


        logger.info(f"Crosscheck this - Matching bet pair found\n"
                    f"----- (Duel) -----\n"
                    f"{oddsapi_event}\n"
                    f"----- Pinnacle -----\n"
                    f"{bolt_event}\n"
                    f"------------------------------------------")

        
        # Get details from oddsappi_event (Duel) & bolt_event (Pinnacle)
        oddsapi_market = oddsapi_event.get('market')
        bolt_market = bolt_event.get('market')

        oddsapi_price = oddsapi_event.get('odds_decimal')
        bolt_price = bolt_event.get('odds_decimal')

        # # Calculate value
        # if oddsapi_price and bolt_price:
        #     value = calculate_value(oddsapi_price, bolt_price) #value returned in percentage

        # if float(value) < MIN_VALUE:
        #     logger.info(f"Skipping game. {value} is below minimum value {MIN_VALUE})")
        #     return None

        oddsapi_hdp = oddsapi_event.get('hdp') # e.g 0.5
        bolt_line = bolt_event.get('outcome_line') # e.g 0.25


        oddsapi_selection = oddsapi_event.get('selection') #e.g home, over, under, away, draw
        
        bolt_over_under = bolt_event.get("outcome_over_under")  # e.g., "O" or "U"
        bolt_target = bolt_event.get("outcome_target")        # e.g., Away name, Team name or "Draw"

        bolt_home = bolt_event.get('home_team', '').strip()
        bolt_away = bolt_event.get('away_team', '').strip()

        # now outcome_target isn't "Home", "Away", "Draw", it's either the actual home team name or away team name, so you have to check and then replace 
        # bolt target with either home away or draw(draw will be written there)

        if bolt_over_under is not None:
            # Map "O"/"U" to "over"/"under"
            over_under_map = {"o": "over", "u": "under"}
            over_under = over_under_map.get(bolt_over_under.lower(), bolt_over_under)
            bolt_selection = over_under_map.get(bolt_over_under.lower())
        elif bolt_target:
            # Map team name to "home", "away", or "draw"
            bolt_target_lower = bolt_target.lower().strip()
            
            if bolt_target_lower == "draw":
                bolt_selection = "draw"
            elif bolt_target_lower == bolt_home.lower():
                bolt_selection = "home"
            elif bolt_target_lower == bolt_away.lower():
                bolt_selection = "away"
            else:
                logger.warning(f"Could not map bolt_target '{bolt_target}' to home/away/draw. "
                            f"Bolt teams: {bolt_home} vs {bolt_away}")
                return
        else:
            logger.info("No valid bolt selection found (no over/under or target)")
            return
        
        # Ensure selections match
        if bolt_selection is not None:
            if oddsapi_selection.lower() != bolt_selection.lower():
                logger.info(f"Selections don't match: {oddsapi_selection} vs {bolt_selection}")
                return
            
        
        # Map market names 
        mapped_odds_market = map_market_name(oddsapi_market)
        mapped_bolt_market = map_market_name(bolt_market)
        
        # Only compare if markets match
        if mapped_odds_market and mapped_bolt_market:
            if mapped_odds_market.lower() != mapped_bolt_market.lower():
                logger.info(f"Markets don't match: {mapped_odds_market} vs {mapped_bolt_market}")
                return
            
        # Calculate value
        if oddsapi_price and bolt_price:
            value = calculate_value(oddsapi_price, bolt_price) #value returned in percentage

        if float(value) < MIN_VALUE:
            logger.info(f"Skipping game. {value} is below minimum value {MIN_VALUE})")
            return None
        
        logger.info(f"[Value bet pair found\n"
                    f"----- VALUE BET SIDE (Duel) -----\n"
                    f"{oddsapi_event}\n"
                    f"----- CORRESPONDING PINNACLE SIDE -----\n"
                    f"{bolt_event}\n"
                    f"------------------------------------------")
        
        

        
if __name__ == "__main__":
    try:
        finder = ValueBetFinder()
        finder.start()
    except Exception as e:
        print(f"Failed to start: {e}")
        logger.error(f"Failed to start application: {e}", exc_info=True)