"""
Streamlit Voice Logging Test App
Real-time speech input testing for voice logging endpoint
"""

import streamlit as st
import requests
import io
import os
from dotenv import load_dotenv
import json
import wave
import tempfile

# Try to import audio recorder (optional)
try:
    from audio_recorder_streamlit import audio_recorder
    AUDIO_RECORDER_AVAILABLE = True
except ImportError:
    AUDIO_RECORDER_AVAILABLE = False

# Load environment variables
load_dotenv()

# Configuration
AZURE_TRANSCRIPTION_ENDPOINT = os.getenv("AZURE_TRANSCRIPTION_ENDPOINT")
AZURE_TRANSCRIPTION_KEY = os.getenv("AZURE_TRANSCRIPTION_KEY")
VOICE_LOGGING_API = "http://localhost:8000/food-voice-log"
JWT_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIrOTE5NzE3MjY1NjExIiwianRpIjoiMjBjNmU4NzAtYjc4MC00NTUxLWE5MTktN2JiMDU2MzUwOWRhIiwiVXNlcklkIjoiZWI0NmVkYTItODUzMy00MDNkLWE4ZjktMWQ3NjlkMjk4Nzc1IiwiSWQiOiJlYjQ2ZWRhMi04NTMzLTQwM2QtYThmOS0xZDc2OWQyOTg3NzUiLCJFbWFpbCI6ImF5dXNoLnJhanB1dEBub3VyaXEuYWkiLCJUZW5hbnRJZCI6IjQiLCJEYXRhYmFzZU5hbWUiOiJGcmlza2FBaUNDTV9IRldMIiwiRGF0YUJhc2VOYW1lIjoiRnJpc2thQWlDQ01fSEZXTCIsIlJvbGVOYW1lIjoiUGF0aWVudCIsIldlbGxuZXNzU3RhdHVzIjoiRW5yb2xsZWQiLCJXZWxsbmVzc1N0YXR1c0NvZGUiOiJFTlJPTExFRCIsIlBhdGllbnRGaXJzdE5hbWUiOiJBeXVzaCIsIklzSW50ZXJuYWxVc2VyIjoiRmFsc2UiLCJQcm9qZWN0VHlwZSI6IkNDTVRlc3RpbmciLCJleHAiOjE3NzUxMTcxODEsImlzcyI6IkhGV0wiLCJhdWQiOiJIRldMIn0.t7ZFEkRJ90_kkxurnA-0-mNiSa6e7eJSAgPj2uWjdMQ"

# Page config
st.set_page_config(
    page_title="Voice Logging Tester",
    page_icon="🎤",
    layout="wide"
)

st.title("🎤 Voice Logging Test Application")
st.markdown("##### Type or speak your meal to get instant nutritional data")
st.markdown("---")

# Sidebar configuration
with st.sidebar:
    st.header("⚙️ Configuration")
    
    # API endpoints
    st.subheader("API Endpoints")
    azure_endpoint = st.text_input("Azure Whisper Endpoint", value=AZURE_TRANSCRIPTION_ENDPOINT, help="Must be an Azure Whisper/Audio deployment URL", type="password")
    azure_key = st.text_input("Azure API Key", value=AZURE_TRANSCRIPTION_KEY, help="Azure OpenAI API Key", type="password")
    voice_api = st.text_input("Voice Logging API", value=VOICE_LOGGING_API, help="Food extraction endpoint")
    
    # Test Azure API connection
    if azure_endpoint and azure_key:
        if st.button("🧪 Verify Azure Config"):
            st.info("📡 Configuration loaded. Ready for transcription.")
            if "whisper" not in azure_endpoint.lower() and "audio" not in azure_endpoint.lower():
                st.warning("⚠️ Warning: Your Azure URL does not look like a Whisper endpoint. Ensure you are not pointing to a text model (like gpt-4o).")
    
    # JWT Token (masked)
    st.subheader("Authentication")
    jwt_token = st.text_input("JWT Token", value=JWT_TOKEN, type="password", help="Bearer token for authentication")
    
    st.markdown("---")
    st.caption("🎙️ **Audio Transcription Tips:**")
    st.caption("1. Click Record")
    st.caption("2. **Wait 1 full second** before speaking")
    st.caption("3. Speak clearly (e.g., '2 apples')")


