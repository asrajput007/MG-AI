
# 📚 Zestiva AI: Unified Health & Wellness Assistant

## 1. Project Overview and Modular Architecture

This project is a multi-domain, agentic AI platform designed to provide highly personalized health, nutrition, and fitness guidance. The core architecture is built on a **modular Python backend** using **FastAPI** as the central intelligence and routing hub, orchestrating a suite of specialized AI **Tools** to serve the user's health goals.

The core principle is **Agentic Orchestration**, where a central intelligence layer manages user state and delegates complex tasks (like meal plan creation, report analysis, and profile updates) to dedicated, encapsulated tools.

### 1.1. Core Components

| Component | Technology | Role |
| :---: | :---: | :---: |
| **Backend (API Gateway)** | **FastAPI** / Python (`app.py`) | Acts as the **central hub**. Validates incoming requests, manages **JWT authorization** for user identity, performs **pre-processing** (image/OCR analysis), and routes all queries to the core Agent. Handles non-blocking background tasks for logging. |
| **Agent Core** | Python (`agent_core.py`) | The central intelligence layer (`ToolBasedNutritionAgent`). It maintains **user state** (`current_constraints`), manages **over 25 Tools**, orchestrates the `QueryClassifierTool`, and runs core programmatic logic (e.g., handling conflicts, programmatically scaling meals). |
| **LLM Services** | Custom Python (`tool_core.py`, `config.py`) | Abstracted layer for **resilient API access** to multiple specialized LLM APIs (Mistral, Azure OpenAI). Implements a **Circuit Breaker** pattern for robust failover and rate limit management. |
| **Data & Persistence** | `requests` / `DatabasePersistenceTool` | All persistent data (**profiles, plans, history, logs**) is handled via asynchronous **HTTP POST/GET** calls to an external Zestiva API (`Zestiva_API_TESTING`). Local knowledge bases are loaded from JSON/PT files. |

---

## 2. Technical Stack and Dependencies

The project is entirely built on **Python**, focusing on stability, high-performance API serving, and advanced analytical capabilities.

### 2.1. Primary Tech Stack

* **Core Language:** Python 3.10+
* **API Framework:** **FastAPI** (with Uvicorn)
* **Core LLM Integration:** Custom `LLMService` (from `tool_core.py`) built on `aiohttp` for asynchronous, failover-enabled requests to Azure/Mistral endpoints.
* **ML/Vision:** `ultralytics` (**YOLOv8** for food detection), `PIL`, `cv2` (used in `app.py`).
* **Numerical/Optimization:** `scipy.optimize`, `numpy` (Critical for programmatic meal scaling).
* **Document Handling:** `pytesseract`, `docx`, `csv`, `io` (for report/image extraction).

### 2.2. Critical Libraries and Specific Usage

| Library | Purpose in Project | Implementation Detail |
| :---: | :---: | :---: |
| **`scipy.optimize`** | **Numerical Scaling Algorithm (CRITICAL)** | The `minimize` function (using the **SLSQP method**) is implemented in `agent_core.py` (via `_programmatically_scale_meal_portions`). It finds the ideal adjustment ratio for every food item to mathematically enforce the precise calorie (e.g., $\pm10$ kcal) and macronutrient targets. |
| **`rapidfuzz`** | **Fuzzy Matching/Typo Tolerance** | Used by `DataService` for quick, approximate matching of food names to the local cache. Crucially used by `ProfileUpdaterTool` to match user-provided, misspelled health conditions/allergies to hardcoded codes. |
| **`pydantic`** | **Data Validation/Schema Enforcement** | Defines the strict schema for incoming data (`ProcessQueryRequest`) and internal state models (`UnifiedConversationState`). Also used by LLM calls (e.g., `ProfileUpdaterTool`) to enforce structured JSON output. |
| **`tool_core` (`LLMService`)**| **Resilient Communication/Failover** | Implements a software **Circuit Breaker pattern** (states: AVAILABLE, HALF\_GATED, COOLDOWN) to ensure fault tolerance. If an LLM endpoint fails (e.g., 429 Rate Limit or 500), the request automatically fails over to the next available endpoint. |
| **`jwt`** | **Security** | Used in `app.py` to decode the `Authorization` JWT token, extract the `user_id` (`"Id"` claim), and secure the API endpoints. |
| **`nltk` & `inflect`** | **Natural Language Pre-processing** | Used in `app.py` for lemmatization and inflections (e.g., plural/singular) to normalize and expand food names before searching the nutritional database, improving fuzzy matching recall. |
| **`ultralytics` (YOLO)** | **Computer Vision & OCR Pre-processing** | The YOLO model is loaded and run in `app.py` to detect food items in user-uploaded images, providing grounding context for the multimodal LLM and improving meal logging accuracy. |
| **`PyPDF2` & `docx`** | **Document Parsing** | These libraries, used in `vitals_and_disease.py`, ensure compatibility with common blood report formats, reliably extracting raw text data from uploaded PDF and DOCX files for subsequent LLM processing. |
| **`fastapi` (`BackgroundTasks`)**| **Asynchronous Logging & Non-Blocking I/O** | Used in `app.py` to ensure high API responsiveness. Tasks like saving the full chat history or generating the profile summary for the *next* turn are offloaded to run asynchronously in the background. |
| **`aiohttp`** | **Async HTTP Client** | The primary asynchronous client used within the `LLMService` to perform parallel and non-blocking HTTP POST requests to multiple LLM endpoints, critical for implementing the failover and circuit breaker logic efficiently. |
---

