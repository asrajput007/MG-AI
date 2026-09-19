"""
Cuisine-Based Meal Combination Rules

Defines valid food-group combinations for each cuisine and meal type.
Each combination is a list of food category names that pair well together.
The system selects one combination randomly, maps each category to technical
slot names, and uses those slots to assemble the meal.

Rules:
1. Each meal gets foods from a valid combination of food categories
2. No nuts/seeds allowed in dinner (enforced separately)
3. Combinations are culturally appropriate for each cuisine
"""

import logging

logger = logging.getLogger(__name__)

# ============================================================================
# COMBO CATEGORY → SLOT MAPPING
# Maps human-readable food categories in combinations to technical slot names
# used by the meal generator's slot_pools system.
# ============================================================================
COMBO_CATEGORY_TO_SLOTS = {
    # --- PROTEIN SOURCES ---
    "plant-based protein foods": ["protein_main", "lean_protein", "protein_source"],
    "mixed non-vegetarian dishes": ["protein_main"],
    "fish dishes": ["protein_main", "lean_protein"],
    "fish and seafood dishes": ["protein_main", "lean_protein"],
    "seafood dishes": ["protein_main", "lean_protein"],
    "baked fish dishes": ["protein_main", "lean_protein"],
    "grilled seafood": ["protein_main", "lean_protein"],
    "shellfish dishes": ["protein_main"],
    "seafood bowls": ["protein_main"],
    "seafood stews": ["protein_main", "soup_or_broth"],
    "red meat dishes": ["protein_main"],
    "egg-based dishes": ["lean_protein", "protein_main"],
    "vegetable and egg dishes": ["lean_protein", "protein_main"],
    "vegetarian main dishes": ["protein_main"],
    "vegetarian dishes": ["protein_main", "lean_protein"],
    "sushi and sashimi": ["protein_main"],
    "legumes": ["protein_main", "lean_protein"],
    "pulses": ["protein_main", "lean_protein"],
    
    # --- GRAINS / STARCH ---
    "porridges": ["grain_base", "starch_base"],
    "cereals": ["grain_base"],
    "bread": ["grain_base", "starch_base"],
    "rice dishes": ["grain_base", "starch_base"],
    "grains": ["grain_base", "starch_base", "light_grain"],
    "flatbreads": ["grain_base", "starch_base"],
    "wholegrain loaves": ["grain_base", "starch_base"],
    "rice and beans dishes": ["grain_base", "starch_base"],
    "grain and legume dishes": ["grain_base", "light_grain"],
    "grains and pulses": ["grain_base", "light_grain"],
    "dosas": ["grain_base", "starch_base"],
    "tortillas and flatbread wraps": ["grain_base", "starch_base"],
    "stuffed corn cakes": ["grain_base", "starch_base"],
    "vegetable dumplings": ["grain_base"],
    "roasted dishes": ["grain_base", "starch_base"],
    
    # --- VEGETABLES / SALADS ---
    "salads": ["primary_vegetable", "cooked_vegetable"],
    "leafy salads": ["primary_vegetable"],
    "vegetable salads": ["primary_vegetable"],
    "vegetable salad": ["primary_vegetable"],
    "cooked vegetable dishes": ["cooked_vegetable", "primary_vegetable"],
    "cooked vegetables": ["cooked_vegetable", "primary_vegetable"],
    "sautéed vegetable dishes": ["cooked_vegetable", "primary_vegetable"],
    "roasted vegetable dishes": ["cooked_vegetable"],
    "roasted vegetables": ["cooked_vegetable"],
    "roasted root vegetable dishes": ["cooked_vegetable"],
    "steamed vegetable dishes": ["cooked_vegetable"],
    "braised vegetable dishes": ["cooked_vegetable"],
    "vegetable stir-fries": ["cooked_vegetable"],
    "vegetable gratins": ["cooked_vegetable"],
    "vegetable purees": ["cooked_vegetable"],
    "vegetable side dishes": ["cooked_vegetable", "primary_vegetable"],
    "vegetable stews": ["cooked_vegetable", "soup_or_broth"],
    "leafy green dishes": ["primary_vegetable"],
    "leafy green vegetables": ["primary_vegetable"],
    "vegetables": ["primary_vegetable", "cooked_vegetable"],
    "eggplant dishes": ["cooked_vegetable"],
    "plantain dishes": ["cooked_vegetable", "starch_base"],
    "mushroom dishes": ["cooked_vegetable"],
    "sea vegetables": ["cooked_vegetable"],
    "seaweed dishes": ["cooked_vegetable"],
    "seaweed products": ["cooked_vegetable"],
    "vegetable porridges": ["cooked_vegetable", "grain_base"],
    "vegetable dishes": ["cooked_vegetable", "primary_vegetable"],
    "vegetable and legume dishes": ["primary_vegetable", "protein_main"],
    "vegetable curries": ["cooked_vegetable"],
    "vegetarian curries": ["cooked_vegetable"],
    
    # --- SOUPS ---
    "soups": ["soup_or_broth"],
    "soups and broths": ["soup_or_broth"],
    "soups and stews": ["soup_or_broth"],
    "vegetable soups": ["soup_or_broth"],
    "vegetable soup": ["soup_or_broth"],
    "stews": ["soup_or_broth"],
    "stews and soups": ["soup_or_broth"],
    "stews and curries": ["soup_or_broth"],
    "hot pot dishes": ["soup_or_broth"],
    
    # --- DAIRY ---
    "low-fat dairy products": ["dairy_or_alt"],
    "low-fat dairy products / dairy alternative": ["dairy_or_alt"],
    "yogurt bowls": ["dairy_or_alt"],
    "yogurt dishes": ["dairy_or_alt"],
    "yogurt and parfaits": ["dairy_or_alt"],
    "plant-based milk alternatives": ["dairy_or_alt"],
    "cream cheese and soft cheese": ["dairy_or_alt"],
    
    # --- NUTS / SEEDS ---
    "seeds and nuts": ["nut_seed"],
    "nuts and seeds": ["nut_seed"],
    
    # --- FRUITS ---
    "fresh fruits": ["fruit", "fruit_snack"],
    
    # --- FATS / CONDIMENTS ---
    "avocado dishes": ["fat_source"],
    "dips and spreads": ["condiment_side"],
    "chutneys and relishes": ["condiment_side"],
    "olives": ["condiment_side", "fat_source"],
    "antipasti": ["condiment_side"],
    "pickled vegetables": ["fermented_side"],
    "fermented foods": ["fermented_side"],
    
    # --- BEVERAGES ---
    "coffee beverages": ["warm_beverage"],
    "tea beverages": ["warm_beverage"],
    "herbal beverages": ["warm_beverage"],
    "herbal drinks": ["warm_beverage"],
    "infused water": ["beverage"],
    "flavored water": ["beverage"],
    "smoothies and shakes": ["beverage", "dairy_or_alt"],
    
    # --- SPICES (no dedicated slot - ignored in slot override) ---
    "spices": [],
    "spices and peppers": [],
    "spices and seasonings": [],
}


