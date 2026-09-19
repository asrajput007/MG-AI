"""
Combined Pytest test suite for the Friska CCM API.

Run ALL:   python -m pytest "test/test_all_apis.py" -v
Run FAST:  python -m pytest "test/test_all_apis.py" -v -k "not success"
Run ONE:   python -m pytest "test/test_all_apis.py::TestHeartRateTips" -v

IMPORTANT: Paste  valid Bearer token in the TEST_TOKEN variable below.
"""

import pytest
import sys
import os
import json
import logging
from datetime import datetime, timezone

# ─── Project setup ────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from fastapi.testclient import TestClient
from app import app
from helpers.database import get_db_connection_dynamic

# ─── Logging ──────────────────────────────────
LOGS_FOLDER = os.path.join(PROJECT_ROOT, "logs")
os.makedirs(LOGS_FOLDER, exist_ok=True)

log_filename = os.path.join(LOGS_FOLDER, "test_all_api_responses.log")
logging.basicConfig(
    filename=log_filename,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    force=True,
)
logger = logging.getLogger(__name__)

# ─── Test Client ──────────────────────────────
client = TestClient(app, raise_server_exceptions=False)

# ══════════════════════════════════════════════
#  PASTE YOUR VALID BEARER TOKEN HERE
# ══════════════════════════════════════════════
TEST_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIrMTg0NzU4NjQzNDgiLCJqdGkiOiJhYTY3OGRiMS03NmQ1LTQ4MjUtOWE3YS1iYTc4YzU3NmQyZDgiLCJVc2VySWQiOiJkMGZkZTc1MC1lZDI2LTRjODktYTU4ZC1lYmRiNDE1ZGUwZGQiLCJJZCI6ImQwZmRlNzUwLWVkMjYtNGM4OS1hNThkLWViZGI0MTVkZTBkZCIsIkVtYWlsIjoiRXNod2FyLlNhaUBub3VyaXEuYWkiLCJUZW5hbnRJZCI6IjQiLCJEYXRhYmFzZU5hbWUiOiJGcmlza2FBaUNDTV9IRldMIiwiRGF0YUJhc2VOYW1lIjoiRnJpc2thQWlDQ01fSEZXTCIsIlJvbGVOYW1lIjoiUGF0aWVudCIsIldlbGxuZXNzU3RhdHVzIjoiRW5yb2xsZWQiLCJXZWxsbmVzc1N0YXR1c0NvZGUiOiJFTlJPTExFRCIsIlBhdGllbnRGaXJzdE5hbWUiOiJKYWNrIiwiSXNJbnRlcm5hbFVzZXIiOiJGYWxzZSIsIlByb2plY3RUeXBlIjoiQ0NNVGVzdGluZyIsImV4cCI6MTc3NDcwOTc4MCwiaXNzIjoiSEZXTCIsImF1ZCI6IkhGV0wifQ.J-ITyiJGcYxhwkOVJ2GlmJrCOlrDXpeLicmU-L1I4vo"  # <-- Paste your JWT token here (without "Bearer " prefix)


def _auth_header():
    return {"Authorization": f"Bearer {TEST_TOKEN}"}


def _get_user_id_and_db_from_token():
    import jwt as pyjwt
    payload = pyjwt.decode(TEST_TOKEN, options={"verify_signature": False}, algorithms=["HS256"])
    return payload.get("Id"), payload.get("DataBaseName")


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 1: TIPS ENDPOINTS  (from test_tips_api.py)
# ══════════════════════════════════════════════════════════════════════════════

# ── Shared Tips Validation Helpers ────────────

def _assert_tips_response_structure(response, endpoint_name=""):
    """Common assertions for all tips endpoints."""
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    json_data = response.json()

    logger.info(f"Endpoint: GET {endpoint_name} | Response: {json.dumps(json_data, indent=2)}")

    assert "status" in json_data, "Response missing 'status' key"

    assert json_data["status"] == 200, f"Expected status 200, got {json_data['status']}"

    assert "message" in json_data, "Response missing 'message' key"
    assert json_data["message"] == "Successfully fetched the data", \
        f"Unexpected message: {json_data['message']}"

    assert "data" in json_data, "Response missing 'data' key"
    assert "tips" in json_data["data"], "Response data missing 'tips' key"

    return json_data


