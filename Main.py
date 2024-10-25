import os
import json
import re
import asyncio
import aiohttp
import websockets
from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QLineEdit, QPushButton, QListWidget, QSplitter
from PyQt6.QtCore import Qt
import sys
import threading

# Discord constants
DISCORD_GATEWAY = "wss://gateway.discord.gg/?v=9&encoding=json"
DISCORD_API = "https://discord.com/api/v9"
USER_TOKEN = ""
CHANNEL_ID = "0"  # Default channel, will change dynamically
MESSAGE_BUFFER_SIZE = 10  # Display the last 10 messages
MAX_MESSAGE_SIZE = 10 * 1024 * 1024  # Set to 10 MB

# Global message buffer and regex for URLs
message_buffer = []
url_regex = re.compile(r'https?://\S+')  # Basic URL regex

# Setup the GUI using PyQt6
class DiscordClientApp(QWidget):
    def __init__(self):
        super().__init__()
        self.servers = []  # List of user's servers
        self.channels = {}  # Dict to store channels for each server
        self.current_channel = CHANNEL_ID  # Currently active channel

        self.init_ui()

        # Start the asyncio loop for Discord client
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.run_loop, daemon=True).start()
        self.loop.create_task(self.fetch_servers_and_channels())  # Fetch servers and channels
        self.loop.create_task(self.discord_client())  # Start Discord client

    def init_ui(self):
        """Initializes the UI components."""
        self.setWindowTitle("MipCord - Discord Client")
        self.setGeometry(200, 200, 800, 600)

        # Main layout with splitter
        layout = QHBoxLayout()
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Server and channel list widgets
        self.server_list = QListWidget()
        self.server_list.currentItemChanged.connect(self.on_server_selected)
        self.channel_list = QListWidget()
        self.channel_list.currentItemChanged.connect(self.on_channel_selected)

        # Vertical layout for server and channel lists
        side_layout = QVBoxLayout()
        side_layout.addWidget(self.server_list)
        side_layout.addWidget(self.channel_list)

        side_widget = QWidget()
        side_widget.setLayout(side_layout)

        # Chat display area
        self.chat_display = QTextEdit(self)
        self.chat_display.setReadOnly(True)

        # Input layout (for input box and send button)
        input_layout = QHBoxLayout()
        self.user_input = QLineEdit(self)
        self.user_input.returnPressed.connect(self.on_send)  # Call on_send when "Enter" is pressed
        send_button = QPushButton("Send", self)
        send_button.clicked.connect(self.on_send)  # Call on_send when "Send" button is clicked
        input_layout.addWidget(self.user_input)
        input_layout.addWidget(send_button)

        # Vertical layout for chat and input
        chat_layout = QVBoxLayout()
        chat_layout.addWidget(self.chat_display)
        chat_layout.addLayout(input_layout)

        chat_widget = QWidget()
        chat_widget.setLayout(chat_layout)

        # Add widgets to splitter
        splitter.addWidget(side_widget)
        splitter.addWidget(chat_widget)
        splitter.setSizes([200, 600])  # Set initial sizes for the side and main areas

        layout.addWidget(splitter)
        self.setLayout(layout)
        self.show()

    def run_loop(self):
        """Runs the asyncio loop in a separate thread."""
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def display_message(self, message):
        """Display a message in the chat display area."""
        self.chat_display.append(message)

    def on_send(self):
        """Handler for sending messages from the input box."""
        content = self.user_input.text()
        if content:  # Only send if the input is not empty
            asyncio.run_coroutine_threadsafe(self.send_message(content), self.loop)
            self.user_input.clear()  # Clear input after sending

    async def fetch_servers_and_channels(self):
        """Fetches the list of servers and channels."""
        headers = {
            "Authorization": USER_TOKEN,
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            # Get servers (guilds) the user is in
            async with session.get(f"{DISCORD_API}/users/@me/guilds", headers=headers) as response:
                if response.status == 200:
                    guilds = await response.json()
                    self.servers = guilds
                    for guild in guilds:
                        self.server_list.addItem(guild['name'])

            # Fetch channels for the first server
            if self.servers:
                await self.fetch_channels(self.servers[0]['id'])

    async def fetch_channels(self, server_id):
        """Fetches the list of channels for a specific server."""
        headers = {
            "Authorization": USER_TOKEN,
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            # Get channels for the selected server (guild)
            async with session.get(f"{DISCORD_API}/guilds/{server_id}/channels", headers=headers) as response:
                if response.status == 200:
                    channels = await response.json()
                    self.channels[server_id] = channels
                    self.channel_list.clear()
                    for channel in channels:
                        if channel['type'] == 0:  # Only list text channels
                            self.channel_list.addItem(channel['name'])

    def on_server_selected(self):
        """Handle when a server is selected from the list."""
        current_item = self.server_list.currentItem()
        if current_item:
            selected_server = next((s for s in self.servers if s['name'] == current_item.text()), None)
            if selected_server:
                server_id = selected_server['id']
                self.loop.create_task(self.fetch_channels(server_id))

    def on_channel_selected(self):
        """Handle when a channel is selected from the list."""
        current_item = self.channel_list.currentItem()
        if current_item:
            selected_channel = next((c for c in self.channels[self.servers[self.server_list.currentRow()]['id']] if c['name'] == current_item.text()), None)
            if selected_channel:
                self.current_channel = selected_channel['id']

    async def heartbeat(self, ws, interval):
        """Send heartbeats to keep WebSocket connection alive."""
        try:
            while True:
                await asyncio.sleep(interval / 1000)
                await ws.send(json.dumps({"op": 1, "d": None}))
        except websockets.ConnectionClosed as e:
            self.display_message(f"Heartbeat error: {e}")

    async def identify(self, ws):
        """Authenticate with the Discord gateway."""
        payload = {
            "op": 2,
            "d": {
                "token": USER_TOKEN,
                "properties": {
                    "$os": "windows",
                    "$browser": "chrome",
                    "$device": "desktop"
                },
                "compress": False,
                "presence": {
                    "status": "online",
                    "since": 0,
                    "activities": [],
                    "afk": False
                }
            }
        }
        await ws.send(json.dumps(payload))

    async def listen(self, ws):
        """Listen for incoming WebSocket events and handle them."""
        global message_buffer
        async for message in ws:
            data = json.loads(message)
            if data.get('t') == 'MESSAGE_CREATE' and data['d']['channel_id'] == self.current_channel:
                content = data['d']['content']
                author = data['d']['author']['username']

                # Skip messages with URLs, attachments, or embeds
                if data['d'].get('attachments') or data['d'].get('embeds') or url_regex.search(content):
                    continue

                # Add formatted message to the buffer
                formatted_message = f"{author}: {content}"
                message_buffer.append(formatted_message)
                if len(message_buffer) > MESSAGE_BUFFER_SIZE:
                    message_buffer.pop(0)  # Keep buffer size fixed

                # Display message in the GUI
                self.display_message(formatted_message)

    async def send_message(self, content):
        """Send a message to the currently selected Discord channel."""
        url = f"{DISCORD_API}/channels/{self.current_channel}/messages"
        message_data = {
            "content": content,
            "tts": False
        }
        headers = {
            "Authorization": USER_TOKEN,
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=message_data) as response:
                if response.status != 200:
                    self.display_message(f"Failed to send message: {response.status}, {await response.text()}")
                elif response.status == 429:
                    retry_after = (await response.json()).get("retry_after", 1)
                    self.display_message(f"Rate limited. Retrying in {retry_after} seconds.")
                    await asyncio.sleep(retry_after)

    async def discord_client(self):
        """Main client to connect and interact with Discord."""
        while True:
            try:
                async with websockets.connect(DISCORD_GATEWAY, max_size=MAX_MESSAGE_SIZE) as ws:
                    # Receive Hello message from Discord
                    hello = await ws.recv()
                    hello_data = json.loads(hello)
                    heartbeat_interval = hello_data["d"]["heartbeat_interval"]

                    # Start the heartbeat task
                    asyncio.create_task(self.heartbeat(ws, heartbeat_interval))

                    # Authenticate with Discord
                    await self.identify(ws)

                    # Listen for messages concurrently
                    await self.listen(ws)

            except websockets.ConnectionClosed as e:
                self.display_message(f"WebSocket connection closed: {e}. Reconnecting in 5 seconds...")
                await asyncio.sleep(5)  # Reconnect after delay

# Run the PyQt6 GUI application
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DiscordClientApp()
    sys.exit(app.exec())
