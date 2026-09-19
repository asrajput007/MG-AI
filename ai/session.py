import re
import os
import uuid
import requests
import json
import warnings
import logging
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, ValidationError
from datetime import date as DateType, time as TimeType, datetime as DateTimeClass
from dateutil.parser import parse as parse_date
import time
import asyncio
from .tool_core import BaseTool, ToolType, ToolResult, LLMService
from functools import partial
import json
logger = logging.getLogger(__name__)

class BaseHealthData(BaseModel):
    user_id: str = Field(default="user_health_123")
    record_date: DateType
    record_time: TimeType
    data_type: str

class SessionBookingRecord(BaseModel):
    user_id: str = Field(default="user_booking_123")
    user_name: str
    expert_name: str
    session_date: DateType
    time_slot: TimeType
    session_type: str
    appointment_type: str
    reminder: str = "15 minutes before"

class UnifiedConversationState(BaseModel):
    intent: Optional[str] = None
    data_type: Optional[str] = None
    record_date: Optional[DateType] = None
    record_time: Optional[TimeType] = None
    user_name: Optional[str] = None
    expert_name: Optional[str] = None
    session_date: Optional[DateType] = None
    time_slot: Optional[TimeType] = None
    session_type: Optional[str] = None
    appointment_type: Optional[str] = None
    reminder: Optional[str] = None
    reminder_pending: bool = False
    confirmation_pending: bool = False
    tracking_complete: bool = False
    conversation_history: List[dict] = []
    bp_systolic: Optional[int] = None
    bp_diastolic: Optional[int] = None
    water_consumption: Optional[float] = None
    number_of_steps: Optional[int] = None
    
    pending_record: Optional[Dict[str, Any]] = None

class ConversationRequest(BaseModel):
    user_input: str
    state: UnifiedConversationState

EXPERT_NAMES = ["Dr. Anjali Singh", "Dr. Amit Patel", "Dr. Maya Sharma"]
SESSION_TYPES = ["Yoga", "Stress Relief", "Nutritional Advice", "Fitness Planning"]
APPOINTMENT_TYPES = ["Video Call", "Audio Call"]

