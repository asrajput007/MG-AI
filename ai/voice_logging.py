import json
import logging
import os
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
from rapidfuzz import fuzz, process
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()
logger = logging.getLogger(__name__)
try:
    from helpers.utils import azure_client
except ImportError:
    logger.warning("Could not import azure_client from helpers.utils")
    azure_client = None

class VoiceLoggingException(Exception):
    """Base exception for voice logging errors"""
    def __init__(self, message: str, error_code: str = "VOICE_LOGGING_ERROR"):
        self.message = message
        self.error_code = error_code
        super().__init__(self.message)


class DatabaseTimeoutError(VoiceLoggingException):
    def __init__(self, message: str = "Database operation timed out or failed to load"):
        super().__init__(message, error_code="DATABASE_TIMEOUT")


class AzureAPIFailure(VoiceLoggingException):
    def __init__(self, message: str = "Azure OpenAI API call failed"):
        super().__init__(message, error_code="AZURE_API_FAILURE")


class InvalidQueryType(VoiceLoggingException):
    def __init__(self, message: str = "Query is not related to meal logging"):
        super().__init__(message, error_code="INVALID_QUERY_TYPE")

class ExtractedFood(BaseModel):
    food_name: str = Field(description="Name of the food item")
    quantity: Optional[float] = Field(default=None, description="Quantity amount (numeric)")
    unit: Optional[str] = Field(default=None, description="Unit of measurement (g, kg, cups, pieces, etc.)")


class FoodExtractionResult(BaseModel):
    foods: List[ExtractedFood] = Field(description="List of extracted food items with quantities")


class NutrientData(BaseModel):
    unit: str = Field(default="100g", description="Standard unit (always 100g)")
    protein: float = Field(description="Protein in grams")
    carbs: float = Field(description="Carbohydrates in grams")
    fat: float = Field(description="Fat in grams")
    fiber: float = Field(description="Dietary fiber in grams")
    sugar: float = Field(description="Total sugar in grams")
    cholesterol: float = Field(description="Cholesterol in mg")
    sodium: float = Field(description="Sodium in mg")
    calories: float = Field(description="Calories in kcal")


class FoodOption(BaseModel):
    food_name: str = Field(description="Name of the food")
    micros: NutrientData = Field(description="Nutritional data per 100g")


class MealLogEntry(BaseModel):
    food_name: str = Field(description="Name of the food")
    quantity: Optional[float] = Field(default=1, description="Numerical quantity value (e.g., 2, 0.5, 1)")
    household_measure: Optional[str] = Field(default=None, description="Household measure unit only (e.g., 'plates', 'cups', 'pieces', 'bowls')")
    source: str = Field(description="Always 'llm_estimation' (LLM-powered nutritional estimation)")
    micros: NutrientData = Field(description="Total nutritional data for the consumed quantity")
    micros_per_unit: NutrientData = Field(description="Nutritional data for 1 unit (e.g., 1 apple if user ate 2 apples)")
    has_multiple_options: bool = Field(default=False, description="True if multiple options are available")
    options: Optional[List[FoodOption]] = Field(default=None, description="List of alternative food options to choose from")


class MealLoggingResponse(BaseModel):
    meal_logging: List[MealLogEntry] = Field(description="List of foods with nutritional data")
    needs_clarification: bool = Field(default=False, description="True if user needs to select from multiple options")


class QueryClassificationResult(BaseModel):
    is_meal_logging: bool = Field(description="True if query is about logging food/meals")
    confidence: float = Field(description="Confidence score 0-1")
    reasoning: str = Field(description="Brief explanation of classification")


class NonMealQueryResponse(BaseModel):
    error: str = Field(default="INVALID_QUERY_TYPE")
    message: str = Field(description="Instruction to user")
    suggestion: str = Field(description="What to do instead")

