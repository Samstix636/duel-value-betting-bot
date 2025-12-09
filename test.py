import os
import dotenv
import requests
from pprint import pprint
from datetime import datetime, timedelta
import pytz
from typing import Optional, Dict, Any
dotenv.load_dotenv()

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
    
    response.raise_for_status()
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


if __name__ == "__main__":
    # Test the function
    data = get_event_odds("62888053")
    pprint(data)
    
    # Example usage of get_odds_from_data
    # print("\n" + "="*80)
    # print("Testing get_odds_from_data function:")
    # print("="*80)
    
    # # Example 1: Get ML odds (no hdp_line needed)
    # duel_home_odds = get_odds_from_data(data, 'Duel', 'ML', 'home')
    # print(f"\nDuel ML Home odds: {duel_home_odds}")
    
    # # duel_away_odds = get_odds_from_data(data, 'Duel', 'ML', 'away')
    # # print(f"Duel ML Away odds: {duel_away_odds}")
    
    # # Example 2: Get Totals odds (hdp_line required)
    # duel_totals_over = get_odds_from_data(data, 'Duel', 'Totals', 'over', hdp_line=59.5)
    # print(f"\nDuel Totals Over 59.5 odds: {duel_totals_over}")
    
    # pinnacle_totals_under = get_odds_from_data(data, 'Pinnacle', 'Totals', 'under', hdp_line=59.5)
    # print(f"Pinnacle Totals Under 59.5 odds: {pinnacle_totals_under}")