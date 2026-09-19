
import base64
from contextlib import asynccontextmanager
import io
import logging
import os
import uuid
import asyncio
from concurrent.futures import ThreadPoolExecutor
from ai.ask_ai import ProcessQueryRequest, chat_intent_summary, handle_analyze_blood_report, handle_process_query
from fastapi import Body, FastAPI, Form, HTTPException, Query, Request, Response, status, UploadFile, File, Header
from fastapi.responses import HTMLResponse, JSONResponse
import json
from datetime import  datetime, timedelta, timezone
from typing import Optional
import requests
import re
from fastapi.middleware.cors import CORSMiddleware
from helpers.automation_script import generate_weekly_meal_plan_celery
from helpers.care_plan import fetch_failed_patients, fetch_pending_patients, process_single_patient
from helpers.duckling import warm_up_duckling
from pydub import AudioSegment
from helpers.database import  get_db_connection_dynamic, workout_db_connection
from helpers.db_async import execute_query, execute_many, execute_transaction
from helpers.models import DashboardMetrics, WeeklyMealPlanRequest
from helpers.utils import DYNAMIC_DB_NAME, KOKORO_API_URL, WEEKDAY_ORDER, _unsubscribe_html_response, calculate_comprehensive_tdee, constraints_match, convert_json_to_profile_for_dashboard_message, extract_date_from_day, extract_food_data_from_mistral_analysis, extract_plan_blocks, fetch_chat_history, fetch_chat_history_v2, fetch_patient_profile, fetch_profile_data, generate_long_sas_url, generate_profile_hash, get_all_chat_titles, \
    get_jwt_payload, get_mistral_analysis, get_mistral_nutritional_assessment, get_mistral_nutritional_breakdown, container_client, get_patient_active_diagnoses, get_plan_dates, \
    get_previous_day_meal_plan_text, get_rotating_tips, get_user_profile, get_user_profile_v2, insert_vital_if_allowed, is_failed_response, is_sas_expired, json_serializer, log_failed_query, log_notification, normalize_payload, normalize_vital_name, normalize_weekly_plan, parse_items_to_meal_plan_text, parse_meal_plan_text_from_db, parse_meal_plan_text_ios, \
    parse_meal_plan_text_to_items, process_resubscribe, process_unsubscribe, save_tdee_goal, serialize_all, serialize_data, split_weekly_meal_plan, transcribe_speech, upsert_chat_history, validate_profile_for_meal_plan, generate_sas_url

from fastapi import BackgroundTasks
from notification_service import send_push_notification
from typing import Optional
background_tasks = BackgroundTasks()

LOGS_FOLDER = "logs"
if not os.path.exists(LOGS_FOLDER):
    os.makedirs(LOGS_FOLDER)
    print(f"Created folder: {LOGS_FOLDER}")

today_date = datetime.now().strftime("%Y-%m-%d")
log_filename = os.path.join(LOGS_FOLDER, f"logs_{today_date}.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(log_filename, encoding='utf-8'),
        logging.StreamHandler()
    ],
    force=True
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Configure larger thread pool for database operations
    loop = asyncio.get_event_loop()
    executor = ThreadPoolExecutor(max_workers=50, thread_name_prefix="db_worker")
    loop.set_default_executor(executor)
    
    logger.info("Configured ThreadPoolExecutor with 50 workers for database operations")
    
    # Check if semantic router dataset has changed and needs rebuilding
    from helpers.dataset_monitor import ensure_model_sync
    ensure_model_sync()
    
    warm_up_duckling()
    
    yield
    
    # Cleanup
    executor.shutdown(wait=True)
    logger.info("Shutdown ThreadPoolExecutor")

app = FastAPI(
    title="Mobile API",
    description="Detailed API documentation for Friska Nutrition.",
    version="1.0.0",
    contact={
        "name": "Friska Support",
        "email": "support@friska.ai",
        "url": "https://friska.ai"
    },
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins (Change this to specific domains for security)
    allow_credentials=True,
    allow_methods=["*"],  # Allows all HTTP methods
    allow_headers=["*"],  # Allows all headers
)

