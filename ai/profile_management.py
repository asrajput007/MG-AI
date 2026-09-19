import json
import logging
import re
import time
import asyncio
from datetime import datetime, timezone, timedelta
from ai.tool_core import ToolType
from typing import List, Dict, Optional
from rapidfuzz import process, fuzz
from .tool_core import BaseTool, LLMService, ToolResult, ToolType
from .base_and_utility import DataService, DatabasePersistenceTool 
from .vitals_and_disease import CalorieCalculatorTool, classify_vital_signs
from helpers.redis_client import get_redis_cache
from helpers.utils import serialize_data
from fastapi import BackgroundTasks
import jwt  
from typing import List, Dict, Optional
from .health_orchestrator import ExpertPersonaLibrary

logger = logging.getLogger(__name__)
class GreetingTool(BaseTool):

    def __init__(self, llm_service: LLMService):
        super().__init__(
            ToolType.GREETING.value,
            "Responds to greetings and simple conversational openers."
        )
        self.llm_service = llm_service

    async def execute(self, query: str, constraints: Dict, chat_history: List[Dict] = None, **kwargs) -> ToolResult:
        import random

        user_name = kwargs.get("name") or constraints.get("name", "there")
        query_lower = query.lower().strip()
        
        # Resolve user's local time from their timezone in profile/constraints
        user_tz_str = constraints.get("timezone", "").strip()
        user_now = self._resolve_user_time(user_tz_str)
        user_hour = user_now.hour
        
        if 5 <= user_hour < 12:
            time_greeting = "Good morning"
        elif 12 <= user_hour < 17:
            time_greeting = "Good afternoon"
        else:
            time_greeting = "Good evening"
        
        logging.info(f"[GreetingTool] User TZ: '{user_tz_str}' | Local hour: {user_hour} | Greeting: '{time_greeting}' | Query: '{query}'")
        
        # Detect intent
        is_farewell = bool(re.search(r'\b(good\s*night|bye|goodbye|good\s*bye|see\s*you|take\s*care|gotta\s*go|signing\s*off|ttyl|cya|nighty?\s*night)\b', query_lower))
        is_thanks = bool(re.search(r'\b(thanks?|thank\s*you|thx|ty|appreciate)\b', query_lower))
        is_negative = bool(re.search(r'\b(fuck|shit|damn|ass|bitch|idiot|stupid|hate|suck|wtf|stfu)\b', query_lower))

        if is_farewell:
            farewells = [
                f"Good night, {user_name}! Rest well and take care 🌙",
                f"Take care, {user_name}! See you next time 😊",
                f"Sweet dreams, {user_name}! Sleep well 🌟",
                f"Bye {user_name}! Hope you had a great day. See you soon! 👋",
                f"Good night, {user_name}! Wishing you a peaceful rest 🌙",
            ]
            return ToolResult(success=True, data={"answer": random.choice(farewells)})
        
        if is_thanks:
            thanks_responses = [
                f"You're welcome, {user_name}! I'm always here for you 😊",
                f"Anytime, {user_name}! Happy to help 💪",
                f"Glad I could help, {user_name}! Reach out whenever you need 😊",
                f"No problem at all, {user_name}! That's what I'm here for 🙌",
                f"My pleasure, {user_name}! Let me know if you need anything else 😊",
            ]
            return ToolResult(success=True, data={"answer": random.choice(thanks_responses)})
        
        if is_negative:
            calm_responses = [
                f"Hey {user_name}, I get it. I'm here to help whenever you're ready 🙏",
                f"I hear you, {user_name}. Let's take a fresh start — what do you need?",
                f"No worries, {user_name}. I'm here for you — just let me know how I can help 😊",
                f"I understand, {user_name}. Whenever you're ready, I'm here to help 💙",
            ]
            return ToolResult(success=True, data={"answer": random.choice(calm_responses)})
        
        # Standard greeting — fully deterministic, NO LLM call
        followups = [
            "What's on your mind today? 😊",
            "How are you doing today?",
            "How can I help you today? 💪",
            "What can I do for you? 😊",
            "Hope you're having a great day! What do you need help with?",
            "Ready when you are! What are you looking for today? 🍎",
            "How's your day going? Anything I can help with?",
            "What would you like to work on today? 😊",
            "Need help with meals, fitness, or anything else?",
            "Happy to see you! What brings you here today? 🌟",
        ]
        
        answer = f"{time_greeting}, {user_name}! {random.choice(followups)}"
        return ToolResult(success=True, data={"answer": answer})

    @staticmethod
    def _resolve_user_time(user_tz_str: str):
        """Resolve user's local datetime from timezone string. Falls back to Asia/Kolkata."""
        import re as _re
        if user_tz_str:
            # Try IANA timezone name first (e.g. "America/New_York", "Asia/Kolkata")
            try:
                import zoneinfo
                return datetime.now(zoneinfo.ZoneInfo(user_tz_str))
            except Exception:
                pass

            # Try abbreviation mapping
            tz_abbr_map = {
                "EST": "America/New_York", "EDT": "America/New_York",
                "CST": "America/Chicago", "CDT": "America/Chicago",
                "MST": "America/Denver", "MDT": "America/Denver",
                "PST": "America/Los_Angeles", "PDT": "America/Los_Angeles",
                "IST": "Asia/Kolkata", "GMT": "Europe/London", "UTC": "UTC",
                "BST": "Europe/London", "CET": "Europe/Paris", "CEST": "Europe/Paris",
                "AEST": "Australia/Sydney", "AEDT": "Australia/Sydney",
                "JST": "Asia/Tokyo", "KST": "Asia/Seoul",
                "SGT": "Asia/Singapore", "HKT": "Asia/Hong_Kong",
                "GST": "Asia/Dubai", "AST": "Asia/Riyadh",
                "ET": "America/New_York", "CT": "America/Chicago",
                "MT": "America/Denver", "PT": "America/Los_Angeles",
            }
            mapped_tz = tz_abbr_map.get(user_tz_str.upper())
            if mapped_tz:
                try:
                    import zoneinfo
                    return datetime.now(zoneinfo.ZoneInfo(mapped_tz))
                except Exception:
                    pass

            # Try numeric offset: "+05:30", "-05:00", "+5:30", "-5"
            offset_match = _re.match(r'^([+-]?)(\d{1,2}):?(\d{2})?$', user_tz_str.strip())
            if offset_match:
                sign = -1 if offset_match.group(1) == '-' else 1
                hours = int(offset_match.group(2))
                minutes = int(offset_match.group(3) or 0)
                total_minutes = sign * (hours * 60 + minutes)
                from datetime import timedelta as _td
                tz_offset = timezone(_td(minutes=total_minutes))
                return datetime.now(tz_offset)

            # Try pure minute offset: "330", "-300"
            try:
                offset_minutes = int(user_tz_str.strip())
                from datetime import timedelta as _td
                tz_offset = timezone(_td(minutes=offset_minutes))
                return datetime.now(tz_offset)
            except (ValueError, TypeError):
                pass

        logging.warning(f"[GreetingTool] No valid timezone (raw: '{user_tz_str}'). Falling back to Asia/Kolkata (IST).")
        try:
            import zoneinfo
            return datetime.now(zoneinfo.ZoneInfo("Asia/Kolkata"))
        except Exception:
            return datetime.now(timezone.utc)
class AudioSummaryTool(BaseTool):
    def __init__(self, llm_service: LLMService):
        super().__init__(
            ToolType.AUDIOSUMMARY.value,
            "Summarizes transcribed audio or long text into a concise, 30-word summary."
        )
        self.llm_service = llm_service

    async def execute(self, text: str, **kwargs) -> ToolResult:
        if not text or not text.strip():
            return ToolResult(success=False, data=None, error="Text to summarize cannot be empty.")
        logger.info(f" Generating audio summary using LLMService...")
        start_time = time.time()
        system_prompt = """
        You are an expert text summarizer. Your task is to produce a concise, accurate, and polished summary of the given text, strictly limited to approximately 30 words.
        This summary will be used for a text-to-speech engine, so it must be natural-sounding and conversational.
        Provide ONLY the final summary as your response. Do not add any conversational text like "Here is the summary:".
        """
        
        try:
            summary = await self.llm_service.query(
                prompt=text,
                system_prompt=system_prompt,
                max_tokens=70,
                temperature=0.5
            )

            if not summary:
                return ToolResult(success=False, data={"summary": ""}, error="The summarization service returned an empty response.")

            duration = time.time() - start_time
            logger.info(f"Audio summary generated in {duration:.2f} seconds.")
            return ToolResult(success=True, data={"summary": summary})
            
        except Exception as e:
            logger.error(f"An error occurred during the audio summary LLM call: {e}")
            return ToolResult(success=False, data=None, error=f"An unexpected error occurred during summarization: {e}")

class SummarizationTool(BaseTool):
    def __init__(self, llm_service: LLMService):
        super().__init__(
            ToolType.SUMMARIZER.value, 
            "Summarizes long text into a concise version."
        )
        self.llm_service = llm_service
    async def execute(self, text: str, **kwargs) -> ToolResult:
        try:
            if len(text.split()) > 50:
                summary_prompt = f"Summarize the following text in 50 words or less:\n\n{text}"
                summarized_text = await self.llm_service.query(
                    prompt=summary_prompt, 
                    system_prompt="You are an expert text summarizer."
                )
            else:
                summarized_text = text            
            return ToolResult(success=True, data={"summary": summarized_text})
        except Exception as e:
            logger.error(f"An unexpected error occurred in SummarizationTool: {e}")
            return ToolResult(success=False, data=None, error=str(e))

