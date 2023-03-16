import os
import discord
import openai
import json
from keep_alive import keep_alive

def load_prompt_parameters(filename):
    with open(filename, 'r') as file:
        return json.load(file)

TOKEN = os.environ['DISCORD_TOKEN']
OPENAI_KEY = os.environ['KEY_OPENAI']

# Set up the OpenAI API client
openai.api_key = OPENAI_KEY

intents = discord.Intents.all()
client = discord.Client(command_prefix='##', intents=intents)

chat_history = []

# Add the previous message to the chat history
def add_chat_history(chat_history, message):
    chat_history.append({"role": "user", "content": message.content})

    # Limit the chat history to the last 5 messages
    chat_history = chat_history[-5:]

# Load prompt parameters from JSON file
prompt_parameters = load_prompt_parameters('prompt_parameters.json')

@client.event
async def on_ready():
    global logged_in_user
    logged_in_user = client.user
    print('We have logged in as {0.user} in main'.format(client))

@client.event
async def on_message(message):
    print("Message Received")

    add_chat_history(chat_history, message)

    if message.author == client.user:
        return

    if message.content.startswith("$$"):
        print(f"Responding to message: {message.content}")

        message_content = message.content[2:]

        response = openai.ChatCompletion.create(
            model=prompt_parameters["model"],
            messages=prompt_parameters["messages"] + chat_history + [{"role": "user", "content": message_content}]
        )

        response_text = response.choices[0].message['content'].strip()

        await message.channel.send(response_text)

keep_alive()
client.run(TOKEN)
