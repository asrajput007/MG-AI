import logging
import re
from typing import Dict, List, Set, Tuple, Optional

logger = logging.getLogger(__name__)



def is_safe_from_blacklist_optimized(
    food_name: str,
    food_data: Dict,
    user_constraints: Dict[str, Set[str]],
    blacklist_keywords: Set[str] = None
) -> Tuple[bool, str]:
    
    food_lower = food_name.lower()
    
    food_allergies = set(str(a).lower().strip() for a in food_data.get('allergies', []))
    food_restrictions = set(str(r).lower().strip() for r in food_data.get('dietary_restrictions_to_avoid', []))
    food_symptom_codes = set(str(s).lower().strip() for s in food_data.get('symptom_aggravating_food_codes', []))
    food_digestive_issues = set(str(d).lower().strip() for d in food_data.get('digestive_issues', []))
    
    
    if user_constraints['allergies'] & food_allergies:
        overlap = user_constraints['allergies'] & food_allergies
        return False, f"Allergen detected: {overlap}"
    
    if user_constraints['restrictions'] & food_restrictions:
        overlap = user_constraints['restrictions'] & food_restrictions
        return False, f"Dietary restriction violated: {overlap}"
    
    if user_constraints['symptoms'] & food_symptom_codes:
        overlap = user_constraints['symptoms'] & food_symptom_codes
        return False, f"Symptom trigger: {overlap}"
    
    if user_constraints['digestive_issues'] & food_digestive_issues:
        overlap = user_constraints['digestive_issues'] & food_digestive_issues
        return False, f"Digestive issue cause: {overlap}"
    

    if user_constraints['medical_conditions']:
        food_suitable_for = set(str(c).lower().strip() for c in food_data.get('is_suitable_for', []))
        
        if food_suitable_for:
            has_match = bool(user_constraints['medical_conditions'] & food_suitable_for)
            if not has_match:
                return False, f"Not suitable for medical condition: {user_constraints['medical_conditions']}"
    
    if blacklist_keywords:
        for banned_word in blacklist_keywords:
            pattern = r'\b' + re.escape(banned_word.lower()) + r'\b'
            if re.search(pattern, food_lower):
                return False, f"Blacklisted keyword: '{banned_word}'"
    
    return True, ""


def build_user_constraint_sets(user_profile: Dict) -> Dict[str, Set[str]]:
   
    return {
        'allergies': set(str(a).lower().strip() for a in user_profile.get('allergies', [])),
        'restrictions': set(str(r).lower().strip() for r in user_profile.get('restrictions', [])),
        'symptoms': set(str(s).lower().strip() for s in user_profile.get('symptom_aggravating_foods', [])),
        'digestive_issues': set(str(d).lower().strip() for d in user_profile.get('digestive_issues', [])),
        'medical_conditions': set(str(c).lower().strip() for c in user_profile.get('medical_conditions', []))
    }




def categorize_foods_with_meal_tag_routing(
    food_pool: Dict[str, Dict], 
    user_profile: Dict = None
) -> Dict[str, Dict[str, List[str]]]:
    
    meal_types = ["breakfast", "lunch", "dinner", "snack", "global"]
    pools = {}
    for meal_type in meal_types:
        pools[meal_type] = {
            'proteins': [],
            'complex_carbs': [],
            'fibrous_veggies': [],
            'fats': []
        }
    
    is_t2d = False
    if user_profile:
        medical_conditions = user_profile.get('medical_conditions', [])
        is_t2d = any(
            'diabetes' in str(cond).lower() or 
            'type 2 diabetes' in str(cond).lower() or
            'insulin resistance' in str(cond).lower()
            for cond in medical_conditions
        )
    
    for food_name, food_data in food_pool.items():
        meal_tags = food_data.get('meal_tags', [])
        
        if not meal_tags:
            meal_tags = ['global']
        
        category = _determine_biological_category(food_name, food_data, is_t2d)
        
        if not category:
            continue  
        
        for meal_tag in meal_tags:
            meal_tag_lower = str(meal_tag).lower().strip()
            
            if meal_tag_lower in ['morning snack', 'evening snack', 'snacks']:
                pool_key = 'snack'
            elif meal_tag_lower in pools:
                pool_key = meal_tag_lower
            else:
                pool_key = 'global'  
            
            pools[pool_key][category].append(food_name)
    
    logger.info(" STAGE 2: TAG-BASED MULTI-POOL ROUTING COMPLETE")
    for meal_type, categories in pools.items():
        total_items = sum(len(items) for items in categories.values())
        if total_items > 0:
            logger.info(f"    {meal_type.upper()}: {total_items} items")
            for category, items in categories.items():
                if items:
                    logger.info(f"      - {category}: {len(items)} items")
    
    return pools


