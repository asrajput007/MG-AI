ALLERGEN_KEYWORDS = {
    'wheat': ['wheat', 'atta', 'maida', 'semolina', 'bulgur', 'couscous', 'farro', 'spelt', 
              'roti', 'chapati', 'paratha', 'naan', 'bread', 'pasta', 'noodles', 'seitan'],
    
    'tree_nuts': ['almond', 'cashew', 'walnut', 'pecan', 'brazil nut', 'hazelnut', 'macadamia',
                  'pistachio', 'pine nut', 'chestnut', 'badam', 'kaju'],
    
    'soy': ['soy', 'soya', 'tofu', 'tempeh', 'edamame', 'miso', 'soy sauce', 'soy milk',
            'textured vegetable protein', 'tvp'],
    
    'sesame': ['sesame', 'til', 'tahini', 'sesame oil', 'sesame seeds'],
    
    'shellfish': ['shrimp', 'prawn', 'crab', 'lobster', 'crayfish', 'clam', 'mussel', 'oyster',
                  'scallop', 'jhinga'],
    
    'corn': ['corn', 'maize', 'makki', 'polenta', 'cornmeal', 'corn flour', 'corn starch',
             'popcorn', 'hominy'],
    
    'fish': ['fish', 'machli', 'salmon', 'tuna', 'cod', 'tilapia', 'mackerel', 'sardine',
             'anchovy', 'herring', 'trout'],
    
    'legumes': ['lentil', 'dal', 'chickpea', 'chana', 'pea', 'matar', 'rajma', 'kidney bean',
                'black bean', 'pinto bean', 'navy bean', 'white bean', 'lima bean',
                'masoor', 'moong', 'urad', 'toor', 'arhar'],
    
    'eggs': ['egg', 'tamago', 'omelette', 'omelet', 'scrambled', 'fried egg', 'boiled egg',
             'poached egg', 'egg tart', 'egg salad', 'deviled egg', 'mayonnaise'],
    
    'peanuts': ['peanut', 'groundnut', 'moongphali', 'peanut butter'],
    
    'dairy': ['milk', 'doodh', 'cheese', 'paneer', 'yogurt', 'dahi', 'curd', 'butter', 'makhan',
              'ghee', 'cream', 'malai', 'ice cream', 'kheer', 'raita', 'lassi', 'whey', 'casein'],
    
    'lactose': ['milk', 'doodh', 'yogurt', 'dahi', 'curd', 'cheese', 'paneer', 'ice cream',
                'kheer', 'raita', 'lassi', 'cream', 'malai', 'whey'],  
    
    'gluten': ['wheat', 'roti', 'chapati', 'naan', 'paratha', 'bread', 'pasta', 'noodles',
               'barley', 'jau', 'rye', 'bulgur', 'couscous', 'farro', 'spelt', 'seitan', 
               'atta', 'maida', 'semolina']
}


DIETARY_RESTRICTION_KEYWORDS = {
    'sugar_free': ['sugar', 'shakkar', 'jaggery', 'gur', 'honey', 'madhu', 'syrup', 'molasses',
                   'candy', 'chocolate', 'cake', 'cookie', 'pastry', 'dessert', 'mithai', 
                   'sweet', 'kheer', 'ladoo', 'barfi', 'gulab jamun', 'jalebi'],
    
    'dairy_free': ALLERGEN_KEYWORDS['dairy'],
    
    'paleo': ['grain', 'rice', 'wheat', 'bread', 'pasta', 'legume', 'dal', 'bean', 'peanut',
              'dairy', 'processed', 'refined oil'],
    
    'lactose_free': ALLERGEN_KEYWORDS['lactose'],
    
    'fodmap': ['onion', 'garlic', 'wheat', 'rye', 'milk', 'yogurt', 'apple', 'mango', 'watermelon',
               'honey', 'high fructose', 'chickpea', 'lentil', 'cashew', 'pistachio'],
    
    'low_sodium': ['salt', 'namak', 'soy sauce', 'pickle', 'achar', 'papad', 'processed',
                   'canned', 'packaged', 'instant', 'chips', 'namkeen'],
    
    'no_red_meat': ['beef', 'lamb', 'mutton', 'goat', 'bakra', 'gosht', 'pork', 'suar',
                    'veal', 'venison', 'bison'],
    
    'low_fat': ['fried', 'deep fried', 'pakora', 'samosa', 'kachori', 'puri', 'bhatura',
                'butter', 'cream', 'ghee', 'coconut oil', 'palm oil', 'fatty', 'oily'],
    
    'gluten_free': ALLERGEN_KEYWORDS['gluten'],
    
    'low_carb': ['rice', 'chawal', 'bread', 'roti', 'pasta', 'noodles', 'potato', 'aloo',
                 'sweet potato', 'yam', 'tapioca', 'corn', 'makki', 'sugar', 'fruit juice'],
    
    'halal': ['pork', 'suar', 'bacon', 'ham', 'sausage', 'alcohol', 'wine', 'beer', 'rum'],
    
    'kosher': ['pork', 'suar', 'shellfish', 'shrimp', 'crab', 'lobster', 'mixing meat dairy']
}


