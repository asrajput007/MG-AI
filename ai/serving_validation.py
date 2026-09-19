import logging
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

VEGETABLE_KEYWORDS = [
    'spinach', 'palak', 'lettuce', 'kale', 'cabbage', 'bok choy', 'collard', 'swiss chard',
    'arugula', 'watercress', 'romaine', 'microgreens',
    'broccoli', 'cauliflower', 'gobi', 'brussels sprouts', 'radish', 'turnip',
    'carrot', 'gajar', 'beet', 'beetroot', 'sweet potato', 'yam', 'potato', 'aloo',
    'pumpkin', 'kaddu', 'zucchini', 'cucumber', 'kheera', 'bottle gourd', 'lauki',
    'bitter gourd', 'karela', 'ridge gourd', 'tori', 'ash gourd',
    'tomato', 'tamatar', 'bell pepper', 'capsicum', 'shimla mirch', 'eggplant', 'baingan',
    'aubergine',
    'okra', 'bhindi', 'green beans', 'string beans', 'french beans', 'asparagus', 'celery',
    'onion', 'pyaz', 'scallion', 'leek', 'shallot',
    'mushroom', 'khumb',
    'vegetable', 'sabzi', 'salad', 'greens', 'mixed veg', 'stir fry', 'veggie'
]

FRUIT_KEYWORDS = [
    'apple', 'seb', 'banana', 'kela', 'orange', 'santra', 'mango', 'aam', 'guava', 'amrood',
    'papaya', 'papita', 'grape', 'angoor', 'watermelon', 'tarbuj', 'melon', 'kharbooja',
    'strawberry', 'blueberry', 'raspberry', 'blackberry', 'cranberry', 'berry', 'berries',
    'lemon', 'nimbu', 'lime', 'grapefruit', 'tangerine', 'mandarin',
    'pineapple', 'ananas', 'coconut', 'nariyal', 'avocado', 'kiwi', 'dragon fruit',
    'passion fruit', 'lychee', 'litchi',
    'peach', 'aadu', 'plum', 'alubukhara', 'apricot', 'khubani', 'cherry', 
    'pear', 'nashpati', 'pomegranate', 'anar', 'fig', 'anjeer', 'date', 'khajoor',
    'persimmon', 'jackfruit', 'kathal',
    'fruit salad', 'fruit bowl', 'fruit chaat'
]
FALSE_POSITIVE_KEYWORDS = [
    'juice', 'ras',
    'dried', 'canned', 'preserved', 'pickled', 'achar',    
    'powder', 'spice', 'seasoning', 'extract', 'essence', 'flavored', 'flavoured'
]


def is_vegetable_serving(food_name: str) -> bool:
    food_lower = food_name.lower()
    
    if any(fp in food_lower for fp in FALSE_POSITIVE_KEYWORDS):
        return False
    return any(veg in food_lower for veg in VEGETABLE_KEYWORDS)


def is_fruit_serving(food_name: str) -> bool:
 
    food_lower = food_name.lower()
    
    if any(fp in food_lower for fp in FALSE_POSITIVE_KEYWORDS):
        return False
    return any(fruit in food_lower for fruit in FRUIT_KEYWORDS)


def count_servings(meal_plan: Dict) -> Tuple[int, int]:
    veg_servings = 0
    fruit_servings = 0
    
    meals = meal_plan.get('meal_plan', [])
    
    for meal in meals:
        foods = meal.get('foods', [])
        
        for food in foods:
            food_name = food.get('name', food.get('Food_Name', ''))
            
            if is_vegetable_serving(food_name):
                veg_servings += 1
            elif is_fruit_serving(food_name):
                fruit_servings += 1
    
    return veg_servings, fruit_servings


def validate_minimum_servings(meal_plan: Dict, min_veg: int = 5, min_fruit: int = 3) -> Dict:

    veg_servings, fruit_servings = count_servings(meal_plan)
    
    veg_gap = max(0, min_veg - veg_servings)
    fruit_gap = max(0, min_fruit - fruit_servings)
    
    valid = (veg_servings >= min_veg) and (fruit_servings >= min_fruit)
    
    if valid:
        message = f" Serving requirements met: {veg_servings} vegetables (need {min_veg}), {fruit_servings} fruits (need {min_fruit})"
    else:
        gaps = []
        if veg_gap > 0:
            gaps.append(f"need {veg_gap} more vegetables")
        if fruit_gap > 0:
            gaps.append(f"need {fruit_gap} more fruits")
        message = f" Serving requirements not met: {veg_servings}/{min_veg} vegetables, {fruit_servings}/{min_fruit} fruits ({', '.join(gaps)})"
    
    return {
        'valid': valid,
        'veg_servings': veg_servings,
        'fruit_servings': fruit_servings,
        'veg_gap': veg_gap,
        'fruit_gap': fruit_gap,
        'message': message
    }