def _determine_biological_category(food_name: str, food_data: Dict, is_t2d: bool = False) -> Optional[str]:
    
    nutrients = food_data.get('nutrients_per_100g_range', {})
    
    def avg(arr):
        if not arr or len(arr) != 2:
            return 0.0
        valid = [v for v in arr if v is not None]
        return sum(valid) / 2.0 if valid else 0.0
    
    protein = avg(nutrients.get('protein_per_100g', [0, 0]))
    carbs = avg(nutrients.get('carbs_per_100g', [0, 0]))
    fat = avg(nutrients.get('fat_per_100g', [0, 0]))
    calories = avg(nutrients.get('calories_per_100g', [0, 0]))
    fiber = avg(nutrients.get('fiber_per_100g', [0, 0]))
    
    if 'category' in food_data:
        return food_data['category']
    
    food_lower = food_name.lower()
    
    high_fiber_keywords = [
        'lentil', 'quinoa', 'bean', 'oat', 'brown rice', 'millet', 
        'barley', 'bulgur', 'farro', 'chickpea', 'dal', 'wild rice'
    ]
    if any(kw in food_lower for kw in high_fiber_keywords) and fiber >= 2.5:
        return 'complex_carbs'
    
    if any(meat in food_lower for meat in ['beef', 'pork', 'chicken', 'turkey', 'lamb', 'steak', 'sausage']):
        return 'proteins'
    
    if calories <= 80 and carbs < 15:
        return 'fibrous_veggies'
    elif fat > 15 or (fat > protein and fat > carbs and fat > 5):
        return 'fats'
    elif protein > 15 or (protein > carbs and protein > fat and protein > 8):
        return 'proteins'
    elif carbs >= 15 and fat < 12:
        if is_t2d and fiber < 2.5:
            if not any(kw in food_lower for kw in high_fiber_keywords):
                return None  
        return 'complex_carbs'
    
    return None




def select_smart_portion(
    food_data: Dict,
    target_kcal: float,
    food_name: str = ""
) -> Tuple[str, Dict, float]:
   
    portions = food_data.get('portions', {})
    
    if not portions:
        logger.warning(f" '{food_name}': No portions data, falling back to 100g baseline")
        return _fallback_to_100g_baseline(food_data, target_kcal)
    
    best_portion_name = None
    best_portion_nutrients = None
    best_ratio = float('inf')  
    best_ratio_distance = float('inf')
    
    for portion_name, portion_data in portions.items():
        portion_nutrients = portion_data.get('nutrients_for_portion', {})
        portion_kcal = portion_nutrients.get('calories_kcal', 0)
        
        if portion_kcal <= 0:
            continue  
        
        ratio = target_kcal / portion_kcal
        ratio_distance = abs(1.0 - ratio)
        
        if ratio_distance < best_ratio_distance:
            best_ratio_distance = ratio_distance
            best_ratio = ratio
            best_portion_name = portion_name
            best_portion_nutrients = portion_nutrients
    
    if best_portion_name is None:
        logger.warning(f" '{food_name}': All portions invalid, falling back to 100g baseline")
        return _fallback_to_100g_baseline(food_data, target_kcal)
    
    logger.debug(
        f" SMART PORTION: '{food_name}' → '{best_portion_name}' "
        f"(ratio: {best_ratio:.2f}x, distance from 1.0: {best_ratio_distance:.3f})"
    )
    
    return best_portion_name, best_portion_nutrients, best_ratio


def _fallback_to_100g_baseline(food_data: Dict, target_kcal: float) -> Tuple[str, Dict, float]:
    
    nutrients = food_data.get('nutrients_per_100g_range', {})
    
    def avg(arr):
        if not arr or len(arr) != 2:
            return 0.0
        valid = [v for v in arr if v is not None]
        return sum(valid) / 2.0 if valid else 0.0
    
    baseline_nutrients = {
        'calories_kcal': avg(nutrients.get('calories_per_100g', [0, 0])),
        'protein_g': avg(nutrients.get('protein_per_100g', [0, 0])),
        'total_fat_g': avg(nutrients.get('fat_per_100g', [0, 0])),
        'total_carbs_g': avg(nutrients.get('carbs_per_100g', [0, 0])),
        'dietary_fiber_g': avg(nutrients.get('fiber_per_100g', [0, 0])),
        'total_sugar_g': avg(nutrients.get('sugar_per_100g', [0, 0])),
        'sodium_mg': avg(nutrients.get('sodium_per_100g', [0, 0])),
        'cholesterol_mg': avg(nutrients.get('cholesterol_per_100g', [0, 0]))
    }
    
    baseline_kcal = baseline_nutrients['calories_kcal']
    ratio = target_kcal / baseline_kcal if baseline_kcal > 0 else 1.0
    
    return "100g Baseline", baseline_nutrients, ratio


