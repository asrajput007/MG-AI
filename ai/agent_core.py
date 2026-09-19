import asyncio
import copy
from datetime import datetime, timezone, timedelta
import time
import json
import re 
import logging
import uuid
import random
from .tool_core import BaseTool
from scipy.optimize import minimize
from .nutrition import generate_batched_profile_notes
from .nutrition_models import NUTRITION_DB, get_portion_bounds
import functools
import numpy as np
import os
from dotenv import load_dotenv
import traceback
from typing import Dict, List, Optional
import hashlib
import redis
from fastapi import BackgroundTasks
from .base_and_utility import DataService, DatabasePersistenceTool, OpenAI_LLMService, QueryClassifierTool
from .health_orchestrator import HealthOrchestrator, DomainType, ExpertPersonaLibrary, FoodAnalyzerTool
from .nutrition import CravingAssistantTool, FoodMatcherTool, GroceryListTool, HistoryRetrieverTool, MealCheckInTool, MealIngredientsTool, MealPlanAdjusterTool, MealPlanGeneratorTool, MealRetrieverTool, NutritionAnalyzerTool, SpecialMealPlanGeneratorTool, WeeklyMealPlanGeneratorTool
from .profile_management import AudioSummaryTool, CalorieCalculatorTool, ConstraintExtractorTool, GeneralQueryTool, GoalUpdaterTool, GreetingTool, ProfileRetrieverTool, ProfileSummaryTool, ProfileUpdaterTool, SummarizationTool, FitnessProfileUpdaterTool, WellnessAdvisorTool
from .tool_core import LLMService, TokenBucket, ToolResult, ToolType
from .vitals_and_disease import BloodReportAnalyzerTool, BloodReportQueryTool, DiseaseAdvisorTool, PanelQueryTool, VitalAdvisorTool, process_blood_report_translations
from .waterstep import WaterStepTool
from .sessionbooking import SessionBookingTool
from .fitness import FitnessPlanGeneratorTool, WorkoutAdjusterTool
from .profile_management import FitnessProfileUpdaterTool, WellnessAdvisorTool
from .deterministic_meal_generator import DeterministicMealGenerator, PoolExhaustedError
from helpers.utils import serialize_data, generate_profile_hash

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

FRISKA_API_URL = os.getenv("FRISKA_API_URL")
fitness_api_key = os.getenv("fitness_api_key")
fitness_endpoint_url = os.getenv("fitness_endpoint_url")


def flatten_and_clean_avoid_foods(food_strings: List[str]) -> List[str]:
    STOP_WORDS = {
        'and', 'or', 'with', 'without', 'the', 'a', 'an', 'in', 'on', 'at', 'to', 'for', 'of', 'from',
        'eg', 'e.g.', 'e.g', 'etc', 'etc.', 'such', 'as', 'like', 'including',
        'high', 'low', 'added', 'excessive', 'sugary', 'sweetened', 'processed', 'refined',
        'fatty', 'salted', 'fried', 'canned', 'frozen', 'packaged', 'instant',
        'highly', 'very', 'too', 'much', 'all', 'any', 'some', 'many',
        'grilled', 'baked', 'roasted', 'boiled', 'steamed', 'raw', 'fresh', 'cooked',
        'fried', 'sauteed', 'stir', 'pan', 'deep',
        'foods', 'food', 'products', 'items', 'meals', 'snacks', 'beverages', 'drinks',
        'serving', 'portions', 'grams', 'cups', 'pieces'
    }
    
    flattened = []
    
    for food_string in food_strings:
        if not food_string:
            continue
        cleaned = re.sub(r'[(),.\-/]', ' ', food_string)
        words = re.split(r'[,\s]+', cleaned)
        for word in words:
            word = word.strip().lower()
            if len(word) >= 3 and word not in STOP_WORDS:
                flattened.append(word)
    
    seen = set()
    result = []
    for item in flattened:
        if item not in seen:
            seen.add(item)
            result.append(item)
    
    return result


def snap_to_culinary_fraction(amount: float) -> float:
    snapped_value = round(amount * 4) / 4
    if snapped_value == 0:
        snapped_value = 0.25
    return snapped_value


CLINICAL_AVOID_MAPPING = {
    "no red meat": ["beef", "pork", "lamb", "mutton", "veal", "venison", "goat", "steak", "bacon", "ham", "sausage", "pepperoni", "prosciutto"],    
    "acid reflux": ["fried", "crispy", "citrus", "tomato", "tamarind", "spicy", "peppermint", "chocolate", "garlic", "onion", "coffee", "alcohol", "mint", "fatty", "bonda", "vada", "bajji", "puri", "coconut milk", "sukka", "kurma", "coconut base", "moilee", "coconut oil", "thepla", "salna", "roast"],
    "heartburn": ["fried", "crispy", "citrus", "tomato", "tamarind", "spicy", "peppermint", "chocolate", "garlic", "onion", "coffee", "alcohol", "mint", "fatty", "bonda", "vada", "bajji", "puri", "moilee", "coconut oil", "thepla", "salna", "roast"],
    "gerd": ["fried", "crispy", "citrus", "tomato", "tamarind", "spicy", "peppermint", "chocolate", "garlic", "onion", "coffee", "alcohol", "mint", "fatty", "bonda", "vada", "bajji", "puri", "coconut milk", "sukka", "kurma", "coconut base", "moilee", "coconut oil", "thepla", "salna", "roast"],
    "ibs": ["wheat", "dairy", "onion", "garlic", "beans", "legumes", "cabbage", "cauliflower", "broccoli", "brussels sprouts", "artificial sweetener", "sorbitol", "fructose"],
    "irritable bowel syndrome": ["wheat", "dairy", "onion", "garlic", "beans", "legumes", "cabbage", "cauliflower", "broccoli", "brussels sprouts", "artificial sweetener", "sorbitol", "fructose"],
    "crohn's disease": ["nuts", "seeds", "raw vegetables", "popcorn", "whole grains", "high fiber", "spicy", "alcohol", "caffeine"],
    "ulcerative colitis": ["nuts", "seeds", "raw vegetables", "popcorn", "whole grains", "high fiber", "spicy", "alcohol", "caffeine", "dairy"],
    "celiac disease": ["wheat", "barley", "rye", "malt", "brewer", "seitan", "couscous", "semolina", "durum", "farro", "spelt"],
    "gluten intolerance": ["wheat", "barley", "rye", "malt", "brewer", "seitan", "couscous", "semolina", "durum", "farro", "spelt"],
    "lactose intolerance": ["milk", "cheese", "yogurt", "cream", "butter", "ice cream", "whey", "casein", "dairy"],
    "gastroparesis": ["high fiber", "fatty", "fried", "raw vegetables", "beans", "legumes", "carbonated", "alcohol"],
    "diverticulitis": ["nuts", "seeds", "popcorn", "corn", "high fiber", "raw vegetables"],
    "diverticulosis": ["nuts", "seeds", "popcorn", "corn"],
    "peptic ulcer": ["spicy", "caffeine", "alcohol", "acidic", "citrus", "tomato", "chocolate"],
    "gastritis": ["spicy", "caffeine", "alcohol", "acidic", "citrus", "tomato", "fried"],
    "hiatal hernia": ["fried", "fatty", "spicy", "citrus", "tomato", "chocolate", "caffeine", "alcohol", "mint"],
    "bile reflux": ["fatty", "fried", "spicy", "alcohol", "caffeine"],
    "small intestinal bacterial overgrowth": ["sugar", "refined carbs", "lactose", "fructose"],
    "sibo": ["sugar", "refined carbs", "lactose", "fructose"],
    "leaky gut": ["gluten", "dairy", "sugar", "processed", "alcohol"],
    "colitis": ["high fiber", "raw vegetables", "spicy", "alcohol", "caffeine"],
    "pancreatitis": ["alcohol", "fatty", "fried", "high fat"],
    "gallstones": ["fatty", "fried", "greasy", "high fat", "egg yolk"],
    "cholecystitis": ["fatty", "fried", "greasy", "high fat"],    
    "gout": ["liver", "organ meat", "sardines", "anchovies", "scallops", "gravy", "red meat", "alcohol", "beer", "high fructose corn syrup", "sweetbreads", "herring", "mackerel", "venison"],
    "hyperuricemia": ["liver", "organ meat", "sardines", "anchovies", "scallops", "red meat", "alcohol", "beer"],
    "kidney stones": ["sweet potato", "spinach", "rhubarb", "beet", "beetroot", "almond", "swiss chard", "chard", "cashew", "chocolate", "bran", "wheat bran", "soy", "peanut", "okra", "collard", "chia", "urad dal", "black gram", "sesame", "amaranth", "sunflower", "fenugreek", "millet", "soya chunks", "taro", "colocasia", "arbi", "pumpkin seeds", "edamame", "moringa"],
    "kidney disease": ["banana", "orange", "tomato", "potato", "avocado", "nuts", "seeds", "whole grains", "dairy", "processed meat", "canned", "pickled", "high sodium", "high potassium"],
    "chronic kidney disease": ["banana", "orange", "tomato", "potato", "avocado", "nuts", "seeds", "whole grains", "dairy", "processed meat", "canned", "pickled", "high sodium", "high potassium"],
    "renal failure": ["banana", "orange", "tomato", "potato", "avocado", "nuts", "dairy", "high sodium", "high potassium", "high phosphorus"],
    "diabetes": ["white bread", "white rice", "sugary", "candy", "soda", "juice", "pastry", "cake", "cookie", "high fructose", "refined carbs"],
    "type 1 diabetes": ["white bread", "white rice", "sugary", "candy", "soda", "juice", "pastry", "cake", "cookie", "high fructose", "refined carbs"],
    "type 2 diabetes": ["white bread", "white rice", "sugary", "candy", "soda", "juice", "pastry", "cake", "cookie", "high fructose", "refined carbs"],
    "prediabetes": ["white bread", "white rice", "sugary", "candy", "soda", "juice", "pastry", "cake", "cookie", "high fructose", "refined carbs"],
    "insulin resistance": ["white bread", "white rice", "sugary", "candy", "soda", "juice", "pastry", "refined carbs"],
    "metabolic syndrome": ["white bread", "white rice", "sugary", "trans fat", "processed", "high sodium"],
    "pcos": ["white bread", "white rice", "sugary", "candy", "soda", "juice", "pastry", "cake", "cookie", "high fructose", "refined carbs", "processed"],
    "polycystic ovary syndrome": ["white bread", "white rice", "sugary", "candy", "soda", "juice", "pastry", "cake", "cookie", "high fructose", "refined carbs", "processed"],
    "thyroid disorder": ["soy", "cruciferous", "gluten", "processed", "sugary"],
    "hypothyroidism": ["soy", "cruciferous", "gluten", "millet", "cassava", "processed", "sugary"],
    "hyperthyroidism": ["iodine", "seaweed", "kelp", "iodized salt", "dairy", "egg yolk", "caffeine"],
    "graves disease": ["iodine", "seaweed", "kelp", "caffeine", "alcohol"],
    "hashimoto's thyroiditis": ["gluten", "soy", "dairy", "processed", "sugary", "cruciferous"],
    "adrenal fatigue": ["caffeine", "sugar", "processed", "alcohol"],
    "cushing's syndrome": ["high sodium", "sugar", "processed"],
    "obesity": ["sugary", "fried", "processed", "high calorie", "fast food"],    
    "high blood pressure": ["salt", "sodium", "canned", "processed", "deli meat", "bacon", "sausage", "pickled", "salted", "soy sauce", "miso"],
    "hypertension": ["salt", "sodium", "canned", "processed", "deli meat", "bacon", "sausage", "pickled", "salted", "soy sauce", "miso"],
    "high cholesterol": ["butter", "cream", "fatty meat", "red meat", "organ meat", "egg yolk", "cheese", "fried", "palm oil", "coconut oil", "trans fat"],
    "hypercholesterolemia": ["butter", "cream", "fatty meat", "red meat", "organ meat", "egg yolk", "cheese", "fried", "palm oil", "coconut oil", "trans fat"],
    "high triglycerides": ["sugar", "refined carbs", "alcohol", "trans fat", "high fructose"],
    "hypertriglyceridemia": ["sugar", "refined carbs", "alcohol", "trans fat", "high fructose"],
    "heart disease": ["salt", "sodium", "saturated fat", "trans fat", "fried", "processed", "red meat", "butter", "cream"],
    "coronary artery disease": ["salt", "sodium", "saturated fat", "trans fat", "fried", "processed", "red meat", "butter", "cream"],
    "atherosclerosis": ["saturated fat", "trans fat", "cholesterol", "processed", "fried"],
    "congestive heart failure": ["salt", "sodium", "canned", "processed", "high fluid"],
    "heart failure": ["salt", "sodium", "canned", "processed"],
    "atrial fibrillation": ["caffeine", "alcohol", "high sodium"],
    "stroke": ["salt", "sodium", "saturated fat", "trans fat", "processed"],
    "peripheral artery disease": ["saturated fat", "trans fat", "processed", "high sodium"],
    "rheumatoid arthritis": ["nightshades", "tomato", "eggplant", "bell pepper", "potato", "gluten", "dairy", "processed", "sugary", "alcohol"],
    "osteoarthritis": ["nightshades", "processed", "sugary", "high omega-6"],
    "lupus": ["alfalfa", "garlic", "nightshades", "alcohol", "processed", "high sodium"],
    "systemic lupus erythematosus": ["alfalfa", "garlic", "nightshades", "alcohol", "processed"],
    "hashimoto": ["gluten", "soy", "dairy", "processed", "sugary", "cruciferous"],
    "multiple sclerosis": ["saturated fat", "trans fat", "processed", "gluten", "dairy"],
    "scleroderma": ["high sodium", "processed", "acidic"],
    "sjogren's syndrome": ["alcohol", "caffeine", "spicy"],
    "ankylosing spondylitis": ["starch", "processed", "sugary"],
    "fibromyalgia": ["gluten", "dairy", "processed", "sugary", "artificial sweetener"],
    "chronic fatigue syndrome": ["sugar", "processed", "caffeine", "alcohol"],
    "migraine": ["aged cheese", "chocolate", "caffeine", "alcohol", "processed meat", "msg", "artificial sweetener", "tyramine", "nitrate", "aspartame"],
    "chronic migraine": ["aged cheese", "chocolate", "caffeine", "alcohol", "processed meat", "msg", "tyramine"],
    "tension headache": ["caffeine", "alcohol", "processed"],
    "cluster headache": ["alcohol", "nitrate"],
    "epilepsy": ["alcohol", "caffeine", "artificial sweetener", "msg"],
    "seizure disorder": ["alcohol", "caffeine", "artificial sweetener"],
    "parkinson's disease": ["high protein"],
    "alzheimer's disease": ["trans fat", "saturated fat", "processed", "sugary", "high sodium"],
    "dementia": ["trans fat", "processed", "alcohol", "high sodium"],
    "neuropathy": ["alcohol", "sugar", "processed"],
    "vertigo": ["salt", "sodium", "caffeine", "alcohol"],
    "meniere's disease": ["salt", "sodium", "caffeine", "alcohol"],
    "fatty liver": ["alcohol", "fried", "fatty", "sugary", "refined carbs", "high fructose", "processed"],
    "non-alcoholic fatty liver disease": ["alcohol", "fried", "fatty", "sugary", "refined carbs", "high fructose", "processed"],
    "nafld": ["alcohol", "fried", "fatty", "sugary", "refined carbs", "high fructose"],
    "cirrhosis": ["alcohol", "salt", "sodium", "raw shellfish", "processed", "high protein"],
    "hepatitis": ["alcohol", "fatty", "fried", "processed"],
    "liver disease": ["alcohol", "fatty", "fried", "processed", "high sodium"],
    "cancer": ["alcohol", "processed meat", "red meat", "sugary", "refined carbs", "fried", "charred", "smoked"],
    "breast cancer": ["alcohol", "soy", "high fat"],
    "colon cancer": ["red meat", "processed meat", "alcohol", "high fat"],
    "prostate cancer": ["high fat", "dairy", "red meat"],
    "asthma": ["sulfites", "preservatives", "processed", "dairy"],
    "copd": ["salt", "sodium", "processed", "fried"],
    "chronic obstructive pulmonary disease": ["salt", "sodium", "processed"],
    "bronchitis": [],
    "pneumonia": [],
    "sleep apnea": ["alcohol", "heavy meals", "fatty"],
    "bloating": ["beans", "legumes", "cruciferous", "cabbage", "broccoli", "cauliflower", "carbonated", "dairy", "artificial sweetener"],
    "gas": ["beans", "legumes", "cruciferous", "cabbage", "broccoli", "cauliflower", "carbonated", "dairy", "artificial sweetener", "onion", "garlic"],
    "constipation": ["processed", "white bread", "white rice", "cheese", "red meat", "fried"],
    "diarrhea": ["dairy", "fatty", "fried", "spicy", "caffeine", "alcohol", "artificial sweetener", "high fiber"],
    "nausea": ["fatty", "fried", "spicy", "very sweet"],
    "indigestion": ["fatty", "fried", "spicy", "acidic", "carbonated"],
    "peanut allergy": ["peanut", "peanuts", "peanut butter", "peanut oil", "mixed nuts"],
    "tree nut allergy": ["almond", "walnut", "cashew", "pistachio", "hazelnut", "pecan", "macadamia", "brazil nut"],
    "shellfish allergy": ["shrimp", "crab", "lobster", "oyster", "clam", "mussel", "scallop", "prawns"],
    "fish allergy": ["fish", "salmon", "tuna", "cod", "tilapia", "mackerel", "sardines", "anchovy"],
    "egg allergy": ["egg", "eggs", "mayonnaise", "meringue"],
    "milk allergy": ["milk", "dairy", "cheese", "yogurt", "butter", "cream", "whey", "casein"],
    "soy allergy": ["soy", "tofu", "tempeh", "edamame", "soy sauce", "miso", "soy milk"],
    "wheat allergy": ["wheat", "bread", "pasta", "flour", "couscous", "seitan"],
    "corn allergy": ["corn", "cornstarch", "corn syrup", "popcorn", "polenta"],
    "sesame allergy": ["sesame", "tahini", "sesame oil", "sesame seeds"],
    "fructose intolerance": ["apple", "pear", "mango", "honey", "agave", "high fructose corn syrup", "fruit juice"],
    "histamine intolerance": ["aged cheese", "fermented", "cured meat", "alcohol", "vinegar", "tomato", "eggplant", "spinach", "avocado", "shellfish"],
    "tyramine sensitivity": ["aged cheese", "cured meat", "fermented", "soy sauce", "miso", "alcohol", "banana", "avocado", "chocolate"],
    "salicylate sensitivity": ["tomato", "berries", "citrus", "spices", "tea"],
    "oxalate sensitivity": ["spinach", "rhubarb", "beet", "swiss chard", "almonds", "cashews"],
    "acne": ["dairy", "high glycemic", "white bread", "white rice", "sugary", "fried", "processed"],
    "eczema": ["dairy", "gluten", "eggs", "nuts", "soy", "citrus", "nightshades"],
    "psoriasis": ["alcohol", "gluten", "dairy", "nightshades", "red meat", "processed", "sugary"],
    "rosacea": ["spicy", "hot drinks", "alcohol", "caffeine", "histamine-rich"],
    "dermatitis": ["dairy", "gluten", "citrus", "nuts"],
    "hives": ["histamine-rich", "fermented", "aged", "processed"],
    "urticaria": ["histamine-rich", "preservatives", "artificial colors"],
    "depression": [],
    "anxiety": ["caffeine", "alcohol", "sugar", "processed"],
    "adhd": ["artificial colors", "preservatives", "sugar"],
    "attention deficit hyperactivity disorder": ["artificial colors", "preservatives", "sugar"],
    "autism": [],
    "bipolar disorder": [],
    "schizophrenia": [],
    "ocd": [],
    "ptsd": [],
    "osteoporosis": ["high sodium", "caffeine", "alcohol", "excessive protein"],
    "osteopenia": ["high sodium", "caffeine", "alcohol"],
    "fracture": [],
    "arthritis": ["nightshades", "processed", "sugary"],
    "gout arthritis": ["liver", "organ meat", "red meat", "alcohol", "beer"],
    "bursitis": [],
    "tendonitis": [],
    "endometriosis": ["red meat", "trans fat", "alcohol", "caffeine"],
    "uterine fibroids": ["red meat", "high fat", "alcohol"],
    "menopause": ["spicy", "caffeine", "alcohol"],
    "pms": ["salt", "sugar", "caffeine", "alcohol"],
    "premenstrual syndrome": ["salt", "sugar", "caffeine", "alcohol"],
    "pregnancy": ["raw meat", "raw fish", "raw eggs", "unpasteurized", "deli meat", "alcohol", "high mercury fish"],
    "gestational diabetes": ["sugar", "refined carbs", "white bread", "white rice", "juice"],
    "preeclampsia": ["high sodium", "processed"],
    "uti": ["caffeine", "alcohol", "spicy", "acidic"],
    "urinary tract infection": ["caffeine", "alcohol", "spicy", "acidic"],
    "interstitial cystitis": ["caffeine", "alcohol", "spicy", "acidic", "citrus", "tomato"],
    "overactive bladder": ["caffeine", "alcohol", "carbonated", "spicy", "acidic"],
    "prostatitis": ["spicy", "caffeine", "alcohol"],
    "benign prostatic hyperplasia": ["caffeine", "alcohol", "spicy"],
    "anemia": [],
    "iron deficiency anemia": [],
    "vitamin b12 deficiency": [],
    "sickle cell disease": [],
    "hemochromatosis": ["red meat", "organ meat", "iron-fortified", "alcohol"],
    "thalassemia": [],
    "hiv": [],
    "aids": [],
    "tuberculosis": [],
    "hepatitis a": ["alcohol"],
    "hepatitis b": ["alcohol"],
    "hepatitis c": ["alcohol", "iron"],
    "mononucleosis": [],
    "covid-19": [],
    "insomnia": ["caffeine", "alcohol", "spicy", "heavy meals"],
    "sleep disorder": ["caffeine", "alcohol", "heavy meals"],
    "chronic pain": [],
    "chronic inflammation": ["sugar", "trans fat", "refined carbs", "processed meat"],
    "edema": ["salt", "sodium", "processed"],
    "varicose veins": ["salt", "sodium"],
    "hemorrhoids": ["spicy", "alcohol", "caffeine"],
    "candida": ["sugar", "refined carbs", "alcohol", "yeast"],
    "yeast infection": ["sugar", "refined carbs", "yeast"],
    "oral thrush": ["sugar", "refined carbs"],
    "dental cavities": ["sugar", "sticky sweets", "soda"],
    "gingivitis": ["sugar", "acidic"],
    "gum disease": ["sugar", "acidic"],
    "vitiligo": [],
    "alopecia": [],
    "hair loss": [],
}

class ToolBasedNutritionAgent:
    def __init__(self):
        self.shared_rate_limiter = TokenBucket(capacity=45, fill_rate=45)
        self.general_llm_service = LLMService(service_type='general', rate_limiter=self.shared_rate_limiter)
        self.blood_report_llm_service = LLMService(service_type='blood_report', rate_limiter=self.shared_rate_limiter)
        self.food_image_llm_service = LLMService(service_type='food_image')
        self.summary_llm_service = LLMService(service_type='audio_summary')
        self.openai_weekly_llm_service = OpenAI_LLMService()
        self.classifier_llm_service = LLMService(service_type='query_classification')
        
        redis_url = os.getenv("REDIS_URL_AI", "redis://localhost:6379")
        try:
            if redis_url.startswith("rediss://"):
                self.redis_client = redis.from_url(
                    redis_url,
                    decode_responses=True,
                    ssl_cert_reqs=None,
                    socket_timeout=5,
                    socket_connect_timeout=5
                )
            else:
                self.redis_client = redis.from_url(redis_url, decode_responses=True, socket_timeout=5, socket_connect_timeout=5)
            self.redis_client.ping()
            logger.info(f"Redis connection established: {redis_url.split('@')[-1]}")
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}. Caching will be disabled.")
            self.redis_client = None

        self.data_service = DataService()
        self.tools = {} 
        db_persistence_tool = DatabasePersistenceTool(FRISKA_API_URL)
        meal_plan_generator_tool = MealPlanGeneratorTool(self.general_llm_service, self.data_service)
        history_retriever_tool = HistoryRetrieverTool(db_persistence_tool, self.general_llm_service)
        calorie_calculator_tool_instance = CalorieCalculatorTool(self.data_service)
        meal_plan_adjuster_tool = MealPlanAdjusterTool(self.general_llm_service, self.data_service)
        grocery_list_tool = GroceryListTool(self.general_llm_service, db_persistence_tool)
        fitness_profile_updater_tool = FitnessProfileUpdaterTool(self.general_llm_service)
        fitness_plan_generator_tool = FitnessPlanGeneratorTool(
            self.general_llm_service, 
            fitness_api_key, 
            fitness_endpoint_url
        )

        workout_plan_adjuster_tool = WorkoutAdjusterTool(self.general_llm_service)

        water_step_tool = WaterStepTool(db_persistence_tool, self.general_llm_service)
        session_booking_tool = SessionBookingTool(self.general_llm_service, db_persistence_tool)

        self.vital_units = {
            "Heart Rate": "bpm",
            "Blood Pressure": "mmHg",
            "Blood Glucose": "mg/dL",
            "Blood Oxygen Saturation": "%",
            "Respiration Rate": "breaths/min",
            "Body Temperature": "°F",
            "Blood Ketones": "mmol/L",
            "Body Fat %": "%"
        }
        self.all_vital_names = list(self.vital_units.keys())    

        self.DIETARY_RESTRICTION_FOOD_MAP = {
            "Dairy-Free": ["milk", "cheese", "yogurt", "butter", "buttery", "cream", "creamy", "ghee", "paneer", "chenna", "malai", "mawa", "khoya", "dahi", "raita", "lassi", "chaas", "thandai", "datshi"],
            "Lactose Intolerance": ["milk", "cheese", "yogurt", "ice cream", "cream", "butter", "ghee", "paneer", "chenna", "chhena", "malai", "mawa", "khoa", "khoya", "buttermilk", "chaas", "lassi", "curd", "dahi"],
            "Gluten-Free": ["wheat", "barley", "rye", "bread", "pasta", "flour", "couscous", "semolina", "spelt", "triticale", "malt"],
            "Low-Sodium": ["canned soup", "processed meats", "soy sauce", "salted nuts", "pickles", "chips", "pretzels", "cured meat"],
            "Low-Fat": ["fried food", "heavy cream", "butter", "fatty meats", "processed snacks", "sausages", "full-fat dairy"],
            "Low-Carb (e.g. Keto)": ["bread", "pasta", "rice", "potatoes", "sugar", "high-sugar fruits", "corn", "beans", "legumes"],
            "Sugar-Free (e.g. for Diabetes)": ["sugar", "soda", "candy", "fruit juice", "syrup", "honey", "white bread", "pastries", "sweetened yogurt"],
            "FODMAP Diet (for Digestive Issues)": ["onion", "garlic", "wheat", "apples", "honey", "milk", "beans", "cauliflower", "mushrooms", "artichokes"],
            "No Red Meat": ["beef", "pork", "lamb", "mutton", "veal", "goat", "bison", "elk", "venison", "caribou", "steak", "ribeye", "sirloin", "chorizo", "boerewors", "suya", "bobotie"],
            "Paleo": ["grains", "legumes", "dairy", "refined sugar", "processed foods", "potatoes"],
            "No Pork": ["pork", "bacon", "ham", "sausage", "lard", "chorizo"],
            "No Beef": ["beef", "veal", "steak", "ribeye", "sirloin", "suya"],
            "No Fried Foods": ["fried", "deep fried", "chipsi", "french fries", "fries", "fried chicken", "tempura", "falafel", "samosa"],
            "No Processed Foods": ["processed", "deli", "sausage", "hot dog", "chorizo", "salami", "pepperoni", "bologna", "spam"],
            "No Chocolate": ["chocolate", "cocoa", "cacao", "dark chocolate", "milk chocolate"],
            "Corn Allergy": ["corn", "maize", "cornstarch", "corn starch", "syrup", "corn syrup", "dextrose", "maltodextrin", "polenta", "marshmallow", "deli", "deli meat", "canned soup", "hot dog", "frankfurter", "cornmeal", "corn flour", "hominy", "masa"],
            "Kidney Stones": ["sweet potato", "spinach", "rhubarb", "beet", "beetroot", "almond", "swiss chard", "chard", "cashew", "chocolate", "bran", "wheat bran", "soy", "peanut", "okra", "collard", "chia", "urad dal", "black gram", "sesame", "amaranth"],
            "Acid Reflux": ["fried", "deep fried", "greasy", "crispy", "spicy", "citrus", "orange", "lemon", "lime", "grapefruit", "tomato sauce", "marinara", "pepper fry", "chili", "mint", "peppermint", "chocolate", "garlic", "onion", "bonda", "vada", "bajji", "puri", "tamarind", "tomato", "berry", "berries", "coconut milk"],
            "Heartburn": ["fried", "deep fried", "greasy", "crispy", "spicy", "citrus", "orange", "lemon", "lime", "grapefruit", "tomato sauce", "marinara", "pepper fry", "chili", "mint", "peppermint", "chocolate", "garlic", "onion", "bonda", "vada", "bajji", "puri", "tamarind", "tomato", "berry", "berries", "coconut milk"],
            "GERD": ["fried", "deep fried", "greasy", "crispy", "spicy", "citrus", "orange", "lemon", "lime", "grapefruit", "tomato sauce", "marinara", "pepper fry", "chili", "mint", "peppermint", "chocolate", "garlic", "onion", "bonda", "vada", "bajji", "puri", "tamarind", "tomato", "berry", "berries", "coconut milk"]
        }


        self.tools = {
            ToolType.PROFILE_RETRIEVER.value: ProfileRetrieverTool(db_tool=db_persistence_tool, llm_service=self.general_llm_service),
            ToolType.PROFILE_UPDATER.value: ProfileUpdaterTool(self.general_llm_service, db_persistence_tool),
            ToolType.DISEASE_ADVISOR.value: DiseaseAdvisorTool(self.data_service, self.general_llm_service),
            ToolType.VITAL_ADVISOR.value: VitalAdvisorTool(self.data_service, self.general_llm_service, self.vital_units),
            ToolType.FOOD_MATCHER.value: FoodMatcherTool(self.data_service),
            ToolType.CALORIE_CALCULATOR.value: calorie_calculator_tool_instance,
            ToolType.MEAL_PLAN_GENERATOR.value: meal_plan_generator_tool,
            ToolType.NUTRITION_ANALYZER.value: NutritionAnalyzerTool(self.general_llm_service, self.data_service),
            ToolType.HISTORY_RETRIEVER.value: history_retriever_tool,
            ToolType.MEAL_RETRIEVER.value: MealRetrieverTool(),
            ToolType.DATABASE_PERSISTENCE.value: db_persistence_tool,
            ToolType.MEAL_CHECK_IN.value: MealCheckInTool(self, self.general_llm_service, self.data_service, meal_plan_generator_tool, db_persistence_tool, history_retriever_tool, meal_plan_adjuster_tool),
            ToolType.MEAL_PLAN_ADJUSTER.value: meal_plan_adjuster_tool,
            ToolType.CONSTRAINT_EXTRACTOR.value: ConstraintExtractorTool(self.data_service),
            ToolType.GOAL_UPDATER.value: GoalUpdaterTool(self.general_llm_service, db_persistence_tool), 
            ToolType.GENERAL_QUERY.value: GeneralQueryTool(self.general_llm_service),
            ToolType.BLOOD_REPORT_ANALYZER.value: BloodReportAnalyzerTool(self.blood_report_llm_service),
            ToolType.MEAL_INGREDIENTS.value: MealIngredientsTool(self.general_llm_service),
            ToolType.PANEL_QUERY.value: PanelQueryTool(self.general_llm_service),
            ToolType.BLOOD_REPORT_QUERY.value: BloodReportQueryTool(self.general_llm_service, db_persistence_tool),
            ToolType.GROCERY_LIST.value: grocery_list_tool,
            ToolType.SPECIAL_MEAL_PLAN_GENERATOR.value: SpecialMealPlanGeneratorTool(self.general_llm_service),
            ToolType.GREETING.value: GreetingTool(self.general_llm_service),
            ToolType.PROFILE_SUMMARY.value: ProfileSummaryTool(self.general_llm_service),
            ToolType.AUDIOSUMMARY.value: AudioSummaryTool(self.summary_llm_service),
            ToolType.CRAVING_ASSISTANT.value: CravingAssistantTool(self.general_llm_service),
            ToolType.FITNESS_PLAN_GENERATOR.value: fitness_plan_generator_tool,
            ToolType.SESSION_BOOKING.value: session_booking_tool,
            ToolType.WATER_STEP_ADVISOR.value: water_step_tool,
            ToolType.FITNESS_PLAN_GENERATOR.value: fitness_plan_generator_tool,
            ToolType.WORKOUT_PLAN_ADJUSTER.value: workout_plan_adjuster_tool,
            ToolType.FITNESS_PROFILE_UPDATER.value: fitness_profile_updater_tool,
            ToolType.WELLNESS_ADVISOR.value: WellnessAdvisorTool(self.general_llm_service),
            ToolType.FOOD_ANALYZER.value: FoodAnalyzerTool(self.general_llm_service, self.data_service),

        }
        
        if self.openai_weekly_llm_service and self.openai_weekly_llm_service.client:
            self.tools[ToolType.WEEKLY_MEAL_PLAN_GENERATOR.value] = WeeklyMealPlanGeneratorTool(self, self.openai_weekly_llm_service, self.data_service, meal_plan_generator_tool)
        self.tools[ToolType.QUERY_CLASSIFIER.value] = QueryClassifierTool(self.classifier_llm_service, self.tools)
        self.tools[ToolType.SUMMARIZER.value] = SummarizationTool(self.general_llm_service)
        self.meal_plan_generator_tool = meal_plan_generator_tool
        
        self.health_orchestrator = HealthOrchestrator(
            query_classifier_tool=self.tools[ToolType.QUERY_CLASSIFIER.value],
            tools=self.tools,
            llm_service=self.general_llm_service,
            data_service=self.data_service
        )
        logger.info("Health Orchestrator initialized with multi-domain support (Nutrition, Fitness, Wellness, Food Analysis)")

    def _get_sensible_portion_limits(self) -> Dict[str, float]:
        return {
            'cup': 2.0, 
            'cups': 2.0, 
            'tbsp': 4.0,
            'tablespoon': 4.0,
            'tablespoons': 4.0,
            'tsp': 6.0,
            'teaspoon': 6.0,
            'teaspoons': 6.0,
            'whole': 4.0,
            'piece': 4.0,
            'pieces': 4.0
        }
    
    def _extract_portion_from_name(self, food_name: str) -> tuple[float, str]:
        pattern = r'\((\d+(?:\.\d+)?)\s*(cup|tbsp|tsp|oz|fl oz|slice|piece|serving|whole)s?\)'
        match = re.search(pattern, food_name, re.IGNORECASE)
        if match:
            return float(match.group(1)), match.group(2).lower()
        return None, None
    
    def _classify_food_form(self, food_name: str) -> str:
        name_lc = food_name.lower()
        if re.search(r'\b(soup|broth|stew|curry|beverage|juice|smoothie|milk|water|tea|coffee)\b', name_lc):
            return 'LIQUID'
        if re.search(r'\b(oil|seed|seeds|butter|spread|dressing|sauce|syrup|honey|sugar|salt)\b', name_lc):
            if 'butternut' not in name_lc and 'pumpkin' not in name_lc and 'doughnut' not in name_lc:
                return 'GRANULAR_OR_LIQUID_CONDIMENT'
        
        if re.search(r'\b(almond|walnut|pecan|cashew|pistachio|peanut|macadamia)\b', name_lc):
            return 'NUTS'
        if re.search(r'\b(burger|sandwich|pizza|muffin|egg|eggs|apple|banana|orange|wrap|taco|burrito|sausage|meatball)\b', name_lc):
            return 'PIECE'
        if re.search(r'\b(rice|quinoa|oats|pasta|noodle|noodles|spaghetti|macaroni|lentil|lentils|bean|beans|chickpea|chickpeas)\b', name_lc):
            return 'GRAIN_OR_LEGUME'
        return 'SOLID_CHUNK'
    
    def _get_culinary_unit_profile(self, food_name: str) -> dict:
        name_lc = food_name.lower()
        
        if re.search(r'\b(ice cream|gelato|sorbet|frozen yogurt|sherbet)\b', name_lc):
            return {'unit': 'cup', 'density': 4.0, 'min_qty': 0.25, 'max_qty': 2.0} 
        if re.search(r'\bpizza\b', name_lc):
            return {'unit': 'slice', 'density': 3.0, 'min_qty': 1.0, 'max_qty': 4.0}
        
        if re.search(r'\b(toast|bread slice|sliced bread|sandwich bread)\b', name_lc):
            return {'unit': 'slice', 'density': 1.0, 'min_qty': 1.0, 'max_qty': 4.0}

        if re.search(r'\b(sandwich|burger|wrap|burrito|taco|quesadilla|hot dog|kebab|shawarma|gyro|falafel wrap|sub|hoagie|panini)\b', name_lc):
            return {'unit': 'piece', 'density': 6.0, 'min_qty': 0.5, 'max_qty': 2.0}

        if re.search(r'\b(pancake|pancakes|waffle|waffles|crepe|crepes|french toast)\b', name_lc):
            return {'unit': 'piece', 'density': 2.0, 'min_qty': 1.0, 'max_qty': 4.0}

        if re.search(r'\b(dumpling|dumplings|momo|momos|gyoza|potsticker|ravioli|tortellini|pierogi|spring roll|egg roll|samosa|samosas|empanada)\b', name_lc):
            return {'unit': 'piece', 'density': 1.0, 'min_qty': 2.0, 'max_qty': 8.0}

        if re.search(r'\b(sushi|sashimi|maki|roll|onigiri|rice ball|bao|bao bun|rice roll)\b', name_lc):
            return {'unit': 'piece', 'density': 1.5, 'min_qty': 2.0, 'max_qty': 8.0}

        if re.search(r'\b(cookie|cookies|brownie|brownies|muffin|muffins|cupcake|cupcakes|donut|donuts|doughnut|pastry|pastries|biscuit|biscuits|scone|croissant|churro|churros|bar|bars)\b', name_lc):
            if not re.search(r'\b(barley|barbecue|bbq)\b', name_lc):
                return {'unit': 'piece', 'density': 2.0, 'min_qty': 0.5, 'max_qty': 3.0}

        if re.search(r'\b(cake|cheesecake|pie|tart|quiche|frittata|flan|casserole slice)\b', name_lc):
            if not re.search(r'\b(rice cake|fish cake|crab cake)\b', name_lc):  
                return {'unit': 'slice', 'density': 3.0, 'min_qty': 0.5, 'max_qty': 2.0}

        if re.search(r'\b(yogurt|yoghurt|pudding|custard|cottage cheese|ricotta|paneer|cream)\b', name_lc):
            if not re.search(r'\b(curry|dish)\b', name_lc):  
                return {'unit': 'cup', 'density': 8.0, 'min_qty': 0.25, 'max_qty': 2.0}

        if re.search(r'\b(soup|stew|curry|dal|dhal|broth|bisque|chowder|beverage|drink|milk|smoothie|juice|shake|tea|coffee|gravy|sauce)\b', name_lc):
            return {'unit': 'cup', 'density': 8.0, 'min_qty': 0.5, 'max_qty': 3.0}
        
        if re.search(r'\b(rice|fried rice|biryani|pulao|pilaf|oat|oats|oatmeal|porridge|quinoa|couscous|barley|pasta|noodles|noodle|ramen|spaghetti|macaroni|bean|beans|lentil|lentils|chickpea|chickpeas|dal|hummus)\b', name_lc):
            if not re.search(r'\b(soup|salad)\b', name_lc): 
                return {'unit': 'cup', 'density': 6.0, 'min_qty': 0.25, 'max_qty': 2.5}

        if re.search(r'\b(chips|crisps|fries|crackers|popcorn|pretzels|nachos|snack mix|trail mix)\b', name_lc):
            return {'unit': 'oz', 'density': 1.0, 'min_qty': 0.5, 'max_qty': 4.0} 

        if re.search(r'\b(fillet|fillets|steak|breast|wing|drumstick|whole|mackerel|salmon|trout|fish|chicken|beef|pork|chop|egg|eggs|sausage|link|patty|cutlet|schnitzel)\b', name_lc):
            if not re.search(r'\b(soup|stew|curry|mince|minced|salad|spread|scrambled|omelette|omelet)\b', name_lc):
                return {'unit': 'piece', 'density': 4.0, 'min_qty': 0.5, 'max_qty': 3.0}
 
        if re.search(r'\b(shrimp|prawn|prawns|scallop|scallops|oyster|oysters|clam|clams|mussel|mussels|crab|lobster|crayfish)\b', name_lc):
            if not re.search(r'\b(soup|stew|salad|cake|curry)\b', name_lc):
                return {'unit': 'piece', 'density': 0.5, 'min_qty': 2.0, 'max_qty': 12.0}

        if re.search(r'\b(meatball|meatballs|falafel|kofta|fish ball|chicken ball|protein ball|energy ball|fritter|fritters|pakora|croquette|arancini|rice ball|skewer|skewers|kebab stick)\b', name_lc):
            if not re.search(r'\b(soup|stew|curry)\b', name_lc):
                return {'unit': 'piece', 'density': 0.75, 'min_qty': 2.0, 'max_qty': 8.0}

        if re.search(r'\b(soup|stew|curry|dal|dhal|broth|bisque|chowder|beverage|drink|milk|smoothie|juice|shake|tea|coffee|gravy|sauce)\b', name_lc):
            return {'unit': 'cup', 'density': 8.0, 'min_qty': 0.5, 'max_qty': 3.0}

        if re.search(r'\b(almond|almonds|walnut|walnuts|pecan|pecans|peanut|peanuts|cashew|cashews|pistachio|pistachios|hazelnut|macadamia|nut|nuts)\b', name_lc):
            if not re.search(r'\b(butter|spread|milk|cream)\b', name_lc):
                return {'unit': 'oz', 'density': 1.0, 'min_qty': 0.5, 'max_qty': 3.0}  

        if re.search(r'\b(chia|flax|hemp|seed|seeds|oil|olive oil|coconut oil|ghee|tahini|pesto)\b', name_lc):
            return {'unit': 'tbsp', 'density': 0.5, 'min_qty': 0.5, 'max_qty': 2.0}
        
        if re.search(r'\b(rice|fried rice|biryani|pulao|pilaf|oat|oats|oatmeal|porridge|quinoa|couscous|barley|pasta|noodles|noodle|ramen|spaghetti|macaroni|bean|beans|lentil|lentils|chickpea|chickpeas|dal|hummus)\b', name_lc):
            if not re.search(r'\b(soup|salad)\b', name_lc):  
                return {'unit': 'cup', 'density': 6.0, 'min_qty': 0.25, 'max_qty': 2.5}
        
        if re.search(r'\b(salad|greens|spinach|kale|cabbage|lettuce|carrot|carrots|broccoli|cauliflower|vegetable|vegetables|veggie|veggies|slaw|coleslaw)\b', name_lc):
            return {'unit': 'cup', 'density': 3.0, 'min_qty': 0.5, 'max_qty': 4.0}
        

        if re.search(r'\b(pickle|chutney|salsa|dip|spread|jam|jelly|honey|syrup|ketchup|mustard|mayo|mayonnaise|relish|marinade|dressing)\b', name_lc):
            return {'unit': 'tbsp', 'density': 0.5, 'min_qty': 0.5, 'max_qty': 2.0}

        return {'unit': 'cup', 'density': 5.5, 'min_qty': 0.25, 'max_qty': 3.0}
    
    def _is_calorie_dense_food(self, food_name: str) -> bool:
        """Identify calorie-dense foods suitable for absorbing deficit (fats, nuts, oils, protein powders)."""
        food_lower = food_name.lower()
        dense_keywords = [
            'oil', 'butter', 'ghee', 'nut', 'almond', 'walnut', 'cashew', 'peanut',
            'avocado', 'cheese', 'cream', 'protein powder', 'peanut butter', 'almond butter',
            'tahini', 'seeds', 'chia', 'flax', 'coconut', 'olive', 'bacon', 'sausage'
        ]
        return any(keyword in food_lower for keyword in dense_keywords)

    def _programmatically_scale_meal_portions(self, parsed_plan: Dict, meal_calorie_distribution: Dict, retry_attempt: int = 0, MAX_RETRIES: int = 2, user_constraints: Dict = None, generator = None) -> Dict:
        logger.info("Starting programmatic scaling of meal portions to enforce exact calorie targets.")
        logger.info(f" Retry context: attempt {retry_attempt + 1}/{MAX_RETRIES}")
        
        user_constraints = user_constraints or {}
        scaled_plan_json = copy.deepcopy(parsed_plan)
        
        sensible_limits = self._get_sensible_portion_limits()
        meal_calorie_deficits = {} 
        foods_clamped = []
        used_bridgers = set() 
        PROTEIN_PERCENTAGE_CEILING = 35.0 
        SODIUM_CEILING_PER_MEAL = 600 

        logger.info("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        logger.info("DERIVED CALORIE TRUTH (PRE-SCALING): Recalculating all calories from macros (4-4-9)")
        logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        
        for meal_name_dct, meal_foods in scaled_plan_json.get("meal_plan", {}).items():
            for food in meal_foods:
                p = food.get('nutrients', {}).get('Protein', 0.0)
                c = food.get('nutrients', {}).get('Carbohydrates', 0.0)
                f = food.get('nutrients', {}).get('Fat', 0.0)
                
                derived_cals = round((p * 4.0) + (c * 4.0) + (f * 9.0))
                
                original_cals = food.get('nutrients', {}).get('Calories', 0)
                
                if derived_cals > 0:
                    food['nutrients']['Calories'] = derived_cals
                    
                    if abs(derived_cals - original_cals) > 5:
                        logger.debug(f"   ✓ {food.get('name', 'Unknown')}: {original_cals:.0f} → {derived_cals:.0f} kcal (P:{p:.1f}g C:{c:.1f}g F:{f:.1f}g)")
            
            meal_total = sum(f.get('nutrients', {}).get('Calories', 0) for f in meal_foods)
            logger.debug(f"   {meal_name_dct}: {meal_total:.0f} kcal (recalculated from macros)")
        
        logger.info(f"    DERIVED CALORIE TRUTH APPLIED: All food calories now match 4-4-9 formula BEFORE scaling")
        
        for meal_name, target_calories in meal_calorie_distribution.items():
            if meal_name not in scaled_plan_json.get("meal_plan", {}):
                continue
            current_meal_calories = sum(
                item.get("nutrients", {}).get("Calories", 0)
                for item in scaled_plan_json["meal_plan"][meal_name]
            )
            if current_meal_calories < 20:
                logger.warning(f" LOW CALORIE MEAL: '{meal_name}' has only {current_meal_calories:.1f} kcal (< 20 threshold)")
                logger.warning(f"Setting scaling ratio to 1.0 (no scaling) and continuing with rest of plan")
                for food_item in scaled_plan_json["meal_plan"][meal_name]:
                    pass  
                continue  

            if current_meal_calories == target_calories:
                logger.info(f"Meal '{meal_name}' is already at target ({current_meal_calories:.0f} kcal). Skipping.")
                continue

            scaling_ratio = target_calories / current_meal_calories
            
            logger.info(f" ISOLATED MEAL SCALING: '{meal_name}'")
            logger.info(f"   Current Meal Total: {current_meal_calories:.0f} kcal")
            logger.info(f"   Target Meal Bucket: {target_calories:.0f} kcal")
            logger.info(f"   Meal-Specific Multiplier: {scaling_ratio:.4f}x")
            logger.info(f"   (This multiplier applies ONLY to '{meal_name}' foods)")
            if scaling_ratio < 0.05 or scaling_ratio > 3.0:
                logger.warning(f" EXTREME MEAL MULTIPLIER: '{meal_name}' has ratio {scaling_ratio:.2f}x (outside ideal 0.05-3.0x)")
                logger.warning(f"   Current: {current_meal_calories:.0f} kcal → Target Bucket: {target_calories:.0f} kcal")
                logger.warning(f"   Applying extreme ratio to enforce strict caloric partitioning")
            logger.info(f"\n BEST-FIT PORTION ANCHORING: Pre-scaling optimization for '{meal_name}'")
            
            meal_items = scaled_plan_json["meal_plan"][meal_name]
            num_items = len(meal_items)
            
            if num_items > 0:
                ideal_item_kcal_target = target_calories / num_items
                
                logger.info(f"   Meal has {num_items} items → Ideal per-item target: {ideal_item_kcal_target:.0f} kcal")
                
                anchored_count = 0
                fallback_count = 0
                
                for idx, food_item in enumerate(meal_items):
                    food_name = food_item.get('name', 'Unknown')
                    current_cals = food_item.get('nutrients', {}).get('Calories', 0)
                    
                    portion_options = food_item.get('portion_options', [])
                    recommended_portion = food_item.get('recommended_portion', {})
                    
                    if portion_options and len(portion_options) > 0:
                        best_option = None
                        best_diff = float('inf')
                        
                        for option in portion_options:
                            option_cals = option.get('calories_kcal', 0)
                            diff = abs(option_cals - ideal_item_kcal_target)
                            
                            if diff < best_diff:
                                best_diff = diff
                                best_option = option
                        
                        if best_option:
                            chosen_label = best_option.get('label', 'Unknown')
                            chosen_portion_g = best_option.get('portion_g', 100.0)
                            chosen_cals = best_option.get('calories_kcal', 0)
                            chosen_protein = best_option.get('protein_g', 0)
                            chosen_carbs = best_option.get('total_carbs_g', 0)
                            chosen_fat = best_option.get('total_fat_g', 0)
                            chosen_fiber = best_option.get('dietary_fiber_g', 0)
                            chosen_sodium = best_option.get('sodium_mg', 0)
                            chosen_sugar = best_option.get('total_sugar_g', 0)
                            chosen_cholesterol = best_option.get('cholesterol_mg', 0)
                            
                            food_item['nutrients']['Portion Weight'] = chosen_portion_g
                            food_item['nutrients']['Calories'] = int(chosen_cals)
                            food_item['nutrients']['Protein'] = round(chosen_protein, 2)
                            food_item['nutrients']['Carbohydrates'] = round(chosen_carbs, 2)
                            food_item['nutrients']['Fat'] = round(chosen_fat, 2)
                            food_item['nutrients']['Fiber'] = round(chosen_fiber, 2)
                            food_item['nutrients']['Sodium'] = round(chosen_sodium, 2)
                            food_item['nutrients']['Sugar'] = round(chosen_sugar, 2)
                            food_item['nutrients']['Cholesterol'] = round(chosen_cholesterol, 2)
                            
                            food_item['portion_label'] = chosen_label
                            
                            food_item['is_anchored'] = True
                            
                            scaling_reduction_pct = ((current_cals / ideal_item_kcal_target) - (chosen_cals / ideal_item_kcal_target)) * 100
                            
                            logger.info(f"    Anchored '{food_name}' to '{chosen_label}' portion ({chosen_cals:.0f} kcal)")
                            logger.info(f"      Was: {current_cals:.0f} kcal → Now: {chosen_cals:.0f} kcal (reduces scaling multiplier)")
                            
                            anchored_count += 1
                        else:
                            logger.debug(f"    No valid portion option found for '{food_name}' - using baseline")
                            food_item['is_anchored'] = False
                            fallback_count += 1
                    
                    elif recommended_portion and recommended_portion.get('calories_kcal'):
                        rec_cals = recommended_portion.get('calories_kcal', 0)
                        rec_portion_g = recommended_portion.get('portion_g', 100.0)
                        rec_protein = recommended_portion.get('protein_g', 0)
                        rec_carbs = recommended_portion.get('total_carbs_g', 0)
                        rec_fat = recommended_portion.get('total_fat_g', 0)
                        rec_fiber = recommended_portion.get('dietary_fiber_g', 0)
                        rec_sodium = recommended_portion.get('sodium_mg', 0)
                        rec_sugar = recommended_portion.get('total_sugar_g', 0)
                        rec_cholesterol = recommended_portion.get('cholesterol_mg', 0)
                        
                        food_item['nutrients']['Portion Weight'] = rec_portion_g
                        food_item['nutrients']['Calories'] = int(rec_cals)
                        food_item['nutrients']['Protein'] = round(rec_protein, 2)
                        food_item['nutrients']['Carbohydrates'] = round(rec_carbs, 2)
                        food_item['nutrients']['Fat'] = round(rec_fat, 2)
                        food_item['nutrients']['Fiber'] = round(rec_fiber, 2)
                        food_item['nutrients']['Sodium'] = round(rec_sodium, 2)
                        food_item['nutrients']['Sugar'] = round(rec_sugar, 2)
                        food_item['nutrients']['Cholesterol'] = round(rec_cholesterol, 2)
                        
                        food_item['portion_label'] = recommended_portion.get('description', 'Recommended')
                        
                        food_item['is_anchored'] = True
                        
                        logger.info(f"    Anchored '{food_name}' to recommended portion ({rec_cals:.0f} kcal)")
                        anchored_count += 1
                    
                    else:
                        logger.debug(f"    No portion data for '{food_name}' - using 100g baseline")
                        food_item['is_anchored'] = False
                        fallback_count += 1
                
                new_meal_total = sum(
                    item.get("nutrients", {}).get("Calories", 0)
                    for item in meal_items
                )
                
                if new_meal_total > 0:
                    new_scaling_ratio = target_calories / new_meal_total
                else:
                    new_scaling_ratio = scaling_ratio
                
                logger.info(f"\n    ANCHORING SUMMARY:")
                logger.info(f"      Anchored: {anchored_count}/{num_items} items")
                logger.info(f"      Fallback: {fallback_count}/{num_items} items")
                logger.info(f"      Pre-anchor meal total: {current_meal_calories:.0f} kcal")
                logger.info(f"      Post-anchor meal total: {new_meal_total:.0f} kcal")
                logger.info(f"      Old scaling ratio: {scaling_ratio:.4f}x")
                logger.info(f"      New scaling ratio: {new_scaling_ratio:.4f}x")
                logger.info(f"       Scaling multiplier {'reduced' if new_scaling_ratio < scaling_ratio else 'adjusted'} via portion anchoring\n")
                
                current_meal_calories = new_meal_total
                scaling_ratio = new_scaling_ratio
            if new_meal_total > target_calories:
                logger.info(f"\n ACCESSORY PRUNING: Meal exceeds target ({new_meal_total:.0f} > {target_calories:.0f} kcal)")
                
                accessories = [
                    item for item in meal_items
                    if item.get('is_core') == False
                ]
                accessories.sort(key=lambda x: x.get('nutrients', {}).get('Calories', 0), reverse=True)
                
                pruned_count = 0
                while new_meal_total > target_calories and accessories:
                    item_to_remove = accessories.pop(0)
                    removed_name = item_to_remove.get('name', 'Unknown')
                    removed_cals = item_to_remove.get('nutrients', {}).get('Calories', 0)
                    
                    meal_items.remove(item_to_remove)
                    new_meal_total -= removed_cals
                    pruned_count += 1
                    
                    logger.info(f"    Pruned accessory '{removed_name}' ({removed_cals:.0f} kcal) to preserve core anchored items")
                    logger.info(f"      New meal total: {new_meal_total:.0f} kcal (target: {target_calories:.0f} kcal)")
                
                if pruned_count > 0:
                    logger.info(f"\n    PRUNING SUMMARY: Removed {pruned_count} accessory item(s)")
                    logger.info(f"      Final meal total: {new_meal_total:.0f} kcal")
                    
                    if new_meal_total > 0:
                        new_scaling_ratio = target_calories / new_meal_total
                    else:
                        new_scaling_ratio = 1.0
                    
                    logger.info(f"      Updated scaling ratio: {new_scaling_ratio:.4f}x\n")
                    
                    current_meal_calories = new_meal_total
                    scaling_ratio = new_scaling_ratio
                else:
                    logger.info(f"    No accessories to prune (all items are core)\n")
            

            
            if meal_name in ['Morning Snack', 'Evening Snack'] and len(meal_items) < 2:
                logger.info(f"\n SNACK FILLER: '{meal_name}' has only {len(meal_items)} item(s), adding low-calorie filler")
                
                used_food_names = set()
                for meal_key, meal_items_list in scaled_plan_json.get("meal_plan", {}).items():
                    for item in meal_items_list:
                        base_name = re.sub(r'\s*\([^)]*\)\s*$', '', item.get('name', '')).strip().lower()
                        used_food_names.add(base_name)
                
                filler_options = [
                    {
                        'name': 'Unsweetened Green Tea (1 cup)',
                        'base_name': 'unsweetened green tea',
                        'nutrients': {
                            'Calories': 2, 'Protein': 0.0, 'Carbohydrates': 0.5, 'Fat': 0.0,
                            'Fiber': 0.0, 'Sodium': 2, 'Iodine': 0.0, 'Sugar': 0.0,
                            'Cholesterol': 0, 'Portion Weight': 240.0
                        },
                        'is_filler': True 
                    },
                    {
                        'name': 'Plain Water (1 cup)',
                        'base_name': 'plain water',
                        'nutrients': {
                            'Calories': 0, 'Protein': 0.0, 'Carbohydrates': 0.0, 'Fat': 0.0,
                            'Fiber': 0.0, 'Sodium': 0, 'Iodine': 0.0, 'Sugar': 0.0,
                            'Cholesterol': 0, 'Portion Weight': 240.0
                        },
                        'is_filler': True 
                    },
                    {
                        'name': 'Herbal Tea (1 cup)',
                        'base_name': 'herbal tea',
                        'nutrients': {
                            'Calories': 2, 'Protein': 0.0, 'Carbohydrates': 0.5, 'Fat': 0.0,
                            'Fiber': 0.0, 'Sodium': 2, 'Iodine': 0.0, 'Sugar': 0.0,
                            'Cholesterol': 0, 'Portion Weight': 240.0
                        },
                        'is_filler': True 
                    }
                ]
                
                filler_item = None
                for option in filler_options:
                    if option['base_name'] not in used_food_names:
                        filler_item = {
                            'name': option['name'],
                            'nutrients': option['nutrients'].copy(),
                            'is_core': False,
                            'is_anchored': False,
                            'is_filler': True  
                        }
                        break
                
                if filler_item is None:
                    filler_item = {
                        'name': 'Plain Water (1 cup)',
                        'nutrients': filler_options[1]['nutrients'].copy(),
                        'is_core': False,
                        'is_anchored': False,
                        'is_filler': True 
                    }
                
                meal_items.append(filler_item)
                filler_cals = filler_item['nutrients']['Calories']
                new_meal_total += filler_cals
                current_meal_calories += filler_cals
                
                logger.info(f"    Added '{filler_item['name']}' ({filler_cals} kcal) to maintain 2-item snack structure")
                logger.info(f"   New meal total: {new_meal_total:.0f} kcal\n")
            
            logger.info(f"Scaling meal '{meal_name}': Current={current_meal_calories:.0f}, Target={target_calories:.0f}, Ratio={scaling_ratio:.4f}")
            
            items_to_remove = []
            meal_deficit = 0.0  
            snapping_calorie_loss = 0.0 
            
            meal_running_protein = 0.0
            meal_running_sodium = 0.0
            
            for idx, food_item in enumerate(scaled_plan_json["meal_plan"][meal_name]):
                original_portion = food_item.get("nutrients", {}).get("Portion Weight", 1)
                food_name_lower = food_item['name'].lower()
                is_zero_cal_liquid = any(
                    liquid_term in food_name_lower 
                    for liquid_term in ['tea', 'water', 'coffee', 'black coffee', 'green tea', 'herbal tea']
                )
                if food_item.get('is_anchored') == True:
                    item_scaling_ratio = 1.0
                    logger.info(f" ANCHOR LOCKED: '{food_item['name']}' bypassing programmatic scaling (ratio forced to 1.0x)")
                    logger.info(f"   Item is anchored to real portion. Preserving absolute portion realism.")
                elif is_zero_cal_liquid:
                    item_scaling_ratio = 1.0
                    logger.info(f"Clamping scaling for '{food_item['name']}' (zero-calorie liquid) to 1.0x - preventing absurd portions")
                else:
                    item_scaling_ratio = scaling_ratio
                
                quantity, unit = self._extract_portion_from_name(food_item['name'])
                
                if quantity and unit and unit.lower() == 'cup':
                    DENSE_GRAINS = ['sorghum', 'oats', 'quinoa', 'rice', 'millet', 'barley', 'bulgur', 'couscous']
                    is_dense_grain = any(grain in food_name_lower for grain in DENSE_GRAINS)
                    
                    if is_dense_grain:
                        desired_volume = quantity * item_scaling_ratio
                        if desired_volume > 2.0:
                            logger.warning(f" VOLUMETRIC CLAMP: '{food_item['name']}' scaled to {desired_volume:.2f} cups (unrealistic)")
                            logger.warning(f"   Clamping to 1.5 cups max and redistributing calorie deficit")
                            
                            original_calories = food_item.get("nutrients", {}).get("Calories", 0)
                            desired_calories = original_calories * item_scaling_ratio
                            clamped_ratio = 1.5 / quantity
                            clamped_calories = original_calories * clamped_ratio
                            calorie_deficit = desired_calories - clamped_calories
                            
                            item_scaling_ratio = clamped_ratio
                            
                            meal_deficit += calorie_deficit
                            foods_clamped.append({
                                'meal': meal_name,
                                'food': food_item['name'],
                                'original_qty': quantity,
                                'desired_qty': desired_volume,
                                'clamped_qty': 1.5,
                                'unit': 'cup',
                                'lost_calories': calorie_deficit
                            })
                
                if quantity and unit:
                    desired_quantity = quantity * item_scaling_ratio
                    max_limit = sensible_limits.get(unit.lower())
                    
                    if max_limit and desired_quantity > max_limit:
                        clamped_ratio = max_limit / quantity 
                        original_calories = food_item.get("nutrients", {}).get("Calories", 0)
                        desired_calories = original_calories * item_scaling_ratio
                        clamped_calories = original_calories * clamped_ratio
                        lost_calories = desired_calories - clamped_calories
                        meal_deficit += lost_calories
                        foods_clamped.append({
                            'meal': meal_name,
                            'food': food_item['name'],
                            'original_qty': quantity,
                            'desired_qty': desired_quantity,
                            'clamped_qty': max_limit,
                            'unit': unit,
                            'lost_calories': lost_calories
                        })
                        
                        logger.warning(f" PORTION CLAMPED: '{food_item['name']}'")
                        logger.warning(f"   Desired: {desired_quantity:.1f} {unit} → Clamped: {max_limit} {unit}")
                        logger.warning(f"   Calorie deficit: {lost_calories:.0f} kcal")
                        item_scaling_ratio = clamped_ratio
                

                if meal_name == "Dinner" and quantity and unit:
                    nutrients = food_item.get("nutrients", {})
                    carbs_g = nutrients.get("Carbohydrates", 0)
                    protein_g = nutrients.get("Protein", 0)
                    fat_g = nutrients.get("Fat", 0)
                    
                    is_carb_food = carbs_g > (protein_g + fat_g)
                    
                    if is_carb_food and unit.lower() in ['oz', 'ozs', 'ounce', 'ounces']:
                        desired_carb_portion = quantity * item_scaling_ratio
                        DINNER_CARB_MAX_OZ = 6.0 
                        
                        if desired_carb_portion > DINNER_CARB_MAX_OZ:
                            logger.warning(f"DINNER CARB CAP: '{food_item['name']}' would scale to {desired_carb_portion:.1f} oz")
                            logger.warning(f"   Capping to {DINNER_CARB_MAX_OZ} oz to maintain lighter dinner")
                            
                            original_calories = food_item.get("nutrients", {}).get("Calories", 0)
                            desired_calories = original_calories * item_scaling_ratio
                            capped_carb_ratio = DINNER_CARB_MAX_OZ / quantity
                            clamped_calories = original_calories * capped_carb_ratio
                            carb_clamp_deficit = desired_calories - clamped_calories
                            
                            meal_target = meal_calorie_distribution.get(meal_name, 400)
                            deficit_pct = (carb_clamp_deficit / meal_target) * 100 if meal_target > 0 else 0
                            
                            if deficit_pct < 20:  
                                item_scaling_ratio = capped_carb_ratio
                                
                                meal_deficit += carb_clamp_deficit
                                foods_clamped.append({
                                    'meal': meal_name,
                                    'food': food_item['name'],
                                    'original_qty': quantity,
                                    'desired_qty': desired_carb_portion,
                                    'clamped_qty': DINNER_CARB_MAX_OZ,
                                    'unit': 'oz',
                                    'lost_calories': carb_clamp_deficit,
                                    'reason': 'dinner_carb_lightness'
                                })
                                logger.warning(f"   Calorie deficit: {carb_clamp_deficit:.0f} kcal ({deficit_pct:.1f}% of meal target)")
                            else:
                                logger.info(f"    SKIPPING CLAMP: Would cause {deficit_pct:.1f}% deficit (> 20% threshold)")
                                logger.info(f"   Allowing full portion to preserve meal calorie target")
                

                new_portion_weight = original_portion * item_scaling_ratio
                new_portion_oz = new_portion_weight / 28.35
                
                food_name_lower = food_item.get('name', '').lower()
                nutrients = food_item.get("nutrients", {})
                item_calories = nutrients.get("Calories", 0)
                

                is_core_food = food_item.get('is_core', False) 
                
                if not is_core_food:
                    core_keywords = ['dal', 'bean', 'lentil', 'chickpea', 'tofu', 'paneer', 
                                    'rice', 'quinoa', 'bread', 'roti', 'pasta', 'noodle',
                                    'chicken', 'fish', 'meat', 'egg', 'salad', 'curry']
                    is_core_food = any(kw in food_name_lower for kw in core_keywords) or item_calories > 100
                
                accessory_keywords = ['garnish', 'topping', 'sprinkle', 'drizzle', 'sauce', 
                                     'chutney', 'pickle', 'raita', 'dip']
                is_accessory = any(kw in food_name_lower for kw in accessory_keywords) and item_calories < 50
                
                if any(kw in food_name_lower for kw in ['soup', 'broth', 'stock', 'stew', 'curry', 'dal', 'chili']):
                    MIN_PORTION_OZ = 4.0  
                elif any(kw in food_name_lower for kw in ['rice', 'quinoa', 'grain', 'oats', 'barley']):
                    MIN_PORTION_OZ = 2.0 
                elif any(kw in food_name_lower for kw in ['sauce', 'dressing', 'salsa', 'chutney']):
                    MIN_PORTION_OZ = 1.0 
                elif any(kw in food_name_lower for kw in ['spice', 'salt', 'pepper', 'garnish']):
                    MIN_PORTION_OZ = 0.1 
                elif item_calories < 10:
                    MIN_PORTION_OZ = 0.5  
                else:
                    MIN_PORTION_OZ = 1.5  
                
                
                if new_portion_oz < MIN_PORTION_OZ and item_calories >= 10:
                    if is_accessory:
                        logger.warning(f" REMOVING accessory '{food_item['name']}' - portion too small ({new_portion_oz:.2f} oz < {MIN_PORTION_OZ} oz)")
                        logger.warning(f"   Calories will be redistributed to core meal items")
                        items_to_remove.append(idx)
                        
                        rejected_item_calories = nutrients.get("Calories", 0) * item_scaling_ratio
                        meal_deficit += rejected_item_calories
                        continue
                    else:
                        logger.info(f" ADJUSTING '{food_item['name']}' to minimum portion")
                        logger.info(f"   Scaled: {new_portion_oz:.2f} oz → Adjusted: {MIN_PORTION_OZ} oz")
                        
                        adjusted_ratio = (MIN_PORTION_OZ * 28.35) / original_portion
                        item_scaling_ratio = adjusted_ratio
                        new_portion_weight = original_portion * item_scaling_ratio
                        new_portion_oz = MIN_PORTION_OZ
                        
                        logger.info(f"   Core food preserved at realistic minimum to support TDEE target")
                

                protein_g = nutrients.get("Protein", 0) * item_scaling_ratio
                carbs_g = nutrients.get("Carbohydrates", 0) * item_scaling_ratio
                fat_g = nutrients.get("Fat", 0) * item_scaling_ratio
                fiber_g = nutrients.get("Fiber", 0) * item_scaling_ratio
                
                macro_total_g = protein_g + carbs_g + fat_g + fiber_g
                density_ratio = macro_total_g / new_portion_weight if new_portion_weight > 0 else 0
                
                MAX_DENSITY_RATIO = 0.95
                
                if density_ratio > MAX_DENSITY_RATIO:
                    logger.warning(f" HIGH DENSITY: '{food_item['name']}'")
                    logger.warning(f"   Macros: {macro_total_g:.1f}g / Portion: {new_portion_weight:.1f}g = {density_ratio*100:.1f}% density")
                    logger.warning(f"   Note: Typical density is 30-60%, max plausible is {MAX_DENSITY_RATIO*100:.0f}%")
                    logger.warning(f"   Keeping item to preserve TDEE target - may indicate database issue")
                
                quantity_for_cap_check, unit_for_cap_check = self._extract_portion_from_name(food_item['name'])
                if quantity_for_cap_check and unit_for_cap_check:
                    unit_lower = unit_for_cap_check.lower()
                    scaled_qty_before_cap = quantity_for_cap_check * item_scaling_ratio
                    max_qty = None
                    clamp_reason = ""
                    
                    nutrients = food_item.get("nutrients", {})
                    carbs_g = nutrients.get("Carbohydrates", 0)
                    protein_g = nutrients.get("Protein", 0)
                    fat_g = nutrients.get("Fat", 0)
                    if unit_lower in ['cup', 'cups']:
                        DENSE_GRAIN_KEYWORDS = ['rice', 'oats', 'oatmeal', 'quinoa', 'barley', 'buckwheat', 
                                               'millet', 'couscous', 'bulgur', 'farro', 'pasta', 'noodle']
                        VEGGIE_LEGUME_KEYWORDS = ['salad', 'greens', 'spinach', 'kale', 'cabbage', 'lettuce', 
                                                 'broccoli', 'cauliflower', 'vegetable', 'veggie', 'slaw',
                                                 'beans', 'lentils', 'chickpeas', 'legumes']
                        
                        is_dense_grain = any(grain in food_name_lower for grain in DENSE_GRAIN_KEYWORDS)
                        is_veggie_or_legume = any(veg in food_name_lower for veg in VEGGIE_LEGUME_KEYWORDS)
                        
                        if is_dense_grain:
                            max_qty = 1.0  
                            clamp_reason = "Dense Grain Physical Volume Cap (FIX #2: 1.0 cup max)"
                        elif is_veggie_or_legume:
                            max_qty = 1.5  
                            clamp_reason = "Veggie/Legume Physical Volume Cap (FIX #2: 1.5 cup max)"
                        else:
                            max_qty = 2.0
                            clamp_reason = "General Cup Portion Cap"
                    
                    elif unit_lower in ['oz', 'ozs', 'ounce', 'ounces']:
                        GRAIN_KEYWORDS = ['rice', 'oats', 'oatmeal', 'quinoa', 'barley', 'buckwheat', 
                                         'millet', 'couscous', 'bulgur', 'farro', 'pasta', 'noodle']
                        is_grain = any(grain in food_name_lower for grain in GRAIN_KEYWORDS)
                        
                        is_carb_dominant = carbs_g > (protein_g + fat_g)
                        
                        if is_grain or is_carb_dominant:
                            max_qty = 10.0  
                            clamp_reason = "Grain/Carb Ounce Cap"
                        else:
                            max_qty = 10.0 
                            clamp_reason = "General Ounce Cap"
                    
                    if unit_lower in ['cup', 'cups']:
                        FRUIT_KEYWORDS = ['apple', 'banana', 'orange', 'berry', 'berries', 'mango', 
                                         'pineapple', 'melon', 'grape', 'papaya', 'kiwi', 'peach', 
                                         'pear', 'plum', 'cherry', 'strawberry', 'blueberry', 'fruit']
                        is_fruit = any(fruit in food_name_lower for fruit in FRUIT_KEYWORDS)
                        
                        if is_fruit and not max_qty:  
                            medical_conditions = user_constraints.get('medical_conditions', [])
                            has_type2_diabetes = any('type 2 diabetes' in str(cond).lower() for cond in medical_conditions)
                            
                            if has_type2_diabetes:
                                max_qty = 0.75  
                                clamp_reason = "Diabetic Fruit Physical Volume Cap (Rule 1)"
                            else:
                                max_qty = 1.5  
                                clamp_reason = "Fruit Physical Volume Cap"
                    
                    if unit_lower in ['tbsp', 'tablespoon', 'tablespoons', 'tsp', 'teaspoon', 'teaspoons']:
                        OIL_KEYWORDS = ['oil', 'olive oil', 'coconut oil', 'ghee', 'butter oil']
                        is_oil = any(oil in food_name_lower for oil in OIL_KEYWORDS)
                        
                        if is_oil:
                            if unit_lower in ['tbsp', 'tablespoon', 'tablespoons']:
                                max_qty = 0.67  
                                clamp_reason = "Oil & Garnish Cap - Rule 2 (2 tsp max)"
                            else:  
                                max_qty = 2.0  
                                clamp_reason = "Oil & Garnish Cap - Rule 2 (2 tsp max)"
                        else:
                            if unit_lower in ['tbsp', 'tablespoon', 'tablespoons']:
                                max_qty = 3.0
                                clamp_reason = "Tablespoon Portion Cap"
                            else:
                                max_qty = 6.0
                                clamp_reason = "Teaspoon Portion Cap"
                    
                    if max_qty and scaled_qty_before_cap > max_qty:
                        max_allowed_multiplier = max_qty / quantity_for_cap_check
                        
                        clamped_ratio = min(item_scaling_ratio, max_allowed_multiplier)
                        
                        original_calories = food_item.get("nutrients", {}).get("Calories", 0)
                        clamped_calories = original_calories * clamped_ratio
                        desired_calories = original_calories * item_scaling_ratio
                        lost_calories = desired_calories - clamped_calories
                        
                        logger.warning(f" PHYSICAL REALITY CLAMP: '{food_item['name']}' would scale to {scaled_qty_before_cap:.2f} {unit_for_cap_check}")
                        logger.warning(f"   {clamp_reason}: Hard cap at {max_qty} {unit_for_cap_check}")
                        logger.warning(f"   Multiplier clamped: {item_scaling_ratio:.2f}x → {clamped_ratio:.2f}x")
                        logger.warning(f"   Calorie deficit: {lost_calories:.1f} kcal (will shift to dense protein/fat, NOT low-density foods)")
                        
                        item_scaling_ratio = clamped_ratio
                        meal_deficit += lost_calories
                        foods_clamped.append({
                            'meal': meal_name,
                            'food': food_item['name'],
                            'original_qty': quantity_for_cap_check,
                            'desired_qty': scaled_qty_before_cap,
                            'clamped_qty': max_qty,
                            'unit': unit_for_cap_check,
                            'lost_calories': lost_calories,
                            'clamp_reason': clamp_reason
                        })
                
                quantity_before_snap, unit_extracted = self._extract_portion_from_name(food_item['name'])
                if quantity_before_snap and unit_extracted:
                    is_spice = item_calories < 10 or any(
                        spice_word in food_name_lower 
                        for spice_word in ['spice', 'garlic', 'ginger', 'herb', 'seasoning', 'salt', 'pepper']
                    )
                    
                    scaled_quantity_raw = quantity_before_snap * item_scaling_ratio
                    
                    snapped_quantity = self._snap_to_quarter_increments(scaled_quantity_raw, is_spice=is_spice)
                    
                    actual_ratio_after_snap = snapped_quantity / quantity_before_snap
                    
                    calories_before_snap = food_item.get("nutrients", {}).get("Calories", 0) * item_scaling_ratio
                    calories_after_snap = food_item.get("nutrients", {}).get("Calories", 0) * actual_ratio_after_snap
                    calorie_diff = calories_before_snap - calories_after_snap
                    snapping_calorie_loss += calorie_diff
                    
                    if abs(calorie_diff) > 1.0:
                        logger.debug(f" SNAP: '{food_item['name']}' qty: {scaled_quantity_raw:.3f} → {snapped_quantity:.2f} {unit_extracted} (cal loss: {calorie_diff:+.1f} kcal)")
                    
                    item_scaling_ratio = actual_ratio_after_snap
                
                base_protein = food_item.get("nutrients", {}).get("Protein", 0)
                base_sodium = food_item.get("nutrients", {}).get("Sodium", 0)
                
                multiplier_adjustments = []
                
                if base_protein > 15.0: 
                    max_protein_multiplier = 30.0 / base_protein
                    if item_scaling_ratio > max_protein_multiplier:
                        original_multiplier = item_scaling_ratio
                        item_scaling_ratio = max_protein_multiplier
                        multiplier_adjustments.append(
                            f"Protein cap: {original_multiplier:.2f}x → {item_scaling_ratio:.2f}x "
                            f"(prevents {base_protein * original_multiplier:.1f}g protein spike)"
                        )
                        logger.info(f"Capped '{food_item['name']}' scaling at {round(item_scaling_ratio, 2)}x to prevent >30g protein spike.")
                        logger.info(f"   Base protein: {base_protein:.1f}g × original {original_multiplier:.2f}x = {base_protein * original_multiplier:.1f}g → CAPPED at {base_protein * item_scaling_ratio:.1f}g")
                
                medical_conditions = user_constraints.get('medical_conditions', [])
                has_diabetes = any('diabetes' in str(cond).lower() for cond in medical_conditions)
                has_hypertension = any('hypertension' in str(cond).lower() or 'high blood pressure' in str(cond).lower() for cond in medical_conditions)
                has_kidney_issues = any('kidney' in str(cond).lower() or 'renal' in str(cond).lower() or 'ckd' in str(cond).lower() for cond in medical_conditions)
                
                if has_diabetes or has_hypertension or has_kidney_issues:
                    max_allowed_sodium_per_item = 350.0
                    risk_conditions = []
                    if has_diabetes:
                        risk_conditions.append("Diabetes")
                    if has_hypertension:
                        risk_conditions.append("Hypertension")
                    if has_kidney_issues:
                        risk_conditions.append("Kidney Issues")
                    logger.info(f"CLINICAL SODIUM RESTRICTION: 350mg limit enforced for {', '.join(risk_conditions)}")
                else:
                    max_allowed_sodium_per_item = 600.0
                
                if base_sodium > max_allowed_sodium_per_item:
                    forced_shrink_ratio = max_allowed_sodium_per_item / base_sodium
                    if item_scaling_ratio > forced_shrink_ratio:
                        original_multiplier = item_scaling_ratio
                        item_scaling_ratio = forced_shrink_ratio
                        multiplier_adjustments.append(
                            f" SODIUM GUILLOTINE (Shrink): {original_multiplier:.2f}x → {item_scaling_ratio:.2f}x "
                            f"(forced {base_sodium:.0f}mg → {base_sodium * item_scaling_ratio:.0f}mg)"
                        )
                        logger.warning(f" SODIUM GUILLOTINE: Shrank '{food_item['name']}' to {round(item_scaling_ratio, 2)}x to force sodium below {max_allowed_sodium_per_item:.0f}mg.")
                        logger.warning(f"   Base sodium: {base_sodium:.0f}mg (TOXIC) → Forced to {base_sodium * item_scaling_ratio:.0f}mg")
                
                elif base_sodium > 0:
                    max_growth_multiplier = max_allowed_sodium_per_item / base_sodium
                    if item_scaling_ratio > max_growth_multiplier:
                        original_multiplier = item_scaling_ratio
                        item_scaling_ratio = max_growth_multiplier
                        multiplier_adjustments.append(
                            f" Sodium cap: {original_multiplier:.2f}x → {item_scaling_ratio:.2f}x "
                            f"(prevents {base_sodium * original_multiplier:.0f}mg sodium spike)"
                        )
                        logger.info(f" Capped '{food_item['name']}' scaling to prevent >{max_allowed_sodium_per_item:.0f}mg sodium spike.")
                        logger.info(f"   Base sodium: {base_sodium:.0f}mg × original {original_multiplier:.2f}x = {base_sodium * original_multiplier:.0f}mg → CAPPED at {base_sodium * item_scaling_ratio:.0f}mg")
                
                if multiplier_adjustments:
                    logger.info(f"    MACRO CEILING ENFORCEMENT for '{food_item['name']}':")
                    for adjustment in multiplier_adjustments:
                        logger.info(f"      {adjustment}")
                
                food_item['name'] = self._adjust_serving_size_in_name(food_item['name'], item_scaling_ratio)
                

                for nutrient, value in food_item.get("nutrients", {}).items():
                    if isinstance(value, (int, float)):
                        scaled_value = value * item_scaling_ratio
                        food_item["nutrients"][nutrient] = scaled_value
                

                nutrients = food_item.get("nutrients", {})
                portion_weight = nutrients.get("Portion Weight", 0)
                protein = nutrients.get("Protein", 0)
                carbs = nutrients.get("Carbohydrates", 0)
                fat = nutrients.get("Fat", 0)
                fiber = nutrients.get("Fiber", 0)
                
                macro_weight = protein + carbs + fat + fiber
                
                if portion_weight > 0 and macro_weight > 0:
                    macro_density = macro_weight / portion_weight
                    
                    if macro_density > 0.95:

                        corrected_portion_weight = macro_weight * 1.05
                        logger.warning(f"⚖️ PHYSICS AUTO-CORRECTION: '{food_item['name']}'")
                        logger.warning(f"   Macro density: {macro_density*100:.1f}% (macros={macro_weight:.1f}g, portion={portion_weight:.1f}g)")
                        logger.warning(f"   Adjusted Portion Weight: {portion_weight:.1f}g → {corrected_portion_weight:.1f}g")
                        food_item["nutrients"]["Portion Weight"] = round(corrected_portion_weight, 2)
                
                nutrients = food_item.get("nutrients", {})
                calories = nutrients.get("Calories", 0)
                protein = nutrients.get("Protein", 0)
                carbs = nutrients.get("Carbohydrates", 0)
                fat = nutrients.get("Fat", 0)
                
                calculated_calories = (protein * 4) + (carbs * 4) + (fat * 9)
                calorie_error = abs(calories - calculated_calories)
                calorie_error_pct = (calorie_error / calories * 100) if calories > 0 else 0
                

                if calorie_error_pct > 20 and calculated_calories > 0:
                    logger.error(f" MACRO-CALORIE LOCK VIOLATION: '{food_item['name']}'")
                    logger.error(f"   Reported: {calories:.0f} kcal | Physics: {calculated_calories:.0f} kcal | Error: {calorie_error_pct:.1f}%")
                    logger.error(f"   P:{protein:.1f}g C:{carbs:.1f}g F:{fat:.1f}g | Scale ratio: {item_scaling_ratio:.3f}x")
                    food_item["nutrients"]["Calories"] = calculated_calories
                    logger.warning(f"    CORRECTED: Overriding calories to {calculated_calories:.0f} kcal (physics-based)")
                elif calculated_calories == 0 and calories > 0:
                    logger.debug(f"    TRUST BASE CALORIES: '{food_item['name']}' has {calories:.0f} kcal but 0 macros - keeping calorie value")
                
                scaled_protein = food_item.get("nutrients", {}).get("Protein", 0)
                scaled_sodium = food_item.get("nutrients", {}).get("Sodium", 0)
                
                projected_protein = meal_running_protein + scaled_protein
                projected_sodium = meal_running_sodium + scaled_sodium
                
                DO_NOT_SCALE = ['spinach', 'salad', 'fruit', 'nut', 'seed', 'oil', 'berry', 'apple', 
                               'dressing', 'almond', 'chia', 'flax', 'hemp', 'greens', 'lettuce', 
                               'tomato', 'carrot', 'cucumber', 'melon', 'grape', 'smoothie', 'oats', 
                               'broccoli', 'asparagus', 'mushroom', 'vegetable', 'veg']
                
                food_name_lower = food_item.get('name', '').lower()
                base_calories = food_item.get("nutrients", {}).get("Calories", 0) / item_scaling_ratio if item_scaling_ratio > 0 else 0
                is_garnish = any(kw in food_name_lower for kw in DO_NOT_SCALE) or base_calories < 50
                
                PROTEIN_CEILING_GRAMS = (target_calories * (PROTEIN_PERCENTAGE_CEILING / 100)) / 4
                logger.debug(f"   Protein ceiling for {meal_name}: {PROTEIN_CEILING_GRAMS:.1f}g (35% of {target_calories:.0f} kcal)")
                
                if projected_protein > PROTEIN_CEILING_GRAMS:
                    protein_overage = projected_protein - PROTEIN_CEILING_GRAMS
                    allowed_protein = max(0, PROTEIN_CEILING_GRAMS - meal_running_protein)
                    clamp_ratio = allowed_protein / scaled_protein if scaled_protein > 0 else 0
                    
                    if is_garnish:
                        logger.info(f" GARNISH IMMUNITY: '{food_item['name']}' exempt from protein ceiling (multiplier locked at 1.0x)")
                        clamp_ratio = 1.0
                    elif clamp_ratio < 0.5:
                        logger.warning(f"ANTI-GHOST FLOOR: '{food_item['name']}' clamp ratio {clamp_ratio:.2f}x raised to 0.5x minimum")
                        clamp_ratio = 0.5
                    
                    if clamp_ratio < 1.0:
                        logger.warning(f" PROTEIN CEILING: {meal_name} would exceed {PROTEIN_CEILING_GRAMS:.1f}g protein (35% of {target_calories:.0f} kcal)")
                        logger.warning(f"   Item: '{food_item['name']}' - Clamping protein contribution")
                        logger.warning(f"   Running: {meal_running_protein:.1f}g + This item: {scaled_protein:.1f}g = {projected_protein:.1f}g > {PROTEIN_CEILING_GRAMS:.1f}g")
                        logger.warning(f"   Applying {clamp_ratio:.2f}x clamp to this item")
                        
                        for nutrient, value in food_item.get("nutrients", {}).items():
                            if isinstance(value, (int, float)):
                                scaled_value = value * clamp_ratio
                                food_item["nutrients"][nutrient] = scaled_value
                        
                        calories_lost = food_item.get("nutrients", {}).get("Calories", 0) * (1 - clamp_ratio)
                        meal_deficit += calories_lost
                        
                        scaled_protein = food_item.get("nutrients", {}).get("Protein", 0)
                        scaled_sodium = food_item.get("nutrients", {}).get("Sodium", 0)
                
                elif projected_sodium > SODIUM_CEILING_PER_MEAL:
                    sodium_overage = projected_sodium - SODIUM_CEILING_PER_MEAL
                    allowed_sodium = max(0, SODIUM_CEILING_PER_MEAL - meal_running_sodium)
                    clamp_ratio = allowed_sodium / scaled_sodium if scaled_sodium > 0 else 0
                    
                    if is_garnish:
                        logger.info(f" GARNISH IMMUNITY: '{food_item['name']}' exempt from sodium ceiling (multiplier locked at 1.0x)")
                        clamp_ratio = 1.0
                    elif clamp_ratio < 0.5:
                        logger.warning(f"ANTI-GHOST FLOOR: '{food_item['name']}' clamp ratio {clamp_ratio:.2f}x raised to 0.5x minimum")
                        clamp_ratio = 0.5
                    
                    if clamp_ratio < 1.0:
                        logger.warning(f" SODIUM CEILING: {meal_name} would exceed 600mg sodium")
                        logger.warning(f"   Item: '{food_item['name']}' - Clamping sodium contribution")
                        logger.warning(f"   Running: {meal_running_sodium:.0f}mg + This item: {scaled_sodium:.0f}mg = {projected_sodium:.0f}mg > 600mg")
                        logger.warning(f"   Applying {clamp_ratio:.2f}x clamp to this item")
                        logger.warning(f"   Remaining calories will shift to low-sodium fats/healthy carbs")
                        
                        for nutrient, value in food_item.get("nutrients", {}).items():
                            if isinstance(value, (int, float)):
                                scaled_value = value * clamp_ratio
                                food_item["nutrients"][nutrient] = scaled_value
                        
                        calories_lost = food_item.get("nutrients", {}).get("Calories", 0) * (1 - clamp_ratio)
                        meal_deficit += calories_lost
                        
                        scaled_protein = food_item.get("nutrients", {}).get("Protein", 0)
                        scaled_sodium = food_item.get("nutrients", {}).get("Sodium", 0)
                
                if user_constraints:
                    medical_conditions = user_constraints.get('medical_conditions', [])
                    
                    has_diabetes = any(
                        'diabetes' in str(cond).lower() or 
                        'type 2 diabetes' in str(cond).lower() or
                        'insulin resistance' in str(cond).lower()
                        for cond in medical_conditions
                    )
                    
                    has_pcos = any(
                        'pcos' in str(cond).lower() or
                        'polycystic' in str(cond).lower()
                        for cond in medical_conditions
                    )
                    
                    carb_pct_ceiling = None
                    condition_name = None
                    
                    if has_diabetes:
                        carb_pct_ceiling = 30.0 
                        condition_name = "Type 2 Diabetes"
                    elif has_pcos:
                        carb_pct_ceiling = 40.0  
                        condition_name = "PCOS"
                    
                    if carb_pct_ceiling:
                        scaled_carbs = food_item.get("nutrients", {}).get("Carbohydrates", 0)
                        scaled_protein_check = food_item.get("nutrients", {}).get("Protein", 0)
                        scaled_fat = food_item.get("nutrients", {}).get("Fat", 0)
                        
                        projected_meal_carbs = sum(
                            item.get("nutrients", {}).get("Carbohydrates", 0) 
                            for item in scaled_plan_json["meal_plan"][meal_name]
                        )
                        projected_meal_protein = sum(
                            item.get("nutrients", {}).get("Protein", 0) 
                            for item in scaled_plan_json["meal_plan"][meal_name]
                        )
                        projected_meal_fat = sum(
                            item.get("nutrients", {}).get("Fat", 0) 
                            for item in scaled_plan_json["meal_plan"][meal_name]
                        )
                        
                        projected_meal_cals = (projected_meal_protein * 4) + (projected_meal_carbs * 4) + (projected_meal_fat * 9)
                        
                        if projected_meal_cals > 0:
                            carb_calories = projected_meal_carbs * 4
                            carb_pct = (carb_calories / projected_meal_cals) * 100
                            
                            if carb_pct > carb_pct_ceiling:
                                logger.warning(f" RULE 3: MACRO CEILING VIOLATED - {meal_name} carb percentage: {carb_pct:.1f}% > {carb_pct_ceiling}% ({condition_name} limit)")
                                
                                food_name_lower = food_item.get('name', '').lower()
                                is_carb_item = scaled_carbs > (scaled_protein_check + scaled_fat)
                                
                                if is_carb_item:
                                    max_allowed_carb_cals = projected_meal_cals * (carb_pct_ceiling / 100)
                                    current_carb_cals = projected_meal_carbs * 4
                                    excess_carb_cals = current_carb_cals - max_allowed_carb_cals
                                    
                                    if scaled_carbs > 0:
                                        max_carbs_this_item = scaled_carbs - (excess_carb_cals / 4)
                                        carb_clamp_ratio = max(0.25, max_carbs_this_item / scaled_carbs) 
                                        
                                        logger.warning(f"    AGGRESSIVE CARB CLAMPING: '{food_item['name']}' from {scaled_carbs:.1f}g to {max_carbs_this_item:.1f}g carbs")
                                        logger.warning(f"   Clamp ratio: {carb_clamp_ratio:.2f}x (shifts {excess_carb_cals:.0f} kcal to fats/proteins)")
                                        
                                        for nutrient, value in food_item.get("nutrients", {}).items():
                                            if isinstance(value, (int, float)):
                                                scaled_value = value * carb_clamp_ratio
                                                if nutrient == "Portion Weight":
                                                    scaled_value = max(2.8, scaled_value)
                                                food_item["nutrients"][nutrient] = scaled_value
                                        
                                        calories_lost = food_item.get("nutrients", {}).get("Calories", 0) * (1 - carb_clamp_ratio)
                                        meal_deficit += calories_lost
                                        
                                        logger.info(f"    RULE 2: Aggressive carb re-balancing applied. Target ~{carb_pct_ceiling}% carbs (deficit: {calories_lost:.0f} kcal → compensate with fats/proteins)")
                                else:
                                    logger.debug(f"   Current food '{food_item['name']}' is not a carb item - not clamping")
                
                meal_running_protein += scaled_protein
                meal_running_sodium += scaled_sodium
            
            
            current_meal_cals = sum(f.get('nutrients', {}).get('Calories', 0) for f in scaled_plan_json["meal_plan"][meal_name])
            local_meal_deficit = target_calories - current_meal_cals
            

            max_items = 2 if 'Snack' in meal_name else (3 if 'Breakfast' in meal_name else 4)
            
            if local_meal_deficit > 30.0:
                logger.info(f" CULINARY INTEGRITY: {meal_name} has {local_meal_deficit:.0f} kcal deficit after portion capping")
                logger.info(f"   Caloric deficit accepted to preserve AI culinary combination (no hardcoded item injection)")
                
                if len(scaled_plan_json["meal_plan"][meal_name]) < max_items:
                    flexible_items = [f for f in scaled_plan_json["meal_plan"][meal_name] 
                                     if f.get('nutrients', {}).get('Carbohydrates', 0) > 5 or 
                                        f.get('nutrients', {}).get('Fat', 0) > 3]
                    
                    if flexible_items and len(flexible_items) > 0:
                        deficit_per_item = local_meal_deficit / len(flexible_items)
                        MAX_PORTION_OZ = 10.0 
                        for f in flexible_items:
                            old_cals = f.get('nutrients', {}).get('Calories', 0)
                            if old_cals > 20: 
                                desired_ratio = (old_cals + deficit_per_item) / old_cals if old_cals > 0 else 1.0
                                
                                current_portion_oz = f.get('nutrients', {}).get('Portion Weight', 0) / 28.35 
                                desired_portion_oz = current_portion_oz * desired_ratio
                                
                                if desired_portion_oz > MAX_PORTION_OZ:
                                    actual_ratio = MAX_PORTION_OZ / current_portion_oz if current_portion_oz > 0 else 1.0
                                    calories_absorbed = old_cals * (actual_ratio - 1.0)
                                    calories_spilled = deficit_per_item - calories_absorbed
                                    
                                    logger.warning(f" PORTION CAP ENFORCED: {f['name']}")
                                    logger.warning(f"   Desired: {desired_portion_oz:.1f} oz → Clamped: {MAX_PORTION_OZ} oz (~280g physical limit)")
                                    logger.warning(f"   Absorbed: {calories_absorbed:.0f} kcal | Spilled: {calories_spilled:.0f} kcal")
                                    
                                    ratio = actual_ratio
                                else:
                                    ratio = desired_ratio
                                
                                for nutrient, value in f.get('nutrients', {}).items():
                                    if isinstance(value, (int, float)):
                                        f['nutrients'][nutrient] = round(value * ratio, 2) if nutrient != 'Calories' else int(value * ratio)
                                
                                f['name'] = self._adjust_serving_size_in_name(f['name'], ratio)
                        
                        logger.info(f" LOCAL EXPANSION: Scaled existing items in {meal_name} to absorb up to {int(local_meal_deficit)} kcal deficit (within 10oz cap).")
                else:
                    flexible_items = [f for f in scaled_plan_json["meal_plan"][meal_name] 
                                     if f.get('nutrients', {}).get('Carbohydrates', 0) > 5 or 
                                        f.get('nutrients', {}).get('Fat', 0) > 3]
                    
                    if flexible_items and len(flexible_items) > 0:
                        deficit_per_item = local_meal_deficit / len(flexible_items)
                        MAX_PORTION_OZ = 10.0
                        
                        for f in flexible_items:
                            old_cals = f.get('nutrients', {}).get('Calories', 0)
                            if old_cals > 20:
                                desired_ratio = (old_cals + deficit_per_item) / old_cals if old_cals > 0 else 1.0
                                current_portion_oz = f.get('nutrients', {}).get('Portion Weight', 0) / 28.35
                                desired_portion_oz = current_portion_oz * desired_ratio
                                
                                if desired_portion_oz > MAX_PORTION_OZ:
                                    actual_ratio = MAX_PORTION_OZ / current_portion_oz if current_portion_oz > 0 else 1.0
                                    calories_absorbed = old_cals * (actual_ratio - 1.0)
                                    calories_spilled = deficit_per_item - calories_absorbed
                                    
                                    logger.warning(f"⚖️ PORTION CAP ENFORCED: {f['name']}")
                                    logger.warning(f"   Desired: {desired_portion_oz:.1f} oz → Clamped: {MAX_PORTION_OZ} oz (~280g physical limit)")
                                    logger.warning(f"   Absorbed: {calories_absorbed:.0f} kcal | Spilled: {calories_spilled:.0f} kcal")
                                    
                                    ratio = actual_ratio
                                else:
                                    ratio = desired_ratio
                                
                                for nutrient, value in f.get('nutrients', {}).items():
                                    if isinstance(value, (int, float)):
                                        f['nutrients'][nutrient] = round(value * ratio, 2) if nutrient != 'Calories' else int(value * ratio)
                                
                                f['name'] = self._adjust_serving_size_in_name(f['name'], ratio)
                        
                        logger.info(f" LOCAL EXPANSION: Scaled existing items in {meal_name} to absorb remaining {int(local_meal_deficit)} kcal deficit.")
            
            current_meal_cals = sum(f.get('nutrients', {}).get('Calories', 0) for f in scaled_plan_json["meal_plan"][meal_name])
            local_meal_deficit = target_calories - current_meal_cals
            
            if local_meal_deficit > 100.0 and generator is not None:
                logger.warning(f" DYNAMIC BRIDGING TRIGGERED: {meal_name} deficit = {local_meal_deficit:.0f} kcal after volume caps")
                
                if len(scaled_plan_json["meal_plan"][meal_name]) < max_items:
                    dense_bridger = generator.get_dynamic_dense_bridger()
                    
                    if dense_bridger:
                        bridger_base_cals = dense_bridger.get('nutrients', {}).get('Calories', 0)
                        
                        if bridger_base_cals > 0:
                            desired_ratio = local_meal_deficit / bridger_base_cals
                            
                            bridger_portion_oz = dense_bridger.get('nutrients', {}).get('Portion Weight', 0) / 28.35
                            desired_portion_oz = bridger_portion_oz * desired_ratio
                            MAX_BRIDGER_OZ = 10.0
                            
                            if desired_portion_oz > MAX_BRIDGER_OZ:
                                ratio = MAX_BRIDGER_OZ / bridger_portion_oz if bridger_portion_oz > 0 else 1.0
                                logger.warning(f"    BRIDGER PORTION CAP: {desired_portion_oz:.1f} oz → {MAX_BRIDGER_OZ} oz")
                            else:
                                ratio = desired_ratio
                            
                            bridger_fat = dense_bridger.get('nutrients', {}).get('Fat', 0) * ratio
                            target_tdee = sum(meal_calorie_distribution.values())
                            daily_fat_limit = (target_tdee * 0.45) / 9.0 
                            
                            current_daily_fat = sum(
                                f.get('nutrients', {}).get('Fat', 0)
                                for meal_foods in scaled_plan_json["meal_plan"].values()
                                for f in meal_foods
                            )
                            
                            if (current_daily_fat + bridger_fat) > daily_fat_limit:
                                allowed_fat = daily_fat_limit - current_daily_fat
                                if allowed_fat > 0:
                                    fat_ratio = allowed_fat / (dense_bridger.get('nutrients', {}).get('Fat', 0) * ratio)
                                    ratio = ratio * fat_ratio
                                    logger.warning(f"    BRIDGER FAT CAP: Clamped to stay within 45% daily fat limit")
                                else:
                                    logger.warning(f"    BRIDGER BLOCKED: Already at 45% daily fat limit")
                                    dense_bridger = None 
                            
                            if dense_bridger:  
                                bridger_sodium = dense_bridger.get('nutrients', {}).get('Sodium', 0) * ratio
                                medical_conditions = user_constraints.get('medical_conditions', [])
                                has_diabetes = any('diabetes' in str(cond).lower() for cond in medical_conditions)
                                max_sodium_per_item = 350.0 if has_diabetes else 600.0
                                
                                if bridger_sodium > max_sodium_per_item:
                                    sodium_ratio = max_sodium_per_item / (dense_bridger.get('nutrients', {}).get('Sodium', 0) * ratio) if dense_bridger.get('nutrients', {}).get('Sodium', 0) > 0 else 1.0
                                    ratio = ratio * sodium_ratio
                                    logger.warning(f"    BRIDGER SODIUM CAP: Clamped to {max_sodium_per_item:.0f}mg limit")
                                
                                for nutrient, value in dense_bridger.get('nutrients', {}).items():
                                    if isinstance(value, (int, float)):
                                        dense_bridger['nutrients'][nutrient] = round(value * ratio, 2) if nutrient != 'Calories' else int(value * ratio)
                                
                                dense_bridger['name'] = self._adjust_serving_size_in_name(dense_bridger['name'], ratio)
                                
                                scaled_plan_json["meal_plan"][meal_name].append(dense_bridger)
                                
                                absorbed_cals = dense_bridger.get('nutrients', {}).get('Calories', 0)
                                logger.info(f"    DYNAMIC BRIDGING: Injected '{dense_bridger['name']}' to absorb {absorbed_cals:.0f} kcal")
                                logger.info(f"   Bridger macros: Fat={dense_bridger.get('nutrients', {}).get('Fat', 0):.1f}g, "
                                           f"Sodium={dense_bridger.get('nutrients', {}).get('Sodium', 0):.0f}mg")
                    else:
                        logger.warning(f"    DYNAMIC BRIDGING: No dense items available from database")
                else:
                    logger.info(f"   📏 PLATE AT CAPACITY: Cannot inject bridger ({len(scaled_plan_json['meal_plan'][meal_name])}/{max_items} items)")
            
            
            for idx in reversed(items_to_remove):
                scaled_plan_json["meal_plan"][meal_name].pop(idx)
            if meal_deficit > 0:
                meal_calorie_deficits[meal_name] = meal_deficit
            
            if snapping_calorie_loss > 0:
                meal_calorie_deficits[meal_name] = meal_calorie_deficits.get(meal_name, 0) + snapping_calorie_loss
                logger.info(f" SNAPPING LOSS for {meal_name}: {snapping_calorie_loss:.1f} kcal (will be compensated in true-up)")
        
        if foods_clamped:
            total_deficit = sum(meal_calorie_deficits.values())
            logger.warning(f"\n PORTION CLAMPING + SNAPPING SUMMARY:")
            logger.warning(f"   Total foods clamped: {len(foods_clamped)}")
            logger.warning(f"   Total calorie deficit (clamping + snapping): {total_deficit:.0f} kcal")
            logger.warning(f"   Deficit by meal: {meal_calorie_deficits}")
            logger.warning(f"   Sample clamped foods: {[f['food'] for f in foods_clamped[:3]]}")
        elif meal_calorie_deficits:
            total_deficit = sum(meal_calorie_deficits.values())
            logger.info(f"\n SNAPPING DEFICIT SUMMARY:")
            logger.info(f"   Total calorie loss from snapping: {total_deficit:.0f} kcal")
            logger.info(f"   Deficit by meal: {meal_calorie_deficits}")
        
        logger.info("Scaling complete. Recalculating all plan totals and summaries.")
        final_recalculated_plan = self._recalculate_plan_json(scaled_plan_json)
        target_tdee = sum(meal_calorie_distribution.values())
        final_recalculated_plan = self._true_up_plan_calories(
            final_recalculated_plan, 
            target_tdee, 
            meal_calorie_deficits=meal_calorie_deficits,
            foods_clamped_list=foods_clamped,
            user_constraints=user_constraints
        )

        
        meal_plan = final_recalculated_plan.get("meal_plan", {})
        final_tdee = final_recalculated_plan.get("total_nutrients", {}).get("Calories", 0)
        
        actual_distribution = {}
        for meal_name in ["Breakfast", "Morning Snack", "Lunch", "Evening Snack", "Dinner"]:
            if meal_name in meal_plan:
                meal_calories = sum(
                    item.get("nutrients", {}).get("Calories", 0)
                    for item in meal_plan[meal_name]
                )
                actual_distribution[meal_name] = meal_calories
                target_calories = meal_calorie_distribution.get(meal_name, 0)
                deviation = meal_calories - target_calories
                pct_of_tdee = (meal_calories / final_tdee * 100) if final_tdee > 0 else 0
                
                logger.info(f"   {meal_name}: {meal_calories:.0f} kcal ({pct_of_tdee:.1f}% of TDEE) | Target: {target_calories} kcal | Deviation: {deviation:+.0f} kcal")
        
        dinner_actual = actual_distribution.get("Dinner", 0)
        lunch_actual = actual_distribution.get("Lunch", 0)
        breakfast_actual = actual_distribution.get("Breakfast", 0)
        
        logger.info(f"\nDINNER LIGHTNESS CHECK:")
        if dinner_actual <= lunch_actual:
            logger.info(f"    PASS: Dinner ({dinner_actual:.0f} kcal) ≤ Lunch ({lunch_actual:.0f} kcal)")
        else:
            logger.warning(f"    WARNING: Dinner ({dinner_actual:.0f} kcal) > Lunch ({lunch_actual:.0f} kcal)")
        
        if abs(dinner_actual - breakfast_actual) < 50:
            logger.info(f"    PASS: Dinner ({dinner_actual:.0f} kcal) ≈ Breakfast ({breakfast_actual:.0f} kcal)")
        
        logger.info(f"\n   Final Daily Total: {final_tdee:.0f} kcal | Target TDEE: {target_tdee} kcal")
        logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
        
        
        return final_recalculated_plan
    
    def _true_up_plan_calories(self, plan_data: Dict, target_tdee: int, meal_calorie_deficits: Dict[str, float] = None, foods_clamped_list: list = None, user_constraints: Dict = None) -> Dict:
        user_constraints = user_constraints or {}
        meal_calorie_deficits = meal_calorie_deficits or {}
        foods_clamped_list = foods_clamped_list or []
        clamped_food_names = {item['food'] for item in foods_clamped_list}
        current_total = plan_data.get("total_nutrients", {}).get("Calories", 0)
        calorie_gap = target_tdee - current_total
        total_deficit = sum(meal_calorie_deficits.values())
        
        if abs(calorie_gap) <= 25.0 and total_deficit == 0:
            logger.info(f"True-up check: Plan within acceptable tolerance ({current_total:.1f} vs {target_tdee} kcal, gap: {calorie_gap:+.1f} kcal)")
            return plan_data
        
        logger.info(f" True-up ACTIVE: Gap analysis - {calorie_gap:+.1f} kcal gap + {total_deficit:.0f} kcal deficit from clamping")
        logger.info(f"   Current: {current_total:.1f} kcal → Target: {target_tdee} kcal")
        
        
        if total_deficit > 0:
            logger.info(f"\n CULINARY INTEGRITY: {total_deficit:.0f} kcal deficit detected from portion capping")
            logger.info(f"    Caloric deficit accepted to preserve AI culinary combinations (no redistribution)")
            logger.info(f"   Deficit source: MAX_PORTION_OZ (10oz cap) enforced on flexible items")
            
            plan_data = self._recalculate_plan_json(plan_data)
            current_total = plan_data.get("total_nutrients", {}).get("Calories", 0)
            final_gap = target_tdee - current_total
            
            logger.info(f" Final plan totals: {current_total:.1f} kcal (gap: {final_gap:+.1f} kcal)")
            logger.info(f"   Gap accepted within tolerance to maintain culinary authenticity")
            
            return plan_data
        
        logger.info(f" CULINARY INTEGRITY: Accepting {calorie_gap:+.1f} kcal gap to preserve meal combinations")
        return plan_data
    
    def _get_optimizer_targets(self, plan_data, clamped_food_names, total_deficit, target_tdee, user_constraints): 
        DO_NOT_SCALE = ['dressing', 'almond', 'chia', 'flax', 'hemp', 'greens', 'lettuce', 
                        'tomato', 'carrot', 'cucumber', 'melon', 'grape', 'smoothie', 'oats', 
                        'broccoli', 'asparagus']
        
        meal_plan = plan_data.get("meal_plan", {})
        dense_food_candidates = []
        for meal_name, foods in meal_plan.items():
            for idx, food in enumerate(foods):
                food_name = food['name']
                if food_name in clamped_food_names:
                    continue
                    
                    food_name_lower = food_name.lower()
                    
                    if any(kw in food_name_lower for kw in DO_NOT_SCALE):
                        logger.debug(f"    GARNISH LOCK: {food_name} - multiplier locked at 1.0x (DO_NOT_SCALE list)")
                        continue
                    
                    food_cals = food.get("nutrients", {}).get("Calories", 0)
                    if food_cals < 50:
                        logger.debug(f"    LOW-CAL LOCK: {food_name} ({food_cals:.1f} kcal) - multiplier locked at 1.0x")
                        continue
                    
                    is_zero_cal_liquid = any(
                        liquid_term in food_name_lower 
                        for liquid_term in ['tea', 'water', 'coffee', 'black coffee', 'green tea', 'herbal tea']
                    )
                    if is_zero_cal_liquid:
                        continue
                    
                    is_low_calorie_vegetable = any(
                        veg in food_name_lower for veg in [
                            'kale', 'spinach', 'lettuce', 'salad', 'cabbage', 'chard',
                            'arugula', 'watercress', 'celery', 'cucumber', 'zucchini',
                            'broccoli', 'cauliflower', 'asparagus'
                        ]
                    )
                    
                    is_legume_or_bean = any(
                        legume in food_name_lower for legume in [
                            'edamame', 'lentil', 'dal', 'bean', 'chickpea', 'legume',
                            'peas', 'soybean', 'black gram', 'urad', 'moong', 'masoor',
                            'chana', 'kidney bean', 'pinto bean', 'black bean'
                        ]
                    )
                    
                    nutrients = food.get("nutrients", {})
                    portion_weight_oz = nutrients.get("Portion Weight", 0)
                    food_cals = nutrients.get("Calories", 0)
                    
                    if portion_weight_oz > 0:
                        weight_grams = portion_weight_oz * 28.35
                        calories_per_100g = (food_cals / weight_grams) * 100
                        if calories_per_100g < 50:
                            logger.debug(f"    EXCLUDED from Cap-and-Spill: {food_name} ({calories_per_100g:.1f} kcal/100g - too low density)")
                            continue
                    
                    if is_low_calorie_vegetable:
                        logger.debug(f"    EXCLUDED from Cap-and-Spill: {food_name} (low-calorie vegetable)")
                        continue
                    
                    if is_legume_or_bean:
                        logger.debug(f"    EXCLUDED from Cap-and-Spill: {food_name} (legume/bean - clamped separately)")
                        continue
                    
                    is_dense = self._is_calorie_dense_food(food_name)
                    if food_cals > 20: 
                        priority = 1 if is_dense else 2  
                        dense_food_candidates.append({
                            'meal': meal_name,
                            'idx': idx,
                            'food': food,
                            'calories': food_cals,
                            'priority': priority,
                            'is_dense': is_dense
                        })
            
            dense_food_candidates.sort(key=lambda x: (x['priority'], -x['calories']))
            
            if dense_food_candidates:
                sensible_limits = self._get_sensible_portion_limits()
                remaining_deficit = total_deficit
                frozen_food_indices = set()  
                MAX_ITERATIONS = 10  
                iteration = 0
                
                logger.info(f"Starting Cap-and-Spill redistribution: {remaining_deficit:.0f} kcal to distribute")
                
                while remaining_deficit > 10 and iteration < MAX_ITERATIONS:
                    iteration += 1
                    unfrozen_foods = [
                        f for idx, f in enumerate(dense_food_candidates) 
                        if idx not in frozen_food_indices
                    ]
                    
                    if not unfrozen_foods:
                        logger.warning(f"All foods frozen at ceiling. Accepting {remaining_deficit:.0f} kcal deficit.")
                        break
                    
                    num_foods_to_boost = min(3, len(unfrozen_foods))
                    selected_foods = unfrozen_foods[:num_foods_to_boost]
                    total_selected_cals = sum(f['calories'] for f in selected_foods)
                    spill_from_this_round = 0.0 
                    logger.info(f"   Iteration {iteration}: Distributing {remaining_deficit:.0f} kcal to {num_foods_to_boost} foods")
                    
                    for food_info in selected_foods:
                        boost_ratio = food_info['calories'] / total_selected_cals
                        calories_to_add = remaining_deficit * boost_ratio
                        old_cals = food_info['calories']
                        new_cals = old_cals + calories_to_add
                        desired_adjustment_ratio = new_cals / old_cals
                        food_item = food_info['food']
                        current_qty, current_unit = self._extract_portion_from_name(food_item['name'])
                        
                        if current_qty and current_unit:
                            desired_qty = current_qty * desired_adjustment_ratio
                            
                            max_limit = sensible_limits.get(current_unit.lower())
                            
                            is_legume_or_bean_item = any(
                                legume in food_item['name'].lower() for legume in [
                                    'edamame', 'lentil', 'dal', 'bean', 'chickpea', 'legume',
                                    'peas', 'soybean', 'black gram', 'urad', 'moong', 'masoor',
                                    'chana', 'kidney bean', 'pinto bean', 'black bean'
                                ]
                            )
                            
                            if is_legume_or_bean_item and current_unit.lower() in ['cup', 'cups'] and desired_qty > 1.0:
                                clamped_ratio = 1.0 / current_qty
                                actual_adjustment_ratio = clamped_ratio
                                clamped_cals = old_cals * actual_adjustment_ratio
                                absorbed_calories = clamped_cals - old_cals
                                spilled_calories = calories_to_add - absorbed_calories
                                food_idx = dense_food_candidates.index(food_info)
                                frozen_food_indices.add(food_idx)
                                logger.warning(f" LEGUME CLAMP: {food_item['name']}")
                                logger.warning(f"Desired: {desired_qty:.1f} {current_unit} → Clamped: 1.0 cup (GI safety limit)")
                                logger.warning(f"Absorbed: {absorbed_calories:.0f} kcal | Spilled: {spilled_calories:.0f} kcal")
                                spill_from_this_round += spilled_calories
                            elif max_limit and desired_qty > max_limit:
                                clamped_ratio = max_limit / current_qty
                                actual_adjustment_ratio = clamped_ratio
                                clamped_cals = old_cals * actual_adjustment_ratio
                                absorbed_calories = clamped_cals - old_cals
                                spilled_calories = calories_to_add - absorbed_calories
                                food_idx = dense_food_candidates.index(food_info)
                                frozen_food_indices.add(food_idx)
                                logger.warning(f"CLAMPED: {food_item['name']}")
                                logger.warning(f"Desired: {desired_qty:.1f} {current_unit} → Clamped: {max_limit} {current_unit}")
                                logger.warning(f"Absorbed: {absorbed_calories:.0f} kcal | Spilled: {spilled_calories:.0f} kcal")
                                
                                spill_from_this_round += spilled_calories
                            else:
                                actual_adjustment_ratio = desired_adjustment_ratio
                        else:
                            food_name_lower = food_item['name'].lower()
                            is_vegetable = any(veg in food_name_lower for veg in ['kale', 'spinach', 'lettuce', 'chard', 'greens', 'cabbage'])
                            if is_vegetable:
                                current_weight_oz = food_item.get("nutrients", {}).get("Portion Weight", 0)
                                desired_weight_oz = current_weight_oz * desired_adjustment_ratio
                                max_weight_oz = 250 / 28.35  
                                if desired_weight_oz > max_weight_oz:
                                    clamped_ratio = max_weight_oz / current_weight_oz
                                    actual_adjustment_ratio = clamped_ratio
                                    clamped_cals = old_cals * actual_adjustment_ratio
                                    absorbed_calories = clamped_cals - old_cals
                                    spilled_calories = calories_to_add - absorbed_calories
                                    food_idx = dense_food_candidates.index(food_info)
                                    frozen_food_indices.add(food_idx)
                                    logger.warning(f"WEIGHT CLAMPED: {food_item['name']}")
                                    logger.warning(f"Desired: {desired_weight_oz:.1f} oz → Clamped: {max_weight_oz:.1f} oz (~250g)")
                                    logger.warning(f"Absorbed: {absorbed_calories:.0f} kcal | Spilled: {spilled_calories:.0f} kcal")
                                    
                                    spill_from_this_round += spilled_calories
                                else:
                                    actual_adjustment_ratio = desired_adjustment_ratio
                            else:
                                actual_adjustment_ratio = desired_adjustment_ratio
                        food_item['name'] = self._adjust_serving_size_in_name(food_item['name'], actual_adjustment_ratio)
                        for nutrient, value in food_item.get("nutrients", {}).items():
                            if isinstance(value, (int, float)):
                                food_item["nutrients"][nutrient] = value * actual_adjustment_ratio
                        food_info['calories'] = food_item.get("nutrients", {}).get("Calories", old_cals)
                        dense_marker = "" if food_info['is_dense'] else ""
                        actual_added = food_info['calories'] - old_cals
                        logger.info(f"   {dense_marker} {food_item['name']}: +{actual_added:.0f} kcal ({old_cals:.0f} → {food_info['calories']:.0f})")
                    remaining_deficit = spill_from_this_round
                    if spill_from_this_round < 10:
                        logger.info(f"Deficit fully absorbed (remaining: {spill_from_this_round:.0f} kcal)")
                        break
                
                if iteration >= MAX_ITERATIONS:
                    logger.warning(f"Reached max iterations ({MAX_ITERATIONS}). Accepting {remaining_deficit:.0f} kcal gap.")
                plan_data = self._recalculate_plan_json(plan_data)
                current_total = plan_data.get("total_nutrients", {}).get("Calories", 0)
                calorie_gap = target_tdee - current_total
                logger.info(f"After Cap-and-Spill redistribution: {current_total:.1f} kcal (gap: {calorie_gap:+.1f} kcal)")
            else:
                logger.warning(f"No suitable calorie-dense foods found for deficit redistribution")
                if abs(calorie_gap) > 50:
                    logger.info(f"⚡ UNIVERSAL MULTIPLIER: Applying proportional scaling to all protein/fat items to close {calorie_gap:+.1f} kcal gap")
                    
                    meal_plan = plan_data.get("meal_plan", {})
                    
                    HARD_PROTEIN_CAP = 130.0  
                    
                    current_protein_g = 0.0
                    for meal_name, foods in meal_plan.items():
                        for food in foods:
                            current_protein_g += food.get("nutrients", {}).get("Protein", 0)
                    
                    protein_locked_foods = []
                    protein_cap_triggered = False
                    
                    if current_protein_g > HARD_PROTEIN_CAP:
                        protein_cap_triggered = True
                        logger.warning(f" HARD PROTEIN CAP TRIGGERED: {current_protein_g:.1f}g > {HARD_PROTEIN_CAP}g")
                        logger.warning(f"    LOCKING all protein-dominant foods at multiplier 1.0x")
                        logger.warning(f"   ⚡ SHIFTING 100% of calorie deficit to Failsafe Fat Dump")
                        
                        for meal_name, foods in meal_plan.items():
                            for food in foods:
                                protein_g = food.get("nutrients", {}).get("Protein", 0)
                                fat_g = food.get("nutrients", {}).get("Fat", 0)
                                carbs_g = food.get("nutrients", {}).get("Carbohydrates", 0)
                                
                                if protein_g > fat_g and protein_g > carbs_g:
                                    food['locked'] = True
                                    food['protein_locked'] = True  
                                    protein_locked_foods.append(food['name'])
                                    logger.debug(f" PROTEIN LOCK: '{food['name']}' (P:{protein_g:.1f}g > F:{fat_g:.1f}g, C:{carbs_g:.1f}g)")
                        
                        logger.info(f"    Locked {len(protein_locked_foods)} protein-dominant foods")
                        logger.info(f"    Sample locked: {', '.join(protein_locked_foods[:3])}")
                    else:
                        logger.info(f"✓ Daily protein check: {current_protein_g:.1f}g / {HARD_PROTEIN_CAP}g ({current_protein_g/HARD_PROTEIN_CAP*100:.0f}%) - No lock needed")
                    
                    DO_NOT_SCALE = ['spinach', 'salad', 'fruit', 'nut', 'seed', 'oil', 'berry', 'apple', 
                                   'dressing', 'almond', 'chia', 'flax', 'hemp', 'greens', 'lettuce', 
                                   'tomato', 'carrot', 'cucumber', 'melon', 'grape', 'smoothie', 'oats', 
                                   'broccoli', 'asparagus']
                    
                    COMPLEX_CARB_KEYWORDS = [
                        'rice', 'quinoa', 'oats', 'oatmeal', 'barley', 'buckwheat', 'millet', 
                        'farro', 'bulgur', 'couscous', 'pasta', 'noodle', 'bread', 'roti', 
                        'chapati', 'tortilla', 'potato', 'sweet potato', 'yam', 'plantain',
                        'lentil', 'dal', 'bean', 'chickpea', 'legume', 'peas'
                    ]
                    
                    FIBROUS_VEGGIE_KEYWORDS = [
                        'broccoli', 'cauliflower', 'cabbage', 'kale', 'spinach', 'chard', 
                        'collard', 'lettuce', 'arugula', 'watercress', 'bok choy', 'brussels', 
                        'asparagus', 'green beans', 'zucchini', 'cucumber', 'eggplant', 
                        'bell pepper', 'tomato', 'carrot', 'celery', 'mushroom', 'squash'
                    ]
                    
                    protein_fat_items = []
                    low_cal_vegetables = [] 
                    blocked_carbs_veggies = [] 
                    
                    for meal_name, foods in meal_plan.items():
                        for idx, food in enumerate(foods):
                            if food['name'] not in clamped_food_names:
                                food_name_lower = food['name'].lower()
                                
                                if food.get('locked', False):
                                    low_cal_vegetables.append((meal_name, idx, food, 0))
                                    logger.debug(f"DAILY PROTEIN LOCK: '{food['name']}' - multiplier locked at 1.0x (protein-dominant)")
                                    continue
                                
                                is_complex_carb = any(kw in food_name_lower for kw in COMPLEX_CARB_KEYWORDS)
                                if is_complex_carb:
                                    blocked_carbs_veggies.append(food['name'])
                                    logger.debug(f" CARB BLOCK: '{food['name']}' - EXCLUDED from true-up (complex carb)")
                                    continue
                                
                                is_fibrous_veggie = any(kw in food_name_lower for kw in FIBROUS_VEGGIE_KEYWORDS)
                                if is_fibrous_veggie:
                                    blocked_carbs_veggies.append(food['name'])
                                    logger.debug(f" VEGGIE BLOCK: '{food['name']}' - EXCLUDED from true-up (fibrous veggie)")
                                    continue
                                
                                if any(kw in food_name_lower for kw in DO_NOT_SCALE):
                                    low_cal_vegetables.append((meal_name, idx, food, 0))
                                    logger.debug(f" GARNISH LOCK: '{food['name']}' - multiplier locked at 1.0x (DO_NOT_SCALE list)")
                                    continue
                                
                                total_calories = food.get("nutrients", {}).get("Calories", 0)
                                if total_calories < 50:
                                    low_cal_vegetables.append((meal_name, idx, food, 0))
                                    logger.debug(f" LOW-CAL LOCK: '{food['name']}' ({total_calories:.1f} kcal) - multiplier locked at 1.0x")
                                    continue
                                
                                calories_per_100g = 0
                                portion_weight = food.get("nutrients", {}).get("Portion Weight", 100)
                                if portion_weight > 0:
                                    calories_per_100g = (total_calories / portion_weight) * 100
                                

                                if calories_per_100g < 50:
                                    low_cal_vegetables.append((meal_name, idx, food, calories_per_100g))
                                    logger.debug(f"VEGETABLE LOCK: '{food['name']}' ({calories_per_100g:.1f} cal/100g) - multiplier locked at 1.0x")
                                    continue
                                
                                protein_g = food.get("nutrients", {}).get("Protein", 0)
                                fat_g = food.get("nutrients", {}).get("Fat", 0)
                                if protein_g > 5 or fat_g > 3: 
                                    protein_fat_items.append((meal_name, idx, food))
                    
                    if blocked_carbs_veggies:
                        logger.info(f" TRUE-UP CARB/VEGGIE BLOCK: Excluded {len(blocked_carbs_veggies)} complex carbs and fibrous veggies from scaling")
                        logger.info(f"   Sample blocked: {', '.join(blocked_carbs_veggies[:5])}")
                    
                    if protein_fat_items:
                        required_multiplier = 1 + (calorie_gap / current_total)
                        safe_multiplier = min(2.5, max(0.75, required_multiplier)) 
                        
                        if required_multiplier > 2.5:
                            logger.warning(f" MULTIPLIER CAP REACHED: Required {required_multiplier:.2f}x, clamped to 2.5x")
                            logger.warning(f"   Remaining deficit will be shifted to pure fats (olive oil, butter, avocado, nuts)")
                        
                        logger.info(f"Applying {safe_multiplier:.3f}x multiplier to {len(protein_fat_items)} protein/fat items")
                        logger.info(f"   Locked {len(low_cal_vegetables)} low-calorie vegetables at 1.0x (no scaling)")
                        
                        for meal_name, idx, food in protein_fat_items:
                            quantity, unit = self._extract_portion_from_name(food['name'])
                            item_multiplier = safe_multiplier  
                            
                            if quantity and unit:
                                unit_lower = unit.lower()
                                scaled_qty = quantity * safe_multiplier
                                max_qty = None
                                
                                if unit_lower in ['cup', 'cups']:
                                    max_qty = 2.5
                                elif unit_lower in ['tbsp', 'tablespoon', 'tablespoons']:
                                    max_qty = 3.0
                                elif unit_lower in ['oz', 'ozs', 'ounce', 'ounces']:
                                    max_qty = 10.0
                                
                                if max_qty and scaled_qty > max_qty:
                                    max_allowed_multiplier = max_qty / quantity
                                    item_multiplier = min(safe_multiplier, max_allowed_multiplier)
                                    logger.warning(f"FIX #4: Universal multiplier capped for '{food['name']}'")
                                    logger.warning(f"   Would be: {scaled_qty:.2f} {unit} → Capped at: {max_qty} {unit}")
                                    logger.warning(f"   Multiplier: {safe_multiplier:.2f}x → {item_multiplier:.2f}x")
                            
                            food['name'] = self._adjust_serving_size_in_name(food['name'], item_multiplier)
                            
                            for nutrient, value in food.get("nutrients", {}).items():
                                if isinstance(value, (int, float)):
                                    food["nutrients"][nutrient] = value * item_multiplier
                            
                            nutrients = food.get("nutrients", {})
                            calories = nutrients.get("Calories", 0)
                            protein = nutrients.get("Protein", 0)
                            carbs = nutrients.get("Carbohydrates", 0)
                            fat = nutrients.get("Fat", 0)
                            
                            calculated_calories = (protein * 4) + (carbs * 4) + (fat * 9)
                            calorie_error = abs(calories - calculated_calories)
                            calorie_error_pct = (calorie_error / calories * 100) if calories > 0 else 0
                            
                            if calorie_error_pct > 20:
                                logger.error(f" UNIVERSAL MULTIPLIER VIOLATION: '{food['name']}'")
                                logger.error(f"   Reported: {calories:.0f} kcal | Physics: {calculated_calories:.0f} kcal | Error: {calorie_error_pct:.1f}%")
                                food["nutrients"]["Calories"] = calculated_calories
                                logger.warning(f"    CORRECTED: Overriding to {calculated_calories:.0f} kcal")
                        
                        plan_data = self._recalculate_plan_json(plan_data)
                        current_total = plan_data.get("total_nutrients", {}).get("Calories", 0)
                        calorie_gap = target_tdee - current_total
                        logger.info(f"After universal multiplier: {current_total:.1f} kcal (remaining gap: {calorie_gap:+.1f} kcal)")
                        
                        should_inject_fats = (protein_cap_triggered and calorie_gap > 50) or (calorie_gap > 100 and safe_multiplier >= 2.5)
                        
                        if should_inject_fats:
                            reason = "PROTEIN CAP" if protein_cap_triggered else "MULTIPLIER CEILING"
                            logger.warning(f"⚡ FAILSAFE CALORIE DUMP ACTIVATED ({reason})")
                            logger.warning(f"   Remaining calorie gap: {calorie_gap:.0f} kcal")
                            logger.warning(f"   Injecting healthy fats to reach target TDEE")
                            
                            fat_options = [
                                {"name": "Extra Virgin Olive Oil", "cals_per_tbsp": 119, "fat_per_tbsp": 13.5, "carbs": 0.0, "fiber": 0.0},
                                {"name": "Sliced Avocado", "cals_per_half": 160, "fat_per_half": 15.0, "carbs": 8.5, "fiber": 6.7},
                                {"name": "Raw Almonds", "cals_per_oz": 164, "fat_per_oz": 14.2, "carbs": 6.1, "fiber": 3.5},
                                {"name": "Chia Seeds", "cals_per_tbsp": 60, "fat_per_tbsp": 3.7, "carbs": 5.0, "fiber": 4.1}
                            ]
                            
                            def is_fat_safe(fat_name: str) -> bool:
                                fat_lower = fat_name.lower()
                                
                                allergies = user_constraints.get('allergies', [])
                                for allergy in allergies:
                                    allergy_lower = str(allergy).lower()
                                    if any(keyword in fat_lower for keyword in ['nut', 'almond', 'tree nut']) and any(kw in allergy_lower for kw in ['nut', 'almond', 'tree nut']):
                                        logger.warning(f"    SAFETY BLOCK: {fat_name} blocked by allergy: {allergy}")
                                        return False
                                    if 'avocado' in fat_lower and 'avocado' in allergy_lower:
                                        logger.warning(f"    SAFETY BLOCK: {fat_name} blocked by allergy: {allergy}")
                                        return False
                                    if 'seed' in fat_lower and 'seed' in allergy_lower:
                                        logger.warning(f"    SAFETY BLOCK: {fat_name} blocked by allergy: {allergy}")
                                        return False
                                
                                symptom_foods = user_constraints.get('symptom_aggravating_foods', [])
                                for symptom_food in symptom_foods:
                                    symptom_lower = str(symptom_food).lower()
                                    if any(keyword in symptom_lower for keyword in fat_lower.split()):
                                        logger.warning(f"    SAFETY BLOCK: {fat_name} blocked by symptom food: {symptom_food}")
                                        return False
                                
                                restrictions = user_constraints.get('dietary_restrictions', [])
                                for restriction in restrictions:
                                    restriction_lower = str(restriction).lower()
                                    if 'nut' in restriction_lower and any(kw in fat_lower for kw in ['nut', 'almond']):
                                        logger.warning(f"    SAFETY BLOCK: {fat_name} blocked by restriction: {restriction}")
                                        return False
                                    if 'fat' in restriction_lower and 'low-fat' in restriction_lower:
                                        logger.warning(f"    SAFETY BLOCK: {fat_name} blocked by Low-Fat restriction")
                                        return False
                                
                                dietary_pref = user_constraints.get('dietary_preference', '')
                                if isinstance(dietary_pref, list):
                                    dietary_pref = ', '.join(dietary_pref)
                                
                                return True
                            
                            safe_fat_options = [fat for fat in fat_options if is_fat_safe(fat['name'])]
                            
                            if not safe_fat_options:
                                logger.error(f"    CRITICAL: All fat options blocked by user restrictions!")
                                logger.error(f"   Allergies: {user_constraints.get('allergies', [])}")
                                logger.error(f"   Symptom foods: {user_constraints.get('symptom_aggravating_foods', [])}")
                                logger.error(f"   Cannot inject fats - accepting calorie gap of {calorie_gap:.0f} kcal")
                            else:
                                logger.info(f"    {len(safe_fat_options)}/{len(fat_options)} fat options are safe for user")
                                fat_options = safe_fat_options  
                                
                                olive_oil_already_used = False
                                meal_plan = plan_data.get("meal_plan", {})
                                
                                for meal_name, foods in meal_plan.items():
                                    for food in foods:
                                        if "Olive Oil" in food.get("name", ""):
                                            olive_oil_already_used = True
                                            break
                                    if olive_oil_already_used:
                                        break
                                
                                if calorie_gap > 300:
                                    chosen_fat = fat_options[1] if len(fat_options) > 1 else fat_options[0]  
                                    units_needed = calorie_gap / chosen_fat.get("cals_per_half", chosen_fat.get("cals_per_tbsp", 100))
                                    unit_name = "half" if "cals_per_half" in chosen_fat else "tbsp"
                                    fat_per_unit = chosen_fat.get("fat_per_half", chosen_fat.get("fat_per_tbsp"))
                                    carbs_per_unit = chosen_fat.get("carbs", 0.0)
                                    fiber_per_unit = chosen_fat.get("fiber", 0.0)
                                    portion_oz = units_needed * (2.5 if unit_name == "half" else 0.47)
                                    actual_cals = units_needed * chosen_fat.get("cals_per_half", chosen_fat.get("cals_per_tbsp", 100))
                                elif calorie_gap > 150:
                                    chosen_fat = fat_options[2] if len(fat_options) > 2 else fat_options[0]  
                                    units_needed = calorie_gap / chosen_fat.get("cals_per_oz", chosen_fat.get("cals_per_tbsp", 100))
                                    unit_name = "oz" if "cals_per_oz" in chosen_fat else "tbsp"
                                    fat_per_unit = chosen_fat.get("fat_per_oz", chosen_fat.get("fat_per_tbsp"))
                                    carbs_per_unit = chosen_fat.get("carbs", 0.0)
                                    fiber_per_unit = chosen_fat.get("fiber", 0.0)
                                    portion_oz = units_needed if unit_name == "oz" else units_needed * 0.47
                                    actual_cals = units_needed * chosen_fat.get("cals_per_oz", chosen_fat.get("cals_per_tbsp", 100))
                                else:
                                    if not olive_oil_already_used and len(fat_options) > 0 and "Olive Oil" in fat_options[0]['name']:
                                        chosen_fat = fat_options[0]  
                                        units_needed = calorie_gap / chosen_fat.get("cals_per_tbsp", 100)
                                        unit_name = "tbsp"
                                        fat_per_unit = chosen_fat.get("fat_per_tbsp")
                                        carbs_per_unit = chosen_fat.get("carbs", 0.0)
                                        fiber_per_unit = chosen_fat.get("fiber", 0.0)
                                        portion_oz = units_needed * 0.47  
                                        actual_cals = units_needed * chosen_fat.get("cals_per_tbsp", 100)
                                    else:
                                        logger.info(f"    Olive oil unavailable, using alternative fat")
                                        chosen_fat = fat_options[-1] if fat_options else {"name": "Safe Fat", "cals_per_tbsp": 60, "fat_per_tbsp": 5.0, "carbs": 0.0, "fiber": 0.0}
                                        units_needed = calorie_gap / chosen_fat.get("cals_per_tbsp", 60)
                                        unit_name = "tbsp"
                                        fat_per_unit = chosen_fat.get("fat_per_tbsp", 5.0)
                                        carbs_per_unit = chosen_fat.get("carbs", 0.0)
                                        fiber_per_unit = chosen_fat.get("fiber", 0.0)
                                        portion_oz = units_needed * 0.35  
                                        actual_cals = units_needed * chosen_fat.get("cals_per_tbsp", 60)
                                
                                target_meal = None
                                
                                if "Olive Oil" in chosen_fat["name"] and not olive_oil_already_used:
                                    for meal_name, foods in meal_plan.items():
                                        for food in foods:
                                            food_name = food.get("name", "").lower()
                                            if any(keyword in food_name for keyword in ["salad", "greens", "lettuce", "spinach", "arugula", "mixed greens"]):
                                                target_meal = meal_name
                                                logger.info(f"   🥗 Found salad in {meal_name}, adding olive oil as dressing")
                                                break
                                        if target_meal:
                                            break
                                    
                                    if not target_meal:
                                        logger.info(f"    No salad found in meal plan, using alternative fat")
                                        if len(fat_options) > 1:
                                            chosen_fat = fat_options[-1]
                                            units_needed = calorie_gap / chosen_fat.get("cals_per_tbsp", 60)
                                            unit_name = "tbsp"
                                            fat_per_unit = chosen_fat.get("fat_per_tbsp", 5.0)
                                            carbs_per_unit = chosen_fat.get("carbs", 0.0)
                                            fiber_per_unit = chosen_fat.get("fiber", 0.0)
                                            portion_oz = units_needed * 0.35
                                            actual_cals = units_needed * chosen_fat.get("cals_per_tbsp", 60)
                                        largest_meal_cals = 0
                                        for meal_name, foods in meal_plan.items():
                                            meal_cals = sum(food.get("nutrients", {}).get("Calories", 0) for food in foods)
                                            if meal_cals > largest_meal_cals:
                                                largest_meal_cals = meal_cals
                                                target_meal = meal_name
                                else:
                                    largest_meal_cals = 0
                                    for meal_name, foods in meal_plan.items():
                                        meal_cals = sum(food.get("nutrients", {}).get("Calories", 0) for food in foods)
                                        if meal_cals > largest_meal_cals:
                                            largest_meal_cals = meal_cals
                                            target_meal = meal_name
                                
                                if target_meal:
                                    fat_display_name = f"{chosen_fat['name']} (Dressing)" if "Olive Oil" in chosen_fat["name"] else chosen_fat['name']
                                    logger.info(f"   ➜ FAT INJECTION into {target_meal}: {units_needed:.2f} {unit_name} {fat_display_name} ({actual_cals:.0f} kcal)")
                                    
                                    fat_dump_item = {
                                        "name": f"{fat_display_name} ({units_needed:.2f} {unit_name})",
                                        "nutrients": {
                                            "Portion Weight": round(portion_oz * 28.35, 1), 
                                            "Calories": int(actual_cals),
                                            "Protein": 0.0 if "Oil" in chosen_fat["name"] else round(units_needed * 2.0, 1),
                                            "Carbohydrates": round(units_needed * carbs_per_unit, 1),
                                            "Fat": round(units_needed * fat_per_unit, 1),
                                            "Fiber": round(units_needed * fiber_per_unit, 1),
                                            "Sodium": 0.0,
                                            "Sugar": 0.0,
                                            "Cholesterol": 0.0,
                                            "Iodine": 0.0
                                        }
                                    }
                                    
                                    meal_plan[target_meal].append(fat_dump_item)
                                    plan_data = self._recalculate_plan_json(plan_data)
                                    current_total = plan_data.get("total_nutrients", {}).get("Calories", 0)
                                    calorie_gap = target_tdee - current_total
                                    logger.info(f"    Fat dump complete: Total now {current_total:.1f} kcal (gap: {calorie_gap:+.1f} kcal)")
                                else:
                                    logger.warning(f"    No suitable meal found for fat injection")
        
        if abs(calorie_gap) <= 25.0:
            logger.info(f"True-up complete: Plan within acceptable tolerance ({current_total:.1f} kcal, target: {target_tdee} kcal, gap: {calorie_gap:+.1f} kcal)")
            return plan_data
        logger.info(f"\n FINAL ADJUSTMENT: Closing remaining {calorie_gap:+.1f} kcal gap")
        meal_plan = plan_data.get("meal_plan", {})
        largest_meal_name = None
        largest_meal_calories = 0
        for meal_name, foods in meal_plan.items():
            meal_cals = sum(food.get("nutrients", {}).get("Calories", 0) for food in foods)
            if meal_cals > largest_meal_calories:
                largest_meal_calories = meal_cals
                largest_meal_name = meal_name
        if not largest_meal_name:
            logger.warning("No meals found for true-up adjustment")
            return plan_data
        largest_food_idx = None
        largest_food_calories = 0
        for idx, food in enumerate(meal_plan[largest_meal_name]):
            food_name = food['name']
            food_name_lower = food_name.lower()
            if food_name in clamped_food_names:
                logger.debug(f"Skipping clamped food: {food_name}")
                continue
            is_zero_cal_liquid = any(
                liquid_term in food_name_lower 
                for liquid_term in ['tea', 'water', 'coffee', 'black coffee', 'green tea', 'herbal tea']
            )
            if is_zero_cal_liquid:
                continue
                
            food_cals = food.get("nutrients", {}).get("Calories", 0)
            if food_cals > largest_food_calories:
                largest_food_calories = food_cals
                largest_food_idx = idx
        
        if largest_food_idx is None:
            logger.warning(f"No suitable food items found in {largest_meal_name} for true-up adjustment")
            return plan_data
        target_food = meal_plan[largest_meal_name][largest_food_idx]
        old_food_cals = target_food.get("nutrients", {}).get("Calories", 0)
        if old_food_cals < 1:
            logger.warning(f"Target food has negligible calories ({old_food_cals:.2f}), skipping true-up")
            return plan_data
        new_food_cals = old_food_cals + calorie_gap
        adjustment_ratio = new_food_cals / old_food_cals
        MAX_PORTION_OZ = 10.0  
        
        medical_conditions = user_constraints.get('medical_conditions', []) if user_constraints else []
        has_diabetes = any('diabetes' in str(cond).lower() for cond in medical_conditions)
        has_hypertension = any('hypertension' in str(cond).lower() or 'high blood pressure' in str(cond).lower() for cond in medical_conditions)
        has_kidney_issues = any('kidney' in str(cond).lower() or 'renal' in str(cond).lower() or 'ckd' in str(cond).lower() for cond in medical_conditions)
        
        if has_diabetes or has_hypertension or has_kidney_issues:
            max_allowed_sodium = 350.0  
            risk_flags = []
            if has_diabetes: risk_flags.append("Diabetes")
            if has_hypertension: risk_flags.append("Hypertension")
            if has_kidney_issues: risk_flags.append("Kidney Issues")
            logger.debug(f"   Clinical sodium limit: 350mg ({', '.join(risk_flags)})")
        else:
            max_allowed_sodium = 600.0  
        
        old_portion_oz = target_food.get("nutrients", {}).get("Portion Weight", 0) / 28.35 
        projected_portion_oz = old_portion_oz * adjustment_ratio
        
        old_sodium = target_food.get("nutrients", {}).get("Sodium", 0)
        projected_sodium = old_sodium * adjustment_ratio
        
        portion_violation = projected_portion_oz > MAX_PORTION_OZ
        sodium_violation = projected_sodium > max_allowed_sodium
        
        if portion_violation or sodium_violation:
            portion_safe_ratio = MAX_PORTION_OZ / old_portion_oz if old_portion_oz > 0 else adjustment_ratio
            sodium_safe_ratio = max_allowed_sodium / old_sodium if old_sodium > 0 else adjustment_ratio
            
            clamped_ratio = min(adjustment_ratio, portion_safe_ratio, sodium_safe_ratio)
            calories_absorbed = old_food_cals * (clamped_ratio - 1.0)
            calories_spilled = calorie_gap - calories_absorbed
            
            logger.warning(f" FINAL ADJUSTMENT SAFETY CAP ENFORCED for '{target_food['name']}':")
            if portion_violation:
                logger.warning(f"   Portion: {projected_portion_oz:.1f} oz → CLAMPED to {MAX_PORTION_OZ} oz")
            if sodium_violation:
                logger.warning(f"   Sodium: {projected_sodium:.0f}mg → CLAMPED to {max_allowed_sodium:.0f}mg")
            logger.warning(f"   Ratio: {adjustment_ratio:.4f}x → {clamped_ratio:.4f}x")
            logger.warning(f"   Calories absorbed: {calories_absorbed:.0f} kcal | Spilled (deficit): {calories_spilled:.0f} kcal")
            logger.warning(f"   🏥 Medical safety > Caloric perfection. Leaving plan in calorie deficit.")
            
            adjustment_ratio = clamped_ratio
        
        logger.info(f"Adjusting '{target_food['name']}' in {largest_meal_name}: {old_food_cals:.1f} → {new_food_cals:.1f} kcal (ratio: {adjustment_ratio:.4f}x)")
        target_food['name'] = self._adjust_serving_size_in_name(target_food['name'], adjustment_ratio)
        for nutrient, value in target_food.get("nutrients", {}).items():
            if isinstance(value, (int, float)):
                target_food["nutrients"][nutrient] = value * adjustment_ratio
        adjusted_plan = self._recalculate_plan_json(plan_data)
        final_total = adjusted_plan.get("total_nutrients", {}).get("Calories", 0)
        final_gap = target_tdee - final_total
        
        max_correction_iterations = 3
        correction_iteration = 0
        
        while abs(final_gap) > 25.0 and correction_iteration < max_correction_iterations:
            correction_iteration += 1
            logger.info(f" TDEE Correction Iteration {correction_iteration}: Gap is {final_gap:+.1f} kcal, applying micro-adjustment...")
            
            if target_food and old_food_cals > 0:
                current_food_cals = target_food.get("nutrients", {}).get("Calories", 0)
                correction_ratio = (current_food_cals + final_gap) / current_food_cals
                
                current_portion_oz = target_food.get("nutrients", {}).get("Portion Weight", 0) / 28.35 
                projected_correction_oz = current_portion_oz * correction_ratio
                
                current_sodium = target_food.get("nutrients", {}).get("Sodium", 0)
                projected_correction_sodium = current_sodium * correction_ratio
                
                portion_violation = projected_correction_oz > MAX_PORTION_OZ
                sodium_violation = projected_correction_sodium > max_allowed_sodium
                
                if portion_violation or sodium_violation:
                    portion_safe_ratio = MAX_PORTION_OZ / current_portion_oz if current_portion_oz > 0 else correction_ratio
                    sodium_safe_ratio = max_allowed_sodium / current_sodium if current_sodium > 0 else correction_ratio
                    
                    clamped_correction = min(correction_ratio, portion_safe_ratio, sodium_safe_ratio)
                    
                    logger.warning(f" ITERATIVE CORRECTION SAFETY CAP: Ratio {correction_ratio:.4f}x → {clamped_correction:.4f}x")
                    if portion_violation:
                        logger.warning(f"   Portion would exceed {MAX_PORTION_OZ} oz cap")
                    if sodium_violation:
                        logger.warning(f"   Sodium would exceed {max_allowed_sodium:.0f}mg cap")
                    logger.warning(f"   🏥 Stopping correction loop. Medical safety > Caloric perfection.")
                    
                    for nutrient, value in target_food.get("nutrients", {}).items():
                        if isinstance(value, (int, float)):
                            target_food["nutrients"][nutrient] = value * clamped_correction
                    break 
                
                logger.debug(f"   Applying correction_ratio: {correction_ratio:.4f}x (within safety limits)")
                
                for nutrient, value in target_food.get("nutrients", {}).items():
                    if isinstance(value, (int, float)):
                        target_food["nutrients"][nutrient] = value * correction_ratio
                
                adjusted_plan = self._recalculate_plan_json(plan_data)
                final_total = adjusted_plan.get("total_nutrients", {}).get("Calories", 0)
                final_gap = target_tdee - final_total
                logger.info(f"   After correction: {final_total:.1f} kcal (gap: {final_gap:+.1f} kcal)")
            else:
                break
        
        if abs(final_gap) > 25.0:
            logger.warning(f" TDEE tolerance exceeded after {correction_iteration} corrections: Final gap is {final_gap:+.1f} kcal (target: ±25 kcal)")
        else:
            logger.info(f" TDEE tolerance achieved: Final gap is {final_gap:+.1f} kcal (within ±25 kcal)")
   
        
        meal_plan = adjusted_plan.get("meal_plan", {})
        current_plan_cals = sum(
            food.get('nutrients', {}).get('Calories', 0)
            for meal_foods in meal_plan.values()
            for food in meal_foods
        )
        convergence_iterations = 0
        MAX_CONVERGENCE_ITERATIONS = 20  
        
        logger.info(f"\n STARTING ±5 KCAL CONVERGENCE LOOP")
        logger.info(f"   Current: {current_plan_cals:.1f} kcal | Target: {target_tdee} kcal | Gap: {target_tdee - current_plan_cals:+.1f} kcal")
        
        while abs(target_tdee - current_plan_cals) > 5.0 and convergence_iterations < MAX_CONVERGENCE_ITERATIONS:
            convergence_iterations += 1
            gap_before = target_tdee - current_plan_cals
            
            correction_ratio = target_tdee / current_plan_cals if current_plan_cals > 0 else 1.0
            
            logger.debug(f"   🔄 Iteration {convergence_iterations}: Gap {gap_before:+.1f} kcal → Applying {correction_ratio:.4f}x direct ratio")
            
            for meal_name, meal_foods in meal_plan.items():
                for food in meal_foods:
                    food_cals = food.get('nutrients', {}).get('Calories', 0)
                    
                    if food_cals <= 10:
                        continue
                    
                    for nutrient, value in food.get("nutrients", {}).items():
                        if isinstance(value, (int, float)):
                            if nutrient == "Portion Weight":
                                food["nutrients"][nutrient] = round(value * correction_ratio, 2)
                            elif nutrient == "Calories":
                                food["nutrients"][nutrient] = int(value * correction_ratio)
                            elif nutrient in ["Protein", "Carbs", "Fat", "Fiber", "Sugar"]:
                                food["nutrients"][nutrient] = round(value * correction_ratio, 1)
                            else:
                                food["nutrients"][nutrient] = value * correction_ratio
            
            adjusted_plan = self._recalculate_plan_json(adjusted_plan)
            current_plan_cals = adjusted_plan.get("total_nutrients", {}).get("Calories", 0)
            gap_after = target_tdee - current_plan_cals
            
            logger.debug(f"   ✓ After scaling: {current_plan_cals:.1f} kcal | Gap: {gap_after:+.1f} kcal")
        
        final_gap = target_tdee - current_plan_cals
        
        if abs(final_gap) <= 5.0:
            logger.info(f" ±5 KCAL CONVERGENCE ACHIEVED in {convergence_iterations} iterations")
            logger.info(f"   Final Total: {current_plan_cals:.1f} kcal | Target: {target_tdee} kcal | Final Gap: {final_gap:+.1f} kcal")
        else:
            logger.warning(f" ±5 KCAL CONVERGENCE NOT ACHIEVED after {convergence_iterations} iterations")
            logger.warning(f"   Final Total: {current_plan_cals:.1f} kcal | Target: {target_tdee} kcal | Final Gap: {final_gap:+.1f} kcal")
        
        final_deficit = target_tdee - current_plan_cals
        
        if abs(final_deficit) > 5.0:  
            logger.info(f"\nPROPORTIONAL DISTRIBUTION: Detected {final_deficit:+.1f} kcal gap after convergence loop")
            logger.info(f"   Searching for flexible fat/carb items to distribute the remainder...")
            
            flexible_items = []
            
            for meal_name, meal_foods in meal_plan.items():
                for food in meal_foods:
                    nutrients = food.get('nutrients', {})
                    protein = nutrients.get('Protein', 0)
                    fat = nutrients.get('Fat', 0)
                    carbs = nutrients.get('Carbohydrates', 0)
                    calories = nutrients.get('Calories', 0)
                    
                    if (fat > protein) or (carbs > protein):
                        if calories > 20:  
                            flexible_items.append({
                                'food': food,
                                'meal_name': meal_name,
                                'original_calories': calories
                            })
            
            if flexible_items:
                logger.info(f"    Found {len(flexible_items)} flexible fat/carb items for distribution")
                
                deficit_per_item = final_deficit / len(flexible_items)
                
                logger.info(f"    Distributing {final_deficit:+.1f} kcal → {deficit_per_item:+.1f} kcal per item")
                
                for item in flexible_items:
                    food = item['food']
                    nutrients = food['nutrients']
                    original_calories = item['original_calories']
                    
                    new_calories = original_calories + deficit_per_item
                    ratio = new_calories / original_calories
                    
                    original_portion = nutrients.get('Portion Weight', 0) 
                    if original_portion == 0:
                        original_portion = original_calories / 1.5
                    
                    nutrients['Calories'] = int(new_calories)
                    nutrients['Protein'] = round(nutrients.get('Protein', 0) * ratio, 1)
                    nutrients['Carbohydrates'] = round(nutrients.get('Carbohydrates', 0) * ratio, 1)
                    nutrients['Fat'] = round(nutrients.get('Fat', 0) * ratio, 1)
                    nutrients['Fiber'] = round(nutrients.get('Fiber', 0) * ratio, 1)
                    nutrients['Sodium'] = round(nutrients.get('Sodium', 0) * ratio, 1)
                    nutrients['Sugar'] = round(nutrients.get('Sugar', 0) * ratio, 1)
                    nutrients['Portion Weight'] = round(original_portion * ratio, 2)
                    
                    portion_weight = nutrients.get('Portion Weight', 0)
                    protein = nutrients.get('Protein', 0)
                    carbs = nutrients.get('Carbohydrates', 0)
                    fat = nutrients.get('Fat', 0)
                    fiber = nutrients.get('Fiber', 0)
                    macro_weight = protein + carbs + fat + fiber
                    
                    if portion_weight > 0 and macro_weight > 0:
                        macro_density = macro_weight / portion_weight
                        if macro_density > 0.95:
                            corrected_portion = macro_weight * 1.05
                            logger.warning(f"⚖️ PHYSICS RE-CORRECTION (post-distribution): '{food['name']}'")
                            logger.warning(f"   Distribution created {macro_density*100:.1f}% density → Fixed to {corrected_portion:.1f}g")
                            nutrients['Portion Weight'] = round(corrected_portion, 2)
                    
                    logger.debug(f"      {food['name']}: {original_calories:.0f} → {new_calories:.0f} kcal (ratio: {ratio:.3f})")
                
                adjusted_plan = self._recalculate_plan_json(adjusted_plan)
                final_total_after_distribution = adjusted_plan.get("total_nutrients", {}).get("Calories", 0)
                final_gap_after_distribution = target_tdee - final_total_after_distribution
                
                logger.info(f"\n    PROPORTIONAL DISTRIBUTION COMPLETE:")
                logger.info(f"      Before: {current_plan_cals:.1f} kcal (gap: {final_deficit:+.1f})")
                logger.info(f"      After:  {final_total_after_distribution:.1f} kcal (gap: {final_gap_after_distribution:+.1f})")
                logger.info(f"       Absolute deviation: {abs(final_gap_after_distribution):.2f} kcal ({abs(final_gap_after_distribution/target_tdee*100):.3f}%)")
                
                current_plan_cals = final_total_after_distribution
                final_gap = final_gap_after_distribution
            else:
                logger.warning(f"    No flexible items found for distribution. Gap remains: {final_deficit:+.1f} kcal")
        else:
            logger.info(f" PROPORTIONAL DISTRIBUTION SKIPPED: Gap already within ±5 kcal ({final_deficit:+.1f} kcal)")
        
        logger.info(f"True-up complete: Final total = {current_plan_cals:.1f} kcal (gap: {final_gap:+.1f} kcal, target: {target_tdee} kcal)")
        return adjusted_plan
    
    def _get_optimizer_targets(self):
        return {
            "MEAL_CALORIE_TARGETS" : {
                'Breakfast': (0.20, 0.25),      
                'Morning Snack': (0.05, 0.15),  
                'Lunch': (0.30, 0.35),         
                'Afternoon Snack': (0.05, 0.15), 
                'Evening Snack': (0.05, 0.15),   
                'Dinner': (0.30, 0.35),         
            },
            
            "MACRONUTRIENT_TARGETS" : {
                'Carbohydrates': (0.45, 0.57),
                'Protein': (0.15, 0.25),     
                'Fat': (0.25, 0.35),          
            }
        }
    def _calculate_meal_calorie_distribution(self, final_calorie_target: float) -> Dict[str, int]:

        if not final_calorie_target:
            return {}
        
        distribution = {
            "Breakfast": 0.25,       
            "Morning Snack": 0.05,   
            "Lunch": 0.35,           
            "Evening Snack": 0.05,   
            "Dinner": 0.30           
        }

        meal_calories = {
            meal: round(final_calorie_target * percentage)
            for meal, percentage in distribution.items()
        }
        
        dinner_target = meal_calories.get("Dinner", 0)
        lunch_target = meal_calories.get("Lunch", 0)
        breakfast_target = meal_calories.get("Breakfast", 0)
        
        logger.info(f" STRICT CALORIC PARTITIONING: TDEE {final_calorie_target:.0f} kcal")
        logger.info(f"   Breakfast: {breakfast_target} kcal (25%)")
        logger.info(f"   Morning Snack: {meal_calories['Morning Snack']} kcal (10%)")
        logger.info(f"   Lunch: {lunch_target} kcal (30%) ← PRIMARY PEAK MEAL")
        logger.info(f"   Evening Snack: {meal_calories['Evening Snack']} kcal (10%)")
        logger.info(f"   Dinner: {dinner_target} kcal (25%) ← LIGHT MEAL")
        logger.info(f"    CRITICAL: These allocations are STRICT - meals MUST NOT EXCEED targets")
        
        if dinner_target > lunch_target:
            logger.error(f" DINNER LIGHTNESS VIOLATION: Dinner ({dinner_target} kcal) > Lunch ({lunch_target} kcal)")
            logger.error(f"   This should be mathematically impossible with 25/30 split")
        elif dinner_target == breakfast_target and dinner_target < lunch_target:
            logger.info(f" DINNER LIGHTNESS VERIFIED: Dinner ({dinner_target} kcal) = Breakfast, < Lunch ({lunch_target} kcal)")
        
        return meal_calories
    
    def _calculate_macro_targets_for_meals(self, meal_calorie_distribution: Dict[str, int], constraints: Dict) -> Dict[str, Dict]:
        medical_conditions = constraints.get('medical_conditions', [])
        is_type2_diabetic = any('type 2' in cond.lower() and 'diabetes' in cond.lower() for cond in medical_conditions)
        is_diabetic = any('diabetes' in cond.lower() for cond in medical_conditions)
        restrictions_lower = [r.lower() for r in (constraints.get('restrictions') or [])]
        pref_val = constraints.get('dietary_preference')
        if isinstance(pref_val, str):
            preferences_lower = [pref_val.lower()]
        elif isinstance(pref_val, list):
            preferences_lower = [p.lower() for p in pref_val]
        else:
            preferences_lower = []
        
        has_low_carb_restriction = (
            any('low_carbs' in r or 'low-carb' in r or 'low carb' in r for r in restrictions_lower) or
            any('low carb' in p for p in preferences_lower)
        )
        
        if is_type2_diabetic:
            carb_pct, protein_pct, fat_pct = 0.30, 0.35, 0.35 
            logger.info("Using Type 2 Diabetes macro targets: Carbs=30%, Protein=35%, Fat=35%")
        elif has_low_carb_restriction:
            carb_pct, protein_pct, fat_pct = 0.375, 0.30, 0.325  
            logger.info("Using LOW_CARBS restriction macro targets: Carbs=37.5%, Protein=30%, Fat=32.5%")
        elif is_diabetic:
            carb_pct, protein_pct, fat_pct = 0.425, 0.275, 0.30 
            logger.info("Using Diabetes macro targets: Carbs=42.5%, Protein=27.5%, Fat=30%")
        else:
            carb_pct, protein_pct, fat_pct = 0.425, 0.275, 0.30  
            logger.info("Using default macro targets: Carbs=42.5%, Protein=27.5%, Fat=30%")
        
        meal_macro_targets = {}
        
        for meal_name, meal_calories in meal_calorie_distribution.items():
            carb_calories = meal_calories * carb_pct
            protein_calories = meal_calories * protein_pct
            fat_calories = meal_calories * fat_pct
            carb_grams = round(carb_calories / 4)
            protein_grams = round(protein_calories / 4)
            fat_grams = round(fat_calories / 9)
            
            meal_macro_targets[meal_name] = {
                "calories": meal_calories,
                "carbs_g": carb_grams,
                "protein_g": protein_grams,
                "fat_g": fat_grams,
                "carb_pct": round(carb_pct * 100, 1),
                "protein_pct": round(protein_pct * 100, 1),
                "fat_pct": round(fat_pct * 100, 1)
            }
            
            logger.info(f"{meal_name}: {meal_calories} kcal → Carbs: {carb_grams}g, Protein: {protein_grams}g, Fat: {fat_grams}g")
        return meal_macro_targets

    def _snap_to_quarter_increments(self, value: float, is_spice: bool = False) -> float:
        snapped = round(value * 4) / 4
        
        if snapped < 0.25 and not is_spice:
            snapped = 0.25
        
        return snapped
    
    def _round_to_kitchen_fraction(self, value: float) -> str:

        if value < 0.1:
            return "0.1"  
        
        whole_part = int(value)
        fractional_part = value - whole_part
        
        common_fractions = [0.0, 0.1, 0.25, 0.33, 0.5, 0.66, 0.75, 1.0]
        
        nearest_fraction = min(common_fractions, key=lambda f: abs(fractional_part - f))
        
        if nearest_fraction == 1.0:
            result = whole_part + 1
            return str(result) if result == int(result) else f"{result:.2f}"
        
        final_value = whole_part + nearest_fraction
        
        if final_value == int(final_value):
            return str(int(final_value))
        else:
            formatted = f"{final_value:.2f}".rstrip('0').rstrip('.')
            return formatted
    
    def _adjust_serving_size_in_name(self, name_string, ratio):
        portion_pattern = r'\((\d+(?:\.\d+)?)\s*(cup|tbsp|tsp|oz|fl oz|slice|piece|serving|whole)s?\)'
        def scale_portion(match):
            try:
                quantity = float(match.group(1))
                unit = match.group(2)
                scaled_quantity = quantity * ratio
                scaled_quantity = max(0.1, scaled_quantity)
                snapped_quantity = snap_to_culinary_fraction(scaled_quantity)
                

                if snapped_quantity == int(snapped_quantity):
                    qty_str = str(int(snapped_quantity))
                else:
                    qty_str = f"{snapped_quantity:.2f}".rstrip('0').rstrip('.')
                
                return f"({qty_str} {unit})"
            except (ValueError, AttributeError) as e:
                logger.warning(f"Failed to scale portion in '{match.group(0)}': {e}")
                return match.group(0) 
        updated_name = re.sub(portion_pattern, scale_portion, name_string, flags=re.IGNORECASE)
        if abs(ratio - 1.0) > 0.05:  
            logger.info(f" Scaled portions (ratio={ratio:.2f}): {name_string} → {updated_name}")
        return updated_name
    def _calculate_macro_ratios(self, protein_g, carbs_g, fat_g):
        p_cal, c_cal, f_cal = protein_g * 4, carbs_g * 4, fat_g * 9
        total_cal = p_cal + c_cal + f_cal
        if total_cal == 0: return {"Protein": 0, "Carbohydrates": 0, "Fat": 0}
        return {
            "Protein": (p_cal / total_cal) * 100,
            "Carbohydrates": (c_cal / total_cal) * 100,
            "Fat": (f_cal / total_cal) * 100
        }

    def _recalculate_plan_json(self, plan_data):
        validated_plan = json.loads(json.dumps(plan_data))
        
        if "meals_nutrients" not in validated_plan:
            validated_plan["meals_nutrients"] = {}
        if "total_nutrients" not in validated_plan:
            validated_plan["total_nutrients"] = {}
        
        grand_total_nutrients = {}
        nutrient_keys = ["Portion Weight", "Protein", "Carbohydrates", "Fat", "Fiber", "Sodium", "Iodine", "Sugar", "Cholesterol", "Calories"] 
        for meal_name, foods in validated_plan.get("meal_plan", {}).items():
            meal_totals = {key: 0 for key in nutrient_keys}
            for food in foods:
                for nutrient, value in food.get("nutrients", {}).items():
                    if nutrient in meal_totals and isinstance(value, (int, float)):
                        meal_totals[nutrient] += value
            
            if meal_name not in validated_plan["meals_nutrients"]:
                validated_plan["meals_nutrients"][meal_name] = {}
            
            validated_plan["meals_nutrients"][meal_name].update(meal_totals)
            p, c, f = meal_totals.get("Protein", 0), meal_totals.get("Carbohydrates", 0), meal_totals.get("Fat", 0)
            validated_plan["meals_nutrients"][meal_name]["Macronutrient Ratio"] = self._calculate_macro_ratios(p, c, f)
            
            for nutrient, value in meal_totals.items():
                grand_total_nutrients[nutrient] = grand_total_nutrients.get(nutrient, 0) + value

        validated_plan["total_nutrients"].update(grand_total_nutrients)
        p_total, c_total, f_total = grand_total_nutrients.get("Protein", 0), grand_total_nutrients.get("Carbohydrates", 0), grand_total_nutrients.get("Fat", 0)
        validated_plan["overall_macro_distribution"] = self._calculate_macro_ratios(p_total, c_total, f_total)
        
        for foods in validated_plan.get("meal_plan", {}).values():
            for food in foods:
                food.pop('nutrients_per_gram', None)
        
        return validated_plan

    def _adjust_meal_plan_with_optimizer(self, original_plan_data, new_target_calories):
        targets = self._get_optimizer_targets()
        MEAL_CALORIE_TARGETS = targets["MEAL_CALORIE_TARGETS"]
        MACRONUTRIENT_TARGETS = targets["MACRONUTRIENT_TARGETS"]
        foods_flat, meal_indices, start_idx = [], {}, 0
        plan_copy = json.loads(json.dumps(original_plan_data))
        try:
            pydantic_bounds_available = True
        except:
            pydantic_bounds_available = False
            logger.warning(" Pydantic bounds not available - using default bounds")
 
        for meal_name, foods in plan_copy.get("meal_plan", {}).items():
            for food in foods:
                nutrients_per_gram = {}
                original_weight = food.get("nutrients", {}).get("Portion Weight", 1)
                if original_weight > 0:
                    for key, val in food.get("nutrients", {}).items():
                        if isinstance(val, (int, float)):
                            nutrients_per_gram[key] = val / original_weight
                food['nutrients_per_gram'] = nutrients_per_gram
                
                if pydantic_bounds_available:
                    food_item = NUTRITION_DB.lookup(food.get('name', ''))
                    if food_item:
                        min_g, max_g = get_portion_bounds(food_item)
                        food['min_bound'] = min_g
                        food['max_bound'] = max_g
                        logger.debug(f"Bounds for {food['name']}: {min_g:.0f}-{max_g:.0f}g")
                    else:
                        food['min_bound'] = original_weight * 0.5
                        food['max_bound'] = original_weight * 2.0
                else:
                    food['min_bound'] = original_weight * 0.5
                    food['max_bound'] = original_weight * 2.0
                
                foods_flat.append(food)
            meal_indices[meal_name] = (start_idx, len(foods_flat))
            start_idx = len(foods_flat)

        if not foods_flat:
            raise ValueError("Meal plan contains no food items to optimize.")

        def objective_function(portions):
            total_nutrients = {"Protein": 0, "Carbohydrates": 0, "Fat": 0, "Calories": 0}
            meal_calories = {meal: 0 for meal in meal_indices}

            for i, food in enumerate(foods_flat):
                for nutrient, per_gram_val in food.get('nutrients_per_gram', {}).items():
                    if nutrient in total_nutrients:
                        total_nutrients[nutrient] += per_gram_val * portions[i]
            
            
                for meal, (start, end) in meal_indices.items():
                    if start <= i < end:
                        meal_calories[meal] += food['nutrients_per_gram'].get('Calories', 0) * portions[i]
                        break
            total_error = ((total_nutrients['Calories'] - new_target_calories) / new_target_calories)**2 * 1000
            if total_nutrients['Calories'] == 0: return total_error * 1e6 
        
        
            macro_ratios = self._calculate_macro_ratios(total_nutrients['Protein'], total_nutrients['Carbohydrates'], total_nutrients['Fat'])
            for macro, (min_r, max_r) in MACRONUTRIENT_TARGETS.items():
                ratio = macro_ratios.get(macro, 0) / 100
                if not (min_r <= ratio <= max_r):
                    total_error += min(abs(ratio - min_r), abs(ratio - max_r))**2 * 500
        
        
            for meal, (min_p, max_p) in MEAL_CALORIE_TARGETS.items():
                if meal in meal_calories:
                    meal_p = meal_calories[meal] / total_nutrients['Calories']
                    if not (min_p <= meal_p <= max_p):
                        total_error += min(abs(meal_p - min_p), abs(meal_p - max_p))**2 * 100
            return total_error

    
        initial_portions = np.array([f['nutrients'].get('PortionWeight', 50) for f in foods_flat])
        bounds = [
            (food['min_bound'], food['max_bound'])
            for food in foods_flat
        ]
        logger.info(f" Optimizer bounds: {[(f['name'][:20], f'{b[0]:.0f}-{b[1]:.0f}g') for f, b in zip(foods_flat, bounds)]}")
    
        result = minimize(objective_function, initial_portions, method='SLSQP', bounds=bounds, options={'maxiter': 500})
        optimized_portions = result.x
        for i, (food, new_portion) in enumerate(zip(foods_flat, optimized_portions)):
            if new_portion < food['min_bound'] + 1 or new_portion > food['max_bound'] - 1:
                logger.warning(f" Optimizer hit bound for {food['name']}: {new_portion:.0f}g (limit: {food['min_bound']:.0f}-{food['max_bound']:.0f}g)")
        new_plan = json.loads(json.dumps(original_plan_data))
        flat_idx = 0
        for meal_name, foods in new_plan.get("meal_plan", {}).items():
            for food in foods:
                new_portion = optimized_portions[flat_idx]
                original_portion = foods_flat[flat_idx]['nutrients'].get('PortionWeight', 1)
                ratio = new_portion / original_portion if original_portion > 0 else 1.0
                food['name'] = self._adjust_serving_size_in_name(food['name'], ratio)
                for key, val_per_g in foods_flat[flat_idx].get('nutrients_per_gram', {}).items():
                    food['nutrients'][key] = val_per_g * new_portion
                food['nutrients']['Portion Weight'] = new_portion
                flat_idx += 1
            
    
        return self._recalculate_plan_json(new_plan)

    @staticmethod
    def clean_display_name(food_name: str) -> str:

        if not food_name:
            return food_name
        
        dietary_stopwords = [
            'vegan', 'vegetarian', 'raw', 'cooked', 'diet', 'alternative', 
            'base', 'based', 'style', 'influence', 'inspired', 'traditional',
            'fresh', 'dried', 'frozen', 'organic', 'natural'
        ]
        
        geographic_stopwords = [
            'indian', 'african', 'american', 'mexican', 'asian', 'chinese',
            'japanese', 'thai', 'korean', 'vietnamese', 'italian', 'french',
            'spanish', 'greek', 'turkish', 'lebanese', 'moroccan', 'egyptian',
            'bhutanese', 'kashmiri', 'punjabi', 'bengali', 'south indian',
            'north indian', 'maghreb', 'east african', 'west african',
            'middle eastern', 'mediterranean', 'caribbean', 'latin american'
        ]
        
        all_stopwords = dietary_stopwords + geographic_stopwords
        pattern = r'\b(?:' + '|'.join(re.escape(word) for word in all_stopwords) + r')\b'
        cleaned = re.sub(pattern, '', food_name, flags=re.IGNORECASE)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        
        if not cleaned or len(cleaned) < 3:
            return food_name
        
        return cleaned
    
    def _deduplicate_meal_items(self, plan_json: Dict) -> Dict:

        meal_plan = plan_json.get("meal_plan", {})
        
        for meal_name, foods in meal_plan.items():
            if not foods:
                continue
            
            def get_base_name(food_item):
                name = food_item.get('name', '')
                base = re.sub(r'\s*\([^)]*\)', '', name).strip().lower()
                return base
            
            name_groups = {}
            for food_item in foods:
                base_name = get_base_name(food_item)
                if base_name not in name_groups:
                    name_groups[base_name] = []
                name_groups[base_name].append(food_item)
            
            merged_foods = []
            for base_name, items in name_groups.items():
                if len(items) == 1:

                    merged_foods.append(items[0])
                    logger.debug(f"✓ '{base_name}' in {meal_name}: No duplicates, kept original (all nutrients intact)")
                else:
                    logger.warning(f" DUPLICATE MERGER: Found {len(items)} instances of '{base_name}' in {meal_name} - merging into one")
                    
                    
                    merged_item = copy.deepcopy(items[0])
                    merged_nutrients = merged_item.get('nutrients', {})
                    
                    nutrient_keys = ['Calories', 'Protein', 'Carbohydrates', 'Fat', 'Fiber', 
                                   'Sodium', 'Cholesterol', 'Sugar', 'Portion Weight', 'Iodine']
                    
                    for nutrient_key in nutrient_keys:
                        nutrient_values = [item.get('nutrients', {}).get(nutrient_key) for item in items]
                        nutrient_values = [v for v in nutrient_values if v is not None]  
                        
                        if nutrient_values:
                            merged_nutrients[nutrient_key] = sum(nutrient_values)
                    
                    merged_item['nutrients'] = merged_nutrients
                    merged_foods.append(merged_item)
                    
                    logger.info(f"    Merged {len(items)} items: {merged_nutrients.get('Calories', 0):.0f} kcal total")
            
            plan_json["meal_plan"][meal_name] = merged_foods
        
        return plan_json
    
    def _apply_hard_heuristic_biology_override(self, plan_json: Dict, restrictions: List[str] = None) -> Dict:

        TRUST_DATABASE_CHOLESTEROL = True
        
        if restrictions is None:
            restrictions = []
        
        is_low_sodium = any('low' in str(r).lower() and 'sodium' in str(r).lower() for r in restrictions)
        
        if TRUST_DATABASE_CHOLESTEROL:
            logger.info(" BIOLOGY OVERRIDE: TRUST DATABASE MODE - Cholesterol restoration DISABLED")
            logger.info("   Database cholesterol values accepted as-is (0mg means 0mg)")
        else:
            logger.info("BIOLOGY OVERRIDE: Trust the Zero enabled, Meat Cholesterol Exception active")
        
        if is_low_sodium:
            logger.info(" LOW-SODIUM RESTRICTION DETECTED: Sodium restoration DISABLED")

        MEAT_CHOLESTEROL_RATES = {
            'chicken': 15, 
            'turkey': 15,
            'duck': 18,
            'goose': 18,
            'beef': 18,   
            'veal': 18,
            'pork': 18,
            'lamb': 18,
            'mutton': 18,
            'steak': 18,   
            'bacon': 20,    
            'sausage': 20,
            'ham': 18,
            'pepperoni': 20,
            'salami': 20,
            'prosciutto': 18,
            'chorizo': 20,
            'fish': 12,   
            'salmon': 12,
            'tuna': 12,
            'shrimp': 14,
            'prawn': 14,
            'egg': 25,     
        }
        
        meats_restored = 0
        sodium_restored = 0
        
        total_restored_cholesterol = 0
        items_with_restored_cholesterol = []

        if not TRUST_DATABASE_CHOLESTEROL:
            for meal_name, items in plan_json.get("meal_plan", {}).items():
                for item in items:
                    name = item.get('name', '').lower()
                    nutrients = item.get('nutrients', {})
                    cholesterol = nutrients.get('Cholesterol', 0)
                    

                    matched_rate = None
                    matched_keyword = None
                    
                    for keyword, rate in MEAT_CHOLESTEROL_RATES.items():
                        if keyword in name:
                            matched_rate = rate
                            matched_keyword = keyword
                            break
                    
                    if matched_rate and cholesterol == 0:
                        portion_weight_g = nutrients.get('Portion Weight', 0)
                        portion_weight_oz = portion_weight_g * 0.035274
                        
                        restored_cholesterol = min(200, portion_weight_oz * matched_rate)  
                        
                        item['nutrients']['Cholesterol'] = restored_cholesterol
                        meats_restored += 1
                        total_restored_cholesterol += restored_cholesterol
                        items_with_restored_cholesterol.append(item)
                        
                        logger.info(f"    MEAT CHOLESTEROL RESTORED: {item['name']} → {restored_cholesterol:.0f}mg (was 0mg, {matched_keyword}: {matched_rate}mg/oz)")
            
            MAX_DAILY_CHOLESTEROL = 300 
            
            if total_restored_cholesterol > MAX_DAILY_CHOLESTEROL and items_with_restored_cholesterol:
                logger.warning(f"   CHOLESTEROL DAILY CAP EXCEEDED: {total_restored_cholesterol:.0f}mg > {MAX_DAILY_CHOLESTEROL}mg")
                logger.info(f"    Applying proportional reduction to {len(items_with_restored_cholesterol)} meat items")
                
                scale_factor = MAX_DAILY_CHOLESTEROL / total_restored_cholesterol
                
                for item in items_with_restored_cholesterol:
                    original_chol = item['nutrients']['Cholesterol']
                    scaled_chol = original_chol * scale_factor
                    item['nutrients']['Cholesterol'] = round(scaled_chol, 1)
                    
                    logger.debug(f"       SCALED: {item['name']} cholesterol {original_chol:.0f}mg → {scaled_chol:.0f}mg ({scale_factor:.2f}x)")
                
                logger.info(f"     Total cholesterol scaled: {total_restored_cholesterol:.0f}mg → {MAX_DAILY_CHOLESTEROL}mg")
            elif meats_restored > 0:
                logger.info(f"  MEAT CHOLESTEROL: Restored {meats_restored} items, total {total_restored_cholesterol:.0f}mg (under {MAX_DAILY_CHOLESTEROL}mg limit)")
            
            if meats_restored > 0:
                logger.info(f"  MEAT CHOLESTEROL SUMMARY: Restored {meats_restored} items with realistic rates (chicken: 15mg/oz, beef: 18mg/oz, fish: 12mg/oz)")
            else:
                logger.info(" MEAT CHOLESTEROL: No meat items needed cholesterol restoration")
        

        for meal_name, items in plan_json.get("meal_plan", {}).items():
            for item in items:
                name = item.get('name', '').lower()
                nutrients = item.get('nutrients', {})
                sodium = nutrients.get('Sodium', 0)
                protein = nutrients.get('Protein', 0)
                calories = nutrients.get('Calories', 0)

                if sodium == 0 and protein > 2 and not is_low_sodium:
                    restored_sodium = int((calories / 100) * 150)
                    item['nutrients']['Sodium'] = restored_sodium
                    sodium_restored += 1
                    
                    logger.info(f"    SODIUM BASELINE RESTORED: {item['name']} → {restored_sodium}mg (was 0mg, {calories:.0f} kcal)")
                elif sodium == 0 and protein > 2 and is_low_sodium:
                    logger.debug(f"    SODIUM SKIP (Low-Sodium): {item['name']} kept at 0mg (protein: {protein}g, {calories:.0f} kcal)")
        

        if is_low_sodium:
            logger.info(" SODIUM BASELINE: SKIPPED - Low-Sodium restriction active (all foods kept at 0mg)")
        elif sodium_restored > 0:
            logger.info(f" SODIUM BASELINE: Restored sodium for {sodium_restored} cooked food items")
        else:
            logger.info(" SODIUM BASELINE: No foods needed sodium baseline correction")
        
        return plan_json
    
    def _format_final_display_names(self, plan_json: Dict) -> Dict:

        logger.info("DATABASE-DRIVEN HOUSEHOLD FORMATTER: Using portion_options from database")
        
        for meal_name, items in plan_json.get("meal_plan", {}).items():
            for item in items:
                original_name = item.get('name', '')
                portion_weight_g = item.get('nutrients', {}).get('Portion Weight', 0)
                
                clean_name = original_name
                while True:
                    match = re.search(r'\s*\([^)]+\)\s*$', clean_name)
                    if not match:
                        break
                    clean_name = re.sub(r'\s*\([^)]+\)\s*$', '', clean_name).strip()
                
                db_household_label = ''
                portion_options = item.get('portion_options', [])
                
                if portion_options and len(portion_options) > 0:
                    best_option = None
                    best_diff = float('inf')
                    
                    for option in portion_options:
                        option_portion_g = option.get('portion_g', 0) or option.get('grams', 0)
                        diff = abs(option_portion_g - portion_weight_g)
                        
                        if diff < best_diff:
                            best_diff = diff
                            best_option = option
                    
                    if best_option:
                        db_household_label = best_option.get('label', '')
                        matched_portion_g = best_option.get('portion_g', 0) or best_option.get('grams', 0)
                        
                        logger.info(f"    DATABASE MATCH: '{clean_name[:40]}' → '{db_household_label}' ({matched_portion_g:.0f}g)")
                        
                        if matched_portion_g > 0:
                            scale_factor = portion_weight_g / matched_portion_g
                            
                            if abs(scale_factor - 1.0) > 0.05:
                                qty_match = re.match(r'^([\d.]+)\s+(.+)$', db_household_label)
                                if qty_match:
                                    original_qty = float(qty_match.group(1))
                                    unit_text = qty_match.group(2)
                                    scaled_qty = original_qty * scale_factor
                                    
                                    standard_fractions = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2]
                                    snapped_qty = min(standard_fractions, key=lambda x: abs(x - scaled_qty))
                                    
                                    # Format as decimal (e.g., 1.75 instead of 1 3/4)
                                    if snapped_qty == int(snapped_qty):
                                        scaled_qty_str = str(int(snapped_qty))
                                    else:
                                        scaled_qty_str = f"{snapped_qty:.2f}".rstrip('0').rstrip('.')
                                    
                                    db_household_label = f"{scaled_qty_str} {unit_text}"
                                    logger.info(f"      Scaled: {portion_weight_g:.0f}g → '{db_household_label}' (snapped from {scaled_qty:.2f})")
                                else:
                                    standard_fractions = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 1.75, 2]
                                    snapped_factor = min(standard_fractions, key=lambda x: abs(x - scale_factor))
                                    
                                    # Format as decimal (e.g., 1.75 instead of 1 3/4)
                                    if snapped_factor == int(snapped_factor):
                                        scale_str = str(int(snapped_factor))
                                    else:
                                        scale_str = f"{snapped_factor:.2f}".rstrip('0').rstrip('.')
                                    
                                    db_household_label = f"{scale_str} {db_household_label}"
                
                item['name'] = clean_name
                item['display_name'] = clean_name
                item['household_measure'] = db_household_label if db_household_label else f"{portion_weight_g:.0f}g"
        
        plan_json = self._recalculate_plan_json(plan_json)
        logger.info(f"DATABASE-DRIVEN HOUSEHOLD FORMATTER COMPLETE")
        
        return plan_json
    
    def _convert_plan_json_to_text(self, plan_json: Dict, restrictions: List[str] = None) -> str:
            def snap_to_closest(value, allowed_values):
                return min(allowed_values, key=lambda x: abs(x - value))
            
            valid_vols = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5]
            valid_pcs  = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5]
            logger.info("GHOST PURGE: Scanning for zero-calorie ghost items...")
            
            MEAL_MINIMUMS = {
                "Breakfast": 3,
                "Morning Snack": 2,
                "Lunch": 4,
                "Evening Snack": 2,
                "Dinner": 4
            }
            
            ghosts_removed = 0
            for meal_name in list(plan_json.get("meal_plan", {}).keys()):
                if 'snack' in meal_name.lower():
                    logger.info(f" GHOST PURGE: Skipping '{meal_name}' (snacks exempt from purge)")
                    continue
                
                original_items = plan_json["meal_plan"][meal_name]
                original_count = len(original_items)
                
                non_ghost_items = [
                    item for item in original_items
                    if item.get("nutrients", {}).get("Calories", 0) >= 5
                ]
                
                minimum_required = MEAL_MINIMUMS.get(meal_name, 3)
                
                if len(non_ghost_items) < minimum_required:
                    ghost_items = [
                        item for item in original_items
                        if item.get("nutrients", {}).get("Calories", 0) < 5
                    ]
                    logger.warning(f"GHOST PURGE: {meal_name} has {len(non_ghost_items)} items after ghost removal")
                    logger.warning(f"   Minimum required: {minimum_required} items (structure: 3-2-4-2-4)")
                    logger.warning(f"   Keeping {minimum_required - len(non_ghost_items)} ghost item(s) to maintain structure")
                    
                    items_to_keep = minimum_required - len(non_ghost_items)
                    plan_json["meal_plan"][meal_name] = non_ghost_items + ghost_items[:items_to_keep]
                else:
                    plan_json["meal_plan"][meal_name] = non_ghost_items
                    removed = original_count - len(non_ghost_items)
                    if removed > 0:
                        ghosts_removed += removed
                        logger.warning(f"GHOST PURGE: Removed {removed} ghost item(s) from {meal_name} (< 5 kcal)")
            
            if ghosts_removed > 0:
                logger.info(f" GHOST PURGE: Removed {ghosts_removed} total ghost items from meal plan")
            else:
                logger.info(" GHOST PURGE: No ghost items detected")
            
            logger.info("POST-PROCESS: Starting duplicate merger and hard heuristic biology override")
            
            plan_json = self._deduplicate_meal_items(plan_json)

            plan_json = self._apply_hard_heuristic_biology_override(plan_json, restrictions=restrictions)
            
            logger.info(" POST-PROCESS: Completed cleanup - ready for text formatting")
            
            parts = ["Here is your meal plan based on your preferences and health needs:"]
            meal_order = ["Breakfast", "Morning Snack", "Lunch", "Evening Snack", "Dinner"]
            daily_totals = {"Protein": 0, "Carbohydrates": 0, "Fat": 0, "Calories": 0}
            liquid_keywords = [
                'milk', 'juice', 'water', 'soup', 'tea', 'coffee', 'smoothie', 
                'lassi', 'buttermilk', 'beverage', 'drink', 'shake', 'soda', 
                'coke', 'lemonade', 'beer', 'wine', 'liquor', 'alcohol', 'cocktail', 'mocktail', 'broth', 'latte', 'cappuccino', 'espresso', 'macchiato'
            ]

            def _correct_portion_text(name_part: str, weight_val: float, is_liquid: bool) -> str:
                name_lower = name_part.lower()
                density = 5.0 
                
                if is_liquid:
                    density = 8.0 
                elif any(x in name_lower for x in ['seed', 'nut', 'almond', 'walnut', 'cashew', 'pecan', 'peanut', 'pistachio', 'hazelnut', 'macadamia']):
                    density = 4.5 
                elif any(x in name_lower for x in ['oat', 'rice', 'quinoa', 'grain', 'pasta', 'spaghetti', 'noodle', 'barley', 'millet', 'lentil', 'bean', 'chickpea', 'dal']):
                    density = 6.5 
                elif any(x in name_lower for x in ['spinach', 'kale', 'lettuce', 'greens']):
                    density = 1.5 
                elif any(x in name_lower for x in ['broccoli', 'cauliflower', 'carrot', 'vegetable', 'veg']):
                    density = 5.5 
                cup_match = re.search(r'\(((?:\d+(?:\.\d+)?)|(?:\d+/\d+))\s*cups?\)', name_part, re.IGNORECASE)
                if cup_match:
                    stated_cups_str = cup_match.group(1)
                    try:
                        if '/' in stated_cups_str:
                            num, den = map(float, stated_cups_str.split('/'))
                            stated_cups = num / den
                        else:
                            stated_cups = float(stated_cups_str)
                    except ValueError:
                        return name_part

                    expected_val = stated_cups * density
                    if abs(expected_val - weight_val) > (expected_val * 0.2) or abs(expected_val - weight_val) > 1.0:
                        correct_cups = weight_val / density
                        is_seed_local = 'seed' in name_lower
                        is_nut_local = any(x in name_lower for x in ['nut', 'almond', 'walnut', 'cashew', 'pecan', 'peanut', 'pistachio', 'hazelnut', 'macadamia'])
                        
                        if is_seed_local:
                            tbsp = correct_cups * 16
                            if tbsp < 1.0:
                                tsp = tbsp * 3
                                snapped_tsp = self._round_to_kitchen_fraction(tsp)
                                new_measure = f"{snapped_tsp} tsp"
                            else:
                                snapped_tbsp = self._round_to_kitchen_fraction(tbsp)
                                new_measure = f"{snapped_tbsp} tbsp"
                        elif is_nut_local and 0.5 <= weight_val <= 1.8:
                             new_measure = "1/4 cup"
                        elif not is_liquid and weight_val < 2.0 and any(x in name_lower for x in ['oil', 'butter']):
                            tbsp = correct_cups * 16
                            snapped_tbsp = self._round_to_kitchen_fraction(tbsp)
                            new_measure = f"{snapped_tbsp} tbsp"
                        else:
                            snapped_cups = self._round_to_kitchen_fraction(correct_cups)
                            if snapped_cups == "0.25":
                                new_measure = "1/4 cup"
                            elif snapped_cups == "0.33":
                                new_measure = "1/3 cup"
                            elif snapped_cups == "0.5":
                                new_measure = "1/2 cup"
                            elif snapped_cups == "0.66":
                                new_measure = "2/3 cup"
                            elif snapped_cups == "0.75":
                                new_measure = "3/4 cup"
                            elif snapped_cups == "1":
                                new_measure = "1 cup"
                            elif float(snapped_cups) > 1 and float(snapped_cups) == int(float(snapped_cups)):
                                new_measure = f"{int(float(snapped_cups))} cups"
                            else:
                                new_measure = f"{snapped_cups} cup"
                        
                        return re.sub(r'\(((?:\d+(?:\.\d+)?)|(?:\d+/\d+))\s*cups?\)', f"({new_measure})", name_part, flags=re.IGNORECASE)
                return name_part

            for meal_name in meal_order:
                if meal_name in plan_json.get("meal_plan", {}) and plan_json["meal_plan"][meal_name]:
                    parts.append(f"\n**{meal_name}**\n")
                    current_meal_totals = {"Protein": 0, "Carbohydrates": 0, "Fat": 0, "Calories": 0}
                    for item in plan_json["meal_plan"][meal_name]:
                        n = item.get("nutrients", {})
                        weight_g = n.get('Portion Weight', 0)
                        
                        household_measure = item.get('household_measure', f"{weight_g:.0f}g")
                        
                        display_name = item.get('display_name') or item.get('name', '')
                        clean_name = display_name
                        while True:
                            match = re.search(r'\s*\([^)]+\)\s*$', clean_name)
                            if not match:
                                break
                            clean_name = re.sub(r'\s*\([^)]+\)\s*$', '', clean_name).strip()
                        clean_name = clean_name.lstrip('- ').strip()
                        
                        is_liquid = False
                        name_lower = display_name.lower()
                        for keyword in liquid_keywords:
                            if re.search(rf'\b{re.escape(keyword)}\b', name_lower):
                                is_liquid = True
                                break

                        if is_liquid:
                            weight_val = weight_g * 0.033814
                            snapped_weight = self._apply_culinary_display_snapping(weight_val, 'fl oz')
                            snapped_weight_g = snapped_weight / 0.033814
                            if snapped_weight == int(snapped_weight):
                                weight_str = f"{int(snapped_weight)} fl oz"
                            else:
                                weight_str = f"{snapped_weight:.2f}".rstrip('0').rstrip('.') + " fl oz"
                        else:
                            weight_val = weight_g * 0.035274
                            snapped_weight = self._apply_culinary_display_snapping(weight_val, 'oz')
                            snapped_weight_g = snapped_weight / 0.035274
                            if snapped_weight == int(snapped_weight):
                                weight_str = f"{int(snapped_weight)} oz"
                            else:
                                weight_str = f"{snapped_weight:.2f}".rstrip('0').rstrip('.') + " oz"
                        
                        # Recalculate macros to match the snapped displayed portion weight
                        # NOTE: Calories are NOT recalculated - they stay as the TDEE scaler set them
                        per_100g = item.get('per_100g', {})
                        if per_100g and snapped_weight_g > 0:
                            # Definitive recalculation from per_100g for perfect accuracy
                            scale = snapped_weight_g / 100.0
                            for nk in ['Protein', 'Carbohydrates', 'Fat', 'Fiber', 'Iodine', 'Sugar', 'Cholesterol']:
                                if nk in per_100g:
                                    n[nk] = round(per_100g[nk] * scale, 1)
                            # Sodium: only recalculate if not restored by biology override
                            if 'Sodium' in per_100g and per_100g['Sodium'] > 0:
                                n['Sodium'] = round(per_100g['Sodium'] * scale, 1)
                            n['Portion Weight'] = snapped_weight_g
                        elif weight_g > 0:
                            snap_ratio = snapped_weight_g / weight_g
                            if abs(snap_ratio - 1.0) > 0.001:
                                # Fallback: proportional adjustment (exclude Calories to preserve TDEE)
                                for nk in ['Protein', 'Carbohydrates', 'Fat', 'Fiber', 'Iodine', 'Sugar', 'Cholesterol']:
                                    if nk in n and isinstance(n[nk], (int, float)):
                                        n[nk] = n[nk] * snap_ratio
                                n['Portion Weight'] = snapped_weight_g
                        
                        for k in current_meal_totals:
                            current_meal_totals[k] += n.get(k, 0)

                        final_clean_name = clean_name
                        while True:
                            match = re.search(r'\s*\([^)]+\)\s*$', final_clean_name)
                            if not match:
                                break
                            final_clean_name = re.sub(r'\s*\([^)]+\)\s*$', '', final_clean_name).strip()
                        nutrient_str = (
                            f"- {final_clean_name} | Household Measure: {household_measure} | Portion Weight: {weight_str} | "
                            f"Protein: {n.get('Protein', 0):.0f}g | Carbs: {n.get('Carbohydrates', 0):.0f}g | Fat: {n.get('Fat', 0):.0f}g | "
                            f"Fiber: {n.get('Fiber', 0):.0f}g | Sodium: {n.get('Sodium', 0):.0f}mg | Iodine: {n.get('Iodine', 0):.0f}mcg | Sugar: {n.get('Sugar', 0):.0f}g | "  
                            f"Cholesterol: {n.get('Cholesterol', 0):.0f}mg | Calories: {n.get('Calories', 0):.0f}kcal"
                        )
                        
                        # Append FoodID, Ingredients, and Recipe metadata from the food item
                        item_food_id = item.get('food_id') or 'UNKNOWN'
                        item_ingredients = item.get('ingredients', [])
                        item_recipe = item.get('recipe', [])
                        
                        if item_ingredients and isinstance(item_ingredients, list):
                            ingredients_text = ", ".join(str(ing) for ing in item_ingredients)
                        else:
                            ingredients_text = "N/A"
                        
                        if item_recipe and isinstance(item_recipe, list):
                            recipe_text = " ".join(str(step) for step in item_recipe)
                        else:
                            recipe_text = "N/A"
                        
                        nutrient_str += f" | FoodID: {item_food_id} | Ingredients: {ingredients_text} | Recipe: {recipe_text}"
                        
                        parts.append(nutrient_str)
                
                    p, c, f = current_meal_totals["Protein"], current_meal_totals["Carbohydrates"], current_meal_totals["Fat"]
                    total_cal = p*4 + c*4 + f*9
                    p_pct = (p*4/total_cal)*100 if total_cal > 0 else 0
                    c_pct = (c*4/total_cal)*100 if total_cal > 0 else 0
                    f_pct = (f*9/total_cal)*100 if total_cal > 0 else 0

                    parts.append(f"\nTotal calories for {meal_name}: {current_meal_totals['Calories']:.1f} kcal")
                    parts.append(f"Macronutrient Ratio: Protein {p:.1f}g ({p_pct:.1f}%), Carbs {c:.1f}g ({c_pct:.1f}%), Fat {f:.1f}g ({f_pct:.1f}%)")
                    
                    for k in daily_totals:
                        daily_totals[k] += current_meal_totals[k]
    
            dp, dc, df = daily_totals["Protein"], daily_totals["Carbohydrates"], daily_totals["Fat"]
            d_total_cal = dp*4 + dc*4 + df*9
            dp_pct = (dp*4/d_total_cal)*100 if d_total_cal > 0 else 0
            dc_pct = (dc*4/d_total_cal)*100 if d_total_cal > 0 else 0
            df_pct = (df*9/d_total_cal)*100 if d_total_cal > 0 else 0

            notes = plan_json.get("condition_specific_notes", "")
    
            parts.append(f"\n**Daily Total Calories:** {daily_totals['Calories']:.1f} kcal")
            parts.append(f"\n**Overall Macronutrient Distribution:** Protein {dp:.1f}g ({dp_pct:.1f}%), Carbohydrates {dc:.1f}g ({dc_pct:.1f}%), Fat {df:.1f}g ({df_pct:.1f}%)")
            if notes:
                parts.append(f"\n**Condition-Specific Notes:**\n{notes}")

            
            return "\n".join(parts)
 
    def _snap_to_culinary_fraction(self, value: float) -> float:
        CULINARY_FRACTIONS = [0.25, 0.5, 0.75, 1.0]
        whole_part = int(value)
        fractional_part = value - whole_part
        if fractional_part < 0.125:
            return float(whole_part)
        
        closest_fraction = min(CULINARY_FRACTIONS, key=lambda x: abs(x - fractional_part))
        
        if closest_fraction == 1.0:
            return float(whole_part + 1)
        else:
            return float(whole_part + closest_fraction)
    
    def _quantize_to_culinary_vector(self, raw_qty: float, unit: str) -> float:
        
        if unit in ['piece', 'slice']:
            allowed_vectors = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
            if raw_qty > max(allowed_vectors):
                return float(round(raw_qty)) 
            return min(allowed_vectors, key=lambda x: abs(x - raw_qty))
            
        elif unit in ['cup', 'tbsp', 'tsp']:
            allowed_vectors = [0.125, 0.25, 0.33, 0.5, 0.66, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0]
            if raw_qty > max(allowed_vectors):
                return round(raw_qty * 2.0) / 2.0 
            return min(allowed_vectors, key=lambda x: abs(x - raw_qty))
            
        return round(raw_qty, 1)
    
    def _apply_culinary_display_snapping(self, quantity: float, unit: str) -> float:
       
        unit_lower = unit.lower().strip()
        
        if unit_lower in ['cup', 'cups', 'fl oz', 'fl ozs', 'fluid ounce', 'fluid ounces']:
            return round(quantity * 2) / 2 
        
        elif unit_lower in ['tbsp', 'tbsps', 'tablespoon', 'tablespoons', 'tsp', 'tsps', 'teaspoon', 'teaspoons']:
            if quantity <= 1.0:
                snapped = round(quantity * 4) / 4
                return max(0.25, snapped) 
            else:
                return round(quantity * 2) / 2
        
        elif unit_lower in ['piece', 'pieces', 'whole', 'slice', 'slices', 'item', 'items']:
            snapped = round(quantity)
            return max(1.0, snapped) 
        
        elif unit_lower in ['oz', 'ozs', 'ounce', 'ounces']:
            return round(quantity * 4) / 4
        
        elif unit_lower in ['g', 'gram', 'grams']:
            return round(quantity / 5) * 5
        
        else:
            return round(quantity * 4) / 4
    
    def _snap_display_name_portions(self, display_name: str) -> str:
        
        pattern = r'\((\d+(?:\.\d+)?)\s*(cup|cups|tbsp|tablespoon|tablespoons|tsp|teaspoon|teaspoons|oz|ozs|ounce|ounces|fl oz|fl ozs|piece|pieces|whole|slice|slices|item|items|g|gram|grams)s?\)'
        
        match = re.search(pattern, display_name, re.IGNORECASE)
        if not match:
            return display_name  
        
        original_quantity = float(match.group(1))
        unit = match.group(2)
        
        snapped_quantity = self._apply_culinary_display_snapping(original_quantity, unit)
        
        if snapped_quantity == int(snapped_quantity):
            formatted_qty = str(int(snapped_quantity))
        else:
            formatted_qty = f"{snapped_quantity:.2f}".rstrip('0').rstrip('.')
        
        unit_display = unit.lower()
        if snapped_quantity == 1.0:
            if unit_display.endswith('s'):
                unit_display = unit_display[:-1]
        else:
            if not unit_display.endswith('s') and unit_display not in ['fl oz']:
                unit_display = unit_display + 's'
        
        new_portion_text = f"({formatted_qty} {unit_display})"
        snapped_name = re.sub(pattern, new_portion_text, display_name, flags=re.IGNORECASE)
        
        if original_quantity != snapped_quantity:
            logger.debug(f" DISPLAY SNAP: '{display_name}' → '{snapped_name}'")
        
        return snapped_name
    
    def format_portions_in_plan(self, plan_text: str) -> str:
        processed_lines = []
        food_line_pattern = re.compile(r'(-?\s*.*?)\((\d+(?:\.\d+)?(?:/\d+)?\s+[a-zA-Z\s]+)\)(\s*\|.*kcal)')
        logger.debug("--- Running format_portions_in_plan ---")

        for line in plan_text.split('\n'):
            match = food_line_pattern.match(line.strip())
            if match:
                food_name_part = match.group(1)
                portion_content = match.group(2).strip()
                rest_of_line = match.group(3)
                logger.debug(f"Processing line: '{line.strip()}' -> Portion content: '{portion_content}'") 

                parts = portion_content.split(maxsplit=1)
                formatted_portion_content = portion_content 
                if len(parts) >= 1:
                    number_str = parts[0]
                    unit = parts[1] if len(parts) > 1 else ""
                    logger.debug(f" Extracted number_str: '{number_str}', unit: '{unit}'")

                    if '/' in number_str:
                        original_number_str = number_str
                        number_str = number_str.split('/')[0].strip()
                        logger.debug(f" Handled slash: '{original_number_str}' -> '{number_str}'") 

                    try:
                        number_val = float(number_str)
                        logger.debug(f" Converted to float: {number_val}") 
                        
                        snapped_value = self._snap_to_culinary_fraction(number_val)
                        
                        if snapped_value == int(snapped_value):
                            formatted_number = str(int(snapped_value))
                        else:
                            formatted_number = f"{snapped_value:.2f}".rstrip('0').rstrip('.')
                        
                        formatted_portion_content = f"{formatted_number} {unit}".strip()
                        logger.debug(f" Final formatted portion: '{formatted_portion_content}'") 

                    except ValueError:
                        logger.warning(f" ValueError: Could not convert '{number_str}' to float. Skipping rounding for this item.") 
                        pass 

                processed_lines.append(f"{food_name_part}({formatted_portion_content}){rest_of_line}")
            else:
                processed_lines.append(line)
                if '|' in line and 'kcal' in line and '(' in line and ')' in line and 'Household Measure:' not in line: 
                     logger.warning(f"Line looked like food but did not match regex: '{line.strip()}'")

        logger.debug("--- Finished format_portions_in_plan ---")
        return "\n".join(processed_lines)
                
    def _format_profile_summary(self, profile: Dict, vital_units: Dict, current_session_vitals: Optional[Dict] = None) -> str:
        if not profile or all(v is None for k, v in profile.items() if k != 'user_id'):
            return "I don't have any of your profile details recorded yet. To get personalized advice, you can tell me about yourself or update your profile in the app."

        parts = ["**Your Current Profile Overview:**"]
        if profile.get('name'): parts.append(f"- Name: {profile.get('name')}")
        if profile.get('age'): parts.append(f"- Age: {profile.get('age')}")
        if profile.get('gender'): parts.append(f"- Gender: {profile.get('gender').title()}")

        if profile.get('height_cm') is not None:
            height_cm = profile['height_cm']
            if 30 < height_cm < 250:
                total_inches = height_cm / 2.54
                feet = int(total_inches // 12)
                inches = round(total_inches % 12)
                if inches == 12:
                    feet += 1
                    inches = 0
                parts.append(f"- Height: {feet}'{inches}\" ({height_cm:.1f} cm)")
            else:
                parts.append(f"- Height: {height_cm:.1f} cm (Please verify this value in your profile)")

        if profile.get('weight_kg') is not None:
            weight_kg = profile['weight_kg']
            if 1 < weight_kg < 300:
                weight_lbs = weight_kg * 2.20462
                parts.append(f"- Current Weight: {weight_lbs:.1f} lbs ({weight_kg:.1f} kg)")
            else:
                parts.append(f"- Current Weight: {weight_kg:.1f} kg (Please verify this value in your profile)")

        if profile.get('waist_circumference_cm') is not None:
            waist_cm = float(profile['waist_circumference_cm'])
            waist_in = waist_cm / 2.54
            parts.append(f"- Waist: {waist_in:.1f} in ({waist_cm:.1f} cm)")
        if profile.get('activity_level'): parts.append(f"- Activity Level: {profile.get('activity_level', '').title()}")
        if profile.get('bmi') is not None: parts.append(f"- BMI: {profile.get('bmi'):.1f}")
        if profile.get('tdee') is not None:
            parts.append(f"- Estimated TDEE: {profile.get('tdee'):.0f} kcal/day")
        if profile.get('dietary_preference'): parts.append(f"- Dietary Preference: {profile['dietary_preference']}")
        if profile.get('cuisine'): parts.append(f"- Preferred Cuisine: {profile.get('cuisine')}")
        if profile.get('restrictions'): parts.append(f"- Dietary Restrictions: {', '.join(profile.get('restrictions', []))}")
        if profile.get('allergies'): parts.append(f"- Allergies (Avoid): {', '.join(profile.get('allergies', []))}")
        if profile.get('medical_conditions'): parts.append(f"- Medical Conditions: {', '.join(profile.get('medical_conditions', []))}")
        if profile.get('digestive_issues'): parts.append(f"- Digestive Issues: {', '.join(profile.get('digestive_issues', []))}")
        if profile.get('symptom_aggravating_foods'): parts.append(f"- Symptom Aggravating Foods: {', '.join(profile.get('symptom_aggravating_foods', []))}")
        goal = profile.get('goal', {})
        if goal.get('type') and goal['type'] != 'Weight Maintenance':
            parts.append(f"- Goal: {goal['type']} {goal.get('target_kg', '')} kg")
        elif goal.get('type'):
            parts.append(f"- Goal: {goal['type']}")
        previous_day_vitals_data = profile.get('vitals_numeric', {})
        if previous_day_vitals_data:
            parts.append("\n**Previous Day Vitals:**")
            for vital_name in self.all_vital_names:
                value = previous_day_vitals_data.get(vital_name)
                unit = vital_units.get(vital_name, "")
                if vital_name == "Blood Pressure" and isinstance(value, dict):
                    parts.append(f"  - {vital_name}: {value.get('systolic', 'N/A')}/{value.get('diastolic', 'N/A')} {unit}".strip())
                elif value is not None:
                    parts.append(f"  - {vital_name}: {value:.1f}{unit}".strip() if vital_name == "Body Temperature" else f"  - {vital_name}: {value} {unit}".strip())
                else:
                    parts.append(f"  - {vital_name}: Not provided")

        if current_session_vitals:
            parts.append("\n**Current Vitals for this session:**")
            for vital_name in self.all_vital_names:
                value = current_session_vitals.get(vital_name)
                if value is None:
                    value = previous_day_vitals_data.get(vital_name)
                unit = vital_units.get(vital_name, "")
                if vital_name == "Blood Pressure" and isinstance(value, dict):
                    parts.append(f"  - {vital_name}: {value.get('systolic', 'N/A')}/{value.get('diastolic', 'N/A')} {unit}".strip())
                elif value is not None:
                    parts.append(f"  - {vital_name}: {value:.1f}{unit}".strip() if vital_name == "Body Temperature" else f"  - {vital_name}: {value} {unit}".strip())
                else:
                    parts.append(f"  - {vital_name}: Not provided")
        return "\n".join(parts) if len(parts) > 1 else "No profile details recorded yet. Tell me about yourself!"

    def _extract_full_meal_section(self, full_plan_text: str, meal_type: str) -> Optional[str]:
        if not full_plan_text or not meal_type:
            return None
        pattern = re.compile(
            rf"(\*\*{re.escape(meal_type)}\*\*.*?)(\n\s*\*\*|\Z)",
            re.IGNORECASE | re.DOTALL
        )
        match = pattern.search(full_plan_text)
        return match.group(1).strip() if match else None


    def _recalculate_and_format_plan_summary(self, meal_plan_text: str) -> str:
        if not meal_plan_text:
            return meal_plan_text

        parsed_plan = self.meal_plan_generator_tool._parse_meal_plan_text_to_json(meal_plan_text)
        lines = meal_plan_text.strip().split('\n')

        summary_start_idx = None
        for idx, line in enumerate(lines):
            line_stripped = line.strip()
            if (re.match(r'\*\*Condition-Specific Notes:', line_stripped, re.IGNORECASE) or
                re.match(r'\*\*Daily\s+Total\s+Calories:', line_stripped, re.IGNORECASE) or
                re.match(r'\*\*Overall Macronutrient Distribution:', line_stripped, re.IGNORECASE)):
                summary_start_idx = idx
                break

        if summary_start_idx is not None:
            meals_part = '\n'.join(lines[:summary_start_idx]).rstrip()
        else:
            meals_part = '\n'.join(lines).rstrip()

        meal_order = ["Breakfast", "Morning Snack", "Lunch", "Evening Snack", "Dinner"]

        def add_missing_summary_for_meal(body_text: str, meal_name: str) -> str:
            """
            If the section for `meal_name` in body_text does NOT already contain
            'Total calories for {meal_name}:' then append a computed summary for that meal.
            """
            pattern = re.compile(
                rf"(\*\*{re.escape(meal_name)}\*\*.*?)(?=\n\*\*[^*]+\*\*|\Z)",
                re.IGNORECASE | re.DOTALL,
            )

            def _repl(match: re.Match) -> str:
                block = match.group(1)
                if re.search(rf"Total calories for\s+{re.escape(meal_name)}\s*:", block, re.IGNORECASE):
                    return block

                meal_info = parsed_plan["meals_nutrients"].get(meal_name, {})
                meal_calories = meal_info.get("Calories", 0.0)
                meal_macros = meal_info.get("Macronutrient Ratio", {})

                summary_lines = [f"Total calories for {meal_name}: {meal_calories:.1f} kcal"]

                if meal_macros and any(meal_macros.values()):
                    summary_lines.append(
                        "Macronutrient Ratio: "
                        f"Protein {meal_macros.get('Protein', 0.0):.1f}%, "
                        f"Carbs {meal_macros.get('Carbohydrates', 0.0):.1f}%, "
                        f"Fat {meal_macros.get('Fat', 0.0):.1f}%"
                    )

                block = block.rstrip() + "\n\n" + "\n".join(summary_lines)
                return block

            return pattern.sub(_repl, body_text, count=1)

        for meal_name in meal_order:
            meals_part = add_missing_summary_for_meal(meals_part, meal_name)

        total_calories = sum(
            parsed_plan["meals_nutrients"].get(meal, {}).get("Calories", 0.0)
            for meal in meal_order
        )

        overall_macros = parsed_plan["overall_macro_distribution"]

        overall_summary = [
            f"**Daily Total Calories:** {total_calories:.1f} kcal",
            "",
            (
                f"**Overall Macronutrient Distribution:** "
                f"Protein {overall_macros['Protein']:.1f}%, "
                f"Carbohydrates {overall_macros['Carbohydrates']:.1f}%, "
                f"Fat {overall_macros['Fat']:.1f}%"
            ),
        ]

        condition_notes = parsed_plan.get("condition_specific_notes", "").strip()
        if condition_notes:
            overall_summary.extend(["", "**Condition-Specific Notes:**"])

            note_lines = [line.strip() for line in condition_notes.split('\n') if line.strip()]

            clean_notes = []
            for note in note_lines:
                cleaned = f"- {note.lstrip('- ')}"
                clean_notes.append(cleaned)

            top_5_notes = clean_notes[:5]
            overall_summary.extend(top_5_notes)
        else:
            fallback_notes = self.meal_plan_generator_tool._generate_fallback_notes({"user_id": "temp_user"})
            overall_summary.extend(["", "**Condition-Specific Notes:**"])
            overall_summary.extend([f"- {note}" for note in fallback_notes[:5]])

        final_text = meals_part + "\n\n" + "\n".join(overall_summary)
        return final_text

    async def _run_meal_plan_orchestrator(self, query: str, chat_history: List[Dict], constraints: Dict, last_agent_context: Dict, **kwargs) -> ToolResult:
        logger.info("=" * 120)
        logger.info(" ORCHESTRATOR ENTRY POINT REACHED - Starting _run_meal_plan_orchestrator()")
        logger.info("=" * 120)
        
        def create_profile_hash(profile_dict: Dict) -> str:
            return generate_profile_hash(profile_dict)
        
        user_id = constraints.get('user_id', 'unknown')
        profile_hash = create_profile_hash(constraints)
        cache_key = f"user_setup_{user_id}_{profile_hash}"
        cached_data = None
        skip_expensive_setup = False
        # FIX 1: Force recalculation - bypass Redis read cache to avoid stale data and async deadlocks
        logger.info(f"CACHE READ BYPASSED: Forcing fresh TDEE/disease recalculation for user {user_id} (key: {cache_key[:20]}...)")    
        # FIX 1 (continued): Always initialize empty - never use cached values for setup
        final_calorie_target = None
        meal_calorie_distribution = {}
        meal_macro_targets = {}
        final_foods_to_avoid = []
        all_recommended_foods = []
        abnormal_test_ranges = {}
        logger.info(f"Executing full pre-generation setup for user {user_id} (will update cache after success)")
        query_lower = query.lower()
        new_plan_patterns = [
            r'\bnew meal plan\b', r'\bdifferent meal plan\b', r'\banother meal plan\b',
            r'\bcreate.*new\b', r'\bgenerate.*new\b', r'\bmake.*new\b',
            r'\banother one\b', r'\bdifferent one\b', r'\btry again\b'
        ]
        
        is_requesting_new_plan = any(re.search(pattern, query_lower) for pattern in new_plan_patterns)
        pending_plan = last_agent_context.get("pending_meal_plan_for_save", "")
        
        if is_requesting_new_plan and pending_plan:
            rejected_foods = re.findall(r'- ([^(|]+)', pending_plan)
            rejected_foods = [food.strip().lower() for food in rejected_foods if food.strip()]
            if rejected_foods:
                logger.info(f" Aggressive Session Memory: User requested new plan. Auto-extracting {len(rejected_foods)} foods from pending plan to avoid list")
                current_avoid_foods = kwargs.get('avoid_foods', [])
                if not isinstance(current_avoid_foods, list):
                    current_avoid_foods = []
                for food in rejected_foods:
                    if food not in current_avoid_foods:
                        current_avoid_foods.append(food)
                kwargs['avoid_foods'] = current_avoid_foods
                logger.info(f" Extended avoid_foods list with {len(rejected_foods)} items. Total avoid: {len(current_avoid_foods)}")
        single_meal_patterns = [
            r'\bwhat(?:\'s| is) (?:my |for )?(?:breakfast|lunch|dinner|morning snack|evening snack)\b',
            r'\bshow (?:me )?(?:my )?(?:breakfast|lunch|dinner|morning snack|evening snack)\b',
            r'\bgive me (?:my )?(?:breakfast|lunch|dinner|morning snack|evening snack)\b',
            r'\b(?:breakfast|lunch|dinner|morning snack|evening snack) plan\b',
            r'\b(?:breakfast|lunch|dinner|morning snack|evening snack) for today\b',
        ]
        
        is_single_meal_query = any(re.search(pattern, query_lower) for pattern in single_meal_patterns)
        has_active_plan = bool(last_agent_context.get("active_meal_plan") or last_agent_context.get("pending_meal_plan_for_save"))
        if is_single_meal_query and has_active_plan:
            logger.info(f" Detected single meal query: '{query}'. Redirecting to MEAL_RETRIEVER instead of generating full plan.")
            meal_retriever = self.tools[ToolType.MEAL_RETRIEVER.value]
            meal_type = None
            if 'breakfast' in query_lower:
                meal_type = 'Breakfast'
            elif 'morning snack' in query_lower:
                meal_type = 'Morning Snack'
            elif 'lunch' in query_lower:
                meal_type = 'Lunch'
            elif 'evening snack' in query_lower:
                meal_type = 'Evening Snack'
            elif 'dinner' in query_lower:
                meal_type = 'Dinner'
            
            if meal_type:
                result = await meal_retriever.execute(meal_type=meal_type, last_agent_context=last_agent_context)
                if result.success:
                    return result
            logger.info("Could not extract specific meal type or retrieval failed. Falling back to showing full plan.")
            history_retriever = self.tools[ToolType.HISTORY_RETRIEVER.value]
            return await history_retriever.execute(
                query="show my plan",
                chat_history=chat_history,
                last_agent_context=last_agent_context,
                token=kwargs.get("token"),
                constraints=constraints
            )
        
        num_meals = 5 
        match = re.search(r'\b(\d+)\s*-?\s*meals?\b', query.lower())
        if match:
            try:
                requested_meals = int(match.group(1))
                if 3 <= requested_meals <= 5:
                    num_meals = requested_meals
                    logger.info(f"User requested a {num_meals}-meal plan.")
            except (ValueError, IndexError):
                pass
        calorie_tool = self.tools[ToolType.CALORIE_CALCULATOR.value]
        target_result = calorie_tool._extract_calorie_target(query)
        has_explicit_target = target_result.success and target_result.data is not None
        # FIX 2, 3, 5: Sequential execution with timeouts, threadpool offloading, graceful fallback
        required_for_calc = {
            'weight_kg': 'Weight', 'height_cm': 'Height', 'age': 'Age',
            'gender': 'Gender', 'activity_level': 'Activity Level'
        }
        missing_fields = [display_name for key, display_name in required_for_calc.items() if constraints.get(key) is None]
        final_calorie_target = None
        if not has_explicit_target and missing_fields:
            return ToolResult(success=True, data={"answer": f"To create an accurate meal plan, I need a complete profile. Could you please provide these missing details?\n- {', '.join(missing_fields)}"})

        try:
            loop = asyncio.get_event_loop()

            # --- TDEE Calculation (sequential, with timeout) ---
            logger.info("Starting sequential TDEE calculation with 15s timeout...")
            calorie_execute = calorie_tool.execute(operation="calculate_profile_metrics", constraints=constraints)
            if asyncio.iscoroutine(calorie_execute) or asyncio.isfuture(calorie_execute):
                tdee_results = await asyncio.wait_for(calorie_execute, timeout=15.0)
            else:
                tdee_results = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: calorie_tool.execute(operation="calculate_profile_metrics", constraints=constraints)),
                    timeout=15.0
                )
            if tdee_results.success and tdee_results.data:
                constraints.update(tdee_results.data)
            logger.info("TDEE calculation completed successfully.")

            # --- Disease Advisor (sequential, with timeout) ---
            logger.info("Starting sequential disease advisor with 15s timeout...")
            disease_advisor_tool = self.tools[ToolType.DISEASE_ADVISOR.value]
            disease_execute = disease_advisor_tool.execute(
                diseases=constraints.get('medical_conditions', []),
                constraints=constraints
            )
            if asyncio.iscoroutine(disease_execute) or asyncio.isfuture(disease_execute):
                disease_advice_results = await asyncio.wait_for(disease_execute, timeout=15.0)
            else:
                disease_advice_results = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: disease_advisor_tool.execute(
                        diseases=constraints.get('medical_conditions', []),
                        constraints=constraints
                    )),
                    timeout=15.0
                )
            logger.info("Disease advisor completed successfully.")

        except asyncio.TimeoutError:
            logger.error("Setup recalculation timed out (15s limit exceeded). Proceeding with baseline profile constraints.")
        except Exception as e:
            logger.error(f"Setup recalculation failed: {e}. Proceeding with baseline profile constraints.")
        expert_tdee_value = constraints.get('expert_tdee')
        if expert_tdee_value and float(expert_tdee_value) > 0:
            final_calorie_target = float(expert_tdee_value)
            logger.info(f" EXPERT TDEE OVERRIDE: Using expert-prescribed TDEE = {final_calorie_target:.0f} kcal (bypassing all adjustments)")
            meal_calorie_distribution = self._calculate_meal_calorie_distribution(final_calorie_target)
            meal_macro_targets = self._calculate_macro_targets_for_meals(meal_calorie_distribution, constraints)
            constraints['tdee'] = final_calorie_target
            constraints['calculated_calorie_target'] = final_calorie_target
        elif has_explicit_target:
            final_calorie_target = float(target_result.data)
            logger.info(f"Using explicit calorie target from query: {final_calorie_target:.0f} kcal")
        elif constraints.get('tdee'):
            raw_calculated_tdee = constraints['tdee']
            print(f"Estimated Tdee : {raw_calculated_tdee:.2f}")
            calculated_tdee = raw_calculated_tdee
            bmi = constraints.get('bmi')
            waist_cm = constraints.get('waist_circumference_cm')
            gender = constraints.get('gender', '').lower()
            bmi_reduction = 0
            if bmi is not None:
                if bmi < 18.5:
                    bmi_reduction = 0
                elif 24.9 <= bmi < 29.9:
                    bmi_reduction = 200
                elif 30 <= bmi < 34.9:
                    bmi_reduction = 300
                elif bmi >= 35:
                    bmi_reduction = 500
                calculated_tdee -= bmi_reduction
            print(f"Estimated Tdee after bmi reduction: {calculated_tdee:.2f}")
            waist_reduction = 0
            if waist_cm is not None and gender:
                if gender == 'female':
                    if 80 <= waist_cm < 88:
                        waist_reduction = max(waist_reduction, 100)
                    elif 88 <= waist_cm <= 100:
                        waist_reduction = max(waist_reduction, 200)
                    elif waist_cm > 100:
                        waist_reduction = max(waist_reduction, 300)
                elif gender == 'male':
                    if 90 <= waist_cm < 100:
                        waist_reduction = max(waist_reduction, 100)
                    elif 100 <= waist_cm < 108:
                        waist_reduction = max(waist_reduction, 200)
                    elif waist_cm >= 108:
                        waist_reduction = max(waist_reduction, 300)
            if waist_reduction > 0:
                calculated_tdee -= waist_reduction
                print(f"Estimated Tdee after general waist reduction: {calculated_tdee:.2f}")
            was_bmi_waist_adjusted = (bmi_reduction > 0 or waist_reduction > 0)

            vitals_adjustment = 0
            vitals_numeric = constraints.get('vitals_numeric', {})
            has_high_vitals, has_low_vitals = False, False
            bp_val = vitals_numeric.get("Blood Pressure")
            if isinstance(bp_val, dict) and (bp_val.get("systolic", 0) > 130 or bp_val.get("diastolic", 0) > 85): has_high_vitals = True
            if vitals_numeric.get("Blood Glucose") and vitals_numeric["Blood Glucose"] > 100: has_high_vitals = True
            body_fat_val = vitals_numeric.get("Body Fat %")
            if body_fat_val is not None:
                if (gender == 'male' and body_fat_val > 25) or (gender == 'female' and body_fat_val > 32): has_high_vitals = True
            if vitals_numeric.get("Blood Glucose") and vitals_numeric["Blood Glucose"] < 70: has_low_vitals = True
            if body_fat_val is not None:
                if (gender == 'male' and body_fat_val < 6) or (gender == 'female' and body_fat_val < 14): has_low_vitals = True
            if has_high_vitals and not has_low_vitals: vitals_adjustment = -100
            elif has_low_vitals and not has_high_vitals: vitals_adjustment = 100
            calculated_tdee += vitals_adjustment
            print(f"Estimated Tdee after vitals : {calculated_tdee:.2f}")
            min_threshold_women, min_threshold_men = 1400, 1700
            if vitals_adjustment < 0:
                if (gender == 'female' and calculated_tdee < min_threshold_women) and abs(vitals_adjustment) > 50:
                    calculated_tdee += (abs(vitals_adjustment) - 50)
                elif (gender == 'male' and calculated_tdee < min_threshold_men) and abs(vitals_adjustment) > 50:
                    calculated_tdee += (abs(vitals_adjustment) - 50)
            print(f"Estimated tdee after special vital consideration : {calculated_tdee:.2f}")
            if gender == 'female': calculated_tdee = max(calculated_tdee, 1200)
            elif gender == 'male': calculated_tdee = max(calculated_tdee, 1500)
            print(f"Estimated tdee after male and female minimal : {calculated_tdee:.2f}")
            user_goal_info = constraints.get('goal', {})
            user_goal_type = user_goal_info.get('type', 'Weight Maintenance')
            adjustment_amount = 300
            if user_goal_type == "Lose Weight":
                calculated_tdee -= adjustment_amount
                print(f"Applying 'Lose Weight' goal adjustment: {calculated_tdee:.2f}")
            elif user_goal_type == "Gain Weight":
                calculated_tdee += adjustment_amount
                print(f"Applying 'Gain Weight' goal adjustment: {calculated_tdee:.2f}")
            else:
                print(f"Goal is '{user_goal_type}'. No additional goal-based adjustment applied.")
            
            final_calorie_target = calculated_tdee
            print(f"Final Target Calorie count after goal adjustment: {final_calorie_target:.2f}")
            
            if final_calorie_target is None or final_calorie_target <= 0:
                logger.warning(" CRITICAL: final_calorie_target is None after TDEE calculation - using WHO average fallback")
                fallback_gender = constraints.get('gender', 'male').lower()
                fallback_age = constraints.get('age', 30)
                fallback_weight = 70 if fallback_gender == 'male' else 60
                fallback_height = 170 if fallback_gender == 'male' else 160
                
                if fallback_gender == 'male':
                    fallback_bmr = 10 * fallback_weight + 6.25 * fallback_height - 5 * fallback_age + 5
                else:
                    fallback_bmr = 10 * fallback_weight + 6.25 * fallback_height - 5 * fallback_age - 161
                
                final_calorie_target = fallback_bmr * 1.2 
                logger.warning(f"   Calculated fallback TDEE: {final_calorie_target:.0f} kcal ({fallback_gender}/{fallback_age}y/{fallback_weight}kg/{fallback_height}cm)")
            meal_calorie_distribution = self._calculate_meal_calorie_distribution(final_calorie_target)
            meal_macro_targets = self._calculate_macro_targets_for_meals(meal_calorie_distribution, constraints)
            logger.info(f"Calculated macro targets for all meals to eliminate LLM hallucination.")
            constraints['tdee'] = final_calorie_target
            constraints['calculated_calorie_target'] = final_calorie_target
            token = kwargs.get("token")
            document_id_for_update = None
            db_tool = self.tools[ToolType.DATABASE_PERSISTENCE.value]
            if token:
                db_result2 = await db_tool.execute(operation="get_all_blood_reports", token=token)
                if db_result2.success and db_result2.data and db_result2.data.get('data'):
                    raw_data = db_result2.data['data']
                    processed_translations = process_blood_report_translations(raw_data)
                    combined_blood_report_data = {}
                    combined_abnormal_test_ranges = {}
                    combined_foods_to_avoid = []
                    combined_recommended_foods = []
                    for translation in processed_translations:
                        blood_data = translation.get("blood_report_data", {})
                        combined_blood_report_data.update(blood_data)
                        abnormal_ranges = translation.get("abnormal_test_ranges", {})
                        combined_abnormal_test_ranges.update(abnormal_ranges)
                        foods_to_avoid = translation.get("foods_to_avoid_from_report", [])
                        for food in foods_to_avoid:
                            if food not in combined_foods_to_avoid:
                                combined_foods_to_avoid.append(food)
                        recommended_foods = translation.get("recommended_foods_from_report", [])
                        for food in recommended_foods:
                            if food not in combined_recommended_foods:
                                combined_recommended_foods.append(food)
                    if combined_blood_report_data:
                        constraints.update({
                            "blood_report_analysis_data": combined_blood_report_data,
                            "abnormal_test_ranges": combined_abnormal_test_ranges,
                            "foods_to_avoid_from_report": combined_foods_to_avoid,
                            "recommended_foods_from_report": combined_recommended_foods
                        })
            blood_report_path = constraints.get('blood_report_path')
            if blood_report_path and os.path.exists(blood_report_path):
                logger.info(f"New blood report file found at {blood_report_path}. Analyzing...")
                blood_analyzer_tool = self.tools[ToolType.BLOOD_REPORT_ANALYZER.value]
                analysis_result_tool = await blood_analyzer_tool.execute(
                    file_path=blood_report_path, token=token, user_id=constraints.get('user_id')
                )
                if analysis_result_tool.success and analysis_result_tool.data:
                    updated_constraints_from_report = analysis_result_tool.data.get("updated_constraints", {})
                    constraints.update(updated_constraints_from_report)
                    analysis_payload_to_save = analysis_result_tool.metadata.get("blood_report_analysis_payload")
                    if analysis_payload_to_save:
                        doc_id = document_id_for_update if document_id_for_update else str(uuid.uuid4())
                        save_payload = {"doc_id": doc_id, "translation": analysis_payload_to_save}
                        save_result = await db_tool.execute(
                            operation="save_blood_report_analysis",
                            analysis_data=save_payload,
                            token=token
                        )
                        if not save_result.success:
                            logger.error(f"Failed to save new blood report analysis: {save_result.error}")
                        else:
                            logger.info(f"Successfully saved/updated blood report analysis with doc_id: {doc_id}.")
                    try: 
                        os.remove(blood_report_path)
                        constraints.pop('blood_report_path', None)
                    except Exception as e:
                        logger.error(f"Error cleaning up blood report file {blood_report_path}: {e}")
                else:
                    logger.error(f"Local blood report analysis failed: {analysis_result_tool.error}")
            ALLOWED_NON_VEG_WHITELIST = [
                'egg', 'eggs', 'chicken', 'meat', 'fish', 'beef', 'seafood',
                'chicken breast', 'chicken thigh', 'grilled chicken', 'boiled chicken',
                'fish fillet', 'grilled fish', 'salmon', 'tuna', 'cod', 'tilapia',
                'shrimp', 'prawns', 'crab', 'lobster', 'shellfish',
                'beef steak', 'ground beef', 'lean beef',
                'egg white', 'boiled egg', 'scrambled eggs', 'omelette'
            ]
            all_foods_to_avoid = set()
            raw_allergies = constraints.get('allergies', [])
            if raw_allergies:
                flattened_allergies = flatten_and_clean_avoid_foods(raw_allergies)
                all_foods_to_avoid.update(flattened_allergies)
                logger.info(f" Flattened allergies: {raw_allergies} → {flattened_allergies}")
                ALLERGY_EXPANSION_MAP = {
                    'tree nut': ['pecan', 'macadamia', 'pistachio', 'hazelnut', 'pine nut', 'brazil nut'],
                    'nut': ['pecan', 'macadamia', 'pistachio', 'hazelnut', 'pine nut', 'brazil nut'],
                }
                for allergy_keyword, expanded_foods in ALLERGY_EXPANSION_MAP.items():
                    if any(allergy_keyword in ' '.join(flattened_allergies).lower() for _ in [None]):
                        all_foods_to_avoid.update(expanded_foods)
                        logger.info(f" Allergy Expansion: '{allergy_keyword}' detected → Auto-banning: {expanded_foods}")
            raw_symptom_foods = constraints.get('symptom_aggravating_foods', [])
            if raw_symptom_foods:
                flattened_symptom_foods = flatten_and_clean_avoid_foods(raw_symptom_foods)
                all_foods_to_avoid.update(flattened_symptom_foods)
                logger.info(f" Flattened symptom foods: {raw_symptom_foods} → {flattened_symptom_foods}")
            DIGESTIVE_ISSUE_FOOD_MAP = {
                "Acid reflux or heartburn": ["Spicy Foods", "Caffeinated beverages", "Chocolate", "Garlic and onion", "High-Fat foods", "Fruits high in acid", "Fried or greasy foods"],
                "Irritable bowel syndrome (IBS)": ["High-fiber foods", "Gluten-containing foods", "Dairy products", "Carbonated drinks", "Fried or greasy foods"],
                "Occasional Bloating": ["Carbonated drinks", "High-fiber foods", "Dairy products", "Legumes", "Beans"],
                "Excessive Gas": ["Carbonated drinks", "High-fiber foods", "Dairy products", "Legumes", "Beans"],
                "Diarrhea": ["High-Fat foods", "Fried or greasy foods", "Dairy products", "Spicy Foods", "Sugar-Free (e.g. for Diabetes)"],
            }
            for issue in constraints.get("digestive_issues", []):
                if issue in DIGESTIVE_ISSUE_FOOD_MAP:
                    digestive_foods = DIGESTIVE_ISSUE_FOOD_MAP[issue]
                    flattened_digestive_foods = flatten_and_clean_avoid_foods(digestive_foods)
                    all_foods_to_avoid.update(flattened_digestive_foods)
                    logger.info(f" Flattened digestive issue foods ({issue}): {digestive_foods} → {flattened_digestive_foods}")
            
            diet_pref = constraints.get('dietary_preference', '')
            if isinstance(diet_pref, list):
                diet_pref = ", ".join(diet_pref)
            diet_pref_lower = diet_pref.lower()
            
            BANNED_EXOTIC_MEAT = [
                'parrot', 'frog', 'rabbit', 'deer', 'venison', 'horse', 'shark',
                'duck', 'goose', 'pigeon', 'ostrich', 'emu', 'alligator',
                'crocodile', 'snake', 'rattlesnake', 'turtle', 'kangaroo', 'bison', 
                'buffalo', 'wild boar', 'caribou', 'reindeer', 'elk', 'bear', 
                'camel', 'squirrel', 'squab', 'game meat', 'bushmeat', 'exotic meat',
                'grouse', 'pheasant', 'partridge', 'wild bird', 'wild game',
                'octopus', 'squid', 'cuttlefish', 'snail', 'escargot',
                'sea urchin', 'urchin', 'jellyfish'
            ]
            
            BANNED_ORGAN_MEATS = [
                'brain', 'liver', 'kidney', 'heart', 'tongue', 'tripe', 
                'sweetbread', 'gizzard', 'giblets', 'blood', 'offal',
                'goat brain', 'chicken liver', 'beef liver', 'pork liver',
                'foot', 'feet', 'chicken feet', 'cow foot', 'pig feet', 'trotters',
                'skin', 'cow skin', 'pork skin', 'chicken skin', 'pomo',
                'intestine', 'intestines', 'chitterlings', 'chitlins',
                'stomach', 'oxtail', 'tail', 'head', 'cheek', 'jowl',
                'spleen', 'pancreas', 'thymus', 'marrow', 'bone marrow',
                'walkie talkies', 'chicken head'
            ]
            
            BANNED_RAW_MEAT = [
                'raw meat', 'raw beef', 'raw chicken', 'raw pork', 'raw fish',
                'tartare', 'steak tartare', 'beef tartare', 'carpaccio',
                'ceviche', 'sashimi', 'sushi', 'poke', 'crudo',
                'gored gored', 'kitfo', 'yukhoe', 'mett', 'ossenworst',
                'arrachera raw', 'wild meat raw', 'raw minced', 'raw ground'
            ]
            
            BANNED_POWDER_FLOUR = [
                'powder', 'flour', 'powdered', 'floured',
                'protein powder', 'whey powder', 'milk powder',
                'cocoa powder', 'baking powder', 'chili powder',
                'curry powder', 'spice powder', 'masala powder',
                'wheat flour', 'rice flour', 'corn flour', 'maize flour',
                'all purpose flour', 'refined flour', 'maida',
                'gram flour', 'besan', 'chickpea flour',
                'almond flour', 'coconut flour', 'oat flour',
                'rye flour', 'barley flour', 'millet flour',
                'soy flour', 'tapioca flour', 'cornstarch',
                'deli', 'deli meat', 'ham hock', 'hot dog', 'frankfurter', 'sausage',
                'marshmallow', 'candy', 'syrup', 'corn syrup', 'maple syrup',
                'fast food', 'frozen meal', 'prepacked', 'pre-packed', 'restaurant',
                'canned', 'gravy', 'diet frozen', 'stuffed', 'with added vegetables',
                'with sauce', 'with meat', 'with poultry', 'cheeseburger', 'double cheeseburger',
                'burger', 'mcdonalds', 'kfc', 'pizza hut', 'taco bell', 'subway',
                'ready meal', 'microwave meal', 'tv dinner', 'instant', 'boxed meal'
            ]
            
            all_foods_to_avoid.update(BANNED_EXOTIC_MEAT)
            all_foods_to_avoid.update(BANNED_ORGAN_MEATS)
            all_foods_to_avoid.update(BANNED_RAW_MEAT)
            all_foods_to_avoid.update(BANNED_POWDER_FLOUR)
            logger.info(f" BANNED EXOTIC MEATS: {len(BANNED_EXOTIC_MEAT)} items added to avoid list")
            logger.info(f" BANNED ORGAN MEATS/UNUSUAL CUTS: {len(BANNED_ORGAN_MEATS)} items added to avoid list")
            logger.info(f" BANNED RAW MEAT DISHES: {len(BANNED_RAW_MEAT)} items added to avoid list")
            logger.info(f" BANNED POWDER & FLOUR ITEMS: {len(BANNED_POWDER_FLOUR)} items added to avoid list")
            
            VEGETARIAN_AVOID = ['chicken', 'beef', 'pork', 'lamb', 'mutton', 'fish', 'shellfish', 'turkey', 'duck', 'venison', 'bacon', 'ham', 'sausage', 'meat', 'gelatin', 'animal broth', 'red meat', 'eggs', 'egg']
            VEGAN_AVOID = VEGETARIAN_AVOID + ['milk', 'cheese', 'yogurt', 'butter', 'ghee', 'honey', 'dairy', 'whey', 'casein', 'ice cream', 'paneer', 'chenna', 'chhena', 'malai', 'mawa', 'khoa', 'khoya', 'buttermilk', 'chaas', 'lassi', 'curd', 'dahi']

            PESCATARIAN_AVOID = [
                'chicken', 'beef', 'pork', 'lamb', 'mutton', 'turkey', 'duck', 
                'venison', 'bacon', 'ham', 'sausage', 'red meat', 'poultry',
                'chicken breast', 'chicken thigh', 'ground beef', 'beef steak',
                'meat' 
            ]
            
            if 'pescatarian' in diet_pref_lower:
                all_foods_to_avoid.update(PESCATARIAN_AVOID)
                logger.info(f" PESCATARIAN MODE: Excluded all poultry and red meat. Only fish/seafood allowed.")
                logger.info(f" Banned items: {PESCATARIAN_AVOID}")
            elif 'vegan' in diet_pref_lower and 'non' not in diet_pref_lower:
                all_foods_to_avoid.update(VEGAN_AVOID)
                logger.info(f"VEGAN MODE: Excluded all animal products ({len(VEGAN_AVOID)} items)")
            elif 'vegetarian' in diet_pref_lower and 'non' not in diet_pref_lower:
                all_foods_to_avoid.update(VEGETARIAN_AVOID)
                logger.info(f" VEGETARIAN MODE: Excluded all meat/fish/eggs ({len(VEGETARIAN_AVOID)} items)")

            user_restrictions = constraints.get('restrictions', [])
            if user_restrictions:
                for restriction in user_restrictions:
                        foods_to_avoid_for_restriction = self.DIETARY_RESTRICTION_FOOD_MAP.get(restriction)

                        if foods_to_avoid_for_restriction:
                            logger.info(f"Adding avoid list for restriction: '{restriction}'")
                            print(f"--- AVOIDING FOODS FOR: {restriction} ---")
                            print(foods_to_avoid_for_restriction)
                            print("-------------------------------------------------")
                            flattened_restriction_foods = flatten_and_clean_avoid_foods(foods_to_avoid_for_restriction)
                            all_foods_to_avoid.update(flattened_restriction_foods)
                            logger.info(f" Flattened restriction foods: {foods_to_avoid_for_restriction} → {flattened_restriction_foods}")
            user_conditions = constraints.get('medical_conditions', [])
            if user_conditions:
                for user_condition_str in user_conditions:
                    user_condition_lower = user_condition_str.lower()
                    
                    if 'type 2' in user_condition_lower and 'diabetes' in user_condition_lower:
                        diabetic_liquid_sugars = ['nectar', 'fruit juice', 'syrup', 'concentrate', 'soda', 'sweetened']
                        all_foods_to_avoid.update(diabetic_liquid_sugars)
                        logger.info(f"Type 2 Diabetes detected → Hard-banning liquid sugars: {diabetic_liquid_sugars}")
                    
                    for disease_entry in self.data_service.disease_data:
                        main_disease_name = disease_entry.get("disease", "").lower()
                        if not main_disease_name:
                            continue

                        keyword = main_disease_name.split()[0].replace(',', '')
                        if keyword and keyword in user_condition_lower:
                            logger.info(f"MATCH FOUND: User condition '{user_condition_str}' contains keyword '{keyword}'. Aggregating all foods to avoid from '{main_disease_name}'.")
                            disease_foods = disease_entry.get("foods_to_avoid", [])
                            flattened_disease_foods = flatten_and_clean_avoid_foods(disease_foods)
                            all_foods_to_avoid.update(flattened_disease_foods)
                            logger.info(f" Flattened disease foods: {disease_foods} → {flattened_disease_foods}")
                            break

            blood_report_foods = constraints.get("foods_to_avoid_from_report", [])
            if blood_report_foods:
                flattened_blood_report_foods = flatten_and_clean_avoid_foods(blood_report_foods)
                all_foods_to_avoid.update(flattened_blood_report_foods)
                logger.info(f" Flattened blood report foods: {blood_report_foods} → {flattened_blood_report_foods}")

            session_rejected_foods = last_agent_context.get('session_rejected_foods', [])
            if session_rejected_foods:
                logger.info(f"Session Memory: Adding {len(session_rejected_foods)} rejected foods to avoid list")
                flattened_rejected_foods = flatten_and_clean_avoid_foods(session_rejected_foods)
                all_foods_to_avoid.update(flattened_rejected_foods)
                logger.info(f" Flattened rejected foods: {session_rejected_foods} → {flattened_rejected_foods}")

            final_foods_to_avoid = sorted([food for food in list(all_foods_to_avoid) if food and food != 'none'])
            all_recommended_foods = constraints.get("recommended_foods_from_report", [])
            abnormal_test_ranges = constraints.get("abnormal_test_ranges", {})
            
            medical_banned_foods = set()
            
            if constraints.get('allergies'):
                for allergy_item in constraints['allergies']:
                    matched_whitelist = [item for item in ALLOWED_NON_VEG_WHITELIST if allergy_item.lower() in item.lower()]
                    if matched_whitelist:
                        medical_banned_foods.update(matched_whitelist)
                        logger.warning(f" MEDICAL FIREWALL: Allergy '{allergy_item}' blocked whitelisted items: {matched_whitelist}")
                    
                    allergy_lower = allergy_item.lower().strip()
                    if 'corn' in allergy_lower or 'maize' in allergy_lower:
                        corn_processed_ban = ['canned', 'deli', 'deli meat', 'frankfurter', 'sausage', 'marshmallow', 'syrup', 'corn syrup', 'processed', 'packaged']
                        medical_banned_foods.update(corn_processed_ban)
                        logger.warning(f" SEVERE CORN ALLERGY DETECTED → Physically banning ALL processed foods with hidden corn: {corn_processed_ban}")
                    
                    if 'dairy' in allergy_lower or 'milk' in allergy_lower or 'lactose' in allergy_lower:
                        dairy_processed_ban = ['deli', 'deli meat', 'processed cheese', 'canned soup', 'instant', 'packaged snack', 'processed']
                        medical_banned_foods.update(dairy_processed_ban)
                        logger.warning(f" SEVERE DAIRY ALLERGY DETECTED → Physically banning processed foods with hidden dairy: {dairy_processed_ban}")
            
            if constraints.get('medical_conditions'):
                for condition in constraints['medical_conditions']:
                    condition_lower = condition.lower()
                    if 'type 2' in condition_lower and 'diabetes' in condition_lower:
                        high_fat_meats = ['beef', 'beef steak', 'ground beef', 'red meat']
                        medical_banned_foods.update(high_fat_meats)
                        logger.warning(f"MEDICAL FIREWALL: Type 2 Diabetes → Restricted high-fat red meats: {high_fat_meats}")
                    if any(term in condition_lower for term in ['gerd', 'acid reflux', 'heartburn']):
                        processed_meats = ['bacon', 'ham', 'sausage']
                        medical_banned_foods.update(processed_meats)
                        logger.warning(f"MEDICAL FIREWALL: GERD/Acid Reflux → Restricted processed meats: {processed_meats}")
            
            if constraints.get('digestive_issues'):
                for issue in constraints['digestive_issues']:
                    if 'ibs' in issue.lower() or 'irritable bowel' in issue.lower():
                        fatty_meats = ['beef', 'bacon', 'sausage']
                        medical_banned_foods.update(fatty_meats)
                        logger.warning(f"MEDICAL FIREWALL: IBS → Restricted fatty meats: {fatty_meats}")
            
            diabetic_hypertension_banned = []
            
            if constraints.get('medical_conditions'):
                for condition in constraints['medical_conditions']:
                    condition_lower = condition.lower()
                    if 'diabetes' in condition_lower or 'diabetic' in condition_lower:
                        diabetic_hypertension_banned = ['chips', 'waffle', 'waffles', 'pudding', 'crackers', 'canned', 'shake', 'shakes', 'syrup', 'fries', 'french fries']
                        medical_banned_foods.update(diabetic_hypertension_banned)
                        logger.warning(f"DIABETIC FIREWALL: Diabetes detected → Blocked processed foods: {diabetic_hypertension_banned}")
                        break
            
            if constraints.get('vitals_numeric'):
                systolic_bp = constraints['vitals_numeric'].get('Blood Pressure Systolic', 0)
                if systolic_bp > 120:
                    if not diabetic_hypertension_banned:  
                        diabetic_hypertension_banned = ['chips', 'waffle', 'waffles', 'pudding', 'crackers', 'canned', 'shake', 'shakes', 'syrup', 'fries', 'french fries']
                        medical_banned_foods.update(diabetic_hypertension_banned)
                    logger.warning(f"HYPERTENSION FIREWALL: BP {systolic_bp} > 120 → Blocked processed foods: {diabetic_hypertension_banned}")
            
            all_foods_to_avoid.update(medical_banned_foods)
            if medical_banned_foods:
                logger.warning(f" MEDICAL SAFETY FIREWALL ACTIVATED: {len(medical_banned_foods)} foods HARD-BANNED for safety")
            
            logger.info(f"=" * 80)
            logger.info(f"FINAL COMBINED FOODS TO AVOID (STRICT HIERARCHY APPLIED):")
            logger.info(f" Total unique avoid words: {len(final_foods_to_avoid)}")
            logger.info(f" Sources included (in priority order):")
            logger.info(f" Banned exotic meats, organ meats/unusual cuts & raw meat dishes (TIER 1 - Always blocked)")
            logger.info(f" Medical Safety Firewall (TIER 3 - Highest priority)")
            logger.info(f" / Dietary preference overrides (TIER 2)")
            logger.info(f" Flattened allergies")
            logger.info(f" Flattened symptom aggravating foods")
            logger.info(f" Flattened digestive issue foods")
            logger.info(f" Flattened restriction foods")
            logger.info(f" Flattened disease-specific foods")
            logger.info(f" Flattened blood report foods")
            logger.info(f" Flattened session rejected foods")
            logger.info(f" Combined list: {final_foods_to_avoid}")
            logger.info(f"=" * 80)
            
            allowed_proteins = [item for item in ALLOWED_NON_VEG_WHITELIST if item not in all_foods_to_avoid]
            is_veg_or_vegan = (('vegetarian' in diet_pref_lower or 'vegan' in diet_pref_lower) and 'non' not in diet_pref_lower)
            if is_veg_or_vegan:
                logger.info(f" USER IS VEGETARIAN/VEGAN: No animal proteins will be included (dietary_preference='{diet_pref}')")
            else:
                logger.info(f" ALLOWED PROTEINS FOR THIS USER: {allowed_proteins[:10]}... (dietary_preference='{diet_pref}')")
            
            if self.redis_client:

                is_meal_checkin_adjustment = kwargs.get('is_meal_checkin_adjustment', False)

                is_valid_tdee = 800 <= final_calorie_target <= 8000
                
                if is_meal_checkin_adjustment:
                    logger.warning(f" CACHE SKIP: Meal check-in adjustment detected - not caching to prevent TDEE corruption")
                    logger.warning(f"   Current final_calorie_target={final_calorie_target:.0f} is remaining calories, NOT actual TDEE")
                elif not is_valid_tdee:
                    logger.warning(f" CACHE SKIP: Invalid TDEE value {final_calorie_target:.0f} kcal (outside 800-8000 range)")
                    logger.warning(f"   This might be remaining calories from meal check-in - refusing to cache")
                else:
                    try:
                        cache_data = {
                            'final_calorie_target': final_calorie_target,
                            'meal_calorie_distribution': meal_calorie_distribution,
                            'meal_macro_targets': meal_macro_targets,
                            'avoid_foods': final_foods_to_avoid,
                            'recommended_foods': all_recommended_foods,
                            'abnormal_test_ranges': abnormal_test_ranges,
                            'blood_report_analysis_data': constraints.get('blood_report_analysis_data', {})
                        }
                        cache_json = json.dumps(cache_data)
                        self.redis_client.setex(cache_key, 86400, cache_json) 
                        logger.info(f" Cached pre-generation setup for 24h (key: {cache_key[:20]}..., TDEE={final_calorie_target:.0f} kcal)")
                    except Exception as e:
                        logger.warning(f"Redis cache write failed: {e}")



        meal_plan_gen_kwargs = {
            "query": query,
            "chat_history": chat_history,
            "constraints": copy.deepcopy(constraints), 
            "last_agent_context": last_agent_context,
            "calorie_target_for_llm": final_calorie_target,
            "num_meals": num_meals,
            "temperature": 0.8,
            "is_modification": kwargs.get("is_modification", False),
            "user_goal": constraints.get('goal', {}).get('type', 'Weight Maintenance'),
            "previous_day_meal_plan": last_agent_context.get("previous_day_meal_plan"),
            "original_plan_context": kwargs.get("original_plan_context"),
            "avoid_foods": copy.deepcopy(final_foods_to_avoid), 
            "eaten_foods_text": kwargs.get("eaten_foods_text"),
            "meal_type_to_update": kwargs.get("meal_type_to_update"),
            "specific_meal_to_modify": kwargs.get("specific_meal_to_modify"),
            "recommended_foods": copy.deepcopy(all_recommended_foods),
            "abnormal_test_ranges": abnormal_test_ranges,
            "meal_calorie_distribution": meal_calorie_distribution,
            "start_date": kwargs.get("start_date"),  # Custom start date for meal plans
            "end_date": kwargs.get("end_date"),      # Custom end date for meal plans
            "meal_macro_targets": meal_macro_targets, 
        }
        meal_plan_gen_kwargs['num_meals'] = num_meals
        MAX_RETRIES = 2
        generation_result = None
        
        logger.info("=" * 80)
        logger.info(" ATTEMPTING DETERMINISTIC PYTHON MEAL GENERATION (0.5s target)")
        logger.info("=" * 80)
        
        deterministic_success = False
        deterministic_start_time = time.time()
        
        try:
            logger.info(" Step 1: Creating DeterministicMealGenerator instance...")
            
            from helpers.food_database_cache import get_food_database_cache
            food_cache = get_food_database_cache()
            cached_foods = food_cache.get_foods_data()  
            
            deterministic_generator = DeterministicMealGenerator(
                data_service=self.data_service,
                foods_data_cache=cached_foods 
            )
            logger.info(" Step 1 COMPLETE: DeterministicMealGenerator instantiated successfully")
            
            logger.info(" Step 2: Building deterministic profile...")
            deterministic_profile = {
                'dietary_preference': constraints.get('dietary_preference', 'non vegetarian'),
                'cuisine': constraints.get('cuisine', 'american'),  
                'allergies': constraints.get('allergies', []),
                'medical_conditions': constraints.get('medical_conditions', []),
                'restrictions': constraints.get('restrictions', []),
                'symptom_aggravating_foods': constraints.get('symptom_aggravating_foods', []),  
                'digestive_issues': constraints.get('digestive_issues', []), 
                'avoid_list': copy.deepcopy(final_foods_to_avoid),  
                'user_id': constraints.get('user_id', 'unknown'),
                'tdee': constraints.get('tdee'),
                'expert_tdee': constraints.get('expert_tdee'),
                'age': constraints.get('age'),
                'gender': constraints.get('gender'),
                'height_cm': constraints.get('height_cm'),
                'weight_kg': constraints.get('weight_kg'),
                'activity_level': constraints.get('activity_level'),
                'waist_cm': constraints.get('waist_cm')
            }
            logger.info(f" Step 2 COMPLETE: Profile built with dietary_preference={deterministic_profile['dietary_preference']}, "
                       f"allergies={len(deterministic_profile['allergies'])}, "
                       f"symptom_aggravating_foods={len(deterministic_profile['symptom_aggravating_foods'])}, "
                       f"🔥 avoid_list={len(deterministic_profile['avoid_list'])} HARD-BANNED ITEMS, "
                       f"TDEE={deterministic_profile.get('tdee')} kcal")
            
            plan_variation = int(datetime.now().timestamp() * 1000000) % 1000000 
            current_date = datetime.now().strftime('%Y-%m-%d')
            
            previous_days_foods = []
            try:
                day1_foods = last_agent_context.get("last_meal_used_foods")
                if day1_foods and isinstance(day1_foods, list):
                    previous_days_foods.append(set(day1_foods))
                    logger.info(f" [DATABASE] Day -1 foods: {len(day1_foods)} items")
                
                day2_foods = last_agent_context.get("last_meal_used_foods_day2")
                if day2_foods and isinstance(day2_foods, list):
                    previous_days_foods.append(set(day2_foods))
                    logger.info(f" [DATABASE] Day -2 foods: {len(day2_foods)} items")
                
                if self.redis_client:
                    try:
                        redis_key = f"meal_diversity:{user_id}:{current_date}"
                        logger.info(f" [REDIS] Retrieving meal diversity from: {redis_key}")
                    
                        loop = asyncio.get_event_loop()
                        redis_foods = await asyncio.wait_for(
                            loop.run_in_executor(None, self.redis_client.smembers, redis_key),
                            timeout=5.0
                        )
                    
                        if redis_foods:
                            redis_foods_set = set(redis_foods)
                            previous_days_foods.append(redis_foods_set)
                            logger.info(f" [REDIS SESSION] Today's unsaved foods: {len(redis_foods_set)} items")
                        else:
                            logger.debug(f" [REDIS SESSION] No unsaved foods found for today")
                    except asyncio.TimeoutError:
                        logger.warning(f" [REDIS TIMEOUT] smembers timed out after 5s - skipping diversity lookup")
                    except Exception as redis_err:
                        logger.warning(f" Redis session cache lookup failed: {redis_err}")
                else:
                    logger.error(f" [REDIS DISABLED] Redis client is None - cannot retrieve session foods")
                
                if previous_days_foods:
                    total_unique = len(set().union(*previous_days_foods))
                    logger.info(f" TOTAL DIVERSITY TRACKING: {total_unique} unique foods from {len(previous_days_foods)} source(s)")
                else:
                    logger.info(" No previous meal history - generating fresh meal plan")
            except Exception as e:
                logger.warning(f" Failed to retrieve previous days' foods: {e}")
                previous_days_foods = []
            
            logger.info(f" Step 3: Calling generate_deterministic_meal_plan() with caloric buckets...")
            logger.info(f"    Date: {current_date} |  Variation: {plan_variation} (ensures uniqueness)")
            
            from ai.meal_plan_fallback import generate_meal_plan_with_fallback
            
            deterministic_plan_json = generate_meal_plan_with_fallback(
                user_profile=deterministic_profile,
                meal_calorie_distribution=meal_calorie_distribution,  
                target_date=current_date,  
                plan_variation=plan_variation,
                previous_days_foods=previous_days_foods if previous_days_foods else None  
            )
            logger.info(f" Step 3 COMPLETE: Deterministic plan generated with {len(deterministic_plan_json.get('meal_plan', {}))} meals")
            
            deterministic_elapsed = time.time() - deterministic_start_time
            logger.info(f" DETERMINISTIC GENERATION SUCCESS in {deterministic_elapsed:.2f}s")
            
            logger.info(" SKIPPING programmatic scaling - universal scaling already completed in deterministic generator")
            logger.info(f"   Universal scaling achieved {deterministic_plan_json.get('daily_totals', {}).get('total_calories', 0)} kcal")
            
            scaled_deterministic_plan = deterministic_plan_json
            
            scaled_deterministic_plan = self._format_final_display_names(scaled_deterministic_plan)
            user_restrictions = constraints.get('restrictions', [])
            deterministic_plan_text = self._convert_plan_json_to_text(scaled_deterministic_plan, restrictions=user_restrictions)
            final_formatted_plan_text = self.format_portions_in_plan(deterministic_plan_text)
            
            # 🔥 NEW: Enrich deterministic meal plan with metadata
            try:
                if final_formatted_plan_text and 'FoodID:' not in final_formatted_plan_text:
                    logger.info("🎯 Enriching deterministic meal plan with metadata...")
                    from helpers.meal_plan_enrichment import enrich_meal_plan_with_metadata
                    final_formatted_plan_text = enrich_meal_plan_with_metadata(final_formatted_plan_text)
                    logger.info("✅ Deterministic meal plan enrichment complete!")
            except Exception as e:
                logger.error(f"❌ Error enriching deterministic meal plan: {e}", exc_info=True)
                logger.warning("⚠️ Continuing with un-enriched deterministic meal plan")
            
            final_plan_json = self.meal_plan_generator_tool._parse_meal_plan_text_to_json(final_formatted_plan_text)
            
            current_used_foods = deterministic_plan_json.get('used_foods', [])
            previous_day1_foods = last_agent_context.get("last_meal_used_foods", [])
            previous_day2_foods = last_agent_context.get("last_meal_used_foods_day2", [])
            
            total_diversity_count = len(set(previous_day1_foods or []) | set(previous_day2_foods or []) | set(current_used_foods))
            
            logger.info(f" Saving {len(current_used_foods)} foods for multi-day tracking")
            
            if self.redis_client and current_used_foods:
                try:
                    redis_key = f"meal_diversity:{user_id}:{current_date}"
                    logger.info(f" [REDIS] Storing {len(current_used_foods)} foods for diversity tracking: {redis_key}")
                    
                    loop = asyncio.get_event_loop()
                    await asyncio.wait_for(
                        loop.run_in_executor(None, self.redis_client.delete, redis_key),
                        timeout=5.0
                    )
                    await asyncio.wait_for(
                        loop.run_in_executor(None, self.redis_client.sadd, redis_key, *current_used_foods),
                        timeout=5.0
                    )
                    await asyncio.wait_for(
                        loop.run_in_executor(None, self.redis_client.expire, redis_key, 86400),
                        timeout=5.0
                    )
                    
                    logger.info(f" [REDIS SESSION] Stored {len(current_used_foods)} foods (TTL: 24h)")
                except asyncio.TimeoutError:
                    logger.warning(f" [REDIS TIMEOUT] Diversity write timed out after 5s - skipping")
                except Exception as redis_err:
                    logger.warning(f" Redis session cache storage failed: {redis_err}")
            elif not self.redis_client:
                logger.error(f" [REDIS DISABLED] Redis client is None - session tracking disabled!")
                logger.error(f"   THIS MEANS: Unsaved meal plans will NOT be tracked for diversity")
            elif not current_used_foods:
                logger.error(f" [REDIS SKIP] No foods to store (current_used_foods is empty)")

            try:
                from helpers.redis_client import get_redis_cache
                redis_cache = get_redis_cache()
                redis_client = redis_cache.redis_client
                context_user_id = constraints.get('user_id', user_id) 
                context_session_id = constraints.get('session_id', 'default_session')
                context_key = f"agent_context:{context_user_id}:{context_session_id}"
                
                updated_context = {
                    **last_agent_context,
                    "last_meal_used_foods": current_used_foods, 
                    "last_meal_used_foods_day2": previous_day1_foods,  
                    "last_meal_plan_generated_at": datetime.now().isoformat()
                }
                
                redis_client.setex(
                    context_key,
                    3600 * 72,  
                    json.dumps(updated_context)
                )
                logger.info(f" Food history persisted to context (3-day retention) - {len(current_used_foods)} Day -1, {len(previous_day1_foods)} Day -2")
            except Exception as ctx_err:
                logger.warning(f" Failed to persist food history to context: {ctx_err}")
            
            generation_result = ToolResult(
                success=True,
                data={
                    "meal_plan_text": final_formatted_plan_text,
                    "meal_plan_json": final_plan_json
                },
                metadata={
                    "pending_meal_plan_for_save": final_formatted_plan_text,
                    "active_meal_plan": final_formatted_plan_text,
                    "awaiting_db_confirmation": True, 
                    "calorie_target_requested": final_calorie_target,
                    "generation_method": "deterministic_python",
                    "generation_time": deterministic_elapsed,
                    "last_meal_used_foods": current_used_foods,
                    "last_meal_used_foods_day2": previous_day1_foods  
                }
            )
            
            deterministic_success = True
            logger.info(f" DETERMINISTIC PIPELINE COMPLETE: Plan ready in {time.time() - deterministic_start_time:.2f}s total")
            
        except (PoolExhaustedError, Exception) as e:
            deterministic_elapsed = time.time() - deterministic_start_time
            logger.warning("=" * 80)
            logger.warning(f" DETERMINISTIC GENERATION FAILED after {deterministic_elapsed:.2f}s")
            logger.warning(f"Error Type: {type(e).__name__}")
            logger.warning(f"Error Message: {str(e)}")
            logger.warning(f"Traceback:\n{traceback.format_exc()}")
            logger.warning("🔄 ENGAGING LLM FALLBACK CIRCUIT...")
            logger.warning("=" * 80)
            deterministic_success = False
        
        
        if not deterministic_success:
            logger.info("=" * 80)
            logger.info("🤖 LLM FALLBACK PATH ACTIVATED")
            logger.info("Starting LLM-based meal generation workflow (18s expected)...")
            logger.info("=" * 80)
            logger.info("Launching batched profile notes generation in parallel with meal generation")
            
            notes_task = asyncio.create_task(
                generate_batched_profile_notes(constraints, self.general_llm_service)
            )
        else:
            logger.info("Launching batched profile notes generation for deterministic plan")
            notes_task = asyncio.create_task(
                generate_batched_profile_notes(constraints, self.general_llm_service)
            )
        
        if not deterministic_success:
            for retry_attempt in range(MAX_RETRIES):
                if retry_attempt > 0:
                    logger.info(f" RETRY ATTEMPT {retry_attempt}/{MAX_RETRIES - 1} - Regenerating meal plan with better food selection...")
                    meal_plan_gen_kwargs['constraints'] = copy.deepcopy(constraints)
                    meal_plan_gen_kwargs['avoid_foods'] = copy.deepcopy(final_foods_to_avoid)
                    meal_plan_gen_kwargs['recommended_foods'] = copy.deepcopy(all_recommended_foods)
                    logger.info(f" Deep-copied user constraints to preserve dietary_preference, cuisine, and allergies")
                
                generation_result = await self.meal_plan_generator_tool.execute(**meal_plan_gen_kwargs)

                if not (generation_result.success and generation_result.data.get("meal_plan_json")):
                    logger.error(f"Meal plan generation failed on attempt {retry_attempt + 1}. Trying next attempt...")
                    if retry_attempt == MAX_RETRIES - 1:
                        return ToolResult(
                            success=False,
                            data={"answer": "I am having trouble generating your meal plan. Please try again in a moment."},
                            error="Meal plan generation failed after all retry attempts"
                        )
                    continue  
                
                logger.info(f"LLM generated initial meal plan draft (attempt {retry_attempt + 1}).")
                logger.warning("⏭️ SKIPPING programmatic scaling for LLM plans - trusting LLM's portion accuracy")
                logger.warning("   Programmatic scaling has known bugs with portion display mismatches")
                
                try:
                    initial_plan_json = generation_result.data["meal_plan_json"]
                    
                    perfectly_scaled_plan_json = initial_plan_json
                    
                    perfectly_scaled_plan_json = self._format_final_display_names(perfectly_scaled_plan_json)
                    
                    user_restrictions = constraints.get('restrictions', [])
                    perfectly_scaled_plan_text = self._convert_plan_json_to_text(perfectly_scaled_plan_json, restrictions=user_restrictions)
                    final_formatted_plan_text = self.format_portions_in_plan(perfectly_scaled_plan_text)
                    final_plan_json = self.meal_plan_generator_tool._parse_meal_plan_text_to_json(final_formatted_plan_text)
                    generation_result.data["meal_plan_text"] = final_formatted_plan_text
                    generation_result.data["meal_plan_json"] = final_plan_json
                    if generation_result.metadata:
                        generation_result.metadata["pending_meal_plan_for_save"] = final_formatted_plan_text
                        generation_result.metadata["active_meal_plan"] = final_formatted_plan_text
                        generation_result.metadata["awaiting_db_confirmation"] = True 
                        generation_result.metadata["generation_method"] = "llm_fallback"

                    final_calories = perfectly_scaled_plan_json.get("total_nutrients", {}).get("Calories", 0)
                    logger.info(f" LLM plan completed on attempt {retry_attempt + 1}! Final plan calories: {final_calories:.0f} kcal (Target: {final_calorie_target:.0f} kcal).")
                    
                    break

                except ValueError as e:

                    logger.error(
                        f" ValueError during scaling (Attempt {retry_attempt + 1}): {e}\n"
                        f"Continuing with current meal plan instead of discarding validated meals."
                    )
                    
                    if retry_attempt == MAX_RETRIES - 1:
                        logger.error(f" All {MAX_RETRIES} retry attempts exhausted. Scaling validation failed on all attempts.")
                        failed_meal = None
                        if "meal '" in str(e):
                            match = re.search(r"meal '([^']+)'", str(e))
                            if match:
                                failed_meal = match.group(1)
                        successful_meals_text = ""
                        failed_meals = []
                        all_expected_meals = ["Breakfast", "Morning Snack", "Lunch", "Evening Snack", "Dinner"]
                        
                        try:
                            original_plan_json = self.meal_plan_generator_tool._parse_meal_plan_text_to_json(
                                generation_result.data.get("meal_plan_text", "")
                            )
                            
                            for meal_name in all_expected_meals:
                                meal_items = original_plan_json.get("meal_plan", {}).get(meal_name, [])
                                if meal_items:
                                    meal_calories = sum(
                                        item.get("nutrients", {}).get("Calories", 0)
                                        for item in meal_items
                                    )
                                    if meal_calories >= 50 and meal_name != failed_meal:
                                        successful_meals_text += f"\n**{meal_name}**\n"
                                        for item in meal_items:
                                            nutrients = item.get("nutrients", {})
                                            name = item.get("name", "Unknown")
                                            display_name = item.get("display_name", name)
                                            household_measure_match = re.search(r'\(([0-9.]+\s+[a-zA-Z]+s?)\)', display_name)
                                            household_measure = household_measure_match.group(1) if household_measure_match else "1 serving"
                                            clean_item_name = re.sub(r'\s*\([0-9.]+\s*[a-zA-Z]+s?\s*\)\s*$', '', name).strip()
                                            
                                            portion_weight = nutrients.get("Portion Weight", 0) / 28.35  
                                            protein = nutrients.get("Protein", 0)
                                            carbs = nutrients.get("Carbohydrates", 0)
                                            fat = nutrients.get("Fat", 0)
                                            calories = nutrients.get("Calories", 0)
                                            successful_meals_text += (
                                                f"- {clean_item_name} | Household Measure: {household_measure} | Portion Weight: {portion_weight:.1f} oz | "
                                                f"Protein: {protein:.1f}g | Carbs: {carbs:.1f}g | Fat: {fat:.1f}g | "
                                                f"Calories: {calories:.0f}kcal\n"
                                            )
                                        successful_meals_text += f"Total calories for {meal_name}: {meal_calories:.1f} kcal\n"
                                    else:
                                        failed_meals.append(meal_name)
                                else:
                                    failed_meals.append(meal_name)
                        except Exception as parse_error:
                            logger.error(f"Error parsing partially generated meals: {parse_error}")
                            successful_meals_text = ""
                        
                        if successful_meals_text:
                            response_message = (
                                "I've prepared part of your meal plan, but ran into some challenges completing the rest. "
                                "Here's what I was able to create for you:\n"
                                f"{successful_meals_text}\n"
                                "---\n\n"
                                "I sincerely apologize, but I'm having difficulty creating "
                            )
                            
                            if failed_meals:
                                failed_meals_str = ", ".join(failed_meals[:-1]) + (f" and {failed_meals[-1]}" if len(failed_meals) > 1 else failed_meals[0])
                                response_message += f"the perfect combination for your **{failed_meals_str}** "
                            else:
                                response_message += "the remaining meals "
                            
                            response_message += (
                                "that meets your strict calorie targets and nutritional requirements. 😔\n\n"
                                "The challenge is balancing your specific dietary restrictions with the precise calorie goals. "
                                "To help me create a complete meal plan that works perfectly for you, could you consider:\n\n"
                                "• **Retry once more** - Sometimes a fresh attempt finds better combinations\n"
                                "• **Relaxing one dietary restriction** temporarily if medically safe\n"
                                "• **Allowing me more flexibility** in food selection while respecting your core requirements\n\n"
                                "Which option would you prefer? I'm here to help! 🤝"
                            )
                        else:
                            response_message = (
                                "I sincerely apologize, but I'm having a hard time finding the perfect combination of foods "
                                "that meets your strict calorie targets and medical constraints. 🍽️\n\n"
                                "Could we try one of these options?\n"
                                "• Retry once more - Sometimes a fresh attempt finds better combinations\n"
                                "• Relax a dietary restriction temporarily\n"
                                "• Let me suggest a similar meal plan with more flexibility\n\n"
                                "Which option works best for you?"
                            )
                        
                        return ToolResult(
                            success=False,
                            data={"answer": response_message},
                            error=f"Scaling validation failed after {MAX_RETRIES} attempts: {str(e)}"
                        )
                    
                    logger.info("Breaking out of retry loop to preserve validated meal selections")
                    break
                        
                except Exception as e:
                    logger.error(f" Critical error during programmatic scaling (Attempt {retry_attempt + 1}): {e}", exc_info=True)
                    
                    if retry_attempt == MAX_RETRIES - 1:
                        logger.warning(" Returning unscaled plan after all attempts failed.")
                        if generation_result and generation_result.metadata:
                            generation_result.metadata["awaiting_db_confirmation"] = True 
                            generation_result.metadata["generation_method"] = "llm_fallback_unscaled"
                        return generation_result
                    
                    continue
        logger.info("Awaiting batched profile notes from parallel task...")
        try:
            profile_notes = await notes_task
            logger.info(f"Profile notes retrieved: {len(profile_notes)} notes")
            
            if generation_result and generation_result.data:
                meal_plan_text = generation_result.data.get("meal_plan_text", "")
                if meal_plan_text and profile_notes:
                    notes_section = "\n\n**Condition-Specific Notes:**\n" + "\n".join(f"- {note}" for note in profile_notes)
                    
                    if "Condition-Specific Notes:" in meal_plan_text:
                        notes_start = meal_plan_text.find("**Condition-Specific Notes:**")
                        if notes_start != -1:
                            meal_plan_text = meal_plan_text[:notes_start].rstrip()
                            logger.info("Removed existing placeholder notes section")
                    
                    meal_plan_text += notes_section
                    generation_result.data["meal_plan_text"] = meal_plan_text
                    
                    if generation_result.metadata:
                        if "pending_meal_plan_for_save" in generation_result.metadata:
                            pending_plan = generation_result.metadata["pending_meal_plan_for_save"]
                            if "Condition-Specific Notes:" in pending_plan:
                                notes_start = pending_plan.find("**Condition-Specific Notes:**")
                                if notes_start != -1:
                                    pending_plan = pending_plan[:notes_start].rstrip()
                            generation_result.metadata["pending_meal_plan_for_save"] = pending_plan + notes_section
                            
                        if "active_meal_plan" in generation_result.metadata:
                            active_plan = generation_result.metadata["active_meal_plan"]
                            if "Condition-Specific Notes:" in active_plan:
                                notes_start = active_plan.find("**Condition-Specific Notes:**")
                                if notes_start != -1:
                                    active_plan = active_plan[:notes_start].rstrip()
                            generation_result.metadata["active_meal_plan"] = active_plan + notes_section
                    
                    logger.info(f"Successfully attached/overwritten {len(profile_notes)} parallel-generated notes to meal plan")
                else:
                    logger.warning(f"Could not attach notes: meal_plan_text={bool(meal_plan_text)}, notes={len(profile_notes)}")
        except Exception as e:
            logger.error(f"Error attaching profile notes: {e}", exc_info=True)
        
        # 🔥 NEW: Enrich meal plan with food_id, ingredients, and recipe metadata
        try:
            if generation_result and generation_result.data:
                meal_plan_text = generation_result.data.get("meal_plan_text", "")
                if meal_plan_text and 'FoodID:' not in meal_plan_text:  # Only enrich if not already enriched
                    logger.info("🎯 Enriching meal plan with metadata (food_id, ingredients, recipe)...")
                    from helpers.meal_plan_enrichment import enrich_meal_plan_with_metadata
                    
                    enriched_text = enrich_meal_plan_with_metadata(meal_plan_text)
                    
                    # Update all occurrences of the meal plan text
                    generation_result.data["meal_plan_text"] = enriched_text
                    
                    if generation_result.metadata:
                        if "pending_meal_plan_for_save" in generation_result.metadata:
                            generation_result.metadata["pending_meal_plan_for_save"] = enriched_text
                        
                        if "active_meal_plan" in generation_result.metadata:
                            generation_result.metadata["active_meal_plan"] = enriched_text
                    
                    logger.info("✅ Meal plan enrichment complete!")
                elif 'FoodID:' in meal_plan_text:
                    logger.info("ℹ️ Meal plan already contains metadata, skipping enrichment")
        except Exception as e:
            logger.error(f"❌ Error enriching meal plan with metadata: {e}", exc_info=True)
            logger.warning("⚠️ Continuing with un-enriched meal plan")
            
        if generation_result.metadata:
            generation_result.metadata["calorie_target_requested"] = final_calorie_target
        else:
            generation_result.metadata = {"calorie_target_requested": final_calorie_target}
        return generation_result
    
    def _is_affirmative(self, query: str) -> bool:
        """Checks for a clear 'yes' intent, avoiding broad matches."""
        query_lower = query.lower().strip().rstrip('.,!?')
        return query_lower in ["yes", "y", "yep", "yeah", "ok", "okay", "sure", "please", "save", "yes please"]

    def _is_negative(self, query: str) -> bool:
        """Checks for a clear 'no' intent, avoiding broad matches."""
        query_lower = query.lower().strip().rstrip('.,!?')
        return query_lower in ["no", "n", "nope", "nah", "not now", "later"]

    async def _check_for_profile_conflict(self, query: str, constraints: Dict) -> Optional[Dict]:
        profile_simple = {
            "dietary_preference": constraints.get("dietary_preference"),
            "cuisine": constraints.get("cuisine"),
            "medical_conditions": constraints.get("medical_conditions", []),
            "allergies": constraints.get("allergies", []),
            "dietary_restrictions": constraints.get("restrictions", []),
            "digestive_issues": constraints.get("digestive_issues", [])
        }

        if not any(profile_simple.values()):
            return None
        dietary_pref = constraints.get("dietary_preference", "")
        if dietary_pref:
            dietary_pref_lower = dietary_pref.lower() if isinstance(dietary_pref, str) else ",".join(dietary_pref).lower()
            query_lower = query.lower()
            is_non_veg_profile = any(term in dietary_pref_lower for term in ['non vegetarian', 'non veg', 'nonvegetarian', 'pescatarian'])
            requests_non_veg = any(term in query_lower for term in ['non-vegetarian', 'non vegetarian', 'chicken', 'meat', 'beef', 'pork', 'fish', 'seafood', 'lamb', 'mutton'])
            if is_non_veg_profile and requests_non_veg:
                logger.info(f" PRE-CHECK: Non-vegetarian profile requesting non-veg food - NO CONFLICT (skipping LLM call)")
                return None
            is_veg_profile = any(term in dietary_pref_lower for term in ['vegetarian', 'vegan']) and 'non' not in dietary_pref_lower
            requests_veg = any(term in query_lower for term in ['vegetarian', 'vegan']) and 'non' not in query_lower
            if is_veg_profile and requests_veg and not requests_non_veg:
                logger.info(f" PRE-CHECK: Vegetarian profile requesting vegetarian food - NO CONFLICT (skipping LLM call)")
                return None

        system_prompt = f"""
        You are a silent, expert conflict detection assistant. Analyze the user's query against their profile.
        Your goal is to find ONE of two types of conflicts:
        1.  **Direct Contradiction:** The query asks for a food/plan that VIOLATES a profile setting (e.g., asks for 'chicken' but profile is 'Vegetarian').
        2.  **New Condition:** The query asks for a plan for a NEW medical condition, allergy, etc., that is not in the profile (e.g., asks for 'PCOS plan' but 'PCOS' is not in medical_conditions).

        - **User Profile:** {json.dumps(profile_simple)}

        Analyze the query and determine the *single* most likely conflict and the *single* update payload required to fix it.

        --- CONFLICT EXAMPLES ---

        **Type 1: Direct Contradiction (Violation)**
        -   Query: "Give me a non-vegetarian meal plan", Profile: "dietary_preference": "Vegetarian"
            -> CONFLICT: {{"conflict": "dietary_preference", "requested": "non-vegetarian", "current": "Vegetarian", "action_type": "REPLACE", "update_payload": {{"add_dietary_preference_codes": ["Non Vegetarian"], "dietary_preference_update_type": "REPLACE"}}}}
        -   Query: "I want an Indian meal plan", Profile: "cuisine": "Mediterranean"
            -> CONFLICT: {{"conflict": "cuisine", "requested": "Indian", "current": "Mediterranean", "action_type": "REPLACE", "update_payload": {{"cuisine": "Indian"}}}}
        -   Query: "I want to add chicken", Profile: "dietary_preference": "Vegan"
            -> CONFLICT: {{"conflict": "dietary_preference", "requested": "chicken (non-vegetarian)", "current": "Vegan", "action_type": "REPLACE", "update_payload": {{"add_dietary_preference_codes": ["Non Vegetarian"], "dietary_preference_update_type": "REPLACE"}}}}
        -   Query: "Give me a meal plan with Nuts", Profile: "allergies": ["Tree Nuts (e.g. Almonds, Walnuts, Cashews)"]
            -> CONFLICT: {{"conflict": "allergies", "requested": "Nuts", "current": "Tree Nuts (e.g. Almonds, Walnuts, Cashews)", "action_type": "REMOVE", "update_payload": {{"remove_food_allergy_codes": ["Tree Nuts (e.g. Almonds, Walnuts, Cashews)"]}}}}
        -   Query: "I want a high-purine diet", Profile: "medical_conditions": ["Gout"]
            -> CONFLICT: {{"conflict": "medical_conditions", "requested": "high-purine diet", "current": "Gout", "action_type": "REMOVE", "update_payload": {{"remove_diagnosis_codes": ["Gout"]}}}}
        -   Query: "Need a plan with wheat pasta", Profile: "dietary_restrictions": ["Gluten-Free"]
            -> CONFLICT: {{"conflict": "dietary_restrictions", "requested": "wheat pasta", "current": "Gluten-Free", "action_type": "REMOVE", "update_payload": {{"remove_dietary_restriction_codes": ["Gluten-Free"]}}}}
        -   Query: "Meal plan with spicy tomato sauce", Profile: "digestive_issues": ["Acid reflux or heartburn"]
            -> CONFLICT: {{"conflict": "digestive_issues", "requested": "spicy tomato sauce", "current": "Acid reflux or heartburn", "action_type": "REMOVE", "update_payload": {{"remove_digestive_issue_codes": ["Acid reflux or heartburn"]}}}}

        **CRITICAL INSTRUCTION FOR ALLERGIES/AVOIDANCE:**
        - If the user states an allergy or avoidance (e.g., "I am allergic to Eggs", "No mushrooms"), set `requested` to the name of the allergen or food to avoid (e.g., "Eggs", "Mushrooms").
        - The system will handle the phrasing. Just identify the item causing the conflict.

        **Type 2: New Condition (Addition)**
        -   Query: "Give me a meal plan for PCOS", Profile: "medical_conditions": ["Gout"]
            -> CONFLICT: {{"conflict": "medical_conditions", "requested": "PCOS", "current": "Profile does not include PCOS", "action_type": "ADD", "update_payload": {{"add_diagnosis_codes": ["Polycystic Ovary Syndrome (PCOS)"]}}}}
        -   Query: "I need a gluten-free plan", Profile: "dietary_restrictions": []
            -> CONFLICT: {{"conflict": "dietary_restrictions", "requested": "Gluten-Free", "current": "Profile does not include Gluten-Free", "action_type": "ADD", "update_payload": {{"add_dietary_restriction_codes": ["Gluten-Free"]}}}}
        -   Query: "Give me a meal plan i have thyroid", Profile: "medical_conditions": []
            -> CONFLICT: {{"conflict": "medical_conditions", "requested": "Thyroid", "current": "Profile does not include Thyroid", "action_type": "ADD", "update_payload": {{"add_diagnosis_codes": ["Hypothyroidism / Hyperthyroidism"]}}}}

        --- NO CONFLICT EXAMPLES (CRITICAL - These should return {{"conflict": "none"}}) ---
        -   Query: "Give me a healthy meal plan" -> NO CONFLICT
        -   Query: "I am now non-vegetarian" (This is a direct update, not a conflict) -> NO CONFLICT
        -   Query: "Give me meal plan without Nuts" (This matches the allergy profile, no conflict) -> NO CONFLICT
        -   Query: "Give me meal plan that should be different from the following meal plan: [...]" (This demands the variation, no conflict) -> NO CONFLICT
        -   Query: "Give me a non-vegetarian meal plan", Profile: "dietary_preference": "Non Vegetarian" -> NO CONFLICT (perfect match)
        -   Query: "Give me a meal plan with chicken", Profile: "dietary_preference": "Non Vegetarian" -> NO CONFLICT (non-veg allows chicken)
        -   Query: "I want a meal plan with fish", Profile: "dietary_preference": "Pescatarian" -> NO CONFLICT (pescatarian allows fish)
        -   Query: "Give me a vegetarian meal plan", Profile: "dietary_preference": "Vegetarian (Cultural/Religious)" -> NO CONFLICT (perfect match)
        -   Query: "Give me a vegan meal plan", Profile: "dietary_preference": "Vegan (Cultural/Religious)" -> NO CONFLICT (perfect match)
        -   Query: "Generate meal plan", Profile: "dietary_preference": "Non Vegetarian" -> NO CONFLICT (generic request, use profile)

        **IMPORTANT RULE FOR DIETARY PREFERENCES:**
        - Non Vegetarian / Non Veg / Pescatarian profiles → NO CONFLICT when requesting meat, chicken, fish, seafood, eggs
        - Vegetarian profiles → CONFLICT only when requesting meat, chicken, fish, seafood
        - Vegan profiles → CONFLICT when requesting meat, chicken, fish, seafood, dairy, eggs
        - If query matches or is compatible with profile → NO CONFLICT

        If a conflict is detected, return ONLY the JSON object. If no conflict, return {{"conflict": "none"}}.
        """
        conflict_schema = {
            "type": "object",
            "properties": {
                "conflict": {"type": "string", "description": "The profile key that is conflicting (e.g., 'dietary_preference', 'allergies', 'medical_conditions', 'cuisine', 'dietary_restrictions', 'digestive_issues', or 'none')"},
                "requested": {"type": "string", "description": "The requested value (e.g., 'non-vegetarian', 'Nuts', 'PCOS')"},
                "current": {"type": "string", "description": "The current profile value (e.g., 'Vegetarian', 'Tree Nuts (e.g. Almonds, Walnuts, Cashews)', 'Profile does not include PCOS')"},
                "action_type": {"type": "string", "enum": ["ADD", "REMOVE", "REPLACE"], "description": "The action needed to resolve the conflict."},
                "update_payload": {"type": "object", "description": "The exact payload needed for ProfileUpdaterTool"}
            },
            "required": ["conflict"]
        }
        try:
            logger.info(f"Checking for profile conflict in query: '{query}'")
            response = await self.general_llm_service.query_json(
                prompt=f"User Query: \"{query}\"",
                system_prompt=system_prompt,
                json_schema=conflict_schema,
                max_tokens=500
            )
            
            if response and response.get("conflict") != "none":
                logger.warning(f"Conflict DETECTED: Requested '{response.get('requested')}' but profile has '{response.get('current')}'")
                return response
        except Exception as e:
            logger.error(f"Error during profile conflict check: {e}")
            
        return None
    
    async def _update_profile_summary_background(self, constraints: Dict, chat_history: List[Dict], last_agent_context: Dict):
        logger.info("Starting background task: Generating profile summary for the *next* turn.")
        try:
            summary_tool = self.tools[ToolType.PROFILE_SUMMARY.value]
            summary_start_time = time.time()
            summary_result = await summary_tool.execute(
                constraints=constraints,
                chat_history=chat_history,
                last_agent_context=last_agent_context 
            )
            summary_duration = time.time() - summary_start_time
            
            if summary_result.success and summary_result.data.get("summary_json"):
                new_summary_json = summary_result.data["summary_json"]
                db_tool = self.tools[ToolType.DATABASE_PERSISTENCE.value]
                token = constraints.get('token') 
                if token:
                    save_result = await db_tool.execute(
                        operation="upsert_profile_summary",
                        summary_data={"profile_summary": new_summary_json},
                        token=token
                    )
                    if save_result.success:
                        logger.info("Successfully saved profile summary to backend.")
                    else:
                        logger.error(f"Failed to save profile summary to backend: {save_result.error}")
                last_agent_context['profile_summary_result'] = summary_result.data 
                logger.info(f"[TIMING] Background Profile Summary Generation took: {summary_duration:.2f} seconds")
                print("\n--- (Background) Profile Summary generated for NEXT turn ---")
                print(json.dumps(new_summary_json, indent=2))
                print("----------------------------------------------------------\n")
            else:
                logger.warning(f"Background profile summary generation failed: {summary_result.error}")
        except Exception as e:
            logger.error(f"A critical error occurred during background profile summary generation: {e}")

    def _detect_followup_context(self, chat_history: List[Dict], agent_context: Dict) -> Optional[Dict]:
        """
        Detect if the current query is a follow-up to a recent tool interaction.
        Returns suggested tool and context if follow-up detected.
        """
        if not chat_history or len(chat_history) < 2:
            return None
        
        # Get last assistant message
        last_assistant_msg = None
        for msg in reversed(chat_history[:-1]):  # Exclude current user message
            if msg.get('role') == 'assistant':
                last_assistant_msg = msg
                break
        
        if not last_assistant_msg:
            return None
        
        last_content = last_assistant_msg.get('content', '').lower()
        current_query = chat_history[-1].get('content', '').lower() if chat_history else ''
        
        # Recipe/Ingredients follow-up detection
        if any(keyword in last_content for keyword in ['recipe', 'ingredients', 'instructions to make', 'here is the recipe']):
            if any(word in current_query for word in ['substitute', 'replace', 'alternative', 'instead of', 'can i use', 'swap']):
                return {'suggested_tool': ToolType.MEAL_INGREDIENTS.value, 'reason': 'recipe_followup'}
        
        # Meal plan follow-up detection
        if any(keyword in last_content for keyword in ['meal plan', 'breakfast:', 'lunch:', 'dinner:', 'snack:', 'total calories']):
            if any(word in current_query for word in ['change', 'replace', 'swap', 'different', 'other', 'alternative', 'remove', 'add']):
                return {'suggested_tool': ToolType.MEAL_PLAN_ADJUSTER.value, 'reason': 'meal_plan_followup'}
            if any(word in current_query for word in ['what is in', 'ingredients', 'recipe', 'how to make']):
                return {'suggested_tool': ToolType.MEAL_INGREDIENTS.value, 'reason': 'meal_plan_recipe_followup'}
        
        # Nutrition analysis follow-up
        if any(keyword in last_content for keyword in ['protein', 'calories', 'carbs', 'fat', 'macros', 'nutrients']):
            if any(word in current_query for word in ['what about', 'how about', 'compare', 'versus', 'vs']):
                return {'suggested_tool': ToolType.NUTRITION_ANALYZER.value, 'reason': 'nutrition_followup'}
        
        # Craving/snack follow-up
        if any(keyword in last_content for keyword in ['craving', 'snack', 'healthy option']):
            if any(word in current_query for word in ['another', 'different', 'other option', 'something else']):
                return {'suggested_tool': ToolType.CRAVING_ASSISTANT.value, 'reason': 'craving_followup'}
        
        # Workout/fitness follow-up
        if any(keyword in last_content for keyword in ['workout', 'exercise', 'training', 'fitness plan']):
            if any(word in current_query for word in ['modify', 'change', 'adjust', 'different exercise']):
                return {'suggested_tool': ToolType.WORKOUT_PLAN_ADJUSTER.value, 'reason': 'workout_followup'}
        
        return None
    
    async def _prepare_context_for_llm(self, chat_history: List[Dict], constraints: Dict, last_agent_context: Dict, max_field_chars: int = 100000, max_chat_msgs: int = 20):
        """
        Prepare compact context for LLM calls:
        - Keep only last `max_chat_msgs` chat messages.
        - Replace oversized fields with an existing profile summary, generated summary, or a truncation label.
        Returns: (prepared_chat, prepared_constraints, prepared_last_context)
        """
        prepared_chat = chat_history[-max_chat_msgs:] if chat_history else []
        prepared_constraints = dict(constraints) if constraints else {}
        prepared_last = dict(last_agent_context) if last_agent_context else {}
        profile_summary_text = None
        ps_candidates = [
            last_agent_context.get('profile_summary_result'),
            last_agent_context.get('profile_summary_json'),
            constraints.get('profile_summary_json'),
            constraints.get('profile_summary_result')
        ]
        for c in ps_candidates:
            if not c:
                continue
            if isinstance(c, str) and c.strip():
                profile_summary_text = c
                break
            if isinstance(c, dict):
                for k in ('summary', 'profile_summary', 'summary_text'):
                    if k in c and c[k]:
                        profile_summary_text = c[k]
                        break
            if profile_summary_text:
                break

        large_keys = ["fitness_plans_json", "active_meal_plan", "pending_meal_plan_for_save", "meal_plans_json"]
        summarizer = self.tools.get(ToolType.SUMMARIZER.value)

        async def maybe_summarize_field(value, field_name: str):
            nonlocal profile_summary_text
            try:
                text = json.dumps(value, default=str)
                if len(text) <= max_field_chars:
                    return None
                if profile_summary_text:
                    logger.info(f"Replaced large field '{field_name}' with existing profile summary.")
                    return profile_summary_text
                if summarizer:
                    excerpt = text[: max_field_chars * 2]
                    try:
                        result = await summarizer.execute(text=excerpt)
                        if result and result.success:
                            s = result.data.get('summary') or result.data.get('answer')
                            if s:
                                profile_summary_text = s
                                logger.info(f"Replaced large field '{field_name}' with generated summary.")
                                return s
                    except Exception as e:
                        logger.warning(f"Summarizer tool failed for field '{field_name}': {e}")
                logger.info(f"Field '{field_name}' exceeded size and no summary available; replacing with label.")
                return f"[TRUNCATED: field too large ({len(text)} chars)]"
            except Exception:
                logger.exception(f"Error while trying to summarize/truncate field '{field_name}'.")
                return "[TRUNCATED: serialization error]"

        for k in large_keys:
            if k in prepared_constraints:
                replacement = await maybe_summarize_field(prepared_constraints[k], k)
                if replacement:
                    prepared_constraints[k] = {"_summary": replacement}
            if k in prepared_last:
                replacement = await maybe_summarize_field(prepared_last[k], k)
                if replacement:
                    prepared_last[k] = {"_summary": replacement}
        for obj in (prepared_constraints, prepared_last):
            for key, val in list(obj.items()):
                try:
                    s = json.dumps(val, default=str)
                    if len(s) > max_field_chars:
                        obj[key] = {"_summary": profile_summary_text or f"[TRUNCATED: field too large ({len(s)} chars)]"}
                        logger.info(f"Replaced oversized field '{key}' with a summary label.")
                except Exception:
                    obj[key] = {"_summary": "[TRUNCATED: serialization error]"}
                    logger.exception(f"Failed to serialize field '{key}' during context preparation.")

        return prepared_chat, prepared_constraints, prepared_last

    async def detect_confirmation_intent(self, text: str) -> str:
        """
        Detect if user input is affirmative, negative, or unknown.
        Uses simple pattern matching instead of fasttext model.
        """
        if self._is_affirmative(text):
            return "AFFIRMATIVE"
        elif self._is_negative(text):
            return "NEGATIVE"
        else:
            return "UNKNOWN"

    async def process_query(self, user_id: str, query: str, chat_history: List[Dict], current_constraints: Dict, last_agent_context: Dict, background_tasks: BackgroundTasks, force_tool_type_str: Optional[str] = None, authorization: Optional[str] = None, start_date: Optional[str] = None, end_date: Optional[str] = None) -> Dict:
        start_time = time.time()
        prepared_chat, prepared_constraints, prepared_last_context = await self._prepare_context_for_llm(
            chat_history, current_constraints, last_agent_context
        )

        working_agent_context = prepared_last_context.copy()
        updated_constraints = prepared_constraints.copy()
        
        if 'user_id' not in updated_constraints:
            updated_constraints['user_id'] = user_id
            
        print("\n================== FULL USER PROFILE (CONSTRAINTS) ==================")
        print(json.dumps(updated_constraints, indent=2, default=str)) 
        print("=====================================================================\n")

        profile_summary_json = last_agent_context.get('profile_summary_json')
        
        if profile_summary_json:
            logging.info("Using profile summary from the previous turn for classification.")
            print("\n--- Profile Summary for this turn (from LAST context) ---")
            print(json.dumps(profile_summary_json, indent=2))
            print("-------------------------------------------------------\n")
        else:
            logging.info("No profile summary found in last context. Classifier will use an empty summary.")
            
        try:
            query_lower = query.lower().strip()
            
            is_awaiting_skip_meal = working_agent_context.get("meal_check_in_phase") == "AWAITING_SKIP_MEAL_TYPE"
            is_awaiting_today_confirmation = "awaiting_today_confirmation" in working_agent_context
            is_meal_check_in_active = "meal_check_in_phase" in working_agent_context
            meal_phase = working_agent_context.get("meal_check_in_phase")
            is_awaiting_db_confirmation = "awaiting_db_confirmation" in working_agent_context
            is_awaiting_goal_and_plan_confirmation = "awaiting_goal_and_plan_confirmation" in working_agent_context
            is_awaiting_shopping = working_agent_context.get("awaiting_shopping_followup", False)
            
            is_awaiting_replacement_confirmation = working_agent_context.get("awaiting_replacement_confirmation", False)
            pending_target_food = working_agent_context.get("pending_target_food", None)

            if is_awaiting_shopping:
                grocery_tool = self.tools[ToolType.GROCERY_LIST.value]

                if await grocery_tool.can_handle_followup(query, prepared_chat):
                    force_tool_type_str = ToolType.GROCERY_LIST.value
                working_agent_context.pop("awaiting_shopping_followup", None)
            

            if is_awaiting_replacement_confirmation and self._is_affirmative(query):
                logger.info(f" REPLACEMENT CONFIRMATION: User confirmed replacement for '{pending_target_food}'")
                
                force_tool_type_str = ToolType.MEAL_PLAN_ADJUSTER.value
                
                target_meal = working_agent_context.get("pending_target_meal", "Lunch")
                adjustment_instructions = f"Replace {pending_target_food} in {target_meal} with a suitable alternative"
                
                working_agent_context["auto_replacement_mode"] = True
                working_agent_context["auto_replacement_instructions"] = adjustment_instructions
                
                working_agent_context.pop("awaiting_replacement_confirmation", None)
                working_agent_context.pop("pending_target_food", None)
                working_agent_context.pop("pending_target_meal", None)
                
                logger.info(f" Auto-replacement triggered: {adjustment_instructions}")
            
            elif is_awaiting_replacement_confirmation and self._is_negative(query):
                logger.info(f" REPLACEMENT DECLINED: User declined replacement for '{pending_target_food}'")
                
                working_agent_context.pop("awaiting_replacement_confirmation", None)
                working_agent_context.pop("pending_target_food", None)
                working_agent_context.pop("pending_target_meal", None)
                
                answer = "No problem. Your meal plan remains unchanged. How else can I help you?"
                return {
                    "answer": answer,
                    "context": working_agent_context,
                    "updated_constraints": updated_constraints,
                    "updated_last_agent_context": working_agent_context
                }
            
            # 🔍 FOLLOW-UP CONTEXT DETECTION
            # Check if this is a follow-up question to a recent tool interaction
            follow_up_context = self._detect_followup_context(prepared_chat, working_agent_context)
            if follow_up_context:
                logger.info(f"📎 FOLLOW-UP DETECTED (tentative): {follow_up_context['suggested_tool']} based on context (reason: {follow_up_context.get('reason', 'unknown')})")
                # Only apply follow-up override for short/vague queries, not explicit new intents
                _current_query_lower = query.lower().strip()
                _explicit_new_intent_keywords = [
                    'create a meal plan', 'generate a meal plan', 'make a meal plan', 'meal plan for me',
                    'create a workout', 'generate a workout', 'make a workout',
                    'what is my profile', 'show my profile', 'what are my',
                    'health tips', 'analyze my', 'what should be my',
                ]
                _is_explicit_new_intent = any(kw in _current_query_lower for kw in _explicit_new_intent_keywords) or len(_current_query_lower.split()) > 12
                if not _is_explicit_new_intent:
                    force_tool_type_str = follow_up_context.get('suggested_tool')
                    logger.info(f"📎 FOLLOW-UP APPLIED: Routing to {force_tool_type_str}")
                else:
                    logger.info(f"📎 FOLLOW-UP SKIPPED: Query appears to be an explicit new intent, deferring to classifier")
            
            classifier_start_time = time.time()
            classifier_tool = self.tools[ToolType.QUERY_CLASSIFIER.value]
            classified_intent_result = await classifier_tool.execute(
                query=query,
                chat_history=prepared_chat,
                profile_summary_json=profile_summary_json,
                last_agent_context=working_agent_context
            )
            
            classified_intent = classified_intent_result.data if classified_intent_result.success else ToolType.OTHER.value
            
            persona_context = None
            if classified_intent_result.success and classified_intent_result.metadata:
                persona_context = classified_intent_result.metadata.get('persona_context')
                if persona_context:
                    logger.info(f"Persona context detected: {persona_context[:100]}...")
            
            classifier_duration = time.time() - classifier_start_time
            logger.info(f"[TIMING] LLM Classification took: {classifier_duration:.2f} seconds")
            selected_tool_str = force_tool_type_str if force_tool_type_str else classified_intent
            YES_NO_PHASES = {
                "AWAITING_CONFIRMATION",
            }
            MEAL_TIMING_RESPONSE_PHASES = {
                "AWAITING_MEAL_TYPE_FOR_LOG", 
                "AWAITING_SKIP_MEAL_TYPE",   
            }
            if is_awaiting_today_confirmation:
                intent = await self.detect_confirmation_intent(query)
                if intent != 'UNKNOWN':
                    selected_tool_str = ToolType.MEAL_CHECK_IN.value
                else:
                    working_agent_context.pop("awaiting_today_confirmation", None)
                    selected_tool_str = classified_intent
            if is_meal_check_in_active and meal_phase in MEAL_TIMING_RESPONSE_PHASES:
                meal_timing_pattern = r'\b(breakfast|morning\s+snack|lunch|evening\s+snack|dinner|brunch|snack)\b'
                if re.search(meal_timing_pattern, query.lower()):
                    logger.info(f"Detected meal timing response '{query}' during phase '{meal_phase}'. Forcing meal_check_in.")
                    selected_tool_str = ToolType.MEAL_CHECK_IN.value
                else:
                    if classified_intent != ToolType.MEAL_CHECK_IN.value:
                        logger.info(f"User interrupted meal timing question. Terminating flow to handle new intent: '{classified_intent}'.")
                        self.tools[ToolType.MEAL_CHECK_IN.value]._clear_check_in_context(working_agent_context)
                        selected_tool_str = classified_intent
                    else:
                        selected_tool_str = ToolType.MEAL_CHECK_IN.value
            elif is_meal_check_in_active and meal_phase in YES_NO_PHASES:
                is_simple_response = self.tools[ToolType.MEAL_CHECK_IN.value]._is_affirmative(query) or self.tools[ToolType.MEAL_CHECK_IN.value]._is_negative(query)
                if is_simple_response:
                    selected_tool_str = ToolType.MEAL_CHECK_IN.value
                else:
                    logger.info(f"User interrupted meal check-in. Terminating flow to handle new intent: '{classified_intent}'.")
                    self.tools[ToolType.MEAL_CHECK_IN.value]._clear_check_in_context(working_agent_context)
                    selected_tool_str = classified_intent 
            elif is_awaiting_skip_meal:
                system_prompt = f"""
You are classifying whether the user answered a “which meal was skipped” question.

Valid answers are only:
Breakfast, Morning Snack, Lunch, Evening Snack, Dinner (and small variants like "morning snacks").

If the user is giving one of those → return ANSWER  
If they are messaging anything else (food eaten, new request, questions, etc. even it contains correct meal type in it) → return NEW

Reply with only:
ANSWER
NEW
"""
                response = await self.general_llm_service.query(
                    prompt=f"Assistant asked: Which meal did you skip?\nUser replied: {query}",
                    system_prompt=system_prompt,
                    max_tokens=2,
                    temperature=0.0
                )
                if response.lower() == "answer":
                    force_tool_type_str = ToolType.MEAL_CHECK_IN.value
                else:
                    self.tools[ToolType.MEAL_CHECK_IN.value]._clear_check_in_context(working_agent_context)
                    selected_tool_str = classified_intent
            elif is_awaiting_goal_and_plan_confirmation:
                if self._is_affirmative(query):
                    working_agent_context.pop("awaiting_goal_and_plan_confirmation", None)
                    selected_tool_str = ToolType.GOAL_AND_PLAN_GENERATOR.value
                elif self._is_negative(query):
                    working_agent_context.pop("awaiting_goal_and_plan_confirmation", None)
                    answer = "Okay, no problem. How can I assist you more?"
                    return {"answer": answer, "context": working_agent_context, "updated_constraints": updated_constraints, "updated_last_agent_context": working_agent_context}
                else:
                    working_agent_context.pop("awaiting_goal_and_plan_confirmation", None)

            elif is_awaiting_db_confirmation:
                if self._is_affirmative(query):
                    logger.info("User confirmed to save the meal plan.")
                    plan_to_save = working_agent_context.get("pending_meal_plan_for_save", None)
                    plan_type = working_agent_context.get("plan_type", "DAILY")
                    grocery_list = working_agent_context.get("grocery_list", None)  # 🛒 Get grocery list text
                    grocery_list_json = working_agent_context.get("grocery_list_json", None)  # 🛒 Get grocery list JSON array
                    
                    if not plan_to_save:
                        plan_to_save = working_agent_context.get("active_meal_plan", None)
                        if plan_to_save:
                            logger.info(" pending_meal_plan_for_save was empty. Using active_meal_plan instead.")
                    
                    answer = "I seem to have misplaced the plan to save. Please generate it again."
                    
                    if plan_to_save and authorization:
                        db_tool = self.tools[ToolType.DATABASE_PERSISTENCE.value]
                        db_result = None
                        
                        if plan_type == "WEEKLY":
                            logger.info("Saving WEEKLY meal plan to 'upsert-weekly-meal-plan' endpoint.")
                            
                            # 🐛 DEBUGGER: Show what's being saved to database
                            print("\n" + "="*100)
                            print("🐛 DEBUGGER: SAVING WEEKLY MEAL PLAN TO DATABASE")
                            print("="*100)
                            print(f"Plan Type: {plan_type}")
                            print(f"Has meal_plan_text: {plan_to_save is not None} ({len(plan_to_save) if plan_to_save else 0} chars)")
                            print(f"Has grocery_list (text): {grocery_list is not None} ({len(grocery_list) if grocery_list else 0} chars)")
                            print(f"Has grocery_list_json: {grocery_list_json is not None}")
                            if grocery_list_json:
                                print(f"grocery_list_json items: {len(grocery_list_json)}")
                                print(f"Sample items: {json.dumps(grocery_list_json[:3], ensure_ascii=False, indent=2)}")
                            print("="*100 + "\n")
                            
                            # 🛒 Pass both text and JSON formats of grocery list
                            db_result = await db_tool.execute(
                                operation="upsert_weekly_meal_plan", 
                                meal_plan_text=plan_to_save,
                                grocery_list=grocery_list,  # Text format
                                grocery_list_json=grocery_list_json,  # 🛒 JSON array format
                                token=authorization
                            )
                        else: 
                            logger.info("Saving DAILY meal plan to 'upsert-meal-plan-details' endpoint.")
                            db_result = await db_tool.execute(
                                operation="upsert_meal_plan", 
                                meal_plan_text=plan_to_save, 
                                token=authorization
                            )

                        if db_result and db_result.success:
                            answer = f"Great! I've saved the {plan_type.lower()} meal plan to your profile."
                            working_agent_context["active_meal_plan"] = plan_to_save
                        elif db_result:
                            answer = f"I tried to save the {plan_type.lower()} plan, but there was an error: {db_result.error}"
                    
                    working_agent_context.pop("awaiting_db_confirmation", None)
                    working_agent_context.pop("pending_meal_plan_for_save", None)
                    working_agent_context.pop("plan_type", None)
                    working_agent_context.pop("grocery_list", None)  # Clear text format
                    working_agent_context.pop("grocery_list_json", None)  # 🛒 Clear JSON format
                    
                    return {"answer": answer, "context": working_agent_context, "updated_constraints": updated_constraints, "updated_last_agent_context": working_agent_context}
                
                elif self._is_negative(query):
                    logger.info("User declined to save the meal plan.")
                    pending_plan = working_agent_context.get("pending_meal_plan_for_save", "")
                    if pending_plan:
                        rejected_foods = re.findall(r'-\s*([^(|]+?)\s*\(', pending_plan)
                        rejected_foods = [food.strip().lower() for food in rejected_foods if food.strip()]
                        
                        if rejected_foods:
                            if 'session_rejected_foods' not in working_agent_context:
                                working_agent_context['session_rejected_foods'] = []
                            for food in rejected_foods:
                                if food not in working_agent_context['session_rejected_foods']:
                                    working_agent_context['session_rejected_foods'].append(food)
                            
                            logger.info(f"Session Memory: Captured {len(rejected_foods)} rejected foods: {rejected_foods[:5]}...")
                            logger.info(f"Total session rejected foods: {len(working_agent_context['session_rejected_foods'])}")
                    
                    working_agent_context.pop("awaiting_db_confirmation", None)
                    working_agent_context.pop("pending_meal_plan_for_save", None)
                    working_agent_context.pop("plan_type", None)  
                    answer = "Okay, no problem. I won't save the plan. How else can I assist you today?"
                    return {"answer": answer, "context": working_agent_context, "updated_constraints": updated_constraints, "updated_last_agent_context": working_agent_context}
                else:
                    logger.info("User ignored meal plan save prompt. Clearing awaiting state and handling new query.")
                    working_agent_context.pop("awaiting_db_confirmation", None)
                    working_agent_context.pop("pending_meal_plan_for_save", None)
                    working_agent_context.pop("plan_type", None)  
                    selected_tool_str = classified_intent

            elif is_awaiting_goal_and_plan_confirmation:
                
                if self._is_affirmative(query):
                    working_agent_context.pop("awaiting_goal_and_plan_confirmation", None)
                    logger.info("User confirmed generating a meal plan after goal update.")
                    selected_tool_str = ToolType.MEAL_PLAN_GENERATOR.value
                elif self._is_negative(query):
    
                    working_agent_context.pop("awaiting_goal_and_plan_confirmation", None)
                    answer = "Okay, I've saved your new goal. Let me know whenever you're ready for an updated meal plan!"
                    return {"answer": answer, "context": working_agent_context, "updated_constraints": updated_constraints, "updated_last_agent_context": working_agent_context}
                else:
                    working_agent_context.pop("awaiting_goal_and_plan_confirmation", None)
                    logger.info("User ignored goal/plan confirmation prompt. Handling new query.")
                    selected_tool_str = classified_intent
                
            else: 
                if force_tool_type_str:
                    selected_tool_str = force_tool_type_str
                else: 
                    query_lower = query.lower().strip()
                    blood_report_keywords = ["blood report", "lab report", "test results"]
                    
                    def keyword_in_text(keyword, text):
                        pattern = r'\b' + re.escape(keyword) + r'\b'
                        return re.search(pattern, text) is not None

                    blood_matches = [kw for kw in blood_report_keywords if keyword_in_text(kw, query_lower)]

                    tool_type_str = None

                    if blood_matches: 
                        print(f"Blood report keyword matches: {blood_matches} in query: '{query_lower}'")
                        tool_type_str = ToolType.BLOOD_REPORT_QUERY.value
                    
                    if tool_type_str:
                        selected_tool_str = tool_type_str
            if force_tool_type_str in [
                ToolType.MEAL_PLAN_GENERATOR.value,
                ToolType.WEEKLY_MEAL_PLAN_GENERATOR.value
            ]:
                selected_tool_str = force_tool_type_str
                logger.info(f"[FORCED TOOL] Preserving explicit meal-plan request: {selected_tool_str}")

            if selected_tool_str in [ToolType.MEAL_CHECK_IN.value, ToolType.MEAL_PLAN_ADJUSTER.value]:
                # Fetch active meal plan from DB if not already in context
                if not working_agent_context.get("active_meal_plan") and not working_agent_context.get("pending_meal_plan_for_save"):
                    history_retriever = self.tools[ToolType.HISTORY_RETRIEVER.value]
                    fetch_start_time = time.time()
                    history_result = await history_retriever.execute(
                        query="get latest", 
                        chat_history=[], 
                        last_agent_context={}, 
                        token=authorization, 
                        constraints=current_constraints
                    )
                    fetch_duration = time.time() - fetch_start_time
                    logger.info(f"[TIMING] Data Fetching (History for {selected_tool_str}) took: {fetch_duration:.2f} seconds")
                    
                    if history_result.success and history_result.metadata and history_result.metadata.get("active_meal_plan"):
                        working_agent_context["active_meal_plan"] = history_result.metadata["active_meal_plan"]
                        logger.info(f" Successfully fetched and stored active meal plan in context for {selected_tool_str}.")
                    else:
                        logger.warning(f" Could not fetch meal plan from database for {selected_tool_str}. User may need to generate one first.")
                else:
                    logger.info(f" Active meal plan already in context for {selected_tool_str} ({len(working_agent_context.get('active_meal_plan', ''))} chars)")

            logger.info(f"Final tool for execution: {selected_tool_str}")
            tool_execution_start = time.time()

            # --- SAFETY NET: HISTORY_RETRIEVER GUARDRAIL ---
            # Lightweight word-set check to catch any LLM misclassification for "show my meal plan" queries.
            # This is a last-resort guardrail — the primary fix is in the classifier prompt.
            _query_lower_plan = query.lower().strip()
            _query_words = set(re.sub(r"[''']", " ", _query_lower_plan).split())
            _is_show_plan_query = (
                bool(_query_words & {"show", "view", "get", "display", "retrieve", "see"})
                and bool(_query_words & {"meal", "plan", "meals"})
                and bool(_query_words & {"my", "me", "current", "saved", "today", "todays", "yesterday", "yesterdays", "latest", "recent"})
                and not bool(_query_words & {"create", "generate", "make", "new", "build"})
                and not bool(_query_words & {"change", "modify", "swap", "replace", "adjust", "update", "remove", "add"})
            )
            # Also catch "what is my meal plan" / "what is my plan" pattern
            _is_what_is_plan = (
                "what" in _query_words and "is" in _query_words
                and "my" in _query_words and bool(_query_words & {"meal", "plan", "meals"})
                and not bool(_query_words & {"create", "generate", "make", "new", "build"})
            )
            if selected_tool_str != ToolType.HISTORY_RETRIEVER.value and (_is_show_plan_query or _is_what_is_plan):
                logger.warning(f"[SAFETY NET] LLM classified '{selected_tool_str}' but query is a show-plan request. Correcting to history_retriever for: '{query[:60]}'")
                selected_tool_str = ToolType.HISTORY_RETRIEVER.value

            # --- SAFETY NET: MEAL_PLAN_ADJUSTER GUARDRAIL ---
            # Catch misclassification of meal modification queries (e.g., routed to workout_adjuster)
            _is_meal_adjust_query = (
                bool(_query_words & {"remove", "replace", "swap", "change", "modify", "add"})
                and bool(_query_words & {"meal", "lunch", "dinner", "breakfast", "snack", "rice", "soup", "chicken", "food", "coffee", "tea", "egg", "milk", "bread", "roti", "dal", "paneer", "fish", "salad", "fruit", "oats", "idli", "dosa", "chapati", "plan"})
                and not bool(_query_words & {"workout", "exercise", "sets", "reps", "cardio", "gym"})
            )
            if selected_tool_str not in [ToolType.MEAL_PLAN_ADJUSTER.value, ToolType.MEAL_CHECK_IN.value] and _is_meal_adjust_query:
                logger.warning(f"[SAFETY NET] LLM classified '{selected_tool_str}' but query is a meal-adjust request. Correcting to meal_plan_adjuster for: '{query[:60]}'")
                selected_tool_str = ToolType.MEAL_PLAN_ADJUSTER.value

            tool_to_run = self.tools.get(selected_tool_str)
            if selected_tool_str in [ToolType.PROFILE_UPDATER.value, ToolType.PROFILE_RETRIEVER.value, ToolType.GOAL_UPDATER.value, ToolType.VITAL_ADVISOR.value]:
                tool_to_run = self.tools.get(selected_tool_str)

            if not tool_to_run:
                logger.error(f"No valid tool found for classified tool: '{selected_tool_str}'. Defaulting to General Query.")
                selected_tool_str = ToolType.GENERAL_QUERY.value
                tool_to_run = self.tools[selected_tool_str]

            # --- TOOL ACCESS RESTRICTION ---
            # Only allow these tools to execute; all others return a polite message
            ALLOWED_TOOLS = {
                ToolType.NUTRITION_ANALYZER.value,
                ToolType.DISEASE_ADVISOR.value,
                ToolType.VITAL_ADVISOR.value,
                ToolType.WELLNESS_ADVISOR.value,
                ToolType.MEAL_RETRIEVER.value,
                ToolType.HISTORY_RETRIEVER.value,
                ToolType.PROFILE_RETRIEVER.value,
                ToolType.GENERAL_QUERY.value,
                ToolType.GREETING.value,
                ToolType.CRAVING_ASSISTANT.value,
                ToolType.FITNESS_PLAN_GENERATOR.value,
                ToolType.MEAL_PLAN_GENERATOR.value,
                ToolType.MEAL_PLAN_ADJUSTER.value,
                ToolType.WEEKLY_MEAL_PLAN_GENERATOR.value,
                ToolType.PANEL_QUERY.value,
                ToolType.WORKOUT_PLAN_ADJUSTER.value,
                ToolType.SPECIAL_MEAL_PLAN_GENERATOR.value,
                ToolType.MEAL_CHECK_IN.value,
            }
            if selected_tool_str not in ALLOWED_TOOLS:
                # Profile update tools - redirect with polite message to use mobile app
                PROFILE_UPDATE_TOOLS = {
                    ToolType.PROFILE_UPDATER.value,
                    ToolType.GOAL_UPDATER.value,
                    ToolType.FITNESS_PROFILE_UPDATER.value,
                }
                if selected_tool_str in PROFILE_UPDATE_TOOLS:
                    logger.info(f"[RESTRICTED] Profile update tool '{selected_tool_str}' blocked. Returning mobile app guidance.")
                    profile_update_message = (
                        f"I appreciate you wanting to update your profile, {updated_constraints.get('name', 'there')}! 😊\n\n"
                        "However, profile changes such as updating your weight, height, goals, dietary preferences, "
                        "allergies, or fitness settings need to be made directly through the **mobile application**.\n\n"
                        "Here's how:\n"
                        "1. Open the Friska app on your phone\n"
                        "2. Go to **Profile Settings**\n"
                        "3. Update your information there\n\n"
                        "Once you save the changes in the app, I'll automatically use your updated profile "
                        "for all future recommendations and meal plans. 💪\n\n"
                        "Is there anything else I can help you with?"
                    )
                    return {
                        "answer": profile_update_message,
                        "tool_used": selected_tool_str,
                        "context": working_agent_context,
                        "updated_constraints": updated_constraints,
                        "updated_last_agent_context": working_agent_context,
                    }

                # Redirect meal/diet plan queries to general_query instead of blocking
                REDIRECT_TO_GENERAL = {
                    ToolType.MEAL_PLAN_GENERATOR.value,
                    ToolType.WEEKLY_MEAL_PLAN_GENERATOR.value,
                    ToolType.SPECIAL_MEAL_PLAN_GENERATOR.value,
                    ToolType.MEAL_PLAN_ADJUSTER.value,
                }
                if selected_tool_str in REDIRECT_TO_GENERAL:
                    logger.info(f"[RESTRICTED] Tool '{selected_tool_str}' redirected to general_query.")
                    # Prepend a maintenance notice to the query so the LLM includes it
                    maintenance_prefix = (
                        "[SYSTEM NOTE: Start your response with this notice - "
                        "'Please note: We are currently under maintenance and not performing real-time database updates. "
                        "The changes you requested will be applicable in upcoming updates. "
                        "For now, here is a general-purpose response aligned with your profile and health conditions:'\n\n"
                    )
                    query = maintenance_prefix + query
                    selected_tool_str = ToolType.GENERAL_QUERY.value
                    tool_to_run = self.tools[selected_tool_str]
                else:
                    logger.warning(f"[RESTRICTED] Tool '{selected_tool_str}' not in ALLOWED_TOOLS. Falling back to general_query.")
                    selected_tool_str = ToolType.GENERAL_QUERY.value
                    tool_to_run = self.tools[selected_tool_str]
            # --- END TOOL ACCESS RESTRICTION ---

            # --- TDEE/CALORIE INTAKE INTERCEPT ---
            # If user asks about daily calorie intake/TDEE, compute deterministically
            _tdee_keywords = ['daily calorie', 'calorie intake', 'calories intake', 'how many calories', 'caloric intake', 'tdee', 'calorie need', 'calories need', 'calories should i', 'calorie should i', 'daily energy', 'maintenance calories']
            _query_lower_tdee = query.lower()
            if selected_tool_str == ToolType.GENERAL_QUERY.value and any(kw in _query_lower_tdee for kw in _tdee_keywords):
                _weight = updated_constraints.get('weight_kg')
                _height = updated_constraints.get('height_cm')
                _age = updated_constraints.get('age')
                _gender = updated_constraints.get('gender')
                _activity = updated_constraints.get('activity_level')
                if all([_weight, _height, _age, _gender, _activity]):
                    calorie_tool = self.tools[ToolType.CALORIE_CALCULATOR.value]
                    _tdee_result = await calorie_tool.execute(operation="calculate_profile_metrics", constraints=updated_constraints)
                    if _tdee_result.success and _tdee_result.data and _tdee_result.data.get('tdee'):
                        _raw_tdee = _tdee_result.data['tdee']
                        _bmi = _tdee_result.data.get('bmi')
                        _waist_cm = updated_constraints.get('waist_circumference_cm')
                        _gender_lower = _gender.lower()
                        
                        # Apply BMI-based reduction (same as meal plan logic)
                        _bmi_reduction = 0
                        if _bmi is not None:
                            if _bmi < 18.5:
                                _bmi_reduction = 0
                            elif 24.9 <= _bmi < 29.9:
                                _bmi_reduction = 200
                            elif 30 <= _bmi < 34.9:
                                _bmi_reduction = 300
                            elif _bmi >= 35:
                                _bmi_reduction = 500
                        
                        # Apply waist circumference reduction
                        _waist_reduction = 0
                        if _waist_cm is not None and _gender_lower:
                            if _gender_lower == 'female':
                                if 80 <= _waist_cm < 88:
                                    _waist_reduction = 100
                                elif 88 <= _waist_cm <= 100:
                                    _waist_reduction = 200
                                elif _waist_cm > 100:
                                    _waist_reduction = 300
                            elif _gender_lower == 'male':
                                if 90 <= _waist_cm < 100:
                                    _waist_reduction = 100
                                elif 100 <= _waist_cm < 108:
                                    _waist_reduction = 200
                                elif _waist_cm >= 108:
                                    _waist_reduction = 300
                        
                        # Apply vitals adjustment
                        _vitals_adjustment = 0
                        _vitals_numeric = updated_constraints.get('vitals_numeric', {})
                        _has_high_vitals, _has_low_vitals = False, False
                        _bp_val = _vitals_numeric.get("Blood Pressure")
                        if isinstance(_bp_val, dict) and (_bp_val.get("systolic", 0) > 130 or _bp_val.get("diastolic", 0) > 85):
                            _has_high_vitals = True
                        if _vitals_numeric.get("Blood Glucose") and _vitals_numeric["Blood Glucose"] > 100:
                            _has_high_vitals = True
                        if _vitals_numeric.get("Blood Glucose") and _vitals_numeric["Blood Glucose"] < 70:
                            _has_low_vitals = True
                        if _has_high_vitals and not _has_low_vitals:
                            _vitals_adjustment = -100
                        elif _has_low_vitals and not _has_high_vitals:
                            _vitals_adjustment = 100
                        
                        # Calculate adjusted TDEE
                        _adjusted_tdee = _raw_tdee - _bmi_reduction - _waist_reduction + _vitals_adjustment
                        
                        # Min threshold recovery (same as calculate_comprehensive_tdee in utils.py)
                        _min_threshold_women, _min_threshold_men = 1200, 1500
                        if _vitals_adjustment < 0:
                            if (_gender_lower == 'female' and _adjusted_tdee < _min_threshold_women) and abs(_vitals_adjustment) > 50:
                                _adjusted_tdee += (abs(_vitals_adjustment) - 50)
                            elif (_gender_lower == 'male' and _adjusted_tdee < _min_threshold_men) and abs(_vitals_adjustment) > 50:
                                _adjusted_tdee += (abs(_vitals_adjustment) - 50)
                        
                        # Enforce minimums
                        if _gender_lower == 'female':
                            _adjusted_tdee = max(_adjusted_tdee, 1200)
                        elif _gender_lower == 'male':
                            _adjusted_tdee = max(_adjusted_tdee, 1500)
                        
                        # Goal-based final target
                        _goal = updated_constraints.get('goal', {})
                        _goal_type = _goal.get('type', '') if isinstance(_goal, dict) else str(_goal)
                        _goal_adjustment = 300
                        
                        _lines = []
                        _lines.append(f"Based on your profile, here is your precise calorie breakdown:\n")
                        _lines.append(f"- **Base TDEE:** {_raw_tdee:.0f} kcal/day")
                        if _bmi:
                            _lines.append(f"- **BMI:** {_bmi:.1f}")
                        _lines.append(f"- **Activity Level:** {_activity}")
                        
                        _adjustments = []
                        if _bmi_reduction > 0:
                            _adjustments.append(f"BMI adjustment: -{_bmi_reduction} kcal")
                        if _waist_reduction > 0:
                            _adjustments.append(f"Waist circumference adjustment: -{_waist_reduction} kcal")
                        if _vitals_adjustment != 0:
                            _sign = "+" if _vitals_adjustment > 0 else ""
                            _adjustments.append(f"Vitals adjustment: {_sign}{_vitals_adjustment} kcal")
                        
                        if _adjustments:
                            _lines.append("")
                            _lines.append("**Health-Based Adjustments:**")
                            for adj in _adjustments:
                                _lines.append(f"- {adj}")
                        
                        _lines.append("")
                        _lines.append(f"- **Your Recommended Daily Intake:** {_adjusted_tdee:.0f} kcal/day")
                        
                        if _goal_type == "Lose Weight":
                            _final = _adjusted_tdee - _goal_adjustment
                            _lines.append(f"- **With Weight Loss Goal (-{_goal_adjustment} kcal):** {_final:.0f} kcal/day")
                        elif _goal_type == "Gain Weight":
                            _final = _adjusted_tdee + _goal_adjustment
                            _lines.append(f"- **With Weight Gain Goal (+{_goal_adjustment} kcal):** {_final:.0f} kcal/day")
                        else:
                            _loss = _adjusted_tdee - _goal_adjustment
                            _gain = _adjusted_tdee + _goal_adjustment
                            _lines.append(f"- **For Weight Loss (-{_goal_adjustment} kcal):** {_loss:.0f} kcal/day")
                            _lines.append(f"- **For Weight Gain (+{_goal_adjustment} kcal):** {_gain:.0f} kcal/day")
                        
                        _tdee_answer = "\n".join(_lines)
                        return {
                            "answer": _tdee_answer,
                            "follow_up_prompt": "Would you like me to generate a meal plan based on these calorie targets?",
                            "audio": _tdee_answer,
                            "context": working_agent_context,
                            "updated_constraints": updated_constraints,
                            "updated_last_agent_context": working_agent_context
                        }
            # --- END TDEE INTERCEPT ---

            tool_kwargs = {
                "query": query, 
                "chat_history": prepared_chat, 
                "constraints": updated_constraints, 
                "last_agent_context": working_agent_context, 
                "user_id": user_id, 
                "token": authorization, 
                "background_tasks": background_tasks,
                "start_date": start_date,  # Custom start date for meal plans
                "end_date": end_date        # Custom end date for meal plans
            }
            
            if persona_context:
                tool_kwargs['persona_context'] = persona_context
                logger.info(f"Injecting persona_context into {selected_tool_str} tool execution")
        
            if selected_tool_str == ToolType.DATABASE_PERSISTENCE.value:
                logger.info(f"Preparing DatabasePersistenceTool execution for query: '{query}'")
                meal_plan_text = working_agent_context.get("active_meal_plan") or working_agent_context.get("pending_meal_plan_for_save")
                if meal_plan_text:
                    tool_kwargs['meal_plan_text'] = meal_plan_text
                    logger.info(f" Added meal_plan_text to kwargs ({len(meal_plan_text)} chars)")
                
                if 'profile_data' in updated_constraints:
                    tool_kwargs['profile_data'] = updated_constraints['profile_data']
                    logger.info(f" Added profile_data to kwargs")        
            if selected_tool_str == ToolType.DISEASE_ADVISOR.value:
                tool_kwargs['diseases'] = [query]

            if selected_tool_str == ToolType.MEAL_PLAN_ADJUSTER.value:
                if working_agent_context.get("auto_replacement_mode"):
                    tool_kwargs['adjustment_instructions'] = working_agent_context.get("auto_replacement_instructions", query)
                    logger.info(f" AUTO-REPLACEMENT MODE: Using stored instructions: '{tool_kwargs['adjustment_instructions']}'")
                    working_agent_context.pop("auto_replacement_mode", None)
                    working_agent_context.pop("auto_replacement_instructions", None)
                else:
                    tool_kwargs['adjustment_instructions'] = query
                
                tool_kwargs['meal_plan_text'] = working_agent_context.get("active_meal_plan") or working_agent_context.get("pending_meal_plan_for_save") or ""
            
            if selected_tool_str == ToolType.MEAL_RETRIEVER.value:
                query_lower = query.lower()
                meal_type = None
                if 'breakfast' in query_lower:
                    meal_type = 'Breakfast'
                elif 'morning snack' in query_lower:
                    meal_type = 'Morning Snack'
                elif 'lunch' in query_lower:
                    meal_type = 'Lunch'
                elif 'evening snack' in query_lower:
                    meal_type = 'Evening Snack'
                elif 'dinner' in query_lower:
                    meal_type = 'Dinner'
                
                if meal_type:
                    tool_kwargs['meal_type'] = meal_type
                    logger.info(f"Extracted meal_type '{meal_type}' for MEAL_RETRIEVER")
                else:
                    logger.warning(f"MEAL_RETRIEVER selected but no meal_type found in query: '{query}'. Falling back to GENERAL_QUERY.")
                    selected_tool_str = ToolType.GENERAL_QUERY.value
                    tool_to_run = self.tools[selected_tool_str]
            
            if selected_tool_str == ToolType.MEAL_PLAN_GENERATOR.value:
                orchestrator_start_time = time.time()
                try:
                    tool_result = await self._run_meal_plan_orchestrator(**tool_kwargs)
                except Exception as e:
                    logger.error(f"SYSTEM_ERROR [ORCHESTRATOR] in Meal Plan Orchestrator: {str(e)}")
                    logger.error(f"Full traceback:\n{traceback.format_exc()}")
                    
                    tool_result = ToolResult(
                        success=False,
                        data={"answer": BaseTool.EMPATHETIC_FALLBACK},
                        error="orchestrator_error",
                        metadata={"error_handled": True, "error_type": "ORCHESTRATOR_ERROR"}
                    )
                orchestrator_duration = time.time() - orchestrator_start_time
                logger.info(f"[TIMING] Meal Plan Orchestrator took: {orchestrator_duration:.2f} seconds")
            elif selected_tool_str in [ToolType.FITNESS_PLAN_GENERATOR.value, ToolType.WORKOUT_PLAN_ADJUSTER.value]:
                
                fitness_payload = updated_constraints.get('fitness_profile_data', {})
                if not fitness_payload: fitness_payload = {}
                for k in ['age', 'gender', 'weight_kg', 'height_cm', 'name']:
                    if not fitness_payload.get(k) and updated_constraints.get(k):
                        fitness_payload[k] = updated_constraints.get(k)
                
                updater = self.tools[ToolType.FITNESS_PROFILE_UPDATER.value]
                parse_result = await updater.execute(query=query, current_profile=fitness_payload)
                parsed_intent = parse_result.data.get("intent", "generate_plan")
                updates = parse_result.data.get("updates", {})
                modification_data = parse_result.data.get("modification", {})

                if parsed_intent == "modify_plan":
                    logger.info(f"Routing to WorkoutAdjusterTool. Mod Data: {modification_data}")
                    current_plan = working_agent_context.get('fitness_plans_json') or updated_constraints.get('fitness_plans_json')
                    
                    if not current_plan:
                        tool_result = ToolResult(False, error="I can't modify the plan because I don't have one saved. Please generate a plan first!")
                    else:
                        adjuster = self.tools[ToolType.WORKOUT_PLAN_ADJUSTER.value]
                        tool_result = await adjuster.safe_execute(
                            query=query, 
                            current_plan=current_plan, 
                            modification_data=modification_data, 
                            user_profile=fitness_payload
                        )
                        
                        if tool_result.success and tool_result.data.get("plans_json"):
                             updated_constraints['fitness_plans_json'] = tool_result.data["plans_json"]
                             working_agent_context['fitness_plans_json'] = tool_result.data["plans_json"]

                else:
                    if updates:
                        fitness_payload.update(updates)
                        updated_constraints['fitness_profile_data'] = fitness_payload 
                    if not fitness_payload.get('days_per_week'): fitness_payload['days_per_week'] = ["Monday", "Wednesday", "Friday"]
                    if not fitness_payload.get('primary_goal'): fitness_payload['primary_goal'] = "General Fitness"
                    generator = self.tools[ToolType.FITNESS_PLAN_GENERATOR.value]
                    tool_kwargs['user_profile'] = fitness_payload
                    tool_result = await generator.safe_execute(**tool_kwargs)
                    if tool_result.success and tool_result.data.get("plans_json"):
                        updated_constraints['fitness_plans_json'] = tool_result.data["plans_json"]
                        working_agent_context['fitness_plans_json'] = tool_result.data["plans_json"]
                        plan_text = tool_result.data.get("fitness_plan") or tool_result.data.get("answer")
                        days_num = len(fitness_payload.get('days_per_week', []))
                        goal_txt = fitness_payload.get('primary_goal')
                        tool_result.data["answer"] = f"Here is your **{days_num}-Day {goal_txt} Plan**:\n\n{plan_text}"

            else:
                tool_result = await tool_to_run.safe_execute(**tool_kwargs)

            if selected_tool_str == ToolType.MEAL_PLAN_ADJUSTER.value:
                if tool_result and tool_result.metadata and tool_result.metadata.get('should_fallback_to_generator'):
                    logger.info("AUTO-FALLBACK TRIGGERED: MealPlanAdjuster detected 'new plan' request, routing to MealPlanGenerator")
                    selected_tool_str = ToolType.MEAL_PLAN_GENERATOR.value
                    try:
                        tool_result = await self._run_meal_plan_orchestrator(**tool_kwargs)
                        logger.info("AUTO-FALLBACK SUCCESS: Generated new meal plan via orchestrator")
                    except Exception as e:
                        logger.error(f"AUTO-FALLBACK FAILED: {e}")
                        
                        tool_result = ToolResult(
                            success=False,
                            data={"answer": BaseTool.EMPATHETIC_FALLBACK},
                            error="fallback_orchestrator_error",
                            metadata={"error_handled": True, "error_type": "FALLBACK_ERROR"}
                        )

            if tool_result and tool_result.success:
                response_data = {}
                if isinstance(tool_result.data, dict):
                    if 'updated_constraints' in tool_result.data: updated_constraints.update(tool_result.data['updated_constraints'])
                    if 'answer' in tool_result.data:
                        response_data["answer"] = tool_result.data['answer']
                    elif 'profile_data' in tool_result.data: response_data["answer"] = self._format_profile_summary(tool_result.data["profile_data"], self.vital_units)
                    elif 'meal_plan_text' in tool_result.data:
                        response_data["answer"] = tool_result.data['meal_plan_text']
                    else: response_data["answer"] = "To accurately log your meal plan, food items and their portion sizes are required."
                else: response_data["answer"] = str(tool_result.data)
                if tool_result.metadata: 
                    working_agent_context.update(tool_result.metadata)
                    
                    # 🐛 DEBUGGER: Show when grocery_list_json enters context
                    if "grocery_list_json" in tool_result.metadata:
                        print("\n" + "="*100)
                        print("🐛 DEBUGGER: GROCERY_LIST_JSON ADDED TO CONTEXT FROM TOOL RESULT")
                        print("="*100)
                        print(f"Tool: {selected_tool_str}")
                        print(f"grocery_list_json items: {len(tool_result.metadata['grocery_list_json'])}")
                        print(f"Context now has grocery_list_json: {'grocery_list_json' in working_agent_context}")
                        print(f"Sample item: {json.dumps(tool_result.metadata['grocery_list_json'][0], ensure_ascii=False) if tool_result.metadata['grocery_list_json'] else 'Empty'}")
                        print("="*100 + "\n")
                
                if working_agent_context.get('profile_triggered_meal_plan'):
                    logger.info("Adding profile update + meal plan generation header")
                    custom_header = "Your Profile has been updated and a new meal plan has been generated as per your profile:\n\n"
                    if "answer" in response_data and response_data["answer"]:
                        response_data["answer"] = custom_header + response_data["answer"]
                    working_agent_context.pop('profile_triggered_meal_plan', None)

                should_await_db = working_agent_context.get("awaiting_db_confirmation", False)
                if should_await_db and force_tool_type_str not in [
                    ToolType.MEAL_PLAN_GENERATOR.value,
                    ToolType.WEEKLY_MEAL_PLAN_GENERATOR.value
                ]:
                    logger.info("MATCHED Condition 1 (Save Prompt): Adding 'Save plan?' prompt.")
                    prompt_text = "Would you like me to save this new plan to your profile? (Please respond with Yes or No)"
                    if "answer" in response_data and response_data["answer"]:
                        response_data["answer"] += f"\n\n---\n\n**{prompt_text}**"
            else:
                # Use the polite fallback answer from tool result data, not the raw error string
                if tool_result and tool_result.data and isinstance(tool_result.data, dict) and tool_result.data.get("answer"):
                    response_data = {"answer": tool_result.data["answer"]}
                elif tool_result and tool_result.error and not tool_result.error.startswith(("llm_timeout", "pydantic_", "tool_parameter", "missing_required", "attribute_access", "fallback_")):
                    # Use tool's error message if it's a user-friendly string (not an internal error code)
                    response_data = {"answer": tool_result.error}
                else:
                    response_data = {"answer": BaseTool.EMPATHETIC_FALLBACK}

            final_answer = response_data.get("answer", "An error occurred.")
            

            replacement_prompts = [
                "would you like to replace it",
                "would you like me to replace",
                "to keep your plan healthy, would you like to replace"
            ]
            
            save_prompts = [
                "would you like me to save this",
                "would you like me to save the",
                "please respond with yes or no"
            ]
            
            has_replacement_question = any(prompt in final_answer.lower() for prompt in replacement_prompts)
            has_save_question = any(prompt in final_answer.lower() for prompt in save_prompts)
            
            if has_replacement_question and has_save_question:
                logger.warning(" ONE QUESTION RULE VIOLATION DETECTED: Both replacement AND save prompts found")
                logger.warning("   Removing save prompt to prevent prompt stacking")
                

                if "---" in final_answer:
                    final_answer = final_answer.split("---")[0].strip()
                    logger.info("   Stripped save prompt after '---' separator")
                
                for save_prompt in save_prompts:
                    if save_prompt in final_answer.lower():
                        lines = final_answer.split("\n")
                        cleaned_lines = [line for line in lines if save_prompt not in line.lower()]
                        final_answer = "\n".join(cleaned_lines).strip()
                        logger.info(f"   Removed inline save prompt: '{save_prompt}'")
                
                working_agent_context.pop("awaiting_db_confirmation", None)
                working_agent_context.pop("pending_meal_plan_for_save", None)
                logger.info("   Cleared awaiting_db_confirmation state")
            

            save_phrase_pattern = r"Would you like me to save.*?\(Please respond with Yes or No\)"
            save_matches = re.findall(save_phrase_pattern, final_answer, re.IGNORECASE | re.DOTALL)
            
            if len(save_matches) > 1:
                logger.warning(f" DUPLICATE SAVE PROMPT DETECTED: Found {len(save_matches)} instances")
                for duplicate in save_matches[1:]:
                    final_answer = final_answer.replace(duplicate, "", 1)
                final_answer = final_answer.strip()
                final_answer = re.sub(r"---\s*---+", "---", final_answer)
                logger.info("   Removed duplicate save prompts")
            
            follow_up = response_data.get("follow_up_prompt")
            audio_summary = final_answer
            total_duration_to_response = time.time() - start_time
            logger.info(f"[TIMING] Total request processing time (to response): {total_duration_to_response:.2f} seconds")

            if authorization:
                db_tool = self.tools.get(ToolType.DATABASE_PERSISTENCE.value)
                if db_tool:
                    current_time_iso = datetime.now().isoformat()
                    user_message_payload = {"role": "user", "content": query, "created_at": current_time_iso}
                    background_tasks.add_task(db_tool.execute, operation="save_chat_message", message_payload=user_message_payload, token=authorization, user_id=user_id)
                    ai_message_payload = {"role": "assistant", "content": final_answer, "created_at": current_time_iso}
                    background_tasks.add_task(db_tool.execute, operation="save_chat_message", message_payload=ai_message_payload, token=authorization, user_id=user_id)
            else:
                logger.warning("No authorization token provided. Cannot save chat history.")
                
            # 🛒 Prepare final response with grocery_list_json for weekly meal plans
            response_dict = {
                "answer": final_answer,
                "follow_up_prompt": follow_up,
                "audio": audio_summary,
                "context": working_agent_context,
                "updated_constraints": updated_constraints,
                "updated_last_agent_context": working_agent_context
            }
            
            # 🛒 Add grocery_list_json ONLY for weekly meal plans
            if working_agent_context.get("plan_type") == "WEEKLY" and "grocery_list_json" in working_agent_context:
                response_dict["grocery_list_json"] = working_agent_context["grocery_list_json"]
                logger.info(f"✅ Added grocery_list_json to response ({len(working_agent_context['grocery_list_json'])} items)")
            
            return response_dict
        except Exception as e:
            logging.error(f"Critical error in agent processing: {e}\n{traceback.format_exc()}")       
            if ToolType.MEAL_CHECK_IN.value in self.tools:
                self.tools[ToolType.MEAL_CHECK_IN.value]._clear_check_in_context(working_agent_context)       
            
            error_answer = BaseTool.EMPATHETIC_FALLBACK
            
            logging.error(f"SYSTEM_ERROR [AGENT_TOP_LEVEL]")
            logging.error(f"Query: {query}")
            logging.error(f"Selected Tool: {selected_tool_str if 'selected_tool_str' in locals() else 'UNKNOWN'}")
            return {
                "answer": error_answer,
                "audio": error_answer, 
                "context": working_agent_context,
                "updated_constraints": updated_constraints,
                "updated_last_agent_context": working_agent_context,
                "error_handled": True,
                "error_type": "AGENT_TOP_LEVEL",
                "error_tool": selected_tool_str if 'selected_tool_str' in locals() else 'UNKNOWN'
            }
        
