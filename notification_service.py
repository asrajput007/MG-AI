# notification_service.py
import firebase_admin
from firebase_admin import credentials, messaging
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Initialize Firebase
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)

def send_push_notification(token, title, body, data=None):
    try:
        message = messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=body
            ),
            token=token,
            data=data or {}
        )
        response = messaging.send(message)
        logger.info(f"Notification sent to {token}: {response}")
        return True
    except Exception as e:
        logger.error(f"Failed to send notification: {str(e)}")
        return False