## 3. Agentic Architecture: The Operational Flow

The Agent Core functions as a self-aware system that dynamically routes user queries based on intent, profile context, and operational safety checks.

### 3.1. Agent Workflow: The Orchestrator Logic (The Master Control)

The Agent's intelligence is executed as a Sequential Flow with Overrides:

* **Request Pre-processing (Multi-modal Input Handling):** Before Agent execution, `app.py` handles multi-modal inputs. If an image URL is present, YOLOv8 runs food item detection, and Azure CV runs OCR on packaging labels. The analysis results (e.g., detected items, extracted nutrition text) are **injected** into the user query text for the Agent to process.
* **Request & Profile Sync (Data Integrity Check):** User query arrives at `app.py`. The `ProfileRetrieverTool` initiates an asynchronous fetch using the JWT token to sync the absolute latest `current_constraints` dictionary from the external Zestiva DB. This ensures the Agent always operates on the single source of truth.
* **Intent Classification (The Router):** The `QueryClassifierTool` uses a dedicated, low-latency LLM to analyze the query, the chat history, and the previous turn's `profile_summary_json`. It outputs a single, definitive `ToolType` string (e.g., `meal_plan_generator`, `vital_advisor`, or `general_query`).
* **Conflict Check (Priority Override - Safety First):** Before running the classified tool, the Agent performs a high-priority, LLM-based check (`_check_for_profile_conflict` in `agent_core.py`). It specifically looks for contradictions (e.g., requesting a "chicken" meal when the profile is "Vegetarian"). If a conflict is detected, the flow is **interrupted**, and the Agent asks the user for confirmation to update their profile (`action_type`: `ADD`, `REMOVE`, or `REPLACE`), safeguarding against unintended profile changes.
* **Specialized Tool Execution:** If no conflict is found, the classified tool runs. This can range from pure programmatic logic (`WaterStepTool`) to a complex, multi-step LLM-based operation (`MealPlanGeneratorTool`).
* **Background Logging and Summary:** After the main task, the Agent dispatches an asynchronous `BackgroundTasks` job to: 1) Save the full user query and AI response to the DB for history tracking, and 2) Run the `ProfileSummaryTool` to update the `profile_summary_json` for the *next* conversation turn, ensuring the classifier has fresh context without delaying the current user response.

### 3.2. Programmatic Meal Plan Scaling Algorithm (The Solver)

This step, located in `agent_core.py`, is the most analytically complex process, ensuring mathematical accuracy:

* **LLM Draft Generation:** The LLM generates a text-based meal plan (the "draft").
* **Parsing:** The Agent calls `_parse_meal_plan_text_to_json` to convert the Markdown draft into a programmatic `Dict` structure, calculating initial macro totals.
* **Optimization Setup:** The routine sets up an objective function for `scipy.optimize.minimize`. This function minimizes the squared error between:
    * The total daily calories and the target calorie goal (e.g., 1750 kcal).
    * The overall macronutrient ratio and the required distribution ranges (e.g., Carb 45-60%).
