FOOD_GROUPS = {
    'starch_base': {
        'description': 'Primary starch/grain base for breakfast',
        'examples': ['oats', 'bread', 'paratha', 'idli', 'dosa', 'upma', 'poha', 'cereal']
    },
    'protein_source': {
        'description': 'Protein-rich breakfast items',
        'examples': ['eggs', 'paneer', 'dal', 'tofu', 'greek yogurt', 'protein powder', 'cottage cheese']
    },
    'fruit': {
        'description': 'Fresh or dried fruits',
        'examples': ['banana', 'apple', 'berries', 'mango', 'papaya', 'orange', 'grapes']
    },
    'dairy_or_alt': {
        'description': 'Dairy or dairy alternatives',
        'examples': ['milk', 'yogurt', 'almond milk', 'soy milk', 'oat milk', 'coconut milk']
    },
    'fat_source': {
        'description': 'Healthy fats',
        'examples': ['nuts', 'seeds', 'nut butter', 'ghee', 'olive oil', 'avocado']
    },
    'condiment_side': {
        'description': 'Breakfast condiments and sides',
        'examples': ['chutney', 'pickle', 'jam', 'honey', 'sambar']
    },
    'beverage': {
        'description': 'Breakfast beverages',
        'examples': ['coffee', 'tea', 'juice', 'smoothie', 'lassi']
    },
    
    'nut_seed': {
        'description': 'Nuts and seeds',
        'examples': ['almonds', 'walnuts', 'cashews', 'pumpkin seeds', 'sunflower seeds', 'mixed nuts']
    },
    'hydration': {
        'description': 'Hydrating beverages',
        'examples': ['water', 'coconut water', 'buttermilk', 'green tea', 'herbal tea']
    },
    'savory_light': {
        'description': 'Light savory snacks',
        'examples': ['roasted chickpeas', 'makhana', 'vegetable sticks', 'hummus', 'crackers']
    },
    'protein_snack': {
        'description': 'Protein-focused snacks',
        'examples': ['boiled eggs', 'paneer cubes', 'greek yogurt', 'protein bar', 'roasted dal']
    },
    'fruit_snack': {
        'description': 'Fruit-based snacks',
        'examples': ['fresh fruit', 'fruit salad', 'dried fruit', 'fruit chaat']
    },
    'energy_source': {
        'description': 'Quick energy snacks',
        'examples': ['dates', 'banana', 'energy bar', 'granola bar', 'trail mix']
    },
    'warm_beverage': {
        'description': 'Warm beverages for evening',
        'examples': ['green tea', 'herbal tea', 'turmeric milk', 'ginger tea', 'chamomile tea']
    },
    
    'grain_base': {
        'description': 'Primary grain/starch base for lunch',
        'examples': ['rice', 'roti', 'quinoa', 'millet', 'pasta', 'noodles', 'pulao', 'biryani']
    },
    'protein_main': {
        'description': 'Main protein dish for lunch',
        'examples': ['dal', 'chicken curry', 'fish curry', 'paneer dish', 'rajma', 'chole', 'egg curry']
    },
    'primary_vegetable': {
        'description': 'Vegetable side dish',
        'examples': ['sabzi', 'stir-fry', 'vegetable curry', 'palak', 'bhindi', 'baingan']
    },
    'soup_or_broth': {
        'description': 'Liquid base or soup',
        'examples': ['rasam', 'sambar', 'dal tadka', 'soup', 'broth', 'kadhi']
    },
    'condiment': {
        'description': 'Accompaniments and condiments',
        'examples': ['raita', 'chutney', 'pickle', 'papad', 'salad']
    },
    
    'light_grain': {
        'description': 'Lighter grains for dinner',
        'examples': ['brown rice', 'millet', 'quinoa', 'buckwheat', 'roti', 'phulka']
    },
    'lean_protein': {
        'description': 'Lean protein for dinner',
        'examples': ['grilled chicken', 'fish', 'dal', 'tofu', 'egg whites', 'paneer']
    },
    'cooked_vegetable': {
        'description': 'Cooked vegetable dishes',
        'examples': ['steamed vegetables', 'roasted vegetables', 'sabzi', 'stir-fry']
    },
    'fermented_side': {
        'description': 'Fermented foods for digestion',
        'examples': ['curd', 'raita', 'buttermilk', 'fermented rice', 'pickles']
    }
}


