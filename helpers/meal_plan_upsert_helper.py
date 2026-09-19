
import requests
from typing import Optional, Dict, Any
from helpers.meal_plan_models import create_meal_plan_payload, validate_meal_plan_payload
import logging

logger = logging.getLogger(__name__)


def create_upsert_payload(
    meal_plan_text: str,
    plan_name: str = "Daily Meal Plan",
    plan_type: str = "DAILY",
    meal_date: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a meal plan payload ready for upsert.
    
    Args:
        meal_plan_text: Meal plan text with embedded metadata
        plan_name: Name of the meal plan
        plan_type: Type of plan (DAILY, WEEKLY, etc.)
        meal_date: Date in YYYY-MM-DD format or None for today
        
    Returns:
        Dictionary payload ready for API or database tool
        
    Example:
        >>> payload = create_upsert_payload(
        ...     meal_plan_text="**Breakfast**\\n- Food | ... | \\"food_id\\": \\"...\\"|",
        ...     plan_name="Daily Meal Plan",
        ...     plan_type="DAILY",
        ...     meal_date="2026-04-20"
        ... )
    """
    payload = create_meal_plan_payload(
        meal_plan_text=meal_plan_text,
        plan_name=plan_name,
        plan_type=plan_type,
        meal_date=meal_date
    )
    
    # Validate before returning
    if not validate_meal_plan_payload(payload):
        raise ValueError("Invalid meal plan payload structure")
    
    return payload


def upsert_meal_plan_via_api(
    payload: Dict[str, Any],
    api_url: str = "http://localhost:8000/upsert-meal-plan",
    authorization_token: Optional[str] = None
) -> Dict[str, Any]:
    """
    Upsert a meal plan via the API endpoint.
    
    Args:
        payload: Meal plan payload (from create_upsert_payload)
        api_url: API endpoint URL
        authorization_token: Bearer token for authentication
        
    Returns:
        API response as dictionary
        
    Example:
        >>> payload = create_upsert_payload(meal_plan_text, ...)
        >>> response = upsert_meal_plan_via_api(payload, authorization_token="your_token")
    """
    headers = {}
    if authorization_token:
        headers["Authorization"] = f"Bearer {authorization_token}"
    
    try:
        response = requests.post(
            api_url,
            json=payload,
            headers=headers
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to upsert meal plan via API: {e}")
        return {
            "status": "error",
            "message": str(e)
        }


async def upsert_meal_plan_via_tool(
    meal_plan_text: str,
    authorization_token: str,
    plan_name: str = "Daily Meal Plan",
    plan_type: str = "DAILY",
    meal_date: Optional[str] = None
) -> Dict[str, Any]:
    """
    Upsert a meal plan using the DatabasePersistenceTool directly.
    
    Args:
        meal_plan_text: Meal plan text with embedded metadata
        authorization_token: Authentication token
        plan_name: Name of the meal plan
        plan_type: Type of plan
        meal_date: Date in YYYY-MM-DD format
        
    Returns:
        Result dictionary with status and data
        
    Example:
        >>> result = await upsert_meal_plan_via_tool(
        ...     meal_plan_text="...",
        ...     authorization_token="your_token"
        ... )
    """
    from ai.base_and_utility import DatabasePersistenceTool
    
    db_tool = DatabasePersistenceTool()
    
    result = await db_tool.execute(
        operation="upsert_meal_plan",
        meal_plan_text=meal_plan_text,
        plan_name=plan_name,
        plan_type=plan_type,
        meal_date=meal_date,
        token=authorization_token
    )
    
    if result.success:
        return {
            "status": "success",
            "message": "Meal plan saved successfully",
            "data": result.data
        }
    else:
        return {
            "status": "error",
            "message": result.error
        }


# Example usage
if __name__ == "__main__":
    # Example meal plan text (truncated for readability)
    example_text = """Here is your meal plan based on your preferences and health needs:

**Breakfast**

- Egg White Spinach Scramble with Whole Grain Toast | Household Measure: 0.5 plate | Portion Weight: 7 oz | Protein: 17g | Carbs: 10g | Fat: 6g | Fiber: 2g | Sodium: 201mg | Iodine: 33mcg | Sugar: 2g | Cholesterol: 0mg | Calories: 159kcal | "food_id": "FOOD_NORT_PROT_005" | "ingredients": [] | "recipe": [] |

Total calories for Breakfast: 159.0 kcal

**Daily Total Calories:** 1245.1 kcal
"""
    
    # Create payload
    print("Creating upsert payload...")
    payload = create_upsert_payload(
        meal_plan_text=example_text,
        plan_name="Daily Meal Plan",
        plan_type="DAILY",
        meal_date="2026-04-20"
    )
    
    print("✅ Payload created successfully!")
    print(f"Plan Name: {payload['plan_name']}")
    print(f"Plan Type: {payload['plan_type']}")
    print(f"Meal Date: {payload['meal_date']}")
    print(f"Text Length: {len(payload['meal_plan_text'])} characters")
    
    # Example: Upsert via API (commented out - uncomment when ready to use)
    # response = upsert_meal_plan_via_api(
    #     payload,
    #     authorization_token="your_token_here"
    # )
    # print(response)
