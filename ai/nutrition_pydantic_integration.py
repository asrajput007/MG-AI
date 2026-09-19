import asyncio
import json
from typing import List, Dict, Optional
import logging

from .nutrition_models import (
    MealPlanResponse, FoodSelection, MacroTargets, CalculatedNutrition,
    MealAnalysis, NUTRITION_DB, calculate_nutrition_for_portion,
    MealType, DailyFoodPartitions
)
from .tool_core import LLMService

logger = logging.getLogger(__name__)



async def generate_single_meal_pydantic(
    meal_name: str,
    meal_llm_service: LLMService,
    base_system_prompt: str,
    meal_calorie_target: int,
    macro_targets: MacroTargets,
    allowed_foods: List[str],
) -> MealAnalysis:

    meal_prompt = f"""
    Generate a meal plan for **{meal_name}** with {meal_calorie_target} calories.
    
    **MACRO TARGETS:**
    - Protein: {macro_targets.protein_pct:.0f}% ({meal_calorie_target * macro_targets.protein_pct / 100 / 4:.0f}g)
    - Carbs: {macro_targets.carbs_pct:.0f}% ({meal_calorie_target * macro_targets.carbs_pct / 100 / 4:.0f}g)
    - Fat: {macro_targets.fat_pct:.0f}% ({meal_calorie_target * macro_targets.fat_pct / 100 / 9:.0f}g)
    
    **ALLOWED FOODS:**
    {json.dumps(allowed_foods)}
    
    **IMPORTANT:**
    - Select 2-4 foods from the allowed list
    - Use realistic portions that naturally sum to ~{meal_calorie_target} calories
    - Follow unit rules: nuts=tbsp, seeds=tsp, grains=cup, proteins=oz
    
    **OUTPUT FORMAT (JSON ONLY):**
    {{
        "meal_type": "{meal_name}",
        "items": [
            {{ "name": "Grilled chicken breast", "quantity": 5.0, "unit": "oz" }},
            {{ "name": "Steamed broccoli", "quantity": 1.0, "unit": "cup" }}
        ],
        "reasoning": "Brief explanation of choices"
    }}
    """
    

    max_retries = 2
    for attempt in range(max_retries):
        try:
            response = await asyncio.wait_for(
                meal_llm_service.query(
                    prompt=meal_prompt,
                    system_prompt=base_system_prompt,
                    max_tokens=600,
                    temperature=0.3
                ),
                timeout=20.0
            )
            

            meal_plan = MealPlanResponse.model_validate_json(response)
            
            logger.info(f" LLM generated valid meal plan for {meal_name}")
            logger.info(f" Foods: {[f.name for f in meal_plan.items]}")
            logger.info(f" Reasoning: {meal_plan.reasoning}")
            
            total_nutrition = CalculatedNutrition(
                calories=0, protein_g=0, carbs_g=0, fat_g=0,
                fiber_g=0, net_carbs_g=0, sodium_mg=0, sugar_g=0, cholesterol_mg=0
            )
            
            for food_selection in meal_plan.items:
                food_item = NUTRITION_DB.lookup(food_selection.name)
                
                if food_item:
                    nutrition = calculate_nutrition_for_portion(
                        food_item,
                        food_selection.quantity,
                        food_selection.unit
                    )
                    
                    total_nutrition.calories += nutrition.calories
                    total_nutrition.protein_g += nutrition.protein_g
                    total_nutrition.carbs_g += nutrition.carbs_g
                    total_nutrition.fat_g += nutrition.fat_g
                    total_nutrition.fiber_g += nutrition.fiber_g
                    total_nutrition.net_carbs_g += nutrition.net_carbs_g
                    total_nutrition.sodium_mg += nutrition.sodium_mg
                    total_nutrition.sugar_g += nutrition.sugar_g
                    total_nutrition.cholesterol_mg += nutrition.cholesterol_mg
                    
                    logger.info(f" {food_selection.name} ({food_selection.quantity} {food_selection.unit.value}): {nutrition.calories:.0f} kcal")
                else:
                    logger.warning(f" Food not in database: {food_selection.name} - using estimate")

            
            meal_analysis = MealAnalysis(
                meal_type=meal_plan.meal_type,
                foods=meal_plan.items,
                nutrition=total_nutrition,
                target_calories=meal_calorie_target,
                target_macros=macro_targets
            )
            
            if meal_analysis.is_valid:
                logger.info(f" {meal_name} meets targets:")
                logger.info(f" Calories: {total_nutrition.calories:.0f}/{meal_calorie_target} ({meal_analysis.calorie_accuracy:.0f}%)")
                logger.info(f" Macros: P:{total_nutrition.protein_pct:.0f}% C:{total_nutrition.carbs_pct:.0f}% F:{total_nutrition.fat_pct:.0f}%")
                return meal_analysis
            else:
                logger.warning(f" {meal_name} attempt {attempt+1} failed validation")
                deviations = meal_analysis.macro_deviation
                logger.warning(f" Macro deviations: P:{deviations['protein']:.0f}% C:{deviations['carbs']:.0f}% F:{deviations['fat']:.0f}%")
                
                if attempt == max_retries - 1:
                    return meal_analysis
                continue
        
        except Exception as e:
            logger.error(f" Error generating {meal_name} (attempt {attempt+1}): {e}")
            if attempt == max_retries - 1:
                raise
            continue
    
    raise RuntimeError(f"Failed to generate valid meal plan for {meal_name} after {max_retries} attempts")



