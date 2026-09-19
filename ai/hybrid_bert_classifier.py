import re
import json
import torch
from pathlib import Path
from typing import Tuple, Optional
from transformers import DistilBertTokenizer, DistilBertForSequenceClassification
import time


class HybridBERTClassifier:
    
    def __init__(self, model_path: str = './bert_query_classifier_finetuned'):
        print("\n[Hybrid BERT Classifier] Initializing...")
        
        print("    Detecting available hardware...")
        if torch.cuda.is_available():
            self.device = torch.device('cuda')
            gpu_name = torch.cuda.get_device_name(0)
            gpu_memory = torch.cuda.get_device_properties(0).total_memory / (1024**3)  
            print(f"    GPU detected: {gpu_name} ({gpu_memory:.1f} GB)")
            print(f"    Using GPU acceleration for inference")
        else:
            self.device = torch.device('cpu')
            cpu_count = torch.get_num_threads()
            print(f"    No GPU detected - using CPU")
            print(f"     CPU threads available: {cpu_count}")
            print(f"    Performance: ~20-50ms per query (still 10x faster than LLM)")
            
            if hasattr(torch, 'set_num_threads'):
                torch.set_num_threads(min(cpu_count, 4)) 
                print(f"     CPU optimization: Using {min(cpu_count, 4)} threads")
        
        print(f"    Loading fine-tuned BERT model from {model_path}...")
        self.tokenizer = DistilBertTokenizer.from_pretrained(model_path)
        self.model = DistilBertForSequenceClassification.from_pretrained(model_path)
        self.model.to(self.device)
        self.model.eval()
        
        if self.device.type == 'cpu':
            for param in self.model.parameters():
                param.requires_grad = False
            print(f"     CPU optimization: Disabled gradient computation")
        
        label_map_path = Path(model_path) / 'label_mapping.json'
        with open(label_map_path, 'r') as f:
            label_data = json.load(f)
            if 'id2label' in label_data:
                self.id2label = {int(k): v for k, v in label_data['id2label'].items()}
            else:
                self.id2label = {int(k): v for k, v in label_data.items()}
        
        print(f"   Model loaded successfully on {self.device}")
        print(f"   Supporting {len(self.id2label)} intent classes")
        
        self._init_rule_patterns()
        
    def _init_rule_patterns(self):
        
        self.profile_update_patterns = [
            r'\b(no|nope|actually|correction|its?)\s+(is\s+)?\d+',
            r'\bupdate\s+(my\s+)?(weight|height|age|name)',
            r'\bchange\s+(my\s+)?(weight|height|age|name)',
            r'\bset\s+(my\s+)?(weight|height|age|name)',
            r'^(no|nope|actually|its)\s', 
            r'\bmeant\s+\d+',  
            r'\bshould\s+be\s+\d+',  
            r'^actually\s+',  
        ]
        
        self.meal_ingredients_patterns = [
            r'\b(what|show|tell|list)\s+(is\s+in|are\s+the|me\s+the)\s+(ingredients?|recipe|composition)',
            r'\bfood\s+(composition|ingredients?|recipe)',
            r'\brecipe\s+details?',
            r'\bingredients?\s+(of|in|for)\s+',
            r'\bwhat\s+is\s+in\s+(this|that|the)\s+(meal|food|dish)',
            r'\bshow\s+(food|meal)\s+(composition|ingredients?)',
            r'\bfood\s+components?',
            r'\b(dish|meal)\s+components?',
            r'\bmade\s+of\s*$',  
            r'\bwhat\s+is\s+(this\s+)?made\s+of',
            r'\bwhat\s+is\s+added\s+in',
            r'\bwhat\s+is\s+included\s*$', 
            r'\bwhat\s+does\s+(this|it)\s+contain', 
            r'\bwhat\s+is\s+(this|it)\s+made\s+(of|from)',
        ]
        
        self.grocery_list_patterns = [
            r'\bgrocery\s+(list|shopping)',
            r'\bshopping\s+list',
            r'\bwhat\s+to\s+buy',
            r'\bbuy\s+(list|groceries)',
            r'\bpurchase\s+list',
            r'\bshopping\s+list\s+generat',  
            r'\bgrocery\s+generat', 
        ]
        
        self.general_query_patterns = [
            r'\brandom\s+(question|query)',
            r'\bgeneral\s+(question|query|info)',
            r'\btell\s+me\s+about\s+nouriqai',
            r'\bwhat\s+(is|does)\s+nouriqai',
            r'\bhelp\s+with\s+something',
            r'\bi\s+have\s+a\s+question',
            r'\bquestion\s+about',
            r'\bhelp\s+me\s+with',
            r'\bhow\s+does\s+.+\s+work', 
            r'\bshow\s+me\s+around', 
            r'\bwhat\s+do\s+you\s+know\s+about',  
        ]
        
        self.meal_plan_adjuster_patterns = [
            r'\b(other|different|alternative)\s+(suggestion|option|meal|food|choice)',
            r'\bchange\s+(the\s+)?(suggestion|meal|food)',
            r'\badjust\s+(my\s+)?(meal|diet)\s+plan',
            r'\bmodify\s+(my\s+)?(meal|diet)',
        ]
        self.workout_adjuster_patterns = [
            r'\b(change|modify|update|adjust)\s+(my\s+)?(gym|workout|training|exercise)\s+(plan|routine)',
            r'\b(gym|workout|training)\s+(plan|routine)\s+(change|modification|update)',
            r'\badjust\s+(training|workout|exercise)',
            r'\bupdate\s+(training|workout)\s+plan',
        ]
        
        self.vital_advisor_patterns = [
            r'\b(blood\s+)?pressure\s+(advice|target|level|reading|normal|range|high|low)',
            r'\b(bp|blood\s+pressure)\s+(advice|target|level|reading)',
            r'\bhypertension\s+target',
            r'\bpressure\s+target',
            r'\bheart\s+rate\s+(advice|target|level|normal|range)',
            r'\bpulse\s+rate',
            r'\bresting\s+heart\s+rate',
            r'\bsugar\s+level',
            r'\bglucose\s+level',
            r'\bblood\s+sugar\s+(advice|target|control|management)',
            r'\boxygen\s+level',
            r'\bo2\s+level',
            r'\bsaturation\s+level',
            r'\bvital\s+(signs?|advice|target)',
            r'\bvitals?\s+(advice|target)',
            r'\b(bp|hr|spo2|rr)\s+target',
        ]
        
        self.history_retriever_keywords = [
            r'\b(show|display|view|see|check)\s+(my|the)?\s*(meal|diet|food)\s+plan',
            r'\b(retrieve|get|fetch)\s+(my|the)?\s*(meal|diet|food)\s+plan',
            r'\bwhat\s+(is|are)\s+(my|the)\s+(today\'?s?\s+|current\s+|daily\s+)?(meal|diet|food)\s+plan',
            r'\bwhat\s+(did|have)\s+you\s+(create|generate|make)',
            r'\bshow.*plan.*saved',
            r'\bmy\s+(current|existing|saved|today\'?s?)\s+(meal|diet)\s+plan',
        ]
        
        self.new_meal_plan_generation_keywords = [
            r'\b(create|generate|make|build|design)\s+(a\s+)?new\s+(meal|diet)\s+plan',
            r'\bnew\s+(meal|diet)\s+plan',
            r'\b(create|generate|make)\s+(me\s+)?(a\s+)?(fresh|brand\s+new)\s+(meal|diet)',
            r'\b(give|provide|show)\s+(me\s+)?(a\s+)?new\s+(meal|diet)\s+plan',
            r'\bstart\s+(a\s+)?new\s+(meal|diet)\s+plan',
            r'\bi\s+want\s+(a\s+)?new\s+(meal|diet)\s+plan',
        ]
        
        self.general_query_educational = [
            r'\b(what\s+(is|are)|explain|define)\s+(protein|carb(ohydrate)?s?|fat(s)?|fiber|calorie(s)?|bmr|tdee|macros?|micronutrients?)',
            r'\b(what\s+(is|are)|explain|define)\s+(vitamin(s)?|mineral(s)?|omega|amino\s+acid|electrolytes?)',
            r'\b(what\s+(is|are)|explain|define)\s+(metabolism|ketosis|digestion|glycemic|insulin|absorption|enzyme(s)?)',
            r'\b(what\s+(is|are)|explain|define)\s+(antioxidants?|inflammation|bmi|body\s+mass|weight\s+loss|muscle\s+gain)',
            r'\b(what\s+(is|are)|explain|define)\s+(supplements?|probiotics?|collagen|creatine|nutrients?)',
            r'\b(difference|compare)\s+between\s+(protein|carbs?|fats?)\s+and\s+',
            r'\bgood\s+(carbs?|fats?|protein)\s+vs\s+bad',
            r'\bhealthy\s+(carbs?|fats?)\s+vs\s+(unhealthy|bad)',
            r'\bsimple\s+vs\s+complex\s+carbs?',
            r'\bcomplete\s+vs\s+incomplete\s+protein',
            r'\bsaturated\s+vs\s+unsaturated\s+fats?',
            r'\b(importance|role|benefits?)\s+of\s+(protein|carbs?|fats?|fiber|vitamins?)',
            r'\bnatural\s+vs\s+(processed|artificial)',
            r'\borganic\s+vs\s+conventional',
            r'\bplant\s+(protein|based)\s+vs\s+animal',
            r'\bhow\s+(does|do)\s+(protein|carbs?|fats?)\s+(work|affect)',
            r'\bwhy\s+(do|should)\s+(i|we)\s+(need|eat)\s+(protein|vitamins?|fiber)',
            r'\bwhat\s+(are|is)\s+(the\s+)?best\s+(protein|carb|fat)\s+sources',
        ]
        
        self.blood_report_query_educational = [
            r'\b(what\s+is|what.s)\s+(normal|ideal|healthy|good|optimal)\s+(range|level|value)',
            r'\b(normal|ideal|healthy|optimal)\s+(range|level|value)\s+(for|of)',
            r'\b(normal|ideal|healthy)\s+(cholesterol|sugar|hemoglobin|blood\s+pressure|hba1c)',
            r'\b(hemoglobin|cholesterol|sugar|hba1c|ldl|hdl)\s+normal\s+range',
            r'\bwhat\s+should\s+(my\s+)?\b(cholesterol|sugar|bp|blood\s+pressure|hemoglobin|hba1c)\s+be',
            r'\b(target|reference)\s+(range|value|level)',
            r'\bis\s+(this|my|[0-9]+)\s+(high|low|normal|elevated)',
            r'\b(above|below)\s+normal\s+(means?|indicates?)',
            r'\bwhat\s+(if|when)\s+(cholesterol|sugar|hemoglobin)\s+(is\s+)?[0-9]',
            r'\bwhat\s+is\s+good\s+(cholesterol|sugar|bp|hemoglobin)',
            r'\bideal\s+(blood\s+)?(sugar|cholesterol|pressure|hemoglobin)',
            r'\bwhat\s+level\s+is\s+(normal|healthy|good)',
        ]

        
        self.vital_advisor_immediate = [
            r'\bmy\s+(blood\s+pressure|bp|heart\s+rate|pulse)\s+is\s+[0-9]',
            r'\b(blood\s+pressure|sugar|glucose)\s+(level|reading)\s+is\s+[0-9]',
            r'\b(bp|hr|sugar)\s+reading\s+(high|low|[0-9])',
            r'\b(cholesterol|triglycerides?)\s+is\s+(high|elevated|low)', 
            r'\b(advice|help|what\s+to\s+do)\s+(on|for|with)\s+high\s+(bp|blood\s+pressure)',
        ]
        
        self.weekly_meal_plan_patterns = [
            r'\b(7|seven)\s+day',
            r'\bweek(ly)?\s+(meal\s+)?plan',
            r'\bplan\s+for\s+(the\s+)?week',
            r'\bprovide\s+a\s+7\s+day',
        ]
        
        self.special_meal_plan_patterns = [
            r'\b(meal|diet)\s+plan\s+for\s+(pregnancy|diabetes|diabetic|hypertension|heart\s+disease)',
            r'\bplan\s+for\s+(pregnancy|pregnant)',
            r'\b(pregnancy|pregnant)\s+(meal|diet)',
            r'\b(gestational|diabetic|cardiac)\s+(meal|diet)',
        ]
        
        self.disease_advisor_patterns = [
            r'\b(advice|tips|management|guidance|help|recommendations?)\s+(for|with)?\s*(diabetes|diabetic|hypertension|heart|disease)',
            r'\b(diabetes|diabetic|hypertension|heart\s+disease)\s+(advice|tips|management|guidance)',
            r'\bcontrol(ling)?\s+(diabetes|hypertension|heart)',
            r'\bmanaging\s+(diabetes|hypertension|heart)',
            r'\b(diabetes|hypertension|heart)\s+control',
        ]
        
        self.special_meal_plan_exclusive = [
            r'\bmeal\s+plan\s+for\s+(pregnancy|hypertension|diabetes)',
            r'\bdiet\s+plan\s+for\s+(pregnancy|hypertension|diabetes)',
            r'\bplan\s+for\s+(pregnant|diabetic)',
        ]
        
        self.blood_report_query_patterns = [
            r'\bwhat\s+is\s+my\s+(hba1c|glucose|cholesterol|hemoglobin)',
            r'\bshow\s+(my\s+)?(blood\s+)?(test\s+)?results?',
            r'\bmy\s+(blood\s+)?(sugar|glucose)\s+levels?',
        ]
        
        self.blood_report_analyzer_patterns = [
            r'\banalyz[eə]\s+(my\s+)?blood',
            r'\bblood\s+(report|work)\s+analysis',
            r'\binterpret\s+(my\s+)?blood',
            r'\bblood\s+work\s+analysis',  
        ]
        
        self.profile_retriever_patterns = [
            r'\bwhat\s+(are|is)\s+my\s+(allergies|dietary\s+restrictions|preferences|diet)',
            r'\bshow\s+my\s+(profile|info|details|allergies)',
            r'\bmy\s+(allergy|allergies|restrictions)',
            r'\bwhat\s+do\s+you\s+know\s+about\s+me',
        ]
        
        self.history_retriever_patterns = [
            r'\beating\s+(log|history)',
            r'\bmeal\s+(log|history)',
            r'\bfood\s+(log|history)',
            r'\bpast\s+(meals|foods)',
            r'\bprevious\s+(meals|foods)',
        ]
        
        
        self.goal_updater_keywords = [
            r'\bchange\s+(my\s+)?goal\s+to\b',
            r'\bupdate\s+(my\s+)?goal\s+to\b',
            r'\bswitch\s+(my\s+)?goal\s+to\b',
            r'\bset\s+(my\s+)?goal\s+to\b',
            r'\b(my\s+)?goal\s+is\s+(now\s+)?(bulking|cutting|maintenance|weight\s+loss|muscle\s+gain)',
            r'\bi\s+want\s+to\s+(bulk|cut|gain\s+muscle|maintain)\b',
            r'\bgoal:\s*(bulking|cutting|weight\s+loss)',
            r'\bmy\s+new\s+goal\s+is\b',
            r'\bi\s+am\s+(bulking|cutting)\b',
        ]
        
        # Educational weight loss queries (should go to general_query, NOT goal_updater)
        self.weight_loss_educational = [
            r'\bhow\s+to\s+(lose|gain)\s+weight\b',
            r'\bwhat\s+is\s+weight\s+(loss|gain)\b',
            r'\btips\s+for\s+(losing|gaining)\s+weight\b',
            r'\badvice\s+(on|for)\s+weight\s+(loss|gain)\b',
            r'\bways\s+to\s+(lose|gain)\s+weight\b',
            r'\bhelp\s+(me\s+)?(lose|gain)\s+weight\b',
            r'\bcan\s+you\s+help\s+(me\s+)?(lose|gain)\s+weight\b',
        ]
        
        # Educational medical term queries (should go to general_query, NOT blood_report_analyzer)
        self.medical_educational_patterns = [
            r'\bwhat\s+(is|are)\s+(cholesterol|triglycerides?|glucose|hemoglobin|ldl|hdl|vldl)\b',
            r'\bdefine\s+(cholesterol|triglycerides?|glucose|hemoglobin)\b',
            r'\bexplain\s+(cholesterol|triglycerides?|glucose|hemoglobin)\b',
            r'\btell\s+me\s+about\s+(cholesterol|triglycerides?|glucose|hemoglobin)\b',
            r'\b(cholesterol|triglycerides?|glucose|hemoglobin)\s+meaning\b',
            r'\bwhat\s+does\s+(cholesterol|triglycerides?|glucose|hemoglobin)\s+mean\b',
        ]
        
        self.special_meal_plan_disease_keywords = [
            r'\b(create|generate|make|give)\s+(me\s+)?(a\s+)?(meal\s+)?plan\s+for\s+(my\s+)?(diabetes|hypertension|cholesterol|thyroid|pcos|kidney|heart)',
            r'\b(meal\s+|diet\s+)?plan\s+for\s+(high\s+)?(blood\s+pressure|bp|sugar|cholesterol|hypertension)',
            r'\b(diabetic|hypertensive)\s+meal\s+plan\b',
            r'\bdiet\s+plan\s+for\s+(diabetes|hypertension|cholesterol)',
            r'\bi\s+have\s+(diabetes|hypertension|high\s+cholesterol)\s+.*\s+(meal|diet)\s+plan',
        ]
        
        self.grocery_list_keywords = [
            r'\bgrocery\s+list\b',
            r'\bshopping\s+list\b',
            r'\bwhat\s+to\s+buy\b',
            r'\bingredients\s+to\s+buy\b',
            r'\bingredients\s+needed\b',  
            r'\blist\s+of\s+groceries\b',
            r'\bshopping\s+items\b',
        ]
        
        self.meal_ingredients_keywords = [
            r'\bingredients\s+(in|of)\b',  
            r'\bwhat\'s\s+in\s+(this|the|my)\s+(meal|food)',
            r'\bcontains\s+what\b',
            r'\bshow\s+(me\s+)?ingredients\b',
            r'\brecipe\s+for\b',
            r'\bhow\s+to\s+make\b',
        ]
        
        self.history_retriever_enhanced_keywords = [
            r'\bshow\s+(me\s+)?my\s+(today\'?s?\s+|current\s+|daily\s+)?(meal\s+)?plan\b',
            r'\bdisplay\s+(my\s+)?(meal\s+)?plan\b',
            r'\bview\s+(my\s+)?(meal\s+)?plan\b',
            r'\bsee\s+(my\s+)?(meal\s+)?plan\b',
            r'\bwhat\s+is\s+my\s+(today\'?s?\s+|current\s+|daily\s+)?(meal\s+)?plan\b',
            r'\bwhat\s+did\s+you\s+(create|generate|make)\b',
            r'\bretrieve\s+(my\s+)?plan\b',
            r'\bget\s+(my\s+)?meal\s+plan\b',
            r'\bcheck\s+my\s+meal\s+plan\b',
            r'\bmy\s+today\'?s?\s+(meal|diet)\s+plan\b',
        ]
        
        self.blood_report_query_normal_ranges = [
            r'\bnormal\s+(range|level|value)\s+(for|of)\s+(cholesterol|hemoglobin|sugar|glucose|hba1c|ldl|hdl)',
            r'\bwhat\s+(is|should\s+be)\s+(normal|good|ideal)\s+(cholesterol|hemoglobin|sugar)',
            r'\b(cholesterol|hemoglobin|sugar|glucose)\s+normal\s+(range|level)',
            r'\bis\s+\d+\s+(cholesterol|sugar|glucose|hemoglobin)\s+(high|low|normal)',
            r'\bideal\s+(cholesterol|blood\s+pressure|sugar)\s+(level|value)',
        ]
        
        self.special_meal_plan_exclusive_patterns = [
            r'\bmuscle\s+gain\s+(diet|meal|plan)',
            r'\blow\s+sodium\s+(diet|meal|plan)',
            r'\bhigh\s+protein\s+(diet|meal|plan)',
            r'\bweight\s+loss\s+(diet|meal|plan)',
            r'\bketo\s+(diet|meal|plan)',
        ]
        
        self.greeting_patterns = [
            r'^(hi|hello|hey|good\s+(morning|afternoon|evening))[\s\!]*$',
            r'^(hi|hello|hey)\s+\w+[\s\!]*$',  # "Hi Friska", "Hello there"
            r'^(hi|hello|hey)\s+\w+\s+(how\s+are\s+you|what\'?s?\s+up)[\s\!\?]*$',  # "Hi Friska how are you"
            r'^how\s+are\s+you[\s\!\?]*$',  # "How are you?"
            r'^how\s+are\s+you\s+(doing|today|feeling)[\s\!\?]*$',
            r'^how\s+do\s+you\s+do[\s\!\?]*$',
            r'^thanks?[\s\!]*$',
            r'^(yes|yeah|yep|yup|ok|okay)[\s\!]*$',
            r'^no\s+thanks?[\s\!]*$', 
            r'^\bbye\b',
            r'\bmakes\s+sense\b',
        ]
        
        # Patterns for bot identity / capability queries → route to general_query
        self.bot_identity_patterns = [
            r'^how\s+old\s+are\s+you[\s\!\?]*$',
            r'^what\s+is\s+your\s+(name|age)[\s\!\?]*$',
            r'^who\s+are\s+you[\s\!\?]*$',
            r'^what\s+are\s+you[\s\!\?]*$',
            r'^what\s+can\s+you\s+do[\s\!\?]*$',
            r'^can\s+you\s+help\s+me[\s\!\?]*$',
            r'^what\s+do\s+you\s+do[\s\!\?]*$',
            r'^tell\s+me\s+about\s+yourself[\s\!\?]*$',
            r'^who\s+made\s+you[\s\!\?]*$',
            r'^who\s+created\s+you[\s\!\?]*$',
            r'^what\s+are\s+your\s+capabilities[\s\!\?]*$',
            r'^how\s+can\s+you\s+help[\s\!\?]*$',
        ]
        
        # Scope violation patterns - queries completely outside health/nutrition/fitness
        self.scope_violation_patterns = [
            r'\b(aws|azure|docker|kubernetes|terraform|jenkins|devops)\b',
            r'\b(python|javascript|java|c\+\+|golang|rust|typescript)\s+(code|program|script|function)',
            r'\b(write|create|generate)\s+(an?\s+)?(essay|story|poem|article|blog|script)',
            r'\b(movie|film|tv\s+show|series|anime)\s+(recommend|suggest)',
            r'\brecommend\s+(a\s+)?(movie|film|tv\s+show|book|game|song)',
            r'\b(capital|president|prime\s+minister)\s+of\s+',
            r'\b(stock|crypto|bitcoin|forex|investment|trading)\s+(advice|tip|predict)',
            r'\b(solve|calculate)\s+.*(equation|integral|derivative|algebra)',
            r'\b(travel|tourist|tourism)\s+(guide|essay|plan|itinerary)',
            r'\b(communication|leadership|management)\s+skills?\b',
            r'\b(action|horror|comedy|romantic)\s+movie',
            r'\bessay\s+(on|about)\b',
            r'\b(debug|fix|compile|deploy)\s+(this|my|the)\s+(code|app|server|script)',
        ]
        
        self.profile_update_exclusions = [
            r'^no\s+thanks?$',  
        ]
        
        self.CONF_THRESHOLD_HIGH = 0.95 
        self.CONF_THRESHOLD_MEDIUM = 0.80 
        self.CONF_THRESHOLD_LOW = 0.60  
    
    def _apply_rules(self, query: str, bert_intent: str, bert_confidence: float) -> Tuple[str, str]:
       
        query_lower = query.lower().strip()
        
        for exclusion in self.profile_update_exclusions:
            if re.match(exclusion, query_lower):
                return bert_intent, 'bert_only'
        
        # Fix: "meal plan" / "diet plan" queries misclassified as fitness_plan_generator
        if bert_intent == 'fitness_plan_generator':
            if re.search(r'\b(meal|diet)\s+plan\b', query_lower):
                if not re.search(r'\b(workout|exercise|training|gym)\s+plan\b', query_lower):
                    return 'meal_plan_generator', 'meal_plan_not_fitness'
        
        general_educational_match = any(re.search(p, query_lower) for p in self.general_query_educational)
        if general_educational_match:
            return 'general_query', 'educational_query_override'
        
        # Check for medical educational queries (e.g., "What is cholesterol?")
        medical_educational_match = any(re.search(p, query_lower) for p in self.medical_educational_patterns)
        if medical_educational_match:
            if bert_intent in ['blood_report_analyzer', 'blood_report_query', 'panel_query']:
                return 'general_query', 'medical_educational_override'
        
        if re.search(r'\b(what\s+(is|are)|explain|define)\s+\w+', query_lower):
            if not re.search(r'\b(in|this|my|the)\s+(banana|apple|rice|chicken|egg|fish|food|meal|dish)', query_lower):
                if bert_intent in ['nutrition_analyzer', 'blood_report_analyzer', 'meal_ingredients']:
                    if bert_confidence < 0.95: 
                        return 'general_query', 'educational_catchall_override'
        
        blood_query_educational_match = any(re.search(p, query_lower) for p in self.blood_report_query_educational)
        if blood_query_educational_match:

            return 'blood_report_query', 'blood_query_educational_override'
        
        if re.search(r'\bis\s+[0-9]+\s+(cholesterol|sugar|hemoglobin|bp|blood\s+pressure)\s+(high|low|normal)', query_lower):
            return 'blood_report_query', 'blood_value_query_override'
        
        vital_immediate_match = any(re.search(p, query_lower) for p in self.vital_advisor_immediate)
        if vital_immediate_match:
            return 'vital_advisor', 'vital_immediate_override'
        
        if not blood_query_educational_match:
            if re.search(r'\b(my\s+)?(blood\s+pressure|bp|sugar|glucose|cholesterol|heart\s+rate|pulse).{0,10}\b(is|reading|level).{0,10}[0-9]', query_lower):
                return 'vital_advisor', 'vital_reading_with_number'
        
        if not blood_query_educational_match:
            if re.search(r'\b(advice|help|what\s+to\s+do)\s+.{0,15}\b(high|low|elevated)\s+(bp|blood\s+pressure|sugar|cholesterol)', query_lower):
                return 'vital_advisor', 'vital_advice_request'
        

        if re.search(r'\b(analyze|interpret|review|read|check)\s+.{0,15}\b(blood|report|test|lab|results?)', query_lower):
            if not blood_query_educational_match: 
                return 'blood_report_analyzer', 'blood_analyzer_action_keyword'
        
        if bert_intent == 'blood_report_analyzer':
            if re.search(r'\b(analyze|interpret|review|read|check)\s+(my\s+)?blood\s+(report|test|work|results)', query_lower):
                if not blood_query_educational_match:
                    return 'blood_report_analyzer', 'blood_analyzer_action_override'
            else:

                if blood_query_educational_match:
                    return 'blood_report_query', 'blood_query_correction'
                elif re.search(r'\bcheck\s+(my\s+)?(cholesterol|sugar|hemoglobin|test)', query_lower):
                    return 'blood_report_query', 'blood_check_correction'
        
        for pattern in self.profile_update_patterns:
            if re.search(pattern, query_lower, re.IGNORECASE):
                if len(query_lower.split()) <= 5 or bert_intent in ['greeting', 'vital_advisor', 'general_query']:
                    return 'profile_updater', 'profile_update_correction'
        
        if re.search(r'\b(update|change|set|my)\s+activity\s+level', query_lower):
            return 'profile_updater', 'profile_activity_update'
        
        if re.search(r'^i\s+am\s+(male|female|man|woman)', query_lower):
            return 'profile_updater', 'profile_identity_statement'
        
        history_retrieval_match = any(re.search(p, query_lower) for p in self.history_retriever_keywords)
        if history_retrieval_match:
            if bert_intent in ['meal_plan_generator', 'weekly_meal_plan_generator', 'special_meal_plan_generator', 'greeting', 'general_query', 'profile_retriever']:
                return 'history_retriever', 'history_retrieval_keyword'
        

        new_plan_match = any(re.search(p, query_lower) for p in self.new_meal_plan_generation_keywords)
        if new_plan_match:
            if bert_intent in ['meal_plan_adjuster', 'goal_updater', 'greeting', 'general_query']:
                return 'meal_plan_generator', 'new_meal_plan_keyword_override'
        
        grocery_match = any(re.search(p, query_lower) for p in self.grocery_list_patterns)
        if grocery_match:
            return 'grocery_list', 'grocery_list_keyword'
        
        ingredients_match = any(re.search(p, query_lower) for p in self.meal_ingredients_patterns)
        if ingredients_match:

            if re.search(r'\b(contain|included|ingredients?|made\s+of)\b', query_lower):

                if bert_intent in ['general_query', 'grocery_list', 'meal_plan_generator', 'greeting']:
                    return 'meal_ingredients', 'meal_ingredients_strong'
            
            if 'made' in query_lower or 'added' in query_lower or 'included' in query_lower or 'contain' in query_lower:
                if any(word in query_lower for word in ['this', 'dish', 'meal', 'food', 'recipe', 'it']):
                    return 'meal_ingredients', 'meal_ingredients_contextual'
                if bert_intent == 'general_query':
                    return 'meal_ingredients', 'meal_ingredients_low_confidence'
            elif bert_intent in ['history_retriever', 'grocery_list', 'meal_plan_generator', 'general_query']:
                return 'meal_ingredients', 'meal_ingredients_pattern'
        
        if bert_intent in ['greeting', 'meal_plan_generator', 'profile_retriever'] or bert_confidence < self.CONF_THRESHOLD_MEDIUM:
            for pattern in self.general_query_patterns:
                if re.search(pattern, query_lower):
                    if bert_confidence < self.CONF_THRESHOLD_HIGH:
                        return 'general_query', 'general_query_pattern'
        
        # --- SCOPE VIOLATION CHECK (highest priority) ---
        scope_violation_match = any(re.search(p, query_lower) for p in self.scope_violation_patterns)
        if scope_violation_match:
            return 'general_query', 'scope_violation_redirect'
        
        # --- BOT IDENTITY / CAPABILITY QUERIES ---
        bot_identity_match = any(re.search(p, query_lower) for p in self.bot_identity_patterns)
        if bot_identity_match:
            return 'general_query', 'bot_identity_query'
        
        # --- GREETING MISCLASSIFICATION FIX ---
        # Catch greetings that BERT misclassifies as fitness_plan_generator or other tools
        if bert_intent in ['fitness_plan_generator', 'meal_plan_generator', 'disease_advisor', 'vital_advisor']:
            greeting_override = any(re.search(p, query_lower) for p in self.greeting_patterns)
            if greeting_override:
                return 'greeting', 'greeting_misclass_fix'
        
        if bert_intent == 'greeting':
            if re.search(r'\bhelp\s+(please|me)\b', query_lower):
                return 'general_query', 'help_request'
            if re.search(r'\btell\s+me\s+something', query_lower):
                return 'general_query', 'tell_request'
        
        for pattern in self.workout_adjuster_patterns:
            if re.search(pattern, query_lower):
                if bert_intent in ['goal_updater', 'meal_plan_adjuster']:
                    return 'workout_plan_adjuster', 'workout_adjuster_pattern'
        
        for pattern in self.meal_plan_adjuster_patterns:
            if re.search(pattern, query_lower):
                if bert_intent in ['meal_plan_generator', 'goal_updater']:
                    return 'meal_plan_adjuster', 'meal_plan_adjuster_pattern'
        
        if re.search(r'\b(increase|decrease|add|reduce|more|less)\s+(calories?|protein|carbs?|fats?)', query_lower):
            if bert_intent in ['goal_updater', 'calorie_calculator', 'meal_plan_generator']:
                return 'meal_plan_adjuster', 'meal_adjust_macros'
        
        special_exclusive_match = any(re.search(p, query_lower) for p in self.special_meal_plan_exclusive)
        if special_exclusive_match:
            return 'special_meal_plan_generator', 'special_meal_plan_exclusive'
        
        if re.search(r'\b(meal|diet)\s+plan\s+for\s+(diabetes|hypertension|cholesterol|thyroid|pcos|heart|kidney)', query_lower):
            return 'special_meal_plan_generator', 'condition_specific_plan'
        
        if re.search(r'\b(create|generate|make|build|design)\s+(meal|diet)', query_lower):
            if re.search(r'\b(diabetes|diabetic|hypertension|cholesterol|thyroid|pcos|heart\s+disease|heart|kidney|ibs)', query_lower):
                return 'special_meal_plan_generator', 'medical_meal_plan_override'
        
        if re.search(r'\b(heart|kidney|liver|diabetes|thyroid|pcos)\s+(disease\s+)?(diet|meal)', query_lower):
            return 'special_meal_plan_generator', 'disease_diet_ultra_override'
        
        if re.search(r'\b(diet|meal)\s+(for|to\s+manage|to\s+control)\s+(hypertension|diabetes|heart)', query_lower):
            return 'special_meal_plan_generator', 'diet_for_condition_ultra'
        
        if re.search(r'\b(meal|diet)\s+plan\s+for\s+(weight\s+loss|muscle\s+gain|cutting|bulking|fitness)', query_lower):
            if bert_intent == 'special_meal_plan_generator':
                return 'meal_plan_generator', 'fitness_goal_not_medical'
        
        disease_advice_match = any(re.search(p, query_lower) for p in self.disease_advisor_patterns)
        if disease_advice_match:
            if not re.search(r'\b(meal|diet|food)\s+plan', query_lower):
                if bert_intent in ['special_meal_plan_generator', 'goal_updater', 'meal_plan_generator']:
                    return 'disease_advisor', 'disease_advisor_priority'
        
        weekly_match = any(re.search(p, query_lower) for p in self.weekly_meal_plan_patterns)
        special_match = any(re.search(p, query_lower) for p in self.special_meal_plan_patterns)
        
        if re.search(r'\b(week(ly)?|7\s+day|seven\s+day|whole\s+week|full\s+week|one\s+week)\s+(long\s+)?(meal|diet)', query_lower):
            return 'weekly_meal_plan_generator', 'weekly_keyword_aggressive'
        
        if special_match and bert_intent in ['meal_plan_generator', 'weekly_meal_plan_generator', 'disease_advisor']:
            return 'special_meal_plan_generator', 'special_meal_plan_keyword'
        
        if weekly_match and bert_intent in ['meal_plan_generator', 'special_meal_plan_generator']:
            return 'weekly_meal_plan_generator', 'weekly_plan_keyword'
        
        if bert_intent == 'weekly_meal_plan_generator':
            if not weekly_match and not re.search(r'\b(week(ly)?|7|seven|whole\s+week|full\s+week)', query_lower):
                if re.search(r'\b(meal|diet)\s+plan', query_lower):
                    return 'meal_plan_generator', 'downgrade_weekly_to_daily'
        
        if bert_intent in ['blood_report_query', 'blood_report_analyzer']:
            query_match = any(re.search(p, query_lower) for p in self.blood_report_query_patterns)
            analyzer_match = any(re.search(p, query_lower) for p in self.blood_report_analyzer_patterns)
            
            if analyzer_match:
                return 'blood_report_analyzer', 'blood_analyzer_keyword'
            if query_match:
                return 'blood_report_query', 'blood_query_keyword'
        
        if 'calorie' in query_lower:

            if 'how many calories' in query_lower:

                if query_lower.strip() in ['how many calories', 'how many calories?']:
                    return 'nutrition_analyzer', 'calorie_ambiguous_nutrition'
                
                has_food_context = any(word in query_lower for word in ['this', 'dish', 'meal', 'food', 'in ', 'are in', 'does'])
                
                if has_food_context:
                    if bert_intent == 'nutrition_analyzer':
                        return bert_intent, 'bert_nutrition_context'
                
                if bert_intent == 'nutrition_analyzer' and bert_confidence > self.CONF_THRESHOLD_HIGH:
                    return bert_intent, 'bert_high_confidence'
                
                return 'calorie_calculator', 'calorie_calc_keyword'
            
            if re.search(r'(what|how\s+much|how\s+many)\s+(should|is|are)\s+(my\s+)?calorie', query_lower):
                if bert_intent in ['goal_updater', 'nutrition_analyzer']:
                    return 'calorie_calculator', 'calorie_calc_pattern'
        

        for pattern in self.goal_updater_keywords:
            if re.search(pattern, query_lower):
                return 'goal_updater', 'goal_change_keyword_override'
        
        # Check if it's an educational weight loss/gain question (should NOT be goal_updater)
        for pattern in self.weight_loss_educational:
            if re.search(pattern, query_lower):
                if bert_intent == 'goal_updater':
                    return 'general_query', 'weight_loss_educational_override'
        
        # If "want to lose/gain weight" without explicit goal-setting context → general_query
        if re.search(r'\bi\s+want\s+to\s+(lose|gain)\s+weight\b', query_lower):
            if not re.search(r'\b(set|change|update|my\s+goal)\b', query_lower):
                return 'general_query', 'weight_want_without_goal_context'
        

        for pattern in self.special_meal_plan_disease_keywords:
            if re.search(pattern, query_lower):
                return 'special_meal_plan_generator', 'disease_meal_plan_override'

        for pattern in self.grocery_list_keywords:
            if re.search(pattern, query_lower):
                return 'grocery_list', 'grocery_shopping_override'
        
        for pattern in self.meal_ingredients_keywords:
            if re.search(pattern, query_lower):
                return 'meal_ingredients', 'meal_contents_override'

        for pattern in self.history_retriever_enhanced_keywords:
            if re.search(pattern, query_lower):
                if not re.search(r'\b(weight|height|age|bmi|stats|profile\s+info)\b', query_lower):
                    return 'history_retriever', 'meal_plan_retrieval_override'
        

        for pattern in self.blood_report_query_normal_ranges:
            if re.search(pattern, query_lower):
                return 'blood_report_query', 'normal_range_query_override'
        
        vital_match = any(re.search(p, query_lower) for p in self.vital_advisor_patterns)
        if vital_match:
            if bert_intent in ['disease_advisor', 'goal_updater', 'profile_updater']:
                return 'vital_advisor', 'vital_advisor_priority'
        
        profile_retrieve_match = any(re.search(p, query_lower) for p in self.profile_retriever_patterns)
        if profile_retrieve_match:
            if bert_intent in ['general_query', 'meal_plan_generator', 'greeting']:
                return 'profile_retriever', 'profile_retriever_pattern'
        
        history_match = any(re.search(p, query_lower) for p in self.history_retriever_patterns)
        if history_match:
            if bert_intent == 'meal_check_in':
                return 'history_retriever', 'history_retriever_pattern'
        
        special_exclusive_goal_match = any(re.search(p, query_lower) for p in self.special_meal_plan_exclusive_patterns)
        if special_exclusive_goal_match:
            if bert_intent in ['goal_updater', 'disease_advisor', 'meal_plan_generator']:
                return 'special_meal_plan_generator', 'special_meal_plan_goal'
        
        greeting_match = any(re.search(p, query_lower) for p in self.greeting_patterns)
        if greeting_match:
            if bert_intent in ['profile_updater', 'general_query']:
                return 'greeting', 'greeting_pattern'
        
        if bert_intent == 'general_query' and bert_confidence < self.CONF_THRESHOLD_LOW:
            if re.search(r'\bwhat\s+(is|does)\s+(this|it)\s+(contain|include|have)', query_lower):
                return 'meal_ingredients', 'meal_ingr_vague_low_conf'
        
        if 'exercise' in query_lower or 'workout' in query_lower:
            if 'recommendation' in query_lower or 'suggest' in query_lower:
                if bert_intent == 'general_query':
                    return 'fitness_plan_generator', 'fitness_recommendation'
        
        if re.search(r'\b(build|gain)\s+muscle', query_lower):

            if not re.search(r'\b(plan|program|routine|workout)', query_lower):
                if bert_intent == 'fitness_plan_generator':
                    return 'goal_updater', 'muscle_goal_pattern'
        
        if bert_confidence < self.CONF_THRESHOLD_LOW:
            
            if re.search(r'\b(other|different|change|alternative)\s+(suggestion|option|choice)', query_lower):
                if bert_intent == 'meal_plan_generator':
                    return 'meal_plan_adjuster', 'adjustment_low_conf'
            
            if re.search(r'\btell\s+me\s+something', query_lower):
                return 'general_query', 'tell_me_something'
            
            if re.match(r'^\d+$', query_lower.strip()):
                return 'profile_updater', 'single_number'
        
        if re.search(r'\bblood\s+work\s+analysis', query_lower):
            if bert_intent == 'panel_query':
                return 'blood_report_analyzer', 'blood_work_analysis'
        
        return bert_intent, 'bert_only'
    
    def predict(self, query: str) -> Tuple[str, float, str]:
       
        inputs = self.tokenizer(
            query,
            return_tensors='pt',
            truncation=True,
            max_length=128,
            padding=True
        ).to(self.device)
        
        with torch.no_grad():
            outputs = self.model(**inputs)
            logits = outputs.logits
            probs = torch.softmax(logits, dim=-1)
            confidence, predicted_id = torch.max(probs, dim=-1)
            
        bert_intent = self.id2label[predicted_id.item()]
        bert_confidence = confidence.item()
        
        final_intent, rule_applied = self._apply_rules(query, bert_intent, bert_confidence)
        
        method = f"BERT({bert_confidence:.2f})+{rule_applied}" if rule_applied != 'bert_only' else f"BERT({bert_confidence:.2f})"
        
        return final_intent, bert_confidence, method
    
    def predict_batch(self, queries: list) -> list:

        results = []
        
        batch_size = 32
        for i in range(0, len(queries), batch_size):
            batch = queries[i:i+batch_size]
            
            for query in batch:
                intent, confidence, method = self.predict(query)
                results.append((query, intent, confidence, method))
        
        return results


