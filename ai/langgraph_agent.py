from typing import TypedDict, Annotated, List, Dict, Any, Optional, Literal
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langchain_core.messages import BaseMessage, AIMessage, HumanMessage, ToolMessage, SystemMessage
from langchain_mistralai import ChatMistralAI
from langchain_core.tools import tool
from dotenv import load_dotenv
import logging
import json
import os
import asyncio
import aiosqlite
from datetime import datetime
import random
load_dotenv()

logger = logging.getLogger(__name__)


if not hasattr(aiosqlite.Connection, 'is_alive'):
    def _is_alive(self):
        """Check if the connection is alive"""
        return self._conn is not None
    aiosqlite.Connection.is_alive = _is_alive
    logger.info(" Patched aiosqlite.Connection with is_alive() method")



class AgentState(TypedDict):

    messages: Annotated[List[BaseMessage], add_messages]
    user_id: str
    thread_id: str
    profile_data: Optional[Dict[str, Any]]
    constraints: Optional[Dict[str, Any]]
    calorie_target: Optional[int]
    meal_plan_context: Optional[Dict[str, Any]]
    validation_errors: List[str]
    retry_count: int



class MealPlanValidator:
    
    @staticmethod
    def validate_meal_plan(
        plan: Dict[str, Any],
        allergies: Optional[List[str]] = None,
        restrictions: Optional[List[str]] = None,
        target_calories: Optional[int] = None,
        medical_conditions: Optional[List[str]] = None
    ) -> List[str]:

        errors = []
        
        if allergies:
            plan_text = json.dumps(plan).lower()
            for allergen in allergies:
                if allergen.lower() in plan_text:
                    errors.append(f" ALLERGEN VIOLATION: Plan contains '{allergen}' which is listed as an allergy")
        
        if restrictions:
            plan_text = json.dumps(plan).lower()
            restricted_keywords = {
                'vegetarian': ['chicken', 'fish', 'beef', 'pork', 'lamb', 'turkey', 'seafood', 'meat'],
                'vegan': ['chicken', 'fish', 'beef', 'pork', 'lamb', 'turkey', 'seafood', 'meat', 'egg', 'dairy', 'milk', 'cheese', 'yogurt', 'butter', 'ghee', 'paneer', 'chenna', 'chhena', 'malai', 'mawa', 'khoa', 'khoya', 'ice cream', 'whey', 'casein'],
                'gluten-free': ['wheat', 'roti', 'chapati', 'bread', 'pasta', 'noodles', 'barley', 'rye'],
                'dairy-free': ['milk', 'cheese', 'yogurt', 'butter', 'cream', 'ghee', 'casein', 'whey', 'ice cream', 'paneer', 'chenna', 'chhena', 'malai', 'mawa', 'khoa', 'khoya', 'buttermilk', 'chaas', 'lassi', 'curd', 'dahi']
            }
            
            for restriction in restrictions:
                restriction_lower = restriction.lower()
                if restriction_lower in restricted_keywords:
                    for keyword in restricted_keywords[restriction_lower]:
                        if keyword in plan_text:
                            errors.append(f" DIETARY RESTRICTION VIOLATION: Plan contains '{keyword}' which violates {restriction} diet")
                            break
        
        if target_calories and target_calories > 0:
            total_calories = plan.get('total_day_macros', {}).get('Calories', 0)
            if total_calories == 0:
                total_calories = sum(
                    meal.get('total_macros', {}).get('Calories', 0)
                    for meal in plan.get('meals', [])
                )
            
            if total_calories > 0:
                calorie_diff_pct = abs(total_calories - target_calories) / target_calories * 100
                if calorie_diff_pct > 15:
                    errors.append(
                        f" CALORIE MISMATCH: Total calories {total_calories:.0f} kcal differs by "
                        f"{calorie_diff_pct:.1f}% from target {target_calories} kcal (allowed: ±15%)"
                    )
            else:
                errors.append(" ZERO CALORIES: Total calories is 0 - indicates database lookup failure")
        
        for meal in plan.get('meals', []):
            macros = meal.get('total_macros', {})
            protein = macros.get('Protein', 0)
            carbs = macros.get('Carbohydrates', 0)
            fat = macros.get('Fat', 0)
            calories = macros.get('Calories', 0)
            
            if calories > 0:
                calculated_calories = (protein * 4) + (carbs * 4) + (fat * 9)
                if calculated_calories > 0:
                    diff_pct = abs(calculated_calories - calories) / calculated_calories * 100
                    if diff_pct > 25:  
                        errors.append(
                            f" MACRO INCONSISTENCY in {meal.get('meal_name', 'Unknown')}: "
                            f"Macros (P:{protein}g C:{carbs}g F:{fat}g) suggest {calculated_calories:.0f} kcal "
                            f"but plan shows {calories:.0f} kcal (diff: {diff_pct:.1f}%)"
                        )
        
        if medical_conditions:
            critical_conditions = {
                'diabetes': ['high sugar', 'white rice', 'refined flour', 'fruit juice', 'honey'],
                'hypertension': ['high sodium', 'pickle', 'papad', 'chips', 'processed'],
                'kidney disease': ['high protein', 'high sodium', 'high potassium']
            }
            
            plan_text = json.dumps(plan).lower()
            for condition in medical_conditions:
                condition_lower = condition.lower()
                if condition_lower in critical_conditions:
                    for risky_item in critical_conditions[condition_lower]:
                        if risky_item in plan_text:
                            errors.append(
                                f" MEDICAL ALERT for {condition}: Plan may contain '{risky_item}' "
                                f"which requires careful monitoring"
                            )
        
        return errors
    
    @staticmethod
    def validate_portion_reasonableness(items: List[Dict[str, Any]]) -> List[str]:
        errors = []
        
        for item in items:
            quantity = item.get('quantity', 0)
            unit = item.get('unit', '').lower()
            food_name = item.get('food_name', '')
            
            if unit in ['cup', 'cups'] and quantity > 5:
                errors.append(f" PORTION WARNING: {quantity} {unit} of {food_name} seems excessive")
            elif unit in ['oz', 'ounces'] and quantity > 16:
                errors.append(f" PORTION WARNING: {quantity} {unit} of {food_name} seems excessive")
            elif unit in ['tbsp', 'tablespoon'] and quantity > 10:
                errors.append(f" PORTION WARNING: {quantity} {unit} of {food_name} seems excessive")
        
        return errors



