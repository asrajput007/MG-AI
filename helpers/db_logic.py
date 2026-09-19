import json
import logging
from datetime import datetime, timedelta, date, time
import random
import uuid
from zoneinfo import ZoneInfo
from typing import Optional, Dict, List, Union
import time as time_module
from fastapi.responses import JSONResponse
import jwt

# You must copy these files from your app2 project to this project
from helpers.database import get_db_connection_dynamic
from helpers.redis_client import RedisProfileCache
from helpers.food_database_cache import get_food_database_cache

from concurrent.futures import ThreadPoolExecutor
from ai.models import DietaryProfileRequest, TranslationUpdateRequest
from helpers.utils import (
    JWT_SECRET_KEY, MASTER_TABLE_DB_NAME, average_vitals, cm_to_feet_inches, consume_all_results, 
    convert_json_to_profile, decode_jwt_token, fetch_vitals, generate_sas_uri, get_codes_from_db, 
    get_meal_plan_data, extract_descriptions,
    get_patient_active_diagnoses, get_tenant_identifier, 
    kg_to_lbs, make_value_clause, parse_datetime, 
    parse_meal_plan_text_to_items, serialize_all, serialize_data, split_weekly_meal_text
)
from fastapi import HTTPException


import os
import re
from datetime import datetime, timedelta, date, time, timezone
from typing import Optional, List, Dict, Any, Union

logger = logging.getLogger(__name__)

PATIENT_FITNESS_PLAN_DAYS_TABLE = "[dbo].[PatientFitnessPlanDays]"
FITNESS_PLAN_EXERCISES_TABLE = "[dbo].[FitnessPlanExercises]"

# --- HELPER: Fetch food details from foods 1.json ---
def get_food_details_from_database(food_name: str) -> Dict:
    """
    Fetch food_id, ingredients, and recipe from foods 1.json by matching food name.
    
    Args:
        food_name: Name of the food item (e.g., "Grilled Chicken Patties")
    
    Returns:
        dict with keys: food_id, ingredients, recipe_steps
    """
    try:
        food_cache = get_food_database_cache()
        foods_data = food_cache.get_foods_data()
        
        if not foods_data or 'foods' not in foods_data:
            return {"food_id": None, "ingredients": None, "recipe_steps": None}
        
        foods_list = foods_data['foods']
        food_name_lower = food_name.lower().strip()
        
        # Try exact match first
        for food_item in foods_list:
            if food_item.get('common_name', '').lower().strip() == food_name_lower:
                recipe_data = food_item.get('recipe', {})
                ingredients = recipe_data.get('ingredients', [])
                steps = recipe_data.get('steps', [])
                
                return {
                    "food_id": food_item.get('food_id'),
                    "ingredients": json.dumps(ingredients) if ingredients else None,
                    "recipe_steps": json.dumps(steps) if steps else None
                }
        
        # If no exact match, try partial match
        for food_item in foods_list:
            common_name = food_item.get('common_name', '').lower().strip()
            if food_name_lower in common_name or common_name in food_name_lower:
                recipe_data = food_item.get('recipe', {})
                ingredients = recipe_data.get('ingredients', [])
                steps = recipe_data.get('steps', [])
                
                return {
                    "food_id": food_item.get('food_id'),
                    "ingredients": json.dumps(ingredients) if ingredients else None,
                    "recipe_steps": json.dumps(steps) if steps else None
                }
        
        # No match found
        return {"food_id": None, "ingredients": None, "recipe_steps": None}
        
    except Exception as e:
        logger.error(f"Error fetching food details for '{food_name}': {e}")
        return {"food_id": None, "ingredients": None, "recipe_steps": None}


