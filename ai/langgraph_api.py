from fastapi import APIRouter, HTTPException, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import json
import logging
import uuid
from datetime import datetime

from ai.langgraph_agent import NutritionAgent, AgentState
from ai.nutrition_validator import NutritionValidator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2", tags=["LangGraph Chat"])

_global_agent: Optional[NutritionAgent] = None


def get_agent() -> NutritionAgent:
    global _global_agent
    if _global_agent is None:
        _global_agent = NutritionAgent()
        logger.info("Initialized LangGraph agent")
    return _global_agent



class ChatStreamRequest(BaseModel):
    message: str = Field(..., description="User message", min_length=1)
    thread_id: Optional[str] = Field(None, description="Thread ID for conversation continuity")
    user_id: str = Field(..., description="User ID")
    profile_data: Optional[Dict[str, Any]] = Field(None, description="User profile data")
    constraints: Optional[Dict[str, Any]] = Field(None, description="Dietary constraints")
    calorie_target: Optional[int] = Field(None, description="Target daily calories", gt=0)


class ThreadListResponse(BaseModel):
    threads: List[Dict[str, Any]]


class ThreadDetailResponse(BaseModel):
    thread_id: str
    user_id: str
    messages: List[Dict[str, Any]]
    metadata: Optional[Dict[str, Any]]



@router.post("/chat/stream")
async def chat_stream(
    request: ChatStreamRequest,
    authorization: Optional[str] = Header(None)
) -> StreamingResponse:
    
    try:
        agent = get_agent()
        
        thread_id = request.thread_id or str(uuid.uuid4())
        
        async def generate_stream():
            try:
                async for chunk in agent.stream_response(
                    user_message=request.message,
                    thread_id=thread_id,
                    user_id=request.user_id,
                    profile_data=request.profile_data,
                    constraints=request.constraints,
                    calorie_target=request.calorie_target
                ):
                    message_chunk, metadata = chunk
                    
                    event_data = {
                        'type': 'token',
                        'content': message_chunk.content if hasattr(message_chunk, 'content') else str(message_chunk),
                        'metadata': metadata,
                        'thread_id': thread_id
                    }
                    
                    yield f"data: {json.dumps(event_data)}\n\n"
                
                yield f"data: {json.dumps({'type': 'done', 'thread_id': thread_id})}\n\n"
                
            except Exception as e:
                logger.error(f"Error in streaming generation: {e}", exc_info=True)
                error_event = {
                    'type': 'error',
                    'error': str(e),
                    'thread_id': thread_id
                }
                yield f"data: {json.dumps(error_event)}\n\n"
        
        return StreamingResponse(
            generate_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Thread-ID": thread_id
            }
        )
    
    except Exception as e:
        logger.error(f"Error in chat_stream endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat/invoke")
async def chat_invoke(
    request: ChatStreamRequest,
    authorization: Optional[str] = Header(None)
) -> Dict[str, Any]:

    try:
        agent = get_agent()
        
        thread_id = request.thread_id or str(uuid.uuid4())
        
        result = await agent.invoke_sync(
            user_message=request.message,
            thread_id=thread_id,
            user_id=request.user_id,
            profile_data=request.profile_data,
            constraints=request.constraints,
            calorie_target=request.calorie_target
        )
        
        messages = result.get('messages', [])
        final_message = messages[-1] if messages else None
        
        response = {
            'thread_id': thread_id,
            'user_id': request.user_id,
            'response': final_message.content if hasattr(final_message, 'content') else str(final_message),
            'metadata': {
                'message_count': len(messages),
                'validation_errors': result.get('validation_errors', []),
                'retry_count': result.get('retry_count', 0)
            }
        }
        
        return response
    
    except Exception as e:
        logger.error(f"Error in chat_invoke endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/threads")
async def list_threads(
    user_id: str,
    authorization: Optional[str] = Header(None)
) -> ThreadListResponse:

    try:
        agent = get_agent()
        
        threads = []
        
        return ThreadListResponse(threads=threads)
    
    except Exception as e:
        logger.error(f"Error listing threads: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/threads/{thread_id}")
async def get_thread(
    thread_id: str,
    user_id: str,
    authorization: Optional[str] = Header(None)
) -> ThreadDetailResponse:

    try:
        agent = get_agent()
        
        state = agent.get_thread_state(thread_id, user_id)
        
        if not state:
            raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")
        
        messages = []
        for msg in state.get('messages', []):
            messages.append({
                'type': msg.__class__.__name__,
                'content': msg.content if hasattr(msg, 'content') else str(msg),
                'timestamp': getattr(msg, 'timestamp', None)
            })
        
        return ThreadDetailResponse(
            thread_id=thread_id,
            user_id=user_id,
            messages=messages,
            metadata={
                'calorie_target': state.get('calorie_target'),
                'validation_errors': state.get('validation_errors', []),
                'retry_count': state.get('retry_count', 0)
            }
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting thread: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/validate-meal-plan")
async def validate_meal_plan(
    meal_plan: Dict[str, Any],
    constraints: Optional[Dict[str, Any]] = None,
    target_calories: Optional[int] = None,
    authorization: Optional[str] = Header(None)
) -> Dict[str, Any]:

    try:
        validator = NutritionValidator()
        
        is_valid, results = validator.validate_meal_plan(
            meal_plan=meal_plan,
            constraints=constraints,
            target_calories=target_calories
        )
        
        report = validator.format_validation_report(results)
        
        return {
            'is_valid': is_valid,
            'validation_results': [r.to_dict() for r in results],
            'report': report,
            'summary': {
                'total_checks': len(results),
                'passed': sum(1 for r in results if r.passed),
                'failed': sum(1 for r in results if not r.passed),
                'critical': sum(1 for r in results if not r.passed and r.severity.value == 'critical'),
                'errors': sum(1 for r in results if not r.passed and r.severity.value == 'error'),
                'warnings': sum(1 for r in results if not r.passed and r.severity.value == 'warning')
            }
        }
    
    except Exception as e:
        logger.error(f"Error validating meal plan: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/threads/{thread_id}")
async def delete_thread(
    thread_id: str,
    user_id: str,
    authorization: Optional[str] = Header(None)
) -> Dict[str, str]:

    try:
        logger.info(f"Deleting thread {thread_id} for user {user_id}")
        
        return {
            'status': 'success',
            'message': f'Thread {thread_id} deleted'
        }
    
    except Exception as e:
        logger.error(f"Error deleting thread: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))



@router.get("/health")
async def health_check() -> Dict[str, Any]:
    try:
        agent = get_agent()
        
        return {
            'status': 'healthy',
            'agent_initialized': agent is not None,
            'timestamp': datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}", exc_info=True)
        return {
            'status': 'unhealthy',
            'error': str(e),
            'timestamp': datetime.utcnow().isoformat()
        }



__all__ = ['router', 'get_agent']