# Include LangGraph API Router
try:
    from ai.langgraph_api import router as langgraph_router
    app.include_router(langgraph_router)
    logger.info("LangGraph API routes registered successfully")
except Exception as e:
    logger.warning(f"Could not register LangGraph routes: {e}")


@app.get("/")
def read_root():
    return {"message": "Welcome to the MG PROD API!"}


# =========================================================
# OPTIMIZED WEEKLY MEAL PLAN API
# =========================================================
@app.post("/generate-weekly-meal-plan")
async def generate_weekly_meal_plan(
    request: Request,
    body: WeeklyMealPlanRequest,
    authorization: Optional[str] = Header(None)
):

    logger.info("==== Weekly Meal Plan API Started ====")

    # =====================================================
    # AUTH
    # =====================================================

    scheme, _, token = authorization.partition(" ")

    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid token")

    payload = get_jwt_payload(request)

    user_id = body.user_id or payload.get("Id")
    database_name = body.database_name or payload.get("DataBaseName")

    if not user_id or not database_name:
        return JSONResponse(
            status_code=400,
            content={"message": "Invalid user"}
        )

    # =====================================================
    # DATE RANGE
    # =====================================================

    start_date = body.start_date

    if isinstance(start_date, str):
        start_date = datetime.strptime(
            start_date,
            "%Y-%m-%d"
        ).date()

    days_until_saturday = (5 - start_date.weekday()) % 7

    end_date = start_date + timedelta(days=days_until_saturday)

    logger.info(f"Weekly range: {start_date} -> {end_date}")

    # =====================================================
    # DB WORK (SHORT CONNECTION)
    # =====================================================

    conn = None
    cursor = None

    # Initialize before the DB block so they're available after
    today_consumed_meals = set()
    existing_dates = set()
    profile = None
    profile_changed = False
    current_profile_hash = None

    try:

        conn = get_db_connection_dynamic(database_name)
        conn.autocommit = False
        cursor = conn.cursor()

        # -------------------------------------------------
        # FETCH PROFILE
        # -------------------------------------------------

        logger.info("Fetching user profile")

        profile = get_user_profile_v2(
            user_id=user_id,
            cursor=cursor
        )

        logger.info("Profile fetched")

        # -------------------------------------------------
        # VALIDATE PROFILE
        # -------------------------------------------------

        is_valid, error_message = validate_profile_for_meal_plan(profile)

        if not is_valid:
            return JSONResponse(
                status_code=400,
                content={
                    "status": 400,
                    "message": error_message
                }
            )

        # -------------------------------------------------
        # PROFILE HASH
        # -------------------------------------------------

        current_profile_hash = generate_profile_hash(profile)

        logger.info(f"Current profile hash: {current_profile_hash}")

        # -------------------------------------------------
        # FETCH CHAT HISTORY
        # -------------------------------------------------

        logger.info("Fetching chat history")

        chat_history, stored_constraints, context = fetch_chat_history_v2(
            cursor,
            user_id
        )

        stored_profile_hash = None

        if stored_constraints:
            stored_profile_hash = stored_constraints.get("profile_hash")

        profile_changed = (
            current_profile_hash != stored_profile_hash
        )

        logger.info(f"Profile changed: {profile_changed}")

        # -------------------------------------------------
        # CHECK EXISTING RANGE
        # -------------------------------------------------

        cursor.execute("""
            SELECT DISTINCT MealDate
            FROM MealPlanDetails WITH (NOLOCK)
            WHERE PatientId=?
            AND MealDate BETWEEN ? AND ?
        """, (
            user_id,
            start_date,
            end_date
        ))

        existing_dates = {
            row[0]
            for row in cursor.fetchall()
        }

        logger.info(f"Existing dates: {existing_dates}")

        # -------------------------------------------------
        # FIX: FETCH TODAY'S CONSUMED MEALS HERE
        # so they are available before insert_rows is built.
        # Only needed when profile has changed, since that's
        # the only scenario where we regenerate today's meals.
        # -------------------------------------------------
        if profile_changed:

            cursor.execute("""
                SELECT DISTINCT
                    mpd.MealType
                FROM UserConsumedMeals ucm WITH (NOLOCK)
                INNER JOIN MealPlanDetails mpd WITH (NOLOCK)
                    ON mpd.Identifier = ucm.UserMealItemId
                WHERE ucm.UserId = ?
                AND ucm.ConsumptionDateOnly = ?
                AND ISNULL(ucm.WasCompleted, 0) = 1
            """, (
                user_id,
                start_date
            ))
        
            today_consumed_meals = {
                row[0]
                for row in cursor.fetchall()
            }
        
            logger.info(
                f"Consumed meal types: {today_consumed_meals}"
            )
                
    finally:

        if cursor:
            cursor.close()

        if conn:
            conn.close()

    # =====================================================
    # CHECK MISSING DATES
    # =====================================================

    requested_dates = set()

    current_date = start_date

    while current_date <= end_date:
        requested_dates.add(current_date)
        current_date += timedelta(days=1)

    missing_dates = requested_dates - existing_dates

    logger.info(f"Missing dates: {missing_dates}")

    should_regenerate = (
        profile_changed or bool(missing_dates)
    )

    logger.info(f"Should regenerate: {should_regenerate}")

    # =====================================================
    # RETURN EXISTING
    # =====================================================

    if not should_regenerate:

        logger.info("Returning existing weekly meal plan")

        return JSONResponse(
            status_code=200,
            content={
                "status": 200,
                "message": "Existing weekly meal plan returned",
                "profile_changed": False,
                "regenerated": False,
                "start_date": str(start_date),
                "end_date": str(end_date)
            }
        )
    # -------------------------------------------------
    # CALCULATE TDEE
    # -------------------------------------------------
    goals = profile.get("goals") or [{}]
    tdee = calculate_comprehensive_tdee(
    gender=profile.get("gender"),
    weight_kg=profile.get("weight_kg"),
    height_cm=profile.get("height_cm"),
    age=profile.get("age"),
    activity_level=profile.get("activity_level"),
    goal_type=goals[0].get("GoalName", "Weight Maintenance"),
    waist_circumference_cm=profile.get("waist_circumference_cm"),
    vitals_numeric=profile.get("vitals_numeric"),
)

    logger.info(f"Calculated TDEE: {tdee}")

    # =====================================================
    # AI CALL (NO DB CONNECTION ACTIVE)
    # =====================================================

    logger.info("Calling AI meal plan generator")

    prompt = """
    Generate a weekly meal plan for me based on my profile.
    """

    request_obj = ProcessQueryRequest(
        query=prompt,
        chat_history=[],
        current_constraints=profile,
        last_agent_context={},
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_date.strftime("%Y-%m-%d"),
        force_tool_type="weekly_meal_plan_generator"
    )

    ai_data = await handle_process_query(
        user_id=user_id,
        token=token,
        request=request_obj,
        background_tasks=background_tasks
    )

    raw_text = ai_data.get("answer", "")

    grocery_items = ai_data.get(
        "grocery_list_json",
        []
    )

    if not raw_text or "Day 1" not in raw_text:

        return JSONResponse(
            status_code=400,
            content={
                "message": "AI returned invalid weekly plan"
            }
        )

    # =====================================================
    # SPLIT DAYS
    # =====================================================

    day_blocks = split_weekly_meal_plan(raw_text)

    if not day_blocks:

        return JSONResponse(
            status_code=400,
            content={
                "message": "Invalid weekly format"
            }
        )

    # =====================================================
    # PREPARE INSERT DATA
    # =====================================================

    meal_times = {
        "Breakfast": "08:00:00",
        "MorningSnack": "10:00:00",
        "Lunch": "13:00:00",
        "EveningSnack": "16:00:00",
        "Dinner": "19:00:00"
    }

    insert_rows = []

    inserted_days = 0

    generated_date = datetime.now(timezone.utc).date()

    # =====================================================
    # BUILD INSERT ROWS
    # today_consumed_meals is now populated before this loop
    # =====================================================

    for day_block in day_blocks:

        meal_date = extract_date_from_day(day_block)

        if not meal_date:
            continue

        # Skip already existing dates (only when profile hasn't changed)
        if not profile_changed and meal_date in existing_dates:

            logger.info(f"Skipping existing date: {meal_date}")

            continue

        items = parse_meal_plan_text_to_items(day_block)

        if not items:

            logger.warning(f"No valid items for {meal_date}")

            continue

        inserted_days += 1

        for item in items:

            meal_type = item["MealType"]

            # ==========================================
            # SKIP CONSUMED MEALS FOR TODAY
            # today_consumed_meals is now correctly populated
            # before this loop, so this check actually works.
            # ==========================================

            if (
                profile_changed
                and meal_date == start_date
                and meal_type in today_consumed_meals
            ):
                logger.info(
                    f"Skipping consumed meal "
                    f"{meal_type} for {meal_date}"
                )
                continue

            nutritional_info = (
                f"Protein: {item['Protein']}g, "
                f"Carbs: {item['Carbohydrates']}g, "
                f"Fat: {item['Fat']}g, "
                f"Fiber: {item['Fiber']}g, "
                f"Sodium: {item['Sodium']}mg, "
                f"Iodine: {item.get('Iodine', 0)}mcg, "
                f"Sugar: {item['Sugar']}g, "
                f"Cholesterol: {item['Cholesterol']}mg, "
                f"Calories: {item['Calories']}kcal"
            )

            insert_rows.append((
                generated_date,
                user_id,
                "Weekly Plan",
                "WEEKLY",
                item["MealType"],
                item["FoodItems"],
                item.get("food_id"),
                json.dumps(item.get("ingredients", [])),
                json.dumps(
                    item.get("recipe", []),
                    ensure_ascii=False
                ),
                item["household_measure"],
                item["household_measure_count"],
                item["household_measure_unit"],
                item["ingredient_total_grams"],
                item["PortionWeight"],
                item.get("PortionWeightUnit"),
                item["Protein"],
                item["Carbohydrates"],
                item["Fat"],
                item["Fiber"],
                item["Sodium"],
                item.get("Iodine", 0),
                item["Sugar"],
                item["Cholesterol"],
                item["Calories"],
                nutritional_info,
                meal_times.get(item["MealType"]),
                meal_date,
                "ACTIVE",
                "System"
            ))

    # =====================================================
    # INSERT INTO DB (SHORT CONNECTION)
    # =====================================================

    conn = None
    cursor = None

    try:

        conn = get_db_connection_dynamic(database_name)
        conn.autocommit = False
        cursor = conn.cursor()

        # NOTE: today_consumed_meals is already populated from
        # the first DB block — no need to re-fetch here.

        cursor.fast_executemany = True

        # -------------------------------------------------
        # DELETE OLD IF PROFILE CHANGED
        # Preserves consumed meal types for today.
        # -------------------------------------------------

        if profile_changed:

            logger.info(
                "Deleting existing meals because profile changed"
            )

            # ==========================================
            # TODAY: KEEP CONSUMED MEAL TYPES
            # ==========================================

            if today_consumed_meals:

                placeholders = ",".join(
                    ["?"] * len(today_consumed_meals)
                )
            
                query = f"""
                    DELETE FROM MealPlanDetails
                    WHERE PatientId = ?
                    AND MealDate = ?
                    AND MealType NOT IN ({placeholders})
                """
            
                params = [
                    user_id,
                    start_date,
                    *today_consumed_meals
                ]
            
                cursor.execute(query, params)
            
                logger.info(
                    f"Preserved meal types: {today_consumed_meals}"
                )
                        
            else:

                cursor.execute("""
                    DELETE FROM MealPlanDetails
                    WHERE PatientId = ?
                    AND MealDate = ?
                """, (
                    user_id,
                    start_date
                ))

                logger.info(
                    f"Deleted all meals for {start_date}"
                )

            # ==========================================
            # FUTURE DAYS: DELETE EVERYTHING
            # ==========================================

            cursor.execute("""
                DELETE FROM MealPlanDetails
                WHERE PatientId = ?
                AND MealDate > ?
                AND MealDate <= ?
            """, (
                user_id,
                start_date,
                end_date
            ))

            logger.info(
                f"Deleted future meals "
                f"from {start_date + timedelta(days=1)} "
                f"to {end_date}"
            )

        # -------------------------------------------------
        # BULK INSERT
        # -------------------------------------------------

        if insert_rows:

            logger.info(
                f"Bulk inserting {len(insert_rows)} rows"
            )

            cursor.executemany("""
                INSERT INTO MealPlanDetails(
                    GeneratedDate,
                    PatientId,
                    PlanName,
                    PlanType,
                    MealType,
                    FoodItems,
                    FoodID,
                    Ingredient,
                    Recipe,
                    household_measure,
                    household_measure_count,
                    household_measure_unit,
                    ingredient_total_grams,
                    PortionWeight,
                    PortionWeightUnit,
                    Protein,
                    Carbohydrates,
                    Fat,
                    Fiber,
                    Sodium,
                    Iodine,
                    Sugar,
                    Cholesterol,
                    Calories,
                    NutritionalInfo,
                    MealTime,
                    MealDate,
                    Status,
                    CreatedBy
                )
                VALUES (
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                )
            """, insert_rows)

        # -------------------------------------------------
        # GROCERY UPSERT
        # -------------------------------------------------

        if grocery_items:

            grocery_json = json.dumps(grocery_items)

            cursor.execute("""
                MERGE GroceryList AS target
                USING (
                    SELECT ? AS PatientId
                ) AS source
                ON target.PatientId = source.PatientId

                WHEN MATCHED THEN
                    UPDATE SET
                        StartDate=?,
                        EndDate=?,
                        GroceryItems=?,
                        RawText='',
                        CreatedBy='System',
                        CreatedAt=GETUTCDATE()

                WHEN NOT MATCHED THEN
                    INSERT (
                        PatientId,
                        StartDate,
                        EndDate,
                        GroceryItems,
                        RawText,
                        CreatedBy
                    )
                    VALUES (?, ?, ?, ?, '', 'System');
            """, (
                user_id,
                start_date,
                end_date,
                grocery_json,
                user_id,
                start_date,
                end_date,
                grocery_json
            ))

        # -------------------------------------------------
        # SAVE TDEE GOAL
        # -------------------------------------------------

        save_tdee_goal(
            cursor=cursor,
            user_id=user_id,
            tdee=tdee,
            start_date=start_date,
            end_date=end_date
        )

        # -------------------------------------------------
        # SINGLE COMMIT
        # -------------------------------------------------

        conn.commit()

        logger.info("Meal plans committed successfully")

    except Exception as e:

        if conn:
            conn.rollback()

        logger.error(
            f"Error saving weekly plan: {str(e)}",
            exc_info=True
        )

        raise HTTPException(
            status_code=500,
            detail="Failed to save meal plan"
        )

    finally:

        if cursor:
            cursor.close()

        if conn:
            conn.close()

    # =====================================================
    # UPDATE CHAT HISTORY
    # =====================================================
    
    try:
    
        updated_constraints = profile.copy()
        updated_constraints["profile_hash"] = current_profile_hash
    
        constraints_json = json.dumps(
            updated_constraints,
            default=json_serializer
        )
    
        # Check if row exists
        existing_row = await execute_query(
            database_name=database_name,
            query="""
                SELECT 1
                FROM [Nauriq].[AI_Formatted_Chat_History]
                WHERE user_id = ?
            """,
            params=(user_id,),
            fetch_type="one"
        )
    
        if existing_row:
    
            await execute_query(
                database_name=database_name,
                query="""
                    UPDATE [Nauriq].[AI_Formatted_Chat_History]
                    SET constraints = ?
                    WHERE user_id = ?
                """,
                params=(
                    constraints_json,
                    user_id
                ),
                fetch_type="none",
                commit=True
            )
    
            logger.info(
                f"Updated chat history for user {user_id}"
            )
    
        else:
    
            await execute_query(
                database_name=database_name,
                query="""
                    INSERT INTO [Nauriq].[AI_Formatted_Chat_History]
                    (
                        user_id,
                        constraints
                    )
                    VALUES (?, ?)
                """,
                params=(
                    user_id,
                    constraints_json
                ),
                fetch_type="none",
                commit=True
            )
    
            logger.info(
                f"Inserted chat history for new user {user_id}"
            )

    except Exception as e:

        logger.error(
            f"Error updating chat history: {str(e)}",
            exc_info=True
        )

    logger.info("==== Weekly Meal Plan API Completed ====")

    return JSONResponse(
        status_code=200,
        content={
            "status": 200,
            "message": "Weekly meal plan processed successfully",
            "profile_changed": profile_changed,
            "re_generated": True,
            "inserted_days": inserted_days,
            "start_date": str(start_date),
            "end_date": str(end_date),
            "days": len(day_blocks),
        }
    )



