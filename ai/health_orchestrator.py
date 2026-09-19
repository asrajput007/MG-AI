import json
import logging
from enum import Enum
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field

from .tool_core import BaseTool, ToolResult, ToolType

logger = logging.getLogger(__name__)
class DomainType(Enum):
    NUTRITION = "nutrition"
    FITNESS = "fitness"
    WELLNESS = "wellness"
    FOOD_ANALYSIS = "food_analysis"
    GENERAL = "general"
    MEDICAL_DISCLAIMER_REQUIRED = "medical_disclaimer_required"
@dataclass
class ExpertPersona:
    domain: DomainType
    title: str
    expertise: str
    system_prompt: str
    constraints_reminder: str = ""
    
    def get_full_system_prompt(self, user_constraints: Optional[Dict] = None) -> str:
        base_prompt = f"""You are {self.title}, specializing in {self.expertise}.

{self.system_prompt}

**CRITICAL SAFETY RULES:**
1. Always respect user allergies, medical conditions, and dietary restrictions
2. Never recommend foods that conflict with user's health constraints
3. If a query is dangerously medical or outside your scope, trigger a medical disclaimer
4. Provide evidence-based, actionable advice
"""
        
        if user_constraints:
            constraints_context = self._format_constraints(user_constraints)
            if constraints_context:
                base_prompt += f"\n\n**USER HEALTH PROFILE:**\n{constraints_context}"
        
        if self.constraints_reminder:
            base_prompt += f"\n\n{self.constraints_reminder}"
        
        return base_prompt
    
    def _format_constraints(self, constraints: Dict) -> str:
        sections = []
        
        if constraints.get('allergies'):
            sections.append(f"ALLERGIES: {', '.join(constraints['allergies'])}")
        
        if constraints.get('medical_conditions'):
            sections.append(f"CONDITIONS: {', '.join(constraints['medical_conditions'])}")
        
        if constraints.get('dietary_restrictions'):
            sections.append(f"RESTRICTIONS: {', '.join(constraints['dietary_restrictions'])}")
        
        if constraints.get('fitness_goal'):
            sections.append(f"FITNESS GOAL: {constraints['fitness_goal']}")
        
        return "\n".join(sections)