def main():
    print("=" * 80)
    print("TESTING HYBRID BERT + RULES CLASSIFIER")
    print("=" * 80)
    
    classifier = HybridBERTClassifier()
    
    print("\n[1] Loading test data...")
    with open('comprehensive_test_1000.json', 'r') as f:
        test_data = json.load(f)
    print(f"   ✓ Loaded {len(test_data)} test queries")
    
    print("\n[2] Running hybrid predictions...")
    start_time = time.time()
    
    correct = 0
    total = len(test_data)
    errors = []
    method_stats = {}
    
    for item in test_data:
        query = item['query']
        expected = item['expected']
        
        predicted, confidence, method = classifier.predict(query)
        
        method_key = method.split('+')[1] if '+' in method else 'bert_only'
        method_stats[method_key] = method_stats.get(method_key, 0) + 1
        
        if predicted == expected:
            correct += 1
        else:
            errors.append({
                'query': query,
                'expected': expected,
                'predicted': predicted,
                'confidence': confidence,
                'method': method
            })
    
    end_time = time.time()
    duration = end_time - start_time
    
    accuracy = (correct / total) * 100
    
    print(f"   ✓ Predictions complete in {duration:.2f}s")
    print(f"   ✓ Speed: {total/duration:.1f} queries/sec")
    
    print("\n[3] Results Summary")
    print(f"   Correct: {correct}/{total}")
    print(f"   Accuracy: {accuracy:.4f}%")
    print(f"\n   Method breakdown:")
    for method, count in sorted(method_stats.items(), key=lambda x: x[1], reverse=True):
        print(f"      {method}: {count} queries ({count/total*100:.1f}%)")
    
    print("\n" + "=" * 80)
    print("PER-CLASS ACCURACY")
    print("=" * 80)
    
    class_stats = {}
    for item in test_data:
        intent = item['expected']
        if intent not in class_stats:
            class_stats[intent] = {'correct': 0, 'total': 0}
        class_stats[intent]['total'] += 1
    
    for item in test_data:
        query = item['query']
        expected = item['expected']
        predicted, _, _ = classifier.predict(query)
        
        if predicted == expected:
            class_stats[expected]['correct'] += 1
    
    print(f"\n{'Intent':<40} {'Correct':<12} {'Accuracy'}")
    print("-" * 80)
    
    perfect_classes = 0
    below_99 = 0
    
    for intent in sorted(class_stats.keys()):
        stats = class_stats[intent]
        acc = (stats['correct'] / stats['total']) * 100
        symbol = '✓' if acc == 100 else '✗'
        print(f"{symbol} {intent:<38} {stats['correct']}/{stats['total']:<8} {acc:.1f}%")
        
        if acc == 100:
            perfect_classes += 1
        if acc < 99:
            below_99 += 1
    
    print("\n" + "=" * 80)
    print("FINAL ASSESSMENT")
    print("=" * 80)
    
    if accuracy >= 99.9:
        print(f"\n✓ SUCCESS: {accuracy:.4f}% accuracy achieved!")
        print(f"   Perfect classes: {perfect_classes}/{len(class_stats)}")
    elif accuracy >= 95:
        print(f"\n⚠ GOOD PROGRESS: {accuracy:.4f}% accuracy")
        print(f"   Gap to 99.9%: {99.9 - accuracy:.2f}%")
        print(f"   Perfect classes: {perfect_classes}/{len(class_stats)}")
        print(f"   Classes below 99%: {below_99}")
    else:
        print(f"\n NEEDS MORE WORK: {accuracy:.4f}% accuracy")
        print(f"   Gap to 99.9%: {99.9 - accuracy:.2f}%")
    
    results_file = 'hybrid_bert_test_results.json'
    with open(results_file, 'w') as f:
        json.dump({
            'accuracy': accuracy,
            'correct': correct,
            'total': total,
            'method_stats': method_stats,
            'class_stats': class_stats,
            'errors': errors
        }, f, indent=2)
    
    print(f"\n✓ Detailed results saved: {results_file}")
    print("=" * 80)


if __name__ == '__main__':
    main()