# ---------------------------------------------------
# MAIN API FOR AUTOMATED WEEKLY MEAL PLAN GENERATION
# ---------------------------------------------------
# @app.post("/automation-weekly-meal-plan")
# async def automate_weekly_meal_plan(
#     request: Request,
#     body: WeeklyMealPlanRequest,
# ):
#     # -------------------------------------------------
#     # VALIDATION
#     # -------------------------------------------------
#     if not body.start_date or not body.end_date:
#         raise HTTPException(status_code=400, detail="start_date and end_date required")

#     try:
#         start_date = datetime.strptime(body.start_date, "%Y-%m-%d").date()
#         end_date = datetime.strptime(body.end_date, "%Y-%m-%d").date()
#     except:
#         raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

#     # ✅ Make end date exclusive
#     end_date_exclusive = end_date + timedelta(days=1)

#     generate_all = body.generate_for_all_users

#     database_name = body.database_name or DYNAMIC_DB_NAME
#     batch_size = body.batch_size or 5
#     conn = get_db_connection_dynamic(database_name)
#     cursor = conn.cursor()

#     try:
#         # -------------------------------------------------
#         # 🔥 FETCH USERS (SMART SQL FILTERING)
#         # -------------------------------------------------
#         if generate_all:
#             cursor.execute("""
#                 DELETE FROM MealPlanDetails
#                 WHERE MealDate >= ? AND MealDate < ?
#             """, (start_date, end_date_exclusive))
        
