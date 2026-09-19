from datetime import datetime, timedelta
import json
import tempfile
import uuid
from celery import group
import pandas as pd
from pydantic import BaseModel
import pytz
from helpers.automation_script import generate_weekly_meal_plan_celery
from helpers.database import get_db_connection_dynamic
from helpers.utils import   MASTER_TABLE_DB_NAME, send_email, weekly_automation_meal_plan_html_completed_email
import asyncio
import logging
from celery.signals import worker_process_init
import time
from celery_app import celery_app
from notification_service import send_push_notification 


logger = logging.getLogger(__name__)

TEST_MODE = False  # Set to True to process only 1 user per tenant for testing purposes

@worker_process_init.connect
def setup_worker(sender, **kwargs):
    """Preload AI models and dependencies when worker starts"""
    print("=" * 60)
    print("PRELOADING AI MODELS...")
    print("=" * 60)
    
    try:
        # ============================================================
        # OPTIMIZATION: Disable Audio Models (Save RAM/Startup Time)
        # We inject mocks so `ai.ask_ai` skips loading Whisper/Kokoro
        # ============================================================
        import sys
        from unittest.mock import MagicMock

        # Mock faster_whisper
        sys.modules["faster_whisper"] = MagicMock()
        sys.modules["faster_whisper"].WhisperModel = MagicMock(side_effect=Exception("Audio models disabled in Celery"))

        # Mock kokoro
        sys.modules["kokoro"] = MagicMock()
        sys.modules["kokoro"].KPipeline = MagicMock(side_effect=Exception("Audio models disabled in Celery"))
        
        print("Audio models disabled via mocking.")

        # Force import of all AI modules to trigger model loading
        print("Loading agent_core...")
        from ai import agent_core
        
        print("Loading fitness module...")
        from ai import fitness
        
        print("Loading nutrition module...")
        from ai import nutrition
        
        print("Loading tool_core...")
        from ai import tool_core
        
        print("Loading ask_ai...")
        from ai.ask_ai import handle_process_query
        
        print("=" * 60)
        print("ALL MODELS LOADED AND READY!")
        print("=" * 60)
        
    except Exception as e:
        print(f"Warning: Model preload failed: {e}")
        print("Worker will continue but first task may be slower.")

#testing task to verify if celery is working on server
@celery_app.task
def test_celery_setup():
    conn = get_db_connection_dynamic("FriskaAiCCM_HFWL")
    cursor = conn.cursor()
    patients = "f8ab0e55-f76e-4cef-af92-cf40da17220a"

    # ---- Fetch active device token ----
    cursor.execute("""
        SELECT TOP 1 DeviceToken
        FROM UserDeviceToken
        WHERE UserId = ? AND IsActive = 1
    """, (patients,))
    row = cursor.fetchone()
    fcm_token = row[0] if row else None
    
     # 🔹 Send push
    send_push_notification(
         token=fcm_token,
         title="Celery is working properly - CCM MG PROD",
         body="This is a test notification to confirm Celery is working properly.",
        )
    return "Test notification sent successfully"



def get_test_user_from_tenant(database_name):
    conn = get_db_connection_dynamic(database_name)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT TOP 1 PatientUserId
        FROM Patient
        WHERE IsActive = 1
          AND EnrollmentStatus = 'ENROLLED'
        ORDER BY NEWID()
    """)

    row = cursor.fetchone()

    cursor.close()
    conn.close()

    return row.PatientUserId if row else None

def get_multi_tenancy_db_name():
    conn = get_db_connection_dynamic(
        MASTER_TABLE_DB_NAME
    )
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT
            TenantId,
            TenantName,
            DisplayName,
            Tenantdbname
        FROM Tenants
        WHERE IsActive = 1
          AND Tenantdbname IS NOT NULL
    """)
    
    tenants = cursor.fetchall()
    cursor.close()
    conn.close()

    return tenants

