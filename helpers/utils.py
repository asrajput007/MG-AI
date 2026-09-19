import ast
import asyncio
from collections import defaultdict
from datetime import date, datetime, timedelta, time, timezone
from decimal import Decimal
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
import hashlib
import io
import json
import random
import re
import logging
import threading
import os
import difflib
import urllib.parse
from typing import Any, Iterable, Iterator, Optional, List, Dict, Generator
from urllib.parse import urlparse, parse_qs, unquote_plus, quote
import time as ti
import uuid
from openai import AzureOpenAI
import requests
import jwt
import pyodbc
from rapidfuzz import fuzz, process
from dotenv import load_dotenv
from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions
from fastapi import HTTPException, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from helpers.database import get_db_connection_dynamic
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


logger = logging.getLogger(__name__)


import os
from dotenv import load_dotenv

load_dotenv()

def load_models(env_key: str):
    raw = os.getenv(env_key)
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError
        return data
    except Exception:
        raise RuntimeError(f"{env_key} must be a JSON list of model configs")


MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY")
MISTRAL_API_ENDPOINT = os.getenv("MISTRAL_API_ENDPOINT")
MISTRAL_MODEL = os.getenv("MISTRAL_MODEL")
AZURE_API_KEY_01 = os.getenv("AZURE_API_KEY_01")
AZURE_ENDPOINT_01 = os.getenv("AZURE_ENDPOINT_01")
REDIS_URL = os.getenv("REDIS_URL")
REDIS_CA_CERT_PATH_MG = os.getenv("REDIS_CA_CERT_PATH_MG")
REDIS_CA_CERT_PATH = os.getenv("REDIS_CA_CERT_PATH")
WHISPER_URL = os.getenv("WHISPER_URL")
KOKORO_API_URL = os.getenv("KOKORO_API_URL")
AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
AZURE_CONTAINER_NAME = os.getenv("AZURE_CONTAINER_NAME")
PERSONALIZED_API_KEY = os.getenv("PERSONALIZED_API_KEY")
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
MASTER_TABLE_DB_NAME = os.getenv("MASTER_TABLE_DB_NAME")
DYNAMIC_DB_NAME = os.getenv("DYNAMIC_DB_NAME")
AZURE_STORAGE_ACCOUNT_NAME  = os.getenv("AZURE_STORAGE_ACCOUNT_NAME")
AZURE_STORAGE_ACCOUNT_KEY  = os.getenv("AZURE_STORAGE_ACCOUNT_KEY")
AZURE_FILE_SHARE_NAME  = os.getenv("AZURE_FILE_SHARE_NAME")
AZURE_EXERCISE_CONTAINER_NAME = os.getenv("AZURE_EXERCISE_CONTAINER_NAME")
AZURE_EXERCISE_ACCOUNT_NAME = os.getenv("AZURE_EXERCISE_ACCOUNT_NAME")
AZURE_EXERCISE_ACCOUNT_KEY = os.getenv("AZURE_EXERCISE_ACCOUNT_KEY")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION")
AZURE_DEPLOYMENT_NAME = os.getenv("AZURE_DEPLOYMENT_NAME")
SMTP_SERVER = os.getenv("SMTP_SERVER")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", 587))
EMAIL_USERNAME = os.getenv("EMAIL_USERNAME")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_FROM = os.getenv("EMAIL_FROM")
USE_TLS = os.getenv("USE_TLS", "TRUE") == "TRUE"
USE_SSL = os.getenv("USE_SSL", "FALSE") == "TRUE"
ENVIRONMENT = os.getenv("ENVIRONMENT")
MISTRAL_API_KEY_PATIENT_ENGAGEMENT = os.getenv("MISTRAL_API_KEY_PATIENT_ENGAGEMENT")
MISTRAL_API_ENDPOINT_PATIENT_ENGAGEMENT = os.getenv("MISTRAL_API_ENDPOINT_PATIENT_ENGAGEMENT")
MISTRAL_API_KEY_PATIENT_ENGAGEMENT_1 = os.getenv("MISTRAL_API_KEY_PATIENT_ENGAGEMENT_1")
MISTRAL_API_ENDPOINT_PATIENT_ENGAGEMENT_1 = os.getenv("MISTRAL_API_ENDPOINT_PATIENT_ENGAGEMENT_1")
UNSUBSCRIBE_ENDPOINT = os.getenv("UNSUBSCRIBE_ENDPOINT")

START_DATE_STR = "29042025"  # DDMMYYYY
START_DATE = datetime.strptime(START_DATE_STR, "%d%m%Y")
def get_today_index():
    today = datetime.now()
    return (today - START_DATE).days + 1  # ID starts from 1

if not AZURE_STORAGE_CONNECTION_STRING:
    raise ValueError("AZURE_STORAGE_CONNECTION_STRING is not set in environment variables")

if not AZURE_CONTAINER_NAME:
    raise ValueError("AZURE_CONTAINER_NAME is not set in environment variables")

blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
container_client = blob_service_client.get_container_client(AZURE_CONTAINER_NAME)

azure_client = AzureOpenAI(
    api_key=AZURE_OPENAI_API_KEY,
    api_version=AZURE_OPENAI_API_VERSION,
    azure_endpoint=AZURE_OPENAI_ENDPOINT
)

# Ensure consistent weekday ordering
WEEKDAY_ORDER = {
    "Monday": 1,
    "Tuesday": 2,
    "Wednesday": 3,
    "Thursday": 4,
    "Friday": 5,
    "Saturday": 6,
    "Sunday": 7,
}

#workout dictionary mapping
focused_workout_tips_mapping = {
    "Diabetes (Type 1, Type 2, Gestational)": "Diabetes_Time_Based_Workout_Tips",
    "Hypothyroidism / Hyperthyroidism": "Thyroid_Disorders_Time_Based_Workout_Tips",#doubt
    "Polycystic Ovary Syndrome (PCOS)": "PCOS_Time_Based_Workout_Tips",
    "Metabolic Syndrome": "Metabolic_Syndrome_Time_Based_Workout_Tips",
    "Hypertension (High Blood Pressure)": "Hypertension_Time_Based_Workout_Tips",
    "High Cholesterol (Hyperlipidemia)": "Hyperlipidemia_Time_Based_Workout_Tips",
    "Coronary Artery Disease": "CAD_Time_Based_Workout_Tips", #doubt
    "Irritable Bowel Syndrome (IBS)": "IBS_Time_Based_Workout_Tips",
    "Inflammatory Bowel Disease (IBD)": "IBD_Time_Based_Workout_Tips",
    "Acid Reflux / GERD": "GERD_Time_Based_Workout_Tips",
    "Celiac Disease": "Celiac_Time_Based_Workout_Tips",
    "Lactose Intolerance": "Lactose_Intolerance_Time_Based_Workout_Tips",
    "Chronic Kidney Disease (CKD)": "CKD_Time_Based_Workout_Tips",
    "Dialysis Support": "Dialysis_Time_Based_Workout_Tips",
    "Fatty Liver Disease (NAFLD/NASH)": "Fatty_Liver_Time_Based_Workout_Tips",
    "Asthma": "Asthma_Time_Based_Workout_Tips",
    "Chronic Obstructive Pulmonary Disease (COPD)": "COPD_Time_Based_Workout_Tips",
    "Depression": "Depression_Time_Based_Workout_Tips",
    "ADHD": "ADHD_Time_Based_Workout_Tips",
    "Alzheimer’s / Cognitive Decline": "Alzheimers_Time_Based_Workout_Tips",
    "Insomnia / Sleep Apnea": "Sleep_Apnea_Time_Based_Workout_Tips",
    "Arthritis (Osteoarthritis, Rheumatoid Arthritis)": "Arthritis_Time_Based_Workout_Tips",
    "Gout": "Gout_Time_Based_Workout_Tips",
    "COVID-19 Recovery": "Covid-19_Time_Based_Workout_Tips",
    "Tuberculosis (Nutrition Support)": "Tuberculosis_TB_Time_Based_Workout_Tips",
    "HIV/AIDS": "HIV_AIDS_Time_Based_Workout_Tips",
    "PCOS": "PCOS_Time_Based_Workout_Tips",
    "Dairy Intolerance": "FODMAP_IBS_Time_Based_Workout_Tips",
    "Epilepsy": "Epilepsy_Time_Based_Workout_Tips",
    "Cognitive Decline / Dementia (Alzheimer's)": "Alzheimers_Time_Based_Workout_Tips",
    "Undernutrition / Malnutrition": "Malnutrition_Time_Based_Workout_Tips",
    "Osteoporosis": "",
    "Obesity": "",
    "Anxiety": "",
    "Migraines": "",
    "Epilepsy": "",
    "Congestive Heart Failure": "",
    "Arrhythmia": "",
    "Stroke Recovery Support": "",
    "Gallbladder Disease": "",
    "Constipation / Diarrhea": "",
    "Kidney Stones": "",
    "Insulin Resistance": "",
    "Fibromyalgia": "",
    "Hepatitis": "",
    "Cirrhosis": "",
    "Lupus": "",
    "Multiple Sclerosis": "",
    "PMS / PMDD": "",
    "Menopause": "",
    "Pregnancy (Trimester Support)": "",
    "Postpartum Recovery": "",
    "Lactation Support": "",
    "Childhood Obesity": "",
    "ADHD": "",
    "Autism Spectrum Nutrition Support": "",
    "Failure to Thrive": "",
    "During Chemotherapy": "",
    "Post-treatment Recovery": "",
    "Appetite & Nutritional Needs": "",
    "Dengue / Typhoid Recovery": "",
    "Acne": "",
    "Psoriasis": "",
    "Eczema": "",
    "Vitiligo": "",
    "Gluten Sensitivity": "",
    "Nut Allergy": "",
    "Egg Allergy": "",
    "Shellfish Allergy": "",
    "Soy Allergy": "",
    "Smoking / Alcohol Use": "",
    "Sedentary Lifestyle": "",
    "Stress / Burnout": "",
    "Work-Related Health Issues": "",
    "Prenatal & Perinatal Conditions": "",
    "Developmental Disorders": "",
    "Malaria": "",
    "Hepatitis (A, B, C)": "",
    "Vaccine-preventable Diseases (supportive care)": "",
    "Eating Disorders (Anorexia, Bulimia)": "",
    "Substance Use Disorders": "",
   " Post-Traumatic Stress Disorder (PTSD)": "",
    "Dermatological Infections (Fungal, Bacterial)": "",
}

def validate_profile_for_meal_plan(profile: dict) -> tuple[bool, str]:
    """
    Validates that essential profile fields are present for meal plan generation.
    
    Returns:
        tuple: (is_valid, error_message)
    """
    required_fields = {
        'name': 'Name',
        'height_cm': 'Height',
        'weight_kg': 'Weight', 
        'gender': 'Gender',
        'activity_level': 'Activity Level',
        'age': 'Age'
    }
    
    missing_fields = []
    
    for field_key, field_name in required_fields.items():
        value = profile.get(field_key)
        
        # Check if value is None, empty string, or "unknown"
        if value is None or value == "" or value == "unknown":
            missing_fields.append(field_name)
            logger.warning(f"Profile validation: '{field_key}' is missing/invalid (value={value!r})")
    
    if missing_fields:
        error_msg = f"Cannot generate meal plan. Missing required information: {', '.join(missing_fields)}"
        logger.warning(f"Profile validation FAILED: {error_msg}")
        return False, error_msg
    
    return True, ""
   
def extract_daily_total_calories(meal_text: str):
    """
    Extracts the Daily Total Calories value from meal_text, handling various formats.
    Returns the float value if found, else None.
    """
    # Try with asterisks and colon
    match = re.search(r"\*{0,2}Daily Total Calories\*{0,2}[:：]?\s*([\d.]+)", meal_text, re.IGNORECASE)
    if match:
        return float(match.group(1))
    # Try without asterisks
    match = re.search(r"Daily Total Calories[:：]?\s*([\d.]+)", meal_text, re.IGNORECASE)
    if match:
        return float(match.group(1))
    # Try with possible "Total Calories" only
    match = re.search(r"Total Calories[:：]?\s*([\d.]+)", meal_text, re.IGNORECASE)
    if match:
        return float(match.group(1))
    return None

def parse_items_to_meal_plan_text(items: list[dict]) -> str:
    """
    Converts a list of meal plan item dicts into a formatted meal plan text.
    """
    # Group items by MealType
    from collections import defaultdict
    meal_type_map = {
        "Breakfast": "Breakfast",
        "MorningSnack": "Morning Snack",
        "Lunch": "Lunch",
        "EveningSnack": "Evening Snack",
        "Dinner": "Dinner"
    }
    grouped = defaultdict(list)
    for item in items:
        meal_type = meal_type_map.get(item["MealType"], item["MealType"])
        grouped[meal_type].append(item)

    lines = []
    for meal in ["Breakfast", "Morning Snack", "Lunch", "Evening Snack", "Dinner"]:
        if meal in grouped:
            lines.append(f"{meal}")
            for item in grouped[meal]:
                lines.append(
                    f"{item['FoodItems']} | Portion Weight: {item['PortionWeight']}{item['PortionWeightUnit']} | Protein: {item['Protein']}g | Carbs: {item['Carbohydrates']}g | Fat: {item['Fat']}g | Fiber: {item['Fiber']}g | Sodium: {item['Sodium']}mg | Sugar: {item['Sugar']}g | Cholesterol: {item['Cholesterol']}mg | Calories: {item['Calories']}kcal"
                )
            lines.append("")  # Blank line after each meal
    return "\n".join(lines).strip()

def parse_nutrient_value(text: str, nutrient_name: str) -> float:
    match = re.search(rf"\b{nutrient_name}\b:\s*([\d.]+)", text, re.IGNORECASE)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return 0.0
    return 0.0

def parse_meal_plan_text_ios(meal_plan_text: str):
    start_marker = "**Breakfast**"
    end_pattern = re.compile(r'^\s*\*\*Daily Total Calories:.*$', re.IGNORECASE | re.MULTILINE)
    meal_name_re = re.compile(r"\*\*(.+?)\*\*")
    meal_total_cal_re = re.compile(r"Total calories for (.+?):\s*([\d.]+) kcal", re.IGNORECASE)
    # daily_total_cal_re = re.compile(r"\*\*Daily Total Calories:\*\*\s*([\d.]+) kcal", re.IGNORECASE)

    # --- Initialize variables for prefix and suffix text ---
    prefix_text = ""
    suffix_text = ""
    parsing_successful = False # Flag to track if JSON was generated

    # --- Find markers and extract prefix/suffix ---
    start_index = meal_plan_text.find(start_marker)
    end_match = end_pattern.search(meal_plan_text)

    if start_index != -1:
        # Find the start of the line containing the start marker
        prefix_end_index = meal_plan_text.rfind('\n', 0, start_index) + 1
        prefix_text = meal_plan_text[:prefix_end_index].strip()

    if end_match is not None:
        suffix_start_index = end_match.end()
        suffix_text = meal_plan_text[suffix_start_index:].strip()


    # --- Check if the text matches the expected meal plan format ---
    # If start marker or end pattern is not found, print original text and skip parsing
    if start_index == -1 or end_match is None:
        return {"error": "Meal plan format invalid or missing markers."}, prefix_text, suffix_text

        pass # Skip the parsing logic below
    else:
        # --- Proceed with Parsing if Format Matches ---
        logger.info("Input text matches expected format. Proceeding with parsing...")
        parsing_successful = True # Set flag as parsing will be attempted

        # Initialize the main data structure
        parsed_data = {
            "meal_plan": {},
            "meals_nutrients": {},
            "total_nutrients": {}
        }

        # Extract Relevant Text Portion (between start and end markers)
        first_line_start = meal_plan_text.rfind('\n', 0, start_index) + 1
        relevant_text = meal_plan_text[first_line_start:end_match.start()]

        # Initialize Grand Totals
        grand_total_protein = 0.0
        grand_total_carbs = 0.0
        grand_total_fiber = 0.0
        grand_total_fat = 0.0
        grand_total_calories = 0.0
        grand_total_portion_weight = 0.0
        grand_total_sodium = 0.0
        grand_total_iodine = 0.0
        grand_total_sugar = 0.0
        grand_total_cholesterol = 0.0

        
        total_calories = extract_daily_total_calories(meal_plan_text)
        if total_calories is None:
            parsed_output = {"error": "Warning: Daily Total Calories line pattern not found in original text."}
        else:
            parsed_output = {"daily_total_calories": total_calories}

        # Process Meal Sections from Relevant Text
        # sections = relevant_text.strip().split('---')
        sections = meal_name_re.split(relevant_text)

        for i in range(1, len(sections), 2):
                meal_name = sections[i].strip()
                meal_text = sections[i + 1].strip()
            
                if meal_name.lower() == "daily total calories:":
                    continue
            
                parsed_data["meal_plan"][meal_name] = []
            
                # Initialize meal totals
                meal_total_protein = 0.0
                meal_total_carbs = 0.0
                meal_total_fiber = 0.0
                meal_total_fat = 0.0
                meal_total_calories = 0.0
                meal_total_portion_weight = 0.0
                meal_total_sodium = 0.0
                meal_total_iodine = 0.0
                meal_total_sugar = 0.0
                meal_total_cholesterol = 0.0
            
                lines = meal_text.split('\n')
                for line in lines:
                    line = line.strip()
                    if line.startswith('-'):
                        parts = line.split('|', 1)
                        if len(parts) == 2:
                            item_name = parts[0][1:].strip()
                            nutrient_text = parts[1].strip()

                            # Extract individual nutrients
                            calories = parse_nutrient_value(nutrient_text, "Calories")
                            protein = parse_nutrient_value(nutrient_text, "Protein")
                            carbs = parse_nutrient_value(nutrient_text, "Carbs")
                            fiber = parse_nutrient_value(nutrient_text, "Fiber")
                            fat = parse_nutrient_value(nutrient_text, "Fat")
                            portion_weight = parse_nutrient_value(nutrient_text, "Portion Weight")
                            sodium = parse_nutrient_value(nutrient_text, "Sodium")
                            iodine = parse_nutrient_value(nutrient_text, "Iodine")
                            sugar = parse_nutrient_value(nutrient_text, "Sugar")
                            cholesterol = parse_nutrient_value(nutrient_text, "Cholesterol")

                            parsed_data["meal_plan"][meal_name].append({"name": item_name,
                                "nutrients": {
                                    "Portion Weight": portion_weight,
                                    "Protein": protein,
                                    "Carbohydrates": carbs,
                                    "Fat": fat,
                                    "Fiber": fiber,
                                    "Sodium": sodium,
                                    "Iodine": iodine,
                                    "Sugar": sugar,
                                    "Cholesterol": cholesterol,
                                    "Calories": calories
                                },
                                "Consumed":"False"
                            })

                            # Accumulate meal totals
                            meal_total_protein += protein
                            meal_total_carbs += carbs
                            meal_total_fiber += fiber
                            meal_total_fat += fat
                            meal_total_portion_weight += portion_weight
                            meal_total_sodium += sodium
                            meal_total_iodine += iodine
                            meal_total_sugar += sugar
                            meal_total_cholesterol += cholesterol
                        # else: # Removed warning for malformed line as it might catch meal titles incorrectly
                        #    print(f"Warning: Skipping malformed item line: {line}")


                    total_cal_match = meal_total_cal_re.search(line)
                    if total_cal_match:
                        try:
                            if total_cal_match.group(1).strip().lower() == meal_name.lower():
                                meal_total_calories = float(total_cal_match.group(2))
                        except ValueError:
                            return {"error": f"Warning: Could not parse total calories from line: {line}"}, prefix_text, suffix_text

                # Store calculated/extracted meal totals
                if meal_total_calories == 0.0 and not any(item for item in parsed_data["meal_plan"][meal_name] if item.get("name","").startswith("Calories for")):
                    return  {"error":f"Warning: Total calories line not found or parsed correctly for meal: {meal_name}. Meal total calories might be inaccurate."}, prefix_text, suffix_text

                parsed_data["meals_nutrients"][meal_name] = {
                    "Calories": meal_total_calories,
                    "Portion Weight": round(meal_total_portion_weight, 1),
                    "Protein": round(meal_total_protein, 1),
                    "Carbohydrates": round(meal_total_carbs, 1),
                    "Fat": round(meal_total_fat, 1),
                    "Fiber": round(meal_total_fiber, 1),
                    "Sodium": round(meal_total_sodium, 1),
                    "Iodine": round(meal_total_iodine, 1),
                    "Sugar": round(meal_total_sugar, 1),
                    "Cholesterol": round(meal_total_cholesterol, 1)
                }

                # Accumulate grand totals
                grand_total_protein += meal_total_protein
                grand_total_carbs += meal_total_carbs
                grand_total_fiber += meal_total_fiber
                grand_total_fat += meal_total_fat
                grand_total_portion_weight += meal_total_portion_weight
                grand_total_sodium += meal_total_sodium
                grand_total_iodine += meal_total_iodine
                grand_total_sugar += meal_total_sugar
                grand_total_cholesterol += meal_total_cholesterol

        # Finalize and Output JSON
        parsed_data["total_nutrients"] = {
            "Calories": grand_total_calories,
            "Portion Weight": round(grand_total_portion_weight, 1),
            "Protein": round(grand_total_protein, 1),
            "Carbohydrates": round(grand_total_carbs, 1),
            "Fat": round(grand_total_fat, 1),
            "Fiber": round(grand_total_fiber, 1),
            "Sodium": round(grand_total_sodium, 1),
            "Iodine": round(grand_total_iodine, 1),
            "Sugar": round(grand_total_sugar, 1),
            "Cholesterol": round(grand_total_cholesterol, 1)
        }

        final_output_structure = [parsed_data]
        json_output = json.dumps(final_output_structure, indent=2)



    # --- Print Prefix and Suffix Text ---
    logger.info("\n--- Prefix Text ---")
    if prefix_text:
        logger.info(prefix_text)
    else:
        # Only print this if parsing wasn't attempted (i.e., format didn't match)
        if not parsing_successful:
            prefix_text="No text found before start marker or format mismatch"
        else: # If parsing happened but prefix was empty
            prefix_text="No text found before start marker"


    logger.info("\n--- Suffix Text ---")
    if suffix_text:
        logger.info(suffix_text)
    else:
        # Only print this if parsing wasn't attempted (i.e., format didn't match)
        if not parsing_successful:
            suffix_text="No text found after end pattern or format mismatch"
        else: # If parsing happened but suffix was empty
            suffix_text="No text found after end pattern"
    return json_output, prefix_text, suffix_text

def parse_meal_plan_text(meal_plan_text: str):
    start_marker = "**Breakfast**"
    end_pattern = re.compile(r'^\s*\*\*Daily Total Calories:.*$', re.IGNORECASE | re.MULTILINE)
    meal_name_re = re.compile(r"\*\*(.+?)\*\*")
    meal_total_cal_re = re.compile(r"Total calories for (.+?):\s*([\d.]+) kcal", re.IGNORECASE)
    daily_total_cal_re = re.compile(r"\*\*Daily Total Calories:\*\*\s*([\d.]+) kcal", re.IGNORECASE)

    # --- Initialize variables for prefix and suffix text ---
    prefix_text = ""
    suffix_text = ""
    parsing_successful = False # Flag to track if JSON was generated

    # --- Find markers and extract prefix/suffix ---
    start_index = meal_plan_text.find(start_marker)
    end_match = end_pattern.search(meal_plan_text)

    if start_index != -1:
        # Find the start of the line containing the start marker
        prefix_end_index = meal_plan_text.rfind('\n', 0, start_index) + 1
        prefix_text = meal_plan_text[:prefix_end_index].strip()

    if end_match is not None:
        suffix_start_index = end_match.end()
        suffix_text = meal_plan_text[suffix_start_index:].strip()


    # --- Check if the text matches the expected meal plan format ---
    # If start marker or end pattern is not found, print original text and skip parsing
    if start_index == -1 or end_match is None:
        return {"error": "Meal plan format invalid or missing markers."}, prefix_text, suffix_text

        pass # Skip the parsing logic below
    else:
        # --- Proceed with Parsing if Format Matches ---
        logger.info("Input text matches expected format. Proceeding with parsing...")
        parsing_successful = True # Set flag as parsing will be attempted

        # Initialize the main data structure
        parsed_data = {
            "meal_plan": {},
            "meals_nutrients": {},
            "total_nutrients": {}
        }

        # Extract Relevant Text Portion (between start and end markers)
        first_line_start = meal_plan_text.rfind('\n', 0, start_index) + 1
        relevant_text = meal_plan_text[first_line_start:end_match.start()]
        # print(f"Successfully extracted text between '{start_marker}' line and Daily Total line.") # Optional debug print

        # Initialize Grand Totals
        grand_total_protein = 0.0
        grand_total_carbs = 0.0
        grand_total_fiber = 0.0
        grand_total_fat = 0.0
        grand_total_calories = 0.0
        grand_total_portion_weight = 0.0
        grand_total_sodium = 0.0
        grand_total_sugar = 0.0
        grand_total_cholesterol = 0.0

        # Parse Daily Total Calories Separately from original text
        daily_total_match = daily_total_cal_re.search(meal_plan_text)
        if daily_total_match:
            try:
                grand_total_calories = float(daily_total_match.group(1))
                # print(f"Successfully parsed Daily Total Calories: {grand_total_calories}") # Optional debug print
            except ValueError:
                return  {"error":"Warning: Could not parse daily total calories value from matched line."}, prefix_text, suffix_text
        else:
            return  {"error":"Warning: Daily Total Calories line pattern not found in original text."}, prefix_text, suffix_text

        # Process Meal Sections from Relevant Text
        sections = relevant_text.strip().split('---')
        


        for section in sections:
            section = section.strip()
            if not section:
                continue

            meal_name_match = meal_name_re.search(section)
            if meal_name_match:
                meal_name = meal_name_match.group(1).strip()

                if meal_name.lower() == "daily total calories:":
                    continue

                # print(f"Processing meal: {meal_name}") # Optional debug print
                parsed_data["meal_plan"][meal_name] = []
                # Initialize meal totals
                meal_total_protein = 0.0
                meal_total_carbs = 0.0
                meal_total_fiber = 0.0
                meal_total_fat = 0.0
                meal_total_calories = 0.0
                meal_total_portion_weight = 0.0
                meal_total_sodium = 0.0
                meal_total_sugar = 0.0
                meal_total_cholesterol = 0.0

                lines = section.split('\n')
                for line in lines:
                    line = line.strip()
                    if line.startswith('-'):
                        parts = line.split('|', 1)
                        if len(parts) == 2:
                            item_name = parts[0][1:].strip()
                            nutrient_text = parts[1].strip()

                            # Extract individual nutrients
                            calories = parse_nutrient_value(nutrient_text, "Calories")
                            protein = parse_nutrient_value(nutrient_text, "Protein")
                            carbs = parse_nutrient_value(nutrient_text, "Carbs")
                            fiber = parse_nutrient_value(nutrient_text, "Fiber")
                            fat = parse_nutrient_value(nutrient_text, "Fat")
                            portion_weight = parse_nutrient_value(nutrient_text, "Portion Weight")
                            sodium = parse_nutrient_value(nutrient_text, "Sodium")
                            sugar = parse_nutrient_value(nutrient_text, "Sugar")
                            cholesterol = parse_nutrient_value(nutrient_text, "Cholesterol")

                            parsed_data["meal_plan"][meal_name].append({"name": item_name,
                                "nutrients": {
                                    "Portion Weight": portion_weight,
                                    "Protein": protein,
                                    "Carbohydrates": carbs,
                                    "Fat": fat,
                                    "Fiber": fiber,
                                    "Sodium": sodium,
                                    "Sugar": sugar,
                                    "Cholesterol": cholesterol,
                                    "Calories": calories
                                }
                            })

                            # Accumulate meal totals
                            meal_total_protein += protein
                            meal_total_carbs += carbs
                            meal_total_fiber += fiber
                            meal_total_fat += fat
                            meal_total_portion_weight += portion_weight
                            meal_total_sodium += sodium
                            meal_total_sugar += sugar
                            meal_total_cholesterol += cholesterol
                        # else: # Removed warning for malformed line as it might catch meal titles incorrectly
                        #    print(f"Warning: Skipping malformed item line: {line}")

                    
                    total_cal_match = meal_total_cal_re.search(line)
                    if total_cal_match:
                        try:
                            if total_cal_match.group(1).strip().lower() == meal_name.lower():
                                meal_total_calories = float(total_cal_match.group(2))
                                # parsed_data["meal_plan"][meal_name].append({"name": f"Calories for {meal_name}","nutrients": {"Calories": meal_total_calories}})
                            # else: # Removed warning for mismatched total line name
                            #    print(f"Warning: Mismatched meal name in total calories line: '{total_cal_match.group(1).strip()}' vs '{meal_name}'")
                        except ValueError:
                            return  {"error":f"Warning: Could not parse total calories from line: {line}"} , prefix_text, suffix_text

                # Store calculated/extracted meal totals
                if meal_total_calories == 0.0 and not any(item for item in parsed_data["meal_plan"][meal_name] if item.get("name","").startswith("Calories for")):
                    return  {"error":f"Warning: Total calories line not found or parsed correctly for meal: {meal_name}. Meal total calories might be inaccurate."}, prefix_text, suffix_text

                parsed_data["meals_nutrients"][meal_name] = {
                    "Calories": meal_total_calories,
                    "Portion Weight": round(meal_total_portion_weight, 1),
                    "Protein": round(meal_total_protein, 1),
                    "Carbohydrates": round(meal_total_carbs, 1),
                    "Fat": round(meal_total_fat, 1),
                    "Fiber": round(meal_total_fiber, 1),
                    "Sodium": round(meal_total_sodium, 1),
                    "Sugar": round(meal_total_sugar, 1),
                    "Cholesterol": round(meal_total_cholesterol, 1)
                }

                # Accumulate grand totals
                grand_total_protein += meal_total_protein
                grand_total_carbs += meal_total_carbs
                grand_total_fiber += meal_total_fiber
                grand_total_fat += meal_total_fat
                grand_total_portion_weight += meal_total_portion_weight
                grand_total_sodium += meal_total_sodium
                grand_total_sugar += meal_total_sugar
                grand_total_cholesterol += meal_total_cholesterol

        # Finalize and Output JSON
        parsed_data["total_nutrients"] = {
            "Calories": grand_total_calories,
            "Portion Weight": round(grand_total_portion_weight, 1),
            "Protein": round(grand_total_protein, 1),
            "Carbohydrates": round(grand_total_carbs, 1),
            "Fat": round(grand_total_fat, 1),
            "Fiber": round(grand_total_fiber, 1),
            "Sodium": round(grand_total_sodium, 1),
            "Sugar": round(grand_total_sugar, 1),
            "Cholesterol": round(grand_total_cholesterol, 1)
        }

        final_output_structure = [parsed_data]
        json_output = json.dumps(final_output_structure, indent=2)



    # --- Print Prefix and Suffix Text ---
    logger.info("\n--- Prefix Text ---")
    if prefix_text:
        logger.info(prefix_text)
    else:
        # Only print this if parsing wasn't attempted (i.e., format didn't match)
        if not parsing_successful:
            prefix_text="No text found before start marker or format mismatch"
        else: # If parsing happened but prefix was empty
            prefix_text="No text found before start marker"


    logger.info("\n--- Suffix Text ---")
    if suffix_text:
        logger.info(suffix_text)
    else:
        # Only logger.info this if parsing wasn't attempted (i.e., format didn't match)
        if not parsing_successful:
            suffix_text="No text found after end pattern or format mismatch"
        else: # If parsing happened but suffix was empty
            suffix_text="No text found after end pattern"
    return json_output, prefix_text, suffix_text