def transcribe_with_azure(audio_bytes, azure_endpoint, azure_key):
    """
    Transcribe audio using Azure OpenAI Whisper API.
    Passes raw bytes directly and uses a context prompt to fix short-query hallucinations.
    """
    try:
        audio_size_mb = len(audio_bytes) / (1024 * 1024)
        
        if audio_size_mb > 25:
            return None, "❌ Audio file too large (max 25MB)"
            
        # Prepare the file for upload - let Azure handle the decoding
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = "audio.wav" # Azure requires a filename to infer type
        
        # Add context prompt. This is CRITICAL for short food queries so the AI 
        # doesn't hallucinate non-food words (e.g., hears "pears" instead of "pairs")
        data = {
            "prompt": "I ate 200g of paneer, chicken, 2 apples, a cup of rice, milk, or other standard food items for my breakfast, lunch, or dinner."
        }
        
        files = {
            'file': ('audio.wav', audio_file, 'audio/wav')
        }
        
        headers = {
            'api-key': azure_key
        }
        
        st.caption(f"🔄 Sending {audio_size_mb:.2f} MB payload to Azure Whisper...")
        
        response = requests.post(
            azure_endpoint,
            headers=headers,
            files=files,
            data=data,
            timeout=60
        )
        
        if response.status_code == 200:
            result = response.json()
            # Whisper returns {'text': 'transcribed text here'}
            text = result.get('text', '').strip()
            
            if text:
                # Basic sanity check on length
                if len(text) < 2:
                    return None, f"⚠️ Transcription too short: '{text}'. Try speaking louder."
                return text, None
            else:
                return None, "❌ Audio processed, but no speech was detected."
                
        elif response.status_code == 401:
            return None, "❌ Unauthorized (401): Check your Azure API Key."
        elif response.status_code == 404:
            return None, "❌ Not Found (404): Check your Azure Endpoint URL. It must be a valid Whisper deployment."
        else:
            return None, f"❌ Azure API error ({response.status_code}): {response.text}"
            
    except requests.exceptions.ConnectionError:
        return None, "❌ Connection Error: Cannot reach Azure OpenAI API."
    except Exception as e:
        return None, f"❌ Unexpected Transcription Error: {str(e)}"


def extract_food_data(transcribed_text, voice_api, jwt_token):
    """Extract nutritional data from transcribed text"""
    try:
        if not transcribed_text or not transcribed_text.strip():
            return None, "Empty input. Please enter what you ate."
        
        if len(transcribed_text.strip()) < 3:
            return None, "Input too short. Please describe what you ate (e.g., '200g chicken')."
        
        headers = {"Authorization": f"Bearer {jwt_token}"}
        data = {"text": transcribed_text.strip()}
        
        response = requests.post(voice_api, data=data, headers=headers, timeout=60)
        
        if response.status_code == 200:
            return response.json(), None
        elif response.status_code == 400:
            error_data = response.json()
            error_msg = error_data.get('message', 'Invalid query type')
            return None, f"Invalid Input: {error_msg}"
        elif response.status_code == 401:
            return None, "Authentication Error: Invalid or expired token"
        elif response.status_code == 404:
            return None, "API Error: Voice logging endpoint not found"
        elif response.status_code == 500:
            return None, "Server Error: The backend service encountered an error"
        else:
            return None, f"API Error ({response.status_code}): {response.text}"
            
    except requests.exceptions.ConnectionError:
        return None, "Connection Error: Cannot connect to backend API. Make sure it's running at localhost:8000"
    except Exception as e:
        return None, f"Unexpected Error: {str(e)}"


