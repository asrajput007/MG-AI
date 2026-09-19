import copy
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import List, Dict, Optional, Literal
from enum import Enum
import logging
import json
import os
import re
from dotenv import load_dotenv
import redis
import hashlib
import ssl
USDA_FIBER_BASELINES = {
    'broccoli': 2.6,
    'spinach': 2.2,
    'carrot': 2.8,
    'lettuce': 1.3,
    'cucumber': 0.5,
    'kale': 2.0,
    'cauliflower': 2.0,
    'zucchini': 1.1,
    'asparagus': 2.1,
    'green bean': 3.4,
    'bell pepper': 1.7,
    'eggplant': 3.0,
    'sweet potato': 3.0,
    'potato': 2.2,
}

USDA_CHOLESTEROL_BASELINES = {
    'egg': 373.0,
    'chicken': 85.0,
    'beef': 90.0,
    'pork': 80.0,
    'salmon': 55.0,
    'shrimp': 195.0,
    'turkey': 109.0,
    'duck': 70.0,
    'lamb': 97.0,
}

def _heal_nutrition_data_with_usda(food_name, nutrition: dict) -> dict:

    healed = copy.deepcopy(nutrition)
    name_lower = str(food_name).lower()
    for veg, fallback_fiber in USDA_FIBER_BASELINES.items():
        if veg in name_lower and (healed.get('fiber', 0) is None or healed.get('fiber', 0) < 0.1):
            healed['fiber'] = fallback_fiber
    for animal, fallback_chol in USDA_CHOLESTEROL_BASELINES.items():
        if animal in name_lower and (healed.get('cholesterol', 0) is None or healed.get('cholesterol', 0) < 0.1):
            healed['cholesterol'] = fallback_chol
    return healed

load_dotenv()
logger = logging.getLogger(__name__)
class MealType(str, Enum):
    BREAKFAST = "Breakfast"
    MORNING_SNACK = "Morning Snack"
    LUNCH = "Lunch"
    EVENING_SNACK = "Evening Snack"
    DINNER = "Dinner"


class PortionUnit(str, Enum):
    CUP = "cup"
    TABLESPOON = "tbsp"
    TEASPOON = "tsp"
    FL_OZ = "fl oz"
    OZ = "oz"
    PIECE = "piece"
    SLICE = "slice"
    WHOLE = "whole"
    SHEET = "sheet"
    BUNCH = "bunch"
    SPRIG = "sprig"
    PINCH = "pinch"
    DASH = "dash"
    CLOVE = "clove"
    HEAD = "head"

class CookingMethod(str, Enum):
    RAW = "raw"
    COOKED = "cooked"
    GRILLED = "grilled"
    BAKED = "baked"
    STEAMED = "steamed"
    ROASTED = "roasted"
    BOILED = "boiled"
    FRIED = "fried"
    FRESH = "fresh"
class NutritionData(BaseModel):
    calories: float = Field(ge=0, le=900, description="Calories per 100g")
    protein: float = Field(ge=0, le=100, description="Protein in grams per 100g")
    carbs: float = Field(ge=0, le=100, description="Carbs in grams per 100g")
    fat: float = Field(ge=0, le=100, description="Fat in grams per 100g")
    fiber: float = Field(ge=0, le=100, description="Fiber in grams per 100g (part of carbs)")
    sodium: float = Field(ge=0, le=10000, description="Sodium in mg per 100g")
    sugar: float = Field(ge=0, le=100, description="Sugar in grams per 100g")
    cholesterol: float = Field(ge=0, le=1000, description="Cholesterol in mg per 100g")
    iodine: float = Field(default=0, ge=0, le=10000, description="Iodine in mcg per 100g")
    
    @model_validator(mode='after')
    def validate_macros_sum(self):
        healed = _heal_nutrition_data_with_usda(getattr(self, 'name', ''), self.model_dump())
        self.fiber = healed.get('fiber', self.fiber)
        self.cholesterol = healed.get('cholesterol', self.cholesterol)
        total_macros = self.protein + self.carbs + self.fat
        if total_macros > 110: 
            raise ValueError(f"Macros (P+C+F) sum to {total_macros}g, exceeds 100g per 100g food")
        if self.fiber > self.carbs:
            raise ValueError(f"Fiber ({self.fiber}g) cannot exceed total carbs ({self.carbs}g)")
        return self


class FoodItem(BaseModel):
    name: str = Field(description="Common food name")
    aliases: List[str] = Field(default_factory=list, description="Alternative names for fuzzy matching")
    category: str = Field(description="Food category (protein, grain, vegetable, etc.)")
    cooking_method: Optional[CookingMethod] = None
    nutrition_per_100g: NutritionData
    typical_portion_g: Dict[str, float] = Field(
        default_factory=dict,
        description="Weight in grams for common portions (e.g., {'1 cup': 240, '1 oz': 28.35})"
    )
    
    is_vegetarian: bool = True
    is_vegan: bool = False
    is_gluten_free: bool = True
    is_dairy_free: bool = True
    suitable_for_diabetes: bool = True
    suitable_for_hypertension: bool = True

