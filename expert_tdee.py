import streamlit as st
import requests
import json
from docx import Document
from io import BytesIO
import re
from typing import Optional, Dict
import os
import tempfile
import base64
import binascii
from datetime import datetime
from audio_recorder_streamlit import audio_recorder
BACKEND_API_URL = "http://127.0.0.1:8000/chat"
# GENERATE_DAILY_MEAL_PLAN_URL = "https://friskaaiccm-api-testing.nouriq.ai/generate-daily-meal-plan"
GENERATE_DAILY_MEAL_PLAN_URL = "http://127.0.0.1:8000/generate-daily-meal-plan"

# FITNESS_GOAL_OPTIONS = ["Weight Loss", "Muscle Gain", "Increase Overall Strength", "Improve Cardiovascular Fitness", "Improve Flexibility & Mobility", "Rehabilitation & Injury Prevention", "Improve Posture and Balance", "General Fitness", "Weight Maintenance"]
# FITNESS_LEVEL_OPTIONS = ["Beginner (0–6 months)", "Intermediate (6 months–2 years)", "Advanced (2+ years)"]
# FITNESS_MEDICAL_CONDITIONS_OPTIONS = ["None", "Hypertension (High Blood Pressure)", "Type 2 Diabetes", "Osteoarthritis", "Chronic Lower Back Pain", "Other"]
# FITNESS_DAYS_OPTIONS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
# FITNESS_DURATION_OPTIONS = ["15-20 minutes", "20-30 minutes", "30-45 minutes", "45-60 minutes"]
# FITNESS_LOCATION_OPTIONS = ["Home", "Gym", "Outdoor", "Any"]
# FITNESS_EQUIPMENT_OPTIONS = ["Bodyweight Only", "Dumbbells", "Resistance Bands", "Kettlebells", "Barbell", "Bench", "Pull-up Bar", "Yoga Mat", "Machines"]

# MEDICAL_CONDITIONS_NUTRITION_OPTIONS = [
#     "None", "Diabetes", "PCOS", "Heart Disease", "High Cholesterol", 
#     "Hypertension", "Osteoporosis", "Arthritis", "Hypothyroidism", 
#     "Hyperthyroidism", "Hashimoto's", "Hidden PCOS", "Diabetes Mellitus", "Type 1 Diabetes (T1DM)", "Type 2 Diabetes (T2DM)", "Gestational Diabetes", "Prediabetes / Impaired Glucose Tolerance (IGT)", 
#     "Insulin Resistance Syndrome", "Diabetic Nephropathy (Kidney Disease)", "Diabetic Retinopathy (Eye Disease)", "Diabetic Neuropathy (Nerve Damage)", "Cardiovascular Diseases (CVD)", "Coronary Artery Disease (CAD)",
#     "Heart Failure", "Arrhythmias", "Peripheral Artery Disease (PAD)", "Cerebrovascular Disease (CBD)", "Dyslipidemia", "High Triglycerides", "High Cholesterol (Hypercholesterolemia)", "High Blood Pressure (Hypertension)", 
#     "High Blood Sugar (Insulin Resistance)", "Obesity", "Class I Obesity", "Class II Obesity", "Class III Obesity", "Childhood Obesity", "Central Obesity (Abdominal Obesity)", "Malnutrition", "Wasting", "Stunting",
#     "Underweight", "Micronutrient Deficiencies", "Overnutrition", "Chronic Kidney Disease (CKD)", "Stage 1", "Stage 2", "Stage 3a", "Stage 3b", "Stage 4", "Stage 5 (End-Stage Renal Disease - ESRD)", "Kidney Stones (Nephrolithiasis)", 
#     "Calcium Oxalate Stones", "Uric Acid Stones", "Cystine Stones", "Struvite Stones", "Calcium Phosphate Stones", "Inflammatory Bowel Disease (IBD)", "Crohn’s Disease", "Ulcerative Colitis", "Irritable Bowel Syndrome (IBS)", 
#     "IBS-C (Constipation-predominant)", "IBS-D (Diarrhea-predominant)", "IBS-M (Mixed)", "Small Intestinal Bacterial Overgrowth (SIBO)", "Hydrogen-dominant SIBO", "Methane-dominant SIBO", "Hydrogen Sulfide SIBO", "Diverticular Disease",
#     "Diverticulosis", "Diverticulitis", "Celiac Disease", "Non-Celiac Gluten Sensitivity", "Lactose Intolerance", "Primary Lactose Intolerance (Adult-type hypolactasia)", "Secondary Lactose Intolerance", "Congenital Lactose Intolerance",
#     "Developmental Lactose Intolerance", "Anemia", "Iron-deficiency Anemia", "Thyroid", "Vitamin B12 Deficiency Anemia", "Folate Deficiency Anemia", "Anemia of Chronic Disease", "Aplastic Anemia", "Hemolytic Anemia", "Sickle Cell Anemia", "Thalassemia", 
#     "Osteoporosis", "Postmenopausal Osteoporosis", "Senile Osteoporosis", "Secondary Osteoporosis", "Arthritis", "Osteoarthritis (OA)", "Rheumatoid Arthritis (RA)", "Gout", "Psoriatic Arthritis", "Ankylosing Spondylitis", "Childhood Arthritis (Juvenile Idiopathic Arthritis)", 
#     "Fibromyalgia", "Cancer", "Breast Cancer", "Colorectal Cancer", "Lung Cancer", "Prostate Cancer", "Fatty Liver (Non-Alcoholic Fatty Liver Disease)", "Non-Alcoholic Fatty Liver Disease (NAFLD)", "Non-Alcoholic Steatohepatitis (NASH)", "Autoimmune Hepatitis", "Sleep Apnea", 
#     "Obstructive Sleep Apnea (OSA)", "Central Sleep Apnea (CSA)", "Complex Sleep Apnea Syndrome", "Binge Eating Disorder (BED)", "Depression", "Major Depressive Disorder (MDD)", "Persistent Depressive Disorder (Dysthymia)", "Postpartum Depression", "Seasonal Affective Disorder (SAD)", "Bipolar Disorder (Depressive Episodes)", 
#     "Anxiety Disorders", "Generalized Anxiety Disorder (GAD)", "Panic Disorder", "Social Anxiety Disorder", "Obsessive-Compulsive Disorder (OCD)", "Post-Traumatic Stress Disorder (PTSD)", "Specific Phobias", "Attention-Deficit/Hyperactivity Disorder (ADHD)", "Predominantly Inattentive Presentation", "Graves' disease"
# ]
# DIGESTIVE_ISSUES_OPTIONS = ["None", "Occasional bloating", "Acid reflux", "IBS","Constipation", "Diarrhea", "Stomach pain", "Nausea", "Excessive gas"]
# CUISINE_OPTIONS = ["None", "African", "Australian", "Brazilian", "Caribbean", "Central American", "Chinese",
#     "East African", "East Asian", "Eastern European", "Ethiopian", "European", "French",
#     "German", "Greek", "Indian", "Indian North", "Indian South", "Indonesian", "International",
#     "Iranian", "Irish", "Italian", "Japanese", "Korean", "Latin American", "Lebanese",
#     "Mediterranean", "Mexican", "Middle Eastern", "Moroccan", "North American", "North African",
#     "Peruvian", "Polish", "Portuguese", "Russian", "Scandinavian", "Scottish", "South American",
#     "Southeast Asian", "Spanish", "Thai", "Turkish", "Vietnamese", "Western", "West African",
#     "Yemeni", "Zulu"
# ]
# CUISINE_OPTIONS.sort()
# FOOD_ALLERGIES_OPTIONS = ["None", "Paneer", "Spinach", "Peanuts", "Tree nuts", "Shellfish", "Fish", "Eggs", "Milk", "Soy", "Wheat", "Sesame", "Corn", "Legumes"]