def transcribe_speech(audio_data: bytes) -> Optional[str]:
    try:
        audio_file = io.BytesIO(audio_data)
        files = {"file": ("recording.wav", audio_file, "audio/wav")}

        # response = requests.post(WHISPER_URL, headers=WHISPER_HEADERS, files=files, timeout=30)
        response = requests.post(WHISPER_URL, files=files, timeout=360)

        response.raise_for_status()

        response_data = response.json()
        transcribed_text = response_data.get("transcribed_text", "").strip()
        return transcribed_text or None

    except requests.exceptions.RequestException as e:
        logger.error(f"Whisper API error: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected Whisper error: {str(e)}")
    return None


def get_rotating_tips(table_name: str, vital_sign_col: str = "Vital Sign") -> JSONResponse:
    """
    Fetches 3 rotating daily tips from the specified table.
    Rotates through all records present in the table.
    Returns standardized JSONResponse.
    """
    index = get_today_index()
    tips_per_day = 3

    try:
        conn = get_db_connection_dynamic(MASTER_TABLE_DB_NAME)
        cursor = conn.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM [{MASTER_TABLE_DB_NAME}].[dbo].[{table_name}]")
        total_records = cursor.fetchone()[0]

        if total_records == 0:
            return JSONResponse(
                status_code=400,
                content={
                    "status": 400,
                    "message": "No tips found in the table.",
                    "data": {"tips": []}
                }
            )

        effective_index = ((index - 1) % (total_records // tips_per_day)) + 1
        start_id = (effective_index - 1) * tips_per_day + 1
        end_id = start_id + tips_per_day - 1

        query = f"""
            SELECT [{vital_sign_col}], [Tip], [Explanation], [Actions]
            FROM {MASTER_TABLE_DB_NAME}.[dbo].[{table_name}]
            WHERE ID BETWEEN ? AND ?
        """
        cursor.execute(query, (start_id, end_id))
        rows = cursor.fetchall()
        conn.close()

        tips = [
            {
                "vital_sign": row[0],
                "tip": row[1],
                "explanation": row[2],
                "actions": row[3]
            }
            for row in rows
        ]

        if tips:
            return JSONResponse(
                status_code=200,
                content={
                    "status": 200,
                    "message": "Successfully fetched the data",
                    "data": {"tips": tips}
                }
            )
        else:
            return JSONResponse(
                status_code=400,
                content={
                    "status": 400,
                    "message": "Failed to fetch data",
                    "data": {"tips": []}
                }
            )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "status": 500,
                "message": f"Internal server error: {e}",
                "data": {"tips": []}
            }
        )

def get_mistral_analysis(image_base64):
    """
    Sends the image to Mistral API for analysis to identify food items and estimate quantities.
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {MISTRAL_API_KEY}"
    }
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
                {"type": "text", "text": """Analyze the meal in this image.
                1. Identify all distinct food items.
                2. For each identified item:
                - Give its portion size in layman's terms (e.g., 'one cup', 'half plate', 'two spoons', 'one slice').
                - Estimate its weight in grams (or ml for liquids).               
                3. List each food item and its estimated quantity on a new line, like "Food Item: Quantity (Unit)".
                4. Do NOT include any introductory or summary sentences like "Identify all distinct food items" or "Estimate the quantity for each identified item". Just the list.
                Output in this exact format (one item per line):
                Food Item (Portion size) – Weight Unit
                Example:
                Pasta (one cup) – 150g
                Tea (one cup) – 200ml
                """}
            ]
        }
    ]

    payload = {
        "model": MISTRAL_MODEL,
        "messages": messages
    }

    try:
        response = requests.post(MISTRAL_API_ENDPOINT, headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()
        return result.get("choices", [{}])[0].get("message", {}).get("content", "")
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Mistral API error: {str(e)}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Error decoding Mistral API response.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")

# --- FUNCTION TO EXTRACT FOOD DATA FROM MISTRAL ANALYSIS ---
def extract_food_data_from_mistral_analysis(analysis_text):
    """
    Parses the text output from Mistral's vision analysis to extract food items
    and their estimated quantities.
    """
    food_items = []
    # Regex to find lines like "Food Item: Quantity (Unit)" or "Food Item: QuantityUnit"
    food_item_pattern = re.compile(
        # Added |oz|ml to the unit group below
        r"^(.*?)\s*\|\s*Portion Weight:\s*([\d.]+)\s*(lbs|fl\s?oz|oz|g|ml)\s*\|\s*Protein:\s*([\d.]+)g\s*\|\s*Carbs:\s*([\d.]+)g\s*\|\s*Fat:\s*([\d.]+)g\s*\|\s*Fiber:\s*([\d.]+)g\s*\|\s*Sodium:\s*([\d.]+)mg\s*\|(?:\s*Iodine:\s*([\d.]+)mcg\s*\|)?\s*Sugar:\s*([\d.]+)g\s*\|\s*Cholesterol:\s*([\d.]+)mg\s*\|\s*Calories:\s*([\d.]+)kcal",
        re.IGNORECASE
    )

    lines = analysis_text.strip().split('\n')
    for line in lines:
        match = food_item_pattern.match(line)
        if match:
            food_name = match.group(1).strip()
            portion_desc = match.group(2).strip()  # e.g., "one cup"
            quantity_str = match.group(3).strip()
            unit = match.group(4).strip().lower()

            try:
                quantity_num = float(quantity_str)
            except ValueError:
                quantity_num = 0.0

            food_items.append({
                'name': food_name,
                'portion_desc': portion_desc,  # New field
                'original_quantity_str': f"{quantity_str}{unit}",
                'quantity_value': quantity_num,
                'quantity_unit': unit
            })
        else:
            # Fallback for lines that don't fit the strict pattern, but might still be food names
            # This handles cases like "Coffee: 1 cup (240ml)" where 'Coffee' is the item, and we want to try looking it up.
            # Avoids processing general descriptive sentences from Mistral's previous verbose outputs.
            # Heuristic to filter out general descriptive sentences from Mistral.
            if ':' in line:
                potential_food_name = line.split(':', 1)[0].strip()
                # If the line starts with a verb or generic descriptive phrase, skip it.
                if any(potential_food_name.lower().startswith(phrase) for phrase in ["identify", "estimate", "list", "break down", "to analyze"]):
                    continue
                food_items.append({
                    'name': potential_food_name,
                    'original_quantity_str': line.split(':', 1)[-1].strip(),
                    'quantity_value': 0.0,
                    'quantity_unit': ''
                })
    return food_items

def get_mistral_nutritional_breakdown(food_items_list):
    """
    Sends a list of identified food items and their quantities to Mistral API
    to get a detailed nutritional breakdown for each item.
    Requests a structured JSON response.
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {MISTRAL_API_KEY}"
    }

    food_list_str = "\n".join([
        f"- {item['name']} ({item.get('portion_desc', 'standard serving')}): {item['original_quantity_str']}"
        for item in food_items_list
    ])

    prompt_text = f"""As a highly accurate nutritional AI, analyze the following food items and their estimated quantities.
    Provide a detailed nutritional breakdown for each item, including The portion description exactly as provided (in a field called "portion_desc"), Calories (kcal), Protein (g), Fat (g), Carbohydrates (g), Fiber (g), Sodium (mg), Sugar (g), Saturated Fat (g), and Cholesterol (mg).
    If a quantity is not specified or is unclear, assume a standard serving size for that item.
    Return the data as a JSON array of objects, where each object represents a food item and its nutrients.

    Food Item:
    {food_list_str}

    Example JSON Structure:
    [
        {{
            "item_name": "Pasta (Rigatoni)",
            "portion_desc": "one cup"
            "quantity": "150g",
            "nutrients": {{
                "Calories": 200.5,
                "Protein": 8.2,
                "Fat": 1.5,
                "Carbohydrates": 40.1,
                "Fiber": 2.5,
                "Sodium": 10.0,
                "Iodine": 0.2,
                "Sugar": 1.2,
                "Saturated Fat": 0.3,
                "Cholesterol": 0.0
            }}
        }},
        {{
            "item_name": "Tomato Sauce",
            "portion_description": "one bowl"
            "quantity": "150g",
            "nutrients": {{
                "Calories": 60.0,
                "Protein": 2.0,
                "Fat": 3.0,
                "Carbohydrates": 8.0,
                "Fiber": 2.0,
                "Sodium": 300.0,
                "Iodine": 0.2,
                "Sugar": 4.0,
                "Saturated Fat": 0.5,
                "Cholesterol": 0.0
            }}
        }}
    ]
    """

    messages = [
        {"role": "user", "content": prompt_text}
    ]

    payload = {
        "model": MISTRAL_MODEL,
        "messages": messages,
        "response_format": {"type": "json_object"} # Request JSON output
    }

    try:
        response = requests.post(MISTRAL_API_ENDPOINT, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Mistral nutritional API error: {str(e)}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Error decoding nutritional breakdown response.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected nutritional breakdown error: {str(e)}")

def get_mistral_nutritional_assessment(total_nutrients):
    """
    Sends total nutritional data to Mistral API for qualitative analysis.
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {MISTRAL_API_KEY}"
    }

    prompt_text = f"""You are a certified nutrition expert. Based on the following total nutritional values, provide a clear and decisive qualitative assessment of the meal’s overall healthiness.

        Nutritional Breakdown:
        - Calories: {total_nutrients.get('Total Calories (kcal)', 0):.2f} kcal
        - Protein: {total_nutrients.get('Total Protein (g)', 0):.2f} g
        - Fat: {total_nutrients.get('Total Fat (g)', 0):.2f} g
        - Carbohydrates: {total_nutrients.get('Total Carbs (g)', 0):.2f} g
        - Fiber: {total_nutrients.get('Total Fiber (g)', 0):.2f} g
        - Sodium: {total_nutrients.get('Total Sodium (mg)', 0):.2f} mg
        - Iodine: {total_nutrients.get('Total Iodine (mcg)', 0):.2f} mcg
        - Saturated Fat: {total_nutrients.get('Total Saturated Fat (g)', 0):.2f} g
        - Sugar: {total_nutrients.get('Total Sugar (g)', 0):.2f} g
        - Cholesterol: {total_nutrients.get('Total Cholesterol (mg)', 0):.2f} mg

        Make a **strong and unambiguous judgment**: is this meal generally **Healthy** or **Unhealthy**? Avoid neutral or vague labels like "moderately healthy" unless absolutely necessary and clearly justified.

        Evaluate the meal considering:
        - Caloric density
        - Protein sufficiency
        - Excessive fat or saturated fat
        - High/Low sodium content
        - Adequate fiber

        Then, provide **exactly two short, specific reasons** that justify your assessment.

        Respond in this format:
        Assessment: [Healthy / Unhealthy]
        Reasons:
        - [Reason 1]
        - [Reason 2]
        """

    messages = [
        {"role": "user", "content": prompt_text}
    ]

    payload = {
        "model": MISTRAL_MODEL,
        "messages": messages
    }

    try:
        response = requests.post(MISTRAL_API_ENDPOINT, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Mistral assessment API error: {str(e)}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Error decoding assessment response.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected assessment error: {str(e)}")

def constraints_match(profile1: dict, profile2: dict) -> bool:
    """
    Compare two profile dictionaries to see if they match for meal plan generation.
    Focuses on key fields that would affect meal planning.
    """
    key_fields = [
        'age', 'gender', 'weight_kg', 'height_cm', 'activity_level',
        'dietary_preference', 'restrictions', 'digestive_issues', 
        'allergies', 'symptom_aggravating_foods', 'medical_conditions'
    ]
    
    for field in key_fields:
        val1 = profile1.get(field)
        val2 = profile2.get(field)
        
        # Handle dietary_preference special case (can be string or list)
        if field == 'dietary_preference':
            val1_norm = val1 if isinstance(val1, list) else [val1] if val1 else []
            val2_norm = val2 if isinstance(val2, list) else [val2] if val2 else []
            if set(val1_norm) != set(val2_norm):
                return False
            continue
        
        # Convert lists to sets for comparison (order doesn't matter)
        if isinstance(val1, list) and isinstance(val2, list):
            if set(val1) != set(val2):
                return False
        elif isinstance(val1, list) or isinstance(val2, list):
            # One is list, other is not - they don't match
            return False
        elif val1 != val2:
            return False
    
    # Check vitals if present - handle different blood pressure formats
    vitals1 = profile1.get('vitals_numeric', {})
    vitals2 = profile2.get('vitals_numeric', {})
    
    # Normalize vitals for comparison
    vitals1_norm = normalize_vitals(vitals1)
    vitals2_norm = normalize_vitals(vitals2)
    
    if vitals1_norm != vitals2_norm:
        return False
    
    return True

def normalize_vitals(vitals: dict) -> dict:
    """
    Normalize vitals to have consistent format for comparison.
    """
    if not vitals:
        return {}
    
    normalized = {}
    
    for key, value in vitals.items():
        if key == 'Blood Pressure':
            if isinstance(value, dict) and 'systolic' in value and 'diastolic' in value:
                # Convert dict format to string format
                normalized[key] = f"{value['systolic']}/{value['diastolic']}"
            elif isinstance(value, str):
                # Already in string format
                normalized[key] = value
            else:
                normalized[key] = value
        else:
            normalized[key] = value
    
    return normalized


failed_indicators = [
                "something went wrong",
                "please try again later",
                
                "i'm still learning how to handle questions like this",
                "still learning how to handle",
                "i don't understand",
                "i can't help with that",
                "can't help with",
                "unable to process",
                "error occurred",
                "try again",
                "apologize",
                "sorry, i",
                "i'm not sure",
                "not sure how to",
                "don't know how to",
                "having trouble",
                "difficulty understanding",
                "unclear about",
                "not clear on",
                "i'm afraid i",
                "unfortunately, i",
                "service unavailable",
                "temporarily unavailable",
                "system error",
                "technical issue",
                "unable to respond"
            ]

def is_failed_response(answer):
    if not answer:
        return False
                
    # Convert answer to lowercase for case-insensitive comparison
    answer_lower = answer.lower().strip()
    
    failed_indicators = [
        "something went wrong, please try again later",
        "i'am sorry",
        "I'm sorry",
        "something went wrong",
        "please try again later",
        "i'm still learning how to handle questions like this",
        "still learning how to handle",
        "i don't understand",
        "i can't help with that",
        "can't help with",
        "unable to process",
        "error occurred",
        "try again",
        "apologize",
        "sorry, i",
        "i'm not sure",
        "not sure how to",
        "don't know how to",
        "having trouble",
        "difficulty understanding",
        "unclear about",
        "not clear on",
        "i'm afraid i",
        "unfortunately, i",
        "service unavailable",
        "temporarily unavailable",
        "system error",
        "technical issue",
        "unable to respond"
    ]
    
    # Check for partial matches (case-insensitive)
    return any(indicator.lower() in answer_lower for indicator in failed_indicators)

# Function to log failed queries
def log_failed_query(user_id, query, ai_response, database_name):
    """Log failed AI response safely with its own connection."""
    try:
        conn_temp = get_db_connection_dynamic(database_name)
        cursor = conn_temp.cursor()
        cursor.execute("""
            INSERT INTO [dbo].[Failed_queries_test]
            ([User_Id], [User_query], [AI Response], [Query_date], [Is_resolved])
            VALUES (?, ?, ?, GETDATE(), 0)
        """, (str(user_id), query, ai_response))
        conn_temp.commit()
    except Exception as log_error:
        logger.error(f"Failed to log failed query: {log_error}")
    finally:
        try: cursor.close()
        except: pass
        try: conn_temp.close()
        except: pass

def get_time_based_workout_tip(cursor, table_name, today_index):
    cursor.execute(f"""
        SELECT [Workout_Timing], [Tip_Title], [Explanation], [Action], [Id]
        FROM {table_name}
        WHERE [Workout_Timing] = ?
        ORDER BY [Id]
    """, "06:30:00")
    #   current_time.strftime("%H:%M:%S"))

    rows = cursor.fetchall()
    if not rows:
        return None

    # Rotate tip for today based on index
    tip_index = (today_index - 1) % len(rows)
    tip_row = rows[tip_index]

    return {
        "workout_timing": tip_row[0],
        "tip_title": tip_row[1],
        "explanation": tip_row[2],
        "action": tip_row[3],
        "id": tip_row[4],
    }
  
NOTIFICATION_TYPES = {
    1: "fitnesstips",
    2: "mealreminder",
    3: "eventreminder",
    4: "workouttips"
}

FITNESS_AI_DB = 'FitnessAi'
def fetch_workout_tip_for_time(table_name: str, local_time: str):
    """
    Fetch random workout tip for given time from condition-specific table.
    """
    conn_ai = get_db_connection_dynamic(FITNESS_AI_DB)
    cursor_ai = conn_ai.cursor()

    cursor_ai.execute(f"""
        SELECT Workout_Timing, Tip_Title, Action
        FROM {table_name}
        WHERE FORMAT(Workout_Timing, 'HH:mm:ss') = ?
    """, local_time)

    tips = cursor_ai.fetchall()

    cursor_ai.close()
    conn_ai.close()

    if not tips:
        return None

    tip = random.choice(tips)  # pick random
    return {
        "Workout_Timing": tip[0],
        "Tip_Title": tip[1],
        "Action": tip[2]
    }

MEAL_REMINDERS = {
    "07:30:00": {
        "title": "Breakfast",
        "text": "It is time for your breakfast. Start your day with a healthy meal. Tap here to log your breakfast.",
        "for_ai_log": "Hi I just got my breakfast reminder. Can you help me log what I ate?"
    },
    "10:00:00": {
        "title": "Mid-Morning Snack Reminder",
        "text": "It's time for your mid-morning snack. A small nutritious snack keeps your energy levels stable. Tap log your snack.",
        "for_ai_log": "Hi I got my mid-morning snack reminder. Can you help me log my snack?"
    },
    "12:00:00": {
        "title": "Lunch Reminder",
        "text": "Time for lunch. A balanced meal now will keep you active through the afternoon. Tap to log your meal.",
        "for_ai_log": "Hi I just got my lunch reminder. Can you help me log what I ate?"
    },
    "16:40:00": {
        "title": "Afternoon Snack Reminder",
        "text": "It's time for your afternoon snack. Choose something light and healthy to keep you going. Tap to log your snack.",
        "for_ai_log": "Hi I received my afternoon snack reminder. Can you help me log what I had?"
    },
    "18:30:00": {
        "title": "Dinner Reminder",
        "text": "It is dinner time. End your day with a light and nutritious meal. Tap to log your meal.",
        "for_ai_log": "Hi I got my dinner reminder. Can you help me log what I ate?"
    }
}

# simple in-memory cache
NOTIFICATION_TYPES_CACHE = {
    "data": None,
    "timestamp": 0
}
CACHE_TTL = 300  

def get_notification_types(conn):
    """
    Fetch and cache Notification_Types from MASTER_TABLE_DB_NAME.
    """
    now = time.time()
    if NOTIFICATION_TYPES_CACHE["data"] and (now - NOTIFICATION_TYPES_CACHE["timestamp"] < CACHE_TTL):
        return NOTIFICATION_TYPES_CACHE["data"]

    cursor = conn.cursor()
    cursor.execute("""
        SELECT Id, Name, Icon
        FROM {MASTER_TABLE_DB_NAME}.[dbo].[Notification_Types]
    """)
    rows = cursor.fetchall()
    cursor.close()

    type_map = {row.Id: {"id": row.Id, "name": row.Name, "icon": row.Icon} for row in rows}
    NOTIFICATION_TYPES_CACHE["data"] = type_map
    NOTIFICATION_TYPES_CACHE["timestamp"] = now
    return type_map


def log_notification(user_id, title, body, notification_type, data, database_name):
    conn = get_db_connection_dynamic(database_name)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO NotificationLogs 
        (user_id, title, body, notification_type, data, is_read, created_at)
        VALUES (?, ?, ?, ?, ?, 0, GETDATE())
    """, (
        user_id,
        title,
        body,
        notification_type,      # INTEGER ID
        json.dumps(data)        # JSON string
    ))

    conn.commit()
    cursor.close()
    conn.close()


# For Exercise video SAS URL    
def generate_sas_url(blob_path: str, years: int = 10) -> str:
    """
    Generates a long-lived SAS URL for a specific blob path.
    """
    try:

        clean_path = blob_path.replace("\\", "/").strip("/")
        if clean_path.lower().startswith(f"{AZURE_EXERCISE_CONTAINER_NAME.lower()}/"):
            clean_path = clean_path[len(AZURE_EXERCISE_CONTAINER_NAME) + 1:]

        expiry = datetime.now(timezone.utc) + timedelta(days=365 * years)

        sas_token = generate_blob_sas(
            account_name=AZURE_EXERCISE_ACCOUNT_NAME,
            container_name=AZURE_EXERCISE_CONTAINER_NAME,
            blob_name=clean_path,
            account_key=AZURE_EXERCISE_ACCOUNT_KEY,
            permission=BlobSasPermissions(read=True),
            expiry=expiry
        )
        
        return f"https://{AZURE_EXERCISE_ACCOUNT_NAME}.blob.core.windows.net/{AZURE_EXERCISE_CONTAINER_NAME}/{quote(clean_path)}?{sas_token}"
    except Exception as e:
        print(f"Error generating SAS for {blob_path}: {e}")
        return None