class FoodSelection(BaseModel):
    name: str = Field(
        description="EXACT food name copied character-for-character from the provided database list (e.g., 'gai lan chinese brocali steamed', 'Raw Extra Firm Tofu Block'). Database has been cleaned of scientific names - use as-is.",
        min_length=3,
        max_length=200
    )
    quantity: float = Field(
        gt=0,
        le=20,  
        description="Portion quantity (REQUIRED - cannot be None or 0)"
    )
    unit: PortionUnit = Field(
        description="Household measurement unit (REQUIRED - MUST be one of: cup, tbsp, tsp, oz, piece, slice, whole)",
        examples=["cup", "tbsp", "tsp", "oz", "piece"]
    )
    
    calories_per_100g: Optional[float] = Field(
        None,
        ge=0,
        le=900,
        description="Optional: Calories per 100g for custom LLM-generated dishes not in database"
    )
    protein_per_100g: Optional[float] = Field(
        None,
        ge=0,
        le=100,
        description="Optional: Protein in grams per 100g for custom LLM-generated dishes"
    )
    carbs_per_100g: Optional[float] = Field(
        None,
        ge=0,
        le=100,
        description="Optional: Carbs in grams per 100g for custom LLM-generated dishes"
    )
    fat_per_100g: Optional[float] = Field(
        None,
        ge=0,
        le=100,
        description="Optional: Fat in grams per 100g for custom LLM-generated dishes"
    )
    

    fiber_per_100g: Optional[float] = Field(
        None,
        ge=0,
        le=50,
        description="Optional: Fiber in grams per 100g - used when DB has missing/zero fiber data"
    )
    sugar_per_100g: Optional[float] = Field(
        None,
        ge=0,
        le=100,
        description="Optional: Sugar in grams per 100g - used when DB has missing/zero sugar data"
    )
    sodium_per_100g: Optional[float] = Field(
        None,
        ge=0,
        le=10000,
        description="Optional: Sodium in mg per 100g - used when DB has missing/zero sodium data"
    )
    cholesterol_per_100g: Optional[float] = Field(
        None,
        ge=0,
        le=1000,
        description="Optional: Cholesterol in mg per 100g - used when DB has missing/zero cholesterol data"
    )
    iodine_per_100g: Optional[float] = Field(
        None,
        ge=0,
        le=3000,
        description="Optional: Iodine in mcg per 100g - used when DB has missing/zero iodine data"
    )
    
    @model_validator(mode='before')
    @classmethod
    def auto_correct_invalid_units(cls, values: dict) -> dict:

        if not isinstance(values, dict):
            return values
        
        name = values.get('name', '')
        unit = values.get('unit', '')
        
        if not name or not unit:
            return values
        
        name_lower = name.lower()
        
        if unit == 'cup':
            seed_keywords = ['seed', 'seeds', 'flax', 'chia', 'hemp']
            if any(keyword in name_lower for keyword in seed_keywords):
                logger.info(f" Auto-corrected: {name} unit 'cup' → 'tsp'")
                values['unit'] = 'tsp'
                return values
            
            nut_keywords = ['nut', 'nuts', 'almond', 'cashew', 'walnut', 'pistachio', 'pecan']
            if any(keyword in name_lower for keyword in nut_keywords):
                logger.info(f" Auto-corrected: {name} unit 'cup' → 'tbsp'")
                values['unit'] = 'tbsp'
                return values
        
        return values
    
    @field_validator('name')
    @classmethod
    def validate_food_name(cls, v: str) -> str:
        banned_words = ['premium', 'organic', 'gourmet', 'artisan', 'deluxe', 'ultra']
        words = v.split()
        cleaned_words = [w for w in words if w.lower() not in banned_words]
        cleaned = ' '.join(cleaned_words).strip()
        
        if len(cleaned) > 150:
            main_words = cleaned.split()[:5]
            cleaned = ' '.join(main_words).strip()
            logger.warning(f"Truncated overly long food name to: {cleaned}")
        
        return cleaned
    
    @model_validator(mode='after')
    def validate_portion_logic(self):
        food_lower = self.name.lower()
        unit_str = self.unit.value
        if 'cup' in unit_str and any(x in food_lower for x in ['almond', 'walnut', 'cashew', 'nut', 'pistachio']):
            raise ValueError(f"Nuts cannot use cups! Use 'tbsp' instead. Got: {self.quantity} {unit_str}")
        if 'cup' in unit_str and any(x in food_lower for x in ['chia', 'flax', 'seed']):
            raise ValueError(f"Seeds cannot use cups! Use 'tsp' instead. Got: {self.quantity} {unit_str}")
        if 'cup' in unit_str and any(x in food_lower for x in ['bread', 'roti', 'chapati', 'naan']):
            raise ValueError(f"Bread items cannot use cups! Use 'piece' or 'slice'. Got: {self.quantity} {unit_str}")
        
        if re.search(r'\begg\b|\beggs\b', food_lower):
            if unit_str in ['cup', 'cups', 'tbsp', 'tablespoon', 'oz', 'ounce']:
                logger.warning(f"  AUTO-CORRECT: '{self.name}' had invalid unit '{unit_str}' → Corrected to 'piece' (qty=1.0)")
                self.unit = PortionUnit.PIECE
                self.quantity = 1.0
        
        
        return self


