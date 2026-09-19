import logging
from typing import Dict, List, Optional
from datetime import datetime
import json
from pathlib import Path
from helpers.food_database_cache import get_food_database_cache
from ai.comprehensive_filtering import apply_comprehensive_filtering
from ai.deterministic_meal_generator import DeterministicMealGenerator
from ai.enhanced_meal_generator import EnhancedMealGenerator, get_plan_totals
from config import BASE_DIR

logger = logging.getLogger(__name__)


def generate_meal_plan_with_fallback(
    user_profile: Dict,
    target_date: str = None,
    plan_variation: int = 0,
    previous_days_foods: List = None,
    meal_calorie_distribution: Dict[str, int] = None,
    silent_mode: bool = False  # 🚀 PERFORMANCE: Reduce logging for speed
) -> Dict:
   
    try:
        if not silent_mode:
            logger.info(" Attempting deterministic meal generation...")
        
        food_cache = get_food_database_cache()
        cached_foods = food_cache.get_foods_data()
        
        deterministic_engine = DeterministicMealGenerator(foods_data_cache=cached_foods)
        
        # 🚀 PERFORMANCE: Enable silent mode for faster generation
        deterministic_engine._silent_mode = silent_mode
        
        deterministic_plan = deterministic_engine.generate_deterministic_meal_plan(
            user_profile=user_profile,
            target_date=target_date,
            plan_variation=plan_variation,
            previous_days_foods=previous_days_foods,
            meal_calorie_distribution=meal_calorie_distribution
        )
        
        if not silent_mode:
            logger.info(" Deterministic generation succeeded")
        return deterministic_plan
    
    except ValueError as e:
        error_msg = str(e)
        
        if "slot_pool_count=0" in error_msg or "Pool exhausted" in error_msg:
            logger.warning(f" Deterministic generator failed: {error_msg}")
            logger.info(" Falling back to enhanced meal generator...")
            
            tdee = calculate_tdee_from_profile(user_profile)
            
            macro_ranges = get_macro_ranges_from_profile(user_profile)
            
            try:
                enhanced_plan = generate_with_enhanced_generator(
                    user_profile=user_profile,
                    total_tdee=tdee,
                    macro_ranges=macro_ranges,
                    previous_days_foods=previous_days_foods
                )
                
                if enhanced_plan:
                    logger.info(" Enhanced generator succeeded")
                    converted_plan = convert_enhanced_to_deterministic_format(
                        enhanced_plan, user_profile
                    )
                    return converted_plan
                else:
                    logger.error(" Enhanced generator returned None (failed to generate plan)")
                    raise ValueError("Enhanced generator failed: returned None")
            
            except Exception as e2:
                logger.error(f" Enhanced generator error: {e2}")
                raise ValueError(f"Both generators failed. Deterministic: {error_msg}, Enhanced: {str(e2)}")
        else:
            logger.error(f" Deterministic generator failed with non-pool error: {error_msg}")
            raise
    
    except Exception as e:
        logger.error(f" Unexpected error in deterministic generator: {e}")
        raise