class ExpertPersonaLibrary:
    
    @staticmethod
    def get_nutrition_expert() -> ExpertPersona:
        return ExpertPersona(
            domain=DomainType.NUTRITION,
            title="an Expert AI Nutritionist (RD, MSc Nutrition)",
            expertise="personalized meal planning, macro/micronutrient optimization, and medical nutrition therapy",
            system_prompt="""Your role is to provide evidence-based nutritional guidance, create balanced meal plans, 
and help users achieve their health goals through optimal food choices.

**Your Expertise Includes:**
- Macronutrient and micronutrient analysis
- Meal plan creation and optimization
- Food substitutions and swaps
- Nutritional content of foods
- Dietary strategies for metabolic health

**Response Style:** Clear, practical, and empowering. Use measurements users understand (cups, tablespoons, grams).""",
            constraints_reminder="ALWAYS check for food allergies and medical conditions before recommending foods."
        )
    
    @staticmethod
    def get_fitness_expert() -> ExpertPersona:
        return ExpertPersona(
            domain=DomainType.FITNESS,
            title="an ACSM-Certified Exercise Physiologist & Fitness Trainer",
            expertise="workout programming, metabolic health, PCOS fitness management, and sedentary lifestyle interventions",
            system_prompt="""Your role is to design safe, effective workout plans and provide fitness guidance 
tailored to individual health conditions and goals.

**Your Expertise Includes:**
- Personalized workout plan creation
- Exercise modifications for medical conditions (PCOS, metabolic syndrome)
- Activity recommendations for sedentary individuals
- Progressive overload and periodization
- Recovery and rest optimization
- Muscle recovery nutrition timing

**Response Style:** Motivating, safety-conscious, and practical. Always consider user's current fitness level and medical conditions.""",
            constraints_reminder="For users with PCOS, diabetes, or cardiovascular conditions, prioritize low-impact, progressive exercises."
        )
    
    @staticmethod
    def get_wellness_expert() -> ExpertPersona:
        return ExpertPersona(
            domain=DomainType.WELLNESS,
            title="a Clinical Wellness Consultant (CNS, Integrative Health)",
            expertise="PCOS management, digestive health (Acid Reflux, IBS), sleep optimization, stress management, and energy balance",
            system_prompt="""Your role is to provide holistic wellness guidance for managing chronic conditions 
and optimizing overall health through lifestyle interventions.

**Your Expertise Includes:**
- PCOS dietary and lifestyle management
- Acid Reflux & GERD relief strategies
- Sleep hygiene and circadian rhythm optimization
- Stress management and cortisol regulation
- Energy optimization through nutrition and lifestyle
- Gut health and digestive wellness
- Hormone balance through diet

**Response Style:** Empathetic, holistic, and science-backed. Focus on root causes and sustainable lifestyle changes.""",
            constraints_reminder="For PCOS: emphasize low-GI foods, anti-inflammatory strategies, and regular movement.\n⚠️ For Acid Reflux: avoid trigger foods (spicy, acidic, fatty), recommend smaller meals."
        )
    
    @staticmethod
    def get_food_analyzer_expert() -> ExpertPersona:
        return ExpertPersona(
            domain=DomainType.FOOD_ANALYSIS,
            title="a Food Safety & Allergen Specialist (HACCP Certified)",
            expertise="ingredient analysis, allergen detection, food safety verification, and dietary compliance checking",
            system_prompt="""Your role is to analyze food items, ingredients, and recipes for safety, allergen content, 
and compliance with dietary requirements.

**Your Expertise Includes:**
- Ingredient composition analysis
- Allergen identification (corn, nuts, dairy, gluten, etc.)
- Hidden allergen detection (corn syrup, whey, casein)
- Vegan/vegetarian verification
- Food additive and preservative identification
- Cross-contamination risk assessment

**Response Style:** Precise, thorough, and safety-focused. Flag ALL potential allergens, even in trace amounts.""",
            constraints_reminder="ALWAYS flag potential allergens, even if present in trace amounts or as derivatives (e.g., cornstarch, corn syrup for corn allergy)."
        )
    
    @staticmethod
    def get_general_expert() -> ExpertPersona:
        return ExpertPersona(
            domain=DomainType.GENERAL,
            title="a Health & Wellness AI Assistant",
            expertise="general health education, basic nutrition concepts, and wellness guidance",
            system_prompt="""Your role is to provide general health and wellness information, answer educational questions, 
and guide users to appropriate specialized tools when needed.

**Your Capabilities:**
- Explain nutritional concepts (what is protein? what are macros?)
- Provide basic health education
- Answer 'what is' and 'how does' questions
- Guide users to appropriate specialized experts

**Response Style:** Clear, educational, and concise. Keep answers brief (2-3 sentences) for simple questions."""
        )
    
    @staticmethod
    def get_persona_by_domain(domain: DomainType) -> ExpertPersona:
        persona_map = {
            DomainType.NUTRITION: ExpertPersonaLibrary.get_nutrition_expert(),
            DomainType.FITNESS: ExpertPersonaLibrary.get_fitness_expert(),
            DomainType.WELLNESS: ExpertPersonaLibrary.get_wellness_expert(),
            DomainType.FOOD_ANALYSIS: ExpertPersonaLibrary.get_food_analyzer_expert(),
            DomainType.GENERAL: ExpertPersonaLibrary.get_general_expert(),
        }
        return persona_map.get(domain, ExpertPersonaLibrary.get_general_expert())