def convert_combo_to_slots(combo_list: list) -> list:
    """
    Convert a cuisine combination (list of category names) to slot-based required_slots.
    
    Args:
        combo_list: e.g. ["Porridges", "Low-fat dairy products", "seeds and nuts"]
    
    Returns:
        List of slot option lists, e.g. [["grain_base", "starch_base"], ["dairy_or_alt"], ["nut_seed"]]
        Returns empty list if mapping fails.
    """
    if not combo_list:
        return []
    
    required_slots = []
    for category in combo_list:
        category_lower = category.lower().strip()
        slots = COMBO_CATEGORY_TO_SLOTS.get(category_lower, [])
        if slots:
            required_slots.append(slots)
        else:
            # Unknown category - log warning but skip it
            logger.warning(f"  COMBO→SLOT: Unknown category '{category}' - no slot mapping found, skipping")
    
    return required_slots


def convert_combo_to_slots_with_categories(combo_list):
    """
    Convert a combo category list to slots WITH per-slot source category info.
    This allows per-slot filtering during food selection.
    
    Returns:
        List of (slot_options, source_category) tuples
    """
    if not combo_list:
        return []
    
    result = []
    for category in combo_list:
        category_lower = category.lower().strip()
        slots = COMBO_CATEGORY_TO_SLOTS.get(category_lower, [])
        if slots:
            result.append((slots, category_lower))
        else:
            logger.warning(f"  COMBO→SLOT: Unknown category '{category}' - no slot mapping found, skipping")
    
    return result