def calculate_tdee_from_profile(user_profile: Dict) -> int:
   
    expert_tdee = user_profile.get('expert_tdee')
    if expert_tdee is not None:
        try:
            val = int(float(expert_tdee))
            if val > 0:
                logger.info(f" EXPERT TDEE OVERRIDE: Using expert-prescribed TDEE = {val} kcal")
                return val
        except (TypeError, ValueError):
            pass

    if 'tdee' in user_profile and user_profile['tdee'] is not None:
        try:
            return int(user_profile['tdee'])
        except (TypeError, ValueError):
            pass
    
    if 'calorie_target' in user_profile and user_profile['calorie_target'] is not None:
        try:
            return int(user_profile['calorie_target'])
        except (TypeError, ValueError):
            pass
    
    if 'bmr' in user_profile and 'activity_level' in user_profile:
        if user_profile['bmr'] is not None and user_profile['activity_level'] is not None:
            try:
                bmr = float(user_profile['bmr'])
                activity_multipliers = {
                    'sedentary': 1.2,
                    'light': 1.375,
                    'moderate': 1.55,
                    'active': 1.725,
                    'very active': 1.9
                }
                multiplier = activity_multipliers.get(
                    str(user_profile['activity_level']).lower(), 
                    1.55
                )
                return int(bmr * multiplier)
            except (TypeError, ValueError):
                pass
    
    age = user_profile.get('age')
    gender = user_profile.get('gender')
    
    if age is None:
        age = 30
    else:
        try:
            age = int(age)
        except (TypeError, ValueError):
            age = 30
    
    if gender is None or not gender:
        gender = 'male'
    else:
        gender = str(gender).lower()
    
    if gender == 'female':
        base = 1800
    else:
        base = 2200
    
    if age > 50:
        base -= 200
    elif age < 25:
        base += 200
    
    logger.warning(f" TDEE not in profile, using estimated: {base} kcal (age: {age}, gender: {gender})")
    return base


def get_macro_ranges_from_profile(user_profile: Dict) -> Dict[str, tuple]:
    
    if 'macro_ranges' in user_profile and user_profile['macro_ranges']:
        return user_profile['macro_ranges']
    
    medical_conditions = user_profile.get('medical_conditions', [])
    
    if not isinstance(medical_conditions, list):
        medical_conditions = []
    medical_conditions = [c for c in medical_conditions if c is not None]
    
    if any('diabetes' in str(c).lower() for c in medical_conditions if c):
        return {
            'protein': (30, 35),
            'carbs': (35, 40),
            'fat': (25, 30)
        }
    
    if any('cholesterol' in str(c).lower() for c in medical_conditions if c):
        return {
            'protein': (25, 30),
            'carbs': (50, 55),
            'fat': (15, 20)
        }
    
    return {
        'protein': (25, 30),
        'carbs': (45, 50),
        'fat': (20, 25)
    }


