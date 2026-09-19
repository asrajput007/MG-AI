import io
from langchain_openai import AzureChatOpenAI
from . import agent_global
import re
import time
import os
from dotenv import load_dotenv
import uuid
from .tool_core import ToolType 
import jwt
import base64
import logging
import asyncio
import aiohttp
import requests
from urllib.parse import urlparse
from ultralytics import YOLO 
from PIL import Image
import pytesseract
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, HTTPException, Depends, Header, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import inflect
from nltk.stem import WordNetLemmatizer
import traceback
import time
from azure.cognitiveservices.vision.computervision import ComputerVisionClient
from msrest.authentication import CognitiveServicesCredentials
from langchain_core.prompts import ChatPromptTemplate, HumanMessagePromptTemplate
from langchain_core.messages import SystemMessage
from langchain_mistralai.chat_models import ChatMistralAI
from langchain_openai import AzureChatOpenAI
from langchain_classic.chains import LLMChain
from langchain_core.output_parsers import PydanticOutputParser
import asyncio
from .agent_core import ToolBasedNutritionAgent


try:
    from .langgraph_agent import LangGraphAgent
    LANGGRAPH_AVAILABLE = True
    logger = logging.getLogger(__name__)
    logger.info(" LangGraph dependencies available")
except ImportError as e:
    LANGGRAPH_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.warning(f" LangGraph not available: {e}")
    LangGraphAgent = None

from .chat_history_summarizer import generate_payload_title_clean
from helpers.utils import load_models

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
p = inflect.engine()
lemmatizer = WordNetLemmatizer()
try:
    lemmatizer.lemmatize('check')
except LookupError:
    logger.info("NLTK 'wordnet' resource not found. Downloading...")
    import nltk
    nltk.download('wordnet')
    logger.info("'wordnet' resource downloaded successfully.")
    
load_dotenv()

FULL_BLOOD_REPORT_DATA = {}
COMPUTER_VISION_MODELS = load_models("COMPUTER_VISION_MODELS")
OCR_TEXT_ANALYSIS_MODELS = load_models("OCR_TEXT_ANALYSIS_MODELS")
YOLO_MODEL_PATH = os.getenv("YOLO_MODEL_PATH")


logger.info("Audio models will use remote API endpoints (WHISPER_URL and KOKORO_API_URL)")

app = FastAPI(
    title="Friska FuelNutrition Assistant API",
    description="This API handles the core logic for the Friska Fuelagent.",
    version="42"
)

agent = ToolBasedNutritionAgent()
agent_global.agent = agent

_langgraph_agent = None

def get_langgraph_agent():
    global _langgraph_agent
    if _langgraph_agent is None and LANGGRAPH_AVAILABLE:
        _langgraph_agent = LangGraphAgent()
        logger.info(" LangGraph agent initialized")
    return _langgraph_agent

USE_LANGGRAPH = os.getenv("USE_LANGGRAPH_AGENT", "false").lower() == "true"
logger.info(f" Routing mode: {'LangGraph Agent' if USE_LANGGRAPH else 'Traditional Agent'}") 
async def get_user_identity_from_token(authorization: Optional[str] = Header(None)) -> dict:
    if authorization is None:
        raise HTTPException(status_code=401, detail="Authorization header is missing")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=401,
            detail="Invalid authorization scheme. Header must be 'Bearer <token>'."
        )
    try:
        decoded_token = jwt.decode(token, options={"verify_signature": False})
        user_id = decoded_token.get("Id")
        database_name = decoded_token.get("DataBaseName")
        if not user_id:
            raise HTTPException(status_code=400, detail="Claim 'Id' not found in token.")
        print(f"Successfully decoded token for Id: {user_id}")
        return {"Id": user_id, "token": token, "DataBaseName": database_name}
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")

class ProcessQueryRequest(BaseModel):
    query: str
    user_id: Optional[str] = None
    image_url: Optional[str] = None
    image_urls: Optional[List[str]] = []
    chat_history: Optional[List[Dict]] = []
    current_constraints: Optional[Dict] = {}
    last_agent_context: Optional[Dict] = {}
    force_tool_type: Optional[str] = None
    token: Optional[str] = None
    start_date: Optional[str] = None  # Format: YYYY-MM-DD for weekly meal plan start
    end_date: Optional[str] = None    # Format: YYYY-MM-DD for weekly meal plan end
    