# EXPERT_NAMES = ["Dr. Anjali Singh", "Dr. Amit Patel"]
# SESSION_TYPES = ["Yoga", "Stress Relief", "Nutritional Advice"]
# APPOINTMENT_TYPES = ["Video Call", "Audio Call"]

st.set_page_config(page_title="NourIQ Ai: Your Personalized Nutrition Assistant", layout="wide")

def get_recipe_details(food_name: str) -> dict:
    """
    Generate recipe details for a food item.
    In production, this would fetch from database or API.
    """
    # Clean food name for better recipe generation
    food_clean = food_name.strip()
    
    # Generate basic recipe structure based on food name
    recipe = {
        "ingredients": [],
        "steps": [],
        "prep_time_minutes": 15
    }
    
    # Common ingredient patterns
    if "rice" in food_clean.lower():
        recipe["ingredients"] = [
            f"{food_clean} - as specified in portion",
            "Water - 2 cups per cup of rice",
            "Salt - to taste"
        ]
        recipe["steps"] = [
            "1. Rinse rice under cold water until water runs clear",
            "2. Add rice and water to pot in 1:2 ratio",
            "3. Bring to boil, then reduce heat to low",
            "4. Cover and simmer for 15-20 minutes",
            "5. Let rest for 5 minutes before serving"
        ]
        recipe["prep_time_minutes"] = 25
    elif "chicken" in food_clean.lower() or "poultry" in food_clean.lower():
        recipe["ingredients"] = [
            f"{food_clean} - as specified in portion",
            "Olive oil - 1 tbsp",
            "Salt and pepper - to taste",
            "Garlic - 2 cloves (optional)",
            "Herbs - as desired"
        ]
        recipe["steps"] = [
            "1. Season the chicken with salt, pepper, and herbs",
            "2. Heat oil in a pan over medium heat",
            "3. Cook chicken until golden brown and cooked through",
            "4. Let rest for 5 minutes before serving"
        ]
        recipe["prep_time_minutes"] = 20
    elif "vegetable" in food_clean.lower() or "veggies" in food_clean.lower():
        recipe["ingredients"] = [
            f"{food_clean} - as specified in portion",
            "Olive oil - 1 tsp",
            "Salt - to taste",
            "Black pepper - to taste"
        ]
        recipe["steps"] = [
            "1. Wash and cut vegetables as desired",
            "2. Heat oil in a pan",
            "3. Add vegetables and sauté until tender",
            "4. Season with salt and pepper"
        ]
        recipe["prep_time_minutes"] = 10
    elif "salad" in food_clean.lower():
        recipe["ingredients"] = [
            f"{food_clean} - as specified in portion",
            "Olive oil - 1 tbsp",
            "Lemon juice - 1 tbsp",
            "Salt and pepper - to taste"
        ]
        recipe["steps"] = [
            "1. Wash and prepare salad ingredients",
            "2. Mix all ingredients in a bowl",
            "3. Drizzle with oil and lemon juice",
            "4. Toss well and serve fresh"
        ]
        recipe["prep_time_minutes"] = 5
    elif "egg" in food_clean.lower():
        recipe["ingredients"] = [
            f"{food_clean} - as specified in portion",
            "Butter or oil - 1 tsp",
            "Salt and pepper - to taste"
        ]
        recipe["steps"] = [
            "1. Heat pan with butter/oil over medium heat",
            "2. Cook eggs as desired (scrambled, fried, etc.)",
            "3. Season with salt and pepper",
            "4. Serve hot"
        ]
        recipe["prep_time_minutes"] = 5
    elif "dal" in food_clean.lower() or "lentil" in food_clean.lower():
        recipe["ingredients"] = [
            f"{food_clean} - as specified in portion",
            "Water - 3 cups",
            "Turmeric powder - 1/4 tsp",
            "Salt - to taste",
            "Cumin seeds - 1/2 tsp",
            "Oil - 1 tsp"
        ]
        recipe["steps"] = [
            "1. Rinse lentils thoroughly",
            "2. Pressure cook with water and turmeric for 3-4 whistles",
            "3. Season with salt when cooked",
            "4. Temper with cumin seeds in hot oil",
            "5. Pour tempering over dal and mix well"
        ]
        recipe["prep_time_minutes"] = 20
    elif "yogurt" in food_clean.lower() or "curd" in food_clean.lower():
        recipe["ingredients"] = [
            f"{food_clean} - as specified in portion"
        ]
        recipe["steps"] = [
            "1. Serve chilled yogurt as is",
            "2. Can be sweetened or used as side dish"
        ]
        recipe["prep_time_minutes"] = 0
    elif "fruit" in food_clean.lower() or "apple" in food_clean.lower() or "banana" in food_clean.lower():
        recipe["ingredients"] = [
            f"{food_clean} - as specified in portion"
        ]
        recipe["steps"] = [
            "1. Wash fruit thoroughly",
            "2. Peel if necessary",
            "3. Cut into desired pieces",
            "4. Serve fresh"
        ]
        recipe["prep_time_minutes"] = 3
    else:
        # Generic recipe for any other food
        recipe["ingredients"] = [
            f"{food_clean} - as specified in portion",
            "Basic seasonings - salt, pepper, oil"
        ]
        recipe["steps"] = [
            "1. Prepare ingredients as needed",
            f"2. Cook or prepare {food_clean} according to standard methods",
            "3. Season to taste",
            "4. Serve as desired"
        ]
        recipe["prep_time_minutes"] = 15
    
    return recipe

