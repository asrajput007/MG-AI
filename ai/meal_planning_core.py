from dataclasses import dataclass, field
from typing import List, Dict, Optional, Literal, Tuple
from datetime import date, datetime, timedelta
import uuid


@dataclass
class MacroNutrients:
    protein_g: float
    carbs_g: float
    fat_g: float
    fiber_g: float = 0
    sodium_mg: float = 0
    sugar_g: float = 0
    cholesterol_mg: float = 0
    calories: float = 0
    
    def to_dict(self) -> Dict:
        return {
            "protein": self.protein_g,
            "carbs": self.carbs_g,
            "fat": self.fat_g,
            "fiber": self.fiber_g,
            "sodium": self.sodium_mg,
            "sugar": self.sugar_g,
            "cholesterol": self.cholesterol_mg,
            "calories": self.calories
        }


@dataclass
class FoodItem:
    name: str
    portion_size: float
    portion_unit: str  
    nutrition: MacroNutrients
    food_id: Optional[str] = None
    recipe_details: Optional[str] = None


@dataclass
class Meal:
    meal_type: str 
    foods: List[FoodItem]
    total_nutrition: MacroNutrients
    meal_time: Optional[str] = None 
    notes: Optional[str] = None


@dataclass
class LockedMeal:
    meal_type: str
    foods: List[FoodItem]
    total_calories: float
    macros: MacroNutrients
    logged_at: datetime
    was_from_plan: bool = False 
    replacement_notes: Optional[str] = None


@dataclass
class AdjustmentTargets:
    calorie_delta: Optional[int] = None  
    protein_delta: Optional[int] = None
    carb_delta: Optional[int] = None
    fat_delta: Optional[int] = None
    
    calorie_percentage: Optional[float] = None 
    protein_percentage: Optional[float] = None
    
    foods_to_remove: List[str] = field(default_factory=list)
    foods_to_replace: Dict[str, str] = field(default_factory=dict)  
    foods_to_add: List[str] = field(default_factory=list)
    
    meals_to_skip: List[str] = field(default_factory=list)  
    redistribute_skipped_calories: bool = True


@dataclass
class MealPlanConfig:
    
    time_horizon: Literal["daily", "weekly"] = "daily"
    start_date: date = field(default_factory=date.today)
    num_days: int = 1  
    
    user_token: str  
    tdee: float 
    target_protein_g: float
    target_carbs_g: float
    target_fat_g: float
    
    health_conditions: List[str] = field(default_factory=list) 
    allergies: List[str] = field(default_factory=list) 
    excluded_foods: List[str] = field(default_factory=list) 
    cuisine_preferences: List[str] = field(default_factory=list)  
    food_group_limits: Dict[str, int] = field(default_factory=dict)  
    
    disease_focus: Optional[str] = None  
    meal_type_focus: Optional[str] = None  
    
    locked_meals: Dict[str, LockedMeal] = field(default_factory=dict) 
    
    adjustment_targets: Optional[AdjustmentTargets] = None
    
    previous_meal_plan: Optional['MealPlan'] = None 
    
    output_format: Literal["detailed", "summary", "simple"] = "detailed"
    include_recipes: bool = False
    include_grocery_list: bool = False
    include_nutrition_breakdown: bool = True


@dataclass
class MealPlan:
    plan_id: str 
    created_at: datetime
    valid_for_dates: List[date] 
    
    meals_by_day: Dict[date, List[Meal]]  
    
    daily_targets: MacroNutrients  
    daily_actuals: Dict[date, MacroNutrients]  
    
    locked_meals: Dict[str, LockedMeal] 
    
    metadata: Dict = field(default_factory=dict)  
    
    def get_meal_calories(self, meal_type: str, day: Optional[date] = None) -> float:
        if day is None:
            day = self.valid_for_dates[0]
        
        meals = self.meals_by_day.get(day, [])
        for meal in meals:
            if meal.meal_type == meal_type:
                return meal.total_nutrition.calories
        return 0
    
    def get_total_calories(self, day: Optional[date] = None) -> float:
        if day is None:
            day = self.valid_for_dates[0]
        
        return self.daily_actuals.get(day, MacroNutrients(0, 0, 0)).calories


@dataclass  
class MealPlanResult:
    success: bool
    meal_plan: Optional[MealPlan] = None
    error_message: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    adjustments_made: List[str] = field(default_factory=list)  
    safety_alerts: List[str] = field(default_factory=list)