class GeneralQueryTool(BaseTool):
    def __init__(self, llm_service: LLMService):
        super().__init__(
            ToolType.GENERAL_QUERY.value,
            "Comprehensive information hub for nutrition, fitness, wellness, and health metrics - handles all informational queries about exercise, workouts, diets, healthy living, and personal health calculations."
        )
        self.llm_service = llm_service

    async def execute(self, query: str, chat_history: List[Dict], constraints: Dict, **kwargs) -> ToolResult:
        profile_context = ""
        if constraints:
            details = []
            if constraints.get('name'): details.append(f"Name: {constraints['name']}")
            if constraints.get('age'): details.append(f"Age: {constraints['age']}")
            if constraints.get('gender'): details.append(f"Gender: {constraints['gender']}")
            if constraints.get('weight_kg'): details.append(f"Weight: {constraints['weight_kg']} kg")
            if constraints.get('height_cm'): details.append(f"Height: {constraints['height_cm']} cm")
            if constraints.get('activity_level'): details.append(f"Activity Level: {constraints['activity_level']}")
            if constraints.get('medical_conditions'): details.append(f"Medical Conditions: {', '.join(constraints['medical_conditions'])}")
            if constraints.get('allergies'): details.append(f"Allergies: {', '.join(constraints['allergies'])}")
            
            if details:
                profile_context = "**USER PROFILE:**\n" + "\n".join([f"- {d}" for d in details]) + "\n\n"
        system_prompt = f"""You are Friska, a dedicated and expert AI health, nutrition, fitness, and wellness assistant. Your purpose is to provide safe, helpful, and accurate information strictly related to the **knowledge base provided below** and comprehensive topics including **nutrition, fitness, exercise, wellness, and health metrics**.

{profile_context}**KNOWLEDGE BASE:**
- **CEO/Founder:** The CEO of Friska Fuel is Shaji Nair.
- **About Friska AI:** Friska Fuel is a cutting-edge health and wellness platform that combines the power of artificial intelligence with expert human support to help individuals manage chronic conditions, improve lifestyle habits, and achieve lasting health outcomes. Integrated with wearable devices and personalized care plans, Friska Fuel delivers real-time insights, nutrition guidance, fitness tracking, and preventive care—all in one seamless mobile app.
- **Location:** Friska Fuel is based in Arlington, Virginia.
- **Your Identity:** You are an AI assistant from Friska AI, a personalized guide for nutrition, fitness, and wellness. You were created by the experts at Friska AI.
- **Common Phrases:**
    - If the user says "ok", "okay", or a similar affirmation, respond with: "Got it! If you have any questions or need assistance in the future, feel free to ask. Have a great day! 😊"
    - If the user asks "how do I log meals" or "how to track food", explain that they can log meals directly in the chat by telling you what they ate (e.g., "I had 2 eggs and toast for breakfast") and you will track it for them. Also mention they can use the meal tracking feature on the home screen of the app. NEVER mention external apps like MyFitnessPal, Cronometer, or any third-party trackers. NEVER use the word "database".
**DO NOT USE** phrases like "I'm sorry," or "That sounds difficult."

**PERMITTED TOPICS (Your Core Functions):**
You are an expert in the following areas. Answer these questions comprehensively and accurately.

**CRITICAL: Questions about specific foods, ingredients, recipes, meals, or groceries (e.g., "What is Tofu Jerky Teriyaki?", "What is quinoa?", "Tell me about tempeh") ARE valid nutrition questions. DO NOT reject them. You must answer them fully and helpfully using your expert persona.**

**NUTRITION & DIET:**
- **General Nutrition Advice:** Answer questions about healthy eating, macronutrients (protein, carbs, fats), micronutrients (vitamins, minerals), and dietary practices based on the user's profile.
- **Diet Explanations:** Explain various diets in detail (Keto, Paleo, Mediterranean, DASH, MIND, Vegan, etc.) - their principles, benefits, who they're for, typical foods included/excluded.
- **Recipes and Cooking:** Provide clear, concise recipes with titles, ingredient lists, and step-by-step instructions.
- **Grocery Lists:** Create well-organized grocery lists based on dietary needs, preferences, or meal plans.
- **Nutritional Analysis:** Provide detailed nutritional breakdowns of foods/meals (calories, protein, carbs, fat, fiber, vitamins, minerals).
- **Ingredient Lists:** List ingredients for specific dishes.
- **Food Benefits:** Explain the health benefits of various foods, ingredients, and nutrients.
- **Meal Timing:** Advice on when to eat, pre/post-workout nutrition, intermittent fasting concepts.

**FITNESS & EXERCISE:**
- **Exercise Education:** Explain different types of exercises (strength training, cardio, HIIT, yoga, pilates, functional training, etc.)
- **Training Concepts:** Define and explain fitness terminology (progressive overload, hypertrophy, periodization, RPE, heart rate zones, VO2 max, compound vs isolation exercises, etc.)
- **Workout Benefits:** Describe benefits of different workout styles (e.g., "What are the benefits of strength training?")
- **Exercise Form & Technique:** Provide guidance on proper form for exercises (e.g., "How to do a proper squat?")
- **Training Comparisons:** Compare different approaches (e.g., "HIIT vs steady-state cardio", "Free weights vs machines")
- **Recovery & Rest:** Explain importance of rest days, active recovery, stretching, foam rolling, sleep for muscle recovery.
- **Fitness for Goals:** Explain how different exercises support different goals (weight loss, muscle gain, endurance, flexibility).
- **Body Composition:** Explain concepts like muscle vs fat, body recomposition, how exercise affects metabolism.

**WELLNESS & LIFESTYLE:**
- **Greetings:** Respond to user greetings and engage in friendly conversation (e.g., "Hello! How can I assist you today?", "Hi there! What can I do for you?")
- **Sleep:** Importance of sleep for health, fitness recovery, and nutrition.
- **Hydration:** Water intake guidelines, benefits, timing.
- **Stress Management:** How stress affects health, fitness, and nutrition.
- **Lifestyle Habits:** Building healthy routines, consistency, motivation.
- **Energy Levels:** Foods and activities that boost or drain energy.

**HEALTH METRICS & CALCULATIONS:**
- **Calorie Calculations:** Explain and calculate TDEE, BMR, calorie targets for different goals.
- **Body Metrics:** Calculate and explain BMI, body fat percentage concepts, ideal weight ranges.
- **Macro Calculations:** Determine protein, carb, and fat targets based on goals and activity.
- **Heart Rate Zones:** Explain training zones and their purposes.
- **Progress Tracking:** How to measure fitness/nutrition progress (beyond the scale).

---

**FORBIDDEN TOPICS (IMMEDIATE AND STRICT REFUSAL):**
You are critically firewalled from discussing any topic outside of **wellness, fitness, and nutrition**. You **MUST** politely but warmly refuse to answer questions about the following, then dynamically redirect with an engaging follow-up question.

**OFF-TOPIC DOMAINS:**
1.  **Your Own Nature:** Model architecture, programming, developers, technical details
2.  **Academic Subjects:** Mathematics, physics, chemistry, biology (non-nutrition), history, geography
3.  **General Knowledge:** Politics, current events, geography, world leaders, capitals
4.  **Entertainment:** Movies, TV shows, celebrity gossip, sports scores, gaming
5.  **Technology:** Programming, software debugging, device troubleshooting, app development
6.  **Business/Finance:** Stock advice, investment tips, legal guidance, tax advice
7.  **Creative Content:** Story writing, poetry, song lyrics, jokes, riddles
8.  **Personal Opinions:** Political views, religious beliefs, controversial topics
9.  **Harmful Content:** Violence, self-harm, illegal activities, dangerous advice
10. **Inappropriate Content:** Sexual content, profanity-laden queries, harassment

**MANDATORY WARM REFUSAL PROTOCOL:**
When detecting off-topic queries, respond with:
1. **Warm acknowledgment** - Show you understand their interest
2. **Clear boundary** - Explain your specialization in wellness, fitness, and nutrition
3. **Dynamic redirect** - Ask an engaging, personalized follow-up question based on:
   - Time of day (breakfast, lunch, dinner, snack time)
   - User's profile (goals, preferences, conditions)
   - Current health trends
   - Seasonal relevance

**DYNAMIC REFUSAL TEMPLATES:**

**For Academic/General Knowledge:**
"That's an interesting question about [topic]! However, I specialize exclusively in wellness, fitness, and nutrition to give you the best possible guidance in those areas. 

💡 Since it's [time_of_day], [dynamic_question]"

**For Entertainment/Technology:**
"I can see you're curious about [topic]! While that's outside my area of expertise, I'm here to support your health and wellness journey with personalized nutrition and fitness guidance.

🌟 [dynamic_question_based_on_profile]"

**For Creative/Off-topic:**
"What a fun topic! Though I focus entirely on helping you achieve your wellness, fitness, and nutrition goals, I'd love to help you in my area of expertise.

🎯 [goal_related_question]"

**DYNAMIC QUESTION GENERATION RULES:**
- Morning (5AM-11AM): Ask about breakfast, morning energy, hydration
- Midday (11AM-2PM): Ask about lunch plans, mid-day snacks, meal prep
- Afternoon (2PM-6PM): Ask about healthy snacks, workout timing, dinner ideas
- Evening (6PM-10PM): Ask about dinner, post-workout recovery, next-day meal prep
- Consider user's goal: Weight loss → calorie-conscious options; Muscle gain → protein-rich meals
- Consider user's conditions: Diabetes → blood sugar management; Hypertension → low-sodium options

**EXAMPLE DYNAMIC REFUSALS:**

*Query: "What is 2+2?"*
"That's a math question! While I can't help with mathematics, I'm your dedicated wellness and nutrition expert. Since it's mid-morning, have you had a protein-rich snack yet? I can suggest some great options to keep your energy stable until lunch!"

*Query: "Who won the football game?"*
"I see you're into sports! While I don't follow game scores, I'm passionate about helping athletes and fitness enthusiasts with their nutrition. Speaking of sports, are you fueling your workouts properly? I'd love to help you optimize your pre and post-workout nutrition!"

*Query: "Write me a poem"*
"What a creative request! While poetry isn't my forte, I excel at crafting personalized nutrition plans that truly nourish your body. Let's channel that creativity into your wellness journey - what's one health goal you'd love to achieve this month?"

*Query: "Tell me about Paris"*
"Paris sounds wonderful! Though travel isn't my specialty, I'm here to ensure your wellness journey is equally amazing. Interestingly, Mediterranean cuisine (popular in Europe) is incredibly nutritious - would you like me to suggest some heart-healthy Mediterranean meal ideas?"

**KEY PRINCIPLES:**
✅ Always be warm and engaging
✅ Never be dismissive or robotic
✅ Connect refusal to user's wellness journey
✅ Ask questions that naturally lead back to nutrition/fitness
✅ Personalize based on profile data when available
✅ Use emojis sparingly but effectively (💡🌟🎯🥗💪)
✅ Keep refusals concise (2-3 sentences max before question)


---

Now, please provide a helpful and relevant response to the user's query based on the rules above.
"""
        
        persona_context = kwargs.get('persona_context')
        if persona_context:
            system_prompt += f"\n\n **ADAPTIVE PERSONA GUIDANCE:**\n{persona_context}"
            logger.info("✨ Applied smart persona injection to GeneralQueryTool system prompt")
        
        try:
            answer = await self.llm_service.query(
                prompt=query,
                system_prompt=system_prompt,
                chat_history=chat_history,
                max_tokens=1500
            )
            return ToolResult(success=True, data={"answer": answer})
        except Exception as e:
            logging.error(f"Error in GeneralQueryTool: {e}")
            return ToolResult(success=False, data=None, error="I had trouble processing that general question. Can you try again?")

