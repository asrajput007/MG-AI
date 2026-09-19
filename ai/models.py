from pydantic import BaseModel
from typing import Optional, List, Dict, Any


class FriskaChatDBRequest(BaseModel):
    user_id: Optional[Any] = None
    query: str
    chat_history: Optional[List[Dict]] = []
    current_constraints: Optional[Dict] = None
    last_agent_context: Optional[Dict] = None
    force_tool_type: Optional[str] = None
    auth_token: Optional[str] = None

class FriskaChatDBRequestWithImage(BaseModel):
    query: Optional[str] = None
    image: Optional[str] = None
    last_agent_context: Optional[dict] = None

class TranslationUpdateRequest(BaseModel):
    doc_id: str
    translation: dict 

class DietaryProfileRequest(BaseModel):
    has_digestive_issues: str | None = None
    dietary_preference_codes: List[str] = []
    dietary_restriction_codes: List[str] = []
    food_allergy_codes: List[str] = []
    digestive_issue_codes: List[str] = []
    other_digestive_issues: List[str] = []
    symptom_aggravating_food_codes: List[str] = []
    preferred_cuisines: List[str] = []
    height: float | None = None
    weight: float | None = None
    waist:  float | None = None

    diagnosis_codes: List[str] | None = None
    diagnosis_notes: str | None = None
    severity: str | None = None
    diagnosed_by: str | None = None

    sittingADay: str | None = None
    physical_activity_level: str | None = None
    uninterruptedSleepHour: int | None = None
    stressLevel: str | None = None
    smokerCode: str | None = None
    smokingStatus: str | None = None
    consumeAlcoholCode: str | None = None
    drinkingStatus: str | None = None
    medicalReqCode: str | None = None