class MealPlanResponse(BaseModel):
    meal_type: MealType
    items: List[FoodSelection] = Field(
        min_length=2,
        max_length=8,
        description="2-8 food items for this meal"
    )
    
    reasoning: Optional[str] = Field(
        None,
        max_length=2000,
        description="Brief explanation of food choices and macro balance"
    )
    
    @field_validator('items')
    @classmethod
    def validate_item_uniqueness(cls, v: List[FoodSelection]) -> List[FoodSelection]:
        if len(v) > 8:
            logger.warning(f"LLM returned {len(v)} items, auto-trimming to first 8")
            v = v[:8]
        
        seen_foods = set()
        for item in v:
            food_normalized = item.name.lower().strip()
            food_base = food_normalized.replace('extra virgin ', '').replace('extra ', '').strip()
            
            if food_normalized in seen_foods or food_base in seen_foods:
                logger.warning(f"Duplicate food detected in meal: '{item.name}' (normalized: '{food_normalized}'). Skipping validation to prevent plan rejection.")

            seen_foods.add(food_normalized)
            seen_foods.add(food_base)
        return v
class DailyFoodPartitions(BaseModel):
    breakfast: List[str] = Field(min_length=3, max_length=3)
    morning_snack: List[str] = Field(min_length=2, max_length=4)
    lunch: List[str] = Field(min_length=4, max_length=4)
    evening_snack: List[str] = Field(min_length=2, max_length=4)
    dinner: List[str] = Field(min_length=4, max_length=4)
    
    @model_validator(mode='after')
    def validate_no_repeats(self):
        all_foods = (
            self.breakfast + self.morning_snack + self.lunch + 
            self.evening_snack + self.dinner
        )
        all_foods_normalized = [f.lower().strip() for f in all_foods]
        
        if len(all_foods_normalized) != len(set(all_foods_normalized)):
            from collections import Counter
            counts = Counter(all_foods_normalized)
            duplicates = [food for food, count in counts.items() if count > 1]
            raise ValueError(f"Foods repeated across meals: {duplicates}")
        
        return self

class MacroTargets(BaseModel):
    protein_pct: float = Field(ge=10, le=50, description="Protein as % of calories")
    carbs_pct: float = Field(ge=5, le=70, description="Carbs as % of calories")
    fat_pct: float = Field(ge=15, le=60, description="Fat as % of calories")
    
    @model_validator(mode='after')
    def validate_sum_to_100(self):
        total = self.protein_pct + self.carbs_pct + self.fat_pct
        if not (99 <= total <= 101): 
            raise ValueError(f"Macro percentages must sum to 100%, got {total}%")
        return self
    
    @classmethod
    def for_diabetes(cls) -> 'MacroTargets':
        return cls(protein_pct=30.0, carbs_pct=35.0, fat_pct=35.0)
    
    @classmethod
    def for_low_carb(cls) -> 'MacroTargets':
        return cls(protein_pct=30.0, carbs_pct=25.0, fat_pct=45.0)
    
    @classmethod
    def balanced(cls) -> 'MacroTargets':
        return cls(protein_pct=20.0, carbs_pct=50.0, fat_pct=30.0)


class CalculatedNutrition(BaseModel):
    calories: float = Field(default=0.0, ge=0)
    protein_g: float = Field(default=0.0, ge=0)
    carbs_g: float = Field(default=0.0, ge=0)
    fat_g: float = Field(default=0.0, ge=0)
    fiber_g: float = Field(default=0.0, ge=0)
    net_carbs_g: float = Field(default=0.0, ge=0, description="Net carbs (carbs - fiber) in grams")
    sodium_mg: float = Field(default=0.0, ge=0)
    sugar_g: float = Field(default=0.0, ge=0)
    cholesterol_mg: float = Field(default=0.0, ge=0)
    iodine_mcg: float = Field(default=0.0, ge=0, description="Iodine in micrograms")
    
    @property
    def protein_pct(self) -> float:
        if self.calories == 0:
            return 0
        return (self.protein_g * 4 / self.calories) * 100
    
    @property
    def carbs_pct(self) -> float:
        if self.calories == 0:
            return 0
        return (self.carbs_g * 4 / self.calories) * 100
    
    @property
    def fat_pct(self) -> float:
        if self.calories == 0:
            return 0
        return (self.fat_g * 9 / self.calories) * 100


class MealAnalysis(BaseModel):
    meal_type: MealType
    foods: List[FoodSelection]
    nutrition: CalculatedNutrition
    target_calories: float
    target_macros: MacroTargets
    
    @property
    def calorie_accuracy(self) -> float:
        if self.target_calories == 0:
            return 0
        return min(100, (self.nutrition.calories / self.target_calories) * 100)
    
    @property
    def macro_deviation(self) -> Dict[str, float]:
        return {
            'protein': abs(self.nutrition.protein_pct - self.target_macros.protein_pct),
            'carbs': abs(self.nutrition.carbs_pct - self.target_macros.carbs_pct),
            'fat': abs(self.nutrition.fat_pct - self.target_macros.fat_pct)
        }
    
    @property
    def is_valid(self) -> bool:
        calorie_ok = 80 <= self.calorie_accuracy <= 120
        deviations = self.macro_deviation
        macros_ok = all(dev <= 20 for dev in deviations.values())
        return calorie_ok and macros_ok


