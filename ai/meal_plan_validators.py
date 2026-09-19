import logging
import re
from typing import Dict, List, Tuple, Set, Optional

logger = logging.getLogger(__name__)


class MealPlanValidator:

    HARD_BLOCKLIST = [
        'corn', 'maize', 'corn tortilla', 'cornmeal', 'cornstarch', 'corn syrup',
        'high fructose corn syrup', 'popcorn', 'corn chips', 'cornbread',
        'cabbage', 'onion', 'onions', 'garlic', 'leek', 'shallot',
        'beans', 'lentils', 'chickpeas', 'legumes', 
        'broccoli', 'cauliflower', 'brussels sprouts', 
    ]
    
    PRIMARY_INGREDIENTS = {
        'tofu', 'spinach', 'meatless meatball', 'bacon', 'tempeh',
        'seitan', 'chicken breast', 'salmon', 'shrimp', 'beef',
        'pork', 'lamb', 'turkey', 'duck', 'tuna', 'cod',
        'lentils', 'chickpeas', 'black beans', 'kidney beans',
        'quinoa', 'brown rice', 'white rice', 'pasta', 'noodles'
    }
    
    HIGH_FIBER_SNACKS = {
        'almonds', 'walnuts', 'cashews', 'peanuts', 'pistachios',
        'chia seeds', 'flax seeds', 'pumpkin seeds', 'sunflower seeds',
        'apple', 'banana', 'orange', 'berries', 'strawberries', 'blueberries',
        'carrots', 'celery', 'cucumber', 'bell pepper', 'cherry tomatoes',
        'hummus', 'edamame', 'roasted chickpeas'
    }
    
    BREAKFAST_MAX_CALORIES = 600 
    SNACK_MAX_CALORIES = 200
    SNACK_MIN_FIBER = 3 
    
    LOW_GLYCEMIC_CARBS = {
        'quinoa', 'brown rice', 'sweet potato', 'yam', 'oats', 'barley',
        'bulgur', 'farro', 'wild rice', 'steel cut oats', 'chickpeas',
        'lentils', 'black beans', 'kidney beans'
    }
    
    REFINED_CARBS = {
        'white rice', 'white bread', 'pasta', 'noodles', 'white flour',
        'bagel', 'croissant', 'muffin', 'cake', 'cookies', 'crackers',
        'chips', 'pretzels', 'cereal'
    }
    
    def __init__(self):
        self.daily_used_ingredients: Set[str] = set()
        self.validation_errors: List[str] = []
        
    def validate_meal_plan(
        self, 
        meal_plan_json: Dict,
        constraints: Dict
    ) -> Tuple[bool, List[str]]:

        self.validation_errors = []
        self.daily_used_ingredients = set()
        
        self._validate_ingredient_variety(meal_plan_json)
        self._validate_nutritional_accuracy(meal_plan_json, constraints)
        self._validate_meal_cohesion(meal_plan_json, constraints)
        self._validate_hard_blocklist(meal_plan_json, constraints)
        
        is_valid = len(self.validation_errors) == 0
        
        if not is_valid:
            logger.warning(f" MEAL PLAN VALIDATION FAILED: {len(self.validation_errors)} errors found")
            for error in self.validation_errors:
                logger.warning(f"   - {error}")
        else:
            logger.info(f" MEAL PLAN VALIDATION PASSED: All rules satisfied")
        
        return is_valid, self.validation_errors
    
    def _validate_ingredient_variety(self, meal_plan_json: Dict):
        ingredient_counts = {}
        
        for meal_name, items in meal_plan_json.get('meal_plan', {}).items():
            for item in items:
                food_name = item.get('name', '').lower()
                
                for primary_ingredient in self.PRIMARY_INGREDIENTS:
                    if primary_ingredient in food_name:
                        ingredient_counts[primary_ingredient] = ingredient_counts.get(primary_ingredient, 0) + 1
                        
                        if ingredient_counts[primary_ingredient] > 1:
                            self.validation_errors.append(
                                f" VARIETY VIOLATION: '{primary_ingredient}' appears "
                                f"{ingredient_counts[primary_ingredient]}x in one day (max: 1)"
                            )
        
        self.daily_used_ingredients = set(ingredient_counts.keys())
        logger.info(f" INGREDIENT VARIETY: Tracked {len(self.daily_used_ingredients)} primary ingredients")
    
    def _validate_nutritional_accuracy(self, meal_plan_json: Dict, constraints: Dict):
       
        dietary_pref = constraints.get('dietary_preference', '')
        
        if isinstance(dietary_pref, list):
            dietary_pref = ', '.join(dietary_pref) if dietary_pref else ''
        
        dietary_pref_lower = dietary_pref.lower() if isinstance(dietary_pref, str) else ''
        is_vegan = 'vegan' in dietary_pref_lower and 'non' not in dietary_pref_lower
        is_vegetarian = ('vegetarian' in dietary_pref_lower and 'non' not in dietary_pref_lower) or is_vegan
        
        for meal_name, items in meal_plan_json.get('meal_plan', {}).items():
            for item in items:
                food_name = item.get('name', '').lower()
                nutrients = item.get('nutrients', {})
                
                cholesterol = nutrients.get('Cholesterol', 0)
                sodium = nutrients.get('Sodium', 0)
                carbs = nutrients.get('Carbs', 0)
                fat = nutrients.get('Fat', 0)
                
                plant_based_indicators = ['tofu', 'tempeh', 'seitan', 'lentil', 'chickpea', 
                                           'bean', 'quinoa', 'vegetable', 'spinach', 'kale',
                                           'broccoli', 'vegan', 'plant-based']
                
                is_plant_based = any(indicator in food_name for indicator in plant_based_indicators)
                
                if (is_vegan or is_vegetarian or is_plant_based) and cholesterol > 0:
                    self.validation_errors.append(
                        f" NUTRITION ERROR: '{item.get('name')}' is plant-based but shows "
                        f"{cholesterol}mg cholesterol (must be 0)"
                    )
                
                is_raw_unsalted = 'raw' in food_name or 'unsalted' in food_name or 'no salt' in food_name
                is_processed = any(word in food_name for word in ['hash brown', 'burger', 'patty', 
                                                                    'meatball', 'sausage', 'bacon',
                                                                    'cooked', 'fried', 'baked'])
                
                if is_processed and not is_raw_unsalted and sodium == 0:
                    self.validation_errors.append(
                        f" NUTRITION ERROR: '{item.get('name')}' is processed/cooked but shows "
                        f"0mg sodium (unrealistic)"
                    )
                
                is_starchy = any(word in food_name for word in ['hash brown', 'potato', 'rice', 
                                                                  'pasta', 'bread', 'noodle'])
                
                if is_starchy:
                    if carbs == 0:
                        self.validation_errors.append(
                            f" NUTRITION ERROR: '{item.get('name')}' is starchy but shows 0g carbs"
                        )
                    
                    # Hash browns cooked in oil should have fat
                    if 'hash brown' in food_name and fat == 0:
                        self.validation_errors.append(
                            f" NUTRITION ERROR: '{item.get('name')}' is fried but shows 0g fat"
                        )
        
        logger.info(f" NUTRITIONAL ACCURACY: Validated {sum(len(items) for items in meal_plan_json.get('meal_plan', {}).values())} items")
    
    def _validate_meal_cohesion(self, meal_plan_json: Dict, constraints: Dict):
        
        medical_conditions = constraints.get('medical_conditions', [])
        has_type2_diabetes = any('type 2' in str(cond).lower() and 'diabetes' in str(cond).lower() 
                                  for cond in medical_conditions)
        
        for meal_name, items in meal_plan_json.get('meal_plan', {}).items():
            meal_name_lower = meal_name.lower()
            meal_calories = sum(item.get('nutrients', {}).get('Calories', 0) for item in items)
            
            if 'breakfast' in meal_name_lower:
                if meal_calories > self.BREAKFAST_MAX_CALORIES:
                    self.validation_errors.append(
                        f" BREAKFAST ERROR: {meal_calories:.0f} kcal exceeds max "
                        f"{self.BREAKFAST_MAX_CALORIES} kcal (too heavy)"
                    )
                
                for item in items:
                    fat = item.get('nutrients', {}).get('Fat', 0)
                    if fat > 20:  
                        self.validation_errors.append(
                            f" BREAKFAST ERROR: '{item.get('name')}' has {fat}g fat "
                            f"(too heavy for breakfast)"
                        )
            
            if 'snack' in meal_name_lower:
                if meal_calories > self.SNACK_MAX_CALORIES:
                    self.validation_errors.append(
                        f" SNACK ERROR: {meal_calories:.0f} kcal exceeds max "
                        f"{self.SNACK_MAX_CALORIES} kcal"
                    )
                
                for item in items:
                    food_name = item.get('name', '').lower()
                    heavy_proteins = ['meatball', 'bacon', 'steak', 'burger', 'patty',
                                       'chicken breast', 'salmon', 'tuna', 'beef', 'pork']
                    
                    if any(protein in food_name for protein in heavy_proteins):
                        self.validation_errors.append(
                            f" SNACK ERROR: '{item.get('name')}' is a heavy protein "
                            f"(snacks should be high-fiber: fruits, nuts, seeds, raw veggies)"
                        )
                    
                    fiber = item.get('nutrients', {}).get('Fiber', 0)
                    if fiber < self.SNACK_MIN_FIBER:
                        is_approved = any(snack in food_name for snack in self.HIGH_FIBER_SNACKS)
                        if not is_approved:
                            self.validation_errors.append(
                                f" SNACK ERROR: '{item.get('name')}' has only {fiber}g fiber "
                                f"(min: {self.SNACK_MIN_FIBER}g). Use fruits, nuts, seeds, or raw veggies."
                            )
            
            if 'dinner' in meal_name_lower and has_type2_diabetes:
                has_lean_protein = False
                has_complex_carb = False
                has_refined_carb = False
                has_high_fat_meat = False
                
                for item in items:
                    food_name = item.get('name', '').lower()
                    
                    lean_proteins = ['chicken breast', 'turkey', 'fish', 'salmon', 'tuna',
                                      'cod', 'tilapia', 'tofu', 'tempeh', 'lentil', 'chickpea']
                    if any(protein in food_name for protein in lean_proteins):
                        has_lean_protein = True
                    
                    high_fat_meats = ['bacon', 'beef', 'pork', 'sausage', 'ground beef',
                                       'steak', 'ribs', 'lamb']
                    if any(meat in food_name for meat in high_fat_meats):
                        has_high_fat_meat = True
                        self.validation_errors.append(
                            f" DIABETES DINNER ERROR: '{item.get('name')}' is high-fat meat "
                            f"(Type 2 Diabetes requires lean proteins)"
                        )
                    
                    if any(carb in food_name for carb in self.LOW_GLYCEMIC_CARBS):
                        has_complex_carb = True
                    
                    if any(carb in food_name for carb in self.REFINED_CARBS):
                        has_refined_carb = True
                        self.validation_errors.append(
                            f" DIABETES DINNER ERROR: '{item.get('name')}' contains refined carbs "
                            f"(Type 2 Diabetes requires low-glycemic carbs like quinoa, brown rice, sweet potato)"
                        )
                
                if not has_lean_protein:
                    self.validation_errors.append(
                        f" DIABETES DINNER ERROR: Missing lean protein source "
                        f"(add chicken breast, fish, tofu, or legumes)"
                    )
                
                if not has_complex_carb and not has_refined_carb:
                    self.validation_errors.append(
                        f" DIABETES DINNER ERROR: Missing complex carbohydrate "
                        f"(add quinoa, brown rice, sweet potato, or legumes)"
                    )
        
        logger.info(f" MEAL COHESION: Validated {len(meal_plan_json.get('meal_plan', {}))} meals")
    
    def _validate_hard_blocklist(self, meal_plan_json: Dict, constraints: Dict):

        allergies = constraints.get('allergies', [])
        avoid_foods = constraints.get('avoid_foods', [])
        digestive_issues = constraints.get('digestive_issues', [])
        
        blocklist = set(self.HARD_BLOCKLIST)
        
        for allergy in allergies:
            blocklist.add(allergy.lower())
        
        for food in avoid_foods:
            if isinstance(food, str):
                blocklist.add(food.lower())
        
        if digestive_issues:
            bloating_triggers = ['cabbage', 'onion', 'garlic', 'beans', 'lentils',
                                  'broccoli', 'cauliflower', 'brussels sprouts',
                                  'carbonated drink', 'soda', 'beer']
            blocklist.update(bloating_triggers)
        
        for meal_name, items in meal_plan_json.get('meal_plan', {}).items():
            for item in items:
                food_name = item.get('name', '').lower()
                
                for blocked_ingredient in blocklist:
                    if blocked_ingredient in food_name:
                        self.validation_errors.append(
                            f" BLOCKLIST VIOLATION: '{item.get('name')}' contains blocked ingredient "
                            f"'{blocked_ingredient}' (user avoid list)"
                        )
        
        logger.info(f" BLOCKLIST: Validated {len(blocklist)} blocked ingredients")


def validate_meal_plan_comprehensive(
    meal_plan_json: Dict,
    constraints: Dict
) -> Tuple[bool, List[str]]:

    validator = MealPlanValidator()
    return validator.validate_meal_plan(meal_plan_json, constraints)