# For Exercise video SAS URL Expiry    
def is_exercise_sas_expired(sas_url: str) -> bool:
    """
    Robust check for Azure SAS token expiry.
    Returns True if expired, invalid, or unparseable.
    """
    if not sas_url:
        return True

    try:
        parsed = urllib.parse.urlparse(sas_url)
        query_params = urllib.parse.parse_qs(parsed.query)
        se_list = query_params.get("se")
        
        if not se_list:
            return True 
        
        se_str = se_list[0]

        try:
            expiry_date = datetime.strptime(se_str, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            expiry_date = datetime.fromisoformat(se_str.replace("Z", "+00:00"))

        expiry_date = expiry_date.replace(tzinfo=timezone.utc)

        if expiry_date < datetime.now(timezone.utc) + timedelta(minutes=5):
            return True 

        return False 

    except Exception as e:
        print(f"[SAS CHECK ERROR] Could not parse SAS: {e}")
        return True
    
# ✅ Helper: Generate SAS URL valid for up to 10 years
def generate_long_sas_url(blob_path: str, validity_days: int = 3650) -> str:
    try:
        blob_client = container_client.get_blob_client(blob_path)
        sas_token = generate_blob_sas(
            account_name=blob_client.account_name,
            container_name=blob_client.container_name,
            blob_name=blob_client.blob_name,
            account_key=blob_service_client.credential.account_key,
            permission=BlobSasPermissions(read=True),
            expiry=datetime.utcnow() + timedelta(days=validity_days),
        )
        return f"{blob_client.url}?{sas_token}"
    except Exception as e:
        print(f"❌ SAS generation failed for {blob_path}: {e}")
        return None

def normalize_condition(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

from difflib import SequenceMatcher

def fuzzy_match_condition(
    user_condition: str,
    known_conditions: dict
):
    user_norm = normalize_condition(user_condition)

    best_match = None
    best_score = 0
    for key in known_conditions.keys():
        key_norm = normalize_condition(key)
        score = int(
            SequenceMatcher(None, user_norm, key_norm).ratio() * 100
        )
        if score > best_score:
            best_score = score
            best_match = key
    return best_match

def is_sas_expired(sas_url: str) -> bool:
    """
    Returns True if the given SAS URL is expired (or cannot be parsed).
    Returns False if SAS expiry parsed and is in the future.
    Conservative: if parsing fails, returns True (treat as expired).
    """
    if not sas_url:
        return True

    try:
        # Extract query params safely (handles full URL or just token)
        parsed = urlparse(sas_url)
        qs = parsed.query if parsed.query else sas_url  # some callers might pass only query string
        params = parse_qs(qs)
        se_values = params.get("se") or params.get("SE") or params.get("Se")
        if not se_values:
            # Try to find 'se=' anywhere (fallback)
            if "se=" not in sas_url.lower():
                return True
            # attempt crude extraction
            # fallthrough to manual decode below
            raw = sas_url
        else:
            raw = se_values[0]

        # decode any URL-encoding
        raw = unquote_plus(raw)

        # Normalize: replace trailing 'Z' with '+00:00' so fromisoformat can parse it
        # Also ensure fractional seconds are <= 6 digits for Python
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"

        # If timezone is like +0000 (no colon), insert colon -> +00:00
        # fromisoformat expects +HH:MM or +HH:MM:SS
        # handle +HHMM or -HHMM forms:
        if (len(raw) >= 5 and (raw[-5] in ['+', '-']) and raw[-2] != ':'):
            # e.g. ...2019-01-01T00:00:00+0530 -> convert to +05:30
            tz_part = raw[-5:]
            raw = raw[:-5] + tz_part[:-2] + ":" + tz_part[-2:]

        # Truncate fractional seconds to max 6 digits if present
        if "." in raw:
            # split at timezone start (+/-)
            tz_index = max(raw.rfind("+"), raw.rfind("-"))
            if tz_index > 0:
                main = raw[:tz_index]
                tz = raw[tz_index:]
            else:
                main = raw
                tz = ""
            if "." in main:
                prefix, frac = main.split(".", 1)
                # keep only digits part of fractional seconds
                frac_digits = "".join(ch for ch in frac if ch.isdigit())
                frac_digits = frac_digits[:6].ljust(1, "0")  # ensure at least one digit if weird
                main = f"{prefix}.{frac_digits}"
            raw = main + tz

        # Parse
        dt = datetime.fromisoformat(raw)  # returns offset-aware if tz present
        # normalize to UTC
        if dt.tzinfo is None:
            # assume UTC if no tz (conservative)
            dt = dt.replace(tzinfo=timezone.utc)
        dt_utc = dt.astimezone(timezone.utc)

        return datetime.now(timezone.utc) > dt_utc

    except Exception:
        # On any parse error: treat as expired so we regenerate SAS (conservative)
        return True

def get_user_profile(user_id: str, database_name: str):
    conn_dynamic = get_db_connection_dynamic(database_name)
    cursor_dynamic = conn_dynamic.cursor()

    # Resolve PatientId
    # cursor_dynamic.execute("SELECT TOP 1 PatientId FROM dbo.Patient WHERE PatientUserId = ?", user_id)
    # row = cursor_dynamic.fetchone()

    # --- Profile Queries ---
    cursor_dynamic.execute("EXEC [dbo].[usp_GetPatientDietaryProfileBySearch] @UserId = ?", user_id)
    profile_row1 = cursor_dynamic.fetchone()
    profile_columns1 = [col[0] for col in cursor_dynamic.description] if profile_row1 else []
    profile_dict1 = dict(zip(profile_columns1, profile_row1)) if profile_row1 else {}

    
    cursor_dynamic.execute("EXEC [dbo].[sp_GetPatinetInfoByUserId] @PatientId = ?", user_id)
    profile_row2 = cursor_dynamic.fetchone()
    profile_columns2 = [col[0] for col in cursor_dynamic.description] if profile_row2 else []
    profile_dict2 = dict(zip(profile_columns2, profile_row2)) if profile_row2 else {}
    
    
    merged_profile = {**profile_dict2, **profile_dict1}
    
    # --- Fetch Patient Goals ---
    cursor_dynamic.execute(f"""
        SELECT 
            pg.PatientGoalCode, pg.GoalCode, gm.GoalName, gm.GoalDescription, 
            gtm.GoalTypeName, gcm.CategoryName, gsm.StatusName,
            pg.TargetValue, pg.CurrentValue, pg.ProgressPercent,
            pg.StartDate, pg.TargetDate
        FROM {database_name}.[dbo].[PatientGoals] pg
        LEFT JOIN [{MASTER_TABLE_DB_NAME}].[Master].[GoalMaster] gm ON pg.GoalCode = gm.GoalCode
        LEFT JOIN [{MASTER_TABLE_DB_NAME}].[Master].[GoalTypeMaster] gtm ON gm.GoalTypeCode = gtm.GoalTypeCode
        LEFT JOIN [{MASTER_TABLE_DB_NAME}].[Master].[GoalCategoryMaster] gcm ON pg.CategoryCode = gcm.CategoryCode
        LEFT JOIN [{MASTER_TABLE_DB_NAME}].[Master].[GoalStatusMaster] gsm ON pg.StatusCode = gsm.StatusCode
        WHERE pg.PatientId = ?
        ORDER BY pg.CreatedDate DESC
    """, user_id)

    patient_goals = []
    
    now = datetime.now(timezone.utc)
    vitals_avg = averagevitals(fetch_vitals(user_id, cursor_dynamic, (now - timedelta(days=1)).strftime("%Y-%m-%d")))
    diagnoses = []
    diagnoses = get_patient_active_diagnoses(user_id, database_name)

    # --- Convert final profile ---
    profile = convert_json_to_profile(merged_profile, vitals_json=vitals_avg, diagnoses=diagnoses)
    profile["goals"] = patient_goals  # ✅ Add the new goals section

    return profile

def get_previous_day_meal_plan_text(previous_date_obj: datetime.date, database_name, user_id):
    """Fetch and format the previous day's meal plan as text"""
    try:
        conn = get_db_connection_dynamic(database_name)
        cursor = conn.cursor()
 
        cursor.execute("""
            SELECT 
                MealType, FoodItems, PortionWeight, PortionWeightUnit,
                Calories, Protein, Carbohydrates, Fat, 
                Fiber, Sodium, Iodine, Sugar, Cholesterol
            FROM dbo.MealPlanDetails
            WHERE PatientId = ? AND MealDate = ?
            ORDER BY MealType, MealPlanId
        """, (user_id, previous_date_obj))
 
        meals = cursor.fetchall()
        columns = [col[0] for col in cursor.description]
 
        cursor.close()
        conn.close()
 
        if not meals:
            return None
 
        meal_plan_text = f"Meal Plan for date ({previous_date_obj:%Y-%m-%d}):\n"
        current_meal_type = None
 
        for row in meals:
            meal = dict(zip(columns, row))
 
            if meal["MealType"] != current_meal_type:
                current_meal_type = meal["MealType"]
                meal_plan_text += f"\n**{current_meal_type}**\n"
 
            portion_weight = meal["PortionWeight"] or 0
            portion_unit = meal["PortionWeightUnit"] or ""
 
            meal_plan_text += (
                f"- {meal['FoodItems']} | "
                f"Portion Weight: {portion_weight}{portion_unit} | "
                f"Protein: {meal['Protein']}g | "
                f"Carbs: {meal['Carbohydrates']}g | "
                f"Fat: {meal['Fat']}g | "
                f"Fiber: {meal['Fiber']}g | "
                f"Sodium: {meal['Sodium']}mg | "
                f"Iodine: {meal['Iodine']}mcg | "
                f"Sugar: {meal['Sugar']}g | "
                f"Cholesterol: {meal['Cholesterol']}mg | "
                f"Calories: {meal['Calories']}kcal\n"
            )
 
        return meal_plan_text.strip()
 
    except Exception as e:
        logger.error(f"Error fetching previous day meal plan: {e}")
        return None
def get_recent_meal_plans_history(database_name: str, user_id: str, days_back: int = 14) -> str:
    """
    Fetch recent meal plans from the last N days to avoid repetition.
    Returns a formatted string with all unique food items from recent meal plans plus category analysis.
    """
    try:
        conn = get_db_connection_dynamic(database_name)
        cursor = conn.cursor()
        
        # Calculate date range
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=days_back)
        
        cursor.execute("""
            SELECT DISTINCT
                MealDate, MealType, FoodItems
            FROM dbo.MealPlanDetails
            WHERE PatientId = ? 
                AND MealDate >= ? 
                AND MealDate < ?
            ORDER BY MealDate DESC, MealType
        """, (user_id, start_date, end_date))
        
        meals = cursor.fetchall()
        cursor.close()
        conn.close()
        
        if not meals:
            return None
        
        # Group by date
        meal_history = {}
        all_foods_used = set()
        
        # Track food categories for pattern detection
        protein_keywords = ['chicken', 'turkey', 'fish', 'tofu', 'paneer', 'egg', 'lentil', 'dal', 'chickpea', 'rajma', 'meat', 'lamb', 'beef', 'pork']
        grain_keywords = ['rice', 'roti', 'chapati', 'bread', 'quinoa', 'oat', 'pasta', 'noodle', 'paratha', 'dosa', 'idli', 'upma', 'poha', 'millet', 'bajra', 'ragi']
        
        proteins_used = set()
        grains_used = set()
        
        for meal_date, meal_type, food_items in meals:
            date_str = meal_date.strftime("%Y-%m-%d")
            if date_str not in meal_history:
                meal_history[date_str] = {}
            if meal_type not in meal_history[date_str]:
                meal_history[date_str][meal_type] = []
            meal_history[date_str][meal_type].append(food_items)
            
            all_foods_used.add(food_items.lower())
            
            # Detect protein and grain categories
            food_lower = food_items.lower()
            for protein in protein_keywords:
                if protein in food_lower:
                    proteins_used.add(protein.capitalize())
            for grain in grain_keywords:
                if grain in food_lower:
                    grains_used.add(grain.capitalize())
        
        # Format output with category analysis
        history_text = f"Recent Meal Plans (Last {days_back} Days - DO NOT REPEAT THESE):\n\n"
        history_text += "=" * 70 + "\n"
        history_text += "CATEGORY ANALYSIS (Proteins & Grains Recently Used):\n"
        history_text += "=" * 70 + "\n"
        
        if proteins_used:
            history_text += f"PROTEINS USED: {', '.join(sorted(proteins_used))}\n"
            history_text += ">> YOU MUST USE DIFFERENT PROTEINS NOT IN THIS LIST <<\n\n"
        
        if grains_used:
            history_text += f"GRAINS USED: {', '.join(sorted(grains_used))}\n"
            history_text += ">> YOU MUST USE DIFFERENT GRAINS NOT IN THIS LIST <<\n\n"
        
        history_text += "=" * 70 + "\n"
        history_text += "DETAILED MEAL HISTORY BY DATE:\n"
        history_text += "=" * 70 + "\n\n"
        
        for date_str in sorted(meal_history.keys(), reverse=True):
            history_text += f"📅 Date: {date_str}\n"
            for meal_type in ["Breakfast", "MorningSnack", "Lunch", "EveningSnack", "Dinner"]:
                if meal_type in meal_history[date_str]:
                    foods = meal_history[date_str][meal_type]
                    history_text += f"  • {meal_type}: {', '.join(foods)}\n"
            history_text += "\n"
        
        history_text += "=" * 70 + "\n"
        history_text += f"TOTAL UNIQUE FOODS TO AVOID: {len(all_foods_used)}\n"
        history_text += "=" * 70 + "\n"
        
        return history_text.strip()
        
    except Exception as e:
        logger.error(f"Error fetching recent meal plans history: {e}")
        return None

def fetch_profile_data(database_name, user_id):
        conn = get_db_connection_dynamic(database_name)
        cursor = conn.cursor()

        cursor.execute("EXEC [dbo].[usp_GetPatientDietaryProfileBySearch] @UserId = ?", user_id)
        profile1 = dict(zip([col[0] for col in cursor.description], cursor.fetchone() or []))

        cursor.execute("EXEC [dbo].[sp_GetPatinetInfoByUserId] @PatientId = ?", user_id)
        profile2 = dict(zip([col[0] for col in cursor.description], cursor.fetchone() or []))

        date = datetime.now()

        cursor.execute(
        "EXEC [dbo].[usp_GetLifestyleCardsSummary] @UserId = ?, @Date = ?",
        (user_id, date)
        )
    
        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()
        lifestyle_rows = [dict(zip(columns, row)) for row in rows]
    
        lifestyle_summary = transform_lifestyle_data(lifestyle_rows)
    
        conn.close()
    
        return {**profile1, **profile2, **lifestyle_summary}
    
def upsert_chat_history(database_name, user_id, chat_history, constraints, context):
        conn = get_db_connection_dynamic(database_name)
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM [Nauriq].[AI_Formatted_Chat_History] WHERE [user_id] = ?", user_id)
        exists = cursor.fetchone()
        if exists:
            cursor.execute("""
                UPDATE [Nauriq].[AI_Formatted_Chat_History]
                SET [chat] = ?, [constraints] = ?, [last_agent_context] = ?
                WHERE [user_id] = ?
            """, (json.dumps(chat_history), json.dumps(constraints), json.dumps(context), user_id))
        else:
            cursor.execute("""
                INSERT INTO [Nauriq].[AI_Formatted_Chat_History] ([user_id], [chat], [constraints], [last_agent_context])
                VALUES (?, ?, ?, ?)
            """, (user_id, json.dumps(chat_history), json.dumps(constraints), json.dumps(context)))
        conn.commit()
        cursor.close()
        conn.close()

def fetch_chat_history(database_name, user_id):
        conn = get_db_connection_dynamic(database_name)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT [chat], [constraints], [last_agent_context]
            FROM [Nauriq].[AI_Formatted_Chat_History]
            WHERE [user_id] = ?
        """, user_id)
        row = cursor.fetchone()
        conn.close()
        return (
            json.loads(row.chat) if row and row.chat else [],
            json.loads(row.constraints) if row and row.constraints else {},
            json.loads(row.last_agent_context) if row and row.last_agent_context else {},
        )

def parse_meal_plan_text_from_db(meal_plan_text: str):
    """
    Parses meal plan text fetched from database.
    Format: "Previous day (DATE) meal plan:\n\n**MealType**\n- - FoodItem | Portion Weight: XXoz/fl oz | ..."
    Returns: (parsed_data_json, prefix_text, suffix_text)
    """
    prefix_text = ""
    suffix_text = ""
    
    # Regex patterns
    meal_header_pattern = re.compile(r"^\*\*([A-Za-z]+)\*\*$", re.MULTILINE)
    item_pattern = re.compile(
        r"^- - (.+?)\s*\|\s*Portion Weight:\s*([\d.]+)(?:fl\s*)?oz\s*\|\s*"
        r"Protein:\s*([\d.]+)g\s*\|\s*Carbs:\s*([\d.]+)g\s*\|\s*"
        r"Fat:\s*([\d.]+)g\s*\|\s*Fiber:\s*([\d.]+)g\s*\|\s*"
        r"Sodium:\s*([\d.]+)mg\s*\|\s*Iodine:\s*([\d.]+)mcg\s*\|\s*"
        r"Sugar:\s*([\d.]+)g\s*\|\s*Cholesterol:\s*([\d.]+)mg\s*\|\s*"
        r"Calories:\s*([\d.]+)kcal",
        re.IGNORECASE
    )
    
    # Find where the meal plan starts (first **MealType**)
    first_meal_match = meal_header_pattern.search(meal_plan_text)
    if first_meal_match:
        prefix_text = meal_plan_text[:first_meal_match.start()].strip()
    
    # Initialize parsed data structure
    parsed_data = {
        "meal_plan": {},
        "meals_nutrients": {},
        "total_nutrients": {}
    }
    
    # Initialize grand totals
    grand_total_protein = 0.0
    grand_total_carbs = 0.0
    grand_total_fiber = 0.0
    grand_total_fat = 0.0
    grand_total_calories = 0.0
    grand_total_portion_weight = 0.0
    grand_total_sodium = 0.0
    grand_total_iodine = 0.0
    grand_total_sugar = 0.0
    grand_total_cholesterol = 0.0
    
    # Split by meal headers
    lines = meal_plan_text.split('\n')
    current_meal = None
    
    for line in lines:
        line_stripped = line.strip()
        
        if not line_stripped:
            continue
        
        # Check if this is a meal header
        meal_match = meal_header_pattern.match(line_stripped)
        if meal_match:
            current_meal = meal_match.group(1)
            parsed_data["meal_plan"][current_meal] = []
            continue
        
        # Check if this is a food item line
        item_match = item_pattern.match(line_stripped)
        if item_match and current_meal:
            food_name = item_match.group(1).strip()
            portion_weight = float(item_match.group(2))
            protein = float(item_match.group(3))
            carbs = float(item_match.group(4))
            fat = float(item_match.group(5))
            fiber = float(item_match.group(6))
            sodium = float(item_match.group(7))
            iodine = float(item_match.group(8))
            sugar = float(item_match.group(9))
            cholesterol = float(item_match.group(10))
            calories = float(item_match.group(11))
            
            # Add to meal plan
            parsed_data["meal_plan"][current_meal].append({
                "name": food_name,
                "nutrients": {
                    "Portion Weight": portion_weight,
                    "Protein": protein,
                    "Carbohydrates": carbs,
                    "Fat": fat,
                    "Fiber": fiber,
                    "Sodium": sodium,
                    "Iodine": iodine,
                    "Sugar": sugar,
                    "Cholesterol": cholesterol,
                    "Calories": calories
                },
                "Consumed": "False"
            })
            
            # Accumulate totals
            grand_total_portion_weight += portion_weight
            grand_total_protein += protein
            grand_total_carbs += carbs
            grand_total_fat += fat
            grand_total_fiber += fiber
            grand_total_sodium += sodium
            grand_total_iodine += iodine
            grand_total_sugar += sugar
            grand_total_cholesterol += cholesterol
            grand_total_calories += calories
    
    # Calculate meal-level totals
    for meal_name, items in parsed_data["meal_plan"].items():
        meal_total_protein = sum(item["nutrients"]["Protein"] for item in items)
        meal_total_carbs = sum(item["nutrients"]["Carbohydrates"] for item in items)
        meal_total_fat = sum(item["nutrients"]["Fat"] for item in items)
        meal_total_fiber = sum(item["nutrients"]["Fiber"] for item in items)
        meal_total_sodium = sum(item["nutrients"]["Sodium"] for item in items)
        meal_total_iodine = sum(item["nutrients"]["Iodine"] for item in items)
        meal_total_sugar = sum(item["nutrients"]["Sugar"] for item in items)
        meal_total_cholesterol = sum(item["nutrients"]["Cholesterol"] for item in items)
        meal_total_calories = sum(item["nutrients"]["Calories"] for item in items)
        meal_total_portion = sum(item["nutrients"]["Portion Weight"] for item in items)
        
        parsed_data["meals_nutrients"][meal_name] = {
            "Calories": round(meal_total_calories, 1),
            "Portion Weight": round(meal_total_portion, 1),
            "Protein": round(meal_total_protein, 1),
            "Carbohydrates": round(meal_total_carbs, 1),
            "Fat": round(meal_total_fat, 1),
            "Fiber": round(meal_total_fiber, 1),
            "Sodium": round(meal_total_sodium, 1),
            "Iodine": round(meal_total_iodine, 1),
            "Sugar": round(meal_total_sugar, 1),
            "Cholesterol": round(meal_total_cholesterol, 1)
        }
    
    # Set total nutrients
    parsed_data["total_nutrients"] = {
        "Calories": round(grand_total_calories, 1),
        "Portion Weight": round(grand_total_portion_weight, 1),
        "Protein": round(grand_total_protein, 1),
        "Carbohydrates": round(grand_total_carbs, 1),
        "Fat": round(grand_total_fat, 1),
        "Fiber": round(grand_total_fiber, 1),
        "Sodium": round(grand_total_sodium, 1),
        "Iodine": round(grand_total_iodine, 1),
        "Sugar": round(grand_total_sugar, 1),
        "Cholesterol": round(grand_total_cholesterol, 1)
    }
    
    json_output = json.dumps([parsed_data], indent=2)
    
    return json_output, prefix_text, suffix_text



def cm_to_feet_inches(cm: float) -> float:
    total_inches = cm / 2.54
    return total_inches

def average_vitals(vitals_list):
    vitals = vitals_list.get("data", {}).get("vitals", {})
    raw_averages = {}

    for key, value in vitals.items():
        if isinstance(value, dict):
            for field in ["heartRateValue", "bloodGlucoseValue", "systolic", "diastolic", "respiratoryRateValue", "bloodOxygenValue", "bodyTempratureValue"]:
                if field in value and value[field] is not None:
                    try:
                        raw_averages[field] = float(value[field])
                    except (ValueError, TypeError):
                        pass
        else:
            try:
                raw_averages[key] = float(value)
            except (ValueError, TypeError):
                continue

    readable_vitals = {}

    if "heartRateValue" in raw_averages:
        readable_vitals["Heart Rate"] = raw_averages["heartRateValue"]
    if "bloodGlucoseValue" in raw_averages:
        readable_vitals["Blood Glucose"] = raw_averages["bloodGlucoseValue"]
    if "systolic" in raw_averages and "diastolic" in raw_averages:
        readable_vitals["Blood Pressure"] = {
            "systolic": raw_averages["systolic"],
            "diastolic": raw_averages["diastolic"]
        }
    if "respiratoryRateValue" in raw_averages:
        readable_vitals["Respiration Rate"] = raw_averages["respiratoryRateValue"]
    if "bloodOxygenValue" in raw_averages:
        readable_vitals["Blood Oxygen Saturation"] = raw_averages["bloodOxygenValue"]
    if "bodyTempratureValue" in raw_averages:
        readable_vitals["Body Temperature"] = raw_averages["bodyTempratureValue"]

    return readable_vitals

def consume_all_results(cur: pyodbc.Cursor) -> None:
    """Safely consume all result sets without disrupting transactions"""
    try:
        # Get the first result set
        cur.fetchall()
        
        # Move through any additional result sets
        while cur.nextset():
            try:
                cur.fetchall()
            except pyodbc.Error:
                # Some result sets might not have data
                pass
    except pyodbc.Error:
        # Handle cases where there are no results
        pass

# Updated consume_all_results function (alternative approach)
def consume_all_results_safe(cur: pyodbc.Cursor) -> None:
    """Safely consume all result sets without disrupting transactions"""
    try:
        # Get the first result set
        cur.fetchall()
        
        # Move through any additional result sets
        while cur.nextset():
            try:
                cur.fetchall()
            except pyodbc.Error:
                # Some result sets might not have data
                pass
    except pyodbc.Error:
        # Handle cases where there are no results
        pass

def lbs_to_kg(lbs):
        return round(float(lbs) * 0.453592, 2)

def parse_weight(weight_str):
        try:
            weight_data = json.loads(weight_str)[0]
            return lbs_to_kg(weight_data["WeightMeasurement"])
        except:
            return None

def parse_waist(waist_str):
    try:
        return round(float(waist_str) * 2.54, 2)
    except (json.JSONDecodeError, KeyError, ValueError, IndexError, TypeError):
        return None
            
def extract_descriptions(json_str):
        try:
            return [item["Description"] for item in json.loads(json_str)] if json_str else []
        except:
            return []

def parse_height(height_str):
    try:
        # Convert the height value (string) to float inches
        height_value_str = json.loads(height_str)[0]["HeightMeasurement"]
        inches = float(height_value_str)
        height_cm = round(inches * 2.54, 2)  # 1 inch = 2.54 cm
        return height_cm
    except (ValueError, TypeError):
        return None
    
# def convert_json_to_profile(json_profile: dict, vitals_json: dict = None, diagnoses: list = None) -> dict:
#     name = f'{json_profile.get("FirstName", "")} {json_profile.get("LastName", "")}'.strip()
#     age = json_profile.get("Age")
#     gender_map = {2: "male", 3: "female", 4: "other"}
#     gender = gender_map.get(json_profile.get("Gender"), "unknown")
#     # gender = json_profile.get("GenderDesc", "unknown")
#     weight = parse_weight(json_profile.get("LatestWeight", "[]"))
#     height_cm = parse_height(json_profile.get("LatestHeight", "[]"))
#     waist_cm = parse_waist(json_profile.get("WaistMeasurement", "[]"))
#     # ethnicity = extract_descriptions(json_profile.get("EthnicityDesc", None))
#     # cuisine = json_profile.get("CursineDes", "")
#     cuisine = json_profile.get("CuisineDes") or json_profile.get("CursineDes", "")
#     activity_level = json_profile.get("PhysicalActivityDesc", "unknown")
#     dietary_preference = extract_descriptions(json_profile.get("DietaryPreferences"))
#     dietary_restrictions = extract_descriptions(json_profile.get("DietaryRestrictions"))
#     digestive_issues = [d for d in extract_descriptions(json_profile.get("DigestiveIssues")) if d != "None"]
#     food_allergies = [a for a in extract_descriptions(json_profile.get("FoodAllergies")) if a != "None"]
#     symptom_aggravating_foods = [a for a in extract_descriptions(json_profile.get("SymptomAggravatingFoods")) if a != "None"]

#     # Build profile
#     profile_data = {
#         "name": name,
#         "age": age,
#         "gender": gender,
#         "weight_kg": weight,
#         "height_cm": height_cm,
#         "waist_circumference_cm":waist_cm,
#         # "ethnicity": ethnicity,
#         "cuisine": cuisine,
#         "activity_level": activity_level,
#         "dietary_preference": dietary_preference,
#         "restrictions": dietary_restrictions,
#         "digestive_issues": digestive_issues,
#         "allergies": food_allergies,
#         "symptom_aggravating_foods": symptom_aggravating_foods
#     }

#     if vitals_json and any(vitals_json.values()):
#         profile_data["vitals_numeric"] = {}
#         if "Heart Rate" in vitals_json:
#             profile_data["vitals_numeric"]["Heart Rate"] = vitals_json["Heart Rate"]
#         if "Blood Pressure" in vitals_json and isinstance(vitals_json["Blood Pressure"], dict):
#             bp = vitals_json["Blood Pressure"]
#             if "systolic" in bp and "diastolic" in bp:
#                 profile_data["vitals_numeric"]["Blood Pressure"] = f"{bp['systolic']}/{bp['diastolic']}"
#         if "Blood Glucose" in vitals_json:
#             profile_data["vitals_numeric"]["Blood Glucose"] = vitals_json["Blood Glucose"]
#         if "Respiration Rate" in vitals_json:
#             profile_data["vitals_numeric"]["Respiration Rate"] = vitals_json["Respiration Rate"]
#         if "Blood Oxygen Saturation" in vitals_json:
#             profile_data["vitals_numeric"]["Blood Oxygen Saturation"] = vitals_json["Blood Oxygen Saturation"]
#         if "Blood Ketones" in vitals_json:
#             profile_data["vitals_numeric"]["Blood Ketones"] = vitals_json["Blood Ketones"]
#         if "Body Temperature" in vitals_json:
#             profile_data["vitals_numeric"]["Body Temperature"] = vitals_json["Body Temperature"]

#         # Remove vitals_numeric if still empty
#         if not profile_data["vitals_numeric"]:
#             del profile_data["vitals_numeric"]
    
#     if diagnoses:
#         profile_data["medical_conditions"] = diagnoses

    

#     return profile_data


def convert_json_to_profile(json_profile: dict, vitals_json: dict = None, diagnoses: list = None) -> dict:
    # ✅ Guard clause
    if (
        not isinstance(json_profile, dict)
        or json_profile.get("status") not in (None, 200)
        or json_profile.get("data") is None and "FirstName" not in json_profile
    ):
        return {
            "error": True,
            "message": json_profile.get("message", "Empty profile data"),
            "profile": None
        }
    
    name = f'{json_profile.get("FirstName", "")} {json_profile.get("LastName", "")}'.strip()
    age = json_profile.get("Age")
    gender_map = {2: "male", 3: "female", 4: "other"}
    gender = gender_map.get(json_profile.get("Gender"), "unknown")

    weight = parse_weight(json_profile.get("LatestWeight", "[]"))
    height_cm = parse_height(json_profile.get("LatestHeight", "[]"))
    waist_cm = parse_waist(json_profile.get("WaistMeasurement", "[]"))

    cuisine = json_profile.get("CuisineDes") or json_profile.get("CursineDes", "")
    activity_level = json_profile.get("PhysicalActivityDesc", "unknown")

    dietary_preference = extract_descriptions(json_profile.get("DietaryPreferences"))
    dietary_restrictions = extract_descriptions(json_profile.get("DietaryRestrictions"))
    digestive_issues = [d for d in extract_descriptions(json_profile.get("DigestiveIssues")) if d != "None"]
    food_allergies = [a for a in extract_descriptions(json_profile.get("FoodAllergies")) if a != "None"]
    symptom_aggravating_foods = [a for a in extract_descriptions(json_profile.get("SymptomAggravatingFoods")) if a != "None"]

    profile_data = {
        "name": name,
        "age": age,
        "gender": gender,
        "weight_kg": weight,
        "height_cm": height_cm,
        "waist_circumference_cm": waist_cm,
        "cuisine": cuisine,
        "activity_level": activity_level,
        "dietary_preference": dietary_preference,
        "restrictions": dietary_restrictions,
        "digestive_issues": digestive_issues,
        "allergies": food_allergies,
        "symptom_aggravating_foods": symptom_aggravating_foods
    }

    # --- Fitness onboarding profile fields ---
    fitness_fields = [
        "primary_goal",
        "secondary_goal",
        "target_body_parts",
        "fitness_level",
        "physical_limitation",
        "specific_avoidance",
        "days_per_week",
        "session_duration",
        "available_equipment",
        "unit_system",
        "workout_location",
        "goal",
    ]

    for field in fitness_fields:
        if field in json_profile:
            if field == "session_duration":
                profile_data[field] = json_profile[field].replace("–", "-").replace(" min", " minutes")
            elif field == "days_per_week":
                value = json_profile[field]
                profile_data[field] = value if isinstance(value, list) else [value]
            else:
                profile_data[field] = json_profile[field]

    # --- Vitals ---
    if vitals_json and any(vitals_json.values()):
        profile_data["vitals_numeric"] = {}

        if "Heart Rate" in vitals_json:
            profile_data["vitals_numeric"]["Heart Rate"] = vitals_json["Heart Rate"]

        if "Blood Pressure" in vitals_json and isinstance(vitals_json["Blood Pressure"], dict):
            bp = vitals_json["Blood Pressure"]
            if "systolic" in bp and "diastolic" in bp:
                profile_data["vitals_numeric"]["Blood Pressure"] = f"{bp['systolic']}/{bp['diastolic']}"

        for key in ["Blood Glucose", "Respiration Rate", "Blood Oxygen Saturation", "Blood Ketones", "Body Temperature"]:
            if key in vitals_json:
                profile_data["vitals_numeric"][key] = vitals_json[key]

        if not profile_data["vitals_numeric"]:
            del profile_data["vitals_numeric"]

    if diagnoses:
        profile_data["medical_conditions"] = diagnoses

    return profile_data

def make_value_clause(values):
    return ', '.join([f"('{v}')" for v in values])

_CACHE: Dict[str, Dict] = {}
_CACHE_LOCK = threading.Lock()
CACHE_TTL = 3600  # seconds

def _normalize(text: str) -> str:
    """
    Normalize for approximate matching:
    - Lowercase
    - Replace hyphens with spaces
    - Remove parenthetical content
    - Remove extra whitespace
    """
    text = text.lower()
    text = text.replace('-', ' ')
    text = re.sub(r'\([^)]*\)', '', text)  # remove (anything)
    text = re.sub(r'[^a-z0-9 ]+', '', text)  # remove punctuation
    return re.sub(r'\s+', ' ', text).strip()



def smart_match(input_val, db_values, threshold=85):
    norm_input = _normalize(input_val)
    norm_db_map = { _normalize(val): val for val in db_values }

    # 1. Exact normalized match
    if norm_input in norm_db_map:
        return norm_db_map[norm_input]

    # 2. Full token match (only when input has >1 word)
    input_tokens = set(norm_input.split())
    for db_val in db_values:
        db_tokens = set(_normalize(db_val).split())
        if input_tokens == db_tokens:
            return db_val
        if input_tokens.issubset(db_tokens) and len(input_tokens) >= 2:
            return db_val

    # 3. Partial match as full word (avoid substring-only match like 'fish' in 'shellfish')
    for db_val in db_values:
        db_tokens = set(_normalize(db_val).split())
        if any(token == norm_input for token in db_tokens):
            return db_val

    # 4. Fuzzy fallback (now re-enabled!)
    best_match, score, _ = process.extractOne(norm_input, db_values, scorer=fuzz.token_sort_ratio)
    if score >= threshold:
        print(f"✔️ Fuzzy match for '{input_val}' → '{best_match}' (score: {score})")
        return best_match

    print(f"⚠️ No match for '{input_val}'")
    return None

def _refresh_cache(conn: pyodbc.Connection, table: str, key_col: str, val_col: str) -> None:
    cur = conn.cursor()
    cur.execute(f"SELECT {key_col}, {val_col} FROM {table}")
    _CACHE[table] = {
        "data": { _normalize(k): v for k, v in cur.fetchall() if k },
        "expires": ti.time() + CACHE_TTL
    }
    cur.close()


def db_lookup(
    conn: pyodbc.Connection,
    values: List[str],
    table: str,
    key_col: str,
    val_col: str,
    return_val: bool = True  # False = return cleaned key instead
) -> List[str]:
    now = ti.time()
    with _CACHE_LOCK:
        if table not in _CACHE or now > _CACHE[table]["expires"]:
            _refresh_cache(conn, table, key_col, val_col)

        mapping = _CACHE[table]["data"]

    results = []
    for v in values:
        norm_v = _normalize(v)
        exact = mapping.get(norm_v)
        if exact:
            results.append(exact if return_val else v.title())
            continue

        # fuzzy match fallback
        close_keys = difflib.get_close_matches(norm_v, mapping.keys(), n=1, cutoff=0.6)
        if close_keys:
            matched_key = close_keys[0]
            matched_val = mapping[matched_key]
            results.append(matched_val if return_val else matched_key.title())
            print(f"✔️ Fuzzy match for '{v}' → '{matched_key}'")
        else:
            print(f"⚠️ No match for '{v}' in {table}")
    return results

def get_codes_from_db(conn, name_col, table,  code_col, values, is_desc_only=False):
    cursor = conn.cursor()
    cursor.execute(f"SELECT {name_col}, {code_col} FROM {table}")
    rows = cursor.fetchall()

    name_to_code = {row[0]: row[1] for row in rows if row[0]}
    db_names = list(name_to_code.keys())

    result = []
    for val in values:
        matched_name = smart_match(val, db_names)
        if matched_name:
            result.append(matched_name if is_desc_only else name_to_code[matched_name])
    return result

# def fetch_patient_dashboard(user_id: str, bearer_token: str, current_date: str):
#     url = PATIENT_DASHBOARD
#     headers = {
#         "Authorization": f"Bearer {bearer_token}",
#         "Content-Type": "application/json"
#     }
#     payload = {
#         "UserId": user_id,
#         "TimePeriod": "DAY",
#         "CurrentDate": current_date,
#         "TimeZoneOffset": 330,
#         "OffsetMinutes": -330
#     }

#     try:
#         # ADDED TIMEOUT: Fail fast after 4 seconds if dashboard is slow
#         response = requests.post(url, headers=headers, json=payload, timeout=4)
        
#         if response.status_code == 200:
#             return response.json()
#         else:
#             print(f"Dashboard API Error: {response.status_code}")
#             return {}
            
#     except requests.exceptions.Timeout:
#         print("Dashboard API timed out (skipped to save time)")
#         return {} 
#     except Exception as e:
#         print(f"Failed to fetch dashboard: {e}")
#         return {}

# def get_jwt_payload(request: Request):
#     auth = request.headers.get("authorization")
#     if not auth or not auth.lower().startswith("bearer "):
#         raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
#     token = auth.split(" ", 1)[1]
#     payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=["HS256"], options={"verify_signature": False})
#     # payload = jwt.decode(token, "dwTtjGhXx02Mm8A6VxTf6Gz5wSTZ2jY2vawMzG3Qz4j", algorithms=["HS256"], options={"verify_signature": False})
#     return payload

def get_jwt_payload(request: Request):
    auth = request.headers.get("authorization")

    if not auth or not auth.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail={
                "status": 401,
                "message": "Unauthorized",
                "data": []
            }
        )

    token = auth.split(" ", 1)[1]

    try:
        payload = jwt.decode(
            token,
            JWT_SECRET_KEY,
            algorithms=["HS256"],
            options={"verify_signature": False}
        )

        # ---------- EXPIRY CHECK ----------
        exp = payload.get("exp")
        if exp and exp < int(datetime.now().timestamp()):
            raise HTTPException(
                status_code=401,
                detail={
                    "status": 401,
                    "message": "Unauthorized",
                    "data": []
                }
            )

        return payload

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail={
                "status": 401,
                "message": "Unauthorized",
                "data": []
            }
        )

    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=401,
            detail={
                "status": 401,
                "message": "Unauthorized",
                "data": []
            }
        )

def get_patient_active_diagnoses(patient_id: str, database_name: str):
    """
    OPTIMIZED: Fetches all diagnoses using only 2 connections total, instead of N+1.
    """
    results = []
    try:
        # 1. Get Codes from Patient DB
        conn_dynamic = get_db_connection_dynamic(database_name)
        cursor_dynamic = conn_dynamic.cursor()
        cursor_dynamic.execute("""
            SELECT ConditionCode
            FROM [dbo].[PatientDiagnosis]
            WHERE PatientId = ? AND IsActive = 1 AND Deleted = 0
        """, patient_id)
        rows = cursor_dynamic.fetchall()
        cursor_dynamic.close()
        conn_dynamic.close()

        if not rows:
            return []

        # Extract codes list
        condition_codes = [row[0] for row in rows]

        # 2. Get Names from Master DB (Single Batch Query)
        conn_master = get_db_connection_dynamic(MASTER_TABLE_DB_NAME)
        cursor_master = conn_master.cursor()
        
        # Create parameter placeholders (?, ?, ?)
        placeholders = ','.join('?' for _ in condition_codes)
        query = f"""
            SELECT MedicalConditionName
            FROM [Master].[HealthDiagnoses]
            WHERE ConditionCode IN ({placeholders})
        """
        
        cursor_master.execute(query, condition_codes)
        master_rows = cursor_master.fetchall()
        
        results = [row[0] for row in master_rows]
        
        cursor_master.close()
        conn_master.close()
        
    except Exception as e:
        print(f"Error getting diagnoses: {e}")
        # Return empty list on error to prevent crashing the whole profile fetch
        return []
        
    return results

def kg_to_lbs(kg: float) -> float:
    return round(kg / 0.453592, 2)

def split_household_measure(household_measure: str):
    """
    Example:
    1 Medium Bowl -> (1, Medium Bowl)
    Medium Apple -> (1, Medium Apple)
    """

    household_measure = household_measure.strip()

    match = re.match(r"^(\d*\.?\d+)\s+(.+)$", household_measure)

    if match:
        return float(match.group(1)), match.group(2).strip()

    return 1, household_measure

def parse_meal_plan_text_to_items(meal_plan_text: str):

    meal_plan_text = meal_plan_text.replace("**", "").strip()

    meal_pattern = re.compile(
        r"^(Breakfast|Morning Snack|Lunch|Evening Snack|Dinner)$",
        re.IGNORECASE | re.MULTILINE
    )

    item_pattern_new = re.compile(
        r"^-?\s*(.*?)\s*\|\s*(?:FoodID:\s*([A-Z_0-9]+)\s*\|\s*)?"
        r"Household Measure:\s*([^|]+?)\s*\|\s*"
        r"Portion Weight:\s*~?([\d.]+)\s*(fl\s?oz|oz|g|ml)\s*\|\s*"
        r"Protein:\s*~?([\d.]+)g\s*\|\s*"
        r"Carbs:\s*~?([\d.]+)g\s*\|\s*"
        r"Fat:\s*~?([\d.]+)g\s*\|\s*"
        r"Fiber:\s*~?([\d.]+)g\s*\|\s*"
        r"Sodium:\s*~?([\d.]+)mg\s*\|"
        r"(?:\s*Iodine:\s*~?([\d.]+)mcg\s*\|)?"
        r"\s*Sugar:\s*~?([\d.]+)g\s*\|\s*"
        r"Cholesterol:\s*~?([\d.]+)mg\s*\|\s*"
        r"Calories:\s*~?([\d.]+)kcal",
        re.IGNORECASE
    )

    lines = meal_plan_text.splitlines()

    current_meal = None
    items = []

    for line in lines:

        line = line.strip()

        if not line:
            continue

        # -----------------------------------
        # Meal Type
        # -----------------------------------

        meal_match = meal_pattern.match(line)

        if meal_match:
            current_meal = meal_match.group(1).replace(" ", "")
            continue

        # -----------------------------------
        # Meal Item
        # -----------------------------------

        item_match = item_pattern_new.match(line)

        if not item_match or not current_meal:
            continue

        food_name = item_match.group(1).strip()

        household_measure = item_match.group(3).strip()

        portion_weight = float(item_match.group(4))
        portion_unit = item_match.group(5).lower().strip()

        protein = float(item_match.group(6))
        carbs = float(item_match.group(7))
        fat = float(item_match.group(8))
        fiber = float(item_match.group(9))
        sodium = float(item_match.group(10))

        iodine = (
            float(item_match.group(11))
            if item_match.group(11)
            else 0.0
        )

        sugar = float(item_match.group(12))
        cholesterol = float(item_match.group(13))
        calories = float(item_match.group(14))

        # -----------------------------------
        # Household Split
        # -----------------------------------

        household_measure_count, household_measure_unit = (
            split_household_measure(household_measure)
        )

        # -----------------------------------
        # Extra Fields
        # -----------------------------------

        food_id_match = re.search(
            r"FoodID:\s*([^|]+)",
            line,
            re.IGNORECASE
        )

        ingredients_match = re.search(
            r"Ingredients:\s*([^|]+)",
            line,
            re.IGNORECASE
        )

        recipe_match = re.search(
            r"Recipe:\s*(.*)$",
            line,
            re.IGNORECASE
        )

        food_id = (
            food_id_match.group(1).strip()
            if food_id_match
            else None
        )

        # -----------------------------------
        # INGREDIENTS
        # -----------------------------------

        ingredients_text = (
            ingredients_match.group(1).strip()
            if ingredients_match
            else ""
        )

        ingredients_list = []
        ingredient_total_grams = 0

        if ingredients_text.upper() != "N/A":

            raw_items = [
                x.strip()
                for x in ingredients_text.split(",")
                if x.strip()
            ]

            merged_items = []

            i = 0

            while i < len(raw_items):

                current = raw_items[i]

                # merge broken ingredient
                # Example:
                # Corn Flakes + Unsweetened 30g

                if (
                    i + 1 < len(raw_items)
                    and re.search(
                        r"\d+\.?\d*\s?(g|ml|oz|tbsp|tsp|cup|cups)$",
                        raw_items[i + 1],
                        re.IGNORECASE
                    )
                ):

                    merged_items.append(
                        f"{current}, {raw_items[i + 1]}"
                    )

                    i += 2

                else:

                    merged_items.append(current)
                    i += 1

            for ingredient in merged_items:

                match = re.match(
                    r"(.+?)\s+(\d+\.?\d*)\s?([a-zA-Z]+)$",
                    ingredient,
                    re.IGNORECASE
                )

                if match:

                    ingredient_name = (
                        match.group(1)
                        .strip()
                        .lower()
                    )

                    quantity = float(match.group(2))

                    unit = (
                        match.group(3)
                        .strip()
                        .lower()
                    )

                    if unit == "g":
                        ingredient_total_grams += quantity

                    ingredients_list.append({
                        "name": ingredient_name,
                        "quantity": quantity,
                        "unit": unit
                    })

                else:

                    ingredients_list.append({
                        "name": ingredient.lower(),
                        "quantity": None,
                        "unit": None
                    })

        # -----------------------------------
        # RECIPE
        # -----------------------------------

        recipe_text = (
            recipe_match.group(1).strip()
            if recipe_match
            else ""
        )

        recipe_list = []

        if recipe_text.upper() != "N/A":

            steps = re.findall(
                r"(?:Step\s*\d+:\s*)(.*?)(?=Step\s*\d+:|$)",
                recipe_text,
                flags=re.IGNORECASE
            )

            if not steps:
                steps = [recipe_text]

            recipe_list = [
                f"{idx}. {step.strip()}"
                for idx, step in enumerate(steps, start=1)
                if step.strip()
            ]

        # -----------------------------------
        # FINAL ITEM
        # -----------------------------------

        items.append({

            "MealType": current_meal,

            "FoodItems": food_name,

            "food_id": food_id,

            "ingredients": ingredients_list,

            "ingredient_total_grams": ingredient_total_grams,

            "recipe": recipe_list,

            "household_measure": household_measure,

            "household_measure_count": household_measure_count,

            "household_measure_unit": household_measure_unit,

            "PortionWeight": portion_weight,

            "PortionWeightUnit": portion_unit,

            "Protein": protein,

            "Carbohydrates": carbs,

            "Fat": fat,

            "Fiber": fiber,

            "Sodium": sodium,

            "Iodine": iodine,

            "Sugar": sugar,

            "Cholesterol": cholesterol,

            "Calories": calories,
        })

    return items


def split_weekly_meal_text(meal_plan_text: str, start_date: date):
    """
    Splits meal_plan_text into date -> text mapping.
    Supports:
    - ### Monday
    - ### 2025-12-15
    """
    meal_plan_text = meal_plan_text.replace("**", "").strip()
 
    # Match headers like ### Monday OR ### 2025-12-15
    header_pattern = re.compile(r"^###\s+(.*)$", re.MULTILINE)
 
    matches = list(header_pattern.finditer(meal_plan_text))
    day_blocks = {}
 
    for idx, match in enumerate(matches):
        header = match.group(1).strip()
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(meal_plan_text)
        content = meal_plan_text[start:end].strip()
 
        # Case 1: Explicit date
        try:
            meal_date = datetime.strptime(header, "%Y-%m-%d").date()
        except ValueError:
            # Case 2: Weekday name
            weekday_map = {
                "monday": 0, "tuesday": 1, "wednesday": 2,
                "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6
            }
            wd = header.lower()
            if wd not in weekday_map:
                continue
 
            meal_date = start_date + timedelta(days=weekday_map[wd])
 
        day_blocks[meal_date] = content
 
    return day_blocks

# FIX: Removed 'async' keyword. This function should be synchronous as it uses blocking pyodbc calls.
def get_meal_plan_data(database_name: str, patient_id: str):
    conn = get_db_connection_dynamic(database_name)
    cursor = conn.cursor()
 
    try:
        today = datetime.today().date()
 
        cursor.execute("""
            SELECT MealType, FoodItems, PortionWeight, PortionWeightUnit,
                   Protein, Carbohydrates, Fat, Fiber,
                   Sodium, Sugar, Cholesterol, Calories
            FROM dbo.MealPlanDetails
            WHERE PatientId = ? AND CAST(MealDate AS DATE) = ?
        """, (patient_id, today))
 
        rows = cursor.fetchall()
        if not rows:
            return {"status": "success", "data": [], "message": "No meal plan found"}
 
        cols = [c[0] for c in cursor.description]
 
        meal_order = {
            "Breakfast": 1,
            "Morning Snack": 2,
            "Lunch": 3,
            "Evening Snack": 4,
            "Dinner": 5
        }
 
        numeric_fields = (
            "PortionWeight", "Protein", "Carbohydrates", "Fat",
            "Fiber", "Sodium", "Sugar", "Cholesterol", "Calories"
        )
 
        meal_plan = {}
        meal_totals = {}
        total_nutrients = {k: 0.0 for k in numeric_fields}
 
        for row in rows:
            meal = dict(zip(cols, row))
 
            meal_type = meal["MealType"].strip().title()
            meal_type = meal_type.replace("Morningsnack", "Morning Snack") \
                                 .replace("Eveningsnack", "Evening Snack")
 
            if meal_type not in meal_order:
                continue
 
            if meal_type not in meal_plan:
                meal_plan[meal_type] = []
                meal_totals[meal_type] = {k: 0.0 for k in numeric_fields}
                meal_totals[meal_type]["PortionWeightUnit"] = meal["PortionWeightUnit"]
 
            nutrients = {
                "PortionWeight": float(meal["PortionWeight"] or 0),
                "PortionWeightUnit": meal["PortionWeightUnit"],
                "Protein": float(meal["Protein"] or 0),
                "Carbohydrates": float(meal["Carbohydrates"] or 0),
                "Fat": float(meal["Fat"] or 0),
                "Fiber": float(meal["Fiber"] or 0),
                "Sodium": float(meal["Sodium"] or 0),
                "Sugar": float(meal["Sugar"] or 0),
                "Cholesterol": float(meal["Cholesterol"] or 0),
                "Calories": float(meal["Calories"] or 0),
            }
 
            meal_plan[meal_type].append({
                "name": meal["FoodItems"],
                "nutrients": nutrients
            })
 
            for k in numeric_fields:
                meal_totals[meal_type][k] += nutrients[k]
                total_nutrients[k] += nutrients[k]
 
        # round numeric values only
        def round_dict(d):
            for k, v in d.items():
                if isinstance(v, float):
                    d[k] = round(v, 1)
            return d
 
        meals_nutrients = {
            k: round_dict(v) for k, v in meal_totals.items()
        }
 
        return {
            "status": "success",
            "data": [{
                "meal_plan": meal_plan,
                "meals_nutrients": meals_nutrients,
                "total_nutrients": round_dict(total_nutrients)
            }]
        }
 
    finally:
        cursor.close()
        conn.close()

AZURE_CONFIG = {
    "connection_string": "DefaultEndpointsProtocol=https;AccountName=endocblobstorageadmin;AccountKey=w+KTqHN2xM8kxpJk3gIJMNQEBF5CymRsLEe5xyAKj0SF6j8sKmzRYjQV7b6A4s7m5TP4L1i0+Io5+ASt+AHdCA==;EndpointSuffix=core.windows.net",
    "container_name": "friskaaitest"
}

def get_tenant_identifier(database_name, tenant_id: str) -> str:
    """Fetch Identifier from Tenants table using TenantId."""
    conn =  get_db_connection_dynamic(database_name)
    cursor = conn.cursor()
    cursor.execute("SELECT Identifier FROM Tenants WHERE TenantId = ?", tenant_id)
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return row.Identifier


def generate_sas_uri(tenant_id: str, user_id: str, folder_id: str, document_id: str, file_name: str) -> str:
    """Generate SAS URI for a specific document blob path."""
    try:
        blob_service_client = BlobServiceClient.from_connection_string(AZURE_CONFIG["connection_string"])

        # ✅ Build correct blob path
        blob_path = f"{tenant_id}/{user_id}/MedicalDocuments/{folder_id}/{document_id}/{file_name}"

        # ✅ Generate SAS Token (valid 1 hr)
        sas_token = generate_blob_sas(
            account_name=blob_service_client.account_name,
            container_name=AZURE_CONFIG["container_name"],
            blob_name=blob_path,
            account_key=blob_service_client.credential.account_key,
            permission=BlobSasPermissions(read=True),
            expiry=datetime.utcnow() + timedelta(hours=1)
        )

        # ✅ Full SAS URL
        sas_url = (
            f"https://{blob_service_client.account_name}.blob.core.windows.net/"
            f"{AZURE_CONFIG['container_name']}/{blob_path}?{sas_token}"
        )

        return sas_url
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating SAS URI: {e}")
    
def serialize_data(obj):
        """Convert non-JSON serializable objects to JSON-compatible formats"""
        from decimal import Decimal
        
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        elif isinstance(obj, time):
            return obj.strftime("%H:%M:%S")
        elif isinstance(obj, Decimal):
            return float(obj)
        return obj

def serialize_all(obj):
        if isinstance(obj, dict):
            return {k: serialize_all(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [serialize_all(v) for v in obj]
        elif isinstance(obj, datetime):
            return obj.isoformat()
        elif isinstance(obj, date):
            return obj.isoformat()
        elif isinstance(obj, time):
            return obj.strftime("%H:%M:%S")
        else:
            return obj
        
def parse_datetime(dt_str: str):
    """
    Safely parse 'YYYY-MM-DD HH:MM:SS' or ISO-like strings.
    """
    if not dt_str:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            continue
    raise ValueError(f"Invalid datetime format: {dt_str}")

def extract_fitness_onboarding_answers(profile_dict3: dict) -> dict:

    if not profile_dict3 or "JsonResult" not in profile_dict3:
        return {}

    data = json.loads(profile_dict3["JsonResult"])

    QUESTION_MAP = {
        "FIT-Q-GOAL-PRIMARY": "primary_goal",
        "FIT-Q-GOAL-REGION": "target_body_parts",
        "FIT-Q-GOAL-LEVEL": "fitness_level",
        "FIT-Q-SAFE-LIMIT": "physical_limitation",
        "FIT-Q-SAFE-AVOID": "specific_avoidance",
        "FIT-Q-EXER-PLACE": "workout_location",
        "FIT-Q-EXER-EQUIP": "available_equipment",
        "FIT-Q-EXER-TIME": "session_duration",
        "FIT-Q-EXER-DAYS": "days_per_week",
    }

    result = {}

    for form in data.get("Forms", []):
        if form.get("FormIdentifier") != "FITNESS-ONBOARDING":
            continue

        for section in form.get("Sections", []):
            for q in section.get("Questions", []):
                qid = q.get("QuestionIdentifier")
                field = QUESTION_MAP.get(qid)
                if not field:
                    continue

                answer = q.get("Answer", {})
                value = (answer.get("AnswerValue") or "").strip()
                if not value:
                    continue

                if q.get("QuestionType") == "MULTI_CHOICE":
                    result[field] = [v.strip() for v in value.split(",")]
                elif q.get("QuestionType") == "YES_NO":
                    result[field] = "None" if value.lower() == "no" else value
                else:
                    result[field] = value

    # Normalizations / derived fields
    # if "session_duration" in result:
    #     result["session_duration"] = result["session_duration"].replace("–", "-") + " minutes"

    result.setdefault("secondary_goal", "None")
    result.setdefault("unit_system", "Metric (kg, cm)")
    result.setdefault("fitness_level", "Beginner (0–6 months)")

    if "primary_goal" in result:
        result["goal"] = result["primary_goal"]

    return result

def fetch_vitals(user_id: str, cursor: str, current_date: str):

    cursor.execute("EXEC [Member].[GetUserNutritionDashboardVitals] @UserId = ?, @CurrentDate = ?, @TimeZoneOffset = ?, @OffsetMinutes = ?", user_id, current_date, 330, -330)
    vitals = dict(zip([col[0] for col in cursor.description], cursor.fetchone() or []))
    
    return vitals


def averagevitals(vitals_list):
    vitals_raw = vitals_list.get("Vitals", {})

    # Step 1: Decode JSON if needed
    if isinstance(vitals_raw, str):
        vitals = json.loads(vitals_raw)
    else:
        vitals = vitals_raw

    raw_values = {}

    for vital_name, vital_data in vitals.items():
        if not isinstance(vital_data, dict):
            continue

        for field in [
            "HeartRateValue",
            "BloodGlucoseValue",
            "Systolic",
            "Diastolic",
            "RespiratoryRateValue",
            "BloodOxygenValue",
            "BodyTempratureValue"
        ]:
            if field in vital_data and vital_data[field] is not None:
                try:
                    raw_values[field] = float(vital_data[field])
                except (ValueError, TypeError):
                    pass

    # Step 2: Human-readable output
    readable_vitals = {}

    if "HeartRateValue" in raw_values:
        readable_vitals["Heart Rate"] = raw_values["HeartRateValue"]

    if "BloodGlucoseValue" in raw_values:
        readable_vitals["Blood Glucose"] = raw_values["BloodGlucoseValue"]

    if "Systolic" in raw_values and "Diastolic" in raw_values:
        readable_vitals["Blood Pressure"] = {
            "systolic": raw_values["Systolic"],
            "diastolic": raw_values["Diastolic"]
        }

    if "RespiratoryRateValue" in raw_values:
        readable_vitals["Respiration Rate"] = raw_values["RespiratoryRateValue"]

    if "BloodOxygenValue" in raw_values:
        readable_vitals["Blood Oxygen Saturation"] = raw_values["BloodOxygenValue"]

    if "BodyTempratureValue" in raw_values:
        readable_vitals["Body Temperature"] = raw_values["BodyTempratureValue"]

    return readable_vitals

async def fetch_patient_profile(user_id: str, database_name: str, token: str):
    conn_dynamic = get_db_connection_dynamic(database_name)
    cursor_dynamic = conn_dynamic.cursor()

    # --- Profile Queries ---
    cursor_dynamic.execute("EXEC [dbo].[usp_GetPatientDietaryProfileBySearch] @UserId = ?", user_id)
    profile_row1 = cursor_dynamic.fetchone()
    profile_columns1 = [col[0] for col in cursor_dynamic.description] if profile_row1 else []
    profile_dict1 = dict(zip(profile_columns1, profile_row1)) if profile_row1 else {}

    
    cursor_dynamic.execute("EXEC [dbo].[sp_GetPatinetInfoByUserId] @PatientId = ?", user_id)
    profile_row2 = cursor_dynamic.fetchone()
    profile_columns2 = [col[0] for col in cursor_dynamic.description] if profile_row2 else []
    profile_dict2 = dict(zip(profile_columns2, profile_row2)) if profile_row2 else {}
    
 
    cursor_dynamic.execute(
        "EXEC usp_GetPatientFormData @FormId = 'FITNESS-ONBOARDING', @PatientId = ?",
        '50bce949-cbe4-43e0-a694-ffb7fe34d73f'
    )
    profile_row3 = cursor_dynamic.fetchone()
    profile_dict3 = dict(zip([c[0] for c in cursor_dynamic.description], profile_row3)) if profile_row3 else {}

    # ✅ EXTRACT fitness answers
    fitness_answers = extract_fitness_onboarding_answers(profile_dict3)
    
    # merged_profile = {**profile_dict2, **profile_dict1}
    merged_profile = {
    **profile_dict2,
    **profile_dict1,
    **fitness_answers
}

    # --- Fetch Patient Goals ---
    cursor_dynamic.execute(f"""
        SELECT 
            pg.PatientGoalCode, pg.GoalCode, gm.GoalName, gm.GoalDescription, 
            gtm.GoalTypeName, gcm.CategoryName, gsm.StatusName,
            pg.TargetValue, pg.CurrentValue, pg.ProgressPercent,
            pg.StartDate, pg.TargetDate
        FROM {database_name}.[dbo].[PatientGoals] pg
        LEFT JOIN [{MASTER_TABLE_DB_NAME}].[Master].[GoalMaster] gm ON pg.GoalCode = gm.GoalCode
        LEFT JOIN [{MASTER_TABLE_DB_NAME}].[Master].[GoalTypeMaster] gtm ON gm.GoalTypeCode = gtm.GoalTypeCode
        LEFT JOIN [{MASTER_TABLE_DB_NAME}].[Master].[GoalCategoryMaster] gcm ON pg.CategoryCode = gcm.CategoryCode
        LEFT JOIN [{MASTER_TABLE_DB_NAME}].[Master].[GoalStatusMaster] gsm ON pg.StatusCode = gsm.StatusCode
        WHERE pg.PatientId = ?
        ORDER BY pg.CreatedDate DESC
    """, user_id)

    patient_goals = []
    goal_rows = cursor_dynamic.fetchall()
    goal_columns = [col[0] for col in cursor_dynamic.description]

    for goal_row in goal_rows:
        goal_dict = dict(zip(goal_columns, goal_row))

        # --- Fetch related ActivityGoals ---
        cursor_dynamic.execute(f"""
            SELECT 
                ag.ActivityGoalCode, ag.ActivityName, ag.TargetValue, ag.CurrentValue, ag.UnitCode,
                ag.ProgressPercent, ag.CreatedDate,
                gm.GoalName, gsm.StatusName
            FROM {database_name}.[dbo].[ActivityGoals] ag
            LEFT JOIN [{MASTER_TABLE_DB_NAME}].[Master].[GoalMaster] gm ON ag.GoalCode = gm.GoalCode
            LEFT JOIN [{MASTER_TABLE_DB_NAME}].[Master].[GoalStatusMaster] gsm ON ag.StatusCode = gsm.StatusCode
            WHERE ag.PatientGoalCode = ?
            ORDER BY ag.CreatedDate DESC
        """, goal_dict["PatientGoalCode"])

        activity_rows = cursor_dynamic.fetchall()
        activity_columns = [col[0] for col in cursor_dynamic.description]
        activities = [dict(zip(activity_columns, r)) for r in activity_rows]

        # Add activities inside each goal
        goal_dict["activities"] = activities
        patient_goals.append(goal_dict)

    # cursor_dynamic.close()
    # conn_dynamic.close()

    # --- Fetch vitals and diagnoses ---    
    now = datetime.now(timezone.utc)
    vitals_avg = averagevitals(fetch_vitals(user_id, cursor_dynamic, (now - timedelta(days=1)).strftime("%Y-%m-%d")))
    diagnoses = []
    diagnoses = get_patient_active_diagnoses(user_id, database_name)

    # --- Convert final profile ---
    profile = convert_json_to_profile(merged_profile, vitals_json=vitals_avg, diagnoses=diagnoses)
    profile["goals"] = patient_goals  # ✅ Add the new goals section

    return profile


def serialize_data(obj):
        """Convert non-JSON serializable objects to JSON-compatible formats"""
        from decimal import Decimal
        
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        elif isinstance(obj, time):
            return obj.strftime("%H:%M:%S")
        elif isinstance(obj, Decimal):
            return float(obj)
        return obj

def extract_plan_blocks(raw_text: str):
    """
    Extracts all {'success': True, 'plan': {...}} blocks safely.
    Returns list of plan dicts.
    """
    plans = []

    # Regex to capture dict blocks starting with {'success':
    pattern = re.compile(r"\{\'success\':\s*True,\s*\'plan\':\s*\{.*?\}\}", re.DOTALL)

    matches = pattern.findall(raw_text)

    for match in matches:
        try:
            # Safely convert Python dict string → dict
            parsed = ast.literal_eval(match)
            if parsed.get("success") and "plan" in parsed:
                plans.append(parsed["plan"])
        except Exception:
            continue

    return plans

def normalize_weekly_plan(plan_blocks):
    """
    Converts list of day plans into dict keyed by day_name.
    """
    weekly_plan = {}

    for plan in plan_blocks:
        day = plan.get("day_name")
        if not day:
            continue

        weekly_plan[day] = {
            "warmup": plan.get("warmup", []),
            "main_workout": plan.get("main_workout", []),
            "cooldown": plan.get("cooldown", []),
            "safety_notes": plan.get("safety_notes", []),
            "warmup_duration": plan.get("warmup_duration"),
            "cooldown_duration": plan.get("cooldown_duration"),
            "main_workout_category": plan.get("main_workout_category")
        }

    return weekly_plan

def get_plan_dates(selected_days):
    if not selected_days:
        raise ValueError("selected_days cannot be empty")

    today = date.today()
    weekday_map = {
        "Monday": 0, "Tuesday": 1, "Wednesday": 2,
        "Thursday": 3, "Friday": 4, "Saturday": 5, "Sunday": 6
    }

    # Normalize & validate days
    valid_selected_days = [
        d for d in selected_days if d in weekday_map
    ]

    if not valid_selected_days:
        raise ValueError("No valid weekday names in selected_days")

    today_idx = today.weekday()
    selected_idxs = [weekday_map[d] for d in valid_selected_days]

    current_monday = today - timedelta(days=today_idx)
    current_sunday = current_monday + timedelta(days=6)

    has_past_days = any(idx < today_idx for idx in selected_idxs)

    if has_past_days:
        start_date = current_monday + timedelta(days=7)
        end_date = start_date + timedelta(days=6)
        valid_days = valid_selected_days
    else:
        start_date = current_monday
        end_date = current_sunday
        valid_days = [
            d for d in valid_selected_days
            if weekday_map[d] >= today_idx
        ]

    return start_date, end_date, valid_days
def parse_recipe_text_to_json(text):
    """
    Parses recipe text into structured JSON.
    Expected Headers:
    1. **Ingredients**
    2. **Recipe**
    """
    data = {"ingredients": [], "steps": []}
    
    try:
        # 1. SPLIT SECTIONS
        # Regex explanation:
        # (?:^|\n)      -> Start of a new line
        # (?:\d+\.\s*)? -> Optional numbering like "1. " 
        # \*\*Ingredients\*\* -> The specific header you asked for
        # [:]* -> Optional colon 
        parts = re.split(r"(?:^|\n)(?:\d+\.\s*)?\*\*Ingredients\*\*[:]*", text, flags=re.IGNORECASE)
        
        if len(parts) < 2: 
            return data
            
        remaining = parts[1]
        
        # Split Recipe Section
        # Matches: "**Recipe**"
        sections = re.split(r"(?:^|\n)(?:\d+\.\s*)?\*\*Recipe\*\*[:]*", remaining, flags=re.IGNORECASE)
        
        ingredients_text = sections[0].strip()
        steps_text = sections[1].strip() if len(sections) > 1 else ""

        # 2. PARSE INGREDIENTS
        # Supports bullets: -, *, and • 
        ing_lines = [line.strip() for line in ingredients_text.split('\n') if line.strip().startswith(('-', '*', '•'))]
        
        for line in ing_lines:
            # Remove the bullet char
            raw = re.sub(r"^[-*•]\s*", "", line).strip()
            
            # Regex: "1 cup Rice" or "0.5 oz chicken"
            # Captures: Qty | Unit | Name
            match = re.match(r"^(\d+(?:/\d+)?(?:\.\d+)?)\s*([a-zA-Z]+)?\s+(.*)", raw)
            
            if match:
                data["ingredients"].append({
                    "qty": match.group(1) or "",
                    "unit": match.group(2) or "unit", 
                    "name": match.group(3) or ""
                })
            else:
                # Fallback
                data["ingredients"].append({"qty": "1", "unit": "unit", "name": raw})

        # 3. PARSE STEPS
        step_lines = re.findall(r"^\d+\.\s+(.*)", steps_text, re.MULTILINE)
        
        for idx, step_desc in enumerate(step_lines):
            clean_desc = step_desc.replace("**", "").strip()
            
            simple_title = f"Step {idx + 1}"
            
            data["steps"].append({
                "title": simple_title, 
                "desc": clean_desc,
                "step_num": idx + 1
            })

    except Exception as e:
        logger.error(f"Recipe Parsing Error: {e}")
        return {"ingredients": [], "steps": []}

    return data

def decode_jwt_token(token: str):
    try:
        return jwt.decode(
            token,
            JWT_SECRET_KEY,
            algorithms=["HS256"],
            options={"verify_signature": False}
        )
    except Exception:
        return None
    
async def weekly_workout_plan(
    user_id: str,
    database_name: str,
    token: str = None,
    background_tasks: BackgroundTasks = None
):
    """
    Core function to generate weekly workout plan.
    Callable by API (with token) or Celery (without token).
    """

    class MockBackgroundTasks:
        def add_task(self, func, *args, **kwargs):
            pass # Do nothing, just prevent crash

    if background_tasks is None:
        background_tasks = MockBackgroundTasks()

    conn = None
    cursor = None

    # Import locally to avoid circular dependency with ai.ask_ai -> ai.tool_core -> helpers.utils
    from ai.ask_ai import handle_process_query, ProcessQueryRequest

    try:
        # 1️⃣ Fetch profile (Token optional now)
        profile = await fetch_patient_profile(user_id, database_name, token)
        profile_json = json.loads(json.dumps(profile, default=serialize_data))
        
        # 2️⃣ Call AI
        api_payload = {
            "query": "Generate a weekly workout plan based on my user profile.",
            "chat_history": [],
            "current_constraints": profile_json,
            "last_agent_context": {},
            "profile_summary": profile_json,
            "force_tool_type": None
        }
        
        request_obj = ProcessQueryRequest(**api_payload)
        ai_response = await handle_process_query(
            user_id=user_id,
            token=token,
            request=request_obj,
            background_tasks=background_tasks
        )

        # 4️⃣ Parse Response
        weekly_plan = {}
        context = ai_response.get("updated_last_agent_context", {})
        if "fitness_plans_json" in context:
            weekly_plan = context["fitness_plans_json"]
        elif "plans_json" in ai_response:
             weekly_plan = ai_response["plans_json"]

        # 5️⃣ Extract Blocks (Fallback)
        if not weekly_plan:
            ai_text = ai_response.get("answer", "")
            raw_blocks = extract_plan_blocks(ai_text)
            weekly_plan = normalize_weekly_plan(raw_blocks)
        
        # 4️⃣ Dates
        start_date, end_date, valid_days = get_plan_dates(profile_json.get("days_per_week", 3))
    
        conn = get_db_connection_dynamic(database_name)
        cursor = conn.cursor()

        # Only keep valid workout days in correct order
        WEEKDAY_ORDER = {
            "Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3,
            "Friday": 4, "Saturday": 5, "Sunday": 6
        }
        ordered_days = sorted(
            valid_days,
            key=lambda d: WEEKDAY_ORDER.get(d, 99)
        )
        
        day_order_map = {day: idx + 1 for idx, day in enumerate(ordered_days)}

        # 5️⃣ Insert Plan
        session_duration_str = str(profile_json.get("session_duration", "30-45 minutes"))

        cursor.execute("""
            INSERT INTO PatientFitnessPlans
            (PatientId, PlanName, DurationWeeks, SessionsPerWeek,
             SessionMinutes, StartDate, EndDate,
             AuthorizedBy, CreatedBy, Status, CreatedAt)
            OUTPUT INSERTED.GuidId
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '1', GETDATE())
        """, (
            user_id,
            "AI Weekly Workout Plan",
            1,
            len(valid_days),
            session_duration_str,
            start_date,
            end_date,
            "AI",
            "AI"
        ))
        plan_guid = cursor.fetchone()[0]

        # 6️⃣ Insert Days & Exercises
        SECTION_MAP = {
            "warmup": "warmup",
            "main_workout": "main_workout",
            "cooldown": "cooldown"
        }
        
        ABBREV_TO_FULL_DAY = {
            "Mon": "Monday", "Tue": "Tuesday", "Wed": "Wednesday",
            "Thu": "Thursday", "Fri": "Friday", "Sat": "Saturday", "Sun": "Sunday"
        }
        
        DAY_TO_INT_MAP = {
            "Monday": 1, "Tuesday": 2, "Wednesday": 3, "Thursday": 4, 
            "Friday": 5, "Saturday": 6, "Sunday": 7
        }

        for day_key, day_data in weekly_plan.items():
            day_name = ABBREV_TO_FULL_DAY.get(day_key, day_key)
            
            if day_name not in day_order_map:
                continue
        
            cursor.execute("""
                INSERT INTO PatientFitnessPlanDays
                (PlanGuidId, DayName, DayOrder, DayOfWeek, ExpertNotes)
                OUTPUT INSERTED.GuidId
                VALUES (?, ?, ?, ?, ?)
            """, (
                plan_guid,
                day_name,
                day_order_map[day_name],  
                DAY_TO_INT_MAP.get(day_name, 1),
                json.dumps(day_data.get("safety_notes", []))
            ))
        
            day_guid = cursor.fetchone()[0]

            exercises_to_insert = []
            order = 1
            for section_key, db_section_name in SECTION_MAP.items():
                exercises = day_data.get(section_key, [])
                for ex in exercises:
                    # Clean RPE
                    rpe_raw = ex.get("intensity_rpe", "")
                    if isinstance(rpe_raw, str):
                        rpe_raw = rpe_raw.replace("RPE", "").strip()
                    
                    # Clean Calories
                    est_cal = ex.get("est_calories", "")
                    if isinstance(est_cal, str):
                        est_cal = est_cal.replace("Est:", "").strip()

                    # Clean Rest
                    rest_val = ex.get("rest", "")
                    if isinstance(rest_val, str):
                        rest_val = re.sub(r'[^\x00-\x7F]+', '-', rest_val)

                    # Instructions
                    instructions = ex.get("steps", [])
                    if isinstance(instructions, list):
                        instructions = " ".join(instructions)
                    
                    # Sets
                    try:
                        sets_val = int(ex.get("sets", 0))
                    except:
                        sets_val = 0

                    exercises_to_insert.append((
                        day_guid,
                        db_section_name,
                        order,
                        ex.get("name"),
                        sets_val,
                        ex.get("reps") or ex.get("hold") or str(ex.get("duration", "")),
                        str(rpe_raw) if rpe_raw else None,
                        ex.get("benefit"),
                        instructions,
                        "AI",
                        rest_val,
                        ex.get("equipment"),
                        est_cal,
                        ex.get("safety_cue")
                    ))
                    order += 1
            
            if exercises_to_insert:
                cursor.executemany("""
                    INSERT INTO FitnessPlanExercises
                    (DayGuidId, Section, ExerciseOrder,
                     Name, Sets, RepsOrTime, RPE,
                     Benefit, Instructions,
                     CreatedBy, CreatedAt,
                     Rest, Equipment, EstimatedCalories, SafetyCue)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, GETDATE(), ?, ?, ?, ?)
                """, exercises_to_insert)
        
        conn.commit()

        return {
            "status": 200,
            "message": "Weekly workout plan generated and saved successfully",
            "data": {
                "plan_guid": plan_guid,
                "start_date": str(start_date),
                "end_date": str(end_date)
            }
        }

    except Exception as e:
        if conn:
            conn.rollback()
        raise e

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

def insert_vital_if_allowed(user_id: str, vital_name: str) -> bool:
    """
    Inserts a new row into GetTime only if:
    - No existing row for UserId+VitalName exists
    - OR the latest row is older than 10 minutes from now.

    Returns True if inserted, False if skipped.
    """
    conn = None
    cursor = None
    try:
        conn = get_db_connection_dynamic(DYNAMIC_DB_NAME)
        cursor = conn.cursor()
 
        # Get the latest row for this UserId + VitalName
        cursor.execute("""
            SELECT TOP 1 [Time]
            FROM GetTime
            WHERE UserId = ? AND VitalName = ?
            ORDER BY LogId DESC
        """, (user_id, vital_name))
        row = cursor.fetchone()
 
        now = datetime.now()
 
        if row:
            last_time = row[0]
            if now - last_time < timedelta(minutes=10):
                print(f"⏳ Skipping insert: last entry was at {last_time}")
                return False
 
        # Insert new row with current timestamp
        cursor.execute("""
            INSERT INTO GetTime (UserId, VitalName, [Time])
            VALUES (?, ?, ?)
        """, (user_id, vital_name, now))
        conn.commit()
        print(f"✅ Inserted: UserId={user_id}, VitalName={vital_name}, Time={now}")
        return True
 
    except Exception as e:
        print(f"❌ Error inserting into GetTime: {e}")
        return False
 
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close() 

def normalize_vital_name(vital_name: str) -> str:
    return (
        vital_name
        .strip()
        .lower()
        .replace(" ", "")
        .replace("_", "")
        .replace("-", "")
    )

def get_all_chat_titles(
    database_name: str,
    user_id: int,
    page: int,
    limit: int,
    search: str | None = None
):
    offset = (page - 1) * limit
    search = search.strip() if search else None

    conn = get_db_connection_dynamic(database_name)
    cursor = conn.cursor()

    # ---------- COUNT ----------
    if search:
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM chat_title
            WHERE user_id = ?
              AND title LIKE ?
            """,
            (user_id, f"%{search}%")
        )
    else:
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM chat_title
            WHERE user_id = ?
            """,
            (user_id,)
        )

    total_records = cursor.fetchone()[0]

    # ---------- DATA ----------
    if search:
        cursor.execute(
            """
            SELECT id, title, date, status
            FROM chat_title
            WHERE user_id = ?
              AND title LIKE ?
            ORDER BY date DESC
            OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
            """,
            (user_id, f"%{search}%", offset, limit)
        )
    else:
        cursor.execute(
            """
            SELECT id, title, date, status
            FROM chat_title
            WHERE user_id = ?
            ORDER BY date DESC
            OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
            """,
            (user_id, offset, limit)
        )

    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    data = [
        {
            "id": r[0],
            "title": r[1],
            "date": r[2],
            "status": r[3],
        }
        for r in rows
    ]

    pagination = {
        "total_records": total_records,
        "current_page": page,
        "per_page": limit,
        "total_pages": (total_records + limit - 1) // limit,
        "has_next": offset + limit < total_records,
        "has_previous": offset > 0,
    }

    return data, pagination

def save_tdee_goal(database_name: str, user_id: str, tdee: float):
    conn = get_db_connection_dynamic(database_name)
    cursor = conn.cursor()

    # Fetch latest calorie goal
    cursor.execute("""
        SELECT TOP 1 GoalId, EndDate, Status
        FROM CCMGoals
        WHERE UserId = ? AND GoalType = 'GOAL_CALORIES' ORDER BY CreatedDate DESC
    """, user_id)

    row = cursor.fetchone()

    # ---------------- CASE 1 ----------------
    # EndDate NULL + Active → UPDATE
    if row and row[1] is None and row[2] == 'ACTIVE':
        cursor.execute("""
            UPDATE CCMGoals
            SET TargetValue = ?, StartDate = GETDATE(), UpdatedBy = 'AI', UpdatedDate = GETDATE() WHERE GoalId = ?
        """, (tdee, row[0]))

    else:
        # ---------------- CASE 2 ----------------
        # EndDate passed + Active → Inactivate old
        if row and row[1] is not None and row[2] == 'ACTIVE':
            cursor.execute("""
                UPDATE CCMGoals
                SET Status = 'Inactive', UpdatedBy = 'AI', UpdatedDate = GETDATE() WHERE GoalId = ?
            """, row[0])

        # ---------------- CASE 3 ----------------
        # EndDate NULL + Inactive OR no record → INSERT new
        cursor.execute("""
            INSERT INTO CCMGoals (
                UserId,ProgramId,GoalType,Metric,TargetMin,TargetMax,TargetValue,Frequency,Unit,StartDate,EndDate,
                Status,ApprovalStatus,ApprovedBy,ApprovedDate,CreatedBy,CreatedDate,UpdatedBy,UpdatedDate
            )
            VALUES (
                ?, NULL,'GOAL_CALORIES','calories', NULL, NULL, ?, 'DAILY', 'kcal', GETDATE(), NULL,
                'ACTIVE', 'APPROVED', 'AI', GETDATE(), 'AI', GETDATE(), 'AI', GETDATE()
            )
        """, (user_id, tdee))

    conn.commit()
    cursor.close()
    conn.close()

def calculate_comprehensive_tdee(
    gender: str,
    age: float,
    weight_kg: float,
    height_cm: float,
    activity_level: str,
    goal_type: str = "Weight Maintenance",
    waist_circumference_cm: float = None,
    vitals_numeric: dict = None,
) -> float:

    # 2. Base TDEE Calculation (Mifflin-St Jeor)
    gender = gender.lower()
    if gender == 'male':
        bmr = (10 * weight_kg) + (6.25 * height_cm) - (5 * age) + 5
    elif gender == 'female':
        bmr = (10 * weight_kg) + (6.25 * height_cm) - (5 * age) - 161
    else:
        bmr = ((10 * weight_kg) + (6.25 * height_cm) - (5 * age) + 5 + 
               (10 * weight_kg) + (6.25 * height_cm) - (5 * age) - 161) / 2

    activity_factors = {
        "sedentary": 1.2,
        "somewhat active": 1.375,
        "moderately active": 1.55,
        "very active": 1.725
    }
    # Base TDEE
    calculated_tdee = bmr * activity_factors.get(activity_level.lower(), 1.2)

    if goal_type in ["Weight Maintenance", "No Weight Goal", "UNKNOWN", None]:
        
        # BMI Reduction
        bmi = weight_kg / ((height_cm / 100) ** 2)
        bmi_reduction = 0
        if 24.9 <= bmi < 29.9: bmi_reduction = 200
        elif 30 <= bmi < 34.9: bmi_reduction = 300
        elif bmi >= 35: bmi_reduction = 500
        calculated_tdee -= bmi_reduction

        # Waist Reduction
        waist_reduction = 0
        if waist_circumference_cm is not None:
            if gender == 'female':
                if 80 <= waist_circumference_cm < 88: waist_reduction = max(waist_reduction, 100)
                elif 88 <= waist_circumference_cm <= 100: waist_reduction = max(waist_reduction, 200)
                elif waist_circumference_cm > 100: waist_reduction = max(waist_reduction, 300)
            elif gender == 'male':
                if 90 <= waist_circumference_cm < 100: waist_reduction = max(waist_reduction, 100)
                elif 100 <= waist_circumference_cm < 108: waist_reduction = max(waist_reduction, 200)
                elif waist_circumference_cm >= 108: waist_reduction = max(waist_reduction, 300)
        
        if waist_reduction > 0:
            calculated_tdee -= waist_reduction

        # Vitals Adjustment
        vitals_numeric = vitals_numeric or {}
        has_high_vitals, has_low_vitals = False, False
        
        bp_val = vitals_numeric.get("Blood Pressure")
        if isinstance(bp_val, dict) and (bp_val.get("systolic", 0) > 130 or bp_val.get("diastolic", 0) > 85): has_high_vitals = True
        
        glucose = vitals_numeric.get("Blood Glucose")
        if glucose:
            if glucose > 100: has_high_vitals = True
            if glucose < 70: has_low_vitals = True
            
        body_fat = vitals_numeric.get("Body Fat %")
        if body_fat is not None:
            if (gender == 'male' and body_fat > 25) or (gender == 'female' and body_fat > 32): has_high_vitals = True
            if (gender == 'male' and body_fat < 6) or (gender == 'female' and body_fat < 14): has_low_vitals = True

        vitals_adjustment = 0
        if has_high_vitals and not has_low_vitals: vitals_adjustment = -100
        elif has_low_vitals and not has_high_vitals: vitals_adjustment = 100
        calculated_tdee += vitals_adjustment

        min_threshold_women, min_threshold_men = 1200, 1500 
        if vitals_adjustment < 0:
            if (gender == 'female' and calculated_tdee < min_threshold_women) and abs(vitals_adjustment) > 50:
                 calculated_tdee += (abs(vitals_adjustment) - 50)
            elif (gender == 'male' and calculated_tdee < min_threshold_men) and abs(vitals_adjustment) > 50:
                 calculated_tdee += (abs(vitals_adjustment) - 50)

    # B) LOSE WEIGHT 
    elif goal_type == "Weight Loss":
        calculated_tdee -= 400 

    # C) GAIN WEIGHT (Standard Surplus)
    elif goal_type == "Weight Gain":
        calculated_tdee += 400

    # 4. Final Hard Floor (Universal Safety)
    if gender == 'female':
        calculated_tdee = max(calculated_tdee, 1200)
    elif gender == 'male':
        calculated_tdee = max(calculated_tdee, 1500)
    
    return float(calculated_tdee)

notification_messages = [
    "Hi {PatientFirstName}, it’s been {Duration} days since your visit with Dr. {DoctorName}. Book a follow-up?",
    "{PatientFirstName}, your last {SessionType} with Dr. {DoctorName} was {Duration} days ago. Book again?",
    "Hey {PatientFirstName}! Time for a follow-up with Dr. {DoctorName}? It’s been {Duration} days.",
    "{PatientFirstName}, it’s been a while since your session with Dr. {DoctorName}. Schedule one now.",
    "Hi {PatientFirstName}, ready for your next consultation with Dr. {DoctorName}?",
    "{PatientFirstName}, your care continues. Book another session with Dr. {DoctorName}.",
    "Hey {PatientFirstName}, follow-ups help! Book your next session with Dr. {DoctorName}.",
    "{PatientFirstName}, reconnect with Dr. {DoctorName} and continue your treatment.",
]

unregistered_patient_messages = [
    "Hi {PatientFirstName}! Book your FREE doctor consultation today.",
    "{PatientFirstName}, your FREE consultation is waiting. Get started now!",
    "New here, {PatientFirstName}? Book a FREE doctor consultation.",
    "Hey {PatientFirstName}! Talk to a doctor with a FREE consultation.",
    "{PatientFirstName}, start your care with a FREE consultation today.",
    "FREE doctor consultation for you, {PatientFirstName}. Book now!",
    "{PatientFirstName}, connect with expert doctors. First session is FREE.",
    "Hi {PatientFirstName}! Your FREE medical consultation is just a tap away.",
]

def all_patients_recent_session_lt_7_days(database_name: str) -> Iterator[Dict[str, Any]]:
    """
    Streams ACTIVE patients whose latest session is >= 7 days ago.
    Also enriches with DoctorName from master DB.
    NOTE: function name kept for compatibility, but logic is now >= 7 days.
    """
    conn = None
    cursor = None
    master_conn = None
    master_cursor = None

    DOCTORS_FROM_MASTER = """
    SELECT UserId, EmployeeFirstName
    FROM dbo.Employee;
    """

    # FIXED: Changed < 7 to >= 7 to match weekly "been a while" intent + min_gap_days=7
    PATIENTS_WITH_SESSION_GTE_7_DAYS = """
    WITH LatestSession AS (
        SELECT
            PatientId,
            ExpertId,
            EndDateTimeUTC,
            ROW_NUMBER() OVER (PARTITION BY PatientId ORDER BY EndDateTimeUTC DESC) AS rn
        FROM dbo.Sessions
    )
    SELECT
        P.PatientUserId,
        P.PatientFirstName,
        P.PatientMiddleName,
        P.PatientLastName,
        P.TimeZone,
        LS.ExpertId,
        LS.EndDateTimeUTC
    FROM dbo.Patient AS P
    INNER JOIN LatestSession AS LS
        ON P.PatientUserId = LS.PatientId
        AND LS.rn = 1
    WHERE
        P.IsActive = 1
        AND LS.EndDateTimeUTC IS NOT NULL
        AND DATEDIFF(DAY, LS.EndDateTimeUTC, SYSUTCDATETIME()) >= 7;
    """

    try:
        conn = get_db_connection_dynamic(database_name)

        # Load doctor mapping once from master
        master_conn = get_db_connection_dynamic({MASTER_TABLE_DB_NAME})
        master_cursor = master_conn.cursor()
        master_cursor.execute(DOCTORS_FROM_MASTER)
        doctors = {row[0]: row[1] for row in master_cursor.fetchall()}

        cursor = conn.cursor()
        cursor.execute(PATIENTS_WITH_SESSION_GTE_7_DAYS)

        cols = [c[0] for c in cursor.description]

        for row in cursor:
            data = dict(zip(cols, row))
            expert_id = data.get("ExpertId")
            doctor_name = doctors.get(expert_id)

            end_dt = data.get("EndDateTimeUTC")
            has_session = end_dt is not None

            yield {
                "PatientUserId": data.get("PatientUserId"),
                "TimeZone": data.get("TimeZone"),
                "PatientFirstName": data.get("PatientFirstName"),
                "EndDateTimeUTC": end_dt,
                "ExpertId": expert_id,
                "DoctorName": doctor_name,
                "Expertise": None,
                "SessionStatus": "Has Sessions" if has_session else "No Sessions",
            }

    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        if master_cursor is not None:
            try:
                master_cursor.close()
            except Exception:
                pass
        if master_conn is not None:
            try:
                master_conn.close()
            except Exception:
                pass

def fetch_vitals(user_id: str, cursor: str, current_date: str):

    if cursor is None:
        con = get_db_connection_dynamic(DYNAMIC_DB_NAME)
        cursor = con.cursor()

    cursor.execute("EXEC [Member].[GetUserNutritionDashboardVitals] @UserId = ?, @CurrentDate = ?, @TimeZoneOffset = ?, @OffsetMinutes = ?", user_id, current_date, 330, -330)
    vitals = dict(zip([col[0] for col in cursor.description], cursor.fetchone() or []))
    
    return vitals

def averagevitals(vitals_list):
    vitals_raw = vitals_list.get("Vitals", {})

    # Step 1: Decode JSON if needed
    if isinstance(vitals_raw, str):
        vitals = json.loads(vitals_raw)
    else:
        vitals = vitals_raw

    raw_values = {}

    for vital_name, vital_data in vitals.items():
        if not isinstance(vital_data, dict):
            continue

        for field in [
            "HeartRateValue",
            "BloodGlucoseValue",
            "Systolic",
            "Diastolic",
            "RespiratoryRateValue",
            "BloodOxygenValue",
            "BodyTempratureValue"
        ]:
            if field in vital_data and vital_data[field] is not None:
                try:
                    raw_values[field] = float(vital_data[field])
                except (ValueError, TypeError):
                    pass

    # Step 2: Human-readable output
    readable_vitals = {}

    if "HeartRateValue" in raw_values:
        readable_vitals["Heart Rate"] = raw_values["HeartRateValue"]

    if "BloodGlucoseValue" in raw_values:
        readable_vitals["Blood Glucose"] = raw_values["BloodGlucoseValue"]

    if "Systolic" in raw_values and "Diastolic" in raw_values:
        readable_vitals["Blood Pressure"] = {
            "systolic": raw_values["Systolic"],
            "diastolic": raw_values["Diastolic"]
        }

    if "RespiratoryRateValue" in raw_values:
        readable_vitals["Respiration Rate"] = raw_values["RespiratoryRateValue"]

    if "BloodOxygenValue" in raw_values:
        readable_vitals["Blood Oxygen Saturation"] = raw_values["BloodOxygenValue"]

    if "BodyTempratureValue" in raw_values:
        readable_vitals["Body Temperature"] = raw_values["BodyTempratureValue"]

    return readable_vitals

def log_meal_plan_user_result(
    database_name: str,
    user_id: str,
    status_code: int,
    response_json: dict = None,
    error_message: str = None
):
    conn = get_db_connection_dynamic(database_name)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO MealPlanAutomationUserLog (
            UserId,
            GeneratedAt,
            StatusCode,
            ResponseJson,
            ErrorMessage
        )
        VALUES (?, GETUTCDATE(), ?, ?, ?)
    """, (
        user_id,
        status_code,
        json.dumps(response_json) if response_json else None,
        error_message
    ))

    conn.commit()
    cursor.close()
    conn.close()

