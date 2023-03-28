import os
import discord
import openai
import json
import asyncio
from discord.ext import commands
from keep_alive import keep_alive
from discord.errors import NotFound
import time
import uuid
import datetime

def load_prompt_parameters(filename):
    with open(filename, 'r') as file:
        return json.load(file)

TOKEN = os.environ['DISCORD_TOKEN']
OPENAI_KEY = os.environ['KEY_OPENAI']

openai.api_key = OPENAI_KEY

intents = discord.Intents.all()
client = commands.Bot(command_prefix="!", intents=intents)

active_threads = set()

user_chat_histories = {}

MAX_RETRIES = 10

async def save_chat_history(user_id, chat_history, chat_logs_folder="chat_logs"):
    user_folder = os.path.join(chat_logs_folder, str(user_id))
    os.makedirs(user_folder, exist_ok=True)

    timestamp = int(time.time())
    formatted_timestamp = datetime.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')

    for i in range(0, len(chat_history), 6):
        segment = chat_history[i:i+6]
        unique_id = str(uuid.uuid4())

        metadata = {
            "timestamp": formatted_timestamp,
            "unique_id": unique_id,
            "user_id": user_id,
        }

        segmented_chat_log = {
            "metadata": metadata,
            "chat_history": segment,
        }

        with open(os.path.join(user_folder, f"{unique_id}.json"), "w") as file:
            json.dump(segmented_chat_log, file, indent=4)
      

def load_chat_history(user_id, unique_ids, chat_logs_folder="chat_logs"):
    user_folder = os.path.join(chat_logs_folder, str(user_id))
    
    # Check if the user folder exists
    if not os.path.exists(user_folder):
        return []

    user_chat_histories[user_id] = []

    for unique_id in unique_ids:
        try:
            with open(os.path.join(user_folder, f"{unique_id}.json"), "r") as file:
                chat_log = json.load(file)
                if chat_log["metadata"]["user_id"] == user_id:
                    user_chat_histories[user_id].extend(chat_log["chat_history"])
        except FileNotFoundError:
            print(f"Chat history file not found for user {user_id} with unique_id {unique_id}.")

    user_chat_histories[user_id] = user_chat_histories[user_id][-20:]


def add_chat_history(user_id, author, content):
    global user_chat_histories
    if user_id not in user_chat_histories:
        user_chat_histories[user_id] = []
    user_chat_histories[user_id].append({"role": "user" if author != client.user else "assistant", "content": content})
    user_chat_histories[user_id] = user_chat_histories[user_id][-20:]

prompt_parameters = load_prompt_parameters('prompt_parameters.json')

api_semaphore = asyncio.Semaphore(30)

# Create the request queue
request_queue = asyncio.Queue()

async def process_requests():
    while True:
        tasks = []
        for _ in range(api_semaphore._value):  # Get the available API slots
            if not request_queue.empty():
                user_id, message_content, response_future = await request_queue.get()
                task = asyncio.create_task(get_response(user_id, message_content))
                tasks.append((task, response_future))
                request_queue.task_done()
            else:
                break

        if tasks:
            # Use asyncio.gather() to process tasks concurrently
            results = await asyncio.gather(*(task for task, _ in tasks), return_exceptions=True)

            # Set the result or exception in the respective response_future
            for (task, response_future), result in zip(tasks, results):
                if isinstance(result, Exception):
                    response_future.set_exception(result)
                else:
                    response_future.set_result(result)

        try:
            await asyncio.wait_for(request_queue.join(), timeout=0.5)  # Short sleep to avoid excessive looping
        except asyncio.exceptions.TimeoutError:
            pass  # Ignore the timeout and continue the loop

async def get_response(message_author_id, message_content):
    for attempt in range(MAX_RETRIES):
        try:
            async with api_semaphore:
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(None, lambda: openai.ChatCompletion.create(
                    model=prompt_parameters["model"],
                    messages=prompt_parameters["messages"] + user_chat_histories.get(message_author_id, []) + [{"role": "user", "content": message_content}],
                    max_tokens=500,
                    temperature=0.8,
                    frequency_penalty=0.25,
                    presence_penalty=0.05
                ))
            return response.choices[0].message['content'].strip()
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                print(f"Error occurred while processing message. Retry attempt {attempt + 1}: {e}")
                await asyncio.sleep(1)
            else:
                raise e

@client.event
async def on_ready():
    print('We have logged in as {0.user} in main'.format(client))
    asyncio.create_task(process_requests())

@client.command()
async def chat(ctx):
    if ctx.author.id in active_threads:
        await ctx.send("Woooah easy tiger! One conversation not enough for you?")
    else:
        print("Chat command triggered")

        # Load chat history for the user
        user_chat_dir = f"chat_logs/{ctx.author.id}"
        if os.path.exists(user_chat_dir):
            user_chat_files = sorted([f for f in os.listdir(user_chat_dir) if f.endswith(".json")], reverse=True)
        else:
            user_chat_files = []

        unique_ids = [file.split(".")[0] for file in user_chat_files[:20]] if user_chat_files else []
        load_chat_history(ctx.author.id, unique_ids)

        thread = await ctx.channel.create_thread(name=f"Chat with {ctx.author.name}", type=discord.ChannelType.private_thread)
        await thread.send(f"Hello {ctx.author.mention}! You can start chatting with me. Type '!end' to end the conversation.")
        active_threads.add(ctx.author.id)

        # Call end_inactive_conversation after starting the conversation
        asyncio.create_task(end_inactive_conversation(ctx.author.id, thread))


@client.command()
async def end(ctx):
    if isinstance(ctx.channel, discord.Thread) and ctx.channel.is_private:
        user_id = ctx.author.id
        if user_id in user_chat_histories:
            chat_history = user_chat_histories[user_id]
            await save_chat_history(user_id, chat_history)
            del user_chat_histories[user_id]

        await asyncio.sleep(2)
        await ctx.channel.delete()
        active_threads.discard(ctx.author.id)

async def end_inactive_conversation(user_id, channel, timeout=15*60):
    await asyncio.sleep(timeout)
    if user_id not in active_threads:
        return

    if user_id in user_chat_histories:
        chat_history = user_chat_histories[user_id]
        await save_chat_history(user_id, chat_history)
        del user_chat_histories[user_id]

    await channel.delete()
    active_threads.discard(user_id)

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    # Process commands first
    await client.process_commands(message)

    # Ignore messages in non-private threads or outside threads
    if not isinstance(message.channel, discord.Thread) or not message.channel.is_private:
        return

    add_chat_history(message.author.id, message.author, message.content)

    message_content = message.content
    response_future = asyncio.Future()

    await request_queue.put((message.author.id, message_content, response_future))

    try:
        response_text = await response_future
        # Check if the channel still exists before sending a message
        if message.channel:
            await message.channel.send(response_text)
            # Add the bot's response to the user's chat history
            add_chat_history(message.author.id, client.user, response_text)

    except NotFound:
        pass
    except Exception as e:
        print(f"Error occurred while processing message after all retries: {e}")
        await message.channel.send("I'm sorry, there was an issue processing your request. Please try again later.")


keep_alive()
client.run(TOKEN)