def scale_nutrients_with_smart_portion(
    food_data: Dict,
    target_kcal: float,
    food_name: str = ""
) -> Dict:
    
    portion_name, portion_nutrients, ratio = select_smart_portion(food_data, target_kcal, food_name)
    
    scaled_nutrients = {}
    for key, value in portion_nutrients.items():
        if isinstance(value, (int, float)):
            scaled_nutrients[key] = value * ratio
    

    portions = food_data.get('portions', {})
    base_portion_g = 100.0  
    
    if portion_name in portions:
        base_portion_g = portions[portion_name].get('portion_g', 100.0)
    
    scaled_portion_g = base_portion_g * ratio
    scaled_portion_oz = scaled_portion_g / 28.35
    
    result = {
        "name": f"{food_name} ({portion_name}) - {scaled_portion_oz:.1f} oz",
        "portion_name": portion_name,
        "portion_weight_g": round(scaled_portion_g, 1),
        "portion_weight_oz": round(scaled_portion_oz, 1),
        "scaling_ratio": round(ratio, 2),
        "nutrients": {
            "Calories": round(scaled_nutrients.get('calories_kcal', 0), 1),
            "Protein": round(scaled_nutrients.get('protein_g', 0), 1),
            "Carbohydrates": round(scaled_nutrients.get('total_carbs_g', 0), 1),
            "Fat": round(scaled_nutrients.get('total_fat_g', 0), 1),
            "Fiber": round(scaled_nutrients.get('dietary_fiber_g', 0), 1),
            "Sugar": round(scaled_nutrients.get('total_sugar_g', 0), 1),
            "Sodium": round(scaled_nutrients.get('sodium_mg', 0), 1),
            "Cholesterol": round(scaled_nutrients.get('cholesterol_mg', 0), 1),
            "Portion Weight": round(scaled_portion_g, 1)
        }
    }
    
    logger.info(
        f" SCALED: '{food_name}' → {portion_name} × {ratio:.2f} = "
        f"{scaled_portion_oz:.1f} oz ({target_kcal:.0f} kcal)"
    )
    
    return result




def example_integration(user_profile: Dict, food_database: Dict):
    
    logger.info("=" * 80)
    logger.info(" 3-STAGE EXTRACTION ARCHITECTURE - INTEGRATION EXAMPLE")
    logger.info("=" * 80)

    logger.info("\n STAGE 1: O(1) SET-BASED METADATA FILTERING")
    user_constraints = build_user_constraint_sets(user_profile)
    
    logger.info(f"   User constraints compiled:")
    logger.info(f"   - Allergies: {len(user_constraints['allergies'])} items")
    logger.info(f"   - Restrictions: {len(user_constraints['restrictions'])} items")
    logger.info(f"   - Symptoms: {len(user_constraints['symptoms'])} items")
    logger.info(f"   - Digestive: {len(user_constraints['digestive_issues'])} items")
    logger.info(f"   - Medical: {len(user_constraints['medical_conditions'])} items")
    
    filtered_foods = {}
    rejected_count = 0
    
    for food_name, food_data in food_database.items():
        is_safe, reason = is_safe_from_blacklist_optimized(
            food_name, 
            food_data, 
            user_constraints
        )
        if is_safe:
            filtered_foods[food_name] = food_data
        else:
            rejected_count += 1
            logger.debug(f"    Rejected: '{food_name}' - {reason}")
    
    logger.info(f"    Filtered: {len(filtered_foods)} safe, {rejected_count} rejected")
    

    logger.info("\n STAGE 2: TAG-BASED MULTI-POOL ROUTING")
    meal_pools = categorize_foods_with_meal_tag_routing(filtered_foods, user_profile)

    logger.info("\n STAGE 3: SMART PORTION SELECTION")
    
    breakfast_proteins = meal_pools['breakfast']['proteins']
    if breakfast_proteins:
        selected_food = breakfast_proteins[0] 
        food_data = filtered_foods[selected_food]
        
        scaled_item = scale_nutrients_with_smart_portion(
            food_data,
            target_kcal=250.0,
            food_name=selected_food
        )
        
        logger.info(f"    Generated: {scaled_item['name']}")
        logger.info(f"    Calories: {scaled_item['nutrients']['Calories']} kcal")
        logger.info(f"    Portion: {scaled_item['portion_name']} × {scaled_item['scaling_ratio']}")
    
    logger.info("\n" + "=" * 80)
    logger.info(" 3-STAGE ARCHITECTURE INTEGRATION COMPLETE")
    logger.info("=" * 80)


if __name__ == "__main__":

    print(" Refactored 3-Stage Extraction Architecture")
    print("=" * 60)
    print(" STAGE 1: O(1) Set-Based Metadata Filtering")
    print(" STAGE 2: Tag-Based Multi-Pool Routing")
    print(" STAGE 3: Closest to 1.0 Smart Portion Selection")
    print("=" * 60)
    print("\nIntegrate these functions into:")
    print("  - ai/deterministic_meal_generator.py")
    print("  - ai/agent_core.py")
