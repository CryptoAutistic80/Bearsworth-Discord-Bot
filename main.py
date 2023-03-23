import os
import discord
import openai
import json
import asyncio
from discord.ext import commands
from keep_alive import keep_alive
from discord.errors import NotFound

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

MAX_RETRIES = 5

def add_chat_history(user_id, author, content):
    global user_chat_histories
    if user_id not in user_chat_histories:
        user_chat_histories[user_id] = []
    user_chat_histories[user_id].append({"role": "user", "content": content})
    user_chat_histories[user_id] = user_chat_histories[user_id][-10:]

prompt_parameters = load_prompt_parameters('prompt_parameters.json')

api_semaphore = asyncio.Semaphore(50)

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
            await asyncio.wait_for(request_queue.join(), timeout=0.8)  # Short sleep to avoid excessive looping
        except asyncio.exceptions.TimeoutError:
            pass  # Ignore the timeout and continue the loop

async def get_response(message_author_id, message_content):
    for attempt in range(MAX_RETRIES):
        try:
            async with api_semaphore:
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(None, lambda: openai.ChatCompletion.create(
                    model=prompt_parameters["model"],
                    messages=prompt_parameters["messages"] + user_chat_histories[message_author_id] + [{"role": "user", "content": message_content}],
                    max_tokens=200
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
        thread = await ctx.channel.create_thread(name=f"Chat with {ctx.author.name}", type=discord.ChannelType.private_thread)
        await thread.send(f"Hello {ctx.author.mention}! You can start chatting with me. Type '!end' to end the conversation.")
        active_threads.add(ctx.author.id)

@client.command()
async def end(ctx):
    if isinstance(ctx.channel, discord.Thread) and ctx.channel.is_private:
        await asyncio.sleep(2)
        await ctx.channel.delete()
        active_threads.discard(ctx.author.id)

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
    except NotFound:
        pass
    except Exception as e:
        print(f"Error occurred while processing message after all retries: {e}")
        await message.channel.send("I'm sorry, there was an issue processing your request. Please try again later.")

keep_alive()
client.run(TOKEN)