def generate_with_enhanced_generator(
    user_profile: Dict,
    total_tdee: int,
    macro_ranges: Dict[str, tuple],
    previous_days_foods: List = None
) -> Optional[Dict]:
    
    try:
        logger.info(" Enhanced generator: Loading foods from raw database with minimal filtering...")
        
        from helpers.food_database_cache import get_food_database_cache
        food_cache = get_food_database_cache()
        cached_foods = food_cache.get_foods_data()
        
        det_gen = DeterministicMealGenerator(foods_data_cache=cached_foods)
        
        logger.info(f" Foods loaded from cache (fast path): {det_gen.foods_json_path}")
        
        raw_foods = []
        
        if 'foods' in det_gen.foods_raw and isinstance(det_gen.foods_raw['foods'], list):
            raw_foods = det_gen.foods_raw['foods']
            logger.info(f"   Detected: Flat 'foods' array structure")
        
        elif 'main_dishes' in det_gen.foods_raw and 'side_dishes' in det_gen.foods_raw:
            main_dishes_array = det_gen.foods_raw.get('main_dishes', [])
            side_dishes_array = det_gen.foods_raw.get('side_dishes', [])
            raw_foods = main_dishes_array + side_dishes_array
            logger.info(f"   Detected: Standard main/side structure")
        
        elif isinstance(det_gen.foods_raw, list):
            raw_foods = det_gen.foods_raw
            logger.info(f"   Detected: Root-level array")
        
        logger.info(f" Raw database: {len(raw_foods)} foods loaded")
        
        if raw_foods:
            sample_food = raw_foods[0]
            logger.debug(f"   Sample food keys: {list(sample_food.keys())[:10]}")
            logger.debug(f"   Sample food name: {sample_food.get('Food Name', sample_food.get('common_name', 'Unknown'))}")
        
        filtered_foods = apply_minimal_safety_filters(raw_foods, user_profile)
        
        logger.info(f" After minimal filtering: {len(filtered_foods)} foods available")
        
        if not filtered_foods:
            logger.error(" No foods available even with minimal filtering")
            logger.error("   Check if database is loaded correctly or if allergen/restriction filters are too strict")
            logger.error(f"   User allergies: {user_profile.get('allergies', [])}")
            logger.error(f"   User restrictions: {user_profile.get('restrictions', [])}")
            logger.error(f"   User dietary pref: {user_profile.get('dietary_preference', 'None')}")
            return None
        
        logger.info(f" Minimal filtering passed: {len(filtered_foods)} foods available for enhanced generator")
        
        prepared_foods = prepare_foods_for_enhanced_generator(filtered_foods)
        logger.info(f" Prepared {len(prepared_foods)} foods for enhanced generator")
        
        selected_cuisine = user_profile.get('cuisine', 'Any')
        if isinstance(selected_cuisine, list):
            selected_cuisine = selected_cuisine[0] if selected_cuisine else 'Any'
        
        selected_pref = user_profile.get('dietary_preference', 'Any')
        if isinstance(selected_pref, list):
            selected_pref = selected_pref[0] if selected_pref else 'Any'
        
        # Build blocked food names from previous_days_foods for cross-day diversity
        blocked_food_names = set()
        if previous_days_foods:
            if isinstance(previous_days_foods, set):
                blocked_food_names = {f.lower().strip() for f in previous_days_foods if f}
            elif isinstance(previous_days_foods, list):
                for day_foods in previous_days_foods:
                    if isinstance(day_foods, set):
                        blocked_food_names.update({f.lower().strip() for f in day_foods if f})
                    elif isinstance(day_foods, str):
                        blocked_food_names.add(day_foods.lower().strip())
        
        if blocked_food_names:
            pre_filter_count = len(prepared_foods)
            prepared_foods = [
                f for f in prepared_foods
                if f.get('Food_Name', f.get('Food Name', f.get('name', ''))).lower().strip() not in blocked_food_names
            ]
            logger.info(f" CROSS-DAY DIVERSITY: Removed {pre_filter_count - len(prepared_foods)} blocked foods from enhanced pool ({len(prepared_foods)} remain)")
        
        logger.info(f" Calling EnhancedMealGenerator with {len(prepared_foods)} foods, TDEE: {total_tdee} kcal")
        generator = EnhancedMealGenerator()
        meal_plan = generator.generate_meal_plan(
            filtered_food_pool=prepared_foods,
            total_tdee=total_tdee,
            macro_ranges=macro_ranges,
            selected_cuisine=selected_cuisine,
            selected_pref=selected_pref,
            max_attempts=50,  
            user_profile=user_profile 
        )
        
        if meal_plan:
            logger.info(f" Enhanced generator returned meal plan with {len(meal_plan.get('meal_plan', []))} meals")
        else:
            logger.error(" Enhanced generator returned None")
        
        return meal_plan
    
    except Exception as e:
        logger.error(f"Enhanced generator error: {e}", exc_info=True)
        return None