#Weekly meal plan Automation tasks 
@celery_app.task
def process_tenant_weekly_meal_plan(
    tenant_id,
    tenant_name,
    display_name,
    database_name,
    week_start_date,
    week_end_date
):

    logger.info(
        f"""
        ==================================================
        STARTING TENANT PROCESSING

        TenantId={tenant_id}
        TenantName={tenant_name}
        Database={database_name}
        ==================================================
        """
    )

    run_id = str(uuid.uuid4())

    started_at = datetime.utcnow()

    start_time = time.time()

    conn = None
    cursor = None

    try:

        conn = get_db_connection_dynamic(
            database_name
        )

        cursor = conn.cursor()

        # =====================================================
        # CREATE MAIN RUN LOG
        # =====================================================

        cursor.execute("""
            INSERT INTO MealPlanAutomationLog
            (
                RunId,
                TenantId,
                TenantName,
                StartedAt,
                WeekStartDate,
                WeekEndDate,
                Status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            run_id,
            tenant_id,
            tenant_name,
            started_at,
            week_start_date,
            week_end_date,
            "RUNNING"
        ))

        conn.commit()

        # =====================================================
        # FETCH USERS
        # =====================================================

        if TEST_MODE:

            logger.info(
                f"[TEST MODE] Selecting 1 user from {tenant_name}"
            )

            cursor.execute("""
                SELECT TOP 1 PatientUserId
                FROM Patient
                WHERE IsActive = 1
                  AND EnrollmentStatus = 'ENROLLED'
                ORDER BY NEWID()
            """)

        else:

            cursor.execute("""
                SELECT PatientUserId
                FROM Patient
                WHERE IsActive = 1
                  AND EnrollmentStatus = 'ENROLLED'
            """)

        user_ids = [
            row.PatientUserId
            for row in cursor.fetchall()
        ]

        total_users = len(user_ids)

        logger.info(
            f"""
            Tenant={tenant_name}
            Database={database_name}
            TotalUsers={total_users}
            TEST_MODE={TEST_MODE}
            """
        )

        if total_users == 0:

            logger.warning(
                f"No eligible users found for tenant {tenant_name}"
            )

            cursor.execute("""
                UPDATE MealPlanAutomationLog
                SET Status='NO_USERS'
                WHERE RunId=?
            """, (run_id,))

            conn.commit()

            return

        cursor.close()
        conn.close()

        cursor = None
        conn = None

        # =====================================================
        # DISPATCH USER TASKS
        # =====================================================

        BATCH_SIZE = 8

        for i in range(0, total_users, BATCH_SIZE):

            batch = user_ids[i:i + BATCH_SIZE]

            logger.info(
                f"""
                Dispatching Batch

                Tenant={tenant_name}
                BatchSize={len(batch)}
                """
            )

            group(
                generate_weekly_meal_plan_for_user.s(
                    run_id,
                    uid,
                    database_name,
                    tenant_name,
                    total_users,
                    start_time,
                    started_at.isoformat(),
                    str(week_start_date),
                    str(week_end_date)
                )
                for uid in batch
            ).apply_async(
                queue="meal_plan"
            )

        logger.info(
            f"""
            ==================================================
            TENANT DISPATCH COMPLETE

            Tenant={tenant_name}
            RunId={run_id}
            Users={total_users}
            ==================================================
            """
        )

        return {
            "tenant_id": tenant_id,
            "tenant_name": tenant_name,
            "database_name": database_name,
            "run_id": run_id,
            "total_users": total_users,
            "week_start_date": str(week_start_date),
            "week_end_date": str(week_end_date),
            "test_mode": TEST_MODE
        }

    except Exception as e:

        logger.error(
            f"""
            TENANT PROCESS FAILED

            Tenant={tenant_name}
            Database={database_name}
            Error={str(e)}
            """,
            exc_info=True
        )

        try:

            if conn:

                conn.rollback()

                cursor.execute("""
                    UPDATE MealPlanAutomationLog
                    SET Status='FAILED'
                    WHERE RunId=?
                """, (run_id,))

                conn.commit()

        except Exception:
            pass

        raise

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

@celery_app.task
def dispatch_weekly_meal_plan_batches():

    today = datetime.utcnow().date()

    days_until_sunday = (6 - today.weekday()) % 7

    if days_until_sunday == 0:
        days_until_sunday = 7

    week_start_date = today + timedelta(days=days_until_sunday)
    week_end_date = week_start_date + timedelta(days=6)

    tenants = get_multi_tenancy_db_name()

    for tenant in tenants:

        process_tenant_weekly_meal_plan.delay(
            tenant.TenantId,
            tenant.TenantName,
            tenant.DisplayName,
            tenant.Tenantdbname,
            str(week_start_date),
            str(week_end_date)
        )

    return {
        "total_tenants": len(tenants),
        "week_start_date": str(week_start_date),
        "week_end_date": str(week_end_date)
    }
@celery_app.task(
    bind=True,
    acks_late=True,
    soft_time_limit=600,
    time_limit=900
)
def generate_weekly_meal_plan_for_user(
    self,
    run_id: str,
    user_id: str,
    database_name: str,
    tenant_name: str,
    total_users: int,
    start_time: float,
    started_at: str,
    week_start_date: str,
    week_end_date: str
):

    result = {}

    week_status = "FAILED"

    final_status = "FAILED"

    status_code = 500

    combined_error = None

    try:

        week_start_date_obj = datetime.strptime(
            week_start_date,
            "%Y-%m-%d"
        ).date()

        week_end_date_obj = datetime.strptime(
            week_end_date,
            "%Y-%m-%d"
        ).date()

        # =================================================
        # CALL CORE FUNCTION
        # =================================================

        result = asyncio.run(
            generate_weekly_meal_plan_celery(
                start_date=week_start_date_obj,
                end_date=week_end_date_obj,
                user_id=user_id,
                database_name=database_name
            )
        )

        week_status = "GENERATED"

        final_status = "SUCCESS"

        status_code = 200

    except Exception as e:

        combined_error = str(e)

        week_status = "FAILED"

        final_status = "FAILED"

        status_code = 500

    # =====================================================
    # SAVE USER LOG
    # =====================================================

    try:

        conn = get_db_connection_dynamic(database_name)

        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO MealPlanAutomationUserLog
            (
                RunId,
                UserId,
                GeneratedAt,
                StatusCode,
                WeekStatus,
                WeekStartDate,
                WeekEndDate,
                ErrorMessage,
                ResponseJson
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            run_id,
            user_id,
            datetime.utcnow(),
            status_code,
            week_status,
            week_start_date,
            week_end_date,
            combined_error,
            json.dumps(result, default=str)
        ))

        conn.commit()

        # =================================================
        # COUNT COMPLETED
        # =================================================

        cursor.execute("""
            SELECT COUNT(*)
            FROM MealPlanAutomationUserLog
            WHERE RunId = ?
        """, run_id)

        completed_users = cursor.fetchone()[0]

        # =================================================
        # SINGLE COMPLETION LOCK
        # =================================================

        if completed_users == total_users:

            cursor.execute("""
                UPDATE MealPlanAutomationLog
                SET Status='PROCESSING_COMPLETION'
                WHERE RunId=?
                AND Status='RUNNING'
            """, run_id)

            conn.commit()

             # VERIFY STATUS AFTER UPDATE
            cursor.execute("""
                SELECT Status
                FROM MealPlanAutomationLog
                WHERE RunId=?
            """, run_id)
        
            status_row = cursor.fetchone()
        
            current_status = (
                status_row[0]
                if status_row
                else None
            )
        
            logger.info(
                f"""
                COMPLETION STATUS CHECK
        
                RunId={run_id}
                CurrentStatus={current_status}
                CompletedUsers={completed_users}
                TotalUsers={total_users}
                """
            )
        
            if current_status == "PROCESSING_COMPLETION":
        
                logger.info(
                    f"""
                    TRIGGERING COMPLETION TASK
        
                    RunId={run_id}
                    """
                )
        
                automation_completed_weekly.delay(
                    run_id,
                    database_name,
                    tenant_name,
                    start_time,
                    started_at,
                    total_users
                )
        
        cursor.close()

        conn.close()

    except Exception as db_err:

        print(
            f"[DB LOG ERROR] user={user_id} error={db_err}"
        )

    return {
        "user_id": user_id,
        "week_status": week_status,
        "final_status": final_status
    }
def generate_run_log_excel_weekly(
    run_id,
    database_name
):

    conn = get_db_connection_dynamic(
        database_name
    )

    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            UserId,
            GeneratedAt,
            StatusCode,
            WeekStatus,
            WeekStartDate,
            WeekEndDate,
            ErrorMessage,
            ResponseJson
        FROM MealPlanAutomationUserLog
        WHERE RunId = ?
        ORDER BY GeneratedAt
    """, run_id)

    rows = cursor.fetchall()

    columns = [
        column[0]
        for column in cursor.description
    ]

    df = pd.DataFrame.from_records(
        rows,
        columns=columns
    )

    cursor.close()

    conn.close()

    tmp_file = tempfile.NamedTemporaryFile(
        delete=False,
        suffix=".xlsx"
    )

    df.to_excel(
        tmp_file.name,
        index=False
    )

    return tmp_file.name

