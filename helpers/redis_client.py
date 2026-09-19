import json
import logging
import redis
from typing import Optional, Dict, List
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Redis Configuration
REDIS_URL = os.getenv("REDIS_URL")
REDIS_CA_CERT_PATH = os.getenv("REDIS_CA_CERT_PATH")
CACHE_VERSION = os.getenv("CACHE_VERSION", "v1")  # Increment to invalidate all caches after schema changes
PROFILE_CACHE_TTL = int(os.getenv("PROFILE_CACHE_TTL", 3600))
CONVERSATION_CACHE_TTL = int(os.getenv("CONVERSATION_CACHE_TTL", 7200))  
MAX_CONVERSATION_HISTORY = 3  # Store last 3 interactions (6 messages total)  


class RedisProfileCache:
    """
    High-performance Redis cache for user profiles.
    
    Features:
    - Sub-millisecond read latency
    - Optimistic updates
    - Graceful degradation to SQL on failure
    """
    
    def __init__(self):
        """Initialize Redis client with connection pooling."""
        self.redis_client = None
        self.enabled = False
        
        try:
            if not REDIS_URL:
                logger.warning("[RedisCache] REDIS_URL not configured. Cache disabled.")
                return
            
            # Parse Redis URL
            redis_config = {
                "decode_responses": True,
                "socket_connect_timeout": 5,
                "socket_timeout": 5,
                "retry_on_timeout": True,
                "health_check_interval": 30
            }
            
            # Add SSL/TLS certificate if provided
            if REDIS_CA_CERT_PATH and os.path.exists(REDIS_CA_CERT_PATH):
                redis_config["ssl_ca_certs"] = REDIS_CA_CERT_PATH
                redis_config["ssl_cert_reqs"] = "required"
            
            # IMPORTANT: REDIS_URL must start with 'rediss://' (with two s's) for SSL to work
            # The 'rediss://' scheme automatically enables SSL; do not pass ssl=True in config
            self.redis_client = redis.from_url(REDIS_URL, **redis_config)
            
            # Test connection
            self.redis_client.ping()
            self.enabled = True
            logger.info("[RedisCache] Connected successfully. Cache enabled.")
            
        except Exception as e:
            logger.warning(f"[RedisCache] Failed to connect: {e}. Operating without cache.")
            self.enabled = False
    
    def _get_cache_key(self, user_id: str, database_name: str) -> str:
        """Generate Redis key for user profile."""
        return f"profile:{CACHE_VERSION}:{database_name}:{user_id}"
    
    def _get_conversation_key(self, user_id: str, database_name: str) -> str:
        """Generate Redis key for conversation history."""
        return f"conversation:{CACHE_VERSION}:{database_name}:{user_id}"
    
    async def get_profile(self, user_id: str, database_name: str) -> Optional[Dict]:
        """
        Retrieve profile from Redis cache.
        
        Args:
            user_id: User identifier
            database_name: Database name for multi-tenancy
            
        Returns:
            Profile dict if found, None otherwise
        """
        if not self.enabled:
            return None
        
        try:
            cache_key = self._get_cache_key(user_id, database_name)
            cached_data = self.redis_client.get(cache_key)
            
            if cached_data:
                profile = json.loads(cached_data)
                logger.info(f"[RedisCache] Cache HIT for user {user_id} (~0ms)")
                return profile
            else:
                logger.info(f"[RedisCache] Cache MISS for user {user_id}")
                return None
                
        except Exception as e:
            logger.warning(f"[RedisCache] Get failed: {e}. Falling back to SQL.")
            return None
    
    async def set_profile(self, user_id: str, database_name: str, profile: Dict) -> bool:
        """
        Store profile in Redis cache with TTL.
        
        Args:
            user_id: User identifier
            database_name: Database name
            profile: Profile dictionary to cache
            
        Returns:
            True if successful, False otherwise
        """
        if not self.enabled:
            return False
        
        try:
            cache_key = self._get_cache_key(user_id, database_name)
            profile_json = json.dumps(profile)
            
            # Set with TTL (expires after PROFILE_CACHE_TTL seconds)
            self.redis_client.setex(
                name=cache_key,
                time=PROFILE_CACHE_TTL,
                value=profile_json
            )
            
            logger.info(f"[RedisCache] Cached profile for user {user_id} (TTL: {PROFILE_CACHE_TTL}s)")
            return True
            
        except Exception as e:
            logger.warning(f"[RedisCache] Set failed: {e}. Continuing without cache.")
            return False
    
    async def invalidate_profile(self, user_id: str, database_name: str) -> bool:
        """
        Invalidate (delete) profile from cache.
        
        Args:
            user_id: User identifier
            database_name: Database name
            
        Returns:
            True if successful, False otherwise
        """
        if not self.enabled:
            return False
        
        try:
            cache_key = self._get_cache_key(user_id, database_name)
            deleted = self.redis_client.delete(cache_key)
            
            if deleted:
                logger.info(f"[RedisCache] Invalidated cache for user {user_id}")
            return bool(deleted)
            
        except Exception as e:
            logger.warning(f"[RedisCache] Invalidation failed: {e}")
            return False
    
    async def get_conversation_history(self, user_id: str, database_name: str) -> List[Dict]:
        """
        Retrieve the last 3 conversation interactions from Redis.
        
        Args:
            user_id: User identifier
            database_name: Database name
            
        Returns:
            List of conversation messages in chronological order.
            Each message: {"role": "user"|"assistant", "message": str, "timestamp": str}
            Returns empty list if no history or on failure.
        """
        if not self.enabled:
            return []
        
        try:
            conv_key = self._get_conversation_key(user_id, database_name)
            # Get all messages (up to MAX_CONVERSATION_HISTORY * 2)
            messages = self.redis_client.lrange(conv_key, 0, -1)
            
            if not messages:
                logger.info(f"[RedisConversation] No history for user {user_id}")
                return []
            
            # Parse JSON messages and reverse to chronological order (oldest first)
            history = [json.loads(msg) for msg in messages]
            history.reverse()  # Redis LPUSH stores newest first, we want oldest first
            
            logger.info(f"[RedisConversation] Retrieved {len(history)} messages for user {user_id}")
            return history
            
        except Exception as e:
            logger.warning(f"[RedisConversation] Get history failed: {e}. Returning empty history.")
            return []
    
    async def add_conversation_turn(self, user_id: str, database_name: str, 
                                    user_message: str, assistant_response: str) -> bool:
        """
        Add a new conversation turn (user message + assistant response) to Redis.
        Automatically maintains a rolling window of the last 3 interactions (6 messages).
        
        Args:
            user_id: User identifier
            database_name: Database name
            user_message: The user's message
            assistant_response: The assistant's response
            
        Returns:
            True if successful, False otherwise
        """
        if not self.enabled:
            return False
        
        try:
            conv_key = self._get_conversation_key(user_id, database_name)
            timestamp = datetime.utcnow().isoformat()
            
            # Create message objects
            assistant_msg = {
                "role": "assistant",
                "message": assistant_response,
                "timestamp": timestamp
            }
            user_msg = {
                "role": "user",
                "message": user_message,
                "timestamp": timestamp
            }
            
            # Push messages to the front of the list (newest first in Redis)
            # Push assistant response first, then user message (so chronologically they're correct)
            self.redis_client.lpush(conv_key, json.dumps(assistant_msg))
            self.redis_client.lpush(conv_key, json.dumps(user_msg))
            
            # Trim to keep only last N interactions (N * 2 messages)
            max_messages = MAX_CONVERSATION_HISTORY * 2
            self.redis_client.ltrim(conv_key, 0, max_messages - 1)
            
            # Set TTL on the conversation history
            self.redis_client.expire(conv_key, CONVERSATION_CACHE_TTL)
            
            logger.info(f"[RedisConversation] Added conversation turn for user {user_id}")
            return True
            
        except Exception as e:
            logger.warning(f"[RedisConversation] Failed to add conversation turn: {e}")
            return False
    
    def clear_user_meal_cache(self, user_id: str) -> Dict[str, any]:
        """
        Clear all meal diversity cache for a specific user across all dates.
        Called when user profile changes (allergies, restrictions, medical conditions, etc.)
        to ensure fresh meal planning with new constraints.
        
        Args:
            user_id: User identifier
            
        Returns:
            Dict with status: {'success': bool, 'keys_deleted': int, 'message': str}
        """
        if not self.enabled:
            return {'success': False, 'keys_deleted': 0, 'message': 'Redis not enabled'}
        
        try:
            # Pattern to match all meal diversity keys for this user
            # Format: meal_diversity:{user_id}:*
            pattern = f"meal_diversity:{user_id}:*"
            
            # Use SCAN to find all matching keys (more efficient than KEYS for production)
            cursor = 0
            keys_to_delete = []
            
            while True:
                cursor, keys = self.redis_client.scan(cursor=cursor, match=pattern, count=100)
                keys_to_delete.extend(keys)
                if cursor == 0:
                    break
            
            # Delete all found keys
            if keys_to_delete:
                deleted_count = self.redis_client.delete(*keys_to_delete)
                logger.info(f"🔥 [REDIS SESSION] Cleared {deleted_count} meal diversity cache keys for user {user_id} (profile changed)")
                return {
                    'success': True,
                    'keys_deleted': deleted_count,
                    'message': f'Cleared {deleted_count} cached meal plans'
                }
            else:
                logger.info(f"🔥 [REDIS SESSION] No meal diversity cache found for user {user_id}")
                return {
                    'success': True,
                    'keys_deleted': 0,
                    'message': 'No cached meal plans to clear'
                }
                
        except Exception as e:
            logger.error(f"❌ [REDIS SESSION] Failed to clear meal cache for user {user_id}: {e}")
            return {
                'success': False,
                'keys_deleted': 0,
                'message': f'Cache clear failed: {str(e)}'
            }
            
        except Exception as e:
            logger.warning(f"[RedisConversation] Add turn failed: {e}. Continuing without cache.")
            return False
    
    async def clear_conversation_history(self, user_id: str, database_name: str) -> bool:
        """
        Clear conversation history for a user.
        
        Args:
            user_id: User identifier
            database_name: Database name
            
        Returns:
            True if successful, False otherwise
        """
        if not self.enabled:
            return False
        
        try:
            conv_key = self._get_conversation_key(user_id, database_name)
            deleted = self.redis_client.delete(conv_key)
            
            if deleted:
                logger.info(f"[RedisConversation] Cleared history for user {user_id}")
            return bool(deleted)
            
        except Exception as e:
            logger.warning(f"[RedisConversation] Clear history failed: {e}")
            return False
    
    def health_check(self) -> bool:
        """Check if Redis is healthy."""
        if not self.enabled:
            return False
        
        try:
            self.redis_client.ping()
            return True
        except Exception:
            return False


# Global singleton instance
_redis_cache = None

def get_redis_cache() -> RedisProfileCache:
    """Get or create the global Redis cache instance."""
    global _redis_cache
    if _redis_cache is None:
        _redis_cache = RedisProfileCache()
    return _redis_cache
