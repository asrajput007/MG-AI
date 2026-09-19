import requests
import logging
import os

logger = logging.getLogger(__name__)

DUCKLING_URL = os.getenv("DUCKLING_URL", "https://friskaaiccm-api-testing.nouriq.ai/duckling/parse")

_warmed = False

def warm_up_duckling():
    global _warmed
    if _warmed:
        return

    try:
        response = requests.post(
            DUCKLING_URL,
            data={"text": "today", "dims": '["time"]'},
            timeout=5.0
        )
        if response.status_code == 200:
            _warmed = True
            logger.info("Duckling warmed up successfully")
        else:
            logger.warning(f"Duckling warm-up failed with status {response.status_code}")
    except Exception as e:
        logger.warning(f"Duckling warm-up failed: {e}")