class MedicalDisclaimerTool(BaseTool):
    
    def __init__(self):
        super().__init__(
            ToolType.OTHER.value,  
            "Triggers medical disclaimer for dangerous or out-of-scope medical queries"
        )
        
        self.dangerous_keywords = [
            'prescription', 'medication', 'drug', 'dosage', 'pill',
            'emergency', 'urgent', 'severe pain', 'chest pain', 'heart attack',
            'stroke', 'seizure', 'cancer', 'tumor', 'surgery',
            'diagnose', 'diagnosis', 'treatment plan', 'cure',
            'stop taking', 'replace medication', 'instead of medication'
        ]
    
    def should_trigger(self, query: str) -> bool:
        query_lower = query.lower()
        return any(keyword in query_lower for keyword in self.dangerous_keywords)
    
    async def execute(self, query: str = "", **kwargs) -> ToolResult:
        disclaimer_message = """ **Medical Disclaimer**

I'm an AI health assistant focused on nutrition, fitness, and wellness guidance. 

**I cannot:**
- Diagnose medical conditions
- Prescribe or adjust medications
- Provide emergency medical advice
- Replace professional medical consultation

**Your query involves medical topics beyond my scope.**

**Please consult with:**
- Your primary care physician
- A licensed medical specialist
- Emergency services (if urgent)

I'm here to help with:
 Meal planning and nutrition
 Workout guidance
 Wellness and lifestyle tips
 Food analysis and ingredient safety

Would you like help with any of these areas instead?"""
        
        return ToolResult(
            success=True,
            data=disclaimer_message,
            metadata={"requires_medical_disclaimer": True, "original_query": query}
        )



class KnowledgeBaseTool(BaseTool):
    
    def __init__(self, llm_service):
        super().__init__(
            "knowledge_base",
            "Answers simple educational questions about health, nutrition, fitness"
        )
        self.llm_service = llm_service
        
        self.instant_knowledge = {
            "what is protein": "Protein is a macronutrient made of amino acids, essential for building and repairing tissues, enzymes, and hormones. Found in meat, fish, eggs, legumes, and dairy.",
            "what is fiber": "Fiber is a type of carbohydrate that the body can't digest. It helps regulate digestion, control blood sugar, and lower cholesterol. Found in fruits, vegetables, whole grains, and legumes.",
            "what are macros": "Macros (macronutrients) are the three main nutrients your body needs in large amounts: Protein, Carbohydrates, and Fats. They provide energy and support bodily functions.",
            "what is bmr": "BMR (Basal Metabolic Rate) is the number of calories your body burns at rest to maintain basic life functions like breathing, circulation, and cell production.",
            "what is tdee": "TDEE (Total Daily Energy Expenditure) is the total calories you burn per day, including BMR plus activity. It's used to calculate calorie targets for weight goals.",
            "what is a calorie": "A calorie is a unit of energy. In nutrition, it measures the energy content in food and drinks. Your body uses calories for all activities, from breathing to exercising.",
        }
    
    def is_simple_question(self, query: str) -> bool:
        query_lower = query.lower().strip()
        
        simple_patterns = [
            query_lower.startswith("what is"),
            query_lower.startswith("what are"),
            query_lower.startswith("define"),
            query_lower.startswith("explain"),
        ]
        
        word_count = len(query_lower.split())
        return any(simple_patterns) and word_count < 15
    
    async def execute(self, query: str = "", chat_history: List[Dict] = None, **kwargs) -> ToolResult:
        query_normalized = query.lower().strip().rstrip('?').rstrip('.')
        
        if query_normalized in self.instant_knowledge:
            return ToolResult(
                success=True,
                data=self.instant_knowledge[query_normalized],
                metadata={"source": "instant_kb", "latency_ms": 0}
            )
        
        system_prompt = """You are a health education assistant. Provide brief, accurate answers to educational questions.

**Guidelines:**
- Keep answers to 2-3 sentences for simple questions
- Use clear, accessible language (avoid jargon)
- Focus on facts, not recommendations
- If the question requires personalization, suggest using specialized tools"""
        
        try:
            response = await self.llm_service.query(
                prompt=f"Question: {query}",
                system_prompt=system_prompt,
                chat_history=chat_history or [],
                max_tokens=150,
                temperature=0.3
            )
            
            return ToolResult(
                success=True,
                data=response,
                metadata={"source": "llm", "query_type": "educational"}
            )
        except Exception as e:
            logger.error(f"Error in KnowledgeBaseTool: {e}")
            return ToolResult(
                success=False,
                data="I'm having trouble answering that question. Could you rephrase it?",
                error=str(e)
            )