class ProcessQueryResponse(BaseModel):
    answer: str
    follow_up_prompt: Optional[str] = None
    audio: Optional[str] = None
    context: Dict
    updated_constraints: Dict
    updated_last_agent_context: Dict
    grocery_list_json: Optional[List[Dict]] = None  # 🛒 JSON array for weekly grocery lists
class MealCheckInRequest(BaseModel):
    meal_plan_text: str
    ConsumptionDate: str
    ConsumptionTime: str
class Nutrient(BaseModel):
    Value: Optional[float]
    Unit: Optional[str]
    Per: Optional[str]
    RDA: Optional[str]
class NutritionData(BaseModel):
    Food_Name: str = Field(..., description="Name of the food/product")
    Serving_Size: Optional[str] = Field(None, description="Serving size (e.g., '1 package (34g)')")
    Servings_Per_Pack: Optional[str] = Field(None, description="Number of servings per pack")
    Nutrition: Dict[str, Nutrient] = Field(..., description="Nutrients with values, units, and RDA")

async def _download_and_encode_image_from_url(url: str) -> Optional[str]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=15) as response:
                response.raise_for_status()
                image_bytes = await response.read()
                return base64.b64encode(image_bytes).decode("utf-8")
    except Exception as e:
        logger.error(f"Failed to download or encode image from URL {url}: {e}")
        return None
    
YOLO_FOOD_MODEL = None

def _load_yolo_model():
    global YOLO_FOOD_MODEL
    try:
        if not os.path.exists(YOLO_MODEL_PATH):
            logger.error(f"CRITICAL: YOLO model file not found at: {YOLO_MODEL_PATH}")
            return
        YOLO_FOOD_MODEL = YOLO(YOLO_MODEL_PATH)
        logger.info(f"Successfully loaded YOLOv9 food model from {YOLO_MODEL_PATH}")
        YOLO_FOOD_MODEL(Image.new('RGB', (640, 640)), verbose=False) 
        logger.info("YOLOv9 model warmed up.")
        
    except Exception as e:
        logger.error(f"Failed to load YOLOv9 model: {e}")
_load_yolo_model()


def normalize_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r'\([^)]*\)', ' ', text) 
    text = re.sub(r'[_\-]', ' ', text)      
    text = re.sub(r'[^a-z\s]', ' ', text)    
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def expand_variations(word: str) -> set:
    variations = set()
    variations.add(word)
    variations.add(p.plural(word))
    variations.add(p.singular_noun(word) or word)
    variations.add(lemmatizer.lemmatize(word))
    return {w for w in variations if w}

def generate_phrase_variations(phrase: str) -> set:
    phrase = normalize_text(phrase)
    words = phrase.split()
    variations = set()
    variations.add(phrase)
    expanded_words = [expand_variations(w) for w in words]
    from itertools import product
    for combo in product(*expanded_words):
        variations.add(" ".join(combo))
    return variations

async def extract_text_from_base64(image_base64: str, cv_client: Optional[ComputerVisionClient]) -> str:
    image_bytes = base64.b64decode(image_base64)
    extracted_text = ""

    try:
        if cv_client:
            image_stream = io.BytesIO(image_bytes)
            read_response = cv_client.read_in_stream(image=image_stream, raw=True)
            operation_location = read_response.headers["Operation-Location"]
            operation_id = operation_location.split("/")[-1]

            while True:
                try:
                    read_result = cv_client.get_read_result(operation_id)
                except Exception as e:
                    logger.error(f"Failed to retrieve OCR result for Base64 image: {e}")
                    break
                current_status = read_result.status
                if current_status not in ['notStarted', 'running']:
                    break
                await asyncio.sleep(1)

            if read_result.status == "succeeded":
                for page in read_result.analyze_result.read_results:
                    for line in page.lines:
                        extracted_text += line.text + " "
            else:
                logger.error(f"Azure OCR failed with status: {read_result.status}")
    except Exception as e:
        logger.error(f"Failed to start Azure Read operation from Base64 stream: {e}")

    if not extracted_text:
        try:
            logger.info("Attempting fallback OCR with Pytesseract...")
            image = Image.open(io.BytesIO(image_bytes))
            extracted_text = pytesseract.image_to_string(image)
            logger.info(f"Pytesseract extracted {len(extracted_text)} chars.")
        except Exception as e:
            logger.error(f"Fallback OCR failed: {e}")

    return extracted_text.strip()

