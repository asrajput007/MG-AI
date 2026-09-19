import logging
from typing import Dict, List, Tuple, Optional

logger = logging.getLogger(__name__)


class IntelligentMealComposer:
    MEAL_VOLUME_CAPS = {
        'breakfast': {
            'total_solid_oz': 16.0, 
            'total_liquid_oz': 20.0, 
            'total_vegetable_oz': 8.0, 
            'max_items': 4
        },
        'lunch': {
            'total_solid_oz': 28.0, 
            'total_liquid_oz': 16.0, 
            'total_vegetable_oz': 20.0, 
            'max_items': 5
        },
        'dinner': {
            'total_solid_oz': 28.0,  
            'total_liquid_oz': 16.0,  
            'total_vegetable_oz': 20.0, 
            'max_items': 5
        },
        'snack': {
            'total_solid_oz': 8.0, 
            'total_liquid_oz': 12.0, 
            'total_vegetable_oz': 4.0, 
            'max_items': 3
        }
    }
    
    
    @staticmethod
    def get_calorie_density_tier(calories_per_100g: float) -> str:
        """Classify food by calorie density"""
        if calories_per_100g < 30:
            return 'ultra_low'  
        elif calories_per_100g < 60:
            return 'very_low'  
        elif calories_per_100g < 100:
            return 'low'  
        elif calories_per_100g < 200:
            return 'medium'  
        elif calories_per_100g < 400:
            return 'high'  
        else:
            return 'very_high'  
    
    DENSITY_MAX_PORTIONS = {
        'ultra_low': 8.0,  
        'very_low': 10.0,  
        'low': 12.0, 
        'medium': 10.0,  
        'high': 4.0,  
        'very_high': 1.5 
    }
    

    
    FLAVOR_INCOMPATIBILITY_MAP = {
        'sweet': ['umami', 'savory', 'bitter', 'fermented'],
        'fruity': ['umami', 'fermented', 'spicy'],
        'chocolate': ['fishy', 'fermented', 'spicy'],
        
        'fishy': ['dairy', 'sweet', 'chocolate', 'fruity'],
        
        'fermented': ['sweet', 'fruity', 'chocolate'],

        'acidic': ['fishy', 'mild_protein']
    }
    
    FOOD_FLAVOR_PROFILES = {
'strawberry': 'fruity',
        'berry': 'fruity',
        'banana': 'fruity',
        'mango': 'fruity',
        'chocolate': 'chocolate',
        'smoothie': 'sweet',
        'milkshake': 'sweet',
        
        'eggplant': 'umami',
        'mushroom': 'umami',
        'tomato': 'umami',
        'spinach': 'mild',
        'broccoli': 'mild',
        'cauliflower': 'mild',
        'onion': 'savory',
        'garlic': 'savory',
        
        'fish': 'fishy',
        'salmon': 'fishy',
        'tuna': 'fishy',
        'haddock': 'fishy',
        'chicken': 'mild_protein',
        'turkey': 'mild_protein',
        'egg': 'mild_protein',
        
        'pickle': 'acidic',
        'sauerkraut': 'fermented',
        'kimchi': 'fermented',
        'yogurt': 'dairy',
        'cheese': 'dairy',
        
        'chili': 'spicy',
        'pepper': 'spicy',
        'hot': 'spicy'
    }
    
    @classmethod
    def check_flavor_compatibility(cls, food_items: List[Dict]) -> Tuple[bool, str]:
        
        meal_flavors = []
        for item in food_items:
            food_name = item.get('name', '').lower()
            matched_flavor = None
            
            for keyword, flavor in cls.FOOD_FLAVOR_PROFILES.items():
                if keyword in food_name:
                    matched_flavor = flavor
                    break
            
            if matched_flavor:
                meal_flavors.append({
                    'food': item.get('name'),
                    'flavor': matched_flavor
                })
        
        for i, item1 in enumerate(meal_flavors):
            for item2 in meal_flavors[i+1:]:
                flavor1 = item1['flavor']
                flavor2 = item2['flavor']
                
                if flavor2 in cls.FLAVOR_INCOMPATIBILITY_MAP.get(flavor1, []):
                    return (False, 
                           f" FLAVOR CLASH: '{item1['food']}' ({flavor1}) + '{item2['food']}' ({flavor2}) "
                           f"are incompatible. These flavors don't pair well.")
                
                if flavor1 in cls.FLAVOR_INCOMPATIBILITY_MAP.get(flavor2, []):
                    return (False,
                           f" FLAVOR CLASH: '{item2['food']}' ({flavor2}) + '{item1['food']}' ({flavor1}) "
                           f"are incompatible. These flavors don't pair well.")
        
        return (True, " Flavor pairing acceptable")
    

    
    @classmethod
    def validate_vegetable_volume(cls, meal_items: List[Dict], meal_name: str) -> Tuple[bool, str, List[Dict]]:

        
        meal_type = 'snack' if 'snack' in meal_name.lower() else meal_name.lower()
        if meal_type not in cls.MEAL_VOLUME_CAPS:
            meal_type = 'lunch' 
        
        max_veg_oz = cls.MEAL_VOLUME_CAPS[meal_type]['total_vegetable_oz']
        
        vegetable_keywords = [
            'vegetable', 'cooked_vegetable', 'primary_vegetable', 
            'fibrous_veggies', 'leafy', 'green', 'salad'
        ]
        
        vegetables = []
        total_veg_oz = 0.0
        
        for item in meal_items:
            food_groups = item.get('food_groups', []) or item.get('categories', [])
            is_vegetable = any(veg_kw in group for veg_kw in vegetable_keywords for group in food_groups)
            
            if is_vegetable:
                portion_g = item.get('nutrients', {}).get('Portion Weight', 0) or item.get('portion_g', 0)
                portion_oz = portion_g / 28.35
                vegetables.append({
                    'index': meal_items.index(item),
                    'name': item.get('name', 'Unknown'),
                    'portion_oz': portion_oz,
                    'portion_g': portion_g,
                    'calories': item.get('nutrients', {}).get('Calories', 0)
                })
                total_veg_oz += portion_oz
        
        if total_veg_oz <= max_veg_oz:
            return (True, f" Vegetable volume OK: {total_veg_oz:.1f} oz (max: {max_veg_oz:.1f} oz)", meal_items)
        
        logger.warning(f"   VEGETABLE OVERLOAD: {meal_name} has {total_veg_oz:.1f} oz vegetables (max: {max_veg_oz:.1f} oz)")
        logger.warning(f"      Vegetables: {', '.join([v['name'] for v in vegetables])}")
        
        if len(vegetables) >= 3:
            vegetables_sorted = sorted(vegetables, key=lambda x: x['calories'])
            removed_veg = vegetables_sorted[0]
            
            logger.warning(f"       REMOVING: '{removed_veg['name']}' (lowest calorie vegetable)")
            meal_items.pop(removed_veg['index'])
            
            total_veg_oz = sum(v['portion_oz'] for v in vegetables[1:])
            vegetables = vegetables[1:]
        
        if total_veg_oz > max_veg_oz:
            scale_factor = max_veg_oz / total_veg_oz
            logger.warning(f"       SCALING vegetables by {scale_factor:.2f}x to meet {max_veg_oz:.1f} oz cap")
            
            for veg in vegetables:
                item = meal_items[veg['index']]
                old_portion = item.get('portion_g', 100)
                new_portion = old_portion * scale_factor
                
                item['portion_g'] = round(new_portion, 1)
                nutrients = item.get('nutrients', {})
                for key in ['Calories', 'Protein', 'Carbohydrates', 'Fat', 'Fiber', 'Sodium', 'Sugar', 'Cholesterol', 'Iodine', 'Portion Weight', 'Potassium']:
                    if key in nutrients:
                        nutrients[key] = round(nutrients[key] * scale_factor, 1)
        
        return (True, f" FIXED: Reduced vegetables to {max_veg_oz:.1f} oz", meal_items)

    
    @classmethod
    def adjust_portions_by_density(cls, meal_items: List[Dict]) -> List[Dict]:

        
        for item in meal_items:
            portion_g = item.get('portion_g', 100) or item.get('nutrients', {}).get('Portion Weight', 100)
            calories = item.get('nutrients', {}).get('Calories', 0)
            
            if portion_g == 0 or calories == 0:
                continue
            
            cal_per_100g = (calories / portion_g) * 100 if portion_g > 0 else 0
            density_tier = cls.get_calorie_density_tier(cal_per_100g)
            
            max_oz = cls.DENSITY_MAX_PORTIONS.get(density_tier, 10.0)
            max_g = max_oz * 28.35
            
            food_groups = item.get('food_groups', []) or item.get('categories', []) or []
            food_groups_lower = [g.lower() for g in food_groups if g]
            is_nut_seed = 'nut_seed' in food_groups_lower
            
            if not is_nut_seed:
                fat_g = item.get('nutrients', {}).get('Fat', 0)
                fat_per_100g = (fat_g / portion_g) * 100 if portion_g > 0 else 0
                is_nut_seed = cal_per_100g > 450 and fat_per_100g > 35
            
            if is_nut_seed:
                MAX_NUT_SEED_OZ = 1.5
                MAX_NUT_SEED_G = MAX_NUT_SEED_OZ * 28.35 
                
                if portion_g > MAX_NUT_SEED_G:
                    cap_factor = MAX_NUT_SEED_G / portion_g
                    logger.warning(f"       NUT/SEED HARD CAP: '{item.get('name', 'Unknown')[:40]}' "
                                  f"is {portion_g:.0f}g ({portion_g/28.35:.1f} oz) → CAPPING to {MAX_NUT_SEED_OZ} oz ({MAX_NUT_SEED_G:.0f}g)")
                    logger.warning(f"         Food groups: {food_groups_lower}")
                    
                    item['portion_g'] = round(MAX_NUT_SEED_G, 1)
                    nutrients = item.get('nutrients', {})
                    for key in ['Calories', 'Protein', 'Carbohydrates', 'Fat', 'Fiber', 'Sodium', 'Sugar', 'Cholesterol', 'Iodine', 'Portion Weight', 'Potassium']:
                        if key in nutrients:
                            nutrients[key] = round(nutrients[key] * cap_factor, 1)
                    continue 
            
            if portion_g > max_g:
                cap_factor = max_g / portion_g
                logger.warning(f"      DENSITY CAP: '{item.get('name', 'Unknown')[:40]}' is {density_tier} density "
                              f"({cal_per_100g:.1f} kcal/100g) → capping at {max_oz:.1f} oz ({max_g:.0f}g)")
                
                item['portion_g'] = round(portion_g * cap_factor, 1)
                nutrients = item.get('nutrients', {})
                for key in ['Calories', 'Protein', 'Carbohydrates', 'Fat', 'Fiber', 'Sodium', 'Sugar', 'Cholesterol', 'Iodine', 'Portion Weight', 'Potassium']:
                    if key in nutrients:
                        nutrients[key] = round(nutrients[key] * cap_factor, 1)
        
        return meal_items
    
    
    @classmethod
    def block_nuts_seeds_from_main_meals(cls, meal_items: List[Dict], meal_name: str) -> List[Dict]:
        """
        Block PURE nuts/seeds from lunch/dinner, but ALLOW grain-based nut/seed foods.
        
        ALLOWS in lunch/dinner:
        - Grain-based nut/seed foods (e.g., "Baked Seeded Rolls" with grain_base + nut_seed tags)
        - Foods where nuts/seeds are a minor component (e.g., "Bread with Sesame Seeds")
        
        BLOCKS from lunch/dinner:
        - Pure nut/seed items (e.g., "Almond Mix", "Pumpkin Seeds" with only nut_seed tag)
        - High-density nut/seed snacks (e.g., nut butters without grain base)
        
        SCALABLE APPROACH: Uses database food_group metadata instead of keywords.
        Works for 21k+ foods across all languages/cuisines.
        """
        meal_name_lower = meal_name.lower()
        is_main_meal = 'lunch' in meal_name_lower or 'dinner' in meal_name_lower
        
        if not is_main_meal:
            return meal_items  
        
        filtered_items = []
        for item in meal_items:
            food_name = item.get('name', 'Unknown')
            
            food_groups = item.get('food_groups', []) or item.get('categories', []) or []
            food_groups_lower = [g.lower() for g in food_groups if g]
            
            is_nut_seed = 'nut_seed' in food_groups_lower
            
            is_grain_based = any(tag in food_groups_lower for tag in ['grain_base', 'starch_base', 'light_grain'])
            
            if not is_nut_seed:
                calories = item.get('nutrients', {}).get('Calories', 0)
                portion_g = item.get('portion_g', 100) or item.get('nutrients', {}).get('Portion Weight', 100)
                if portion_g > 0:
                    cal_per_100g = (calories / portion_g) * 100
                    fat_g = item.get('nutrients', {}).get('Fat', 0)
                    fat_per_100g = (fat_g / portion_g) * 100
                    
                    is_nut_seed = cal_per_100g > 450 and fat_per_100g > 35
            
            if is_nut_seed and is_grain_based:
                logger.info(f"       ALLOWING grain-based nut/seed in {meal_name.upper()}: '{food_name}' ")
                logger.info(f"         Food groups: {food_groups_lower} (has both nut_seed + grain tags)")
                filtered_items.append(item)
            elif is_nut_seed:
                logger.error(f"       BLOCKING PURE NUT/SEED FROM {meal_name.upper()}: '{food_name}' ")
                logger.error(f"         Food groups: {food_groups_lower}")
                logger.error(f"         Pure nuts/seeds should ONLY be in breakfast or snacks, NOT main meals!")
                continue  
            else:
                filtered_items.append(item)
        
        return filtered_items
 
    
    @classmethod
    def block_standalone_spices_condiments(cls, meal_items: List[Dict], meal_name: str) -> List[Dict]:

        filtered_items = []
        
        for item in meal_items:
            food_name = item.get('name', 'Unknown')
            portion_g = item.get('portion_g', 0) or item.get('nutrients', {}).get('Portion Weight', 0)
            calories = item.get('nutrients', {}).get('Calories', 0)
            
            food_groups = item.get('food_groups', []) or item.get('categories', []) or []
            food_groups_lower = [g.lower() for g in food_groups if g]
            
            is_pure_condiment = any(tag in food_groups_lower for tag in [
                'spice', 'seasoning', 'herb', 'spice_blend'
            ])
            
            is_small_condiment = (
                'condiment_side' in food_groups_lower or 'condiment' in food_groups_lower
            ) and portion_g < 50  

            is_garnish = calories < 30 and portion_g < 30
            
            is_inappropriate = is_pure_condiment or is_small_condiment or is_garnish
            
            if is_inappropriate:
                logger.warning(f"        BLOCKING STANDALONE SPICE/CONDIMENT: '{food_name}' ({portion_g:.0f}g, {calories:.0f} kcal)")
                logger.warning(f"         Food groups: {food_groups_lower}")
                logger.warning(f"         Reason: Small portion condiment (should be IN dishes, not standalone)")
                continue 
            
            filtered_items.append(item)
        
        if len(filtered_items) < len(meal_items):
            logger.info(f"      Removed {len(meal_items) - len(filtered_items)} inappropriate spice/condiment items from {meal_name}")
        
        return filtered_items

    
    @classmethod
    def validate_and_fix_meal(cls, meal_items: List[Dict], meal_name: str) -> Tuple[bool, str, List[Dict]]:

        
        if not meal_items:
            return (True, "Empty meal", meal_items)
        
        logger.info(f"\n    INTELLIGENT MEAL COMPOSITION: Validating '{meal_name}'")
        
        meal_items = cls.block_nuts_seeds_from_main_meals(meal_items, meal_name)
        
        meal_items = cls.block_standalone_spices_condiments(meal_items, meal_name)
        
        flavor_ok, flavor_msg = cls.check_flavor_compatibility(meal_items)
        logger.info(f"      {flavor_msg}")
        
        if not flavor_ok:
            if len(meal_items) > 2:
                removed = meal_items.pop()
                logger.warning(f"       FIXING: Removed '{removed.get('name')}' due to flavor clash")
                flavor_ok = True
        
        meal_items = cls.adjust_portions_by_density(meal_items)
        
        veg_ok, veg_msg, meal_items = cls.validate_vegetable_volume(meal_items, meal_name)
        logger.info(f"      {veg_msg}")
        
        meal_type = 'snack' if 'snack' in meal_name.lower() else meal_name.lower()
        if meal_type not in cls.MEAL_VOLUME_CAPS:
            meal_type = 'lunch'
        
        caps = cls.MEAL_VOLUME_CAPS[meal_type]
        total_solid_oz = 0.0
        total_liquid_oz = 0.0
        
        for item in meal_items:
            portion_g = item.get('nutrients', {}).get('Portion Weight', 0) or item.get('portion_g', 0)
            portion_oz = portion_g / 28.35
            
            food_name_lower = item.get('name', '').lower()
            is_liquid = any(kw in food_name_lower for kw in ['smoothie', 'juice', 'shake', 'milk', 'water', 'tea', 'coffee', 'soup', 'broth'])
            
            if is_liquid:
                total_liquid_oz += portion_oz
            else:
                total_solid_oz += portion_oz
        
        volume_ok = True
        if total_solid_oz > caps['total_solid_oz']:
            scale_factor = caps['total_solid_oz'] / total_solid_oz
            logger.warning(f"      TOTAL SOLID VOLUME: {total_solid_oz:.1f} oz > {caps['total_solid_oz']:.1f} oz cap")
            logger.warning(f"         Scaling all solids by {scale_factor:.2f}x")
            volume_ok = False
            
            for item in meal_items:
                food_name_lower = item.get('name', '').lower()
                is_liquid = any(kw in food_name_lower for kw in ['smoothie', 'juice', 'shake', 'milk', 'water', 'tea', 'coffee', 'soup', 'broth'])
                
                if not is_liquid:
                    item['portion_g'] = round(item.get('portion_g', 100) * scale_factor, 1)
                    nutrients = item.get('nutrients', {})
                    for key in ['Calories', 'Protein', 'Carbohydrates', 'Fat', 'Fiber', 'Sodium', 'Sugar', 'Cholesterol', 'Iodine', 'Portion Weight', 'Potassium']:
                        if key in nutrients:
                            nutrients[key] = round(nutrients[key] * scale_factor, 1)
        
        if total_liquid_oz > caps['total_liquid_oz']:
            scale_factor = caps['total_liquid_oz'] / total_liquid_oz
            logger.warning(f"       TOTAL LIQUID VOLUME: {total_liquid_oz:.1f} oz > {caps['total_liquid_oz']:.1f} oz cap")
            logger.warning(f"         Scaling all liquids by {scale_factor:.2f}x")
            volume_ok = False
            
            for item in meal_items:
                food_name_lower = item.get('name', '').lower()
                is_liquid = any(kw in food_name_lower for kw in ['smoothie', 'juice', 'shake', 'milk', 'water', 'tea', 'coffee', 'soup', 'broth'])
                
                if is_liquid:
                    item['portion_g'] = round(item.get('portion_g', 100) * scale_factor, 1)
                    nutrients = item.get('nutrients', {})
                    for key in ['Calories', 'Protein', 'Carbohydrates', 'Fat', 'Fiber', 'Sodium', 'Sugar', 'Cholesterol', 'Iodine', 'Portion Weight', 'Potassium']:
                        if key in nutrients:
                            nutrients[key] = round(nutrients[key] * scale_factor, 1)
        
        if volume_ok:
            logger.info(f"       Volume OK: Solids={total_solid_oz:.1f}oz, Liquids={total_liquid_oz:.1f}oz")
        
        logger.info(f"\n       COMPOSITION SUMMARY:")
        logger.info(f"          Nuts/Seeds: Capped at 1.5 oz max")
        logger.info(f"          Flavor Pairing: Checked for incompatibilities") 
        logger.info(f"          Density-Aware: Low-cal foods capped appropriately")
        logger.info(f"          Vegetable Volume: Max {caps['total_vegetable_oz']:.1f} oz per meal")
        logger.info(f"          Total Volume: Solids max {caps['total_solid_oz']:.1f} oz, Liquids max {caps['total_liquid_oz']:.1f} oz")
        
        final_msg = " Meal composition validated and optimized"
        return (True, final_msg, meal_items)