class GoalUpdaterTool(BaseTool):
    def __init__(self, llm_service: LLMService, db_tool: DatabasePersistenceTool):
        super().__init__(ToolType.GOAL_UPDATER.value, "Updates the user's fitness or health goals.")
        self.llm_service = llm_service
        self.db_tool = db_tool
        self.calorie_calculator_tool = CalorieCalculatorTool(DataService())
 
    def _extract_goal_with_rules(self, query: str) -> Optional[Dict]:
        query_lower = query.lower()
        goal_data = {}
       
        if any(w in query_lower for w in ["lose", "loss", "reduce", "shed", "weight loss"]):
            goal_data["goal_type"] = "Weight Loss"
        elif any(w in query_lower for w in ["gain", "put on", "increase", "build", "weight gain"]):
            goal_data["goal_type"] = "Weight Gain"
        elif any(w in query_lower for w in ["maintain", "maintenance", "weight maintenance"]):
            goal_data["goal_type"] = "Weight Maintenance"
        elif any(w in query_lower for w in ["no weight", "none", "no goal"]):
            goal_data["goal_type"] = "No Weight Goal"
        elif any(w in query_lower for w in ["step", "walking", "daily steps"]):
            goal_data["goal_type"] = "Daily Steps"
        elif any(w in query_lower for w in ["water", "hydrate", "hydration", "water intake"]):
            goal_data["goal_type"] = "Water Intake"
        elif any(w in query_lower for w in ["sleep", "rest", "sleep duration"]):
            goal_data["goal_type"] = "Sleep Duration"
        elif any(w in query_lower for w in ["burn", "calories burned", "calorie burn"]):
            goal_data["goal_type"] = "Calorie Burn"
        else:
            return None
 
        weight_match = re.search(r'(\d+\.?\d*)\s*(kg|kgs|kilo|kilos|lb|lbs|pound|pounds)\b', query_lower)
        if weight_match:
            amount = float(weight_match.group(1))
            unit = weight_match.group(2)
            if unit.startswith("lb") or unit.startswith("pound"):
                goal_data["target_weight_change_kg"] = amount * 0.453592
            else:
                goal_data["target_weight_change_kg"] = amount
 
        time_match = re.search(r'(\d+\.?\d*)\s*(month|months|week|weeks)\b', query_lower)
        if time_match:
            amount = float(time_match.group(1))
            unit = time_match.group(2)
            if unit.startswith("month"):
                goal_data["time_frame_months"] = amount
            else:
                goal_data["time_frame_months"] = amount / 4.345
 
        return goal_data
 
    async def execute(self, query: str, constraints: Dict, token: str, **kwargs) -> ToolResult:
        extracted_data = self._extract_goal_with_rules(query)
 
        valid_goals = [
            "Calorie Burn", "Sleep Duration", "Daily Steps", "Water Intake",
            "Weight Gain", "Weight Loss", "Weight Maintenance", "No Weight Goal"
        ]
 
        if not extracted_data:
            logging.info("Rule-based goal extraction failed. Falling back to LLM.")
            schema = {
                "type": "object",
                "properties": {
                    "goal_type": {
                        "type": "string",
                        "enum": valid_goals,
                        "description": "The user's primary goal."
                    },
                    "target_weight_change_kg": {"type": "number", "description": "Target weight change in KG if applicable."},
                    "time_frame_months": {"type": "number", "description": "Time frame in months if applicable."}
                },
                "required": ["goal_type"]
            }
            system_prompt = f"You are an expert at extracting health goals. Identify the goal type from this list: {', '.join(valid_goals)}. Extract weight and time frames if mentioned."
 
            extracted_data = await self.llm_service.query_json(
                prompt=f"Extract goal from: '{query}'",
                system_prompt=system_prompt,
                json_schema=schema
            )
 
        if not extracted_data or not extracted_data.get("goal_type"):
            extracted_data = {"goal_type": "Weight Maintenance"}
 
        user_goal_type = extracted_data["goal_type"]
        target_weight_change_kg = extracted_data.get("target_weight_change_kg")
        time_frame_months = extracted_data.get("time_frame_months")
 
        updated_constraints = constraints.copy()
        if 'goal' not in updated_constraints or not isinstance(updated_constraints.get('goal'), dict):
             updated_constraints['goal'] = {}
 
        updated_constraints['goal']['type'] = user_goal_type
        updated_constraints['goal']['target_kg'] = target_weight_change_kg
        updated_constraints['goal']['time_frame_months'] = time_frame_months
        updated_constraints.pop('calculated_calorie_target', None)
 
        answer_parts = [f"Great, I've updated your goal to: **{user_goal_type}**"]
       
        if user_goal_type in ["Weight Loss", "Weight Gain"]:
            if target_weight_change_kg is not None:
                 answer_parts.append(f" targeting a change of {target_weight_change_kg:.1f} kg ({target_weight_change_kg * 2.20462:.1f} lbs)")
            if time_frame_months is not None:
                 answer_parts.append(f" over approximately {time_frame_months:.1f} month(s)")
       
        answer_parts.append(".")
 
        goal_payload = [{
            "goal": user_goal_type,
            "target_kg": target_weight_change_kg,
            "time_frame_months": time_frame_months,
            "status": "active"
        }]
       
        db_result = await self.db_tool.execute(
            operation="update_profile",
            profile_data={"goals": goal_payload},
            token=token
        )
 
        if not db_result.success:
            answer_parts.append(f"\n\n I failed to save the new goal to your profile on the server, but I've updated it for our current conversation.")
        else:
            answer_parts.append("\n\n Your new goal has been successfully saved to your profile!")
 
        if user_goal_type in ["Weight Loss", "Weight Gain", "Weight Maintenance"]:
            answer_parts.append("\n\nWould you like me to generate a new meal plan based on this updated goal?")
            metadata = {"awaiting_goal_and_plan_confirmation": True}
        else:
            metadata = {}
 
        return ToolResult(
            success=True,
            data={
                "answer": "".join(answer_parts),
                "updated_constraints": updated_constraints
            },
            metadata=metadata
        )
