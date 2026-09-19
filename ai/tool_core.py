from abc import ABC, abstractmethod
import asyncio
from binascii import Error
import itertools
import re
import os
import aiohttp
from dataclasses import dataclass, field
import time
from enum import Enum
import json
import logging
import random
from typing import Any, Dict, List, Optional, Callable
from dotenv import load_dotenv
from pydantic import BaseModel
from collections import deque
import traceback
from pydantic import ValidationError
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    wait_exponential_jitter,
    stop_after_attempt,
    RetryError
)

from helpers.utils import load_models

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()


def get_optimized_context(chat_history: List[Dict], max_messages: int = 5) -> List[Dict]:

    if not chat_history or len(chat_history) <= max_messages:
        return chat_history or []
    
    optimized = chat_history[-max_messages:]
    
    if optimized and optimized[0].get('role') == 'assistant':
        optimized = [
            {'role': 'user', 'content': '[Previous conversation context]'},
            *optimized
        ]
    
    logger.debug(f"⚡ CONTEXT OPTIMIZATION: Reduced {len(chat_history)} messages → {len(optimized)} messages")
    return optimized
    
AUDIO_SUMMARY_MODELS = load_models("AUDIO_SUMMARY_MODELS")
BLOOD_REPORT_MODELS = load_models("BLOOD_REPORT_MODELS")
FOOD_IMAGE_MODELS = load_models("FOOD_IMAGE_MODELS")
GENERAL_MODELS = load_models("GENERAL_MODELS")

_GPT4O_SEMAPHORE = None

def get_gpt4o_semaphore():

    global _GPT4O_SEMAPHORE
    if _GPT4O_SEMAPHORE is None:
        try:
            loop = asyncio.get_running_loop()
            _GPT4O_SEMAPHORE = asyncio.Semaphore(30)
            logger.info(" GPT-4o-mini Semaphore lazy-initialized with limit=30 (2000 RPM ≈ 33 RPS)")
        except RuntimeError:
            logger.warning(" get_gpt4o_semaphore() called outside async context - semaphore will be created on first use")
    return _GPT4O_SEMAPHORE

class ToolType(Enum):
    MEAL_PLAN_GENERATOR = "meal_plan_generator"
    WEEKLY_MEAL_PLAN_GENERATOR = "weekly_meal_plan_generator"
    NUTRITION_ANALYZER = "nutrition_analyzer"
    FOOD_MATCHER = "food_matcher"
    DISEASE_ADVISOR = "disease_advisor"
    VITAL_ADVISOR = "vital_advisor"
    CALORIE_CALCULATOR = "calorie_calculator"
    HISTORY_RETRIEVER = "history_retriever"
    QUERY_CLASSIFIER = "query_classifier"
    DATABASE_PERSISTENCE = "database_persistence_tool"
    MEAL_CHECK_IN = "meal_check_in"
    MEAL_RETRIEVER = "meal_retriever"
    GOAL_UPDATER = "goal_updater"
    FOOD_RETRIEVER = "food_retriever"
    MEAL_PLAN_ADJUSTER = "meal_plan_adjuster"
    CONSTRAINT_EXTRACTOR = "constraint_extractor"
    PROFILE_RETRIEVER = "profile_retriever"
    PROFILE_UPDATER = "profile_updater"
    GENERAL_QUERY = "general_query"
    BLOOD_REPORT_ANALYZER = "blood_report_analyzer"
    BLOOD_REPORT_QUERY = "blood_report_query" 
    PANEL_QUERY = "panel_query"
    MEAL_INGREDIENTS = "meal_ingredients"
    PROFILE_SUMMARY = "profile_summary"
    GREETING = "greeting"
    SUMMARIZER  = "summarizer"
    AUDIOSUMMARY = "audiosummary" 
    SPECIAL_MEAL_PLAN_GENERATOR = "special_meal_plan_generator"
    CRAVING_ASSISTANT = "craving_assistant"
    GROCERY_LIST = "grocery_list"
    FITNESS_PLAN_GENERATOR = "fitness_plan_generator"
    WORKOUT_PLAN_ADJUSTER = "workout_adjuster_tool"
    SESSION_BOOKING = "session_booking"
    WATER_STEP_ADVISOR = "water_step_advisor"
    FITNESS_PROFILE_UPDATER = "fitness_profile_updater"
    KNOWLEDGE_BASE = "knowledge_base"
    FOOD_ANALYZER = "food_analyzer"
    MEDICAL_DISCLAIMER = "medical_disclaimer"
    WELLNESS_ADVISOR = "wellness_advisor"
    
    OTHER = "other"
    