class NutritionDatabase:  
    def __init__(self, json_path: str = None):

        self.foods: Dict[str, FoodItem] = {}
        if json_path is None:
            base_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "datasets")
            main_path = os.path.join(base_path, "Foods_nested_USA_main_dishes.json")
            side_path = os.path.join(base_path, "Foods_nested_USA_side_dishes.json")
            fallback_path = os.path.join(base_path, "foods 1.json")
            
            if os.path.exists(main_path):
                self._load_from_nested_json(main_path, side_path)
            elif os.path.exists(fallback_path):
                logger.info(" USA nested files not found, using foods 1.json fallback")
                self._load_from_flat_json(fallback_path)  
            else:
                logger.error(" No nutrition database files found!")
                logger.error(f"   Checked: {main_path}")
                logger.error(f"   Checked: {fallback_path}")
        else:
            if json_path.endswith("foods 1.json"):
                self._load_from_flat_json(json_path) 
            elif not os.path.exists(json_path):
                self._load_from_json(json_path)
            else:
                self._load_from_nested_json(json_path, None)
    
    def _extract_macro_value(self, value) -> float:

        if value is None:
            return 0.0
        
        if isinstance(value, (list, tuple)):
            if len(value) == 0:
                return 0.0
            try:
                return round(sum(float(v) for v in value) / 2.0, 2)
            except (ValueError, TypeError):
                return 0.0
        
        try:
            return float(value)
        except (ValueError, TypeError):
            return 0.0
    
    def _load_from_nested_json(self, main_path: str, side_path: str = None):

        if not os.path.exists(main_path):
            logger.error(f" Nutrition database JSON not found: {main_path}")
            logger.warning(" Database will be empty - meal generation will fail!")
            return
        
        logger.info(f" Loading nutrition database from nested JSON datasets")
        logger.info(f"   Main dishes: {main_path}")
        if side_path and os.path.exists(side_path):
            logger.info(f"   Side dishes: {side_path}")
        
        loaded_count = 0
        skipped_count = 0
        
        try:
            with open(main_path, 'r', encoding='utf-8') as f:
                main_data = json.load(f)
            
            side_data = {}
            if side_path and os.path.exists(side_path):
                with open(side_path, 'r', encoding='utf-8') as f:
                    side_data = json.load(f)
            
            cuisine_data = {**main_data, **side_data}
            
            for cuisine_name, dietary_types in cuisine_data.items():
                if not isinstance(dietary_types, dict):
                    continue
                
                for diet_type, food_items in dietary_types.items():
                    if not isinstance(food_items, dict):
                        continue
                    
                    for food_name_raw, macro_data in food_items.items():
                        if not isinstance(macro_data, dict):
                            continue
                        
                        try:
                            food_name_display = food_name_raw.replace('_', ' ').title()
                            
                            calories = self._extract_macro_value(macro_data.get('calories_per_100g'))
                            protein = self._extract_macro_value(macro_data.get('protein_per_100g'))
                            fat = self._extract_macro_value(macro_data.get('fat_per_100g'))
                            fiber = self._extract_macro_value(macro_data.get('fiber_per_100g', 0))
                            
                            carbs = self._extract_macro_value(macro_data.get('carbohydrates_per_100g'))
                            if carbs == 0.0 and calories > 0:
                                calculated_carbs = (calories - (protein * 4) - (fat * 9)) / 4.0
                                carbs = max(0.0, round(calculated_carbs, 2))
                            
                            sodium = self._extract_macro_value(macro_data.get('sodium_per_100g', 100))
                            sugar = self._extract_macro_value(macro_data.get('sugar_per_100g', 0))
                            cholesterol = self._extract_macro_value(macro_data.get('cholesterol_per_100g', 0))
                            iodine = 0.0  
                            if calories <= 0:
                                logger.debug(f" Skipping '{food_name_display}' - zero calories in JSON source")
                                skipped_count += 1
                                continue
                            
                            category = self._categorize_food(food_name_display)
                            cooking_method = self._detect_cooking_method(food_name_display)
                            
                            nutrition_data = NutritionData(
                                calories=round(calories, 1),
                                protein=round(protein, 2),
                                carbs=round(carbs, 2),
                                fat=round(fat, 2),
                                fiber=round(fiber, 2),
                                sodium=round(sodium, 2),
                                sugar=round(sugar, 2),
                                cholesterol=round(cholesterol, 2),
                                iodine=iodine
                            )
                            
                            food_item = FoodItem(
                                name=food_name_display,
                                aliases=[],
                                category=category,
                                cooking_method=cooking_method,
                                nutrition_per_100g=nutrition_data,
                                typical_portion_g={},
                                is_vegetarian=diet_type.lower() in ['vegetarian', 'vegan'],
                                is_vegan=diet_type.lower() == 'vegan'
                            )
                            
                            self.foods[food_name_display.lower()] = food_item
                            loaded_count += 1
                            
                        except Exception as e:
                            logger.debug(f"Error processing food '{food_name_raw}': {e}")
                            skipped_count += 1
                            continue
            
            logger.info(f" Loaded {loaded_count} foods from nested JSON datasets")
            if skipped_count > 0:
                logger.warning(f" Skipped {skipped_count} invalid food entries")
                
        except Exception as e:
            logger.error(f" Error loading nested JSON datasets: {e}")
            import traceback
            traceback.print_exc()
    
    def _load_from_json(self, json_path: str):
        
        if not os.path.exists(json_path):
            logger.error(f" Nutrition database JSON not found: {json_path}")
            logger.warning(" Database will be empty - meal generation will fail!")
            return
        
        logger.info(f" Loading nutrition database from: {json_path}")
        loaded_count = 0
        skipped_count = 0
        
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                cuisine_data = json.load(f)
            
            for cuisine_name, dietary_types in cuisine_data.items():
                if not isinstance(dietary_types, dict):
                    continue
                
                for diet_type, food_items in dietary_types.items():
                    if not isinstance(food_items, dict):
                        continue
                    
                    for food_name_raw, macro_data in food_items.items():
                        if not isinstance(macro_data, dict):
                            continue
                        
                        try:
                            food_name_display = food_name_raw.replace('_', ' ').title()
                            
                            calories = self._extract_macro_value(macro_data.get('calories_per_100g'))
                            protein = self._extract_macro_value(macro_data.get('protein_per_100g'))
                            fat = self._extract_macro_value(macro_data.get('fat_per_100g'))
                            fiber = self._extract_macro_value(macro_data.get('fiber_per_100g', 0))
                            
                            carbs = self._extract_macro_value(macro_data.get('carbohydrates_per_100g'))
                            if carbs == 0.0 and calories > 0:
                                calculated_carbs = (calories - (protein * 4) - (fat * 9)) / 4
                                carbs = max(0.0, calculated_carbs)
                            
                            sodium = self._extract_macro_value(macro_data.get('sodium_per_100g', 100))
                            sugar = self._extract_macro_value(macro_data.get('sugar_per_100g', 0))
                            cholesterol = self._extract_macro_value(macro_data.get('cholesterol_per_100g', 0))
                            iodine = self._extract_macro_value(macro_data.get('iodine_per_100g', 0))
                            
                            if calories <= 0:
                                logger.debug(f" Skipping '{food_name_display}' - zero calories in JSON source")
                                skipped_count += 1
                                continue
                            
                            category = self._categorize_food(food_name_display)
                            cooking_method = self._detect_cooking_method(food_name_display)
                            
                            nutrition_data = NutritionData(
                                calories=round(calories, 1),
                                protein=round(protein, 2),
                                carbs=round(carbs, 2),
                                fat=round(fat, 2),
                                fiber=round(fiber, 2),
                                sodium=round(sodium, 1),
                                sugar=round(sugar, 2),
                                cholesterol=round(cholesterol, 1),
                                iodine=round(iodine, 2)
                            )
                            
                            food_item = FoodItem(
                                name=food_name_display,
                                aliases=[],
                                category=category,
                                cooking_method=cooking_method,
                                nutrition_per_100g=nutrition_data,
                                typical_portion_g={"1 serving": 100},  
                                is_vegetarian=True,
                                is_vegan=False,
                                is_gluten_free=True,
                                is_dairy_free=True,
                                suitable_for_diabetes=True,
                                suitable_for_hypertension=True
                            )
                            
                            self.add_food(food_item)
                            loaded_count += 1
                            
                        except Exception as e:
                            logger.debug(f"Error loading food '{food_name_raw}': {e}")
                            skipped_count += 1
                            continue
        
        except Exception as e:
            logger.error(f" Failed to load JSON: {e}")
            import traceback
            traceback.print_exc()
            return
        
        logger.info(f" Nutrition database loaded: {loaded_count:,} foods from JSON ({skipped_count} skipped)")
        logger.info(f" Database contains {len(self.foods):,} searchable entries (including aliases)")
    
    def _load_from_flat_json(self, json_path: str):

        if not os.path.exists(json_path):
            logger.error(f" Nutrition database JSON not found: {json_path}")
            logger.warning(" Database will be empty - meal generation will fail!")
            return
        
        logger.info(f" Loading FLAT nutrition database from: {json_path}")
        loaded_count = 0
        skipped_count = 0
        
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            if not isinstance(data, dict) or 'foods' not in data:
                logger.error(" Invalid format: Expected {'foods': [...]}")
                return
            
            foods_array = data.get('foods', [])
            if not isinstance(foods_array, list):
                logger.error(" Invalid format: 'foods' must be an array")
                return
            
            logger.info(f"    Processing {len(foods_array)} foods from flat array...")
            
            for food_item in foods_array:
                if not isinstance(food_item, dict):
                    skipped_count += 1
                    continue
                
                try:
                    food_name = (food_item.get('common_name') or 
                                food_item.get('food_name') or 
                                food_item.get('name') or 
                                food_item.get('display_name'))
                    
                    if not food_name:
                        logger.debug(" Skipping item with no name")
                        skipped_count += 1
                        continue
                    
                    calories = float(food_item.get('calories', 0))
                    protein = float(food_item.get('protein', 0))
                    carbs = float(food_item.get('carbs', 0) or food_item.get('carbohydrates', 0))
                    fat = float(food_item.get('fat', 0))
                    fiber = float(food_item.get('fiber', 0))
                    sodium = float(food_item.get('sodium', 100))
                    sugar = float(food_item.get('sugar', 0))
                    cholesterol = float(food_item.get('cholesterol', 0))
                    iodine = float(food_item.get('iodine', 0))
                    
                    if calories <= 0:
                        logger.debug(f" Skipping '{food_name}' - zero calories")
                        skipped_count += 1
                        continue
                    
                    category = self._categorize_food(food_name)
                    cooking_method = self._detect_cooking_method(food_name)
                    
                    nutrition_data = NutritionData(
                        calories=round(calories, 1),
                        protein=round(protein, 2),
                        carbs=round(carbs, 2),
                        fat=round(fat, 2),
                        fiber=round(fiber, 2),
                        sodium=round(sodium, 1),
                        sugar=round(sugar, 2),
                        cholesterol=round(cholesterol, 1),
                        iodine=round(iodine, 2)
                    )
                    
                    food_entry = FoodItem(
                        name=food_name,
                        aliases=[],
                        category=category,
                        cooking_method=cooking_method,
                        nutrition_per_100g=nutrition_data,
                        typical_portion_g={"1 serving": 100},
                        is_vegetarian=True,
                        is_vegan=False,
                        is_gluten_free=True,
                        is_dairy_free=True,
                        suitable_for_diabetes=True,
                        suitable_for_hypertension=True
                    )
                    
                    self.add_food(food_entry)
                    loaded_count += 1
                    
                except Exception as e:
                    logger.debug(f"Error loading food item: {e}")
                    skipped_count += 1
                    continue
        
        except Exception as e:
            logger.error(f" Failed to load FLAT JSON: {e}")
            import traceback
            traceback.print_exc()
            return
        
        logger.info(f" FLAT database loaded: {loaded_count:,} foods from JSON ({skipped_count} skipped)")
        logger.info(f" Database contains {len(self.foods):,} searchable entries (including aliases)")
    
    def _categorize_food(self, food_name: str) -> str:
        name_lower = food_name.lower()
        if any(x in name_lower for x in ['chicken', 'turkey', 'beef', 'pork', 'lamb', 'fish', 'salmon', 
                                          'tuna', 'shrimp', 'egg', 'tofu', 'tempeh', 'seitan', 'paneer']):
            return "protein"
        if any(x in name_lower for x in ['rice', 'bread', 'pasta', 'noodle', 'quinoa', 'oat', 'cereal',
                                          'roti', 'chapati', 'naan', 'tortilla', 'pita']):
            return "grain"
        if any(x in name_lower for x in ['spinach', 'broccoli', 'cauliflower', 'carrot', 'tomato', 
                                          'lettuce', 'kale', 'pepper', 'cucumber', 'onion', 'mushroom']):
            return "vegetable"
        if any(x in name_lower for x in ['apple', 'banana', 'orange', 'berry', 'grape', 'melon', 
                                          'mango', 'pineapple', 'avocado', 'peach', 'pear']):
            return "fruit"
        if any(x in name_lower for x in ['almond', 'walnut', 'cashew', 'peanut', 'pistachio', 
                                          'chia', 'flax', 'seed', 'nut']):
            return "nuts"
        if any(x in name_lower for x in ['milk', 'yogurt', 'cheese', 'cream', 'butter', 'ghee', 'paneer']):
            return "dairy"
        if any(x in name_lower for x in ['lentil', 'bean', 'chickpea', 'dal', 'pea', 'soy']):
            return "legume"
        if any(x in name_lower for x in ['oil', 'butter', 'ghee', 'lard', 'shortening']):
            return "oil_fat"
        if any(x in name_lower for x in ['juice', 'tea', 'coffee', 'soda', 'drink', 'beverage', 'smoothie']):
            return "liquid"
        return "other"
    
    def _detect_cooking_method(self, food_name: str) -> CookingMethod:
        name_lower = food_name.lower()
        
        if any(x in name_lower for x in ['grilled', 'grill']):
            return CookingMethod.GRILLED
        elif any(x in name_lower for x in ['baked', 'bake', 'roasted', 'roast']):
            return CookingMethod.BAKED
        elif any(x in name_lower for x in ['fried', 'fry']):
            return CookingMethod.FRIED
        elif any(x in name_lower for x in ['steamed', 'steam']):
            return CookingMethod.STEAMED
        elif any(x in name_lower for x in ['boiled', 'boil']):
            return CookingMethod.BOILED
        elif any(x in name_lower for x in ['cooked', 'cook']):
            return CookingMethod.COOKED
        elif any(x in name_lower for x in ['raw', 'fresh']):
            return CookingMethod.FRESH
        else:
            return CookingMethod.RAW
    
    def _parse_serving_size(self, serving_size: str) -> Dict[str, float]:
        conversions = {
            'cup': 240,
            'tbsp': 15,
            'tablespoon': 15,
            'tsp': 5,
            'teaspoon': 5,
            'oz': 28.35,
            'fl oz': 29.57,
            'piece': 50,
            'slice': 30,
            'whole': 50
        }
        
        result = {}
        
        serving_lower = serving_size.lower()
        for unit, grams in conversions.items():
            if unit in serving_lower:
                match = re.search(r'(\d+(?:/\d+)?|\d+\.\d+)', serving_lower)
                if match:
                    qty_str = match.group(1)
                    if '/' in qty_str:
                        num, denom = qty_str.split('/')
                        qty = float(num) / float(denom)
                    else:
                        qty = float(qty_str)
                    
                    result[serving_size] = qty * grams
                else:
                    result[serving_size] = grams 
                break
        
        if not result:
            result["1 serving"] = 100
        
        return result
    
    def add_food(self, food: FoodItem):
        self.foods[food.name.lower()] = food
        for alias in food.aliases:
            self.foods[alias.lower()] = food
    
    def lookup(self, food_name: str) -> Optional[FoodItem]:
        return self.foods.get(food_name.lower())
    def fuzzy_search(self, query: str, limit: int = 5) -> List[FoodItem]:
        from rapidfuzz import process, fuzz
        
        matches = process.extract(
            query.lower(),
            self.foods.keys(),
            scorer=fuzz.token_sort_ratio,
            limit=limit
        )
        
        seen = set()
        results = []
        for match_key, score, _ in matches:
            food = self.foods[match_key]
            if food.name not in seen:
                seen.add(food.name)
                results.append(food)
        
        return results
    
    def get_by_category(self, category: str) -> List[FoodItem]:
        seen = set()
        results = []
        for food in self.foods.values():
            if food.category == category and food.name not in seen:
                seen.add(food.name)
                results.append(food)
        return results
    
    def get_suitable_for_diabetes(self) -> List[FoodItem]:
        seen = set()
        results = []
        for food in self.foods.values():
            if food.suitable_for_diabetes and food.name not in seen:
                seen.add(food.name)
                results.append(food)
        return results

