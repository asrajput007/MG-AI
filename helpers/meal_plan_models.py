from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from datetime import date


class MealPlanPayload(BaseModel):

    plan_name: str = Field(
        default="Daily Meal Plan",
        description="Name of the meal plan"
    )
    
    plan_type: str = Field(
        default="DAILY",
        description="Type of meal plan (DAILY, WEEKLY, etc.)"
    )
    
    meal_date: Optional[str] = Field(
        default=None,
        description="Date for the meal plan in YYYY-MM-DD format, or null for today"
    )
    
    meal_plan_text: str = Field(
        ...,  # Required field
        description=(
            "Meal plan text in Markdown format with embedded metadata. "
            "Each food item line contains pipe-separated nutrition data AND "
            "hidden metadata at the end: | \"food_id\": \"...\" | \"ingredients\": [] | \"recipe\": [] |"
        )
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "plan_name": "Daily Meal Plan",
                "plan_type": "DAILY",
                "meal_date": None,
                "meal_plan_text": (
                    "**Breakfast**\n"
                    "- Egg White Spinach Scramble | Household Measure: 0.5 plate | "
                    "Portion Weight: 7 oz | Protein: 17g | Carbs: 10g | Fat: 6g | "
                    "Fiber: 2g | Sodium: 201mg | Iodine: 33mcg | Sugar: 2g | "
                    "Cholesterol: 0mg | Calories: 159kcal | "
                    "\"food_id\": \"FOOD_NORT_PROT_005\" | \"ingredients\": [] | \"recipe\": [] |\n"
                )
            }
        }


class FoodItemData(BaseModel):
    """Represents a single food item with its metadata"""
    
    food_name: str = Field(
        ...,
        description="Display name of the food item"
    )
    
    food_id: str = Field(
        ...,
        description="Unique identifier for the food item in the database"
    )
    
    ingredients: List[str] = Field(
        default_factory=list,
        description="List of ingredient names for this food item"
    )
    
    recipe: List[str] = Field(
        default_factory=list,
        description="List of recipe preparation steps"
    )
    
    meal_type: str = Field(
        default="",
        description="Type of meal (Breakfast, Lunch, Dinner, etc.)"
    )
    
    nutritional_info: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional nutritional information (calories, protein, etc.)"
    )


class CleanedMealPlanResponse(BaseModel):
    """
    Response model for cleaned meal plan data.
    Used when returning parsed meal plan to the UI.
    """
    
    cleaned_text: str = Field(
        ...,
        description="Meal plan text with metadata stripped out, ready for Markdown rendering"
    )
    
    food_items: List[FoodItemData] = Field(
        default_factory=list,
        description="List of food items with their metadata"
    )
    
    food_id_mapping: Dict[str, str] = Field(
        default_factory=dict,
        description="Quick lookup mapping: food_name -> food_id"
    )
    
    total_items: int = Field(
        default=0,
        description="Total number of food items in the meal plan"
    )


class MealPlanUpsertRequest(BaseModel):
    """
    Request model for upserting a meal plan.
    This is what the frontend sends to the backend.
    """
    
    meal_plan_text: str = Field(
        ...,
        description="Raw meal plan text with embedded metadata"
    )
    
    plan_name: str = Field(
        default="Daily Meal Plan",
        description="Name of the meal plan"
    )
    
    plan_type: str = Field(
        default="DAILY",
        description="Type of meal plan"
    )
    
    meal_date: Optional[str] = Field(
        default=None,
        description="Date in YYYY-MM-DD format"
    )


# Type aliases for convenience
MealPlanText = str  # Raw meal plan text with metadata
CleanedMealPlanText = str  # Meal plan text without metadata
FoodIdMapping = Dict[str, str]  # food_name -> food_id mapping


# Example TypedDict definitions (alternative to Pydantic for simpler use cases)
from typing import TypedDict

class MealPlanPayloadDict(TypedDict, total=False):
    """TypedDict version of MealPlanPayload for simpler type hints"""
    plan_name: str
    plan_type: str
    meal_date: Optional[str]
    meal_plan_text: str


class FoodItemDict(TypedDict):
    """TypedDict version of FoodItemData"""
    food_name: str
    food_id: str
    ingredients: List[str]
    recipe: List[str]
    meal_type: str


# Validation functions
def validate_meal_plan_payload(payload: Dict[str, Any]) -> bool:
    """
    Validate that a meal plan payload has the required structure.
    
    Args:
        payload: Dictionary to validate
        
    Returns:
        True if valid, False otherwise
    """
    try:
        # Check required fields
        if 'meal_plan_text' not in payload:
            return False
        
        # Validate using Pydantic
        MealPlanPayload(**payload)
        return True
    except Exception:
        return False


def create_meal_plan_payload(
    meal_plan_text: str,
    plan_name: str = "Daily Meal Plan",
    plan_type: str = "DAILY",
    meal_date: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a properly formatted meal plan payload dictionary.
    
    Args:
        meal_plan_text: The meal plan text with embedded metadata
        plan_name: Name of the plan
        plan_type: Type of plan (DAILY, WEEKLY, etc.)
        meal_date: Date string in YYYY-MM-DD format or None
        
    Returns:
        Dictionary representing the meal plan payload
    """
    payload = MealPlanPayload(
        plan_name=plan_name,
        plan_type=plan_type,
        meal_date=meal_date,
        meal_plan_text=meal_plan_text
    )
    return payload.model_dump()


# Example usage
if __name__ == "__main__":
    # Example 1: Create a payload
    sample_text = """**Breakfast**
- Egg White Scramble | Household Measure: 0.5 plate | Portion Weight: 7 oz | Protein: 17g | Carbs: 10g | Fat: 6g | Fiber: 2g | Sodium: 201mg | Iodine: 33mcg | Sugar: 2g | Cholesterol: 0mg | Calories: 159kcal | "food_id": "FOOD_001" | "ingredients": [] | "recipe": [] |
"""
    
    payload = create_meal_plan_payload(sample_text)
    print("Created Payload:")
    print(payload)
    
    # Example 2: Validate a payload
    is_valid = validate_meal_plan_payload(payload)
    print(f"\nPayload is valid: {is_valid}")
    
    # Example 3: Create Pydantic models
    meal_plan = MealPlanPayload(**payload)
    print(f"\nPydantic Model:")
    print(f"Plan Name: {meal_plan.plan_name}")
    print(f"Plan Type: {meal_plan.plan_type}")
    print(f"Text Length: {len(meal_plan.meal_plan_text)} chars")