@dataclass
class ToolResult:
    success: bool
    data: Any
    error: Optional[str] = None
    metadata: Optional[Dict] = None

class BaseTool(ABC):
    def __init__(self, name: str, description: str): 
        self.name, self.description = name, description
    
    EMPATHETIC_FALLBACK = (
        "I'm working hard to handle this request, but I've encountered a slight technical difficulty at the moment. "
        "I've noted this as feedback to update my knowledge base so I can better assist with similar questions in the future. "
        "Thank you for your patience and for helping me improve!"
    )
    
    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult: 
        pass
    
    async def safe_execute(self, **kwargs) -> ToolResult:
        
        
        try:
            return await self.execute(**kwargs)
            
        except asyncio.TimeoutError as e:
            logger.error(f" SYSTEM_ERROR [LLM_TIMEOUT] in {self.name}: {str(e)}")
            logger.error(f"Tool: {self.name}, Kwargs keys: {list(kwargs.keys())}")
            return ToolResult(
                success=False,
                data={"answer": self.EMPATHETIC_FALLBACK},
                error="llm_timeout",
                metadata={"error_handled": True, "error_type": "LLM_TIMEOUT", "tool_name": self.name}
            )
            
        except ValidationError as e:
            logger.error(f" SYSTEM_ERROR [VALIDATION_ERROR] in {self.name}: {str(e)}")
            logger.error(f"Validation details: {e.errors()}")
            return ToolResult(
                success=False,
                data={"answer": self.EMPATHETIC_FALLBACK},
                error="pydantic_validation_failed",
                metadata={"error_handled": True, "error_type": "VALIDATION_ERROR", "tool_name": self.name, "validation_errors": e.errors()}
            )
            
        except TypeError as e:
            logger.error(f" SYSTEM_ERROR [TYPE_ERROR] in {self.name}: {str(e)}")
            logger.error(f"Kwargs provided: {list(kwargs.keys())}")
            logger.error(traceback.format_exc())
            return ToolResult(
                success=False,
                data={"answer": self.EMPATHETIC_FALLBACK},
                error="tool_parameter_mismatch",
                metadata={"error_handled": True, "error_type": "TYPE_ERROR", "tool_name": self.name}
            )
            
        except KeyError as e:
            logger.error(f" SYSTEM_ERROR [KEY_ERROR] in {self.name}: {str(e)}")
            logger.error(traceback.format_exc())
            return ToolResult(
                success=False,
                data={"answer": self.EMPATHETIC_FALLBACK},
                error="missing_required_data",
                metadata={"error_handled": True, "error_type": "KEY_ERROR", "tool_name": self.name}
            )
            
        except AttributeError as e:
            logger.error(f" SYSTEM_ERROR [ATTRIBUTE_ERROR] in {self.name}: {str(e)}")
            logger.error(traceback.format_exc())
            return ToolResult(
                success=False,
                data={"answer": self.EMPATHETIC_FALLBACK},
                error="attribute_access_error",
                metadata={"error_handled": True, "error_type": "ATTRIBUTE_ERROR", "tool_name": self.name}
            )
            
        except Exception as e:
            logger.error(f" SYSTEM_ERROR [UNEXPECTED] in {self.name}: {str(e)}")
            logger.error(f"Full traceback:\n{traceback.format_exc()}")
            logger.error(f"Query: {kwargs.get('query', 'N/A')}")
            return ToolResult(
                success=False,
                data={"answer": self.EMPATHETIC_FALLBACK},
                error="unexpected_system_error",
                metadata={"error_handled": True, "error_type": "UNEXPECTED", "tool_name": self.name}
            )
    
    def validate_required_params(self, kwargs: dict, required: list) -> Optional[str]:

        missing = [param for param in required if param not in kwargs or kwargs[param] is None]
        if missing:
            return f"Missing required parameters: {', '.join(missing)}"
        return None