UNIT_TO_GRAMS = {
    "cup": 240.0,          
    "fl oz": 29.57,       
    "tbsp": 15.0,         
    "tablespoon": 15.0,
    "tsp": 5.0,             
    "teaspoon": 5.0,
    "oz": 28.35,        
    "g": 1.0,               
    "gram": 1.0,
    "kg": 1000.0,          
    "piece": 50.0,         
    "slice": 30.0,     
    "whole": 50.0,        
}

CATEGORY_CUP_WEIGHTS = {
    "grain": 195.0,        
    "vegetable": 150.0,     
    "fruit": 125.0,      
    "liquid": 240.0,       
    "legume": 180.0,       
    "dairy": 245.0,        
    "nuts": 135.0,         
    "seeds": 150.0,        
}


def convert_portion_to_grams(food_item: FoodItem, quantity: float, unit: PortionUnit) -> float:
    portion_str = f"{int(quantity)} {unit.value}" if quantity == int(quantity) else f"{quantity} {unit.value}"
    
    if portion_str in food_item.typical_portion_g:
        db_weight = food_item.typical_portion_g[portion_str]
        logger.debug(f"Exact DB match: {portion_str} = {db_weight}g")
        return db_weight
    unit_str = unit.value.lower()
    if unit_str == "cup":
        base_weight_per_unit = CATEGORY_CUP_WEIGHTS.get(food_item.category, 240.0)
        total_grams = quantity * base_weight_per_unit
        logger.debug(f"Category-based cup conversion: {quantity} cups × {base_weight_per_unit}g = {total_grams}g")
        return total_grams
    
    if unit_str in UNIT_TO_GRAMS:
        base_weight_per_unit = UNIT_TO_GRAMS[unit_str]
        total_grams = quantity * base_weight_per_unit
        logger.debug(f"Standard conversion: {quantity} {unit_str} × {base_weight_per_unit}g = {total_grams}g")
        return total_grams
    if unit_str in ["piece", "slice", "whole"]:
        if food_item.typical_portion_g:
            reference_weight = list(food_item.typical_portion_g.values())[0]
            total_grams = quantity * reference_weight
            logger.debug(f"Fallback piece conversion: {quantity} × {reference_weight}g = {total_grams}g")
            return total_grams
        return quantity * UNIT_TO_GRAMS.get(unit_str, 50.0)
    
    logger.warning(f"Unknown unit '{unit_str}', using 100g fallback")
    return quantity * 100.0


