import json
import logging
from typing import Dict, List, Optional, Callable, Any
import asyncio
import re
from .tool_core import BaseTool, ToolType, ToolResult, LLMService
from functools import partial
import math

logger = logging.getLogger(__name__)

# --- Calculation Functions (Moved from waterstep_backend.py) ---

def _calculate_steps_logic(constraints: Dict) -> Dict:
    """Calculates personalized daily step goal based on profile constraints."""
    
    # Extract data with safe fallbacks
    age = constraints.get("age", 30)
    gender = constraints.get("gender", "Male")
    weight_kg = constraints.get("weight_kg", 75.0)
    height_cm = constraints.get("height_cm", 175.0)
    bmi = weight_kg / ((height_cm / 100)**2) if weight_kg and height_cm else 22.0
    
    # Fallbacks from the new frontend form
    occupation = constraints.get("occupation", "Sedentary (office)") 
    activity_level = constraints.get("activity_level", "Beginner") 
    goal_type = constraints.get("goal", {}).get("type", "Maintenance") 
    
    base_steps = 8000
    adjustments = {}
    explanations = {}

    # 1. Age Adjustment
    if 18 <= age <= 30:
        adj = 1000
        explanations['Age'] = "Younger age group (+1000)"
    elif 31 <= age <= 50:
        adj = 0
        explanations['Age'] = "Middle age baseline (+0)"
    elif 51 <= age <= 65:
        adj = -1000
        explanations['Age'] = "Senior age group (-1000)"
    elif age >= 66:
        adj = -2000
        explanations['Age'] = "Elderly age group (-2000)"
    else:
        adj = 0
        explanations['Age'] = "Under 18 baseline (+0)"
    adjustments['Age'] = adj

    # 2. Gender Adjustment
    if gender.lower() == 'male':
        adj = 500
        explanations['Gender'] = "Male baseline (+500)"
    else:
        adj = 0
        explanations['Gender'] = "Female baseline (+0)"
    adjustments['Gender'] = adj

    # 3. BMI Adjustment
    if bmi < 25:
        adj = 500
        explanations['BMI'] = f"BMI {bmi:.1f} (Healthy/Low) (+500)"
    elif 25 <= bmi <= 30:
        adj = 0
        explanations['BMI'] = f"BMI {bmi:.1f} (Overweight) (+0)"
    else: # > 30
        adj = -1000
        explanations['BMI'] = f"BMI {bmi:.1f} (Obese) (-1000)"
    adjustments['BMI'] = adj

    # 4. Occupation Adjustment (from Water/Step Profile)
    occ = occupation.lower()
    if 'active' in occ or 'labor' in occ or 'delivery' in occ:
        adj = 2000
        explanations['Occupation'] = "Active Job (+2000)"
    elif 'moderate' in occ or 'teacher' in occ or 'retail' in occ:
        adj = 1000
        explanations['Occupation'] = "Moderate Activity Job (+1000)"
    else: # Sedentary / Office
        adj = -1000
        explanations['Occupation'] = "Sedentary Job (-1000)"
    adjustments['Occupation'] = adj

    # 5. Activity Level Adjustment (from Water/Step Profile)
    act = activity_level.lower()
    if 'advanced' in act:
        adj = 2000
        explanations['Fitness Level'] = "Advanced Fitness (+2000)"
    elif 'intermediate' in act:
        adj = 1000
        explanations['Fitness Level'] = "Intermediate Fitness (+1000)"
    else: # Beginner
        adj = 0
        explanations['Fitness Level'] = "Beginner Fitness (+0)"
    adjustments['Activity Level'] = adj

    # 6. Goal / Health Adjustment
    goal = goal_type.lower()
    if 'weight loss' in goal:
        adj = 1000
        explanations['Goal'] = "Weight Loss (+1000)"
    elif 'weight gain' in goal:
        adj = -1000
        explanations['Goal'] = "Weight Gain Focus (-1000)"
    elif 'health' in goal or 'diabetes' in goal or 'management' in goal:
        adj = -500
        explanations['Goal'] = "Health Management Cap (-500)"
    else: # Maintenance
        adj = 0
        explanations['Goal'] = "Maintenance (+0)"
    adjustments['Goal'] = adj

    # Final Calculation
    total_steps = base_steps + sum(adjustments.values())
    total_steps = max(2000, total_steps)

    return {
        "base_steps": base_steps,
        "total_steps": int(total_steps),
        "breakdown": adjustments,
        "explanations": explanations
    }

