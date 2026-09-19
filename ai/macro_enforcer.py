import logging
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

MAX_PROTEIN_PCT = 35.0  
MAX_PROTEIN_PCT_RESCUE = 40.0 
MAX_CARBS_PCT = 35.0
MAX_FAT_PCT = 50.0

TARGET_PROTEIN_PCT = 28.0 
TARGET_PROTEIN_PCT_RESCUE = 38.0  
TARGET_CARBS_PCT = 28.0    
TARGET_FAT_PCT = 44.0      

MAX_ACCEPTABLE_CALORIE_LOSS_PCT = 15.0  

CALS_PER_G_PROTEIN = 4
CALS_PER_G_CARBS = 4
CALS_PER_G_FAT = 9


def calculate_macro_percentages(meal_plan: Dict) -> Dict[str, float]:

    if 'meal_plan' in meal_plan and isinstance(meal_plan['meal_plan'], dict):
        meals = meal_plan['meal_plan']
    else:
        meals = meal_plan
    
    total_protein = 0.0
    total_carbs = 0.0
    total_fat = 0.0
    
    for meal_name, items in meals.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                nutrients = item.get('nutrients', {})
                total_protein += nutrients.get('Protein', 0)
                total_carbs += nutrients.get('Carbohydrates', 0)
                total_fat += nutrients.get('Fat', 0)
    
    macro_calories = (total_protein * CALS_PER_G_PROTEIN) + (total_carbs * CALS_PER_G_CARBS) + (total_fat * CALS_PER_G_FAT)
    
    if macro_calories == 0:
        return {'protein_pct': 0, 'carbs_pct': 0, 'fat_pct': 0, 'total_cals': 0}
    
    protein_pct = (total_protein * CALS_PER_G_PROTEIN / macro_calories) * 100
    carbs_pct = (total_carbs * CALS_PER_G_CARBS / macro_calories) * 100
    fat_pct = (total_fat * CALS_PER_G_FAT / macro_calories) * 100
    
    return {
        'protein_pct': protein_pct,
        'carbs_pct': carbs_pct,
        'fat_pct': fat_pct,
        'total_cals': macro_calories,
        'total_protein_g': total_protein,
        'total_carbs_g': total_carbs,
        'total_fat_g': total_fat
    }


def check_macro_violations(meal_plan: Dict, rescue_mode: bool = False) -> Tuple[bool, List[str]]:

    macros = calculate_macro_percentages(meal_plan)
    violations = []
    
    protein_ceiling = MAX_PROTEIN_PCT_RESCUE if rescue_mode else MAX_PROTEIN_PCT
    
    if macros['protein_pct'] > protein_ceiling:
        violations.append(f"Protein {macros['protein_pct']:.1f}% > {protein_ceiling}%")
    
    if macros['carbs_pct'] > MAX_CARBS_PCT:
        violations.append(f"Carbs {macros['carbs_pct']:.1f}% > {MAX_CARBS_PCT}%")
    
    if macros['fat_pct'] > MAX_FAT_PCT:
        violations.append(f"Fat {macros['fat_pct']:.1f}% > {MAX_FAT_PCT}%")
    
    return (len(violations) > 0, violations)