@celery_app.task
def automation_completed_weekly(
    run_id,
    database_name,
    tenant_name,
    start_time,
    started_at,
    total_users
):

    finished_at = datetime.utcnow()

    total_time = round(
        time.time() - start_time,
        2
    )

    conn = get_db_connection_dynamic(
        database_name
    )

    cursor = conn.cursor()

    # =====================================================
    # COUNTS
    # =====================================================

    cursor.execute("""
        SELECT
            SUM(
                CASE
                    WHEN WeekStatus='GENERATED'
                    THEN 1
                    ELSE 0
                END
            ),

            SUM(
                CASE
                    WHEN WeekStatus='SKIPPED'
                    THEN 1
                    ELSE 0
                END
            ),

            SUM(
                CASE
                    WHEN StatusCode=500
                    THEN 1
                    ELSE 0
                END
            )

        FROM MealPlanAutomationUserLog
        WHERE RunId = ?
    """, run_id)

    row = cursor.fetchone()

    generated_week = row[0] or 0

    skipped = row[1] or 0

    failed = row[2] or 0

    # =====================================================
    # FETCH WEEK DATES
    # =====================================================

    cursor.execute("""
        SELECT
            WeekStartDate,
            WeekEndDate
        FROM MealPlanAutomationLog
        WHERE RunId=?
    """, run_id)

    dates_row = cursor.fetchone()

    week_start_date = dates_row[0]

    week_end_date = dates_row[1]

    # =====================================================
    # FINAL STATUS
    # =====================================================

    run_status = "COMPLETED"

    if failed > 0:
        run_status = "PARTIAL"

    # =====================================================
    # UPDATE MAIN LOG
    # =====================================================

    cursor.execute("""
        UPDATE MealPlanAutomationLog
        SET
            FinishedAt = ?,
            TotalUsers = ?,
            GeneratedWeekCount = ?,
            SkippedCount = ?,
            FailedCount = ?,
            TotalTimeSec = ?,
            TotalTimeMin = ?,
            Status = ?
        WHERE RunId = ?
    """, (
        finished_at,
        total_users,
        generated_week,
        skipped,
        failed,
        total_time,
        round(total_time / 60, 2),
        run_status,
        run_id
    ))

    conn.commit()

    cursor.close()

    conn.close()

    # =====================================================
    # REPORT
    # =====================================================

    report = {
        "started_at": str(started_at),
        "finished_at": str(finished_at),
        "week_start_date": str(week_start_date),
        "week_end_date": str(week_end_date),
        "total_users": total_users,
        "generated_week": generated_week,
        "skipped": skipped,
        "failed": failed,
        "total_time_seconds": total_time,
        "status": run_status
    }

    recipients = [
        "Shaheen.Ansari@nouriq.ai",
        "ayush.rajput@nouriq.ai",
        "Sashank.Donepudi@friska.ai",
        "jahnavi.maddipatla@friska.ai",
        "bhargavi.n@friska.ai",
        "Ajay.Kumar@nouriq.ai",
        "Ramandeep.saha@nouriq.ai",
        "Vali.Shaikh@friska.ai"
    ]

    html_body = weekly_automation_meal_plan_html_completed_email(
        report
    )

    subject_status = (
        "SUCCESS"
        if run_status == "COMPLETED"
        else "PARTIAL"
    )

    excel_file = generate_run_log_excel_weekly(
        run_id,
        database_name
    )

    try:

        prefix = "[TEST]" if TEST_MODE else ""
        send_email(
            subject=f"{prefix} - Weekly Meal Plan Automation Report - {subject_status} - {tenant_name}",
            body=html_body,
            to_emails=recipients,
            attachment_path=excel_file
        )
        logger.info(
            f"EMAIL SENT SUCCESSFULLY | RunId={run_id}"
        )
    except Exception as email_err:

        logger.error(
            f"EMAIL FAILED | RunId={run_id} | Error={str(email_err)}",
            exc_info=True
        )

    return report

