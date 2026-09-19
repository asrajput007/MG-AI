import sys
import os
import asyncio
import logging
import json

# Add the project root to sys.path to allow imports
project_root = r"D:\Friska\HFWL\Production\NouriqAi-Friska-CCM"
if project_root not in sys.path:
    sys.path.append(project_root)

from helpers.utils import prepare_fitness_video_notification_for_user, log_notification, DYNAMIC_DB_NAME
from notification_service import send_push_notification

# Configure logging to console
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("TestFitnessNotif")

async def test_single_user_notification(user_id, database_name):
    logger.info(f"🚀 Starting test for user: {user_id} on DB: {database_name}")
    
    try:
        # 1. Processing (Profile fetch, Safety Check, Video selection)
        logger.info("--- Step 1: Processing Profile & Safety Check ---")
        result = prepare_fitness_video_notification_for_user(user_id, database_name)
        
        if not result:
            logger.error("❌ Failed: No result returned from preparation.")
            return
            
        if "error" in result:
            logger.warning(f"🛑 SAFETY STOP: {result['error']}")
            logger.info("✅ SUCCESS: The system correctly identified the safety condition and BLOCKED the notification.")
            return

        token = result.get("fcm_token")
        if not token:
            logger.error("❌ Failed: No FCM token found for this user.")
            return

        body_data = result["body"]
        display_title = "Let's get moving"
        display_body = result.get("notification_msg", "Workout waiting! Tap to start.")

        logger.info(f"✅ Safety check passed. Preparing to send push.")
        logger.info(f"   Message: {display_body}")
        logger.info(f"   Data Payload: {json.dumps(body_data, indent=2)}")

        # 2. Push Notification
        logger.info("--- Step 2: Sending Push Notification ---")
        success = send_push_notification(
            token=token,
            title=display_title,
            body=display_body,
            data=body_data
        )

        if success:
            logger.info("✅ Push sent successfully via Firebase.")
        else:
            logger.warning("⚠️ Push delivery failed. Still logging to database for history.")

        # 3. Logging
        logger.info("--- Step 3: Logging to Database ---")
        log_notification(
            user_id=user_id,
            title=display_title,
            body=display_body,
            notification_type=8,
            data=body_data,
            database_name=database_name
        )
        logger.info("✅ Notification logged successfully.")

    except Exception as e:
        logger.error(f"💥 Unexpected Error: {str(e)}", exc_info=True)

async def process_notification_sequence(
    user_id,
    database_name,
    count=15,
    delay_seconds=1
):
    """
    Sends notifications sequentially for SAME user.

    Example:
    - count=10 -> sends 10 notifications
    - count=15 -> sends 15 notifications
    """

    logger.info(
        f"🚀 Starting sequential notification test "
        f"for user {user_id}"
    )

    logger.info(f"📦 Total notifications to send: {count}")

    for i in range(1, count + 1):

        logger.info("\n" + "=" * 80)
        logger.info(f"📨 Sending Notification {i}/{count}")
        logger.info("=" * 80)

        try:

            await test_single_user_notification(
                user_id,
                database_name
            )

            logger.info(
                f"✅ Notification {i}/{count} completed"
            )

        except Exception as e:

            logger.error(
                f"💥 Notification {i}/{count} failed: {str(e)}",
                exc_info=True
            )

        # delay between sends
        if i < count:
            logger.info(
                f"⏳ Waiting {delay_seconds}s before next notification..."
            )

            await asyncio.sleep(delay_seconds)

    logger.info("\n🎉 Sequential notification test completed.")

async def test_manual_notification(user_id, database_name, custom_title, custom_body, custom_data):
    logger.info(f"🚀 Starting MANUAL test for user: {user_id}")
    
    try:
        from helpers.database import get_db_connection_dynamic
        conn = get_db_connection_dynamic(database_name)
        cursor = conn.cursor()
        
        # Fetch token
        cursor.execute("SELECT TOP 1 DeviceToken FROM UserDeviceToken WHERE UserId = ? AND IsActive = 1", (user_id,))
        row = cursor.fetchone()
        token = row[0] if row else None
        
        if not token:
            logger.error("❌ Failed: No FCM token found.")
            return

        # Send Push
        success = send_push_notification(
            token=token,
            title=custom_title,
            body=custom_body,
            data=custom_data
        )

        if success:
            logger.info("✅ Push sent successfully via Firebase.")
        else:
            logger.warning("⚠️ Push delivery failed. Still logging to database.")

        # Log to DB
        log_notification(
            user_id=user_id,
            title=custom_title,
            body=custom_body,
            notification_type=8,
            data=custom_data,
            database_name=database_name
        )
        logger.info("✅ Notification logged successfully.")

    except Exception as e:
        logger.error(f"💥 Error in manual test: {str(e)}", exc_info=True)

if __name__ == "__main__":
    # --- CONFIGURATION ---
    # Real processing based on User ID
    # Priority: Command line argument > Default ID
    if len(sys.argv) > 1:
        TEST_USER_ID = sys.argv[1]
    else:
        # TEST_USER_ID = "b20b2298-a559-4775-9208-017f243ce8ce" #JO
        # TEST_USER_ID = "68e54701-210b-475f-b6d8-eae7c9052775" #bhragavi
        # TEST_USER_ID = "4351b124-1aa8-4b16-8a40-f5d6ef2ceb60"
    #    TEST_USER_ID = "a21a8d1c-27fe-433e-a2ca-12d69371edc7" #shaheen
    #    TEST_USER_ID = "eb46eda2-8533-403d-a8f9-1d769d298775" #Ayush
    #  TEST_USER_ID = "8be988c1-682f-4b6a-91ef-a0964a5c491a" #Anandu , Ashwini
    #    TEST_USER_ID = "790332a3-bb50-4b9d-8024-fae2230ef7b1"
      TEST_USER_ID = "c06c6318-40b8-4334-a64f-6df2bb7b7e2e" #Misba UAT
    #    TEST_USER_ID = "759138b7-fe10-4fb1-ab94-ab3f4e13dccd" #Chris UAT
        
    DB_NAME = DYNAMIC_DB_NAME            
    
    # Trigger Real Processing (Profile Fetch -> Safety Check -> Video Match -> Push)
    # asyncio.run(test_single_user_notification(TEST_USER_ID, DB_NAME))
NOTIFICATION_COUNT = 15
DELAY_SECONDS = 1

asyncio.run(
    process_notification_sequence(
        TEST_USER_ID,
        DB_NAME,
        count=NOTIFICATION_COUNT,
        delay_seconds=DELAY_SECONDS
    )
)