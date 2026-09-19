"""
Meal Plan Display Component
Renders hybrid JSON meal plans with markdown text and interactive recipe exploration.
"""

import streamlit as st
import json
from typing import List, Dict, Optional


def display_hybrid_meal_plan(
    meal_plan_text: str,
    foods_data: List[Dict],
    plan_name: str = "Daily Meal Plan",
    plan_type: str = "DAILY"
):
    """
    Display meal plan using hybrid JSON structure.
    
    Args:
        meal_plan_text: Markdown-formatted meal plan text
        foods_data: List of food metadata dicts with structure:
                    [{"food_name": str, "food_id": str, "ingredients": List[str], "recipe": List[str]}]
        plan_name: Name of the meal plan
        plan_type: Type (DAILY, WEEKLY)
    """
    
    # Header
    st.markdown(f"### {plan_name}")
    if plan_type:
        st.caption(f"Plan Type: {plan_type}")
    st.markdown("---")
    
    # Render meal plan text as markdown
    st.markdown(meal_plan_text, unsafe_allow_html=True)
    
    st.markdown("---")
    
    # Explore Recipes Section
    if foods_data and len(foods_data) > 0:
        st.markdown("### 🍽️ Explore Recipes")
        st.caption(f"Click on any food item below to view its ingredients and recipe")
        
        # Create columns for a grid layout (3 cards per row)
        cols_per_row = 3
        num_foods = len(foods_data)
        
        for i in range(0, num_foods, cols_per_row):
            cols = st.columns(cols_per_row)
            
            for j in range(cols_per_row):
                idx = i + j
                if idx >= num_foods:
                    break
                
                food = foods_data[idx]
                food_name = food.get("food_name", "Unknown Food")
                food_id = food.get("food_id", "N/A")
                ingredients = food.get("ingredients", [])
                recipe = food.get("recipe", [])
                
                with cols[j]:
                    # Create an expander for each food item
                    with st.expander(f"📌 {food_name}", expanded=False):
                        st.markdown(f"**Food ID:** `{food_id}`")
                        
                        # Display Ingredients
                        if ingredients and len(ingredients) > 0:
                            st.markdown("**🥗 Ingredients:**")
                            for ingredient in ingredients:
                                st.markdown(f"- {ingredient}")
                        else:
                            st.info("No ingredients available")
                        
                        st.markdown("")  # Spacing
                        
                        # Display Recipe Steps
                        if recipe and len(recipe) > 0:
                            st.markdown("**👨‍🍳 Recipe:**")
                            for step_idx, step in enumerate(recipe, 1):
                                st.markdown(f"{step_idx}. {step}")
                        else:
                            st.info("No recipe steps available")
                        
                        # Add a button to simulate handleFoodClick(food_id)
                        if st.button(f"View Details", key=f"btn_{food_id}_{idx}"):
                            handle_food_click(food_id, food_name)


def handle_food_click(food_id: str, food_name: str):
    """
    Handle food card click event.
    
    Args:
        food_id: Unique identifier for the food item
        food_name: Name of the food item
    """
    st.toast(f"🍽️ Viewing details for {food_name} (ID: {food_id})", icon="ℹ️")
    
    # Store in session state for future use
    if "selected_food" not in st.session_state:
        st.session_state.selected_food = {}
    
    st.session_state.selected_food = {
        "food_id": food_id,
        "food_name": food_name
    }
    
    # You can add more logic here, such as:
    # - Fetch detailed nutrition information from database
    # - Display a modal/sidebar with more details
    # - Navigate to a dedicated food details page
    # - Log analytics event
    
    st.info(f"Selected: {food_name} (ID: {food_id})")


def display_legacy_meal_plan(meal_plan_text: str):
    """
    Display meal plan using legacy text-only format.
    Falls back to simple markdown rendering.
    
    Args:
        meal_plan_text: Meal plan text (markdown or plain text)
    """
    st.markdown(meal_plan_text, unsafe_allow_html=True)


# Example usage function
def render_meal_plan(meal_plan_data: Dict):
    """
    Smart renderer that detects format and uses appropriate display function.
    
    Args:
        meal_plan_data: Dictionary containing meal plan data. Can be:
            - Hybrid JSON: {"meal_plan_text": str, "foods_data": List[Dict], ...}
            - Legacy: {"meal_plan_text": str} or plain string
    """
    
    # Handle string input (legacy format)
    if isinstance(meal_plan_data, str):
        display_legacy_meal_plan(meal_plan_data)
        return
    
    # Handle dictionary input
    if not isinstance(meal_plan_data, dict):
        st.error("Invalid meal plan data format")
        return
    
    # Extract fields
    meal_plan_text = meal_plan_data.get("meal_plan_text", "")
    foods_data = meal_plan_data.get("foods_data", [])
    plan_name = meal_plan_data.get("plan_name", "Daily Meal Plan")
    plan_type = meal_plan_data.get("plan_type", "DAILY")
    
    # Check if this is hybrid JSON format
    if foods_data and isinstance(foods_data, list) and len(foods_data) > 0:
        display_hybrid_meal_plan(meal_plan_text, foods_data, plan_name, plan_type)
    else:
        # Fall back to legacy display
        st.markdown(f"### {plan_name}")
        if plan_type:
            st.caption(f"Plan Type: {plan_type}")
        st.markdown("---")
        display_legacy_meal_plan(meal_plan_text)