def prepare_foods_for_enhanced_generator(foods: List[Dict]) -> List[Dict]:
    
    prepared = []
    
    for food in foods:
        if 'name' in food and 'Food_Name' not in food:
            food['Food_Name'] = food['name']
        elif 'Food Name' in food and 'Food_Name' not in food:
            food['Food_Name'] = food['Food Name']
        elif 'common_name' in food and 'Food_Name' not in food:
            food['Food_Name'] = food['common_name']
        elif 'Food_Name' not in food:
            food['Food_Name'] = f"Unknown_{id(food)}"
        
        if 'food_id' in food and 'Food_ID' not in food:
            food['Food_ID'] = food['food_id']
        elif 'Food_ID' not in food and 'food_id' not in food:
            food_name = food.get('Food_Name', '')
            food['Food_ID'] = hash(food_name) if food_name else id(food)
        
        if 'per_100g_nutrients' in food:
            per_100g = food['per_100g_nutrients']
            food['Calories_per_100g'] = per_100g.get('calories_kcal', 0.0)
            food['Protein_g_per_100g'] = per_100g.get('protein_g', 0.0)
            food['Carbs_g_per_100g'] = per_100g.get('total_carbs_g', 0.0)
            food['Fat_g_per_100g'] = per_100g.get('total_fat_g', 0.0)
            food['Fiber_g_per_100g'] = per_100g.get('dietary_fiber_g', 0.0)
            food['Sugar_per_100g'] = per_100g.get('total_sugar_g', 0.0)
            food['Sodium_per_100g'] = per_100g.get('sodium_mg', 0.0)
            food['Cholesterol_per_100g'] = per_100g.get('cholesterol_mg', 0.0)
            food['Iodine_per_100g'] = per_100g.get('iodine_mcg', 0.0)
        
        for nutrient in ['Calories', 'Protein_g', 'Carbs_g', 'Fat_g', 
                         'Fiber_g', 'Sugar', 'Sodium', 'Cholesterol', 'Iodine']:
            per_g_key = f'{nutrient}_per_g'
            per_100g_key = f'{nutrient}_per_100g'
            
            if per_g_key not in food:
                if per_100g_key in food:
                    try:
                        food[per_g_key] = float(food[per_100g_key]) / 100.0
                    except (TypeError, ValueError):
                        food[per_g_key] = 0.0
                else:
                    food[per_g_key] = 0.0
        
        if 'portion_options' in food and isinstance(food['portion_options'], list):
            for i, portion in enumerate(food['portion_options'][:5], 1):  # Max 5 portions
                food[f'portion_{i}_grams'] = portion.get('grams', portion.get('portion_g', 0.0))
                food[f'portion_{i}_label'] = portion.get('label', f'Portion {i}')
        
        has_portion = False
        for i in range(1, 6):
            if f'portion_{i}_grams' in food and food[f'portion_{i}_grams']:
                has_portion = True
                break
        
        if not has_portion:
            food['portion_1_grams'] = 100.0
            food['portion_1_label'] = '100g'
        
        prepared.append(food)
    
    return prepared