def get_llm_client():
    if OCR_TEXT_ANALYSIS_MODELS:
        model_cfg = OCR_TEXT_ANALYSIS_MODELS[0]
        return AzureChatOpenAI(
            azure_endpoint=model_cfg["endpoint"].split("/openai")[0],  
            deployment_name="gpt-4o-mini-weekly",
            api_version="2025-01-01-preview",  
            api_key=model_cfg["api_key"],
            temperature=0.0,
        )
    return None

llm_model = get_llm_client()

def get_cv_client():
    if COMPUTER_VISION_MODELS:
        return ComputerVisionClient(COMPUTER_VISION_MODELS[0]["endpoint"], CognitiveServicesCredentials(COMPUTER_VISION_MODELS[0]["api_key"]))
    return None

cv_client = get_cv_client()
def create_profile_chain(llm):
    parser = PydanticOutputParser(pydantic_object=NutritionData)
 
    system_prompt = f"""
You are a nutrition label extraction assistant.
Your goal is to extract **nutritional values per serving** from OCR text, not per 100g.
 
---
 
### Column Understanding
... (keep your column logic as‑is)
...

---
 
### Output Rules
- Output strictly **valid JSON** (no text).
- Output a **single JSON object** that matches the `NutritionData` Pydantic model below.
- Do not output a list; output an object with keys:
  - "Food_Name"
  - "Serving_Size" (optional)
  - "Servings_Per_Pack" (optional)
  - "Nutrition" (a dictionary of nutrient names → Nutrient objects)

Each Nutrient must have:
- "Value": float or null
- "Unit": string or null
- "Per": string or null (e.g., "Per Serve", "Per 100 g")
- "RDA": string or null (e.g., "25", "10.5")

- Ignore per‑100 g values when per‑serve values exist.
- If only "Per 100 g" values are available and serving size is known, calculate per‑serving values using:
  per_serving_value = (per_100g_value * serving_size) / 100
  and mark them as "Per Serve".

---
 
### Example
Input OCR:
Energy (kcal)
524
105
Protein (g)
7.1
1.4
 
Output JSON:
{{
  "Food_Name": "Sample Product",
  "Serving_Size": "30 g",
  "Servings_Per_Pack": "10",
  "Nutrition": {{
    "Energy": {{
      "Value": 105,
      "Unit": "kcal",
      "Per": "Per Serve",
      "RDA": "20"
    }},
    "Protein": {{
      "Value": 1.4,
      "Unit": "g",
      "Per": "Per Serve",
      "RDA": "15"
    }}
  }}
}}

---
 
{parser.get_format_instructions()}
"""
 
    prompt_template = ChatPromptTemplate.from_messages([
        SystemMessage(content=system_prompt),
        HumanMessagePromptTemplate.from_template("Text to parse:\n\n{text}"),
    ])

    chain = prompt_template | llm | parser
    return chain
    
def clean_serving_size(serving_size_raw: Optional[str]) -> str:
    """
    Cleans the raw serving size string to prioritize displaying all explicit weights/volumes,
    e.g., converting "1 serving 100ml (55g)" to "1 serving / 100ml / 55g".
    """
    if not serving_size_raw:
        return 'N/A'

    explicit_measures = re.findall(
        r'([\d\./,]+)\s*?(g|mg|ml|oz|cup|tbsp|tsp|fl\.? oz|gram|serving|pack)', 
        serving_size_raw, 
        re.IGNORECASE
    )
    
    clean_measures_set = set()
    
    for quantity, unit in explicit_measures:
        unit_lower = unit.lower()
        
        if 'g' in unit_lower or 'gram' in unit_lower: unit = 'g'
        elif 'ml' in unit_lower: unit = 'ml'
        elif 'cup' in unit_lower: unit = 'cup'
        elif 'oz' in unit_lower: unit = 'oz'
        elif 'serving' in unit_lower: unit = 'serving'
        elif 'pack' in unit_lower: unit = 'pack'
        elif 'mg' in unit_lower: unit = 'mg'
        elif 'tbsp' in unit_lower: unit = 'tbsp'
        elif 'tsp' in unit_lower: unit = 'tsp'
        elif 'fl. oz' in unit_lower: unit = 'fl oz'
        else: continue

        measure_str = f"{quantity}{unit}" if unit in ['g', 'mg', 'ml', 'oz'] else f"{quantity} {unit}"
        clean_measures_set.add(measure_str.strip())
        
    if clean_measures_set:
        measures_list = list(clean_measures_set)
        
        def custom_sort(item):
            item_lower = item.lower()
            if 'serving' in item_lower or 'pack' in item_lower or re.match(r'^\d+$', item_lower):
                 return 0
            elif 'cup' in item_lower or 'ml' in item_lower or 'fl oz' in item_lower:
                 return 1
            else: 
                 return 2
        
        measures_list.sort(key=custom_sort)

        return " / ".join(measures_list)

    final_fallback = serving_size_raw
    final_fallback = re.sub(r'package|serving size|container|pack|ea\.', '', final_fallback, flags=re.IGNORECASE).strip()
    
    final_fallback = re.sub(r'\s+/\s*', ' / ', final_fallback)
    final_fallback = re.sub(r'\s+', ' ', final_fallback).strip()
    
    return final_fallback if final_fallback else 'N/A'