UNIVERSAL_SLOTS = {
    "breakfast": [
        "grain_base", "light_grain",     
        "protein_source", "lean_protein", "protein_shake", 
        "dairy_or_alt",       
        "nut_seed",                    
        "fruit",                          
        "warm_beverage", "beverage"    
    ],
    
    "morning_snack": [
        "fruit_snack", "fruit",          
        "protein_snack",                
        "nut_seed",                      
        "hydration",                
        "beverage"                       
    ],
    
    "lunch": [
        "starch_base", "grain_base",    
        "protein_main",                 
        "primary_vegetable",            
        "cooked_vegetable",             
        "fat_source",               
        "condiment_side",             
        "fermented_side",              
        "hydration"                     
    ],
    
    "evening_snack": [
        "savory_light",              
        "protein_snack",                
        "nut_seed",                   
        "warm_beverage", "beverage"    
    ],
    
    "dinner": [
        "protein_main",                
        "primary_vegetable",            
        "cooked_vegetable",              
        "light_grain", "starch_base",  
        "fat_source",              
        "soup_or_broth"               
    ]
}




MEAL_FOOD_GROUPS = {
    'breakfast': [
        'starch_base',
        'protein_source',
        'fruit',
        'fat_source',
        'dairy_or_alt',
        'condiment_side',
        'beverage'
    ],
    'morning_snack': [
        'fruit',
        'nut_seed',
        'dairy_or_alt',
        'hydration'
    ],
    'lunch': [
        'grain_base',
        'protein_main',
        'primary_vegetable',
        'soup_or_broth',
        'condiment',
        'fat_source',
        'hydration'
    ],
    'evening_snack': [
        'savory_light',
        'protein_snack',
        'fruit_snack',
        'nut_seed',
        'energy_source',
        'warm_beverage',
        'hydration'
    ],
    'dinner': [
        'light_grain',
        'lean_protein',
        'cooked_vegetable',
        'soup_or_broth',
        'fermented_side',
        'fat_source',
        'hydration'
    ]
}




def get_food_group_info(group_name: str) -> dict:
    
    return FOOD_GROUPS.get(group_name)


def get_meal_food_groups(meal_type: str) -> list:
   
    return MEAL_FOOD_GROUPS.get(meal_type.lower(), [])


def validate_food_group(group_name: str) -> bool:
   
    return group_name in FOOD_GROUPS


def get_all_food_groups() -> list:
    
    return list(FOOD_GROUPS.keys())


def suggest_food_groups(food_name: str) -> list:
    
    food_lower = food_name.lower()
    suggestions = []
    
    for group_name, info in FOOD_GROUPS.items():
        examples = info.get('examples', [])
        
        for example in examples:
            if example.lower() in food_lower or food_lower in example.lower():
                suggestions.append(group_name)
                break
    
    return suggestions


ANNOTATION_GUIDE = """
Food Group Annotation Guide
===========================

How to annotate your food database with food groups:

1. Single Group Foods:
   - "Banana" -> "fruit"
   - "Almonds" -> "nut_seed"
   - "Brown Rice" -> "grain_base"

2. Multiple Group Foods (use | delimiter):
   - "Banana Smoothie" -> "fruit|beverage|dairy_or_alt"
   - "Egg Toast" -> "protein_source|starch_base"
   - "Fruit Yogurt" -> "fruit|dairy_or_alt"

3. Context-Specific Groups:
   Some foods can belong to different groups based on meal context:
   - "Paneer" -> "protein_source" (breakfast) or "protein_main" (lunch/dinner)
   - "Milk" -> "dairy_or_alt" (snack) or "beverage" (breakfast)

4. Multi-Purpose Foods:
   Foods that work in multiple meal types should list all applicable groups:
   - "Greek Yogurt" -> "dairy_or_alt|protein_source|protein_snack"
   - "Quinoa" -> "grain_base|light_grain|starch_base"

5. Database Field Format:
   Add a 'food_group' or 'Food_Group' field to your database:
   
   CSV:
   food_id,common_name,food_group
   1,Banana,fruit
   2,Egg Toast,protein_source|starch_base
   3,Greek Yogurt,dairy_or_alt|protein_source
   
   JSON:
   {
     "food_id": 1,
     "common_name": "Banana",
     "food_group": "fruit"
   }

Usage in Meal Generation:
- The system will prefer foods that fulfill unfulfilled food groups
- Each meal type targets specific food groups (see MEAL_FOOD_GROUPS)
- Foods are scored higher if they introduce new, needed groups
"""


if __name__ == "__main__":
    print("=== Food Group Configuration ===\n")
    
    print("Available Food Groups:")
    for group in get_all_food_groups():
        info = get_food_group_info(group)
        print(f"  - {group}: {info['description']}")
    
    print("\n=== Meal Type Food Groups ===\n")
    for meal_type in ['breakfast', 'morning_snack', 'lunch', 'evening_snack', 'dinner']:
        groups = get_meal_food_groups(meal_type)
        print(f"{meal_type.title()}: {', '.join(groups)}")
    
    print("\n=== Food Group Suggestions ===\n")
    test_foods = ['banana', 'chicken curry', 'greek yogurt', 'brown rice', 'almonds']
    for food in test_foods:
        suggestions = suggest_food_groups(food)
        print(f"{food}: {', '.join(suggestions) if suggestions else 'No suggestions'}")
    
    print("\n" + ANNOTATION_GUIDE)