* **Solving (SLSQP):** The solver adjusts the portion size (in grams) for every single food item within the meal plan to find the optimal solution that hits the target calories with the maximum macro compliance.
* **Reformatting:** The perfectly scaled and validated meal plan structure is converted back into the final Markdown display text (`_convert_plan_json_to_text`) for the user.

### 3.3. Blood Report Analysis Workflow

The `BloodReportAnalyzerTool` combines multiple components for robust data extraction:

* **Document Extraction:** Detects the file type (`.pdf`, `.docx`, `.csv`, `.jpg`) and uses the appropriate reader (`PyPDF2`, `docx`, `pytesseract`) to extract raw text content.
* **LLM Translation & Structuring:** The raw, messy text is sent to a dedicated LLM with a highly prescriptive JSON schema (e.g., `[{"test_name_abbr": "WBC", "value": 11.5, "range": "high"}]`). This enforces structured output.
* **Matching & Consolidation:** Extracted test values are matched against the local medical reference knowledge base (`panel.json`) using fuzzy matching (`rapidfuzz`). The tool then consolidates the final list of foods to avoid and recommended foods, which are stored directly in the `current_constraints` for real-time planning and advice.

---

## 4. Data Structures, Static Knowledge, and Typing

### 4.1. Core State Management: `current_constraints` (Dict / JSON)

This dictionary is the most critical Python structure, passed between the frontend and the agent on every call. It represents the user's single source of truth.

| Key Name | Data Type (Python/Internal) | Purpose & Granular Flow Use |
| :---: | :---: | :---: |
| `medical_conditions` | `List[String]` | Used for absolute food exclusion (e.g., high-purine foods for Gout) in the Meal Plan generator's system prompt. |
| `vitals_numeric` | `Dict[String, Union[Float, String]]` | Stores raw readings. Used by `VitalAdvisorTool` to fetch the corresponding entry from `vitals 11.json` for customized advice. |
| `blood_report_analysis_data` | `Dict / JSON` | Stores the parsed and validated lab results, used by `BloodReportQueryTool` to answer detailed questions like "What are my Triglycerides?". |
| `foods_to_avoid_from_report` | `List[String]` | Consolidated avoidance list. This combines conflicts from allergies, diseases (`Diseases_cleaned.json`), and abnormal lab results (e.g., high cholesterol). |
| `target_steps`, `target_water_liters` | `Int`, `Float` | Calculated outputs of the `WaterStepTool`. Used by the LLM in `waterstep.py` to generate motivational responses. |
| `last_agent_context` | `Dict` | Stores ephemeral state like `active_meal_plan`, `meal_check_in_phase`, and the temporary token to skip DB profile fetches. |

### 4.2. Static Knowledge Bases (JSON Files)

| File Name | Structure | Role in System |
| :---: | :---: | :---: |
| `Diseases_cleaned.json` | `List of Dicts` | Provides absolute food avoidance lists for safety checks against over 50 health conditions, accessed by `DiseaseAdvisorTool`. |
| `Medical_Condition_Food_Prompt.json` | `Dict[String, String]` | Contains highly prescriptive system prompts (with required macro ranges and food group rules) used to constrain LLM behavior during specialized meal generation. |
| `panel.json` & `blood_report.json` | `Nested Dicts` | Defines the full medical reference data for laboratory tests, normal ranges, and generalized nutritional recommendations, accessed by `BloodReportQueryTool`. |
| `vitals 11.json` | `Nested Dicts` | Defines classification logic and targeted food recommendations for abnormal vital signs (e.g., Tachycardia, Hyperglycemia). |

### 4.3. Internal Typing and Conversational State Models

| Model/Class | Type | Purpose & Data Structure |
| :---: | :---: | :---: |
| `ToolResult` | `dataclass` | The standardized output for every single tool, enforcing consistent returns of (success: bool, data: Any, error: str, metadata: Dict). This ensures seamless integration across tools. |
| `ProcessQueryRequest` | `Pydantic BaseModel` | Input validation model for the main API endpoint (`app.py`), ensuring all required inputs (query, constraints, history) are present and correctly typed. |
| `UnifiedConversationState` | `Pydantic BaseModel` | The state machine model used by `SessionBookingTool` to track the user's progress through multi-turn conversational flows, ensuring no required booking field is missed. |
| Meal Plan Structure | `Dict[Meal, List[ItemDict]]` | The native Python data structure used in the Agent Core for calculation. Each `ItemDict` contains the detailed, mathematically validated nutritional values after the `scipy.optimize` step. |