def _calculate_water_logic(constraints: Dict, daily_steps: int) -> Dict:
    """Calculates personalized daily water goal in Liters and mL."""
    
    # Extract data with safe fallbacks
    weight_kg = constraints.get("weight_kg", 75.0)
    gender = constraints.get("gender", "Male")
    medical_conditions = constraints.get("medical_conditions", [])
    
    # Fallbacks from the new frontend form
    climate = constraints.get("climate", "Moderate / Temperate")
    diet_type = constraints.get("diet_type", "Standard")
    
    # 1. Baseline: Weight (kg) * 35 mL
    baseline_ml = weight_kg * 35

    # 2. Activity: 6 * (Steps / 100) - Assuming all activity is 6mL/100 steps
    activity_adj_ml = 6 * (daily_steps / 100)

    # 3. Climate Adjustment
    climate_adj_ml = 0
    clim = climate.lower()
    if "hot" in clim or "humid" in clim or "altitude" in clim:
        climate_adj_ml = 500
    elif "dry" in clim or "conditioned" in clim:
        climate_adj_ml = 250
    
    # 4. Diet Adjustment
    diet_adj_ml = 0
    diet = diet_type.lower()
    if "keto" in diet or "low-carb" in diet:
        diet_adj_ml = 500
    elif "protein" in diet or "fiber" in diet or "salt" in diet or "spicy" in diet:
        diet_adj_ml = 300

    # 5. Condition / Gender Adjustments
    condition_adj_ml = 0
    conditions_lower = [c.lower() for c in medical_conditions]
    
    # Only check pregnancy/lactation if gender is Female
    if gender.lower() == 'female':
        is_pregnant = any("pregnan" in c for c in conditions_lower)
        is_lactating = any("lactat" in c or "breastfeeding" in c for c in conditions_lower)
        if is_lactating:
            condition_adj_ml = 850
        elif is_pregnant:
            condition_adj_ml = 300

    # Final Calculation
    total_ml = baseline_ml + activity_adj_ml + climate_adj_ml + diet_adj_ml + condition_adj_ml
    total_liters = round(total_ml / 1000, 2)

    return {
        "total_liters": total_liters,
        "total_ml": int(total_ml),
        "breakdown": {
            "Baseline (Weight * 35)": int(baseline_ml),
            "Activity (Steps)": int(activity_adj_ml),
            "Climate Adj": climate_adj_ml,
            "Diet Adj": diet_adj_ml,
            "Condition Adj": condition_adj_ml
        }
    }


