
import re
import json
from typing import Dict, List, Tuple, Optional
import logging

logger = logging.getLogger(__name__)


class FoodItemMetadata:
    """Represents metadata for a single food item"""
    def __init__(self, food_name: str, food_id: Optional[str] = None, 
                 ingredients: Optional[List] = None, recipe: Optional[List] = None,
                 line_number: int = 0, meal_type: str = ""):
        self.food_name = food_name
        self.food_id = food_id or "UNKNOWN"
        self.ingredients = ingredients or []
        self.recipe = recipe or []
        self.line_number = line_number
        self.meal_type = meal_type
    
    def to_dict(self) -> Dict:
        """Convert to dictionary format"""
        return {
            "food_name": self.food_name,
            "food_id": self.food_id,
            "ingredients": self.ingredients,
            "recipe": self.recipe,
            "line_number": self.line_number,
            "meal_type": self.meal_type
        }


class MealPlanParser:
    """Parser for meal plan text with embedded metadata"""
    
    # Regex pattern to match the embedded metadata at the end of food item lines
    # Matches: | "food_id": "..." | "ingredients": [...] | "recipe": [...] |
    METADATA_PATTERN = re.compile(
        r'\s*\|\s*"food_id":\s*"([^"]*)"\s*\|\s*"ingredients":\s*(\[.*?\])\s*\|\s*"recipe":\s*(\[.*?\])\s*\|?\s*$',
        re.IGNORECASE
    )
    PLAIN_METADATA_PATTERN = re.compile(
        r'\s*\|\s*FoodID:\s*([^|]+)\s*\|\s*Ingredients:\s*(.*?)\s*\|\s*Recipe:\s*(.*?)\s*$',
        re.IGNORECASE
    )
    
    # Pattern to match meal type headers
    MEAL_HEADER_PATTERN = re.compile(r'^\*\*([A-Za-z\s]+)\*\*$', re.MULTILINE)
    
    # Pattern to match food item lines (with or without leading dash)
    FOOD_ITEM_LINE_PATTERN = re.compile(r'^-?\s*(.+?)\s*\|')
    
    @classmethod
    def parse_meal_plan_text(cls, meal_plan_text: str) -> Tuple[str, List[FoodItemMetadata]]:
        """
        Parse meal plan text to extract metadata and return cleaned text.
        
        Args:
            meal_plan_text: The raw meal plan text with embedded metadata
            
        Returns:
            Tuple of (cleaned_text, list_of_food_metadata)
            - cleaned_text: Markdown text with metadata stripped out
            - list_of_food_metadata: List of FoodItemMetadata objects
        """
        if not meal_plan_text:
            return "", []
        
        lines = meal_plan_text.split('\n')
        cleaned_lines = []
        food_metadata_list = []
        current_meal_type = ""
        
        for line_num, line in enumerate(lines):
            # Check if this is a meal type header
            meal_header_match = cls.MEAL_HEADER_PATTERN.match(line)
            if meal_header_match:
                current_meal_type = meal_header_match.group(1).strip()
                cleaned_lines.append(line)
                continue
            
            # Check if this line contains metadata
            metadata_match = cls.METADATA_PATTERN.search(line)
            
            if metadata_match:
                # Extract metadata
                food_id = metadata_match.group(1)
                ingredients_str = metadata_match.group(2)
                recipe_str = metadata_match.group(3)
                
                # Parse JSON arrays
                try:
                    ingredients = json.loads(ingredients_str)
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse ingredients JSON on line {line_num}: {ingredients_str}")
                    ingredients = []
                
                try:
                    recipe = json.loads(recipe_str)
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse recipe JSON on line {line_num}: {recipe_str}")
                    recipe = []
                
                # Remove metadata from the line
                cleaned_line = cls.METADATA_PATTERN.sub('', line).rstrip()
                cleaned_lines.append(cleaned_line)
                if ingredients:
                    cleaned_lines.append(
                        f"  Ingredients: {', '.join(str(item) for item in ingredients)}"
                    )
                if recipe:
                    cleaned_lines.append(
                        f"  Recipe: {' '.join(str(step) for step in recipe)}"
                    )
                
                # Extract food name from the cleaned line
                food_name = cls._extract_food_name(cleaned_line)
                
                # Create metadata object
                metadata = FoodItemMetadata(
                    food_name=food_name,
                    food_id=food_id,
                    ingredients=ingredients,
                    recipe=recipe,
                    line_number=line_num,
                    meal_type=current_meal_type
                )
                food_metadata_list.append(metadata)
            else:
                plain_metadata_match = cls.PLAIN_METADATA_PATTERN.search(line)
                if plain_metadata_match:
                    food_id = plain_metadata_match.group(1).strip()
                    ingredients = [
                        item.strip()
                        for item in plain_metadata_match.group(2).split(",")
                        if item.strip()
                    ]
                    recipe = [plain_metadata_match.group(3).strip()] if plain_metadata_match.group(3).strip() else []
                    cleaned_line = line[:plain_metadata_match.start()].rstrip()
                    food_name = cls._extract_food_name(cleaned_line)

                    cleaned_lines.append(cleaned_line)
                    if ingredients:
                        cleaned_lines.append(f"  Ingredients: {', '.join(ingredients)}")
                    if recipe:
                        cleaned_lines.append(f"  Recipe: {recipe[0]}")

                    food_metadata_list.append(FoodItemMetadata(
                        food_name=food_name,
                        food_id=food_id,
                        ingredients=ingredients,
                        recipe=recipe,
                        line_number=line_num,
                        meal_type=current_meal_type
                    ))
                else:
                    # Line doesn't have metadata, keep as is
                    cleaned_lines.append(line)
        
        cleaned_text = '\n'.join(cleaned_lines)
        return cleaned_text, food_metadata_list
    
    @classmethod
    def _extract_food_name(cls, line: str) -> str:
        """Extract the food name from a food item line"""
        # Match pattern: - Food Name | ...
        match = cls.FOOD_ITEM_LINE_PATTERN.match(line)
        if match:
            return match.group(1).strip()
        return line.strip()
    
    @classmethod
    def get_food_id_mapping(cls, meal_plan_text: str) -> Dict[str, str]:
        """
        Get a mapping of food names to food IDs
        
        Args:
            meal_plan_text: The raw meal plan text with embedded metadata
            
        Returns:
            Dictionary mapping food_name -> food_id
        """
        _, metadata_list = cls.parse_meal_plan_text(meal_plan_text)
        return {meta.food_name: meta.food_id for meta in metadata_list}
    
    @classmethod
    def get_metadata_by_food_name(cls, meal_plan_text: str, food_name: str) -> Optional[FoodItemMetadata]:
        """
        Get metadata for a specific food item by name
        
        Args:
            meal_plan_text: The raw meal plan text with embedded metadata
            food_name: The name of the food item to search for
            
        Returns:
            FoodItemMetadata object if found, None otherwise
        """
        _, metadata_list = cls.parse_meal_plan_text(meal_plan_text)
        
        # Try exact match first
        for meta in metadata_list:
            if meta.food_name.lower() == food_name.lower():
                return meta
        
        # Try partial match
        for meta in metadata_list:
            if food_name.lower() in meta.food_name.lower():
                return meta
        
        return None
    
    @classmethod
    def extract_all_metadata(cls, meal_plan_text: str) -> List[Dict]:
        """
        Extract all food item metadata as dictionaries
        
        Args:
            meal_plan_text: The raw meal plan text with embedded metadata
            
        Returns:
            List of dictionaries containing metadata for each food item
        """
        _, metadata_list = cls.parse_meal_plan_text(meal_plan_text)
        return [meta.to_dict() for meta in metadata_list]