class ToolExecutionWrapper:
    
    TRANSIENT_ERROR_TYPES = (TimeoutError, ConnectionError, ConnectionRefusedError)
    MAX_RETRIES = 3
    
    @staticmethod
    async def wrap_tool_call(request: Dict[str, Any], call_tool) -> ToolMessage:

        tool_name = request.get('name', '')
        tool_call_id = request.get('id', '')
        
        for attempt in range(1, ToolExecutionWrapper.MAX_RETRIES + 1):
            try:
                result = await call_tool(request) if asyncio.iscoroutinefunction(call_tool) else call_tool(request)
                
                if tool_name in ['MealPlanGeneratorTool', 'WeeklyMealPlanGeneratorTool']:
                    validation_errors = ToolExecutionWrapper._validate_meal_plan_result(result, request)
                    
                    if validation_errors:
                        error_msg = "Meal plan validation failed:\n" + "\n".join(validation_errors)
                        error_msg += "\n\nPlease regenerate the meal plan addressing ALL the above issues."
                        
                        logger.warning(f"Meal plan validation failed on attempt {attempt}: {validation_errors}")
                        
                        if attempt < ToolExecutionWrapper.MAX_RETRIES:
                            continue  
                        else:
                            return ToolMessage(
                                content=error_msg,
                                tool_call_id=tool_call_id,
                                additional_kwargs={'validation_failed': True}
                            )
                
                if isinstance(result, ToolMessage):
                    return result
                else:
                    return ToolMessage(
                        content=json.dumps(result) if isinstance(result, dict) else str(result),
                        tool_call_id=tool_call_id
                    )
                
            except ToolExecutionWrapper.TRANSIENT_ERROR_TYPES as e:
                if attempt == ToolExecutionWrapper.MAX_RETRIES:
                    return ToolMessage(
                        content=f"Tool '{tool_name}' failed after {ToolExecutionWrapper.MAX_RETRIES} attempts: {e}",
                        tool_call_id=tool_call_id,
                        additional_kwargs={'error_type': 'transient'}
                    )
                wait_time = 0.2 * (2 ** (attempt - 1))
                logger.warning(f"Transient error in {tool_name} (attempt {attempt}): {e}. Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
                
            except Exception as e:
                logger.error(f"Non-retryable error in {tool_name}: {e}", exc_info=True)
                return ToolMessage(
                    content=f"Tool '{tool_name}' encountered an error: {str(e)}",
                    tool_call_id=tool_call_id,
                    additional_kwargs={'error_type': 'permanent'}
                )
        
        return ToolMessage(
            content=f"Tool '{tool_name}' failed unexpectedly",
            tool_call_id=tool_call_id
        )
    
    @staticmethod
    def _validate_meal_plan_result(result: Any, request: Dict[str, Any]) -> List[str]:
        try:
            args = request.get('args', {})
            constraints = args.get('constraints', {})
            target_calories = args.get('calorie_target', None)
            
            if isinstance(result, ToolMessage):
                content = result.content
            elif hasattr(result, 'data'):
                content = result.data
            else:
                content = result
            
            if isinstance(content, str):
                try:
                    plan_data = json.loads(content)
                except json.JSONDecodeError:
                    plan_data = {'meals': [], 'total_day_macros': {}}
            else:
                plan_data = content
            
            validator = MealPlanValidator()
            errors = validator.validate_meal_plan(
                plan=plan_data,
                allergies=constraints.get('allergies', []),
                restrictions=constraints.get('restrictions', []),
                target_calories=target_calories,
                medical_conditions=constraints.get('medical_conditions', [])
            )
            
            return errors
            
        except Exception as e:
            logger.error(f"Error validating meal plan result: {e}")
            return [] 



class NutritionAgent:
    
    def __init__(self, model_name: str = "mistral-small-2503"):
        
        general_models_str = os.getenv("GENERAL_MODELS", "[]")
        
        try:
            general_models = json.loads(general_models_str)
            mistral_models = [m for m in general_models if "models.ai.azure.com" in m.get("endpoint", "")]
            
            if mistral_models:
                selected_model = random.choice(mistral_models)
                mistral_api_key = selected_model["api_key"]
                mistral_endpoint = selected_model["endpoint"]
                
                if mistral_endpoint.endswith("/chat/completions"):
                    mistral_endpoint = mistral_endpoint.replace("/chat/completions", "")
                
                logger.info(f" Using Mistral endpoint from GENERAL_MODELS: {mistral_endpoint}")
            else:
                mistral_api_key = os.getenv("MISTRAL_API_KEY")
                mistral_endpoint = os.getenv("MISTRAL_API_ENDPOINT")
                
                if mistral_endpoint and mistral_endpoint.endswith("/chat/completions"):
                    mistral_endpoint = mistral_endpoint.replace("/chat/completions", "")
                
                logger.info(" No Mistral endpoints in GENERAL_MODELS, using MISTRAL_API_ENDPOINT")
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing GENERAL_MODELS: {e}")
            mistral_api_key = os.getenv("MISTRAL_API_KEY")
            mistral_endpoint = os.getenv("MISTRAL_API_ENDPOINT")
            
            if mistral_endpoint and mistral_endpoint.endswith("/chat/completions"):
                mistral_endpoint = mistral_endpoint.replace("/chat/completions", "")
        
        if not mistral_api_key or not mistral_endpoint:
            logger.warning("MISTRAL_API_KEY or MISTRAL_API_ENDPOINT not set")
        
        logger.info(f" Initializing ChatMistralAI with endpoint: {mistral_endpoint}")
        logger.info(f" Model: {model_name}")
        
        try:
            self.model = ChatMistralAI(
                model=model_name,
                endpoint=mistral_endpoint,
                mistral_api_key=mistral_api_key,
                temperature=0.7,
                max_tokens=4000
            )
            logger.info(" ChatMistralAI initialized successfully")
        except Exception as e:
            logger.error(f" Failed to initialize ChatMistralAI: {e}")
            raise
        
        db_path = os.path.join(os.path.dirname(__file__), "..", "langgraph_checkpoints.db")
        self._checkpointer_cm = AsyncSqliteSaver.from_conn_string(db_path)
        self.checkpointer = None
        self._checkpointer_initialized = False
        
        self.tools_registry = {}
        self.graph = None
    
    async def _ensure_checkpointer(self):
        if not self._checkpointer_initialized:
            self.checkpointer = await self._checkpointer_cm.__aenter__()
            self._checkpointer_initialized = True
            logger.info(" AsyncSqliteSaver initialized successfully")
    
    def register_tool(self, tool_name: str, tool_instance: Any):
        self.tools_registry[tool_name] = tool_instance
    
    def _create_langchain_tools(self) -> List:
        langchain_tools = []
        
        for tool_name, tool_instance in self.tools_registry.items():
            @tool
            async def wrapped_tool(*args, **kwargs):
                return await tool_instance.execute(**kwargs)
            
            wrapped_tool.name = tool_name
            wrapped_tool.description = tool_instance.description
            langchain_tools.append(wrapped_tool)
        
        return langchain_tools
    
    def _agent_node(self, state: AgentState) -> Dict[str, Any]:

        messages = state["messages"]
        
        system_msg = self._build_system_message(state)
        full_messages = [system_msg] + messages
        
        langchain_tools = self._create_langchain_tools()
        model_with_tools = self.model.bind_tools(langchain_tools)
        
        try:
            response = model_with_tools.invoke(full_messages)
            return {"messages": [response]}
        except Exception as e:
            error_msg = str(e)
            logger.error(f" LLM invocation failed: {error_msg}")
            
            if "ValidationError" in error_msg or "ChatMessageWithImage" in error_msg:
                logger.error(
                    " CRITICAL: Mistral API Pydantic validation error detected. "
                    "This indicates a message format incompatibility. "
                    "Ensure langchain-mistralai is at version 0.1.2 or higher."
                )
            
            error_response = AIMessage(
                content=f"I encountered an error while processing your request: {error_msg[:200]}. "
                        "Please try again or contact support if the issue persists."
            )
            return {"messages": [error_response]}
    
    def _build_system_message(self, state: AgentState) -> SystemMessage:
        profile = state.get("profile_data", {})
        constraints = state.get("constraints", {})
        calorie_target = state.get("calorie_target")
        
        context_parts = ["You are Friska, an expert AI nutrition coach."]
        
        if calorie_target:
            context_parts.append(f"Target daily calories: {calorie_target} kcal")
        
        if constraints:
            if constraints.get('allergies'):
                context_parts.append(f"Allergies: {', '.join(constraints['allergies'])}")
            if constraints.get('restrictions'):
                context_parts.append(f"Dietary restrictions: {', '.join(constraints['restrictions'])}")
            if constraints.get('medical_conditions'):
                context_parts.append(f"Medical conditions: {', '.join(constraints['medical_conditions'])}")
        
        context_parts.append(
            "\nIMPORTANT: When generating meal plans, ensure:\n"
            "1. All allergens are strictly avoided\n"
            "2. Dietary restrictions are respected\n"
            "3. Calorie targets are met within ±10%\n"
            "4. Macros are balanced appropriately for any medical conditions\n"
            "5. Portion sizes are reasonable and achievable"
        )
        
        return SystemMessage(content="\n".join(context_parts))
    
    def _should_continue(self, state: AgentState) -> Literal["tools", "__end__"]:
        messages = state["messages"]
        last_message = messages[-1]
        
        if isinstance(last_message, AIMessage) and last_message.tool_calls:
            return "tools"
        
        return "__end__"
    
    def build_graph(self) -> StateGraph:
        """Build the LangGraph StateGraph"""
        workflow = StateGraph(AgentState)
        
        langchain_tools = self._create_langchain_tools()
        tool_node = ToolNode(
            tools=langchain_tools,
            handle_tool_errors=True  
        )
        
        workflow.add_node("agent", self._agent_node)
        workflow.add_node("tools", tool_node)
        
        workflow.add_edge(START, "agent")
        workflow.add_conditional_edges(
            "agent",
            self._should_continue,
            {
                "tools": "tools",
                "__end__": END
            }
        )
        workflow.add_edge("tools", "agent") 
        
        self.graph = workflow.compile(checkpointer=self.checkpointer)
        
        return self.graph
    
    async def stream_response(
        self,
        user_message: str,
        thread_id: str,
        user_id: str,
        profile_data: Optional[Dict] = None,
        constraints: Optional[Dict] = None,
        calorie_target: Optional[int] = None
    ):

        await self._ensure_checkpointer()
        
        if not self.graph:
            self.build_graph()
        
        config = {
            "configurable": {
                "thread_id": thread_id,
                "user_id": user_id
            }
        }
        
        inputs = {
            "messages": [HumanMessage(content=user_message)],
            "user_id": user_id,
            "thread_id": thread_id,
            "profile_data": profile_data,
            "constraints": constraints,
            "calorie_target": calorie_target,
            "validation_errors": [],
            "retry_count": 0
        }
        
        async for chunk in self.graph.astream(inputs, config, stream_mode="messages"):
            yield chunk
    
    async def invoke_sync(
        self,
        user_message: str,
        thread_id: str,
        user_id: str,
        profile_data: Optional[Dict] = None,
        constraints: Optional[Dict] = None,
        calorie_target: Optional[int] = None
    ) -> Dict[str, Any]:

        await self._ensure_checkpointer()
        
        if not self.graph:
            self.build_graph()
        
        config = {
            "configurable": {
                "thread_id": thread_id,
                "user_id": user_id
            }
        }
        
        inputs = {
            "messages": [HumanMessage(content=user_message)],
            "user_id": user_id,
            "thread_id": thread_id,
            "profile_data": profile_data,
            "constraints": constraints,
            "calorie_target": calorie_target,
            "validation_errors": [],
            "retry_count": 0
        }
        
        result = await self.graph.ainvoke(inputs, config)
        return result
    
    def get_thread_state(self, thread_id: str, user_id: str) -> Optional[AgentState]:
        """Retrieve thread state from checkpointer"""
        if not self.graph:
            return None
        
        config = {
            "configurable": {
                "thread_id": thread_id,
                "user_id": user_id
            }
        }
        
        try:
            state_snapshot = self.graph.get_state(config)
            return state_snapshot.values if state_snapshot else None
        except Exception as e:
            logger.error(f"Error retrieving thread state: {e}")
            return None
    
    def get_thread_history(self, thread_id: str, user_id: str) -> List[BaseMessage]:
        """Get message history for a thread"""
        state = self.get_thread_state(thread_id, user_id)
        if state:
            return state.get("messages", [])
        return []



LangGraphAgent = NutritionAgent

__all__ = [
    'NutritionAgent',
    'LangGraphAgent',  
    'AgentState',
    'MealPlanValidator',
    'ToolExecutionWrapper'
]