# Cuisine-based meal combination rules
# Format: {cuisine: {meal_type: [[category1, category2, category3], ...]}}
CUISINE_MEAL_COMBINATIONS = {
    "african": {
        "breakfast": [
            ["Porridges", "Seeds and nuts", "Tea beverages"],
            ["Egg-based dishes", "Vegetable side dishes", "Coffee beverages"],
            ["Plantain dishes", "Legumes", "Herbal beverages"],
            ["Yogurt bowls", "Fresh fruits", "Infused water"],
            ["Vegetable and egg dishes", "Cooked vegetable dishes", "Tea beverages"],
            ["Plant-based protein foods", "Vegetable purees", "Flavored water"],
            ["Fish dishes", "Leafy green vegetables", "Coffee beverages"]
        ],
        "lunch": [
            ["Stews and curries", "Leafy green vegetables", "Infused water"],
            ["Mixed non-vegetarian dishes", "Vegetable side dishes", "Tea beverages"],
            ["Fish dishes", "Vegetable salads", "Grains and pulses"],
            ["Legumes", "Roasted dishes", "Herbal drinks"],
            ["Plant-based protein foods", "Salads", "Flavored water"],
            ["Vegetable curries", "Pulses", "Infused water"],
            ["Seafood stews", "Leafy salads", "Tea beverages"]
        ],
        "dinner": [
            ["Seafood stews", "Leafy salads", "Vegetable purees"],
            ["Vegetable and legume dishes", "Roasted root vegetable dishes", "Herbal beverages"],
            ["Grilled seafood", "Vegetable stews", "Infused water"],
            ["Mixed non-vegetarian dishes", "Steamed vegetable dishes", "Tea beverages"],
            ["Fish dishes", "Cooked vegetable dishes", "Flavored water"],
            ["Vegetarian main dishes", "Mushroom dishes", "Infused water"],
            ["Plant-based protein foods", "Leafy green dishes", "Herbal drinks"]
        ]
    },
    "central american": {
        "breakfast": [
            ["Porridges", "Low-fat dairy products", "Seeds and nuts"],
            ["Egg-based dishes", "Vegetables", "Bread"],
            ["Cereals", "Low-fat dairy products / dairy alternative"],
            ["Plant-based protein foods", "Vegetables"],
            ["Porridges", "Low-fat dairy products", "Seeds and nuts"]
        ],
        "lunch": [
            ["Mixed non-vegetarian dishes", "Salads", "Cooked vegetable dishes"],
            ["Seafood dishes", "Grains", "Vegetable salads"],
            ["Plant-based protein foods", "Tortillas and flatbread wraps", "Salads"],
            ["Legumes", "Rice and beans dishes", "Vegetable salads"],
            ["Mixed non-vegetarian dishes", "Cooked vegetables", "Salads"],
            ["Fish dishes", "Grains", "Vegetable salads"],
            ["Pulses", "Rice and beans dishes", "Salads", "Vegetable dishes"]
        ],
        "dinner": [
            ["Mixed non-vegetarian dishes", "Salads", "Soups"],
            ["Seafood dishes", "Vegetable salads", "Soups and stews"],
            ["Plant-based protein foods", "Salads", "Vegetable soups"],
            ["Red meat dishes", "Vegetable salads"],
            ["Fish dishes", "Salads", "Soups and stews"],
            ["Plant-based protein foods", "Vegetable salad", "Cooked vegetable dishes"],
            ["Legumes", "Salads", "Roasted vegetable dishes"]
        ]
    },
    "chinese": {
        "breakfast": [
            ["Porridges", "Egg-based dishes", "Tea beverages"],
            ["Vegetable dumplings", "Plant-based milk alternatives", "Fresh fruits"],
            ["Soups and broths", "Vegetable stir-fries", "Seeds and nuts"],
            ["Plant-based protein foods", "Steamed vegetable dishes", "Tea beverages"],
            ["Vegetable and egg dishes", "Mushroom dishes", "Herbal beverages"],
            ["Fish dishes", "Leafy green vegetables", "Infused water"],
            ["Yogurt bowls", "Fresh fruits", "Flavored water"]
        ],
        "lunch": [
            ["Mixed non-vegetarian dishes", "Vegetable stir-fries", "Soups"],
            ["Fish dishes", "Leafy green dishes", "Infused water"],
            ["Plant-based protein foods", "Mushroom dishes", "Tea beverages"],
            ["Legumes", "Roasted vegetable dishes", "Tea beverages"],
            ["Vegetable curries", "Pulses", "Flavored water"],
            ["Seafood dishes", "Vegetable soups", "Herbal drinks"],
            ["Vegetarian dishes", "Grains and pulses", "Tea beverages"]
        ],
        "dinner": [
            ["Hot pot dishes", "Seaweed products", "Vegetable dishes"],
            ["Vegetable and egg dishes", "Steamed vegetable dishes", "Tea beverages"],
            ["Seafood dishes", "Braised vegetable dishes", "Herbal drinks"],
            ["Baked fish dishes", "Vegetable side dishes", "Tea beverages"],
            ["Vegetable and legume dishes", "Mushroom dishes", "Herbal drinks"],
            ["Mixed non-vegetarian dishes", "Vegetable stews", "Infused water"],
            ["Plant-based protein foods", "Leafy green dishes", "Herbal beverages"]
        ]
    },
    "european": {
        "breakfast": [
            ["Wholegrain loaves", "Low-fat dairy products", "Coffee beverages"],
            ["Egg-based dishes", "Roasted vegetable dishes", "Tea beverages"],
            ["Yogurt bowls", "Nuts and seeds", "Fresh fruits"],
            ["Porridges", "Fresh fruits", "Herbal beverages"],
            ["Vegetable and egg dishes", "Cooked vegetable dishes", "Infused water"],
            ["Plant-based protein foods", "Leafy salads", "Flavored water"],
            ["Fish dishes", "Vegetable side dishes", "Coffee beverages"]
        ],
        "lunch": [
            ["Fish and seafood dishes", "Leafy salads", "Infused water"],
            ["Salads", "Mixed non-vegetarian dishes", "Vegetable soups"],
            ["Vegetarian dishes", "Grain and legume dishes", "Flavored water"],
            ["Vegetable curries", "Pulses", "Flavored water"],
            ["Plant-based protein foods", "Cooked vegetable dishes", "Infused water"],
            ["Seafood dishes", "Vegetable soups", "Herbal drinks"],
            ["Legumes", "Roasted vegetable dishes", "Tea beverages"]
        ],
        "dinner": [
            ["Grilled seafood", "Steamed vegetable dishes", "Herbal beverages"],
            ["Vegetable gratins", "Salads", "Legumes"],
            ["Baked fish dishes", "Leafy green vegetables", "Tea beverages"],
            ["Stews and soups", "Roasted vegetable dishes", "Herbal beverages"],
            ["Vegetable and legume dishes", "Mushroom dishes", "Herbal drinks"],
            ["Mixed non-vegetarian dishes", "Vegetable stews", "Infused water"],
            ["Plant-based protein foods", "Leafy green dishes", "Herbal beverages"]
        ]
    },
    "international": {
        "breakfast": [
            ["Yogurt and parfaits", "Seeds and nuts", "Tea beverages"],
            ["Egg-based dishes", "Wholegrain loaves", "Coffee beverages"],
            ["Smoothies and shakes", "Plant-based protein foods", "Fresh fruits"],
            ["Porridges", "Low-fat dairy products", "Herbal beverages"],
            ["Vegetable and egg dishes", "Roasted vegetable dishes", "Infused water"],
            ["Fish dishes", "Vegetable salads", "Flavored water"],
            ["Plant-based protein foods", "Leafy green dishes", "Tea beverages"]
        ],
        "lunch": [
            ["Salads", "Grilled seafood", "Infused water"],
            ["Mixed non-vegetarian dishes", "Roasted Vegetables", "Vegetable soups"],
            ["Shellfish dishes", "Leafy salads", "Herbal beverages"],
            ["Vegetable curries", "Pulses", "Flavored water"],
            ["Plant-based protein foods", "Cooked vegetable dishes", "Infused water"],
            ["Seafood dishes", "Vegetable soups", "Herbal drinks"],
            ["Vegetarian dishes", "Grains and pulses", "Tea beverages"]
        ],
        "dinner": [
            ["Baked fish dishes", "Leafy salads", "Herbal beverages"],
            ["Vegetarian main dishes", "Legumes", "Vegetable side dishes"],
            ["Vegetarian curries", "Grains and pulses", "Infused water"],
            ["Stews and soups", "Roasted vegetable dishes", "Herbal beverages"],
            ["Grilled seafood", "Steamed vegetable dishes", "Infused water"],
            ["Vegetable and legume dishes", "Mushroom dishes", "Herbal drinks"],
            ["Plant-based protein foods", "Leafy green dishes", "Herbal beverages"]
        ]
    },
    "indian": {
        "breakfast": [
            ["Dosas", "Chutneys and relishes", "Tea beverages"],
            ["Porridges", "Seeds and nuts", "Low-fat dairy products"],
            ["Vegetable and egg dishes", "Flatbreads", "Fresh fruits"],
            ["Plant-based protein foods", "Vegetable side dishes", "Coffee beverages"],
            ["Yogurt bowls", "Fresh fruits", "Infused water"],
            ["Legumes", "Cooked vegetable dishes", "Herbal beverages"],
            ["Fish dishes", "Leafy salads", "Tea beverages"]
        ],
        "lunch": [
            ["Vegetable curries", "Legumes", "Leafy salads"],
            ["Mixed non-vegetarian dishes", "Grains and pulses", "Yogurt dishes"],
            ["Fish dishes", "Leafy salads", "Infused water"],
            ["Vegetable curries", "Pulses", "Flavored water"],
            ["Plant-based protein foods", "Cooked vegetable dishes", "Infused water"],
            ["Seafood dishes", "Vegetable soups", "Herbal drinks"],
            ["Vegetarian dishes", "Grains and pulses", "Tea beverages"]
        ],
        "dinner": [
            ["Vegetarian curries", "Roasted vegetable dishes", "Infused water"],
            ["Fish dishes", "Vegetable dishes", "Spices and peppers"],
            ["Vegetarian dishes", "Pulses", "Herbal drinks"],
            ["Stews and soups", "Roasted vegetable dishes", "Herbal beverages"],
            ["Grilled seafood", "Steamed vegetable dishes", "Infused water"],
            ["Vegetable and legume dishes", "Mushroom dishes", "Herbal drinks"],
            ["Plant-based protein foods", "Leafy green dishes", "Herbal beverages"]
        ]
    },
    "indian south": {
        "breakfast": [
            ["Dosas", "Chutneys and relishes", "Coffee beverages"],
            ["Vegetable porridges", "Low-fat dairy products", "Tea beverages"],
            ["Egg-based dishes", "Spices", "Fresh fruits"],
            ["Plant-based protein foods", "Vegetable curries", "Infused water"],
            ["Yogurt dishes", "Seeds and nuts", "Herbal beverages"],
            ["Legumes", "Leafy green vegetables", "Tea beverages"],
            ["Fish dishes", "Vegetable side dishes", "Flavored water"]
        ],
        "lunch": [
            ["Vegetable curries", "Rice dishes", "Yogurt dishes"],
            ["Seafood dishes", "Vegetable side dishes", "Infused water"],
            ["Fish dishes", "Leafy green vegetables", "Infused water"],
            ["Mixed non-vegetarian dishes", "Vegetable salads", "Infused water"],
            ["Legumes", "Roasted vegetable dishes", "Tea beverages"],
            ["Plant-based protein foods", "Cooked vegetable dishes", "Infused water"],
            ["Vegetarian dishes", "Grains and pulses", "Tea beverages"]
        ],
        "dinner": [
            ["Soups and stews", "Vegetable dishes", "Leafy salads"],
            ["Vegetable and legume dishes", "Spices and seasonings", "Herbal beverages"],
            ["Vegetarian curries", "Legumes", "Herbal beverages"],
            ["Grilled seafood", "Steamed vegetable dishes", "Infused water"],
            ["Vegetarian main dishes", "Leafy salads", "Flavored water"],
            ["Mixed non-vegetarian dishes", "Vegetable stews", "Infused water"],
            ["Plant-based protein foods", "Leafy green dishes", "Herbal beverages"]
        ]
    },
    "italian": {
        "breakfast": [
            ["Egg-based dishes", "Roasted vegetable dishes", "Coffee beverages"],
            ["Yogurt bowls", "Fresh fruits", "Tea beverages"],
            ["Wholegrain loaves", "Cream cheese and soft cheese", "Seeds and nuts"],
            ["Vegetable and egg dishes", "Antipasti", "Infused water"],
            ["Plant-based protein foods", "Leafy salads", "Herbal beverages"],
            ["Porridges", "Low-fat dairy products", "Coffee beverages"],
            ["Fish dishes", "Cooked vegetable dishes", "Flavored water"]
        ],
        "lunch": [
            ["Salads", "Fish dishes", "Infused water"],
            ["Vegetable soups", "Mixed non-vegetarian dishes", "Leafy salads"],
            ["Seafood dishes", "Leafy salads", "Flavored water"],
            ["Legumes", "Roasted vegetable dishes", "Tea beverages"],
            ["Vegetable curries", "Pulses", "Flavored water"],
            ["Plant-based protein foods", "Cooked vegetable dishes", "Infused water"],
            ["Vegetarian dishes", "Grains and pulses", "Tea beverages"]
        ],
        "dinner": [
            ["Grilled seafood", "Sautéed vegetable dishes", "Antipasti"],
            ["Vegetable gratins", "Legumes", "Herbal beverages"],
            ["Vegetarian dishes", "Pulses", "Herbal beverages"],
            ["Stews and soups", "Roasted vegetable dishes", "Herbal beverages"],
            ["Baked fish dishes", "Vegetable side dishes", "Tea beverages"],
            ["Vegetable and legume dishes", "Mushroom dishes", "Herbal drinks"],
            ["Plant-based protein foods", "Leafy green dishes", "Herbal beverages"]
        ]
    },
    "japanese": {
        "breakfast": [
            ["Soups and broths", "Fish dishes", "Sea vegetables"],
            ["Egg-based dishes", "Pickled vegetables", "Tea beverages"],
            ["Plant-based protein foods", "Vegetable dishes", "Seaweed products"],
            ["Porridges", "Seeds and nuts", "Herbal beverages"],
            ["Vegetable and egg dishes", "Mushroom dishes", "Infused water"],
            ["Yogurt bowls", "Fresh fruits", "Tea beverages"],
            ["Seafood dishes", "Leafy green vegetables", "Flavored water"]
        ],
        "lunch": [
            ["Sushi and sashimi", "Leafy salads", "Tea beverages"],
            ["Fish and seafood dishes", "Vegetable stir-fries", "Soups"],
            ["Seafood bowls", "Leafy green vegetables", "Infused water"],
            ["Mixed non-vegetarian dishes", "Vegetable salads", "Infused water"],
            ["Legumes", "Roasted vegetable dishes", "Tea beverages"],
            ["Plant-based protein foods", "Cooked vegetable dishes", "Infused water"],
            ["Vegetarian dishes", "Grains and pulses", "Tea beverages"]
        ],
        "dinner": [
            ["Hot pot dishes", "Mushroom dishes", "Vegetable side dishes"],
            ["Grilled seafood", "Steamed vegetable dishes", "Infused water"],
            ["Vegetarian dishes", "Seaweed dishes", "Herbal drinks"],
            ["Stews and soups", "Roasted vegetable dishes", "Herbal beverages"],
            ["Vegetarian main dishes", "Leafy salads", "Flavored water"],
            ["Mixed non-vegetarian dishes", "Vegetable stews", "Infused water"],
            ["Plant-based protein foods", "Leafy green dishes", "Herbal beverages"]
        ]
    },
    "korean": {
        "breakfast": [
            ["Soups and broths", "Egg-based dishes", "Fermented foods"],
            ["Vegetable dishes", "Fish dishes", "Tea beverages"],
            ["Porridges", "Pickled vegetables", "Plant-based milk alternatives"],
            ["Plant-based protein foods", "Leafy green dishes", "Herbal beverages"],
            ["Vegetable and egg dishes", "Mushroom dishes", "Infused water"],
            ["Yogurt bowls", "Seeds and nuts", "Flavored water"],
            ["Seafood dishes", "Cooked vegetable dishes", "Tea beverages"]
        ],
        "lunch": [
            ["Mixed non-vegetarian dishes", "Leafy green dishes", "Fermented foods"],
            ["Vegetable stir-fries", "Soups", "Infused water"],
            ["Seafood dishes", "Leafy green vegetables", "Infused water"],
            ["Fish dishes", "Leafy salads", "Herbal beverages"],
            ["Legumes", "Roasted vegetable dishes", "Tea beverages"],
            ["Vegetable curries", "Pulses", "Flavored water"],
            ["Vegetarian dishes", "Grains and pulses", "Tea beverages"]
        ],
        "dinner": [
            ["Stews and soups", "Seafood dishes", "Vegetable side dishes"],
            ["Vegetable stews", "Plant-based protein foods", "Herbal drinks"],
            ["Vegetarian dishes", "Fermented foods", "Herbal beverages"],
            ["Grilled seafood", "Steamed vegetable dishes", "Infused water"],
            ["Vegetarian main dishes", "Leafy salads", "Flavored water"],
            ["Baked fish dishes", "Vegetable side dishes", "Tea beverages"],
            ["Mixed non-vegetarian dishes", "Vegetable stews", "Infused water"]
        ]
    },
    "latin american": {
        "breakfast": [
            ["Egg-based dishes", "Rice and beans dishes", "Coffee beverages"],
            ["Stuffed corn cakes", "Fresh fruits", "Tea beverages"],
            ["Plant-based protein foods", "Cooked vegetable dishes", "Low-fat dairy products"],
            ["Porridges", "Seeds and nuts", "Herbal beverages"],
            ["Vegetable and egg dishes", "Avocado dishes", "Infused water"],
            ["Yogurt bowls", "Fresh fruits", "Flavored water"],
            ["Fish dishes", "Vegetable salads", "Coffee beverages"]
        ],
        "lunch": [
            ["Mixed non-vegetarian dishes", "Vegetable salads", "Infused water"],
            ["Fish dishes", "Salads", "Legumes"],
            ["Seafood dishes", "Leafy salads", "Infused water"],
            ["Vegetable curries", "Pulses", "Flavored water"],
            ["Plant-based protein foods", "Cooked vegetable dishes", "Infused water"],
            ["Seafood dishes", "Vegetable soups", "Herbal drinks"],
            ["Vegetarian dishes", "Grains and pulses", "Tea beverages"]
        ],
        "dinner": [
            ["Stews and soups", "Roasted vegetable dishes", "Fresh fruits"],
            ["Seafood stews", "Leafy salads", "Herbal beverages"],
            ["Vegetarian dishes", "Pulses", "Herbal beverages"],
            ["Grilled seafood", "Steamed vegetable dishes", "Infused water"],
            ["Baked fish dishes", "Vegetable side dishes", "Tea beverages"],
            ["Vegetable and legume dishes", "Mushroom dishes", "Herbal drinks"],
            ["Plant-based protein foods", "Leafy green dishes", "Herbal beverages"]
        ]
    },
    "middle eastern": {
        "breakfast": [
            ["Egg-based dishes", "Dips and spreads", "Tea beverages"],
            ["Wholegrain loaves", "Olives", "Low-fat dairy products"],
            ["Yogurt dishes", "Nuts and seeds", "Fresh fruits"],
            ["Plant-based protein foods", "Vegetable side dishes", "Coffee beverages"],
            ["Vegetable and egg dishes", "Roasted vegetable dishes", "Infused water"],
            ["Porridges", "Seeds and nuts", "Herbal beverages"],
            ["Fish dishes", "Leafy salads", "Flavored water"]
        ],
        "lunch": [
            ["Salads", "Mixed non-vegetarian dishes", "Legumes"],
            ["Grilled seafood", "Vegetable dishes", "Infused water"],
            ["Seafood dishes", "Leafy green vegetables", "Flavored water"],
            ["Fish dishes", "Leafy salads", "Herbal beverages"],
            ["Vegetable curries", "Pulses", "Flavored water"],
            ["Plant-based protein foods", "Cooked vegetable dishes", "Infused water"],
            ["Vegetarian dishes", "Grains and pulses", "Tea beverages"]
        ],
        "dinner": [
            ["Stews", "Leafy salads", "Roasted dishes"],
            ["Vegetarian main dishes", "Eggplant dishes", "Herbal beverages"],
            ["Vegetarian dishes", "Grain and legume dishes", "Herbal beverages"],
            ["Stews and soups", "Roasted vegetable dishes", "Herbal beverages"],
            ["Baked fish dishes", "Vegetable side dishes", "Tea beverages"],
            ["Vegetable and legume dishes", "Mushroom dishes", "Herbal drinks"],
            ["Plant-based protein foods", "Leafy green dishes", "Herbal beverages"]
        ]
    },
    "north american": {
        "breakfast": [
            ["Porridges", "Low-fat dairy products", "seeds and nuts"],
            ["Egg-based dishes", "Sautéed vegetable dishes", "Bread"],
            ["Cereals", "Low-fat dairy products / dairy alternative", "Fresh fruits"],
            ["Plant-based protein foods", "Sautéed vegetable dishes", "Fresh fruits"],
            ["Smoothies and shakes", "Seeds and nuts", "Fresh fruits"],
            ["Yogurt bowls", "Seeds and nuts", "Fresh fruits"],
            ["Egg-based dishes", "Wholegrain loaves", "Low-fat dairy products"]
        ],
        "lunch": [
            ["Plant-based protein foods", "Grains", "Salads", "Cooked vegetable dishes"],
            ["Legumes", "Grains", "Leafy salads", "Cooked vegetables"],
            ["Plant-based protein foods", "Sautéed vegetable dishes", "Salads", "Soups"],
            ["Legumes", "Leafy salads", "Vegetable soup", "Grains"],
            ["Vegetarian dishes", "Salads", "Cooked vegetable dishes", "Grains"],
            ["Plant-based protein foods", "Leafy salads", "Roasted vegetable dishes", "Grains"],
            ["Pulses", "Salads", "Sautéed vegetable dishes", "Soups"]
        ],
        "dinner": [
            ["Plant-based protein foods", "Salads", "Cooked vegetable dishes", "Soups"],
            ["Legumes", "Leafy salads", "Roasted vegetable dishes", "Soups and broths"],
            ["Plant-based protein foods", "Salads", "Sautéed vegetable dishes", "Vegetable soups"],
            ["Vegetarian dishes", "Leafy salads", "Cooked vegetable dishes", "Soups"],
            ["Legumes", "Salads", "Steamed vegetable dishes", "Soups and broths"],
            ["Plant-based protein foods", "Leafy salads", "Roasted vegetable dishes", "Vegetable soups"],
            ["Pulses", "Salads", "Cooked vegetable dishes", "Soups"]
        ]
    },
    "indian north": {
        "breakfast": [
            ["Vegetable and egg dishes", "Flatbreads", "Tea beverages"],
            ["Porridges", "Seeds and nuts", "Low-fat dairy products"],
            ["Yogurt dishes", "Fresh fruits", "Spices"],
            ["Plant-based protein foods", "Vegetable side dishes", "Coffee beverages"],
            ["Egg-based dishes", "Cooked vegetable dishes", "Infused water"],
            ["Legumes", "Leafy green vegetables", "Herbal beverages"],
            ["Fish dishes", "Vegetable salads", "Flavored water"]
        ],
        "lunch": [
            ["Vegetable curries", "Legumes", "Leafy salads"],
            ["Mixed non-vegetarian dishes", "Grains and pulses", "Infused water"],
            ["Seafood dishes", "Leafy green vegetables", "Infused water"],
            ["Fish dishes", "Leafy salads", "Herbal beverages"],
            ["Legumes", "Roasted vegetable dishes", "Tea beverages"],
            ["Plant-based protein foods", "Cooked vegetable dishes", "Infused water"],
            ["Vegetarian dishes", "Grains and pulses", "Tea beverages"]
        ],
        "dinner": [
            ["Vegetable stews", "Roasted vegetable dishes", "Herbal beverages"],
            ["Fish dishes", "Vegetable side dishes", "Spices and peppers"],
            ["Vegetarian curries", "Legumes", "Herbal beverages"],
            ["Grilled seafood", "Steamed vegetable dishes", "Infused water"],
            ["Vegetarian main dishes", "Leafy salads", "Flavored water"],
            ["Vegetable and legume dishes", "Mushroom dishes", "Herbal drinks"],
            ["Plant-based protein foods", "Leafy green dishes", "Herbal beverages"]
        ]
    },
    "north indian": {
        "breakfast": [
            ["Vegetable and egg dishes", "Flatbreads", "Tea beverages"],
            ["Porridges", "Seeds and nuts", "Low-fat dairy products"],
            ["Yogurt dishes", "Fresh fruits", "Spices"],
            ["Plant-based protein foods", "Vegetable side dishes", "Coffee beverages"],
            ["Egg-based dishes", "Cooked vegetable dishes", "Infused water"],
            ["Legumes", "Leafy green vegetables", "Herbal beverages"],
            ["Fish dishes", "Vegetable salads", "Flavored water"]
        ],
        "lunch": [
            ["Vegetable curries", "Legumes", "Leafy salads"],
            ["Mixed non-vegetarian dishes", "Grains and pulses", "Infused water"],
            ["Seafood dishes", "Leafy green vegetables", "Infused water"],
            ["Fish dishes", "Leafy salads", "Herbal beverages"],
            ["Legumes", "Roasted vegetable dishes", "Tea beverages"],
            ["Plant-based protein foods", "Cooked vegetable dishes", "Infused water"],
            ["Vegetarian dishes", "Grains and pulses", "Tea beverages"]
        ],
        "dinner": [
            ["Vegetable stews", "Roasted vegetable dishes", "Herbal beverages"],
            ["Fish dishes", "Vegetable side dishes", "Spices and peppers"],
            ["Vegetarian curries", "Legumes", "Herbal beverages"],
            ["Grilled seafood", "Steamed vegetable dishes", "Infused water"],
            ["Vegetarian main dishes", "Leafy salads", "Flavored water"],
            ["Vegetable and legume dishes", "Mushroom dishes", "Herbal drinks"],
            ["Plant-based protein foods", "Leafy green dishes", "Herbal beverages"]
        ]
    },
    "south american": {
        "breakfast": [
            ["Egg-based dishes", "Rice and beans dishes", "Coffee beverages"],
            ["Stuffed corn cakes", "Fresh fruits", "Tea beverages"],
            ["Yogurt bowls", "Nuts and seeds", "Low-fat dairy products"],
            ["Porridges", "Seeds and nuts", "Herbal beverages"],
            ["Vegetable and egg dishes", "Avocado dishes", "Infused water"],
            ["Plant-based protein foods", "Cooked vegetable dishes", "Flavored water"],
            ["Fish dishes", "Vegetable salads", "Coffee beverages"]
        ],
        "lunch": [
            ["Mixed non-vegetarian dishes", "Vegetable salads", "Legumes"],
            ["Fish dishes", "Leafy salads", "Infused water"],
            ["Seafood dishes", "Leafy salads", "Infused water"],
            ["Vegetable curries", "Pulses", "Flavored water"],
            ["Plant-based protein foods", "Cooked vegetable dishes", "Infused water"],
            ["Seafood dishes", "Vegetable soups", "Herbal drinks"],
            ["Vegetarian dishes", "Grains and pulses", "Tea beverages"]
        ],
        "dinner": [
            ["Stews and soups", "Roasted vegetable dishes", "Herbal beverages"],
            ["Seafood bowls", "Vegetable side dishes", "Pulses"],
            ["Vegetarian dishes", "Grain and legume dishes", "Herbal beverages"],
            ["Grilled seafood", "Steamed vegetable dishes", "Infused water"],
            ["Vegetarian main dishes", "Leafy salads", "Flavored water"],
            ["Vegetable and legume dishes", "Mushroom dishes", "Herbal drinks"],
            ["Plant-based protein foods", "Leafy green dishes", "Herbal beverages"]
        ]
    },
    "spanish": {
        "breakfast": [
            ["Wholegrain loaves", "Dips and spreads", "Coffee beverages"],
            ["Egg-based dishes", "Fresh fruits", "Tea beverages"],
            ["Yogurt bowls", "Seeds and nuts", "Low-fat dairy products"],
            ["Porridges", "Fresh fruits", "Herbal beverages"],
            ["Vegetable and egg dishes", "Roasted vegetable dishes", "Infused water"],
            ["Plant-based protein foods", "Leafy salads", "Flavored water"],
            ["Fish dishes", "Vegetable side dishes", "Coffee beverages"]
        ],
        "lunch": [
            ["Fish and seafood dishes", "Salads", "Infused water"],
            ["Mixed non-vegetarian dishes", "Roasted vegetable dishes", "Vegetable soups"],
            ["Seafood dishes", "Leafy green vegetables", "Infused water"],
            ["Legumes", "Roasted vegetable dishes", "Tea beverages"],
            ["Vegetable curries", "Pulses", "Flavored water"],
            ["Plant-based protein foods", "Cooked vegetable dishes", "Infused water"],
            ["Vegetarian dishes", "Grains and pulses", "Tea beverages"]
        ],
        "dinner": [
            ["Grilled seafood", "Steamed vegetable dishes", "Herbal beverages"],
            ["Vegetarian main dishes", "Leafy salads", "Legumes"],
            ["Vegetarian dishes", "Pulses", "Herbal beverages"],
            ["Stews and soups", "Roasted vegetable dishes", "Herbal beverages"],
            ["Baked fish dishes", "Vegetable side dishes", "Tea beverages"],
            ["Mixed non-vegetarian dishes", "Vegetable stews", "Infused water"],
            ["Plant-based protein foods", "Leafy green dishes", "Herbal beverages"]
        ]
    },
    "thai": {
        "breakfast": [
            ["Soups and broths", "Egg-based dishes", "Tea beverages"],
            ["Vegetable stir-fries", "Plant-based protein foods", "Fresh fruits"],
            ["Porridges", "Seeds and nuts", "Herbal beverages"],
            ["Vegetable and egg dishes", "Mushroom dishes", "Infused water"],
            ["Yogurt bowls", "Fresh fruits", "Flavored water"],
            ["Fish dishes", "Leafy green vegetables", "Tea beverages"],
            ["Plant-based protein foods", "Cooked vegetable dishes", "Coffee beverages"]
        ],
        "lunch": [
            ["Vegetable curries", "Mixed non-vegetarian dishes", "Leafy salads"],
            ["Seafood dishes", "Vegetable side dishes", "Infused water"],
            ["Fish dishes", "Leafy salads", "Flavored water"],
            ["Legumes", "Roasted vegetable dishes", "Tea beverages"],
            ["Plant-based protein foods", "Cooked vegetable dishes", "Infused water"],
            ["Seafood dishes", "Vegetable soups", "Herbal drinks"],
            ["Vegetarian dishes", "Grains and pulses", "Tea beverages"]
        ],
        "dinner": [
            ["Soups and stews", "Fish dishes", "Steamed vegetable dishes"],
            ["Vegetarian curries", "Leafy green vegetables", "Tea beverages"],
            ["Vegetarian dishes", "Mushroom dishes", "Tea beverages"],
            ["Stews and soups", "Roasted vegetable dishes", "Herbal beverages"],
            ["Grilled seafood", "Steamed vegetable dishes", "Infused water"],
            ["Vegetable and legume dishes", "Mushroom dishes", "Herbal drinks"],
            ["Plant-based protein foods", "Leafy green dishes", "Herbal beverages"]
        ]
    },
    "turkish": {
        "breakfast": [
            ["Egg-based dishes", "Olives", "Tea beverages"],
            ["Wholegrain loaves", "Low-fat dairy products", "Fresh fruits"],
            ["Yogurt dishes", "Seeds and nuts", "Coffee beverages"],
            ["Porridges", "Seeds and nuts", "Herbal beverages"],
            ["Vegetable and egg dishes", "Roasted vegetable dishes", "Infused water"],
            ["Plant-based protein foods", "Leafy salads", "Flavored water"],
            ["Fish dishes", "Vegetable side dishes", "Tea beverages"]
        ],
        "lunch": [
            ["Mixed non-vegetarian dishes", "Salads", "Legumes"],
            ["Grilled seafood", "Vegetable side dishes", "Infused water"],
            ["Seafood dishes", "Leafy green vegetables", "Infused water"],
            ["Fish dishes", "Leafy salads", "Herbal beverages"],
            ["Vegetable curries", "Pulses", "Flavored water"],
            ["Plant-based protein foods", "Cooked vegetable dishes", "Infused water"],
            ["Vegetarian dishes", "Grains and pulses", "Tea beverages"]
        ],
        "dinner": [
            ["Stews", "Eggplant dishes", "Herbal beverages"],
            ["Vegetarian main dishes", "Leafy salads", "Vegetable soups"],
            ["Vegetarian dishes", "Pulses", "Herbal beverages"],
            ["Stews and soups", "Roasted vegetable dishes", "Herbal beverages"],
            ["Baked fish dishes", "Vegetable side dishes", "Tea beverages"],
            ["Mixed non-vegetarian dishes", "Vegetable stews", "Infused water"],
            ["Plant-based protein foods", "Leafy green dishes", "Herbal beverages"]
        ]
    },
    "vietnamese": {
        "breakfast": [
            ["Soups and broths", "Egg-based dishes", "Tea beverages"],
            ["Vegetable stir-fries", "Plant-based protein foods", "Fresh fruits"],
            ["Porridges", "Seeds and nuts", "Herbal drinks"],
            ["Vegetable and egg dishes", "Mushroom dishes", "Infused water"],
            ["Yogurt bowls", "Fresh fruits", "Flavored water"],
            ["Fish dishes", "Leafy green dishes", "Tea beverages"],
            ["Plant-based protein foods", "Cooked vegetable dishes", "Coffee beverages"]
        ],
        "lunch": [
            ["Salads", "Mixed non-vegetarian dishes", "Infused water"],
            ["Fish dishes", "Leafy green dishes", "Vegetable soups"],
            ["Seafood dishes", "Leafy salads", "Flavored water"],
            ["Legumes", "Roasted vegetable dishes", "Tea beverages"],
            ["Vegetable curries", "Pulses", "Flavored water"],
            ["Plant-based protein foods", "Cooked vegetable dishes", "Infused water"],
            ["Vegetarian dishes", "Grains and pulses", "Tea beverages"]
        ],
        "dinner": [
            ["Stews and soups", "Seafood dishes", "Vegetable side dishes"],
            ["Vegetable curries", "Leafy salads", "Tea beverages"],
            ["Vegetarian dishes", "Mushroom dishes", "Tea beverages"],
            ["Grilled seafood", "Steamed vegetable dishes", "Infused water"],
            ["Vegetarian main dishes", "Leafy salads", "Flavored water"],
            ["Baked fish dishes", "Vegetable side dishes", "Tea beverages"],
            ["Mixed non-vegetarian dishes", "Vegetable stews", "Infused water"]
        ]
    }
}

