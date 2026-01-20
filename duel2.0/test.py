import websocket
import json
import time
import requests
import logging
from pprint import pprint

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    filename="boltoddstest.log")      

logger = logging.getLogger("BoltoddsTestLogger")

class WebSocketClient:
    def __init__(self, uri):
        self.uri = uri
        self.ws = None
        self.subscribed = False
        self.should_reconnect = True
        
    def on_open(self, ws):
        """Called when the WebSocket connection is established"""
        print("WebSocket connection opened")
        # Subscription message will be sent after receiving ack_message
        # The server should send an ack_message first, which we'll handle in on_message
        
    def on_message(self, ws, message):
        """Called when a message is received from the server"""
        try:
            data = json.loads(message)
            
            # Handle ack message (first message after connection)
            if not self.subscribed:
                print(f"Ack message: {message}")
                # Send the subscription message after receiving ack
                subscribe_message = {
                    "action": "subscribe",
                    "filters": {
                        # "sports": ["NBA", "MLB", "Wimbledon (M)"],
                        "sportsbooks": ["pinnacle"],
                        # "games": ["San Francisco Giants vs Philadelphia Phillies, 2025-07-07, 09", "Corinthians vs Bragantino, 2025-07-13, 06"],
                        "markets": ["Moneyline", "Spread", "1st Half Spread", "1st Half Moneyline" "Total Goals", "1st Half Asian Spread","1st Half Total Goals", 
                                    "3 Way", "Asian Spread", 'Total', '1st Half Total', '1st Half Total', '1st Half Total Points', 'Total Points']
                    }
                }
                ws.send(json.dumps(subscribe_message))
                self.subscribed = True
                return
            
            # Handle ping messages
            if data.get('action') == 'ping':
                return
            
            # Handle different message types
            action = data.get('action')
            
            # sent upon connection, initial state of odds subbed to
            if action == 'initial_state':
                self.handle_initial_state(data)
            
            # entire game odds update
            elif action == 'game_update':
                self.handle_game_update(data)
            
            # games done
            elif action == 'game_removed':
                self.handle_game_removed(data)
            
            # game added
            elif action == 'game_added':
                self.handle_game_added(data)
            
            # singular line odd update
            # if odds r None or '', no odds available for line i.e its deleted, suspended etc
            elif action == 'line_update':
                self.handle_line_update(data)
            
            # all games from a specific sport and book have been cleared
            elif action == 'sport_clear':
                self.handle_sport_clear(data)
            
            # all games for this book have been cleared
            elif action == 'book_clear':
                self.handle_book_clear(data)
                
        except json.JSONDecodeError as e:
            print(f"Error parsing JSON: {e}")
        except Exception as e:
            print(f"Error processing message: {e}")
    
    def on_error(self, ws, error):
        """Called when an error occurs"""
        print(f"WebSocket error: {error}")
    
    def on_close(self, ws, close_status_code, close_msg):
        """Called when the WebSocket connection is closed"""
        print(f"Connection closed — status: {close_status_code}, reason: {close_msg}")
        self.subscribed = False
        
        if self.should_reconnect:
            print("Reconnecting...")
            time.sleep(5)
            self.connect()
    
    def handle_initial_state(self, data):
        """Handle initial_state action"""
        pass
    
    def handle_game_update(self, data):
        """Handle game_update action"""
        logger.info("--------------------------------Game updated--------------------------------")
        logger.info(data)
    
    def handle_game_removed(self, data):
        """Handle game_removed action"""
        pass
    
    def handle_game_added(self, data):
        """Handle game_added action"""
        logger.info("--------------------------------Game added--------------------------------")
        logger.info(data)
    
    def handle_line_update(self, data):
        """Handle line_update action"""
        logger.info("Line updated")
        logger.info(data)
    
    def handle_sport_clear(self, data):
        """Handle sport_clear action"""
        pass
    
    def handle_book_clear(self, data):
        """Handle book_clear action"""
        pass
    
    def connect(self):
        """Connect to the WebSocket server"""
        self.ws = websocket.WebSocketApp(
            self.uri,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close
        )
        # run_forever() blocks, so run it in a separate thread or use it directly
        self.ws.run_forever()
    
    def start(self):
        """Start the WebSocket client"""
        self.connect()
    
    def stop(self):
        """Stop the WebSocket client"""
        self.should_reconnect = False
        if self.ws:
            self.ws.close()


def run_client():
    """Main function to run the WebSocket client"""
    uri = "wss://spro.agency/api?key=a65f44a6-99e9-4c70-8b12-0aab385b449e"
    client = WebSocketClient(uri)
    
    try:
        client.start()
    except KeyboardInterrupt:
        print("\nStopping WebSocket client...")
        client.stop()




def fetch_and_print_markets():
    url = "https://spro.agency/api/get_markets?key=a65f44a6-99e9-4c70-8b12-0aab385b449e&sportsbooks=pinnacle"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        pprint(data)
    except Exception as e:
        print(f"Failed to fetch markets: {e}")

# Example call

if __name__ == "__main__":
    run_client()
    # fetch_and_print_markets()
