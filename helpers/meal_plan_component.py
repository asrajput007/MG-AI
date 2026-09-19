"""
Streamlit Meal Plan Component
==============================
Interactive UI component for displaying meal plans with clickable food items.

This component:
1. Parses meal plan text to strip embedded metadata
2. Renders clean Markdown for the UI
3. Makes food items clickable to show detailed information
4. Handles food item clicks with handleFoodClick(food_id)
"""

import streamlit as st
import re
from typing import Dict, List, Optional
from helpers.meal_plan_parser import MealPlanParser, FoodItemMetadata


class MealPlanRenderer:
    """Streamlit component for rendering interactive meal plans"""
    
    @staticmethod
    def render_meal_plan(meal_plan_text: str, show_food_details: bool = True):
        """
        Render a meal plan with clickable food items.
        
        Args:
            meal_plan_text: Raw meal plan text with embedded metadata
            show_food_details: If True, make food items clickable for detail view
        """
        # Parse the meal plan to extract metadata
        cleaned_text, metadata_list = MealPlanParser.parse_meal_plan_text(meal_plan_text)
        
        if not metadata_list:
            # No metadata found, just display as regular markdown
            st.markdown(cleaned_text)
            return
        
        # Create a mapping for quick lookup
        metadata_map = {meta.food_name: meta for meta in metadata_list}
        
        if show_food_details:
            # Render with interactive food items
            MealPlanRenderer._render_interactive_meal_plan(
                cleaned_text, 
                metadata_map
            )
        else:
            # Just render the cleaned markdown
            st.markdown(cleaned_text)
    
    @staticmethod
    def _render_interactive_meal_plan(cleaned_text: str, metadata_map: Dict[str, FoodItemMetadata]):
        """
        Render meal plan with clickable food items using custom HTML/CSS.
        
        Args:
            cleaned_text: The cleaned meal plan text
            metadata_map: Mapping of food names to their metadata
        """
        # Split into lines for processing
        lines = cleaned_text.split('\n')
        html_parts = []
        
        # CSS for styling
        css = """
        <style>
        .meal-plan-container {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            line-height: 1.6;
        }
        .meal-header {
            font-size: 1.3em;
            font-weight: bold;
            color: #1f77b4;
            margin-top: 1em;
            margin-bottom: 0.5em;
        }
        .food-item {
            padding: 8px 12px;
            margin: 4px 0;
            border-left: 3px solid #4CAF50;
            background-color: #f8f9fa;
            cursor: pointer;
            transition: all 0.2s ease;
            border-radius: 4px;
        }
        .food-item:hover {
            background-color: #e9ecef;
            border-left-color: #2e7d32;
            transform: translateX(5px);
        }
        .food-item-clickable {
            color: #1976d2;
        }
        .meal-summary {
            font-style: italic;
            color: #666;
            margin: 8px 0;
        }
        .food-detail-card {
            border: 1px solid #ddd;
            border-radius: 8px;
            padding: 16px;
            margin: 12px 0;
            background-color: #ffffff;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .food-detail-header {
            font-size: 1.2em;
            font-weight: bold;
            color: #2e7d32;
            margin-bottom: 12px;
        }
        .food-detail-section {
            margin: 8px 0;
        }
        .food-detail-label {
            font-weight: bold;
            color: #555;
        }
        </style>
        """
        
        html_parts.append(css)
        html_parts.append('<div class="meal-plan-container">')
        
        # Pattern to identify food item lines (starting with -)
        food_item_pattern = re.compile(r'^-\s*(.+)')
        
        for line in lines:
            if not line.strip():
                html_parts.append('<br>')
                continue
            
            # Check if it's a meal header (**Breakfast**, etc.)
            if line.startswith('**') and line.endswith('**'):
                meal_name = line.replace('**', '')
                html_parts.append(f'<div class="meal-header">{meal_name}</div>')
                continue
            
            # Check if it's a food item line
            food_match = food_item_pattern.match(line)
            if food_match:
                food_line = food_match.group(1)
                
                # Try to find this food item in metadata
                food_name = MealPlanParser._extract_food_name(line)
                
                if food_name in metadata_map:
                    # Make it clickable
                    food_id = metadata_map[food_name].food_id
                    # Use a unique key for the button based on food_id
                    html_parts.append(f'<div class="food-item food-item-clickable" data-food-id="{food_id}">• {food_line}</div>')
                else:
                    # Not in metadata, just display normally
                    html_parts.append(f'<div class="food-item">• {food_line}</div>')
                continue
            
            # Check if it's a summary line
            if 'Total calories' in line or 'Macronutrient Ratio' in line:
                html_parts.append(f'<div class="meal-summary">{line}</div>')
                continue
            
            # Regular text line
            html_parts.append(f'<div>{line}</div>')
        
        html_parts.append('</div>')
        
        # Render the HTML
        st.markdown(''.join(html_parts), unsafe_allow_html=True)
        
        # Add clickable functionality using Streamlit buttons
        st.markdown("---")
        st.markdown("##### 🍽️ Click on a food item to see details:")
        
        # Create clickable buttons for each food item
        for food_name, metadata in metadata_map.items():
            if st.button(f"📋 {food_name}", key=f"food_btn_{metadata.food_id}"):
                handle_food_click(metadata.food_id, metadata)
    
    @staticmethod
    def render_meal_plan_simple(meal_plan_text: str):
        """
        Simple rendering of meal plan with cleaned text only (no interactivity).
        
        Args:
            meal_plan_text: Raw meal plan text with embedded metadata
        """
        cleaned_text, _ = MealPlanParser.parse_meal_plan_text(meal_plan_text)
        
        # Apply custom styling for meal summaries
        styled_text = re.sub(
            r'^(Total calories for .*?)$', 
            r'<div class="meal-summary">\1</div>', 
            cleaned_text, 
            flags=re.MULTILINE
        )
        styled_text = re.sub(
            r'^(Macronutrient Ratio: .*?)$', 
            r'<div class="meal-summary">\1</div>', 
            styled_text, 
            flags=re.MULTILINE
        )
        
        css = """
        <style>
        .meal-summary {
            font-style: italic;
            color: #666;
            background-color: #f0f0f0;
            padding: 8px;
            margin: 8px 0;
            border-radius: 4px;
        }
        </style>
        """
        
        st.markdown(css + styled_text, unsafe_allow_html=True)