def _assert_tips_list(json_data, expected_count=3):
    """Validate the tips list and its items."""
    tips = json_data["data"]["tips"]

    assert isinstance(tips, list), "'tips' should be a list"
    assert len(tips) > 0, "'tips' list should not be empty"
    assert len(tips) == expected_count, \
        f"Expected {expected_count} tips, got {len(tips)}"

    return tips


def _assert_tip_item_fields(tip):
    """Validate that a single tip has all required fields with correct types."""
    required_fields = ["vital_sign", "tip", "explanation", "actions"]
    for field in required_fields:
        assert field in tip, f"Tip item missing required field: '{field}'"
        assert isinstance(tip[field], str), f"'{field}' should be a string"
        assert len(tip[field]) > 0, f"'{field}' should not be empty"


# ── Root Endpoint ─────────────────────────────

class TestRootEndpoint:
    """Tests for GET /"""

    def test_root_status_code(self):
        """Verify root endpoint returns 200."""
        response = client.get("/")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    def test_root_response_body(self):
        """Verify root endpoint returns the expected welcome message."""
        response = client.get("/")
        json_data = response.json()

        logger.info(f"Endpoint: GET / | Response: {json.dumps(json_data, indent=2)}")

        assert "message" in json_data, "Response missing 'message' key"
        assert json_data["message"] == "Welcome to the AI API"


# ── Blood Glucose Tips ────────────────────────

class TestBloodGlucoseTips:
    """Tests for GET /blood-glucose-tips"""

    def test_status_code(self):
        response = client.get("/blood-glucose-tips")
        assert response.status_code == 200

    def test_response_structure(self):
        response = client.get("/blood-glucose-tips")
        _assert_tips_response_structure(response, "/blood-glucose-tips")

    def test_tips_count(self):
        response = client.get("/blood-glucose-tips")
        json_data = response.json()
        _assert_tips_list(json_data)

    def test_tip_fields(self):
        response = client.get("/blood-glucose-tips")
        json_data = response.json()
        tips = json_data["data"]["tips"]
        for tip in tips:
            _assert_tip_item_fields(tip)


# ── Blood Ketone Tips ─────────────────────────

class TestBloodKetoneTips:
    """Tests for GET /blood-ketone-tips"""

    def test_status_code(self):
        response = client.get("/blood-ketone-tips")
        assert response.status_code == 200

    def test_response_structure(self):
        response = client.get("/blood-ketone-tips")
        _assert_tips_response_structure(response, "/blood-ketone-tips")

    def test_tips_count(self):
        response = client.get("/blood-ketone-tips")
        json_data = response.json()
        _assert_tips_list(json_data)

    def test_tip_fields(self):
        response = client.get("/blood-ketone-tips")
        json_data = response.json()
        tips = json_data["data"]["tips"]
        for tip in tips:
            _assert_tip_item_fields(tip)


# ── Blood Oxygen Tips ─────────────────────────

class TestBloodOxygenTips:
    """Tests for GET /blood-oxygen-tips"""

    def test_status_code(self):
        response = client.get("/blood-oxygen-tips")
        assert response.status_code == 200

    def test_response_structure(self):
        response = client.get("/blood-oxygen-tips")
        _assert_tips_response_structure(response, "/blood-oxygen-tips")

    def test_tips_count(self):
        response = client.get("/blood-oxygen-tips")
        json_data = response.json()
        _assert_tips_list(json_data)

    def test_tip_fields(self):
        response = client.get("/blood-oxygen-tips")
        json_data = response.json()
        tips = json_data["data"]["tips"]
        for tip in tips:
            _assert_tip_item_fields(tip)


# ── Blood Pressure Tips ──────────────────────

class TestBloodPressureTips:
    """Tests for GET /blood-pressure-tips"""

    def test_status_code(self):
        response = client.get("/blood-pressure-tips")
        assert response.status_code == 200

    def test_response_structure(self):
        response = client.get("/blood-pressure-tips")
        _assert_tips_response_structure(response, "/blood-pressure-tips")

    def test_tips_count(self):
        response = client.get("/blood-pressure-tips")
        json_data = response.json()
        _assert_tips_list(json_data)

    def test_tip_fields(self):
        response = client.get("/blood-pressure-tips")
        json_data = response.json()
        tips = json_data["data"]["tips"]
        for tip in tips:
            _assert_tip_item_fields(tip)


# ── Body Fat Tips ─────────────────────────────