def calculate_nutrition_for_portion(
    food_item: FoodItem,
    quantity: float,
    unit: PortionUnit
) -> CalculatedNutrition:
    grams = convert_portion_to_grams(food_item, quantity, unit)
    multiplier = grams / 100.0
    
    nutrition = food_item.nutrition_per_100g
    
    carbs = nutrition.carbs * multiplier
    fiber = nutrition.fiber * multiplier
    return CalculatedNutrition(
        calories=nutrition.calories * multiplier,
        protein_g=nutrition.protein * multiplier,
        carbs_g=carbs,
        fat_g=nutrition.fat * multiplier,
        fiber_g=fiber,
        net_carbs_g=max(carbs - fiber, 0),
        sodium_mg=nutrition.sodium * multiplier,
        sugar_g=nutrition.sugar * multiplier,
        cholesterol_mg=nutrition.cholesterol * multiplier,
        iodine_mcg=nutrition.iodine * multiplier
    )

class FoodCategoryBounds(BaseModel):
    min_grams: float = Field(gt=0, description="Minimum realistic portion in grams")
    max_grams: float = Field(gt=0, description="Maximum realistic portion in grams")
    display_unit: str = Field(description="Human-friendly unit for display")
    
    def to_display_range(self) -> str:
        min_val = self.min_grams / 28.35 if self.display_unit == "oz" else self.min_grams / 15 if self.display_unit == "tbsp" else self.min_grams / 240 if self.display_unit == "cup" else self.min_grams
        max_val = self.max_grams / 28.35 if self.display_unit == "oz" else self.max_grams / 15 if self.display_unit == "tbsp" else self.max_grams / 240 if self.display_unit == "cup" else self.max_grams
        return f"{min_val:.1f}-{max_val:.1f} {self.display_unit}"


