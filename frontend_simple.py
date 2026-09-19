import streamlit as st
import requests
import json
from datetime import datetime, timedelta

BACKEND_API_URL = "http://127.0.0.1:7000/generate-weekly-meal-plan"  # Replace with your actual backend API URL

st.set_page_config(page_title="NourIQ Chat Test", layout="centered")
st.title("NourIQ AI - Chat Test Interface")

if "messages" not in st.session_state:
    st.session_state.messages = []
if "auth_token" not in st.session_state:
    st.session_state.auth_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIrOTE5NzE3MjY1NjExIiwianRpIjoiZjRlNjVhMDYtNTMwMy00MGU1LTgyZjAtZmNlY2IxOTE5YmE4IiwiVXNlcklkIjoiZWI0NmVkYTItODUzMy00MDNkLWE4ZjktMWQ3NjlkMjk4Nzc1IiwiSWQiOiJlYjQ2ZWRhMi04NTMzLTQwM2QtYThmOS0xZDc2OWQyOTg3NzUiLCJFbWFpbCI6ImF5dXNoLnJhanB1dEBub3VyaXEuYWkiLCJUZW5hbnRJZCI6IjQiLCJEYXRhYmFzZU5hbWUiOiJGcmlza2FBaUNDTV9IRldMIiwiRGF0YUJhc2VOYW1lIjoiRnJpc2thQWlDQ01fSEZXTCIsIlJvbGVOYW1lIjoiUGF0aWVudCIsIldlbGxuZXNzU3RhdHVzIjoiRW5yb2xsZWQiLCJUZW5hbnROYW1lIjoiSEZXTCBDb21wYW55IiwiV2VsbG5lc3NTdGF0dXNDb2RlIjoiRU5ST0xMRUQiLCJQYXRpZW50Rmlyc3ROYW1lIjoiQXl1c2giLCJJc0ludGVybmFsVXNlciI6IkZhbHNlIiwiUHJvamVjdFR5cGUiOiJDQ01UZXN0aW5nIiwiZXhwIjoxNzgzOTQ1MzYyLCJpc3MiOiJIRldMIiwiYXVkIjoiSEZXTCJ9.Ya8H-mknzfvu4qWTLgoYXuRgM0V121ycswxRxJb-CMA"


def call_backend(query: str):
    headers = {
        "Authorization": f"Bearer {st.session_state.auth_token}",
        "Content-Type": "application/json"
    }
    json_data = {
        "user_id": None,
        "database_name": None,
        "token": None,
        "start_date": datetime.now().strftime("%Y-%m-%d"),
        "end_date": (datetime.now() + timedelta(days=6)).strftime("%Y-%m-%d"),
        "generate_for_all_users": False,
        "force_regenerate": True
    }
    try:
        response = requests.post(BACKEND_API_URL, json=json_data, headers=headers, timeout=500)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        st.error(f"Backend Error: {e}")
        return None


# Display chat history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"], unsafe_allow_html=True)

# Chat input
if user_input := st.chat_input("Type your message..."):
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            result = call_backend(user_input)
            if result:
                if "data" in result:
                    answer = result["data"].get("answer", "No response.")
                elif "message" in result:
                    answer = result["message"]
                    if result.get("re_generated"):
                        answer += f"\n\n✅ Generated {result.get('days', 7)} days ({result.get('start_date')} → {result.get('end_date')})"
                else:
                    answer = json.dumps(result, indent=2)
            else:
                answer = "Failed to get a response from the backend."
            st.markdown(answer, unsafe_allow_html=True)

    st.session_state.messages.append({"role": "assistant", "content": answer})
    st.rerun()

# Sidebar clear button
if st.sidebar.button("Clear Chat"):
    st.session_state.messages = []
    st.rerun()
