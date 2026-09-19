import json
from ai.ask_ai import ProcessQueryRequest, handle_process_query
from datetime import  datetime, timezone
import logging
from helpers.database import  get_db_connection_dynamic
from helpers.utils import DYNAMIC_DB_NAME, extract_date_from_day, get_user_profile_v2,  parse_meal_plan_text_to_items, split_weekly_meal_plan, validate_profile_for_meal_plan

logger = logging.getLogger(__name__)

# =========================================================
# OPTIMIZED CELERY WEEKLY MEAL PLAN GENERATOR
# =========================================================

async def generate_weekly_meal_plan_celery(
    start_date,
    end_date,
    user_id: str,
    database_name: str,
    token: str = None
):

    conn = None
    cursor = None

    try:

        logger.info(
            f"[START] Processing meal plan for user {user_id}"
        )

        # =================================================
        # STEP 1: FETCH PROFILE (SHORT DB CONNECTION)
        # =================================================

        conn = get_db_connection_dynamic(database_name)

        conn.autocommit = False

        cursor = conn.cursor()

        profile = get_user_profile_v2(
            user_id=user_id,
            cursor=cursor
        )

        is_valid, error_message = validate_profile_for_meal_plan(
            profile
        )

        if not is_valid:

            logger.warning(
                f"Skipping user {user_id}: {error_message}"
            )

            return

        # -------------------------------------------------
        # CLOSE CONNECTION BEFORE AI
        # -------------------------------------------------

        cursor.close()
        conn.close()

        cursor = None
        conn = None

        # =================================================
        # STEP 2: AI CALL (NO DB CONNECTION ACTIVE)
        # =================================================

        logger.info(
            f"[AI] Generating weekly meal plan for {user_id}"
        )

        prompt = (
            "Generate a weekly meal plan "
            "for me based on my profile."
        )

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
            background_tasks=None
        )

        raw_text = ai_data.get("answer", "")

        grocery_items = ai_data.get(
            "grocery_list_json",
            []
        )

        if not raw_text or "Day 1" not in raw_text:

            logger.warning(
                f"Invalid AI response for user {user_id}"
            )

            return

        # =================================================
        # STEP 3: SPLIT DAYS
        # =================================================

        day_blocks = split_weekly_meal_plan(raw_text)

        if not day_blocks:

            logger.warning(
                f"No valid day blocks for user {user_id}"
            )

            return

        logger.info(
            f"User {user_id} | Parsed days: {len(day_blocks)}"
        )

        # =================================================
        # STEP 4: PREPARE INSERT DATA
        # =================================================

        meal_times = {
            "Breakfast": "08:00:00",
            "MorningSnack": "10:00:00",
            "Lunch": "13:00:00",
            "EveningSnack": "16:00:00",
            "Dinner": "19:00:00"
        }

        insert_rows = []

        generated_date = datetime.now(
            timezone.utc
        ).date()

        for day_block in day_blocks:

            meal_date = extract_date_from_day(day_block)

            if not meal_date:
                continue

            items = parse_meal_plan_text_to_items(
                day_block
            )

            if not items:

                logger.warning(
                    f"No items for {meal_date} | User {user_id}"
                )

                continue

            logger.info(
                f"Meal date: {meal_date} | "
                f"Items: {len(items)}"
            )

            for item in items:

                ingredient_json = json.dumps(
                    item.get("ingredients", []),
                    ensure_ascii=False
                )

                recipe_json = json.dumps(
                    item.get("recipe", []),
                    ensure_ascii=False
                )

                nutritional_info = (
                    f"Protein: {item['Protein']}g, "
                    f"Carbs: {item['Carbohydrates']}g, "
                    f"Fat: {item['Fat']}g, "
                    f"Fiber: {item['Fiber']}g, "
                    f"Sodium: {item['Sodium']}mg, "
                    f"Iodine: {item.get('Iodine',0)}mcg, "
                    f"Sugar: {item['Sugar']}g, "
                    f"Cholesterol: {item['Cholesterol']}mg, "
                    f"Calories: {item['Calories']}kcal"
                )

                # =========================================
                # OPTIONAL DEBUG LOGS
                # =========================================

                logger.info(
                    f"""
                    Lengths ->
                    FoodItems={len(str(item["FoodItems"]))}
                    Ingredient={len(ingredient_json)}
                    Recipe={len(recipe_json)}
                    NutritionalInfo={len(nutritional_info)}
                    """
                )

                insert_rows.append((
                    generated_date,
                    user_id,
                    "Weekly Plan",
                    "WEEKLY",
                    item["MealType"],
                    item["FoodItems"],
                    item.get("food_id"),

                    ingredient_json,

                    recipe_json,

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

                    meal_times.get(
                        item["MealType"]
                    ),

                    meal_date,

                    "ACTIVE",
                    "System"
                ))

        logger.info(
            f"Prepared {len(insert_rows)} rows "
            f"for user {user_id}"
        )

        # =================================================
        # STEP 5: SAVE TO DB
        # =================================================

        conn = get_db_connection_dynamic(database_name)

        conn.autocommit = False

        cursor = conn.cursor()

        # =================================================
        # DELETE EXISTING RANGE
        # =================================================

        logger.info(
            f"Deleting existing weekly meals "
            f"for user {user_id}"
        )

        cursor.execute("""
            DELETE FROM MealPlanDetails
            WHERE PatientId=?
            AND MealDate >= ?
            AND MealDate < ?
        """, (
            user_id,
            start_date,
            end_date
        ))

        # =================================================
        # INSERT ROWS ONE BY ONE
        # (avoids pyodbc truncation bug)
        # =================================================

        if insert_rows:

            logger.info(
                f"Inserting {len(insert_rows)} rows "
                f"for user {user_id}"
            )

            insert_query = """
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
            """

            for idx, row in enumerate(insert_rows):

                try:

                    cursor.execute(
                        insert_query,
                        row
                    )

                except Exception as row_error:

                    logger.error(
                        f"Failed row index: {idx}"
                    )

                    logger.error(
                        f"Failed row data: {row}"
                    )

                    raise row_error

        # =================================================
        # STEP 6: GROCERY UPSERT
        # =================================================

        if grocery_items:

            grocery_json = json.dumps(
                grocery_items,
                ensure_ascii=False
            )

            logger.info(
                f"Saving grocery list for user {user_id}"
            )

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

        # =================================================
        # STEP 7: COMMIT
        # =================================================

        conn.commit()

        logger.info(
            f"[SUCCESS] Meal plan generated "
            f"for user {user_id}"
        )

    except Exception as e:

        logger.error(
            f"[ERROR] User {user_id}: {str(e)}",
            exc_info=True
        )

        if conn:
            conn.rollback()

    finally:

        try:
            if cursor:
                cursor.close()
        except:
            pass

        try:
            if conn:
                conn.close()
        except:
            pass