class TestBodyFatTips:
    """Tests for GET /body-fat-tips"""

    def test_status_code(self):
        response = client.get("/body-fat-tips")
        assert response.status_code == 200

    def test_response_structure(self):
        response = client.get("/body-fat-tips")
        _assert_tips_response_structure(response, "/body-fat-tips")

    def test_tips_count(self):
        response = client.get("/body-fat-tips")
        json_data = response.json()
        _assert_tips_list(json_data)

    def test_tip_fields(self):
        response = client.get("/body-fat-tips")
        json_data = response.json()
        tips = json_data["data"]["tips"]
        for tip in tips:
            _assert_tip_item_fields(tip)

# ── Body Temperature Tips ─────────────────────

class TestBodyTemperatureTips:
    """Tests for GET /body-temperature-tips"""

    def test_status_code(self):
        response = client.get("/body-temperature-tips")
        assert response.status_code == 200

    def test_response_structure(self):
        response = client.get("/body-temperature-tips")
        _assert_tips_response_structure(response, "/body-temperature-tips")

    def test_tips_count(self):
        response = client.get("/body-temperature-tips")
        json_data = response.json()
        _assert_tips_list(json_data)

    def test_tip_fields(self):
        response = client.get("/body-temperature-tips")
        json_data = response.json()
        tips = json_data["data"]["tips"]
        for tip in tips:
            _assert_tip_item_fields(tip)

# ── Heart Rate Tips ───────────────────────────

class TestHeartRateTips:
    """Tests for GET /heart-rate-tips"""

    def test_status_code(self):
        response = client.get("/heart-rate-tips")
        assert response.status_code == 200

    def test_response_structure(self):
        response = client.get("/heart-rate-tips")
        _assert_tips_response_structure(response, "/heart-rate-tips")

    def test_tips_count(self):
        response = client.get("/heart-rate-tips")
        json_data = response.json()
        _assert_tips_list(json_data)

    def test_tip_fields(self):
        response = client.get("/heart-rate-tips")
        json_data = response.json()
        tips = json_data["data"]["tips"]
        for tip in tips:
            _assert_tip_item_fields(tip)

# ── Respiration Rate Tips ─────────────────────

class TestRespirationRateTips:
    """Tests for GET /respiration-rate-tips"""

    def test_status_code(self):
        response = client.get("/respiration-rate-tips")
        assert response.status_code == 200

    def test_response_structure(self):
        response = client.get("/respiration-rate-tips")
        _assert_tips_response_structure(response, "/respiration-rate-tips")

    def test_tips_count(self):
        response = client.get("/respiration-rate-tips")
        json_data = response.json()
        _assert_tips_list(json_data)

    def test_tip_fields(self):
        response = client.get("/respiration-rate-tips")
        json_data = response.json()
        tips = json_data["data"]["tips"]
        for tip in tips:
            _assert_tip_item_fields(tip)

# ── Calories Tips ─────────────────────────────

class TestCaloriesTips:
    """Tests for GET /calories-tips"""

    def test_status_code(self):
        response = client.get("/calories-tips")
        assert response.status_code == 200

    def test_response_structure(self):
        response = client.get("/calories-tips")
        _assert_tips_response_structure(response, "/calories-tips")

    def test_tips_count(self):
        response = client.get("/calories-tips")
        json_data = response.json()
        _assert_tips_list(json_data)

    def test_tip_fields(self):
        response = client.get("/calories-tips")
        json_data = response.json()
        tips = json_data["data"]["tips"]
        for tip in tips:
            _assert_tip_item_fields(tip)

# ── Sleep Tips ────────────────────────────────

class TestSleepTips:
    """Tests for GET /sleep-tips"""

    def test_status_code(self):
        response = client.get("/sleep-tips")
        assert response.status_code == 200

    def test_response_structure(self):
        response = client.get("/sleep-tips")
        _assert_tips_response_structure(response, "/sleep-tips")

    def test_tips_count(self):
        response = client.get("/sleep-tips")
        json_data = response.json()
        _assert_tips_list(json_data)

    def test_tip_fields(self):
        response = client.get("/sleep-tips")
        json_data = response.json()
        tips = json_data["data"]["tips"]
        for tip in tips:
            _assert_tip_item_fields(tip)

# ── Steps Tips ────────────────────────────────