def display_food_result(food_entry):
    """Display a single food entry with formatting"""
    food_name = food_entry.get('food_name', 'Unknown')
    source = food_entry.get('source', 'unknown')
    micros = food_entry.get('micros', {})
    has_options = food_entry.get('has_multiple_options', False)
    options = food_entry.get('options', [])
    
    # Extract quantity and household measure from user input
    quantity = food_entry.get('quantity', 1)  # Numerical value only (e.g., 2, 0.5, 1)
    household_measure = food_entry.get('household_measure', '')  # Unit only (e.g., "plates", "cups", "pieces")
    
    # Determine if household measure is countable (pieces, bowls) vs weight-based (grams)
    countable_measures = ['pieces', 'plates', 'bowls', 'cups', 'glasses', 'slices', 'spoons', 'servings']
    is_countable = household_measure and any(measure in household_measure.lower() for measure in countable_measures)
    
    with st.container():
        col1, col2 = st.columns([3, 1])
        with col1:
            # Always show just the food name without quantity
            st.markdown(f"### 🍽️ {food_name}")
            st.caption(f"📊 Source: {source.upper()}")
        with col2:
            if has_options:
                st.info(f"🔀 {len(options)} options")
    
    if micros:
        st.markdown("#### 📈 Nutritional Information")
        
        # Show serving info only for countable household measures (not weight-based)
        if quantity and quantity > 1 and is_countable:
            # For countable items like "4 pieces", "2 bowls"
            st.info(f"💡 **Values shown below are for {quantity} {household_measure}**")
        elif quantity and not is_countable:
            # For weight-based like "200g" - show total portion info
            if quantity != 1 and quantity != 100:  # Don't show for standard 100g serving
                st.info(f"💡 **Values shown below are for total portion ({quantity}g)**")
        
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.metric("Calories", f"{micros.get('calories', 0):.1f} kcal")
            st.metric("Protein", f"{micros.get('protein', 0):.1f}g")
        with col2:
            st.metric("Carbs", f"{micros.get('carbs', 0):.1f}g")
            st.metric("Fat", f"{micros.get('fat', 0):.1f}g")
        with col3:
            st.metric("Fiber", f"{micros.get('fiber', 0):.1f}g")
            st.metric("Sugar", f"{micros.get('sugar', 0):.1f}g")
        with col4:
            st.metric("Sodium", f"{micros.get('sodium', 0):.1f}mg")
            st.metric("Cholesterol", f"{micros.get('cholesterol', 0):.1f}mg")
        
        # Calculate portion weight from micros unit (which contains scaled grams like "400g")
        portion_weight_g = micros.get('portion_weight_g', 0)
        scaled_unit = micros.get('unit', '100g')  # This contains the weight (e.g., "400g")
        
        # If portion_weight_g is 0, try to parse from scaled_unit
        if portion_weight_g == 0 and scaled_unit:
            import re
            match = re.search(r'(\d+(?:\.\d+)?)\s*g(?:\b|$)', scaled_unit, re.IGNORECASE)
            if match:
                portion_weight_g = float(match.group(1))
        
        portion_weight_oz = portion_weight_g / 28.3495 if portion_weight_g > 0 else 0
        
        st.markdown("---")
        col_p1, col_p2, col_p3 = st.columns(3)
        with col_p1:
            # Display quantity - with unit for weight-based, without for countable
            if quantity:
                if is_countable:
                    st.metric("📊 Quantity", f"{quantity}")
                else:
                    # Weight-based: show with unit (e.g., "200g")
                    st.metric("📊 Quantity", f"{quantity}g")
            else:
                st.metric("📊 Quantity", "1")
        
        with col_p2:
            # Display household measure as unit only (cups, plates, pieces - NOT grams)
            if is_countable:
                st.metric("📏 Household Measure", household_measure)
            else:
                # For weight-based or no household measure
                if household_measure:
                    st.metric("📏 Household Measure", household_measure)
                else:
                    st.metric("📏 Household Measure", "Weight-based")
        
        with col_p3:
            if portion_weight_g > 0:
                # Show total weight for the quantity
                st.metric("⚖️ Total Weight", f"{portion_weight_g:.1f}g ({portion_weight_oz:.1f} oz)")
            else:
                st.metric("⚖️ Portion Weight", "See household measure")
        
        # Show per-serving breakdown if quantity > 1
        if quantity and quantity > 1:
            with st.expander("📊 Per Serving Breakdown"):
                # For countable items (pieces, bowls), divide by quantity
                # For weight-based (grams), show per 100g standard serving
                if is_countable:
                    st.caption(f"**Nutritional values per single {household_measure.rstrip('s')}:**")
                    cols = st.columns(4)
                    cols[0].caption(f"Calories: {micros.get('calories', 0)/quantity:.1f} kcal")
                    cols[1].caption(f"Protein: {micros.get('protein', 0)/quantity:.1f}g")
                    cols[2].caption(f"Carbs: {micros.get('carbs', 0)/quantity:.1f}g")
                    cols[3].caption(f"Fat: {micros.get('fat', 0)/quantity:.1f}g")
                    
                    if portion_weight_g > 0:
                        st.caption(f"Weight per {household_measure.rstrip('s')}: {portion_weight_g/quantity:.1f}g ({portion_weight_oz/quantity:.1f} oz)")
                else:
                    # For weight-based, show per 100g standard serving
                    scaling_factor = 100.0 / quantity  # Convert back to per 100g
                    st.caption("**Nutritional values per 100g (standard serving):**")
                    cols = st.columns(4)
                    cols[0].caption(f"Calories: {micros.get('calories', 0) * scaling_factor:.1f} kcal")
                    cols[1].caption(f"Protein: {micros.get('protein', 0) * scaling_factor:.1f}g")
                    cols[2].caption(f"Carbs: {micros.get('carbs', 0) * scaling_factor:.1f}g")
                    cols[3].caption(f"Fat: {micros.get('fat', 0) * scaling_factor:.1f}g")
    
    if has_options and options:
        with st.expander(f"🔍 View All {len(options)} Options"):
            for idx, option in enumerate(options, 1):
                opt_name = option.get('food_name', 'Unknown')
                opt_micros = option.get('micros', {})
                opt_unit = opt_micros.get('unit', '100g')
                
                st.markdown(f"**Option {idx}: {opt_name}**")
                cols = st.columns(5)
                cols[0].caption(f"Calories: {opt_micros.get('calories', 0):.1f}")
                cols[1].caption(f"Protein: {opt_micros.get('protein', 0):.1f}g")
                cols[2].caption(f"Carbs: {opt_micros.get('carbs', 0):.1f}g")
                cols[3].caption(f"Fat: {opt_micros.get('fat', 0):.1f}g")
                cols[4].caption(f"Measure: {opt_unit}")
                if idx < len(options):
                    st.divider()