# Cuisine aliases - map variations to the canonical key in CUISINE_MEAL_COMBINATIONS
CUISINE_COMBINATION_ALIASES = {
    'south indian': 'indian south',
    'northern indian': 'indian north',
    'indian northern': 'indian north',
    'southern indian': 'indian south',
    'indian southern': 'indian south',
    'american': 'north american',
    'global': 'international',
    'mediterranean': 'european',
    'french': 'european',
    'german': 'european',
    'british': 'european',
    'greek': 'european',
    'portuguese': 'european',
    'scandinavian': 'european',
    'nordic': 'european',
    'eastern european': 'european',
    'russian': 'european',
    'polish': 'european',
    'ukrainian': 'european',
    'estonian': 'european',
    'mexican': 'latin american',
    'brazilian': 'south american',
    'peruvian': 'south american',
    'colombian': 'south american',
    'argentinian': 'south american',
    'chilean': 'south american',
    'caribbean': 'latin american',
    'east african': 'african',
    'west african': 'african',
    'north african': 'african',
    'nigerian': 'african',
    'ethiopian': 'african',
    'moroccan': 'african',
    'egyptian': 'african',
    'south african': 'african',
    'lebanese': 'middle eastern',
    'iranian': 'middle eastern',
    'persian': 'middle eastern',
    'iraqi': 'middle eastern',
    'yemeni': 'middle eastern',
    'malaysian': 'thai',
    'indonesian': 'thai',
    'laotian': 'thai',
    'cambodian': 'thai',
    'east asian': 'chinese',
    'taiwanese': 'chinese',
    'australian': 'international',
    'pacific islander': 'international',
}