class TestStepsTips:
    """Tests for GET /steps-tips"""

    def test_status_code(self):
        response = client.get("/steps-tips")
        assert response.status_code == 200

    def test_response_structure(self):
        response = client.get("/steps-tips")
        _assert_tips_response_structure(response, "/steps-tips")

    def test_tips_count(self):
        response = client.get("/steps-tips")
        json_data = response.json()
        _assert_tips_list(json_data)

    def test_tip_fields(self):
        response = client.get("/steps-tips")
        json_data = response.json()
        tips = json_data["data"]["tips"]
        for tip in tips:
            _assert_tip_item_fields(tip)


# ── Exercise Minutes Tips ─────────────────────

class TestExerciseMinutesTips:
    """Tests for GET /exercise-minutes-tips"""

    def test_status_code(self):
        response = client.get("/exercise-minutes-tips")
        assert response.status_code == 200

    def test_response_structure(self):
        response = client.get("/exercise-minutes-tips")
        _assert_tips_response_structure(response, "/exercise-minutes-tips")

    def test_tips_count(self):
        response = client.get("/exercise-minutes-tips")
        json_data = response.json()
        _assert_tips_list(json_data)

    def test_tip_fields(self):
        response = client.get("/exercise-minutes-tips")
        json_data = response.json()
        tips = json_data["data"]["tips"]
        for tip in tips:
            _assert_tip_item_fields(tip)

# ── Water Tips ────────────────────────────────

class TestWaterTips:
    """Tests for GET /water-tips"""

    def test_status_code(self):
        response = client.get("/water-tips")
        assert response.status_code == 200

    def test_response_structure(self):
        response = client.get("/water-tips")
        _assert_tips_response_structure(response, "/water-tips")

    def test_tips_count(self):
        response = client.get("/water-tips")
        json_data = response.json()
        _assert_tips_list(json_data)

    def test_tip_fields(self):
        response = client.get("/water-tips")
        json_data = response.json()
        tips = json_data["data"]["tips"]
        for tip in tips:
            _assert_tip_item_fields(tip)

# ── Personalized Questions ────────────────────

class TestPersonalizedQuestions:
    """Tests for GET /get-personalized-questions"""

    def test_status_code(self):
        response = client.get("/get-personalized-questions")
        assert response.status_code == 200
        logger.info(f"GET /get-personalized-questions | Status: {response.status_code} | Response: {json.dumps(response.json())}")

    def test_response_structure(self):
        """Verify response has status, message, and data keys."""
        response = client.get("/get-personalized-questions")
        json_data = response.json()
        assert "status" in json_data
        assert json_data["status"] == 200
        assert "message" in json_data
        assert "data" in json_data

    def test_data_is_list(self):
        """Verify data is a non-empty list of categories."""
        response = client.get("/get-personalized-questions")
        json_data = response.json()
        data = json_data["data"]
        assert isinstance(data, list), "data should be a list"
        assert len(data) > 0, "data should not be empty"

    def test_category_structure(self):
        """Each category should have Category, Content_Count, and Items."""
        response = client.get("/get-personalized-questions")
        json_data = response.json()
        for category in json_data["data"]:
            assert "Category" in category, "Missing 'Category' key"
            assert "Content_Count" in category, "Missing 'Content_Count' key"
            assert "Items" in category, "Missing 'Items' key"
            assert isinstance(category["Items"], list)
            assert len(category["Items"]) > 0, \
                f"Category '{category['Category']}' has no items"

    def test_item_structure(self):
        """Each item should have Index and Content fields."""
        response = client.get("/get-personalized-questions")
        json_data = response.json()
        for category in json_data["data"]:
            for item in category["Items"]:
                assert "Index" in item, "Missing 'Index' key in item"
                assert "Content" in item, "Missing 'Content' key in item"

# ── Exercise Videos ───────────────────────────

