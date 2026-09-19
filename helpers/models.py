from typing import Dict, List, Optional

from pydantic import BaseModel

class DashboardMetrics(BaseModel):
    total_cal: int
    consumed_cal: int
    burned_cal: int
    total_steps: int
    achieved_steps: int
    total_water: int
    consumed_water: int
    total_sleep: float
    consumed_sleep: float
    weight: float
    weight_goal: float
    activity_min: int
    activity_goal: int

class MealPlanRequest(BaseModel):
    expert_tdee: Optional[float] = None
    force_regenerate: Optional[bool] = False

class WeeklyMealPlanRequest(BaseModel):
    user_id: Optional[str] = None
    database_name: Optional[str] = None
    token: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    generate_for_all_users: Optional[bool] = False
    force_regenerate: Optional[bool] = False
    batch_size: Optional[int] = 10

class FrontendProfileMealPlanRequest(BaseModel):
    """Meal plan request that carries the full profile collected on the
    frontend (frontend1.py), so the backend can skip the DB profile lookup
    entirely and generate the plan directly from these constraints."""
    profile: Dict
    user_id: Optional[str] = None
    query: Optional[str] = "Create a meal plan for me based on my profile."
    chat_history: Optional[List[Dict]] = []
    last_agent_context: Optional[Dict] = {}
    force_tool_type: Optional[str] = "meal_plan_generator"
    start_date: Optional[str] = None
    end_date: Optional[str] = None