def display_enhanced_meal_plan(meal_plan_text: str):
    """
    Display meal plan with expandable recipe details for each food item.
    Parses the meal plan text directly to create interactive dropdowns.
    """
    try:
        # Display header if present (e.g., "Meal Plan for date (2026-04-10):")
        header_match = re.search(r'^Meal Plan for date \(.+?\):', meal_plan_text, re.MULTILINE)
        if header_match:
            st.markdown(f"### {header_match.group(0)}")
            st.markdown("---")
        
        # Parse meal plan sections
        lines = meal_plan_text.split('\n')
        current_meal = None
        meal_items = {}
        
        # Regex patterns for parsing (supports both integer and decimal formats)
        meal_header_pattern = re.compile(r'^\*\*(.+?)\*\*$')
        food_item_pattern = re.compile(
            r'^-\s*(.+?)\s*\|\s*Household Measure:\s*(.+?)\s*\|\s*Portion Weight:\s*(.+?)\s*\|\s*'
            r'Protein:\s*([\d.]+)g\s*\|\s*Carbs:\s*([\d.]+)g\s*\|\s*Fat:\s*([\d.]+)g\s*\|\s*Fiber:\s*([\d.]+)g\s*\|\s*'
            r'Sodium:\s*([\d.]+)mg\s*\|\s*Iodine:\s*([\d.]+)mcg\s*\|\s*Sugar:\s*([\d.]+)g\s*\|\s*Cholesterol:\s*([\d.]+)mg\s*\|\s*'
            r'Calories:\s*([\d.]+)kcal'
        )
        total_calories_pattern = re.compile(r'^Total calories for (.+?):\s*([\d.]+)\s*kcal$')
        macro_ratio_pattern = re.compile(r'^Macronutrient Ratio:\s*(.+)$')
        
        # Parse the meal plan
        meal_summaries = {}
        for line in lines:
            line = line.strip()
            
            # Check for meal header
            meal_match = meal_header_pattern.match(line)
            if meal_match:
                meal_name = meal_match.group(1)
                if meal_name not in ["Daily Total Calories:", "Overall Macronutrient Distribution:", "Condition-Specific Notes:"]:
                    current_meal = meal_name
                    meal_items[current_meal] = []
                continue
            
            # Check for food item
            food_match = food_item_pattern.match(line)
            if food_match and current_meal:
                food_data = {
                    'name': food_match.group(1).strip(),
                    'household_measure': food_match.group(2).strip(),
                    'portion_weight': food_match.group(3).strip(),
                    'protein': float(food_match.group(4)),
                    'carbs': float(food_match.group(5)),
                    'fat': float(food_match.group(6)),
                    'fiber': float(food_match.group(7)),
                    'sodium': float(food_match.group(8)),
                    'iodine': float(food_match.group(9)),
                    'sugar': float(food_match.group(10)),
                    'cholesterol': float(food_match.group(11)),
                    'calories': float(food_match.group(12))
                }
                meal_items[current_meal].append(food_data)
                continue
            
            # Check for meal summary
            total_cal_match = total_calories_pattern.match(line)
            if total_cal_match:
                meal_name = total_cal_match.group(1)
                if meal_name not in meal_summaries:
                    meal_summaries[meal_name] = {'calories': total_cal_match.group(2)}
                continue
            
            macro_match = macro_ratio_pattern.match(line)
            if macro_match and current_meal:
                if current_meal in meal_summaries:
                    meal_summaries[current_meal]['macros'] = macro_match.group(1)
        
        # Check if we successfully parsed any food items
        total_items = sum(len(items) for items in meal_items.values())
        if total_items == 0:
            # No items parsed, fall back to simple text display
            st.markdown(meal_plan_text, unsafe_allow_html=True)
            return
        
        # Display the enhanced meal plan
        for meal_name, items in meal_items.items():
            if not items:
                # Skip empty meals
                continue
                
            st.markdown(f"### **{meal_name}**")
            
            # Display meal summary if available
            if meal_name in meal_summaries:
                summary = meal_summaries[meal_name]
                st.markdown(f'<div class="meal-summary">Total calories for {meal_name}: {summary.get("calories", "0")} kcal</div>', unsafe_allow_html=True)
                if 'macros' in summary:
                    st.markdown(f'<div class="meal-summary">Macronutrient Ratio: {summary["macros"]}</div>', unsafe_allow_html=True)
            
            # Display each food item with recipe dropdown
            for food_item in items:
                with st.expander(f"🍽️ {food_item['name']} ({food_item['calories']:.0f} kcal)"):
                    # Display nutrition info
                    col1, col2 = st.columns(2)
                    with col1:
                        st.markdown("**Nutrition Info:**")
                        st.write(f"• **Calories: {food_item['calories']:.0f} kcal**")
                        st.write(f"• Household Measure: {food_item['household_measure']}")
                        st.write(f"• Portion: {food_item['portion_weight']}")
                        st.write(f"• Protein: {food_item['protein']:.0f}g")
                        st.write(f"• Carbs: {food_item['carbs']:.0f}g")
                        st.write(f"• Fat: {food_item['fat']:.0f}g")
                    
                    with col2:
                        st.write(f"• Fiber: {food_item['fiber']:.0f}g")
                        st.write(f"• Sodium: {food_item['sodium']:.0f}mg")
                        st.write(f"• Iodine: {food_item['iodine']:.0f}mcg")
                        st.write(f"• Sugar: {food_item['sugar']:.0f}g")
                        st.write(f"• Cholesterol: {food_item['cholesterol']:.0f}mg")
                    
                    st.divider()
                    
                    # Fetch and display recipe details
                    recipe = get_recipe_details(food_item['name'])
                    
                    st.markdown("**📖 Recipe:**")
                    st.write(f"⏱️ Prep Time: **{recipe['prep_time_minutes']} minutes**")
                    
                    st.markdown("**Ingredients:**")
                    for ingredient in recipe["ingredients"]:
                        st.write(f"• {ingredient}")
                    
                    st.markdown("**Steps:**")
                    for step in recipe["steps"]:
                        st.write(step)
            
            st.markdown("---")
        
        # Display remaining content (daily totals, notes, etc.)
        daily_total_match = re.search(r'\*\*Daily Total Calories:\*\*n(.+?)(?=\n\n|\Z)', meal_plan_text, re.DOTALL)
        if daily_total_match:
            st.markdown(f'<div class="meal-summary">{daily_total_match.group(0)}</div>', unsafe_allow_html=True)
        
        overall_macro_match = re.search(r'\*\*Overall Macronutrient Distribution:\*\*n(.+?)', meal_plan_text)
        if overall_macro_match:
            st.markdown(f'<div class="meal-summary">{overall_macro_match.group(0)}</div>', unsafe_allow_html=True)
        
        notes_match = re.search(r'\*\*Condition-Specific Notes:\*\*\n(.+?)(?=\n\n---|\Z)', meal_plan_text, re.DOTALL)
        if notes_match:
            st.markdown("### **Condition-Specific Notes:**")
            notes = notes_match.group(1).strip().split('\n')
            for note in notes:
                if note.strip().startswith('-'):
                    st.info(note.strip()[1:].strip())
        
        # Display save prompt if present
        save_prompt_match = re.search(r'Would you like me to save.+', meal_plan_text)
        if save_prompt_match:
            st.markdown(f"**{save_prompt_match.group(0)}**")
    
    except Exception as e:
        # If parsing fails, fall back to simple text
        st.markdown(meal_plan_text, unsafe_allow_html=True)