async def handle_process_query_langgraph(user_id: str, token: str, request: ProcessQueryRequest, background_tasks: BackgroundTasks) -> dict:
    """
    LangGraph agent handler with enhanced validation and retry logic.
    Wraps the LangGraph agent to match the traditional system's response format.
    """
    try:
        langgraph_agent = get_langgraph_agent()
        if not langgraph_agent:
            logger.error("LangGraph agent not initialized, falling back to traditional")
            return await handle_process_query(user_id, token, request, background_tasks)
        
        from langchain_core.messages import HumanMessage
        
        thread_id = request.last_agent_context.get("thread_id") if request.last_agent_context else f"thread_{user_id}_{int(time.time())}"
        
        logger.info(" Running LangGraph agent...")
        final_state = await langgraph_agent.invoke_sync(
            user_message=request.query,
            thread_id=thread_id,
            user_id=user_id,
            profile_data=request.current_constraints,
            constraints=request.current_constraints,
            calorie_target=request.current_constraints.get("target_calories")
        )
        
        messages = final_state.get("messages", [])
        if messages:
            last_message = messages[-1]
            answer = last_message.content if hasattr(last_message, 'content') else str(last_message)
        else:
            answer = "I apologize, but I couldn't generate a response."
        
        result = {
            "answer": answer,
            "audio": None, 
            "context": final_state.get("context", {}),
            "updated_constraints": request.current_constraints,
            "updated_last_agent_context": {
                **request.last_agent_context,
                "thread_id": final_state.get("thread_id"),
                "validation_errors": final_state.get("validation_errors", []),
                "last_system": "langgraph"
            }
        }
        
        logger.info(f" LangGraph agent completed successfully")
        return result
        
    except Exception as e:
        logger.error(f" LangGraph agent failed: {e}", exc_info=True)
        logger.info(" Falling back to traditional agent")
        return await handle_process_query(user_id, token, request, background_tasks)