def generate_personalized_notifications(
    patients_iter: Iterable[Dict[str, Any]],
    has_session_templates: list[str],
    no_session_templates: list[str],
    min_gap_days: int = 7,
    now_utc: datetime | None = None,
) -> Iterator[Dict[str, Any]]:
    """
    Yields one notification payload at a time (streaming).
    """
    if not has_session_templates or not no_session_templates:
        raise ValueError("Template lists must be non-empty lists of strings.")

    now_utc = now_utc or datetime.now(timezone.utc)
    tz_utc = timezone.utc

    for patient in patients_iter:
        patient_id = patient.get("PatientUserId")
        if patient_id is None:
            continue

        patient_id = str(patient_id)
        first_name = (patient.get("PatientFirstName") or "").strip() or "there"
        status = patient.get("SessionStatus") or "No Sessions"

        if status == "Has Sessions":
            session_date = patient.get("EndDateTimeUTC")
            if not session_date:
                continue

            if session_date.tzinfo is None:
                session_date = session_date.replace(tzinfo=tz_utc)

            duration_days = (now_utc - session_date).days

            # Safety net guard (kept)
            if duration_days < min_gap_days:
                continue

            values = defaultdict(
                str,
                {
                    "PatientFirstName": first_name,
                    "DoctorName": patient.get("DoctorName") or "our expert doctors",
                    "SessionType": patient.get("Expertise") or "consultation",
                    "SessionDate": session_date.strftime("%d %b %Y"),
                    "Duration": max(duration_days, 0),
                },
            )
            message = random.choice(has_session_templates).format_map(values)

        else:
            values = defaultdict(str, {"PatientFirstName": first_name})
            message = random.choice(no_session_templates).format_map(values)

        yield {
            "PatientUserId": patient_id,
            "PatientFirstName": first_name,
            "TimeZone": patient.get("TimeZone"),
            "SessionStatus": status,
            "NotificationMessage": message,
            "GeneratedAtUTC": now_utc.isoformat(),
        }