CATEGORY_BOUNDS: Dict[str, FoodCategoryBounds] = {
    "protein": FoodCategoryBounds(min_grams=85, max_grams=227, display_unit="oz"),  
    "grain": FoodCategoryBounds(min_grams=97.5, max_grams=390, display_unit="cup"),  
    "vegetable": FoodCategoryBounds(min_grams=75, max_grams=450, display_unit="cup"),  
    "fruit": FoodCategoryBounds(min_grams=62.5, max_grams=375, display_unit="cup"),  
    "nuts": FoodCategoryBounds(min_grams=9, max_grams=30, display_unit="tbsp"),  
    "seeds": FoodCategoryBounds(min_grams=3.5, max_grams=21, display_unit="tbsp"),  
    "dairy": FoodCategoryBounds(min_grams=122.5, max_grams=490, display_unit="cup"),  
    "legume": FoodCategoryBounds(min_grams=99, max_grams=297, display_unit="cup"),  
    "oil_fat": FoodCategoryBounds(min_grams=5, max_grams=30, display_unit="tbsp"),  
    "liquid": FoodCategoryBounds(min_grams=118, max_grams=473, display_unit="fl oz"),  
}


def get_portion_bounds(food_item: FoodItem) -> tuple[float, float]:
    bounds = CATEGORY_BOUNDS.get(food_item.category)
    if bounds:
        return (bounds.min_grams, bounds.max_grams)
    return (50, 300)

