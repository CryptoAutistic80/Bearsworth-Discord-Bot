Discord Chatbot with OpenAI

This Python script creates a chatbot using the Discord API and the OpenAI API. When a user sends a message to the bot in a private thread, the script sends the message to the OpenAI API to generate a response. The chatbot uses a GPT-3 language model to generate responses.

Prerequisites:

Before running this script, you need to have the following:

A Discord account and a server where you have the permission to add a bot.
A registered account on OpenAI's platform and an API key.

Setup: 

1.   Clone this repository to your local machine.
2.   Install the required Python packages using pip:

     pip install discord openai

3.   Create a prompt_parameters.json file in the same directory as the script. The file should contain the following JSON object:
    
    {
        "model": "<your GPT-3 model ID>",
        "messages": [
            {"role": "bot", "content": "Hello! How can I help you?"}
        ]
    }

4.   Replace <your GPT-3 model ID> with the ID of the GPT-3 model you want to use. You can find your model ID in your OpenAI            dashboard.

5.   Set your Discord bot token and OpenAI API key as environment variables:

export DISCORD_TOKEN=<your Discord bot token>
export KEY_OPENAI=<your OpenAI API key>

Usage:

1.   Start the chatbot by running the script:

     python main.py

2.   Add the bot to your Discord server by visiting the Discord Developers Portal, creating a new bot and copying its token.

3.   Send a direct message to the bot on Discord to start chatting! Type /chat to start a new conversation and /end to end a            conversation.

Acknowledgements:

This script was developed using the Discord.py library and the OpenAI API. Special thanks to the developers of these tools for making it easy to build chatbots with Python.