# --- MEAL PLAN LOGIC ---
def upsert_meal_plan_details_logic(user_id: str, database_name: str, meal_plan_text: str, plan_name: str = "Daily Meal Plan", plan_type: str = "DAILY", meal_date: str = None):
    if not meal_date:
        meal_date = datetime.now().strftime("%Y-%m-%d")
 
    items = parse_meal_plan_text_to_items(meal_plan_text)
    if not items:
        raise HTTPException(status_code=400, detail="No valid meal items found in text.")
 
    # Prefix plan_name with GeneratedDate
    # now = datetime.now()
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    meal_date_obj = datetime.strptime(meal_date, "%Y-%m-%d")
    generated_date_str = now.strftime("%Y-%m-%d")
    plan_name_with_date = f"{plan_name} - {meal_date_obj}"
 
    # Connect to DB
    conn = get_db_connection_dynamic(database_name)
    cursor = conn.cursor()
 
    # Delete previous records with same MealDate, PatientId
    delete_query = """
        DELETE FROM [dbo].[MealPlanDetails]
        WHERE [MealDate] = ? AND [PatientId] = ?
    """
    cursor.execute(delete_query, (meal_date, user_id))
 
    results = []
    meal_times = {
        "Breakfast": "08:00:00",
        "MorningSnack": "10:00:00",
        "Lunch": "13:00:00",
        "EveningSnack": "16:00:00",
        "Dinner": "19:00:00"
    }
    
    # STEP 1: Collect all payloads (silent mode - no console prints)
    all_payloads = []
    insert_query = """
        INSERT INTO [dbo].[MealPlanDetails]
            ([GeneratedDate],[PatientId],[PlanName],[PlanType],[MealType],[FoodItems],[FoodId],[Ingredients],[Recipe],[household_measure],
             [PortionWeight],[PortionWeightUnit],[Protein],[Carbohydrates],[Fat],[Fiber],[Sodium],[Sugar],
             [Cholesterol],[Calories],[Iodine],[NutritionalInfo],[DietaryRestrictions],
             [MealTime],[MealDate],[PreprationInstructions],[Status],[NutritionScore],
             [CreatedBy],[CreatedAt],[UpdatedBy],[UpdatedAt])
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
    
    for item in items:
        try:
            # 🎯 PRIORITY: Use metadata from parsed item if available, otherwise lookup from database
            if 'food_id' in item and item['food_id']:
                food_id = item['food_id']
                # Convert Python list to JSON string for database
                import json
                ingredients = json.dumps(item.get('ingredients', []))
                recipe_steps = json.dumps(item.get('recipe', []))
            else:
                # Fallback: Fetch from foods database
                food_details = get_food_details_from_database(item['FoodItems'])
                food_id = food_details.get('food_id')
                ingredients = food_details.get('ingredients')  # JSON string
                recipe_steps = food_details.get('recipe_steps')  # JSON string
            
            nutritional_info = (
                f"Protein: {item['Protein']}g, Carbs: {item['Carbohydrates']}g, "
                f"Fat: {item['Fat']}g, Fiber: {item['Fiber']}g, Sodium: {item['Sodium']}mg, "
                f"Sugar: {item['Sugar']}g, Cholesterol: {item['Cholesterol']}mg, "
                f"Calories: {item['Calories']}kcal, Iodine: {item.get('Iodine', 0)}mcg"
            )

            insert_values = (
                generated_date_str, user_id, plan_name_with_date, plan_type,
                item['MealType'], item['FoodItems'], food_id, ingredients, recipe_steps, item.get('household_measure'), item['PortionWeight'],
                item.get("PortionWeightUnit"),
                item['Protein'], item['Carbohydrates'], item['Fat'], item['Fiber'],
                item['Sodium'], item['Sugar'], item['Cholesterol'], item['Calories'],
                item.get('Iodine', 0),
                nutritional_info, "", meal_times.get(item['MealType']), meal_date_obj, None,
                "ACTIVE", None, 
                "System", now, "System", now
            )
            
            all_payloads.append({
                'item': item,
                'food_id': food_id,
                'ingredients': ingredients,
                'recipe_steps': recipe_steps,
                'insert_values': insert_values
            })
            
        except Exception as e:
            print(f"Error preparing payload for {item.get('FoodItems')}: {str(e)}")
            continue
    
    # Silent mode - no extra prints, payload already shown in base_and_utility.py
    # Execute all inserts
    results = []
    for payload_data in all_payloads:
        try:
            cursor.execute(insert_query, payload_data['insert_values'])
            results.append({
                "MealType": payload_data['item']['MealType'], 
                "FoodItems": payload_data['item']['FoodItems'], 
                "FoodId": payload_data['food_id'],
                "status": "inserted"
            })
        except Exception as e:
            # Silent error handling - backend is fixing the DB schema
            results.append({
                "MealType": payload_data['item'].get('MealType'), 
                "FoodItems": payload_data['item'].get('FoodItems'), 
                "error": str(e)
            })
            continue
 
    conn.commit()
    cursor.close()

    return {"status": 200, "message": "Meal plan details updated", "results": results}

def upsert_weekly_meal_plan_logic(user_id: str, database_name: str, meal_plan_text: str = None, plan_name: str = "Weekly Meal Plan", plan_type: str = "WEEKLY", start_date: Optional[str] = None, weekly_meal_plans: Union[dict, list] = None, grocery_list: Optional[str] = None, grocery_list_json: Optional[list] = None):
    """
    Upsert weekly meal plan with optional grocery list.
    
    Args:
        grocery_list: Human-readable formatted grocery list text (legacy)
        grocery_list_json: Structured JSON array format [{"item": "...", "quantity": "..."}]
    """
    # Determine start date
    try:
        now = datetime.now(ZoneInfo("Asia/Kolkata"))
    except:
        now = datetime.now()
        
    start_date_obj = now.date()
    end_date_obj = start_date_obj + timedelta(days=6)
    patient_id = user_id

    conn = get_db_connection_dynamic(database_name)
    cursor = conn.cursor()

    try:
        delete_query = """
            DELETE FROM [dbo].[MealPlanDetails]
            WHERE [MealDate] BETWEEN ? AND ? AND [PatientId] = ?
        """
        cursor.execute(delete_query, (start_date_obj, end_date_obj, patient_id))
    
        meal_times = {
            "Breakfast": "08:00:00",
            "MorningSnack": "10:00:00",
            "Lunch": "13:00:00",
            "EveningSnack": "16:00:00",
            "Dinner": "19:00:00"
        }
    
        results = []
        
        # 🛒 Track if grocery list has been inserted (only add to first record)
        grocery_list_inserted = False
    
        def insert_day_plan(meal_date_obj, parsed_items):
            nonlocal grocery_list_inserted
            plan_name_with_date = f"{plan_name} - {meal_date_obj.strftime('%Y-%m-%d')}"
            
            for idx, item in enumerate(parsed_items, 1):
                try:
                    # 🎯 PRIORITY: Use metadata from parsed item if available
                    food_id = item.get('food_id', None)
                    ingredients_json = None
                    recipe_json = None
                    
                    if 'ingredients' in item and item['ingredients']:
                        import json
                        ingredients_json = json.dumps(item['ingredients'])
                    if 'recipe' in item and item['recipe']:
                        import json
                        recipe_json = json.dumps(item['recipe'])
                    
                    nutritional_info = (
                        f"Protein: {item['Protein']}g, Carbs: {item['Carbohydrates']}g, "
                        f"Fat: {item['Fat']}g, Fiber: {item['Fiber']}g, Sodium: {item['Sodium']}mg, "
                        f"Sugar: {item['Sugar']}g, Cholesterol: {item['Cholesterol']}mg, "
                        f"Calories: {item['Calories']}kcal, Iodine: {item.get('Iodine', 0)}mcg"
                    )
                    
                    # 🛒 NEW: Add grocery_list as JSON array to NutritionalInfo for the first record only
                    if not grocery_list_inserted and idx == 1:
                        import json
                        if grocery_list_json:
                            # Store as proper JSON array
                            grocery_json_str = json.dumps(grocery_list_json, ensure_ascii=False)
                            nutritional_info = f"{nutritional_info}\n\n[GROCERY_LIST_JSON]\n{grocery_json_str}\n[/GROCERY_LIST_JSON]"
                            logger.info(f"🛒 Grocery list JSON added to first meal record ({len(grocery_list_json)} items)")
                        elif grocery_list:
                            # Fallback to text format
                            nutritional_info = f"{nutritional_info}\n\n[GROCERY_LIST]\n{grocery_list}\n[/GROCERY_LIST]"
                            logger.info(f"🛒 Grocery list text added to first meal record (length: {len(grocery_list)} chars)")
                        grocery_list_inserted = True
    
                    cursor.execute("""
                        INSERT INTO [dbo].[MealPlanDetails]
                        ([GeneratedDate],[PatientId],[PlanName],[PlanType],[MealType],[FoodItems],[FoodId],[Ingredients],[Recipe],[household_measure],
                        [PortionWeight],[PortionWeightUnit],[Protein],[Carbohydrates],[Fat],[Fiber],[Sodium],[Sugar],
                        [Cholesterol],[Calories],[Iodine],[NutritionalInfo],[DietaryRestrictions],
                        [MealTime],[MealDate],[PreprationInstructions],[Status],[NutritionScore],
                        [CreatedBy],[CreatedAt],[UpdatedBy],[UpdatedAt])
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        now.strftime("%Y-%m-%d"), patient_id, plan_name_with_date, plan_type,
                        item['MealType'], item['FoodItems'], food_id, ingredients_json, recipe_json, item.get('household_measure'), item['PortionWeight'],
                        item['PortionWeightUnit'],
                        item['Protein'], item['Carbohydrates'], item['Fat'], item['Fiber'],
                        item['Sodium'], item['Sugar'], item['Cholesterol'], item['Calories'],
                        item.get('Iodine', 0),
                        nutritional_info, "",
                        meal_times.get(item['MealType']), meal_date_obj, None,
                        "ACTIVE", None, "System", now, "System", now
                    ))
                    results.append({
                        "MealDate": meal_date_obj.strftime("%Y-%m-%d"),
                        "MealType": item['MealType'],
                        "FoodItems": item['FoodItems'],
                        "status": "inserted"
                    })
                except Exception as e:
                    # Silent error handling - backend is fixing the DB schema
                    results.append({
                        "MealDate": meal_date_obj.strftime("%Y-%m-%d"),
                        "MealType": item.get('MealType'),
                        "FoodItems": item.get('FoodItems'),
                        "error": str(e)
                    })
    
        # 🚀 Handle different input structures
        if weekly_meal_plans:
            if isinstance(weekly_meal_plans, dict):
                # date -> text mapping
                for meal_date_str, text in weekly_meal_plans.items():
                    meal_date_obj = datetime.strptime(meal_date_str, "%Y-%m-%d").date()
                    items = parse_meal_plan_text_to_items(text)
                    insert_day_plan(meal_date_obj, items)
    
            elif isinstance(weekly_meal_plans, list):
                # Sequential list → start today
                for i, text in enumerate(weekly_meal_plans):
                    meal_date_obj = start_date_obj + timedelta(days=i)
                    items = parse_meal_plan_text_to_items(text)
                    insert_day_plan(meal_date_obj, items)
    
        # elif meal_plan_text:
        #     # One text → repeat for 7 days
        #     parsed_items = parse_meal_plan_text_to_items(meal_plan_text)
        #     for i in range(7):
        #         insert_day_plan(start_date_obj + timedelta(days=i), parsed_items)
        # else:
        #     raise HTTPException(status_code=400, detail="Provide either meal_plan_text or weekly_meal_plans.")
        elif meal_plan_text:
            # Split weekly text into per-day content
            day_wise_text = split_weekly_meal_text(meal_plan_text, start_date_obj)
    
            for meal_date_obj, day_text in day_wise_text.items():
                # Only allow 7-day window
                if meal_date_obj < start_date_obj or meal_date_obj > end_date_obj:
                    continue
    
                parsed_items = parse_meal_plan_text_to_items(day_text)
                insert_day_plan(meal_date_obj, parsed_items)
        else:
            raise {"status_code":400, "message":"Provide either meal_plan_text or weekly_meal_plans."}
        
        conn.commit()
        return {"status": 200, "message": "Weekly plan saved", "results": results}
    except Exception as e:
        return {"status": 500, "message": str(e)}
    finally:
        cursor.close()
        
def get_latest_meal_plan_logic(user_id: str, database_name: str):
    json_result = get_meal_plan_data(database_name, user_id)
    
    # Inject text representation if successful, as GroceryListTool expects 'meal_plan_text'
    if json_result.get("status") == "success" and json_result.get("data"):
        from datetime import date
        from helpers.utils import get_previous_day_meal_plan_text
        
        # Fetch text for today
        text = get_previous_day_meal_plan_text(date.today(), database_name, user_id)
        if text:
            json_result["data"][0]["meal_plan_text"] = text
            
    return json_result

def get_latest_weekly_meal_plan_logic(user_id: str, database_name: str):
    conn = get_db_connection_dynamic(database_name)
    cursor = conn.cursor()
    try:
        # Get the latest generated weekly plan date
        cursor.execute("""
            SELECT TOP 1 GeneratedDate 
            FROM [dbo].[MealPlanDetails] 
            WHERE PatientId = ? AND PlanType = 'WEEKLY' 
            ORDER BY GeneratedDate DESC
        """, (user_id,))
        row = cursor.fetchone()
        if not row:
            return {"status": 404, "message": "No weekly plan found"}
        
        latest_date = row[0]
        
        # Fetch all entries for that generation
        cursor.execute("""
            SELECT MealDate, MealType, FoodItems, FoodId, household_measure, PortionWeight, PortionWeightUnit,
                   Protein, Carbohydrates, Fat, Fiber, Sodium, Iodine, Sugar, Cholesterol, Calories
            FROM [dbo].[MealPlanDetails]
            WHERE PatientId = ? AND PlanType = 'WEEKLY' AND GeneratedDate = ?
            ORDER BY MealDate, MealType
        """, (user_id, latest_date))
        
        rows = cursor.fetchall()
        if not rows:
            return {"status": 404, "message": "No weekly plan details found"}
            
        columns = [col[0] for col in cursor.description]
        text_parts = []
        current_date = None
        
        try:
            from helpers.food_database_cache import get_food_database_cache
            food_cache = get_food_database_cache()
            foods_db = food_cache.get('foods', [])
            food_id_lookup = {f['food_id']: f for f in foods_db if 'food_id' in f}
            has_food_db = True
        except Exception:
            has_food_db = False
            food_id_lookup = {}
        
        for row in rows:
            item = dict(zip(columns, row))
            if item['MealDate'] != current_date:
                current_date = item['MealDate']
                text_parts.append(f"\n### {current_date.strftime('%Y-%m-%d')}")
            
            unit = item['PortionWeightUnit'] or ''
            measure = f" ({item['household_measure']})" if item.get("household_measure") else ""
            
            food_id = item.get('FoodId')
            grocery_suffix = ""
            if has_food_db and food_id and food_id in food_id_lookup:
                grocery_items = food_id_lookup[food_id].get('grocery_items')
                if grocery_items:
                    grocery_suffix = f" [Ingredients: {grocery_items}]"
            
            text_parts.append(
                f"- {item['FoodItems']}{measure} | Portion Weight: {item['PortionWeight']}{unit}{grocery_suffix}"
            )
            
        return {"status": 200, "data": [{"meal_plan_text": "\n".join(text_parts)}]}
        
    except Exception as e:
        return {"status": 500, "message": str(e)}
    finally:
        cursor.close()
        