#             conn.commit()
        
#             print("✅ Existing meal plans deleted for date range")

#         if generate_all:
#             cursor.execute("""
#                 SELECT PatientUserId, Email, PatientFirstName
#                 FROM Patient
#                 WHERE PatientUserId IS NOT NULL 
#                   AND IsActive = 1 
#                   AND EnrollmentStatus = 'ENROLLED'
#                   AND Email IS NOT NULL
#             """)        
#         else:
#             cursor.execute("""
#                 SELECT p.PatientUserId, p.Email, p.PatientFirstName
#                 FROM Patient p
#                 WHERE p.PatientUserId IS NOT NULL 
#                   AND p.IsActive = 1 
#                   AND p.EnrollmentStatus = 'ENROLLED'
#                   AND p.Email IS NOT NULL
#                   AND NOT EXISTS (
#                         SELECT 1
#                         FROM MealPlanDetails m
#                         WHERE m.PatientId = p.PatientUserId
#                           AND m.MealDate >= ? AND m.MealDate < ?
#                         GROUP BY m.PatientId
#                         HAVING COUNT(DISTINCT CAST(m.MealDate AS date)) = 7
#                   )
#             """, (start_date, end_date_exclusive))

#         columns = [col[0] for col in cursor.description]
#         users = [dict(zip(columns, row)) for row in cursor.fetchall()]

