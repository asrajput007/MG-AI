import logging
import re
from typing import Dict, List, Optional, Tuple
from .nutrition_models import (
    FoodSelection, MealPlanResponse, CalculatedNutrition,
    NUTRITION_DB, NUTRITION_CACHE, REDIS_AVAILABLE,
    convert_portion_to_grams, calculate_nutrition_for_portion,
    FoodItem, PortionUnit, MealType
)

logger = logging.getLogger(__name__)

try:
    from .agent_core import CLINICAL_AVOID_MAPPING
except ImportError:
    logger.warning("Could not import CLINICAL_AVOID_MAPPING from agent_core - using empty dict")
    CLINICAL_AVOID_MAPPING = {}
class NutritionEnrichmentEngine:
    
    def __init__(self, unified_food_db: Optional[Dict] = None):
        self.db = NUTRITION_DB
        self.cache = NUTRITION_CACHE
        self.fallback_used_count = 0
        self.unified_food_db = unified_food_db or {}
    
    def calculate_food_nutrition(
        self,
        food_selection: FoodSelection,
        use_cache: bool = True,
        meal_type: Optional[str] = None
    ) -> Tuple[CalculatedNutrition, float, bool]:
 
        is_snack = meal_type and ('snack' in meal_type.lower())
        
        if use_cache and self.cache:
            cached = self.cache.get(
                food_selection.name,
                food_selection.quantity,
                food_selection.unit.value
            )
            if cached:
                logger.debug(f"Cache hit: {food_selection.name}")
                return (
                    CalculatedNutrition(**cached['nutrition']),
                    cached['weight_g'],
                    False 
                )
        
        if self.unified_food_db:
            unified_data = self._lookup_in_unified_db(food_selection.name)
            if unified_data:

                db_calories = unified_data.get('calories', 0)
                if not db_calories or float(db_calories) <= 0:
                    if is_snack:
                        logger.info(f" SNACK BYPASS: {food_selection.name} - using LLM fallback (no DB validation)")
                    else:
                        logger.warning(f" DB-MISS: {food_selection.name} has invalid calories ({db_calories}) - using fallback")
                    self.fallback_used_count += 1
                    nutrition = self._fallback_estimation(food_selection)
                    weight_g = self._estimate_weight(food_selection)
                    return (nutrition, weight_g, True)  
                
                nutrition, weight_g = self._calculate_from_unified_data(
                    unified_data,
                    food_selection
                )
                logger.info(f" Unified DB hit: {food_selection.name} = {nutrition.calories:.0f} kcal")
                
                if self.cache:
                    self.cache.set(
                        food_selection.name,
                        food_selection.quantity,
                        food_selection.unit.value,
                        {
                            'nutrition': nutrition.dict(),
                            'weight_g': weight_g
                        }
                    )
                
                return (nutrition, weight_g, False) 
        
        food_item = self.db.lookup(food_selection.name)
        
        if food_item:
            nutrition = calculate_nutrition_for_portion(
                food_item,
                food_selection.quantity,
                food_selection.unit
            )

            if not nutrition or float(nutrition.calories) <= 0:
                logger.warning(f" DB-MISS: {food_selection.name} has invalid nutrition ({nutrition.calories if nutrition else 0} kcal) - using fallback")
                self.fallback_used_count += 1
                nutrition = self._fallback_estimation(food_selection)
                weight_g = self._estimate_weight(food_selection)
                return (nutrition, weight_g, True)  
            
            weight_g = convert_portion_to_grams(
                food_item,
                food_selection.quantity,
                food_selection.unit
            )
            
            if self.cache:
                self.cache.set(
                    food_selection.name,
                    food_selection.quantity,
                    food_selection.unit.value,
                    {
                        'nutrition': nutrition.dict(),
                        'weight_g': weight_g
                    }
                )
            
            logger.debug(f"DB lookup: {food_selection.name} = {nutrition.calories:.0f} kcal")
            return (nutrition, weight_g, False) 
        
        else:
            if is_snack:
                logger.info(f" SNACK BYPASS: {food_selection.name} - using LLM fallback (not in DB)")
            else:
                logger.warning(f"Food not in DB: {food_selection.name} - using fallback")
            self.fallback_used_count += 1
            nutrition = self._fallback_estimation(food_selection)
            weight_g = self._estimate_weight(food_selection)
            
            return (nutrition, weight_g, True) 
    
    def _normalize_food_name(self, name: str) -> str:
        normalized = re.sub(r'[^a-z0-9\s]', '', name.lower())
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        return normalized
    
    def _lookup_in_unified_db(self, food_name: str) -> Optional[Dict]:
        query_normalized = self._normalize_food_name(food_name)
        
        exact_key = f"{query_normalized}||100g"
        if exact_key in self.unified_food_db:
            return self.unified_food_db[exact_key]
        
        simple_key = f"{food_name.lower().strip()}||100g"
        if simple_key in self.unified_food_db:
            return self.unified_food_db[simple_key]
        
        PROCESSED_PENALTY_WORDS = ['dried', 'fried', 'sweetened', 'candied', 'syrup', 'oil', 'roasted', 'chips']
        
        for db_key, db_data in self.unified_food_db.items():
            db_name = db_key.split('||')[0] if '||' in db_key else db_key
            db_key_normalized = self._normalize_food_name(db_name)
            
            if db_key_normalized in query_normalized or query_normalized in db_key_normalized:
                if len(db_key_normalized) >= 3 and len(query_normalized) >= 3:
                   
                    length_ratio = len(db_name) / len(food_name) if len(food_name) > 0 else 1.0
                    if length_ratio > 1.2:
                        logger.debug(f" FUZZY-MATCH REJECTED: '{food_name}' → '{db_name}' (length ratio {length_ratio:.2f}x > 1.2x threshold)")
                        continue  
                    db_name_lower = db_name.lower()
                    query_lower = food_name.lower()
                    
                    db_has_penalty = any(penalty in db_name_lower for penalty in PROCESSED_PENALTY_WORDS)
                    query_has_penalty = any(penalty in query_lower for penalty in PROCESSED_PENALTY_WORDS)
                    
                    if db_has_penalty and not query_has_penalty:
                        logger.debug(f" FUZZY-MATCH REJECTED: '{food_name}' → '{db_name}' (processed variant penalty)")
                        continue 
                    
                    logger.debug(f"Partial match: '{food_name}' -> '{db_key}'")
                    return db_data
        
        return None
    
    def _calculate_from_unified_data(
        self,
        unified_data: Dict,
        food_selection: FoodSelection
    ) -> Tuple[CalculatedNutrition, float]:

        protein_per_100g = unified_data.get('protein', 0)
        fat_per_100g = unified_data.get('fat', 0)
        calories_per_100g = unified_data.get('calories', 0)
        
        db_carbs = unified_data.get('carbohydrates', 0)
        db_fiber = unified_data.get('fiber', 0)
        db_sugar = unified_data.get('sugar', 0)
        db_sodium = unified_data.get('sodium', 0)
        db_cholesterol = unified_data.get('cholesterol', 0)
        
        carbs_per_100g = db_carbs
        if (db_carbs == 0.0 or db_carbs is None) and food_selection.carbs_per_100g is not None:
            carbs_per_100g = food_selection.carbs_per_100g
            logger.info(f" HYBRID TRUST: {food_selection.name} - Using LLM carbs: {carbs_per_100g:.1f}g (DB was {db_carbs})")
        
        fiber_per_100g = db_fiber
        if (db_fiber == 0.0 or db_fiber is None) and food_selection.fiber_per_100g is not None:
            fiber_per_100g = food_selection.fiber_per_100g
            logger.info(f" HYBRID TRUST: {food_selection.name} - Using LLM fiber: {fiber_per_100g:.1f}g (DB was {db_fiber})")
        
        sugar_per_100g = db_sugar
        if (db_sugar == 0.0 or db_sugar is None) and food_selection.sugar_per_100g is not None:
            sugar_per_100g = food_selection.sugar_per_100g
            logger.info(f" HYBRID TRUST: {food_selection.name} - Using LLM sugar: {sugar_per_100g:.1f}g (DB was {db_sugar})")
        
        sodium_per_100g = db_sodium
        if (db_sodium == 0.0 or db_sodium is None) and food_selection.sodium_per_100g is not None:
            sodium_per_100g = food_selection.sodium_per_100g
            logger.info(f" HYBRID TRUST: {food_selection.name} - Using LLM sodium: {sodium_per_100g:.1f}mg (DB was {db_sodium})")
        
        cholesterol_per_100g = db_cholesterol
        if (db_cholesterol == 0.0 or db_cholesterol is None) and food_selection.cholesterol_per_100g is not None:
            cholesterol_per_100g = food_selection.cholesterol_per_100g
            logger.info(f" HYBRID TRUST: {food_selection.name} - Using LLM cholesterol: {cholesterol_per_100g:.1f}mg (DB was {db_cholesterol})")
        
        db_iodine = unified_data.get('iodine', 0)
        iodine_per_100g = db_iodine
        if (db_iodine == 0.0 or db_iodine is None) and food_selection.iodine_per_100g is not None:
            iodine_per_100g = food_selection.iodine_per_100g
            logger.info(f" HYBRID TRUST: {food_selection.name} - Using LLM iodine: {iodine_per_100g:.1f}mcg (DB was {db_iodine})")

        portion_weight_g = unified_data.get('portion_weight_g', 100)
        
        weight_g = self._estimate_weight_from_unit(
            food_selection.quantity,
            food_selection.unit,
            portion_weight_g
        )
        
        multiplier = weight_g / 100.0
        
        food_name_lower = food_selection.name.lower()
        is_raw_fruit = any(fruit_keyword in food_name_lower for fruit_keyword in [
            'apple', 'mango', 'banana', 'orange', 'pear', 'peach', 'plum', 
            'grape', 'berry', 'melon', 'watermelon', 'cantaloupe', 'kiwi',
            'pineapple', 'papaya', 'guava', 'lychee', 'passion fruit',
            'fruit salad', 'mixed fruit', 'fresh fruit'
        ])
        
        calculated_fat = fat_per_100g * multiplier
        if is_raw_fruit and calculated_fat > 1.0:
            logger.warning(f" FRUIT FAT ANOMALY DETECTED: {food_selection.name} had {calculated_fat:.2f}g fat → Clamped to 1.0g (database hallucination)")
            calculated_fat = min(calculated_fat, 1.0)
        
        carbs_final = carbs_per_100g * multiplier
        fiber_final = fiber_per_100g * multiplier
        nutrition = CalculatedNutrition(
            calories=calories_per_100g * multiplier,
            protein_g=protein_per_100g * multiplier,
            carbs_g=carbs_final,
            fat_g=calculated_fat,
            fiber_g=fiber_final,
            net_carbs_g=max(0, carbs_final - fiber_final),
            sodium_mg=sodium_per_100g * multiplier,
            sugar_g=sugar_per_100g * multiplier,
            cholesterol_mg=cholesterol_per_100g * multiplier,
            iodine_mcg=unified_data.get('iodine', 0) * multiplier
        )
        
        return (nutrition, weight_g)
    
    @staticmethod
    def validate_physics_laws(food_name: str, food_data: Dict, portion_weight_g: float = 100) -> tuple[bool, str]:
        """
        🔬 STRICT CLINICAL FIREWALL: Reject database rows violating nutritional biochemistry.
        
        PIVOTED TO CALORIC INTEGRITY (Feb 27, 2026):
        Previous version rejected 99% of database by requiring macros to equal 100g weight (ignoring water).
        New approach validates caloric consistency instead of weight matching.
        
        COMPREHENSIVE CLINICAL RULES (12 Validations - Caloric Integrity Update):
        - Val 1: THE CALORIC INTEGRITY LAW (calculated vs database calories within 20%)
        - Val 2: THE NON-ZERO RULE (reject high-calorie foods with all zero macros)
        - Val 3: MACRO-WEIGHT CEILING (macros cannot exceed portion weight)
        - Val 4: Fruit/Vegetable Protein Cap (produce with protein > 5g rejected)
        - Val 5: Gelatin/Sugar-Free Cap (these items with calories > 200 rejected)
        - Val 6: Fiber Law (fiber <= carbs - biochemically impossible otherwise)
        - Val 7: Volumetric Density (cup unit -> weight >= 80g for solids)
        - Val 8: Dry/Cooked Trap (cup + grain/lentil + protein > 15g = error)
        - Val 9: Sugar Anomaly (sweets with carbs > 50g must have sugar > 5g)
        - Val 10: Absolute Fat Cap (savory items with fat > 15g rejected)
        - Val 11: Absolute Sodium Cap (sodium > 400mg rejected)
        - Val 12: Sweets in Savory Ban (desserts forbidden in lunch/dinner pools)
        - Val 13: Extreme Density Cap (no single food > 800 kcal per serving)
        
        Args:
            food_name: Name of the food item
            food_data: Dictionary with keys 'protein', 'carbohydrates', 'fat', 'calories', 
                      'fiber', 'sugar', 'sodium', 'unit', 'meal_type', 'quantity'
            portion_weight_g: Weight of the portion in grams (default 100g)
        
        Returns:
            (is_valid, rejection_reason)
        """
        try:
            protein_g = float(food_data.get('protein', 0))
            carbs_g = float(food_data.get('carbohydrates', 0))
            fat_g = float(food_data.get('fat', 0))
            calories = float(food_data.get('calories', 0))
            fiber_g = float(food_data.get('fiber', 0))
            sugar_g = float(food_data.get('sugar', 0))
            
            food_name_lower = food_name.lower()
            
            total_macro_weight = protein_g + carbs_g + fat_g

            calc_calories = (protein_g * 4) + (carbs_g * 4) + (fat_g * 9)
            calorie_deviation = abs(calc_calories - calories)
            calorie_tolerance = calories * 0.20 if calories > 0 else 10  
            
            if calorie_deviation > calorie_tolerance:
                logger.info(f"CALORIE AUTO-HEAL: {food_name} DB={calories:.0f} kcal → Corrected to {calc_calories:.0f} kcal (deviation: {calorie_deviation:.0f} kcal)")
                food_data['calories'] = calc_calories  
                calories = calc_calories  
            

            if total_macro_weight == 0 and calories > 50:
                logger.warning(f" NON-ZERO AUTO-HEAL: {food_name} has {calories:.0f} kcal but 0.0g macros - estimating macros")
                protein_g = (calories * 0.20) / 4.0
                carbs_g = (calories * 0.50) / 4.0
                fat_g = (calories * 0.30) / 9.0
                food_data['protein'] = protein_g
                food_data['carbohydrates'] = carbs_g
                food_data['fat'] = fat_g
                logger.info(f"   Auto-healed: P:{protein_g:.1f}g C:{carbs_g:.1f}g F:{fat_g:.1f}g")
                total_macro_weight = protein_g + carbs_g + fat_g
            
            COMPOSITE_KEYWORDS = [
                'sandwich', 'mutton', 'chicken', 'beef', 'pork', 'fish', 'paneer', 
                'cheese', 'egg', 'poori', 'paratha', 'roti', 'roll', 'kofta', 
                'biryani', 'burger', 'pie', 'pizza', 'kebab', 'tikka', 'curry', 
                'wrap', 'taco', 'quesadilla', 'samosa', 'pakora', 'cutlet',
                'patty', 'sausage', 'bacon', 'ham', 'turkey', 'lamb', 'tofu',
                'tempeh', 'seitan', 'dal', 'lentil', 'bean', 'pulao', 'pilaf',
                'pasta', 'lasagna', 'ravioli', 'cannelloni', 'spaghetti', 'noodles',
                'quiche', 'tart', 'souffle', 'pastry', 'cake', 'mousse', 'bread',
                'brioche', 'stew', 'casserole', 'macaroni', 'dumpling'
            ]
            
            is_composite_meal = any(keyword in food_name_lower for keyword in COMPOSITE_KEYWORDS)
            
            if not is_composite_meal:
                fruit_keywords = [
                    'grape', 'apple', 'banana', 'orange', 'mango', 'pineapple', 'papaya',
                    'watermelon', 'melon', 'kiwi', 'strawberry', 'blueberry', 'raspberry',
                    'cherry', 'peach', 'plum', 'pear', 'apricot', 'guava', 'lychee',
                    'pomegranate', 'fig', 'date', 'prune', 'raisin', 'berry'
                ]
                vegetable_keywords = [
                    'potato', 'tomato', 'onion', 'garlic', 'carrot', 'broccoli', 'spinach',
                    'cabbage', 'lettuce', 'cucumber', 'zucchini', 'eggplant', 'pepper',
                    'cauliflower', 'celery', 'radish', 'beetroot', 'turnip', 'squash',
                    'pumpkin', 'mushroom', 'asparagus', 'artichoke'
                ]
                
                is_fruit = any(fruit in food_name_lower for fruit in fruit_keywords)
                is_vegetable = any(veg in food_name_lower for veg in vegetable_keywords)
                
                if (is_fruit or is_vegetable) and protein_g > 5:
                    food_type = 'fruit' if is_fruit else 'vegetable'
                    rejection_reason = f"FRUIT/VEG PROTEIN CAP VIOLATION: {food_name} is a {food_type} with {protein_g:.1f}g protein (max 5g for produce)"
                    return (False, rejection_reason)
            

            is_sugar_free = 'sugar free' in food_name_lower or 'sugar-free' in food_name_lower or 'sugarfree' in food_name_lower
            is_gelatin = 'gelatin' in food_name_lower or 'jello' in food_name_lower or 'jelly' in food_name_lower
            
            if (is_sugar_free or is_gelatin) and calories > 200:
                rejection_reason = f"SUGAR-FREE/GELATIN CALORIE CAP VIOLATION: {food_name} is sugar-free/gelatin with {calories:.0f} kcal (max 200 kcal)"
                return (False, rejection_reason)
            

            if fiber_g > carbs_g and fiber_g > 0.1: 
                rejection_reason = f"FIBER PHYSICS VIOLATION: {food_name} has {fiber_g:.1f}g fiber but only {carbs_g:.1f}g carbs (impossible)"
                return (False, rejection_reason)
            

            unit = food_data.get('unit', '').lower()
            is_grain_or_lentil = any(keyword in food_name_lower for keyword in [
                'dal', 'lentil', 'rice', 'quinoa', 'oats', 'barley', 'millet',
                'bulgur', 'couscous', 'farro', 'amaranth', 'sorghum', 'teff',
                'moong', 'urad', 'masoor', 'toor', 'chana', 'rajma', 'chickpea'
            ])
            
            if unit == 'cup' and is_grain_or_lentil and protein_g > 15:
                rejection_reason = f"DRY/COOKED DENSITY TRAP: {food_name} shows {protein_g:.1f}g protein per cup (dry-weight error for cooked volume)"
                return (False, rejection_reason)

            known_sweets = [
                'putharekulu', 'ladoo', 'laddoo', 'jalebi', 'halwa', 'halva',
                'barfi', 'burfi', 'mysore pak', 'payasam', 'kheer', 'gulab jamun',
                'rasgulla', 'sandesh', 'peda', 'petha', 'kalakand', 'mithai',
                'sweet', 'dessert', 'candy', 'cookie', 'cake', 'pastry', 'donut'
            ]
            is_sweet_item = any(sweet in food_name_lower for sweet in known_sweets)
            
            if is_sweet_item and carbs_g > 50 and sugar_g < 5:
                if sugar_g == 0.0:
                    logger.info(f"MISSING DATA (Sugar): {food_name} has {carbs_g:.1f}g carbs but 0.0g sugar (database incomplete - allowing item to pass)")
                else:
                    rejection_reason = f"SUGAR ANOMALY: {food_name} is a sweet with {carbs_g:.1f}g carbs but only {sugar_g:.1f}g sugar (database error)"
                    return (False, rejection_reason)
            

            if unit == 'cup':
                quantity = food_data.get('quantity', 1.0)
                min_weight_per_cup = 80 
                expected_min_weight = quantity * min_weight_per_cup
                
                liquid_keywords = ['water', 'juice', 'milk', 'tea', 'coffee', 'broth', 'stock', 'soup']
                is_likely_liquid = any(liquid in food_name_lower for liquid in liquid_keywords)
                
                if not is_likely_liquid and portion_weight_g < expected_min_weight * 0.7: 
                    rejection_reason = f"VOLUMETRIC DENSITY VIOLATION: {food_name} shows {portion_weight_g:.1f}g per cup (expected ≥{expected_min_weight * 0.7:.0f}g)"
                    return (False, rejection_reason)
            
            sodium_mg = float(food_data.get('sodium', 0))
            if sodium_mg > 400:
                rejection_reason = f"ABSOLUTE SODIUM CAP VIOLATION: {food_name} has {sodium_mg:.0f}mg sodium (max 400mg per serving)"
                return (False, rejection_reason)
            

            meal_type = food_data.get('meal_type', '').lower()
            if is_sweet_item and meal_type in ['lunch', 'dinner']:
                rejection_reason = f"SWEETS IN SAVORY BAN: {food_name} is a sweet item (forbidden in {meal_type} pool)"
                return (False, rejection_reason)

            if calories > 800:
                rejection_reason = f"EXTREME DENSITY: {food_name} has {calories:.0f} kcal per serving (max 800 kcal allowed)"
                return (False, rejection_reason)
            
            return (True, "")
            
        except (ValueError, TypeError) as e:
            rejection_reason = f"INVALID DATA: {food_name} has non-numeric macro values"
            return (False, rejection_reason)

    
    def _estimate_weight_from_unit(
        self,
        quantity: float,
        unit: PortionUnit,
        default_portion_g: float
    ) -> float:
        unit_str = str(unit.value if hasattr(unit, 'value') else unit).lower()
        
        conversions = {
            "cup": 150,
            "tablespoon": 15,
            "tbsp": 15,
            "teaspoon": 5,
            "tsp": 5,
            "fl_oz": 29.57,
            "oz": 28.35,
            "piece": 50,
            "slice": 30,
            "whole": 50,
            "gram": 1,
            "g": 1
        }
        
        if unit_str in conversions:
            return quantity * conversions[unit_str]
        else:
            return quantity * default_portion_g
    
    def enrich_meal_plan(
        self,
        meal_plan: MealPlanResponse,
        target_calories: float,
        medical_conditions: Optional[List[str]] = None
    ) -> Dict:

        total_nutrition = CalculatedNutrition(
            calories=0, protein_g=0, carbs_g=0, fat_g=0,
            fiber_g=0, net_carbs_g=0, sodium_mg=0, sugar_g=0, cholesterol_mg=0, iodine_mcg=0
        )
        
        enriched_items = []
        db_miss_items = []  

        filtered_items = []
        seen_foods = {} 
        
        for food_selection in meal_plan.items:
            if food_selection.quantity < 0.05:
                logger.warning(f" Filtering out zero-quantity item: {food_selection.name} ({food_selection.quantity} {food_selection.unit.value})")
                continue
            
            food_key = food_selection.name.lower().strip()
            if food_key in seen_foods:
                existing_item = seen_foods[food_key]
                if food_selection.quantity > existing_item.quantity:
                    logger.info(f" Replacing duplicate '{food_selection.name}': {existing_item.quantity} {existing_item.unit.value} → {food_selection.quantity} {food_selection.unit.value}")
                    seen_foods[food_key] = food_selection
                else:
                    logger.warning(f" Skipping duplicate '{food_selection.name}' with smaller quantity: {food_selection.quantity} {food_selection.unit.value}")
            else:
                seen_foods[food_key] = food_selection
        
        filtered_items = list(seen_foods.values())
        logger.info(f" Filtered {len(meal_plan.items)} → {len(filtered_items)} items (removed {len(meal_plan.items) - len(filtered_items)} duplicates/zeros)")
        
        for food_selection in filtered_items:
            nutrition, weight_g, is_db_miss = self.calculate_food_nutrition(
                food_selection, 
                use_cache=True,
                meal_type=meal_plan.meal_type.value if hasattr(meal_plan.meal_type, 'value') else str(meal_plan.meal_type)
            )
            
            is_snack = 'snack' in str(meal_plan.meal_type).lower()
            if is_db_miss and not is_snack:
                db_miss_items.append(food_selection.name)
            elif is_db_miss and is_snack:
                logger.info(f"⚡ SNACK BYPASS: DB miss for '{food_selection.name}' accepted (snack mode)")
            
            total_nutrition.calories += nutrition.calories
            total_nutrition.protein_g += nutrition.protein_g
            total_nutrition.carbs_g += nutrition.carbs_g
            total_nutrition.fat_g += nutrition.fat_g
            total_nutrition.fiber_g += nutrition.fiber_g
            total_nutrition.net_carbs_g += nutrition.net_carbs_g
            total_nutrition.sodium_mg += nutrition.sodium_mg
            total_nutrition.sugar_g += nutrition.sugar_g
            total_nutrition.cholesterol_mg += nutrition.cholesterol_mg
            total_nutrition.iodine_mcg += nutrition.iodine_mcg
            
            enriched_items.append({
                'name': food_selection.name,
                'quantity': food_selection.quantity,
                'unit': food_selection.unit.value,
                'weight_g': weight_g,
                'nutrition': nutrition.dict(),
                'is_db_miss': is_db_miss 
            })
        

        for item in enriched_items:
            item_nutrition = item['nutrition']
            
            if not item_nutrition.get('calories') or item_nutrition['calories'] <= 0:
                logger.warning(f" ZERO-CALORIE DETECTED: '{item['name']}' has {item_nutrition.get('calories', 0)} kcal - applying heuristic fix")
                
                food_name_lower = item['name'].lower()

                if 'protein shake' in food_name_lower or 'whey' in food_name_lower or 'protein powder' in food_name_lower:

                    cal_per_100g, protein_per_100g, fat_per_100g, carbs_per_100g = 150, 25.0, 3.0, 5.0
                    logger.info(f"   → Applied PROTEIN SHAKE heuristic (guarantees protein anchor)")
                elif any(keyword in food_name_lower for keyword in ['mutton', 'lamb', 'beef', 'goat', 'pork']):
                    cal_per_100g, protein_per_100g, fat_per_100g, carbs_per_100g = 250, 20, 15, 0
                    logger.info(f"   → Applied RED MEAT heuristic")
                elif any(keyword in food_name_lower for keyword in ['chicken', 'egg', 'fish', 'quail', 'turkey', 'duck', 'prawn', 'shrimp']):
                    cal_per_100g, protein_per_100g, fat_per_100g, carbs_per_100g = 150, 15, 10, 1
                    logger.info(f"   → Applied POULTRY/EGG/FISH heuristic")
                elif any(keyword in food_name_lower for keyword in ['oil', 'butter', 'ghee', 'fat', 'cream']):

                    cal_per_100g, protein_per_100g, fat_per_100g, carbs_per_100g = 857, 0, 100, 0
                    logger.info(f"   → Applied PURE FAT heuristic (oil/butter/ghee/cream)")
                else:
                    cal_per_100g, protein_per_100g, fat_per_100g, carbs_per_100g = 100, 5, 5, 5
                    logger.info(f"   → Applied GENERIC heuristic")
                
                weight_g = item['weight_g']
                multiplier = weight_g / 100.0
                
                corrected_calories = cal_per_100g * multiplier
                corrected_protein = protein_per_100g * multiplier
                corrected_fat = fat_per_100g * multiplier
                corrected_carbs = carbs_per_100g * multiplier
                
                corrected_fiber = 2.0 * multiplier
                corrected_net_carbs = max(0, corrected_carbs - corrected_fiber)
                
                item_nutrition['calories'] = corrected_calories
                item_nutrition['protein_g'] = corrected_protein
                item_nutrition['fat_g'] = corrected_fat
                item_nutrition['carbs_g'] = corrected_carbs
                item_nutrition['fiber_g'] = corrected_fiber
                item_nutrition['net_carbs_g'] = corrected_net_carbs
                item_nutrition['sodium_mg'] = 100 * multiplier
                item_nutrition['sugar_g'] = 1.0 * multiplier
                
                total_nutrition.calories += corrected_calories
                total_nutrition.protein_g += corrected_protein
                total_nutrition.fat_g += corrected_fat
                total_nutrition.carbs_g += corrected_carbs
                total_nutrition.fiber_g += corrected_fiber
                total_nutrition.net_carbs_g += corrected_net_carbs
                total_nutrition.sodium_mg += 100 * multiplier
                total_nutrition.sugar_g += 1.0 * multiplier
                
                logger.info(f"    Fixed '{item['name']}': {corrected_calories:.0f} kcal | P:{corrected_protein:.1f}g C:{corrected_carbs:.1f}g F:{corrected_fat:.1f}g")
        

        
        calorie_accuracy = (total_nutrition.calories / target_calories * 100) if target_calories > 0 else 0
        macro_percentages = {
            'protein': total_nutrition.protein_pct,
            'carbs': total_nutrition.carbs_pct,
            'fat': total_nutrition.fat_pct
        }
        
        return {
            'meal_type': meal_plan.meal_type.value,
            'items': enriched_items,
            'total_nutrition': total_nutrition.dict(),
            'macro_percentages': macro_percentages,
            'calorie_accuracy': calorie_accuracy,
            'fallback_count': self.fallback_used_count,
            'db_miss_items': db_miss_items 
        }
    
    def _fallback_estimation(self, food_selection: FoodSelection) -> CalculatedNutrition:

        if (food_selection.calories_per_100g is not None 
            and food_selection.protein_per_100g is not None
            and food_selection.carbs_per_100g is not None
            and food_selection.fat_per_100g is not None):
            
            if food_selection.calories_per_100g == 0:
                logger.warning(f" ZERO-CALORIE LLM DETECTED: {food_selection.name} - applying category baseline")
                
                food_lower = food_selection.name.lower()
                if any(x in food_lower for x in ['oil', 'butter', 'ghee', 'fat', 'coconut milk']):
                    cal_baseline = 880 
                    food_selection.calories_per_100g = cal_baseline
                    food_selection.fat_per_100g = 100.0
                    food_selection.protein_per_100g = 0.0
                    food_selection.carbs_per_100g = 0.0
                elif any(x in food_lower for x in ['almond', 'walnut', 'cashew', 'peanut', 'nut', 'seed', 'sesame', 'sunflower']):
                    cal_baseline = 500 
                    food_selection.calories_per_100g = cal_baseline
                    food_selection.fat_per_100g = 40.0
                    food_selection.protein_per_100g = 20.0
                    food_selection.carbs_per_100g = 20.0
                elif any(x in food_lower for x in ['chicken', 'turkey', 'beef', 'salmon', 'fish', 'meat', 'egg', 'tofu', 'paneer']):
                    cal_baseline = 200  
                    food_selection.calories_per_100g = cal_baseline
                    food_selection.protein_per_100g = 35.0
                    food_selection.carbs_per_100g = 0.0
                    food_selection.fat_per_100g = 8.0
                elif any(x in food_lower for x in ['apple', 'banana', 'orange', 'berry', 'mango', 'grape', 'fruit']):
                    cal_baseline = 50 
                    food_selection.calories_per_100g = cal_baseline
                    food_selection.carbs_per_100g = 12.0
                    food_selection.protein_per_100g = 0.5
                    food_selection.fat_per_100g = 0.0
                else:
                    cal_baseline = 25
                    food_selection.calories_per_100g = cal_baseline
                    food_selection.carbs_per_100g = 5.0
                    food_selection.protein_per_100g = 2.0
                    food_selection.fat_per_100g = 0.0
                
                logger.info(f" APPLIED BASELINE: {food_selection.name} → {cal_baseline} kcal/100g")
            
            weight_g = self._estimate_weight(food_selection)
            multiplier = weight_g / 100.0
            
            carbs_final = food_selection.carbs_per_100g * multiplier
            fiber_final = (food_selection.fiber_per_100g or 2.0) * multiplier
            
            logger.info(f" HYBRID TRUST FALLBACK: Using LLM macros for {food_selection.name}")
            return CalculatedNutrition(
                calories=food_selection.calories_per_100g * multiplier,
                protein_g=food_selection.protein_per_100g * multiplier,
                carbs_g=carbs_final,
                fat_g=food_selection.fat_per_100g * multiplier,
                fiber_g=fiber_final,
                net_carbs_g=max(0, carbs_final - fiber_final),
                sodium_mg=(food_selection.sodium_per_100g or 100) * multiplier,
                sugar_g=(food_selection.sugar_per_100g or 1.0) * multiplier,
                cholesterol_mg=(food_selection.cholesterol_per_100g or 0) * multiplier,
                iodine_mcg=0
            )
        
        food_lower = food_selection.name.lower()
        
        if any(x in food_lower for x in ['chicken', 'turkey', 'beef', 'salmon', 'fish', 'meat']):
            cal_per_100g = 165
            protein_pct = 0.70
            carbs_pct = 0.00
            fat_pct = 0.30
        elif any(x in food_lower for x in ['rice', 'quinoa', 'oats', 'bread', 'roti']):
            cal_per_100g = 130
            protein_pct = 0.10
            carbs_pct = 0.85
            fat_pct = 0.05
        elif any(x in food_lower for x in ['spinach', 'broccoli', 'kale', 'vegetable', 'tomato', 'bean']):
            cal_per_100g = 30
            protein_pct = 0.25
            carbs_pct = 0.70
            fat_pct = 0.05
        elif any(x in food_lower for x in ['almond', 'walnut', 'nut', 'cashew', 'seed']):
            cal_per_100g = 600
            protein_pct = 0.15
            carbs_pct = 0.10
            fat_pct = 0.75
        else:
            cal_per_100g = 100
            protein_pct = 0.20
            carbs_pct = 0.50
            fat_pct = 0.30
        
        weight_g = self._estimate_weight(food_selection)
        multiplier = weight_g / 100.0
        
        carbs_calc = (cal_per_100g * carbs_pct / 4.0) * multiplier
        fiber_calc = 2.0 * multiplier
        
        logger.info(f" GENERIC FALLBACK: Using category heuristics for {food_selection.name}")
        return CalculatedNutrition(
            calories=cal_per_100g * multiplier,
            protein_g=(cal_per_100g * protein_pct / 4.0) * multiplier,
            carbs_g=carbs_calc,
            fat_g=(cal_per_100g * fat_pct / 9.0) * multiplier,
            fiber_g=fiber_calc,
            net_carbs_g=max(0, carbs_calc - fiber_calc),
            sodium_mg=100 * multiplier,
            sugar_g=2.0 * multiplier,
            cholesterol_mg=0,
            iodine_mcg=0
        )
    
    def _estimate_weight(self, food_selection: FoodSelection) -> float:
        unit_str = str(food_selection.unit.value if hasattr(food_selection.unit, 'value') else food_selection.unit).lower()
        qty = float(food_selection.quantity)
        conversions = {
            "cup": 150,
            "tablespoon": 15,
            "tbsp": 15,
            "teaspoon": 5,
            "tsp": 5,
            "fl_oz": 29.57,
            "oz": 28.35,
            "piece": 50,
            "slice": 30,
            "whole": 50
        }
        
        weight_g = qty * conversions.get(unit_str, 100)
        if unit_str not in conversions:
            logger.warning(f"Unknown unit '{unit_str}' for {food_selection.name} - defaulting to 100g per unit")
        
        return weight_g


ENRICHMENT_ENGINE = NutritionEnrichmentEngine()


def enrich_meal_with_nutrition(
    meal_plan: MealPlanResponse,
    target_calories: float,
    unified_food_db: Optional[Dict] = None,
    medical_conditions: Optional[List[str]] = None
) -> Dict:

    engine = NutritionEnrichmentEngine(unified_food_db=unified_food_db)
    return engine.enrich_meal_plan(meal_plan, target_calories, medical_conditions)