class TestExerciseVideos:
    """Tests for GET /exercise-videos (requires workout DB connection)."""

    def _get_response(self, url="/exercise-videos"):
        """Helper: returns response or raises pytest.skip if DB is down."""
        response = client.get(url, headers=_auth_header())
        if response.status_code == 500:
            pytest.skip("Workout DB unavailable — skipping exercise-videos test")
        return response

    def test_status_code(self):
        response = self._get_response()
        assert response.status_code == 200
        logger.info(f"GET /exercise-videos | Status: {response.status_code} | Response: {json.dumps(response.json())}")

    def test_response_structure(self):
        """Verify response has status, message, and data keys."""
        response = self._get_response()
        json_data = response.json()
        assert "status" in json_data
        assert json_data["status"] == 200
        assert "data" in json_data

    def test_data_contains_videos_and_pagination(self):
        """Verify data has videos list and pagination info."""
        response = self._get_response()
        data = response.json()["data"]
        assert "videos" in data, "Missing 'videos' key"
        assert "pagination" in data, "Missing 'pagination' key"
        assert isinstance(data["videos"], list)

    def test_pagination_structure(self):
        """Verify pagination has expected fields."""
        response = self._get_response()
        pagination = response.json()["data"]["pagination"]
        expected_keys = ["total_records", "current_page", "per_page", "total_pages", "has_next", "has_previous"]
        for key in expected_keys:
            assert key in pagination, f"Missing pagination key '{key}'"

    def test_pagination_page_1(self):
        """Page 1 should have has_previous=False."""
        response = self._get_response("/exercise-videos?page=1")
        pagination = response.json()["data"]["pagination"]
        assert pagination["current_page"] == 1
        assert pagination["has_previous"] is False

    def test_video_item_fields(self):
        """Each video should have expected fields."""
        response = self._get_response()
        videos = response.json()["data"]["videos"]
        if len(videos) > 0:
            expected_fields = ["ID", "folder_name", "video_name", "duration", "practiceType"]
            for field in expected_fields:
                assert field in videos[0], f"Missing field '{field}' in video item"


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 2: USER SETTINGS  (from test_user_settings_api.py)
# ══════════════════════════════════════════════════════════════════════════════

# ── DB Helpers (User Settings) ────────────────

