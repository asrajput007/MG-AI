"""
Global Food Database Cache
===========================
Loads foods database ONCE at server startup and stores in memory.
Eliminates repeated disk I/O that was causing 2-3 second latency per request.

Performance improvement:
- Before: Load 21,148 foods from disk every request (~2-3s)
- After: Load once at startup, fetch from memory (<1ms)
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, Optional
from config import BASE_DIR

logger = logging.getLogger(__name__)


class FoodDatabaseCache:
    """
    Singleton cache for food database.
    
    Loads foods 1.json once at server startup and reuses for all requests.
    Thread-safe for read operations (database is immutable after load).
    """
    
    _instance = None
    _foods_data: Optional[Dict] = None
    _loaded = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        """Initialize cache (only loads database once)."""
        if not self._loaded:
            self._load_database()
    
    def _load_database(self):
        """Load foods database from disk (called only once at startup)."""
        try:
            foods_json_path = Path(BASE_DIR) / "datasets" / "foods 1.json"
            
            logger.info("=" * 80)
            logger.info("🚀 INITIALIZING GLOBAL FOOD DATABASE CACHE")
            logger.info("=" * 80)
            logger.info(f"   Loading from: {foods_json_path}")
            
            if not foods_json_path.exists():
                logger.error(f"❌ Food database not found: {foods_json_path}")
                raise FileNotFoundError(f"Food database not found: {foods_json_path}")
            
            with open(foods_json_path, 'r', encoding='utf-8') as f:
                self._foods_data = json.load(f)
            
            # Count foods for logging
            food_count = self._count_foods(self._foods_data)
            
            logger.info(f"✅ Food database loaded successfully")
            logger.info(f"   Total foods: {food_count:,}")
            logger.info(f"   Memory footprint: ~{self._estimate_size_mb():.1f} MB")
            logger.info(f"   Status: CACHED IN MEMORY")
            logger.info("   All subsequent requests will use this cached copy")
            logger.info("=" * 80)
            
            self._loaded = True
            
        except Exception as e:
            logger.error(f"❌ Failed to load food database: {e}")
            logger.error("   Server will not be able to generate meal plans!")
            raise
    
    def _count_foods(self, data: Dict) -> int:
        """Count total foods in database."""
        if not data:
            return 0
        
        # Handle different schema formats
        if 'foods' in data:
            foods_data = data['foods']
            if isinstance(foods_data, list):
                return len(foods_data)
            elif isinstance(foods_data, dict):
                return len(foods_data.get('main_dishes', [])) + len(foods_data.get('side_dishes', []))
        
        if 'main_dishes' in data and 'side_dishes' in data:
            return len(data.get('main_dishes', [])) + len(data.get('side_dishes', []))
        
        if isinstance(data, list):
            return len(data)
        
        return 0
    
    def _estimate_size_mb(self) -> float:
        """Estimate memory footprint in MB."""
        try:
            import sys
            return sys.getsizeof(json.dumps(self._foods_data)) / (1024 * 1024)
        except:
            return 0.0
    
    def get_foods_data(self) -> Dict:
        """
        Get cached foods database.
        
        Returns:
            Dict: Full foods database (read-only, immutable)
        
        Performance:
            - Before: 2-3 seconds (disk I/O)
            - After: <1ms (memory access)
        """
        if not self._loaded or self._foods_data is None:
            logger.warning("⚠️ Food database not loaded! Attempting to load now...")
            self._load_database()
        
        return self._foods_data
    
    def is_loaded(self) -> bool:
        """Check if database is loaded in cache."""
        return self._loaded and self._foods_data is not None
    
    def reload(self):
        """
        Reload database from disk (use only for hot-reload scenarios).
        
        Warning: This will briefly block all meal plan requests.
        Only use when database file has been updated.
        """
        logger.warning("🔄 Manually reloading food database...")
        self._loaded = False
        self._foods_data = None
        self._load_database()
        logger.info("✅ Database reload complete")


# Global singleton instance (lazy initialization)
_food_cache = None


def get_food_database_cache() -> FoodDatabaseCache:
    """
    Get global food database cache instance.
    
    Returns:
        FoodDatabaseCache: Singleton cache instance
    
    Example:
        >>> cache = get_food_database_cache()
        >>> foods = cache.get_foods_data()  # <1ms memory access
    """
    global _food_cache
    if _food_cache is None:
        _food_cache = FoodDatabaseCache()
    return _food_cache


def preload_food_database():
    """
    Preload food database at server startup.
    
    Call this in FastAPI lifespan to load database before accepting requests.
    
    Example:
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            preload_food_database()  # Load once at startup
            yield
    """
    cache = get_food_database_cache()
    if not cache.is_loaded():
        logger.info("🔥 Preloading food database for first time...")
        cache.get_foods_data()  # Force load
    else:
        logger.info("✅ Food database already loaded in cache")
