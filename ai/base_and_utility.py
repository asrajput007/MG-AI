from collections import defaultdict
import csv
import os
from dotenv import load_dotenv
from .tool_core import BaseTool, ToolType, ToolResult, LLMService
import json
import asyncio
import re
import logging
import requests
from datetime import date
from functools import partial
from typing import List, Optional, Dict, Tuple, Any
from rapidfuzz import process, fuzz
from openai import AsyncAzureOpenAI 
import helpers.db_logic as db_logic
import jwt
from pydantic import BaseModel

logger = logging.getLogger(__name__)
load_dotenv()
query_classification_config_raw = os.getenv("OPENAI_WEEKLY_PLAN_CONFIG")
if not query_classification_config_raw:
    query_classification_config_raw = "{}"
OPENAI_WEEKLY_PLAN_CONFIG = json.loads(query_classification_config_raw)
class DatabasePersistenceTool(BaseTool):
    def __init__(self, api_base_url: str = None): 
        super().__init__(
            ToolType.DATABASE_PERSISTENCE.value,
            "Saves/retrieves user data directly from the database functions."
        )
        self.api_base_url = api_base_url

    def _decode_token(self, token: str):
        """Helper to extract user context from token without validation (assumed valid by endpoint)"""
        try:
            if token.lower().startswith("bearer "):
                token = token.split(" ")[1]
            decoded = jwt.decode(token, options={"verify_signature": False})
            return decoded.get("Id"), decoded.get("DataBaseName"), decoded.get("TenantId")
        except Exception as e:
            logger.error(f"Token decoding failed: {e}")
            return None, None, None

    async def execute(self, query: str = "", operation: str = None, **kwargs) -> ToolResult:

        if not operation:
            operation = self._determine_operation_from_query(query, kwargs)
            if not operation:
                logger.error(f"Could not determine database operation from query: '{query}'")
                return ToolResult(
                    success=False,
                    data=None,
                    error="I couldn't determine what database action you want. Please be more specific."
                )
        
        valid_operations = [
            "upsert_meal_plan", "upsert_weekly_meal_plan", "get_latest_meal_plan",
            "get_latest_weekly_meal_plan", "get_profile", "update_profile",
            "upsert_profile_summary", "get_profile_summary",
            "get_latest_blood_report_analysis", "save_blood_report_analysis",
            "meal_checkin", "get_all_blood_reports"
        ]
        
        if operation not in valid_operations:
            logger.error(f"Invalid database operation: '{operation}'")
            return ToolResult(
                success=False,
                data=None,
                error=f"Invalid database operation: {operation}"
            )
        
        logger.info(f" DatabasePersistenceTool executing operation: '{operation}'")

        loop = asyncio.get_running_loop()
        func_to_run = partial(self._execute_sync, operation=operation, **kwargs)
        return await loop.run_in_executor(None, func_to_run)
    
    def _determine_operation_from_query(self, query: str, kwargs: Dict) -> Optional[str]:

        if not query:
            if 'meal_plan_text' in kwargs:
                return 'upsert_meal_plan'
            if 'profile_data' in kwargs:
                return 'update_profile'
            return None
        
        query_lower = query.lower()
        
        if 'weekly' in query_lower:
            if any(kw in query_lower for kw in ['save', 'store', 'persist']):
                return 'upsert_weekly_meal_plan'
            return 'get_latest_weekly_meal_plan'
        
        if any(kw in query_lower for kw in ['save', 'store', 'persist', 'remember']):
            if any(kw in query_lower for kw in ['meal plan', 'diet plan', 'plan']):
                return 'upsert_meal_plan'
            if any(kw in query_lower for kw in ['profile', 'my info', 'my data']):
                return 'update_profile'
            if any(kw in query_lower for kw in ['blood report', 'lab report']):
                return 'save_blood_report_analysis'
        
        if any(kw in query_lower for kw in ['get', 'show', 'retrieve', 'fetch', 'load']):
            if any(kw in query_lower for kw in ['meal plan', 'diet plan', 'plan']):
                return 'get_latest_meal_plan'
            if any(kw in query_lower for kw in ['profile', 'my info', 'my data']):
                return 'get_profile'
            if any(kw in query_lower for kw in ['blood report', 'lab report']):
                return 'get_latest_blood_report_analysis'
        
        if any(kw in query_lower for kw in ['ate', 'eaten', 'consumed', 'check-in', 'checkin', 'log meal']):
            return 'meal_checkin'
        
        logger.warning(f"Could not determine operation from query: '{query}'")
        return None

    def _execute_sync(self, operation: str, **kwargs) -> ToolResult:
        token = kwargs.get("token")
        if not token:
            return ToolResult(success=False, data=None, error="Authorization token required.")
        
        user_id, db_name, tenant_id = self._decode_token(token)
        if not user_id or not db_name:
            return ToolResult(success=False, data=None, error="Invalid Token: Missing User Id or DB Name")

        try:
            if operation == "upsert_meal_plan":
                # Get parameters
                meal_plan_text = kwargs.get("meal_plan_text")
                plan_name = kwargs.get("plan_name", "Daily Meal Plan")
                plan_type = kwargs.get("plan_type", "DAILY")
                meal_date = kwargs.get("meal_date")
                
                # Parse meal plan text to extract items and enrich with food details
                from helpers.utils import parse_meal_plan_text_to_items
                from helpers.food_database_cache import get_food_database_cache
                import json
                import re
                
                items = parse_meal_plan_text_to_items(meal_plan_text)
                
                # Fetch FoodId, Ingredients, Recipe for each item
                food_cache = get_food_database_cache()
                foods_data = food_cache.get_foods_data()
                foods_list = foods_data.get('foods', []) if foods_data else []
                
                enriched_text = meal_plan_text
                
                for item in items:
                    food_name = item['FoodItems']
                    food_name_lower = food_name.lower().strip()
                    food_id = None
                    food_ingredients = []
                    food_recipe = []
                    
                    # Try to find food in database
                    for food_item in foods_list:
                        common_name = food_item.get('common_name', '').lower().strip()
                        if food_name_lower == common_name or food_name_lower in common_name or common_name in food_name_lower:
                            food_id = food_item.get('food_id')
                            recipe_data = food_item.get('recipe', {})
                            food_ingredients = recipe_data.get('ingredients', [])
                            food_recipe = recipe_data.get('steps', [])
                            break
                    
                    # Find the food item line and append food_id, ingredients, recipe
                    # Pattern: - Food Name | ... | Calories: XXXkcal
                    # We need to find this exact line and append to it
                    calories_text = f"Calories: {item['Calories']}kcal"
                    
                    # Find the position and append
                    if calories_text in enriched_text:
                        # Create enrichment string
                        enrichment = f' | "food_id": "{food_id}" | "ingredients": {json.dumps(food_ingredients, ensure_ascii=False)} | "recipe": {json.dumps(food_recipe, ensure_ascii=False)} |'
                        
                        # Split by lines, find matching line, append
                        lines = enriched_text.split('\n')
                        for i, line in enumerate(lines):
                            # Check if this line contains the food name AND calories
                            if food_name in line and calories_text in line:
                                # Append enrichment to this line
                                lines[i] = line.rstrip() + enrichment
                                break
                        
                        enriched_text = '\n'.join(lines)
                
                # Build payload with enriched meal_plan_text
                payload = {
                    "meal_plan_text": enriched_text,
                    "plan_name": plan_name,
                    "plan_type": plan_type,
                    "meal_date": meal_date
                }
                
                # Print payload
                print("\n" + "="*100)
                print("📦 PAYLOAD (food_id, ingredients, recipe embedded in meal_plan_text)")
                print("="*100)
                print(json.dumps(payload, indent=2, ensure_ascii=False))
                print("="*100 + "\n")
                
                result = db_logic.upsert_meal_plan_details_logic(
                    user_id=user_id,
                    database_name=db_name,
                    meal_plan_text=enriched_text,
                    plan_name=plan_name,
                    plan_type=plan_type,
                    meal_date=meal_date
                )
                if result.get("status") == 200:
                    return ToolResult(success=True, data=result, metadata={"confirmation": "Meal plan saved."})
                return ToolResult(success=False, data=None, error=result.get("message"))

            elif operation == "upsert_weekly_meal_plan":
                import json
                from helpers.utils import split_weekly_meal_text, parse_meal_plan_text_to_items
                from datetime import datetime, timedelta
                
                weekly_payload = kwargs.get("meal_plan_text")
                grocery_list = kwargs.get("grocery_list", None)  # Get text format
                grocery_list_json = kwargs.get("grocery_list_json", None)  # 🛒 Get JSON array format
                
                print("\n" + "="*100)
                print("📦 WEEKLY MEAL PLAN PAYLOAD (RAW TEXT)")
                print("="*100)
                print(f"\nPayload Length: {len(weekly_payload) if weekly_payload else 0} characters")
                print("\n--- RAW MEAL PLAN TEXT ---")
                print(weekly_payload)
                print("="*100 + "\n")
                
                # 🛒 Show grocery list if available
                if grocery_list or grocery_list_json:
                    print("\n" + "="*100)
                    print("🛒 GROCERY LIST (TO BE SAVED WITH MEAL PLAN)")
                    print("="*100)
                    if grocery_list_json:
                        print(f"\n✅ JSON Array Format: {len(grocery_list_json)} items")
                        print("\n--- GROCERY LIST JSON ---")
                        print(json.dumps(grocery_list_json, indent=2, ensure_ascii=False))
                    if grocery_list:
                        print(f"\nText Format Length: {len(grocery_list)} characters")
                        print("\n--- GROCERY LIST TEXT ---")
                        print(grocery_list)
                    print("="*100 + "\n")
                
                # 🔥 NEW: Show structured payload that will be saved to database (matching daily format)
                try:
                    now = datetime.now()
                    start_date_obj = now.date()
                    end_date_obj = start_date_obj + timedelta(days=6)
                    
                    # Build unified payload structure (matching daily meal plan format)
                    unified_payload = {
                        "meal_plan_text": weekly_payload,
                        "grocery_list": grocery_list if grocery_list else None,  # Text format (legacy)
                        "grocery_list_json": grocery_list_json if grocery_list_json else [],  # 🛒 JSON array format
                        "plan_name": f"Weekly Meal Plan",
                        "plan_type": "WEEKLY",
                        "start_date": start_date_obj.strftime("%Y-%m-%d"),
                        "end_date": end_date_obj.strftime("%Y-%m-%d"),
                        "total_days": 7,
                        "user_id": user_id,
                        "database": db_name
                    }
                    
                    print("\n" + "="*100)
                    print("📦 UNIFIED STRUCTURED PAYLOAD (DATABASE FORMAT - Same as Daily)")
                    print("="*100)
                    print(f"User ID: {user_id}")
                    print(f"Database: {db_name}")
                    print(f"Date Range: {start_date_obj} to {end_date_obj}")
                    print(f"Total Days: 7")
                    print(f"Has Grocery List Text: {'Yes' if grocery_list else 'No'}")
                    print(f"Has Grocery List JSON: {'Yes' if grocery_list_json else 'No'} ({len(grocery_list_json) if grocery_list_json else 0} items)")  # 🛒
                    print("\n--- STRUCTURED JSON PAYLOAD ---")
                    print(json.dumps(unified_payload, indent=2, ensure_ascii=False, default=str))
                    print("="*100 + "\n")
                    
                except Exception as parse_error:
                    logger.error(f"Failed to build weekly payload for display: {parse_error}")
                    print(f"\n⚠️ Could not build structured payload: {parse_error}\n")
                
                # 🛒 Pass both text and JSON formats of grocery list
                result = db_logic.upsert_weekly_meal_plan_logic(
                    user_id=user_id,
                    database_name=db_name,
                    meal_plan_text=weekly_payload,
                    grocery_list=grocery_list,  # Text format
                    grocery_list_json=grocery_list_json  # 🛒 JSON array format
                )
                if result.get("status") == 200:
                    return ToolResult(success=True, data=result, metadata={"confirmation": "Weekly plan saved."})
                return ToolResult(success=False, data=None, error=result.get("message"))

            elif operation == "get_latest_weekly_meal_plan":
                result = db_logic.get_latest_weekly_meal_plan_logic(user_id, db_name)
                if result.get("status") == 200:
                    return ToolResult(success=True, data=result)
                return ToolResult(success=False, data=None, error=result.get("message"))

            elif operation == "get_latest_meal_plan":
                data = db_logic.get_latest_meal_plan_logic(user_id, db_name)
                if data:
                    return ToolResult(success=True, data=data)
                return ToolResult(success=True, data=None, metadata={"message": "No plans found."})

            elif operation == "get_profile":
                result = db_logic.get_profile_logic(user_id, db_name, token)
                if result.get("status") == 200:
                    return ToolResult(success=True, data=result)
                return ToolResult(success=False, data=None, error=result.get("error"))

            elif operation == "update_profile":
                result = db_logic.update_profile_logic(user_id, db_name, kwargs.get("profile_data"))
                if result.get("status") == 200:
                    return ToolResult(success=True, data=result, metadata={"confirmation": "Profile updated."})
                return ToolResult(success=False, data=None, error=result.get("message"))

            elif operation == "meal_checkin":
                data = kwargs.get("checkin_data", {})
                result = db_logic.meal_checkin_logic(
                    user_id, db_name, 
                    data.get("meal_plan_text"), 
                    data.get("ConsumptionDate"), 
                    data.get("ConsumptionTime")
                )
                if result.get("status") == 200:
                    return ToolResult(success=True, data=result)
                return ToolResult(success=False, data=None, error=result.get("message"))

            elif operation == "upsert_profile_summary":
                summary_data = kwargs.get("summary_data")
                payload = summary_data.get("profile_summary", summary_data)
                result = db_logic.upsert_profile_summary_logic(user_id, db_name, payload)
                if result.get("status") == 200:
                    return ToolResult(success=True, data=result)
                return ToolResult(success=False, data=None, error=result.get("message"))

            elif operation == "get_profile_summary":
                result = db_logic.get_profile_summary_logic(user_id, db_name)
                if result.get("status") == 200:
                    return ToolResult(success=True, data=result)
                return ToolResult(success=True, data=None) 

            elif operation == "get_latest_blood_report_analysis":
                result = db_logic.get_blood_report_logic(user_id, db_name, tenant_id)
                if result.get("status") == 200:
                    return ToolResult(success=True, data=result)
                return ToolResult(success=False, data=None, error=result.get("message"))

            elif operation == "save_blood_report_analysis":
                payload = kwargs.get("analysis_data", {})
                result = db_logic.upsert_blood_report_translation_logic(
                    db_name, payload.get("doc_id"), payload.get("translation")
                )
                if result.get("status") == 200:
                    return ToolResult(success=True, data=result)
                return ToolResult(success=False, data=None, error=result.get("message"))

            else:
                return ToolResult(success=False, data=None, error=f"Unknown operation: {operation}")

        except Exception as e:
            logger.error(f"DB Logic Error in {operation}: {e}")
            return ToolResult(success=False, data=None, error=str(e))
        

    