def apply_minimal_safety_filters(foods: List[Dict], user_profile: Dict) -> List[Dict]:
    
    filtered = []
    
    raw_allergies = user_profile.get('allergies', [])
    if not isinstance(raw_allergies, list):
        raw_allergies = []
    allergies = [a.lower().strip() for a in raw_allergies if a is not None and str(a).strip()]
    
    raw_restrictions = user_profile.get('restrictions', [])
    if not isinstance(raw_restrictions, list):
        raw_restrictions = []
    restrictions = [r.lower().strip() for r in raw_restrictions if r is not None and str(r).strip()]
    
    dietary_pref_raw = user_profile.get('dietary_preference', '')
    dietary_pref = str(dietary_pref_raw).lower().strip() if dietary_pref_raw else ''
    
    logger.info(f" Minimal filtering: {len(allergies)} allergies, {len(restrictions)} restrictions, pref: {dietary_pref}")
    
    sample_food = foods[0] if foods else {}
    has_allergen_field = 'allergens' in sample_food
    has_dietary_pref_field = 'dietary_preference' in sample_food
    
    if not has_allergen_field:
        logger.warning(" Foods missing 'allergens' field - using name-based heuristic filtering")
    if not has_dietary_pref_field:
        logger.warning(" Foods missing 'dietary_preference' field - using name-based heuristic filtering")
    
    for food in foods:
        food_name = str(food.get('name', food.get('Food_Name', ''))).lower()
        
        if has_allergen_field:
            food_allergens = str(food.get('allergens', '')).lower()
            has_allergen = any(allergen in food_allergens for allergen in allergies if allergen)
        else:
            has_allergen = False
            for allergen in allergies:
                if not allergen:
                    continue
                allergen_keywords = {
                    'dairy': ['milk', 'cheese', 'butter', 'cream', 'yogurt', 'paneer', 'ghee'],
                    'eggs': ['egg', 'omelette', 'scrambled', 'fried egg'],
                    'soy': ['soy', 'tofu', 'tempeh', 'edamame'],
                    'nuts': ['peanut', 'almond', 'cashew', 'walnut', 'hazelnut', 'pecan'],
                    'shellfish': ['shrimp', 'crab', 'lobster', 'prawn', 'crawfish'],
                    'fish': ['salmon', 'tuna', 'cod', 'fish', 'anchovy', 'sardine'],
                    'wheat': ['wheat', 'bread', 'pasta', 'noodle', 'flour', 'bagel', 'pita'],
                    'gluten': ['wheat', 'bread', 'pasta', 'barley', 'rye', 'noodle']
                }
                keywords = allergen_keywords.get(allergen, [allergen])
                if any(kw in food_name for kw in keywords):
                    has_allergen = True
                    break
        
        if has_allergen:
            continue
        
        if has_dietary_pref_field:
            food_pref = str(food.get('dietary_preference', '')).lower()
            
            if dietary_pref == 'vegan':
                if 'vegan' not in food_pref:
                    continue
            elif dietary_pref == 'vegetarian':
                if 'vegetarian' not in food_pref and 'vegan' not in food_pref:
                    continue
        else:
            if dietary_pref in ['vegan', 'vegetarian']:
                animal_keywords = ['chicken', 'beef', 'pork', 'lamb', 'mutton', 'turkey', 
                                 'duck', 'fish', 'salmon', 'tuna', 'shrimp', 'crab', 'lobster',
                                 'prawn', 'meat', 'bacon', 'sausage', 'ham']
                if any(kw in food_name for kw in animal_keywords):
                    continue
                
                egg_keywords = ['egg', 'omelette', 'omelet', 'scrambled egg', 'fried egg',
                               'tamago', 'tea egg', 'braised egg', 'marbled egg', 'boiled egg',
                               'poached egg', 'egg tart', 'egg salad', 'deviled egg']
                if any(kw in food_name for kw in egg_keywords):
                    continue
                
                if dietary_pref == 'vegan':
                    dairy_keywords = ['milk', 'cheese', 'butter', 'cream', 'yogurt', 
                                    'ghee', 'paneer', 'whey', 'casein', 'lactose']
                    if any(kw in food_name for kw in dairy_keywords):
                        continue

        food_restrictions = str(food.get('restrictions', '')).lower()
        
        skip_food = False
        for restriction in restrictions:
            if restriction in ['keto', 'ketogenic'] and 'high-carb' in food_restrictions:
                skip_food = True
                break
            elif restriction in ['low-sodium', 'low sodium'] and 'high-sodium' in food_restrictions:
                skip_food = True
                break
            elif restriction in ['diabetic', 'diabetes'] and 'high-sugar' in food_restrictions:
                skip_food = True
                break
        
        if skip_food:
            continue
        
        filtered.append(food)
    
    logger.info(f" Minimal filtering complete: {len(filtered)}/{len(foods)} foods retained")
    
    return filtered