# --- Helper Workers for Parallel Execution ---
def _fetch_dietary_profile_worker(user_id, database_name):
    """Worker to fetch dietary profile in its own thread/connection"""
    try:
        conn = get_db_connection_dynamic(database_name)
        cursor = conn.cursor()
        cursor.execute("EXEC [dbo].[usp_GetPatientDietaryProfileBySearch] @UserId = ?", user_id)
        row = cursor.fetchone()
        cols = [c[0] for c in cursor.description] if row else []
        data = dict(zip(cols, row)) if row else {}

        # print(f"\n--- [DEBUG] DATA FROM Dietary SP (usp_GetPatientDietaryProfileBySearch) ---\n{json.dumps(data, indent=2, default=str)}\n-----------------------------------------------------------")
        cursor.close()
        
        return data
    except Exception as e:
        print(f"Error in dietary profile worker: {e}")
        return {}

def _fetch_patient_info_worker(user_id, database_name):
    """Worker to fetch patient info in its own thread/connection"""
    try:
        conn = get_db_connection_dynamic(database_name)
        cursor = conn.cursor()
        cursor.execute("EXEC [dbo].[sp_GetPatinetInfoByUserId] @PatientId = ?", user_id)
        row = cursor.fetchone()
        cols = [c[0] for c in cursor.description] if row else []
        data = dict(zip(cols, row)) if row else {}

        # print(f"\n--- [DEBUG] DATA FROM Patient Info SP (sp_GetPatinetInfoByUserId) ---\n{json.dumps(data, indent=2, default=str)}\n-------------------------------------------------------------")
        cursor.close()
        
        return data
    except Exception as e:
        print(f"Error in patient info worker: {e}")
        return {}
    
def get_profile_logic(user_id: str, database_name: str, token: str):
    try:
        import time # Ensure time is imported
        start_total = time.time()
        
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        # --- Define Timing Wrappers ---
        # These wrappers measure exactly how long EACH parallel task takes
        def timed_dietary():
            s = time.time()
            res = _fetch_dietary_profile_worker(user_id, database_name)
            # print(f"[TIMING DIAGNOSTIC] Dietary SP: {time.time() - s:.2f}s")
            return res

        def timed_patient_info():
            s = time.time()
            res = _fetch_patient_info_worker(user_id, database_name)
            # print(f"[TIMING DIAGNOSTIC] Patient Info SP: {time.time() - s:.2f}s")
            return res

        def timed_vitals():
            s = time.time()
            # This relies on the updated fetch_vitals in utils.py
            # res = fetch_patient_dashboard(user_id, token, yesterday) 
            res = fetch_vitals(user_id, None, yesterday)  # Direct DB call to isolate vitals timing
            # print(f"[TIMING DIAGNOSTIC] Dashboard API: {time.time() - s:.2f}s")
            return res

        def timed_diagnoses():
            s = time.time()
            # This relies on the updated N+1 fix in utils.py
            res = get_patient_active_diagnoses(user_id, database_name)
            # print(f"[TIMING DIAGNOSTIC] Diagnoses DB: {time.time() - s:.2f}s")
            return res

        # --- PARALLEL EXECUTION START ---
        with ThreadPoolExecutor(max_workers=4) as executor:
            future_p1 = executor.submit(timed_dietary)
            future_p2 = executor.submit(timed_patient_info)
            future_vitals = executor.submit(timed_vitals)
            future_diagnoses = executor.submit(timed_diagnoses)

            # Gather results
            p1 = future_p1.result()
            p2 = future_p2.result()
            vitals_list = future_vitals.result()
            diagnoses = future_diagnoses.result()
        # --- PARALLEL EXECUTION END ---

        # print(f"[TIMING DIAGNOSTIC] Total Parallel Block: {time.time() - start_total:.2f}s")

        merged_profile = {**p2, **p1}

        # print(f"\n--- [DEBUG] FINAL COMBINED PROFILE DATA (Merged) ---\n{json.dumps(merged_profile, indent=2, default=str)}\n----------------------------------------------------")
        vitals_avg = average_vitals(vitals_list)
        
        profile = convert_json_to_profile(merged_profile, vitals_json=vitals_avg, diagnoses=diagnoses)
        return {"status": 200, "data": profile}

    except Exception as e:
        print(f"CRITICAL PROFILE DB ERROR: {e}")
        import traceback
        traceback.print_exc()
        return {"status": 500, "error": str(e)}

def get_cuisine_codes(conn, cuisines: list[str]) -> list[str]:
    return get_codes_from_db(
        conn=conn,
        name_col="Description",
        table=f"{MASTER_TABLE_DB_NAME}.[dbo].[Country]",
        code_col="Code",
        values=cuisines
    )