try:
    REDIS_AVAILABLE = True
    class NutritionCache:        
        def __init__(self, redis_url: str = None):
            if redis_url is None:
                redis_url = os.getenv("REDIS_URL_AI")
            
            try:
                ca_cert_path = os.getenv("REDIS_CA_CERT_PATH")
                connection_kwargs = {"decode_responses": True}
                if ca_cert_path and os.path.exists(ca_cert_path):
                    connection_kwargs["ssl_cert_reqs"] = ssl.CERT_REQUIRED
                    connection_kwargs["ssl_ca_certs"] = ca_cert_path
                
                self.redis_client = redis.from_url(redis_url, **connection_kwargs)
                self.redis_client.ping()
                logger.info("Redis nutrition cache connected")
            except Exception as e:
                logger.warning(f"Redis unavailable, using in-memory cache: {e}")
                self.redis_client = None
                self._memory_cache = {}
        
        def _make_key(self, food_name: str, quantity: float, unit: str) -> str:
            raw = f"{food_name.lower()}:{quantity}:{unit}"
            return f"nutrition:{hashlib.md5(raw.encode()).hexdigest()}"
        def get(self, food_name: str, quantity: float, unit: str) -> Optional[Dict]:
            key = self._make_key(food_name, quantity, unit)
            
            if self.redis_client:
                try:
                    cached = self.redis_client.get(key)
                    if cached:
                        return json.loads(cached)
                except Exception as e:
                    logger.debug(f"Redis get error: {e}")
            else:
                return self._memory_cache.get(key)
            return None
        def set(self, food_name: str, quantity: float, unit: str, nutrition: Dict, ttl: int = 86400):
            key = self._make_key(food_name, quantity, unit)
            if self.redis_client:
                try:
                    self.redis_client.setex(key, ttl, json.dumps(nutrition))
                except Exception as e:
                    logger.debug(f"Redis set error: {e}")
            else:
                self._memory_cache[key] = nutrition
    
    NUTRITION_CACHE = NutritionCache()
    
except ImportError:
    REDIS_AVAILABLE = False
    NUTRITION_CACHE = None
    logger.info("Redis not installed - caching disabled")
NUTRITION_DB = NutritionDatabase()



class AdjusterIntent(BaseModel):
    action_type: str = Field(
        description="Type of action: 'add' (add more of an item), 'remove' (remove an item), or 'swap' (replace an item)"
    )
    target_meals: List[str] = Field(
        description="List of meals to modify (e.g., ['Breakfast'], ['Lunch', 'Dinner'])"
    )
    target_item_to_remove: Optional[str] = Field(
        default=None,
        description="The specific food item to swap/remove (e.g., 'Zucchini', 'Chicken breast'). Required for 'swap' and 'remove' actions."
    )
    item_to_add: Optional[str] = Field(
        default=None,
        description="The food item to add (e.g., 'chicken', 'vegetables'). Required for 'add' and 'swap' actions."
    )
    is_dietary_conflict: bool = Field(
        default=False,
        description="True if the request explicitly mentions a food that violates dietary preferences"
    )
    
    @property
    def target_meal(self) -> str:
        return self.target_meals[0] if self.target_meals else ""
    
    @field_validator('target_meals', mode='before')
    @classmethod
    def normalize_meal_names(cls, v) -> List[str]:
        if isinstance(v, str):
            v = [v]
        return [meal.strip().title() for meal in v]
    
    @field_validator('target_item_to_remove', 'item_to_add')
    @classmethod
    def clean_item_name(cls, v: Optional[str]) -> Optional[str]:
        return v.strip().lower() if v else None
    
    @field_validator('action_type')
    @classmethod
    def validate_action(cls, v: str) -> str:
        v = v.lower().strip()
        # Map 'create' to 'add' for backwards compatibility
        if v == 'create':
            v = 'add'
        if v not in ['add', 'remove', 'swap']:
            raise ValueError(f"action_type must be 'add', 'remove', or 'swap', got '{v}'")
        return v
