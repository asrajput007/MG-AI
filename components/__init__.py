"""
Components module for UI display elements.
"""

from .meal_plan_display import (
    display_hybrid_meal_plan,
    display_legacy_meal_plan,
    render_meal_plan,
    handle_food_click
)

__all__ = [
    'display_hybrid_meal_plan',
    'display_legacy_meal_plan',
    'render_meal_plan',
    'handle_food_click'
]