def convert_enhanced_to_deterministic_format(
    enhanced_plan: Dict,
    user_profile: Dict
) -> Dict:
    
    deterministic_format = {
        'meal_plan': {},
        'meals_nutrients': {},
        'total_nutrients': {},
        'daily_totals': {},
        'metadata': {
            'generator': 'enhanced_fallback',
            'user_id': user_profile.get('user_id', 'unknown')
        }
    }
    
    for meal in enhanced_plan.get('meal_plan', []):
        meal_name = meal['meal_name']
        foods = meal['foods']
        
        meal_items = []
        meal_nutrients = {
            'Calories': 0,
            'Protein': 0,
            'Carbohydrates': 0,
            'Fat': 0,
            'Fiber': 0,
            'Sodium': 0,
            'Sugar': 0
        }
        
        for food in foods:
            food_name = food.get('Food_Name', food.get('common_name', 'Unknown'))
            portion_g = food.get('Adjusted_Portion_g', 100)
            serving_size = food.get('Serving_Size', f'{portion_g}g')

            recipe_data = food.get('recipe', {})
            if isinstance(recipe_data, dict):
                recipe_ingredients = recipe_data.get('ingredients', [])
                recipe_steps = recipe_data.get('steps', [])
            else:
                recipe_ingredients = []
                recipe_steps = []

            meal_items.append({
                'name': f"{food_name} ({serving_size})",
                'nutrients': {
                    'Calories': round(food.get('Calories', 0), 1),
                    'Protein': round(food.get('Protein_g', 0), 1),
                    'Carbohydrates': round(food.get('Carbs_g', 0), 1),
                    'Fat': round(food.get('Fat_g', 0), 1),
                    'Fiber': round(food.get('Fiber_g', 0), 1),
                    'Sodium': round(food.get('Sodium', 0), 1),
                    'Iodine': round(food.get('Iodine', 0), 1),
                    'Sugar': round(food.get('Sugar', 0), 1),
                    'Cholesterol': round(food.get('Cholesterol', 0), 1),
                    'Portion Weight': round(portion_g, 1)
                },
                'food_id': food.get('food_id'),
                'ingredients': recipe_ingredients,
                'recipe': recipe_steps,
            })
            
            meal_nutrients['Calories'] += food.get('Calories', 0)
            meal_nutrients['Protein'] += food.get('Protein_g', 0)
            meal_nutrients['Carbohydrates'] += food.get('Carbs_g', 0)
            meal_nutrients['Fat'] += food.get('Fat_g', 0)
            meal_nutrients['Fiber'] += food.get('Fiber_g', 0)
            meal_nutrients['Sodium'] += food.get('Sodium', 0)
            meal_nutrients['Sugar'] += food.get('Sugar', 0)
        
        deterministic_format['meal_plan'][meal_name] = meal_items
        deterministic_format['meals_nutrients'][meal_name] = meal_nutrients
        
        for nutrient, value in meal_nutrients.items():
            deterministic_format['total_nutrients'][nutrient] = \
                deterministic_format['total_nutrients'].get(nutrient, 0) + value
    
    totals = get_plan_totals(enhanced_plan['meal_plan'])
    deterministic_format['daily_totals'] = {
        'Calories': totals['calories'],
        'Protein': totals['protein'],
        'Carbohydrates': totals['carbs'],
        'Fat': totals['fat'],
        'Sodium': totals.get('sodium', 0),
        'Cholesterol': totals.get('cholesterol', 0),
        'Fiber': totals.get('fiber', 0),
        'Sugar': totals.get('sugar', 0)
    }
    
    return deterministic_format



def example_integration_in_nutrition_py():

    pass


if __name__ == "__main__":
    print(" Testing Meal Plan Fallback Mechanism\n")
    
    test_profile = {
        'user_id': 'test_user',
        'dietary_preference': 'Vegan',
        'allergies': ['Peanuts', 'Soy', 'Gluten'],
        'dietary_restrictions': ['Low-Sodium', 'Low-Sugar'],
        'medical_conditions': ['Diabetes', 'High Cholesterol'],
        'cuisine': ['Indian'],
        'disliked_foods': ['Mushrooms', 'Eggplant'],
        'age': 45,
        'gender': 'Female'
    }
    
    try:
        plan = generate_meal_plan_with_fallback(
            user_profile=test_profile,
            target_date='2026-03-16',
            plan_variation=123
        )
        
        print(" Meal plan generated successfully!")
        print(f"\nDaily Totals:")
        print(f"  Calories: {plan['daily_totals']['Calories']:.0f} kcal")
        print(f"  Protein: {plan['daily_totals']['Protein']:.0f}g")
        print(f"  Carbs: {plan['daily_totals']['Carbohydrates']:.0f}g")
        print(f"  Fat: {plan['daily_totals']['Fat']:.0f}g")
        
        print(f"\nGenerator used: {plan['metadata']['generator']}")
        
    except Exception as e:
        print(f" Error: {e}")