st.markdown("""
<style>
    .meal-summary {
        font-size: 30px !important;
        font-weight: bold !important;
        color: #FFFFFF;
    }
    .stButton>button {
        width: 100%;
        margin-bottom: 5px;
        background-color: #4CAF50;
        color: white;
        padding: 10px 15px;
        border: none;
        border-radius: 8px;
        cursor: pointer;
        font-size: 16px;
        transition: background-color 0.3s ease;
        box-shadow: 2px 2px 5px rgba(0,0,0,0.2);
    }
    .stButton>button:hover {
        background-color: #45a049;
    }
    .stButton>button:active {
        background-color: #3e8e41;
        box_shadow: 0 0 0 rgba(0,0,0,0.2);
        transform: translateY(1px);
    }
    .unified-button-nutrition > button {
        background-color: #4CAF50 !important;
    }
    .unified-button-fitness > button {
        background-color: #764ba2 !important;
    }
    .unified-button-waterstep > button {
        background-color: #3e8e41 !important;
    }
    .unified-button-booking > button {
        background-color: #007bff !important;
    }
    /* Custom style for the combined input area */
    /* st.chat_input uses a complex structure. Targeting the elements within the column is safer. */
    .stAudioRecorder button {
        /* Ensure the microphone button is visually distinct and prominent */
        background-color: #dc3545 !important; 
        border-radius: 50%;
        width: 40px;
        height: 40px;
        padding: 0;
        display: flex;
        align-items: center;
        justify-content: center;
    }
    .stAudioRecorder button:hover {
        background-color: #c82333 !important;
    }
</style>
""", unsafe_allow_html=True)


st.image("image.png") 
st.title("Your Personalized Health Assistant (Unified Profile)")

if "messages" not in st.session_state:
    st.session_state.messages = []
if "profile_complete" not in st.session_state:
    st.session_state.profile_complete = False
if "current_constraints" not in st.session_state:
    st.session_state.current_constraints = {}
if "last_agent_context" not in st.session_state:
    st.session_state.last_agent_context = {}
if "pending_images" not in st.session_state:
    st.session_state.pending_images = []
# Always update auth token to latest value (avoids stale expired tokens in session)
st.session_state.auth_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIrOTE5NzE3MjY1NjExIiwianRpIjoiNDAwMjI5YjAtNzg0OS00NjYwLTg1Y2QtMjkzM2RjMmU3OWRhIiwiVXNlcklkIjoiZWI0NmVkYTItODUzMy00MDNkLWE4ZjktMWQ3NjlkMjk4Nzc1IiwiSWQiOiJlYjQ2ZWRhMi04NTMzLTQwM2QtYThmOS0xZDc2OWQyOTg3NzUiLCJFbWFpbCI6ImF5dXNoLnJhanB1dEBub3VyaXEuYWkiLCJUZW5hbnRJZCI6IjQiLCJEYXRhYmFzZU5hbWUiOiJGcmlza2FBaUNDTV9IRldMIiwiRGF0YUJhc2VOYW1lIjoiRnJpc2thQWlDQ01fSEZXTCIsIlJvbGVOYW1lIjoiUGF0aWVudCIsIldlbGxuZXNzU3RhdHVzIjoiRW5yb2xsZWQiLCJXZWxsbmVzc1N0YXR1c0NvZGUiOiJFTlJPTExFRCIsIlBhdGllbnRGaXJzdE5hbWUiOiJBeXVzaCIsIklzSW50ZXJuYWxVc2VyIjoiRmFsc2UiLCJQcm9qZWN0VHlwZSI6IkNDTVRlc3RpbmciLCJleHAiOjE3Nzk0NzMyMjcsImlzcyI6IkhGV0wiLCJhdWQiOiJIRldMIn0.9BM8MP2EUv1dhs7kaPH4EkdzePlRufWd4xUSue65d_c"
if "blood_report_temp_path" not in st.session_state:
    st.session_state.blood_report_temp_path = None
if "fitness_profile_data" not in st.session_state:
    st.session_state.fitness_profile_data = {}
    