async def generate_daily_food_partitions_pydantic(
    llm_service: LLMService,
    constraints: Dict,
    avoid_foods: List[str],
    macro_targets: MacroTargets
) -> DailyFoodPartitions:

    
    prompt = f"""
    Create disjoint food sets for 5 meals throughout the day.
    
    **RULES:**
    - Each meal: 3-5 unique food items
    - NO food repeats across different meals
    - Respect macro targets: {macro_targets.protein_pct:.0f}% protein, {macro_targets.carbs_pct:.0f}% carbs, {macro_targets.fat_pct:.0f}% fat
    - AVOID: {', '.join(avoid_foods)}
    
    **OUTPUT FORMAT (JSON ONLY):**
    {{
        "breakfast": ["Cooked oats", "Greek yogurt", "Fresh berries", "Raw almonds"],
        "morning_snack": ["Fresh apple", "Raw walnuts"],
        "lunch": ["Grilled chicken breast", "Cooked quinoa", "Steamed broccoli", "Chia seeds"],
        "evening_snack": ["Paneer", "Cucumber slices"],
        "dinner": ["Baked salmon", "Cooked brown rice", "Steamed spinach", "Avocado"]
    }}
    """
    
    response = await llm_service.query(
        prompt=prompt,
        max_tokens=800,
        temperature=0.4
    )
    
    try:
        partitions = DailyFoodPartitions.model_validate_json(response)
        logger.info(" Daily food partitions generated with NO duplicates")
        return partitions
    except ValueError as e:
        logger.error(f" LLM generated duplicate foods: {e}")
        raise



def scale_meal_to_target(
    meal_analysis: MealAnalysis,
    max_scale: float = 1.3,
    min_scale: float = 0.8
) -> MealAnalysis:

    current_cals = meal_analysis.nutrition.calories
    target_cals = meal_analysis.target_calories
    
    if current_cals == 0:
        logger.error("Cannot scale meal with 0 calories")
        return meal_analysis
    
    scale_ratio = target_cals / current_cals
    
    if scale_ratio > max_scale or scale_ratio < min_scale:
        logger.warning(f" Scale ratio {scale_ratio:.2f} outside limits [{min_scale}, {max_scale}]")
        logger.warning(f" Food selections were too far off - should retry LLM generation")
        return meal_analysis
    
    scaled_nutrition = CalculatedNutrition(
        calories=meal_analysis.nutrition.calories * scale_ratio,
        protein_g=meal_analysis.nutrition.protein_g * scale_ratio,
        carbs_g=meal_analysis.nutrition.carbs_g * scale_ratio,
        fat_g=meal_analysis.nutrition.fat_g * scale_ratio,
        fiber_g=meal_analysis.nutrition.fiber_g * scale_ratio,
        net_carbs_g=max(0, (meal_analysis.nutrition.carbs_g - meal_analysis.nutrition.fiber_g) * scale_ratio),
        sodium_mg=meal_analysis.nutrition.sodium_mg * scale_ratio,
        sugar_g=meal_analysis.nutrition.sugar_g * scale_ratio,
        cholesterol_mg=meal_analysis.nutrition.cholesterol_mg * scale_ratio
    )
    
    scaled_foods = []
    for food in meal_analysis.foods:
        scaled_food = FoodSelection(
            name=food.name,
            quantity=food.quantity * scale_ratio,
            unit=food.unit
        )
        scaled_foods.append(scaled_food)
    
    scaled_meal = MealAnalysis(
        meal_type=meal_analysis.meal_type,
        foods=scaled_foods,
        nutrition=scaled_nutrition,
        target_calories=meal_analysis.target_calories,
        target_macros=meal_analysis.target_macros
    )
    
    logger.info(f" Scaled meal by {scale_ratio:.2f}x: {current_cals:.0f} → {scaled_nutrition.calories:.0f} kcal")
    
    return scaled_meal



async def generate_full_day_meal_plan_pydantic(
    llm_services: Dict[str, LLMService],
    base_system_prompt: str,
    calorie_distribution: Dict[str, int],
    macro_targets: MacroTargets,
    daily_food_partitions: DailyFoodPartitions
) -> Dict[str, MealAnalysis]:

    
    tasks = []
    
    if "Breakfast" in llm_services:
        tasks.append(
            generate_single_meal_pydantic(
                meal_name="Breakfast",
                meal_llm_service=llm_services["Breakfast"],
                base_system_prompt=base_system_prompt,
                meal_calorie_target=calorie_distribution.get("Breakfast", 500),
                macro_targets=macro_targets,
                allowed_foods=daily_food_partitions.breakfast
            )
        )
    
    if "Morning Snack" in llm_services:
        tasks.append(
            generate_single_meal_pydantic(
                meal_name="Morning Snack",
                meal_llm_service=llm_services["Morning Snack"],
                base_system_prompt=base_system_prompt,
                meal_calorie_target=calorie_distribution.get("Morning Snack", 150),
                macro_targets=macro_targets,
                allowed_foods=daily_food_partitions.morning_snack
            )
        )
    
    if "Lunch" in llm_services:
        tasks.append(
            generate_single_meal_pydantic(
                meal_name="Lunch",
                meal_llm_service=llm_services["Lunch"],
                base_system_prompt=base_system_prompt,
                meal_calorie_target=calorie_distribution.get("Lunch", 650),
                macro_targets=macro_targets,
                allowed_foods=daily_food_partitions.lunch
            )
        )
    
    if "Evening Snack" in llm_services:
        tasks.append(
            generate_single_meal_pydantic(
                meal_name="Evening Snack",
                meal_llm_service=llm_services["Evening Snack"],
                base_system_prompt=base_system_prompt,
                meal_calorie_target=calorie_distribution.get("Evening Snack", 150),
                macro_targets=macro_targets,
                allowed_foods=daily_food_partitions.evening_snack
            )
        )
    
    if "Dinner" in llm_services:
        tasks.append(
            generate_single_meal_pydantic(
                meal_name="Dinner",
                meal_llm_service=llm_services["Dinner"],
                base_system_prompt=base_system_prompt,
                meal_calorie_target=calorie_distribution.get("Dinner", 650),
                macro_targets=macro_targets,
                allowed_foods=daily_food_partitions.dinner
            )
        )
    
    meal_analyses = await asyncio.gather(*tasks)
    
    result = {}
    for meal_analysis in meal_analyses:
        result[meal_analysis.meal_type.value] = meal_analysis
    
    logger.info(f" Generated complete day meal plan with {len(result)} meals")
    
    return result