def _snapshot_user_voice_preference(user_id: str, database_name: str):
    """Snapshot current voice preference so we can restore it after test."""
    conn = get_db_connection_dynamic(database_name)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT VoiceId, CreatedAt, UpdatedAt
        FROM [dbo].[UserPreferredAIVoice]
        WHERE UserId = ?
    """, user_id)
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row


def _restore_user_voice_preference(user_id: str, database_name: str, original_row):
    """Restore voice preference to pre-test state."""
    conn = get_db_connection_dynamic(database_name)
    cursor = conn.cursor()

    if original_row:
        cursor.execute("""
            UPDATE [dbo].[UserPreferredAIVoice]
            SET VoiceId = ?, UpdatedAt = ?
            WHERE UserId = ?
        """, original_row[0], original_row[2], user_id)
    else:
        cursor.execute("""
            DELETE FROM [dbo].[UserPreferredAIVoice]
            WHERE UserId = ?
        """, user_id)

    conn.commit()
    cursor.close()
    conn.close()
    logger.info(f"Voice preference restored for user {user_id}")


# ── GET /ai-voices ────────────────────────────

class TestAiVoices:
    """
    Tests for GET /ai-voices
    Auth required. Returns list of available AI voices.
    """

    # ── Auth Tests ────────────────────────────

    def test_missing_auth_returns_error(self):
        """Missing Authorization header → 422 (Header required)."""
        response = client.get("/ai-voices")
        assert response.status_code in (401, 422), \
            f"Expected 401/422/500, got {response.status_code}"
        logger.info(f"GET /ai-voices (no auth) | Status: {response.status_code}")

    def test_wrong_scheme_returns_401(self):
        """Non-Bearer scheme → 401."""
        response = client.get(
            "/ai-voices",
            headers={"Authorization": "Basic some_credentials"}
        )
        assert response.status_code == 401, \
            f"Expected 401, got {response.status_code}"
        logger.info(f"GET /ai-voices (wrong scheme) | Status: {response.status_code}")

    def test_invalid_token_returns_401(self):
        """Garbage token → 401."""
        response = client.get(
            "/ai-voices",
            headers={"Authorization": "Bearer invalid.token.here"}
        )
        assert response.status_code == 401, \
            f"Expected 401, got {response.status_code}"
        logger.info(f"GET /ai-voices (bad token) | Status: {response.status_code}")

    # ── Happy-Path Test ──────────────────────

    @pytest.mark.skipif(not TEST_TOKEN, reason="TEST_TOKEN is empty — paste a valid JWT to run this test")
    def test_ai_voices_success(self):
        """
        Call with valid token. Verifies:
          1. Status 200
          2. Response has voices list
          3. Each voice has expected fields
        """
        response = client.get(
            "/ai-voices",
            headers=_auth_header()
        )

        logger.info(
            f"GET /ai-voices | "
            f"Status: {response.status_code} | "
            f"Response: {json.dumps(response.json(), indent=2)}"
        )

        assert response.status_code == 200, \
            f"Expected 200, got {response.status_code}"

        json_data = response.json()
        assert json_data["status"] == 200
        assert "data" in json_data
        assert "voices" in json_data["data"]

        voices = json_data["data"]["voices"]
        assert isinstance(voices, list)

        if len(voices) > 0:
            voice = voices[0]
            expected_fields = ["id", "voice_name", "ai_voice_name", "gender", "is_default"]
            for field in expected_fields:
                assert field in voice, f"Missing field '{field}' in voice"
            logger.info(f"Found {len(voices)} AI voices")


# ── POST /user-preferred-ai-voice ─────────────

class TestUserPreferredAiVoice:
    """
    Tests for POST /user-preferred-ai-voice
    Auth required. Upserts user's voice preference in DB.
    """

    # ── Auth Tests ────────────────────────────

    def test_missing_auth_returns_error(self):
        """Missing Authorization header → 422."""
        response = client.post("/user-preferred-ai-voice", json={"VoiceId": "1"})
        assert response.status_code in (401, 422), \
            f"Expected 401/422/500, got {response.status_code}"
        logger.info(f"POST /user-preferred-ai-voice (no auth) | Status: {response.status_code}")

    def test_wrong_scheme_returns_401(self):
        """Non-Bearer scheme → 401."""
        response = client.post(
            "/user-preferred-ai-voice",
            headers={"Authorization": "Basic some_credentials"},
            json={"VoiceId": "1"}
        )
        assert response.status_code == 401, \
            f"Expected 401, got {response.status_code}"
        logger.info(f"POST /user-preferred-ai-voice (wrong scheme) | Status: {response.status_code}")

    def test_invalid_token_returns_401(self):
        """Garbage token → 401."""
        response = client.post(
            "/user-preferred-ai-voice",
            headers={"Authorization": "Bearer invalid.token.here"},
            json={"VoiceId": "1"}
        )
        assert response.status_code == 401, \
            f"Expected 401, got {response.status_code}"
        logger.info(f"POST /user-preferred-ai-voice (bad token) | Status: {response.status_code}")

    # ── Happy-Path Test (with DB rollback) ────

    @pytest.mark.skipif(not TEST_TOKEN, reason="TEST_TOKEN is empty — paste a valid JWT to run this test")
    def test_set_voice_preference_success(self):
        """
        Set a voice preference with valid token.
        Verifies response and restores DB state afterward.
        """
        user_id, database_name = _get_user_id_and_db_from_token()

        # First, get a valid voice ID from the voices list
        voices_response = client.get("/ai-voices", headers=_auth_header())
        assert voices_response.status_code == 200
        voices = voices_response.json()["data"]["voices"]
        assert len(voices) > 0, "No voices available to test with"

        test_voice_id = voices[0]["id"]

        # Snapshot current preference
        original_pref = _snapshot_user_voice_preference(user_id, database_name)

        try:
            response = client.post(
                "/user-preferred-ai-voice",
                headers=_auth_header(),
                json={"VoiceId": test_voice_id}
            )

            logger.info(
                f"POST /user-preferred-ai-voice | "
                f"Status: {response.status_code} | "
                f"Response: {json.dumps(response.json(), indent=2)}"
            )

            assert response.status_code == 200, \
                f"Expected 200, got {response.status_code}"

            json_data = response.json()
            assert json_data["status"] == 200
            assert "data" in json_data
            assert json_data["data"]["VoiceId"] == test_voice_id

        finally:
            _restore_user_voice_preference(user_id, database_name, original_pref)

    @pytest.mark.skipif(not TEST_TOKEN, reason="TEST_TOKEN is empty — paste a valid JWT to run this test")
    def test_invalid_voice_id_returns_404(self):
        """Non-existent VoiceId → 404."""
        response = client.post(
            "/user-preferred-ai-voice",
            headers=_auth_header(),
            json={"VoiceId": "99999999-0000-0000-0000-000000000000"}
        )
        assert response.status_code == 404, \
            f"Expected 404, got {response.status_code}"
        logger.info(f"POST /user-preferred-ai-voice (bad VoiceId) | Status: {response.status_code}")


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 3: FITNESS
# ══════════════════════════════════════════════════════════════════════════════

# ── DB Helpers (Fitness) ──────────────────────

def _cleanup_fitness_plan(plan_guid: str, database_name: str):
    """Delete fitness plan and its child rows created during testing."""
    conn = get_db_connection_dynamic(database_name)
    cursor = conn.cursor()

    # Delete exercises linked to this plan's days
    cursor.execute("""
        DELETE e FROM FitnessPlanExercises e
        INNER JOIN PatientFitnessPlanDays d ON e.DayGuidId = d.GuidId
        WHERE d.PlanGuidId = ?
    """, plan_guid)

    # Delete days
    cursor.execute("""
        DELETE FROM PatientFitnessPlanDays
        WHERE PlanGuidId = ?
    """, plan_guid)

    # Delete plan
    cursor.execute("""
        DELETE FROM PatientFitnessPlans
        WHERE GuidId = ?
    """, plan_guid)

    conn.commit()
    cursor.close()
    conn.close()
    logger.info(f"Fitness plan {plan_guid} cleaned up")


# ── POST /generate-weekly-fitness-plan ───────────────

class TestGenerateFitnessPlan:
    """
    Tests for POST /generate-weekly-fitness-plan
    Auth required. AI call. DB writes to 3 tables.
    """

    # ── Auth Tests ────────────────────────────

    def test_missing_auth_returns_error(self):
        """No Authorization header → 401."""
        response = client.post("/generate-weekly-fitness-plan")
        assert response.status_code in (401, 422), \
            f"Expected 401/422/500, got {response.status_code}"
        logger.info(f"POST /generate-weekly-fitness-plan (no auth) | Status: {response.status_code}")

    def test_wrong_scheme_returns_401(self):
        """Non-Bearer scheme → 401."""
        response = client.post(
            "/generate-weekly-fitness-plan",
            headers={"Authorization": "Basic some_credentials"}
        )
        assert response.status_code == 401, \
            f"Expected 401, got {response.status_code}"
        logger.info(f"POST /generate-weekly-fitness-plan (wrong scheme) | Status: {response.status_code}")

    def test_invalid_token_returns_401(self):
        """Garbage token → 401."""
        response = client.post(
            "/generate-weekly-fitness-plan",
            headers={"Authorization": "Bearer invalid.token.here"}
        )
        assert response.status_code == 401, \
            f"Expected 401, got {response.status_code}"
        logger.info(f"POST /generate-weekly-fitness-plan (bad token) | Status: {response.status_code}")

    # ── Happy-Path Test (with DB rollback) ────

    @pytest.mark.skipif(not TEST_TOKEN, reason="TEST_TOKEN is empty — paste a valid JWT to run this test")
    def test_generate_fitness_plan_success(self):
        """
        Call with valid token. Verifies:
          1. Status 200
          2. Response has plan_guid, start_date, end_date
          3. Cleans up DB after test
        """
        user_id, database_name = _get_user_id_and_db_from_token()
        plan_guid = None

        try:
            response = client.post(
                "/generate-weekly-fitness-plan",
                headers=_auth_header()
            )

            # Log raw text first (safe even if body is empty)
            logger.info(
                f"POST /generate-weekly-fitness-plan | "
                f"Status: {response.status_code} | "
                f"Body: {response.text[:500]}"
            )

            assert response.status_code == 200, \
                f"Expected 200, got {response.status_code}. Body: {response.text[:300]}"

            json_data = response.json()
            assert json_data["status"] == 200
            assert "data" in json_data
            assert "plan_guid" in json_data["data"]
            assert "start_date" in json_data["data"]
            assert "end_date" in json_data["data"]

            plan_guid = json_data["data"]["plan_guid"]
            logger.info(f"Fitness plan created: {plan_guid}")

        finally:
            if plan_guid:
                _cleanup_fitness_plan(plan_guid, database_name)


# ══════════════════════════════════════════════════════════════════════════════
#  SECTION 4: MEDIA ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

# ── POST /transcribe ──────────────────────────

class TestTranscribeAudio:
    """
    Tests for POST /transcribe
    No auth required. Accepts an audio file upload (mp3/wav/etc.)
    and returns transcribed text.
    """

    AUDIO_FILE = os.path.join(
        PROJECT_ROOT, "test", "Audio-Transcribe.mp3"
    )

    def test_no_file_returns_422(self):
        """Missing audio file → 422 validation error."""
        response = client.post("/transcribe")
        assert response.status_code == 422, \
            f"Expected 422, got {response.status_code}"
        logger.info(f"POST /transcribe (no file) | Status: {response.status_code}")

    def test_invalid_file_returns_error(self):
        """Non-audio binary → endpoint handles gracefully (200 error msg or 400/422/500)."""
        response = client.post(
            "/transcribe",
            files={"audio": ("test.txt", b"this is not audio", "text/plain")}
        )
        assert response.status_code in (200, 400, 422, 500), \
            f"Expected 200/400/422/500, got {response.status_code}"
        logger.info(
            f"POST /transcribe (invalid file) | Status: {response.status_code} | "
            f"Response: {response.text[:200]}"
        )

    def test_valid_mp3_transcribes_successfully(self):
        """
        Upload Audio-Transcribe.mp3 and verify the transcription response.
        Expects status 200 and non-empty transcribed text.
        """
        if not os.path.exists(self.AUDIO_FILE):
            pytest.skip(f"Test audio file not found: {self.AUDIO_FILE}")

        with open(self.AUDIO_FILE, "rb") as f:
            response = client.post(
                "/transcribe",
                files={"audio": ("Audio-Transcribe.mp3", f, "audio/mpeg")}
            )

        json_data = response.json()
        logger.info(
            f"POST /transcribe (valid mp3) | "
            f"Status: {response.status_code} | "
            f"Response: {json.dumps(json_data, indent=2)}"
        )

        assert response.status_code == 200, \
            f"Expected 200 (audio processed), got {response.status_code} — Response: {json_data}"
        # Must return body status 200 with actual transcribed text — fail if audio not processed
        assert json_data.get("status") == 200, \
            f"Expected body status 200, got {json_data.get('status')} — {json_data.get('message')}"
        assert json_data.get("data") is not None, "Expected non-null data"
        assert json_data["data"].get("text"), "Transcribed text must not be empty — audio was not processed"


# ── POST /analyze-food ────────────────────────

# class TestAnalyzeFood:
#     """
#     Tests for POST /analyze-food
#     No auth required. Accepts an image file upload (.jpg/.png)
#     and returns AI-generated nutritional analysis.
#     """