def update_profile_logic(user_id: str, database_name: str, profile_data: dict):
    try:
        # Convert dict to Pydantic model to handle validation/defaults
        payload = DietaryProfileRequest(**profile_data)
        
        conn = get_db_connection_dynamic(database_name)
        cursor = conn.cursor()

        cursor.execute("SELECT TOP 1 PatientId FROM dbo.Patient WHERE PatientUserId = ?", user_id)
        row = cursor.fetchone()
        if not row:
            return {"status": 404, "message": "Patient not found"}
        patient_id = row[0]

        # Check for dietary keys to determine if we need to merge existing data
        dietary_keys = [
            'dietary_preference_codes', 'dietary_restriction_codes', 'food_allergy_codes',
            'digestive_issue_codes', 'other_digestive_issues', 'symptom_aggravating_food_codes',
            'preferred_cuisines'
        ]
        
        # If this is a dietary update, fetch existing values for any missing keys to prevent overwriting with empty
        if any(k in profile_data for k in dietary_keys):
            missing_keys = [k for k in dietary_keys if k not in profile_data]
            if missing_keys:
                try:
                    current_dietary = _fetch_dietary_profile_worker(user_id, database_name)
                    key_to_db = {
                        'dietary_preference_codes': 'DietaryPreferences',
                        'dietary_restriction_codes': 'DietaryRestrictions',
                        'food_allergy_codes': 'FoodAllergies',
                        'digestive_issue_codes': 'DigestiveIssues',
                        'other_digestive_issues': 'OtherDigestiveIssues',
                        'symptom_aggravating_food_codes': 'SymptomAggravatingFoods'
                    }
                    for k in missing_keys:
                        if k in key_to_db:
                            db_val = current_dietary.get(key_to_db[k])
                            if db_val:
                                setattr(payload, k, extract_descriptions(db_val))
                    
                    if 'preferred_cuisines' in missing_keys:
                        current_info = _fetch_patient_info_worker(user_id, database_name)
                        cuisine_val = current_info.get('CuisineDes') or current_info.get('CursineDes')
                        if cuisine_val:
                            payload.preferred_cuisines = [c.strip() for c in cuisine_val.split(',')]
                except Exception as e:
                    print(f"Error merging existing profile for partial update: {e}")

        # Map Codes
        if payload.dietary_preference_codes:
            payload.dietary_preference_codes = [d for d in payload.dietary_preference_codes if d and d.lower() not in ['none', 'prefer not to say', '']]
            if payload.dietary_preference_codes:
                payload.dietary_preference_codes = get_codes_from_db(conn, "Description", f"{MASTER_TABLE_DB_NAME}.[Master].[DietaryPreference]", "Code", payload.dietary_preference_codes)
        if payload.dietary_restriction_codes:
            payload.dietary_restriction_codes = [r for r in payload.dietary_restriction_codes if r and r.lower() not in ['none', 'prefer not to say', '']]
            if payload.dietary_restriction_codes:
                payload.dietary_restriction_codes = get_codes_from_db(conn, "Description", f"{MASTER_TABLE_DB_NAME}.[Master].[DietaryRestriction]", "Code", payload.dietary_restriction_codes)
        if payload.food_allergy_codes:
            payload.food_allergy_codes = [a for a in payload.food_allergy_codes if a and a.lower() not in ['none', 'prefer not to say', '']]
            if payload.food_allergy_codes:
                payload.food_allergy_codes = get_codes_from_db(conn, "Description", f"{MASTER_TABLE_DB_NAME}.[Master].[FoodAllergy]", "Code", payload.food_allergy_codes)
        if payload.digestive_issue_codes:
            payload.digestive_issue_codes = [d for d in payload.digestive_issue_codes if d and d.lower() not in ['none', 'prefer not to say', '']]
            if payload.digestive_issue_codes:
                payload.digestive_issue_codes = get_codes_from_db(conn, "Description", f"{MASTER_TABLE_DB_NAME}.[Master].[DigestiveIssue]", "Code", payload.digestive_issue_codes)
        if payload.symptom_aggravating_food_codes:
            payload.symptom_aggravating_food_codes = [s for s in payload.symptom_aggravating_food_codes if s and s.lower() not in ['none', 'prefer not to say', '']]
            if payload.symptom_aggravating_food_codes:
                payload.symptom_aggravating_food_codes = get_codes_from_db(conn, "Description", f"{MASTER_TABLE_DB_NAME}.[Master].[SymptomAggravatingFood]", "Code", payload.symptom_aggravating_food_codes)
        if payload.diagnosis_codes:
            payload.diagnosis_codes = get_codes_from_db(conn, "MedicalConditionName", f"{MASTER_TABLE_DB_NAME}.[Master].[HealthDiagnoses]", "ConditionCode", payload.diagnosis_codes)
        if payload.physical_activity_level:
            result = get_codes_from_db(conn, "Description", f"{MASTER_TABLE_DB_NAME}.[Master].[PhysicalActivity]", "ActivityCode", [payload.physical_activity_level])
            if not result:
                raise HTTPException(400, f"Invalid physical activity '{payload.physical_activity_level}'")
            payload.physical_activity_level = result[0]
        # ---------- preferred cuisine --------------------------------------
        preferred_cuisine_codes = []
        
        if payload.preferred_cuisines is not None:
            if payload.preferred_cuisines:
                preferred_cuisine_codes = get_cuisine_codes(
                    conn,
                    payload.preferred_cuisines
                )
        
                if not preferred_cuisine_codes:
                    raise HTTPException(400, "Invalid preferred cuisine")
            else:
                # Explicit empty list [] → delete all cuisines
                preferred_cuisine_codes = []

        # Check if any dietary field is actually present in the input profile_data
        # This prevents running SP with defaults if only 'goals' or other fields were sent
        has_dietary_update = any(k in profile_data for k in dietary_keys)

        # ---------- call dietary SP ----------------------------------------
        if has_dietary_update and any([
            payload.dietary_preference_codes, payload.dietary_restriction_codes,
            payload.food_allergy_codes, payload.digestive_issue_codes,
            payload.other_digestive_issues, payload.symptom_aggravating_food_codes,
            payload.preferred_cuisines is not None
        ]):
            dietary_sql = f"""
            DECLARE @DietaryPreferenceCodes       [Master].[StringList];
            DECLARE @DietaryRestrictionCodes      [Master].[StringList];
            DECLARE @FoodAllergyCodes             [Master].[StringList];
            DECLARE @DigestiveIssueCodes          [Master].[StringList];
            DECLARE @SymptomAggravatingFoodCodes  [Master].[StringList];
            DECLARE @OtherDigestiveIssues         [Master].[DescriptionList];
            DECLARE @PreferredCuisineCodes        [Master].[StringList];

            {"INSERT INTO @DietaryPreferenceCodes (Value) VALUES "       + make_value_clause(payload.dietary_preference_codes)        if payload.dietary_preference_codes else ""}
            {"INSERT INTO @DietaryRestrictionCodes (Value) VALUES "      + make_value_clause(payload.dietary_restriction_codes)       if payload.dietary_restriction_codes else ""}
            {"INSERT INTO @FoodAllergyCodes (Value) VALUES "             + make_value_clause(payload.food_allergy_codes)              if payload.food_allergy_codes else ""}
            {"INSERT INTO @DigestiveIssueCodes (Value) VALUES "          + make_value_clause(payload.digestive_issue_codes)           if payload.digestive_issue_codes else ""}
            {"INSERT INTO @SymptomAggravatingFoodCodes (Value) VALUES "  + make_value_clause(payload.symptom_aggravating_food_codes)  if payload.symptom_aggravating_food_codes else ""}
            {"INSERT INTO @OtherDigestiveIssues (Description) VALUES "   + make_value_clause(payload.other_digestive_issues)          if payload.other_digestive_issues else ""}
            {"INSERT INTO @PreferredCuisineCodes (Value) VALUES "        + make_value_clause(preferred_cuisine_codes)                 if preferred_cuisine_codes else ""}

            EXEC dbo.AIUpdatePatientDietary_Profile
                 @UserId = ?, @SittingTime = ?, @PhysicalActivityLevel = ?,
                 @StressLevel = ?, @HasDigestiveIssues = ?,
                 @DietaryPreferenceCodes = @DietaryPreferenceCodes,
                 @DietaryRestrictionCodes = @DietaryRestrictionCodes,
                 @FoodAllergyCodes = @FoodAllergyCodes,
                 @DigestiveIssueCodes = @DigestiveIssueCodes,
                 @OtherDigestiveIssues = @OtherDigestiveIssues,
                 @SymptomAggravatingFoodCodes = @SymptomAggravatingFoodCodes,
                 @PreferredCuisineCodes = @PreferredCuisineCodes,
                 @CreatedBy = ?, @ModifiedBy = ?"""
            
            cursor.execute(
                dietary_sql,
                user_id,
                payload.sittingADay,
                payload.physical_activity_level,
                payload.stressLevel,
                payload.has_digestive_issues,
                "User", "User"
            )
            consume_all_results(cursor)

        # ---------- diagnosis & health SP ----------------------------------
        diagnosis_present = bool(payload.diagnosis_codes)
        health_present    = any([
            payload.sittingADay, payload.stressLevel, payload.consumeAlcoholCode,payload.physical_activity_level,
            payload.smokerCode, payload.smokingStatus, payload.drinkingStatus,
            payload.medicalReqCode, payload.uninterruptedSleepHour
        ])
        update_flag = "B" if diagnosis_present and health_present else \
                      "DIA" if diagnosis_present else \
                      "HEP" if health_present else None

        if update_flag:
            diag_tvp = ""
            if payload.diagnosis_codes:
                diag_tvp = "INSERT INTO @DiagnosisCodes (ConditionCode) VALUES " + make_value_clause(payload.diagnosis_codes)

            health_sql = f"""
            DECLARE @DiagnosisCodes [dbo].[DiagnosisCodeType];
            {diag_tvp}
            EXEC dbo.AddUpdatePatientDiagnosisAndHealthProfile
                 @PatientId = ?, @DiagnosisCodes = @DiagnosisCodes,
                 @DiagnosisNotes = ?, @Severity = ?, @DiagnosedBy = ?,
                 @MedicalReqCode = ?, @SittingADay = ?, @PhysicalActivity = ?,
                 @UninterruptedSleepHour = ?, @StressLevel = ?, @SmokerCode = ?,
                 @SmokingStatus = ?, @ConsumeAlcoholCode = ?, @DrinkingStatus = ?,
                 @CreatedBy = ?, @ModifiedBy = ?, @UpdateFlag = ?"""

            cursor.execute(
                health_sql,
                user_id,
                payload.diagnosis_notes, payload.severity, payload.diagnosed_by,
                payload.medicalReqCode, payload.sittingADay, payload.physical_activity_level,
                payload.uninterruptedSleepHour, payload.stressLevel,
                payload.smokerCode, payload.smokingStatus,
                payload.consumeAlcoholCode, payload.drinkingStatus,
                "User", "User", update_flag
            )
            consume_all_results(cursor)
        
        # Height/Weight
        if payload.height:
            cursor.execute("INSERT INTO Member.HeightRecords (RecordedDate, MemberID, HeightMeasurement, unit, Deleted, CreatedDate, CreatedBy, UserId) VALUES (?, ?, ?, 'ft', 0, ?, 'User', ?)",
                datetime.now(), patient_id, cm_to_feet_inches(payload.height), datetime.now(), user_id)
        if payload.weight:
            cursor.execute("INSERT INTO Member.WeightRecords (RecordedDate, MemberID, WeightMeasurement, unit, Deleted, CreatedDate, CreatedBy, UserId) VALUES (?, ?, ?, 'lbs', 0, ?, 'User', ?)",
                datetime.now(), patient_id, kg_to_lbs(payload.weight), datetime.now(), user_id)
        if payload.waist:
            cursor.execute("""
            INSERT INTO Member.WaistCircumference (RecordedDate, MemberID, Measurement, unit, Deleted, CreatedDate, CreatedBy, UserId) VALUES (?, ?, ?, 'In', 0, ?, 'User', ?)""",
            datetime.now(), patient_id, payload.waist, datetime.now(), user_id)
            
        conn.commit(); cursor.close();
        
        # 🔥 Clear Redis meal diversity cache when profile changes
        # This ensures fresh meal planning with updated dietary restrictions, allergies, or medical conditions
        try:
            redis_cache = RedisProfileCache()
            if redis_cache.enabled:
                clear_result = redis_cache.clear_user_meal_cache(user_id)
                if clear_result['success'] and clear_result['keys_deleted'] > 0:
                    logger.info(f"✅ Profile updated for user {user_id} - Cleared {clear_result['keys_deleted']} cached meal plans")
                else:
                    logger.info(f"✅ Profile updated for user {user_id} - No cached meal plans to clear")
            else:
                logger.debug(f"✅ Profile updated for user {user_id} - Redis cache not enabled")
        except Exception as redis_err:
            # Don't fail profile update if cache clear fails - just log warning
            logger.warning(f"⚠️ Profile updated successfully but failed to clear meal cache for user {user_id}: {redis_err}")
        
        return {"status": 200, "message": "Profile updated successfully"}
    except Exception as e:
        return {"status": 500, "internal message": str(e), "message": "Error updating profile"}