class UniversalMealPlanningEngine:
  
    
    def __init__(self, db_service, llm_service, nutrition_service):

        self.db = db_service
        self.llm = llm_service
        self.nutrition = nutrition_service
        
        self._food_cache = {}
        self._constraint_cache = {}
    

    
    async def generate_meal_plan(self, config: MealPlanConfig) -> MealPlanResult:
        
        try:
            targets = await self._calculate_targets(config)
            
            meals_by_day = {}
            for day_offset in range(config.num_days):
                day = config.start_date + timedelta(days=day_offset)
                
                day_locked_meals = self._get_locked_meals_for_day(config, day)
                
                remaining_meals = await self._generate_day_meals(
                    day=day,
                    targets=targets,
                    locked_meals=day_locked_meals,
                    config=config
                )
                
                meals_by_day[day] = day_locked_meals + remaining_meals
            
            daily_actuals = {}
            for day, meals in meals_by_day.items():
                daily_actuals[day] = self._sum_meal_nutrition(meals)
            
            warnings, safety_alerts = await self._validate_meal_plan(meals_by_day, config)
            
            meal_plan = MealPlan(
                plan_id=self._generate_plan_id(),
                created_at=datetime.now(),
                valid_for_dates=list(meals_by_day.keys()),
                meals_by_day=meals_by_day,
                daily_targets=targets,
                daily_actuals=daily_actuals,
                locked_meals=config.locked_meals,
                metadata=self._build_metadata(config)
            )
            
            return MealPlanResult(
                success=True,
                meal_plan=meal_plan,
                warnings=warnings,
                safety_alerts=safety_alerts,
                adjustments_made=self._generate_adjustment_log(config)
            )
            
        except Exception as e:
            return MealPlanResult(
                success=False,
                error_message=f"Failed to generate meal plan: {str(e)}"
            )
    

    
    async def _calculate_targets(self, config: MealPlanConfig) -> MacroNutrients:
        base_targets = MacroNutrients(
            protein_g=config.target_protein_g,
            carbs_g=config.target_carbs_g,
            fat_g=config.target_fat_g,
            calories=config.tdee
        )
        
        if config.adjustment_targets:
            adj = config.adjustment_targets
            
            if adj.calorie_delta:
                base_targets.calories += adj.calorie_delta
            if adj.protein_delta:
                base_targets.protein_g += adj.protein_delta
            if adj.carb_delta:
                base_targets.carbs_g += adj.carb_delta
            if adj.fat_delta:
                base_targets.fat_g += adj.fat_delta
            
            if adj.calorie_percentage:
                base_targets.calories *= adj.calorie_percentage
            if adj.protein_percentage:
                base_targets.protein_g *= adj.protein_percentage
        
        return base_targets
    
    async def _generate_day_meals(
        self,
        day: date,
        targets: MacroNutrients,
        locked_meals: List[Meal],
        config: MealPlanConfig
    ) -> List[Meal]:
        
        remaining_targets = self._calculate_remaining_targets(targets, locked_meals)
        
        all_meal_types = ["Breakfast", "Morning Snack", "Lunch", "Evening Snack", "Dinner"]
        locked_meal_types = {meal.meal_type for meal in locked_meals}
        
        if config.adjustment_targets and config.adjustment_targets.meals_to_skip:
            for skipped in config.adjustment_targets.meals_to_skip:
                if skipped in all_meal_types:
                    all_meal_types.remove(skipped)
        
        meal_types_to_generate = [mt for mt in all_meal_types if mt not in locked_meal_types]
        
        generated_meals = []
        for meal_type in meal_types_to_generate:
            meal = await self._generate_single_meal(
                meal_type=meal_type,
                remaining_targets=remaining_targets,
                config=config
            )
            generated_meals.append(meal)
            
            remaining_targets = self._subtract_nutrition(remaining_targets, meal.total_nutrition)
        
        return generated_meals
    
    async def _generate_single_meal(
        self,
        meal_type: str,
        remaining_targets: MacroNutrients,
        config: MealPlanConfig
    ) -> Meal:
        
        meal_calorie_target = self._allocate_calories_to_meal(meal_type, remaining_targets.calories)
        
        foods = await self._select_foods_for_meal(
            meal_type=meal_type,
            calorie_target=meal_calorie_target,
            config=config
        )
        
        total_nutrition = self._sum_food_nutrition(foods)
        
        return Meal(
            meal_type=meal_type,
            foods=foods,
            total_nutrition=total_nutrition
        )
    
    async def _select_foods_for_meal(
        self,
        meal_type: str,
        calorie_target: float,
        config: MealPlanConfig
    ) -> List[FoodItem]:
        raise NotImplementedError("_select_foods_for_meal must be implemented by subclass or external service")

    
    def _get_locked_meals_for_day(self, config: MealPlanConfig, day: date) -> List[Meal]:
        meals = []
        for meal_type, locked in config.locked_meals.items():
            if day == config.start_date: 
                meal = Meal(
                    meal_type=meal_type,
                    foods=locked.foods,
                    total_nutrition=locked.macros
                )
                meals.append(meal)
        return meals
    
    def _calculate_remaining_targets(
        self,
        targets: MacroNutrients,
        locked_meals: List[Meal]
    ) -> MacroNutrients:
        locked_nutrition = self._sum_meal_nutrition(locked_meals)
        
        return MacroNutrients(
            protein_g=max(0, targets.protein_g - locked_nutrition.protein_g),
            carbs_g=max(0, targets.carbs_g - locked_nutrition.carbs_g),
            fat_g=max(0, targets.fat_g - locked_nutrition.fat_g),
            calories=max(0, targets.calories - locked_nutrition.calories)
        )
    
    def _sum_meal_nutrition(self, meals: List[Meal]) -> MacroNutrients:
        total = MacroNutrients(0, 0, 0, 0, 0, 0, 0, 0)
        for meal in meals:
            total.protein_g += meal.total_nutrition.protein_g
            total.carbs_g += meal.total_nutrition.carbs_g
            total.fat_g += meal.total_nutrition.fat_g
            total.fiber_g += meal.total_nutrition.fiber_g
            total.sodium_mg += meal.total_nutrition.sodium_mg
            total.sugar_g += meal.total_nutrition.sugar_g
            total.cholesterol_mg += meal.total_nutrition.cholesterol_mg
            total.calories += meal.total_nutrition.calories
        return total
    
    def _sum_food_nutrition(self, foods: List[FoodItem]) -> MacroNutrients:
        total = MacroNutrients(0, 0, 0, 0, 0, 0, 0, 0)
        for food in foods:
            n = food.nutrition
            total.protein_g += n.protein_g
            total.carbs_g += n.carbs_g
            total.fat_g += n.fat_g
            total.fiber_g += n.fiber_g
            total.sodium_mg += n.sodium_mg
            total.sugar_g += n.sugar_g
            total.cholesterol_mg += n.cholesterol_mg
            total.calories += n.calories
        return total
    
    def _subtract_nutrition(self, a: MacroNutrients, b: MacroNutrients) -> MacroNutrients:
        return MacroNutrients(
            protein_g=max(0, a.protein_g - b.protein_g),
            carbs_g=max(0, a.carbs_g - b.carbs_g),
            fat_g=max(0, a.fat_g - b.fat_g),
            fiber_g=max(0, a.fiber_g - b.fiber_g),
            sodium_mg=max(0, a.sodium_mg - b.sodium_mg),
            sugar_g=max(0, a.sugar_g - b.sugar_g),
            cholesterol_mg=max(0, a.cholesterol_mg - b.cholesterol_mg),
            calories=max(0, a.calories - b.calories)
        )
    
    def _allocate_calories_to_meal(self, meal_type: str, remaining_calories: float) -> float:
        ratios = {
            "Breakfast": 0.25,
            "Morning Snack": 0.05,
            "Lunch": 0.35,
            "Evening Snack": 0.05,
            "Dinner": 0.30
        }
        return remaining_calories * ratios.get(meal_type, 0.20)
    def _generate_plan_id(self) -> str:
        
        return str(uuid.uuid4())
    
    def _build_metadata(self, config: MealPlanConfig) -> Dict:
        return {
            "version": 1,
            "time_horizon": config.time_horizon,
            "disease_focus": config.disease_focus,
            "has_locked_meals": len(config.locked_meals) > 0,
            "had_adjustments": config.adjustment_targets is not None,
            "generated_from_previous": config.previous_meal_plan is not None
        }
    
    def _generate_adjustment_log(self, config: MealPlanConfig) -> List[str]:
        """Generate human-readable log of adjustments made"""
        log = []
        
        if config.locked_meals:
            for meal_type in config.locked_meals:
                log.append(f"Locked {meal_type} (already logged)")
        
        if config.adjustment_targets:
            adj = config.adjustment_targets
            if adj.calorie_delta:
                sign = "+" if adj.calorie_delta > 0 else ""
                log.append(f"Adjusted calories: {sign}{adj.calorie_delta} kcal")
            if adj.protein_delta:
                sign = "+" if adj.protein_delta > 0 else ""
                log.append(f"Adjusted protein: {sign}{adj.protein_delta}g")
            if adj.foods_to_remove:
                log.append(f"Removed foods: {', '.join(adj.foods_to_remove)}")
            if adj.foods_to_replace:
                for old, new in adj.foods_to_replace.items():
                    log.append(f"Replaced {old} with {new}")
        
        return log
    
    async def _validate_meal_plan(
        self,
        meals_by_day: Dict[date, List[Meal]],
        config: MealPlanConfig
    ) -> Tuple[List[str], List[str]]:
        return [], []