class QueryClassifierTool(BaseTool):
    def __init__(self, llm_service: LLMService, tools: Dict[str, BaseTool]):
        super().__init__(ToolType.QUERY_CLASSIFIER.value, "Classifies user queries into the most appropriate tool type using Azure OpenAI.")
        self.llm_service = llm_service 
        self.tools = tools
        self.classification_client = None
        classification_api_key = os.getenv("CLASSIFICATION_MODEL_API_KEY")
        classification_endpoint = os.getenv("CLASSIFICATION_MODEL_ENDPOINT")
        classification_api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
        if classification_api_key and classification_endpoint:
            self.classification_client = AsyncAzureOpenAI(
                api_key=classification_api_key,
                azure_endpoint=classification_endpoint,
                api_version=classification_api_version
            )
            logger.info("[QueryClassifier] Using Azure classification model endpoint")
        else:
            logger.warning("[QueryClassifier] Azure classification model configuration is incomplete")
        
        self.csv_patterns = []
    
    def _load_csv_patterns(self) -> List[Dict[str, str]]:
        
        csv_path = "datasets/query_classification.csv"
        patterns = []
        
        csv_to_enum_mapping = {
            "workout_plan_adjuster": "workout_adjuster_tool",
            "database_persistence": "database_persistence_tool",
        }
        
        try:
            if not os.path.exists(csv_path):
                logger.warning(f"CSV classification database not found at {csv_path}")
                return patterns
            
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get('Tool_Name') and row.get('User_Query'):
                        tool_name_lower = row['Tool_Name'].strip().lower()
                        tool_name_mapped = csv_to_enum_mapping.get(tool_name_lower, tool_name_lower)
                        
                        patterns.append({
                            'tool': tool_name_mapped,
                            'pattern': row['User_Query'].strip(),
                            'pattern_lower': row['User_Query'].strip().lower()
                        })
            
            logger.info(f"[QueryClassifier] Loaded {len(patterns)} patterns from CSV database")
            return patterns
        except Exception as e:
            logger.error(f"Error loading CSV patterns: {e}")
            return patterns
    
    def _match_csv_pattern(self, query: str) -> Optional[str]:

        if not self.csv_patterns:
            return None
        
        query_clean = query.strip()
        query_lower = query_clean.lower()
        
        for pattern_obj in self.csv_patterns:
            if pattern_obj['pattern'] == query_clean:
                logger.info(f"[CSV Match] EXACT: '{query_clean}' -> {pattern_obj['tool']}")
                return pattern_obj['tool']
        
        for pattern_obj in self.csv_patterns:
            if pattern_obj['pattern_lower'] == query_lower:
                logger.info(f"[CSV Match] EXACT_LOWER: '{query_lower}' -> {pattern_obj['tool']}")
                return pattern_obj['tool']
        
        for pattern_obj in self.csv_patterns:
            if pattern_obj['pattern_lower'] in query_lower or query_lower in pattern_obj['pattern_lower']:
                logger.info(f"[CSV Match] SUBSTRING: '{query_lower}' contains/in '{pattern_obj['pattern_lower']}' -> {pattern_obj['tool']}")
                return pattern_obj['tool']
        
        logger.info(f"[CSV Match] NO_MATCH: '{query_clean}' - falling back to LLM")
        return None

    def _build_system_prompt(self, tools: Dict[str, BaseTool], profile_summary_json: Optional[Dict] = None, last_agent_context: Optional[Dict] = None) -> str:        
        summary_context = "No user summary available."
        if profile_summary_json:
            summary_context = f"""**User Profile Summary (for additional context):**
```json
{json.dumps(profile_summary_json, indent=2)}
```
"""

        active_state_context = ""
        if last_agent_context:
            active_states = []
            if last_agent_context.get("meal_check_in_phase"):
                active_states.append(f"System is in meal check-in flow (phase: {last_agent_context['meal_check_in_phase']})")
            if last_agent_context.get("awaiting_db_confirmation"):
                active_states.append("System is awaiting database save confirmation from user")
            if last_agent_context.get("awaiting_shopping_followup"):
                active_states.append("System is awaiting shopping list followup from user")
            if last_agent_context.get("awaiting_meal_plan_confirmation_for_vitals"):
                active_states.append("System is awaiting meal plan confirmation for vitals from user")
            if last_agent_context.get("awaiting_food_add_confirmation"):
                active_states.append("System is awaiting food addition confirmation from user")
            
            if active_states:
                active_state_context = f"\n\n**IMPORTANT - Active System State:**\n" + "\n".join(f"- {state}" for state in active_states) + "\nIf the user's message is a response to any of these active states (e.g., 'yes', 'no', 'ok', 'sure'), classify based on the IMPLIED ACTION, not as GREETING.\n"

        prompt_header = f"""You are an expert AI assistant for classifying user queries. Analyze the user's query, chat history, and profile summary to determine the most appropriate **SINGLE** tool type.

{summary_context}

**Key Distinction (VERY IMPORTANT):**
- **Vital Signs vs. Profile Updates:** ANY query where a user states a specific vital sign reading (e.g., "my blood pressure is 140/90") MUST be classified as `vital_advisor`. The `profile_updater` is for non-vital info like allergies or diseases.
- **Stating a fact (informational, non-vital):** Use 'profile_updater'. Examples: "I have diabetes," "I am allergic to peanuts."
- **Asking for advice (question):** Use 'disease_advisor' or 'nutrition_analyzer'. Examples: "What should I eat for diabetes?", "Can I eat peanuts?"
- **Acknowledgement vs Greeting Rule:** Acknowledgements ("ok", "yes", "no", "sure", "fine", "done") are NOT greetings. They are classified based on the action they confirm or reject. If no action is inferable, classify as GREETING.

**Dialogue Act Rule (CRITICAL):**
If the user message is a RESPONSE to an ACTIONABLE assistant message
(e.g., confirmations, acknowledgements, acceptance, rejection),
it MUST be classified based on the IMPLIED ACTION — not as GREETING.

NOTE: The following examples are illustrative ONLY.
They are provided to guide the reasoning process and decision logic.
Do NOT memorize, pattern-match, or limit classification to these examples.
Always generalize based on dialogue intent, actionability, and context.
Examples:
- Assistant: "Do you want to log this meal?"
  User: "yes" → MEAL_CHECK_IN
- Assistant: "Should I add this?"
  User: "okay" → MEAL_PLAN_ADJUSTER
- Assistant: "Do you want to book a session?"
  User: "sure" → SESSION_BOOKING
Second-order continuation example (IMPORTANT):
- Assistant: "If you'd like me to proceed, please reply Yes."
  User: "no" → (action is rejected; flow is resolved)
- Assistant: "All good. Let me know when you'd like to continue."
  User: "okay" → GREETING

Short answers inherit intent from the previous assistant message.

**🎯 CRITICAL ROUTING RULES - ACTION-BASED PRIORITY:**

1. **ACTION VERBS TAKE PRECEDENCE OVER NOUNS:**
   - If the user says "swap", "replace", "change", "remove", "add", "modify", "adjust" → Focus on the ACTION, not the food type mentioned
   - Example: "replace vegan cheese" → meal_plan_adjuster (NOT food_analyzer, even though 'vegan' is mentioned)
   - Example: "swap tofu with chicken" → meal_plan_adjuster (NOT nutrition_analyzer)
   - Example: "change my dinner to indian food" → meal_plan_adjuster (NOT general_query)

2. **CONTEXT MATTERS FOR DOMAIN CLASSIFICATION:**
   - "Is this vegan?" → food_analyzer (checking safety/ingredients)
   - "I am vegan" → profile_updater (setting dietary preference)
   - "Give me vegan meal plan" → meal_plan_generator (generating new plan)
   - "Replace chicken with vegan option" → meal_plan_adjuster (modifying existing plan)

3. **MULTI-ACTION QUERIES:**
   If the user query requires MULTIPLE actions (e.g., "Update my weight and generate a meal plan", "I am allergic to nuts and want a low-carb plan"), you must determine the PRIMARY action. However, indicate complexity by appending "|COMPLEX" to the tool name.
   
   Examples:
   - "Update my weight to 70kg and create a meal plan" → profile_updater|COMPLEX
   - "I have diabetes, give me a meal plan" → profile_updater|COMPLEX
   - "Show my profile and generate a meal plan" → profile_retriever|COMPLEX

**🚨 COMMON MISCLASSIFICATION PREVENTION (READ CAREFULLY):**

⚠️ **DAILY vs WEEKLY MEAL PLANS** - These are DIFFERENT entities:
   - **meal_plan_generator** = SINGLE DAY / TODAY / DAILY (default when no time period mentioned)
     • "create a meal plan" → meal_plan_generator ✅
     • "what should I eat today" → meal_plan_generator ✅
     • "give me a plan" (no time specified) → meal_plan_generator ✅
   
   - **weekly_meal_plan_generator** = ONLY for EXPLICIT weekly/7-day/multi-day requests
     • MUST contain keywords: "week", "weekly", "7 days", "7-day", "multi-day"
     • "give me a weekly meal plan" → weekly_meal_plan_generator ✅
     • "plan for the week" → weekly_meal_plan_generator ✅
     • "7-day meal plan" → weekly_meal_plan_generator ✅

⚠️ **PROFILE vs MEAL PLAN HISTORY:**
   - **profile_retriever** = View PERSONAL INFO (allergies, preferences, conditions, settings)
     • "show my profile", "what are my dietary preferences" → profile_retriever ✅
   
   - **history_retriever** = View SAVED MEAL PLANS (current or past food plans)
     • "show my meal plan", "what did I eat yesterday" → history_retriever ✅

⚠️ **NEW PLAN vs MODIFY PLAN vs VIEW PLAN (MOST CRITICAL RULE):**
   - **Generating NEW:** "create plan", "generate a meal plan", "make me a plan" → meal_plan_generator / weekly_meal_plan_generator
   - **Modifying EXISTING:** "swap chicken", "replace breakfast", "change dinner" → meal_plan_adjuster
   - **Viewing SAVED:** "show my plan", "view my plan", "what is my plan", "show my today's meal plan", "get my meal plan", "my meal plan" → history_retriever
   
   🔑 **THE GOLDEN RULE:** If query has (show|view|see|display|get|what is) + (my) + (plan|meal plan|meals) → ALWAYS history_retriever.
   Even with extra words like "today's", "current", "saved", "latest" — it is STILL history_retriever.
   ONLY use meal_plan_generator when user says "create", "generate", "make", "build", or "what should I eat".

**Available Tool Types:**
"""
        tool_descriptions = []
        tool_order = [
            ToolType.GREETING.value,
            ToolType.PROFILE_UPDATER.value,
            ToolType.PROFILE_RETRIEVER.value,
            ToolType.GOAL_UPDATER.value,
            ToolType.PROFILE_SUMMARY.value,
            ToolType.BLOOD_REPORT_ANALYZER.value,
            ToolType.PANEL_QUERY.value, 
            ToolType.BLOOD_REPORT_QUERY.value,
            ToolType.MEAL_PLAN_ADJUSTER.value,
            ToolType.MEAL_PLAN_GENERATOR.value,
            ToolType.WEEKLY_MEAL_PLAN_GENERATOR.value,
            ToolType.NUTRITION_ANALYZER.value,
            ToolType.SPECIAL_MEAL_PLAN_GENERATOR.value,
            ToolType.DISEASE_ADVISOR.value,
            ToolType.VITAL_ADVISOR.value,
            ToolType.HISTORY_RETRIEVER.value,
            ToolType.MEAL_CHECK_IN.value,
            ToolType.GENERAL_QUERY.value,
            ToolType.MEAL_INGREDIENTS.value,
            ToolType.GREETING.value,
            ToolType.OTHER.value,
            ToolType.CRAVING_ASSISTANT.value,
            ToolType.AUDIOSUMMARY.value,
            ToolType.GROCERY_LIST.value,
            ToolType.FITNESS_PLAN_GENERATOR.value,
            ToolType.WATER_STEP_ADVISOR.value,
            ToolType.SESSION_BOOKING.value,
            ToolType.WORKOUT_PLAN_ADJUSTER.value,
            ToolType.KNOWLEDGE_BASE.value,
            ToolType.FOOD_ANALYZER.value,
            ToolType.MEDICAL_DISCLAIMER.value,
            ToolType.WELLNESS_ADVISOR.value,
        ]
        
        for tool_name in tool_order:
            if tool_name in tools:
                if tool_name == ToolType.MEAL_PLAN_ADJUSTER.value:
                    description = '''🔄 **MEAL PLAN MODIFIER** - Use ONLY when user wants to MODIFY an EXISTING meal plan.
                    
                    **CRITICAL ROUTING RULES:**
                    ✅ Route HERE when query contains:
                       - Action verbs: "swap", "replace", "change", "modify", "adjust", "remove", "add"
                       - PLUS specific food item: "replace chicken", "swap zucchini with spinach"
                       - Meal-specific changes: "change my dinner", "modify breakfast"
                       - Item additions: "add more protein to lunch", "remove carbs from dinner"
                    
                    Examples:
                       - "replace chicken with tofu" ✅
                       - "swap breakfast oatmeal" ✅
                       - "change my dinner to south indian" ✅
                       - "remove zucchini from my plan" ✅
                       - "add vegetables to lunch" ✅
                    
                    ❌ DO NOT route here if:
                       - User asks for NEW plan: "give me a meal plan", "create a plan" → meal_plan_generator
                       - No modification action mentioned: "what should I eat today" → meal_plan_generator
                       - General requests without specific swap: "I want healthy food" → meal_plan_generator
                    
                    **KEY RULE:** Must have BOTH action verb (swap/replace/change/add/remove) AND context of existing plan. Meal name is optional.'''
                elif tool_name == ToolType.WORKOUT_PLAN_ADJUSTER.value:
                    description = 'User wants to **change, modify, replace, swap, adjust, or add** something to an existing WORKOUT or FITNESS plan (e.g., "change my workout routine", "replace squats with lunges", "add cardio to my plan", "reduce sets in my leg day"). NOT for meal/food changes — those go to meal_plan_adjuster.'
                elif tool_name == ToolType.SPECIAL_MEAL_PLAN_GENERATOR.value:
                    description = "Use ONLY when the user explicitly asks to **generate, create, make, or build** a meal plan for one of these specific named diets: 'Mediterranean diet', 'DASH diet', 'MIND diet', or 'Anti-Inflammatory diet'. If the user asks informational questions like 'What is the DASH diet?' or 'Explain MIND diet', use 'general_query' instead."
                elif tool_name == ToolType.MEAL_PLAN_GENERATOR.value:
                    description = '''🍽️ **DAILY MEAL PLAN GENERATOR** - Use ONLY when user wants to CREATE/GENERATE a NEW meal plan.
                    
                    ✅ Route HERE when:
                    - User explicitly asks to CREATE a new plan: "create a meal plan", "generate a plan for me"
                    - User asks what to eat (seeking new recommendation): "what should I eat today?"
                    - General new-plan requests WITHOUT specifying time period (defaults to daily)
                    
                    ❌ DO NOT route here if:
                    - User wants to VIEW/SHOW their EXISTING/SAVED plan → Use history_retriever
                      Examples: "show my meal plan", "show my today\'s meal plan", "what is my plan", "view my plan"
                    - User mentions: "week", "weekly", "7 days" → Use weekly_meal_plan_generator
                    - User wants to MODIFY existing plan → Use meal_plan_adjuster
                    
                    **KEY DISTINCTION:** "create/generate a meal plan" = HERE. "show/view/get my meal plan" = history_retriever.
                    The words "show", "view", "get", "display", "see", "what is" + "my plan" = ALWAYS history_retriever.'''
                elif tool_name == ToolType.PROFILE_UPDATER.value:
                    description = 'User wants to change **personal details** like their name, age, **weight** ("my weight is 70kg", "update my weight to 95kg", "Weight: 97"), height, waist circumference, allergies ("I am allergic to nuts"), medical conditions ("I have diabetes"), dietary restrictions, **cuisine preferences** ("Set my cuisine to Indian", "Change my cuisine to Italian"), or overall dietary preferences ("I am non-veg now", "I prefer vegetarian food"). **IMPORTANT**: Use this tool for weight/height updates that are meant to update the user\'s profile, NOT for vital sign readings.'
                elif tool_name == ToolType.GREETING.value:
                    description = """User is engaging in small talk, like greetings ("hello", "hi", "good morning", "how are you", "what you ate/eat", "okay", "Fuck you", "fuck", "shit", etc.), farewells ("bye", etc.), or expressions of gratitude ("thank you", "thanks", etc.).
                    Use GREETING when:
                    - The previous assistant message did not request any action or decision
                    - The user response is a conversational acknowledgement or closure

                    DO NOT use GREETING when:
                    - The message is a contextual response to the assistant
                    - The message implicitly confirms, rejects, or continues a prior assistant action
                    - The intent can be inferred from chat history even if the message is short or vague.
                    """
                elif tool_name == ToolType.PROFILE_RETRIEVER.value:
                    description = """👤 **PROFILE INFORMATION VIEWER** - Use ONLY for viewing PERSONAL DETAILS (NOT meal plans).
                    
                    ✅ Route HERE for:
                    - "show my profile", "view my profile"
                    - "what are my details?", "my information"
                    - "what dietary preferences do I have?"
                    - "show my allergies", "what medical conditions do I have?"
                    - "what cuisine am I set to?"
                    - "my personal information", "my settings"
                    
                    ❌ DO NOT route here if user mentions:
                    - "meal plan", "meals", "food plan" → Use history_retriever
                    - "calories", "macros", "nutrition" → Use history_retriever or meal_plan_generator
                    - "today", "yesterday", "this week" → Use history_retriever
                    
                    **KEY DISTINCTION:** Profile = personal info (allergies, preferences, conditions). Meal plans = history_retriever."""
                elif tool_name == ToolType.GOAL_UPDATER.value:
                    description = "User wants to change their fitness goal (e.g., \"I want to gain 5kg\")."
                elif tool_name == ToolType.NUTRITION_ANALYZER.value:
                    description = """🔬 **FOOD NUTRITION ANALYZER** - Use for SPECIFIC FOOD nutritional queries (NOT general health advice).
                    
                    ✅ Route HERE for:
                    - Nutritional content: "how much protein in chicken?", "calories in rice"
                    - Food comparisons: "which is better, rice or quinoa?", "chicken vs tofu"
                    - Health benefits of SPECIFIC foods: "is salmon healthy?", "benefits of broccoli"
                    - Macro breakdown: "protein in eggs?", "carbs in banana?"
                    
                    ❌ DO NOT route here for:
                    - General health advice → Use general_query
                    - Disease-specific diet → Use disease_advisor
                    - Recipe/cooking instructions → Use meal_ingredients
                    - "What should I eat for..." → Use appropriate generator or advisor
                    
                    **Focus:** Analyzing specific food properties and nutritional data."""
                elif tool_name == ToolType.DISEASE_ADVISOR.value:
                    description = "User is **asking for dietary advice** specifically related to a medical condition, disease, symptom or general symptom (e.g., \"what to eat for a stomachache?\", \"diet for fever\",\"i am feeling dizzy\",\"i am feeling weak\", etc.)."
                elif tool_name == ToolType.CRAVING_ASSISTANT.value: 
                    description = 'User is expressing a specific food craving or asking for snack ideas (e.g., "I am craving fish", "I crave milk", "I want something sweet", "healthy snack for salt craving"). **PRIORITIZE this tool if the user uses the word "craving" or "crave", even if they mention a specific food.**'
                elif tool_name == ToolType.WATER_STEP_ADVISOR.value:
                    description = 'User is asking about their **step goal**, **water goal** (e.g., "what is my step count goal?", "how much water should I drink?", "Today Water Count", "Today step count").'
                elif tool_name == ToolType.VITAL_ADVISOR.value:
                    description = "User is **stating a VITAL SIGN reading** like heart rate, blood pressure, blood glucose, body temperature, etc. (e.g., \"my blood pressure is 140/90\", \"my heart rate is 150\", \"my blood sugar is high\"). **DO NOT USE** this tool for weight/height updates - those are profile updates, not vital signs. Use 'profile_updater' for weight/height changes."
                elif tool_name == ToolType.HISTORY_RETRIEVER.value:
                    description = """📋 **MEAL PLAN HISTORY VIEWER** - Use for viewing/showing EXISTING or SAVED meal plans.
                    
                    ✅ Route HERE for ANY query that asks to SEE/VIEW/SHOW/GET an existing meal plan:
                    - "show my meal plan", "view my meal plan", "display my meal plan"
                    - "show my today's meal plan", "show my today meal plan"
                    - "what is my meal plan?", "what is my current meal plan?"
                    - "what is my plan for today?", "what's my plan?"
                    - "get my meal plan", "get my saved plan"
                    - "show me my plan", "see my plan", "see my meals"
                    - "what was my meal plan yesterday?", "last week\'s plan"
                    - "show me what I ate", "my food history"
                    - "retrieve my plan", "my meal plan"
                    - "what did you generate for me?"
                    
                    🔑 **CRITICAL RULE:** If the query contains (show/view/see/display/get/what is) + (my) + (meal plan/plan/meals),
                    it is ALWAYS history_retriever — even if "today", "yesterday", "current", or other time words are present.
                    
                    ❌ DO NOT route here ONLY if user wants:
                    - To CREATE/GENERATE a NEW plan: "create", "generate", "make me a plan" → Use meal_plan_generator
                    - To MODIFY existing plan: "swap", "replace", "change" → Use meal_plan_adjuster
                    - Profile information (allergies, preferences) → Use profile_retriever"""
                elif tool_name == ToolType.MEAL_INGREDIENTS.value:
                    description = "If the user is asking about the ingredients, components, composition, or what's in a specific food item, dish, meal, recipe, or asking 'what are the ingredients of [food name]' or 'what's in my [meal]', 'how do I make pasta?', 'recipe for chicken soup', 'what's in a margherita pizza?' then redirect to the meal_ingredients_tool."
                elif tool_name == ToolType.WEEKLY_MEAL_PLAN_GENERATOR.value:
                    description = '''📅 **WEEKLY MEAL PLAN GENERATOR** - Use ONLY for **EXPLICIT MULTI-DAY** requests (7 days or more).
                    
                    ✅ Route HERE when query contains these keywords:
                    - "week", "weekly", "for the week"
                    - "7 days", "7-day", "seven days"
                    - "multi-day", "multiple days"
                    - "entire week", "whole week", "full week"
                    
                    Examples:
                    - "give me a weekly meal plan"
                    - "plan my meals for the week"
                    - "I need a 7-day plan"
                    - "create a weekly indian meal plan"
                    
                    ❌ DO NOT route here if:
                    - No time period specified → Use meal_plan_generator (defaults to daily)
                    - Query says "today" or "daily" → Use meal_plan_generator
                    - User wants to modify existing plan → Use meal_plan_adjuster
                    
                    **STRICT RULE:** Must contain explicit weekly/multi-day keywords to use this tool.'''
                elif tool_name == ToolType.MEAL_CHECK_IN.value:
                    description = "User wants to log or check in a meal they have eaten or skipped (e.g., 'I had chicken salad for lunch', 'I skipped breakfast today', 'Hi I just got my breakfast reminder. Can you help me log what I ate?', 'Hi I got my mid-morning snack reminder. Can you help me log my snack?', 'Hi I just got my lunch reminder. Can you help me log what I ate?', 'Hi I received my afternoon snack reminder. Can you help me log what I had?', 'Hi I got my dinner reminder. Can you help me log what I ate?')."
                elif tool_name == ToolType.GENERAL_QUERY.value:
                    description = "Answers general questions exclusively about personal health, nutrition, wellness, and fitness. This is the correct tool for inquiries about healthy eating, the benefits of foods, **explanations of diets (e.g. 'What is DASH diet?')**, exercise routines, and calculating personal metrics like **calorie target, TDEE, or daily intake**. IMPORTANT: This tool must only be used for the specified health-related domains and must NOT be used for any other topics, including but not limited to sexual content, socio-demographics, world news, sports, programing language, or politics, etc."
                elif tool_name == ToolType.GROCERY_LIST.value:
                    description = 'User asks for a **grocery list** or **shopping list** (e.g., "give me my grocery list", "what do I need to buy for this plan?"). This tool consolidates all ingredients from the latest daily or weekly meal plan.'
                elif tool_name == ToolType.OTHER.value:
                    description = "Any query that is a greeting, non-domain question, or cannot be classified otherwise." 
                elif tool_name == ToolType.PROFILE_SUMMARY.value:
                    description = 'User asks for a summary of their profile, journey, or interactions (e.g., "summarize my profile", "give me a summary of my journey").'
                elif tool_name == ToolType.FITNESS_PLAN_GENERATOR.value:
                    description = 'User is asking for a **new workout plan** or **fitness plan** (e.g., "create a workout plan", "generate a 4-day split").'
                elif tool_name == ToolType.BLOOD_REPORT_ANALYZER.value:
                    description = "User mentions analyzing or providing advice based on a 'blood report', 'lab report', or 'test results'."
                elif tool_name == ToolType.PANEL_QUERY.value:
                    description = "User is asking specific questions about health panels (e.g., 'What is a lipid panel?', 'Tell me about TSH?', 'What are some sugar-friendly recipes?', 'What foods help with thyroid health?'). This tool uses the panel.json data."
                elif tool_name == ToolType.BLOOD_REPORT_QUERY.value:  
                    description = "User is asking specific questions about their blood report data, such as details about a test (e.g., 'What is my WBC?'), a panel (e.g., 'Tell me about the Lipid Panel?'), or asking for food recommendations based on blood report findings (e.g., 'What foods are good for high cholesterol?', 'What recipes are good for PCOS?')."
                elif tool_name == ToolType.SESSION_BOOKING.value:
                    description = 'User is asking to **book**, **schedule**, or **set up an appointment** with an **expert**, **doctor**, or **specialist** (e.g., "book a session", "schedule a consultation", "I need to meet with a nutritionist").'
                elif tool_name == ToolType.AUDIOSUMMARY.value:
                    description = "User wants to summarize a long piece of text, often from a transcribed audio message. Use for queries like 'summarize this' or 'give me the short version'."
                elif tool_name == ToolType.KNOWLEDGE_BASE.value:
                    description = 'Use for SIMPLE EDUCATIONAL QUESTIONS about health concepts (e.g., "What is protein?", "What are macros?", "What is BMR?", "Define fiber"). This is a fast-path tool for quick factual answers. DO NOT use for personalized advice or recommendations.'
                elif tool_name == ToolType.FOOD_ANALYZER.value:
                    description = 'Use when user asks about FOOD SAFETY, INGREDIENTS, or ALLERGEN DETECTION (e.g., "Is this snack safe for me?", "Does pasta contain corn?", "Is this vegan?", "Check if this has gluten"). This tool analyzes ingredient composition and allergen presence.'
                elif tool_name == ToolType.MEDICAL_DISCLAIMER.value:
                    description = 'Use ONLY when the query involves DANGEROUS MEDICAL TOPICS like prescriptions, medications, diagnoses, or emergency symptoms that are beyond the scope of nutrition/fitness guidance.'
                elif tool_name == ToolType.WELLNESS_ADVISOR.value:
                    description = 'Use for HOLISTIC WELLNESS queries related to lifestyle management, stress, sleep, energy, or managing chronic conditions through lifestyle (e.g., "How can I manage my PCOS symptoms?", "Tips for better sleep", "How to boost energy naturally"). This is distinct from disease_advisor which focuses on dietary advice for conditions.'
                else:
                    description = tools[tool_name].description
                tool_descriptions.append(f"- '{tool_name}': {description}")

        prompt_footer = f"""Bias warning: Do NOT overuse GREETING. It is the most restrictive category.

**CRITICAL OUTPUT FORMAT:**
You MUST respond with a valid JSON object in this exact format:
{{
  "tool": "<exact_tool_name>"
}}

Example valid responses:
{{"tool": "meal_plan_generator"}}
{{"tool": "profile_updater"}}
{{"tool": "greeting"}}

Do NOT respond with plain text. Do NOT add explanations. ONLY return the JSON object.{active_state_context}"""
        
        return prompt_header + "\n".join(tool_descriptions) + prompt_footer

    async def execute(self, query: str, chat_history: List[Dict] = None, profile_summary_json: Optional[Dict] = None, last_agent_context: Optional[Dict] = None, **kwargs) -> ToolResult:
        try:

            logger.info(f"[Classification] Using Azure classifier for: '{query}'")
            
            from .tool_core import get_optimized_context
            optimized_history = get_optimized_context(chat_history, max_messages=5)
            
            system_prompt_with_summary = self._build_system_prompt(self.tools, profile_summary_json, last_agent_context)            
            context = "\n".join(f"{msg['role']}: {msg['content']}" for msg in (optimized_history[-3:] if optimized_history else []))
            full_prompt_for_llm = f"Chat Context:\n{context}\n\nUser Query to classify: '{query}'"            
            
            response = await self._call_azure_classifier(system_prompt_with_summary, full_prompt_for_llm)
            
            if not response:
                logger.warning("[Classification] Azure returned no response, using CATCH-ALL (GENERAL_QUERY)")
                return ToolResult(True, ToolType.GENERAL_QUERY.value, metadata={"fallback": "azure_no_response", "classifier": "azure"})
            
            response_clean = response.strip()
            if response_clean.startswith("```json"):
                response_clean = response_clean[7:]
            if response_clean.startswith("```"):
                response_clean = response_clean[3:]
            if response_clean.endswith("```"):
                response_clean = response_clean[:-3]
            response_clean = response_clean.strip()
            
            try:
                parsed_response = json.loads(response_clean)
                classified_type = parsed_response.get("tool", "").strip().lower()
            except json.JSONDecodeError as json_err:
                logger.warning(f"[Classification] Failed to parse JSON from Azure: {response}. Error: {json_err}. Using CATCH-ALL (GENERAL_QUERY)")
                return ToolResult(True, ToolType.GENERAL_QUERY.value, metadata={"fallback": "json_parse_error", "classifier": "azure"})
            
            valid_tools = [t.value for t in ToolType]
            
            if classified_type in valid_tools:
                logger.info(f"[Classification] PRIMARY (Azure): '{query}' -> {classified_type}")
                
                persona_metadata = self._detect_domain_persona(query, classified_type, profile_summary_json)
                persona_metadata['classifier'] = 'azure'
                
                return ToolResult(True, classified_type, metadata=persona_metadata)
            else:
                logger.warning(f"[Classification] Azure returned invalid tool '{classified_type}', using CATCH-ALL (GENERAL_QUERY)")
                return ToolResult(True, ToolType.GENERAL_QUERY.value, metadata={"fallback": "invalid_tool", "attempted_tool": classified_type, "classifier": "azure"})
            
        except Exception as e:

            logger.error(f"[Classification] Exception in QueryClassifierTool: {e}. Using CATCH-ALL (GENERAL_QUERY)")
            return ToolResult(True, ToolType.GENERAL_QUERY.value, metadata={"fallback": "exception", "error": str(e), "classifier": "exception_fallback"})

    async def _call_azure_classifier(self, system_prompt: str, user_prompt: str) -> Optional[str]:
        if not self.classification_client:
            return None

        deployment = os.getenv("CLASSIFICATION_MODEL_DEPLOYMENT") or os.getenv("CLASSIFICATION_MODEL_NAME")
        if not deployment:
            logger.error("[Azure] CLASSIFICATION_MODEL_DEPLOYMENT is not configured")
            return None

        try:
            response = await self.classification_client.chat.completions.create(
                model=deployment,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0,
                max_tokens=100,
                response_format={"type": "json_object"}
            )
            return response.choices[0].message.content if response.choices else None
        except Exception as e:
            logger.error(f"[Azure] Classification request failed: {e}")
            return None
    
    async def _call_gemini(self, system_prompt: str, user_prompt: str) -> Optional[str]:
        import aiohttp
        import time as _time
        
        gemini_api_key = os.getenv("GEMINI_API_KEY")
        if not gemini_api_key:
            logger.error("[Gemini] GEMINI_API_KEY not found in environment variables")
            return None
        
        url = f"https://aiplatform.googleapis.com/v1/publishers/google/models/gemini-3.1-flash-lite:generateContent?key={gemini_api_key}"
        
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": f"{system_prompt}\n\n{user_prompt}"}]
                }
            ],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 100,
                "topP": 1.0
            }
        }
        
        headers = {"Content-Type": "application/json"}
        
        request_start = _time.time()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as response:
                    latency_ms = (_time.time() - request_start) * 1000
                    
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"[Gemini] API error ({response.status}): {error_text[:300]}")
                        return None
                    
                    data = await response.json()
                    
                    candidates = data.get("candidates", [])
                    if candidates and len(candidates) > 0:
                        content = candidates[0].get("content", {})
                        parts = content.get("parts", [])
                        if parts and len(parts) > 0:
                            text = parts[0].get("text", "")
                            logger.info(f"[Gemini] Classification response received (latency: {latency_ms:.0f}ms)")
                            return text
                    
                    logger.error(f"[Gemini] Unexpected response structure: {json.dumps(data)[:300]}")
                    return None
                    
        except aiohttp.ClientError as e:
            logger.error(f"[Gemini] Request failed: {e}")
            return None
        except Exception as e:
            logger.error(f"[Gemini] Unexpected error: {e}")
            return None

    def _detect_domain_persona(self, query: str, classified_tool: str, profile_summary: Optional[Dict] = None) -> Dict:

        query_lower = query.lower()
        metadata = {
            "domain": "general",
            "persona_context": None,
            "user_profile_context": {}
        }
        
        if profile_summary:
            if profile_summary.get('weight'):
                metadata['user_profile_context']['weight'] = profile_summary.get('weight')
            if profile_summary.get('fitness_level'):
                metadata['user_profile_context']['fitness_level'] = profile_summary.get('fitness_level')
            if profile_summary.get('medical_conditions'):
                metadata['user_profile_context']['conditions'] = profile_summary.get('medical_conditions')
            if profile_summary.get('activity_level'):
                metadata['user_profile_context']['activity_level'] = profile_summary.get('activity_level', 'sedentary')
        
        fitness_keywords = ['workout', 'exercise', 'gym', 'training', 'muscle', 'cardio', 'fitness']
        if any(keyword in query_lower for keyword in fitness_keywords) or classified_tool in ['fitness_plan_generator', 'workout_adjuster_tool']:
            metadata['domain'] = 'fitness'
            
            activity_level = metadata['user_profile_context'].get('activity_level', 'sedentary')
            weight = metadata['user_profile_context'].get('weight', '95kg')
            
            metadata['persona_context'] = f"""Note: The user is asking about fitness. Adapt your tone to be an encouraging ACSM-certified trainer.
USER FITNESS CONTEXT:
- Current activity level: {activity_level}
- Weight: {weight}
- Approach: Progressive, safe, and motivating (especially for sedentary/beginner users)
- Focus: Building consistency and confidence, not intimidation"""
            
            logger.info(f" PERSONA INJECTION: FITNESS domain detected, injected trainer context")
        
        wellness_keywords = ['tired', 'bloated', 'acid', 'reflux', 'sleep', 'energy', 'pcos', 'stress', 'anxiety']
        if any(keyword in query_lower for keyword in wellness_keywords) or classified_tool in ['wellness_advisor', 'disease_advisor', 'vital_advisor']:
            metadata['domain'] = 'wellness'
            
            conditions = metadata['user_profile_context'].get('conditions', [])
            conditions_str = ', '.join(conditions) if conditions else 'None'
            
            metadata['persona_context'] = f"""Note: The user is seeking wellness/health guidance. Adopt the tone of an empathetic clinical wellness consultant.
USER WELLNESS CONTEXT:
- Medical conditions: {conditions_str}
- Focus: Holistic, root-cause approach with actionable lifestyle interventions
- Tone: Compassionate, science-backed, and practical"""
            
            logger.info(f" PERSONA INJECTION: WELLNESS domain detected, injected consultant context")
        
        food_analysis_keywords = ['ingredient', 'safe', 'contains', 'label', 'check', 'vegan', 'allergy', 'allergen']
        if any(keyword in query_lower for keyword in food_analysis_keywords) or classified_tool in ['food_analyzer', 'meal_ingredients']:
            metadata['domain'] = 'food_analysis'
            
            metadata['persona_context'] = """Note: The user is asking about food safety/ingredient analysis. Adopt the tone of a meticulous food safety specialist.
FOOD ANALYSIS FOCUS:
- Thoroughness: Check for hidden allergens (corn syrup, whey, casein, etc.)
- Safety: Flag ALL potential allergens, even in trace amounts
- Clarity: Provide clear yes/no answers on safety"""
            
            logger.info(f" PERSONA INJECTION: FOOD_ANALYSIS domain detected, injected specialist context")
        
        nutrition_keywords = ['meal', 'food', 'diet', 'nutrition', 'calories', 'macros', 'eat']
        if any(keyword in query_lower for keyword in nutrition_keywords) or classified_tool in ['meal_plan_generator', 'nutrition_analyzer', 'meal_plan_adjuster']:
            metadata['domain'] = 'nutrition'
            
            metadata['persona_context'] = """Note: The user is asking about nutrition. Adopt the tone of an expert registered dietitian (RD).
NUTRITION FOCUS:
- Evidence-based recommendations
- Practical meal planning and food choices
- Clear measurements (cups, grams, servings)
- Always respect allergies and medical conditions"""
            
            logger.info(f" PERSONA INJECTION: NUTRITION domain detected, injected dietitian context")
        
        return metadata

