from datetime import datetime, timedelta
from thefuzz import fuzz
from rapidfuzz import process, fuzz as rf_fuzz
import re
import logging
import pytz

logger = logging.getLogger("Helper") 
logger.setLevel(logging.INFO)          

def american_to_decimal(american_odds: str | int | None) -> float | None:
    if american_odds is None:
        return None

    try:
        odds = int(american_odds)
    except (TypeError, ValueError):
        return None

    if odds > 0:
        decimal = (odds / 100) + 1
    else:
        decimal = (100 / abs(odds)) + 1

    return round(decimal, 2)

def est_to_utc(time_str: str) -> str:
    """
    Convert a time string in EST to UTC, formatted as 'YYYY-MM-DDTHH:MM:SSZ'.
    Expects input: 'YYYY-MM-DD, HH:MM AM/PM'
    """
    # Parse input string
    dt_naive = datetime.strptime(time_str, "%Y-%m-%d, %I:%M %p")
    
    # Localize to EST/EDT (America/New_York handles both)
    est_tz = pytz.timezone("America/New_York")
    dt_est = est_tz.localize(dt_naive)
    
    # Convert to UTC
    dt_utc = dt_est.astimezone(pytz.UTC)
    
    # Format in ISO 8601 style with 'Z'
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

def is_within_minute(time_str, minute_val = 2):
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


def calculate_value(slower_odds, sharp_odds):
    value = (float(slower_odds) - float(sharp_odds))*100/(float(sharp_odds))
    value = round(value, 2)
    return value

def is_less_than_24_hours_away(time_str: str) -> bool:
    if not time_str:
        return False

    # normalize case
    ts = time_str.lower()

    # convert 2026-01-17t200000z → 2026-01-17T20:00:00Z
    ts = re.sub(
        r'(\d{4}-\d{2}-\d{2})t(\d{2})(\d{2})(\d{2})z',
        r'\1T\2:\3:\4Z',
        ts
    )

    try:
        given_time = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.UTC)
    except ValueError:
        return False

    current_time = datetime.now(pytz.UTC)
    time_difference = given_time - current_time

    return timedelta(0) < time_difference <= timedelta(hours=48)

def is_event_started(time_str: str) -> bool:
    try:
        event_time = datetime.strptime(time_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.UTC)
    except Exception:
        return False
    now = datetime.now(pytz.UTC)
    return event_time <= now

def clean_slug(slug):
    slug = slug.lower()
    
    # Remove special characters, keep only alphanumeric, spaces, and pipes
    slug = re.sub(r'[^a-z0-9\s\-|:.]', '', slug)
    
    # Replace multiple spaces with single space
    slug = re.sub(r'\s+', ' ', slug)
    
    # Replace spaces with hyphens
    slug = slug.replace(' ', '-')
    
    # Remove multiple consecutive hyphens (but keep pipes intact)
    slug = re.sub(r'-+', '-', slug)
    
    # Clean up any hyphens adjacent to pipes
    slug = re.sub(r'-\|', '|', slug)
    slug = re.sub(r'\|-', '|', slug)
    
    return slug.strip('-')

league_map = {
    # boltodds : oddsapi
    "epl": "england-premier-league",
    "pl": "england-premier-league",
    "la-liga": "spain-laliga",
    "laliga": "spain-laliga",
    "bundesliga": "germany-bundesliga",
    "nba": "national-basketball-association",
    "ncaab": "ncaa-mens-basketball",
    "ncaab (w)": "ncaa-womens-basketball",
    "ncaab-women": "ncaa-womens-basketball",
    "wnba": "womens-national-basketball-association",
    "nfl": "national-football-league",
    "cfl": "canadian-football-league",
    "nhl": "national-hockey-league",
    "mls": "major-league-soccer",
    "efl championship": "english-football-league-championship", 
    "mlb": "major-league-baseball",
    "ncaa hockey": "national-collegiate-athletic-association-hockey",
    "ncaa baseball": "national-collegiate-athletic-association-baseball",
    "atp": "association-of-tennis-professionals",
    "wta": "womens-tennis-association",
    "ncaa football": "national-collegiate-athletic-association-football",
    "primeira-liga":"portugal-liga-portugal",
    "champions-league": "international-clubs-uefa-champions-league"
}

team_map= {
    # boltodds : oddsapi
    "estrela": "estrela-amadora",
    "estoril": "estoril-praia",
    "verona": "hellas-verona",

}