# Sub-categories that are classified as nuts/seeds - NEVER allowed in dinner
NUT_SEED_SUB_CATEGORIES = {
    'seeds and nuts',
    'nuts and seeds', 
    'dried fruits and nuts',
    'fruit and nut snacks',
    'nut butters',
    'nut and seed spreads',
    'nut spreads',
    'nut-based beverages',
    'seeds',
}

# Keywords to identify nut/seed foods for dinner blocking
NUT_SEED_KEYWORDS_FOR_DINNER_BLOCK = [
    'almond', 'walnut', 'cashew', 'pistachio', 'pecan', 'hazelnut',
    'macadamia', 'brazil nut', 'pine nut', 'peanut', 'nut mix',
    'mixed nuts', 'trail mix', 'sunflower seed', 'pumpkin seed',
    'chia seed', 'flax seed', 'hemp seed', 'sesame seed',
    'seed mix', 'seeds', 'hemp heart', 'nuts', 'nut butter',
    'almond butter', 'cashew butter', 'peanut butter', 'tahini'
]


def get_cuisine_combination_key(cuisine: str) -> str:
    """Get the canonical cuisine key for meal combinations lookup."""
    if not cuisine:
        return 'international'
    
    cuisine_lower = cuisine.lower().strip()
    
    # Direct match
    if cuisine_lower in CUISINE_MEAL_COMBINATIONS:
        return cuisine_lower
    
    # Check aliases
    if cuisine_lower in CUISINE_COMBINATION_ALIASES:
        return CUISINE_COMBINATION_ALIASES[cuisine_lower]
    
    # Fallback to international
    return 'international'