class DataService:
    def __init__(self):
        """
        🔧 UNIFIED DATASET APPROACH:
        Loads food data from Foods_nested_USA_*.json instead of .pt cache file.
        Parses macro values from lists/ranges and builds unified database.
        """
        self.cache = self._load_unified_food_database()
        self.food_index = self._build_food_index()
        self.all_food_names_for_matching = list(self.food_index.keys())
        self.disease_data = self._load_disease_data()
        self.vitals_data = self._load_vitals_data()
        print(f" DataService initialized with {len(self.cache.get('foods', {}))} foods from unified database")
    
    def _extract_macro_value(self, value: Any) -> float:

        if value is None:
            return 0.0
        
        if isinstance(value, (list, tuple)):
            if len(value) == 0:
                return 0.0
            try:
                return round(sum(float(v) for v in value) / 2.0, 2)
            except (ValueError, TypeError):
                return 0.0
        
        try:
            return float(value)
        except (ValueError, TypeError):
            return 0.0
    
    def _load_unified_food_database(self) -> Dict:

        try:
            main_path = 'datasets/Foods_nested_USA_main_dishes.json'
            side_path = 'datasets/Foods_nested_USA_side_dishes.json'
            fallback_path = 'datasets/foods 1.json'
            
            if not os.path.exists(main_path):
                print(f" Foods_nested_USA_main_dishes.json not found at {main_path}")
                print(f" Attempting to load fallback database from {fallback_path}...")
                return self._load_fallback_food_database(fallback_path)
            
            print(f" Loading unified food database from nested JSON datasets...")
            
            with open(main_path, 'r', encoding='utf-8') as f:
                main_dishes_data = json.load(f)
            
            side_dishes_data = {}
            if os.path.exists(side_path):
                with open(side_path, 'r', encoding='utf-8') as f:
                    side_dishes_data = json.load(f)
            
            cuisine_data = {**main_dishes_data, **side_dishes_data}
            
            unified_foods = {}
            total_items = 0
            
            for cuisine_name, dietary_types in cuisine_data.items():
                if not isinstance(dietary_types, dict):
                    continue
                
                for diet_type, food_items in dietary_types.items():
                    if not isinstance(food_items, dict):
                        continue
                    
                    for food_name_raw, macro_data in food_items.items():
                        if not isinstance(macro_data, dict):
                            continue
                        
                        food_name_display = food_name_raw.replace('_', ' ').title()
                        
                        protein_avg = self._extract_macro_value(macro_data.get('protein_per_100g'))
                        calories_avg = self._extract_macro_value(macro_data.get('calories_per_100g'))
                        fat_avg = self._extract_macro_value(macro_data.get('fat_per_100g'))
                        fiber_avg = self._extract_macro_value(macro_data.get('fiber_per_100g', 0))
                        

                        carbs_avg = self._extract_macro_value(macro_data.get('carbohydrates_per_100g'))
                        if carbs_avg == 0.0 and calories_avg > 0:
                            calculated_carbs = (calories_avg - (protein_avg * 4) - (fat_avg * 9)) / 4.0
                            carbs_avg = max(0.0, round(calculated_carbs, 2)) 
                        
                        cache_key = f"{food_name_display}||100g"
                        
                        unified_foods[cache_key] = {
                            'protein': protein_avg,
                            'calories': calories_avg,
                            'carbohydrates': carbs_avg,
                            'fat': fat_avg,
                            'fiber': fiber_avg,
                            'portion_weight_g': 100, 
                            'cuisine': cuisine_name.strip().lower(),  
                            'diet_type': diet_type
                        }
                        
                        lowercase_key = f"{food_name_display.lower().strip()}||100g"
                        unified_foods[lowercase_key] = unified_foods[cache_key]
                        
                        total_items += 1
            
            print(f" Loaded {total_items} unique food items from {len(cuisine_data)} cuisines")
            print(f" Total database entries: {len(unified_foods)} (including normalized keys)")
            
            return {'foods': unified_foods}
            
        except Exception as e:
            print(f" Error loading unified food database: {e}")
            import traceback
            traceback.print_exc()
            return {'foods': {}}
    
    def _load_fallback_food_database(self, fallback_path: str):
        
        try:
            if not os.path.exists(fallback_path):
                print(f" Fallback food database not found at {fallback_path}")
                return {'foods': {}}
            
            print(f" Loading fallback food database from {fallback_path}...")
            
            with open(fallback_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            foods_array = data.get('foods', [])
            
            if not isinstance(foods_array, list):
                print(f" Unexpected structure in {fallback_path}: 'foods' is not a list")
                return {'foods': {}}
            
            unified_foods = {}
            total_items = 0
            
            for food_item in foods_array:
                if not isinstance(food_item, dict):
                    continue
                
                food_name = food_item.get('common_name', '').strip()
                if not food_name:
                    continue
                
                per_100g = food_item.get('per_100g_nutrients', {})
                
                if not per_100g:
                    calories = food_item.get('calories_kcal', 0.0)
                    protein = food_item.get('protein_g', 0.0)
                    fat = food_item.get('total_fat_g', 0.0)
                    carbs = food_item.get('total_carbs_g', 0.0)
                    fiber = food_item.get('dietary_fiber_g', 0.0)
                    sodium = food_item.get('sodium_mg', 0.0)
                else:
                    calories = per_100g.get('calories_kcal', 0.0)
                    protein = per_100g.get('protein_g', 0.0)
                    fat = per_100g.get('total_fat_g', 0.0)
                    carbs = per_100g.get('total_carbs_g', 0.0)
                    fiber = per_100g.get('dietary_fiber_g', 0.0)
                    sodium = per_100g.get('sodium_mg', 0.0)
                
                try:
                    calories = float(calories) if calories else 0.0
                    protein = float(protein) if protein else 0.0
                    fat = float(fat) if fat else 0.0
                    carbs = float(carbs) if carbs else 0.0
                    fiber = float(fiber) if fiber else 0.0
                    sodium = float(sodium) if sodium else 0.0
                except (ValueError, TypeError):
                    print(f" Invalid nutrition data for '{food_name}', skipping")
                    continue
                
                food_name_display = food_name
                
                cache_key = f"{food_name_display}||100g"
                
                cuisine = food_item.get('cuisine', ['global'])
                if isinstance(cuisine, list):
                    cuisine = cuisine[0] if cuisine else 'global'
                
                unified_foods[cache_key] = {
                    'protein': protein,
                    'calories': calories,
                    'carbohydrates': carbs,
                    'fat': fat,
                    'fiber': fiber,
                    'sodium': sodium,
                    'portion_weight_g': 100,
                    'cuisine': str(cuisine).strip().lower(),
                    'food_group': food_item.get('food_group', ''),
                    'meal_timing': food_item.get('meal_timing', [])
                }
                
                total_items += 1
            
            print(f" Loaded {total_items} foods from fallback database")
            print(f" Total cache entries: {len(unified_foods)}")
            
            return {'foods': unified_foods}
            
        except Exception as e:
            print(f" Error loading fallback food database: {e}")
            import traceback
            traceback.print_exc()
            return {'foods': {}}
    
    def _load_disease_data(self):
        try:
            disease_file_path = 'datasets/Diseases_cleaned.json'
            if not os.path.exists(disease_file_path):
                print(f"Warning: Disease data file not found: '{disease_file_path}'.")
                return []
            
            with open(disease_file_path, 'r', encoding='utf-8') as file:
                raw_data = json.load(file)
            
            flat_data = []
            if isinstance(raw_data, dict):
                for disease_key, entry in raw_data.items():
                    if isinstance(entry, dict):
                        if 'disease' not in entry and 'name' not in entry:
                            entry['disease'] = disease_key
                        flat_data.append(entry)
            elif isinstance(raw_data, list):
                for entry in raw_data:
                    if isinstance(entry, list):
                        flat_data.extend(e for e in entry if isinstance(e, dict))
                    elif isinstance(entry, dict):
                        flat_data.append(entry)
            
            print(f"DEBUG: Loaded {len(flat_data)} disease entries from {disease_file_path}")
            
            return flat_data
        except Exception as e:
            print(f"Error loading disease data: {e}")
            return []
    
    def _load_vitals_data(self):
        try:
            vitals_file_path = 'datasets/vitals 11.json'
            if not os.path.exists(vitals_file_path):
                print(f"Warning: Vitals data file not found: '{vitals_file_path}'.");
                return {"vital_signs": []}
            with open(vitals_file_path, 'r', encoding='utf-8') as file:
                return json.load(file)
        except Exception as e:
            print(f"Error loading vitals data: {e}");
            return {"vital_signs": []}
    def _normalize_name_for_index(self, name: str) -> str:
        name = name.lower(); name = re.sub(r'\(.*?\)', '', name).strip(); name = re.sub(r',.*', '', name).strip()
        descriptors = ['organic', 'fresh', 'raw', 'dried', 'chopped', 'sliced', 'steamed', 'grilled', 'baked', 'roasted','cooked', 'boiled', 'canned', 'frozen', 'peeled', 'with skin', 'without skin', 'boneless', 'skinless','sweetened', 'unsweetened', 'salted', 'unsalted', 'low sodium', 'low fat', 'whole', 'ground','lean', 'extra lean', 'regular', 'instant', 'quick', 'old fashioned', 'steel cut']
        for desc in descriptors: name = re.sub(rf'\b{re.escape(desc)}\b', '', name, flags=re.IGNORECASE).strip()
        name = re.sub(r'\s+', ' ', name).strip(); return name
    def _normalize_portion(self, portion: str) -> str:
        p = portion.lower().strip(); p = re.sub(r'\(.*?\)', '', p).strip()
        common_units = {"tbsp": "tablespoon", "tsp": "teaspoon", "oz": "ounce", "fl oz": "fluid ounce","g": "gram", "kg": "kilogram", "ml": "milliliter", "l": "liter", "lb": "pound","med": "medium", "lg": "large", "sm": "small", "sl": "slice"}
        for abb, full in common_units.items(): p = re.sub(rf'\b{re.escape(abb)}\b\.?', full, p)
        p = re.sub(r's\b', '', p); p = re.sub(r'\s+', ' ', p).strip(); return p
    def _build_food_index(self):
        index = defaultdict(list)
        if 'foods' not in self.cache or not self.cache['foods']: return index
        for food_key_from_cache in self.cache['foods'].keys():
            try:
                name_part_original, _ = food_key_from_cache.split('||', 1)
                normalized_index_name = self._normalize_name_for_index(name_part_original)
                if normalized_index_name and food_key_from_cache not in index[normalized_index_name]: index[normalized_index_name].append(food_key_from_cache)
            except Exception as e: print(f"Warning: Error building food index for key '{food_key_from_cache}': {e}")
        return index
    def get_food_data(self, food_name_query: str, portion_query: str = "100g") -> Optional[Dict]:
        normalized_food_name_query = self._normalize_name_for_index(food_name_query); normalized_portion_query = self._normalize_portion(portion_query)
        if normalized_food_name_query in self.food_index:
            candidate_cache_keys = self.food_index[normalized_food_name_query]; best_match_data = None; highest_portion_score = -1
            for cache_key in candidate_cache_keys:
                _, portion_from_key = cache_key.split('||', 1); normalized_portion_from_key = self._normalize_portion(portion_from_key)
                if normalized_portion_from_key == normalized_portion_query: return self.cache['foods'][cache_key]
                portion_score = fuzz.ratio(normalized_portion_query, normalized_portion_from_key)
                if portion_score > highest_portion_score: highest_portion_score = portion_score; best_match_data = self.cache['foods'][cache_key]
            if best_match_data and highest_portion_score >= 75: return best_match_data
            elif candidate_cache_keys: return self.cache['foods'][candidate_cache_keys[0]]
        return None

class OpenAI_LLMService:
    def __init__(self):
        if all(k in OPENAI_WEEKLY_PLAN_CONFIG for k in ["api_key", "endpoint", "api_version", "deployment"]):
            self.client = AsyncAzureOpenAI(
                api_key=OPENAI_WEEKLY_PLAN_CONFIG["api_key"],
                azure_endpoint=OPENAI_WEEKLY_PLAN_CONFIG["endpoint"],
                api_version=OPENAI_WEEKLY_PLAN_CONFIG["api_version"],
            )
            self.deployment_name = OPENAI_WEEKLY_PLAN_CONFIG["deployment"]
        else:
            self.client = None
            self.deployment_name = None
            logger.warning("Query classification LLM service is not configured. Query classification will use fallback.")

    async def query(self, system_prompt: str, prompt: str, chat_history: List[Dict], max_tokens: int, temperature: float) -> Optional[str]:
        clean_history = [
            {"role": msg.get("role"), "content": msg.get("content")}
            for msg in chat_history
            if msg.get("role") and msg.get("content")
        ]
        
        messages = [{"role": "system", "content": system_prompt}]
        if clean_history:
            messages.extend(clean_history)
        messages.append({"role": "user", "content": prompt})

        try:
            logger.info(f"Querying dedicated OpenAI endpoint with deployment: {self.deployment_name}")
            response = await self.client.chat.completions.create(
                model=self.deployment_name,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=120.0 
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"Dedicated OpenAI query failed: {e}")
            return None
    
    async def query_structured(self, system_prompt: str, prompt: str, chat_history: List[Dict], response_model: type[BaseModel], max_tokens: int = 1000, temperature: float = 0.3) -> Optional[BaseModel]:
        """Query LLM with forced structured output matching Pydantic model - prevents hallucinations!"""
        if not self.client:
            logger.error("OpenAI client not configured for structured queries")
            return None
        
        clean_history = [
            {"role": msg.get("role"), "content": msg.get("content")}
            for msg in chat_history
            if msg.get("role") and msg.get("content")
        ]
        
        schema = response_model.model_json_schema()
        enhanced_system_prompt = f"""{system_prompt}

CRITICAL: You MUST respond with ONLY valid JSON matching this exact schema:
{json.dumps(schema, indent=2)}

Do not include any text outside the JSON object."""
        
        messages = [{"role": "system", "content": enhanced_system_prompt}]
        if clean_history:
            messages.extend(clean_history)
        messages.append({"role": "user", "content": prompt})
        
        try:
            logger.info(f"Querying OpenAI with structured output (Pydantic validation)")
            response = await self.client.chat.completions.create(
                model=self.deployment_name,
                messages=messages,
                response_format={"type": "json_object"}, 
                max_tokens=max_tokens,
                temperature=temperature,
                timeout=120.0
            )
            
            json_str = response.choices[0].message.content
            
            result = response_model.model_validate_json(json_str)
            logger.info(f" Successfully validated structured response")
            return result
            
        except Exception as e:
            logger.error(f"Structured query failed: {e}")
            try:
                json_str = response.choices[0].message.content
                match = re.search(r'\{.*\}', json_str, re.DOTALL)
                if match:
                    result = response_model.model_validate_json(match.group(0))
                    logger.warning(f" Extracted JSON with regex fallback")
                    return result
            except:
                pass
            return None