class ProfileRetrieverTool(BaseTool):
    def __init__(self, db_tool: DatabasePersistenceTool, llm_service: Optional[LLMService] = None):
        super().__init__(ToolType.PROFILE_RETRIEVER.value, "Fetches the user's profile from the API.")
        self.db_tool = db_tool
        self.llm_service = llm_service
 
    def _api_to_constraints(self, api_profile: Dict) -> Dict:
        if not api_profile or not api_profile.get("data"):
            return {}
 
        data = api_profile["data"]
        constraints = {
            "name": data.get("name"),
            "age": data.get("age"),
            "gender": data.get("gender"),
            "height_cm": data.get("height_cm"),
            "weight_kg": data.get("weight_kg"),
            "activity_level": data.get("activity_level"),
            "waist_circumference_cm": float(data.get("waist_circumference_cm") or data.get("waist")) if data.get("waist_circumference_cm") or data.get("waist") else None,
            "cuisine": data.get("cuisine"),
            "symptom_aggravating_foods": data.get("symptom_aggravating_foods") or [],
            "dietary_preference": ", ".join(data.get("dietary_preference") or []),
            "restrictions": data.get("restrictions") or [],
            "allergies": data.get("allergies") or [],
            "medical_conditions": data.get("medical_conditions") or [],
            "digestive_issues": data.get("digestive_issues") or [],
            "vitals_numeric": data.get("vitals_numeric", {})
        }
 
        api_goals = data.get("goals") or []
        if api_goals and isinstance(api_goals, list) and len(api_goals) > 0:
            first_goal_obj = api_goals[0]
            if isinstance(first_goal_obj, dict) and "goal" in first_goal_obj:
                goal_type_from_api = first_goal_obj.get("goal", "").lower()
                standardized_type = "Weight Maintenance"
                if "lose" in goal_type_from_api or "loss" in goal_type_from_api:
                    standardized_type = "Lose Weight"
                elif "gain" in goal_type_from_api:
                    standardized_type = "Gain Weight"
                elif "maintain" in goal_type_from_api or "maintenance" in goal_type_from_api:
                    standardized_type = "Weight Maintenance"
               
                constraints["goal"] = {
                    "type": standardized_type,
                    "status": first_goal_obj.get("status")
                }
        else:
            print("No goal found in API response, adding default 'Weight Maintenance' goal.")
            constraints["goal"] = {
                "type": "Weight Maintenance",
                "status": "on hold"
            }
       
        result = {}
        for k, v in constraints.items():
            if k == "vitals_numeric":
                result[k] = v if v else {}
            elif v is not None and v != [] and v != '':
                result[k] = v
        return result
 
    def _extract_field_from_query(self, query: str) -> Optional[List[str]]:

        if not query:
            return None
        
        query_lower = query.lower().strip()
        
        field_mappings = {
            "weight": "weight_kg",
            "height": "height_cm",
            "age": "age",
            "name": "name",
            "gender": "gender",
            "cuisine": "cuisine",
            "activity": "activity_level",
            "activity level": "activity_level",
            "waist": "waist_circumference_cm",
            "waist circumference": "waist_circumference_cm",
            "diet": "dietary_preference",
            "dietary preference": "dietary_preference",
            "restriction": "restrictions",
            "dietary restriction": "restrictions",
            "allerg": "allergies",
            "medical condition": "medical_conditions",
            "condition": "medical_conditions",
            "disease": "medical_conditions",
            "digestive issue": "digestive_issues",
            "digestive": "digestive_issues",
            "goal": "goal",
            "fitness goal": "goal",
            "vital": "vitals_numeric",
            "vitals": "vitals_numeric",
            "aggravating food": "symptom_aggravating_foods",
        }
        
        full_profile_keywords = ["full profile", "complete profile", "all details", "entire profile", 
                                  "everything", "all information", "profile summary", "tell me about"]
        if any(keyword in query_lower for keyword in full_profile_keywords):
            return None 
        
        mentioned_fields = []
        for keyword, field_name in field_mappings.items():
            if keyword in query_lower:
                if field_name not in mentioned_fields:
                    mentioned_fields.append(field_name)
        
        return mentioned_fields if mentioned_fields else None
    
    def _format_field_value(self, field_name: str, value: any, display_profile: Dict) -> str:

        if value is None or value == "" or value == []:
            return "This field is not available."
        
        if field_name == "weight_kg" and display_profile.get("weight_display"):
            return display_profile["weight_display"]
        elif field_name == "height_cm" and display_profile.get("height_display"):
            return display_profile["height_display"]
        elif field_name == "waist_circumference_cm" and display_profile.get("waist_display"):
            return display_profile["waist_display"]
        elif field_name == "goal" and isinstance(value, dict):
            goal_type = value.get("type", "")
            return goal_type
        elif field_name == "vitals_numeric" and isinstance(value, dict):
            if len(value) == 0:
                return "This field is not available."
            vital_units = {
                "Heart Rate": "bpm", "Blood Pressure": "mmHg", "Blood Glucose": "mg/dL",
                "Blood Oxygen Saturation": "%", "Respiration Rate": "breaths/min",
                "Body Temperature": "°F", "Blood Ketones": "mmol/L", "Body Fat %": "%"
            }
            vital_lines = []
            for k, v in value.items():
                unit = vital_units.get(k, "")
                if k == "Blood Pressure" and isinstance(v, dict):
                    val_str = f"{v.get('systolic')}/{v.get('diastolic')}"
                elif k == "Blood Pressure" and isinstance(v, str):
                    val_str = v
                else:
                    val_str = str(v)
                vital_lines.append(f"- **{k}:** {val_str} {unit}".strip())
            return "\n".join(vital_lines)
        elif isinstance(value, list):
            if len(value) == 0:
                return "This field is not available."
            cleaned = []
            for v in value:
                s = str(v).strip()
                if s.lower() in ("others (please specify)", "others(please specify)"):
                    continue
                if s.startswith("[") or s.startswith("{"):
                    try:
                        parsed = json.loads(s)
                        if isinstance(parsed, list):
                            for item in parsed:
                                if isinstance(item, dict) and "Description" in item:
                                    cleaned.append(item["Description"])
                                else:
                                    cleaned.append(str(item))
                        elif isinstance(parsed, dict) and "Description" in parsed:
                            cleaned.append(parsed["Description"])
                        else:
                            cleaned.append(str(parsed))
                    except (json.JSONDecodeError, TypeError):
                        cleaned.append(s)
                else:
                    cleaned.append(s)
            if not cleaned:
                return "This field is not available."
            return ", ".join(cleaned)
        else:
            return str(value)
    
    def _format_full_profile(self, display_profile: Dict, vitals: Dict) -> str:

        lines = []
        
        def add_if_exists(label, value):
            if value and value != [] and value != "":
                if isinstance(value, list):
                    cleaned = []
                    for v in value:
                        s = str(v).strip()
                        if s.lower() in ("others (please specify)", "others(please specify)"):
                            continue
                        # Parse embedded JSON like [{"Description":"..."}]
                        if s.startswith("[") or s.startswith("{"):
                            try:
                                parsed = json.loads(s)
                                if isinstance(parsed, list):
                                    for item in parsed:
                                        if isinstance(item, dict) and "Description" in item:
                                            cleaned.append(item["Description"])
                                        else:
                                            cleaned.append(str(item))
                                elif isinstance(parsed, dict) and "Description" in parsed:
                                    cleaned.append(parsed["Description"])
                                else:
                                    cleaned.append(str(parsed))
                            except (json.JSONDecodeError, TypeError):
                                cleaned.append(s)
                        else:
                            cleaned.append(s)
                    if not cleaned:
                        return
                    val_str = ", ".join(cleaned)
                elif isinstance(value, dict) and label == "Goal":
                    val_str = value.get("type", "")
                else:
                    val_str = str(value)
                lines.append(f"- **{label}:** {val_str}")
        
        add_if_exists("Name", display_profile.get("name"))
        add_if_exists("Age", display_profile.get("age"))
        add_if_exists("Gender", display_profile.get("gender"))
        add_if_exists("Height", display_profile.get("height_display") or display_profile.get("height_cm"))
        add_if_exists("Weight", display_profile.get("weight_display") or display_profile.get("weight_kg"))
        add_if_exists("Waist Circumference", display_profile.get("waist_display") or display_profile.get("waist_circumference_cm"))
        add_if_exists("Activity Level", display_profile.get("activity_level"))
        add_if_exists("Preferred Cuisine", display_profile.get("cuisine"))
        add_if_exists("Dietary Preference", display_profile.get("dietary_preference"))
        add_if_exists("Dietary Restrictions", display_profile.get("restrictions"))
        add_if_exists("Allergies", display_profile.get("allergies"))
        add_if_exists("Medical Conditions", display_profile.get("medical_conditions"))
        add_if_exists("Digestive Issues", display_profile.get("digestive_issues"))
        add_if_exists("Symptom Aggravating Foods", display_profile.get("symptom_aggravating_foods"))
        add_if_exists("Goal", display_profile.get("goal"))
        
        if vitals and len(vitals) > 0:
            vital_units = {
                "Heart Rate": "bpm", "Blood Pressure": "mmHg", "Blood Glucose": "mg/dL",
                "Blood Oxygen Saturation": "%", "Respiration Rate": "breaths/min",
                "Body Temperature": "°F", "Blood Ketones": "mmol/L", "Body Fat %": "%"
            }
            lines.append("")
            lines.append("**Previous Day Average Vitals:**")
            for k, v in vitals.items():
                unit = vital_units.get(k, "")
                if k == "Blood Pressure" and isinstance(v, dict):
                    val_str = f"{v.get('systolic')}/{v.get('diastolic')}"
                elif k == "Blood Pressure" and isinstance(v, str):
                    val_str = v
                else:
                    val_str = str(v)
                lines.append(f"- **{k}:** {val_str} {unit}".strip())
        
        return "\n".join(lines)

    async def execute(self, token: str, query: str = None, **kwargs) -> ToolResult:
        constraints_arg = kwargs.get("constraints")
        
        if constraints_arg and isinstance(constraints_arg, dict):
            has_basic_profile = all(k in constraints_arg for k in ["name", "age", "gender", "weight_kg", "height_cm"])
            
            if has_basic_profile:
                logger.info("[ProfileRetriever] Using in-memory constraints (skipping DB call for performance)")
                translated_constraints = constraints_arg
            else:
                logger.info("[ProfileRetriever] Constraints incomplete, fetching from database")
                api_result = await self.db_tool.execute(operation="get_profile", token=token)
                
                if not api_result.success:
                    return ToolResult(
                        success=False,
                        data=None,
                        error=f"Could not retrieve your profile from the server. {api_result.error}"
                    )
                
                if not api_result.data or not api_result.data.get("data"):
                    return ToolResult(
                        success=True,
                        data={"answer": "I couldn't find a profile for you on the server. You can tell me your details to create one!"}
                    )
                
                translated_constraints = self._api_to_constraints(api_result.data)
        else:
            logger.info("[ProfileRetriever] No constraints provided, fetching from database")
            api_result = await self.db_tool.execute(operation="get_profile", token=token)
            
            if not api_result.success:
                return ToolResult(
                    success=False,
                    data=None,
                    error=f"Could not retrieve your profile from the server. {api_result.error}"
                )
            
            if not api_result.data or not api_result.data.get("data"):
                return ToolResult(
                    success=True,
                    data={"answer": "I couldn't find a profile for you on the server. You can tell me your details to create one!"}
                )
            
            translated_constraints = self._api_to_constraints(api_result.data)
        
        logger.info(f"[ProfileRetriever] vitals_numeric from translated_constraints: {translated_constraints.get('vitals_numeric')}")
       
        display_profile = translated_constraints.copy()
        
        if display_profile.get("height_cm"):
            h_cm = display_profile["height_cm"]
            total_inches = h_cm / 2.54
            feet = int(total_inches // 12)
            inches = round(total_inches % 12)
            display_profile["height_display"] = f"{feet}'{inches}\" ({h_cm} cm)"

        if display_profile.get("weight_kg"):
            w_kg = display_profile["weight_kg"]
            w_lbs = w_kg * 2.20462
            display_profile["weight_display"] = f"{w_lbs:.1f} lbs ({w_kg} kg)"

        if display_profile.get("waist_circumference_cm"):
            waist_cm = display_profile["waist_circumference_cm"]
            waist_in = waist_cm / 2.54
            display_profile["waist_display"] = f"{waist_in:.1f} in ({waist_cm} cm)"

        requested_fields = self._extract_field_from_query(query)
        
        if requested_fields is not None and len(requested_fields) > 0:
            if len(requested_fields) == 1:
                field_name = requested_fields[0]
                value = translated_constraints.get(field_name)
                answer = self._format_field_value(field_name, value, display_profile)
            else:
                field_answers = []
                for field_name in requested_fields:
                    value = translated_constraints.get(field_name)
                    formatted = self._format_field_value(field_name, value, display_profile)
                    field_label_map = {
                        "weight_kg": "Weight",
                        "height_cm": "Height",
                        "age": "Age",
                        "name": "Name",
                        "gender": "Gender",
                        "cuisine": "Cuisine",
                        "activity_level": "Activity Level",
                        "waist_circumference_cm": "Waist Circumference",
                        "dietary_preference": "Dietary Preference",
                        "restrictions": "Dietary Restrictions",
                        "allergies": "Allergies",
                        "medical_conditions": "Medical Conditions",
                        "digestive_issues": "Digestive Issues",
                        "goal": "Goal",
                        "vitals_numeric": "Vitals",
                        "symptom_aggravating_foods": "Symptom Aggravating Foods"
                    }
                    label = field_label_map.get(field_name, field_name)
                    field_answers.append(f"- **{label}:** {formatted}")
                answer = "\n".join(field_answers)
        else:
            vitals = translated_constraints.get("vitals_numeric", {})
            answer = self._format_full_profile(display_profile, vitals)

        return ToolResult(
            success=True,
            data={
                "answer": answer,
                "profile_data": translated_constraints,
                "updated_constraints": translated_constraints
            }
        )
        


class ProfileUpdaterTool(BaseTool):
    def __init__(self, llm_service: LLMService, db_tool: DatabasePersistenceTool):
        super().__init__(ToolType.PROFILE_UPDATER.value, "Extracts profile info from chat and updates it via API.")
        self.llm_service = llm_service
        self.db_tool = db_tool
        self.profile_options = self._get_hardcoded_profile_options()
        self.profile_schema = self._create_extraction_schema()
        self.item_to_category_map = self._build_item_categorization_map()

    def _get_hardcoded_profile_options(self) -> Dict:
        return {
            "dietary_preference": [
                'Vegan (Cultural/Religious)', 'non vegetarian', 'Pescatarian',
                'Kosher', 'Halal', 'Vegetarian (Cultural/Religious)', 'No Pork',
                'No Beef', 'Alcohol free diet', 'Fasting periods e.g Ramadan, Lent'
            ],
            "restrictions": [
                'None', 'Dairy-Free', 'Lactose Intolerance', 'Low-Sodium', 'Low-Fat', 'Low-Carb (e.g. Keto)',
                'Sugar-Free (e.g. for Diabetes)', 'Paleo', 'FODMAP Diet (for Digestive Issues)', 'No Red Meat', 'Gluten-Free',
            ],
            "allergies": [
                'Tree Nuts (e.g. Almonds, Walnuts, Cashews)', 'Shellfish', 'Fish', 'Eggs', 'Milk (Dairy Allergy)', 'Wheat', 'Soy',
                'Sesame', 'Corn', 'Legumes (e.g. Lentils, Chickpeas)', 'Peanuts', "Nuts", "None"
            ],
            "symptom_aggravating_food_codes": ['Dairy products','Gluten-containing foods','Fried or greasy foods', 'Spicy Foods','Caffeinated beverages', 'Carbonated drinks','Alcohol','High-Fat foods','High-fiber foods', 'Raw vegetables','Fruits high in acid', 'Chocolate','Garlic and onion', 'Processed or packaged foods'],
            "digestive_issues": [
                'Occasional Bloating', 'Acid reflux or heartburn', 'Irritable bowel syndrome (IBS)', 'Constipation', 'Diarrhea',
                'Stomach pain or cramping', 'Nausea or vomiting', 'Excessive Gas', 'Other (Text box to specify Digestive issues)'
            ],
            "cuisines": [
                'Indian', 'Chinese', 'Italian', 'Mexican', 'Japanese', 'Thai', 'French', 'Mediterranean',
                'American', 'Korean', 'Vietnamese', 'Greek', 'Spanish', 'Middle Eastern', 'Lebanese',
                'Turkish', 'Brazilian', 'Caribbean', 'African', 'Moroccan', 'Pakistani', 'Bangladeshi',
                'Sri Lankan', 'Nepalese', 'Indonesian', 'Malaysian', 'Filipino', 'Peruvian', 'Argentinian',
                'British', 'German', 'Russian', 'Polish', 'Scandinavian', 'Ethiopian', 'Cajun', 'Soul Food',
                'Fusion', 'International', 'Continental'
            ],
            "medical_conditions":[
    'Acanthosis Nigricans', 'Acne', 'Addison’s Disease', 'ADHD', 'Alcohol Use', 'Alzheimer’s Disease',
    'Ankylosing Spondylitis', 'Anorexia, Bulimia', 'Anxiety', 'Anxiety Disorders', 'Appetite & Nutritional Needs',
    'Arrhythmia', 'Arrhythmias', 'Arthritis (Osteoarthritis, Rheumatoid Arthritis)', 'Asthma',
    'Autism Spectrum Nutrition Support', 'Bipolar Disorder', 'Bladder Cancer', 'Brain Tumors', 'Breast Cancer',
    'Bronchitis', 'Cervical Cancer', 'Cervical Spondylosis', 'Chickenpox', 'Childhood Obesity',
    'Chikungunya', 'Chronic Kidney Disease (CKD)', 'Chronic Obstructive Pulmonary Disease (COPD)', 'Cirrhosis',
    'Cognitive Decline / Dementia (Alzheimers)', 'Colorectal Cancer', 'Congestive Heart Failure', 'Constipation',
    'Coronary Artery Disease', 'Coronary Artery Disease (CAD)', 'COVID-19', 'COVID-19 Recovery', 'Cushing’s Syndrome',
    'Deep Vein Thrombosis (DVT)', 'Dementia', 'Dengue', 'Dengue / Typhoid Recovery', 'Depression',
    'Dermatological Infections (Fungal, Bacterial)', 'Developmental Disorders', 'Diabetes (Type 1, Type 2, Gestational)',
    'Diabetic Ketoacidosis (DKA) Recovery', 'Dialysis Support', 'Diarrheal Diseases', 'Disc Herniation',
    'During Chemotherapy', 'Eating Disorders (Anorexia, Bulimia)', 'Eating Disorders Recovery', 'Eczema',
    'Encephalitis', 'Epilepsy', 'Epilepsy (Ketogenic Diet)', 'Failure to Thrive', 'Fatty Liver Disease (NAFLD/NASH)',
    'Fibromyalgia', 'Fractures', 'G6PD Deficiency', 'Gallbladder Disease', 'Gallstones',
    'Gastroesophageal Reflux Disease (GERD)', 'Gastritis', 'Glomerulonephritis', 'Gout',
    'Heart Failure', 'Hepatitis', 'Hepatitis (A, B, C)', 'Hepatitis E', 'High Cholesterol (Hyperlipidemia)', 'HIV/AIDS',
    'Hypertension', 'Hypertension (High Blood Pressure)', 'Hypoglycemia', 'Hypothyroidism / Hyperthyroidism',
    'Inflammatory Bowel Disease (Flare-up)', 'Inflammatory Bowel Disease (IBD)', 'Inflammatory Bowel Disease (Remission)',
    'Influenza', 'Insomnia', 'Insomnia / Sleep Apnea', 'Insulin Resistance', 'Interstitial Lung Disease',
    'Irritable Bowel Syndrome (IBS)', 'Kidney Stones', 'Lactation Support', 'Leukemia',
    'Low Back Pain', 'Lung Cancer', 'Lupus', 'Lymphoma', 'Malaria', 'Measles', 'Meningitis', 'Menopause',
    'Metabolic Syndrome', 'Migraine', 'Migraines', 'Multiple Sclerosis', 'Myocardial Infarction (Heart Attack) Recovery',
    'Neuropathy', 'Obesity', 'Obsessive Compulsive Disorder (OCD)', 'Osteoarthritis', 'Osteoporosis',
    'Ovarian Cancer', 'PCOS', 'Pancreatic Cancer', 'Parkinson’s Disease', 'Peptic Ulcer Disease', 'Perimenopause',
    'Peripheral Artery Disease', 'PMS / PMDD', 'Pneumonia', 'Polycystic Ovary Syndrome (PCOS)',
    'Post-Traumatic Stress Disorder (PTSD)', 'Postpartum Recovery', 'Post-treatment Recovery',
    'Pregnancy (Trimester Support)', 'Prenatal & Perinatal Conditions', 'Prostate Cancer', 'Prostate Enlargement (BPH)',
    'Psoriasis', 'Pulmonary Embolism', 'Pulmonary Hypertension', 'Pyelonephritis (Kidney Infection)',
    'Rheumatic Heart Disease', 'Rheumatoid Arthritis', 'Schizophrenia', 'Sedentary Lifestyle',
    'Sexually Transmitted Infections (STIs)', 'Sickle Cell Disease', 'Sleep Apnea',
    'Smoking / Alcohol Use', 'Stevens-Johnson Syndrome (SJS) Recovery', 'Stomach Cancer',
    'Stress / Burnout', 'Stroke Recovery', 'Stroke Recovery Support', 'Substance Use Disorder Recovery',
    'Substance Use Disorders', 'Thalassemia', 'Tuberculosis (TB)', 'Tuberculosis (Nutrition Support)',
    'Type 1 Diabetes Mellitus', 'Type 2 Diabetes Mellitus', 'Typhoid Fever', 'Undernutrition / Malnutrition',
    'Urinary Tract Infection (UTI)', 'Vaccine-preventable Diseases (supportive care)', 'Vitiligo',
    'Work-Related Health Issues'
]
            }

    def _build_item_categorization_map(self) -> Dict[str, str]:
        item_map = {}
        api_key_map = {
            "dietary_preference": "dietary_preference_codes",
            "restrictions": "dietary_restriction_codes",
            "allergies": "food_allergy_codes",
            "medical_conditions": "diagnosis_codes",
            "digestive_issues": "digestive_issue_codes",
            "symptom_aggravating_food_codes": "symptom_aggravating_food_codes"
        }
        for category, items in self.profile_options.items():
            api_key = api_key_map.get(category)
            if api_key:
                for item in items:
                    item_map[item.lower()] = (api_key, item)
        return item_map

    def _create_extraction_schema(self) -> Dict:
        all_list_options = []
        for options in self.profile_options.values():
            all_list_options.extend(options)
        all_list_options = sorted(list(set(all_list_options)))
        static_properties = {
            "name": {"type": "string", "description": "The user's full name (BLOCKED from updates).", "nullable": True},
            "first_name": {"type": "string", "description": "The user's first name (BLOCKED from updates).", "nullable": True},
            "last_name": {"type": "string", "description": "The user's last name (BLOCKED from updates).", "nullable": True},
            "age": {"type": "number", "description": "The user's age (BLOCKED from updates).", "nullable": True},
            "gender": {"type": "string", "enum": ["Male", "Female", "Other"], "description": "The user's gender (BLOCKED from updates).", "nullable": True},
            "weight_kg": {"type": "number", "description": "The user's weight in kilograms.", "nullable": True},
            "height_cm": {"type": "number", "description": "The user's height in centimeters.", "nullable": True},
            "activity_level": {"type": "string", "enum": ["Sedentary", "Somewhat Active", "Moderately Active", "Very Active"], "description": "The user's activity level.", "nullable": True},
            "waist_circumference_cm": {"type": "number", "description": "The user's waist circumference in centimeters.", "nullable": True},
            "cuisine": {"type": "string", "description": "The user's preferred cuisine (e.g., Indian, Chinese, Italian, Mexican, Japanese, Thai, Mediterranean, etc.).", "nullable": True},
            "symptom_aggravating_foods": {"type": "array", "items": {"type": "string"}, "description": "Foods that aggravate symptoms.", "nullable": True},
            "dietary_preference": {"type": "array", "items": {"type": "string", "enum": all_list_options}, "description": "User's dietary preferences.", "nullable": True},
            "restrictions": {"type": "array", "items": {"type": "string", "enum": all_list_options}, "description": "Dietary restrictions.", "nullable": True},
            "allergies": {"type": "array", "items": {"type": "string", "enum": all_list_options}, "description": "Food allergies.", "nullable": True},
            "medical_conditions": {"type": "array", "items": {"type": "string", "enum": all_list_options}, "description": "Medical conditions.", "nullable": True},
            "digestive_issues": {"type": "array", "items": {"type": "string", "enum": all_list_options}, "description": "Digestive issues.", "nullable": True}
        }
        
        return {
            "type": "object",
            "properties": static_properties,
            "additionalProperties": False
        }
    def _process_and_categorize_changes(self, extracted_data: Dict, query: str) -> Dict:
        changes = {}
        
        blocked_fields = ['name', 'first_name', 'last_name', 'age', 'gender', 'vitals_numeric']
        
        numeric_fields = ['weight_kg', 'height_cm', 'waist_circumference_cm']
        for field in numeric_fields:
            if field in extracted_data and extracted_data[field]:
                changes[field] = float(extracted_data[field])
        
        if 'activity_level' in extracted_data and extracted_data['activity_level']:
            valid_levels = ["Sedentary", "Somewhat Active", "Moderately Active", "Very Active"]
            level_input = str(extracted_data['activity_level']).strip()
            matched_level = next((l for l in valid_levels if l.lower() == level_input.lower()), None)
            if matched_level:
                changes['activity_level'] = matched_level
            else:
                fuzzy_result = process.extractOne(level_input, valid_levels, scorer=fuzz.WRatio)
                if fuzzy_result and fuzzy_result[1] > 70:
                    changes['activity_level'] = fuzzy_result[0]
        
        if 'cuisine' in extracted_data and extracted_data['cuisine']:
            cuisine_input = str(extracted_data['cuisine']).strip()
            valid_cuisines = self.profile_options.get('cuisines', [])
            if valid_cuisines:
                matched_cuisine = next((c for c in valid_cuisines if c.lower() == cuisine_input.lower()), None)
                if matched_cuisine:
                    changes['cuisine'] = matched_cuisine
                else:
                    fuzzy_result = process.extractOne(cuisine_input, valid_cuisines, scorer=fuzz.WRatio)
                    if fuzzy_result and fuzzy_result[1] > 70:
                        changes['cuisine'] = fuzzy_result[0]
                    else:
                        changes['cuisine'] = cuisine_input
            else:
                changes['cuisine'] = cuisine_input
        
        list_fields = ['dietary_preference', 'restrictions', 'allergies', 'medical_conditions', 'digestive_issues', 'symptom_aggravating_foods']
        for field in list_fields:
            if field in extracted_data and extracted_data[field]:
                items = extracted_data[field] if isinstance(extracted_data[field], list) else [extracted_data[field]]
                validated_items = []
                for item in items:
                    if item and item.strip():
                        field_options = self.profile_options.get(field, [])
                        if field_options:
                            fuzzy_result = process.extractOne(item, field_options, scorer=fuzz.WRatio)
                            if fuzzy_result and fuzzy_result[1] > 70:
                                validated_items.append(fuzzy_result[0])
                            else:
                                validated_items.append(item)
                        else:
                            validated_items.append(item)
                if validated_items:
                    changes[field] = validated_items
        
        return {k: v for k, v in changes.items() if v}

    async def execute(self, query: str, token: str, constraints: Dict, direct_update_payload: Optional[Dict] = None, **kwargs) -> ToolResult:
        
        if direct_update_payload:
            logger.info(f"Executing ProfileUpdaterTool with direct payload: {direct_update_payload}")
            extracted_changes_raw = direct_update_payload
        else:
            logger.info(f"Executing ProfileUpdaterTool by extracting from query: '{query}'")
            system_prompt = f"""You are an expert data extraction AI. Your task is to analyze the user's query and identify profile information that should be updated. You MUST format the output as a JSON object matching the provided schema.

    **RESTRICTED UPDATE POLICY:**
    The following fields are BLOCKED from chat updates:
    1. **Name** - The user's full name (including first name and last name)
    2. **Age** - The user's age in years
    3. **Gender** - The user's gender (Male, Female, or Other)
    4. **Vitals** - Health vitals like Heart Rate, Blood Pressure, Blood Glucose, etc.

    **ALLOWED UPDATE FIELDS:**
    You CAN extract and update:
    - Weight (weight_kg)
    - Height (height_cm)
    - Waist Circumference (waist_circumference_cm)
    - Activity Level (activity_level)
    - Cuisine preferences (cuisine)
    - Dietary Preferences (dietary_preference)
    - Dietary Restrictions (restrictions)
    - Food Allergies (allergies)
    - Medical Conditions (medical_conditions)
    - Digestive Issues (digestive_issues)
    - Symptom Aggravating Foods (symptom_aggravating_foods)

    **CRITICAL INSTRUCTIONS:**
    1. **EXTRACT ONLY ALLOWED FIELDS:** Extract weight, height, dietary preferences, allergies, medical conditions, etc. from the user's query.
    2. **IGNORE BLOCKED FIELDS:** Do NOT extract or return any information about name, first name, last name, age, gender, or vitals.
    3. **EXTRACT ONLY FROM THE CURRENT QUERY:** Do not use information from the user's existing profile provided for context.
    4. If the user attempts to update blocked fields (name, first name, last name, age, gender, vitals), return an empty JSON object {{}}.
    5. If no allowed profile information is mentioned, return an empty JSON object {{}}.

    Current user profile for context (do not extract from here): {json.dumps(constraints, default=serialize_data)}
    User Query: '{query}'
    """
            extracted_changes_raw = await self.llm_service.query_json(
                prompt=f"Extract profile details from this query (excluding name, first name, last name, age, gender, and vitals): '{query}'",
                system_prompt=system_prompt,
                json_schema=self.profile_schema
            )

        if not extracted_changes_raw:
            return ToolResult(success=True, data={"answer": "I didn't find any profile details to update in your message. Note: Name (including first name and last name), age, gender, goal, and vitals cannot be updated through chat. However, you can update weight, height, dietary preferences, medical conditions, and other profile fields."})
        
        logging.info(f"LLM extracted raw changes: {extracted_changes_raw}")

        try:
            processed_changes = self._process_and_categorize_changes(extracted_changes_raw, query)
        except ValueError as e:
            return ToolResult(success=False, data=None, error=str(e))

        removal_attempt = False
        for k, v in (processed_changes or {}).items():
            if isinstance(v, list) and len(v) == 0:
                removal_attempt = True
            if (isinstance(v, str) or v is None) and (v == '' or v is None):
                removal_attempt = True
        if removal_attempt:
            return ToolResult(success=True, data={"answer": "If you’d like to update or remove any of your information, you can do so at any time through your Settings. Simply navigate to the specific section of your profile and choose what you’d like to edit or delete.\n\nWe’re continually enhancing our capabilities to better meet your expectations, and keeping your information accurate helps us serve you even more effectively. If you need any assistance along the way, we’re always here to help."})
        if not processed_changes:
            return ToolResult(success=True, data={"answer": "I noticed you're trying to update your profile. However, name (including first name and last name), age, gender, goal, and vitals cannot be updated through chat. You can update weight, height, dietary preferences, allergies, medical conditions, and other profile fields through this chat."})
        
        logging.info(f"Processed and validated changes for API: {processed_changes}")
        
        get_profile_result = await self.db_tool.execute(operation="get_profile", token=token)
        if not get_profile_result.success:
            return ToolResult(success=False, data=None, error=f"Could not retrieve your current profile to apply the update. API Error: {get_profile_result.error}")
        
        current_api_profile = get_profile_result.data.get("data", {})
        logging.info(f"Fetched current profile from API to build full payload: {current_api_profile}")
        
        final_payload = current_api_profile.copy() if current_api_profile else {}
        
        blocked_fields = ['name', 'first_name', 'last_name', 'FirstName', 'LastName', 'age', 'Age', 'gender', 'Gender', 'vitals_numeric', 'goal']
        
        field_mapping = {
            'dietary_preference': 'dietary_preference_codes',
            'restrictions': 'dietary_restriction_codes',
            'allergies': 'food_allergy_codes',
            'medical_conditions': 'diagnosis_codes',
            'digestive_issues': 'digestive_issue_codes',
            'symptom_aggravating_foods': 'symptom_aggravating_food_codes',
            'weight_kg': 'weight',
            'height_cm': 'height',
            'waist_circumference_cm': 'waist',
            'activity_level': 'physical_activity_level',
            'cuisine': 'preferred_cuisines'
        }
        
        for internal_field, api_field in field_mapping.items():
            if internal_field in processed_changes and internal_field not in blocked_fields:
                value = processed_changes[internal_field]
                if api_field == 'preferred_cuisines' and isinstance(value, str):
                    value = [value]
                if internal_field == 'allergies':
                    final_payload['food_allergy_codes'] = value
                else:
                    final_payload[api_field] = value
        
        api_ready_payload = final_payload
        
        logging.info("\n--- CONSTRUCTED FINAL API PAYLOAD FOR PROFILE UPDATE ---\n%s\n--------------------------------------------------\n", json.dumps(api_ready_payload, indent=2))
        
        api_result = await self.db_tool.execute(
            operation="update_profile",
            profile_data=api_ready_payload,
            token=token
        )
        
        if not api_result.success:
            return ToolResult(success=False, data=None, error=f"I failed to update your profile. The server said: {api_result.error}")
        
        await asyncio.sleep(1.5)
        
        retriever_tool = ProfileRetrieverTool(self.db_tool)
        refreshed_profile_result = await retriever_tool.execute(token=token)
        
        if not refreshed_profile_result.success:
            return ToolResult(
                success=True,
                data={"answer": "Got it! Your profile has been updated."},
                metadata={"updated_constraints": constraints}
            )
        
        refreshed_constraints = refreshed_profile_result.data.get("updated_constraints", {})
        refreshed_constraints.pop('calculated_calorie_target', None)
        
        updated_fields = [k for k in processed_changes.keys() if k not in ['name', 'first_name', 'last_name', 'age', 'gender', 'vitals_numeric', 'goal']]
        if updated_fields:
            answer_text = "Great! I've successfully updated your profile with the changes you requested."
        else:
            answer_text = "Note: Profile updates through chat are restricted. Name (including first name and last name), age, gender, goal, and vitals cannot be updated via chat. However, other profile fields like weight, height, dietary preferences, and medical conditions can be updated."
        
        return ToolResult(
            success=True,
            data={
                "answer": answer_text,
                "updated_constraints": refreshed_constraints
            },
            metadata={"awaiting_meal_plan_confirmation": True if updated_fields else False}
        )

class ProfileSummaryTool(BaseTool):
    def __init__(self, llm_service: LLMService):
        super().__init__(
            ToolType.PROFILE_SUMMARY.value,
            "Generates a structured JSON summary of the user's profile and history for internal AI use."
        )
        self.llm_service = llm_service

    async def execute(self, constraints: Dict, chat_history: List[Dict], last_agent_context: Dict, **kwargs) -> ToolResult:
        recent_chat_summary = "\n".join([f"{msg['role']}: {msg['content']}" for msg in chat_history[-6:]])
        previous_plan = last_agent_context.get("active_meal_plan") or last_agent_context.get("pending_meal_plan_for_save")        
        json_schema = {
            "type": "object",
            "properties": {
                "user_basics": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "age": {"type": "integer"},
                        "gender": {"type": "string"}
                    }
                },
                "health_profile": {
                    "type": "object",
                    "properties": {
                        "primary_goal": {"type": "string", "description": "e.g., Lose Weight, Manage Diabetes"},
                        "medical_conditions": {"type": "array", "items": {"type": "string"}},
                        "allergies": {"type": "array", "items": {"type": "string"}},
                        "dietary_preference": {"type": "string"},
                        "activity_level": {"type": "string"}
                    }
                },
                "recent_interaction": {
                    "type": "object",
                    "properties": {
                        "key_requests": {"type": "string", "description": "Summarize the last 2-3 user requests in one sentence."},
                        "mentioned_foods": {"type": "array", "items": {"type": "string"}, "description": "List any specific foods the user recently mentioned wanting to add or remove."}
                    }
                },
                "preferences_and_patterns": {
                    "type": "object",
                    "description": "Captures long-term user preferences, habits, and feedback themes.",
                    
                    "properties": {
                        "liked_foods": {"type": "array", "items": {"type": "string"}, "description": "Specific foods or ingredients the user has explicitly said they enjoy."},
                        "disliked_foods": {"type": "array", "items": {"type": "string"}, "description": "Specific foods or ingredients the user has explicitly said they want to avoid."},
                        "preferred_cuisines": {"type": "array", "items": {"type": "string"}, "description": "A list of cuisines the user has shown a preference for (e.g., 'Indian', 'Mediterranean')."},
                        "flavor_profile": {"type": "array", "items": {"type": "string"}, "description": "User's preferred flavors (e.g., 'spicy', 'savory', 'mild', 'sweet')."},
                        "texture_preferences": {"type": "array", "items": {"type": "string"}, "description": "User's preferred food textures (e.g., 'crunchy snacks', 'soft foods')."},
                        "eating_schedule": {"type": "string", "description": "Observed eating habits (e.g., 'tends to skip breakfast', 'eats a late dinner', 'prefers 3 large meals')."},
                        "cooking_constraints": {"type": "string", "description": "User's cooking limitations (e.g., 'needs quick 15-minute meals', 'prefers simple recipes', 'no oven')."},
                        "symptom_food_triggers": {"type": "array", "items": {"type": "string"}, "description": "Foods the user has linked to negative symptoms (e.g., 'spicy food causes heartburn')."},
                        "positive_feedback_themes": {"type": "array", "items": {"type": "string"}, "description": "Types of meals or suggestions the user has responded positively to (e.g., 'enjoys oat-based breakfasts', 'likes smoothie suggestions')."},
                        "negative_feedback_themes": {"type": "array", "items": {"type": "string"}, "description": "Recurring complaints or dislikes (e.g., 'finds plans too repetitive', 'dislikes leafy greens in lunch')."}
                        }
                        },
                    "advanced_user_insights": {
            "type": "object",
            "description": "Deeper insights into user's lifestyle, mindset, and specific health data.",
            "properties": {
                "key_vitals": {
                    "type": "object",
                    "description": "Stores recent, specific vital sign numbers for data-driven advice.",
                    "properties": {
                        "blood_pressure": {"type": "string", "description": "e.g., '120/80'"},
                        "blood_glucose": {"type": "number", "description": "e.g., 95"},
                        "hba1c": {"type": "number", "description": "e.g., 5.7"}
                    }
                },
                "medications_and_supplements": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of medications or supplements the user is taking that might affect nutrition."
                },
                "motivational_drivers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "The user's core reasons for their goal (e.g., 'upcoming wedding', 'doctor's advice', 'wants more energy')."
                },
                "common_barriers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Obstacles the user faces (e.g., 'stress eating', 'social events', 'cravings for sweets')."
                },
                "eating_environment": {
                    "type": "string",
                    "description": "Typical location and social context of meals (e.g., 'eats at desk alone', 'family dinners', 'dines out frequently')."
                },
                "beverage_habits": {
                    "type": "object",
                    "description": "User's typical fluid intake.",
                    "properties": {
                        "water_intake": {"type": "string", "description": "e.g., 'low', 'adequate', 'high'"},
                        "other_drinks": {"type": "string", "description": "e.g., '2 cups of coffee daily', 'avoids sugary drinks'"}
                    }
                },
                "snacking_behavior": {
                    "type": "string",
                    "description": "Describes the user's snacking patterns (e.g., 'prefers salty snacks in the afternoon', 'craves sweets after dinner')."
                },
                "progress_markers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "How the user measures success beyond weight (e.g., 'feeling more energetic', 'clothes fit better', 'improved sleep')."
                },
                "short_term_focus": {
                    "type": "string",
                    "description": "The user's immediate, actionable goal (e.g., 'stick to the plan for 3 days', 'try one new vegetable this week')."
                },
                "shopping_and_pantry_habits": {
                    "type": "string",
                    "description": "How the user sources their food (e.g., 'shops weekly', 'relies on food delivery', 'limited access to fresh produce')."
                }
            }
        }
    },
    "required": ["user_basics", "health_profile", "recent_interaction"]
}
                    

        system_prompt = f"""
        You are an expert AI assistant that creates concise, actionable JSON summaries of user profiles for other AI agents.
        Your task is to populate a JSON object based on the provided user profile, chat history, and historical context.
        Focus on extracting key, actionable details that would help in creating a highly personalized meal plan.

        **User Profile Data (Constraints):**
        {json.dumps(constraints, indent=2, default=serialize_data)}

        **Recent Chat History:**
        {recent_chat_summary}

        **Previous Meal Plan Context (if available):**
        {previous_plan or "No previous meal plan in this session."}

        **Instructions:**
        Analyze all the provided information and generate a single, valid JSON object that strictly adheres to the defined schema.
        Do not add any text or explanation outside of the JSON object itself.
        """
        
        try:
            summary_json = await self.llm_service.query_json(
                prompt="Generate the internal-use JSON summary based on the provided data.",
                system_prompt=system_prompt,
                json_schema=json_schema,
                chat_history=[] 
            )
            
            if not summary_json:
                return ToolResult(success=False, data={"summary_json": {}}, error="Failed to generate a valid profile summary JSON.")
            
            final_summary_with_context = summary_json.copy()
            final_summary_with_context.update({
                "user_id": constraints.get("user_id"),
                "name": constraints.get("name"),
                "age": constraints.get("age"),
            })

            return ToolResult(success=True, data={"summary_json": final_summary_with_context})
            
        except Exception as e:
            logging.error(f"Error in ProfileSummaryTool: {e}")
            return ToolResult(success=False, data={"summary_json": {}}, error=f"An exception occurred while generating the summary: {e}")