def automation_html_completed_email(data: dict) -> str:

    total_minutes = round(data['total_time_seconds'] / 60, 2)

    is_success = data["status"] == "COMPLETED"
    status_color = "#16a34a" if is_success else "#f59e0b"
    status_text = "SUCCESS ✔" if is_success else "PARTIAL ⚠"

    return f"""
    <html>
    <body style="margin:0;padding:0;background-color:#1e1e1e;font-family:Arial,sans-serif;color:#ffffff;">
        <div style="max-width:680px;margin:40px auto;background:#2b2b2b;border-radius:12px;overflow:hidden;">

            <!-- Header -->
            <div style="padding:32px 30px 16px 30px;text-align:center;">
                <h1 style="margin:0 0 6px 0;font-size:26px;font-weight:bold;color:#ffffff;">
                    Meal Plan Automation Report
                </h1>
                <p style="margin:0;color:#aaaaaa;font-size:14px;">
                    From Nouriq Team
                </p>
                <p style="margin:6px 0 0 0;color:#cccccc;font-size:13px;">
                    Environment: <strong>{ENVIRONMENT}</strong>
                </p>
            </div>

            <!-- Divider -->
            <hr style="border:none;border-top:1px solid #3d3d3d;margin:0 30px;" />

            <!-- Status Badge -->
            <div style="text-align:center;padding:24px 30px 8px 30px;">
                <span style="
                    display:inline-block;
                    background-color:{status_color};
                    color:#ffffff;
                    padding:10px 36px;
                    border-radius:30px;
                    font-size:16px;
                    font-weight:bold;
                    letter-spacing:0.5px;
                ">
                    {status_text}
                </span>
            </div>

            <!-- Execution Info -->
            <div style="padding:20px 30px 8px 30px;">
                <p style="margin:6px 0;font-size:14px;">
                    <strong>Execution Time:</strong> {data['total_time_seconds']} seconds
                </p>
                <p style="margin:6px 0;font-size:14px;">
                    <strong>Run Time (UTC):</strong> {data['finished_at']}
                </p>
            </div>

            <!-- Main Stats Table -->
            <div style="padding:16px 30px;">
                <table style="width:100%;border-collapse:collapse;border-radius:8px;overflow:hidden;">
                    <thead>
                        <tr style="background:#3a3a3a;">
                            <th style="padding:14px;text-align:center;font-size:14px;color:#ffffff;border-right:1px solid #4a4a4a;">Total Patients</th>
                            <th style="padding:14px;text-align:center;font-size:14px;color:#ffffff;border-right:1px solid #4a4a4a;">Success</th>
                            <th style="padding:14px;text-align:center;font-size:14px;color:#ffffff;">Failed</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr style="background:#2f2f2f;">
                            <td style="padding:16px;text-align:center;font-size:22px;font-weight:bold;color:#ffffff;border-right:1px solid #4a4a4a;">
                                {data['total_users']}
                            </td>
                            <td style="padding:16px;text-align:center;font-size:22px;font-weight:bold;color:#22c55e;border-right:1px solid #4a4a4a;">
                                {data['total_users'] - data['failed']}
                            </td>
                            <td style="padding:16px;text-align:center;font-size:22px;font-weight:bold;color:#ef4444;">
                                {data['failed']}
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>

            <!-- Divider -->
            <hr style="border:none;border-top:1px solid #3d3d3d;margin:0 30px;" />

            <!-- Detail Breakdown Table -->
            <div style="padding:20px 30px;">
                <table style="width:100%;border-collapse:collapse;">
                    <tbody>
                        <tr style="border-bottom:1px solid #3d3d3d;">
                            <td style="padding:12px 8px;font-size:14px;color:#aaaaaa;">Generated Today</td>
                            <td style="padding:12px 8px;font-size:14px;font-weight:bold;text-align:right;color:#ffffff;">
                                {data['generated_today']}
                            </td>
                        </tr>
                        <tr style="border-bottom:1px solid #3d3d3d;">
                            <td style="padding:12px 8px;font-size:14px;color:#aaaaaa;">Generated Tomorrow</td>
                            <td style="padding:12px 8px;font-size:14px;font-weight:bold;text-align:right;color:#ffffff;">
                                {data['generated_tomorrow']}
                            </td>
                        </tr>
                        <tr style="border-bottom:1px solid #3d3d3d;">
                            <td style="padding:12px 8px;font-size:14px;color:#aaaaaa;">Skipped</td>
                            <td style="padding:12px 8px;font-size:14px;font-weight:bold;text-align:right;color:#f59e0b;">
                                {data['skipped']}
                            </td>
                        </tr>
                        <tr style="border-bottom:1px solid #3d3d3d;">
                            <td style="padding:12px 8px;font-size:14px;color:#aaaaaa;">Failed</td>
                            <td style="padding:12px 8px;font-size:14px;font-weight:bold;text-align:right;color:#ef4444;">
                                {data['failed']}
                            </td>
                        </tr>
                        <tr>
                            <td style="padding:12px 8px;font-size:14px;color:#aaaaaa;">Total Time</td>
                            <td style="padding:12px 8px;font-size:14px;font-weight:bold;text-align:right;color:#ffffff;">
                                {data['total_time_seconds']} sec ({total_minutes} min)
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>

            <!-- Footer -->
            <div style="padding:16px 30px 28px 30px;text-align:center;">
                <p style="margin:0;font-size:12px;color:#666666;">
                    This is an automated report generated by the AI Meal Plan System.
                </p>
            </div>

        </div>
    </body>
    </html>
    """

