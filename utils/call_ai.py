import os
from dotenv import load_dotenv
from groq import Groq

# Load environment variables from .env file
load_dotenv()

print("Starting Groq client...")
api_key = os.environ.get("GROQ_API_KEY")
if api_key:
    print(f"Loading API key from environment variable...")
else:
    print("WARNING: GROQ_API_KEY not found in environment")

client = Groq(
    api_key=os.environ.get("GROQ_API_KEY"),
)


def call_ai(messages, model="llama-3.3-70b-versatile",system_prompt="You are a helpful assistant."):

    print("LLM Called Generating docker files...")

    chat_completion = client.chat.completions.create(
        messages=[
            {"role": "system", "content": system_prompt},
            *messages
        ],
        model=model,
    )

    return chat_completion.choices[0].message.content