class ConstraintExtractorTool(BaseTool):
    def __init__(self, data_service: DataService):
        super().__init__(ToolType.CONSTRAINT_EXTRACTOR.value, "Displays the user's current profile details. Does not update information.")
        self.data_service = data_service
    async def execute(self, constraints: Dict, **kwargs) -> ToolResult:
        is_profile_empty = not any(k != 'user_id' and v is not None for k, v in constraints.items())

        if is_profile_empty:
            answer = "I don't have any of your profile details recorded yet. To get personalized advice, you can update your profile in the app."
            return ToolResult(True, data={"answer": answer})
        return ToolResult(True, data={"profile_data": constraints})
class VitalAdvisorTool(BaseTool):
    def __init__(self, data_service: DataService, llm_service: LLMService, vital_units: Dict):
        super().__init__(ToolType.VITAL_ADVISOR.value, "Provides nutritional advice based on vital signs.")
        self.data_service = data_service
        self.llm_service = llm_service
        self.vital_units = vital_units

    async def execute(self, query: str, chat_history: List[Dict], constraints: Dict, **kwargs) -> ToolResult:
        age = constraints.get('age')
        gender = constraints.get('gender')

        extract_schema = {
            "type": "object",
            "properties": {
                "vital_name": {"type": "string", "nullable": True},
                "vital_value": {"type": ["number", "string"], "nullable": True}
            }
        }
        extract_system_prompt = """Extract a single vital sign name and its numerical value from the user's query.
        If a vital is explicitly mentioned with a value, provide it.
        For "Blood Pressure", provide the value as a string "Systolic/Diastolic" (e.g., "120/80").
        For "Body Fat %", ensure the '%' is not part of the number.
        If no vital and value are explicitly mentioned, return null for both.
        Example outputs:
        - {"vital_name": "Heart Rate", "vital_value": 150}
        - {"vital_name": "Blood Pressure", "vital_value": "130/85"}
        - {"vital_name": "Body Temperature", "vital_value": 98.6}
        - {"vital_name": null, "vital_value": null}
        """

        extracted_vital_data = await self.llm_service.query_json(
            prompt=f"Extract vital sign and value from: '{query}'",
            system_prompt=extract_system_prompt,
            json_schema=extract_schema,
            chat_history=chat_history
        )

        specific_vital_name = extracted_vital_data.get('vital_name')
        specific_vital_value = extracted_vital_data.get('vital_value')

        if specific_vital_name and specific_vital_value is not None:
            vitals_to_classify = {specific_vital_name: specific_vital_value}
        else:
            vitals_to_classify = constraints.get('vitals_numeric', {})

        if not vitals_to_classify:
            return ToolResult(
                success=False,
                error="I don't have your vital signs data to provide advice. Please update your profile with your vital signs or ask about a specific reading."
            )

        classification_results = classify_vital_signs(
            vital_signs_input=vitals_to_classify,
            vital_signs_data=self.data_service.vitals_data,
            age=age,
            gender=gender
        )

        response_parts = []
        llm_response_prompt_parts = []

        for vital_name, result in classification_results.items():
            status = result.get('nutritional_status_type', 'Unknown')
            calories_advice = result.get('totalCalories', 'No changes')
            nutritional_considerations = result.get('nutritionalConsiderations', 'Consult healthcare provider')
            food_recs = result.get('foodRecommendations', [])

            vital_value_display = vitals_to_classify.get(vital_name)
            unit = self.vital_units.get(vital_name, "")
            if vital_name == "Blood Pressure" and isinstance(vital_value_display, dict):
                value_with_unit = f"{vital_value_display.get('systolic')}/{vital_value_display.get('diastolic')} {unit}"
            else:
                value_with_unit = f"{vital_value_display} {unit}".strip()

            llm_response_prompt_parts.append(f"Vital: {vital_name}")
            llm_response_prompt_parts.append(f"Current Value: {value_with_unit}")
            llm_response_prompt_parts.append(f"Status: {status}")
            llm_response_prompt_parts.append(f"Nutritional Considerations: {nutritional_considerations}")
            if calories_advice and calories_advice != "No changes in Total Daily Energy Expenditure (TDEE)":
                llm_response_prompt_parts.append(f"Calorie Advice: {calories_advice}")
            if food_recs:
                llm_response_prompt_parts.append("Food Recommendations:")
                for rec in food_recs:
                    llm_response_prompt_parts.append(f"- {rec.get('name')} ({rec.get('quantity')}): {rec.get('how_it_helps')}")
            llm_response_prompt_parts.append("\n---")

        llm_system_prompt = f"""You are Friska, a caring and knowledgeable AI nutritionist. Your goal is to provide immediate, clear, and actionable dietary advice based on the user's vital signs and your existing knowledge. Your response MUST be in English.

**Tone and Persona (CRITICAL):**
- Acknowledge the user's reading in a supportive, non-alarming way.
- **DO NOT USE** phrases like "I'm sorry about that reading" or "That's a high number."
- **INSTEAD, USE** encouraging and proactive language that focuses on positive action.
- **Example:** If blood pressure is elevated, a good opening is: "Thank you for sharing that. I see your blood pressure reading is elevated. This is valuable information, and we can explore some nutritional steps that are known to support healthy blood pressure levels."

Based on the provided vital sign classifications and food recommendations:
1.  **Start with a direct, conversational assessment** of the vital sign(s).
2.  **Explain the status** (e.g., "Your Heart Rate is elevated...").
3.  **Provide the nutritional considerations.**
4.  **Immediately list the recommended food items** for that condition. Explain briefly *why* each food is helpful (from the provided 'how_it_helps' information).
5.  If a vital is normal, state that clearly and positively.
6.  Always end with a gentle reminder to consult a healthcare professional for medical advice.
7.  **If multiple vitals are provided, address each one in turn** following the same structure.
User Profile: {json.dumps(constraints, indent=2, default=serialize_data)}
"""
        llm_user_prompt = f"Please provide immediate nutritional advice for the following vital sign(s):\n" + "\n".join(llm_response_prompt_parts)

        try:
            llm_final_response = await self.llm_service.query(
                prompt=llm_user_prompt,
                system_prompt=llm_system_prompt,
                chat_history=chat_history,
                max_tokens=3500
            )
            return ToolResult(
                success=True,
                data={"answer": llm_final_response},
                metadata={"awaiting_meal_plan_confirmation_for_vitals": True}
            )
        except Exception as e:
            print(f"Error during LLM vital advice generation: {e}")
            return ToolResult(
                success=False,
                error="I encountered an issue while generating advice for your vital signs. Please try again."
            )        