# Main interface
st.header("🎙️ Voice Logging - Type or Speak Your Meal")

input_mode = st.radio(
    "Choose input method:",
    ["📝 Text Input", "🎤 Browser Recording", "📁 Upload Audio File"],
    horizontal=True,
    index=0 
)

st.markdown("---")

# Text input mode
if "Text Input" in input_mode:
    st.markdown("### ⌨️ Text Input Mode")
    
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("🍗 200g Chicken"): st.session_state.quick_text = "I had 200 grams of chicken"
    with col2:
        if st.button("🍎 2 Apples"): st.session_state.quick_text = "I ate 2 apples"
    with col3:
        if st.button("🥜 Handful of Almonds"): st.session_state.quick_text = "I had a handful of almonds"
    
    default_text = st.session_state.get('quick_text', '')
    test_input = st.text_area(
        "Enter your meal description:",
        value=default_text,
        placeholder="e.g., I had 200 grams of paneer for lunch",
        height=100
    )
    
    if default_text and test_input == default_text:
        st.session_state.quick_text = ''
    
    if st.button("🚀 Get Nutritional Data", type="primary", use_container_width=True):
        if test_input.strip():
            with st.spinner("🔍 Extracting nutritional data..."):
                result, error = extract_food_data(test_input.strip(), voice_api, jwt_token)
                
                if result:
                    st.success("✅ Successfully extracted nutritional data!")
                    st.info(test_input.strip())
                    
                    # Display raw JSON payload
                    with st.expander("🔍 View Raw JSON Response"):
                        st.json(result)
                    
                    for idx, food_entry in enumerate(result.get('meal_logging', [])):
                        if idx > 0: st.markdown("---")
                        display_food_result(food_entry)
                        
                elif error:
                    st.error(f"❌ {error}")
        else:
            st.warning("⚠️ Please enter a meal description")