# def send_email(subject: str, body: str, to_emails: list[str]):

#     if not to_emails:
#         return

#     msg = MIMEMultipart("alternative")
#     msg["From"] = EMAIL_FROM
#     msg["To"] = ", ".join(to_emails)
#     msg["Subject"] = subject

#     msg.attach(MIMEText("Meal Plan Automation Report", "plain"))
#     msg.attach(MIMEText(body, "html"))

#     try:
#         if USE_SSL:
#             server = smtplib.SMTP_SSL(SMTP_SERVER, EMAIL_PORT)
#         else:
#             server = smtplib.SMTP(SMTP_SERVER, EMAIL_PORT)
#             server.ehlo()
#             if USE_TLS:
#                 server.starttls()
#                 server.ehlo()

#         server.login(EMAIL_USERNAME, EMAIL_PASSWORD)
#         server.sendmail(EMAIL_FROM, to_emails, msg.as_string())
#         server.quit()

#         print("Email sent successfully ✅")

#     except Exception as e:
#         print("Email failed ❌")
#         print(str(e))

def send_email_celery_test(to_email: str, subject: str, body: str):
    try:
        msg = MIMEMultipart()
        msg["From"] = EMAIL_FROM
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))
        server = smtplib.SMTP(SMTP_SERVER, EMAIL_PORT)
        server.starttls()
        server.login(EMAIL_USERNAME, EMAIL_PASSWORD)
        server.sendmail(EMAIL_FROM, to_email, msg.as_string())
        server.quit()
        return True

    except Exception as e:
        print(f"Email send failed: {e}")
        return False


def send_email(subject: str, body: str, to_emails: list[str], attachment_path=None, attachment_filename="meal_plan_logs.xlsx"):

    if not to_emails:
        return

    msg = MIMEMultipart()
    msg["From"] = EMAIL_FROM
    msg["To"] = ", ".join(to_emails)
    msg["Subject"] = subject

    msg.attach(MIMEText(body, "html"))

    # Attach Excel file
    if attachment_path:
        with open(attachment_path, "rb") as f:
            part = MIMEApplication(f.read(), Name=attachment_filename)

        part["Content-Disposition"] = f'attachment; filename="{attachment_filename}"'
        msg.attach(part)

    try:
        if USE_SSL:
            server = smtplib.SMTP_SSL(SMTP_SERVER, EMAIL_PORT)
        else:
            server = smtplib.SMTP(SMTP_SERVER, EMAIL_PORT)
            server.starttls()

        server.login(EMAIL_USERNAME, EMAIL_PASSWORD)
        server.sendmail(EMAIL_FROM, to_emails, msg.as_string())
        server.quit()

        print("Email sent successfully ✅")

    except Exception as e:
        print("Email failed ❌")
        print(str(e))
 
     
def convert_json_to_profile_for_dashboard_message(json_profile: dict, diagnoses: list = None) -> dict:
    age = json_profile.get("Age")
    gender_map = {2: "male", 3: "female", 4: "other"}
    gender = gender_map.get(json_profile.get("Gender"), "unknown")
    height_cm = parse_height(json_profile.get("LatestHeight", "[]"))
    # height_value_str = json.loads(height_str)[0]["HeightMeasurement"]
    

    profile_data = {
        "age": age,
        "gender": gender,
        "height_cm": height_cm,
    }

    if diagnoses:
        profile_data["medical_conditions"] = diagnoses

    lifestyle_fields = [
        "total_cal",
        "consumed_cal",
        "burned_cal",
        "total_steps",
        "achieved_steps",
        "total_water",
        "consumed_water",
        "total_sleep",
        "consumed_sleep",
        "weight",
        "weight_goal",
        "activity_min",
        "activity_goal",
    ]
    
    for field in lifestyle_fields:
        if field in json_profile:
            profile_data[field] = json_profile[field]

    return profile_data

#dashboard messages helpers
def transform_lifestyle_data(data):
    response = {
        "total_cal": 0,
        "consumed_cal": 0,
        "burned_cal": 0,
        "total_steps": 0,
        "achieved_steps": 0,
        "total_water": 0,
        "consumed_water": 0,
        "total_sleep": 0,
        "consumed_sleep": 0,
        "weight": 0,
        "weight_goal": 0,
        "activity_min": 0,
        "activity_goal": 0
    }

    for item in data:
        metric = item.get("Metric")

        if metric == "calories":
            response["total_cal"] = item.get("GoalValue") or 0
            response["consumed_cal"] = item.get("ConsumedCalories") or 0
            response["burned_cal"] = item.get("TotalBurnedCalories") or 0

        elif metric == "steps":
            response["total_steps"] = item.get("GoalValue") or 0
            response["achieved_steps"] = item.get("ActualValue") or 0

        elif metric == "water":
            response["total_water"] = item.get("GoalValue") or 0
            response["consumed_water"] = item.get("ActualValue") or 0

        elif metric == "sleep":
            response["total_sleep"] = item.get("GoalValue") or 0
            response["consumed_sleep"] = item.get("ActualValue") or 0

        elif metric == "weight":
            response["weight"] = item.get("ActualValue") or 0
            response["weight_goal"] = item.get("GoalValue") or 0

        elif metric == "activity":
            response["activity_min"] = item.get("ActualValue") or 0
            response["activity_goal"] = item.get("GoalValue") or 0

    return response

from decimal import Decimal

def normalize_payload(payload: dict) -> dict:
    normalized = {}

    for k, v in payload.items():
        if isinstance(v, Decimal):
            normalized[k] = float(v)
        else:
            normalized[k] = v

    # Height conversion
    height_cm = float(normalized.get("height_cm", 0) or 0)
    normalized["height_inches"] = round(height_cm / 2.54, 2) if height_cm else 64

    # Weight conversion
    weight_kg = float(normalized.get("weight_kg", 0) or 0)
    normalized["weight"] = round(weight_kg * 2.20462, 2) if weight_kg else float(normalized.get("weight", 0) or 0)

    # BMI calculation ONCE
    weight = normalized["weight"]
    height_inches = normalized["height_inches"]

    if weight > 0 and height_inches > 0:
        normalized["bmi"] = round((weight / (height_inches ** 2)) * 703, 1)
    else:
        normalized["bmi"] = 0.0

    return normalized

def build_health_context(payload: dict) -> str:
    return (
        f"Medical condition: {payload.get('medical_condition', 'Diabetes Type 2')}, "
        f"Age: {payload.get('age', 65)} years, "
        f"Sex: {payload.get('gender', 'Female')}, "
        f"Weight: {payload.get('weight')} lbs, "
        f"BMI: {payload.get('bmi')}"
    )

async def generate_calories_ai(payload: dict) -> dict:

    consumed = payload.get("consumed_cal", 0)
    goal = payload.get("total_cal", 0)
    burned = payload.get("burned_cal", 0)
    steps = payload.get("achieved_steps", 0)
    activity = payload.get("activity_min", 0)

    bmi = payload["bmi"]
    health_context = build_health_context(payload)

    auto_burned = (steps * 0.04) + (activity * 4)
    total_burned = burned + auto_burned
    net_consumed = consumed - total_burned

    if net_consumed > goal:
        status = "Above Recommended"
    elif abs(net_consumed - goal) <= 50:
        status = "At Recommended"
    else:
        status = "Below Recommended"

    titles = generate_calorie_titles(status, goal)

    system_prompt = f"""
    You are a professional Medical AI Health Coach. Return JSON only.

    STATUS: {status}
    USER CONTEXT: {health_context}
    BMI: {bmi}

    FORMAT:
    {{
        "motivational_message": "Short quote",
        "log_message": "Dashboard message",
        "specific_metric_message": "Calories insight",
        "title1": "{titles['title1']}",
        "title2": "{titles['title2']}",
        "helps": ["10-15 words", "10-15 words", "10-15 words"],
        "uses": ["10-15 words", "10-15 words", "10-15 words"]
    }}
    """

    response = azure_client.chat.completions.create(
        model=AZURE_DEPLOYMENT_NAME,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "Provide calories coaching"}
        ],
        response_format={"type": "json_object"},
        temperature=0.1
    )

    return json.loads(response.choices[0].message.content)

#without build query
# async def generate_metric_ai(metric_key: str, payload: dict) -> Dict:

#     bmi = payload["bmi"]
#     health_context = build_health_context(payload)

#     user_query, status = build_user_query_for_metric(metric_key, payload, health_context)

#     metric_value_map = {
#         "weight": payload.get("weight_goal", 0),
#         "steps": payload.get("total_steps", 0),
#         "water": payload.get("total_water", 0),
#         "sleep": payload.get("total_sleep", 0),
#         "activity": payload.get("activity_goal", 0)
#     }

#     current_value = metric_value_map.get(metric_key, 0)

#     status = "At Recommended"  # Simplified

#     titles = generate_dynamic_titles(metric_key, status, current_value)

#     system_prompt = f"""
#     You are a professional Medical AI Health Coach. Return JSON only.

#     METRIC: {metric_key}
#     STATUS: {status}
#     USER CONTEXT: {health_context}
#     BMI: {bmi}

#     FORMAT:
#     {{
#         "metric_message": "Green box message",
#         "title1": "{titles['title1']}",
#         "title2": "{titles['title2']}",
#         "helps": ["10-15 words", "10-15 words", "10-15 words"],
#         "uses": ["10-15 words", "10-15 words", "10-15 words"]
#         {',"bmi_points":["10-15 words","10-15 words","10-15 words"]' if metric_key == "weight" else ""}
#     }}
#     """

#     response = azure_client.chat.completions.create(
#         model=AZURE_DEPLOYMENT_NAME,
#         messages=[
#             {"role": "system", "content": system_prompt},
#             {"role": "user", "content": f"Provide coaching for {metric_key}"}
#         ],
#         response_format={"type": "json_object"},
#         temperature=0.1
#     )

#     return json.loads(response.choices[0].message.content)

async def generate_metric_ai(metric_key: str, payload: dict) -> Dict:

    bmi = payload["bmi"]
    health_context = build_health_context(payload)

    # ✅ USE your existing function
    user_query, status = build_user_query_for_metric(
        metric_key,
        payload,
        health_context
    )

    metric_value_map = {
        "weight": payload.get("weight_goal", 0),
        "steps": payload.get("total_steps", 0),
        "water": payload.get("total_water", 0),
        "sleep": payload.get("total_sleep", 0),
        "activity": payload.get("activity_goal", 0)
    }

    current_value = metric_value_map.get(metric_key, 0)

    titles = generate_dynamic_titles(metric_key, status, current_value)

    system_prompt = f"""
    You are a professional Medical AI Health Coach. Return JSON only.

    METRIC: {metric_key}
    STATUS: {status}
    USER CONTEXT: {health_context}
    BMI: {bmi}

    Use EXACT titles:
    - title1: "{titles['title1']}"
    - title2: "{titles['title2']}"

    FORMAT:
    {{
        "metric_message": "Green box message",
        "title1": "{titles['title1']}",
        "title2": "{titles['title2']}",
        "helps": ["10-15 words", "10-15 words", "10-15 words"],
        "uses": ["10-15 words", "10-15 words", "10-15 words"]
        {',"bmi_points":["10-15 words","10-15 words","10-15 words"]' if metric_key == "weight" else ""}
    }}
    """

    response = azure_client.chat.completions.create(
        model=AZURE_DEPLOYMENT_NAME,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query}  # ✅ dynamic query used
        ],
        response_format={"type": "json_object"},
        temperature=0.1
    )

    return json.loads(response.choices[0].message.content)

def generate_calorie_titles(status: str, goal: float) -> Dict[str, str]:
    titles_map = {
        "At Recommended": {
            "title1": f"How staying near your {int(goal)} kcal goal supports your health:",
            "title2": "How the App balances intake and calorie burn:"
        },
        "Above Recommended": {
            "title1": f"How exceeding your {int(goal)} kcal goal affects your health:",
            "title2": f"How returning toward {int(goal)} kcal improves balance:"
        },
        "Below Recommended": {
            "title1": f"How staying below your {int(goal)} kcal goal impacts your health:",
            "title2": f"How reaching {int(goal)} kcal supports energy balance:"
        }
    }
    return titles_map.get(status)

def build_user_query_for_metric(metric_key: str, payload: dict, health_context: str) -> tuple:
    status = "At Recommended"

    current_map = {
        "weight": payload.get("weight_goal"),
        "steps": payload.get("total_steps"),
        "water": payload.get("total_water"),
        "sleep": payload.get("total_sleep"),
        "activity": payload.get("activity_goal")
    }

    current = current_map.get(metric_key, 0)

    if metric_key == "weight":
        user_query = (
            f"My weight goal is {current} lbs. Based on my condition ({health_context}), "
            f"provide 3 points explaining why this supports my health and 3 points on how the app uses it."
        )
    elif metric_key == "steps":
        user_query = (
            f"My steps goal is {current}. Based on {health_context}, "
            f"provide 3 health benefits and 3 points on how the app tracks this."
        )
    elif metric_key == "water":
        user_query = (
            f"My water intake goal is {current} oz. Based on {health_context}, "
            f"provide 3 hydration benefits and 3 app usage points."
        )
    elif metric_key == "sleep":
        user_query = (
            f"My sleep goal is {current} hours. Based on {health_context}, "
            f"provide 3 sleep benefits and 3 tracking points."
        )
    elif metric_key == "activity":
        user_query = (
            f"My activity goal is {current} minutes. Based on {health_context}, "
            f"provide 3 benefits and 3 tracking points."
        )
    else:
        user_query = "Provide health coaching."

    return user_query, status

def generate_dynamic_titles(metric: str, status: str, baseline_value: float) -> Dict[str, str]:
    titles_map = {
        "weight": {
            "At Recommended": {
                "title1": f"How the recommended weight of {baseline_value} lbs is helpful for you:",
                "title2": f"How the App uses this {baseline_value} lbs Goal:"
            }
        },
        "steps": {
            "At Recommended": {
                "title1": f"How the recommended steps goal of {int(baseline_value)} is helpful for you:",
                "title2": f"How the App tracks your {int(baseline_value)} steps goal:"
            }
        },
        "water": {
            "At Recommended": {
                "title1": f"How the recommended water intake of {int(baseline_value)} fl oz is helpful for you:",
                "title2": f"How the App supports your hydration goal:"
            }
        },
        "sleep": {
            "At Recommended": {
                "title1": f"How the recommended sleep duration of {baseline_value} hours supports your health:",
                "title2": f"How the App monitors healthy sleep patterns:"
            }
        },
        "activity": {
            "At Recommended": {
                "title1": f"How the recommended activity duration of {int(baseline_value)} minutes benefits your health:",
                "title2": f"How the App tracks balanced physical activity:"
            }
        }
    }
    return titles_map.get(metric, {}).get(status, {"title1": "Health Insight", "title2": "App Logic"})
   

def safe(val):
    try:
        return round(float(val), 2)
    except:
        return 0

#-------------------------------#
#Send next day meal plan through email
#-------------------------------#
import pandas as pd
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text
from urllib.parse import quote_plus
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from helpers.database import SERVER, DYNAMIC_USERNAME, DYNAMIC_PASSWORD
import html
def get_mealplan_with_diagnosis_email(database_name: str, chunksize: int = 50000) -> pd.DataFrame:
    """
    Combines meal plan, diagnosis, and patient goals.

    - Meal plan rows are preserved (duplicates allowed)
    - Diagnosis and Goals are aggregated per user
    - Both are merged and duplicated across meal rows
    """

    encoded_password = quote_plus(DYNAMIC_PASSWORD)

    connection_string_meal = (
        f"mssql+pyodbc://{DYNAMIC_USERNAME}:{encoded_password}"
        f"@{SERVER}/{database_name}"
        "?driver=ODBC+Driver+18+for+SQL+Server"
        "&Encrypt=yes"
        "&TrustServerCertificate=yes"
    )

    connection_string_diag = (
        f"mssql+pyodbc://{DYNAMIC_USERNAME}:{encoded_password}"
        f"@{SERVER}/FriskaAiCCM_HFWL"
        "?driver=ODBC+Driver+18+for+SQL+Server"
        "&Encrypt=yes"
        "&TrustServerCertificate=yes"
    )

    engine_meal = create_engine(
        connection_string_meal,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True
    )

    engine_diag = create_engine(connection_string_diag, pool_pre_ping=True)

    # ----------- Step 1: Meal Data -----------
    today_date = (datetime.today() + timedelta(days=0)).date()
    #tommorow_date = (datetime.today() + timedelta(days=1)).date()
    #yesterday_date = (datetime.today() - timedelta(days=1)).date()

    meal_query = text("""
        SELECT
            PatientId,
            PlanName,
            PlanType,
            MealType,
            FoodItems,
            PortionWeight,
            Protein,
            Carbohydrates,
            Fat,
            Fiber,
            Sodium,
            Iodine,
            Sugar,
            Cholesterol,
            Calories,
            NutritionalInfo,
            MealTime,
            MealDate,
            PreprationInstructions,
            MealDateOnly
        FROM MealPlanDetails
        WHERE MealDateOnly = :meal_date
    """)

    meal_chunks = []

    with engine_meal.connect() as conn:
        for chunk in pd.read_sql_query(
            meal_query,
            conn,
            params={"meal_date": today_date},
            chunksize=chunksize
        ):
            meal_chunks.append(chunk)

    meal_df = pd.concat(meal_chunks, ignore_index=True) if meal_chunks else pd.DataFrame()

    if meal_df.empty:
        return meal_df

    # ----------- Step 2: Diagnosis Data -----------
    diag_query = text(f"""
        SELECT DISTINCT
            hd.MedicalConditionName AS Disease,
            p.PatientFirstName AS PatientName,
            p.PatientUserId AS UserId,
            p.Email
        FROM FriskaAiCCM_HFWL.dbo.PatientDiagnosis pd
        JOIN FriskaAiCCM_HFWL.dbo.Patient p
            ON p.PatientUserId = pd.PatientId
        JOIN {MASTER_TABLE_DB_NAME}.Master.HealthDiagnoses hd
            ON pd.ConditionCode = hd.ConditionCode
        WHERE pd.IsActive = 1
        AND pd.Deleted = 0
    """)

    with engine_diag.connect() as conn:
        diag_df = pd.read_sql_query(diag_query, conn)

    diag_df = (
        diag_df.groupby(["UserId", "PatientName", "Email"], as_index=False)
        .agg({
            "Disease": lambda x: list(dict.fromkeys(x))  # preserves order
        })
    )

    # ----------- Step 3: Goals Data (NEW) -----------
    goal_query = text("""
        SELECT DISTINCT 
            g.PatientId AS UserId,
            go.Focus AS Goal
        FROM PatientGoalOption AS go
        JOIN PatientGoals AS g
            ON go.PatientGoalId = g.PatientGoalCode
    """)

    with engine_diag.connect() as conn:
        goal_df = pd.read_sql_query(goal_query, conn)

    # Aggregate goals per user
    goal_df = (
        goal_df.groupby("UserId", as_index=False)
        .agg({
            "Goal": lambda x: list(dict.fromkeys(x))
        })
    )

    # ----------- Step 4: Merge Diagnosis -----------
    final_df = meal_df.merge(
        diag_df,
        how="left",
        left_on="PatientId",
        right_on="UserId"
    )

    # ----------- Step 5: Merge Goals (NEW) -----------
    final_df = final_df.merge(
        goal_df,
        how="left",
        left_on="PatientId",
        right_on="UserId",
        suffixes=("", "_goal")
    )

    # ----------- Step 6: Cleanup -----------
    final_df.drop(columns=["UserId", "UserId_goal"], inplace=True, errors="ignore")

    # Fill missing values
    final_df["Disease"] = final_df["Disease"].apply(
        lambda x: x if isinstance(x, list) else []
    )

    final_df["Goal"] = final_df["Goal"].apply(
        lambda x: x if isinstance(x, list) else []
    )

    return final_df

def generate_mealplan_email_html(recipient_name, df):
    """
    Generates a personalized HTML meal plan email.
    Note: 'friskalogo.png' is referenced in the HTML. For this to show in emails,
    ensure your sending script attaches the image with Content-ID: logo_image
    or replace the src with a public URL.
    """
    def safe_get_val(df, col, default=None):
        if col in df.columns and not df[col].empty:
            return df[col].dropna().tolist()
        return default if default is not None else []

    def clean_list_string(raw_val):
        # ✅ Step 1: Flatten nested lists
        def flatten(lst):
            for item in lst:
                if isinstance(item, (list, tuple, pd.Series)):
                    yield from flatten(item)
                else:
                    yield item

        # ✅ Handle list input
        if isinstance(raw_val, (list, tuple, pd.Series)):
            flat_vals = list(flatten(raw_val))

            items = [
                str(i).strip()
                for i in flat_vals
                if i is not None and str(i).strip().lower() not in ("none", "nan", "", "null")
            ]

            items = list(dict.fromkeys(items))  # remove duplicates

            return ", ".join(items).replace("_", " ").title()

        # ✅ Handle scalar
        if raw_val is None or pd.isna(raw_val):
            return ""

        s = str(raw_val).strip()

        if s.lower() in ("none", "nan", "null", "", "['']", "[]", "[' ']", '[""]'):
            return ""

        return s

    # ----------------------------
    # 🛑 Empty DF guard
    # ----------------------------
    if df is None or (isinstance(df, pd.DataFrame) and df.empty):
        return "<p>No meal plan available for tomorrow.</p>"

    df = df.copy()

    # Ensure numeric columns exist and are valid numbers
    for col in ["Calories", "Protein", "Carbohydrates", "Fat", "Fiber", "PortionWeight"]:
        if col not in df.columns:
            df[col] = 0
        else:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    # ----------------------------
    # 👤 Recipient & Patient Info
    # ----------------------------
    display_name = html.escape(str(recipient_name)) if pd.notna(recipient_name) and str(recipient_name).strip() else "there"

    disease_raw = clean_list_string(safe_get_val(df, "Disease"))
    goal_raw = clean_list_string(safe_get_val(df, "Goal"))

    # Format for display
    disease_clean = html.escape(disease_raw.replace("_", " ").title()) if disease_raw else ""
    goal_clean = html.escape(goal_raw.replace("_", " ").title()) if goal_raw else ""

    # ----------------------------
    # 📊 Daily totals
    # ----------------------------
    totals = {
        "portionweight": round(df["PortionWeight"].sum(), 1),
        "cal": int(df["Calories"].sum()),
        "protein": round(df["Protein"].sum(), 1),
        "carb": round(df["Carbohydrates"].sum(), 1),
        "fat": round(df["Fat"].sum(), 1),
        "fiber": round(df["Fiber"].sum(), 1)
    }

    # ----------------------------
    # 🍽️ Meal section builder
    # ----------------------------
    df = df.sort_values("MealTime")
    if "FoodItems" in df.columns:
        df["FoodItems"] = df["FoodItems"].astype(str).str.replace("^-\\s*", "", regex=True)

    def build_meal_section(meal_type, meal_df):
        m_time = meal_df["MealTime"].iloc[0]
        time_str = m_time.strftime("%I:%M %p") if hasattr(m_time, "strftime") else str(m_time)

        row_list = []
        for _, row in meal_df.iterrows():
            row_list.append(f"""
                <tr>
                    <td style="padding:10px;border-bottom:1px solid #eee;">{html.escape(str(row.get("FoodItems", "")))}</td>
                    <td style="padding:10px;border-bottom:1px solid #eee;text-align:center;">{round(row["PortionWeight"], 1)}</td>
                    <td style="padding:10px;border-bottom:1px solid #eee;text-align:center;">{int(row["Calories"])}</td>
                    <td style="padding:10px;border-bottom:1px solid #eee;text-align:center;">{row["Protein"]}</td>
                    <td style="padding:10px;border-bottom:1px solid #eee;text-align:center;">{row["Carbohydrates"]}</td>
                    <td style="padding:10px;border-bottom:1px solid #eee;text-align:center;">{row["Fat"]}</td>
                    <td style="padding:10px;border-bottom:1px solid #eee;text-align:center;">{row["Fiber"]}</td>
                </tr>""")

        return f"""
        <tr>
            <td style="padding:20px 40px;">
                <h2 style="color:#2d3748;margin-bottom:5px;">{meal_type}</h2>
                <p style="color:#64748b;margin-top:0;">⏰ Recommended meal time: {time_str}</p>
                <div style="background:#f1f5f9; padding:12px; border-radius:6px; font-size:14px; margin-bottom:15px; border-left: 4px solid #3b82f6;">
                    <strong>Meal Impact:</strong> {round(meal_df["PortionWeight"].sum(), 1)} oz | {int(meal_df["Calories"].sum())} kcal | {round(meal_df["Protein"].sum(), 1)}g Protein | {round(meal_df["Carbohydrates"].sum(), 1)}g Carb | {round(meal_df["Fat"].sum(), 1)}g Fat | {round(meal_df["Fiber"].sum(), 1)}g Fibre
                </div>
                <table width="100%" style="border-collapse:collapse;font-size:14px;">
                    <thead>
                        <tr style="background:#f8fafc;color:#475569;">
                        <th style="padding:10px;text-align:left;border-bottom:2px solid #e2e8f0;">Menu</th>

                        <th style="padding:10px;text-align:center;border-bottom:2px solid #e2e8f0;">
                            Portion
<span style="font-size:12px; color:#94a3b8;">(oz)</span>
                        </th>

                        <th style="padding:10px;text-align:center;border-bottom:2px solid #e2e8f0;">
                            Cals
<span style="font-size:12px; color:#94a3b8;">(kcal)</span>
                        </th>

                        <th style="padding:10px;text-align:center;border-bottom:2px solid #e2e8f0;">
                            Protein
<span style="font-size:12px; color:#94a3b8;">(g)</span>
                        </th>

                        <th style="padding:10px;text-align:center;border-bottom:2px solid #e2e8f0;">
                            Carb
<span style="font-size:12px; color:#94a3b8;">(g)</span>
                        </th>

                        <th style="padding:10px;text-align:center;border-bottom:2px solid #e2e8f0;">
                            Fat
<span style="font-size:12px; color:#94a3b8;">(g)</span>
                        </th>

                        <th style="padding:10px;text-align:center;border-bottom:2px solid #e2e8f0;">
                            Fiber
<span style="font-size:12px; color:#94a3b8;">(g)</span>
                        </th>
                    </tr>
                    </thead>
                    <tbody>{"".join(row_list)}</tbody>
                </table>
            </td>
        </tr>"""

    meal_groups = df.groupby("MealType", sort=False)
    meal_sections_html = "".join(
    build_meal_section(
            m_type.replace("MorningSnack", "Morning Snack")
                .replace("EveningSnack", "Evening Snack"),
            m_df
        )
        for m_type, m_df in meal_groups
    )

    # ----------------------------
    # 📧 Dynamic Engaging Intro
    # ----------------------------
    if disease_clean and goal_clean:
        intro_text = f"At <strong>FriskaAI CCM</strong>, your nutrition plan is tailored to support the management of <strong>{disease_clean}</strong> while aligning with your goal of <strong>{goal_clean}</strong>. This plan is designed to provide balanced, clinically informed nutrition for the day ahead."

    elif disease_clean:
        intro_text = f"At <strong>FriskaAI CCM</strong>, this nutrition plan is designed to support the management of <strong>{disease_clean}</strong>. It focuses on delivering balanced and appropriate nutrients to promote stability and overall well-being."

    elif goal_clean:
        intro_text = f"At <strong>FriskaAI CCM</strong>, this plan is structured to support your goal of <strong>{goal_clean}</strong>. It provides a balanced nutritional approach to help you stay consistent and on track."

    else:
        intro_text = "At <strong>FriskaAI CCM</strong>, your personalized nutrition plan is designed to support your overall health and well-being. It provides balanced, structured meals to help you maintain consistency and feel your best."

    base_font = "font-family:Segoe UI, Tahoma, Geneva, Verdana, sans-serif;"
    # ----------------------------
    # 📧 Template Assembly
    # ----------------------------
    return f"""
        <!DOCTYPE html>
        <html>
        <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Your FriskaAI CCM Meal Plan</title>

        <style>
        @media only screen and (max-width:600px) {{
            .container {{
                width: 100% !important;
            }}
            .padding {{
                padding: 16px !important;
            }}
            .small-padding {{
                padding: 12px !important;
            }}
            .text-center {{
                text-align: center !important;
            }}
            .stack {{
                display: block !important;
                width: 100% !important;
            }}
        }}
        </style>

        </head>

        <body style="margin:0; padding:0; background-color:#f5f5f5; font-family:Arial, sans-serif;">

        <table width="100%" cellpadding="0" cellspacing="0" bgcolor="#f5f5f5">
        <tr>
        <td align="center">

        <!-- MAIN CONTAINER -->
        <table class="container" width="100%" style="max-width:650px; margin:0 auto; background:#ffffff; border-radius:10px; overflow:hidden;">

        <!-- LOGO -->
        <tr>
        <td align="center"
            style="background-color:#ffffff; padding:20px 0;">

            <img src="cid:logo_image" alt="FriskaAI CCM Logo"
                width="300"
                style="width:300px; height:auto; display:block; border:0;">

        </td>
        </tr>

        <!-- GREETING -->
        <tr>
        <td class="padding" style="padding:20px;">
        <h2 style="margin:0 0 10px; color:#1e293b;">Hi {display_name},</h2>
        <p style="margin:0; color:#475569; line-height:1.6;">
        {intro_text}
        </p>
        </td>
        </tr>

        <!-- SUMMARY -->
        <tr>
        <td class="padding" style="padding:20px;">
        <table width="100%" cellpadding="0" cellspacing="0" style="background:#1e293b; color:#ffffff; border-radius:12px;">
        <tr>
        <td class="small-padding" style="padding:20px; text-align:center;">

        <!-- 🔹 Heading -->
        <p style="margin:0 0 15px 0; font-size:14px; letter-spacing:1px; color:#94a3b8; font-weight:600;">
            DAILY NUTRITION SUMMARY
        </p>

        <!-- 🔹 Metrics Row -->
        <table width="100%" cellpadding="0" cellspacing="0" style="table-layout:fixed;">
        <tr>

        <td width="20%" align="center" style="padding:10px;">
        <strong style="font-size:17px; display:block;">{totals.get('cal', 0)}kcal</strong>
        <small style="color:#cbd5e1;">CALORIES</small>
        </td>

        <td width="20%" align="center" style="padding:10px;">
        <strong style="font-size:17px; display:block;">{totals.get('protein', 0)}g</strong>
        <small style="color:#cbd5e1;">PROTEIN</small>
        </td>

        <td width="20%" align="center" style="padding:10px;">
        <strong style="font-size:17px; display:block;">{totals.get('carb', 0)}g</strong>
        <small style="color:#cbd5e1;">CARB</small>
        </td>

        <td width="20%" align="center" style="padding:10px;">
        <strong style="font-size:17px; display:block;">{totals.get('fat', 0)}g</strong>
        <small style="color:#cbd5e1;">FAT</small>
        </td>

        <td width="20%" align="center" style="padding:10px;">
        <strong style="font-size:17px; display:block;">{totals.get('fiber', 0)}g</strong>
        <small style="color:#cbd5e1;">FIBER</small>
        </td>

        </tr>
        </table>

        </td>
        </tr>
        </table>
        </td>
        </tr>

        <!-- MEALS -->
        {meal_sections_html}

        <!-- QUOTE -->
        <tr>
        <td class="padding text-center" style="padding:20px;">
        <p style="font-style:italic; color:#64748b; text-align:center; margin:0;">
        "Small, consistent choices today lead to big changes tomorrow."
        </p>
        </td>
        </tr>
        <!-- Footer -->
        <tr>
        <td style="background:#0f172a;color:#cbd5e1;padding:28px 0;text-align:center;font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:1.6;">

            <!-- Inner Container (removes left gap issue) -->
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:0 auto;">
            <tr>
            <td style="padding:0 20px;text-align:center;">

                <!-- Heading -->
                <h3 style="margin:0 0 10px 0;color:#ffffff;font-size:20px;">
                    Committed to Your Health Journey
                </h3>

                <!-- Subtext -->
                <p style="margin:0 0 20px 0;">
                    Thank you for trusting FriskaAi CCM as your personalized health partner.
                </p>

                <!-- Social Icons -->
                <div style="margin-bottom:20px;">
                    <a href="https://www.youtube.com/@Friska_Ai" target="_blank" style="margin:0 6px;display:inline-block;">
                        <img src="https://cdn-icons-png.flaticon.com/512/174/174883.png" width="28" alt="YouTube" style="display:block;border:0;">
                    </a>

                    <a href="https://www.linkedin.com/company/friskaai" target="_blank" style="margin:0 6px;display:inline-block;">
                        <img src="https://cdn-icons-png.flaticon.com/512/174/174857.png" width="28" alt="LinkedIn" style="display:block;border:0;">
                    </a>

                    <a href="https://x.com/FriskaAi_" target="_blank" rel="noopener noreferrer" style="margin:0 6px;display:inline-block;">
                        <img src="https://cdn-icons-png.flaticon.com/512/5969/5969020.png" width="28" alt="X" style="display:block;border:0;">
                    </a>
                </div>

                <!-- Divider -->
                <div style="border-top:1px solid #334155;padding-top:18px;font-size:14px;color:#94a3b8;">

                    <!-- Links -->
                    <p style="margin:6px 0;">
                        For Privacy Policy, Terms & Conditions, or other information please visit
                        <a href="https://www.friska.ai"
                        style="color:#94a3b8;text-decoration:underline;word-break:break-word;">
                        https://www.friska.ai
                        </a>
                    </p>

                    <!-- Contact Title -->
                    <p style="margin:12px 0 6px 0;font-weight:600;">
                        Contact Us
                    </p>

                    <!-- Contact Row (same line) -->
                    <table role="presentation" cellpadding="0" cellspacing="0" align="center" style="margin:6px auto 0 auto;">
                    <tr>
                        <td style="padding:0 12px;color:#94a3b8;white-space:nowrap;font-size:14px;">
                            ✉️
                            <a href="mailto:q@friska.ai"
                            style="color:#94a3b8;text-decoration:none;">
                            q@friska.ai
                            </a>
                        </td>

                        <td style="padding:0 12px;color:#94a3b8;white-space:nowrap;font-size:14px;">
                            📞
                            <a href="tel:7032021655"
                            style="color:#94a3b8;text-decoration:none;">
                            703-202-1655
                            </a>
                        </td>
                    </tr>
                    </table>

                    <!-- Footer Note -->
                    <p style="margin:14px 0 0 0;color:#64748b;font-size:13px;">
                        © HFWL Company. Empowering your health journey with clinical precision.
                    </p>

                </div>

            </td>
            </tr>
            </table>

        </td>
        </tr>
        </table>
        </td>
        </tr>
        </table>

        </body>
        </html>
        """