def handle_food_click(food_id: str, metadata: Optional[FoodItemMetadata] = None):
    """
    Handle click event on a food item.
    This is the placeholder function mentioned in requirements.
    
    Args:
        food_id: The unique identifier for the food item
        metadata: Optional metadata object with full food details
    """
    st.session_state.selected_food_id = food_id
    st.session_state.selected_food_metadata = metadata
    
    # Display food details in an expander or modal
    if metadata:
        st.markdown("---")
        st.markdown(f"### 🍽️ {metadata.food_name}")
        
        col1, col2 = st.columns([1, 2])
        
        with col1:
            st.markdown("**Meal Type:**")
            st.info(metadata.meal_type or "N/A")
        
        with col2:
            if metadata.ingredients:
                st.markdown("**Ingredients:**")
                for ingredient in metadata.ingredients:
                    st.markdown(f"- {ingredient}")
            else:
                st.markdown("**Ingredients:** *Not available*")
        
        if metadata.recipe:
            st.markdown("**Recipe:**")
            for i, step in enumerate(metadata.recipe, 1):
                st.markdown(f"{i}. {step}")
        else:
            st.markdown("**Recipe:** *Not available*")
        
        st.markdown("---")
    else:
        st.warning(f"No metadata found for food_id: {food_id}")


def render_meal_plan_with_collapsible_details(meal_plan_text: str):
    """
    Alternative rendering approach using expanders for food details.
    
    Args:
        meal_plan_text: Raw meal plan text with embedded metadata
    """
    # Parse the meal plan
    cleaned_text, metadata_list = MealPlanParser.parse_meal_plan_text(meal_plan_text)
    
    # Display the cleaned meal plan
    st.markdown("### 📋 Your Meal Plan")
    MealPlanRenderer.render_meal_plan_simple(meal_plan_text)
    
    # Show food details in expandable sections
    if metadata_list:
        st.markdown("---")
        st.markdown("### 🔍 Food Item Details")
        
        # Group by meal type
        meal_groups = {}
        for meta in metadata_list:
            meal_type = meta.meal_type or "Other"
            if meal_type not in meal_groups:
                meal_groups[meal_type] = []
            meal_groups[meal_type].append(meta)
        
        # Render each meal type
        for meal_type, items in meal_groups.items():
            with st.expander(f"**{meal_type}** ({len(items)} items)"):
                for meta in items:
                    st.markdown(f"#### {meta.food_name}")
                    
                    col1, col2 = st.columns(2)
                    with col1:
                        st.markdown(f"**Meal Type:** {meta.meal_type or 'N/A'}")
                    with col2:
                        if st.button("📊 View Full Details", key=f"detail_{meta.food_id}"):
                            handle_food_click(meta.food_id, meta)
                    
                    if meta.ingredients:
                        with st.container():
                            st.markdown("**Ingredients:**")
                            st.markdown(", ".join(meta.ingredients))
                    
                    if meta.recipe:
                        with st.container():
                            st.markdown("**Preparation:**")
                            for i, step in enumerate(meta.recipe, 1):
                                st.markdown(f"{i}. {step}")
                    
                    st.markdown("---")


