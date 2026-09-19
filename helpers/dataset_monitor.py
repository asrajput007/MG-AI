"""
Dataset Change Detection for Semantic Router

Monitors query_classification.csv for changes and triggers model rebuild when needed.
Used during FastAPI startup to ensure the FAISS index is always in sync with the dataset.

HOW IT WORKS:
=============

1. During FastAPI startup (lifespan event), `ensure_model_sync()` is called
2. It calculates MD5 hash of datasets/query_classification.csv
3. Compares with stored hash in datasets/current_model_hash.txt
4. If hashes differ (or no hash file exists):
   - Logs that dataset has changed
   - FastSemanticRouter will automatically rebuild FAISS index when instantiated
   - Saves new hash for next startup
5. If hashes match:
   - No rebuild needed
   - Continues with existing cached model

AZURE DEPLOYMENT:
=================

When you deploy to Azure Web Service:
- Your updated query_classification.csv is deployed with the new code
- On first container startup, ensure_model_sync() detects the change
- FastSemanticRouter rebuilds its index from the new CSV
- Hash is saved, preventing unnecessary rebuilds on container restarts

MANUAL REBUILD:
===============

To force a rebuild without changing the dataset:
```python
from helpers.dataset_monitor import force_rebuild_marker
force_rebuild_marker()  # Deletes hash file
# Restart app - model will rebuild
```

Or simply delete: datasets/current_model_hash.txt
"""

import hashlib
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Paths
DATASET_PATH = "datasets/query_classification.csv"
HASH_FILE_PATH = "datasets/current_model_hash.txt"


def calculate_md5_hash(file_path: str) -> str:
    """
    Calculate MD5 hash of a file.
    
    Args:
        file_path: Path to the file
        
    Returns:
        MD5 hash as hex string
    """
    md5_hash = hashlib.md5()
    
    with open(file_path, 'rb') as f:
        # Read in chunks to handle large files efficiently
        for chunk in iter(lambda: f.read(4096), b""):
            md5_hash.update(chunk)
    
    return md5_hash.hexdigest()


def get_stored_hash() -> str | None:
    """
    Get the previously stored MD5 hash from file.
    
    Returns:
        Stored hash string or None if file doesn't exist
    """
    if not os.path.exists(HASH_FILE_PATH):
        return None
    
    try:
        with open(HASH_FILE_PATH, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except Exception as e:
        logger.warning(f"[DatasetMonitor] Failed to read hash file: {e}")
        return None


def save_hash(hash_value: str) -> bool:
    """
    Save MD5 hash to file.
    
    Args:
        hash_value: MD5 hash to save
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Ensure directory exists
        os.makedirs(os.path.dirname(HASH_FILE_PATH), exist_ok=True)
        
        with open(HASH_FILE_PATH, 'w', encoding='utf-8') as f:
            f.write(hash_value)
        
        logger.info(f"[DatasetMonitor] Saved new hash: {hash_value}")
        return True
        
    except Exception as e:
        logger.error(f"[DatasetMonitor] Failed to save hash: {e}")
        return False


def check_dataset_changed() -> tuple[bool, str]:
    """
    Check if the dataset has changed since last startup.
    
    Returns:
        Tuple of (has_changed: bool, current_hash: str)
    """
    if not os.path.exists(DATASET_PATH):
        logger.error(f"[DatasetMonitor] Dataset not found at {DATASET_PATH}")
        raise FileNotFoundError(f"Dataset not found: {DATASET_PATH}")
    
    # Calculate current hash
    current_hash = calculate_md5_hash(DATASET_PATH)
    logger.info(f"[DatasetMonitor] Current dataset hash: {current_hash}")
    
    # Get stored hash
    stored_hash = get_stored_hash()
    
    if stored_hash is None:
        logger.info("[DatasetMonitor] No previous hash found. This appears to be first startup or hash file was deleted.")
        return True, current_hash
    
    logger.info(f"[DatasetMonitor] Stored dataset hash: {stored_hash}")
    
    # Compare hashes
    has_changed = current_hash != stored_hash
    
    if has_changed:
        logger.warning(f"[DatasetMonitor] Dataset has CHANGED! Old: {stored_hash}, New: {current_hash}")
    else:
        logger.info("[DatasetMonitor] Dataset unchanged. Using existing model.")
    
    return has_changed, current_hash


def ensure_model_sync() -> None:
    """
    Main function to ensure semantic router model is in sync with dataset.
    
    This function:
    1. Checks if dataset has changed
    2. Logs appropriate messages
    3. Updates the hash file
    
    Note: The FastSemanticRouter automatically rebuilds its index when instantiated,
    so we don't need to manually trigger a rebuild. We just need to track the hash.
    
    Should be called during FastAPI startup (lifespan event).
    """
    logger.info("[DatasetMonitor] Starting dataset change detection...")
    
    try:
        has_changed, current_hash = check_dataset_changed()
        
        if has_changed:
            logger.info("[DatasetMonitor] Dataset changed detected. Semantic router will rebuild index on next instantiation.")
            logger.info("[DatasetMonitor] Note: FastSemanticRouter automatically rebuilds FAISS index from CSV during __init__")
        else:
            logger.info("[DatasetMonitor] 📦 Dataset unchanged. Using cached model state.")
        
        # Save current hash for next startup
        save_hash(current_hash)
        logger.info("[DatasetMonitor] Dataset monitoring complete.")
        
    except Exception as e:
        logger.error(f"[DatasetMonitor] Error during dataset monitoring: {e}")
        logger.warning("[DatasetMonitor] Continuing startup despite monitoring error...")
        # Don't raise - allow app to start even if monitoring fails


def force_rebuild_marker() -> None:
    """
    Force a rebuild on next startup by deleting the hash file.
    
    Useful for manual cache invalidation or debugging.
    """
    try:
        if os.path.exists(HASH_FILE_PATH):
            os.remove(HASH_FILE_PATH)
            logger.info("[DatasetMonitor] Forced rebuild marker set. Model will rebuild on next startup.")
        else:
            logger.info("[DatasetMonitor] Hash file already missing. Rebuild will occur naturally.")
    except Exception as e:
        logger.error(f"[DatasetMonitor] Failed to delete hash file: {e}")
