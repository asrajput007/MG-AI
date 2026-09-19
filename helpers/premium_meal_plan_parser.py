"""
Structured parser for weekly/daily meal plan text produced by the backend.

The backend emits plans in a semi-structured markdown format, e.g.:

    ### Day 1: Saturday, September 20, 2026

    **Breakfast**
    - Vegetable Masala Oats | Household Measure: 1 bowl | Portion Weight: 250g |
      Protein: 35g | Carbs: 95g | Fat: 25g | Fiber: 12g | Sodium: 410mg |
      Iodine: 5mcg | Sugar: 8g | Cholesterol: 0mg | Calories: 769kcal |
      "food_id": "FOOD_..." | "ingredients": [...] | "recipe": [...] |

    Total calories for Breakfast: 769.0 kcal
    Macronutrient Ratio: Protein 35.0g (18%), Carbs 95.0g (49%), Fat 25.0g (29%)

    **Daily Total Calories:** 2636 kcal

This module turns that raw text into plain dataclasses so the premium UI can
render it without re-parsing markdown in the view layer. This is purely a
read/presentation layer - it does not change or regenerate any data.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional


NUTRIENT_FIELD_MAP = {
    "Protein": "protein_g",
    "Carbs": "carbs_g",
    "Fat": "fat_g",
    "Fiber": "fiber_g",
    "Sodium": "sodium_mg",
    "Iodine": "iodine_mcg",
    "Sugar": "sugar_g",
    "Cholesterol": "cholesterol_mg",
    "Calories": "calories",
}

FOOD_ID_RE = re.compile(r'"food_id"\s*:\s*"([^"]*)"')
INGREDIENTS_RE = re.compile(r'"ingredients"\s*:\s*(\[.*?\])')
RECIPE_RE = re.compile(r'"recipe"\s*:\s*(\[.*?\])')
HOUSEHOLD_MEASURE_RE = re.compile(r'Household Measure:\s*([^|]+)')
PORTION_WEIGHT_RE = re.compile(r'Portion Weight:\s*([^|]+)')

DAY_HEADER_RE = re.compile(r'^#{1,3}\s*Day\s*(\d+)\s*:\s*([^,]+),\s*(.+)$', re.IGNORECASE)
MEAL_HEADER_RE = re.compile(r'^\*\*([A-Za-z][A-Za-z \-]*)\*\*$')
FOOD_LINE_RE = re.compile(r'^-\s*(.+)')
MEAL_TOTAL_RE = re.compile(r'Total calories for\s+(.+?):\s*([\d.]+)\s*kcal', re.IGNORECASE)
MACRO_RATIO_RE = re.compile(
    r'Protein\s+([\d.]+)\s*g?\s*\(([\d.]+)%\).*?Carbs\s+([\d.]+)\s*g?\s*\(([\d.]+)%\).*?Fat\s+([\d.]+)\s*g?\s*\(([\d.]+)%\)',
    re.IGNORECASE
)
DAILY_TOTAL_RE = re.compile(r'Daily Total Calories.*?([\d.]+)\s*kcal', re.IGNORECASE)


@dataclass
class FoodItem:
    name: str
    household_measure: str = ""
    portion_weight: str = ""
    nutrients: Dict[str, float] = field(default_factory=dict)
    food_id: Optional[str] = None
    ingredients: List[str] = field(default_factory=list)
    recipe: List[str] = field(default_factory=list)

    @property
    def calories(self) -> float:
        return self.nutrients.get("calories", 0.0)

    @property
    def protein_g(self) -> float:
        return self.nutrients.get("protein_g", 0.0)

    @property
    def carbs_g(self) -> float:
        return self.nutrients.get("carbs_g", 0.0)

    @property
    def fat_g(self) -> float:
        return self.nutrients.get("fat_g", 0.0)


@dataclass
class Meal:
    name: str
    items: List[FoodItem] = field(default_factory=list)
    total_calories: float = 0.0
    macro_ratio: Optional[Dict[str, float]] = None

    @property
    def total_protein_g(self) -> float:
        return sum(i.protein_g for i in self.items)

    @property
    def total_carbs_g(self) -> float:
        return sum(i.carbs_g for i in self.items)

    @property
    def total_fat_g(self) -> float:
        return sum(i.fat_g for i in self.items)

    @property
    def display_calories(self) -> float:
        return self.total_calories or sum(i.calories for i in self.items)


@dataclass
class DayPlan:
    day_number: int
    day_name: str = ""
    date_label: str = ""
    meals: List[Meal] = field(default_factory=list)
    daily_total_calories: Optional[float] = None

    @property
    def total_calories(self) -> float:
        if self.daily_total_calories:
            return self.daily_total_calories
        return sum(m.display_calories for m in self.meals)

    @property
    def total_protein_g(self) -> float:
        return sum(m.total_protein_g for m in self.meals)

    @property
    def total_carbs_g(self) -> float:
        return sum(m.total_carbs_g for m in self.meals)

    @property
    def total_fat_g(self) -> float:
        return sum(m.total_fat_g for m in self.meals)


def _parse_food_line(line: str) -> Optional[FoodItem]:
    match = FOOD_LINE_RE.match(line)
    if not match:
        return None
    body = match.group(1)

    name = body.split('|', 1)[0].strip()
    if not name:
        return None

    food_id_match = FOOD_ID_RE.search(body)
    ingredients_match = INGREDIENTS_RE.search(body)
    recipe_match = RECIPE_RE.search(body)
    hm_match = HOUSEHOLD_MEASURE_RE.search(body)
    pw_match = PORTION_WEIGHT_RE.search(body)

    nutrients: Dict[str, float] = {}
    for label, key in NUTRIENT_FIELD_MAP.items():
        m = re.search(rf'{label}:\s*([\d.]+)', body)
        if m:
            try:
                nutrients[key] = float(m.group(1))
            except ValueError:
                pass

    ingredients: List[str] = []
    if ingredients_match:
        try:
            import json
            ingredients = json.loads(ingredients_match.group(1))
        except Exception:
            ingredients = []

    recipe: List[str] = []
    if recipe_match:
        try:
            import json
            recipe = json.loads(recipe_match.group(1))
        except Exception:
            recipe = []

    return FoodItem(
        name=name,
        household_measure=(hm_match.group(1).strip() if hm_match else ""),
        portion_weight=(pw_match.group(1).strip() if pw_match else ""),
        nutrients=nutrients,
        food_id=(food_id_match.group(1) if food_id_match else None),
        ingredients=ingredients,
        recipe=recipe,
    )


def parse_weekly_plan(text: str) -> List[DayPlan]:
    """Parse a weekly (multi-day) or single-day meal plan into structured DayPlan objects.

    Returns an empty list if no recognizable "### Day N: ..." headers are found, so
    callers can gracefully fall back to the legacy plain-text renderer.
    """
    if not text:
        return []

    lines = text.split('\n')
    days: List[DayPlan] = []
    current_day: Optional[DayPlan] = None
    current_meal: Optional[Meal] = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        day_match = DAY_HEADER_RE.match(line)
        if day_match:
            if current_day:
                days.append(current_day)
            current_day = DayPlan(
                day_number=int(day_match.group(1)),
                day_name=day_match.group(2).strip(),
                date_label=day_match.group(3).strip(),
            )
            current_meal = None
            continue

        if current_day is None:
            # No day header yet - not a recognizable weekly plan structure.
            continue

        meal_match = MEAL_HEADER_RE.match(line)
        if meal_match:
            current_meal = Meal(name=meal_match.group(1).strip())
            current_day.meals.append(current_meal)
            continue

        daily_total_match = DAILY_TOTAL_RE.search(line)
        if daily_total_match:
            try:
                current_day.daily_total_calories = float(daily_total_match.group(1))
            except ValueError:
                pass
            continue

        meal_total_match = MEAL_TOTAL_RE.search(line)
        if meal_total_match and current_meal:
            try:
                current_meal.total_calories = float(meal_total_match.group(2))
            except ValueError:
                pass
            continue

        macro_match = MACRO_RATIO_RE.search(line)
        if macro_match and current_meal:
            current_meal.macro_ratio = {
                "protein_g": float(macro_match.group(1)),
                "protein_pct": float(macro_match.group(2)),
                "carbs_g": float(macro_match.group(3)),
                "carbs_pct": float(macro_match.group(4)),
                "fat_g": float(macro_match.group(5)),
                "fat_pct": float(macro_match.group(6)),
            }
            continue

        if current_meal is not None and line.startswith('-'):
            food_item = _parse_food_line(line)
            if food_item:
                current_meal.items.append(food_item)

    if current_day:
        days.append(current_day)

    return days