# Audio Upload Mode
elif "Upload Audio File" in input_mode:
    st.markdown("### 📁 Upload Audio File")
    
    uploaded_file = st.file_uploader("Choose an audio file", type=['wav', 'mp3', 'm4a', 'ogg', 'webm'])
    
    if uploaded_file is not None:
        st.audio(uploaded_file)
        
        if st.button("🔄 Transcribe & Extract", type="primary"):
            if not azure_endpoint or not azure_key:
                st.error("❌ Azure Transcription API not configured in sidebar or .env")
            else:
                with st.spinner("🎧 Transcribing audio..."):
                    audio_bytes = uploaded_file.getvalue()
                    transcribed_text, trans_error = transcribe_with_azure(audio_bytes, azure_endpoint, azure_key)
                
                if transcribed_text:
                    st.success("✅ Transcription Complete!")
                    st.info(f"**Heard:** '{transcribed_text}'")
                    
                    with st.spinner("🔍 Extracting nutritional data..."):
                        result, error = extract_food_data(transcribed_text, voice_api, jwt_token)
                    
                    if result:
                        # Display raw JSON payload
                        with st.expander("🔍 View Raw JSON Response"):
                            st.json(result)
                        
                        for idx, food_entry in enumerate(result.get('meal_logging', [])):
                            if idx > 0: st.markdown("---")
                            display_food_result(food_entry)
                    elif error:
                        st.error(f"❌ Backend Error: {error}")
                else:
                    st.error(trans_error)

# Voice Input Mode
elif AUDIO_RECORDER_AVAILABLE and "Browser Recording" in input_mode:
    st.markdown("### 🎤 Voice Recording")
    st.warning("⚠️ **Crucial step:** Click Record, **wait 1 full second**, then speak clearly.")
    
    audio_bytes = audio_recorder(
        text="Click to record",
        recording_color="#e74c3c",
        neutral_color="#3498db",
        icon_name="microphone",
        icon_size="3x",
    )
    
    if audio_bytes:
        st.audio(audio_bytes, format="audio/wav")
        
        if st.button("🔄 Process Recording", type="primary"):
            if not azure_endpoint or not azure_key:
                st.error("❌ Azure API missing. Check sidebar configuration.")
            else:
                with st.spinner("🎧 Processing speech..."):
                    transcribed_text, trans_error = transcribe_with_azure(audio_bytes, azure_endpoint, azure_key)
                
                if transcribed_text:
                    st.success("✅ Speech recognized!")
                    st.info(f"**Heard:** '{transcribed_text}'")
                    
                    with st.spinner("🔍 Fetching nutrition..."):
                        result, error = extract_food_data(transcribed_text, voice_api, jwt_token)
                    
                    if result:
                        # Display raw JSON payload
                        with st.expander("🔍 View Raw JSON Response"):
                            st.json(result)
                        
                        for idx, food_entry in enumerate(result.get('meal_logging', [])):
                            if idx > 0: st.markdown("---")
                            display_food_result(food_entry)
                    elif error:
                        st.error(f"❌ Backend Error: {error}")
                else:
                    st.error(trans_error)
else:
    st.error("Browser recording not available. Run: `pip install audio-recorder-streamlit`")