# --- CRITICAL FIX 1: DYNAMIC MIC KEY INITIALIZATION ---
if "mic_key" not in st.session_state:
    st.session_state.mic_key = "initial_mic_key"


def call_backend_api(payload: dict, token: str, files: list = None):
    try:
        headers = {
            "Authorization": f"Bearer {token}"
            # Do NOT manually set Content-Type here; 'requests' does it for multipart
        }
        
        # We need to send 'query' and 'last_agent_context' as Form fields
        form_data = {
            "query": payload.get("query", ""),
            "last_agent_context": json.dumps(payload.get("last_agent_context", {}))
        }

        # Handle Image files for the 'image' parameter in your backend
        file_payload = None
        if files and len(files) > 0:
            # Taking the first image as your backend 'image' parameter expects one file
            file_payload = {"image": (files[0].name, files[0].getvalue(), files[0].type)}

        # Use 'data=' for form-data, 'files=' for the image
        response = requests.post(
            BACKEND_API_URL, 
            data=form_data, 
            files=file_payload, 
            headers=headers, 
            timeout=1200
        )
        
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        st.error(f"Backend Error: {e}")
        return None

def convert_images_to_base64_urls(files: list):
    try:
        base64_urls = []
        for uploaded_file in files:
            image_bytes = uploaded_file.getvalue()
            base64_encoded = base64.b64encode(image_bytes).decode('utf-8')
            
            mime_type = uploaded_file.type if uploaded_file.type else "image/jpeg"
            data_url = f"data:{mime_type};base64,{base64_encoded}"
            base64_urls.append(data_url)
        
        return base64_urls
    except Exception as e:
        st.error(f"Failed to process images: {e}")
        return None

# def send_message_to_backend(user_input: str, force_tool: Optional[str] = None, images: list = None):
#     with st.spinner("Friska is thinking..."):
#         api_payload = {
#             "query": user_input,
#             "chat_history": st.session_state.messages, 
#             "current_constraints": st.session_state.current_constraints,
#             "last_agent_context": st.session_state.last_agent_context,
#             "force_tool_type": force_tool 
#         }
        
#         response_data = call_backend_api(api_payload, st.session_state.auth_token, files = images) 

#         if response_data:
#             st.session_state.messages.append({
#                 "role": "assistant", 
#                 "content": response_data.get("answer"), 
#                 "audio": response_data.get("audio"), 
#                 "context": response_data.get("context")
#             })
                
#             st.session_state.current_constraints = response_data.get("updated_constraints", {})
#             st.session_state.last_agent_context = response_data.get("updated_last_agent_context", {})
    
#             st.rerun()
            
def send_message_to_backend(user_input: str, force_tool: Optional[str] = None, images: list = None):
    with st.spinner("Friska is thinking..."):
        api_payload = {
            "query": user_input,
            "constraints": st.session_state.current_constraints,
            "last_agent_context": st.session_state.last_agent_context,
            "force_tool_type": force_tool 
        }
        
        full_response = call_backend_api(api_payload, st.session_state.auth_token, files=images) 

        if full_response and "data" in full_response:
            # Extract 'ai_data' from the 'data' key returned by your FastAPI chat endpoint
            ai_data = full_response["data"]
            
            st.session_state.messages.append({
                "role": "assistant", 
                "content": ai_data.get("answer"), # Backend uses 'answer'
                "audio": ai_data.get("audio_base64"), # Your backend uses 'audio_base64'
                "context": ai_data.get("context")
            })
                
            # Update history and context
            st.session_state.last_agent_context = ai_data.get("updated_last_agent_context", {})
    
            st.rerun()

def save_uploaded_file_temp(uploaded_file):
    try:
        temp_dir = "temp_uploads"
        os.makedirs(temp_dir, exist_ok=True)
        
        temp_file_path = os.path.join(temp_dir, uploaded_file.name)
        counter = 1
        original_temp_file_path = temp_file_path
        while os.path.exists(temp_file_path):
            name, ext = os.path.splitext(original_temp_file_path)
            temp_file_path = f"{name}_{counter}{ext}"
            counter += 1

        with open(temp_file_path, "wb") as f:
            f.write(uploaded_file.getvalue())
        return temp_file_path
    except Exception as e:
        st.error(f"Error saving file: {e}")
        return None

def render_unified_profile_form():
    st.header("1. Complete Your Health Profile")
    st.info("Fill out the relevant sections below and click the corresponding button to generate your plan or set your goals.")
    
    with st.form(key="unified_profile_form"):

        st.markdown("---")
        st.subheader("🥝 Nutrition Assistant Profile")

        st.markdown("##### Expert TDEE Override (Optional)")
        st.caption("If an expert has prescribed a specific TDEE value, enter it here. This will override the auto-calculated TDEE for meal plan generation.")
        nut_expert_tdee = st.number_input("Expert TDEE (kcal)", min_value=0.0, max_value=10000.0, value=0.0, step=50.0, format="%.0f", key="nut_expert_tdee", help="Leave at 0 to use auto-calculated TDEE based on your profile.")

        nut_submitted = st.form_submit_button("Generate My Meal Plan", type="primary", use_container_width=True, help="Creates or updates your Nutrition Profile and generates a personalized Meal Plan.", key="nut_submit_btn")


    if nut_submitted:
        with st.spinner("Creating your profile and generating a personalized meal plan..."):
            try:
                headers = {
                    "Authorization": f"Bearer {st.session_state.auth_token}",
                    "Content-Type": "application/json"
                }
                request_body = {}
                if nut_expert_tdee > 0:
                    request_body["expert_tdee"] = nut_expert_tdee
                    st.session_state.current_constraints["expert_tdee"] = nut_expert_tdee

                resp = requests.post(
                    GENERATE_DAILY_MEAL_PLAN_URL,
                    json=request_body,
                    headers=headers,
                    timeout=1200
                )
                resp.raise_for_status()
                resp_data = resp.json()

                if resp_data.get("status") == 200:
                    raw_text = resp_data.get("raw_text", "")
                    parsed_output = resp_data.get("parsed_output", {})
                    recipes = resp_data.get("recipes", [])
                    grocery_list = resp_data.get("grocery_list", [])
                    
                    # Store parsed meal plan, recipes, and grocery list for quick access
                    st.session_state.parsed_meal_plan = parsed_output
                    st.session_state.meal_plan_recipes = recipes
                    st.session_state.meal_plan_grocery_list = grocery_list
                    
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": raw_text,
                        "audio": None,
                        "context": {"is_meal_plan": True}
                    })
                    st.session_state.profile_complete = True
                    st.rerun()
                else:
                    st.error(f"API Error: {resp_data.get('message', 'Unknown error')}")
            except requests.exceptions.RequestException as e:
                st.error(f"Backend Error: {e}")