def get_valid_sub_categories_for_meal(cuisine: str, meal_type: str, seed: int = None) -> list:
    """
    Get a valid combination of sub-categories for a given cuisine and meal type.
    
    Args:
        cuisine: User's cuisine preference (e.g., 'indian north', 'chinese')
        meal_type: One of 'breakfast', 'lunch', 'dinner'
        seed: Optional seed for reproducible random selection
    
    Returns:
        List of valid Sub_Food_category strings for this meal, or empty list if no rules found
    """
    import random as _random
    
    cuisine_key = get_cuisine_combination_key(cuisine)
    
    cuisine_combos = CUISINE_MEAL_COMBINATIONS.get(cuisine_key, {})
    if not cuisine_combos:
        logger.debug(f"No meal combinations defined for cuisine '{cuisine_key}', using 'international'")
        cuisine_combos = CUISINE_MEAL_COMBINATIONS.get('international', {})
    
    meal_key = meal_type.lower().strip()
    meal_combos = cuisine_combos.get(meal_key, [])
    
    if not meal_combos:
        logger.debug(f"No combinations for meal '{meal_key}' in cuisine '{cuisine_key}'")
        return []
    
    # Select a random combination (use seed for reproducibility if provided)
    if seed is not None:
        rng = _random.Random(seed)
        selected_combo = rng.choice(meal_combos)
    else:
        selected_combo = _random.choice(meal_combos)
    
    logger.info(f"  CUISINE COMBINATION: Selected {selected_combo} for {cuisine_key}/{meal_key}")
    return selected_combo


def is_food_in_sub_categories(food_macros: dict, valid_sub_categories: list) -> bool:
    """
    Check if a food item belongs to any of the valid sub-categories.
    Uses fuzzy matching to handle variations in sub-category naming.
    
    Args:
        food_macros: The macros dict for the food (from all_foods_macros)
        valid_sub_categories: List of valid Sub_Food_category strings
    
    Returns:
        True if the food matches any valid sub-category
    """
    if not valid_sub_categories:
        return True  # No restrictions if no categories defined
    
    food_sub_cat = food_macros.get('sub_food_category', '')
    if not food_sub_cat:
        return True  # Allow foods without sub-category (don't block them)
    
    food_sub_cat_lower = food_sub_cat.lower().strip()
    
    for valid_cat in valid_sub_categories:
        valid_cat_lower = valid_cat.lower().strip()
        
        # Exact match
        if food_sub_cat_lower == valid_cat_lower:
            return True
        
        # Partial/fuzzy match - check if key words overlap
        # e.g., "Vegetable stir-fries" should match "Vegetable stir-fries" 
        # "Leafy green vegetables" should match "Leafy green dishes"
        food_words = set(food_sub_cat_lower.replace('-', ' ').split())
        valid_words = set(valid_cat_lower.replace('-', ' ').split())
        
        # If significant word overlap (>50% of shorter set), consider it a match
        if food_words and valid_words:
            common_words = food_words & valid_words
            # Remove common filler words and generic suffixes that appear in most categories
            filler_words = {'and', 'with', 'the', 'a', 'an', 'of', 'in', 'for',
                          'dishes', 'foods', 'products', 'items', 'based', 'beverages',
                          'drinks', 'alternatives'}
            meaningful_common = common_words - filler_words
            meaningful_food = food_words - filler_words
            meaningful_valid = valid_words - filler_words
            
            if meaningful_common and meaningful_food and meaningful_valid:
                shorter_len = min(len(meaningful_food), len(meaningful_valid))
                if shorter_len > 0 and len(meaningful_common) / shorter_len >= 0.5:
                    return True
    
    return False