def add_indian_foods_to_database():

    from .nutrition_models import FoodItem, NutritionData, CookingMethod
    
    indian_foods = [
        FoodItem(
            name="Whole wheat roti",
            aliases=["roti", "chapati", "phulka"],
            category="grain",
            cooking_method=CookingMethod.COOKED,
            nutrition_per_100g=NutritionData(
                calories=297, protein=11, carbs=55, fat=5.9,
                fiber=10, sodium=430, sugar=2.7, cholesterol=0
            ),
            typical_portion_g={"1 piece": 40, "2 piece": 80},
            is_vegetarian=True, is_vegan=True
        ),
        FoodItem(
            name="Dal makhani",
            aliases=["dal", "lentil curry"],
            category="legume",
            cooking_method=CookingMethod.COOKED,
            nutrition_per_100g=NutritionData(
                calories=150, protein=8, carbs=18, fat=5,
                fiber=5, sodium=350, sugar=2, cholesterol=10
            ),
            typical_portion_g={"1 cup": 240, "0.5 cup": 120, "0.75 cup": 180},
            is_vegetarian=True, is_vegan=False
        ),
        FoodItem(
            name="Palak paneer",
            aliases=["spinach paneer", "saag paneer"],
            category="protein",
            cooking_method=CookingMethod.COOKED,
            nutrition_per_100g=NutritionData(
                calories=180, protein=12, carbs=8, fat=12,
                fiber=3, sodium=420, sugar=3, cholesterol=35
            ),
            typical_portion_g={"1 cup": 240, "0.5 cup": 120},
            is_vegetarian=True, is_vegan=False
        ),
        FoodItem(
            name="Matar paneer",
            aliases=["peas paneer"],
            category="protein",
            cooking_method=CookingMethod.COOKED,
            nutrition_per_100g=NutritionData(
                calories=165, protein=10, carbs=12, fat=9,
                fiber=4, sodium=380, sugar=5, cholesterol=30
            ),
            typical_portion_g={"1 cup": 240, "0.5 cup": 120},
            is_vegetarian=True, is_vegan=False
        ),
        FoodItem(
            name="Moong dal cheela",
            aliases=["moong dal pancake", "cheela"],
            category="protein",
            cooking_method=CookingMethod.COOKED,
            nutrition_per_100g=NutritionData(
                calories=145, protein=10, carbs=20, fat=3,
                fiber=6, sodium=250, sugar=1, cholesterol=0
            ),
            typical_portion_g={"1 piece": 80, "2 piece": 160},
            is_vegetarian=True, is_vegan=True
        )
    ]
    
    for food in indian_foods:
        NUTRITION_DB.add_food(food)
    
    logger.info(f" Added {len(indian_foods)} Indian foods to database")



async def demo_pydantic_meal_generation():

    add_indian_foods_to_database()
    
    diabetes_macros = MacroTargets.for_diabetes()
    print(f"Diabetes Macros: P:{diabetes_macros.protein_pct}% C:{diabetes_macros.carbs_pct}% F:{diabetes_macros.fat_pct}%")
    
    chicken_foods = NUTRITION_DB.fuzzy_search("chicken")
    print(f"\nFound {len(chicken_foods)} chicken options:")
    for food in chicken_foods:
        print(f"  - {food.name}: {food.nutrition_per_100g.calories} kcal/100g")
    
    safe_foods = NUTRITION_DB.get_suitable_for_diabetes()
    print(f"\nDiabetes-friendly foods: {len(safe_foods)} options")
    
    print("\n Pydantic integration ready!")


if __name__ == "__main__":
    asyncio.run(demo_pydantic_meal_generation())