if not st.session_state.profile_complete:
    render_unified_profile_form()

else:

    chat_col, buttons_col = st.columns([3, 1])

    with chat_col:
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                content_to_display = message.get("content", "")
                
                if not isinstance(content_to_display, str):
                    content_to_display = json.dumps(content_to_display, indent=2)

                context = message.get("context", {})
                is_meal_plan = context.get("is_meal_plan", False) if isinstance(context, dict) else False
                
                if message["role"] == "assistant" and is_meal_plan:
                    # Display enhanced meal plan with recipe dropdowns
                    display_enhanced_meal_plan(content_to_display)
                else:
                    st.markdown(content_to_display, unsafe_allow_html=True)
                
                if message["role"] == "assistant" and context and context.get("play_video"):
                    video_file_name = context["play_video"]
                    video_path = video_file_name 
                    
                    if os.path.exists(video_path):
                        st.video(video_path)
                    else:
                        st.warning(f"Video file '{video_file_name}' not found. Please ensure it is placed in the 'videos' directory.")
      
                
                if message["role"] == "assistant" and message.get("audio"):
                    audio_data = message["audio"]
    # Check if the data is a string and long enough to be a real Base64 audio file
                    if audio_data and isinstance(audio_data, str) and len(audio_data) > 100: 
                        try:
                            audio_bytes = base64.b64decode(audio_data, validate=True)
                            st.audio(audio_bytes, format="audio/mp3")
                        except (binascii.Error, TypeError, ValueError) as e:
            # Error now caught, providing a debug message without crashing the frontend
                            print(f"DEBUG: Could not decode audio for a message. Error: {e}")
                            st.info("TTS Audio playback skipped due to an audio decoding error from the backend.")


        if st.session_state.last_agent_context.get("expecting_meal_image_upload"):
            uploaded_meal_image = st.file_uploader("Upload a photo of what you ate", type=["jpg", "jpeg", "png"], key="meal_image_upload")

            if uploaded_meal_image:
                st.session_state.last_agent_context["expecting_meal_image_upload"] = False

                with st.spinner("Analyzing your meal image..."):
                    try:
                        response = requests.post(
                            "http://127.0.0.1:8502/analyze-meal-image",
                            files={"file": uploaded_meal_image.getvalue()}
                        )
                        result = response.json()

                        if result.get("success"):
                            analysis_text = result["analysis_text"]
                            st.session_state.messages.append({"role": "user", "content": analysis_text})
                            send_message_to_backend(analysis_text)
                        else:
                            st.error(f"Analysis failed: {result.get('error')}")
                    except Exception as e:
                        st.error(f"Error contacting backend for image analysis: {e}")

        # --- START MODIFIED MIC/INPUT SECTION (Optimized for Streamlit 1.52.1) ---
        
        # Streamlit's chat_input is complex. Use the old-style columns for better control over mic placement
        col_mic, col_upload, col_input = st.columns([0.5, 0.5, 4])
        
        audio_bytes = None
        mic_available = False

        # Audio Recording Section
        with col_mic:
            st.markdown("<div style='height: 30px;'></div>", unsafe_allow_html=True) # Vertical alignment spacer
            try:
                # --- CRITICAL FIX 2: Use the dynamic key for the audio recorder ---
                audio_bytes = audio_recorder(
                    text="",
                    key=st.session_state.mic_key 
                )
                mic_available = True
            except Exception as e:
                # Catch-all for any failure, allowing the app to continue
                st.error(f"🎙️ Mic component failed to load. Error: {e}", icon="⚠️")
                mic_available = False

        # Image Upload
        with col_upload:
             uploaded_images = st.file_uploader(
                "📎",
                type=["jpg", "jpeg", "png"],
                accept_multiple_files=True,
                key="chat_image_uploader",
                label_visibility="collapsed"
            )
        
        # Text Input & Logic
        with col_input:
            
            # --- CRITICAL FIX 4: CHECK AND RESET PENDING VOICE STATE ---
            # Use a session state flag to prevent re-execution of the audio logic 
            # after the first successful transcription.
            if "voice_input_ready" not in st.session_state:
                st.session_state.voice_input_ready = None
                
            transcribed_text_to_process = None
            if st.session_state.voice_input_ready:
                transcribed_text_to_process = st.session_state.voice_input_ready
                st.session_state.voice_input_ready = None # Consume the state
                st.session_state.pending_images = []
                
            if transcribed_text_to_process:
                user_input = transcribed_text_to_process
                st.session_state.messages.append({"role": "user", "content": user_input})
                
                # Display the transcribed text immediately in the chat column
                with chat_col:
                    with st.chat_message("user"):
                        st.markdown(f"*(Voice)*: {user_input}") 
                
                # Send the transcribed text to your main conversational API
                send_message_to_backend(user_input, images=None) # Images are now handled in the previous turn if needed
                
                # We do NOT call st.rerun here; send_message_to_backend will call it after the API response is received.
            
            # Check if audio was recorded successfully
            # This is the original logic that *prepares* the transcribed text.
            elif audio_bytes is not None and mic_available:
                st.info("Processing recorded voice command...")
                with st.spinner("Transcribing voice command..."):
                    try:
                        # Call your backend's STT endpoint to transcribe the audio
                        base_url = GENERATE_DAILY_MEAL_PLAN_URL.rsplit('/', 1)[0]
                        
                        stt_res = requests.post(
                            f"{base_url}/transcribe/", 
                            files={'file': ('audio.wav', audio_bytes, 'audio/wav')},
                            timeout=100
                        )
                        stt_res.raise_for_status()
                        transcribed_text = stt_res.json().get('transcribed_text', '')

                        if transcribed_text and "Could not understand" not in transcribed_text:
                            # CRITICAL: Store the transcribed text and force a rerun
                            st.session_state.voice_input_ready = transcribed_text
                            
                            # Force key change to reset the microphone widget
                            st.session_state.mic_key = str(datetime.now()) 
                            
                            # CRITICAL: Rerun to process the `voice_input_ready` state
                            st.rerun() 

                        else:
                            st.warning("Sorry, I didn't catch that. Could you please type your command instead?")
                            # Clear the key even on failure to reset the mic button.
                            st.session_state.mic_key = str(datetime.now()) 
                            st.rerun() # Rerun to reset mic state
                            
                    except Exception as e:
                        st.error(f"Error during transcription: {e}")
                        # Clear the key even on critical failure
                        st.session_state.mic_key = str(datetime.now()) 
                        st.rerun() # Rerun to reset mic state

            elif user_input := st.chat_input("Ask me about your plan or anything else..."):
                
                images_to_send = st.session_state.pending_images.copy() if st.session_state.pending_images else None
                
                st.session_state.messages.append({"role": "user", "content": user_input})
                with chat_col:
                    with st.chat_message("user"):
                        st.markdown(user_input)
                        if images_to_send:
                            for img in images_to_send:
                                st.image(img, width=200)
                
                st.session_state.pending_images = []
                
                send_message_to_backend(user_input, images=images_to_send)

        # Handle pending images display
        if uploaded_images:
            st.session_state.pending_images = uploaded_images
            
        # Display image upload count clearly *outside* the chat_input if images are pending
        if st.session_state.pending_images:
            st.caption(f"{len(st.session_state.pending_images)} image(s) selected for next message.")

        # --- END MODIFIED MIC/INPUT SECTION ---


    with buttons_col:
        st.subheader("Meal Check-in")
        
        meal_types = ["Breakfast", "Morning Snack", "Lunch", "Evening Snack", "Dinner"]

        plan_context = st.session_state.last_agent_context
        current_plan_text = plan_context.get("pending_meal_plan_for_save") or plan_context.get("active_meal_plan")
        
        is_daily_plan = current_plan_text and plan_context.get("plan_type") == "DAILY"
        is_weekly_plan = current_plan_text and plan_context.get("plan_type") == "WEEKLY"
        plan_exists = bool(current_plan_text)

        for meal_type in meal_types:
            if st.button(f"Check-in for {meal_type}", disabled=not plan_exists, help="Please generate a meal plan first to enable check-in." if not plan_exists else None, key=f"checkin_{meal_type}"):
                user_query = f"Hi I just got my {meal_type} reminder. Can you help me log what I ate"
                
                st.session_state.messages.append({"role": "user", "content": user_query})
                
                send_message_to_backend(user_query, force_tool="meal_check_in", images=None)

        st.subheader("Generate Daily Meal Plan")
        st.caption("Use the `/generate-daily-meal-plan` API with optional Expert TDEE override.")
        
        expert_tdee_input = st.number_input(
            "Expert TDEE (kcal)", 
            min_value=0.0, max_value=10000.0, value=float(st.session_state.current_constraints.get("expert_tdee", 0.0)), 
            step=50.0, format="%.0f", 
            key="output_expert_tdee",
            help="Enter expert-prescribed TDEE. Leave 0 to use auto-calculated value."
        )
        
        force_regen = st.checkbox(
            "Force regenerate (ignore existing plan)",
            value=False,
            key="force_regenerate_checkbox",
            help="Check this to generate a fresh meal plan even if one exists for today"
        )
        
        if st.button("Generate Daily Meal Plan", key="btn_generate_daily_meal_plan", use_container_width=True):
            with st.spinner("Generating daily meal plan..."):
                try:
                    headers = {"Authorization": f"Bearer {st.session_state.auth_token}", "Content-Type": "application/json"}
                    request_body = {"force_regenerate": force_regen}  # Always send this parameter
                    
                    if expert_tdee_input > 0:
                        request_body["expert_tdee"] = expert_tdee_input
                        st.session_state.current_constraints["expert_tdee"] = expert_tdee_input
                    
                    if force_regen:
                        print(f"🔥 Frontend: Sending force_regenerate=True to backend")
                    else:
                        print(f"ℹ️ Frontend: Sending force_regenerate=False to backend")
                    
                    print(f"📤 Frontend: Request body = {request_body}")
                    
                    resp = requests.post(GENERATE_DAILY_MEAL_PLAN_URL, json=request_body, headers=headers, timeout=1200)
                    resp.raise_for_status()
                    resp_data = resp.json()
                    
                    if resp_data.get("status") == 200:
                        raw_text = resp_data.get("raw_text", "")
                        is_existing = resp_data.get("is_existing", False)
                        parsed_output = resp_data.get("parsed_output", {})
                        recipes = resp_data.get("recipes", [])
                        grocery_list = resp_data.get("grocery_list", [])
                        message = resp_data.get("message", "")
                        
                        # Store parsed meal plan, recipes, and grocery list
                        st.session_state.parsed_meal_plan = parsed_output
                        st.session_state.meal_plan_recipes = recipes
                        st.session_state.meal_plan_grocery_list = grocery_list
                        
                        st.session_state.messages.append({"role": "assistant", "content": raw_text, "audio": None, "context": {"is_meal_plan": True}})
                        
                        # Show appropriate success message
                        if is_existing:
                            st.info(message)
                        else:
                            st.success(message)
                        
                        st.rerun()
                    else:
                        st.error(f"API Error: {resp_data.get('message', 'Unknown error')}")
                except requests.exceptions.RequestException as e:
                    st.error(f"Request failed: {e}")

        st.subheader("📖 Recipes")
        
        has_recipes = "meal_plan_recipes" in st.session_state and st.session_state.meal_plan_recipes
        
        if st.button("View All Recipes", 
                      disabled=not has_recipes, 
                      help="Generate a meal plan first to view recipes." if not has_recipes else "See cooking instructions for all food items.", 
                      key="view_recipes"):
            if has_recipes:
                recipes = st.session_state.meal_plan_recipes
                
                # Format recipes for display
                recipes_text = "# 📖 Your Meal Plan Recipes\n\n"
                current_meal = None
                
                for recipe_item in recipes:
                    meal_type = recipe_item.get("meal_type")
                    food_name = recipe_item.get("food_name")
                    recipe = recipe_item.get("recipe", {})
                    
                    if meal_type != current_meal:
                        recipes_text += f"\n## **{meal_type}**\n\n"
                        current_meal = meal_type
                    
                    recipes_text += f"### {food_name}\n\n"
                    
                    # Ingredients
                    ingredients = recipe.get("ingredients", [])
                    if ingredients:
                        recipes_text += "**Ingredients:**\n"
                        for ing in ingredients:
                            recipes_text += f"- {ing}\n"
                        recipes_text += "\n"
                    
                    # Steps
                    steps = recipe.get("steps", [])
                    if steps:
                        recipes_text += "**Instructions:**\n"
                        for step in steps:
                            recipes_text += f"{step}\n"
                        recipes_text += "\n"
                    
                    prep_time = recipe.get("prep_time_minutes", 0)
                    if prep_time > 0:
                        recipes_text += f"*Prep time: {prep_time} minutes*\n\n"
                    
                    recipes_text += "---\n\n"
                
                # Add to messages
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": recipes_text,
                    "audio": None,
                    "context": {"is_recipes": True}
                })
                st.rerun()

        st.subheader("🛒 Grocery List")
        
        has_grocery_list = "meal_plan_grocery_list" in st.session_state and st.session_state.meal_plan_grocery_list
        
        if st.button("Generate Grocery List", 
                      disabled=not has_grocery_list, 
                      help="Generate a meal plan first to create a grocery list." if not has_grocery_list else "Get a shopping list with all ingredients.", 
                      key="generate_grocery_list"):
            if has_grocery_list:
                grocery_list = st.session_state.meal_plan_grocery_list
                
                # Format grocery list for display
                grocery_text = "# 🛒 Your Grocery List\n\n"
                grocery_text += f"**Total Ingredients:** {len(grocery_list)}\n\n"
                grocery_text += "## Shopping List\n\n"
                
                for item in grocery_list:
                    ingredient = item.get("ingredient")
                    quantity = item.get("quantity")
                    times_used = item.get("times_used", 1)
                    
                    usage_info = f" (used in {times_used} recipes)" if times_used > 1 else ""
                    grocery_text += f"- **{ingredient}**: {quantity}{usage_info}\n"
                
                grocery_text += "\n---\n\n"
                grocery_text += "*Tip: Check your pantry before shopping to avoid duplicate purchases!*\n"
                
                # Add to messages
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": grocery_text,
                    "audio": None,
                    "context": {"is_grocery_list": True}
                })
                st.rerun()
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": grocery_text,
                    "audio": None,
                    "context": {"is_grocery_list": True}
                })
                st.rerun()

    st.sidebar.title("Actions")
    st.sidebar.title("🧪 TTS Debugging")

    last_assistant_message = None
    for msg in reversed(st.session_state.messages):
        if msg.get("role") == "assistant":
            last_assistant_message = msg
            break

    if last_assistant_message:
        original_text = last_assistant_message.get("content", "N/A")
        summarized_text = last_assistant_message.get("audio")

        st.sidebar.subheader("Original Text (Input to Summary)")
        st.sidebar.text_area("Original", value=original_text, height=200, key="debug_original", disabled=True)

        # Updated Debugging for the Base64 Audio data
        st.sidebar.subheader("Base64 Audio Data (from backend)")
        if summarized_text:
            st.sidebar.text_area("Base64 String", value=summarized_text[:100] + "..." if len(summarized_text) > 100 else summarized_text, height=100, key="debug_summary", disabled=True)
            if len(summarized_text) < 50:
                 st.sidebar.warning("Note: The 'audio' field is short. This might be a mistake if Base64 audio was expected.")
        else:
            st.sidebar.info("No audio data was sent for the last message.")
    else:
        st.sidebar.info("No assistant message yet to display.")

    st.sidebar.divider()
    
    if st.sidebar.button("Start Over"):
        token = st.session_state.get("auth_token", "")
        if st.session_state.blood_report_temp_path and os.path.exists(st.session_state.blood_report_temp_path):
            try:
                os.remove(st.session_state.blood_report_temp_path)
                print(f"Cleaned up temporary blood report file: {st.session_state.blood_report_temp_path}")
            except Exception as e:
                print(f"Error cleaning up temporary blood report file: {e}")
        
        for key in list(st.session_state.keys()):
            if key not in ["auth_token"]:
                del st.session_state[key]
        st.session_state.profile_complete = False
        st.rerun()
    
    if st.sidebar.button("Save Chat Logs"):
        doc = Document()
        profile = st.session_state.get('current_constraints', {})
        doc.add_heading(f"Friska Chat History for {profile.get('name', 'User')}", level=1)
        if profile:
            doc.add_heading("User Profile", level=2)
            profile_text_parts = []
            for k, v in profile.items():
                if k not in ['vitals_numeric', 'vitals_text']:
                    key_title = k.replace('_', ' ').title()
                    value_str = ", ".join(v) if isinstance(v, list) else str(v)
                    profile_text_parts.append(f"{key_title}: {value_str}")
            
            if 'vitals_numeric' in profile and profile['vitals_numeric']:
                profile_text_parts.append("\nVitals:")
                for vital_key, vital_value in profile['vitals_numeric'].items():
                        profile_text_parts.append(f"- {vital_key.replace('_', ' ').title()}: {vital_value}")

            doc.add_paragraph("\n".join(profile_text_parts))
        
        doc.add_heading("Conversation", level=2)
        for msg in st.session_state.messages:
            content = msg.get('content', '')
            if isinstance(content, dict):
                content = json.dumps(content, indent=2)

            clean_content = re.sub(r'<.*?>', '', content)
            clean_content = clean_content.replace("**", "")
            doc.add_paragraph(f"{msg['role'].title()}: {clean_content}", style='Body Text')
        
        doc_io = BytesIO()
        doc.save(doc_io)
        doc_io.seek(0)
        
        st.sidebar.download_button(
            label="Download Chat Logs as .docx",
            data=doc_io,
            file_name=f"friska_chat_{profile.get('name', 'user').lower()}_history.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )