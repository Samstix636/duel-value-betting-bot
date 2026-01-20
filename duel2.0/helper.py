from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
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
    
    # Localize to EST
    dt_est = dt_naive.replace(tzinfo=ZoneInfo("America/New_York"))
    
    # Convert to UTC
    dt_utc = dt_est.astimezone(ZoneInfo("UTC"))
    
    # Format in ISO 8601 style with 'Z'
    return dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

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

    return timedelta(0) < time_difference <= timedelta(hours=24)

def clean_slug(slug):
    slug = slug.lower()
    
    # Remove special characters, keep only alphanumeric, spaces, and pipes
    slug = re.sub(r'[^a-z0-9\s\-|]', '', slug)
    
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
    if (slug1, slug2) in failed_matches:
        logger.info("Skipping already seen failed match set")
        return False
    
    # slug 1 is from odds api, slug 2 is from bolt odds
    cleaned_slug1 = clean_slug(slug1)
    cleaned_slug2 = clean_slug(slug2)
    
    try:
        sport1, home1, away1, date1 = cleaned_slug1.split("|", 3)
        sport2, home2, away2, date2 = cleaned_slug2.split("|", 3)
    except ValueError:
        return False
    
    if date1 != date2:
        return False # Dates don't match exactly → no need to continue
    
    if not is_less_than_24_hours_away(date1):
        return False
    
    if not sport1 or not sport2:
        return False
    
    if sport1.lower() != sport2.lower():
        return False  # different sports → impossible match

    cleanhome1 = normalize_team(home1)
    cleanhome2 = normalize_team(home2)
    home_score = fuzz.token_sort_ratio(cleanhome1, cleanhome2)

    cleanaway1 = normalize_team(away1)
    cleanaway2 = normalize_team(away2)
    away_score = fuzz.token_sort_ratio(cleanaway1, cleanaway2)

    if home_score < threshold or away_score < threshold:
        return False
    
    normalized_slug1 = f"{sport1}|{cleanhome1}|{cleanaway1}|{date1}"
    normalized_slug2 = f"{sport2}|{cleanhome2}|{cleanaway2}|{date2}"

    if home_score >= 50 or away_score >= 50:
        logger.info("Odds comparison:\n"
        "  Odds API slug: %s\n"
        "  Bolts Odds slug: %s\n"
        "  Odds API normalized slug: %s\n"
        "  Bolts Odds normalized slug: %s\n"
        "  Scores -> home: %s, away: %s", 
        slug1, slug2, normalized_slug1, normalized_slug2, home_score, away_score)

    is_match = (home_score >= threshold and away_score >= threshold)

    if not is_match:
        return False

    return True

market_map = {
    # BoltOdds : OddsAPI
    "Moneyline": "ML",
    "Spread": "Spread",
    "1st Half Spread": "Spread HT",
    "1st Half Moneyline": "ML HT",
    "Total Goals": "Totals",
    "1st Half Total Goals": "Totals HT",
    "1st Half Asian Spread": "Asian Handicap HT",
    "Asian Spread": "Asian Handicap",
    "3 Way": "ML",
    "Total": "Totals",
    "1st Half Total": "Totals HT"
}

def map_market_name(raw_market):
    return market_map.get(raw_market, raw_market)


def get_sport_from_league(league: str) -> str | None:
    league = league.strip().lower()

    sport_map = {
        "hockey": {
            "nhl",
            "ncaa-hockey",
            "national-hockey-league",
            "national-collegiate-athletic-association-hockey",
        },
        "basketball": {
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
        },
        "baseball": {
            "mlb",
            "ncaa-baseball",
            "major-league-baseball",
            "national-collegiate-athletic-association-baseball",
        },
        "tennis": {
            "grand-slams",
            "atp",
            "wta",
            "association-of-tennis-professionals",
            "womens-tennis-association",
            "atp-wta-tours",
            "challenger-tournaments",
            "itf-events",
        },
        "football": {
            "nfl",
            "ncaa-football",
            "cfl",
            "nfl-preseason",
            "national-collegiate-athletic-association-football",
            
        },
        "soccer": {
            "mls",
            "bundesliga",
            "la-liga",
            "ligue-1",
            "serie-a",
            "epl", "pl", "spain-laliga", "laliga", "germany-bundesliga",
            "efl championship", "english-football-league-championship", 
            "primeira-liga",
            "major-league-soccer", "portugal-liga-portugal"
            "england-premier-league", "champions-league", 
            "international-clubs-uefa-champions-league",
        },
    }

    for sport, leagues in sport_map.items():
        if league in leagues:
            return sport

    return None