# Convenience function for easy integration
def display_meal_plan(meal_plan_text: str, interactive: bool = True, style: str = "simple"):
    """
    Main entry point for displaying meal plans.
    
    Args:
        meal_plan_text: Raw meal plan text with embedded metadata
        interactive: Whether to make food items clickable
        style: Display style - "simple", "interactive", or "collapsible"
    """
    if not meal_plan_text or not isinstance(meal_plan_text, str):
        st.warning("No meal plan text provided")
        return
    
    if style == "collapsible":
        render_meal_plan_with_collapsible_details(meal_plan_text)
    elif style == "interactive" and interactive:
        renderer = MealPlanRenderer()
        renderer.render_meal_plan(meal_plan_text, show_food_details=True)
    else:
        # Simple style
        MealPlanRenderer.render_meal_plan_simple(meal_plan_text)


# Example usage in Streamlit app
if __name__ == "__main__":
    st.set_page_config(page_title="Meal Plan Viewer", layout="wide")
    
    st.title("🍽️ Interactive Meal Plan Viewer")
    
    # Sample meal plan text with metadata
    sample_text = """Here is your meal plan based on your preferences and health needs:

**Breakfast**
- Egg White Spinach Scramble with Whole Grain Toast | Household Measure: 0.5 plate | Portion Weight: 7 oz | Protein: 17g | Carbs: 10g | Fat: 6g | Fiber: 2g | Sodium: 201mg | Iodine: 33mcg | Sugar: 2g | Cholesterol: 0mg | Calories: 159kcal | "food_id": "FOOD_NORT_PROT_005" | "ingredients": ["egg whites", "spinach", "whole grain bread", "olive oil"] | "recipe": ["Whisk egg whites", "Sauté spinach in olive oil", "Scramble eggs with spinach", "Toast bread", "Serve together"] |
- Cranberry Pumpkin Seed Mix | Household Measure: 1 small handful | Portion Weight: 1 oz | Protein: 5g | Carbs: 6g | Fat: 9g | Fiber: 2g | Sodium: 2mg | Iodine: 1mcg | Sugar: 2g | Cholesterol: 0mg | Calories: 119kcal | "food_id": "FOOD_NORT_SNACK_012" | "ingredients": ["dried cranberries", "pumpkin seeds"] | "recipe": [] |

Total calories for Breakfast: 278.0 kcal
Macronutrient Ratio: Protein 22.0g (31%), Carbs 16.0g (23%), Fat 15.0g (46%)

**Lunch**
- Black Bean and Veggie Wrap | Household Measure: 0.75 wrap | Portion Weight: 7 oz | Protein: 11g | Carbs: 33g | Fat: 9g | Fiber: 8g | Sodium: 275mg | Iodine: 8mcg | Sugar: 3g | Cholesterol: 0mg | Calories: 254kcal | "food_id": "FOOD_NORT_LUNCH_025" | "ingredients": ["black beans", "whole wheat tortilla", "bell peppers", "onions", "lettuce", "tomatoes"] | "recipe": ["Cook black beans", "Sauté vegetables", "Warm tortilla", "Assemble wrap with beans and vegetables", "Roll and serve"] |

Total calories for Lunch: 254.0 kcal
Macronutrient Ratio: Protein 11.0g (17%), Carbs 33.0g (52%), Fat 9.0g (31%)
"""
    
    # Display style selector
    style = st.selectbox(
        "Choose display style:",
        ["simple", "interactive", "collapsible"]
    )
    
    # Render the meal plan
    display_meal_plan(sample_text, interactive=True, style=style)