def get_serving_recommendations(veg_gap: int, fruit_gap: int) -> List[str]:
 
    recommendations = []
    
    veg_options = [
        "Fresh salad with lettuce, tomato, cucumber",
        "Steamed broccoli",
        "Roasted mixed vegetables",
        "Sautéed spinach (palak)",
        "Carrot and cucumber sticks",
        "Bell pepper strips",
        "Vegetable soup",
        "Stir-fried mixed vegetables"
    ]
    
    fruit_options = [
        "Apple (medium)",
        "Berries (1 cup mixed berries)",
        "Guava",
        "Pear",
        "Orange segments",
        "Papaya (small bowl)",
        "Watermelon (small bowl)",
        "Fruit salad (apple, guava, berries)"
    ]
    
    for i in range(min(veg_gap, len(veg_options))):
        recommendations.append(f" Add to snacks/meals: {veg_options[i]}")
    
    for i in range(min(fruit_gap, len(fruit_options))):
        recommendations.append(f" Add to snacks/meals: {fruit_options[i]}")
    
    return recommendations


def log_serving_analysis(meal_plan: Dict, min_veg: int = 5, min_fruit: int = 3):
 
    logger.info("=" * 80)
    logger.info(" SERVING ANALYSIS (Clinical Nutrition Standards)")
    logger.info("=" * 80)
    logger.info(f"Minimum Requirements: {min_veg} vegetables, {min_fruit} fruits (daily)")
    logger.info("")
    
    meals = meal_plan.get('meal_plan', [])
    total_veg = 0
    total_fruit = 0
    
    for meal in meals:
        meal_name = meal.get('meal_name', 'Unknown')
        foods = meal.get('foods', [])
        
        meal_veg = 0
        meal_fruit = 0
        veg_foods = []
        fruit_foods = []
        
        for food in foods:
            food_name = food.get('name', food.get('Food_Name', ''))
            
            if is_vegetable_serving(food_name):
                meal_veg += 1
                veg_foods.append(food_name)
            elif is_fruit_serving(food_name):
                meal_fruit += 1
                fruit_foods.append(food_name)
        
        total_veg += meal_veg
        total_fruit += meal_fruit
        
        logger.info(f"{meal_name}:")
        if veg_foods:
            logger.info(f"    Vegetables ({meal_veg}): {', '.join(veg_foods)}")
        if fruit_foods:
            logger.info(f"    Fruits ({meal_fruit}): {', '.join(fruit_foods)}")
        if not veg_foods and not fruit_foods:
            logger.info(f"    No vegetables or fruits")
        logger.info("")
    
    logger.info("=" * 80)
    logger.info(f" DAILY TOTALS: {total_veg} vegetables, {total_fruit} fruits")
    logger.info(f" TARGETS: {min_veg} vegetables, {min_fruit} fruits")
    
    veg_gap = max(0, min_veg - total_veg)
    fruit_gap = max(0, min_fruit - total_fruit)
    
    if veg_gap > 0:
        logger.warning(f" VEGETABLE GAP: Need {veg_gap} more servings")
    else:
        logger.info(f" VEGETABLES: Met requirement (+{total_veg - min_veg} extra)")
    
    if fruit_gap > 0:
        logger.warning(f" FRUIT GAP: Need {fruit_gap} more servings")
    else:
        logger.info(f" FRUITS: Met requirement (+{total_fruit - min_fruit} extra)")
    
    if veg_gap > 0 or fruit_gap > 0:
        logger.info("")
        logger.info("💡 RECOMMENDATIONS:")
        recommendations = get_serving_recommendations(veg_gap, fruit_gap)
        for rec in recommendations:
            logger.info(f"   {rec}")
    
    logger.info("=" * 80)
