import asyncio
import websockets
import ssl
import logging

# --- Set the URL to test ---
TEST_URI = "wss://home-1-evanp.duckdns.org:5000/waterfall"

# --- This adds extra debug logging (uncomment if needed) ---
# logging.basicConfig(level=logging.INFO)
# logging.getLogger('websockets').setLevel(logging.DEBUG)

async def test_websocket():
    print(f"\n--- Attempting to connect to: {TEST_URI} ---")

    # We create a default SSL context
    ssl_context = ssl.create_default_context()

    try:
        # --- THIS IS THE FIXED LINE ---
        # I've replaced the broken asyncio.wait_for with the
        # built-in 'open_timeout' parameter.
        async with websockets.connect(TEST_URI, ssl=ssl_context, open_timeout=10.0) as websocket:

            print("\n✅ --- SUCCESS: Connection Established! ---")
            print(f"   Server: {websocket.response_headers.get('Server')}")

            print("   Waiting for first message...")
            try:
                # This part was already correct: wait 5s for a message
                message = await asyncio.wait_for(websocket.recv(), timeout=20.0)
                print(f"   SUCCESS: Received message! Length: {len(message)}")
            except asyncio.TimeoutError:
                print("   WARNING: Connection opened, but no message received after 5 seconds.")

    # This will catch the 'open_timeout'
    except asyncio.TimeoutError:
        print(f"\n❌ --- FAILED: Connection Timed Out ---")
        print("   This is an 'ETIMEDOUT'. The router or a firewall is dropping the packet.")
        print("   This points to your AT&T router blocking port 443.")

    # This will catch the 'readyState: 3' error
    except websockets.exceptions.ConnectionClosedError as e:
        print(f"\n❌ --- FAILED: Connection Closed (Error) ---")
        print(f"   Code: {e.code}")
        print(f"   Reason: {e.reason}")
        print("   This is the Python equivalent of 'readyState: 3'.")
        print("   The server (Nginx) *is* answering but slammed the door.")

    except (ConnectionRefusedError, OSError) as e:
        print(f"\n❌ --- FAILED: Connection Refused or OS Error ---")
        print(f"   Error: {e}")
        print("   This means the router/firewall actively rejected the connection on port 443.")

    except Exception as e:
        print(f"\n❌ --- FAILED: An unexpected error occurred ---")
        print(f"   Type: {type(e)}")
        print(f"   Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_websocket())
