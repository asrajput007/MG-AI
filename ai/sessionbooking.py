import re
import os
import uuid
import json
import logging
from typing import List, Optional, Dict, Any, Tuple
from pydantic import BaseModel, Field
from datetime import date as DateType, datetime, timedelta
import asyncio
from .tool_core import BaseTool, ToolType, ToolResult, LLMService
import traceback

logger = logging.getLogger(__name__)


class UnifiedConversationState(BaseModel):
    intent: Optional[str] = None
    
    role_name: Optional[str] = None
    expert_id: Optional[str] = None
    expert_name: Optional[str] = None
    expert_role_category: Optional[str] = None 
    
    selected_date: Optional[str] = None 
    selected_slot_start: Optional[str] = None 
    selected_slot_end: Optional[str] = None  
    duration_minutes: int = 30
    
    appointment_type: str = "Video" 
    chief_complaint: Optional[str] = None
    
    confirmation_pending: bool = False
    
    available_experts_list: List[Dict] = []
    available_slots_list: List[Dict] = []
    conversation_history: List[dict] = []

class SessionBookingTool(BaseTool):

    def __init__(self, llm_service: LLMService, db_tool):
        super().__init__(
            ToolType.SESSION_BOOKING.value,
            "Book, view, or cancel sessions with real experts (Nutritionists, Physicians, etc)."
        )
        self.llm_service = llm_service
        self.db_tool = db_tool
        
        self.KNOWN_ROLES = [
            "Nutritionist", "Yoga Instructor", "Wellness Coordinator", 
            "Registered Nurse Consultant", "Fitness Consultant", "Physician"
        ]


    def _print_debug(self, title, data):
        print(f"\n--- {title} ---")
        print(json.dumps(data, indent=2, default=str))
        print("---------------------\n")

    def _normalize_role(self, user_input: str) -> Optional[str]:
        """Maps user input (e.g., 'dietician') to API roles (e.g., 'Nutritionist')."""
        if not user_input: return None
        u = user_input.lower()
        if "yoga" in u: return "Yoga Instructor"
        if "nutrition" in u or "diet" in u or "food" in u: return "Nutritionist"
        if "doctor" in u or "physician" in u: return "Physician"
        if "nurse" in u: return "Registered Nurse Consultant"
        if "fitness" in u or "gym" in u or "trainer" in u: return "Fitness Consultant"
        if "wellness" in u: return "Wellness Coordinator"
        return None

    def _get_program_id_for_role(self, role_name: str) -> str:
        if not role_name: return "MNT" 
        r = role_name.lower()
        
        if "nutrition" in r: return "NUT"
        if "yoga" in r or "fitness" in r: return "FIT"
        if "physician" in r or "nurse" in r or "wellness" in r or "mental" in r: return "MNT"
        
        return "MNT" 

    def _get_appointment_type_code(self, user_type: str) -> str:
        if not user_type: return "VRTL"
        u = user_type.lower()
        if "phone" in u or "audio" in u or "call" in u: return "PHN"
        return "VRTL"


    async def _fetch_experts(self, role_name: str, token: str) -> List[Dict]:
        result = await self.db_tool.execute(
            operation="get_experts", 
            role_name=role_name, 
            token=token
        )
        if result.success and result.data:
            data = result.data.get("data", result.data) 
            if isinstance(data, list):
                return data
        logger.error(f"Failed to fetch experts: {result.error}")
        return []

    async def _fetch_slots(self, expert_id: str, date_str: str, token: str) -> List[Dict]:
        result = await self.db_tool.execute(
            operation="get_expert_time_slots", 
            expert_id=expert_id, 
            filter_type="date", 
            duration=30,
            token=token
        )
        if result.success and result.data:
            slots = result.data.get("data", [])
            valid_slots = []
            for slot in slots:
                if slot.get("Status") == "Available":
                    start = slot.get("StartDateTime")
                    if start and date_str in start:
                        valid_slots.append(slot)
            return valid_slots
        return []

    async def _book_session_api(self, state: UnifiedConversationState, token: str) -> Tuple[bool, str]:
        """Calls /book-session with corrected payload"""
        
        program_id = self._get_program_id_for_role(state.role_name)
        appt_type_code = self._get_appointment_type_code(state.appointment_type)
        
        payload = {
            "ExpertId": state.expert_id,
            "StartDateTimeUTC": state.selected_slot_start, 
            "EndDateTimeUTC": state.selected_slot_end,    
            "DurationMinutes": state.duration_minutes,
            "ProgramId": program_id, 
            "Status": "CON",
            "RepeatEnabled": 0, 
            "ChiefComplaint": state.chief_complaint or "General Consultation",
            "AppointmentType": appt_type_code,
            "SessionType": "1T01"
        }
        
        self._print_debug("Booking Payload", payload)
        
        result = await self.db_tool.execute(
            operation="book_session",
            booking_payload=payload,
            token=token
        )
        
        if result.success:
            return True, "Booking successful! Your session has been confirmed."
        else:
            return False, f"Booking failed: {result.error}"


    async def _extract_intent_and_entities(self, query: str, state: UnifiedConversationState) -> Dict:
   
        system_prompt = f"""You are a booking assistant.
        Current State:
        - Role: {state.role_name}
        - Expert: {state.expert_name}
        - Date: {state.selected_date}
        - Slot: {state.selected_slot_start}
        
        Available Experts (if any fetched): {[e.get('FirstName') for e in state.available_experts_list]}
        Available Slots (if any fetched): {[s.get('StartDateTime') for s in state.available_slots_list]}

        Task: Extract JSON.
        1. 'intent': "search_experts", "select_expert", "select_slot", "confirm_booking", "cancel_flow", "view_sessions", "provide_info".
        2. 'role': If user mentions a role (nutritionist, doctor).
        3. 'expert_name': If user selects a person.
        4. 'date': If user mentions a date (convert to YYYY-MM-DD, today is {DateType.today()}).
        5. 'time': If user mentions a time.
        6. 'complaint': Reason for visit.
        7. 'appt_type': If user specifies 'video' or 'audio'/'phone'.
        """
        
        schema = {
            "type": "object",
            "properties": {
                "intent": {"type": "string"},
                "role": {"type": "string"},
                "expert_name": {"type": "string"},
                "date": {"type": "string"},
                "time": {"type": "string"},
                "complaint": {"type": "string"},
                "appt_type": {"type": "string"}
            }
        }
        
        return await self.llm_service.query_json(query, system_prompt, schema)


    async def execute(self, query: str, constraints: Dict, last_agent_context: Dict, token: str, **kwargs) -> ToolResult:
        
        if 'booking_state' in last_agent_context:
            state = UnifiedConversationState(**last_agent_context['booking_state'])
        else:
            state = UnifiedConversationState()

        if "my session" in query.lower() or "show bookings" in query.lower():
            res = await self.db_tool.execute("get_my_sessions", token=token)
            if res.success and res.data:
                sessions = res.data.get("data", [])
                if not sessions:
                    return ToolResult(True, {"answer": "You don't have any upcoming sessions."})
                
                msg = "Here are your upcoming sessions:\n"
                for s in sessions:
                    expert = s.get('ExpertName', 'Expert')
                    start = s.get('StartDateTimeUTC', 'TBD')
                    status = s.get('Status', 'Unknown')
                    msg += f"- **{expert}** on {start} ({status})\n"
                return ToolResult(True, {"answer": msg})
            else:
                return ToolResult(True, {"answer": "Failed to retrieve sessions."})

        extraction = await self._extract_intent_and_entities(query, state)
        self._print_debug("LLM Extraction", extraction)
        
        if extraction.get("role"):
            mapped_role = self._normalize_role(extraction["role"])
            if mapped_role: state.role_name = mapped_role
            
        if extraction.get("date"):
            state.selected_date = extraction["date"]
            
        if extraction.get("complaint"):
            state.chief_complaint = extraction["complaint"]
            
        if extraction.get("appt_type"):
            state.appointment_type = extraction["appt_type"]


        if not state.role_name:
            return ToolResult(
                True, 
                {"answer": f"I can help you book a session. What type of expert do you need? (e.g., {', '.join(self.KNOWN_ROLES)})"},
                metadata={"booking_state": state.model_dump(mode="json")}
            )

        if state.role_name and not state.expert_id:
            
            if extraction.get("expert_name"):
                target = extraction["expert_name"].lower()
                found = None
                for e in state.available_experts_list:
                    name = f"{e.get('FirstName', '')} {e.get('LastName', '')}".strip().lower()
                    if target in name:
                        found = e
                        break
                
                if found:
                    state.expert_id = found.get("Id") or found.get("UserId")
                    state.expert_name = f"{found.get('FirstName')} {found.get('LastName')}"
                else:
                    return ToolResult(True, {"answer": f"I couldn't find an expert named '{extraction['expert_name']}' in the list. Please choose from the available experts."})
            
            if not state.expert_id:
                experts = await self._fetch_experts(state.role_name, token)
                state.available_experts_list = experts 
                
                if not experts:
                    return ToolResult(True, {"answer": f"Sorry, I couldn't find any {state.role_name}s available right now."})
                
                names = [f"{e.get('FirstName')} {e.get('LastName')}" for e in experts]
                msg = f"Here are the available {state.role_name}s:\n" + "\n".join([f"- {n}" for n in names])
                msg += "\n\nWho would you like to book with?"
                
                return ToolResult(True, {"answer": msg}, metadata={"booking_state": state.model_dump(mode="json")})

        if state.expert_id and not state.selected_date:
            return ToolResult(
                True, 
                {"answer": f"Great, looking for a slot with {state.expert_name}. What date would you like to book? (e.g., 'tomorrow', 'Dec 12th')"},
                metadata={"booking_state": state.model_dump(mode="json")}
            )

        if state.expert_id and state.selected_date and not state.selected_slot_start:
            
            user_time = extraction.get("time")
            slots = await self._fetch_slots(state.expert_id, state.selected_date, token)
            state.available_slots_list = slots
            self._print_debug("Available Slots", slots)

            if not slots:
                return ToolResult(
                    True, 
                    {"answer": f"I checked {state.expert_name}'s schedule for {state.selected_date}, but there are no available slots. Would you like to try a different date?"},
                    metadata={"booking_state": state.model_dump(mode="json")}
                )

            if user_time:
                matched_slot = None
                for slot in slots:
                    api_dt = slot.get("StartDateTime", "")
                    if user_time in api_dt: 
                        matched_slot = slot
                        break
                
                if matched_slot:
                    state.selected_slot_start = matched_slot.get("StartDateTime")
                    state.selected_slot_end = matched_slot.get("EndDateTime")
                    state.confirmation_pending = True
                else:
                    times = [s.get("StartDateTime").split("T")[1][:5] for s in slots]
                    return ToolResult(
                        True, 
                        {"answer": f"I couldn't find a slot at {user_time}. Available times are: {', '.join(times)}."},
                        metadata={"booking_state": state.model_dump(mode="json")}
                    )
            
            if not state.selected_slot_start:
                times = []
                for s in slots:
                    dt = s.get("StartDateTime", "")
                    if "T" in dt:
                        times.append(dt.split("T")[1][:5]) 
                
                msg = f"Available slots for {state.expert_name} on {state.selected_date}:\n" + ", ".join(times)
                msg += "\n\nWhich time works for you?"
                return ToolResult(True, {"answer": msg}, metadata={"booking_state": state.model_dump(mode="json")})

        if state.selected_slot_start and state.confirmation_pending:
            if "yes" in query.lower() or "confirm" in query.lower() or "book" in query.lower():
                success, msg = await self._book_session_api(state, token)
                return ToolResult(True, {"answer": msg}, metadata={"booking_state": {}}) 
            elif "no" in query.lower() or "cancel" in query.lower():
                state.confirmation_pending = False
                state.selected_slot_start = None 
                return ToolResult(True, {"answer": "Okay, let's pick a different time. What time would you prefer?"}, metadata={"booking_state": state.model_dump(mode="json")})
            else:
                readable_time = state.selected_slot_start.replace("T", " ")
                msg = f"**Please Confirm:**\n- Expert: {state.expert_name}\n- Date/Time: {readable_time}\n- Type: {state.appointment_type}\n\nShall I book this?"
                return ToolResult(True, {"answer": msg}, metadata={"booking_state": state.model_dump(mode="json")})

        return ToolResult(True, {"answer": "I'm lost in the process. Let's start over. What expert do you need?"}, metadata={"booking_state": {}})