#         total_users = len(users)
#         print(f"Users to process: {total_users}")

#         # -------------------------------------------------
#         # 🚀 BATCH PROCESSING
#         # -------------------------------------------------

#         BATCH_SIZE = batch_size

#         for i in range(0, total_users, BATCH_SIZE):
#             batch = users[i:i + BATCH_SIZE]

#             tasks = [
#                 generate_weekly_meal_plan_celery(
#                     start_date,
#                     end_date_exclusive,  # ✅ FIXED
#                     user_id=user["PatientUserId"],
#                     database_name=database_name,
#                 )
#                 for user in batch
#             ]

#             if tasks:
#                 await asyncio.gather(*tasks)

#         return {
#             "message": f"Processed {total_users} users",
#             "mode": "ALL USERS" if generate_all else "ONLY MISSING USERS"
#         }

#     finally:
#         cursor.close()
#         conn.close()


@app.post("/automation-weekly-meal-plan")
async def automate_weekly_meal_plan(
    request: Request,
    body: WeeklyMealPlanRequest,
):
    # -------------------------------------------------
    # VALIDATION
    # -------------------------------------------------
    if not body.start_date or not body.end_date:
        raise HTTPException(status_code=400, detail="start_date and end_date required")

    try:
        start_date = datetime.strptime(body.start_date, "%Y-%m-%d").date()
        end_date = datetime.strptime(body.end_date, "%Y-%m-%d").date()
    except:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

    # ✅ Make end date exclusive
    end_date_exclusive = end_date + timedelta(days=1)

    generate_all = body.generate_for_all_users

    database_name = body.database_name or DYNAMIC_DB_NAME
    batch_size = body.batch_size or 5
    conn = get_db_connection_dynamic(database_name)
    cursor = conn.cursor()

    try:
        # -------------------------------------------------
        # 🔥 FETCH USERS (SMART SQL FILTERING)
        # -------------------------------------------------
        if generate_all:
            cursor.execute("""
                DELETE FROM MealPlanDetails
                WHERE MealDate >= ? AND MealDate < ?
            """, (start_date, end_date_exclusive))
        
            conn.commit()
        
            print("✅ Existing meal plans deleted for date range")

        expected_days = (end_date_exclusive - start_date).days

        if generate_all:
        
            cursor.execute("""
                SELECT
                    PatientUserId,
                    Email,
                    PatientFirstName
                FROM Patient
                WHERE PatientUserId IS NOT NULL
                  AND IsActive = 1
                  AND EnrollmentStatus = 'ENROLLED'
                  AND Email IS NOT NULL
            """)
        
        else:
        
            cursor.execute("""
                SELECT
                    p.PatientUserId,
                    p.Email,
                    p.PatientFirstName
                FROM Patient p
        
                LEFT JOIN (
                    SELECT
                        PatientId,
                        COUNT(DISTINCT MealDate) AS TotalDays
                    FROM MealPlanDetails
                    WHERE MealDate >= ?
                      AND MealDate < ?
                    GROUP BY PatientId
                ) mp
                    ON mp.PatientId = p.PatientUserId
        
                WHERE
                    p.PatientUserId IS NOT NULL
                    AND p.IsActive = 1
                    AND p.EnrollmentStatus = 'ENROLLED'
                    AND p.Email IS NOT NULL
                    AND ISNULL(mp.TotalDays,0) < ?
            """, (
                start_date,
                end_date_exclusive,
                expected_days
            ))
        
        columns = [col[0] for col in cursor.description]
        users = [dict(zip(columns, row)) for row in cursor.fetchall()]

        total_users = len(users)
        print(f"Users to process: {total_users}")

        # -------------------------------------------------
        # 🚀 BATCH PROCESSING
        # -------------------------------------------------

        BATCH_SIZE = batch_size

        for i in range(0, total_users, BATCH_SIZE):
            batch = users[i:i + BATCH_SIZE]

            tasks = [
                generate_weekly_meal_plan_celery(
                    start_date,
                    end_date,  # ✅ FIXED
                    user_id=user["PatientUserId"],
                    database_name=database_name,
                )
                for user in batch
            ]

            if tasks:
                await asyncio.gather(*tasks)

        return {
            "message": f"Processed {total_users} users",
            "mode": "ALL USERS" if generate_all else "ONLY MISSING USERS"
        }

    finally:
        cursor.close()
        conn.close()