def upsert_profile_summary_logic(user_id: str, database_name: str, summary_json: dict):
    try:
        conn = get_db_connection_dynamic(database_name)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM [dbo].[AI_ProfileSummary] WHERE [user_id] = ?", user_id)
        exists = cursor.fetchone()[0]
        now = datetime.now()
        if exists:
            cursor.execute("UPDATE [dbo].[AI_ProfileSummary] SET [profile_summary] = ?, [updated_at] = ? WHERE [user_id] = ?", (json.dumps(summary_json), now, user_id))
        else:
            cursor.execute("INSERT INTO [dbo].[AI_ProfileSummary] ([user_id], [profile_summary], [created_at], [updated_at]) VALUES (?, ?, ?, ?)", (user_id, json.dumps(summary_json), now, now))
        conn.commit()
        cursor.close()
        
        return {"status": 200, "message": "Summary updated"}
    except Exception as e:
        return {"status": 500, "message": str(e)}

def get_profile_summary_logic(user_id: str, database_name: str):
    try:
        conn = get_db_connection_dynamic(database_name)
        cursor = conn.cursor()
        cursor.execute("SELECT [profile_summary] FROM [dbo].[AI_ProfileSummary] WHERE [user_id] = ?", user_id)
        row = cursor.fetchone()
        cursor.close()
        
        if row:
            return {"status": 200, "data": {"profile_summary": json.loads(row[0])}}
        return {"status": 404, "message": "No summary found"}
    except Exception as e:
        return {"status": 500, "message": str(e)}