class SessionBookingTool(BaseTool):

    def __init__(self, llm_service: LLMService):
        super().__init__(
            ToolType.SESSION_BOOKING.value,
            "Manages the conversational flow for booking expert sessions (audio/text)."
        )
        self.llm_service = llm_service
        self.booking_required_fields = ["user_name", "expert_name", "session_date", "time_slot", "session_type", "appointment_type"]

    def _get_field_descriptions(self) -> str:
        return f"""- user_name: Your full name
- expert_name: Choose from {', '.join(EXPERT_NAMES)}
- session_type: Choose from {', '.join(SESSION_TYPES)}
- session_date: Date for the session (MUST be today or future date)
- time_slot: Time for the session (Suggest 9 AM - 5 PM slots)
- appointment_type: Choose from {', '.join(APPOINTMENT_TYPES)}
- reminder: Your preferred reminder timing (e.g., "15 minutes before", "1 hour before")"""
    def _create_system_prompt(self, state: UnifiedConversationState, user_name: str) -> str:
        field_desc = self._get_field_descriptions()
        if not state.user_name and user_name and user_name != "there":
             state.user_name = user_name
        
        all_booking_keys = self.booking_required_fields + ["reminder"]
        current_data = {
            k: getattr(state, k) for k in all_booking_keys
            if getattr(state, k) is not None
        }
        
        missing_fields = [
            f for f in all_booking_keys if current_data.get(f) is None and f != "user_name"
        ]
        
        return f"""You are a friendly and precise AI assistant specialized in booking expert sessions. Today is {DateType.today()}.

**Known User Information:**
- User Name: {state.user_name}
- Current Session Data: {json.dumps(current_data, default=str)}
- Missing Fields: {', '.join(missing_fields) if missing_fields else 'None'}

**Available Booking Options:**
{field_desc}

**CRITICAL LLM RULES:**
1. **Extraction:** Extract any mention of name, expert, date, time, type, or appointment type from the user's input.
2. **Date/Time Validation:** If extracted date is in the past, or time is outside 9 AM - 5 PM, politely ask the user to choose a valid future date/time.
3. **Conversational Flow (Confirmation Loop):**
   - **If information is missing:** Ask ONLY for the next 1-2 missing fields politely.
   - **If ALL required fields (user_name, expert_name, session_date, time_slot, session_type, appointment_type, reminder) are present:** - Generate a CONCISE confirmation summary (e.g., "Confirm booking Dr. Singh for Nutritional Advice on [Date] at [Time] with a 15-minute reminder?").
     - **Crucially, the LLM must respond ONLY with the FINAL confirmation summary, NO JSON.**
4. **Final Confirmation (User's turn to confirm):** If the user confirms ('yes', 'sure', 'ok') the summary in the next turn, the Tool handles the JSON creation; the LLM should not attempt to create JSON here.
"""

    def _create_booking_record(self, state: UnifiedConversationState) -> SessionBookingRecord:
        """Generates a mock booking record for confirmation."""
        return SessionBookingRecord(
            user_id="user_booking_123",
            user_name=state.user_name or "Client",
            expert_name=state.expert_name,
            session_date=state.session_date,
            time_slot=state.time_slot,
            session_type=state.session_type,
            appointment_type=state.appointment_type,
            reminder=state.reminder or "15 minutes before" 
        )
    
    def _handle_confirmation(self, query: str, state: UnifiedConversationState) -> Optional[ToolResult]:
        
        query_lower = query.strip().lower()
        is_affirmative = query_lower in ["yes", "yep", "yeah", "ok", "okay", "sure", "confirm"]
        is_negative = query_lower in ["no", "nope", "nah", "cancel", "wrong"]
        
        if not state.confirmation_pending:
            return None 

        if is_affirmative:
            booking_data = self._create_booking_record(state).dict()
            session_id = str(uuid.uuid4())
            meet_id = f"{uuid.uuid4().hex[:3]}-{uuid.uuid4().hex[:4]}-{uuid.uuid4().hex[:3]}"
            google_meet_link = f"https://meet.google.com/{meet_id}"
            
            response_data = {
                "answer": f"Great! Your session is **CONFIRMED**.",
                "booking_data": {
                    "session_booking_id": session_id,
                    "status": "approved",
                    "data": booking_data,
                    "google_meet_link": google_meet_link
                }
            }
            state.confirmation_pending = False
            state.conversation_history = []
            return ToolResult(
                True, 
                data={"answer": response_data["answer"], "booking_data": response_data},
                metadata={
                    "booking_state": state.model_dump(mode="json") 
                }
            )
        
        elif is_negative:
            state.confirmation_pending = False
            state.session_date = None
            state.time_slot = None
            state.conversation_history = []
            return ToolResult(
                True, 
                data={"answer": "Got it — nothing has been booked."},
                metadata={
                    "booking_state": state.model_dump(mode="json")
                }
            ) 
            
        return None 


    async def _extract_and_validate_data(self, user_input: str, state: UnifiedConversationState) -> Dict:
        schema_properties = {
            "user_name": {"type": "string"},
            "expert_name": {"type": "string", "enum": EXPERT_NAMES},
            "session_date": {"type": "string", "description": "YYYY-MM-DD format only"},
            "time_slot": {"type": "string", "description": "HH:MM format only, 24-hour clock"},
            "session_type": {"type": "string", "enum": SESSION_TYPES},
            "appointment_type": {"type": "string", "enum": APPOINTMENT_TYPES},
            "reminder": {"type": "string", "description": "User's preferred reminder timing (e.g., '15 min prior', '1 hour before')."},
        }

        extraction_schema = {"type": "object", "properties": schema_properties}
        
        extraction_system_prompt = f"""You are a precise data extractor. Your task is to extract ALL available fields from the user's input.
        
        - If a field is present, format the date as YYYY-MM-DD and time as HH:MM.
        - **Today's Date is {DateType.today()}.** Use this for relative terms (today, tomorrow).
        - Use the following available options strictly: Expert: {EXPERT_NAMES}, Type: {SESSION_TYPES}, Appointment: {APPOINTMENT_TYPES}.
        
        Extract ONLY the JSON, containing only fields that could be inferred from the user's input."""
        
        extracted_data = await self.llm_service.query_json(
            prompt=user_input, 
            system_prompt=extraction_system_prompt, 
            json_schema=extraction_schema, 
            max_tokens=200
        )
        
        validated_changes = {}
        if extracted_data:
            for k_raw, v in extracted_data.items():
                if v is None: continue
                
                k = k_raw.strip().lower().replace('_', '').replace(' ', '')
                
                k_final = None
                if k in ["sessiontype", "type"]:
                    k_final = "session_type"
                elif k in ["appointmenttype"]:
                    k_final = "appointment_type"
                elif k in ["expertname"]:
                    k_final = "expert_name"
                elif k in ["sessiondate", "date"]:
                    k_final = "session_date"
                elif k in ["timeslot", "time"]:
                    k_final = "time_slot"
                elif k in ["username", "name"]:
                    k_final = "user_name"
                elif k in ["reminder", "remind"]: 
                    k_final = "reminder"
                else:
                    logger.warning(f"Unrecognized extracted key: {k_raw}. Skipping.")
                    continue

                try:
                    if k_final == "session_date":
                        d = parse_date(v).date()
                        if d < DateType.today():
                            raise ValueError("Date is in the past.")
                        validated_changes[k_final] = d
                        
                    elif k_final == "time_slot":
                        t = parse_date(v).time()
                        start_time = TimeType(9, 0)
                        end_time = TimeType(17, 0)
                        if not (start_time <= t <= end_time):
                            raise ValueError("Time slot is outside working hours.")
                        validated_changes[k_final] = t
                        
                    elif k_final == "user_name":
                        validated_changes[k_final] = str(v).strip().title()
                    else:
                        validated_changes[k_final] = v
                        
                except Exception as e:
                    logger.warning(f"Validation failed for {k_final}={v}: {e}")
                    pass
        return validated_changes

    async def execute(self, query: str, constraints: Dict, last_agent_context: Dict, **kwargs) -> ToolResult:
        
        user_name = constraints.get("name", "there")
        
        if 'booking_state' not in last_agent_context:
            state = UnifiedConversationState(
                intent="session_booking",
                user_name=user_name,
                conversation_history=[]
            )
        else:
            state = UnifiedConversationState(**last_agent_context['booking_state'])
            
        confirmation_result = self._handle_confirmation(query, state)
        if confirmation_result:
            return confirmation_result 
        
        extracted_changes = await self._extract_and_validate_data(query, state)
        
        for k, v in extracted_changes.items():
            setattr(state, k, v)
            
        initial_missing_fields_no_reminder = [
            f for f in self.booking_required_fields if getattr(state, f) is None
        ]
        
        
        if not initial_missing_fields_no_reminder and state.reminder is None and not state.reminder_pending:
            state.reminder_pending = True
            
            final_answer = "All set with the main details! Now, the last question: **What is your preferred reminder timing (e.g., '15 minutes before', '1 hour before')?**"
            state.conversation_history.append({"role": "assistant", "content": final_answer})
            
        elif state.reminder_pending and state.reminder is None:
            if query and not any(kw in query.lower() for kw in ["book", "cancel", "no", "yes", "expert"]):
                state.reminder = query.strip()
                state.reminder_pending = False
            else:
                 final_answer = "I still need your preferred reminder timing. Can you tell me, for example, '15 minutes before'?"
                 state.conversation_history.append({"role": "assistant", "content": final_answer})
                 
        if state.reminder is not None and not initial_missing_fields_no_reminder and not state.confirmation_pending:
            system_prompt = self._create_system_prompt(state, user_name)
            state.conversation_history = [{"role": "system", "content": system_prompt}]
            llm_confirmation_response = await self.llm_service.query(
                prompt=f"Generate the final confirmation summary using the complete data. User last said: {query}",
                system_prompt=system_prompt,
                chat_history=state.conversation_history,
                max_tokens=150,
                temperature=0.4
            )
            
            state.confirmation_pending = True
            final_answer = llm_confirmation_response.strip()
            
        elif initial_missing_fields_no_reminder:
            
            system_prompt = self._create_system_prompt(state, user_name)
            
            if not state.conversation_history or state.conversation_history[0].get('role') != 'system':
                state.conversation_history.insert(0, {"role": "system", "content": system_prompt})
            else:
                state.conversation_history[0]['content'] = system_prompt
            
            state.conversation_history.append({"role": "user", "content": query})
            
            llm_response = await self.llm_service.query(
                prompt=f"The user provided data. Now, ask the next required question to fill these missing fields: {', '.join(initial_missing_fields_no_reminder)}.",
                system_prompt=system_prompt,
                chat_history=state.conversation_history,
                max_tokens=150,
                temperature=0.6
            )
            final_answer = llm_response.strip()
            

        last_agent_context['booking_state'] = state.model_dump(mode="json")
        
        return ToolResult(
            success=True,
            data={"answer": final_answer},
            metadata={
                "booking_state": state.model_dump(mode="json")
            } 
        )