def adjust_meal_plan_macros(meal_plan: Dict, target_calories: int = None) -> Tuple[Dict, bool]:

    if 'meal_plan' in meal_plan and isinstance(meal_plan['meal_plan'], dict):
        meals = meal_plan['meal_plan']
    else:
        meals = meal_plan
    
    was_any_adjustment = False
    max_iterations = 50 
    iteration = 0
    original_calories = calculate_macro_percentages(meal_plan)['total_cals']
    
    rescue_mode = False
    if target_calories and original_calories > 0:
        calorie_ratio = original_calories / target_calories
        if calorie_ratio < 0.70: 
            rescue_mode = True
            logger.warning("=" * 80)
            logger.warning(" HIGH PROTEIN RESCUE MODE ACTIVATED")
            logger.warning(f"   Current: {original_calories:.0f} kcal | Target: {target_calories:.0f} kcal")
            logger.warning(f"   Shortfall: {(1 - calorie_ratio) * 100:.1f}% below target")
            logger.warning(f"   PROTEIN CEILING RAISED: 35% → 40% (medically safe)")
            logger.warning(f"   STRATEGY: Fill calorie gap with HIGH PROTEIN foods")
            logger.warning("=" * 80)
    
    while iteration < max_iterations:
        iteration += 1
        macros = calculate_macro_percentages(meal_plan)
        has_violations, violations = check_macro_violations(meal_plan, rescue_mode=rescue_mode)
        
        if not has_violations:
            if iteration == 1:
                if rescue_mode:
                    logger.info(f" RESCUE MODE: Macro constraints met (protein ceiling: 40%)")
                else:
                    logger.info(" Macro constraints met - no adjustment needed")
            else:
                logger.info(f" All macros within limits after {iteration-1} iteration(s)")
                final_macros = calculate_macro_percentages(meal_plan)
                calorie_change = ((final_macros['total_cals'] - original_calories) / original_calories * 100) if original_calories > 0 else 0
                logger.info(f"   Final: Protein {final_macros['protein_pct']:.1f}%, Carbs {final_macros['carbs_pct']:.1f}%, Fat {final_macros['fat_pct']:.1f}%")
                logger.info(f"   Calories: {final_macros['total_cals']:.0f} ({calorie_change:+.1f}% from original)")
                if rescue_mode:
                    logger.info(f"   \ud83d\udea8 RESCUE MODE was active (protein ceiling: 40%)")
            return (meal_plan, was_any_adjustment)
        
        if iteration == 1:
            logger.warning(f" MACRO VIOLATIONS DETECTED: {', '.join(violations)}")
            logger.warning(f"   Current: Protein {macros['protein_pct']:.1f}%, Carbs {macros['carbs_pct']:.1f}%, Fat {macros['fat_pct']:.1f}%")
            logger.warning(f"   Total Calories: {macros['total_cals']:.0f} kcal" + (f" (Target: {target_calories} kcal)" if target_calories else ""))
            if rescue_mode:
                logger.warning(f"   \ud83d\udea8 RESCUE MODE ACTIVE: Protein ceiling raised to 40%")
        
        protein_ceiling = MAX_PROTEIN_PCT_RESCUE if rescue_mode else MAX_PROTEIN_PCT
        protein_excess = max(0, macros['protein_pct'] - protein_ceiling)
        carbs_excess = max(0, macros['carbs_pct'] - MAX_CARBS_PCT)
        fat_excess = max(0, macros['fat_pct'] - MAX_FAT_PCT)
        
        max_excess = max(protein_excess, carbs_excess, fat_excess)
        
        if max_excess == 0:
            return (meal_plan, was_any_adjustment)
        
        was_any_adjustment = True
        original_cals = macros['total_cals']
        
        if max_excess == fat_excess:
            target_fat_pct = TARGET_FAT_PCT
            non_fat_cals = (macros['total_protein_g'] * CALS_PER_G_PROTEIN) + (macros['total_carbs_g'] * CALS_PER_G_CARBS)
            target_fat_cals = (target_fat_pct * non_fat_cals) / (100 - target_fat_pct)
            target_fat_g = target_fat_cals / CALS_PER_G_FAT
            fat_scale_factor = target_fat_g / macros['total_fat_g'] if macros['total_fat_g'] > 0 else 1.0
            
            logger.warning(f" ITERATION {iteration}: Adjusting FAT: {macros['total_fat_g']:.1f}g → {target_fat_g:.1f}g (x{fat_scale_factor:.3f})")
            logger.warning(f"   {macros['fat_pct']:.1f}% → {target_fat_pct}%")
            
            for meal_name, items in meals.items():
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    nutrients = item.get('nutrients', {})
                    if 'Fat' in nutrients:
                        nutrients['Fat'] *= fat_scale_factor
                        nutrients['Calories'] = (nutrients.get('Protein', 0) * CALS_PER_G_PROTEIN) + \
                                               (nutrients.get('Carbohydrates', 0) * CALS_PER_G_CARBS) + \
                                               (nutrients['Fat'] * CALS_PER_G_FAT)
            
            new_macros = calculate_macro_percentages(meal_plan)
            calorie_loss = original_cals - new_macros['total_cals']
            
            if calorie_loss > 50: 
                protein_room = MAX_PROTEIN_PCT - new_macros['protein_pct']
                carbs_room = MAX_CARBS_PCT - new_macros['carbs_pct']
                
                if protein_room > 2 or carbs_room > 2:
                    if protein_room > carbs_room:
                        protein_cals_add = calorie_loss * 0.7
                        carbs_cals_add = calorie_loss * 0.3
                    else:
                        protein_cals_add = calorie_loss * 0.3
                        carbs_cals_add = calorie_loss * 0.7
                    
                    protein_g_add = protein_cals_add / CALS_PER_G_PROTEIN
                    carbs_g_add = carbs_cals_add / CALS_PER_G_CARBS
                    
                    protein_boost = 1 + (protein_g_add / new_macros['total_protein_g']) if new_macros['total_protein_g'] > 0 else 1.0
                    carbs_boost = 1 + (carbs_g_add / new_macros['total_carbs_g']) if new_macros['total_carbs_g'] > 0 else 1.0
                    
                    logger.warning(f"💡 COMPENSATING: +{protein_g_add:.1f}g protein, +{carbs_g_add:.1f}g carbs to recover {calorie_loss:.0f} kcal")
                    
                    for meal_name, items in meals.items():
                        if not isinstance(items, list):
                            continue
                        for item in items:
                            if not isinstance(item, dict):
                                continue
                            nutrients = item.get('nutrients', {})
                            if protein_room > 2 and 'Protein' in nutrients:
                                nutrients['Protein'] *= protein_boost
                            if carbs_room > 2 and 'Carbohydrates' in nutrients:
                                nutrients['Carbohydrates'] *= carbs_boost
                            nutrients['Calories'] = (nutrients.get('Protein', 0) * CALS_PER_G_PROTEIN) + \
                                                   (nutrients.get('Carbohydrates', 0) * CALS_PER_G_CARBS) + \
                                                   (nutrients.get('Fat', 0) * CALS_PER_G_FAT)
        
        elif max_excess == carbs_excess:
            target_carbs_pct = TARGET_CARBS_PCT
            non_carb_cals = (macros['total_protein_g'] * CALS_PER_G_PROTEIN) + (macros['total_fat_g'] * CALS_PER_G_FAT)
            target_carbs_cals = (target_carbs_pct * non_carb_cals) / (100 - target_carbs_pct)
            target_carbs_g = target_carbs_cals / CALS_PER_G_CARBS
            carbs_scale_factor = target_carbs_g / macros['total_carbs_g'] if macros['total_carbs_g'] > 0 else 1.0
            
            carbs_cals_removed = (macros['total_carbs_g'] - target_carbs_g) * CALS_PER_G_CARBS
            projected_new_cals = original_cals - carbs_cals_removed
            calorie_loss_pct = ((original_cals - projected_new_cals) / original_cals) * 100 if original_cals > 0 else 0

            
            logger.warning(f" ITERATION {iteration}: Adjusting CARBS: {macros['total_carbs_g']:.1f}g → {target_carbs_g:.1f}g (x{carbs_scale_factor:.3f})")
            logger.warning(f"   {macros['carbs_pct']:.1f}% → {target_carbs_pct}% (calorie impact: -{calorie_loss_pct:.1f}%)")
            
            for meal_name, items in meals.items():
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    nutrients = item.get('nutrients', {})
                    if 'Carbohydrates' in nutrients:
                        nutrients['Carbohydrates'] *= carbs_scale_factor
                        nutrients['Calories'] = (nutrients.get('Protein', 0) * CALS_PER_G_PROTEIN) + \
                                               (nutrients['Carbohydrates'] * CALS_PER_G_CARBS) + \
                                               (nutrients.get('Fat', 0) * CALS_PER_G_FAT)
            
            new_macros = calculate_macro_percentages(meal_plan)
            calorie_gap = original_cals - new_macros['total_cals']
            
            if calorie_gap > 50:
                protein_room = MAX_PROTEIN_PCT - new_macros['protein_pct']
                fat_room = MAX_FAT_PCT - new_macros['fat_pct']
                
                if protein_room > 2 or fat_room > 2:
                    if protein_room > fat_room:
                        protein_cals_add = calorie_gap * 0.6
                        fat_cals_add = calorie_gap * 0.4
                    else:
                        protein_cals_add = calorie_gap * 0.4
                        fat_cals_add = calorie_gap * 0.6
                    
                    protein_g_add = protein_cals_add / CALS_PER_G_PROTEIN
                    fat_g_add = fat_cals_add / CALS_PER_G_FAT
                    
                    protein_boost = 1 + (protein_g_add / new_macros['total_protein_g']) if new_macros['total_protein_g'] > 0 else 1.0
                    fat_boost = 1 + (fat_g_add / new_macros['total_fat_g']) if new_macros['total_fat_g'] > 0 else 1.0
                    
                    logger.warning(f"💡 COMPENSATING: +{protein_g_add:.1f}g protein, +{fat_g_add:.1f}g fat to recover {calorie_gap:.0f} kcal")
                    
                    for meal_name, items in meals.items():
                        if not isinstance(items, list):
                            continue
                        for item in items:
                            if not isinstance(item, dict):
                                continue
                            nutrients = item.get('nutrients', {})
                            if protein_room > 2 and 'Protein' in nutrients:
                                nutrients['Protein'] *= protein_boost
                            if fat_room > 2 and 'Fat' in nutrients:
                                nutrients['Fat'] *= fat_boost
                            nutrients['Calories'] = (nutrients.get('Protein', 0) * CALS_PER_G_PROTEIN) + \
                                                   (nutrients.get('Carbohydrates', 0) * CALS_PER_G_CARBS) + \
                                                   (nutrients.get('Fat', 0) * CALS_PER_G_FAT)
        
        else:  
            target_protein_pct = TARGET_PROTEIN_PCT
            non_protein_cals = (macros['total_carbs_g'] * CALS_PER_G_CARBS) + (macros['total_fat_g'] * CALS_PER_G_FAT)
            target_protein_cals = (target_protein_pct * non_protein_cals) / (100 - target_protein_pct)
            target_protein_g = target_protein_cals / CALS_PER_G_PROTEIN
            protein_scale_factor = target_protein_g / macros['total_protein_g'] if macros['total_protein_g'] > 0 else 1.0
            
            logger.warning(f" ITERATION {iteration}: Adjusting PROTEIN: {macros['total_protein_g']:.1f}g → {target_protein_g:.1f}g (x{protein_scale_factor:.3f})")
            logger.warning(f"   {macros['protein_pct']:.1f}% → {target_protein_pct}%")
            
            for meal_name, items in meals.items():
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    nutrients = item.get('nutrients', {})
                    if 'Protein' in nutrients:
                        nutrients['Protein'] *= protein_scale_factor
                        nutrients['Calories'] = (nutrients['Protein'] * CALS_PER_G_PROTEIN) + \
                                               (nutrients.get('Carbohydrates', 0) * CALS_PER_G_CARBS) + \
                                               (nutrients.get('Fat', 0) * CALS_PER_G_FAT)
            
            new_macros = calculate_macro_percentages(meal_plan)
            calorie_gap = original_cals - new_macros['total_cals']
            
            if calorie_gap > 50:
                carbs_room = MAX_CARBS_PCT - new_macros['carbs_pct']
                fat_room = MAX_FAT_PCT - new_macros['fat_pct']
                
                if carbs_room > 2 or fat_room > 2:
                    if carbs_room > fat_room:
                        carbs_cals_add = calorie_gap * 0.6
                        fat_cals_add = calorie_gap * 0.4
                    else:
                        carbs_cals_add = calorie_gap * 0.4
                        fat_cals_add = calorie_gap * 0.6
                    
                    carbs_g_add = carbs_cals_add / CALS_PER_G_CARBS
                    fat_g_add = fat_cals_add / CALS_PER_G_FAT
                    
                    carbs_boost = 1 + (carbs_g_add / new_macros['total_carbs_g']) if new_macros['total_carbs_g'] > 0 else 1.0
                    fat_boost = 1 + (fat_g_add / new_macros['total_fat_g']) if new_macros['total_fat_g'] > 0 else 1.0
                    
                    logger.warning(f" COMPENSATING: +{carbs_g_add:.1f}g carbs, +{fat_g_add:.1f}g fat to recover {calorie_gap:.0f} kcal")
                    
                    for meal_name, items in meals.items():
                        if not isinstance(items, list):
                            continue
                        for item in items:
                            if not isinstance(item, dict):
                                continue
                            nutrients = item.get('nutrients', {})
                            if carbs_room > 2 and 'Carbohydrates' in nutrients:
                                nutrients['Carbohydrates'] *= carbs_boost
                            if fat_room > 2 and 'Fat' in nutrients:
                                nutrients['Fat'] *= fat_boost
                            nutrients['Calories'] = (nutrients.get('Protein', 0) * CALS_PER_G_PROTEIN) + \
                                                   (nutrients.get('Carbohydrates', 0) * CALS_PER_G_CARBS) + \
                                                   (nutrients.get('Fat', 0) * CALS_PER_G_FAT)
        
        new_macros = calculate_macro_percentages(meal_plan)
        logger.warning(f"   After iteration {iteration}: Protein {new_macros['protein_pct']:.1f}%, Carbs {new_macros['carbs_pct']:.1f}%, Fat {new_macros['fat_pct']:.1f}%")
        logger.warning(f"   Calories: {new_macros['total_cals']:.0f}")
    
    logger.warning(f" Max iterations ({max_iterations}) reached - some macros may still be slightly out of range")
    final_macros = calculate_macro_percentages(meal_plan)
    logger.warning(f"   Final: Protein {final_macros['protein_pct']:.1f}%, Carbs {final_macros['carbs_pct']:.1f}%, Fat {final_macros['fat_pct']:.1f}%")
    return (meal_plan, was_any_adjustment)