async def generate_swap_weekly_meal_plan_celery(
    start_date,
    end_date,
    user_id: str,
    database_name: str,
    token: str = None
):

    conn = None
    cursor = None

    try:

        logger.info(
            f"[START] Processing meal plan for user {user_id}"
        )

        # =================================================
        # STEP 1: FETCH PROFILE (SHORT DB CONNECTION)
        # =================================================

        conn = get_db_connection_dynamic(database_name)

        conn.autocommit = False

        cursor = conn.cursor()

        profile = get_user_profile_v2(
            user_id=user_id,
            cursor=cursor
        )

        is_valid, error_message = validate_profile_for_meal_plan(
            profile
        )

        if not is_valid:

            logger.warning(
                f"Skipping user {user_id}: {error_message}"
            )

            return

        # -------------------------------------------------
        # CLOSE CONNECTION BEFORE AI
        # -------------------------------------------------

        cursor.close()
        conn.close()

        cursor = None
        conn = None

        # =================================================
        # STEP 2: AI CALL (NO DB CONNECTION ACTIVE)
        # =================================================

        logger.info(
            f"[AI] Generating weekly meal plan for {user_id}"
        )

        prompt = (
            "Generate a weekly meal plan "
            "for me based on my profile."
        )

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
            background_tasks=None
        )

        raw_text = ai_data.get("answer", "")

        grocery_items = ai_data.get(
            "grocery_list_json",
            []
        )

        if not raw_text or "Day 1" not in raw_text:

            logger.warning(
                f"Invalid AI response for user {user_id}"
            )

            return

        # =================================================
        # STEP 3: SPLIT DAYS
        # =================================================

        day_blocks = split_weekly_meal_plan(raw_text)

        if not day_blocks:

            logger.warning(
                f"No valid day blocks for user {user_id}"
            )

            return

        logger.info(
            f"User {user_id} | Parsed days: {len(day_blocks)}"
        )

        # =================================================
        # STEP 4: PREPARE INSERT DATA
        # =================================================

        meal_times = {
            "Breakfast": "08:00:00",
            "MorningSnack": "10:00:00",
            "Lunch": "13:00:00",
            "EveningSnack": "16:00:00",
            "Dinner": "19:00:00"
        }

        insert_rows = []

        generated_date = datetime.now(
            timezone.utc
        ).date()

        for day_block in day_blocks:

            meal_date = extract_date_from_day(day_block)

            if not meal_date:
                continue

            items = parse_meal_plan_text_to_items(
                day_block
            )

            if not items:

                logger.warning(
                    f"No items for {meal_date} | User {user_id}"
                )

                continue

            logger.info(
                f"Meal date: {meal_date} | "
                f"Items: {len(items)}"
            )

            for item in items:

                ingredient_json = json.dumps(
                    item.get("ingredients", []),
                    ensure_ascii=False
                )

                recipe_json = json.dumps(
                    item.get("recipe", []),
                    ensure_ascii=False
                )

                nutritional_info = (
                    f"Protein: {item['Protein']}g, "
                    f"Carbs: {item['Carbohydrates']}g, "
                    f"Fat: {item['Fat']}g, "
                    f"Fiber: {item['Fiber']}g, "
                    f"Sodium: {item['Sodium']}mg, "
                    f"Iodine: {item.get('Iodine',0)}mcg, "
                    f"Sugar: {item['Sugar']}g, "
                    f"Cholesterol: {item['Cholesterol']}mg, "
                    f"Calories: {item['Calories']}kcal"
                )

                # =========================================
                # OPTIONAL DEBUG LOGS
                # =========================================

                logger.info(
                    f"""
                    Lengths ->
                    FoodItems={len(str(item["FoodItems"]))}
                    Ingredient={len(ingredient_json)}
                    Recipe={len(recipe_json)}
                    NutritionalInfo={len(nutritional_info)}
                    """
                )

                insert_rows.append((
                    generated_date,
                    user_id,
                    "Weekly Plan",
                    "WEEKLY",
                    item["MealType"],
                    item["FoodItems"],
                    item.get("food_id"),

                    ingredient_json,

                    recipe_json,

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

                    meal_times.get(
                        item["MealType"]
                    ),

                    meal_date,

                    "ACTIVE",
                    "System"
                ))

        logger.info(
            f"Prepared {len(insert_rows)} rows "
            f"for user {user_id}"
        )

        # =================================================
        # STEP 5: SAVE TO DB
        # =================================================

        conn = get_db_connection_dynamic(database_name)

        conn.autocommit = False

        cursor = conn.cursor()

        # # =================================================
        # # DELETE EXISTING RANGE
        # # =================================================

        # logger.info(
        #     f"Deleting existing weekly meals "
        #     f"for user {user_id}"
        # )

        # cursor.execute("""
        #     DELETE FROM MealPlanDetails
        #     WHERE PatientId=?
        #     AND MealDate >= ?
        #     AND MealDate < ?
        # """, (
        #     user_id,
        #     start_date,
        #     end_date
        # ))

        # =================================================
        # INSERT ROWS ONE BY ONE
        # (avoids pyodbc truncation bug)
        # =================================================

        if insert_rows:

            logger.info(
                f"Inserting {len(insert_rows)} rows "
                f"for user {user_id}"
            )

            insert_query = """
                INSERT INTO AlternativeMealPlanDetails(
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
            """

            for idx, row in enumerate(insert_rows):

                try:

                    cursor.execute(
                        insert_query,
                        row
                    )

                except Exception as row_error:

                    logger.error(
                        f"Failed row index: {idx}"
                    )

                    logger.error(
                        f"Failed row data: {row}"
                    )

                    raise row_error

        # =================================================
        # STEP 6: GROCERY UPSERT
        # =================================================

        if grocery_items:

            grocery_json = json.dumps(
                grocery_items,
                ensure_ascii=False
            )

            logger.info(
                f"Saving grocery list for user {user_id}"
            )

            cursor.execute("""
                MERGE AlternativeGroceryList AS target
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

        # =================================================
        # STEP 7: COMMIT
        # =================================================

        conn.commit()

        logger.info(
            f"[SUCCESS] Meal plan generated "
            f"for user {user_id}"
        )

    except Exception as e:

        logger.error(
            f"[ERROR] User {user_id}: {str(e)}",
            exc_info=True
        )

        if conn:
            conn.rollback()

    finally:

        try:
            if cursor:
                cursor.close()
        except:
            pass

        try:
            if conn:
                conn.close()
        except:
            pass