DIETARY_PREFERENCE_KEYWORDS = {
    'vegetarian': ['meat', 'chicken', 'fish', 'seafood', 'egg', 'beef', 'lamb', 'pork', 
                   'mutton', 'goat', 'turkey', 'duck'],
    
    'vegan': ['meat', 'chicken', 'fish', 'seafood', 'egg', 'beef', 'lamb', 'pork',
              'dairy', 'milk', 'cheese', 'paneer', 'yogurt', 'butter', 'ghee', 'honey'],
    
    'pescatarian': ['meat', 'chicken', 'beef', 'lamb', 'pork', 'mutton', 'goat', 'turkey'],
    
    'halal': DIETARY_RESTRICTION_KEYWORDS['halal'],
    
    'no_pork': ['pork', 'suar', 'bacon', 'ham', 'sausage', 'pork chop', 'ribs'],
    
    'kosher': DIETARY_RESTRICTION_KEYWORDS['kosher'],
    
    'no_beef': ['beef', 'gai ka gosht', 'steak', 'burger', 'roast beef', 'beef curry'],
    
    'no_red_meat': ['beef', 'lamb', 'mutton', 'goat', 'bakra', 'gosht', 'pork', 'suar',
                    'veal', 'venison', 'bison'],
    
    'alcohol_free': ['alcohol', 'wine', 'beer', 'rum', 'whiskey', 'vodka', 'liquor',
                     'brandy', 'champagne', 'cocktail']
}


DIGESTIVE_ISSUE_FOODS = {
    'acid_reflux': {
        'avoid': ['spicy', 'mirch', 'chili', 'tomato', 'tamatar', 'citrus', 'orange', 'lemon',
                  'coffee', 'chocolate', 'fried', 'fatty', 'onion', 'garlic', 'mint']
    },
    
    'ibs': {
        'avoid': ['wheat', 'dairy', 'onion', 'garlic', 'beans', 'cabbage', 'broccoli',
                  'cauliflower', 'spicy', 'fried', 'caffeine', 'alcohol']
    },
    
    'constipation': {
        'recommend': ['fiber', 'whole grain', 'fruits', 'vegetables', 'prunes', 'flaxseed'],
        'avoid': ['processed', 'white bread', 'white rice', 'banana', 'cheese']
    },
    
    'diarrhea': {
        'recommend': ['rice', 'banana', 'toast', 'apple sauce', 'yogurt'],
        'avoid': ['dairy', 'fried', 'spicy', 'high fiber', 'caffeine', 'alcohol']
    },
    
    'bloating': {
        'avoid': ['beans', 'lentils', 'cabbage', 'broccoli', 'cauliflower', 'onion',
                  'carbonated', 'wheat', 'dairy']
    },
    
    'excessive_gas': {
        'avoid': ['beans', 'lentils', 'dal', 'cabbage', 'broccoli', 'cauliflower',
                  'brussels sprouts', 'onion', 'carbonated drinks', 'chewing gum']
    },
    
    'stomach_pain': {
        'avoid': ['spicy', 'fried', 'acidic', 'caffeine', 'alcohol', 'processed']
    },
    
    'nausea': {
        'recommend': ['ginger', 'mint', 'bland foods', 'crackers', 'toast'],
        'avoid': ['fried', 'greasy', 'spicy', 'strong odors']
    }
}


SYMPTOM_AGGRAVATING_FOODS = {
    'dairy_products': ALLERGEN_KEYWORDS['dairy'],
    
    'high_fat_foods': ['fried', 'deep fried', 'pakora', 'samosa', 'butter', 'ghee', 'cream',
                       'fatty meat', 'bacon', 'sausage', 'cheese', 'coconut oil', 'palm oil'],
    
    'gluten_foods': ALLERGEN_KEYWORDS['gluten'],
    
    'high_fiber_foods': ['bran', 'whole wheat', 'beans', 'lentils', 'popcorn', 'seeds',
                         'raw vegetables', 'prunes', 'dried fruit'],
    
    'fried_greasy_foods': ['fried', 'deep fried', 'pakora', 'samosa', 'puri', 'bhatura',
                           'french fries', 'chips', 'fried chicken'],
    
    'raw_vegetables': ['raw salad', 'raw cabbage', 'raw broccoli', 'raw cauliflower',
                       'raw onion', 'raw pepper'],
    
    'spicy_foods': ['spicy', 'hot', 'chili', 'mirch', 'pepper', 'cayenne', 'jalapeno',
                    'vindaloo', 'hot sauce'],
    
    'acidic_fruits': ['orange', 'lemon', 'lime', 'grapefruit', 'pineapple', 'tomato',
                      'tamatar', 'berries'],
    
    'caffeinated_beverages': ['coffee', 'tea', 'chai', 'green tea', 'black tea', 'cola',
                              'energy drink', 'caffeine'],
    
    'chocolate': ['chocolate', 'cocoa', 'hot chocolate', 'chocolate cake', 'chocolate bar'],
    
    'carbonated_drinks': ['soda', 'cola', 'sprite', 'carbonated', 'fizzy', 'sparkling water'],
    
    'garlic_onions': ['garlic', 'lehsun', 'onion', 'pyaz', 'spring onion', 'leek', 'shallot'],
    
    'alcohol': ['alcohol', 'wine', 'beer', 'rum', 'whiskey', 'vodka', 'liquor'],
    
    'processed_packaged_foods': ['processed', 'packaged', 'instant', 'canned', 'preserved',
                                  'ready-to-eat', 'fast food', 'junk food']
}