class WaterStepTool(BaseTool):
    def __init__(self, db_tool, llm_service: LLMService):
        super().__init__(
            ToolType.WATER_STEP_ADVISOR.value,
            "Calculates, logs, and reports daily step and water goals and progress."
        )
        self.db_tool = db_tool # For potential future logging
        self.llm_service = llm_service # For motivational response generation

    async def _generate_response(self, targets: Dict, current_progress: Dict, query: str) -> str:
        """Generates a conversational and motivational response using LLM."""
        
        target_steps = targets.get("target_steps", 8000)
        target_water = targets.get("target_water_liters", 2.5)
        
        current_steps = current_progress.get("current_steps", 0)
        current_water = current_progress.get("current_water_liters", 0)
        
        steps_left = max(0, target_steps - current_steps)
        water_left = max(0, target_water - current_water)

        system_prompt = f"""You are Friska, an extremely motivational, friendly, and detail-oriented AI wellness coach.
The user is checking their daily progress or asking about their goals. Provide a supportive, conversational response.

- Target Steps: {target_steps} | Steps Completed: {current_progress.get("current_steps", 0)}
- Target Water: {target_water:.1f} Liters | Water Completed: {current_progress.get("current_water_liters", 0.0):.1f} Liters
- Steps Remaining: {max(0, target_steps - current_progress.get("current_steps", 0))} | Water Remaining: {max(0, target_water - current_progress.get("current_water_liters", 0.0)):.1f} Liters

Instructions:
1. Start with a warm greeting and mention the user's name (if available in the constraints).
2. Clearly state the user's **current steps progress** compared to the goal (e.g., "You've crushed 75% of your steps!").
3. Clearly state the user's **current water progress** compared to the goal.
4. **Must include the goals and the remaining amount** in the response.
5. Use highly encouraging language. Frame the remaining amount as an achievable **challenge** for the rest of the day.
6. **MANDATORY PHRASING:** Your response MUST convey the sentiment: "I hope you will complete this [remaining steps/water] goal!"
"""
        user_prompt = f"The user asked: '{query}'"
        
        try:
            response = await self.llm_service.query(
                prompt=user_prompt,
                system_prompt=system_prompt,
                max_tokens=250,
                temperature=0.7
            )
            return response
        except Exception as e:
            logger.error(f"LLM response generation failed: {e}")
            return (
                f"Your daily goals are: **{target_steps} steps** and **{target_water:.1f} Liters of water**. "
                f"You have **{steps_left} steps** and **{water_left:.1f} Liters** remaining. "
                f"I hope you will complete this goal! You're doing great—keep pushing!"
            )


    async def execute(self, query: str, constraints: Dict, last_agent_context: Dict, **kwargs) -> ToolResult:
        
        # 1. Calculate the current daily goals (Steps and Water)
        step_calc_result = _calculate_steps_logic(constraints)
        target_steps = step_calc_result.get("total_steps")

        water_calc_result = _calculate_water_logic(constraints, target_steps)
        target_water = water_calc_result.get("total_liters")

        if target_steps is None or target_water is None:
            return ToolResult(
                success=False, 
                data=None, 
                error="I cannot calculate your step or water goals. Please ensure your Age, Weight, Height, and Goal are set."
            )
        
        
        
        current_progress = {
            "current_steps": constraints.get("current_steps", 0), 
            "current_water_liters": constraints.get("current_water_liters", 0.0) 
        }

        # 3. Check for logging intent
        log_match = re.search(r'\b(?:log|i walked|i drank|drank|had|drank)\s+(\d+)\s*(?:steps|ml|l|liter|cup)', query.lower())

        if log_match:
            # Handle logging intent (Future: This would call a logging API)
            value = float(log_match.group(1))
            unit_match = log_match.group(0)
            
            log_type = None
            if "step" in unit_match or "walked" in unit_match:
                log_type = "steps"
                current_progress["current_steps"] += int(value)
                log_message = f"I've logged **{int(value)} steps** for you. Your total steps are now **{current_progress['current_steps']}**."
            elif any(u in unit_match for u in ["ml", "l", "liter", "cup", "drank"]):
                log_type = "water"
                if "ml" in unit_match: water_logged = value / 1000
                elif "cup" in unit_match: water_logged = value * 0.236588 # US cup to Liters
                else: water_logged = value
                
                current_progress["current_water_liters"] += water_logged
                log_message = f"I've logged **{water_logged:.2f} Liters** of water. Your total water intake is now **{current_progress['current_water_liters']:.2f} Liters**."
            else:
                 log_message = "I couldn't identify if that was steps or water. Please specify (e.g., 'log 1000 steps')."
            
            # Update constraints for persistence in next turn
            constraints['current_steps'] = current_progress['current_steps']
            constraints['current_water_liters'] = current_progress['current_water_liters']
            
            # Follow up with goal status
            llm_goal_response = await self._generate_response(
                targets={"target_steps": target_steps, "target_water_liters": target_water},
                current_progress=current_progress,
                query=f"Follow up to logging: {log_message}"
            )
            
            return ToolResult(
                success=True,
                data={"answer": f"{log_message}\n\n{llm_goal_response}"},
                metadata={"updated_constraints": constraints}
            )

        # 4. Generate goal status response
        llm_response = await self._generate_response(
            targets={"target_steps": target_steps, "target_water_liters": target_water},
            current_progress=current_progress,
            query=query
        )
        
        # Update constraints with targets for next turns, but don't overwrite progress
        constraints['target_steps'] = target_steps
        constraints['target_water_liters'] = target_water
        
        return ToolResult(
            success=True,
            data={"answer": llm_response},
            metadata={"updated_constraints": constraints}
        )