import os
import discord
import openai
import json
import asyncio
from discord.ext import commands
from keep_alive import keep_alive
from discord.errors import NotFound
import numpy as np
import time
import uuid as uuid_lib
import datetime
import pinecone

def load_prompt_parameters(filename):
    with open(filename, 'r') as file:
        return json.load(file)

TOKEN = os.environ['DISCORD_TOKEN']
OPENAI_KEY = os.environ['KEY_OPENAI']
PINECONE_KEY = os.environ.get('YOUR_PINECONE_API_KEY')

openai.api_key = OPENAI_KEY

intents = discord.Intents.all()
client = commands.Bot(command_prefix="!", intents=intents)

active_threads = set()

user_chat_histories = {}

MAX_RETRIES = 10

pinecone.init(api_key=PINECONE_KEY, environment='us-east1-gcp')
indexer = pinecone.Index("brain69")

# Define the gpt3_embedding function here
async def gpt3_embedding(content, engine='text-embedding-ada-002'):
    content = content.encode(encoding='ASCII', errors='ignore').decode()  # fix any UNICODE errors
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(None, lambda: openai.Embedding.create(
        input=content,
        engine=engine
    ))
    vector = response['data'][0]['embedding']  # this is a normal list
    return vector

async def save_chat_history(user_id, chat_history, chat_logs_folder="chat_logs"):
    try:
        user_folder = os.path.join(chat_logs_folder, str(user_id))
        os.makedirs(user_folder, exist_ok=True)

        timestamp = int(time.time())
        formatted_timestamp = datetime.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')

        for i in range(0, len(chat_history), 6):
            segment = chat_history[i:i+6]
            unique_id = str(uuid_lib.uuid4())

            metadata = {
                "timestamp": formatted_timestamp,
                "unique_id": unique_id,
                "user_id": user_id,
            }

            segmented_chat_log = {
                "metadata": metadata,
                "chat_history": segment,
            }

            # Save chat log to a file
            with open(os.path.join(user_folder, f"{unique_id}.json"), "w") as file:
                json.dump(segmented_chat_log, file, indent=4)

            # Vectorize the chat log and upsert it to the Pinecone index
            content = ' '.join([message['content'] for message in segment])
            vector = await gpt3_embedding(content)
            vector_np = np.array(vector)  # Convert the list to a NumPy array
            
            indexer.upsert([(unique_id, vector_np.tolist())], namespace="convo-logs")  # Updated line
    except Exception as e:
        print(f"Failed to save chat history: {e}")


async def query_pinecone(query, top_k=8, namespace="convo-logs"):
    query_vector = await gpt3_embedding(query)
    query_vector_np = np.array(query_vector)
    query_results = indexer.query(
        vector=query_vector_np.tolist(),
        top_k=top_k,
        namespace=namespace,
    )
    return [(match['id'], match['score']) for match in query_results['matches']]


def load_chat_history(user_id, chat_logs_folder="chat_logs"):
    user_folder = os.path.join(chat_logs_folder, str(user_id))

    # Check if the user folder exists
    if not os.path.exists(user_folder):
        return []

    user_chat_histories[user_id] = []

    chat_files = []
    for root, _, files in os.walk(user_folder):
        for file in files:
            if file.endswith(".json"):
                chat_files.append(os.path.join(root, file))

    # Sort files based on timestamp
    chat_files.sort(key=lambda x: json.load(open(x))["metadata"]["timestamp"])

    for chat_file in chat_files:
        try:
            with open(chat_file, "r") as file:
                segmented_chat_log = json.load(file)
                if segmented_chat_log["metadata"]["user_id"] == user_id:
                    user_chat_histories[user_id].extend(segmented_chat_log["chat_history"])
        except FileNotFoundError:
            print(f"Chat history file not found for user {user_id} with file {chat_file}.")


def load_recent_messages(user_id, num_messages=20, chat_logs_folder="chat_logs"):
    user_folder = os.path.join(chat_logs_folder, str(user_id))

    if not os.path.exists(user_folder):
        return []

    chat_files = []
    for root, _, files in os.walk(user_folder):
        for file in files:
            if file.endswith(".json"):
                chat_files.append(os.path.join(root, file))

    chat_files.sort(key=lambda x: json.load(open(x))["metadata"]["timestamp"], reverse=True)

    recent_messages = []
    for chat_file in chat_files:
        try:
            with open(chat_file, "r") as file:
                segmented_chat_log = json.load(file)
                if segmented_chat_log["metadata"]["user_id"] == user_id:
                    recent_messages.extend(segmented_chat_log["chat_history"])
                if len(recent_messages) >= num_messages:
                    break
        except FileNotFoundError:
            print(f"Chat history file not found for user {user_id} with file {chat_file}.")

    return recent_messages[-num_messages:]


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
            # Query Pinecone for the 10 most semantically relevant messages by UUID
            pinecone_results = await query_pinecone(message_content)
            relevant_messages = []
            for uuid_val, _ in pinecone_results:
                # search for the file in all folders under chat_logs
                for user_folder in os.listdir('chat_logs'):
                    folder_path = os.path.join('chat_logs', user_folder)
                    if not os.path.isdir(folder_path):
                        continue
                    file_path = os.path.join(folder_path, f"{uuid_val}.json")
                    if not os.path.exists(file_path):
                        continue
                    with open(file_path, "r") as f:
                        chat_log = json.load(f)
                        relevant_messages.extend(chat_log["chat_history"])

            # Combine the semantically relevant messages with the recent messages
            recent_messages = user_chat_histories.get(message_author_id, [])
            combined_messages = relevant_messages + recent_messages

            async with api_semaphore:
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(None, lambda: openai.ChatCompletion.create(
                    model=prompt_parameters["model"],
                    messages=prompt_parameters["messages"] + combined_messages + [{"role": "user", "content": message_content}],
                    max_tokens=200,
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

        # Load chat history for the user only if it's not already loaded
        if ctx.author.id not in user_chat_histories:
            load_chat_history(ctx.author.id)

        # Check if the ctx.channel is a TextChannel before creating a thread
        if not isinstance(ctx.channel, discord.TextChannel):
            await ctx.send("You can only start a chat in a text channel.")
            return

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
            asyncio.create_task(save_chat_history(user_id, chat_history))
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

    try:
        await channel.delete()
    except NotFound:
        print(f"Channel not found for user {user_id}. It may have been deleted by another process.")
    except Exception as e:
        print(f"Error occurred while deleting the channel for user {user_id}: {e}")

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

    # Load the most recent messages when the !chat command is used
    if message.content.startswith("!chat"):
        recent_messages = load_recent_messages(message.author.id)
        user_chat_histories[message.author.id] = recent_messages

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