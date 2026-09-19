from typing import List, Dict, Any
from pydantic import BaseModel
import json
import re
try:
    import openai
except ImportError:
    openai = None  

try:
    import anthropic
except ImportError:
    anthropic = None  

from .nutrition_models import (
    MealPlanResponse, 
    MacroTargets, 
    NUTRITION_DB,
    CalculatedNutrition
)
from .nutrition_pydantic_integration import calculate_nutrition_for_portion



example_data = {
    "meal_type": "Breakfast",
    "items": [
        {"name": "Oatmeal", "quantity": 1.0, "unit": "cup"}
    ]
}

async def generate_meal_pydantic_clean(
    meal_name: str,
    llm_service,
    meal_calorie_target: int,
    macro_targets: MacroTargets,
    allowed_foods: List[str]
):

    
    prompt = f"""
    Generate {meal_name} with {meal_calorie_target} calories.
    Macros: P:{macro_targets.protein_pct}% C:{macro_targets.carbs_pct}% F:{macro_targets.fat_pct}%
    
    Allowed foods: {allowed_foods}
    
    Return JSON:
    {{
        "meal_type": "{meal_name}",
        "items": [
            {{"name": "...", "quantity": 1.0, "unit": "cup"}}
        ]
    }}
    """
    
    response = await llm_service.query(prompt, max_tokens=600)
    
    meal_plan = MealPlanResponse.model_validate_json(response)

    total_calories = 0
    total_protein = 0
    total_carbs = 0
    total_fat = 0
    
    for food_selection in meal_plan.items:
        food_item = NUTRITION_DB.lookup(food_selection.name)
        if food_item:
            nutrition = calculate_nutrition_for_portion(
                food_item, food_selection.quantity, food_selection.unit
            )
            total_calories += nutrition.calories
            total_protein += nutrition.protein_g
            total_carbs += nutrition.carbs_g
            total_fat += nutrition.fat_g
    
    return {
        'meal_name': meal_name,
        'foods': meal_plan.items,
        'calories': total_calories,
        'protein_g': total_protein,
        'carbs_g': total_carbs,
        'fat_g': total_fat
    }


async def query_openai_structured(prompt: str, response_model: type[BaseModel]):

    response = await openai.ChatCompletion.acreate(
        model="gpt-4o", 
        messages=[
            {"role": "system", "content": "You are a nutrition assistant. Always respond with valid JSON."},
            {"role": "user", "content": prompt}
        ],
        response_format={"type": "json_object"},  
        temperature=0.3
    )
    
    json_str = response.choices[0].message.content
    
    result = response_model.model_validate_json(json_str)
    return result


async def query_openai_function_calling(prompt: str):

    schema = MealPlanResponse.model_json_schema()
    
    response = await openai.ChatCompletion.acreate(
        model="gpt-4o",
        messages=[
            {"role": "user", "content": prompt}
        ],
        functions=[{
            "name": "generate_meal_plan",
            "description": "Generate a meal plan with specific foods and portions",
            "parameters": schema
        }],
        function_call={"name": "generate_meal_plan"}
    )
    
    function_args = response.choices[0].message.function_call.arguments
    
    meal_plan = MealPlanResponse.model_validate_json(function_args)
    return meal_plan




async def query_claude_structured(prompt: str, response_model: type[BaseModel]):

    client = anthropic.AsyncAnthropic(api_key="...")
    
    schema = response_model.model_json_schema()
    
    system_prompt = f"""
    You are a nutrition assistant.
    You MUST respond with ONLY valid JSON matching this exact schema:
    
    {json.dumps(schema, indent=2)}
    
    Do not include any text outside the JSON object.
    """
    
    response = await client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=1024,
        system=system_prompt,
        messages=[
            {"role": "user", "content": prompt}
        ]
    )
    
    json_str = response.content[0].text
    
    result = response_model.model_validate_json(json_str)
    return result



async def query_generic_llm_structured(llm_service, prompt: str, response_model: type[BaseModel]):

    
    schema = response_model.model_json_schema()
    
    enhanced_prompt = f"""
    {prompt}
    
    CRITICAL: Respond with ONLY valid JSON matching this schema:
    
    {json.dumps(schema, indent=2)}
    
    Example:
    {json.dumps(response_model.model_validate(example_data).model_dump(), indent=2)}
    
    Do NOT include any text outside the JSON.
    """
    
    response = await llm_service.query(enhanced_prompt, max_tokens=600)
    
    try:
        result = response_model.model_validate_json(response)
        return result
    except Exception as e:
        
        match = re.search(r'\{.*\}', response, re.DOTALL)
        if match:
            json_str = match.group(0)
            result = response_model.model_validate_json(json_str)
            return result
        else:
            raise ValueError(f"Could not parse JSON from LLM response: {e}")


class OpenAI_LLMService:
    
    async def query_structured(
        self,
        prompt: str,
        response_model: type[BaseModel],
        system_prompt: str = "",
        **kwargs
    ):

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        
        response = await openai.ChatCompletion.acreate(
            model=self.model,
            messages=messages,
            response_format={"type": "json_object"},
            temperature=kwargs.get('temperature', 0.3),
            max_tokens=kwargs.get('max_tokens', 1000)
        )
        
        json_str = response.choices[0].message.content
        
        result = response_model.model_validate_json(json_str)
        return result


async def _generate_single_meal_part(
    self,
    meal_name: str,
    meal_llm_service: Any,  
    base_system_prompt: str,
    meal_calorie_target: int,
    allowed_foods: List[str],
    macro_targets: MacroTargets,
    **kwargs
) -> str:

    
    prompt = f"""
    Generate {meal_name} with ~{meal_calorie_target} calories.
    
    Macro Targets:
    - Protein: {macro_targets.protein_pct}%
    - Carbs: {macro_targets.carbs_pct}%
    - Fat: {macro_targets.fat_pct}%
    
    Allowed Foods: {allowed_foods}
    
    Select 2-4 foods with realistic portions.
    """
    
    meal_plan = await meal_llm_service.query_structured(
        prompt=prompt,
        response_model=MealPlanResponse,
        system_prompt=base_system_prompt
    )
    
    total_nutrition = CalculatedNutrition(...)  
    
    for food_selection in meal_plan.items:
        food_item = NUTRITION_DB.lookup(food_selection.name)
        if food_item:
            nutrition = calculate_nutrition_for_portion(
                food_item, food_selection.quantity, food_selection.unit
            )
    
    output = f"**{meal_name}**\\n"
    for food in meal_plan.items:
        output += f"- {food.name} ({food.quantity} {food.unit.value})\\n"
    
    return output