---

## 5. Deep Dive: Core Tools and Data Interplay

This section details the intricate workings of the most specialized tools, focusing on how they leverage data types, LLM capabilities, and programmatic logic to achieve precision and safety.

### 5.1. LLMService and Circuit Breaker Logic (`tool_core.py`)

The custom `LLMService` is crucial for reliability. It manages a pool of multiple, specialized LLM endpoints (Mistral/Azure OpenAI, detailed in `config.py`).

* **Data Types:** The pool is stored as a `List[Dict]` (`self.all_models`), and the state of each endpoint is managed in a Python `Dict` (`self.endpoint_states`), which holds the `status` (`AVAILABLE`, `HALF_GATED`, `COOLDOWN`), `cooldown_until` (`float` timestamp), and `failure_count` (`int`).
* **Operational Detail:** The core logic runs in `_query_with_failover`. If an endpoint returns a critical error (like `429 Rate Limit`), its state transitions to `COOLDOWN`, making it unavailable for a fixed period (e.g., 30 minutes). Requests then failover to the next healthy endpoint, ensuring high application uptime.

### 5.2. Profile Updater and Fuzzy Matching (`profile_management.py`)

The `ProfileUpdaterTool` is responsible for translating vague user intent ("I'm allergic to peanuts now") into a structured, safe API call.

* **Data Types:** It uses a massive, internal Python `Dict` (`self.item_to_category_map`) that maps user-friendly phrases (lowercase strings) to standardized codes and their API categories (e.g., `'nuts'` maps to `('food_allergy_codes', 'Tree Nuts (e.g. Almonds, Walnuts, Cashews)')`).
* **Operational Detail:** It uses the **`rapidfuzz`** library with the `WRatio` scorer (a robust fuzzy matching algorithm) to find the best match between the user's potentially misspelled or incomplete input and the official medical/dietary codes. This ensures data integrity by preventing the saving of invalid, user-typed strings.

### 5.3. Water & Step Goal Calculation (`waterstep.py`)

The `WaterStepTool` calculates personalized activity and hydration targets based on static, rule-based algorithms.

* **Data Types:** The core logic uses Python `Dict`s to store adjustments (`adjustments: Dict[str, int]`) which are then summed to find the final target. The final goal is stored in the `current_constraints` as `Int` (`target_steps`) and `Float` (`target_water_liters`).
* **Operational Detail:** Calculations use a baseline (e.g., 8000 steps) and apply heuristic adjustments based on profile factors (Age, BMI, Activity Level, Occupation, Goal Type). This is a purely programmatic function, independent of the LLM, ensuring consistent and precise goal setting.

### 5.4. Recipe & Ingredients (`nutrition.py`)

The `MealIngredientsTool` provides a detailed list of ingredients for a given meal or food item.
The `GroceryListTool` generates a consolidated grocery list from the user's active daily or weekly meal plan.
The `CravingAssistantTool` suggests three quick, healthy snack options based on user cravings and profile constraints.
The `NutritionAnalyzerTool` analyzes nutrition, checks for unsafe foods, and evaluates meal plans.

### 5.5. Health Panel Q&A (`vitals_and_disease.py`)

The `PanelQueryTool` answers specific queries about general health panels, nutritional recommendations, and recipes from the panel.json file, not specific to an uploaded blood report.
The `DiseaseAdvisorTool` provides disease-specific dietary recommendations using an LLM.
The `VitalAdvisorTool` provides nutritional advice based on vital signs.

### 5.6. Meal Plan generator 

The `MealPlanGeneratorTool` generates or creates personalized meal plans.
The `WeeklyMealPlanGeneratorTool` generates a full 7-day (weekly) personalized meal plan from Monday to Sunday.
The `SpecialMealPlanGeneratorTool` generates or creates special meal plans (eg. anti-inflammatory diet plan)

### 5.7. Daily Meal Logger

The `MealCheckInTool` manages meal check-in flows, logs consumed meals, and adjusts daily plans accordingly. (via meal image analysis & Packaged Food Analysis (OCR))
The `MealPlanAdjusterTool` modifies an existing meal plan by adding, removing, replacing items, or scaling portions.
---

