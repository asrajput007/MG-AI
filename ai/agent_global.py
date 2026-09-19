from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .agent_core import ToolBasedNutritionAgent

agent: Optional["ToolBasedNutritionAgent"] = None