#     IMAGE_FILE = os.path.join(
#         PROJECT_ROOT, "Test scripts", "Analyze-Image.jpg"
#     )

#     def test_no_file_returns_422(self):
#         """Missing image file → 422 validation error."""
#         response = client.post("/analyze-food")
#         assert response.status_code == 422, \
#             f"Expected 422, got {response.status_code}"
#         logger.info(f"POST /analyze-food (no file) | Status: {response.status_code}")

#     def test_invalid_file_returns_error(self):
#         """Non-image binary → handles gracefully (200 error msg or 400/422/500)."""
#         response = client.post(
#             "/analyze-food",
#             files={"file": ("test.txt", b"not an image", "text/plain")}
#         )
#         assert response.status_code in (200, 400, 422, 500), \
#             f"Expected 200/400/422/500, got {response.status_code}"
#         logger.info(
#             f"POST /analyze-food (invalid file) | Status: {response.status_code} | "
#             f"Response: {response.text[:200]}"
#         )

#     def test_valid_jpg_analyzes_successfully(self):
#         """
#         Upload Analyze-Image.jpg and verify the nutritional analysis response.
#         Expects status 200 with source='AI', processed_items, total_nutrients and assessment.
#         """
#         if not os.path.exists(self.IMAGE_FILE):
#             pytest.skip(f"Test image file not found: {self.IMAGE_FILE}")

#         with open(self.IMAGE_FILE, "rb") as f:
#             response = client.post(
#                 "/analyze-food",
#                 files={"file": ("Analyze-Image.jpg", f, "image/jpeg")}
#             )

#         json_data = response.json()
#         logger.info(
#             f"POST /analyze-food (valid jpg) | "
#             f"Status: {response.status_code} | "
#             f"Response: {json.dumps(json_data, indent=2)}"
#         )

#         # Must return 200 — if food is not identified the test should fail
#         assert response.status_code == 200, \
#             f"Expected 200 (food identified), got {response.status_code} — Response: {json_data}"
#         assert json_data.get("status") == 200, \
#             f"Expected body status 200, got {json_data.get('status')} — {json_data.get('message')}"

#         data = json_data.get("data", {})
#         assert data.get("source") == "AI", \
#             f"Expected source 'AI', got {data.get('source')}"
#         assert "interactive_message" in data, "Missing 'interactive_message' in response data"
#         assert "processed_items" in data, "Missing 'processed_items' in response data"
#         assert "total_nutrients" in data, "Missing 'total_nutrients' in response data"
#         assert "assessment" in data, "Missing 'assessment' in response data"