class CalorieCalculatorTool(BaseTool):
    def __init__(self, data_service: DataService):
        super().__init__(ToolType.CALORIE_CALCULATOR.value, "Calculates calorie targets, BMI, TDEE, and meal totals")
        self.data_service = data_service

    def _extract_calorie_target(self, query: str) -> ToolResult:
        patterns = [r'(\d{3,5})\s*(?:kcal|calories|calorie|cal)\b', r'target\s*(?:of|is)?\s*(\d{3,5})\b', r'\b(?:around|about|approx|approximately)\s*(\d{3,5})\b', r'(\d{3,5})\s*daily\b', r'budget\s*(?:of|is)?\s*(\d{3,5})\b']
        for pattern in patterns:
            match = re.search(pattern, query.lower())
            if match:
                try: calorie_val_str = next(g for g in match.groups() if g and g.isdigit())
                except StopIteration: continue
                if calorie_val_str: return ToolResult(success=True, data=int(calorie_val_str))
        return ToolResult(success=True, data=None, error="No calorie target found in query.")

    def _calculate_bmi(self, weight_kg: float, height_cm: float) -> Optional[float]:
        if not weight_kg or not height_cm or weight_kg <= 0 or height_cm <= 0: return None
        return weight_kg / ((height_cm / 100) ** 2)

    def _calculate_tdee(self, gender: str, weight_kg: float, height_cm: float, age: float, activity_level: str) -> Optional[float]:
        if not all([gender, weight_kg, height_cm, age]) or any(v <= 0 for v in [weight_kg, height_cm, age]): return None
        bmr: float
        if gender.lower() == "male":
            bmr = (10 * weight_kg) + (6.25 * height_cm) - (5 * age) + 5
        elif gender.lower() == "female":
            bmr = (10 * weight_kg) + (6.25 * height_cm) - (5 * age) - 161
        else:
            bmr_male=(10*weight_kg)+(6.25*height_cm)-(5*age)+5
            bmr_female=(10*weight_kg)+(6.25*height_cm)-(5*age)-161
            bmr=(bmr_male+bmr_female)/2
        activity_factors = {"sedentary":1.2, "somewhat active":1.375, "moderately active":1.55, "very active":1.725}
        return bmr * activity_factors.get(activity_level.lower(), 1.2)

    async def execute(self, operation: str, **kwargs) -> ToolResult:
        if operation == "extract_target": return self._extract_calorie_target(kwargs.get('query', ''))
        elif operation == "calculate_profile_metrics":
            profile_data = kwargs.get('constraints', {}); weight, height, age, gender, activity = (profile_data.get(k) for k in ['weight_kg', 'height_cm', 'age', 'gender', 'activity_level'])
            metrics = {}
            if weight and height: metrics['bmi'] = self._calculate_bmi(weight, height)
            if gender and weight and height and age and activity: metrics['tdee'] = self._calculate_tdee(gender, weight, height, age, activity)
            if not metrics: return ToolResult(False, {}, error="Insufficient data for BMI/TDEE. Need age, gender, weight, height, activity.")
            return ToolResult(True, metrics)
        return ToolResult(False, error="Unknown calorie calculation operation")
    
