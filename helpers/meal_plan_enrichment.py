import re
import json
import logging
from typing import Dict, List, Tuple
from helpers.utils import parse_meal_plan_text_to_items
from helpers.food_database_cache import get_food_database_cache

logger = logging.getLogger(__name__)


def enrich_meal_plan_with_metadata(meal_plan_text: str) -> str:
    """
    Enrich meal plan text by adding food_id, ingredients, and recipe metadata to each food item line.
    
    Args:
        meal_plan_text: Plain meal plan text without metadata
        
    Returns:
        Enriched meal plan text with embedded metadata
        
    Example:
        Input:  "- Egg Scramble | ... | Calories: 159kcal"
        Output: "- Egg Scramble | ... | Calories: 159kcal | \"food_id\": \"FOOD_001\" | \"ingredients\": [...] | \"recipe\": [...] |"
    """
    if not meal_plan_text:
        return meal_plan_text
    
    logger.info("🔧 Starting meal plan enrichment...")
    
    try:
        # Parse meal plan to get food items
        items = parse_meal_plan_text_to_items(meal_plan_text)
        if not items:
            logger.warning("No items found to enrich")
            return meal_plan_text
        
        logger.info(f"✅ Parsed {len(items)} food items from meal plan")
    except Exception as e:
        logger.error(f"❌ Failed to parse meal plan: {e}")
        return meal_plan_text
    
    # Load food database
    try:
        food_cache = get_food_database_cache()
        foods_data = food_cache.get_foods_data()
        foods_list = foods_data.get('foods', []) if foods_data else []
        logger.info(f"✅ Loaded {len(foods_list)} foods from database cache")
    except Exception as e:
        logger.error(f"❌ Failed to load food database: {e}")
        return meal_plan_text
    
    enriched_text = meal_plan_text
    enrichment_count = 0
    
    for idx, item in enumerate(items):
        food_name = item['FoodItems']
        food_name_lower = food_name.lower().strip()
        
        # Find matching food in database
        food_id = None
        food_ingredients = []
        food_recipe = []
        
        best_match = None
        best_score = 0
        
        for food_item in foods_list:
            common_name = food_item.get('common_name', '').lower().strip()
            
            # Calculate similarity score
            if food_name_lower == common_name:
                score = 100  # Exact match
            elif food_name_lower in common_name:
                score = 90
            elif common_name in food_name_lower:
                score = 85
            else:
                # Use fuzzy matching
                try:
                    from rapidfuzz import fuzz
                    score = fuzz.ratio(food_name_lower, common_name)
                except ImportError:
                    # Fallback: simple substring matching
                    if any(word in common_name for word in food_name_lower.split()):
                        score = 70
                    else:
                        score = 0
            
            if score > best_score and score >= 70:  # Minimum 70% similarity
                best_score = score
                best_match = food_item
        
        if best_match:
            food_id = best_match.get('food_id')
            recipe_data = best_match.get('recipe', {})
            food_ingredients = recipe_data.get('ingredients', []) if isinstance(recipe_data, dict) else []
            food_recipe = recipe_data.get('steps', []) if isinstance(recipe_data, dict) else []
            logger.debug(f"  ✓ Food {idx+1}/{len(items)}: '{food_name}' -> food_id={food_id} (match score: {best_score})")
        else:
            food_id = "UNKNOWN"
            logger.warning(f"  ⚠ Food {idx+1}/{len(items)}: '{food_name}' -> NO MATCH in database")
        
        # Format ingredients and recipe as plain text
        ingredients_text = ", ".join(food_ingredients) if food_ingredients else "N/A"
        recipe_text = " > ".join(food_recipe) if food_recipe else "N/A"
        
        # Create enrichment string in plain text format
        enrichment = f' | FoodID: {food_id} | Ingredients: {ingredients_text} | Recipe: {recipe_text}'
        
        # Find the food item line and append metadata
        calories_value = item['Calories']
        
        # Try multiple calorie formats
        possible_calorie_texts = [
            f"Calories: {calories_value}kcal",
            f"Calories: {int(calories_value)}kcal",
            f"Calories: {calories_value:.1f}kcal"
        ]
        
        # Split by lines, find matching line, append
        lines = enriched_text.split('\n')
        line_found = False
        
        for i, line in enumerate(lines):
            # Check if this line contains the food name AND any of the calorie formats
            if food_name in line and any(cal_text in line for cal_text in possible_calorie_texts):
                # Make sure we don't enrich the same line twice
                if 'FoodID:' not in line:
                    lines[i] = line.rstrip() + enrichment
                    enrichment_count += 1
                    line_found = True
                    logger.debug(f"    → Enriched line {i+1}")
                    break
        
        if not line_found:
            logger.warning(f"  ⚠ Could not find line to enrich for '{food_name}' (calories: {calories_value})")
        
        enriched_text = '\n'.join(lines)
    
    logger.info(f"✅ Enrichment complete: {enrichment_count}/{len(items)} items enriched")
    
    return enriched_text


# Example usage
if __name__ == "__main__":
    sample_text = """Here is your meal plan:

**Breakfast**

- Egg White Spinach Scramble | Household Measure: 0.5 plate | Portion Weight: 7 oz | Protein: 17g | Carbs: 10g | Fat: 6g | Fiber: 2g | Sodium: 201mg | Iodine: 33mcg | Sugar: 2g | Cholesterol: 0mg | Calories: 159kcal

Total calories for Breakfast: 159.0 kcal
"""
    
    print("Original text:")
    print(sample_text)
    print("\n" + "="*80 + "\n")
    
    enriched = enrich_meal_plan_with_metadata(sample_text)
    
    print("Enriched text:")
    print(enriched)