def example_usage():
    """How to use the intelligent composer"""
    
    meal_items = [
        {
            'name': 'Cooked Leafy Greens',
            'portion_g': 540,  
            'nutrients': {'Calories': 100, 'Portion Weight': 540},
            'food_groups': ['primary_vegetable', 'cooked_vegetable']
        },
        {
            'name': 'Cooked Fresh Spinach',
            'portion_g': 397, 
            'nutrients': {'Calories': 80, 'Portion Weight': 397},
            'food_groups': ['primary_vegetable']
        },
        {
            'name': 'Baked Cabbage',
            'portion_g': 362,
            'nutrients': {'Calories': 120, 'Portion Weight': 362},
            'food_groups': ['cooked_vegetable']
        },
        {
            'name': 'Grilled Chicken',
            'portion_g': 198, 
            'nutrients': {'Calories': 300, 'Portion Weight': 198},
            'food_groups': ['protein_main']
        }
    ]

    
    is_valid, message, fixed_items = IntelligentMealComposer.validate_and_fix_meal(
        meal_items, 
        meal_name='Lunch'
    )
    
    print(f"\n{message}")
    print(f"Items after fix: {len(fixed_items)}")
    for item in fixed_items:
        print(f"  - {item['name']}: {item['portion_g']:.0f}g")


if __name__ == "__main__":
    example_usage()