class FoodDatabase:
    
    def __init__(self):
        self.foods_data: List[Dict] = []
        self.loaded = False
        self._load_database()
    
    def _load_database(self):
        try:
            import time
            start_time = time.time()
            base_dir = Path(__file__).parent.parent
            foods_path = base_dir / "datasets" / "foods 1.json"
            
            if not foods_path.exists():
                logger.error(f"Food database not found at: {foods_path}")
                return
            
            with open(foods_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.foods_data = data.get('foods', [])
                
            load_time = time.time() - start_time
            if load_time > 10.0:
                raise DatabaseTimeoutError(f"Database loading exceeded timeout (took {load_time:.2f}s)")
            
            self.loaded = True
            logger.info(f" Loaded {len(self.foods_data)} food items from database in {load_time:.2f}s")
        
        except FileNotFoundError:
            logger.error(f" Database file not found: {foods_path}")
            raise DatabaseTimeoutError(f"Database file not found: {foods_path}")
        except json.JSONDecodeError as e:
            logger.error(f" Invalid JSON in database file: {e}")
            raise DatabaseTimeoutError(f"Database file corrupted: {e}")
        except DatabaseTimeoutError:
            raise
        except Exception as e:
            logger.error(f" Unexpected error loading database: {e}")
            raise DatabaseTimeoutError(f"Failed to load database: {str(e)}")
    
    def _validate_preparation_match(self, user_query: str, matched_name: str) -> Tuple[bool, float]:
        user_lower = user_query.lower().strip()
        match_lower = matched_name.lower().strip()
        
        preparation_conflicts = {
            'raw': ['cooked', 'boiled', 'steamed', 'grilled', 'fried', 'baked', 'roasted', 'sauteed', 'braised'],
            'cooked': ['raw', 'uncooked'],
            'fried': ['boiled', 'steamed', 'grilled', 'baked'],
            'grilled': ['fried', 'boiled', 'steamed'],
            'baked': ['fried', 'boiled', 'steamed'],
            'steamed': ['fried', 'baked', 'roasted'],
            'fresh': ['dried', 'frozen', 'canned'],
            'dried': ['fresh', 'wet'],
            'whole': ['sliced', 'diced', 'chopped'],
        }
        
        user_preparation = None
        for prep in preparation_conflicts.keys():
            if prep in user_lower:
                user_preparation = prep
                break
        
        if not user_preparation:
            return (True, 0.0)
        
        conflicts = preparation_conflicts.get(user_preparation, [])
        for conflicting_prep in conflicts:
            if conflicting_prep in match_lower:
                logger.warning(
                    f" Preparation conflict: User wants '{user_preparation}' but database has '{conflicting_prep}' "
                    f"('{user_query}' vs '{matched_name}')"
                )
                return (False, 1.0)  
        
        if user_preparation in match_lower:
            logger.info(f" Preparation match: '{user_preparation}' in both user query and database")
            return (True, 0.0)  
        
        logger.info(
            f" Preparation ambiguous: User wants '{user_preparation}' but database '{matched_name}' "
            f"doesn't specify - applying 30% confidence penalty"
        )
        return (True, 0.3) 
    
    def search_food(self, food_name: str, threshold: int = 85) -> Optional[Dict]:
        if not self.loaded or not self.foods_data:
            logger.warning("Food database not loaded")
            return None
        
        search_query = food_name.lower().strip()
        
        food_names = [food.get('common_name', '') for food in self.foods_data]
        
        for idx, name in enumerate(food_names):
            if name.lower() == search_query:
                logger.info(f" Exact match: '{food_name}' → '{name}'")
                return self.foods_data[idx]
        
        search_words = set(search_query.split())
        word_matches = []
        
        for idx, name in enumerate(food_names):
            name_lower = name.lower()
            name_words = set(name_lower.split())
            
            priority = 0
            match_type = None
            
            if search_query in name_words:
                match_type = "word"
                
                cooking_methods = ["cooked", "baked", "grilled", "steamed", "boiled", "roasted", 
                                 "fried", "sauteed", "braised", "stewed"]
                
                if name_lower.startswith(search_query + " ") or name_lower.startswith(search_query):
                    priority = 100
                    if any(prep in name_lower for prep in cooking_methods):
                        priority += 10
                
                elif any(name_lower.startswith(method + " " + search_query) for method in cooking_methods):
                    priority = 95
                    if search_query in ["rice", "wheat", "quinoa", "barley", "oats"]:
                        priority += 10  
                
                else:
                    priority = 50
                    if any(prep in name_lower for prep in cooking_methods):
                        priority += 10
                
                if len(search_query.split()) == 1:  
                    if any(combo in name_lower for combo in ["milk", "noodles", "flour", "powder", "oil", "sauce", "soup", "cake", "bread", "cracker", "cookie", "pudding", "drink", "beverage"]):
                        priority -= 30
                
                if any(simple in name_lower for simple in ["plain", "white", "brown", "basmati", "jasmine"]):
                    priority += 5
                
                if len(search_query.split()) == 1: 
                    if any(separator in name_lower for separator in [" and ", " & ", " with "]):
                        priority -= 20
                
                if any(flavored in name_lower for flavored in ["honey", "butter", "cheese", "chocolate", "sweet"]):
                    priority -= 10
                
                word_matches.append((idx, name, len(name), priority, match_type))
            
            elif search_words and len(search_words) > 1 and search_words.issubset(name_words):
                match_type = "multi-word"
                priority = 80
                word_matches.append((idx, name, len(name), priority, match_type))        
        if word_matches:
            word_matches.sort(key=lambda x: (-x[3], x[2]))
            idx, name, _, priority, match_type = word_matches[0]
            
            is_valid, penalty = self._validate_preparation_match(food_name, name)
            if not is_valid:
                logger.warning(f" Rejected word match due to preparation conflict: '{food_name}' → '{name}'")
            else:
                logger.info(f" {match_type.title()} match: '{food_name}' → '{name}' (priority: {priority})")
                return self.foods_data[idx]
        
        result = process.extractOne(
            food_name,
            food_names,
            scorer=fuzz.WRatio,
            score_cutoff=threshold
        )
        
        if result:
            matched_name, score, index = result
            food_data = self.foods_data[index]
            
            search_lower = search_query.lower()
            matched_lower = matched_name.lower()
            
            if len(search_lower) < len(matched_lower) * 0.6:
                if search_lower in matched_lower and search_lower not in matched_lower.split():
                    logger.info(f" Rejected substring match: '{food_name}' → '{matched_name}' (score: {score})")
                    return None
            
            logger.info(f" Fuzzy match: '{food_name}' → '{matched_name}' (score: {score})")
            return food_data
        
        logger.info(f" No database match found for: '{food_name}' (threshold: {threshold})")
        return None
    
    def search_food_multiple(self, food_name: str, threshold: int = 85, max_results: int = 5) -> List[Dict]:
 
        if not self.loaded or not self.foods_data:
            logger.warning("Food database not loaded")
            return []
        
        search_query = food_name.lower().strip()
        
        food_names = [food.get('common_name', '') for food in self.foods_data]
        
        exact_match = None
        for idx, name in enumerate(food_names):
            if name.lower() == search_query:
                exact_match = self.foods_data[idx]
                logger.info(f" Exact match found: '{food_name}' → '{name}' (returning single result)")
                return [exact_match]  
        
        search_words = set(search_query.split())
        word_matches = []
        
        for idx, name in enumerate(food_names):
            name_lower = name.lower()
            name_words = set(name_lower.split())
            
            priority = 0
            match_type = None
            
            cooking_methods = ["cooked", "baked", "grilled", "steamed", "boiled", "roasted", 
                             "fried", "sauteed", "braised", "stewed"]
            
            if search_query in name_words:
                match_type = "word"
                
                if name_lower.startswith(search_query + " ") or name_lower.startswith(search_query):
                    priority = 100
                    if any(prep in name_lower for prep in cooking_methods):
                        priority += 10
                
                elif any(name_lower.startswith(method + " " + search_query) for method in cooking_methods):
                    priority = 95
                    if search_query in ["rice", "wheat", "quinoa", "barley", "oats"]:
                        priority += 10
                
                else:
                    priority = 50
                    if any(prep in name_lower for prep in cooking_methods):
                        priority += 10
                
                if len(search_query.split()) == 1:
                    if any(combo in name_lower for combo in ["milk", "noodles", "flour", "powder", "oil", "sauce", "soup", "cake", "bread", "cracker", "cookie", "pudding", "drink", "beverage"]):
                        priority -= 30
                
                if any(simple in name_lower for simple in ["plain", "white", "brown", "basmati", "jasmine"]):
                    priority += 5
                
                if len(search_query.split()) == 1:
                    if any(separator in name_lower for separator in [" and ", " & ", " with "]):
                        priority -= 20
                
                if any(flavored in name_lower for flavored in ["honey", "butter", "cheese", "chocolate", "sweet"]):
                    priority -= 10
                
                if priority > 0:
                    word_matches.append((idx, name, len(name), priority, match_type))
            
            elif search_words and len(search_words) > 1 and search_words.issubset(name_words):
                match_type = "multi-word"
                priority = 80
                word_matches.append((idx, name, len(name), priority, match_type))
        
        if word_matches:
            word_matches.sort(key=lambda x: (-x[3], x[2]))
            
            results = []
            for idx, name, _, priority, match_type in word_matches[:max_results * 2]:  
                is_valid, penalty = self._validate_preparation_match(food_name, name)
                if is_valid:  
                    results.append(self.foods_data[idx])
                    if len(results) >= max_results:
                        break
            
            if results:
                logger.info(f" Found {len(results)} options for '{food_name}': {[r['common_name'] for r in results]}")
                return results
        
        fuzzy_results = process.extract(
            food_name,
            food_names,
            scorer=fuzz.WRatio,
            score_cutoff=threshold,
            limit=max_results
        )
        
        if fuzzy_results:
            results = []
            for matched_name, score, index in fuzzy_results:
                food_data = self.foods_data[index]
                
                search_lower = search_query.lower()
                matched_lower = matched_name.lower()
                
                if len(search_lower) < len(matched_lower) * 0.6:
                    if search_lower in matched_lower and search_lower not in matched_lower.split():
                        continue
                
                is_valid, penalty = self._validate_preparation_match(food_name, matched_name)
                if not is_valid:
                    continue 
                
                adjusted_threshold = threshold * (1 + penalty)
                if score < adjusted_threshold:
                    continue  
                
                results.append(food_data)
            
            if results:
                logger.info(f" Fuzzy found {len(results)} options for '{food_name}': {[r['common_name'] for r in results]}")
                return results
        
        logger.info(f" No database matches found for: '{food_name}' (threshold: {threshold})")
        return []
    
    def extract_nutrients(self, food_data: Dict) -> Optional[NutrientData]:
        """
        Extract nutritional data per 100g from food database entry
        
        Args:
            food_data: Food entry from database
        
        Returns:
            NutrientData object or None if data is missing
        """
        try:
            nutrients = food_data.get('per_100g_nutrients', {})
            
            return NutrientData(
                unit="100g",
                protein=nutrients.get('protein_g', 0.0),
                carbs=nutrients.get('total_carbs_g', 0.0),
                fat=nutrients.get('total_fat_g', 0.0),
                fiber=nutrients.get('dietary_fiber_g', 0.0),
                sugar=nutrients.get('total_sugar_g', 0.0),
                cholesterol=nutrients.get('cholesterol_mg', 0.0),
                sodium=nutrients.get('sodium_mg', 0.0),
                calories=nutrients.get('calories_kcal', 0.0)
            )
        
        except Exception as e:
            logger.error(f"Error extracting nutrients: {e}")
            return None
    
    def extract_piece_weight_from_db(self, food_data: Dict) -> Optional[float]:

        try:
            for i in range(1, 6):
                portion_label_key = f'portion_{i}_label'
                portion_grams_key = f'portion_{i}_grams'
                
                label = food_data.get(portion_label_key, '').lower()
                grams = food_data.get(portion_grams_key)
                
                if label and grams:
                    piece_indicators = ['1 piece', '1 whole', '1 item', '1 medium', '1 small', '1 large', '1 roti', '1 idli']
                    if any(indicator in label for indicator in piece_indicators):
                        logger.info(f"🗄️ DATABASE PIECE WEIGHT: {food_data.get('common_name', 'unknown')} = {grams}g/piece (from '{label}')")
                        return float(grams)
            
            return None
        
        except Exception as e:
            logger.error(f"Error extracting piece weight from database: {e}")
            return None



class VoiceLoggingAgent:
    """
    Main agent for voice logging and nutritional data extraction using LLM
    
    Features:
    - 🌍 Global measurement support: handles cups, bowls, plates, handfuls, grams, ounces, etc.
    - 🔢 Fractional quantities: processes "half apple", "quarter cup", "three-quarter bowl"
    - 📏 Size modifiers: understands "small bowl", "medium plate", "large serving"
    - 🤖 LLM-powered estimation: uses AI to estimate nutrients for all foods
    - 📊 Automatic scaling: adjusts all nutrients based on portion size (per-serving and total)
    
    Note: This agent uses LLM exclusively for nutritional estimation, providing flexible
    and accurate results for any food item worldwide without database dependencies.
    """
    
    EDGE_CASE_UNITS = {
        # Handfuls
        'handful': 30.0,
        'handfuls': 30.0,
        'a handful': 30.0,
        'small handful': 20.0,
        'large handful': 40.0,
        
        # Pinches
        'pinch': 0.5,
        'pinches': 0.5,
        'a pinch': 0.5,
        
        # Slices
        'slice': 30.0,
        'slices': 30.0,
        'a slice': 30.0,
        'small slice': 20.0,
        'thin slice': 20.0,
        'medium slice': 30.0,
        'large slice': 45.0,
        'thick slice': 45.0,
        
        # Bowls
        'bowl': 200.0,
        'bowls': 200.0,
        'a bowl': 200.0,
        'small bowl': 150.0,
        'medium bowl': 225.0,
        'large bowl': 300.0,
        
        # Plates
        'plate': 250.0,
        'plates': 250.0,
        'a plate': 250.0,
        'small plate': 150.0,
        'medium plate': 250.0,
        'large plate': 350.0,
        
        # Spoons
        'spoon': 15.0,
        'spoons': 15.0,
        'a spoon': 15.0,
        'tablespoon': 15.0,
        'tablespoons': 15.0,
        'tbsp': 15.0,
        'teaspoon': 5.0,
        'teaspoons': 5.0,
        'tsp': 5.0,
        
        # Servings/Portions
        'serving': 100.0,
        'servings': 100.0,
        'portion': 100.0,
        'portions': 100.0,
        'small serving': 75.0,
        'medium serving': 100.0,
        'large serving': 150.0,
        
        # Cups
        'cup': 200.0,
        'cups': 200.0,
        'a cup': 200.0,
        'small cup': 150.0,
        'medium cup': 200.0,
        'large cup': 300.0,
        'half cup': 100.0,
        'quarter cup': 50.0,
        'three-quarter cup': 150.0,
        
        # Glasses (volumetric units for liquids: 1ml ≈ 1g for water-based liquids)
        'glass': 240.0,
        'glasses': 240.0,
        'a glass': 240.0,
        'small glass': 180.0,
        'medium glass': 240.0,
        'large glass': 350.0,
    }

    PIECE_WEIGHTS = {
        # Eggs
        'egg': 50.0,
        'eggs': 50.0,
        'small egg': 40.0,
        'small eggs': 40.0,
        'medium egg': 50.0,
        'medium eggs': 50.0,
        'large egg': 60.0,
        'large eggs': 60.0,
        'extra large egg': 70.0,
        'extra large eggs': 70.0,
        'egg white': 33.0,
        'egg whites': 33.0,
        'egg yolk': 17.0,
        'egg yolks': 17.0,
        
        # Apples
        'apple': 180.0,
        'apples': 180.0,
        'small apple': 130.0,
        'small apples': 130.0,
        'medium apple': 180.0,
        'medium apples': 180.0,
        'large apple': 240.0,
        'large apples': 240.0,
        
        # Bananas
        'banana': 120.0,
        'bananas': 120.0,
        'small banana': 90.0,
        'small bananas': 90.0,
        'medium banana': 120.0,
        'medium bananas': 120.0,
        'large banana': 150.0,
        'large bananas': 150.0,
        
        # Oranges
        'orange': 130.0,
        'oranges': 130.0,
        'small orange': 100.0,
        'small oranges': 100.0,
        'medium orange': 130.0,
        'medium oranges': 130.0,
        'large orange': 180.0,
        'large oranges': 180.0,
        
        # Pears
        'pear': 180.0,
        'pears': 180.0,
        'small pear': 130.0,
        'small pears': 130.0,
        'medium pear': 180.0,
        'medium pears': 180.0,
        'large pear': 230.0,
        'large pears': 230.0,
        
        # Peaches
        'peach': 150.0,
        'peaches': 150.0,
        'small peach': 110.0,
        'small peaches': 110.0,
        'medium peach': 150.0,
        'medium peaches': 150.0,
        'large peach': 200.0,
        'large peaches': 200.0,
        
        # Plums
        'plum': 65.0,
        'plums': 65.0,
        'small plum': 50.0,
        'small plums': 50.0,
        'medium plum': 65.0,
        'medium plums': 65.0,
        'large plum': 85.0,
        'large plums': 85.0,
        
        # Kiwis
        'kiwi': 75.0,
        'kiwis': 75.0,
        'small kiwi': 60.0,
        'small kiwis': 60.0,
        'medium kiwi': 75.0,
        'medium kiwis': 75.0,
        'large kiwi': 95.0,
        'large kiwis': 95.0,
        
        # Mangoes
        'mango': 200.0,
        'mangos': 200.0,
        'mangoes': 200.0,
        'small mango': 150.0,
        'small mangos': 150.0,
        'small mangoes': 150.0,
        'medium mango': 200.0,
        'medium mangos': 200.0,
        'medium mangoes': 200.0,
        'large mango': 300.0,
        'large mangos': 300.0,
        'large mangoes': 300.0,
        
        # Strawberries
        'strawberry': 12.0,
        'strawberries': 12.0,
        'small strawberry': 8.0,
        'small strawberries': 8.0,
        'medium strawberry': 12.0,
        'medium strawberries': 12.0,
        'large strawberry': 18.0,
        'large strawberries': 18.0,
        
        # Grapes
        'grape': 5.0,
        'grapes': 5.0,
        
        # Cherries
        'cherry': 8.0,
        'cherries': 8.0,
        
        # Blueberries
        'blueberry': 0.5,
        'blueberries': 0.5,
        
        # Raspberries
        'raspberry': 4.0,
        'raspberries': 4.0,
        
        # Dates
        'date': 7.0,
        'dates': 7.0,
        'small date': 5.0,
        'small dates': 5.0,
        'medium date': 7.0,
        'medium dates': 7.0,
        'large date': 10.0,
        'large dates': 10.0,
        
        # Figs
        'fig': 50.0,
        'figs': 50.0,
        'small fig': 40.0,
        'small figs': 40.0,
        'medium fig': 50.0,
        'medium figs': 50.0,
        'large fig': 65.0,
        'large figs': 65.0,
        
        # Apricots
        'apricot': 35.0,
        'apricots': 35.0,
        'small apricot': 28.0,
        'small apricots': 28.0,
        'medium apricot': 35.0,
        'medium apricots': 35.0,
        'large apricot': 45.0,
        'large apricots': 45.0,
        
        # Lemons
        'lemon': 58.0,
        'lemons': 58.0,
        'small lemon': 45.0,
        'small lemons': 45.0,
        'medium lemon': 58.0,
        'medium lemons': 58.0,
        'large lemon': 75.0,
        'large lemons': 75.0,
        
        # Limes
        'lime': 44.0,
        'limes': 44.0,
        'small lime': 35.0,
        'small limes': 35.0,
        'medium lime': 44.0,
        'medium limes': 44.0,
        'large lime': 55.0,
        'large limes': 55.0,
        
        # Tomatoes
        'tomato': 120.0,
        'tomatoes': 120.0,
        'small tomato': 85.0,
        'small tomatoes': 85.0,
        'cherry tomato': 20.0,
        'cherry tomatoes': 20.0,
        'medium tomato': 120.0,
        'medium tomatoes': 120.0,
        'large tomato': 180.0,
        'large tomatoes': 180.0,
        
        # Potatoes
        'potato': 170.0,
        'potatoes': 170.0,
        'small potato': 120.0,
        'small potatoes': 120.0,
        'medium potato': 170.0,
        'medium potatoes': 170.0,
        'large potato': 230.0,
        'large potatoes': 230.0,
        
        # Carrots
        'carrot': 60.0,
        'carrots': 60.0,
        'small carrot': 45.0,
        'small carrots': 45.0,
        'medium carrot': 60.0,
        'medium carrots': 60.0,
        'large carrot': 80.0,
        'large carrots': 80.0,
        
        # Cucumbers
        'cucumber': 300.0,
        'cucumbers': 300.0,
        'small cucumber': 200.0,
        'small cucumbers': 200.0,
        'medium cucumber': 300.0,
        'medium cucumbers': 300.0,
        'large cucumber': 400.0,
        'large cucumbers': 400.0,
        
        # Onions
        'onion': 110.0,
        'onions': 110.0,
        'small onion': 70.0,
        'small onions': 70.0,
        'medium onion': 110.0,
        'medium onions': 110.0,
        'large onion': 150.0,
        'large onions': 150.0,
        
        # Garlic
        'garlic': 5.0,
        'garlic clove': 5.0,
        'garlic cloves': 5.0,
        'small garlic clove': 3.0,
        'small garlic cloves': 3.0,
        'medium garlic clove': 5.0,
        'medium garlic cloves': 5.0,
        'large garlic clove': 7.0,
        'large garlic cloves': 7.0,
        
        # Nuts & Seeds (per piece weights)
        'almond': 1.2,
        'almonds': 1.2,
        'walnut': 5.0,
        'walnuts': 5.0,
        'cashew': 1.5,
        'cashews': 1.5,
        'pistachio': 0.7,
        'pistachios': 0.7,
        'peanut': 0.9,
        'peanuts': 0.9,
        'pecan': 2.0,
        'pecans': 2.0,
        'hazelnut': 1.3,
        'hazelnuts': 1.3,
        'macadamia': 2.0,
        'macadamias': 2.0,
        'brazil nut': 5.0,
        'brazil nuts': 5.0,
        
        # Breads (slice weights)
        'bread': 30.0,
        'bread slice': 30.0,
        'slice of bread': 30.0,
        'small bread slice': 25.0,
        'medium bread slice': 30.0,
        'large bread slice': 40.0,
        'thin slice': 25.0,
        'thick slice': 40.0,
        
        # Indian Breads
        'roti': 40.0,
        'rotis': 40.0,
        'small roti': 30.0,
        'small rotis': 30.0,
        'medium roti': 40.0,
        'medium rotis': 40.0,
        'large roti': 55.0,
        'large rotis': 55.0,
        
        'chapati': 40.0,
        'chapatis': 40.0,
        'small chapati': 30.0,
        'small chapatis': 30.0,
        'medium chapati': 40.0,
        'medium chapatis': 40.0,
        'large chapati': 55.0,
        'large chapatis': 55.0,
        
        'paratha': 60.0,
        'parathas': 60.0,
        'small paratha': 45.0,
        'small parathas': 45.0,
        'medium paratha': 60.0,
        'medium parathas': 60.0,
        'large paratha': 80.0,
        'large parathas': 80.0,
        
        'naan': 90.0,
        'naans': 90.0,
        'small naan': 70.0,
        'small naans': 70.0,
        'medium naan': 90.0,
        'medium naans': 90.0,
        'large naan': 120.0,
        'large naans': 120.0,
        
        'idli': 40.0,
        'idlis': 40.0,
        'small idli': 30.0,
        'small idlis': 30.0,
        'medium idli': 40.0,
        'medium idlis': 40.0,
        'large idli': 55.0,
        'large idlis': 55.0,
        
        'dosa': 80.0,
        'dosas': 80.0,
        'small dosa': 60.0,
        'small dosas': 60.0,
        'medium dosa': 80.0,
        'medium dosas': 80.0,
        'large dosa': 110.0,
        'large dosas': 110.0,
    }
    
    def __init__(self):
        self.food_db = FoodDatabase()
        self.azure_client = azure_client
    
    def _is_liquid_food(self, food_name: str) -> bool:
        """
        Detect if food should be measured in ml instead of grams
        
        Args:
            food_name: Name of the food
        
        Returns:
            True if food is a liquid (use ml), False otherwise (use g)
        """
        if not food_name:
            return False
        
        food_lower = food_name.lower()
        
        # Liquid keywords
        liquid_keywords = [
            'milk', 'water', 'juice', 'tea', 'coffee', 'drink', 'beverage',
            'smoothie', 'shake', 'lassi', 'buttermilk', 'coconut water',
            'soda', 'cola', 'lemonade', 'wine', 'beer', 'alcohol',
            'soup', 'broth', 'stock', 'dal water', 'rasam',
            'oil', 'ghee', 'honey', 'syrup', 'sauce (liquid)', 'gravy',
            'yogurt drink', 'kefir', 'kombucha'
        ]
        
        return any(keyword in food_lower for keyword in liquid_keywords)
    
    async def classify_query(self, user_input: str) -> QueryClassificationResult:
        """
        Classify if the user's query is about meal logging or something else.
        
        Args:
            user_input: Raw text from user
        
        Returns:
            QueryClassificationResult with classification outcome
        
        Raises:
            AzureAPIFailure: If Azure OpenAI API call fails
        """
        if not self.azure_client:
            raise AzureAPIFailure("Azure OpenAI client not initialized")
        
        try:
            classification_prompt = f"""You are a query classifier for a nutritional meal logging system.

Analyze this user input and determine if it's about logging food/meals/beverages they ate/drank/consumed, or if it's something else (advice, general questions, chat).

User Input: "{user_input}"

Classify as:
- is_meal_logging: true if user is reporting food/beverages they consumed (e.g., "I ate 2 apples", "had chicken for lunch", "consumed 200g rice", "I drank water", "had coffee", "drank juice")
- is_meal_logging: false if user is asking questions, seeking advice, or chatting (e.g., "What should I eat?", "How many calories in rice?", "Hello", "I want to lose weight")

IMPORTANT: Logging beverages (water, coffee, tea, juice, milk, etc.) is MEAL LOGGING - classify as TRUE.

Provide confidence (0.0 to 1.0) and brief reasoning."""
            
            response = self.azure_client.chat.completions.create(
                model=os.getenv("AZURE_DEPLOYMENT_NAME", "gpt-4o-mini"),
                messages=[
                    {"role": "system", "content": "You are a query intent classifier. Respond with structured JSON only."},
                    {"role": "user", "content": classification_prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=200
            )
            
            content = response.choices[0].message.content
            data = json.loads(content)
            result = QueryClassificationResult(**data)
            
            logger.info(f" Classification: is_meal_logging={result.is_meal_logging}, confidence={result.confidence:.2f}")
            return result
            
        except Exception as e:
            logger.error(f" Query classification failed: {e}")
            raise AzureAPIFailure(f"Failed to classify query: {str(e)}")
    
    async def normalize_user_query(self, user_input: str) -> str:
        """
        Normalize user's fumbled/misspelled query into standard format
        
        Args:
            user_input: Raw user input (may contain typos, grammar errors, etc.)
        
        Returns:
            Normalized, corrected query string
        
        Raises:
            AzureAPIFailure: If Azure OpenAI API call fails
        """
        if not self.azure_client:
            logger.warning("Azure client not available, using raw input without normalization")
            return user_input
        
        normalization_prompt = f"""You are a query normalization assistant for meal logging.

Your task is to:
1. Fix spelling mistakes (e.g., "appl" → "apple", "banan" → "banana")
2. Correct grammar errors
3. Standardize food names (e.g., "2 appls" → "2 apples")
4. Preserve ALL quantities and units EXACTLY as stated
5. Keep the meaning identical - DO NOT add or remove information

CRITICAL RULES:
✅ Fix typos: "I ate 2 appls" → "I ate 2 apples"
✅ Standardize: "i had banan" → "I had banana"
✅ Preserve quantities: "150g rise" → "150g rice" (keep 150g!)
❌ DO NOT change meaning: "I had coffee" should NOT become "I had coffee with milk"
❌ DO NOT add details: "I ate rice" should NOT become "I ate 100g rice"
❌ DO NOT remove details: "I ate 2 apples" should NOT become "I ate apples"

Examples:
- Input: "i ate 2 appls and 1 banan"
  Output: "I ate 2 apples and 1 banana"

- Input: "had 150ml coffe"
  Output: "had 150ml coffee"

- Input: "i drank glassof water"
  Output: "I drank glass of water"

- Input: "ate 2 egg with spnach"
  Output: "ate 2 eggs with spinach"

User Input: "{user_input}"

Return ONLY the normalized query as plain text (no JSON, no quotes, no formatting)."""

        try:
            response = self.azure_client.chat.completions.create(
                model=os.getenv("AZURE_DEPLOYMENT_NAME", "gpt-4o-mini"),
                messages=[
                    {"role": "system", "content": "You are a query normalization assistant. Return only the corrected query text."},
                    {"role": "user", "content": normalization_prompt}
                ],
                temperature=0.1,
                max_tokens=150
            )
            
            normalized_query = response.choices[0].message.content.strip()
            
            # Remove quotes if LLM wrapped the output
            normalized_query = normalized_query.strip('"\'')
            
            logger.info(f"🔧 QUERY NORMALIZATION: '{user_input}' → '{normalized_query}'")
            return normalized_query
            
        except Exception as e:
            logger.error(f"⚠️ Query normalization failed: {e}, using original input")
            return user_input
    
    async def extract_foods_from_text(self, user_input: str) -> FoodExtractionResult:
        """
        Parse natural language input to extract food names and quantities
        
        Args:
            user_input: User's natural language text (e.g., "I ate 2 apples and 1 pear")
        
        Returns:
            FoodExtractionResult with extracted foods
        
        Raises:
            AzureAPIFailure: If Azure OpenAI API call fails
        """
        if not self.azure_client:
            raise AzureAPIFailure("Azure OpenAI client not initialized")
        
        system_prompt = """You are a nutritional data extraction assistant specialized in parsing meal descriptions.

Your task is to parse natural language input and extract:
1. Food items mentioned (INCLUDING SIZE MODIFIERS if present)
2. Quantities (numeric values OR descriptive amounts)
3. Units (grams, ml, fl oz, pieces, handful, pinch, slice, bowl, etc.)

⚠️ CRITICAL: EXTRACT ONLY WHAT THE USER ACTUALLY SAID - DO NOT ADD ASSUMPTIONS!

🔧 SIZE MODIFIERS (HIGHEST PRIORITY FOR ACCURACY):
✅ If user mentions size (small, medium, large, thin, thick, etc.), INCLUDE IT in food_name:
   - "I ate 2 small apples" → food_name: "small apples", quantity: 2
   - "I had a large banana" → food_name: "large banana", quantity: 1
   - "I ate thin roti" → food_name: "thin roti", quantity: null
   - "I had 3 big tomatoes" → food_name: "large tomatoes", quantity: 3
   - "I ate tiny carrots" → food_name: "small carrots", quantity: null

✅ Normalize size words to standard terms:
   - big → large
   - tiny/mini → small
   - regular/normal → medium
   - huge/jumbo/extra large → extra large

❌ If user does NOT mention size, do NOT add it:
   - "I ate 2 apples" → food_name: "apples" (NOT "medium apples")

🔧 MEASUREMENT PRIORITY (NEW - CRITICAL FOR ACCURACY):
1. If user provides BOTH weight/volume AND household measure (e.g., "150g poha... 1 bowl"):
   → PRIORITIZE the weight/volume measurement (150g)
   → IGNORE the household measure (1 bowl) - it's redundant
2. If user provides weight/volume only (e.g., "150ml coffee"):
   → Use that measurement
3. If user provides household measure only (e.g., "1 bowl of rice"):
   → Use that household measure
4. If user provides neither:
   → Set both quantity and unit to null

COMPOUND DISH RULES (HIGHEST PRIORITY):
🔧 UPDATED: Better separation of combo foods
✅ If user says "X with Y and Z" where Y and Z are GARNISHES/MINOR:
   → Extract as ONE item: "X with Y and Z"
✅ If user says "X with Y" where Y is a SEPARATE SUBSTANTIAL FOOD:
   → Extract as TWO items: "X" and "Y"
   → Example: "poha with salad" → ["poha", "salad"] (2 items)
   → Example: "eggs with spinach" → ["eggs with spinach"] (1 item, spinach is ingredient)
✅ Use common sense: If Y sounds like a side dish (salad, fruit, yogurt, etc.), split it
❌ If user says "X and Y" as separate list → Extract as TWO items: "X" and "Y"

Examples:
- "eggs with spinach and tomato" → ONE item: "eggs with spinach and tomato" (ingredients)
- "chicken with rice" → ONE item: "chicken with rice" (common combo)
- "poha with salad" → TWO items: "poha", "salad" (salad is a side)
- "eggs and toast" → TWO items: "eggs", "toast"
- "150g poha and 1 bowl" → ONE item: "poha" with quantity=150, unit="g" (ignore "1 bowl")

DO NOT ADD WORDS THE USER DIDN'T SAY:
❌ NEVER add: "salted", "cooked", "grilled", "boiled", "raw", etc. unless user said it
❌ NEVER invent preparation methods
❌ NEVER label specific foods as "meal" - use the actual food name
✅ ONLY use the EXACT words from user input

QUANTITY RULES:
- For weight units (g, kg, oz, lb), extract the exact numeric value
- For volume units (ml, l, fl oz), extract the exact numeric value
- For descriptive portions (handful, pinch, slice, bowl, plate, spoon), use quantity=1 and the descriptive unit
- For counts (pieces, items), use the numeric count
- For size modifiers on FOOD (small, medium, large), include in the FOOD NAME: "small apple", "large plate"
- For size modifiers on HOUSEHOLD UNITS (small cup, large bowl), include in the UNIT: quantity=1, unit="small cup"
- For fractional words (half, quarter, three-quarter), convert to decimal: half=0.5, quarter=0.25
- ⚠️ ARTICLES "a" / "an" / "one" = quantity 1:
  - "I ate a banana" → quantity: 1, unit: "pieces"
  - "I had an apple" → quantity: 1, unit: "pieces"
  - "I ate one orange" → quantity: 1, unit: "pieces"
  - "I had a small cup of rice" → quantity: 1, unit: "small cup"
  - "I ate a large bowl of soup" → quantity: 1, unit: "large bowl"
- ⚠️ If NO quantity AND NO article, set BOTH quantity AND unit to null (do NOT invent quantities!)

CORRECT Examples:
- "I had eggs with spinach and tomato" → [{"food_name": "eggs with spinach and tomato", "quantity": null, "unit": null}]
- "I ate 2 small eggs with spinach" → [{"food_name": "small eggs with spinach", "quantity": 2, "unit": "pieces"}]
- "I ate 2 eggs with spinach" → [{"food_name": "eggs with spinach", "quantity": 2, "unit": "pieces"}]
- "I ate a banana" → [{"food_name": "banana", "quantity": 1, "unit": "pieces"}]
- "I had an apple" → [{"food_name": "apple", "quantity": 1, "unit": "pieces"}]
- "I ate a standard apple" → [{"food_name": "apple", "quantity": 1, "unit": "pieces"}]
- "I had one orange" → [{"food_name": "orange", "quantity": 1, "unit": "pieces"}]
- "I had chicken curry with rice" → [{"food_name": "chicken curry with rice", "quantity": null, "unit": null}]
- "I ate paneer and roti" → [{"food_name": "paneer", "quantity": null, "unit": null}, {"food_name": "roti", "quantity": null, "unit": null}]
- "I had 200g steak" → [{"food_name": "steak", "quantity": 200, "unit": "g"}]
- "I had 150ml black coffee" → [{"food_name": "black coffee", "quantity": 150, "unit": "ml"}]
- "I drank 12 fl oz orange juice" → [{"food_name": "orange juice", "quantity": 12, "unit": "fl oz"}]
- "I had 150g poha... 1 bowl" → [{"food_name": "poha", "quantity": 150, "unit": "g"}] (ignore "1 bowl" - redundant)
- "I had poha with salad" → [{"food_name": "poha", "quantity": null, "unit": null}, {"food_name": "salad", "quantity": null, "unit": null}]
- "I had a bowl of rice" → [{"food_name": "rice", "quantity": 1, "unit": "bowl"}]
- "I ate grilled chicken" → [{"food_name": "grilled chicken", "quantity": null, "unit": null}]
- "I had a small bowl of paneer curry" → [{"food_name": "paneer curry", "quantity": 1, "unit": "small bowl"}]
- "I ate 2 small apples" → [{"food_name": "small apples", "quantity": 2, "unit": "pieces"}]
- "I had a large banana" → [{"food_name": "large banana", "quantity": 1, "unit": "pieces"}]
- "I ate 3 big tomatoes" → [{"food_name": "large tomatoes", "quantity": 3, "unit": "pieces"}]
- "I had tiny carrots" → [{"food_name": "small carrots", "quantity": null, "unit": null}]
- "I ate 2 thick rotis" → [{"food_name": "large rotis", "quantity": 2, "unit": "pieces"}]
- "I had a thin slice of bread" → [{"food_name": "thin slice", "quantity": 1, "unit": "pieces"}]
- "I ate a small cup of raspberry pie" → [{"food_name": "raspberry pie", "quantity": 1, "unit": "small cup"}]
- "I had a large glass of milk" → [{"food_name": "milk", "quantity": 1, "unit": "large glass"}]
- "I ate a medium bowl of rice" → [{"food_name": "rice", "quantity": 1, "unit": "medium bowl"}]

WRONG Examples (DO NOT DO THIS):
❌ "I had eggs with spinach" → [{"food_name": "scrambled eggs", ...}] - NEVER add "scrambled"!
❌ "I had spinach" → [{"food_name": "salted spinach", ...}] - NEVER add "salted"!
❌ "I had eggs" → [{"food_name": "eggs", "quantity": 2, "unit": "pieces"}] - NEVER invent quantities!
❌ "I had tomato" → [{"food_name": "tomato", "quantity": 25, "unit": "g"}] - NEVER invent weights!
❌ "I had idli" → [{"food_name": "meal", ...}] - NEVER use generic "meal" - use actual food name!
❌ "I had 150g poha... 1 bowl" → [{"food_name": "poha", "quantity": 1, "unit": "bowl"}] - WRONG! Prioritize weight (150g)!
❌ "I ate 2 apples" → [{"food_name": "medium apples", ...}] - NEVER add size if user didn't say it!
❌ "I had a small banana" → [{"food_name": "banana", ...}] - WRONG! Must include "small" in food_name!
❌ "I ate big carrots" → [{"food_name": "big carrots", ...}] - WRONG! Normalize "big" to "large"!
❌ "I had a small cup of rice" → [{"food_name": "small rice", ...}] - WRONG! Size goes in UNIT, not food_name!
❌ "I ate a large bowl of soup" → [{"food_name": "large soup", ...}] - WRONG! Size goes in UNIT ("large bowl")!

Return ONLY valid JSON matching the schema."""

        user_prompt = f"""Extract food items from this text:

"{user_input}"

Return a JSON object with a 'foods' array containing food_name, quantity, and unit for each item."""

        try:
            response = self.azure_client.chat.completions.create(
                model=os.getenv("AZURE_DEPLOYMENT_NAME", "gpt-4o-mini"),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=500
            )
            
            content = response.choices[0].message.content
            data = json.loads(content)
            
            logger.info(f" AI extraction result: {data}")
            
            result = FoodExtractionResult(**data)
            
            for food in result.foods:
                logger.info(f"    Extracted: '{food.food_name}' (qty: {food.quantity} {food.unit or ''})")
            
            return result
        
        except json.JSONDecodeError as e:
            logger.error(f" Failed to parse AI response as JSON: {e}")
            raise AzureAPIFailure(f"Invalid JSON response from Azure OpenAI: {str(e)}")
        except Exception as e:
            logger.error(f" Food extraction failed: {e}")
            raise AzureAPIFailure(f"Failed to extract foods from text: {str(e)}")
    
    async def estimate_nutrients_with_ai(self, food_name: str) -> Optional[NutrientData]:
        """
        Use Azure OpenAI to estimate nutritional profile for unknown foods
        
        Args:
            food_name: Name of the food item
        
        Returns:
            NutrientData with estimated nutritional values
        """
        if not self.azure_client:
            logger.error("Azure OpenAI client not available for nutrient estimation")
            return None
        
        system_prompt = """You are a highly accurate nutritional analysis expert.
Given a food name, provide the nutritional values STRICTLY PER 100g based on standard USDA or nutritional databases.

CRITICAL REQUIREMENTS:
1. ALL values MUST be normalized to exactly 100g portions (not per serving, not per piece)
2. Your estimates must be realistic and based on common nutritional knowledge
3. For raw foods, use raw nutritional values; for cooked foods, use cooked values

Return ONLY valid JSON with these exact keys (all per 100g):
- protein: grams per 100g
- carbs: grams per 100g
- fat: grams per 100g
- fiber: grams per 100g
- sugar: grams per 100g
- cholesterol: mg per 100g
- sodium: mg per 100g
- calories: kcal per 100g

Example for "grilled chicken breast" (per 100g):
{
  "protein": 31.0,
  "carbs": 0.0,
  "fat": 3.6,
  "fiber": 0.0,
  "sugar": 0.0,
  "cholesterol": 85.0,
  "sodium": 74.0,
  "calories": 165.0
}

IMPORTANT: Do NOT provide per-serving or per-piece values. ONLY per 100g values."""

        user_prompt = f"""Provide nutritional values STRICTLY PER 100g (not per serving, not per piece) for: "{food_name}"

Ensure all values are normalized to exactly 100 grams of this food.

Return JSON with protein, carbs, fat, fiber, sugar, cholesterol, sodium, and calories - all per 100g."""

        try:
            response = self.azure_client.chat.completions.create(
                model=os.getenv("AZURE_DEPLOYMENT_NAME", "gpt-4o-mini"),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=300
            )
            
            content = response.choices[0].message.content
            data = json.loads(content)
            logger.info(f" AI estimated nutrients for '{food_name}': {data}")
            return NutrientData(unit="100g", **data)
        
        except Exception as e:
            logger.error(f" AI nutrient estimation failed: {e}")
            return None
    
    def _extract_household_measure(self, unit: Optional[str], food_data: Optional[Dict] = None, food_name: Optional[str] = None) -> Optional[str]:
        """
        Extract household measure from unit and food data
        PRIORITY: Return friendly household units (cups, glasses, bowls) NOT scientific units
        
        Args:
            unit: Unit string (e.g., "g", "cups", "pieces")
            food_data: Database entry for food
            food_name: Name of food (for liquid detection)
        
        Returns:
            Household measure string (e.g., "cups", "glasses", "pieces") or "Weight-based"
        """
        if unit:
            unit_lower = unit.lower().strip()
            
            household_measures = {
                'plates': ['plate', 'plates'],
                'bowls': ['bowl', 'bowls'],
                'cups': ['cup', 'cups'],
                'glasses': ['glass', 'glasses'],
                'pieces': ['piece', 'pieces', 'pcs'],
                'slices': ['slice', 'slices'],
                'spoons': ['spoon', 'spoons', 'tablespoon', 'tablespoons', 'teaspoon', 'teaspoons'],
                'handfuls': ['handful', 'handfuls'],
                'servings': ['serving', 'servings', 'portion', 'portions']
            }
            
            for household_measure, variants in household_measures.items():
                if any(variant in unit_lower for variant in variants):
                    return household_measure
            
            # If user specified non-household unit (g, kg, oz, lb), check database for household measure
            weight_units = ['g', 'gram', 'grams', 'kg', 'kilogram', 'oz', 'ounce', 'lb', 'pound']
            if any(weight in unit_lower for weight in weight_units):
                if food_data:
                    db_household = food_data.get('household_serving_size') or food_data.get('serving_description')
                    if db_household:
                        logger.info(f"📏 Using database household measure: '{db_household}'")
                        return db_household
                # No database household measure - return generic label
                return "Weight-based"
            
            # Other units (return as-is if reasonable)
            return unit_lower
        
        # Priority 2: Database household measure
        if food_data:
            db_household = (
                food_data.get('household_serving_size') or 
                food_data.get('serving_description') or
                food_data.get('household_measure') or
                food_data.get('default_serving')
            )
            if db_household:
                logger.info(f"📏 No user unit specified, using database default: '{db_household}'")
                return db_household
        
        # Priority 3: Default based on food type
        if self._is_liquid_food(food_name, food_data):
            return "cups"  # Default household measure for liquids
        return "Weight-based"  # Generic fallback for solids
    
    def _is_liquid_food(self, food_name: Optional[str], food_data: Optional[Dict] = None) -> bool:
        """
        Detect if a food is liquid (beverages, soups, etc.)
        
        Args:
            food_name: Name of the food
            food_data: Database entry (can check food_group or other metadata)
        
        Returns:
            True if food is liquid, False otherwise
        """
        if not food_name:
            return False
        
        name_lower = food_name.lower()
        
        # Liquid keywords
        liquid_keywords = [
            'milk', 'juice', 'water', 'coffee', 'tea', 'soda', 'cola',
            'smoothie', 'shake', 'lassi', 'buttermilk', 'cream',
            'soup', 'broth', 'stock', 'dal', 'rasam', 'sambar',
            'oil', 'vinegar', 'sauce', 'gravy', 'curry',
            'wine', 'beer', 'liquor', 'alcohol', 'vodka', 'whiskey',
            'syrup', 'honey', 'molasses', 'nectar',
            'beverage', 'drink', 'liquid'
        ]
        
        # Check food name
        if any(keyword in name_lower for keyword in liquid_keywords):
            return True
        
        # Check database food_group if available
        if food_data:
            food_group = food_data.get('food_group', '').lower()
            if any(keyword in food_group for keyword in ['beverage', 'drink', 'liquid', 'soup']):
                return True
        
        return False
    
    def _convert_quantity_for_display(self, quantity: float, unit: str, food_name: Optional[str] = None, food_data: Optional[Dict] = None) -> float:
        """
        Convert quantity to match household measure for display
        
        Args:
            quantity: Quantity in grams (or original unit)
            unit: Original unit
            food_name: Name of food
            food_data: Database entry (to find household measure conversions)
        
        Returns:
            Converted quantity value for household measure
        """
        unit_lower = unit.lower() if unit else ""
        
        # If user specified household units (cups, pieces, etc.), keep quantity as-is
        household_units = ['cup', 'glass', 'bowl', 'plate', 'piece', 'slice', 'spoon', 'handful', 'serving']
        if any(hu in unit_lower for hu in household_units):
            return quantity
        
        # For weight-based units, try to convert to database household measure
        if any(u in unit_lower for u in ['g', 'kg', 'gram', 'kilogram', 'oz', 'ounce', 'lb', 'pound']):
            # Convert to grams first
            grams = quantity
            if 'kg' in unit_lower or 'kilogram' in unit_lower:
                grams = quantity * 1000
            elif 'oz' in unit_lower or 'ounce' in unit_lower:
                grams = quantity * 28.3495
            elif 'lb' in unit_lower or 'pound' in unit_lower:
                grams = quantity * 453.592
            
            # Try to find database portion conversion
            if food_data:
                # Check portion_X_grams and portion_X_label fields
                for i in range(1, 6):
                    portion_grams = food_data.get(f'portion_{i}_grams')
                    portion_label = food_data.get(f'portion_{i}_label', '')
                    
                    if portion_grams and portion_label:
                        # Check if this is a single household unit (1 cup, 1 glass, etc.)
                        label_lower = portion_label.lower()
                        if label_lower.startswith('1 ') or label_lower.startswith('one '):
                            # Calculate how many of these units
                            num_units = grams / float(portion_grams)
                            logger.info(f"📏 Converted {grams}g to {num_units:.1f} based on '{portion_label}' = {portion_grams}g")
                            return round(num_units, 1)
            
            # No database conversion found - return as fractional value suitable for "Weight-based" display
            # This will be shown in Portion Weight as fl oz or oz
            return round(quantity, 1)
        
        return quantity
    
    def _normalize_edge_case_unit(self, quantity: Optional[float], unit: Optional[str]) -> Tuple[float, str]:
        if not unit:
            return (quantity or 1.0, unit or 'serving')
        
        unit_lower = unit.lower().strip()
        
        if unit_lower in self.EDGE_CASE_UNITS:
            grams_per_unit = self.EDGE_CASE_UNITS[unit_lower]
            total_grams = (quantity or 1.0) * grams_per_unit
            logger.info(f" Normalized '{quantity} {unit}' → {total_grams}g (using {grams_per_unit}g per {unit_lower})")
            return (total_grams, 'g')
        
        for edge_unit, grams in self.EDGE_CASE_UNITS.items():
            if unit_lower.startswith(edge_unit) or edge_unit in unit_lower:
                total_grams = (quantity or 1.0) * grams
                logger.info(f" Normalized '{quantity} {unit}' → {total_grams}g (matched '{edge_unit}': {grams}g)")
                return (total_grams, 'g')
        
        return (quantity, unit)
    
    def _convert_to_grams(self, quantity: Optional[float], unit: Optional[str], food_name: Optional[str] = None) -> float:
        """
        Convert quantity + unit to grams multiplier (relative to 100g base)
        
        Args:
            quantity: Numerical quantity (e.g., 2, 0.5)
            unit: Unit of measurement (e.g., "g", "cups", "pieces")
            food_name: Name of food (used for piece-based conversions)
        
        Returns:
            Multiplier relative to 100g (e.g., 2.0 means 200g)
        """
    
    def _extract_size_multiplier(self, food_name: str) -> Tuple[float, str]:
        """
        Extract size modifier from food name and return multiplier + base food name
        
        Args:
            food_name: Food name potentially with size modifier (e.g., "small apple", "large banana")
        
        Returns:
            Tuple of (multiplier, base_food_name)
            - multiplier: 0.7 for small, 1.0 for medium/default, 1.4 for large
            - base_food_name: food name without size modifier
        """
        if not food_name:
            return (1.0, food_name)
        
        food_lower = food_name.lower().strip()
        
        # Size mappings with multipliers (based on USDA standards)
        size_mappings = {
            'extra large': 1.6,
            'extra-large': 1.6,
            'jumbo': 1.6,
            'huge': 1.6,
            'large': 1.4,
            'big': 1.4,
            'medium': 1.0,
            'regular': 1.0,
            'normal': 1.0,
            'small': 0.7,
            'tiny': 0.7,
            'mini': 0.7,
            'thin': 0.75,
            'thick': 1.3,
        }
        
        # Check for size modifiers at the start of the food name
        for size_word, multiplier in size_mappings.items():
            # Match "small apple" or "small apples"
            if food_lower.startswith(size_word + ' '):
                base_food = food_name[len(size_word):].strip()
                logger.info(f"🔍 SIZE DETECTED: '{food_name}' → multiplier={multiplier}x, base='{base_food}'")
                return (multiplier, base_food)
        
        # No size modifier found
        return (1.0, food_name)
    
    def _convert_to_grams(self, quantity: Optional[float], unit: Optional[str], food_name: Optional[str] = None, food_data: Optional[Dict] = None) -> float:
        """
        Convert quantity + unit to grams multiplier (relative to 100g base)
        
        Args:
            quantity: Numerical quantity (e.g., 2, 0.5)
            unit: Unit of measurement (e.g., "g", "cups", "pieces")
            food_name: Name of food (used for piece-based conversions)
            food_data: Database entry for food (for dynamic piece weights)
        
        Returns:
            Multiplier relative to 100g (e.g., 2.0 means 200g)
        """
        if not quantity or not unit:
            return 1.0  
        
        normalized_quantity, normalized_unit = self._normalize_edge_case_unit(quantity, unit)
        
        if not normalized_unit:
            return 1.0
        
        unit_lower = normalized_unit.lower().strip()
        
        # Weight-based units
        if unit_lower in ['g', 'gram', 'grams']:
            return normalized_quantity / 100.0
        elif unit_lower in ['kg', 'kilogram', 'kilograms']:
            return (normalized_quantity * 1000.0) / 100.0
        elif unit_lower in ['oz', 'ounce', 'ounces']:
            return (normalized_quantity * 28.35) / 100.0
        elif unit_lower in ['lb', 'pound', 'pounds']:
            return (normalized_quantity * 453.592) / 100.0
        elif unit_lower in ['mg', 'milligram', 'milligrams']:
            return (normalized_quantity / 1000.0) / 100.0
        
        # 🔧 FIX: Volumetric units for liquids (CRITICAL BUG FIX)
        # These were missing, causing 150ml → 15,000g instead of 150g
        elif unit_lower in ['ml', 'milliliter', 'milliliters', 'millilitre', 'millilitres']:
            # For water-based liquids: 1ml ≈ 1g (density ≈ 1.0)
            # For oils/syrups: density varies (0.9-1.4), but we use 1.0 as safe default
            grams = normalized_quantity * 1.0  # 150ml → 150g
            logger.info(f"🔧 VOLUMETRIC FIX: {normalized_quantity}ml → {grams}g (1ml ≈ 1g for liquids)")
            return grams / 100.0  # Return multiplier relative to 100g
        elif unit_lower in ['l', 'liter', 'liters', 'litre', 'litres']:
            # 1 liter = 1000ml ≈ 1000g for water-based liquids
            grams = normalized_quantity * 1000.0  # 1L → 1000g
            logger.info(f"🔧 VOLUMETRIC FIX: {normalized_quantity}L → {grams}g (1L = 1000ml ≈ 1000g)")
            return grams / 100.0
        elif unit_lower in ['fl oz', 'fl. oz', 'fluid ounce', 'fluid ounces', 'floz']:
            # 1 fl oz ≈ 29.5735ml ≈ 29.5735g for water-based liquids
            grams = normalized_quantity * 29.5735  # 12 fl oz → 354.88g
            logger.info(f"🔧 VOLUMETRIC FIX: {normalized_quantity} fl oz → {grams:.1f}g (1 fl oz ≈ 29.57g)")
            return grams / 100.0
        elif 'cl' == unit_lower or unit_lower in ['centiliter', 'centiliters', 'centilitre', 'centilitres']:
            # 1 cl = 10ml ≈ 10g for water-based liquids
            grams = normalized_quantity * 10.0
            logger.info(f"🔧 VOLUMETRIC FIX: {normalized_quantity}cl → {grams}g (1cl = 10ml ≈ 10g)")
            return grams / 100.0
        
        # Piece-based units with dynamic database lookup
        piece_units = ['piece', 'pieces', 'pc', 'pcs', 'whole', 'item', 'items']
        if any(piece_unit in unit_lower for piece_unit in piece_units):
            if quantity:
                # PRIORITY 1: Try database piece weight (dynamic, works for ANY food worldwide)
                if food_data:
                    db_piece_weight = self.food_db.extract_piece_weight_from_db(food_data)
                    if db_piece_weight:
                        total_grams = quantity * db_piece_weight
                        logger.info(f"📊 DATABASE PIECE WEIGHT: '{food_name}' = {db_piece_weight}g/piece × {quantity} = {total_grams}g (from database)")
                        return total_grams / 100.0
                
                # PRIORITY 2: Try exact match in PIECE_WEIGHTS dictionary
                if food_name:
                    food_lower = food_name.lower().strip()
                    
                    # Try exact match first
                    if food_lower in self.PIECE_WEIGHTS:
                        grams_per_piece = self.PIECE_WEIGHTS[food_lower]
                        total_grams = quantity * grams_per_piece
                        logger.info(f"📏 EXACT PIECE MATCH: '{food_name}' = {grams_per_piece}g/piece × {quantity} = {total_grams}g")
                        return total_grams / 100.0
                    
                    # Try substring match (e.g., "small apple" matches "small apple" or "apple" in keys)
                    for food_key, weight_per_piece in self.PIECE_WEIGHTS.items():
                        if food_key in food_lower or food_lower in food_key:
                            total_grams = quantity * weight_per_piece
                            logger.info(f"📏 SUBSTRING MATCH: '{food_name}' matched '{food_key}' = {weight_per_piece}g/piece × {quantity} = {total_grams}g")
                            return total_grams / 100.0
                    
                    # PRIORITY 3: Try intelligent size-based fallback
                    # Extract size modifier and try matching base food
                    size_multiplier, base_food = self._extract_size_multiplier(food_name)
                    if size_multiplier != 1.0:
                        # Size modifier found - try matching base food
                        base_food_lower = base_food.lower().strip()
                        
                        # Try exact match on base food
                        if base_food_lower in self.PIECE_WEIGHTS:
                            base_weight = self.PIECE_WEIGHTS[base_food_lower]
                            adjusted_weight = base_weight * size_multiplier
                            total_grams = quantity * adjusted_weight
                            logger.info(f"🎯 SIZE-ADJUSTED MATCH: '{food_name}' → base '{base_food}' ({base_weight}g) × {size_multiplier} = {adjusted_weight}g/piece × {quantity} = {total_grams}g")
                            return total_grams / 100.0
                        
                        # Try substring match on base food
                        for food_key, weight_per_piece in self.PIECE_WEIGHTS.items():
                            if food_key in base_food_lower or base_food_lower in food_key:
                                adjusted_weight = weight_per_piece * size_multiplier
                                total_grams = quantity * adjusted_weight
                                logger.info(f"🎯 SIZE-ADJUSTED SUBSTRING: '{food_name}' → matched '{food_key}' ({weight_per_piece}g) × {size_multiplier} = {adjusted_weight}g/piece × {quantity} = {total_grams}g")
                                return total_grams / 100.0
                
                # PRIORITY 4: Final fallback - 100g per piece (conservative default)
                logger.info(f"⚠️ No piece weight data for '{food_name}', using 100g/piece default")
                return float(quantity)
        
        # Unknown unit handling - CRITICAL: Use reasonable default instead of 100g
        # Changed from 100g default to 15g (1 tablespoon equivalent) to prevent unrealistic portions
        if quantity:
            # Treat unknown descriptive units as ~15g per unit (similar to tablespoon)
            # This prevents "spoon of X" from becoming 100g
            logger.warning(
                f"⚠️ UNKNOWN UNIT: '{unit}' not recognized! "
                f"Using tablespoon equivalent: {quantity} × 15g = {quantity * 15}g. "
                f"If this unit needs a different conversion, add it to EDGE_CASE_UNITS."
            )
            return (quantity * 15.0) / 100.0  # Convert to multiplier relative to 100g
        
        # No quantity specified - use small default (1 tablespoon equivalent)
        logger.warning(f"⚠️ Unknown unit '{unit}' with no quantity, using 15g default (1 tablespoon equivalent)")
        return 0.15  # 15g
    
    def _scale_nutrients(self, nutrients: NutrientData, quantity: Optional[float], unit: Optional[str], food_name: Optional[str] = None, food_data: Optional[Dict] = None) -> NutrientData:
        """
        Scale nutrients from 100g base to actual portion size
        
        Args:
            nutrients: Base nutrients (per 100g)
            quantity: Numerical quantity value
            unit: Unit of measurement
            food_name: Name of food (for piece-based conversions)
            food_data: Database entry for food (for dynamic piece weights)
        
        Returns:
            Scaled NutrientData for actual portion
        """
        scaling_factor = self._convert_to_grams(quantity, unit, food_name, food_data)
        
        if scaling_factor == 1.0:
            return nutrients
        
        actual_grams = scaling_factor * 100.0
        scaled_nutrients = NutrientData(
            unit=f"{actual_grams:.0f}g",  
            protein=round(nutrients.protein * scaling_factor, 2),
            carbs=round(nutrients.carbs * scaling_factor, 2),
            fat=round(nutrients.fat * scaling_factor, 2),
            fiber=round(nutrients.fiber * scaling_factor, 2),
            sugar=round(nutrients.sugar * scaling_factor, 2),
            cholesterol=round(nutrients.cholesterol * scaling_factor, 2),
            sodium=round(nutrients.sodium * scaling_factor, 2),
            calories=round(nutrients.calories * scaling_factor, 1)
        )
        
        logger.info(f" Scaled nutrients from 100g → {actual_grams:.0f}g (factor: {scaling_factor:.2f}x)")
        return scaled_nutrients
    
    def _calculate_per_unit_nutrients(self, nutrients: NutrientData, quantity: Optional[float], unit: Optional[str], food_name: Optional[str] = None, food_data: Optional[Dict] = None) -> NutrientData:
        """
        Calculate nutrients for 1 unit (e.g., if user ate 2 apples, calculate for 1 apple)
        
        Args:
            nutrients: Base nutrients (per 100g)
            quantity: User's total quantity (e.g., 2 for "2 apples")
            unit: Unit of measurement
            food_name: Name of food
            food_data: Database entry for food
        
        Returns:
            NutrientData for 1 unit
        """
        # Calculate per-unit scaling factor
        if quantity and quantity > 0:
            # User specified quantity (e.g., 2 apples) → scale for 1 unit
            per_unit_quantity = 1.0
            per_unit_scaling_factor = self._convert_to_grams(per_unit_quantity, unit, food_name, food_data)
        else:
            # No quantity specified → use 100g as 1 unit
            per_unit_scaling_factor = 1.0
        
        if per_unit_scaling_factor == 1.0:
            # Return nutrients as-is (100g base)
            return nutrients
        
        per_unit_grams = per_unit_scaling_factor * 100.0
        per_unit_nutrients = NutrientData(
            unit=f"{per_unit_grams:.0f}g",
            protein=round(nutrients.protein * per_unit_scaling_factor, 2),
            carbs=round(nutrients.carbs * per_unit_scaling_factor, 2),
            fat=round(nutrients.fat * per_unit_scaling_factor, 2),
            fiber=round(nutrients.fiber * per_unit_scaling_factor, 2),
            sugar=round(nutrients.sugar * per_unit_scaling_factor, 2),
            cholesterol=round(nutrients.cholesterol * per_unit_scaling_factor, 2),
            sodium=round(nutrients.sodium * per_unit_scaling_factor, 2),
            calories=round(nutrients.calories * per_unit_scaling_factor, 1)
        )
        
        logger.info(f" Calculated per-unit nutrients: {per_unit_grams:.0f}g per unit")
        return per_unit_nutrients
    
    async def process_voice_input(self, user_input: str, return_multiple_options: bool = True) -> MealLoggingResponse:
        logger.info(f"📝 Processing voice input: '{user_input}'")
        
        # Step 1: Normalize query to fix typos/fumbled input
        try:
            normalized_input = await self.normalize_user_query(user_input)
            if normalized_input != user_input:
                logger.info(f"✅ Query normalized: '{user_input}' → '{normalized_input}'")
            else:
                logger.info(f"✅ Query unchanged after normalization")
        except Exception as e:
            logger.warning(f"⚠️ Query normalization failed: {e}, using original input")
            normalized_input = user_input
        
        try:
            classification = await self.classify_query(normalized_input)
            if not classification.is_meal_logging and classification.confidence >= 0.7:
                logger.warning(f" Non-meal query detected: {classification.reasoning}")
                raise InvalidQueryType(
                    f"This query appears to be '{classification.reasoning.lower()}' rather than meal logging. "
                    f"Please use the chat interface for general questions, advice, or non-food-logging queries."
                )
            
            if classification.confidence < 0.7:
                logger.info(f" Low confidence classification ({classification.confidence:.2f}), proceeding with caution")
        
        except AzureAPIFailure:
            logger.warning(" Query classification unavailable, proceeding with food extraction")
        
        # Step 2: Extract foods from normalized query
        try:
            extraction_result = await self.extract_foods_from_text(normalized_input)
            logger.info(f" Extracted {len(extraction_result.foods)} food item(s)")
            
            if len(extraction_result.foods) == 0:
                logger.warning(" No food items extracted from input")
                raise InvalidQueryType(
                    "Could not identify any food items in your input. "
                    "Please describe what you ate (e.g., 'I ate 2 apples and 200g rice') or use the chat interface for other queries."
                )
        except AzureAPIFailure:
            raise
        except InvalidQueryType:
            raise
        
        meal_entries: List[MealLogEntry] = []
        needs_clarification = False
        
        # Process each extracted food item using LLM only
        for food_item in extraction_result.foods:
            food_name = food_item.food_name
            quantity = food_item.quantity
            unit = food_item.unit
            
            portion_info = f"{quantity} {unit}" if quantity and unit else "standard 100g"
            logger.info(f"\n Processing: '{food_name}' (portion: {portion_info}) - Using LLM estimation")
            
            try:
                # Use AI to estimate nutritional data
                ai_nutrients = await self.estimate_nutrients_with_ai(food_name)
                
                if ai_nutrients:
                    # ✅ PORTION LOGIC: Respect user's input, intelligent defaults for countable foods
                    if quantity is None and unit is None:
                        # Check if food is a countable item (fruits, vegetables, eggs, breads)
                        food_lower = food_name.lower().strip()
                        is_countable = False
                        
                        # Try exact match or substring match in PIECE_WEIGHTS
                        if food_lower in self.PIECE_WEIGHTS:
                            is_countable = True
                        else:
                            for piece_food in self.PIECE_WEIGHTS.keys():
                                if piece_food in food_lower or food_lower in piece_food:
                                    is_countable = True
                                    break
                        
                        if is_countable:
                            # Countable food without quantity → default to 1 piece
                            final_quantity = 1
                            final_unit = 'pieces'
                            logger.info(f"🎯 Countable food '{food_name}' without quantity → defaulting to 1 piece")
                        else:
                            # Non-countable food (rice, curry, etc.) → default to 100g
                            final_quantity = 100
                            final_unit = 'g'
                            logger.info(f"⚠️ No portion specified for '{food_name}', using default: 100g")
                    elif quantity is not None and unit is None:
                        # User specified count only (e.g., "2 almonds") → infer as pieces
                        final_quantity = quantity
                        final_unit = 'pieces'
                        logger.info(f"✅ User specified quantity: {quantity} pieces")
                    elif quantity is None and unit is not None:
                        # User specified unit only (e.g., "a cup of rice") → use 1 unit
                        final_quantity = 1
                        final_unit = unit
                        logger.info(f"✅ User specified unit: 1 {unit}")
                    else:
                        # User specified both (e.g., "2 cups of rice") → use exact values
                        final_quantity = quantity
                        final_unit = unit
                        logger.info(f"✅ User specified portion: {quantity} {unit}")
                    
                    # Convert for display
                    display_quantity = self._convert_quantity_for_display(final_quantity, final_unit, food_name, None)
                    household_measure = self._extract_household_measure(final_unit, None, food_name)
                    
                    scaled_ai_nutrients = self._scale_nutrients(ai_nutrients, final_quantity, final_unit, food_name, None)
                    per_unit_ai_nutrients = self._calculate_per_unit_nutrients(ai_nutrients, quantity, unit, food_name, None)
                    meal_entries.append(MealLogEntry(
                        food_name=food_name,
                        quantity=display_quantity,
                        household_measure=household_measure,
                        source="llm_estimation",
                        micros=scaled_ai_nutrients,
                        micros_per_unit=per_unit_ai_nutrients,
                        has_multiple_options=False
                    ))
                    logger.info(f"✅ LLM estimation successful for: {food_name}")
                else:
                    logger.error(f"❌ Failed to get nutritional data for: {food_name}")
            except Exception as e:
                logger.error(f"❌ LLM estimation failed for '{food_name}': {e}")
        
        response = MealLoggingResponse(
            meal_logging=meal_entries,
            needs_clarification=needs_clarification
        )
        
        logger.info(f"✅ Processing complete. {len(meal_entries)} food(s) processed using LLM estimation.")
        
        return response
voice_logging_agent = VoiceLoggingAgent()