class FitnessProfileUpdaterTool(BaseTool):
    def __init__(self, llm_service: LLMService):
        super().__init__(
            ToolType.FITNESS_PROFILE_UPDATER.value,
            "Extracts fitness parameter updates and modification intents."
        )
        self.llm_service = llm_service

    def _validate_and_normalize(self, updates: dict) -> dict:

        normalized = {}
        
        if 'name' in updates:
            normalized['name'] = str(updates['name']).strip()
            
        if 'age' in updates:
            try: normalized['age'] = int(updates['age'])
            except: pass
            
        if 'weight_kg' in updates:
            try: normalized['weight_kg'] = float(updates['weight_kg'])
            except: pass

        if 'height_cm' in updates:
            try: normalized['height_cm'] = float(updates['height_cm'])
            except: pass

        list_fields = ['medical_conditions', 'days_per_week', 'available_equipment', 'injuries', 'goals']
        for field in list_fields:
            if field in updates:
                val = updates[field]
                if isinstance(val, str):
                    normalized[field] = [x.strip() for x in val.split(',')]
                elif isinstance(val, list):
                    normalized[field] = [str(x).strip() for x in val]

        text_fields = ['fitness_level', 'gender', 'primary_goal', 'activity_level']
        for field in text_fields:
            if field in updates and updates[field]:
                normalized[field] = str(updates[field]).strip()

        return normalized

    async def execute(self, query: str, current_profile: Dict, **kwargs) -> ToolResult:
        context_preview = {k: v for k, v in current_profile.items() if k in ["primary_goal", "days_per_week", "fitness_level"]}
        
        json_schema = {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string", 
                    "enum": ["generate_plan", "modify_plan", "no_action"],
                    "description": "'generate_plan' for NEW plans or GLOBAL changes (e.g., 'too hard'). 'modify_plan' for specific exercise swaps."
                },
                "updates": {
                    "type": "object",
                    "properties": {
                        "primary_goal": {"type": "string"},
                        "days_per_week": {"type": "array", "items": {"type": "string"}},
                        "fitness_level": {"type": "string", "enum": ["Beginner (0–6 months)", "Intermediate (6–18 months)", "Advanced (>18 months)"]},
                        "physical_limitation": {"type": "string"},
                        "medical_conditions": {"type": "array", "items": {"type": "string"}}
                    }
                },
                "modification": {
                    "type": "object",
                    "properties": {
                        "day": {"type": "string"},
                        "target_index": {"type": "integer"},
                        "target_name": {"type": "string", "description": "Extract the specific exercise name if no number is given (e.g. 'ankle circles')"},
                        "target_section": {
                            "type": "string", 
                            "enum": ["warmup", "main_workout", "cooldown"]
                        },
                        "modification_type": {"type": "string", "enum": ["easier", "harder", "replace"]}
                    }
                }
            },
            "required": ["intent"]
        }

        system_prompt = f"""You are a Fitness Configuration Assistant.
Translate User Query into a JSON object matching the provided schema.

**CURRENT CONTEXT:**
{json.dumps(context_preview)}

**INTENT LOGIC:**
1. **modify_plan**: User targets a SPECIFIC exercise (e.g., "Change squats", "Alter the 3rd one").
   - If user gives a NUMBER, use `target_index`.
   - If user gives a NAME (e.g., "ankle circles"), use `target_name`.
   - Extract `target_section` if mentioned (warmup, cooldown).

2. **generate_plan**: User wants a NEW plan or GLOBAL change.
   - "This plan is too hard" -> intent: `generate_plan`, updates: {{ "fitness_level": "Beginner (0–6 months)" }} (Downgrade level)
   - "This plan is too easy" -> intent: `generate_plan`, updates: {{ "fitness_level": "Advanced (>18 months)" }} (Upgrade level)
   - "I have knee pain" -> intent: `generate_plan`, updates: {{ "physical_limitation": "Knee Pain" }}

**MAPPINGS:**
- "warm up", "starting" -> "warmup"
- "cool down", "ending" -> "cooldown"
- "main", "workout" -> "main_workout"

**EXAMPLES:**
Query: "Alter ankle circle in friday, warmup"
Output: {{ "intent": "modify_plan", "modification": {{ "day": "Friday", "target_name": "ankle circle", "target_section": "warmup", "modification_type": "replace" }} }}

Query: "This plan is very hard for me"
Output: {{ "intent": "generate_plan", "updates": {{ "fitness_level": "Beginner (0–6 months)" }} }}
"""
        try:
            parsed_data = await self.llm_service.query_json(
                prompt=query,
                system_prompt=system_prompt,
                json_schema=json_schema,
                chat_history=[], 
                max_tokens=400
            )
            
            if parsed_data.get("updates"):
                parsed_data["updates"] = self._validate_and_normalize(parsed_data["updates"])
            
            return ToolResult(success=True, data=parsed_data)

        except Exception as e:
            logger.error(f"Error in FitnessProfileUpdaterTool: {e}")
            return ToolResult(success=True, data={"intent": "generate_plan", "updates": {}})


