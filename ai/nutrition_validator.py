from typing import Dict, Any, List, Optional, Tuple
from enum import Enum
import logging
import json
import re

logger = logging.getLogger(__name__)


class ValidationSeverity(Enum):
    INFO = "info"        
    WARNING = "warning"    
    ERROR = "error"     
    CRITICAL = "critical" 


class ValidationResult:
    def __init__(
        self,
        passed: bool,
        severity: ValidationSeverity = ValidationSeverity.INFO,
        message: str = "",
        field: Optional[str] = None,
        suggested_fix: Optional[str] = None
    ):
        self.passed = passed
        self.severity = severity
        self.message = message
        self.field = field
        self.suggested_fix = suggested_fix
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'passed': self.passed,
            'severity': self.severity.value,
            'message': self.message,
            'field': self.field,
            'suggested_fix': self.suggested_fix
        }


class NutritionValidator:

    CALORIE_TOLERANCE_PCT = 15.0 
    MACRO_TOLERANCE_PCT = 25.0   
    
    CALORIES_PER_G_PROTEIN = 4
    CALORIES_PER_G_CARBS = 4
    CALORIES_PER_G_FAT = 9
    
    def __init__(self):
        self.validation_history = []
    
    def validate_meal_plan(
        self,
        meal_plan: Dict[str, Any],
        constraints: Optional[Dict[str, Any]] = None,
        target_calories: Optional[int] = None
    ) -> Tuple[bool, List[ValidationResult]]:

        results = []
        
        results.extend(self._validate_schema(meal_plan))
        
        if constraints and constraints.get('allergies'):
            results.extend(self._validate_allergens(meal_plan, constraints['allergies']))
        
        if constraints and constraints.get('restrictions'):
            results.extend(self._validate_dietary_restrictions(meal_plan, constraints['restrictions']))
        
        if target_calories:
            results.extend(self._validate_calories(meal_plan, target_calories))
        
        results.extend(self._validate_macro_consistency(meal_plan))
        
        if constraints and constraints.get('medical_conditions'):
            results.extend(self._validate_medical_compatibility(meal_plan, constraints['medical_conditions']))
        
        results.extend(self._validate_portions(meal_plan))
        
        results.extend(self._validate_non_zero_calories(meal_plan))
        
        self.validation_history.append({
            'timestamp': logger.time,
            'results': [r.to_dict() for r in results]
        })
        
        has_errors = any(
            not r.passed and r.severity in [ValidationSeverity.ERROR, ValidationSeverity.CRITICAL]
            for r in results
        )
        
        return not has_errors, results
    
    def _validate_schema(self, meal_plan: Dict[str, Any]) -> List[ValidationResult]:
        results = []
        
        if 'meals' not in meal_plan:
            results.append(ValidationResult(
                passed=False,
                severity=ValidationSeverity.ERROR,
                message="Missing 'meals' field in meal plan",
                suggested_fix="Ensure meal plan contains 'meals' array"
            ))
        
        if 'total_day_macros' not in meal_plan and 'meals' in meal_plan:
            results.append(ValidationResult(
                passed=False,
                severity=ValidationSeverity.WARNING,
                message="Missing 'total_day_macros' field",
                suggested_fix="Calculate and include total_day_macros"
            ))
        
        return results
    
    def _validate_allergens(self, meal_plan: Dict[str, Any], allergies: List[str]) -> List[ValidationResult]:
        results = []
        plan_text = json.dumps(meal_plan).lower()
        
        for allergen in allergies:
            allergen_lower = allergen.lower()
            
            allergen_patterns = {
                'peanut': ['peanut', 'peanuts', 'groundnut'],
                'tree nut': ['almond', 'cashew', 'walnut', 'pecan', 'pistachio', 'hazelnut'],
                'dairy': ['milk', 'cheese', 'paneer', 'yogurt', 'yoghurt', 'ghee', 'butter', 'cream'],
                'egg': ['egg', 'eggs'],
                'soy': ['soy', 'soya', 'tofu', 'tempeh'],
                'gluten': ['wheat', 'barley', 'rye', 'roti', 'chapati', 'bread', 'pasta'],
                'fish': ['fish', 'salmon', 'tuna', 'cod', 'tilapia'],
                'shellfish': ['shrimp', 'prawn', 'crab', 'lobster', 'shellfish']
            }
            
            patterns_to_check = allergen_patterns.get(allergen_lower, [allergen_lower])
            
            for pattern in patterns_to_check:
                if pattern in plan_text:
                    results.append(ValidationResult(
                        passed=False,
                        severity=ValidationSeverity.CRITICAL,
                        message=f"ALLERGEN DETECTED: Plan contains '{pattern}' (allergen: {allergen})",
                        field='allergies',
                        suggested_fix=f"Remove all foods containing {allergen} and regenerate meal plan"
                    ))
                    break  
        
        if not results:
            results.append(ValidationResult(
                passed=True,
                severity=ValidationSeverity.INFO,
                message=f" No allergens detected (checked: {', '.join(allergies)})"
            ))
        
        return results
    
    def _validate_dietary_restrictions(self, meal_plan: Dict[str, Any], restrictions: List[str]) -> List[ValidationResult]:
        results = []
        plan_text = json.dumps(meal_plan).lower()
        
        restriction_keywords = {
            'vegetarian': {
                'forbidden': ['chicken', 'fish', 'beef', 'pork', 'lamb', 'turkey', 'meat', 'seafood', 'shrimp'],
                'description': 'meat and seafood'
            },
            'vegan': {
                'forbidden': ['chicken', 'fish', 'beef', 'pork', 'lamb', 'turkey', 'meat', 'seafood', 
                            'egg', 'dairy', 'milk', 'cheese', 'paneer', 'yogurt', 'ghee', 'butter', 'honey'],
                'description': 'animal products'
            },
            'gluten-free': {
                'forbidden': ['wheat', 'roti', 'chapati', 'bread', 'pasta', 'noodle', 'barley', 'rye'],
                'description': 'gluten-containing grains'
            },
            'dairy-free': {
                'forbidden': ['milk', 'cheese', 'yogurt', 'yoghurt', 'butter', 'cream', 'ghee', 'casein', 'whey', 'ice cream', 'paneer', 'chenna', 'chhena', 'malai', 'mawa', 'khoa', 'khoya', 'buttermilk', 'chaas', 'lassi', 'curd', 'dahi'],
                'description': 'dairy products'
            },
            'keto': {
                'forbidden': ['rice', 'bread', 'pasta', 'potato', 'sweet potato', 'corn', 'oats'],
                'description': 'high-carb foods'
            },
            'paleo': {
                'forbidden': ['bread', 'pasta', 'rice', 'dairy', 'legume', 'bean', 'lentil'],
                'description': 'processed grains and legumes'
            }
        }
        
        for restriction in restrictions:
            restriction_lower = restriction.lower()
            
            if restriction_lower in restriction_keywords:
                forbidden_items = restriction_keywords[restriction_lower]['forbidden']
                description = restriction_keywords[restriction_lower]['description']
                
                violations = []
                for item in forbidden_items:
                    if item in plan_text:
                        violations.append(item)
                
                if violations:
                    results.append(ValidationResult(
                        passed=False,
                        severity=ValidationSeverity.CRITICAL,
                        message=f"DIETARY VIOLATION ({restriction}): Plan contains {description}: {', '.join(violations)}",
                        field='restrictions',
                        suggested_fix=f"Remove {', '.join(violations)} and replace with {restriction}-friendly alternatives"
                    ))
        
        if not results:
            results.append(ValidationResult(
                passed=True,
                severity=ValidationSeverity.INFO,
                message=f"✓ Dietary restrictions followed ({', '.join(restrictions)})"
            ))
        
        return results
    
    def _validate_calories(self, meal_plan: Dict[str, Any], target_calories: int) -> List[ValidationResult]:
        results = []
        
        total_calories = meal_plan.get('total_day_macros', {}).get('Calories', 0)
        
        if total_calories == 0:
            total_calories = sum(
                meal.get('total_macros', {}).get('Calories', 0)
                for meal in meal_plan.get('meals', [])
            )
        
        if total_calories == 0:
            results.append(ValidationResult(
                passed=False,
                severity=ValidationSeverity.ERROR,
                message="Total calories is 0 - indicates missing nutrition data",
                field='calories',
                suggested_fix="Ensure all meal items have valid calorie values"
            ))
            return results
        
        diff_pct = abs(total_calories - target_calories) / target_calories * 100
        
        if diff_pct > self.CALORIE_TOLERANCE_PCT:
            severity = ValidationSeverity.ERROR if diff_pct > 25 else ValidationSeverity.WARNING
            results.append(ValidationResult(
                passed=False,
                severity=severity,
                message=f"Calorie mismatch: {total_calories:.0f} kcal vs target {target_calories} kcal (off by {diff_pct:.1f}%)",
                field='calories',
                suggested_fix=f"Adjust portions to reach {target_calories} kcal (±{self.CALORIE_TOLERANCE_PCT}% tolerance)"
            ))
        else:
            results.append(ValidationResult(
                passed=True,
                severity=ValidationSeverity.INFO,
                message=f"✓ Calories within target: {total_calories:.0f} kcal (target: {target_calories}, diff: {diff_pct:.1f}%)"
            ))
        
        return results
    
    def _validate_macro_consistency(self, meal_plan: Dict[str, Any]) -> List[ValidationResult]:
        results = []
        
        for meal in meal_plan.get('meals', []):
            meal_name = meal.get('meal_name', 'Unknown')
            macros = meal.get('total_macros', {})
            
            protein = macros.get('Protein', 0)
            carbs = macros.get('Carbohydrates', 0)
            fat = macros.get('Fat', 0)
            calories = macros.get('Calories', 0)
            
            if calories > 0 and (protein > 0 or carbs > 0 or fat > 0):
                calculated_calories = (
                    protein * self.CALORIES_PER_G_PROTEIN +
                    carbs * self.CALORIES_PER_G_CARBS +
                    fat * self.CALORIES_PER_G_FAT
                )
                
                if calculated_calories > 0:
                    diff_pct = abs(calculated_calories - calories) / calculated_calories * 100
                    
                    if diff_pct > self.MACRO_TOLERANCE_PCT:
                        results.append(ValidationResult(
                            passed=False,
                            severity=ValidationSeverity.WARNING,
                            message=(
                                f"Macro inconsistency in {meal_name}: "
                                f"P={protein}g C={carbs}g F={fat}g suggests {calculated_calories:.0f} kcal "
                                f"but plan shows {calories:.0f} kcal (diff: {diff_pct:.1f}%)"
                            ),
                            field='macros',
                            suggested_fix="Recalculate nutrition values from database"
                        ))
        
        if not results:
            results.append(ValidationResult(
                passed=True,
                severity=ValidationSeverity.INFO,
                message="✓ Macro values are consistent with calories"
            ))
        
        return results
    
    def _validate_medical_compatibility(self, meal_plan: Dict[str, Any], conditions: List[str]) -> List[ValidationResult]:
        results = []
        plan_text = json.dumps(meal_plan).lower()
        
        medical_risks = {
            'diabetes': {
                'high_risk': ['refined sugar', 'white rice', 'white bread', 'fruit juice', 'candy'],
                'moderate_risk': ['potato', 'pasta', 'honey'],
                'message': 'may cause blood sugar spikes'
            },
            'hypertension': {
                'high_risk': ['high sodium', 'pickle', 'papad', 'chips', 'processed meat'],
                'moderate_risk': ['cheese', 'canned food'],
                'message': 'may increase blood pressure'
            },
            'kidney disease': {
                'high_risk': ['high protein', 'high sodium', 'high potassium'],
                'moderate_risk': ['banana', 'orange', 'tomato'],
                'message': 'may stress kidney function'
            },
            'heart disease': {
                'high_risk': ['saturated fat', 'trans fat', 'fried food'],
                'moderate_risk': ['butter', 'ghee', 'coconut oil'],
                'message': 'may affect cardiovascular health'
            }
        }
        
        for condition in conditions:
            condition_lower = condition.lower()
            
            if condition_lower in medical_risks:
                risks = medical_risks[condition_lower]
                high_risk_found = [item for item in risks['high_risk'] if item in plan_text]
                moderate_risk_found = [item for item in risks['moderate_risk'] if item in plan_text]
                
                if high_risk_found:
                    results.append(ValidationResult(
                        passed=False,
                        severity=ValidationSeverity.CRITICAL,
                        message=(
                            f"HIGH RISK for {condition}: Plan contains {', '.join(high_risk_found)} "
                            f"which {risks['message']}"
                        ),
                        field='medical_conditions',
                        suggested_fix=f"Remove {', '.join(high_risk_found)} and choose {condition}-friendly alternatives"
                    ))
                
                if moderate_risk_found:
                    results.append(ValidationResult(
                        passed=True,
                        severity=ValidationSeverity.WARNING,
                        message=(
                            f"MODERATE RISK for {condition}: Plan contains {', '.join(moderate_risk_found)}. "
                            f"Monitor portions carefully."
                        ),
                        field='medical_conditions'
                    ))
        
        return results
    
    def _validate_portions(self, meal_plan: Dict[str, Any]) -> List[ValidationResult]:
        results = []
        
        for meal in meal_plan.get('meals', []):
            meal_name = meal.get('meal_name', 'Unknown')
            
            for item in meal.get('items', []):
                quantity = item.get('quantity', 0)
                unit = item.get('unit', '').lower()
                food_name = item.get('food_name', '')
                
                portion_limits = {
                    'cup': 5,
                    'cups': 5,
                    'oz': 16,
                    'ounce': 16,
                    'ounces': 16,
                    'tbsp': 10,
                    'tablespoon': 10,
                    'tsp': 15,
                    'teaspoon': 15,
                    'piece': 10,
                    'pieces': 10
                }
                
                limit = portion_limits.get(unit, None)
                
                if limit and quantity > limit:
                    results.append(ValidationResult(
                        passed=False,
                        severity=ValidationSeverity.WARNING,
                        message=f"Large portion in {meal_name}: {quantity} {unit} of {food_name} (typical max: {limit} {unit})",
                        field='portions',
                        suggested_fix=f"Consider reducing to {limit} {unit} or less"
                    ))
        
        return results
    
    def _validate_non_zero_calories(self, meal_plan: Dict[str, Any]) -> List[ValidationResult]:
        results = []
        
        for meal in meal_plan.get('meals', []):
            meal_name = meal.get('meal_name', 'Unknown')
            calories = meal.get('total_macros', {}).get('Calories', 0)
            
            if 'snack' in meal_name.lower() and calories < 50:
                continue
            
            if calories == 0:
                results.append(ValidationResult(
                    passed=False,
                    severity=ValidationSeverity.ERROR,
                    message=f"Zero calories in {meal_name} - indicates database lookup failure",
                    field='calories',
                    suggested_fix=f"Verify all food items in {meal_name} have nutrition data"
                ))
        
        return results
    
    def format_validation_report(self, results: List[ValidationResult]) -> str:
        if not results:
            return "No validation results available."
        
        critical = [r for r in results if r.severity == ValidationSeverity.CRITICAL and not r.passed]
        errors = [r for r in results if r.severity == ValidationSeverity.ERROR and not r.passed]
        warnings = [r for r in results if r.severity == ValidationSeverity.WARNING and not r.passed]
        passed = [r for r in results if r.passed]
        
        report_lines = ["=== MEAL PLAN VALIDATION REPORT ===\n"]
        
        if critical:
            report_lines.append(" CRITICAL ISSUES (Must Fix):")
            for r in critical:
                report_lines.append(f"   {r.message}")
                if r.suggested_fix:
                    report_lines.append(f"     Fix: {r.suggested_fix}")
            report_lines.append("")
        
        if errors:
            report_lines.append(" ERRORS (Must Fix):")
            for r in errors:
                report_lines.append(f"  • {r.message}")
                if r.suggested_fix:
                    report_lines.append(f"    Fix: {r.suggested_fix}")
            report_lines.append("")
        
        if warnings:
            report_lines.append("  WARNINGS (Review Recommended):")
            for r in warnings:
                report_lines.append(f"  • {r.message}")
            report_lines.append("")
        
        if passed:
            report_lines.append(f" PASSED CHECKS ({len(passed)}):")
            for r in passed[:5]:  
                report_lines.append(f"  • {r.message}")
            if len(passed) > 5:
                report_lines.append(f"  ... and {len(passed) - 5} more")
        
        total = len(results)
        failed = len(critical) + len(errors)
        report_lines.append(f"\n SUMMARY: {total - failed}/{total} checks passed")
        
        if critical or errors:
            report_lines.append("\n  VALIDATION FAILED - Meal plan requires corrections")
        else:
            report_lines.append("\n VALIDATION PASSED - Meal plan is safe to use")
        
        return "\n".join(report_lines)



__all__ = [
    'NutritionValidator',
    'ValidationResult',
    'ValidationSeverity'
]