async def handle_process_query(user_id: str, token: str, request: ProcessQueryRequest, background_tasks: BackgroundTasks) -> dict:
    final_query = request.query
    force_tool = request.force_tool_type
    all_image_urls = []
    all_ocr_text_to_combine = []
    analysis_results = []
    
    if request.image_url:
        all_image_urls.append(request.image_url)
    if request.image_urls:
        all_image_urls.extend(request.image_urls)
    
    if all_image_urls:
        logger.info(f"Image URLs detected: {len(all_image_urls)} images. Analyzing food...")

        all_ocr_text_to_combine = []
        analysis_results = []
        for idx, img_url in enumerate(all_image_urls):
            try:
                if img_url.startswith("data:"):
                    base64_data = img_url.split(",", 1)[1] if "," in img_url else img_url
                    image_base64 = base64_data
                else:
                    image_base64 = await _download_and_encode_image_from_url(img_url)
                
                if not image_base64:
                    logger.warning(f"Failed to process image {idx+1}: Could not obtain base64 data.")
                    continue

                messages = [{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
                        {"type": "text", "text": f"""You are an expert food recognition model specialized in analyzing meal images.
                Your job is to accurately identify what type of food is shown and provide results in a structured, minimal format.

                ### Classification Rules:
                1. If the image contains packaged food (e.g., chips packet, bottled drink, chocolate bar with wrapper, cereal box),
                respond with exactly:
                PACKAGED_FOOD

                2. If the image contains unpackaged, edible food (e.g., cooked or prepared meal, served dish, fruit, salad, sandwich, wrap, Chicken Caesar Salad):
                respond with exactly:
                CONTINENTAL_FOOD

                3. If the image does not contain food, respond with exactly:
                NON_FOOD_IMAGE"""
                    }]
                }]
                
                logger.info(f"Routing request for image {idx+1} to the dedicated Food Image LLM Service.")
                
                try:
                    analysis_text = await asyncio.wait_for(
                        agent.food_image_llm_service.query_multimodal(
                            messages=messages,
                            max_tokens=15,
                            temperature=0
                        ),
                        timeout=15.0 
                    )
                except asyncio.TimeoutError:
                    logger.error(f"[IMAGE] Timeout classifying image {idx+1}")
                    continue
                    
                if analysis_text.upper().strip() == "PACKAGED_FOOD":
                    ocr_text = await extract_text_from_base64(image_base64, cv_client)
                    if ocr_text:
                        all_ocr_text_to_combine.append(ocr_text)
                    
                elif analysis_text and "NON_FOOD" not in analysis_text.upper():
                    if 'YOLO_FOOD_MODEL' in globals() and YOLO_FOOD_MODEL:
                        try:
                            image_bytes = base64.b64decode(image_base64)
                            img = Image.open(io.BytesIO(image_bytes))
                            yolo_results = YOLO_FOOD_MODEL(img, verbose=False)
                            detected_classes = yolo_results[0].boxes.cls
                            if detected_classes is not None and len(detected_classes) > 0:
                                detected_items = [YOLO_FOOD_MODEL.names[int(c)] for c in detected_classes]
                                detected_items_list = sorted(list(set(detected_items))) 
                                logger.info(f"YOLOv9 detected items in image {idx+1}: {detected_items_list}")
                            else:
                                logger.info(f"YOLOv9 ran on image {idx+1} but found no food items.")

                        except Exception as yolo_e:
                            logger.error(f"YOLOv9 prediction failed for image {idx+1}: {yolo_e}")
                            detected_items_list = []
                    else:
                        logger.warning("YOLO_FOOD_MODEL is not loaded. Skipping YOLOv9 step.")
                    
                    llm_prompt_text = ""
                    if detected_items_list:
                        logger.info(f"Using YOLO-grounded prompt for image {idx+1}.")
                        llm_prompt_text = f"""You are an expert food recognition model.
    A YOLOv9 model has pre-scanned this image and detected the following items: {detected_items_list}

    Your task is to analyze the image and provide a detailed breakdown *only* for these detected items.
    - Provide a main food name (e.g., Chicken Salad, Veg Sandwich).
    - Provide an approximate portion size (e.g., 1 bowl, 1 sandwich).
    - In one set of parentheses, list the *visible components* from the YOLO list and any other major visible ingredients (like lettuce, sauce) with their approximate quantities.

    Format strictly as:
    <Main food name> (<portion size>) (item1 (quantity), item2 (quantity), item3 (quantity))
    
    Example:
    Chicken Caesar Salad (1 bowl) (grilled chicken (100g), romaine lettuce (80g), croutons (30g), parmesan (10g), dressing (20g))
    """
                        messages = [{
                            "role": "user",
                            "content": [
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
                                {"type": "text", "text": llm_prompt_text}]
                        }]
                        analysis_text = await agent.food_image_llm_service.query_multimodal(
                            messages=messages,
                            max_tokens=512,
                            temperature=0
                        )
                        analysis_results.append(f"Image {idx+1}: {analysis_text.strip()}")
                else:
                    logger.warning(f"Image {idx+1} was determined not to be food.")
 
            except Exception as e:
                logger.error(f"Error during image {idx+1} analysis API call: {e}")
 
    if all_ocr_text_to_combine:
        combined_ocr_prompt = "\n---\n".join(all_ocr_text_to_combine)
        
        try:
            if llm_model:
                profile_chain = create_profile_chain(llm_model)
                result = profile_chain.invoke({"text": combined_ocr_prompt})
                serving_size_clean = clean_serving_size(result.Serving_Size)
                summary_ui = f"**Food Name:** {result.Food_Name} | **Serving:** {serving_size_clean or 'N/A'}"
                
                nutrient_parts = []
                for k, nut in result.Nutrition.items():
                    unit_str = nut.Unit if nut.Unit else ''
                    clean_unit = re.sub(r'\s*\([^)]*\)\s*', '', unit_str).strip()
                    nut_info = f"{nut.Value}{clean_unit}"
                    nutrient_parts.append(f"{k}: {nut_info}")
 
                if nutrient_parts:
                    summary_ui += " | " + " | ".join(nutrient_parts)
                
                analysis_results = [summary_ui]
            else:
                logger.warning("LLM model for OCR text analysis not configured. Skipping structured extraction.")
                analysis_results.append(f"Packaged Food: Raw Combined OCR Data (Extraction Skipped): {combined_ocr_prompt.strip()}")
            
        except Exception as e:
            logger.error(f"Combined structured extraction failed: {e}")
            analysis_results.append(f"Packaged Food: Raw Combined OCR Data (Extraction Failed): {combined_ocr_prompt.strip()}")
    
    if analysis_results:
        combined_analysis = "\n".join(analysis_results)
        final_query = f"{request.query} - {combined_analysis}"
        logger.info(f"Image analysis successful. New query: {final_query}")
    elif all_image_urls and not analysis_results and not all_ocr_text_to_combine:
        # Images were provided but none contained food
        non_food_message = (
            "I can only analyze food and nutrition-related images. "
            "The image you shared doesn't appear to contain food items. "
            "Please share a photo of your meal, snack, or packaged food item, "
            "and I'll provide a detailed nutritional breakdown for you! 🍽️"
        )
        return {
            "answer": non_food_message,
            "audio": non_food_message,
            "context": request.last_agent_context or {},
            "updated_constraints": request.current_constraints,
            "updated_last_agent_context": request.last_agent_context or {},
            "grocery_list_json": [],
        }
 
    result = await agent.process_query(
        user_id=user_id,
        query=final_query,
        chat_history=request.chat_history,
        current_constraints=request.current_constraints,
        last_agent_context=request.last_agent_context,
        background_tasks=background_tasks,
        force_tool_type_str=force_tool,
        authorization=token,
        start_date=request.start_date,  # Pass custom start date
        end_date=request.end_date        # Pass custom end date
    )
    
    print("Thankyou")
    return {
        "answer": result.get("answer", "An error occurred."),
        "audio": result.get("audio"),
        "context": result.get("context", {}),
        "updated_constraints": result.get("updated_constraints", request.current_constraints),
        "updated_last_agent_context": result.get("updated_last_agent_context", request.last_agent_context),
        "grocery_list_json": result.get("grocery_list_json", []),  # 🛒 Pass grocery_list_json to frontend
    }
 