def is_nut_or_seed_food(food_name: str, food_macros: dict = None) -> bool:
    """
    Check if a food is a nut or seed item (for dinner blocking).
    Checks food name, sub-category, AND ingredients (grocery_items).
    
    Args:
        food_name: The food name
        food_macros: Optional macros dict for sub-category and ingredient check
    
    Returns:
        True if the food is a nut/seed item
    """
    food_lower = food_name.lower()
    
    # Check by keywords in food name
    if any(kw in food_lower for kw in NUT_SEED_KEYWORDS_FOR_DINNER_BLOCK):
        return True
    
    # Check by sub-category
    if food_macros:
        sub_cat = food_macros.get('sub_food_category', '').lower().strip()
        if sub_cat in NUT_SEED_SUB_CATEGORIES:
            return True
        
        # Check by ingredients (grocery_items) - catches hidden nuts/seeds like tahini
        grocery_items = food_macros.get('_grocery_items', '') or ''
        if grocery_items:
            grocery_lower = grocery_items.lower()
            # Only flag PRIMARY nut/seed ingredients (not plant milks or trace additions)
            NUT_SEED_PRIMARY_INGREDIENT_KEYWORDS = [
                'almonds', 'walnuts', 'cashews', 'pistachios', 'pecans', 'hazelnuts',
                'macadamia', 'pine nut', 'peanuts', 'tahini', 'nut butter',
                'sunflower seed', 'pumpkin seed', 'almond butter', 'cashew butter',
                'peanut butter',
            ]
            # Exclude plant milks (almond milk, cashew milk, etc.)
            EXCLUDE_PATTERNS = ['almond milk', 'cashew milk', 'hazelnut milk']
            cleaned_grocery = grocery_lower
            for pattern in EXCLUDE_PATTERNS:
                cleaned_grocery = cleaned_grocery.replace(pattern, '')
            
            for kw in NUT_SEED_PRIMARY_INGREDIENT_KEYWORDS:
                if kw in cleaned_grocery:
                    return True
    
    return False


# Sub_Food_categories that are "spreads/sauces" needing a companion food
# Max 1 per meal - these should NOT be standalone main items
SPREAD_SAUCE_SUB_CATEGORIES = {
    'dips and spreads',
    'sauces and condiments',
    'condiments and sauces',
    'sauces and marinades',
    'nut butters',
    'nut and seed spreads',
    'nut spreads',
    'fish spreads',
    'fruit spreads',
    'spreads and preserves',
    'chutneys and relishes',
    'condiments and relishes',
    'condiments and marinades',
    'marinades',
}


def is_spread_or_sauce_food(food_macros: dict) -> bool:
    """Check if a food is a spread/sauce/dip that needs a companion (bread/cracker/base)."""
    if not food_macros:
        return False
    sub_cat = food_macros.get('sub_food_category', '').lower().strip()
    return sub_cat in SPREAD_SAUCE_SUB_CATEGORIES