class FoodAnalyzerTool(BaseTool):
    
    def __init__(self, llm_service, data_service):
        super().__init__(
            "food_analyzer",
            "Analyzes food for ingredient safety, allergen detection, vegan verification"
        )
        self.llm_service = llm_service
        self.data_service = data_service
        
        self.allergen_derivatives = {
            'corn': ['cornstarch', 'corn syrup', 'high fructose corn syrup', 'corn oil', 
                     'maize', 'dextrose', 'maltodextrin', 'citric acid (corn-derived)'],
            'dairy': ['milk', 'whey', 'casein', 'lactose', 'butter', 'ghee', 'cream', 
                      'cheese', 'yogurt', 'buttermilk'],
            'gluten': ['wheat', 'barley', 'rye', 'malt', 'spelt', 'kamut', 'triticale', 
                       'wheat flour', 'semolina'],
            'soy': ['soy', 'soybean', 'soy lecithin', 'tofu', 'tempeh', 'miso', 
                    'soy sauce', 'edamame'],
            'nuts': ['almond', 'walnut', 'cashew', 'pecan', 'pistachio', 'macadamia',
                     'hazelnut', 'brazil nut', 'pine nut'],
        }
    
    async def execute(self, query: str = "", food_item: str = "", 
                     constraints: Dict = None, **kwargs) -> ToolResult:
        
        if not food_item:
            food_item = self._extract_food_from_query(query)
        
        if not food_item:
            return ToolResult(
                success=False,
                data="Could not identify the food item to analyze. Please specify the food.",
                error="missing_food_item"
            )
        
        allergies = constraints.get('allergies', []) if constraints else []
        restrictions = constraints.get('dietary_restrictions', []) if constraints else []
        
        system_prompt = ExpertPersonaLibrary.get_food_analyzer_expert().get_full_system_prompt(constraints)
        
        analysis_prompt = f"""Analyze the following food item for safety and compliance:

**Food Item:** {food_item}

**User Allergies:** {', '.join(allergies) if allergies else 'None'}
**Dietary Restrictions:** {', '.join(restrictions) if restrictions else 'None'}

**Analysis Required:**
1. **Ingredient Breakdown:** What are the main ingredients/components?
2. **Allergen Detection:** Does it contain or potentially contain any of the user's allergens (including derivatives)?
3. **Dietary Compliance:** Does it comply with the user's dietary restrictions?
4. **Safety Rating:** Safe / Caution / Unsafe (with reasoning)
5. **Recommendations:** Can the user consume this? Any precautions?

**FORMAT YOUR RESPONSE AS:**
```json
{{
    "food_item": "{food_item}",
    "ingredients": ["ingredient1", "ingredient2"],
    "allergen_check": {{
        "contains_allergens": true/false,
        "detected_allergens": ["allergen1"],
        "hidden_allergens": ["derivative1"]
    }},
    "dietary_compliance": {{
        "compliant": true/false,
        "violations": ["restriction1"]
    }},
    "safety_rating": "safe/caution/unsafe",
    "recommendation": "Brief recommendation text",
    "can_add_to_meal_plan": true/false
}}
```
"""
        
        persona_context = kwargs.get('persona_context')
        if persona_context:
            system_prompt += f"\n\n **ADAPTIVE PERSONA GUIDANCE:**\n{persona_context}"
            logger.info("✨ Applied smart persona injection to FoodAnalyzerTool system prompt")
        
        try:
            response = await self.llm_service.query(
                prompt=analysis_prompt,
                system_prompt=system_prompt,
                max_tokens=600,
                temperature=0.2
            )
            
            response_clean = response.strip()
            if response_clean.startswith("```json"):
                response_clean = response_clean[7:]
            if response_clean.startswith("```"):
                response_clean = response_clean[3:]
            if response_clean.endswith("```"):
                response_clean = response_clean[:-3]
            response_clean = response_clean.strip()
            
            analysis_result = json.loads(response_clean)
            
            user_message = self._format_analysis_response(analysis_result)
            
            return ToolResult(
                success=True,
                data=user_message,
                metadata={
                    "analysis": analysis_result,
                    "can_add_to_meal_plan": analysis_result.get("can_add_to_meal_plan", False),
                    "food_item": food_item
                }
            )
            
        except json.JSONDecodeError:
            return ToolResult(
                success=True,
                data=response,
                metadata={"food_item": food_item, "format": "raw"}
            )
        except Exception as e:
            logger.error(f"Error in FoodAnalyzerTool: {e}")
            return ToolResult(
                success=False,
                data=f"Error analyzing food item: {str(e)}",
                error=str(e)
            )
    
    def _extract_food_from_query(self, query: str) -> str:
        query_lower = query.lower()
        
        patterns = [
            r'is (.+?) safe',
            r'can i eat (.+?)(?:\?|$)',
            r'analyze (.+?)(?:\?|$)',
            r'check (.+?)(?:\?|$)',
            r'about (.+?)(?:\?|$)',
        ]
        
        import re
        for pattern in patterns:
            match = re.search(pattern, query_lower)
            if match:
                return match.group(1).strip()
        
        return ""
    
    def _format_analysis_response(self, analysis: Dict) -> str:
        food = analysis.get('food_item', 'this food')
        safety = analysis.get('safety_rating', 'unknown').upper()
        recommendation = analysis.get('recommendation', '')
        
        emoji_map = {
            'safe': '✅',
            'caution': '⚠️',
            'unsafe': '🚫'
        }
        emoji = emoji_map.get(safety.lower(), '❓')
        
        message = f"{emoji} **{food.title()} - {safety}**\n\n"
        
        allergen_check = analysis.get('allergen_check', {})
        if allergen_check.get('contains_allergens'):
            detected = allergen_check.get('detected_allergens', [])
            hidden = allergen_check.get('hidden_allergens', [])
            message += f" **Allergen Alert:**\n"
            if detected:
                message += f"- Contains: {', '.join(detected)}\n"
            if hidden:
                message += f"- Hidden allergens: {', '.join(hidden)}\n"
            message += "\n"
        
        dietary_compliance = analysis.get('dietary_compliance', {})
        if not dietary_compliance.get('compliant', True):
            violations = dietary_compliance.get('violations', [])
            message += f" **Dietary Restriction Violation:**\n- {', '.join(violations)}\n\n"
        
        message += f"**Recommendation:** {recommendation}"
        
        return message