class TokenBucket:
    def __init__(self, capacity: int, fill_rate: float):
        self.capacity = capacity  
        self.fill_rate = fill_rate 
        self.tokens = capacity
        self.last_check = time.time()
        self._lock = None  
        logger.info(f"TokenBucket initialized with capacity {capacity} and rate {fill_rate} RPS.")
    
    @property
    def lock(self):
        if self._lock is None:
            try:
                self._lock = asyncio.Lock()
                logger.debug("TokenBucket lock lazy-initialized")
            except RuntimeError:
                # No running loop - will fail gracefully
                logger.warning("TokenBucket lock creation failed - no running event loop")
        return self._lock

    async def consume(self, count: int = 1):
        async with self.lock:
            now = time.time()
            self.tokens += self.fill_rate * (now - self.last_check)
            self.last_check = now
            self.tokens = min(self.tokens, self.capacity)

            if self.tokens >= count:
                self.tokens -= count
                return True
            
            needed = count - self.tokens
            wait_time = needed / self.fill_rate
        
        logger.warning(f"Rate limit exceeded. Waiting for {wait_time:.3f}s to consume token.")
        await asyncio.sleep(wait_time)
        return await self.consume(count)

class LLMService:
    def __init__(self, service_type: str = 'general', model_list: Optional[List[Dict]] = None, rate_limiter: Optional[TokenBucket] = None):
        self.service_type = service_type
        self.rate_limiter = None  
        
        self.env_mapping = {
            'general': 'GENERAL_MODELS',
            'blood_report': 'BLOOD_REPORT_MODELS',
            'audio_summary': 'AUDIO_SUMMARY_MODELS',
            'weekly_plan': 'OPENAI_WEEKLY_PLAN_CONFIG',
            'query_classification': 'OPENAI_WEEKLY_PLAN_CONFIG',
            'breakfast': 'BREAKFAST_MODELS',
            'lunch': 'LUNCH_MODELS',
            'dinner': 'DINNER_MODELS',
            'food_image': 'FOOD_IMAGE_MODELS',
            'default': 'GENERAL_MODELS' 
        }

        if model_list:
            self.configs = model_list
        else:
            env_key = self.env_mapping.get(service_type, 'GENERAL_MODELS')
            self.configs = self._load_configs_from_env(env_key)

        if not self.configs:
            logger.error(f"CRITICAL: No endpoints found for service '{service_type}'")
            self.configs = [{"endpoint": "", "api_key": ""}]

        self.config_cycler = itertools.cycle(self.configs)
        self._cycler_lock = None  
        
        self.is_gpt4o_mini = service_type in ['audio_summary', 'query_classification']
        
        logger.info(f" LLMService initialized with STRICT ROUND-ROBIN for '{service_type}' ({len(self.configs)} endpoints)")
        if self.is_gpt4o_mini:
            logger.info(f" GPT-4o-mini detected - will use Semaphore(30) for rate control")
    
    @property
    def cycler_lock(self):
        if self._cycler_lock is None:
            try:
                self._cycler_lock = asyncio.Lock()
                logger.debug(f"LLMService cycler_lock lazy-initialized for {self.service_type}")
            except RuntimeError:
                logger.warning(f"LLMService lock creation failed for {self.service_type} - no running event loop")
        return self._cycler_lock

    def _load_configs_from_env(self, env_key: str) -> List[Dict[str, str]]:

        raw_val = os.getenv(env_key, "[]")
        try:
            data = json.loads(raw_val)
            
            if isinstance(data, list):
                if not data: return []
                return data
            
            elif isinstance(data, dict):
                return [data]
            
            return []
        except json.JSONDecodeError:
            logger.error(f"Failed to decode JSON for env var: {env_key}")
            return []
    
    async def _get_next_endpoint(self) -> Dict:

        async with self.cycler_lock:
            return next(self.config_cycler)

    async def _execute_single_request(self, messages: List[Dict], max_tokens: int, temperature: float, 
                                      response_format: Optional[Dict] = None, attempt_num: int = 1) -> Optional[str]:
        
        current_config = await self._get_next_endpoint()
        endpoint = current_config.get("endpoint")
        api_key = current_config.get("api_key")
        deployment = current_config.get("deployment")
        api_version = current_config.get("api_version")

        if not endpoint:
            logger.error(f"Configuration missing endpoint URL for service {self.service_type}")
            raise ValueError("Missing endpoint configuration")

        if deployment and api_version:
            base_endpoint = endpoint.rstrip('/')
            endpoint = f"{base_endpoint}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"

        request_start = time.time()

        try:
            serialized_messages = []
            for msg in messages:
                role = None
                content = None
                
                if isinstance(msg, dict):
                    role = msg.get("role")
                    content = msg.get("content")
                elif hasattr(msg, 'role') and hasattr(msg, 'content'):
                    role = msg.role
                    content = msg.content
                else:
                    try:
                        msg_dict = dict(msg)
                        role = msg_dict.get("role")
                        content = msg_dict.get("content")
                    except:
                        logger.warning(f"Could not serialize message: {type(msg)}, skipping")
                        continue
                
                if role is not None and content is not None:
                    role_str = str(role).lower() if not isinstance(role, str) else role
                    content_str = str(content) if not isinstance(content, str) else content
                    serialized_messages.append({"role": role_str, "content": content_str})
                else:
                    logger.warning(f"Message missing role or content, skipping: {msg}")
                    continue
            
            payload = {
                "messages": serialized_messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "seed": 42,
                **({"response_format": response_format} if response_format else {})
            }
            if temperature > 0.01:
                payload["top_p"] = 1.0

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
                "api-key": api_key
            }

            if self.is_gpt4o_mini:
                semaphore = get_gpt4o_semaphore()
                async with semaphore:
                    async with aiohttp.ClientSession() as session:
                        async with session.post(
                            endpoint,
                            headers=headers,
                            json=payload,
                            timeout=aiohttp.ClientTimeout(total=60)
                        ) as response:
                            return await self._process_response(response, endpoint, request_start, attempt_num)
            else:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        endpoint,
                        headers=headers,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=60)
                    ) as response:
                        return await self._process_response(response, endpoint, request_start, attempt_num)

        except aiohttp.ClientError as e:
            logger.error(f" Request exception (attempt {attempt_num}): {str(e)}")
            raise 

    async def _process_response(self, response: aiohttp.ClientResponse, endpoint: str, 
                                request_start: float, attempt_num: int) -> Optional[str]:
        
        latency_ms = (time.time() - request_start) * 1000
        
        if response.status == 429:
            error_text = await response.text()
            logger.warning(f" Rate limit (429) on attempt {attempt_num}. Switching to next endpoint...")
            raise Exception(f"HTTP 429 Rate Limit: {error_text}")
        
        if response.status >= 500:
            error_text = await response.text()
            logger.error(f" Server error ({response.status}) on attempt {attempt_num}: {error_text}")
            raise Exception(f"HTTP {response.status} Server Error: {error_text}")
        
        if response.status != 200:
            error_text = await response.text()
            logger.error(f"LLM query failed for {endpoint} with status {response.status}: {error_text}")
            return None

        response_text = await response.text()
        
        try:
            data = json.loads(response_text)
        except json.JSONDecodeError as e:
            logger.error(f"LLM query for {endpoint} returned invalid JSON. Error: {e}. Body: {response_text[:500]}")
            return None
        
        if not isinstance(data, dict):
            logger.error(f"LLM query for {endpoint} returned a non-dict response. Type: {type(data).__name__}. Body: {response_text[:500]}")
            return None
        
        choices = data.get('choices')
        if isinstance(choices, list) and len(choices) > 0:
            first_choice = choices[0]
            if isinstance(first_choice, dict):
                message = first_choice.get('message')
                if isinstance(message, dict):
                    content = message.get('content')
                    if content:
                        endpoint_short = endpoint.split('/')[2] if '/' in endpoint else endpoint[:40]
                        logger.info(f" LLM request succeeded (attempt {attempt_num}, latency: {latency_ms:.0f}ms) - endpoint: {endpoint_short}")
                        return content
        
        logger.error(f"Invalid response structure from API at {endpoint}. Data: {json.dumps(data)[:200]}")
        return None

    async def _execute_request(self, messages: List[Dict], max_tokens: int, temperature: float, 
                               response_format: Optional[Dict] = None) -> Optional[str]:
        
        validated_messages = []
        for i, msg in enumerate(messages):
            if not isinstance(msg, dict):
                logger.error(f"⚠️ SAFETY CHECK FAILED: Message {i} is {type(msg)}, not dict. Converting...")
                try:
                    if hasattr(msg, 'role') and hasattr(msg, 'content'):
                        role_str = str(msg.role).lower() if not isinstance(msg.role, str) else msg.role
                        content_str = str(msg.content) if not isinstance(msg.content, str) else msg.content
                        validated_messages.append({"role": role_str, "content": content_str})
                    else:
                        logger.error(f"Cannot convert message {i}, skipping")
                        continue
                except Exception as e:
                    logger.error(f"Failed to convert message {i}: {e}, skipping")
                    continue
            else:
                role = msg.get("role")
                content = msg.get("content")
                if role and content:
                    role_str = str(role).lower() if not isinstance(role, str) else role
                    content_str = str(content) if not isinstance(content, str) else content
                    validated_messages.append({"role": role_str, "content": content_str})
                else:
                    logger.warning(f"Message {i} missing role or content, skipping")
        
        if not validated_messages:
            logger.error("No valid messages to send after validation")
            return None
        
        attempt_num = 0
        
        try:
            async for attempt in AsyncRetrying(
                retry=retry_if_exception_type((Exception,)),  
                wait=wait_exponential_jitter(initial=1, max=10, jitter=0.25),  
                stop=stop_after_attempt(4), 
                reraise=False 
            ):
                with attempt:
                    attempt_num = attempt.retry_state.attempt_number
                    
                    result = await self._execute_single_request(
                        validated_messages, max_tokens, temperature, response_format, attempt_num
                    )
                    
                    if result:
                        return result
                    
                    logger.info(f" Retrying with next endpoint ({attempt_num + 1}/4)...")
                    raise Exception("Non-retriable error, trying next endpoint")
        
        except RetryError:
            logger.critical(f" All 4 attempts failed for '{self.service_type}' after trying 4 different endpoints")
        except Exception as e:
            logger.error(f" Unexpected error in _execute_request: {e}")
        
        return None

    async def query_multimodal(self, messages: List[Dict], max_tokens: int = 2048, temperature: float = 0.1) -> Optional[str]:

        content = await self._execute_request(messages, max_tokens, temperature)
        return content

    async def query(self, prompt: str, system_prompt: str, chat_history: Optional[List[Dict]] = None, max_tokens=4096, temperature: float = 0.1) -> str:
        messages = [{"role": "system", "content": system_prompt}]
        if chat_history:
            for msg in chat_history:
                if isinstance(msg, dict) and 'role' in msg and 'content' in msg:
                    role_str = str(msg["role"]).lower() if not isinstance(msg["role"], str) else msg["role"]
                    content_str = str(msg["content"]) if not isinstance(msg["content"], str) else msg["content"]
                    messages.append({"role": role_str, "content": content_str})
        messages.append({"role": "user", "content": prompt})
        
        content = await self._execute_request(messages, max_tokens, temperature)
        return content if content is not None else "Sorry, I'm having trouble connecting to my knowledge base right now. Please try again shortly."

    
    async def query_json(self, prompt: str, system_prompt: str, json_schema: Dict, chat_history: Optional[List[Dict]] = None, max_tokens=4096) -> Dict:
        schema_instruction = f"\n\nYou must output valid JSON matching this schema:\n{json.dumps(json_schema, indent=2)}"
        full_system_prompt = system_prompt + schema_instruction

        messages = [{"role": "system", "content": full_system_prompt}]
        
        if chat_history:
            for msg in chat_history:
                if isinstance(msg, dict) and 'role' in msg and 'content' in msg:
                    role_str = str(msg["role"]).lower() if not isinstance(msg["role"], str) else msg["role"]
                    content_str = str(msg["content"]) if not isinstance(msg["content"], str) else msg["content"]
                    messages.append({"role": role_str, "content": content_str})
        
        messages.append({"role": "user", "content": prompt})
        
        response_format = {"type": "json_object"}

        content_str = await self._execute_request(messages, max_tokens, 0.2, response_format)
        
        if content_str:
            try:
                return json.loads(content_str)
            except json.JSONDecodeError:
                logger.error(f"LLM returned invalid JSON, attempting to clean: {content_str[:200]}")
                
                cleaned_content = re.sub(r'^```(?:json)?\s*', '', content_str.strip(), flags=re.MULTILINE)
                cleaned_content = re.sub(r'\s*```$', '', cleaned_content.strip(), flags=re.MULTILINE)
                
                if cleaned_content != content_str:
                    logger.info("Stripped markdown code block wrappers from LLM response")
                    try:
                        return json.loads(cleaned_content)
                    except json.JSONDecodeError:
                        pass  
                
                clean_str = re.search(r'\{.*\}', cleaned_content, re.DOTALL)
                if clean_str:
                    try:
                        return json.loads(clean_str.group(0))
                    except json.JSONDecodeError:
                        logger.error("Failed to parse JSON even after cleaning.")
                return {}
        return {}

    async def query_structured(self, system_prompt: str, prompt: str, chat_history: Optional[List[Dict]], 
                               response_model, max_tokens: int = 4096, temperature: float = 0.1):
        
        try:
            from pydantic import BaseModel, ValidationError
            json_schema = response_model.model_json_schema()
            schema_instruction = f"\n\nYou MUST respond with valid JSON matching this exact schema:\n{json.dumps(json_schema, indent=2)}"
            full_system_prompt = system_prompt + schema_instruction
            messages = [{"role": "system", "content": full_system_prompt}]
            if chat_history:
                for msg in chat_history:
                    if isinstance(msg, dict) and 'role' in msg and 'content' in msg:
                        role_str = str(msg["role"]).lower() if not isinstance(msg["role"], str) else msg["role"]
                        content_str = str(msg["content"]) if not isinstance(msg["content"], str) else msg["content"]
                        messages.append({"role": role_str, "content": content_str})
            messages.append({"role": "user", "content": prompt})
            
            response_format = {"type": "json_object"}
            
            content_str = await self._execute_request(messages, max_tokens, temperature, response_format)
            
            if not content_str:
                logger.error("query_structured: No response from LLM")
                return None
            
            try:
                result = response_model.model_validate_json(content_str)
                return result
            except (ValidationError, json.JSONDecodeError) as e:
                logger.warning(f"Direct parsing failed: {e}, attempting cleanup")
                
                if isinstance(e, ValidationError):
                    for error in e.errors():
                        field = '.'.join(str(loc) for loc in error['loc'])
                        logger.warning(f" Validation error in '{field}': {error['msg']}")
                        if error['type'] == 'string_too_long':
                            logger.warning(f" Value length: {len(error['input'])} (max allowed: {error.get('ctx', {}).get('max_length', 'unknown')})")
                            logger.warning(f" Truncated value: {error['input'][:100]}...")
                
                cleaned_content = re.sub(r'^```(?:json)?\s*', '', content_str.strip(), flags=re.MULTILINE)
                cleaned_content = re.sub(r'\s*```$', '', cleaned_content.strip(), flags=re.MULTILINE)
                
                if cleaned_content != content_str:
                    logger.info("Stripped markdown code block wrappers from LLM response")
                    try:
                        result = response_model.model_validate_json(cleaned_content)
                        return result
                    except (ValidationError, json.JSONDecodeError):
                        pass  

                def attempt_json_completion(incomplete_json: str) -> str:
                   
                    completed = incomplete_json.strip()
                    
                    open_braces = completed.count('{') - completed.count('}')
                    open_brackets = completed.count('[') - completed.count(']')
                    
                    quote_count = 0
                    i = 0
                    while i < len(completed):
                        if completed[i] == '"' and (i == 0 or completed[i-1] != '\\'):
                            quote_count += 1
                        i += 1
                    
                    if quote_count % 2 == 1:
                        completed += '"'
                        logger.warning(" JSON AUTO-COMPLETION: Closed incomplete string")
                    
                    completed = re.sub(r',\s*$', '', completed)
                    
                    # Close arrays
                    for _ in range(open_brackets):
                        completed += ']'
                        logger.warning(f" JSON AUTO-COMPLETION: Closed {open_brackets} unclosed array(s)")
                    
                    # Close objects
                    for _ in range(open_braces):
                        completed += '}'
                        logger.warning(f" JSON AUTO-COMPLETION: Closed {open_braces} unclosed object(s)")
                    
                    return completed
                
                try:
                    completed_json = attempt_json_completion(cleaned_content)
                    if completed_json != cleaned_content:
                        logger.info("Attempting to parse auto-completed JSON")
                        result = response_model.model_validate_json(completed_json)
                        logger.info(" Successfully parsed auto-completed JSON")
                        return result
                except (ValidationError, json.JSONDecodeError) as e:
                    logger.debug(f"Auto-completion failed: {e}")
                    pass  
                
                clean_match = re.search(r'\{.*\}', cleaned_content, re.DOTALL)
                if clean_match:
                    try:
                        result = response_model.model_validate_json(clean_match.group(0))
                        return result
                    except ValidationError as ve:
                        logger.error(f"Pydantic validation failed after cleanup: {ve}")
                        for i, error in enumerate(ve.errors()[:3]):
                            field = '.'.join(str(loc) for loc in error['loc'])
                            logger.error(f" Error {i+1} in '{field}': {error['msg']}")
                        return None
                
                logger.error(f"Could not extract valid JSON from response: {content_str[:200]}")
                return None
                
        except ImportError:
            logger.error("Pydantic not available for query_structured")
            return None
        except Exception as e:
            logger.error(f"Unexpected error in query_structured: {e}")
            return None

    
