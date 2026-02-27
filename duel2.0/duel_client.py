import os
import time
import random
import logging
import requests
import threading
import asyncio
import json
import re
from datetime import datetime
from typing import Optional, Dict, Any
from pathlib import Path
from playwright.async_api import async_playwright, Page, BrowserContext
import dotenv
from aioconsole import ainput
import math
dotenv.load_dotenv()

logger = logging.getLogger("DuelClient")

class DuelClient:
    """
    Client for automating Duel.com betting operations using Playwright.
    Handles authentication, token extraction, and bet placement.
    Connects to a Kameleo Chroma browser profile via CDP WebSocket.
    """
    
    def __init__(self, timeout: int = 120000, accounts_file: str = "accounts.txt"):
        """
        Initialize the DuelClient.
        
        Args:
            timeout: Default timeout for page operations in milliseconds
            accounts_file: Path to the accounts.txt file containing account details
        """
        self.timeout = timeout
        self.accounts_file = accounts_file
        self.playwright = None
        self.browser = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.auth_token: Optional[str] = None
        self.base_url = "https://duel.com"
        self.api_base_url = "https://api-a-c7818b61-600.sptpub.com"
        self.token_refresh_timer: Optional[threading.Timer] = None
        self.token_refresh_interval = 5 * 60  # 5 minutes in seconds
        self._token_refresh_lock = threading.Lock()
        self._is_running = True
        self.balance = 0
        self.target_button_selectors = [
                '#bt-root > div > div > div:nth-child(2) > div:nth-child(1) > div > div > div > div > div > div > div:nth-child(1)',
                '#bt-root > div > div > div:nth-child(2) > div:nth-child(1) > div > div > div > div > div > div > div:nth-child(2)'
            ]
        self._token_refresh_event = threading.Event()  # Event to signal main thread to refresh token
        self._loop: Optional[asyncio.AbstractEventLoop] = None  # Event loop for async operations
        self._page_lock: Optional[asyncio.Lock] = None  # Async lock to prevent concurrent Playwright page operations
        
        # Account information
        self.selected_account: Optional[Dict[str, str]] = None
        self.proxy_config: Optional[Dict[str, str]] = None

        self.x_session_id = None
        self.x_ga_client_id = None
        self.x_ga_session_id = None
        self.x_ua_model = None
        self.x_ua_platform_version = None
        self.kameleo_profile_id = None
        self.user_agent = None
        self.stake_amount = None
        
    def _load_session_params(self, filepath: str = "input.txt"):
        """
        Read session parameters and Kameleo profile ID from a file.
        Expected format (one key=value per line):
            x_session_id=<value>
            x_ga_client_id=<value>
            x_ga_session_id=<value>
            kameleo_profile_id=<value>
            user_agent=<value>
            x_ua_model=<value>
            x_ua_platform_version=<value>
            stake_amount=<value>
            if stake_amount is not None:
                self.stake_amount = int(stake_amount)
            else:
                self.stake_amount = 100
        """
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Session params file not found: {filepath}")

        params = {}
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    key, value = line.split('=', 1)
                    params[key.strip()] = value.strip()

        required = ['x_session_id', 'x_ga_client_id', 'x_ga_session_id', 'kameleo_profile_id', 'user_agent', 'x_ua_model', 'x_ua_platform_version', 'stake_amount']
        for key in required:
            if key not in params or not params[key]:
                raise ValueError(f"Missing or empty '{key}' in {filepath}")

        self.x_session_id = params['x_session_id']
        self.x_ga_client_id = params['x_ga_client_id']
        self.x_ga_session_id = params['x_ga_session_id']
        self.kameleo_profile_id = params['kameleo_profile_id']
        self.user_agent = params['user_agent']
        self.x_ua_model = params['x_ua_model']
        self.x_ua_platform_version = params['x_ua_platform_version']
        self.stake_amount = int(params['stake_amount'])
        logger.info(f"Loaded session params from {filepath}")

    @staticmethod
    def read_accounts(accounts_file: str) -> Dict[str, Dict[str, str]]:
        """
        Read accounts from accounts.txt file.
        
        Format: account_name,username,password,proxy_host:proxy_port:proxy_user:proxy_pass
        
        Args:
            accounts_file: Path to accounts.txt file
            
        Returns:
            Dictionary mapping account names to account details
        """
        accounts = {}
        
        if not os.path.exists(accounts_file):
            raise FileNotFoundError(f"Accounts file not found: {accounts_file}")
        
        try:
            with open(accounts_file, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    
                    # Format: account_name,username,password,proxy_host:proxy_port:proxy_user:proxy_pass
                    parts = line.split(',')
                    if len(parts) != 4:
                        logger.warning(f"Invalid format on line {line_num} (expected 4 comma-separated parts): {line}")
                        continue
                    
                    account_name = parts[0].strip()
                    username = parts[1].strip()
                    password = parts[2].strip()
                    proxy_info = parts[3].strip()
                    
                    # Parse proxy information (format: host:port:user:pass)
                    proxy_parts = proxy_info.split(':')
                    if len(proxy_parts) != 4:
                        logger.warning(f"Invalid proxy format on line {line_num} (expected host:port:user:pass): {proxy_info}")
                        continue
                    
                    proxy_host, proxy_port, proxy_user, proxy_pass = proxy_parts
                    
                    accounts[account_name] = {
                        'username': username,
                        'password': password,
                        'proxy_host': proxy_host,
                        'proxy_port': proxy_port,
                        'proxy_user': proxy_user,
                        'proxy_pass': proxy_pass
                    }
            
            logger.info(f"Loaded {len(accounts)} accounts from {accounts_file}")
            return accounts
            
        except Exception as e:
            logger.error(f"Error reading accounts file: {e}", exc_info=True)
            raise
    
    @staticmethod
    def select_account(accounts: Dict[str, Dict[str, str]]) -> Dict[str, str]:
        """
        Prompt user to select an account.
        
        Args:
            accounts: Dictionary of available accounts
            
        Returns:
            Selected account details dictionary
        """
        if not accounts:
            raise ValueError("No accounts available")
        
        print("\n" + "=" * 60)
        print("Available Accounts:")
        print("=" * 60)
        account_list = list(accounts.keys())
        for idx, account_name in enumerate(account_list, 1):
            print(f"{idx}. {account_name}")
        print("=" * 60)
        
        while True:
            try:
                choice = input(f"\nSelect account (1-{len(account_list)}): ").strip()
                choice_idx = int(choice) - 1
                
                if 0 <= choice_idx < len(account_list):
                    selected_name = account_list[choice_idx]
                    selected_account = accounts[selected_name]
                    print(f"\nSelected account: {selected_name}")
                    print(f"Username: {selected_account['username']}")
                    print(f"Proxy: {selected_account['proxy_host']}:{selected_account['proxy_port']}")
                    return selected_account
                else:
                    print(f"Please enter a number between 1 and {len(account_list)}")
            except ValueError:
                print("Please enter a valid number")
            except KeyboardInterrupt:
                print("\nSelection cancelled")
                raise
    
    async def start(self):
        """Connect to a Kameleo Chroma browser profile via CDP WebSocket."""
        try:
            # Load and select account if not already selected
            if not self.selected_account:
                accounts = self.read_accounts(self.accounts_file)
                self.selected_account = self.select_account(accounts)
            
            kameleo_profile_id = self.kameleo_profile_id
            kameleo_ws = f'ws://localhost:5050/playwright/{kameleo_profile_id}'
            
            logger.info(f"Connecting to Kameleo Chroma profile: {kameleo_profile_id}")
            print(f"Connecting to Kameleo profile: {kameleo_profile_id}")
            
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.connect_over_cdp(endpoint_url=kameleo_ws)
            self.context = self.browser.contexts[0]
            self.page = await self.context.new_page()
            
            logger.info("Connected to Kameleo Chroma browser successfully")
            
        except Exception as e:
            logger.error(f"Error connecting to Kameleo browser: {e}", exc_info=True)
            raise
    
    async def verify_proxy(self) -> bool:
        """
        Verify the proxy connection by checking the IP address.
        Navigates to an IP-checking website and displays the IP being used.
        
        Returns:
            True if proxy verification successful, False otherwise
        """
        try:
            if not self.page:
                logger.warning("Page not available, cannot verify proxy")
                return False
            
            logger.info("Verifying proxy connection...")
            print("\n" + "=" * 60)
            print("Verifying Proxy Connection...")
            print("=" * 60)
            
            # Try multiple IP-checking services for reliability
            ip_check_urls = [
                # "https://api.ipify.org?format=json",
                "https://httpbin.org/ip",
                # "https://api.myip.com",
            ]
            
            ip_address = None
            
            for url in ip_check_urls:
                try:
                    logger.info(f"Checking IP via {url}...")
                    await self.page.goto(url, wait_until="networkidle", timeout=10000)
                    await asyncio.sleep(1)  # Wait for page to fully load
                    
                    # Get page content
                    content = await self.page.content()
                    
                    # Try to parse JSON response
                    try:
                        # Extract JSON from page
                        json_text = await self.page.evaluate("() => document.body.innerText")
                        data = json.loads(json_text)
                        
                        # Different services return different formats
                        if 'ip' in data:
                            ip_address = data['ip']
                        elif 'origin' in data:
                            ip_address = data['origin']
                        elif 'query' in data:
                            ip_address = data['query']
                        
                        if ip_address:
                            logger.info(f"Successfully retrieved IP: {ip_address}")
                            break
                    except (json.JSONDecodeError, Exception) as e:
                        logger.debug(f"Could not parse JSON from {url}: {e}")
                        continue
                        
                except Exception as e:
                    logger.warning(f"Failed to check IP via {url}: {e}")
                    continue
            
            # If JSON parsing failed, try to get IP from page text
            if not ip_address:
                try:
                    # Try a simple text-based IP service
                    await self.page.goto("https://icanhazip.com", wait_until="networkidle", timeout=10000)
                    await asyncio.sleep(1)
                    ip_text = await self.page.evaluate("() => document.body.innerText.trim()")
                    if ip_text and self._is_valid_ip(ip_text):
                        ip_address = ip_text.strip()
                except Exception as e:
                    logger.warning(f"Failed to get IP from icanhazip.com: {e}")
            
            if ip_address:
                # Display proxy information
                print(f"\n✓ Proxy Connection Verified")
                print(f"  Proxy Server: {self.proxy_config['server']}")
                print(f"  Your IP Address: {ip_address}")
                print(f"  Proxy Username: {self.proxy_config['username']}")
                print("=" * 60)
                logger.info(f"Proxy verified - IP Address: {ip_address}")
                return True
            else:
                print("\n✗ Failed to verify proxy - Could not retrieve IP address")
                print("=" * 60)
                logger.warning("Failed to verify proxy - could not retrieve IP address")
                return False
                
        except Exception as e:
            logger.error(f"Error verifying proxy: {e}", exc_info=True)
            print(f"\n✗ Error verifying proxy: {e}")
            print("=" * 60)
            return False
    
    @staticmethod
    def _is_valid_ip(ip_string: str) -> bool:
        """
        Simple validation to check if a string is a valid IP address.
        
        Args:
            ip_string: String to validate
            
        Returns:
            True if valid IP format, False otherwise
        """
        ip_pattern = r'^(\d{1,3}\.){3}\d{1,3}$'
        if not re.match(ip_pattern, ip_string):
            return False
        
        # Check each octet is between 0-255
        parts = ip_string.split('.')
        try:
            return all(0 <= int(part) <= 255 for part in parts)
        except ValueError:
            return False
    
    async def stop(self):
        """Stop the Playwright connection. Does not close the Kameleo browser profile."""
        try:
            # Stop token refresh timer
            self._is_running = False
            if self.token_refresh_timer:
                self.token_refresh_timer.cancel()
                self.token_refresh_timer = None
            
            # Close only the page — Kameleo manages the browser/context lifecycle
            if self.page:
                await self.page.close()
            if self.playwright:
                await self.playwright.stop()
            logger.info("Playwright connection stopped (Kameleo profile remains active)")
        except Exception as e:
            logger.error(f"Error stopping Playwright connection: {e}", exc_info=True)
    
    def _refresh_token_periodically(self):
        """
        Internal method to signal token refresh every 30 minutes.
        This is called by a timer thread and sets an event to signal the main thread.
        The actual Playwright operations must happen in the main thread.
        """
        if not self._is_running:
            return
        
        try:
            logger.info("Token refresh timer triggered - signaling main thread to refresh token...")
            # Signal the main thread to perform the refresh
            self._token_refresh_event.set()
            
            # Schedule next refresh
            if self._is_running:
                self.token_refresh_timer = threading.Timer(
                    self.token_refresh_interval,
                    self._refresh_token_periodically
                )
                self.token_refresh_timer.daemon = True
                self.token_refresh_timer.start()
                
        except Exception as e:
            logger.error(f"Error in token refresh timer: {e}", exc_info=True)
            # Schedule next refresh even if this one failed
            if self._is_running:
                self.token_refresh_timer = threading.Timer(
                    self.token_refresh_interval,
                    self._refresh_token_periodically
                )
                self.token_refresh_timer.daemon = True
                self.token_refresh_timer.start()
    
    async def refresh_token_if_needed(self) -> bool:
        """
        Check if token refresh is needed and perform it if so.
        This method must be called from the async event loop.
        Uses an async lock to prevent concurrent Playwright page operations
        and retries up to 3 times on failure.
        
        Returns:
            True if token was refreshed, False otherwise
        """
        if not self._token_refresh_event.is_set():
            return False
        
        # Clear the event
        self._token_refresh_event.clear()
        
        # Initialize the async lock if not yet created (must be done inside a running loop)
        if self._page_lock is None:
            self._page_lock = asyncio.Lock()
        
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                async with self._page_lock:
                    logger.info(f"Refreshing authorization token (attempt {attempt}/{max_retries})...")
                    
                    # Perform the actual token refresh
                    token = await self.extract_auth_token_from_request(request_url_pattern='my_bets/list')
                
                if token:
                    with self._token_refresh_lock:
                        self.auth_token = token
                    logger.info("Authorization token refreshed successfully")
                    return True
                else:
                    logger.warning(f"Token refresh attempt {attempt}/{max_retries} failed - no token captured")
                    if attempt < max_retries:
                        await asyncio.sleep(2)
                        
            except Exception as e:
                logger.error(f"Error refreshing token (attempt {attempt}/{max_retries}): {e}", exc_info=True)
                if attempt < max_retries:
                    await asyncio.sleep(2)
        
        logger.error("All token refresh attempts failed, keeping existing token")
        return False
    
    def start_token_refresh(self):
        """
        Start the periodic token refresh mechanism.
        Token will be refreshed every 30 minutes.
        """
        if not self._is_running:
            return
        
        logger.info(f"Starting token refresh mechanism (every {self.token_refresh_interval // 60} minutes)")
        self.token_refresh_timer = threading.Timer(
            self.token_refresh_interval,
            self._refresh_token_periodically
        )
        self.token_refresh_timer.daemon = True
        self.token_refresh_timer.start()
    
    async def is_logged_in(self) -> bool:
        """
        Check if user is already logged in to Duel.com.
        
        Returns:
            True if logged in, False otherwise
        """
        try:
            if not self.page:
                return False
            
            # Navigate to the main page
            await self.page.goto(self.base_url, wait_until="domcontentloaded", timeout=self.timeout)
            await asyncio.sleep(2)  # Wait for page to fully load
            
            # Check for login indicators (adjust selectors based on actual page structure)
            # Common indicators: user menu, account button, logout button, etc.
            login_indicators = [
                '#headlessui-menu-button-3'
            ]
            
            for selector in login_indicators:
                try:
                    element = await self.page.query_selector(selector)
                    if element:
                        logger.info("User is already logged in")
                        return True
                except:
                    continue
            
            # Check for login page indicators
            login_page_indicators = [
                "xpath=//button[@data-testid='navigation-header-login-button']",
                'span:has-text("Login")'
            ]
            
            for selector in login_page_indicators:
                try:
                    element = await self.page.query_selector(selector)
                    if element:
                        logger.info("User is not logged in")
                        return False
                except:
                    continue
            
            # If we can't determine, assume not logged in
            logger.warning("Could not determine login status, assuming not logged in")
            return False
            
        except Exception as e:
            logger.error(f"Error checking login status: {e}", exc_info=True)
            return False
    
    async def login(self, username: Optional[str] = None, password: Optional[str] = None):
        """
        Login to Duel.com.
        
        Args:
            username: Username/email (if None, uses selected account credentials)
            password: Password (if None, uses selected account credentials)
        """
        try:
            if not self.page:
                raise RuntimeError("Browser not started. Call start() first.")
            
            # Use selected account credentials if not provided
            if not username or not password:
                if self.selected_account:
                    username = username or self.selected_account['username']
                    password = password or self.selected_account['password']
                else:
                    username = username or os.getenv("DUEL_USERNAME")
                    password = password or os.getenv("DUEL_PASSWORD")
            
            if not username or not password:
                raise ValueError("Username and password must be provided, selected from accounts file, or set in environment variables (DUEL_USERNAME, DUEL_PASSWORD)")
            
            logger.info("Attempting to login to Duel.com...")
            
            # Navigate to login page or main page
            await self.page.goto(self.base_url, wait_until="domcontentloaded", timeout=self.timeout)
            await asyncio.sleep(2)
            
            # Try to find and click login button if on main page
            login_button_selectors = [
                "xpath=//button[@data-testid='navigation-header-login-button']",
                'span:has-text("Login")'

            ]
            
            for selector in login_button_selectors:
                try:
                    login_btn = await self.page.query_selector(selector)
                    if login_btn:
                        await login_btn.click()
                        await asyncio.sleep(2)
                        break
                except:
                    continue
            
            # Wait for login form to appear
            await asyncio.sleep(2)
            
            # Find and fill username/email field
            username_selectors = [
                '#login-email',
                "xpath=//input[@id='login-email']"
                
            ]
            
            username_filled = False
            for selector in username_selectors:
                try:
                    username_field = await self.page.query_selector(selector)
                    if username_field:
                        await username_field.fill(username)
                        username_filled = True
                        logger.info("Username field filled")
                        break
                except:
                    continue
            
            if not username_filled:
                raise RuntimeError("Could not find username/email input field")
            
            # Find and fill password field
            password_selectors = [
                '#login-password',
                "xpath=//input[@id='login-password']"
            ]
            
            password_filled = False
            for selector in password_selectors:
                try:
                    password_field = await self.page.query_selector(selector)
                    if password_field:
                        await password_field.fill(password)
                        password_filled = True
                        logger.info("Password field filled")
                        break
                except:
                    continue
            
            if not password_filled:
                raise RuntimeError("Could not find password input field")
            
            # Find and click submit button
            submit_selectors = [
                "xpath=//button[@data-testid='login-form-submit-btn']"
            ]
            await asyncio.sleep(2)
            
            submitted = False
            for selector in submit_selectors:
                try:
                    submit_btn = await self.page.query_selector(selector)
                    if submit_btn:
                        await submit_btn.click()
                        submitted = True
                        logger.info("Login form submitted")
                        break
                except:
                    continue
            
            if not submitted:
                raise RuntimeError("Could not find submit button")
            
            # Wait for login to complete
            await asyncio.sleep(10)
            
            # Verify login was successful
            if await self.is_logged_in():
                logger.info("Login successful")
            else:
                raise RuntimeError("Login failed - still not logged in after attempt")
                
        except Exception as e:
            logger.error(f"Error during login: {e}", exc_info=True)
            raise
    
    async def navigate_to_my_bets(self):
        """Navigate to the My Bets page."""
        try:
            if not self.page:
                raise RuntimeError("Browser not started. Call start() first.")
            
            logger.info("Navigating to My Bets page...")

            # await self.page.goto(f"{self.base_url}/sports", wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(5)
            
            # Try different ways to navigate to My Bets
            my_bets_selectors = [
                '#nav-bar-bets'
            ]
            
            navigated = False
            for selector in my_bets_selectors:
                try:
                    element = await self.page.query_selector(selector)
                    if element:
                        await element.click()
                        navigated = True
                        logger.info("Clicked My Bets link")
                        print("Clicked My Bets link")

                        break
                except:
                    continue
            
            # If clicking didn't work, try direct navigation
            if not navigated:
                await self.page.goto(f"{self.base_url}/sports?bt-path=/bets", wait_until="domcontentloaded", timeout=self.timeout)
                logger.info("Navigated directly to My Bets URL")
            
            await asyncio.sleep(5)  # Wait for page to load
            
            logger.info("Successfully navigated to My Bets page")
            
        except Exception as e:
            logger.error(f"Error navigating to My Bets: {e}", exc_info=True)
            raise
    
    async def extract_auth_token_from_request(self, request_url_pattern: str = 'my_bets/list', timeout_seconds: int = 120) -> Optional[str]:
        """
        Set up request interception to capture authorization token from API requests.
        Then click one of the buttons on My Bets page to trigger the API call.
        Retries button clicks until the token is captured or the timeout is reached.
        
        Args:
            request_url_pattern: Optional pattern to match specific API endpoint
            timeout_seconds: Max time in seconds to wait for token capture (default 120s / 2 minutes)
            
        Returns:
            Authorization token if found, None otherwise
        """
        try:
            if not self.page:
                raise RuntimeError("Browser not started. Call start() first.")
            
            logger.info(f"Setting up request interception to capture auth token (timeout: {timeout_seconds}s)...")
            
            captured_token = None
            request_count = 0
            
            def handle_request(request):
                nonlocal captured_token, request_count
                request_count += 1
                headers = request.headers
                url = request.url
                
                if 'authorization' in headers:
                    token = headers['authorization']
                    if request_url_pattern:
                        if request_url_pattern in url:
                            captured_token = token
                            logger.info(f"Captured auth token from matching endpoint: {url}")
                            logger.info(f"Captured auth token: {token}")
            
            # Set up request listener BEFORE navigating
            self.page.on("request", handle_request)
            
            # Navigate to My Bets page if not already there
            current_url = self.page.url
            if "bets" not in current_url.lower():
                logger.info("Navigating to My Bets page to capture token...")
                await self.navigate_to_my_bets()
                await asyncio.sleep(8)
            
            # Keep retrying button clicks until token is captured or timeout is reached
            start_time = time.time()
            attempt = 0
            
            while not captured_token and (time.time() - start_time) < timeout_seconds:
                attempt += 1
                elapsed = int(time.time() - start_time)
                logger.info(f"Token capture attempt {attempt} ({elapsed}s / {timeout_seconds}s elapsed)...")
                
                button_clicked = False
                buttons_clicked = 0
                max_buttons_to_click = 2
                
                for selector in self.target_button_selectors:
                    if captured_token:
                        break
                    try:
                        buttons = await self.page.query_selector_all(selector)
                        if buttons and len(buttons) > 0:
                            for i, button in enumerate(buttons[:max_buttons_to_click]):
                                if buttons_clicked >= max_buttons_to_click or captured_token:
                                    break
                                try:
                                    await button.click(timeout=15000)
                                    buttons_clicked += 1
                                    button_clicked = True
                                    logger.info(f"Clicked filter button {buttons_clicked} with selector: {selector}")
                                    await asyncio.sleep(3)
                                except Exception as e:
                                    logger.debug(f"Could not click button {i}: {e}")
                                    continue
                            if button_clicked:
                                break
                    except Exception as e:
                        logger.error(f"Could not find buttons with selector {selector}: {e}")
                        continue

                self.target_button_selectors.reverse()
                
                if not captured_token:
                    # Wait before retrying
                    await asyncio.sleep(5)
            
            # Remove the listener
            try:
                self.page.remove_listener("request", handle_request)
            except:
                pass
            
            logger.info(f"Processed {request_count} requests during token extraction")
            
            if captured_token:
                self.auth_token = captured_token
                logger.info("Successfully captured authorization token")
                print("\n" + "=" * 60)
                print(">>> AUTH TOKEN CAPTURED SUCCESSFULLY <<<")
                print("=" * 60 + "\n")
                return captured_token
            else:
                logger.error("Failed to capture authorization token within timeout")
                print("\n" + "=" * 60)
                print(">>> FAILED TO CAPTURE AUTH TOKEN <<<")
                print(f">>> Timed out after {timeout_seconds} seconds")
                print("=" * 60 + "\n")
                return None
                
        except Exception as e:
            logger.error(f"Error extracting auth token: {e}", exc_info=True)
            return None
    
    def get_auth_token(self, force_refresh: bool = False) -> Optional[str]:
        """
        Get the current authorization token, refreshing if necessary.
        
        Args:
            force_refresh: Force refresh of the token by clicking buttons again
            
        Returns:
            Authorization token
        """
        if self.auth_token and not force_refresh:
            return self.auth_token
        
        # Try to extract token (this needs to be called from async context)
        # For now, return existing token if available
        return self.auth_token
    
    async def update_balance(self) -> bool:
        """
        Read the account balance from the page and update self.balance.
        
        Returns:
            True if balance was successfully updated, False otherwise
        """
        try:
            if not self.page:
                logger.warning("Page not available, cannot read balance")
                return False
            
            # Navigate to a page where balance is visible (My Bets page)
            current_url = self.page.url
            if "bets" not in current_url.lower():
                await self.navigate_to_my_bets()
                await asyncio.sleep(2)  # Wait for page to load
            
            # Read balance using xpath
            balance_element = self.page.locator("xpath=//span[@data-testid='currency-amount']")
            
            
            # Get text content
            balance_text = await balance_element.text_content(timeout=5000)
            print(f"Balance text: {balance_text}")
            
            if not balance_text:
                logger.error("Balance text is empty")
                return False
            
            # Clean the balance text: remove "$" and ","
            cleaned_balance = balance_text.replace('$', '').replace(',', '').strip()
            
            try:
                # Convert to float
                self.balance = float(cleaned_balance)
                logger.info(f"Balance updated: ${self.balance:.2f}")
                return True
            except ValueError as e:
                logger.error(f"Failed to convert balance to float: {cleaned_balance}, error: {e}")
                return False
                
        except Exception as e:
            logger.error(f"Error updating balance: {e}", exc_info=True)
            return False
    
    async def place_bet(
        self,
        duel_event_id: str,
        sport: str,
        market_name: str,
        selection: str,
        hdp: Optional[float],
        odds: float
    ) -> Dict[str, Any]:
        """
        Place a bet on Duel.com.
        
        Args:
            duel_event_id: The Duel event ID
            market_name: Market name (e.g., 'ML', 'Totals', 'Spread')
            selection: Selection (e.g., 'home', 'away', 'over', 'under')
            hdp: Handicap/totals line (optional)
            odds: The odds for the bet
            
        Returns:
            Response dictionary from the API
        """
        # Ensure we have a token
        token = self.get_auth_token()
        if not token:
            raise RuntimeError("No authorization token available. Please login first.")
        
        # Initialize the async lock if not yet created (must be done inside a running loop)
        if self._page_lock is None:
            self._page_lock = asyncio.Lock()
        
        # Acquire page lock for Playwright operations (update_balance navigates the shared page)
        async with self._page_lock:
            balance_updated = await self.update_balance()
            if not balance_updated:
                logger.warning("Failed to update balance, using existing balance value")
        
        if self.balance <= 0:
            raise RuntimeError(f"Invalid balance: ${self.balance}. Cannot place bet.")
        
        url = f"{self.api_base_url}/api/v2/coupon/brand/2482975601191952386/bet/place" #2482975601191952386
        
        # Map market and selection to IDs
        market_id = ''
        selection_id = ''
        specifier = ''
        
        
        
        
        if market_name == "3-Way Result" or (market_name == "ML" and sport.lower() == 'soccer'):
            market_id = "1"
            if selection == "home":
                selection_id = "1"
            elif selection == "away":
                selection_id = "3"
            elif selection == "draw":
                selection_id = "2"

        elif market_name == "ML":
            if sport.lower() in ['ice hockey']:
                market_id = "406"
            elif sport.lower() in ["volleyball", "tennis", 'esports']:
                market_id = "186"
            else:
                market_id = "219"

            if selection == "home":
                selection_id = "4"
            elif selection == "away":
                selection_id = "5"
            
        elif market_name == "Spread":
            if sport.lower() == 'basketball':
                market_id = '223'
            else:
                market_id = "16"
            
            if selection == "home":
                selection_id = "1714"
                
            elif selection == "away":
                selection_id = "1715"
                hdp = -1 * hdp
            specifier = f"hcp={hdp}"

        elif market_name == "Totals":
            if sport.lower() == 'basketball':
                market_id = '225'
            else:
                market_id = "18"
            specifier = f'total={hdp}'
            if selection == "over":
                selection_id = "12"
            elif selection == "under":
                selection_id = "13"
        elif market_name == "Totals HT":
            market_id = '68'
            specifier = f'total={hdp}'
            if selection == "over":
                selection_id = "12"
            elif selection == "under":
                selection_id = "13"
        else:
            raise ValueError(f"Unsupported market: {market_name}")
        
        bet_request_id = f"{duel_event_id}-{market_id}-{specifier}-{selection_id}"
        
        # Calculate stake as 1.5% of balance
        # stake = self.balance * 0.02
        # stake = round(stake)
        stake = self.stake_amount if self.stake_amount is not None else 100
        
        if stake <= 0:
            raise RuntimeError(f"Calculated stake is invalid: ${stake}. Balance may be too low.")

        max_stake = self.check_max_stake(duel_event_id=duel_event_id,
                                                        sport=sport,
                                                        market_name=market_name,
                                                        selection=selection,
                                                        hdp=hdp,
                                                        odds=odds)
        logger.info(f"Max stake: {max_stake}")
        print(f"Max stake: {max_stake}")

        if float(max_stake) < 10:
            return f"Max stake limit reached"
        elif float(max_stake) < stake:
            stake = math.floor(float(max_stake))
            
        
        logger.info(f"Calculated stake: ${stake:.2f} (1.5% of ${self.balance:.2f})")
        logger.info(f"Placing bet for event_id: {duel_event_id}, market_name: {market_name}, selection: {selection}, hdp: {hdp}, odds: {odds}, balance: ${self.balance:.2f}")
        
        payload = [
            {
                "type": "1/1",
                "sum": str(stake),
                "k": str(odds),
                "global_id": None,
                "bonus_id": None,
                "bet_request_id": bet_request_id,
                "odds_change": "higher",
                "selections": [
                    {
                        "event_id": duel_event_id,
                        "market_id": market_id,
                        "specifiers": specifier,
                        "outcome_id": selection_id,
                        "k": str(odds),
                        "source": {
                            "layout": "tile",
                            "page": "/:sportSlug/:categorySlug/:tournamentSlug/:eventSlugAndId",
                            "section": "",
                            "extra": {
                                "market": "Event Plate",
                                "timeFilter": "",
                                "banner_type": "",
                                "tab": ""
                            }
                        },
                        "promo_id": None,
                        "bonus_id": None,
                        "timestamp": int(datetime.now().timestamp() * 1000)
                    }
                ]
            }
        ]
        
        logger.info(f"Bet payload: {payload}")
        
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Referer": "https://duel.com/",
            "Content-Type": "application/json",
            "Authorization": token,
            "X-Session-Id": self.x_session_id,
            "X-GA-Client-Id": self.x_ga_client_id,
            "X-GA-Session-Id": self.x_ga_session_id,
            "x-ua-full-version-list": '"Not:A-Brand";v="99.0.0.0", "Google Chrome";v="145.0.7632.75", "Chromium";v="145.0.7632.75"',
            "x-ua-model":self.x_ua_model,
            "x-ua-platform-version":self.x_ua_platform_version,
            "Origin": "https://duel.com",
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "cross-site",
            "Priority": "u=0",
            "TE": "trailers"
        }
        
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=10)
            response.raise_for_status()
            result = response.json()
            logger.info(f"Bet Response: {result}")
            print(f"Bet Response: {result}")
            return result
        except requests.RequestException as e:
            logger.error(f"Error placing bet: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response: {e.response.text}")
            raise

    def check_max_stake(self,
        duel_event_id: str,
        sport: str,
        market_name: str,
        selection: str,
        hdp: Optional[float],
        odds: float
    ) -> Dict[str, Any]:
        """
        Place a bet on Duel.com.
        
        Args:
            duel_event_id: The Duel event ID
            market_name: Market name (e.g., 'ML', 'Totals', 'Spread')
            selection: Selection (e.g., 'home', 'away', 'over', 'under')
            hdp: Handicap/totals line (optional)
            odds: The odds for the bet
            
        Returns:
            Response dictionary from the API
        """
        # Ensure we have a token
        token = self.get_auth_token()
        if not token:
            raise RuntimeError("No authorization token available. Please login first.")
        
        # Initialize the async lock if not yet created (must be done inside a running loop)
        # if self._page_lock is None:
        #     self._page_lock = asyncio.Lock()
        
        # Acquire page lock for Playwright operations (update_balance navigates the shared page)
        
        if self.balance <= 0:
            raise RuntimeError(f"Invalid balance: ${self.balance}. Cannot place bet.")
        
        # url = f"{self.api_base_url}/api/v2/coupon/brand/2482975601191952386/bet/place" 
        url = "https://api-a-c7818b61-600.sptpub.com/api/v1/coupon/max"
        
        # Map market and selection to IDs
        market_id = ''
        selection_id = ''
        specifier = ''
        
        
        logger.info(f"Checking Max Stake for event_id: {duel_event_id}, market_name: {market_name}, selection: {selection}, hdp: {hdp}, odds: {odds}, balance: ${self.balance:.2f}")
        
        if market_name == "3-Way Result" or (market_name == "ML" and sport.lower() == 'soccer'):
            market_id = "1"
            if selection == "home":
                selection_id = "1"
            elif selection == "away":
                selection_id = "3"
            elif selection == "draw":
                selection_id = "2"

        elif market_name == "ML":
            if sport.lower() in ['ice hockey']:
                market_id = "406"
            elif sport.lower() in ["volleyball", "tennis", 'esports']:
                market_id = "186"
            else:
                market_id = "219"

            if selection == "home":
                selection_id = "4"
            elif selection == "away":
                selection_id = "5"
            
        elif market_name == "Spread":
            if sport.lower() == 'basketball':
                market_id = '223'
            else:
                market_id = "16"
            
            if selection == "home":
                selection_id = "1714"
                
            elif selection == "away":
                selection_id = "1715"
                
            specifier = f"hcp={hdp}"

        elif market_name == "Totals":
            if sport.lower() == 'basketball':
                market_id = '225'
            else:
                market_id = "18"
            specifier = f'total={hdp}'
            if selection == "over":
                selection_id = "12"
            elif selection == "under":
                selection_id = "13"
        elif market_name == "Totals HT":
            market_id = '68'
            specifier = f'total={hdp}'
            if selection == "over":
                selection_id = "12"
            elif selection == "under":
                selection_id = "13"
        else:
            raise ValueError(f"Unsupported market: {market_name}")
        
        bet_request_id = f"{duel_event_id}-{market_id}-{specifier}-{selection_id}"
        
        # Calculate stake as 1.5% of balance
        # stake = self.balance * 0.02
        # stake = round(stake)
        # stake = 100
        
        
        payload = {
            "type": "1/1",
            "sum": "5",
            "k": str(odds),
            "global_id": None,
            "bonus_id": None,
            "bet_request_id": bet_request_id,
            "odds_change": "higher",
            "selections": [
                {
                    "event_id": duel_event_id,
                    "market_id": market_id,
                    "specifiers": specifier,
                    "outcome_id": selection_id,
                    "k": str(odds),
                    "source": {
                        "layout": "tile",
                        "page": "/:sportSlugAndId",
                        "section": "Promo",
                        "extra": {
                            "market": "sport_page",
                            "timeFilter": "",
                            "banner_type": "",
                            "tab": ""
                        }
                    },
                    "promo_id": None,
                    "bonus_id": None,
                    "timestamp": 1771505164011
                }
            ]
        }
        headers = {
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.7",
            "authorization": token,
            "content-type": "application/json",
            "origin": "https://duel.com",
            "priority": "u=1, i",
            "referer": "https://duel.com/",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "cross-site",
            "sec-gpc": "1",
            "user-agent": self.user_agent,
        }
        logger.info(f"Max_Stake Payload: {payload}")
        logger.info(f"Max_Stake Headers: {headers}")
        try:
            response = requests.post(url, json=payload, headers=headers)
            response.raise_for_status()
            result = response.json()
            logger.info(f"Max Stake Response: {result}")
            max_stake = result.get('max_bet')
            return max_stake
        except requests.RequestException as e:
            logger.error(f"Error placing bet: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response: {e.response.text}")
            raise
    
    def place_bet_sync(
        self,
        duel_event_id: str,
        sport: str,
        market_name: str,
        selection: str,
        hdp: Optional[float],
        odds: float
    ) -> Dict[str, Any]:
        """
        Synchronous wrapper for place_bet that can be called from non-async contexts.
        Uses the event loop to run the async method.
        
        Args:
            duel_event_id: The Duel event ID
            market_name: Market name (e.g., 'ML', 'Totals', 'Spread')
            selection: Selection (e.g., 'home', 'away', 'over', 'under')
            hdp: Handicap/totals line (optional)
            odds: The odds for the bet
            
        Returns:
            Response dictionary from the API
        """
        if self._loop and self._loop.is_running():
            # If loop is running, schedule the coroutine in the existing loop
            future = asyncio.run_coroutine_threadsafe(
                self.place_bet(duel_event_id, sport, market_name, selection, hdp, odds),
                self._loop
            )
            return future.result(timeout=60)  # 60 second timeout for bet placement
        else:
            # If no loop or loop not running, create a new one
            return asyncio.run(self.place_bet(duel_event_id, sport, market_name, selection, hdp, odds))
    
    def get_bet_odds(self, duel_event_id: str) -> Optional[float]:
        """
        Get the settled odds for a bet placed on a specific event.
        
        Args:
            duel_event_id: The Duel event ID
            
        Returns:
            The settled odds, or None if not found
        """
        token = self.get_auth_token()
        if not token:
            raise RuntimeError("No authorization token available. Please login first.")
        
        url = f"{self.api_base_url}/api/v1/my_bets/list"
        
        querystring = {
            "currency": "USD",
            "lang": "en",
            "limit": "15",
            "skip": "0",
            "status": "",
            "timestamp_from": "",
            "timestamp_to": ""
        }
        
        headers = {
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.9",
            "authorization": token,
            "content-type": "application/json",
            "origin": "https://duel.com",
            "referer": "https://duel.com/",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": self.user_agent,
        }
        
        try:
            response = requests.get(url, headers=headers, params=querystring, timeout=10)
            response.raise_for_status()
            bet_list = response.json().get('results', [])
            
            for bet in bet_list:
                if bet.get('selections') and len(bet['selections']) > 0:
                    if bet['selections'][0].get('event_id') == str(duel_event_id):
                        return float(bet.get('k', 0)), self.balance
            
            return None, self.balance
        except requests.RequestException as e:
            logger.error(f"Error getting bet odds: {e}")
            return None, self.balance

    
    
    async def initialize(self):
        """
        Complete initialization workflow:
        1. Load and select account
        2. Start browser with proxy
        3. Login with selected account credentials
        4. Navigate to My Bets
        5. Extract auth token
        """
        try:
            logger.info("Initializing DuelClient...")
            
            # Load session params (includes kameleo_profile_id) before connecting
            self._load_session_params()
            
            # Load and select account if not already selected
            if not self.selected_account:
                accounts = self.read_accounts(self.accounts_file)
                self.selected_account = self.select_account(accounts)
            
            # Connect to Kameleo browser
            await self.start()
            
            # Login with selected account credentials
            logger.info("Attempting to login with selected account...")
            await self.page.goto("https://duel.com", wait_until="domcontentloaded", timeout=60000)

            await ainput("Login manually and press Enter to continue...")
            
            # Verify login was successful
            if not await self.is_logged_in():
                logger.error("Login failed - could not verify login status")
                return False
            
            # Navigate to My Bets and extract token
            await self.navigate_to_my_bets()
            await asyncio.sleep(5)
            token = await self.extract_auth_token_from_request(request_url_pattern='my_bets/list')
            
            if not token:
                logger.error("Could not extract auth token")
                return False
            
            if token:
                logger.info("DuelClient initialized successfully")
                # Start periodic token refresh
                self.start_token_refresh()
                return True
            else:
                logger.warning("DuelClient initialized but could not extract auth token")
                return False
                
        except Exception as e:
            logger.error(f"Error initializing DuelClient: {e}", exc_info=True)
            raise

