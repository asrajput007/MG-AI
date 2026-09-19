import logging
import requests
import time
import asyncio
from datetime import datetime
from . import agent_global 

logger = logging.getLogger(__name__)

async def generate_payload_title_clean(chat_history: list, agent):
    if not chat_history:
        return "New Chat"

    chat_text = "\n".join([f"{msg.get('role', '').upper()}: {msg.get('content', '')}" 
                           for msg in chat_history[-5:]])

    system_prompt = "You are a concise assistant. Your task is to describe the current interaction intent."
    user_prompt = (
        f"Based on this history:\n{chat_text}\n\n"
        "Summarize the action in one short sentence. "
        "Check the user history last 5 messages to understand the context and generate a concise title "
        "IMPORTANT: Should be of 3-4 words. And do NOT use the word 'User'. "
        "If the user is asking for a meal plan, start with 'Meal plan Request'."
        "Do NOT include prefixes like 'Title:' or 'Normal Conversation:'. "
        "Example: 'Meal plan Request'"
    )

    try:
        title = await agent.general_llm_service.query(
            prompt=user_prompt,
            system_prompt=system_prompt,
            chat_history=[],
            max_tokens=40,
            temperature=0.3
        )
        clean_title = title.strip()
        print(f"\n>>> Generated Title: {clean_title}")
        return clean_title
    except Exception as e:
        print(f"Summary Error: {e}")
        return "Chat Session"