@app.post("/process-query", response_model=ProcessQueryResponse)
async def process_user_query(request: ProcessQueryRequest, background_tasks: BackgroundTasks, identity: dict = Depends(get_user_identity_from_token)):
    """Unified endpoint that routes to either traditional or LangGraph agent based on environment variable"""
    user_id = identity["Id"]
    token = identity["token"]
    
    if USE_LANGGRAPH and LANGGRAPH_AVAILABLE:
        logger.info(f" Routing to LangGraph agent for user {user_id}")
        result = await handle_process_query_langgraph(user_id=user_id, token=token, request=request, background_tasks=background_tasks)
    else:
        if USE_LANGGRAPH and not LANGGRAPH_AVAILABLE:
            logger.warning(" LangGraph requested but not available, falling back to traditional agent")
        logger.info(f" Routing to traditional agent for user {user_id}")
        result = await handle_process_query(user_id=user_id, token=token, request=request, background_tasks=background_tasks)
    
    return ProcessQueryResponse(
        answer=result["answer"],
        grocery_list_json=result.get("grocery_list_json", []),
        audio=result["audio"],
        context=result.get("context", {}),
        updated_constraints=result.get("updated_constraints"),
        updated_last_agent_context=result.get("updated_last_agent_context")
    )

MIME_TYPE_MAP = {
        'image/jpeg': '.jpg',
        'image/png': '.png',
        'application/pdf': '.pdf',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
        'application/msword': '.doc',
        'text/plain': '.txt',
        'text/csv': '.csv',
    }