def parse_and_clean_meal_plan(meal_plan_text: str) -> Tuple[str, Dict[str, Dict]]:
    """
    Convenience function to parse meal plan text and return cleaned text with metadata
    
    Args:
        meal_plan_text: The raw meal plan text with embedded metadata
        
    Returns:
        Tuple of (cleaned_text, metadata_dict)
        - cleaned_text: Markdown text ready for UI display
        - metadata_dict: Dictionary mapping food_name -> full metadata dict
    """
    cleaned_text, metadata_list = MealPlanParser.parse_meal_plan_text(meal_plan_text)
    
    metadata_dict = {
        meta.food_name: {
            'food_id': meta.food_id,
            'ingredients': meta.ingredients,
            'recipe': meta.recipe,
            'meal_type': meta.meal_type
        }
        for meta in metadata_list
    }
    
    return cleaned_text, metadata_dict


# Example usage and testing
if __name__ == "__main__":
    # Test with sample data
    sample_meal_plan = """Here is your meal plan based on your preferences and health needs:

**Breakfast**
- Egg White Spinach Scramble with Whole Grain Toast | Household Measure: 0.5 plate | Portion Weight: 7 oz | Protein: 17g | Carbs: 10g | Fat: 6g | Fiber: 2g | Sodium: 201mg | Iodine: 33mcg | Sugar: 2g | Cholesterol: 0mg | Calories: 159kcal | "food_id": "FOOD_NORT_PROT_005" | "ingredients": ["egg whites", "spinach", "whole grain bread"] | "recipe": ["Scramble egg whites with spinach", "Toast bread", "Serve together"] |
- Cranberry Pumpkin Seed Mix | Household Measure: 1 small handful | Portion Weight: 1 oz | Protein: 5g | Carbs: 6g | Fat: 9g | Fiber: 2g | Sodium: 2mg | Iodine: 1mcg | Sugar: 2g | Cholesterol: 0mg | Calories: 119kcal | "food_id": "FOOD_NORT_SNACK_012" | "ingredients": ["cranberries", "pumpkin seeds"] | "recipe": [] |

**Lunch**
- Black Bean and Veggie Wrap | Household Measure: 0.75 wrap | Portion Weight: 7 oz | Protein: 11g | Carbs: 33g | Fat: 9g | Fiber: 8g | Sodium: 275mg | Iodine: 8mcg | Sugar: 3g | Cholesterol: 0mg | Calories: 254kcal | "food_id": "FOOD_NORT_LUNCH_025" | "ingredients": [] | "recipe": [] |
"""
    
    print("="*80)
    print("TESTING MEAL PLAN PARSER")
    print("="*80)
    
    # Test 1: Basic parsing
    cleaned, metadata_list = MealPlanParser.parse_meal_plan_text(sample_meal_plan)
    print("\n1. CLEANED TEXT:")
    print("-" * 80)
    print(cleaned)
    
    print("\n2. EXTRACTED METADATA:")
    print("-" * 80)
    for meta in metadata_list:
        print(f"\nFood: {meta.food_name}")
        print(f"  Food ID: {meta.food_id}")
        print(f"  Meal Type: {meta.meal_type}")
        print(f"  Ingredients: {meta.ingredients}")
        print(f"  Recipe Steps: {len(meta.recipe)} steps")
    
    # Test 2: Food ID mapping
    print("\n3. FOOD ID MAPPING:")
    print("-" * 80)
    mapping = MealPlanParser.get_food_id_mapping(sample_meal_plan)
    for food_name, food_id in mapping.items():
        print(f"{food_name} -> {food_id}")
    
    # Test 3: Get specific metadata
    print("\n4. SPECIFIC FOOD LOOKUP:")
    print("-" * 80)
    meta = MealPlanParser.get_metadata_by_food_name(sample_meal_plan, "Cranberry Pumpkin Seed Mix")
    if meta:
        print(f"Found: {meta.food_name}")
        print(f"Food ID: {meta.food_id}")
        print(f"Ingredients: {meta.ingredients}")
    
    print("\n" + "="*80)