def normalize_league(league, league_map=league_map, threshold=70):
    league_clean = league.lower()

    # Replace gender markers
    league_clean = league_clean.replace("(m)", "men")
    league_clean = league_clean.replace("(w)", "women")

    # Remove the word 'tennis'
    league_clean = league_clean.replace("tennis", "")
    league_clean = league_clean.replace("international clubs", "") 

    # Remove special characters except hyphens and spaces
    league_clean = re.sub(r'[^a-z0-9\s-]', '', league_clean)

    # Replace multiple spaces with single space
    league_clean = re.sub(r'\s+', ' ', league_clean).strip()

    # Replace spaces with hyphens **after cleaning**
    league_clean = league_clean.replace(' ', '-')
    
    # Exact match first
    if league_clean in league_map:
        return league_map[league_clean]
    
    # Fuzzy match: find closest key in league_map
    match = process.extractOne(
    query=league_clean,
    choices=league_map.keys(),
    scorer=rf_fuzz.token_sort_ratio
)
        
    if match and match[1] >= threshold:  # unpack: match[0] = key, match[1] = score
        return league_map[match[0]]  # return canonical name
    
    return league_clean  # fallback to cleaned string

def normalize_team(team, team_map=team_map, threshold=70):
    if not team:
        return ""

    # Lowercase
    team_clean = team.lower()

    # Remove special characters except letters, numbers, and spaces
    team_clean = re.sub(r'[^a-z0-9\s-]', '', team_clean)

    # Replace multiple spaces with single space
    team_clean = re.sub(r'\s+', ' ', team_clean).strip()

    # Replace spaces with hyphens
    team_clean = team_clean.replace(' ', '-')

    # Exact match first
    if team_clean in team_map:
        return team_map[team_clean]
    
    # Fuzzy match: find closest key in league_map
    match = process.extractOne(
    query=team_clean,
    choices=team_map.keys(),
    scorer=rf_fuzz.token_sort_ratio
)
        
    if match and match[1] >= threshold:  
        return team_map[match[0]] 
    
    return team_clean

failed_matches: set[tuple[str, str]] = set()

def events_match(slug1: str, slug2: str, oddsapi_sport_slug: str, threshold: int = 65) -> tuple[str | None, bool]:
    # skip if we already know this pair failed
    # if (slug1, slug2) in failed_matches:
    #     logger.info("Skipping already seen failed match set")
    #     return False
    
    # slug 1 is from odds api, slug 2 is from bolt odds
    cleaned_slug1 = clean_slug(slug1)
    cleaned_slug2 = clean_slug(slug2)
    is_match = False
    
    
    try:
        sport1, home1, away1, date1 = cleaned_slug1.split("|", 3)
        sport2, home2, away2, date2 = cleaned_slug2.split("|", 3)
    except ValueError:
        return False
    
    # if not sport1 or not sport2:
        logger.info(f"Sports don't match: {sport1} vs {sport2}")
        return False
    
    

    # else:
    #     return False  # different sports → impossible match

    cleanhome1 = normalize_team(home1)
    cleanhome2 = normalize_team(home2)
    home_score = fuzz.token_sort_ratio(cleanhome1, cleanhome2)

    cleanaway1 = normalize_team(away1)
    cleanaway2 = normalize_team(away2)
    away_score = fuzz.token_sort_ratio(cleanaway1, cleanaway2)

    # if home_score < threshold or away_score < threshold:
    #     logger.info(f"Scores don't match: Home Score - {home_score} vs Away Score - {away_score}")
    #     return False
    
    normalized_slug1 = f"{sport1}|{cleanhome1}|{cleanaway1}|{date1}"
    normalized_slug2 = f"{sport2}|{cleanhome2}|{cleanaway2}|{date2}"

    if home_score >= 65 and away_score >= 65:
        is_match = True
        logger.info(f"Team matched: {cleanhome1} vs {cleanhome2} = {home_score} and {cleanaway1} vs {cleanaway2} = {away_score}")
        # print(f"Team matched: {cleanhome1} vs {cleanhome2} = {home_score} and {cleanaway1} vs {cleanaway2} = {away_score}")
    else:
        return False


    if not is_within_15_minutes(date1.lower(), date2.lower()):
        logger.info(f"Starting times are far apart: {date1} vs {date2}")
        return False # Dates don't match exactly → no need to continue

    # if sport1.lower() == sport2.lower():
    #     logger.info(f"Sports matched: {sport1} vs {sport2}")

    if not is_match:
        return False

    return True

