This program has been designed to run in replit

create an open ai account and get a secret API key, store the key in secrets as KEY_OPENAI
create your discord bot and store it's token in secrets as DISCORD_TOKEN

Create prompt_parameters.json and fill it wil the prompting of your choice in the following format:

{
    "model": "gpt-3.5-turbo",
    "messages": [
        {
            "role": "system",
            "content": "INSERT CONTENT"
        },
        {
            "role": "user",
            "content": "INSERT CONTENT"
        },
        {
            "role": "assistant",
            "content": "INSERT CONTENT"
        }]
}


you can also use uptime robot to keep it running in replit indefinetly 