def check_food_against_allergens(food_name: str, allergens: list) -> bool:

    if not allergens:
        return True
    
    food_lower = food_name.lower()
    
    for allergen in allergens:
        allergen_key = allergen.lower().replace(' ', '_').replace('-', '_')
        keywords = ALLERGEN_KEYWORDS.get(allergen_key, [])
        
        if any(keyword in food_lower for keyword in keywords):
            return False
    
    return True


def check_food_against_restrictions(food_name: str, restrictions: list) -> bool:

    if not restrictions:
        return True
    
    food_lower = food_name.lower()
    
    for restriction in restrictions:
        restriction_key = restriction.lower().replace(' ', '_').replace('-', '_')
        keywords = DIETARY_RESTRICTION_KEYWORDS.get(restriction_key, [])
        
        if any(keyword in food_lower for keyword in keywords):
            return False
    
    return True


def check_food_against_preferences(food_name: str, preference: str) -> bool:

    if not preference or preference.lower() in ['none', 'prefer not to say', 'non vegetarian', 'non_vegetarian', 'omnivore', '']:
        return True
    
    food_lower = food_name.lower()
    preference_key = preference.lower().replace(' ', '_').replace('-', '_')
    keywords = DIETARY_PREFERENCE_KEYWORDS.get(preference_key, [])
    
    if any(keyword in food_lower for keyword in keywords):
        return False
    
    return True


def check_food_against_digestive_issues(food_name: str, digestive_issues: list) -> bool:

    if not digestive_issues:
        return True
    
    food_lower = food_name.lower()
    
    for issue in digestive_issues:
        issue_key = issue.lower().replace(' ', '_').replace('-', '_')
        issue_data = DIGESTIVE_ISSUE_FOODS.get(issue_key, {})
        avoid_keywords = issue_data.get('avoid', [])
        
        if any(keyword in food_lower for keyword in avoid_keywords):
            return False
    
    return True


def check_food_against_symptom_foods(food_name: str, symptom_foods: list) -> bool:

    if not symptom_foods:
        return True
    
    food_lower = food_name.lower()
    
    for symptom_category in symptom_foods:
        category_key = symptom_category.lower().replace(' ', '_').replace('-', '_')
        keywords = SYMPTOM_AGGRAVATING_FOODS.get(category_key, [])
        
        if any(keyword in food_lower for keyword in keywords):
            return False
    
    return True


def apply_comprehensive_filtering(food_name: str, user_profile: dict) -> bool:

    allergens = user_profile.get('allergies', user_profile.get('food_allergies', []))
    restrictions = user_profile.get('dietary_restrictions', user_profile.get('food_restrictions', []))
    preference = user_profile.get('dietary_preference', '')
    digestive_issues = user_profile.get('digestive_issues', [])
    symptom_foods = user_profile.get('symptom_aggravating_foods', [])
    
    if isinstance(allergens, str):
        allergens = [allergens] if allergens and allergens.lower() not in ['none', 'prefer not to say', ''] else []
    else:
        allergens = [a for a in allergens if a and a.lower() not in ['none', 'prefer not to say', '']]
    
    if isinstance(restrictions, str):
        restrictions = [restrictions] if restrictions and restrictions.lower() not in ['none', 'prefer not to say', ''] else []
    else:
        restrictions = [r for r in restrictions if r and r.lower() not in ['none', 'prefer not to say', '']]
    
    if isinstance(digestive_issues, str):
        digestive_issues = [digestive_issues] if digestive_issues and digestive_issues.lower() not in ['none', 'prefer not to say', ''] else []
    else:
        digestive_issues = [d for d in digestive_issues if d and d.lower() not in ['none', 'prefer not to say', '']]
    
    if isinstance(symptom_foods, str):
        symptom_foods = [symptom_foods] if symptom_foods and symptom_foods.lower() not in ['none', 'prefer not to say', ''] else []
    else:
        symptom_foods = [s for s in symptom_foods if s and s.lower() not in ['none', 'prefer not to say', '']]
    
    if not check_food_against_allergens(food_name, allergens):
        return False
    
    if not check_food_against_restrictions(food_name, restrictions):
        return False
    
    if not check_food_against_preferences(food_name, preference):
        return False
    
    if not check_food_against_digestive_issues(food_name, digestive_issues):
        return False
    
    if not check_food_against_symptom_foods(food_name, symptom_foods):
        return False
    
    return True