def send_mealplan_email(
    recipient_name,
    recipient_email,
    sender_email,
    sender_password,
    html_content
):
    """Send meal plan email with embedded logo (CID)."""

    # recipient_name = patient_df.get("PatientName", pd.Series(["there"])).iloc[0]
    # recipient_email = patient_df.get("Email", pd.Series([None])).iloc[0]
    # patient_id = patient_df["PatientId"].iloc[0]

    # TEMP override (remove in production)
    # recipient_email = "jagadesh.pamanji@nouriq.ai"

    recipient_name = str(recipient_name).strip() if pd.notna(recipient_name) else "there"
    if not recipient_name:
        recipient_name = "there"

    try:
        # Generate HTML
        # html_content = generate_mealplan_email_html(recipient_name, patient_df)

        # ----------------------------
        # ✅ Create ROOT message (related)
        # ----------------------------
        msg = MIMEMultipart("related")
        msg["Subject"] = "Your Meal Plan for Tomorrow 🍽️ (FriskaAi CMM)"
        msg["From"] = sender_email
        msg["To"] = recipient_email

        # ----------------------------
        # ✅ Create alternative part
        # ----------------------------
        alt_part = MIMEMultipart("alternative")
        msg.attach(alt_part)

        alt_part.attach(MIMEText(html_content, "html"))

        # ----------------------------
        # ✅ Attach logo image (CID)
        # ----------------------------
        try:
            with open("static/friskalogo.png", "rb") as f:
                img = MIMEImage(f.read())

            img.add_header("Content-ID", "<logo_image>")  # MUST match HTML
            img.add_header("Content-Disposition", "inline", filename="static/friskalogo.png")

            msg.attach(img)

        except FileNotFoundError:
            print("⚠️ Logo image not found: static/friskalogo.png")

        # ----------------------------
        # 📡 Send Email
        # ----------------------------
        with smtplib.SMTP("smtp.office365.com", 587) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.send_message(msg)

        print(f"✅ Email sent to {recipient_email}")
        return "Sent"

    except Exception as e:
        print(f"❌ Failed to send email to {recipient_email}: {e}")
        return "Failed"

def already_sent_email(user_id, email_type_id):
    conn = None
    cursor = None

    try:
        conn = get_db_connection_dynamic(DYNAMIC_DB_NAME)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT 1
            FROM EmailLogs
            WHERE UserId = ?
            AND EmailTypeId = ?
            AND CAST(CreatedAt AS DATE) = CAST(GETDATE() AS DATE)
            AND Status = 'Sent'
        """, (user_id, email_type_id))

        return cursor.fetchone() is not None

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


# -------------------------------------------------------
# Sending the Daily Health Tip
# -------------------------------------------------------
import asyncio
topic_lock = asyncio.Lock()

async def get_next_topic():

    async with topic_lock:
        
        conn = get_db_connection_dynamic(DYNAMIC_DB_NAME)
        cursor = conn.cursor()

        # Get first unprocessed topic
        cursor.execute("""
            SELECT TOP 1 Id, Topic
            FROM MessageTopicsProcessing
            WHERE IsProcessed = 0
            ORDER BY Id
        """)
        row = cursor.fetchone()

        # If all topics processed → reset
        if not row:
            cursor.execute("UPDATE MessageTopicsProcessing SET IsProcessed = 0")
            conn.commit()

            cursor.execute("""
                SELECT TOP 1 Id, Topic
                FROM MessageTopicsProcessing
                WHERE IsProcessed = 0
                ORDER BY Id
            """)
            row = cursor.fetchone()

        topic_id = row.Id
        topic = row.Topic

        # Mark topic as processed
        cursor.execute("""
            UPDATE MessageTopicsProcessing
            SET IsProcessed = 1
            WHERE Id = ?
        """, topic_id)

        conn.commit()

        cursor.close()
        conn.close()

        return topic

async def get_health_tips(user_id, topic):

    def fetch_from_db():
        conn = get_db_connection_dynamic(DYNAMIC_DB_NAME)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT Body
            FROM UserDailyPersonalizedHealthTip
            WHERE UserId = ? AND Topic = ?
        """, user_id, topic)

        rows = cursor.fetchall()

        cursor.close()
        conn.close()

        if not rows:
            return "No previous health tips found"

        return [row.Body for row in rows]

    return await asyncio.to_thread(fetch_from_db)
  
def get_patient_active_diagnoses_for_health_tip(patient_id, cursor):

    cursor.execute(f"""
        SELECT hd.MedicalConditionName
        FROM {DYNAMIC_DB_NAME}.dbo.PatientDiagnosis pd
        JOIN [{MASTER_TABLE_DB_NAME}].Master.HealthDiagnoses hd
            ON pd.ConditionCode = hd.ConditionCode
        WHERE pd.PatientId = ?
        AND pd.IsActive = 1
        AND pd.Deleted = 0
    """, patient_id)

    return [r[0] for r in cursor.fetchall()]

def convert_json_to_profile_for_health_tip(profile, vitals=None, diagnoses=None):

    name = f"{profile.get('FirstName','')} {profile.get('LastName','')}".strip()

    gender_map = {2: "male", 3: "female", 4: "other"}

    result = {

        "name": name,
        "age": profile.get("Age"),
        "gender": gender_map.get(profile.get("Gender"), "unknown"),

        "weight_kg": parse_weight(profile.get("LatestWeight", "[]")),
        "height_cm": parse_height(profile.get("LatestHeight", "[]")),
        "waist_circumference_cm": parse_waist(profile.get("WaistMeasurement")),

        "activity_level": profile.get("PhysicalActivityDesc"),

        "dietary_preference": extract_descriptions(profile.get("DietaryPreferences")),
        "restrictions": extract_descriptions(profile.get("DietaryRestrictions")),
        "digestive_issues": extract_descriptions(profile.get("DigestiveIssues")),
        "allergies": extract_descriptions(profile.get("FoodAllergies"))
    }

    if vitals:
        result["vitals"] = vitals

    if diagnoses:
        result["medical_conditions"] = diagnoses

    return result

def fetch_patient_profile_for_health_tip(user_id, cursor):

    # -------------------------
    # PROFILE 1
    # -------------------------

    cursor.execute(
        "EXEC dbo.usp_GetPatientDietaryProfileBySearch @UserId=?",
        user_id
    )

    row = cursor.fetchone()
    profile1 = dict(zip([c[0] for c in cursor.description], row)) if row else {}

    # -------------------------
    # PROFILE 2
    # -------------------------

    cursor.execute(
        "EXEC dbo.sp_GetPatinetInfoByUserId @PatientId=?",
        user_id
    )

    row = cursor.fetchone()
    profile2 = dict(zip([c[0] for c in cursor.description], row)) if row else {}

    merged_profile = {**profile1, **profile2}

    # -------------------------
    # VITALS
    # -------------------------
    now = datetime.now(timezone.utc)
    current_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    vitals = averagevitals(fetch_vitals(user_id, cursor, current_date))

    # -------------------------
    # DIAGNOSIS
    # -------------------------

    diagnoses = get_patient_active_diagnoses_for_health_tip(user_id, cursor)

    # -------------------------
    # FINAL PROFILE
    # -------------------------

    profile = convert_json_to_profile_for_health_tip(
        merged_profile,
        vitals,
        diagnoses
    )

    return profile

def fetch_profiles_from_dataframe_daily_health_tip(df_combined):

    conn = get_db_connection_dynamic(DYNAMIC_DB_NAME)
    cursor = conn.cursor()

    profiles = []

    for user_id in df_combined["UserId"].unique():

        try:

            profile = fetch_patient_profile_for_health_tip(user_id, cursor)

            profile["UserId"] = user_id

            profiles.append(profile)

        except Exception as e:

            print("Error fetching", user_id, e)

    cursor.close()
    conn.close()

    return pd.DataFrame(profiles)
              
#weekly consultation
def fetch_inactive_patients_sessions_7days(
    patient_db: str,
    master_db: str,
    batch_size: int = 1000
) -> pd.DataFrame:
    """
    Fetch patients whose last session was >= 7 days ago,
    along with total sessions and doctor name.

    Args:
        patient_db (str): Patient database name
        master_db (str): Master database name (for Employee table)
        connection_string (str): pyodbc connection string
        batch_size (int): Number of rows to fetch per batch

    Returns:
        pd.DataFrame: Final combined dataframe
    """

    query = f"""
    WITH SessionStats AS (
        SELECT
            S.PatientId,
            COUNT(*) AS SessionsTotal,
            MAX(S.EndDateTimeUTC) AS LatestSessionDate
        FROM {patient_db}.dbo.Sessions S
        GROUP BY S.PatientId
    )
    SELECT
        P.PatientUserId,
        P.PatientFirstName,
        P.Email,
        SS.SessionsTotal,
        SS.LatestSessionDate,
        E.EmployeeFirstName AS ExpertName
    FROM {patient_db}.dbo.Patient P
    INNER JOIN SessionStats SS
        ON P.PatientUserId = SS.PatientId
    OUTER APPLY (
        SELECT TOP (1)
            S.ExpertId,
            S.Status,
            S.EndDateTimeUTC
        FROM {patient_db}.dbo.Sessions S
        WHERE S.PatientId = SS.PatientId
        ORDER BY S.EndDateTimeUTC DESC
    ) LS
    LEFT JOIN {master_db}.dbo.Employee E
        ON LS.ExpertId = E.UserId
    WHERE
        P.IsActive = 1
        AND P.EnrollmentStatus = 'ENROLLED'
        AND (
            -- Case 1: Latest session older than 7 days
            SS.LatestSessionDate <= DATEADD(DAY, -7, SYSUTCDATETIME())

            OR

            -- Case 2: Latest session within 7 days AND cancelled
            (
                SS.LatestSessionDate > DATEADD(DAY, -7, SYSUTCDATETIME())
                AND LS.Status = 'CAN'
            )
        )
        AND NOT EXISTS (
            SELECT 1
            FROM {patient_db}.dbo.EmailUnsubscribe EU
            WHERE EU.UserId = P.PatientUserId
            AND EU.IsUnsubscribed = 1
        );
    """

    conn = None
    cursor = None

    try:
        conn = get_db_connection_dynamic("master")
        cursor = conn.cursor()
        cursor.execute(query)

        columns = [col[0] for col in cursor.description]

        all_batches = []

        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break

            df_batch = pd.DataFrame.from_records(rows, columns=columns)
            all_batches.append(df_batch)

        if all_batches:
            df = pd.concat(all_batches, ignore_index=True)
            return df
        else:
            df = pd.DataFrame(columns=columns)
            return df

    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass

def get_consultation_email_html(
    recipient_name,
    end_date_time_utc=None,
    doctor_name=None,
    SessionsTotal=None
):
    """
    Generates the consultation booking HTML email template.
    Dynamically inserts patient name, days since last session,
    and doctor name. Handles missing/null values gracefully.
    """

    tracked_download_link = "https://deeplink-friskaaiccm.friska.ai/ccm-app/session_add_v2?"
    base_font = "font-family:Segoe UI, Tahoma, Geneva, Verdana, sans-serif;"

    # ── Compute days since last session ──────────────────────────────────────
    days_since = None
    if SessionsTotal is not None and SessionsTotal > 1:
        if end_date_time_utc is not None:
            try:
                if isinstance(end_date_time_utc, str):
                    end_date_time_utc = datetime.fromisoformat(end_date_time_utc)
                if end_date_time_utc.tzinfo is None:
                    end_date_time_utc = end_date_time_utc.replace(tzinfo=timezone.utc)
                days_since = (datetime.now(timezone.utc) - end_date_time_utc).days
            except Exception:
                days_since = None
    else:
        days_since = None

    # ── Sanitize display values ───────────────────────────────────────────────
    display_name   = recipient_name.strip() if recipient_name and str(recipient_name).strip() not in ("", "nan", "None") else "there"
    display_doctor = f"Dr. {doctor_name.strip()}" if doctor_name and str(doctor_name).strip() not in ("", "nan", "None") else "your care expert"
    display_days   = f"{days_since} day{'s' if days_since != 1 else ''}" if days_since is not None else None

    # ── Build the dynamic hero message ───────────────────────────────────────
    if days_since is not None and days_since >= 0:
        hero_intro = (
            f"Hey <strong>{display_name}</strong>, it has been <strong>{display_days}</strong> "
            f"since your last consultation with <strong>{display_doctor}</strong>."
        )
        hero_cta = "You haven't booked a session since then — please schedule your next consultation to stay on track."
    else:
        hero_intro = (
            f"Hey <strong>{display_name}</strong>, We noticed something — looks like you haven't booked a consultation with our experts yet?"
        )
        hero_cta = "Regular check-ins help catch small issues before they become big ones — please schedule your consultation today."

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <!--[if mso]>
        <style type="text/css">
            body, table, td, h1, h2, h3, p, a, li, span {{ font-family: Arial, sans-serif !important; }}
            .btn {{ mso-hide: none !important; }}
            .btn span {{ color: #ffffff !important; }}
        </style>
        <![endif]-->
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                line-height: 1.6;
                color: #333333;
                background-color: #f5f5f5;
                margin: 0;
                padding: 0;
            }}
            table {{ border-collapse: collapse; mso-table-lspace: 0pt; mso-table-rspace: 0pt; }}
            img {{ border: 0; outline: none; text-decoration: none; -ms-interpolation-mode: bicubic; display: block; }}
            a {{ color: #ffffff; text-decoration: none; }}
            a:visited {{ color: #ffffff !important; text-decoration: none !important; }}
            a span {{ color: #ffffff !important; }}
            .email-container {{ width: 100%; max-width: 650px; margin: 0 auto; background: #ffffff;}}
            .header {{ padding: 30px 40px; border-bottom: 1px solid #e5e5e5; }}
            .hero-section {{ padding: 40px; }}
            .info-card {{ background: #f8f9fa; padding: 25px; border-radius: 8px; border: 1px solid #e5e5e5; }}
            .info-card h2 {{ color: #007BFF; font-size: 20px; margin-bottom: 15px; }}
            .benefit-list {{ margin: 15px 0; padding: 0; list-style-type: none; }}
            .benefit-list li {{ margin-bottom: 8px; font-size: 13px; color: #4a5568; }}
            .btn {{
                display: inline-block;
                padding: 12px 30px;
                background-color: #007BFF;
                color: #ffffff !important;
                text-decoration: none;
                border-radius: 6px;
                font-weight: 600;
                font-size: 14px;
            }}
            .btn span {{ color: #ffffff !important; }}
            .app-image-card {{
                background: #f8f9fa;
                padding: 10px;
                border-radius: 8px;
                border: 1px solid #e5e5e5;
                text-align: center;
            }}
            .expert-image-container {{
                width: 140px; height: 140px; border-radius: 70px; margin: 0 auto 15px;
                border: 3px solid #e9d5ff; overflow: hidden;
            }}
            .expert-image-container img {{ width: 140px; height: 140px; object-fit: cover; border-radius: 70px; }}
            .expert-name {{ color: #2d3748; font-size: 15px; font-weight: 700; margin-bottom: 5px; }}
            .expert-title {{ color: #007BFF; font-size: 13px; margin-bottom: 10px; font-weight: 600; }}
            .expert-bio {{ color: #4a5568; font-size: 11px; line-height: 1.5; margin-bottom: 15px; height: 80px; }}

            /* ── Highlight banner ── */
            .days-banner {{
                background: linear-gradient(135deg, #eff6ff, #dbeafe);
                border-left: 4px solid #007BFF;
                border-radius: 6px;
                padding: 14px 18px;
                margin: 0 0 20px 0;
                font-size: 15px;
                color: #1e3a5f;
                line-height: 1.6;
            }}

            @media only screen and (max-width: 600px) {{
                .stack-on-mobile {{ display: block !important; width: 100% !important; padding-left: 0 !important; padding-right: 0 !important; }}
                .expert-bio {{ height: auto !important; }}
            }}
        </style>
    </head>
    <body style="margin: 0; padding: 0; background-color: #f5f5f5;">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" align="center" style="background-color: #f5f5f5;">
            <tr>
                <td align="center" style="padding: 20px 0;">
                    <table role="presentation" width="650" cellspacing="0" cellpadding="0" border="0" class="email-container" style="background-color: #ffffff; border-radius: 10px; overflow: hidden;">

                        <!-- Header -->
                        <tr>
                        <td align="center"
                            style="background-color:#ffffff; padding:20px 0;">

                            <img src="cid:logo_image" alt="FriskaAI CCM Logo"
                                width="300"
                                style="width:300px; height:auto; display:block; border:0;">

                        </td>
                        </tr>

                        <!-- Hero Section -->
                        <tr>
                            <td class="hero-section">
                                <div class="days-banner">
                                    {hero_intro}

                                    <span style="color:#4a5568; font-size:15px;">{hero_cta}</span>
                                </div>
                                <p style="color: #007BFF; font-size: 15px; margin-bottom: 10px; font-weight: 600;">
                                    Many health changes happen quietly before symptoms appear.
                                </p>
                                <p style="color: #007BFF; font-size: 15px; font-weight: 600;">
                                    That's why expert guidance and timely support matter.
                                </p>
                            </td>
                        </tr>

                        <!-- Two Column Section -->
                        <tr>
                            <td style="padding: 0 40px 40px 40px;">
                                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
                                    <tr>
                                        <td width="320" valign="top" class="stack-on-mobile" style="padding-right: 10px; padding-bottom: 20px;">
                                            <div class="info-card">
                                                <h2>Your Personalized Health Partner</h2>
                                                <p style="font-size: 14px; color: #2d3748; font-weight: bold;">
                                                    A quick consultation can help you understand your health better. Simply log in to the FriskaAi CCM app and schedule your consultation from your dashboard:
                                                </p>
                                                <ul class="benefit-list">
                                                    <li>• Review your progress and vitals</li>
                                                    <li>• Adjust nutrition or fitness plans if needed</li>
                                                    <li>• Catch small issues before they become big ones</li>
                                                    <li>• Get clarity, confidence, and expert advice</li>
                                                </ul>
                                                <a href="{tracked_download_link}" class="btn" target="_blank">
                                                    <span style="color: #ffffff;">Book now</span>
                                                </a>
                                            </div>
                                        </td>
                                        <td width="250" valign="top" class="stack-on-mobile" style="padding-left: 10px;">
                                            <div class="app-image-card">
                                                <img src="cid:mobile_image" width="100%" style="width:100%; max-width:220px; height:auto; display:block; margin:0 auto;" alt="App Screenshot">
                                            </div>
                                        </td>
                                    </tr>
                                </table>
                            </td>
                        </tr>

                        <!-- Experts Section -->
                        <tr>
                            <td style="padding: 40px; background-color: #ffffff; text-align: center; border-top: 1px solid #f0f0f0;">
                                <h2 style="margin-bottom: 30px; font-size: 22px; color: #2d3748;">Meet Our Expert Team</h2>
                                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
                                    <tr>
                                        <td width="33%" valign="top" class="stack-on-mobile" style="padding: 10px;">
                                            <div class="expert-image-container"><img src="cid:expert1_image" alt="Rino"></div>
                                            <div class="expert-name">Fjorino Musaku</div>
                                            <div class="expert-title">Nutritionist, Diabetes Educator M.Ed, Certified ACSM-EP & CDCES</div>
                                            <div class="expert-bio">Diabetes care specialist working with top athletes.</div>
                                        </td>
                                        <td width="33%" valign="top" class="stack-on-mobile" style="padding: 10px;">
                                            <div class="expert-image-container"><img src="cid:expert2_image" alt="Kristin"></div>
                                            <div class="expert-name">Kristin Gahwiler</div>
                                            <div class="expert-title">Fitness Consultant</div>
                                            <div class="expert-bio">20+ years in cardiac rehab. Motion is lotion!</div>
                                        </td>
                                        <td width="34%" valign="top" class="stack-on-mobile" style="padding: 10px;">
                                            <div class="expert-image-container"><img src="cid:expert3_image" alt="Erin"></div>
                                            <div class="expert-name">Erin Szuch</div>
                                            <div class="expert-title">Nurse Practitioner, BSN/MSN, NP-BC</div>
                                            <div class="expert-bio">Committed to compassionate, holistic care.</div>
                                        </td>
                                    </tr>
                                </table>
                            </td>
                        </tr>

                        <!-- Success Stories -->
                        <tr>
                            <td style="padding: 40px; background-color: #f8f9fa;">
                                <h2 style="text-align: center; margin-bottom: 30px; font-size: 22px; color: #2d3748;">
                                    Success Stories from Our Community
                                </h2>
                                <div style="text-align:center; margin-bottom:25px;">
                                    <div style="font-size:48px; font-weight:700; color:#007BFF; line-height:1;">20+</div>
                                    <div style="font-size:16px; color:#4a5568; margin-top:6px; max-width:420px; margin-left:auto; margin-right:auto;">
                                        consultations completed by one of our members within 3 months of using FriskaAi CCM.
                                    </div>
                                </div>
                                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
                                    <tr>
                                        <td align="center" style="padding: 0 10px;">
                                            <table role="presentation" width="310" cellspacing="0" cellpadding="0" border="0" align="center" class="stack-on-mobile" style="margin: 0 auto;">
                                                <tr>
                                                    <td valign="top" align="center">
                                                        <div style="background:#ffffff; border-radius:12px; overflow:hidden; border:1px solid #e5e5e5; margin-bottom:16px; text-align: left;">
                                                            <a href="https://youtube.com/shorts/keb_bUwfhn8?feature=share" target="_blank">
                                                                <img src="cid:success1_image" width="100%" style="width:100%; height:150px; object-fit:cover; display:block;">
                                                            </a>
                                                            <div style="padding:15px;">
                                                                <div style="font-size:14px; font-weight:700; color:#2d3748; margin-bottom:6px;">
                                                                    "I haven't felt better in a very long time"
                                                                </div>
                                                                <div style="font-size:12px; color:#4a5568; line-height:1.5;">
                                                                    Alan joined FriskaAi CCM weighing 299 lbs. In just one and a half months, he reached 265 lbs — a 34 lb transformation. By following a structured program and working closely with experts for clinical tracking and blood tests, he stayed consistent and saw real results.
                                                                </div>
                                                                <div style="font-size:11px; margin-top:8px; font-weight:600; color:#007BFF;">
                                                                    — Alan, 50 (Lost 34 lbs in 6 weeks)
                                                                </div>
                                                            </div>
                                                        </div>
                                                    </td>
                                                </tr>
                                            </table>
                                        </td>
                                    </tr>
                                </table>
                            </td>
                        </tr>

                        <!-- Footer (updated per request) -->
                        <tr>
                        <td style="background:#0f172a;color:#cbd5e1;padding:28px;text-align:center;{base_font}font-size:17px;line-height:1.6;">

                        <h3 style="margin:0 0 10px 0;color:#ffffff;font-size:20px;line-height:1.25;">
                        Committed to Your Health Journey
                        </h3>

                        <p style="margin:0 0 20px 0;">
                        Thank you for trusting FriskaAi CCM as your personalized health partner.
                        </p>

                        <div style="margin-bottom:20px;">
                        <a href="https://www.youtube.com/@Friska_Ai" target="_blank" style="margin:0 6px;display:inline-block;">
                        <img src="https://cdn-icons-png.flaticon.com/512/174/174883.png" width="28" alt="YouTube" style="display:block;border:0;">
                        </a>
                        <a href="https://www.linkedin.com/company/friskaai" target="_blank" style="margin:0 6px;display:inline-block;">
                        <img src="https://cdn-icons-png.flaticon.com/512/174/174857.png" width="28" alt="LinkedIn" style="display:block;border:0;">
                        <a href="https://x.com/FriskaAi_" target="_blank" rel="noopener noreferrer" style="text-decoration: none;">
                            <img src="https://cdn-icons-png.flaticon.com/512/5969/5969020.png" 
                                alt="X (Twitter)" 
                                width="32" 
                                height="32" 
                                style="display: inline-block; margin: 0 10px;">
                        </a>
                        </div>

                        <div style="border-top:1px solid #334155;padding-top:18px;font-size:14px;color:#94a3b8;">

                        <p style="margin:6px 0;line-height:1.5;">
                        For Privacy policy, Terms and Conditions or other information please visit
                        <a href="https://www.friska.ai" style="color:#94a3b8;text-decoration:underline;">https://www.friska.ai</a>
                        </p>
                        <p style="margin:10px 0 4px 0;color:#94a3b8;font-weight:600;">
                        Contact Us
                        </p>
                        <table role="presentation" cellpadding="0" cellspacing="0" align="center" style="margin:10px auto 0 auto;">
                        <tr>
                            <td style="padding:0 10px;color:#94a3b8;white-space:nowrap;">
                            ✉️ <a href="mailto:q@friska.ai" style="color:#94a3b8;text-decoration:none;">q@friska.ai</a>
                            </td>
                            <td style="padding:0 10px;color:#94a3b8;white-space:nowrap;">
                            📞 <a href="tel:7032021655" style="color:#94a3b8;text-decoration:none;">703-202-1655</a>
                            </td>
                        </tr>
                        </table>
                        <!-- ADD THIS BLOCK before copyright -->
                        <p style="margin:14px 0 0 0; color:#94a3b8; font-size:13px;">
                        If you no longer wish to receive these emails, unsubscribe
                        <a href="{{unsubscribe_link}}" style="color:#94a3b8;text-decoration:underline;">
                        here
                        </a>
                        This can not be undone.
                        </p>

                        <p style="margin:14px 0 0 0;color:#64748b;">
                        © HFWL Company. Empowering your health journey with clinical precision.
                        </p>

                        </div>

                        </td>
                        </tr>

                        </table>
                        </td>
                        </tr>
                        </table>
                        </body>
                        </html>"""

def send_weekly_consultation_email(
    sender_email,
    sender_password,
    recipient_email,
    subject,
    html_content,
    template_type,
    patient_id,
    email_type_id,
    attachments=None,):
    """
    Send email with embedded images using CID + optional file attachments.
    Images are sourced from the 'email_images' subfolder located in the project root.
    """
    try:
        # -----------------------------
        # Outer container for attachments
        # -----------------------------
        outer = MIMEMultipart("mixed")
        outer["Subject"] = subject
        outer["From"] = sender_email
        outer["To"] = recipient_email

        from urllib.parse import quote
        # -----------------------------
        # Generate unsubscribe token
        # -----------------------------
        token = generate_unsubscribe_token(patient_id, email_type_id)


        unsubscribe_url = f"{UNSUBSCRIBE_ENDPOINT}/unsubscribe?token={quote(token)}"

        # Headers (for email clients like Gmail)
        outer["List-Unsubscribe"] = f"<mailto:{sender_email}>, <{unsubscribe_url}>"
        outer["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

        # Inject into HTML template
        html_content = html_content.replace("{unsubscribe_link}", unsubscribe_url)

        # -----------------------------
        # Inner container for CID images + HTML
        # -----------------------------
        msg = MIMEMultipart("related")
        outer.attach(msg)

        msg_alternative = MIMEMultipart("alternative")
        msg.attach(msg_alternative)

        html_part = MIMEText(html_content, "html", "utf-8")
        msg_alternative.attach(html_part)

        # -----------------------------
        # Image path resolution
        # -----------------------------
        # 1. Get the directory where static folder is located
        BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

        # 3. Point to the 'email_images' folder in the root
        image_dir = os.path.join(BASE_DIR, "static", "email_images")

        common_images = {"logo_image": "friskalogo.png"}

        template_images = {
            "consultation": {
                "mobile_image": "Friskaccm_image4.png",
                "expert1_image": "Expert_1_image.png",
                "expert2_image": "expert_2_image.png",
                "expert3_image": "expert_3_image.png",
                "success1_image": "Success_story_Alan.png"
            },
            "fjorino_musaku": {
                "expert1_image": "Expert_1_image.jpg",
            },
            "download_app": {
                "appimage2": "Friskaccm_image2.png",
                "appimage3": "Friskaccm_image3.png",
                "unlock_1": "unlock1.png",
                "unlock_2": "unlock2.png",
                "unlock_3": "unlock3.png",
                "unlock_4": "unlock4.png",
            },
            "migrate_ccm": {
                "ccmappimage1": "mobileimage.png",
                "ccmappimage2": "Friskaccm_image2.png",
                "success1_image": "Success_Story_1.png",
                "success2_image": "Success_Story_2.png",
            },
        }

        all_images = {**common_images, **template_images.get(template_type, {})}

        for cid, filename in all_images.items():
            image_path = os.path.join(image_dir, filename)
            if os.path.exists(image_path):
                with open(image_path, "rb") as img_file:
                    img = MIMEImage(img_file.read())
                    img.add_header("Content-ID", f"<{cid}>")
                    img.add_header("Content-Disposition", "inline", filename=filename)
                    msg.attach(img)
            else:
                logging.warning(f"Image not found for CID '{cid}': {image_path}")

        # -----------------------------
        # Attachments (true attachments)
        # -----------------------------
        if attachments:
            for uf in attachments:
                if uf is None:
                    continue
                data = uf.getvalue()
                part = MIMEApplication(data)
                part.add_header("Content-Disposition", "attachment", filename=uf.name)
                outer.attach(part)

        # -----------------------------
        # Send via Office365 SMTP
        # -----------------------------
        with smtplib.SMTP("smtp.office365.com", 587) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, recipient_email, outer.as_string())

        return "Sent", "Email sent successfully!"

    except Exception as e:
        logging.error(f"Failed to send email to {recipient_email}: {str(e)}")
        return "Failed", f"Failed to send: {str(e)}"
 
#---------------------------------------
# Unsubscribe
#---------------------------------------
def process_unsubscribe(token: str) -> bool:
    try:    
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=["HS256"])
        user_id = payload.get("userId")
        email_type = payload.get("emailType")
        print(f"UserId: {user_id}, email_type :{email_type}")
        if not user_id or not email_type:
            return False, ""

    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return False, ""

    conn = None
    try:
        conn = get_db_connection_dynamic(DYNAMIC_DB_NAME)
        cursor = conn.cursor()

        query = """
        MERGE EmailUnsubscribe AS target
        USING (SELECT ? AS UserId, ? AS EmailType) AS source
            ON target.UserId = source.UserId AND target.EmailType = source.EmailType
        WHEN MATCHED THEN
            UPDATE SET 
                IsUnsubscribed = 1,
                UpdatedAt      = GETUTCDATE()
        WHEN NOT MATCHED THEN
            INSERT (UserId, EmailType, IsUnsubscribed, CreatedAt, UpdatedAt)
            VALUES (source.UserId, source.EmailType, 1, GETUTCDATE(), GETUTCDATE());
        """

        cursor.execute(query, user_id, email_type)
        conn.commit()

        query2 = """
        SELECT EmailTypeName FROM EmailTypes
        WHERE EmailTypeId = ?
        """

        cursor.execute(query2, (email_type,))
        row = cursor.fetchone()
        email_name = row[0] if row else ""
        print(email_name)
        return True, email_name

    except Exception as e:
        print("DB Error:", str(e))
        return False, ""

    finally:
        if conn:
            conn.close()

def process_resubscribe(token: str):
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=["HS256"])
        user_id = payload.get("userId")
        email_type = payload.get("emailType")
        if not user_id or not email_type:
            return False, ""
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return False, ""

    conn = None
    try:
        conn = get_db_connection_dynamic(DYNAMIC_DB_NAME)
        cursor = conn.cursor()

        query = """
        UPDATE EmailUnsubscribe
        SET IsUnsubscribed = 0, UpdatedAt = GETUTCDATE()
        WHERE UserId = ? AND EmailType = ?
        """
        cursor.execute(query, user_id, email_type)
        conn.commit()

        query2 = "SELECT EmailTypeName FROM EmailTypes WHERE EmailTypeId = ?"
        cursor.execute(query2, (email_type,))
        row = cursor.fetchone()
        email_name = row[0] if row else ""
        return True, email_name

    except Exception as e:
        print("DB Error:", str(e))
        return False, ""

    finally:
        if conn:
            conn.close()

def generate_unsubscribe_token(user_id: int, email_type: str) -> str:
    payload = {
        "userId": user_id,
        "emailType": email_type,
        "exp": datetime.now(timezone.utc) + timedelta(days=7)
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm="HS256")

def generate_unsubscribe_headers(token: str):
    unsubscribe_url = f"{UNSUBSCRIBE_ENDPOINT}/unsubscribe?token={token}"

    headers = {
        "List-Unsubscribe": f"<mailto:{EMAIL_FROM}>, <{unsubscribe_url}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"
    }

    return headers


def _unsubscribe_html_response(success: bool, email_name: str, token: str = "") -> HTMLResponse:
    if success:
        html_content = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Unsubscribed</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    min-height: 100vh;
                    margin: 0;
                    background-color: #f5f5f5;
                }}
                .card {{
                    background: white;
                    padding: 40px;
                    border-radius: 10px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                    text-align: center;
                    max-width: 400px;
                    width: 90%;
                }}
                .icon {{ font-size: 50px; margin-bottom: 20px; }}
                h1 {{ color: #2c7a4b; font-size: 24px; margin-bottom: 10px; }}
                p {{ color: #666; font-size: 15px; line-height: 1.5; }}
                .email {{
                    display: inline-block;
                    margin-top: 10px;
                    padding: 6px 14px;
                    background-color: #eaf4ee;
                    color: #2c7a4b;
                    border-radius: 20px;
                    font-size: 14px;
                    font-weight: bold;
                    word-break: break-all;
                }}
                .resubscribe-btn {{
                    margin-top: 20px;
                    background: none;
                    border: none;
                    color: #999;
                    font-size: 13px;
                    cursor: pointer;
                    text-decoration: underline;
                    padding: 0;
                }}
                .resubscribe-btn:hover {{ color: #555; }}
                .resubscribe-btn:disabled {{ color: #ccc; cursor: default; text-decoration: none; }}
            </style>
        </head>
        <body>
            <div class="card">
                <div id="card-content">
                    <div class="icon">✅</div>
                    <h1>Successfully Unsubscribed</h1>
                    <p>We're sorry to see you go. You will no longer receive emails related to {email_name} updates.</p>

                    <button class="resubscribe-btn" onclick="resubscribe(this)">
                        Changed your mind? Resubscribe
                    </button>
                </div>
            </div>
            <script>
                async function resubscribe(btn) {{
                    btn.disabled = true;
                    btn.textContent = "Processing...";
                    try {{
                        const res = await fetch("/resubscribe?token={token}", {{ method: "POST" }});
                        const data = await res.json();
                        if (res.ok && data.success) {{
                            document.getElementById("card-content").innerHTML = `
                                <div class="icon">🔔</div>
                                <h1 style="color:#2c7a4b;">You've Resubscribed</h1>
                                <p>You will now receive <strong>{email_name}</strong> again.</p>
                            `;
                        }} else {{
                            btn.disabled = false;
                            btn.textContent = "Changed your mind? Resubscribe";
                            alert("Something went wrong. Please try again.");
                        }}
                    }} catch (e) {{
                        btn.disabled = false;
                        btn.textContent = "Changed your mind? Resubscribe";
                        alert("Network error. Please try again.");
                    }}
                }}
            </script>
        </body>
        </html>
        """
    else:
        # failure HTML unchanged, token not needed
        html_content = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Invalid Link</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    min-height: 100vh;
                    margin: 0;
                    background-color: #f5f5f5;
                }}
                .card {{
                    background: white;
                    padding: 40px;
                    border-radius: 10px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                    text-align: center;
                    max-width: 400px;
                    width: 90%;
                }}
                .icon {{ font-size: 50px; margin-bottom: 20px; }}
                h1 {{ color: #c0392b; font-size: 24px; margin-bottom: 10px; }}
                p {{ color: #666; font-size: 15px; line-height: 1.5; }}
                .email {{
                    display: inline-block;
                    margin-top: 10px;
                    padding: 6px 14px;
                    background-color: #fdecea;
                    color: #c0392b;
                    border-radius: 20px;
                    font-size: 14px;
                    font-weight: bold;
                    word-break: break-all;
                }}
            </style>
        </head>
        <body>
            <div class="card">
                <div class="icon">❌</div>
                <h1>Invalid or Expired Link</h1>
                <p>The unsubscribe link for:</p>
                <span class="email">{email_name}</span>
                <p style="margin-top: 16px;">is no longer valid.
It may have already been used or has expired.</p>
            </div>
        </body>
        </html>
        """

    return HTMLResponse(content=html_content, status_code=200)
 
#weekly meal plan
def split_weekly_meal_plan(raw_text: str):
    pattern = r"(### Day \d+: .*?)(?=### Day \d+:|$)"
    return re.findall(pattern, raw_text, re.DOTALL)

def extract_date_from_day(day_text: str):
    match = re.search(r"### Day \d+: .*?, (.+)", day_text)
    if match:
        date_str = match.group(1).strip()
        try:
            return datetime.strptime(date_str, "%B %d, %Y").date()
        except:
            return None
    return None

def json_serializer(obj):

    if isinstance(obj, Decimal):
        return float(obj)

    if isinstance(obj, (datetime, date)):
        return obj.isoformat()

    if isinstance(obj, uuid.UUID):
        return str(obj)

    return str(obj)


#new for weekly
def get_patient_active_diagnoses_v2(cursor, patient_id: str):

    query = f"""
        SELECT DISTINCT h.MedicalConditionName
        FROM [dbo].[PatientDiagnosis] pd WITH (NOLOCK)
        INNER JOIN [{MASTER_TABLE_DB_NAME}].[Master].[HealthDiagnoses] h WITH (NOLOCK)
            ON pd.ConditionCode = h.ConditionCode
        WHERE pd.PatientId = ?
          AND pd.IsActive = 1
          AND pd.Deleted = 0
          AND ISNULL(h.ActiveStatus, 0) = 1
          AND ISNULL(h.IsHealthCondition, 0) = 1
    """

    cursor.execute(query, patient_id)

    return [row[0] for row in cursor.fetchall()]

def fetch_vitals_v2(user_id: str, cursor, current_date: str):

    cursor.execute(
        """
        EXEC [Member].[GetUserNutritionDashboardVitals]
            @UserId = ?,
            @CurrentDate = ?,
            @TimeZoneOffset = ?,
            @OffsetMinutes = ?
        """,
        user_id,
        current_date,
        330,
        -330
    )

    row = cursor.fetchone()

    return dict(zip([col[0] for col in cursor.description], row or []))

# =========================================================
# FETCH USER PROFILE V2
# =========================================================

def get_user_profile_v2(user_id: str, cursor):

    # -----------------------------------
    # PROFILE 1
    # -----------------------------------

    cursor.execute(
        "EXEC [dbo].[usp_GetPatientDietaryProfileBySearch] @UserId = ?",
        user_id
    )

    row1 = cursor.fetchone()

    dict1 = (
        dict(zip([col[0] for col in cursor.description], row1))
        if row1 else {}
    )

    # -----------------------------------
    # PROFILE 2
    # -----------------------------------

    cursor.execute(
        "EXEC [dbo].[sp_GetPatinetInfoByUserId] @PatientId = ?",
        user_id
    )

    row2 = cursor.fetchone()

    dict2 = (
        dict(zip([col[0] for col in cursor.description], row2))
        if row2 else {}
    )

    merged_profile = {
        **dict2,
        **dict1
    }

    # -----------------------------------
    # ACTIVE GOAL
    # -----------------------------------

    cursor.execute(f"""
        SELECT TOP 1
            pg.GoalCode,
            gm.GoalName
        FROM PatientGoals pg
        LEFT JOIN [{MASTER_TABLE_DB_NAME}].[Master].[GoalMaster] gm
            ON gm.GoalCode = pg.GoalCode
        WHERE pg.PatientId = ?
        AND pg.CategoryCode = 'LIFESTYLE'
        AND pg.StatusCode = 'Active'
        ORDER BY pg.StartDate DESC
    """, user_id)

    goal_row = cursor.fetchone()

    goal_data = {}

    if goal_row:
        goal_data = dict(
            zip(
                [col[0] for col in cursor.description],
                goal_row
            )
        )

    # -----------------------------------
    # VITALS
    # -----------------------------------

    now = datetime.now(timezone.utc)

    vitals_avg = averagevitals(
        fetch_vitals_v2(
            user_id,
            cursor,
            (now - timedelta(days=1)).strftime("%Y-%m-%d")
        )
    )

    # -----------------------------------
    # DIAGNOSIS
    # -----------------------------------

    diagnoses = get_patient_active_diagnoses_v2(
        cursor,
        user_id
    )

    # -----------------------------------
    # FINAL PROFILE
    # -----------------------------------

    profile = convert_json_to_profile(
        merged_profile,
        vitals_json=vitals_avg,
        diagnoses=diagnoses
    )

    # Add goal
    profile["goals"] = [goal_data] if goal_data else []

    return profile

# =========================================================
# UPDATE / INSERT CALORIES GOAL
# =========================================================

def save_tdee_goal(
    cursor,
    user_id: str,
    tdee: float,
    start_date,
    end_date
):

    logger.info(
        f"Updating calories goal for user {user_id} | TDEE={tdee}"
    )

    new_calories = round(float(tdee))

    # --------------------------------------------------
    # FETCH LATEST CALORIES GOAL TEMPLATE
    # --------------------------------------------------

    cursor.execute("""
        SELECT TOP 1
            UserId,
            ProgramId,
            GoalType,
            Metric,
            Frequency,
            Unit,
            ApprovalStatus,
            ApprovedBy,
            ApprovedDate
        FROM dbo.CCMGoals
        WHERE UserId = ?
        AND UPPER(Metric) = 'CALORIES'
        ORDER BY GoalId DESC
    """, user_id)

    existing_goal = cursor.fetchone()

    if not existing_goal:

        logger.warning(
            f"No existing CALORIES goal found for user {user_id}"
        )

        return

    columns = [col[0] for col in cursor.description]

    goal_data = dict(zip(columns, existing_goal))

    # --------------------------------------------------
    # INACTIVATE OLD GOALS
    # --------------------------------------------------

    cursor.execute("""
        UPDATE dbo.CCMGoals
        SET
            Status='INACTIVE',
            UpdatedBy='AI',
            UpdatedDate=GETUTCDATE()
        WHERE UserId=?
        AND UPPER(Metric)='CALORIES'
        AND Status='ACTIVE'
    """, user_id)

    # --------------------------------------------------
    # INSERT NEW GOAL
    # --------------------------------------------------

    cursor.execute("""
        INSERT INTO dbo.CCMGoals
        (
            UserId,
            ProgramId,
            GoalType,
            Metric,
            TargetMin,
            TargetMax,
            TargetValue,
            Frequency,
            Unit,
            StartDate,
            EndDate,
            Status,
            ApprovalStatus,
            ApprovedBy,
            ApprovedDate,
            CreatedBy,
            CreatedDate,
            UpdatedBy,
            UpdatedDate
        )
        VALUES
        (
            ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?,
            'ACTIVE',
            ?, ?, ?,
            'SYSTEM',
            GETUTCDATE(),
            NULL,
            NULL
        )
    """, (
        goal_data["UserId"],
        goal_data["ProgramId"],
        goal_data["GoalType"],
        goal_data["Metric"],

        0,
        new_calories,
        new_calories,

        goal_data["Frequency"],
        goal_data["Unit"],

        start_date,
        end_date,

        goal_data["ApprovalStatus"],
        goal_data["ApprovedBy"],
        goal_data["ApprovedDate"]
    ))

    logger.info(
        f"Calories goal updated successfully for user {user_id}"
    )


def generate_profile_hash(profile: dict) -> str:
    """
    Generate a deterministic hash for profile fields affecting meal plans
    """

    key_fields = [
        'age', 'gender', 'weight_kg', 'height_cm', 'activity_level',
        'dietary_preference', 'restrictions', 'digestive_issues',
        'cuisine', 'allergies', 'symptom_aggravating_foods',
        'medical_conditions', 'vitals_numeric'
    ]

    normalized = {}

    for key in key_fields:
        val = profile.get(key)

        if val in [None, "", []]:
            normalized[key] = None

        elif isinstance(val, list):
            normalized[key] = sorted([str(v).strip().lower() for v in val])

        elif isinstance(val, dict):
            normalized[key] = {k: str(v).lower() for k, v in sorted(val.items())}

        else:
            normalized[key] = str(val).strip().lower()

    profile_string = json.dumps(normalized, sort_keys=True)

    return hashlib.sha256(profile_string.encode()).hexdigest()

#weeklyhtmlmeal
def weekly_automation_meal_plan_html_completed_email(data: dict) -> str:

    total_minutes = round(
        data['total_time_seconds'] / 60,
        2
    )

    is_success = data["status"] == "COMPLETED"

    status_color = (
        "#16a34a"
        if is_success
        else "#f59e0b"
    )

    status_text = (
        "SUCCESS ✔"
        if is_success
        else "PARTIAL ⚠"
    )

    return f"""
    <html>
    <body style="
        margin:0;
        padding:0;
        background-color:#1e1e1e;
        font-family:Arial,sans-serif;
        color:#ffffff;
    ">

        <div style="
            max-width:700px;
            margin:40px auto;
            background:#2b2b2b;
            border-radius:12px;
            overflow:hidden;
        ">

            <!-- HEADER -->
            <div style="
                padding:32px 30px 16px 30px;
                text-align:center;
            ">

                <h1 style="
                    margin:0 0 6px 0;
                    font-size:28px;
                    font-weight:bold;
                    color:#ffffff;
                ">
                    Weekly Meal Plan Report
                </h1>

                <p style="
                    margin:0;
                    color:#aaaaaa;
                    font-size:14px;
                ">
                    Nouriq AI Automation System
                </p>

                <p style="
                    margin:8px 0 0 0;
                    color:#cccccc;
                    font-size:13px;
                ">
                    Environment:
                    <strong>{ENVIRONMENT}</strong>
                </p>

            </div>

            <!-- DIVIDER -->
            <hr style="
                border:none;
                border-top:1px solid #3d3d3d;
                margin:0 30px;
            " />

            <!-- STATUS -->
            <div style="
                text-align:center;
                padding:24px 30px 8px 30px;
            ">

                <span style="
                    display:inline-block;
                    background-color:{status_color};
                    color:#ffffff;
                    padding:10px 36px;
                    border-radius:30px;
                    font-size:16px;
                    font-weight:bold;
                    letter-spacing:0.5px;
                ">
                    {status_text}
                </span>

            </div>

            <!-- DATE RANGE -->
            <div style="
                padding:12px 30px;
                text-align:center;
            ">

                <p style="
                    margin:0;
                    font-size:15px;
                    color:#dddddd;
                ">
                    <strong>Week Range:</strong>
                    {data['week_start_date']}
                    →
                    {data['week_end_date']}
                </p>

            </div>

            <!-- EXECUTION -->
            <div style="
                padding:10px 30px 10px 30px;
            ">

                <p style="
                    margin:6px 0;
                    font-size:14px;
                ">
                    <strong>Execution Time:</strong>
                    {data['total_time_seconds']} sec
                    ({total_minutes} min)
                </p>

                <p style="
                    margin:6px 0;
                    font-size:14px;
                ">
                    <strong>Completed At (UTC):</strong>
                    {data['finished_at']}
                </p>

            </div>

            <!-- MAIN STATS -->
            <div style="
                padding:16px 30px;
            ">

                <table style="
                    width:100%;
                    border-collapse:collapse;
                    border-radius:8px;
                    overflow:hidden;
                ">

                    <thead>

                        <tr style="
                            background:#3a3a3a;
                        ">

                            <th style="
                                padding:14px;
                                text-align:center;
                                font-size:14px;
                                color:#ffffff;
                                border-right:1px solid #4a4a4a;
                            ">
                                Total Patients
                            </th>

                            <th style="
                                padding:14px;
                                text-align:center;
                                font-size:14px;
                                color:#ffffff;
                                border-right:1px solid #4a4a4a;
                            ">
                                Generated
                            </th>

                            <th style="
                                padding:14px;
                                text-align:center;
                                font-size:14px;
                                color:#ffffff;
                            ">
                                Failed
                            </th>

                        </tr>

                    </thead>

                    <tbody>

                        <tr style="
                            background:#2f2f2f;
                        ">

                            <td style="
                                padding:16px;
                                text-align:center;
                                font-size:22px;
                                font-weight:bold;
                                color:#ffffff;
                                border-right:1px solid #4a4a4a;
                            ">
                                {data['total_users']}
                            </td>

                            <td style="
                                padding:16px;
                                text-align:center;
                                font-size:22px;
                                font-weight:bold;
                                color:#22c55e;
                                border-right:1px solid #4a4a4a;
                            ">
                                {data['generated_week']}
                            </td>

                            <td style="
                                padding:16px;
                                text-align:center;
                                font-size:22px;
                                font-weight:bold;
                                color:#ef4444;
                            ">
                                {data['failed']}
                            </td>

                        </tr>

                    </tbody>

                </table>

            </div>

            <!-- DIVIDER -->
            <hr style="
                border:none;
                border-top:1px solid #3d3d3d;
                margin:0 30px;
            " />

            <!-- DETAILS -->
            <div style="
                padding:20px 30px;
            ">

                <table style="
                    width:100%;
                    border-collapse:collapse;
                ">

                    <tbody>

                        <tr style="
                            border-bottom:1px solid #3d3d3d;
                        ">

                            <td style="
                                padding:12px 8px;
                                font-size:14px;
                                color:#aaaaaa;
                            ">
                                Generated Weekly Plans
                            </td>

                            <td style="
                                padding:12px 8px;
                                font-size:14px;
                                font-weight:bold;
                                text-align:right;
                                color:#22c55e;
                            ">
                                {data['generated_week']}
                            </td>

                        </tr>

                        <tr style="
                            border-bottom:1px solid #3d3d3d;
                        ">

                            <td style="
                                padding:12px 8px;
                                font-size:14px;
                                color:#aaaaaa;
                            ">
                                Skipped
                            </td>

                            <td style="
                                padding:12px 8px;
                                font-size:14px;
                                font-weight:bold;
                                text-align:right;
                                color:#f59e0b;
                            ">
                                {data['skipped']}
                            </td>

                        </tr>

                        <tr style="
                            border-bottom:1px solid #3d3d3d;
                        ">

                            <td style="
                                padding:12px 8px;
                                font-size:14px;
                                color:#aaaaaa;
                            ">
                                Failed
                            </td>

                            <td style="
                                padding:12px 8px;
                                font-size:14px;
                                font-weight:bold;
                                text-align:right;
                                color:#ef4444;
                            ">
                                {data['failed']}
                            </td>

                        </tr>

                        <tr>

                            <td style="
                                padding:12px 8px;
                                font-size:14px;
                                color:#aaaaaa;
                            ">
                                Total Runtime
                            </td>

                            <td style="
                                padding:12px 8px;
                                font-size:14px;
                                font-weight:bold;
                                text-align:right;
                                color:#ffffff;
                            ">
                                {data['total_time_seconds']} sec
                                ({total_minutes} min)
                            </td>

                        </tr>

                    </tbody>

                </table>

            </div>

            <!-- FOOTER -->
            <div style="
                padding:16px 30px 28px 30px;
                text-align:center;
            ">

                <p style="
                    margin:0;
                    font-size:12px;
                    color:#666666;
                ">
                    This is an automated report generated by the Weekly Meal Plan AI System.
                </p>

            </div>

        </div>

    </body>
    </html>
    """

def fetch_chat_history_v2(cursor, user_id):
        try:
            cursor.execute("""
                SELECT [chat], [constraints], [last_agent_context]
                FROM [Nauriq].[AI_Formatted_Chat_History]
                WHERE [user_id] = ?
            """, user_id)
            row = cursor.fetchone()
            return (
                json.loads(row.chat) if row and row.chat else [],
                json.loads(row.constraints) if row and row.constraints else {},
                json.loads(row.last_agent_context) if row and row.last_agent_context else {},
            )
        except Exception as e:
            logger.error(f"Error fetching chat history for user {user_id}: {e}")
            return [], {}, {}

def upsert_weekly_grocery_list(
    cursor,
    user_id,
    start_date,
    end_date,
    grocery_items,
):
    grocery_json = json.dumps(grocery_items)

    cursor.execute("""
        MERGE GroceryList AS target
        USING
        (
            SELECT
                ? AS PatientId,
                ? AS StartDate,
                ? AS EndDate
        ) AS source

        ON
            target.PatientId = source.PatientId
            AND target.StartDate = source.StartDate
            AND target.EndDate = source.EndDate

        WHEN MATCHED THEN
            UPDATE SET
                GroceryItems = ?,
                RawText = '',
                UpdatedBy = 'System',
                UpdatedAt = GETUTCDATE()

        WHEN NOT MATCHED THEN
            INSERT
            (
                PatientId,
                StartDate,
                EndDate,
                GroceryItems,
                RawText,
                CreatedBy
            )
            VALUES
            (
                ?, ?, ?, ?, '', 'System'
            );
    """, (
        user_id,
        start_date,
        end_date,

        grocery_json,

        user_id,
        start_date,
        end_date,
        grocery_json
    ))

def get_week_range(any_date):
    # Sunday = week start
    week_start = any_date - timedelta(days=(any_date.weekday() + 1) % 7)

    # Saturday = week end
    week_end = week_start + timedelta(days=6)

    return week_start, week_end