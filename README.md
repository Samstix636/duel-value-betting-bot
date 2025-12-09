# Odds API - Value Bet Finder & Automated Betting

Automated value bet detection system that monitors odds from Duel and Pinnacle sportsbooks via WebSocket, identifies value betting opportunities, and places bets automatically on Duel.com.

## Features

- **Real-time Odds Monitoring**: WebSocket connection to Odds-API.io for live odds updates
- **Value Bet Detection**: Compares Duel and Pinnacle odds to identify profitable betting opportunities
- **Automated Betting**: Integrates with Duel.com to place bets automatically using Playwright
- **Google Sheets Integration**: Reads configuration and logs value bets to Google Sheets
- **Smart Filtering**: Filters by sport, market type, time windows, and minimum value thresholds
- **Proxy Support**: Supports proxy configuration for Duel.com access
- **Token Management**: Automatic authentication token refresh every 15 minutes

## Requirements

- Python 3.8+
- Google Sheets API credentials (`google_client.json`)
- Odds-API.io API key
- Duel.com account credentials
- Playwright browser automation
- Proxy configuration (optional but recommended)

## Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install firefox

# Set up environment variables
cp .env.example .env
# Edit .env with your API keys and credentials
```

## Configuration

### Environment Variables

Create a `.env` file with:

```env
ODDS_API_KEY=your_odds_api_key
DUEL_USERNAME=your_duel_username
DUEL_PASSWORD=your_duel_password
```

### Google Sheets Setup

1. Create a Google Sheet with two worksheets:
   - **Input**: Contains betting parameters (min/max odds, value thresholds, etc.)
   - **valuebet_system_2**: Logs detected value bets

2. Place your `google_client.json` service account file in the project root

### Accounts File

Create `accounts.txt` with format:
```
account_name,username,password,proxy_host:proxy_port:proxy_user:proxy_pass
```

## Usage

```bash
python valuebet.py
```

The system will:
1. Connect to Odds-API WebSocket for real-time odds
2. Monitor Duel and Pinnacle odds for matching markets
3. Calculate value percentage when both bookmakers have odds
4. Filter by your configured thresholds
5. Automatically place bets on Duel.com when value bets are found
6. Log all value bets to Google Sheets

## Supported Markets

- Moneyline (ML)
- Spread
- Totals (Over/Under)
- Team Totals
- First Half markets
- Tennis-specific markets (Games, Sets)

## Architecture

- **`valuebet.py`**: Main application that monitors odds and orchestrates betting
- **`duel_client.py`**: Duel.com automation client using Playwright for browser automation and API calls

## Notes

- The system filters events to only those starting within 24-48 hours
- Tennis events must start >45 minutes away, other sports >2 minutes
- Bets are placed with 1.5% of account balance as stake
- Handball ML markets are automatically skipped

## License

Private project - All rights reserved