# --- MEAL CHECKIN LOGIC ---
def meal_checkin_logic(user_id: str, database_name: str, meal_plan_text: str, consumption_date: str, consumption_time: str):
    items = parse_meal_plan_text_to_items(meal_plan_text)
    if not items:
        return {"status": 400, "message": "No valid meal items found"}
    
    # Validate that consumption_date is today only
    try:
        consumption_date_obj = datetime.strptime(consumption_date, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return {"status": 400, "message": "Invalid date format. Use YYYY-MM-DD."}
    
    today = datetime.now().date()
    if consumption_date_obj != today:
        return {"status": 403, "message": "Meal logging is only allowed for today. You cannot log meals for previous or upcoming days."}
    
    conn = get_db_connection_dynamic(database_name)
    cursor = conn.cursor()
    now = datetime.now()
    
    try:
        # Delete existing checkin for same meal/date
        cursor.execute("DELETE FROM [dbo].[UserConsumedMeals] WHERE [UserId] = ? AND [MealType] = ? AND [ConsumptionDate] = ?", (user_id, items[0]['MealType'], consumption_date))
        
        results = []
        for item in items:
            insert_query = """
                INSERT INTO [dbo].[UserConsumedMeals]
                ([UserId], [MealName], [MealType], [FoodItems],
                [ConsumptionDate], [ConsumptionTime], [PortionWeight], [Protein], [Carbohydrates],
                [Fat], [Fiber], [Sodium], [Iodine], [Sugar], [Cholesterol], [Calories], 
                [WasCompleted], [CreatedBy], [CreatedAt], [UpdatedBy], [UpdatedAt])
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'System', ?, 'System', ?)
            """
            values = (
                user_id, item['FoodItems'], item['MealType'], item['FoodItems'],
                consumption_date, consumption_time, item['PortionWeight'], item['Protein'], item['Carbohydrates'],
                item['Fat'], item['Fiber'], item['Sodium'], item.get('Iodine', 0), item['Sugar'], item['Cholesterol'], item['Calories'],
                now, now
            )
            cursor.execute(insert_query, values)
            results.append({"MealType": item['MealType'], "FoodItems": item['FoodItems'], "status": "inserted"})
        
        conn.commit()
        return {"status": 200, "message": "Meal Checked in", "results": results}
    except Exception as e:
        return {"status": 500, "message": str(e)}
    finally:
        cursor.close()
        
# --- DOCUMENT LOGIC ---
def get_blood_report_logic(user_id: str, database_name: str, tenant_id: str):
    # Needs tenant_id from token
    try:
        tenant_identifier = get_tenant_identifier(MASTER_TABLE_DB_NAME, tenant_id)
        conn = get_db_connection_dynamic(database_name)
        cursor = conn.cursor()
        cursor.execute("SELECT DocumentId, FolderId, DocumentName, Translation FROM [DocumentManagement].[Documents] WHERE UserId = ? ORDER BY CreatedDate desc", (user_id,))
        row = cursor.fetchone()
        data = []
        if row:
            sas_uri = generate_sas_uri(tenant_identifier, user_id, row.FolderId, row.DocumentId, row.DocumentName)
            data.append({"document_id": row.DocumentId, "file_path": sas_uri, "translation": row.Translation})
        cursor.close()
        
        return {"status": 200, "data": data}
    except Exception as e:
        return {"status": 500, "message": str(e)}

def upsert_blood_report_translation_logic(database_name: str, doc_id: str, translation: dict):
    try:
        conn = get_db_connection_dynamic(database_name)
        cursor = conn.cursor()
        cursor.execute("UPDATE [DocumentManagement].[Documents] SET Translation = ? WHERE DocumentId = ?", (json.dumps(translation, ensure_ascii=False), doc_id))
        conn.commit()
        cursor.close()
        
        return {"status": 200, "message": "Translation updated"}
    except Exception as e:
        return {"status": 500, "message": str(e)}   

def get_experts_data(payload: dict):
    """
    Fetch experts with optional role filter and availability.

    payload = {
        "include_availability": bool,
        "role_name": Optional[str]
    }
    """

    include_availability = payload.get("include_availability", False)
    role_name = payload.get("role_name")

    DEFAULT_ALLOWED_ROLES = [
        "0037d96f-b35e-4fca-ac80-c873197085f5",
        "0037d96f-b35e-4fca-ac80-c873197085f6",
        "0037d96f-b35e-4fca-ac80-c873197085f7"
    ]


    # 🌍 Global master DB
    conn = get_db_connection_dynamic(MASTER_TABLE_DB_NAME)
    cursor = conn.cursor()

    try:
        # 1️⃣ Resolve role IDs
        role_ids = DEFAULT_ALLOWED_ROLES.copy()

        if role_name:
            cursor.execute(
                "SELECT Id FROM AspNetRoles WHERE Name = ?",
                role_name
            )
            row = cursor.fetchone()
            if not row:
                return {
                    "filtered_by": role_name,
                    "experts": []
                }
            role_ids = [row[0]]

        # 2️⃣ Fetch experts
        placeholders = ",".join(["?"] * len(role_ids))

        cursor.execute(f"""
            SELECT 
                e.EmployeeId,
                e.UserId,
                e.EmployeeFirstName,
                e.EmployeeLastName,
                e.MeetingLink,
                e.Degree,
                e.YearOfexperience,
                e.Biography,
                r.RoleId
            FROM Employee e
            INNER JOIN AspNetUserRoles r ON e.UserId = r.UserId
            WHERE r.RoleId IN ({placeholders})
        """, role_ids)

        rows = cursor.fetchall()
        columns = [c[0] for c in cursor.description]

        experts = [dict(zip(columns, row)) for row in rows]

        # 3️⃣ Fetch availability (optional)
        if include_availability:
            for expert in experts:
                cursor.execute(
                    "EXEC usp_GetUserAvailability @UserId = ?",
                    expert["UserId"]
                )
                avail_rows = cursor.fetchall()
                avail_cols = [c[0] for c in cursor.description] if avail_rows else []

                expert["availability"] = [
                    serialize_all(dict(zip(avail_cols, r)))
                    for r in avail_rows
                ]

        return {
            "filtered_by": role_name if role_name else "default roles",
            "experts": experts
        }

    finally:
        cursor.close()
        
def get_expert_time_slots(payload: dict):
    """
    payload = {
        "user_id": str,                # expert user id (required)
        "filter_type": "date|week|month",
        "filter_value": "YYYY-MM-DD",  # required when filter_type=date
        "duration": int               # minutes
    }
    """
    filter_type = payload.get("filter_type", "date")
    filter_value = payload.get("filter_value")
    duration = payload.get("duration", 30)
    user_id = payload.get("user_id")


    # ------------------------------
    # DB connection (Global Master)
    # ------------------------------
    conn = get_db_connection_dynamic(MASTER_TABLE_DB_NAME)
    cursor = conn.cursor()

    try:
        # ------------------------------
        # Fetch expert timezone
        # ------------------------------
        cursor.execute(
            "SELECT TOP 1 TimeZoneId FROM dbo.UserAvailability WHERE UserId = ?",
            user_id
        )
        row = cursor.fetchone()
        timezone = row[0] if row and row[0] else "UTC"

        # ------------------------------
        # Date range logic
        # ------------------------------
        today = datetime.today()

        if filter_type == "date":
            if not filter_value:
                raise Exception("filter_value is required when filter_type=date")
            start_date = end_date = filter_value

        elif filter_type == "week":
            start_date = today.strftime("%Y-%m-%d")
            end_date = (today + timedelta(days=7)).strftime("%Y-%m-%d")

        elif filter_type == "month":
            start_date = today.strftime("%Y-%m-%d")
            end_date = (today + timedelta(days=30)).strftime("%Y-%m-%d")

        else:
            raise Exception("Invalid filter_type")

        # ------------------------------
        # Execute Stored Procedure
        # ------------------------------
        cursor.execute(
            """
            EXEC GetAvailableTimeSlots
                @UserId=?,
                @UserTimeZone=?,
                @StartDate=?,
                @EndDate=?,
                @DurationMinutes=?
            """,
            (user_id, timezone, start_date, end_date, duration)
        )

        rows = cursor.fetchall()
        columns = [col[0] for col in cursor.description]

        # ------------------------------
        # Serialize results
        # ------------------------------
        slots = []
        for row in rows:
            record = {}
            for col, val in zip(columns, row):
                if isinstance(val, (datetime, date)):
                    record[col] = val.isoformat()
                elif isinstance(val, time):
                    record[col] = val.strftime("%H:%M:%S")
                else:
                    record[col] = val
            slots.append(record)

        return {
            "expert_id": user_id,
            "filter_type": filter_type,
            "start_date": start_date,
            "end_date": end_date,
            "timezone_used": timezone,
            "duration_minutes": duration,
            "slot_count": len(slots),
            "slots": slots
        }

    finally:
        cursor.close()
        
def book_session_service(token: str, body: dict):
    try:
        # --------------------------
        # JWT PAYLOAD
        # --------------------------
        jwt_payload = decode_jwt_token(token)

        patient_id = jwt_payload.get("Id")
        tenant_id = jwt_payload.get("TenantId")
        tenant_db = jwt_payload.get("DataBaseName")

        if not patient_id or not tenant_id or not tenant_db:
            return {
                "status": 400,
                "message": "Token missing Id/TenantId/Database",
                "data": None
            }

        body = body or {}

        # --------------------------
        # REQUIRED FIELDS
        # --------------------------
        req_fields = [
            "ExpertId",
            "StartDateTimeUTC",
            "EndDateTimeUTC",
            "DurationMinutes",
            "Status"
        ]

        if not all(body.get(f) for f in req_fields):
            return {
                "status": 400,
                "message": f"Missing one of {req_fields}",
                "data": None
            }

        # --------------------------
        # REQUEST VALUES
        # --------------------------
        expert_id = body["ExpertId"]
        start_str = body["StartDateTimeUTC"]
        end_str = body["EndDateTimeUTC"]
        duration = int(body["DurationMinutes"])
        session_status = body["Status"]

        program_id = body.get("ProgramId")
        coverage_type = body.get("CoverageType")
        chief_complaint = body.get("ChiefComplaint")
        repeat_enabled = int(bool(body.get("RepeatEnabled", 0)))
        appointment_type = body.get("AppointmentType")
        session_type = body.get("SessionType")
        group_session_id = body.get("GroupSeassionId")
        acs_room_id = body.get("AcsRoomId")
        outlook_id = body.get("OutlookId")

        # --------------------------
        # PARSE DATETIME
        # --------------------------
        start_dt = parse_datetime(start_str)
        end_dt = parse_datetime(end_str)

        if end_dt <= start_dt:
            return {
                "status": 400,
                "message": "EndDateTimeUTC must be after StartDateTimeUTC",
                "data": None
            }

        # --------------------------
        # GET FACILITY ID (MASTER DB)
        # --------------------------
        master_conn = get_db_connection_dynamic(MASTER_TABLE_DB_NAME)
        master_cur = master_conn.cursor()
        master_cur.execute(
            "SELECT Identifier FROM Tenants WHERE TenantId = ?",
            (tenant_id,)
        )
        row = master_cur.fetchone()
        master_cur.close()
        # return_connection(master_conn, MASTER_TABLE_DB_NAME, "dynamic")

        if not row:
            return {
                "status": 404,
                "message": "Tenant not found",
                "data": None
            }

        facility_id = row[0]

        # --------------------------
        # TENANT DB CONNECTION
        # --------------------------
        conn = get_db_connection_dynamic(tenant_db)
        cursor = conn.cursor()

        cursor.execute(
            "SELECT TimeZone FROM Patient WHERE PatientUserId = ?",
            (patient_id,)
        )
        tz_row = cursor.fetchone()

        # 🔥 CLEAN DIRTY TIMEZONE DATA ('EST → EST)
        raw_tz = tz_row[0] if tz_row and tz_row[0] else "UTC"
        client_tz = raw_tz.strip().strip("'").strip('"')

        # --------------------------
        # CHECK OVERLAPPING SESSIONS
        # --------------------------
        cursor.execute(
            """
            SELECT 1
            FROM Sessions
            WHERE Status IN ('CON','RES','INP')
              AND NOT (EndDateTimeUTC <= ? OR StartDateTimeUTC >= ?)
              AND (ExpertId = ? OR PatientId = ?)
            """,
            (start_dt, end_dt, expert_id, patient_id)
        )

        if cursor.fetchone():
            cursor.close()
            
            return {
                "status": 200,
                "message": "Requested time slot is not available",
                "data": None
            }

        # --------------------------
        # INSERT SESSION
        # --------------------------
        cursor.execute(
            """
            INSERT INTO Sessions (
                Identifier, ParentSessionId,
                StartDateTimeUTC, EndDateTimeUTC,
                DurationMinutes, ClientTimeZone,
                PatientId, ExpertId,
                ProgramId, CoverageType, Status,
                FacilityId, ChiefComplaint,
                RepeatEnabled,
                CreatedAt, ModifiedAt,
                CreatedBy, ModifiedBy,
                AppointmentType, SessionType,
                GroupSeassionId, AcsRoomId, OutlookId
            )
            OUTPUT INSERTED.SessionId, INSERTED.Identifier
            VALUES (
                NEWID(), NEWID(),
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                GETDATE(), GETDATE(),
                ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                start_dt, end_dt, duration, client_tz,
                patient_id, expert_id,
                program_id, coverage_type, session_status,
                facility_id, chief_complaint, repeat_enabled,
                patient_id, patient_id,
                appointment_type, session_type,
                group_session_id, acs_room_id, outlook_id
            )
        )

        session_id, session_identifier = cursor.fetchone()

        conn.commit()
        cursor.close()
        

        return {
            "status": 200,
            "message": "Session booked successfully",
            "data": {
                "SessionId": session_id,
                "Identifier": session_identifier,
                "ExpertId": expert_id,
                "Start": start_dt.isoformat(),
                "End": end_dt.isoformat(),
                "Status": session_status
            }
        }

    except Exception as e:
        return {
            "status": 500,
            "message": f"Internal error: {str(e)}",
            "data": None
        }

def update_session_service(token: str, payload: dict):
    jwt_payload = decode_jwt_token(token)
    if not jwt_payload:
        raise Exception("Invalid token")

    patient_id = jwt_payload.get("Id")
    tenant_db = jwt_payload.get("DataBaseName")

    session_id = payload.get("session_id")
    new_status = payload.get("Status")

    if not session_id or not new_status:
        raise Exception("session_id and Status required")

    conn = get_db_connection_dynamic(tenant_db)
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE Sessions
        SET Status = ?, ModifiedAt = GETDATE(), ModifiedBy = ?
        WHERE SessionId = ?
        """,
        (new_status, patient_id, session_id),
    )

    cursor.execute("SELECT Identifier FROM Sessions WHERE SessionId = ?", session_id)
    row = cursor.fetchone()
    if not row:
        raise Exception("Session not found")

    identifier = row[0]

    if new_status == "CAN":
        cursor.execute("DELETE FROM Reminders WHERE SessionId = ?", identifier)

    conn.commit()
    cursor.close()
    

    return {"SessionId": session_id, "Status": new_status}

def upsert_weekly_fitness_plan_logic(user_id: str, database_name: str, fitness_plans_json: dict, session_duration: str = None, conn_dynamic = None, created_by: str = "AI", constraints_json: dict = None):
    """
    SUPER-OPTIMIZED UPSERT:
    1. Batches header lookup, deactivation, and wipes into one trip.
    2. Uses fast_executemany for high-speed inserts.
    3. Integrates constraints update into the same transaction.
    """
    logger.info(f"[DBUpsert] Starting optimized upsert for user {user_id}")
    
    if not session_duration:
        session_duration = "30-45 minutes"
    session_duration = str(session_duration)

    conn = conn_dynamic if conn_dynamic else get_db_connection_dynamic(database_name)
    own_conn = True if conn_dynamic is None else False
    
    max_retries = 5
    attempt = 0
    
    while attempt < max_retries:
        attempt += 1
        cursor = conn.cursor()
        t_start = time_module.time()
        
        try:
            today = date.today()
            
            # 1. BATCH HEADER & WIPE (One Round-Trip)
            t_sub = time_module.time()
            batch_init_sql = f"""
                SET NOCOUNT ON;
                DECLARE @PlanGuid UNIQUEIDENTIFIER;
                
                -- 1a. Find existing active plan
                SELECT TOP 1 @PlanGuid = GuidId 
                FROM [dbo].[PatientFitnessPlans]
                WHERE PatientId = ? AND IsActive = 1
                AND ? BETWEEN StartDate AND EndDate;
                
                IF @PlanGuid IS NOT NULL
                BEGIN
                    UPDATE [dbo].[PatientFitnessPlans]
                    SET CreatedAt = GETDATE(), SessionsPerWeek = ?, CreatedBy = 'AI', AuthorizedBy = ?
                    WHERE GuidId = @PlanGuid;
                END
                ELSE
                BEGIN
                    -- Create new header (anchor to Sunday)
                    DECLARE @Sun DATE = DATEADD(day, -(DATEDIFF(day, '19000107', ?) % 7), ?);
                    DECLARE @Sat DATE = DATEADD(day, 6, @Sun);
                    
                    INSERT INTO [dbo].[PatientFitnessPlans]
                    (PatientId, PlanName, DurationWeeks, SessionsPerWeek, SessionMinutes, StartDate, EndDate, CreatedBy, AuthorizedBy, IsActive, CreatedAt)
                    VALUES (?, 'AI Weekly Workout Plan', 1, ?, ?, @Sun, @Sat, 'AI', ?, 1, GETDATE());
                    
                    SELECT @PlanGuid = GuidId FROM [dbo].[PatientFitnessPlans] WHERE PatientId = ? AND IsActive = 1 AND StartDate = @Sun;
                END

                -- 1b. Physically DELETE all other plans (and their contents) for this user
                -- Optimized using Table Variables to avoid JOIN deadlocks
                DECLARE @OldPlanDays TABLE (DayGuid UNIQUEIDENTIFIER);
                
                INSERT INTO @OldPlanDays (DayGuid)
                SELECT D.GuidId FROM {PATIENT_FITNESS_PLAN_DAYS_TABLE} D
                JOIN [dbo].[PatientFitnessPlans] P ON D.PlanGuidId = P.GuidId
                WHERE P.PatientId = ? AND P.GuidId != @PlanGuid;

                DELETE FROM {FITNESS_PLAN_EXERCISES_TABLE}
                WHERE DayGuidId IN (SELECT DayGuid FROM @OldPlanDays);

                DELETE FROM {PATIENT_FITNESS_PLAN_DAYS_TABLE}
                WHERE GuidId IN (SELECT DayGuid FROM @OldPlanDays);

                DELETE FROM [dbo].[PatientFitnessPlans]
                WHERE PatientId = ? AND GuidId != @PlanGuid;
                
                -- 1c. Wipe old contents of the CURRENT plan (if we are updating it)
                DECLARE @CurrentPlanDays TABLE (DayGuid UNIQUEIDENTIFIER);
                
                INSERT INTO @CurrentPlanDays (DayGuid)
                SELECT GuidId FROM {PATIENT_FITNESS_PLAN_DAYS_TABLE} WHERE PlanGuidId = @PlanGuid;

                DELETE FROM {FITNESS_PLAN_EXERCISES_TABLE}
                WHERE DayGuidId IN (SELECT DayGuid FROM @CurrentPlanDays);

                DELETE FROM {PATIENT_FITNESS_PLAN_DAYS_TABLE} WHERE PlanGuidId = @PlanGuid;
                
                SELECT @PlanGuid, (SELECT IsActive FROM [dbo].[PatientFitnessPlans] WHERE GuidId = @PlanGuid);
            """
            
            sessions_count = len(fitness_plans_json) if fitness_plans_json else 3
            cursor.execute(batch_init_sql, (
                user_id, today, sessions_count, created_by, # Update path
                today, today, # New path Sunday calculation
                user_id, sessions_count, session_duration, created_by, # Insert path
                user_id, # PlanGuid retrieval
                user_id, user_id # 1b: Delete exercises, days, and other plans
            ))
            
            row = cursor.fetchone()
            plan_guid = row[0] if row else None
            is_active = row[1] if row and len(row) > 1 else 1 # Fallback to 1 if not returned
            
            if not plan_guid:
                raise ValueError("Failed to retrieve or create PlanGuid")
            
            # Close first set to prepare for inserts
            while cursor.nextset(): pass
            logger.info(f"[DBUpsert] Batch Init (Header + Wipe) took {time_module.time() - t_sub:.3f}s")

            # 2. DATA PREPARATION
            t_sub = time_module.time()
            SECTION_MAP = {"warmup": "warmup", "main_workout": "main_workout", "cooldown": "cooldown"}
            ABBREV_TO_FULL_DAY = {"Mon": "Monday", "Tue": "Tuesday", "Wed": "Wednesday", "Thu": "Thursday", "Fri": "Friday", "Sat": "Saturday", "Sun": "Sunday"}
            DAY_TO_INT_MAP = {"Sunday": 0, "Monday": 1, "Tuesday": 2, "Wednesday": 3, "Thursday": 4, "Friday": 5, "Saturday": 6}
            
            S = lambda v, l: str(v)[:l] if v else v # Truncate helper
            
            days_to_insert = []
            exercises_to_insert = []
            
            for day_key, day_data in fitness_plans_json.items():
                day_guid = str(uuid.uuid4())
                day_name = ABBREV_TO_FULL_DAY.get(day_key, day_key)
                day_order = DAY_TO_INT_MAP.get(day_name, 1)
                
                try:
                    cal_raw = "".join(filter(str.isdigit, str(day_data.get("est_total_calories", "0")).split()[0]))
                    est_total_calories = int(cal_raw) if cal_raw else 0
                except: est_total_calories = 0
                
                # Difficulty Level Formatting (Standardized Clean Strings)
                raw_diff = str(day_data.get("difficulty_level", "Beginner")).strip().lower()
                if "advanced" in raw_diff:
                    difficulty = "Advanced"
                elif "intermediate" in raw_diff:
                    difficulty = "Intermediate"
                else:
                    difficulty = "Beginner"

                days_to_insert.append((
                    day_guid, plan_guid, day_name, day_order, day_order,
                    json.dumps(day_data.get("safety_notes", [])),
                    S(day_data.get("workout_title"), 300),
                    S(day_data.get("main_workout_category"), 300),
                    difficulty,
                    S(day_data.get("warmup_duration"), 50),
                    S(day_data.get("cooldown_duration"), 50),
                    est_total_calories,
                    S(day_data.get("est_total_duration"), 50)
                ))
                
                ex_order = 1
                for section_key, db_section in SECTION_MAP.items():
                    for ex in day_data.get(section_key, []):
                        # Intensity/Calories cleanup
                        rpe = str(ex.get("intensity_rpe", "")).replace("RPE", "").strip()
                        cal_raw = "".join(filter(str.isdigit, str(ex.get("est_calories", "")).split()[0]))
                        try: est_cal = int(cal_raw) if cal_raw else 0
                        except: est_cal = 0
                        
                        rest = re.sub(r'[^\x00-\x7F]+', '-', str(ex.get("rest", "")))
                        instructions = ex.get("steps", [])
                        if isinstance(instructions, list): instructions = json.dumps(instructions)
                        
                        try: sets = int(float(ex.get("sets", 0)))
                        except: sets = 0
                        
                        vid_guid = ex.get("unique_id") or ex.get("gui_id")
                        if not (isinstance(vid_guid, str) and len(vid_guid) >= 32): vid_guid = None
                        
                        try: est_sec = int(float(ex.get("est_time_sec", 0)))
                        except: est_sec = 0

                        exercises_to_insert.append((
                            str(uuid.uuid4()), day_guid, S(ex.get("unique_id"), 500), S(db_section, 30), ex_order,
                            S(ex.get("name"), 200), sets, 
                            S(ex.get("reps") or ex.get("hold") or str(ex.get("duration", "")), 4000), 
                            S(rpe, 10), S(created_by, 500), rest, S(ex.get("equipment"), 100),
                            est_cal, vid_guid, est_sec, S(ex.get("est_time_human"), 50),
                            S(ex.get("benefit"), 1000), S(ex.get("safety_cue"), 1000),
                            S(instructions, 4000), 
                            ex.get("met_value")
                        ))
                        ex_order += 1
            
            logger.info(f"[DBUpsert] Data Prep took {time_module.time() - t_sub:.3f}s")

            # 3. FAST BATCH INSERTS
            t_sub = time_module.time()
            cursor.fast_executemany = True
            
            if days_to_insert:
                cursor.executemany(f"""
                    INSERT INTO {PATIENT_FITNESS_PLAN_DAYS_TABLE} 
                    (GuidId, PlanGuidId, DayName, DayOrder, DayOfWeek, ExpertNotes, WorkoutTitle, WorkoutCategory, DifficultyLevel, WarmupDuration, CooldownDuration, EstimatedTotalCalories, EstimatedTotalDuration)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, days_to_insert)
            
            if exercises_to_insert:
                cursor.executemany(f"""
                    INSERT INTO {FITNESS_PLAN_EXERCISES_TABLE}
                    (GuidId, DayGuidId, ExerciseIdentifier, Section, ExerciseOrder, Name, Sets, RepsOrTime, RPE, CreatedBy, Rest, Equipment, EstimatedCalories, VideoGuidId, EstimatedTimeSec, EstimatedTime, Benefit, SafetyCue, Instructions, METValue)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, exercises_to_insert)
            
            logger.info(f"[DBUpsert] Fast Batch Inserts took {time_module.time() - t_sub:.3f}s")

            # 4. CONSTRAINTS UPDATE (Integrated)
            if constraints_json:
                t_sub = time_module.time()
                const_json_str = json.dumps(constraints_json)
                cursor.execute("""
                    IF EXISTS (SELECT 1 FROM [Nauriq].[AI_Formatted_Chat_History] WHERE [user_id] = ?)
                        UPDATE [Nauriq].[AI_Formatted_Chat_History] SET [fitness_constraints] = ? WHERE [user_id] = ?;
                    ELSE
                        INSERT INTO [Nauriq].[AI_Formatted_Chat_History] ([user_id], [fitness_constraints]) VALUES (?, ?);
                """, (user_id, const_json_str, user_id, user_id, const_json_str))
                logger.info(f"[DBUpsert] Constraints integrated update took {time_module.time() - t_sub:.3f}s")
            if own_conn:
                conn.commit()
            
            logger.info(f"[DBUpsert] Transaction COMPLETE in {time_module.time() - t_start:.3f}s")
            return {
                "status": 200, 
                "message": "Optimized upsert successful", 
                "data": {
                    "plan_guid": plan_guid,
                    "is_active": is_active,
                    "start_date": str(today - timedelta(days=(today.weekday() + 1) % 7)),
                    "end_date": str(today - timedelta(days=(today.weekday() + 1) % 7) + timedelta(days=6))
                }
            }

        except Exception as e:
            try: conn.rollback()
            except: pass
            logger.error(f"[DBUpsert] Attempt {attempt} failed: {e}")
            if attempt >= max_retries:
                return {"status": 500, "message": f"Database error: {str(e)}"}
            # Exponential backoff jitter for high-contention scenarios
            base_sleep = attempt * 2.5
            sleep_time = base_sleep + random.uniform(0, attempt * 2.5)
            logger.warning(f"[DBUpsert] Deadlock detected. Sleeping for {sleep_time:.2f}s before attempt {attempt+1}/{max_retries}")
            time_module.sleep(sleep_time)
        finally:
            cursor.close()

def get_latest_weekly_fitness_plan_logic(user_id: str, database_name: str):
    """
    Retrieves the latest active weekly workout plan for a user in JSON format.
    Sync version for db_logic.
    """
    conn = get_db_connection_dynamic(database_name)
    cursor = conn.cursor()
    try:
        # 1. Get Latest Active Plan based on Date Range
        try:
             today_str = datetime.now(timezone.utc).date().isoformat()
        except:
             today_str = date.today().isoformat()
             
        cursor.execute("""
            SELECT TOP 1 GuidId, PlanName, DurationWeeks, SessionsPerWeek, 
                   SessionMinutes, StartDate, EndDate, CreatedAt, IsActive
            FROM PatientFitnessPlans
            WHERE PatientId = ? AND IsActive = 1
              AND ? BETWEEN StartDate AND EndDate
            ORDER BY CreatedAt DESC
        """, (user_id, today_str))
        
        plan_row = cursor.fetchone()
        if not plan_row:
            return {"status": 404, "message": "No active workout plan found for today."}
            
        plan_guid = plan_row[0]
        
        # Helper to safely serialize dates
        def safe_str(val):
            return str(val) if val else ""

        formatted_plan = {} 
        plan_metadata = {
            "weekly_id": plan_guid,
            "plan_name": plan_row[1],
            "start_date": safe_str(plan_row[5]),
            "end_date": safe_str(plan_row[6]),
            "session_duration": plan_row[4],
            "is_active": plan_row[8]
        }
        
        # 2. Get Days (Joined with PlanDays)
        cursor.execute("""
            SELECT GuidId, DayName, DayOrder, DayOfWeek, ExpertNotes, WorkoutTitle, WorkoutCategory, DifficultyLevel, WarmupDuration, CooldownDuration, EstimatedTotalCalories, EstimatedTotalDuration
            FROM PatientFitnessPlanDays
            WHERE PlanGuidId = ?
            ORDER BY DayOrder ASC
        """, (plan_guid,))
        
        days = cursor.fetchall()
        
        for day in days:
            day_guid = day[0]
            day_name = day[1] 
            
            # Initialize the day structure 
            day_obj = {
                "day_name": day_name,
                "workout_title": day[5],
                "main_workout_category": day[6],
                "difficulty_level": day[7],
                "warmup_duration": day[8],
                "cooldown_duration": day[9],
                "est_total_calories": day[10],
                "est_total_duration": day[11],
                "warmup": [],
                "main_workout": [],
                "cooldown": [],
                "safety_notes": json.loads(day[4]) if day[4] else []
            }
            
            # 3. Get Exercises for this Day
            cursor.execute("""
                SELECT Name, Sets, RepsOrTime, RPE, Benefit, Instructions, 
                       Rest, Equipment, EstimatedCalories, SafetyCue, Section, VideoGuidId,
                       ExerciseIdentifier, EstimatedTimeSec, EstimatedTime
                FROM FitnessPlanExercises
                WHERE DayGuidId = ?
                ORDER BY ExerciseOrder ASC
            """, (day_guid,))
            
            exercises = cursor.fetchall()
            
            for ex in exercises:
                section = ex[10] # e.g. "warmup", "main_workout"
                
                ex_obj = {
                    "name": ex[0],
                    "sets": ex[1],
                    "reps": ex[2],
                    "intensity_rpe": ex[3],
                    "benefit": ex[4],
                    "steps": [ex[5]] if ex[5] else [], # Wrap instructions in list
                    "rest": ex[6],
                    "equipment": ex[7],
                    "est_calories": ex[8],
                    "safety_cue": ex[9],
                    "gui_id": ex[11],
                    "unique_id": ex[12],
                    "est_time_sec": ex[13],
                    "est_time_human": ex[14]
                }
                
                # Assign to correct section list
                if section in day_obj:
                    day_obj[section].append(ex_obj)
            
            # Use day name (e.g., "Monday") as key in response
            formatted_plan[day_name] = day_obj
            
        return {
            "status": 200, 
            "message": "Active plan retrieved", 
            "data": {
                "meta": plan_metadata,
                "days": formatted_plan
            }
        }
        
    except Exception as e:
        return {"status": 500, "message": str(e)}
    finally:
        cursor.close()
        conn.close()

def get_latest_fitness_plan_logic(user_id: str, database_name: str):
    """
    Retrieves ONLY the current day's workout plan from the latest active weekly plan.
    Sync version for db_logic compatibility.
    """
    try:
        # 1. Get Weekly Plan
        weekly_result = get_latest_weekly_fitness_plan_logic(user_id, database_name)

        if weekly_result["status"] != 200:
            return weekly_result

        weekly_data = weekly_result["data"]
        days = weekly_data.get("days", {})

        # 2. Determine Current Day (UTC per project standard)
        try:
             today_name = datetime.now(timezone.utc).strftime("%A")
        except:
             today_name = date.today().strftime("%A")

        # 3. Filter for Today
        if today_name in days:
            daily_plan = days[today_name]
            # Wrap in expected response format - mimicking structure but for single day
            return {
                "status": 200,
                "message": f"Active workout plan found for {today_name}.",
                "data": {
                    "meta": weekly_data.get("meta"),
                    "days": {today_name: daily_plan}
                }
            }
        else:
            return {
                "status": 404,
                "message": f"No workout scheduled for today ({today_name}) in the active plan.",
                "data": None
            }

    except Exception as e:
        return {"status": 500, "message": str(e)}