async def handle_analyze_blood_report(user_id: str,
    token: str,
    blood_report_file: Optional[UploadFile] = None,
    report_text: Optional[str] = None,
    report_url: Optional[str] = None,
    temp_dir: str = "temp_uploads"
) -> Dict[str, Any]:
    temp_file_path = None    
    os.makedirs(temp_dir, exist_ok=True)
    try:
        if blood_report_file:
            logger.info(f"Processing uploaded file for user {user_id}")
            content_type = blood_report_file.content_type
            extension = MIME_TYPE_MAP.get(content_type)
            if not extension and blood_report_file.filename:
                filename_ext = os.path.splitext(blood_report_file.filename)[1].lower()
                if filename_ext in ['.jpg', '.jpeg', '.png', '.pdf', '.docx', '.txt', '.csv']:
                    extension = filename_ext
            if not extension:
                raise HTTPException(status_code=400, detail=f"Unsupported or unrecognized file type: '{content_type}'. Please upload a valid document or image.")
            safe_filename = f"{uuid.uuid4()}{extension}"
            temp_file_path = os.path.join(temp_dir, f"{user_id}_{safe_filename}")
            with open(temp_file_path, "wb") as buffer:
                buffer.write(await blood_report_file.read())
        elif report_text:
            logger.info(f"Processing raw text input for user {user_id}")
            temp_file_path = os.path.join(temp_dir, f"{user_id}_{uuid.uuid4()}.txt")
            with open(temp_file_path, "w", encoding="utf-8") as f:
                f.write(report_text)
        elif report_url:
            logger.info(f"Processing URL input for user {user_id}: {report_url}")
            async with aiohttp.ClientSession() as session:
                async with session.get(report_url, timeout=30) as response:
                    response.raise_for_status()
                    content_type = response.headers.get('Content-Type', '').split(';')[0].strip()
                    extension = MIME_TYPE_MAP.get(content_type)
                    if not extension:
                        parsed_url_path = urlparse(report_url).path
                        url_extension = os.path.splitext(parsed_url_path)[1].lower()
                        if url_extension in MIME_TYPE_MAP.values():
                            extension = url_extension
                        else:
                            raise HTTPException(status_code=400, detail=f"Could not determine file type from URL. The server reported Content-Type: '{content_type}'.")
                    filename = f"{uuid.uuid4()}{extension}"
                    temp_file_path = os.path.join(temp_dir, f"{user_id}_{filename}")
                    with open(temp_file_path, "wb") as f:
                        while True:
                            chunk = await response.content.read(8192)
                            if not chunk:
                                break
                            f.write(chunk)
        else:
            raise HTTPException(status_code=400, detail="No input provided. Please provide a file, text, or a URL.")
        logger.info(f"Input saved temporarily at: {temp_file_path}")
        blood_analyzer_tool = agent.tools.get(ToolType.BLOOD_REPORT_ANALYZER.value)
        if not blood_analyzer_tool:
            raise HTTPException(status_code=500, detail="Blood Report Analyzer tool is not available.")
        analysis_result = await blood_analyzer_tool.analyze_blood_report(file_path=temp_file_path)
        if "error" in analysis_result:
            raise HTTPException(status_code=422, detail=analysis_result["error"])
        doc_id = str(uuid.uuid4())
        save_payload = {"doc_id": doc_id, "translation": analysis_result}
        db_tool = agent.tools["database_persistence_tool"]
        save_result = await db_tool.execute(
            operation="save_blood_report_analysis",
            analysis_data=save_payload,
            token=token,
            user_id=user_id
        )
        if not save_result.success:
            logger.error(f"Failed to save blood report analysis for user {user_id}: {save_result.error}")
        else:
            logger.info(f"Successfully saved blood report analysis for user {user_id}.")
        return analysis_result

    except (requests.exceptions.RequestException, aiohttp.ClientError) as e:
        logger.error(f"Failed to download file from URL: {e}")
        raise HTTPException(status_code=400, detail=f"Failed to download file from URL: {e}")
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
                logger.info(f"Cleaned up temporary file: {temp_file_path}")
            except Exception:
                logger.exception("Failed to remove temporary file during cleanup.")