class WellnessAdvisorTool(BaseTool):

    
    def __init__(self, llm_service: LLMService):
        super().__init__(
            ToolType.WELLNESS_ADVISOR.value,
            "Provides holistic wellness guidance for managing chronic conditions through lifestyle interventions"
        )
        self.llm_service = llm_service
        
        self.wellness_guidelines = {
            'pcos': {
                'dietary': [
                    'Focus on low-glycemic index foods (whole grains, legumes, non-starchy vegetables)',
                    'Include anti-inflammatory foods (fatty fish, turmeric, berries)',
                    'Limit refined carbs and sugar to manage insulin resistance',
                    'Eat regular meals to stabilize blood sugar'
                ],
                'lifestyle': [
                    'Aim for 150 minutes of moderate exercise per week',
                    'Manage stress through yoga, meditation, or deep breathing',
                    'Prioritize 7-8 hours of quality sleep',
                    'Consider strength training 2-3x/week to improve insulin sensitivity'
                ]
            },
            'acid_reflux': {
                'dietary': [
                    'Eat smaller, frequent meals instead of large meals',
                    'Avoid trigger foods (spicy, acidic, fatty, chocolate, caffeine)',
                    'Don\'t eat 2-3 hours before bedtime',
                    'Stay upright after meals for at least 30 minutes'
                ],
                'lifestyle': [
                    'Elevate head of bed by 6-8 inches',
                    'Maintain a healthy weight',
                    'Avoid tight-fitting clothing',
                    'Quit smoking if applicable'
                ]
            },
            'sleep': {
                'optimization': [
                    'Maintain consistent sleep-wake schedule (same time daily)',
                    'Create dark, cool, quiet sleep environment (65-68°F ideal)',
                    'Limit screen time 1-2 hours before bed (blue light disrupts melatonin)',
                    'Avoid caffeine after 2pm and heavy meals before sleep',
                    'Consider magnesium-rich foods (almonds, spinach, pumpkin seeds)'
                ]
            },
            'stress': {
                'management': [
                    'Practice daily mindfulness or meditation (even 5-10 minutes helps)',
                    'Engage in regular physical activity (releases endorphins)',
                    'Maintain social connections and talk about feelings',
                    'Consider adaptogenic herbs (ashwagandha, rhodiola) with healthcare provider approval',
                    'Practice time management and set boundaries'
                ]
            },
            'energy': {
                'optimization': [
                    'Eat balanced meals with protein, complex carbs, and healthy fats',
                    'Stay hydrated (aim for 2-3 liters water daily)',
                    'Take short movement breaks if sedentary (every 30-60 minutes)',
                    'Check for nutrient deficiencies (iron, B12, vitamin D)',
                    'Limit sugar crashes by avoiding refined carbs'
                ]
            }
        }
    
    async def execute(self, query: str = "", constraints: Dict = None, 
                     chat_history: List[Dict] = None, **kwargs) -> ToolResult:
        
        conditions = constraints.get('medical_conditions', []) if constraints else []
        allergies = constraints.get('allergies', []) if constraints else []
        
        wellness_persona = ExpertPersonaLibrary.get_wellness_expert()
        system_prompt = wellness_persona.get_full_system_prompt(constraints)
        
        quick_guidelines = self._get_quick_guidelines(query, conditions)
        
        wellness_prompt = f"""User Query: {query}

**User Health Context:**
Medical Conditions: {', '.join(conditions) if conditions else 'None'}
Allergies: {', '.join(allergies) if allergies else 'None'}

**Your Task:**
Provide holistic, actionable wellness guidance addressing the user's query. Focus on:
1. **Root Cause Context**: Explain why this matters for their health
2. **Actionable Steps**: 3-5 practical lifestyle interventions
3. **Timeline**: What to expect and when
4. **Safety**: Any precautions or when to seek medical help

**Response Structure:**
🎯 **Understanding the Issue**
[Brief explanation of root cause/mechanism]

💡 **Recommended Actions**
1. [Action 1 with specific details]
2. [Action 2 with specific details]
3. [Action 3 with specific details]

⏰ **What to Expect**
[Timeline and expected outcomes]

⚠️ **Important Notes**
[Precautions, when to see doctor, etc.]
"""
        
        if quick_guidelines:
            wellness_prompt += f"\n\n**Evidence-Based Guidelines (for reference):**\n{quick_guidelines}"
        
        persona_context = kwargs.get('persona_context')
        if persona_context:
            system_prompt += f"\n\n **ADAPTIVE PERSONA GUIDANCE:**\n{persona_context}"
            logger.info(" Applied smart persona injection to WellnessAdvisorTool system prompt")
        
        try:
            response = await self.llm_service.query(
                prompt=wellness_prompt,
                system_prompt=system_prompt,
                chat_history=chat_history or [],
                max_tokens=800,
                temperature=0.3
            )
            
            return ToolResult(
                success=True,
                data=response,
                metadata={
                    "domain": "wellness",
                    "conditions_considered": conditions
                }
            )
            
        except Exception as e:
            logger.error(f"Error in WellnessAdvisorTool: {e}")
            return ToolResult(
                success=False,
                data="I'm having trouble generating wellness guidance right now. Please try rephrasing your question or consult with a healthcare provider.",
                error=str(e)
            )
    
    def _get_quick_guidelines(self, query: str, conditions: List[str]) -> str:
        query_lower = query.lower()
        guidelines = []
        
        if any('pcos' in cond.lower() for cond in conditions) or 'pcos' in query_lower:
            if 'pcos' in self.wellness_guidelines:
                guidelines.append("**PCOS Management:**")
                guidelines.extend([f"- {tip}" for tip in self.wellness_guidelines['pcos']['dietary']])
                guidelines.extend([f"- {tip}" for tip in self.wellness_guidelines['pcos']['lifestyle']])
        
        if any(term in query_lower for term in ['acid reflux', 'gerd', 'heartburn', 'reflux']):
            if 'acid_reflux' in self.wellness_guidelines:
                guidelines.append("\n**Acid Reflux Management:**")
                guidelines.extend([f"- {tip}" for tip in self.wellness_guidelines['acid_reflux']['dietary']])
                guidelines.extend([f"- {tip}" for tip in self.wellness_guidelines['acid_reflux']['lifestyle']])
        
        if any(term in query_lower for term in ['sleep', 'insomnia', 'tired', 'fatigue', 'energy']):
            if 'sleep' in self.wellness_guidelines:
                guidelines.append("\n**Sleep Optimization:**")
                guidelines.extend([f"- {tip}" for tip in self.wellness_guidelines['sleep']['optimization']])
        
        if any(term in query_lower for term in ['stress', 'anxious', 'overwhelmed', 'anxiety']):
            if 'stress' in self.wellness_guidelines:
                guidelines.append("\n**Stress Management:**")
                guidelines.extend([f"- {tip}" for tip in self.wellness_guidelines['stress']['management']])
        
        if any(term in query_lower for term in ['energy', 'fatigue', 'tired', 'exhausted', 'weak']):
            if 'energy' in self.wellness_guidelines:
                guidelines.append("\n**Energy Optimization:**")
                guidelines.extend([f"- {tip}" for tip in self.wellness_guidelines['energy']['optimization']])
        
        return "\n".join(guidelines) if guidelines else ""