## 6. Project Architecture: Summary of Key Files and Responsibilities

This section provides a map of the file structure and the primary classes or functions within each file, showcasing the clean separation of concerns.

| File | Primary Classes / Functions | Core Responsibility |
| :---: | :---: | :---: |
| `app.py` | `process_user_query`, `analyze_blood_report_endpoint` | **API Gateway (Entry Point)**. Handles Fast API endpoints, JWT security, YOLO/OCR image pre-processing, and delegates all processing to the Agent Core. |
| `agent_core.py` | `ToolBasedNutritionAgent`, `_run_meal_plan_orchestrator`, `_programmatically_scale_meal_portions` | **Agent Orchestrator**. Manages the full query flow, handles conflict resolution, runs the complex SciPy optimization, and orchestrates tool execution. |
| `tool_core.py` | `LLMService`, `ToolType` (Enum), `TokenBucket` | **LLM Abstraction and Resilience**. Provides failover logic for LLM API calls and defines the centralized `ToolType` vocabulary used across the entire Agent. |
| `base_and_utility.py`| `DatabasePersistenceTool`, `QueryClassifierTool`, `DataService` | **Infrastructure & Routing**. Manages all database I/O via an external API proxy, routes queries via the classifier, and provides access to local data caches (nutrition, diseases). |
| `nutrition.py` | `MealPlanGeneratorTool`, `MealCheckInTool`, `GroceryListTool` | **Nutrition Logic**. Contains core LLM-based meal plan drafting, complex logic for post-consumption plan adjustment, and ingredient extraction. |
| `profile_management.py`| `ProfileUpdaterTool`, `GoalUpdaterTool`, `ProfileSummaryTool` | **User State Management**. Logic for updating user details, setting goals, and generating the internal profile summary (JSON for LLM context). |
| `vitals_and_disease.py`| `BloodReportAnalyzerTool`, `VitalAdvisorTool`, `CalorieCalculatorTool` | **Health & Medical Logic**. Handles blood report OCR/analysis, vital sign assessment, and TDEE/BMI calculation. |
| `fitness.py` | `FitnessPlanGeneratorTool` | **Fitness Planning**. Generates structured, multi-day workout plans using a highly constrained LLM model call, adhering to medical safety checks. |
| `sessionbooking.py` | `SessionBookingTool`, `UnifiedConversationState` (Pydantic) | **Conversational Flow Management**. Handles the multi-turn state machine for scheduling appointments with experts. |
| `waterstep.py` | `WaterStepTool`, `_calculate_steps_logic`, `_calculate_water_logic` | **Goal Calculation & Logging**. Programmatic calculation and logging of daily step and water intake goals based on user's profile, weight, and activity. |

---

## 7. Upcoming Features: Fitness Model Expansion (Future State)

The Fitness Model (`FitnessPlanGeneratorTool`) is designed for significant expansion in subsequent project phases. The current architecture supports the integration of these features seamlessly:

### 7.1. Enhanced User Experience

* **Exercise Video Integration:** The generated fitness plan JSON (`plan_json` in `fitness.py`) will be enriched with a video URL (`video_url: string`) for each exercise. This enables the frontend (UI) to display a demonstration video alongside the set/rep instructions, greatly enhancing safety and compliance.
* **Interactive Sessions:** Implementation of a feature allowing the user to select an **Exercise Video** and complete a virtual session, providing real-time feedback (e.g., "Good form, slow down the eccentric phase").

### 7.2. Expert Interaction and Scheduling

* **Sessions with Fitness Experts:** The existing `SessionBookingTool` will be extended to specifically book sessions with a **Fitness Expert** or **Personal Trainer** (separate from Nutrition Experts), allowing for specialized guidance on form correction, routine modification, and motivation.
* **AI-Driven Session Booking:** The `SessionBookingTool` (`sessionbooking.py`) will be integrated with the **Nutrition and Fitness Experts'** real-time schedules. The AI will use Pydantic models to track user availability and preference (e.g., "video call at 5 PM on Tuesday") and non-blockingly check the external schedule API for the next available slot, providing instant confirmation. This uses the `UnifiedConversationState` model to manage the multi-turn booking state efficiently.