@app.post("/analyze-blood-report")
async def analyze_blood_report_endpoint(
    identity: dict = Depends(get_user_identity_from_token),
    blood_report_file: Optional[UploadFile] = File(None),
    report_text: Optional[str] = Form(None),
    report_url: Optional[str] = Form(None)
):
    user_id = identity["Id"]
    token = identity["token"]
    
    try:
        analysis_result = await handle_analyze_blood_report(
            user_id=user_id,
            token=token,
            blood_report_file=blood_report_file,
            report_text=report_text,
            report_url=report_url
        )
        return analysis_result
    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        logger.error(f"An unexpected error occurred during blood report analysis for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="An internal error occurred while processing the report.")
                             
@app.post("/meal-checkin")
async def meal_checkin(request: MealCheckInRequest, identity: dict = Depends(get_user_identity_from_token)):
    user_id = identity["Id"]; token = identity["token"]
    print(f"Received meal check-in for user_id: {user_id}")
    db_tool = agent.tools[ToolType.DATABASE_PERSISTENCE_TOOL.value]
    result = await db_tool.execute(operation="meal_checkin", checkin_data=request.dict(), token=f"Bearer {token}")
    if not result.success:
        raise HTTPException(status_code=502, detail=f"Failed to ssave meal check-in to backend: {result.error}")
    return {"status": "success", "message": "Meal check-in recorded successfully.", "details": result.data}

@app.post("/analyze-meal-image")
async def analyze_meal_image(request: ProcessQueryRequest, background_tasks: BackgroundTasks, identity: dict = Depends(get_user_identity_from_token)):
    user_id = identity["Id"]
    token = identity["token"]
    
    result = await handle_process_query(user_id=user_id, token=token, request=request, background_tasks=background_tasks)
    
    return ProcessQueryResponse(
        answer=result["answer"],
        audio=result["audio"],
        context=result.get("context", {}),
        updated_constraints=result.get("updated_constraints"),
        updated_last_agent_context=result.get("updated_last_agent_context")
    ) 



@app.post("/process-vitals-query", response_model=ProcessQueryResponse)
async def process_vitals_query(request: ProcessQueryRequest, identity: dict = Depends(get_user_identity_from_token)):
    user_id = identity["Id"]
    token = identity["token"]
    logger.info(f"Processing vitals query for user: {user_id}")

    result = await agent.process_query(
        user_id=user_id,
        query=request.query,
        chat_history=[], 
        current_constraints={},
        last_agent_context={},  
        force_tool_type_str=ToolType.VITAL_ADVISOR.value,
        authorization=token
    )
    
    return ProcessQueryResponse(
        answer=result.get("answer", "An error occurred while processing your vitals query."),
        audio=result.get("audio"),
        context=result.get("context", {}),
        updated_constraints=result.get("updated_constraints", request.current_constraints),
        updated_last_agent_context=result.get("updated_last_agent_context", request.last_agent_context)
    )
@app.get("/")
def read_root():
    return {"message": f"Friska FuelTesting Agent (v{app.version}) is running"}



import helpers.db_logic as db_logic  

@app.get("/get-profile")
async def get_user_profile_endpoint(identity: dict = Depends(get_user_identity_from_token)):
    """
    Directly fetches the user profile using db_logic, bypassing Agent tools.
    """
    user_id = identity["Id"]
    token = identity["token"]
    database_name = identity["DataBaseName"] 

    logger.info(f"Directly fetching profile for user {user_id}")

    if not database_name:
         raise HTTPException(status_code=400, detail="DataBaseName missing in token")

    try:

        result = await asyncio.to_thread(
            db_logic.get_profile_logic, 
            user_id=user_id, 
            database_name=database_name, 
            token=token
        )

        if result.get("status") == 200:
            return {
                "status": "success",
                "data": result.get("data")
            }
        else:
            raise HTTPException(status_code=result.get("status", 500), detail=result.get("error", "Unknown error"))

    except Exception as e:
        logger.error(f"Error in direct profile fetch: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    



@app.post("/transcribe/")
async def transcribe_audio_endpoint(file: UploadFile = File(...)):

    
    audio_content = await file.read()
    
    try:
        from helpers.utils import transcribe_speech
        
        stt_start = time.perf_counter()
        transcription = transcribe_speech(audio_content)
        stt_duration = time.perf_counter() - stt_start
        
        if not transcription:
            logger.warning("Whisper API returned empty transcription")
            return JSONResponse(content={
                "transcribed_text": "Could not understand audio.",
                "stt_duration": stt_duration
            })
        
        return JSONResponse(content={
            "transcribed_text": transcription,
            "stt_duration": stt_duration
        })
        
    except Exception as e:
        logger.error(f"Transcription error: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {e}")
    
async def chat_intent_summary(request: ProcessQueryRequest, identity: dict = Depends(get_user_identity_from_token)):

    if not request.chat_history:
        return {"status": "error", "message": "Payload chat_history is empty"}
    
    generated_title = await generate_payload_title_clean(request.chat_history, agent_global.agent)
    
    return {
        "status": "success", 
        "answer": generated_title 
    }