class HealthOrchestrator:

    
    def __init__(self, query_classifier_tool: BaseTool, tools: Dict[str, BaseTool],
                 llm_service, data_service=None):
        self.query_classifier = query_classifier_tool
        self.tools = tools
        self.llm_service = llm_service
        self.data_service = data_service
        
        self.medical_disclaimer_tool = MedicalDisclaimerTool()
        self.knowledge_base_tool = KnowledgeBaseTool(llm_service)
        self.food_analyzer_tool = FoodAnalyzerTool(llm_service, data_service) if data_service else None
        
        self.shared_context = {}
        
        logger.info("HealthOrchestrator initialized with multi-domain support")
    
    def detect_domain(self, query: str, classified_tool: str) -> Tuple[DomainType, bool]:
        
        query_lower = query.lower()
        
        if self.medical_disclaimer_tool.should_trigger(query):
            return DomainType.MEDICAL_DISCLAIMER_REQUIRED, False
        
        tool_domain_map = {
            'meal_plan_generator': DomainType.NUTRITION,
            'meal_plan_adjuster': DomainType.NUTRITION,
            'nutrition_analyzer': DomainType.NUTRITION,
            'weekly_meal_plan_generator': DomainType.NUTRITION,
            'special_meal_plan_generator': DomainType.NUTRITION,
            'craving_assistant': DomainType.NUTRITION,
            
            'fitness_plan_generator': DomainType.FITNESS,
            'workout_adjuster_tool': DomainType.FITNESS,
            'fitness_profile_updater': DomainType.FITNESS,
            
            'disease_advisor': DomainType.WELLNESS,
            'vital_advisor': DomainType.WELLNESS,
            'panel_query': DomainType.WELLNESS,
            'blood_report_analyzer': DomainType.WELLNESS,
            
            'food_analyzer': DomainType.FOOD_ANALYSIS,
            'meal_ingredients': DomainType.FOOD_ANALYSIS,
            
            'general_query': DomainType.GENERAL,
            'greeting': DomainType.GENERAL,
        }
        
        primary_domain = tool_domain_map.get(classified_tool, DomainType.GENERAL)
        
        is_hybrid = self._is_hybrid_query(query_lower, primary_domain)
        
        return primary_domain, is_hybrid
    
    def _is_hybrid_query(self, query_lower: str, primary_domain: DomainType) -> bool:
        domain_indicators = {
            DomainType.NUTRITION: ['meal', 'food', 'diet', 'nutrition', 'calories', 'macros'],
            DomainType.FITNESS: ['workout', 'exercise', 'fitness', 'training', 'gym', 'muscle'],
            DomainType.WELLNESS: ['pcos', 'acid reflux', 'sleep', 'energy', 'stress', 'wellness'],
            DomainType.FOOD_ANALYSIS: ['safe', 'allergen', 'ingredient', 'vegan', 'contains'],
        }
        
        detected_domains = []
        for domain, indicators in domain_indicators.items():
            if any(indicator in query_lower for indicator in indicators):
                detected_domains.append(domain)
        
        return len(detected_domains) > 1
    
    def get_expert_system_prompt(self, domain: DomainType, constraints: Optional[Dict] = None) -> str:
        persona = ExpertPersonaLibrary.get_persona_by_domain(domain)
        return persona.get_full_system_prompt(constraints)
    
    async def route_query(self, query: str, chat_history: List[Dict] = None,
                         constraints: Dict = None, **kwargs) -> Tuple[str, DomainType, Dict]:

        if self.medical_disclaimer_tool.should_trigger(query):
            return "medical_disclaimer", DomainType.MEDICAL_DISCLAIMER_REQUIRED, {}
        
        if self.knowledge_base_tool.is_simple_question(query):
            return "knowledge_base", DomainType.GENERAL, {"fast_path": True}
        
        classification_result = await self.query_classifier.execute(
            query=query,
            chat_history=chat_history,
            profile_summary_json=constraints,
            **kwargs
        )
        
        if not classification_result.success:
            return "general_query", DomainType.GENERAL, {}
        
        classified_tool = classification_result.data
        
        domain, is_hybrid = self.detect_domain(query, classified_tool)
        
        metadata = {
            "domain": domain.value,
            "is_hybrid": is_hybrid,
            "classified_tool": classified_tool
        }
        
        logger.info(f"[Orchestrator] Query routed: tool={classified_tool}, domain={domain.value}, hybrid={is_hybrid}")
        
        return classified_tool, domain, metadata
    
    async def execute_with_context_sharing(self, classified_tool: str, domain: DomainType,
                                          query: str, constraints: Dict = None, **kwargs) -> ToolResult:

        if classified_tool == "medical_disclaimer":
            return await self.medical_disclaimer_tool.execute(query=query)
        
        if classified_tool == "knowledge_base":
            result = await self.knowledge_base_tool.execute(query=query, **kwargs)
            return result
        
        if classified_tool == "food_analyzer" and self.food_analyzer_tool:
            result = await self.food_analyzer_tool.execute(
                query=query, 
                constraints=constraints,
                **kwargs
            )
            
            if result.success and result.metadata:
                self.shared_context['last_food_analysis'] = result.metadata
                
                if result.metadata.get('can_add_to_meal_plan'):
                    food_item = result.metadata.get('food_item', 'this food')
                    result.data += f"\n\n **This food is safe for you!**\nWould you like me to add {food_item} to your meal plan?"
            
            return result
        
        tool = self.tools.get(classified_tool)
        if not tool:
            return ToolResult(
                success=False,
                data=f"Tool '{classified_tool}' not found",
                error="tool_not_found"
            )
        
        if hasattr(tool, 'llm_service'):
            expert_prompt = self.get_expert_system_prompt(domain, constraints)
            kwargs['expert_system_prompt'] = expert_prompt
        
        result = await tool.execute(query=query, constraints=constraints, **kwargs)
        
        self.shared_context[f'last_{classified_tool}_result'] = result
        
        return result
    
    def get_shared_context(self, context_key: str) -> Optional[Dict]:
        return self.shared_context.get(context_key)
    
    def clear_shared_context(self):
        self.shared_context = {}