def is_within_15_minutes(time_str1, time_str2):
    """
    Compare two ISO8601 UTC time strings and check if they are within 15 minutes of each other.

    Args:
        time_str1 (str): Time string in format "2026-01-25t11:30:00z"
        time_str2 (str): Time string in format "2026-01-25t11:30:00z"

    Returns:
        bool: True if times are within 15 minutes either way, False otherwise
    """
    from datetime import datetime, timezone

    def parse_utc(dtstr):
        # Some iso strings are lower/upper 't'
        # Accept with or without 'Z' at end, always treat as UTC
        s = dtstr.strip()
        if "t" in s:
            s = s.replace("t", "T")
        if s.lower().endswith("z"):
            s = s[:-1]
        try:
            return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            try:
                # Try parsing with milliseconds if present
                return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=timezone.utc)
            except Exception:
                raise

    try:
        d1 = parse_utc(time_str1)
        d2 = parse_utc(time_str2)
    except Exception as e:
        # If parsing fails, consider not within 15 minutes
        return False

    delta = abs((d1 - d2).total_seconds())
    return delta <= 15 * 60

def deduplicate_by_key(dict_list, key):
    """
    Deduplicate a list of dictionaries by a specific key.
    Keeps the first occurrence of each unique key value.
    
    Args:
        dict_list: List of dictionaries
        key: Key to deduplicate on
        
    Returns:
        List of unique dictionaries
    """
    seen = {}
    result = []
    for item in dict_list:
        key_value = item.get(key)
        if key_value not in seen:
            seen[key_value] = True
            result.append(item)
    return result


market_map = {
    # BoltOdds/odds api market : normalized mapped market name
    "Moneyline": "ML",
    "ML": "ML",
    "3 Way": "1x2",
    "3-Way Result": "1x2",

    "Spread": "Spread",
    "Asian Spread": "Spread",
    "Asian Handicap": "Spread",

    "1st Half Spread": "Spread HT",
    "Spread HT": "Spread HT",
    "1st Half Asian Spread": "Spread HT",
    "Asian Handicap HT": "Spread HT",

    "1st Half Moneyline": "ML HT",
    "ML HT": "ML HT",

    "Total Goals": "Totals",
    "Totals": "Totals",
    "Total Points": "Totals",
    "Total": "Totals",
    
    "1st Half Total Goals": "Totals HT",
    "1st Half Total Points": "Totals HT",
    "1st Half Total": "Totals HT",
    "Totals HT": "Totals HT"
    
    
}

def map_market_name(raw_market):
    # logger.info(f"Mapping market name: {raw_market}")
    return market_map.get(raw_market, None)


def transpose_duel_market_name(market_name, sport):
    if market_name == "First Set Winner":
        return 'ML 1st Set'
    elif sport == "football" and market_name == "ML":
        return "3 Way"
    else:
        return market_name


def get_sport_from_league(league: str) -> str | None:
    league = league.strip().lower().replace(" ", "-")
    # logger.info(f"searching for League: {league}")

    sport_map = {
        "ice-hockey": [
            "nhl",
            "ncaa-hockey",
            "national-hockey-league",
            "national-collegiate-athletic-association-hockey",
        ],
        "basketball": [
            "nba",
            "ncaab",
            "ncaab-w",
            "wnba",
            "ncaa-mens-basketball",
            "ncaa-womens-basketball",
            "womens-national-basketball-association",
            "nba-summer",
            "nba-preseason",
            "euroleague",
            "national-basketball-association",
        ],
        "baseball": [
            "mlb",
            "ncaa-baseball",
            "major-league-baseball",
            "national-collegiate-athletic-association-baseball",
        ],
        "american-football": [
            "nfl",
            "ncaa-football",
            "cfl",
            "nfl-preseason",
            "national-collegiate-athletic-association-football",
            
        ],
        "football": [
            "mls",
            "bundesliga",
            "la-liga",
            "ligue-1",
            "serie-a",
            "epl", "pl", "spain-laliga", "laliga", "germany-bundesliga",
            "efl-championship", "english-football-league-championship", 
            "primeira-liga",
            "major-league-soccer", "portugal-liga-portugal",
            "england-premier-league", "champions-league", 
            "international-clubs-uefa-champions-league",
            "world-cup",
        ],
    }

    for sport, leagues in sport_map.items():
        if league in leagues:
            return sport

    return None
    
