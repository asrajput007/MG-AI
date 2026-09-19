import logging
import random
import re
from typing import Dict, List, Optional, Set, Tuple
from collections import defaultdict

try:
    from ai.food_group_config import UNIVERSAL_SLOTS
except ImportError:
    UNIVERSAL_SLOTS = None

try:
    from ai.serving_validation import (
        validate_minimum_servings, 
        log_serving_analysis,
        is_vegetable_serving,
        is_fruit_serving
    )
except ImportError:
    validate_minimum_servings = None
    log_serving_analysis = None
    is_vegetable_serving = None
    is_fruit_serving = None

logger = logging.getLogger(__name__)


class EnhancedMealGenerator:

    PREFERRED_FOOD_GROUPS = None  
    
    MEAL_ALLOCATIONS = {
        'breakfast': 0.25,
        'morning_snack': 0.05,
        'lunch': 0.35,
        'evening_snack': 0.05,
        'dinner': 0.30
    }
    
    def __init__(self):
        if UNIVERSAL_SLOTS:
            self.PREFERRED_FOOD_GROUPS = {
                meal: set(groups) for meal, groups in UNIVERSAL_SLOTS.items()
            }
        else:
            self.PREFERRED_FOOD_GROUPS = {
                'breakfast': {
                    'starch_base', 'protein_source', 'fruit', 'fat_source', 
                    'dairy_or_alt', 'condiment_side', 'beverage'
                },
                'morning_snack': {
                    'fruit', 'nut_seed', 'dairy_or_alt', 'hydration'
                },
                'lunch': {
                    'grain_base', 'protein_main', 'primary_vegetable', 'soup_or_broth', 
                    'condiment', 'fat_source', 'hydration'
                },
                'evening_snack': {
                    'savory_light', 'protein_snack', 'fruit_snack', 'nut_seed', 
                    'energy_source', 'warm_beverage', 'hydration'
                },
                'dinner': {
                    'light_grain', 'lean_protein', 'cooked_vegetable', 'soup_or_broth', 
                    'fermented_side', 'fat_source', 'hydration'
                }
            }
        
        self.daily_used_foods = set()
        self.cuisine_tracker = {'pref': 0, 'global': 0}
        self.diet_tracker = {'non_veg': 0, 'other': 0}
    
    def calculate_meal_targets(
        self, 
        total_tdee: int, 
        macro_ranges: Dict[str, Tuple[int, int]]
    ) -> Dict[str, Dict]:
        """
        Calculate calorie and macro targets for each meal.
        
        Args:
            total_tdee: Total daily energy expenditure in kcal
            macro_ranges: Dict with 'protein', 'carbs', 'fat' as keys,
                         (min%, max%) tuples as values
        
        Returns:
            Dict mapping meal names to target dicts with:
                - calories: target calories
                - protein: (min_g, max_g)
                - carbs: (min_g, max_g)
                - fat: (min_g, max_g)
        """
        meal_targets = {}
        
        for meal_name, percentage in self.MEAL_ALLOCATIONS.items():
            meal_calories = total_tdee * percentage
            
            meal_targets[meal_name] = {
                'calories': meal_calories,
                'protein': (
                    (meal_calories * (macro_ranges['protein'][0] / 100)) / 4,
                    (meal_calories * (macro_ranges['protein'][1] / 100)) / 4
                ),
                'carbs': (
                    (meal_calories * (macro_ranges['carbs'][0] / 100)) / 4,
                    (meal_calories * (macro_ranges['carbs'][1] / 100)) / 4
                ),
                'fat': (
                    (meal_calories * (macro_ranges['fat'][0] / 100)) / 9,
                    (meal_calories * (macro_ranges['fat'][1] / 100)) / 9
                )
            }
        
        return meal_targets
    
    def _get_food_portions(self, food_item: Dict) -> List[Dict]:
        """
        Extract up to 5 portion options from food item.
        
        Args:
            food_item: Dict with portion_1_grams, portion_1_label, etc.
        
        Returns:
            List of dicts with 'grams' and 'label' keys
        """
        portions = []
        
        for i in range(1, 6):
            grams_key = f'portion_{i}_grams'
            label_key = f'portion_{i}_label'
            
            if grams_key in food_item:
                try:
                    grams = float(food_item[grams_key])
                    if grams > 0:
                        label = food_item.get(label_key, f"{grams}g")
                        if not label or str(label).lower() in ['nan', 'none', '']:
                            label = f"{grams}g"
                        portions.append({'grams': grams, 'label': str(label)})
                except (TypeError, ValueError):
                    continue
        
        if not portions:
            portions.append({'grams': 100.0, 'label': '100g'})
        
        return portions
    
    def _get_nutrient_per_gram(self, food_item: Dict, nutrient: str) -> float:

        per_g_key = f'{nutrient}_per_g'
        if per_g_key in food_item:
            try:
                return float(food_item[per_g_key])
            except (TypeError, ValueError):
                pass
        
        per_100g_key = f'{nutrient}_per_100g'
        if per_100g_key in food_item:
            try:
                return float(food_item[per_100g_key]) / 100.0
            except (TypeError, ValueError):
                pass
        
        if nutrient in food_item:
            try:
                value = float(food_item[nutrient])
                portion_grams = float(food_item.get('portion_1_grams', 100))
                return value / portion_grams if portion_grams > 0 else 0
            except (TypeError, ValueError):
                pass
        
        return 0.0
    
    def adjust_portions_discrete(
        self, 
        combination: List[Dict], 
        targets: Dict
    ) -> List[Dict]:
        
        if not combination:
            return []
        
        item_portions = [self._get_food_portions(item) for item in combination]
        
        current_selection = [0] * len(combination)
        
        def calculate_totals(selection: List[int]) -> Dict[str, float]:
            totals = {'calories': 0, 'protein': 0, 'carbs': 0, 'fat': 0}
            
            for i, idx in enumerate(selection):
                grams = item_portions[i][idx]['grams']
                totals['calories'] += self._get_nutrient_per_gram(combination[i], 'Calories') * grams
                totals['protein'] += self._get_nutrient_per_gram(combination[i], 'Protein_g') * grams
                totals['carbs'] += self._get_nutrient_per_gram(combination[i], 'Carbs_g') * grams
                totals['fat'] += self._get_nutrient_per_gram(combination[i], 'Fat_g') * grams
            
            return totals
        
        def calculate_error(selection: List[int]) -> float:
            totals = calculate_totals(selection)
            errors = 0
            
            for nutrient in ['protein', 'carbs', 'fat']:
                min_t, max_t = targets[nutrient]
                if totals[nutrient] < min_t:
                    errors += ((totals[nutrient] - min_t) ** 2) * 0.17
                elif totals[nutrient] > max_t:
                    errors += ((totals[nutrient] - max_t) ** 2) * 0.17
            
            errors += ((totals['calories'] - targets['calories']) ** 2) * 0.5
            
            return errors
        
        best_error = calculate_error(current_selection)
        improved = True
        
        while improved:
            improved = False
            best_new_selection = list(current_selection)
            
            for i in range(len(combination)):
                for p_idx in range(len(item_portions[i])):
                    if p_idx == current_selection[i]:
                        continue
                    
                    test_selection = list(current_selection)
                    test_selection[i] = p_idx
                    test_error = calculate_error(test_selection)
                    
                    if test_error < best_error:
                        best_error = test_error
                        best_new_selection = list(test_selection)
                        improved = True
            
            if improved:
                current_selection = best_new_selection
        
        final_totals = calculate_totals(current_selection)
        current_cals = final_totals['calories']
        adjustment_ratio = 1.0
        
        if current_cals > 0:
            desired_ratio = targets['calories'] / current_cals
            adjustment_ratio = max(0.95, min(1.05, desired_ratio))
        
        final_meal = []
        nutrients_to_update = [
            'Calories', 'Protein_g', 'Carbs_g', 'Fat_g', 'Fiber_g', 
            'Sugar', 'Sodium', 'Cholesterol', 'Iodine'
        ]
        
        for i, idx in enumerate(current_selection):
            item = combination[i].copy()
            selected_portion = item_portions[i][idx]
            
            base_grams = selected_portion['grams']
            adjusted_grams = round(base_grams * adjustment_ratio)
            
            item['Adjusted_Portion_g'] = adjusted_grams
            item['Serving_Size'] = selected_portion['label']
            
            for nutrient in nutrients_to_update:
                per_g = self._get_nutrient_per_gram(item, nutrient)
                item[nutrient] = per_g * adjusted_grams
            
            final_meal.append(item)
        
        return final_meal
    
    def _get_item_food_groups(self, food_item: Dict) -> Set[str]:

        raw_groups = food_item.get('Food_Group') or food_item.get('food_group', '')
        
        if not raw_groups or str(raw_groups).lower() in ['nan', 'none', 'null', '']:
            return set()
        
        groups = set()
        for g in re.split(r'\s*[\|,\/;]\s*', str(raw_groups)):
            cleaned = g.strip().lower()
            if cleaned and cleaned not in ['nan', 'none', 'null']:
                groups.add(cleaned)
        
        return groups
    
    def _is_too_similar(self, food_name: str, used_names: List[str]) -> bool:

        words1 = set(re.findall(r'\w+', str(food_name).lower()))
        if not words1:
            return False
        
        for used_name in used_names:
            words2 = set(re.findall(r'\w+', str(used_name).lower()))
            if not words2:
                continue
            
            overlap = len(words1 & words2)
            min_len = min(len(words1), len(words2))
            
            if min_len > 0 and overlap / min_len > 0.5:
                return True
        
        return False
    
    def _calculate_commonality_score(self, food_item: Dict) -> float:

        score = 1.0
        
        meal_tags = food_item.get('meal_tags', [])
        if isinstance(meal_tags, list):
            num_meals = len(meal_tags)
            if num_meals >= 4: 
                score *= 0.7 
            elif num_meals >= 2: 
                score *= 0.85 
            elif num_meals == 1: 
                score *= 1.1 
            else: 
                score *= 1.2 
        
        cuisine = food_item.get('cuisine', [])
        if isinstance(cuisine, list):
            if len(cuisine) == 0 or 'global' in [str(c).lower() for c in cuisine]:
                score *= 0.75 
            elif len(cuisine) >= 3: 
                score *= 0.85 
            elif len(cuisine) == 1: 
                score *= 1.05 
        
        food_name = food_item.get('name', food_item.get('Food_Name', ''))
        name_length = len(str(food_name))
        
        if name_length <= 15: 
            score *= 0.9 
        elif name_length > 40: 
            score *= 1.1 
        
        if '(' in food_name and ')' in food_name:
            score *= 1.05
        
        if food_item.get('is_superfood', False):
            score *= 0.85 
        
        food_groups = self._get_item_food_groups(food_item)
        common_groups = {
            'fruit', 'vegetable', 'grain', 'protein_source', 
            'whole_grain', 'legume', 'nut_seed', 'dairy'
        }
        
        if food_groups & common_groups: 
            score *= 0.9
        
        return max(0.6, min(1.5, score))
    
    def _get_macro_validation_ranges(self, user_profile: Optional[Dict]) -> Dict[str, float]:
        
        if not user_profile:
            return {
                'protein_min': 20, 'protein_max': 25,
                'carbs_min': 40, 'carbs_max': 45,
                'fat_min': 30, 'fat_max': 35
            }
        
        health_conditions = user_profile.get('health_conditions', [])
        if not isinstance(health_conditions, list):
            health_conditions = [health_conditions] if health_conditions else []
        
        conditions_lower = [str(c).lower() for c in health_conditions]
        
        goal = str(user_profile.get('goal', '')).lower()
        
        dietary_pref = str(user_profile.get('dietary_preference', '')).lower()
        
        age = user_profile.get('age', 0)
        gender = str(user_profile.get('gender', '')).lower()
        tdee = user_profile.get('tdee', 2000)
        

        is_weight_loss = goal in ['weight loss', 'weight_loss', 'fat loss', 'fat_loss']
        is_woman_over_50 = gender in ['female', 'woman', 'f'] and age >= 50
        
        if is_weight_loss or is_woman_over_50:

            
            reason = "WEIGHT LOSS" if is_weight_loss else f"WOMEN OVER 50 (age: {age})"
            logger.info(f" Using HIGH PROTEIN ranges ({reason}: target 120-130g protein)")
            return {
                'protein_min': 25, 'protein_max': 35, 
                'carbs_min': 35, 'carbs_max': 45,      
                'fat_min': 25, 'fat_max': 35        
            }
        
        low_carb_conditions = ['diabetes', 'type 2 diabetes', 'type 1 diabetes', 'pcos', 'prediabetes']
        low_carb_goals = ['low carb', 'low-carb', 'keto', 'ketogenic']
        low_carb_prefs = ['low carb', 'low-carb', 'keto', 'ketogenic']
        
        is_low_carb = (
            any(cond in condition for condition in conditions_lower for cond in low_carb_conditions) or
            any(goal_kw in goal for goal_kw in low_carb_goals) or
            any(pref_kw in dietary_pref for pref_kw in low_carb_prefs)
        )
        
        if is_low_carb:
            is_plant_based = dietary_pref in ['vegetarian', 'vegan', 'plant-based']
            
            if is_plant_based:
                logger.info("📋 Using LOW-CARB VEGETARIAN ranges (diabetic vegetarian adjusted)")
                return {
                    'protein_min': 18, 'protein_max': 30,  
                    'carbs_min': 35, 'carbs_max': 50,    
                    'fat_min': 25, 'fat_max': 45         
                }
            else:
                logger.info(" Using LOW-CARB macro ranges (diabetic/PCOS/low-carb goal)")
                return {
                    'protein_min': 20, 'protein_max': 30,
                    'carbs_min': 30, 'carbs_max': 35,
                    'fat_min': 40, 'fat_max': 45
                }
        else:
            logger.info(" Using STANDARD macro ranges (general population)")
            return {
                'protein_min': 20, 'protein_max': 25,
                'carbs_min': 40, 'carbs_max': 45,
                'fat_min': 30, 'fat_max': 35
            }
    
    def _calculate_plan_quality_score(
        self, 
        plan: List[Dict], 
        total_tdee: int,
        selected_cuisine: str,
        validation_ranges: Dict[str, float]
    ) -> float:

        score = 0.0
        
        totals = get_plan_totals(plan)
        cal_diff = abs(totals['calories'] - total_tdee)
        cal_diff_pct = (cal_diff / total_tdee) * 100 if total_tdee > 0 else 100
        
        if cal_diff_pct <= 5:
            score += 25
        elif cal_diff_pct <= 10:
            score += 20
        elif cal_diff_pct <= 15:
            score += 15
        else:
            score += max(0, 10 - cal_diff_pct)
        
        if totals['calories'] > 0:
            protein_cals = totals['protein'] * 4
            carbs_cals = totals['carbs'] * 4
            fat_cals = totals['fat'] * 9
            total_macro_cals = protein_cals + carbs_cals + fat_cals
            
            if total_macro_cals > 0:
                protein_pct = (protein_cals / total_macro_cals) * 100
                carbs_pct = (carbs_cals / total_macro_cals) * 100
                fat_pct = (fat_cals / total_macro_cals) * 100
                
                if validation_ranges['protein_min'] <= protein_pct <= validation_ranges['protein_max']:
                    score += 15
                elif abs(protein_pct - validation_ranges['protein_min']) <= 3 or abs(protein_pct - validation_ranges['protein_max']) <= 3:
                    score += 10
                
                if validation_ranges['carbs_min'] <= carbs_pct <= validation_ranges['carbs_max']:
                    score += 15
                elif abs(carbs_pct - validation_ranges['carbs_max']) <= 3:
                    score += 10
                
                if validation_ranges['fat_min'] <= fat_pct <= validation_ranges['fat_max']:
                    score += 10
                elif abs(fat_pct - validation_ranges['fat_min']) <= 3:
                    score += 7
        
        if selected_cuisine and selected_cuisine.lower() not in ['any', 'all', 'mixed']:
            cuisine_matched = 0
            total_foods = 0
            
            for meal in plan:
                for food in meal.get('foods', []):
                    total_foods += 1
                    if self._matches_cuisine(food, selected_cuisine):
                        cuisine_matched += 1
                    elif 'global' in str(food.get('cuisine', '')).lower():
                        cuisine_matched += 0.5
            
            if total_foods > 0:
                cuisine_match_pct = (cuisine_matched / total_foods) * 100
                score += min(25, cuisine_match_pct / 4) 
        else:
            score += 25 
        
        main_meals = [m for m in plan if m.get('meal_name', '').lower() in ['breakfast', 'lunch', 'dinner']]
        for meal in main_meals:
            has_protein = any(
                self._get_item_food_groups(f) & {'protein_source', 'lean_protein', 'protein_main', 'legume'}
                for f in meal.get('foods', [])
            )
            if has_protein:
                score += 3.33
        
        return round(min(100, score), 1)
    
    def _matches_cuisine(self, food_item: Dict, target_cuisine: str) -> bool:
        food_cuisine = str(food_item.get('cuisine', food_item.get('cuisine_name', ''))).lower()
        target = target_cuisine.lower()
        
        if target in food_cuisine or food_cuisine in target:
            return True
        
        regional_mappings = {
            'central american': ['mexican', 'costa rican', 'guatemalan', 'nicaraguan', 'honduran', 'salvadoran', 'panamanian'],
            'south american': ['brazilian', 'peruvian', 'colombian', 'argentinian', 'chilean', 'venezuelan'],
            'southeast asian': ['thai', 'vietnamese', 'indonesian', 'malaysian', 'filipino'],
            'east asian': ['chinese', 'japanese', 'korean'],
            'middle eastern': ['lebanese', 'turkish', 'moroccan', 'persian', 'iranian', 'egyptian'],
            'mediterranean': ['greek', 'italian', 'spanish', 'french'],
            'african': ['ethiopian', 'nigerian', 'moroccan', 'south african']
        }
        
        for region, cuisines in regional_mappings.items():
            if target in region or region in target:
                if any(c in food_cuisine for c in cuisines):
                    return True
        
        return False
    
    def _food_group_matches(self, item_groups: set, target_groups: set) -> bool:

        if not item_groups or not target_groups:
            return False
        
        if item_groups & target_groups:
            return True
        
        group_aliases = {
            'protein_main': {'protein_source', 'lean_protein', 'protein', 'legume'},
            'lean_protein': {'protein_source', 'protein_main', 'protein'},
            'protein_source': {'protein_main', 'lean_protein', 'protein'},
            'grain_base': {'whole_grain', 'grain', 'starch', 'complex_carb'},
            'light_grain': {'whole_grain', 'grain', 'complex_carb'},
            'starch_base': {'whole_grain', 'grain', 'complex_carb', 'starch'},
            'primary_vegetable': {'vegetable', 'fibrous_veggie', 'veggie'},
            'cooked_vegetable': {'vegetable', 'fibrous_veggie', 'veggie'},
            'soup_or_broth': {'soup', 'broth', 'liquid_meal', 'stew'},
            'dairy_or_alt': {'dairy', 'dairy_alternative', 'plant_milk'},
            'nut_seed': {'nuts', 'seeds', 'nut', 'seed'},
            'fat_source': {'healthy_fat', 'fat', 'oil'},
            'fruit_snack': {'fruit'},
            'savory_light': {'snack', 'light_snack'},
            'energy_source': {'complex_carb', 'grain', 'natural_sugar'},
            'protein_snack': {'protein_source', 'protein', 'nut_seed'}
        }
        
        for target in target_groups:
            aliases = group_aliases.get(target, set())
            if target in item_groups or (aliases & item_groups):
                return True
        
        return False
    
    def _is_appropriate_for_meal(self, food_item: Dict, meal_tag: str, user_profile: Optional[Dict] = None) -> bool:

        food_name = str(food_item.get('name', food_item.get('Food_Name', ''))).lower()
        food_groups = self._get_item_food_groups(food_item)
        meal_tags = food_item.get('meal_tags', [])
        
        is_low_carb_diet = False
        is_vegetarian = False
        is_low_sodium = False
        
        if user_profile:
            health_conditions = user_profile.get('health_conditions', [])
            if not isinstance(health_conditions, list):
                health_conditions = [health_conditions] if health_conditions else []
            conditions_lower = [str(c).lower() for c in health_conditions]
            
            low_carb_conditions = ['diabetes', 'type 2 diabetes', 'type 1 diabetes', 'pcos', 'prediabetes']
            is_low_carb_diet = any(cond in condition for condition in conditions_lower for cond in low_carb_conditions)
            
            dietary_pref = str(user_profile.get('dietary_preference', '')).lower()
            is_vegetarian = dietary_pref in ['vegetarian', 'vegan', 'plant-based']
            food_restrictions = user_profile.get('food_restrictions', [])
            if not isinstance(food_restrictions, list):
                food_restrictions = [food_restrictions] if food_restrictions else []
            restrictions_lower = [str(r).lower() for r in food_restrictions]
            
            low_sodium_conditions = ['hypertension', 'high blood pressure', 'kidney disease', 'heart disease', 
                                    'chronic kidney disease', 'renal disease', 'ckd', 'cardiovascular']
            low_sodium_restrictions = ['low-sodium', 'low sodium', 'no salt', 'sodium-free']
            
            is_low_sodium = (any(cond in condition for condition in conditions_lower for cond in low_sodium_conditions) or
                            any(rest in restriction for restriction in restrictions_lower for rest in low_sodium_restrictions))
        

        if is_low_sodium:
            nutrients = food_item.get('per_100g_nutrients', food_item.get('nutrients', {}))
            sodium_mg = float(nutrients.get('sodium_mg', nutrients.get('Sodium_mg', 0)))
            
            food_groups = self._get_item_food_groups(food_item)
            food_name = str(food_item.get('name', food_item.get('Food_Name', ''))).lower()

            lenient_groups = {'fruit', 'fruit_snack', 'primary_vegetable', 'cooked_vegetable', 
                             'fibrous_veggies', 'grain_base', 'starch_base', 'light_grain'}
            is_lenient_category = bool(food_groups & lenient_groups)
            
            veggie_fruit_keywords = ['vegetable', 'veggie', 'salad', 'greens', 'fruit', 'berries', 
                                     'apple', 'banana', 'orange', 'tomato', 'carrot', 'broccoli',
                                     'spinach', 'lettuce', 'cucumber', 'rice', 'quinoa', 'oats']
            is_veggie_fruit = any(kw in food_name for kw in veggie_fruit_keywords)
            
            if is_lenient_category or is_veggie_fruit:
                MAX_SODIUM = 300  
            else:
                MAX_SODIUM = 200 
            
            if sodium_mg > MAX_SODIUM:
                return False  
        
        if is_low_carb_diet:
            nutrients = food_item.get('per_100g_nutrients', food_item.get('nutrients', {}))
            protein_g = float(nutrients.get('protein_g', nutrients.get('Protein_g', 0)))
            carbs_g = float(nutrients.get('carbs_g', nutrients.get('Carbs_g', 0)))
            fat_g = float(nutrients.get('fat_g', nutrients.get('total_fat_g', nutrients.get('fat', 0))))
            
            prot_carb_ratio = protein_g / carbs_g if carbs_g > 0 else float('inf')
            

            nut_seed_keywords = ['almond', 'walnut', 'cashew', 'peanut', 'pistachio', 
                                'hazelnut', 'pecan', 'macadamia', 'chia', 'hemp', 
                                'pumpkin seed', 'sunflower seed', 'flax']
            is_nut_seed = any(kw in food_name for kw in nut_seed_keywords)
            
            if is_nut_seed and prot_carb_ratio > 0.3:
                pass 
            else:
                sweet_keywords = [
                    'dessert', 'candy', 'candied', 'syrup', 'honey',
                    'jam', 'jelly', 'compote', 'stewed', 'dried',
                    'fruit juice', 'sherbet', 'pudding', 'custard', 'mousse',
                    'cake', 'cookie', 'pastry', 'danish', 'croissant',
                    'almond jelly', 'almond tofu', 'mut man', 'sweetened',  
                    'glazed', 'frosted', 'sugared', 'honeyed'
                ]
                
                if any(kw in food_name for kw in sweet_keywords):
                    return False
                
                high_sugar_keywords = ['grape', 'mango', 'pineapple', 'juice', 'smoothie']
                if any(kw in food_name for kw in high_sugar_keywords):
                    return False
        

        if is_vegetarian and meal_tag in ['breakfast', 'lunch', 'dinner']:
            nutrients = food_item.get('per_100g_nutrients', food_item.get('nutrients', {}))
            protein_g = float(nutrients.get('protein_g', nutrients.get('Protein_g', 0)))
            carbs_g = float(nutrients.get('carbs_g', nutrients.get('Carbs_g', 0)))
            calories = float(nutrients.get('calories_kcal', nutrients.get('calories', 0)))
            
            beverage_groups = {'beverage', 'hydration', 'drink', 'tea', 'coffee'}
            condiment_groups = {'condiment', 'condiment_side', 'spice', 'seasoning'}
            food_groups_set = self._get_item_food_groups(food_item)
            
            if food_groups_set & beverage_groups:
                if 'milk' not in food_name and 'yogurt' not in food_name:
                    return False
            
            if food_groups_set <= condiment_groups:
                return False
            
            condiment_keywords = ['spice blend', 'seasoning', 'za\'atar', 'zaatar', 'dukkah', 
                                 'spice mix', 'salt', 'pepper blend']
            if any(kw in food_name for kw in condiment_keywords):
                if calories < 50:
                    return False
            

            tdee = getattr(self, 'tdee', 2000)  
            
            is_athlete = False
            if user_profile:
                activity_level = str(user_profile.get('activity_level', '')).lower()
                goal = str(user_profile.get('goal', '')).lower()
                is_athlete = activity_level in ['very_active', 'extremely_active'] or goal in ['muscle_gain', 'athletic_performance']
            
            if is_athlete and tdee >= 2600:
                min_protein = 2.2  
            elif tdee < 1900:
                min_protein = 2.3  
            elif tdee < 2600:
                min_protein = 2.0  
            else:
                min_protein = 1.8 
            
            if protein_g < min_protein:
                return False
        
        if is_low_carb_diet:
            nutrients = food_item.get('per_100g_nutrients', food_item.get('nutrients', {}))
            protein_g = float(nutrients.get('protein_g', nutrients.get('Protein_g', 0)))
            carbs_g = float(nutrients.get('carbs_g', nutrients.get('Carbs_g', 0)))
            
            if carbs_g > 0:
                prot_carb_ratio = protein_g / carbs_g
                if prot_carb_ratio < 0.05:
                    return False
        
        if isinstance(meal_tags, list):
            meal_tags_lower = {str(tag).lower() for tag in meal_tags}
        else:
            meal_tags_lower = set()
        
        meal_tag_variations = {
            'breakfast': {'breakfast'},
            'morning_snack': {'morning snack', 'morning_snack', 'snack'},
            'lunch': {'lunch'},
            'evening_snack': {'evening snack', 'evening_snack', 'snack'},
            'dinner': {'dinner'}
        }
        
        supported_tags = meal_tag_variations.get(meal_tag, {meal_tag})
        has_meal_tag = bool(meal_tags_lower & supported_tags)
        
        if meal_tags_lower and not has_meal_tag:
            return False
        
        inappropriate_keywords = [
            'alcohol', 'cocktail', 'beer', 'wine', 'liquor', 'whiskey', 
            'vodka', 'rum', 'gin', 'tequila', 'pisco', 'sake'
        ]
        
        if any(keyword in food_name for keyword in inappropriate_keywords):
            return False
        
        if meal_tag in ['breakfast', 'lunch', 'dinner']:
            raw_ingredient_keywords = [
                'oil', 'paste', 'powder', 'extract', 'flour', 'bran',
                'meal (ground)', 'cocoa bean', 'cocoa paste', 'shiro powder',
                'raw', 'uncooked'
            ]
            
            sweet_beverage_keywords = [
                'sherbet', 'compote', 'pudding', 'custard', 'mousse',
                'smoothie', 'milkshake', 'frappe', 'slush',
                'syrup', 'nectar', 'cordial'
            ]
            
            nutrients = food_item.get('per_100g_nutrients', {})
            if nutrients:
                fat_g = nutrients.get('fat_g', nutrients.get('total_fat_g', nutrients.get('fat', 0)))
                cal = nutrients.get('calories_kcal', nutrients.get('calories', 0))
                if fat_g > 0 and cal > 0:
                    fat_cal = fat_g * 9
                    fat_pct = (fat_cal / cal) * 100 if cal > 0 else 0
                    if fat_pct > 90:  
                        return False
            
            if any(keyword in food_name for keyword in raw_ingredient_keywords):
                return False
            
            if any(keyword in food_name for keyword in sweet_beverage_keywords):
                return False
        
        if meal_tag in ['breakfast', 'lunch', 'dinner']:  
            dessert_keywords = ['ice cream', 'cake', 'candy', 'cookie', 'brownie', 'fudge', 'pudding', 'custard', 'mousse']
            dessert_groups = {'dessert', 'sweet_treat', 'confection'}
            
            is_dessert_name = any(keyword in food_name for keyword in dessert_keywords)
            is_dessert_group = bool(food_groups & dessert_groups)
            
            if is_dessert_name or is_dessert_group:
                if 'jam' not in food_name and 'jelly' not in food_name and 'honey' not in food_name:
                    return False
            
            if food_groups <= {'condiment', 'condiment_side', 'spice', 'seasoning'}:
                return False
            
            if food_groups <= {'fat_source', 'healthy_fat'}:
                return False
            
            if meal_tag == 'breakfast':
                if 'fried' in food_name and 'patty' in food_name:
                    return False
            
            if meal_tag in ['lunch', 'dinner']:
                pure_beverage_groups = {'beverage', 'hydration', 'drink'}
                if food_groups <= pure_beverage_groups:
                    return False
                
                side_only_groups = {'fermented_side', 'condiment_side'}
                if food_groups <= side_only_groups:
                    return False
        
        return True
    
    def _has_exact_match(self, cell_value: str, target: str) -> bool:
        
        if not cell_value or str(cell_value).lower() in ['nan', 'none', 'null', '']:
            return False
        
        parts = re.split(r'\s*[\|,\/;]\s*', str(cell_value))
        cleaned_parts = [
            p.replace('"', '').replace("'", "").replace('[', '').replace(']', '').strip().title() 
            for p in parts
        ]
        
        return target in cleaned_parts
    
    def select_initial_combination(
        self,
        food_pool: List[Dict],
        meal_tag: str,
        targets: Dict,
        used_food_ids: List[str],
        selected_cuisine: str,
        selected_pref: str,
        max_items: int = 3
    ) -> List[Dict]:

        target_groups = self.PREFERRED_FOOD_GROUPS.get(meal_tag, set())
        
        available = [
            f for f in food_pool 
            if f.get('Food_ID', f.get('food_id')) not in used_food_ids
            and self._is_appropriate_for_meal(f, meal_tag, user_profile=getattr(self, 'user_profile', None))
        ]
        
        if not available:
            logger.warning(f" No available foods for {meal_tag} (all foods already used or inappropriate)")
            return []
        
        logger.debug(f"   {meal_tag}: {len(available)} foods available after appropriateness filtering")
        
        if target_groups:
            eligible = []
            for food in available:
                item_groups = self._get_item_food_groups(food)

                if not item_groups:
                    eligible.append(food)
                elif self._food_group_matches(item_groups, target_groups):
                    eligible.append(food)
            
            if eligible:
                logger.debug(f"   {meal_tag}: {len(eligible)} foods match target food groups")
                available = eligible
            else:
                logger.warning(f"    {meal_tag}: No foods match target food groups, using all available foods")
        
        combination = []
        current_totals = {'calories': 0, 'protein': 0, 'carbs': 0, 'fat': 0}
        used_names_in_meal = []
        fulfilled_groups = set()
        
        current_veg_servings = 0
        current_fruit_servings = 0
        
        macro_midpoints = {
            'protein': (targets['protein'][0] + targets['protein'][1]) / 2,
            'carbs': (targets['carbs'][0] + targets['carbs'][1]) / 2,
            'fat': (targets['fat'][0] + targets['fat'][1]) / 2,
        }
        
        for _ in range(max_items):
            if current_totals['calories'] > targets['calories'] * 1.2:
                break
            
            if not available:
                break
            
            eligible = []
            for food in available:
                item_groups = self._get_item_food_groups(food)
                if not item_groups.intersection(fulfilled_groups):
                    eligible.append(food)
            
            if not eligible:
                break
            
            eligible = [
                f for f in eligible 
                if not self._is_too_similar(
                    f.get('Food_Name', f.get('common_name', '')), 
                    used_names_in_meal
                )
            ]
            
            if not eligible:
                break
            
            def score_food(food: Dict) -> float:
                cal = self._get_nutrient_per_gram(food, 'Calories') * 100
                prot = self._get_nutrient_per_gram(food, 'Protein_g') * 100
                carb = self._get_nutrient_per_gram(food, 'Carbs_g') * 100
                fat = self._get_nutrient_per_gram(food, 'Fat_g') * 100
                
                next_cal = current_totals['calories'] + cal
                next_prot = current_totals['protein'] + prot
                next_carb = current_totals['carbs'] + carb
                next_fat = current_totals['fat'] + fat
                
                cal_err = ((next_cal / targets['calories']) - 1) ** 2
                prot_err = ((next_prot / macro_midpoints['protein']) - 1) ** 2 if macro_midpoints['protein'] > 0 else 0
                carb_err = ((next_carb / macro_midpoints['carbs']) - 1) ** 2 if macro_midpoints['carbs'] > 0 else 0
                fat_err = ((next_fat / macro_midpoints['fat']) - 1) ** 2 if macro_midpoints['fat'] > 0 else 0
                

                base_score = cal_err * 0.4 + prot_err * 0.3 + carb_err * 0.2 + fat_err * 0.1
                
                is_low_carb_diet = False
                is_vegetarian = False
                is_athlete = False
                if self.user_profile:
                    health_conditions = self.user_profile.get('health_conditions', [])
                    if not isinstance(health_conditions, list):
                        health_conditions = [health_conditions] if health_conditions else []
                    conditions_lower = [str(c).lower() for c in health_conditions]
                    low_carb_conditions = ['diabetes', 'type 2 diabetes', 'type 1 diabetes', 'pcos', 'prediabetes']
                    is_low_carb_diet = any(cond in condition for condition in conditions_lower for cond in low_carb_conditions)
                    
                    dietary_pref = str(self.user_profile.get('dietary_preference', '')).lower()
                    is_vegetarian = dietary_pref in ['vegetarian', 'vegan', 'plant-based']
                    
                    activity_level = str(self.user_profile.get('activity_level', '')).lower()
                    goal = str(self.user_profile.get('goal', '')).lower()
                    is_athlete = activity_level in ['very_active', 'extremely_active'] or goal in ['muscle_gain', 'athletic_performance']
                
                food_groups = self._get_item_food_groups(food)
                protein_groups = {'protein_source', 'lean_protein', 'protein_main', 'legume', 'nut_seed'}
                food_name_lower = str(food.get('Food_Name', food.get('name', ''))).lower()

                complete_dish_markers = ['cooked', 'steamed', 'baked', 'sauteed', 'grilled', 'roasted',
                                        'braised', 'stir-fry', 'curry', 'stew', 'soup', 'salad',
                                        'bowl', 'plate', 'mix', 'medley', 'combo', 'blend',
                                        'pasta', 'rice and', 'with', 'stuffed', 'topped']
                is_complete_dish = any(marker in food_name_lower for marker in complete_dish_markers)
                
                if is_complete_dish and (' and ' in food_name_lower or ' with ' in food_name_lower):
                    base_score *= 0.5 
                

                protein_threshold = 0.5 if is_vegetarian else 0.6
                if current_totals['protein'] < macro_midpoints['protein'] * protein_threshold:
                    if food_groups & protein_groups:
                        if is_low_carb_diet or is_vegetarian:
                            base_score *= 0.05 
                        else:
                            base_score *= 0.3  
                
                fat_groups = {'fat_source', 'healthy_fat', 'nut_seed'}  
                if current_totals['fat'] < macro_midpoints['fat'] * 0.6:
                    if food_groups & fat_groups:
                        if is_low_carb_diet or is_vegetarian:
                            base_score *= 0.05 
                        else:
                            base_score *= 0.4  
                
                carb_threshold = 0.3 if is_vegetarian else 0.4
                if current_totals['carbs'] > macro_midpoints['carbs'] * carb_threshold:
                    if carb > 0:
                        prot_carb_ratio = prot / carb
                        if is_low_carb_diet or is_vegetarian:
                            if prot_carb_ratio < 0.15:  
                                base_score *= 3.0  
                            elif prot_carb_ratio < 0.05:  
                                base_score *= 5.0 
                        else:
                            if prot_carb_ratio < 0.15:
                                base_score *= 1.5  
                            elif prot_carb_ratio < 0.05:
                                base_score *= 2.0 
                

                if is_vegetable_serving and is_fruit_serving:
                    food_name_check = str(food.get('Food_Name', food.get('name', '')))
                    
                    if is_vegetable_serving(food_name_check) and current_veg_servings < 5:
                        shortage = 5 - current_veg_servings
                        if shortage >= 4:  
                            base_score *= 0.5 
                        elif shortage >= 3:  
                            base_score *= 0.6  
                        elif shortage >= 2: 
                            base_score *= 0.7 
                        else:  
                            base_score *= 0.8  
                    elif is_fruit_serving(food_name_check) and current_fruit_servings < 3:
                        shortage = 3 - current_fruit_servings
                        if shortage >= 2:  
                            base_score *= 0.6  
                        else:  
                            base_score *= 0.75 
                
                item_groups = self._get_item_food_groups(food)
                new_groups = item_groups.intersection(target_groups) - fulfilled_groups
                if new_groups:
                    base_score *= (0.5 ** len(new_groups))  
                elif target_groups:
                    base_score *= 1.5  
                
                commonality_factor = self._calculate_commonality_score(food)
                base_score *= commonality_factor  
                
                return base_score
            
            scored = [(score_food(f), f) for f in eligible]
            scored.sort(key=lambda x: x[0])
            
            valid_options = [f for _, f in scored]
            
            if selected_cuisine and selected_cuisine.lower() not in ['any', 'all', 'mixed']:
                cuisine_matched = [
                    f for f in valid_options
                    if self._matches_cuisine(f, selected_cuisine)
                ]
                

                if meal_tag in ['breakfast', 'lunch', 'dinner']:
                    if cuisine_matched:
                        valid_options = cuisine_matched 
                    elif not cuisine_matched:
                        global_foods = [
                            f for f in valid_options
                            if 'global' in str(f.get('cuisine', f.get('cuisine_name', ''))).lower()
                        ]
                        if global_foods:
                            valid_options = global_foods
                else:
                    if cuisine_matched:
                        top_count = max(1, int(len(cuisine_matched) * 0.9))
                        valid_options = cuisine_matched[:top_count]
            
            if selected_pref.lower() == "non-vegetarian":
                total_diet = self.diet_tracker['non_veg'] + self.diet_tracker['other']
                nv_ratio = self.diet_tracker['non_veg'] / total_diet if total_diet > 0 else 0.0
                
                if nv_ratio < 0.50:
                    nv_only = [
                        f for f in valid_options 
                        if self._has_exact_match(
                            f.get('Dietary_Preference', f.get('dietary_preferences', '')), 
                            'Non-Vegetarian'
                        )
                    ]
                    if nv_only:
                        valid_options = nv_only
                elif nv_ratio > 0.70:
                    other_only = [
                        f for f in valid_options 
                        if not self._has_exact_match(
                            f.get('Dietary_Preference', f.get('dietary_preferences', '')), 
                            'Non-Vegetarian'
                        )
                    ]
                    if other_only:
                        valid_options = other_only
            
            if selected_cuisine and selected_cuisine.lower() not in ["any", "global"]:
                total_selected = self.cuisine_tracker['pref'] + self.cuisine_tracker['global']
                pref_ratio = self.cuisine_tracker['pref'] / total_selected if total_selected > 0 else 0.0
                
                if pref_ratio < 0.70:
                    pref_only = [
                        f for f in valid_options 
                        if self._has_exact_match(
                            f.get('Cuisine', f.get('cuisine', '')), 
                            selected_cuisine
                        )
                    ]
                    if pref_only:
                        valid_options = pref_only
                else:
                    global_only = [
                        f for f in valid_options 
                        if self._has_exact_match(
                            f.get('Cuisine', f.get('cuisine', '')), 
                            'Global'
                        )
                    ]
                    if global_only:
                        valid_options = global_only
            
            top_candidates = valid_options[:30]
            if not top_candidates:
                break
            
            selected = random.choice(top_candidates)
            
            combination.append(selected)
            food_name = selected.get('Food_Name', selected.get('common_name', ''))
            used_names_in_meal.append(food_name)
            
            current_totals['calories'] += self._get_nutrient_per_gram(selected, 'Calories') * 100
            current_totals['protein'] += self._get_nutrient_per_gram(selected, 'Protein_g') * 100
            current_totals['carbs'] += self._get_nutrient_per_gram(selected, 'Carbs_g') * 100
            current_totals['fat'] += self._get_nutrient_per_gram(selected, 'Fat_g') * 100
            
            if is_vegetable_serving and is_fruit_serving:
                selected_name = selected.get('Food_Name', selected.get('name', ''))
                if is_vegetable_serving(selected_name):
                    current_veg_servings += 1
                elif is_fruit_serving(selected_name):
                    current_fruit_servings += 1
            
            if selected_cuisine and selected_cuisine.lower() not in ["any", "global"]:
                if self._has_exact_match(selected.get('Cuisine', selected.get('cuisine', '')), selected_cuisine):
                    self.cuisine_tracker['pref'] += 1
                else:
                    self.cuisine_tracker['global'] += 1
            
            if selected_pref.lower() == "non-vegetarian":
                if self._has_exact_match(selected.get('Dietary_Preference', selected.get('dietary_preferences', '')), 'Non-Vegetarian'):
                    self.diet_tracker['non_veg'] += 1
                else:
                    self.diet_tracker['other'] += 1
            
            newly_claimed = self._get_item_food_groups(selected)
            fulfilled_groups.update(newly_claimed)
            
            available = [f for f in available if f != selected]
        
        return combination
    
    def generate_meal_plan(
        self,
        filtered_food_pool: List[Dict],
        total_tdee: int,
        macro_ranges: Dict[str, Tuple[int, int]],
        selected_cuisine: str = "Any",
        selected_pref: str = "Any",
        max_attempts: int = 100,  
        user_profile: Optional[Dict] = None  
    ) -> Optional[Dict]:
        
        logger.info(f" Enhanced generator: {len(filtered_food_pool)} foods, TDEE={total_tdee} kcal, max_attempts={max_attempts}")
        
        self.user_profile = user_profile
        self.tdee = total_tdee  
        
        validation_ranges = self._get_macro_validation_ranges(user_profile)
        logger.info(f" Macro validation ranges: P:{validation_ranges['protein_min']}-{validation_ranges['protein_max']}%, "
                   f"C:{validation_ranges['carbs_min']}-{validation_ranges['carbs_max']}%, "
                   f"F:{validation_ranges['fat_min']}-{validation_ranges['fat_max']}%")
        
        meal_counts = {}
        for meal_type in ['breakfast', 'morning_snack', 'lunch', 'evening_snack', 'dinner']:
            appropriate_foods = [f for f in filtered_food_pool if self._is_appropriate_for_meal(f, meal_type, user_profile)]
            meal_counts[meal_type] = len(appropriate_foods)
        
        logger.info(f"   Foods per meal type: breakfast={meal_counts['breakfast']}, "
                   f"morning_snack={meal_counts['morning_snack']}, lunch={meal_counts['lunch']}, "
                   f"evening_snack={meal_counts['evening_snack']}, dinner={meal_counts['dinner']}")
        
        meal_targets = self.calculate_meal_targets(total_tdee, macro_ranges)
        
        best_plan = None
        best_diff = float('inf')
        
        for attempt in range(max_attempts):
            # Reset trackers
            self.daily_used_foods = set()
            self.cuisine_tracker = {'pref': 0, 'global': 0}
            self.diet_tracker = {'non_veg': 0, 'other': 0}
            
            full_meal_plan = []
            used_ids = []
            
            meal_order = ['breakfast', 'morning_snack', 'lunch', 'evening_snack', 'dinner']
            
            for meal_name in meal_order:
                targets = meal_targets[meal_name]
                
                # Use 4 items for lunch/dinner (3-1-4-1-4 structure), 3 for breakfast, 1 for snacks
                if meal_name in ['lunch', 'dinner']:
                    meal_max_items = 4
                elif meal_name == 'breakfast':
                    meal_max_items = 3
                else:
                    meal_max_items = 1
                
                initial_combination = self.select_initial_combination(
                    food_pool=filtered_food_pool,
                    meal_tag=meal_name,
                    targets=targets,
                    used_food_ids=used_ids,
                    selected_cuisine=selected_cuisine,
                    selected_pref=selected_pref,
                    max_items=meal_max_items
                )
                
                if meal_name in ['breakfast', 'lunch', 'dinner'] and initial_combination:
                    has_protein_source = False
                    total_protein = 0
                    
                    for food in initial_combination:
                        food_groups = self._get_item_food_groups(food)
                        protein_pct = food.get('Protein_g', 0) / max(food.get('Calories', 1) / 4, 0.1)  
                        
                        protein_groups = {'protein_source', 'lean_protein', 'protein_main', 'protein_snack', 'legume', 'nut_seed'}
                        if food_groups & protein_groups or protein_pct > 0.15: 
                            has_protein_source = True
                        
                        total_protein += food.get('Protein_g', 0)
                    
                    if not has_protein_source and total_protein < 5:
                        logger.debug(f"       {meal_name}: No protein source found, skipping attempt")
                        continue 
                
                if initial_combination:
                    final_combination = self.adjust_portions_discrete(
                        initial_combination, targets
                    )
                    
                    full_meal_plan.append({
                        'meal_name': meal_name.replace('_', ' ').title(),
                        'foods': final_combination
                    })
                    
                    for item in final_combination:
                        food_id = item.get('Food_ID', item.get('food_id'))
                        if food_id:
                            used_ids.append(food_id)
            
            if not full_meal_plan:
                continue
            
            total_cals = 0
            total_protein = 0
            total_carbs = 0
            total_fat = 0
            
            for meal in full_meal_plan:
                for food in meal['foods']:
                    total_cals += food.get('Calories', 0)
                    total_protein += food.get('Protein_g', 0)
                    total_carbs += food.get('Carbs_g', 0)
                    total_fat += food.get('Fat_g', 0)
            

            critically_bad = False
            macro_valid = True
            
            if total_cals > 0:
                protein_cals = total_protein * 4
                carbs_cals = total_carbs * 4
                fat_cals = total_fat * 9
                total_macro_cals = protein_cals + carbs_cals + fat_cals
                
                if total_macro_cals > 0:
                    protein_pct = (protein_cals / total_macro_cals) * 100
                    carbs_pct = (carbs_cals / total_macro_cals) * 100
                    fat_pct = (fat_cals / total_macro_cals) * 100
                    
                    logger.debug(f"   Attempt {attempt+1}: {total_cals:.0f} kcal | P:{protein_pct:.1f}% C:{carbs_pct:.1f}% F:{fat_pct:.1f}%")
                    

                    if protein_pct < 8: 
                        logger.debug(f"       CRITICAL: Protein {protein_pct:.1f}% dangerously low (absolute minimum 8%)")
                        critically_bad = True
                    elif fat_pct > 65: 
                        logger.debug(f"       CRITICAL: Fat {fat_pct:.1f}% dangerously high (absolute maximum 65%)")
                        critically_bad = True
                    
                    if critically_bad:
                        continue
                    

                    if protein_pct < validation_ranges['protein_min']:
                        logger.debug(f"       Protein {protein_pct:.1f}% too low (need >{validation_ranges['protein_min']}%)")
                        macro_valid = False
                    elif protein_pct > validation_ranges['protein_max']:
                        logger.debug(f"       Protein {protein_pct:.1f}% too high (need <{validation_ranges['protein_max']}%)")
                        macro_valid = False
                    elif carbs_pct < validation_ranges['carbs_min']:
                        logger.debug(f"       Carbs {carbs_pct:.1f}% too low (need >{validation_ranges['carbs_min']}%)")
                        macro_valid = False
                    elif carbs_pct > validation_ranges['carbs_max']:
                        logger.debug(f"       Carbs {carbs_pct:.1f}% too high (need <{validation_ranges['carbs_max']}%)")
                        macro_valid = False
                    elif fat_pct < validation_ranges['fat_min']:
                        logger.debug(f"       Fat {fat_pct:.1f}% too low (need >{validation_ranges['fat_min']}%)")
                        macro_valid = False
                    elif fat_pct > validation_ranges['fat_max']:
                        logger.debug(f"       Fat {fat_pct:.1f}% too high (need <{validation_ranges['fat_max']}%)")
                        macro_valid = False
            
            diff = abs(total_cals - total_tdee)
            
            cuisine_quality_score = 1.0  
            if selected_cuisine and selected_cuisine.lower() not in ['any', 'all', 'mixed']:
                cuisine_matched_count = 0
                total_foods = 0
                
                for meal in full_meal_plan:
                    for food in meal['foods']:
                        total_foods += 1
                        if self._matches_cuisine(food, selected_cuisine):
                            cuisine_matched_count += 1
                        food_cuisine = str(food.get('cuisine', food.get('cuisine_name', ''))).lower()
                        if 'global' in food_cuisine:
                            cuisine_matched_count += 0.5  
                
                if total_foods > 0:
                    cuisine_quality_score = cuisine_matched_count / total_foods
                    
                    if cuisine_quality_score < 0.6:  
                        logger.debug(f"       Poor cuisine match: {cuisine_quality_score*100:.0f}% (need >60%)")
                        macro_valid = False
            

            if macro_valid:
                best_diff = diff
                best_plan = full_meal_plan
                logger.info(f" Meal plan generated in {attempt + 1} attempts (diff: {diff:.0f} kcal)")
                break
            elif diff < best_diff:
                best_diff = diff
                best_plan = full_meal_plan
        
        if best_plan:
            totals = get_plan_totals(best_plan)
            protein_cals = totals['protein'] * 4
            carbs_cals = totals['carbs'] * 4
            fat_cals = totals['fat'] * 9
            total_macro_cals = protein_cals + carbs_cals + fat_cals
            
            if total_macro_cals > 0:
                protein_pct = (protein_cals / total_macro_cals) * 100
                carbs_pct = (carbs_cals / total_macro_cals) * 100
                fat_pct = (fat_cals / total_macro_cals) * 100
                logger.info(f" Macros: Protein {protein_pct:.1f}%, Carbs {carbs_pct:.1f}%, Fat {fat_pct:.1f}%")
                
                quality_score = self._calculate_plan_quality_score(
                    best_plan, total_tdee, selected_cuisine, validation_ranges
                )
                logger.info(f" Overall Plan Quality: {quality_score:.1f}/100")
                
                if quality_score >= 80:
                    quality_grade = "Excellent (A)"
                elif quality_score >= 70:
                    quality_grade = "Good (B)"
                elif quality_score >= 60:
                    quality_grade = "Fair (C)"
                elif quality_score >= 50:
                    quality_grade = "Poor (D)"
                else:
                    quality_grade = "Very Poor (F)"
                
                logger.info(f" Quality Grade: {quality_grade}")
                
                if selected_cuisine and selected_cuisine.lower() not in ['any', 'all', 'mixed']:
                    cuisine_matched_count = 0
                    total_foods = 0
                    
                    for meal in best_plan:
                        for food in meal.get('foods', []):
                            total_foods += 1
                            if self._matches_cuisine(food, selected_cuisine):
                                cuisine_matched_count += 1
                            food_cuisine = str(food.get('cuisine', food.get('cuisine_name', ''))).lower()
                            if 'global' in food_cuisine and not self._matches_cuisine(food, selected_cuisine):
                                cuisine_matched_count += 0.5
                    
                    if total_foods > 0:
                        cuisine_match_pct = (cuisine_matched_count / total_foods) * 100
                        logger.info(f" Cuisine Match: {cuisine_match_pct:.1f}% {selected_cuisine} foods")
                        
                        if cuisine_match_pct < 70:
                            logger.warning(f" Low cuisine match: {cuisine_match_pct:.1f}% (target: >70%)")
                
                logger.info(f" Meal plan details:")
                for meal in best_plan:
                    meal_name = meal.get('meal_name', 'Unknown')
                    logger.info(f"   {meal_name}:")
                    for food in meal.get('foods', []):
                        food_name = food.get('name', food.get('Food_Name', 'Unknown'))
                        calories = food.get('Calories', 0)
                        protein = food.get('Protein_g', 0)
                        food_cuisine = str(food.get('cuisine', food.get('cuisine_name', ''))).lower()
                        
                        cuisine_indicator = ""
                        if selected_cuisine and selected_cuisine.lower() not in ['any', 'all', 'mixed']:
                            if self._matches_cuisine(food, selected_cuisine):
                                cuisine_indicator = f" [{selected_cuisine}] ✓"
                            elif 'global' in food_cuisine:
                                cuisine_indicator = " [Global]"
                            else:
                                cuisine_indicator = f" [{food_cuisine}] ⚠️"
                        
                        logger.info(f"      - {food_name}{cuisine_indicator}: {calories:.0f} kcal, {protein:.1f}g protein")
                
                if protein_pct < validation_ranges['protein_min'] or protein_pct > validation_ranges['protein_max']:
                    logger.warning(f"  Protein {protein_pct:.1f}% is outside ideal range ({validation_ranges['protein_min']}-{validation_ranges['protein_max']}%)")
                if carbs_pct < validation_ranges['carbs_min'] or carbs_pct > validation_ranges['carbs_max']:
                    logger.warning(f"  Carbs {carbs_pct:.1f}% is outside ideal range ({validation_ranges['carbs_min']}-{validation_ranges['carbs_max']}%)")
                if fat_pct < validation_ranges['fat_min'] or fat_pct > validation_ranges['fat_max']:
                    logger.warning(f"  Fat {fat_pct:.1f}% is outside ideal range ({validation_ranges['fat_min']}-{validation_ranges['fat_max']}%)")
            
            logger.info(f" Final calories: {totals['calories']:.0f} kcal (target: {total_tdee} kcal, diff: {best_diff:.0f} kcal)")
            
            meal_plan_dict = {
                'meal_plan': best_plan,
                'daily_calories': totals['calories'],
                'target_calories': total_tdee,
                'calorie_diff': best_diff
            }
            
            if validate_minimum_servings and log_serving_analysis:
                log_serving_analysis(meal_plan_dict, min_veg=5, min_fruit=3)
                
                serving_validation = validate_minimum_servings(meal_plan_dict, min_veg=5, min_fruit=3)
                logger.info(serving_validation['message'])
                
                meal_plan_dict['serving_validation'] = serving_validation
            
            return meal_plan_dict
        
        logger.warning(f" Failed to generate meal plan after {max_attempts} attempts")
        logger.warning(f"   Check logs above for rejection reasons (protein/carbs/fat violations)")
        return None


def get_plan_totals(meal_plan: List[Dict]) -> Dict[str, float]:
    """Calculate daily totals for all nutrients including micronutrients"""
    totals = {
        'calories': 0, 
        'protein': 0, 
        'carbs': 0, 
        'fat': 0,
        'sodium': 0,
        'cholesterol': 0,
        'fiber': 0,
        'sugar': 0
    }
    
    for meal in meal_plan:
        for food in meal.get('foods', []):
            totals['calories'] += food.get('Calories', 0)
            totals['protein'] += food.get('Protein_g', 0)
            totals['carbs'] += food.get('Carbs_g', 0)
            totals['fat'] += food.get('Fat_g', 0)
            totals['sodium'] += food.get('Sodium_mg', 0)
            totals['cholesterol'] += food.get('Cholesterol_mg', 0)
            totals['fiber'] += food.get('Fiber_g', 0)
            totals['sugar'] += food.get('Sugar_g', 0)
    
    return totals
