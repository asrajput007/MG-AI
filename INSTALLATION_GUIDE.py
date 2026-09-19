"""
Installation and Deployment Guide for LangGraph System
"""

# STEP 1: Install Dependencies
# ============================

"""
Run this command to install all required packages:

pip install -r requirements.txt

New dependencies added:
- langgraph: Core graph-based agent framework
- langgraph-checkpoint: Checkpointing system for conversation memory
- langgraph-checkpoint-sqlite: SQLite backend for checkpoints
"""

# STEP 2: Environment Configuration
# ==================================

"""
Add these to your .env file:

# Required
MISTRAL_API_KEY=your_mistral_api_key_here

# Optional (with defaults)
USE_LANGGRAPH_AGENT=true
LANGGRAPH_DB_PATH=./langgraph_checkpoints.db
ENABLE_VALIDATION=true
CALORIE_TOLERANCE=15.0
MAX_RETRY_ATTEMPTS=3
"""

# STEP 3: Test Installation
# ==========================

"""
Run the test suite to verify everything is working:

python test_langgraph_integration.py

Expected output:
- ✅ PASS: validator
- ✅ PASS: agent
- ✅ PASS: checkpointing
- ✅ PASS: fastapi
- ✅ PASS: meal_plan

If all tests pass, you're ready to go!
"""

# STEP 4: Start the Server
# =========================

"""
Start the FastAPI server:

# Development
uvicorn app:app --reload --host 0.0.0.0 --port 8000

# Production
gunicorn app:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
"""

# STEP 5: Verify Endpoints
# =========================

"""
Test the new endpoints:

1. Health Check:
   curl http://localhost:8000/api/v2/health

2. API Documentation:
   Open http://localhost:8000/docs in your browser

3. Test Streaming Chat:
   curl -X POST http://localhost:8000/api/v2/chat/stream \
     -H "Content-Type: application/json" \
     -d '{
       "message": "Create a 2000 calorie meal plan",
       "user_id": "test-user",
       "calorie_target": 2000
     }'

4. Test Validation:
   curl -X POST http://localhost:8000/api/v2/validate-meal-plan \
     -H "Content-Type: application/json" \
     -d '{
       "meal_plan": {...},
       "target_calories": 2000
     }'
"""

# STEP 6: Integration Options
# ============================

"""
Choose your integration approach:

OPTION A: Parallel Systems (Safest)
- Keep existing endpoints running
- Use new endpoints for testing
- Gradually migrate users
- Easy rollback if needed

OPTION B: Add Validation Only (Quick Win)
- Wrap existing meal plan generator
- Get immediate bug fixes
- Minimal code changes
- See ai/validated_meal_generator.py

OPTION C: Full Migration (Best Long-term)
- Replace agent with LangGraph agent
- Full error handling and retry
- Conversation memory
- Best reliability
"""

# STEP 7: Monitoring
# ==================

"""
Monitor these metrics:

1. Validation Failure Rate
   - Track how often validation catches errors
   - Alert if >20% failure rate (indicates LLM issues)

2. Retry Count
   - Average retries per meal plan
   - Should be <1.5 on average

3. Calorie Accuracy
   - % of plans within ±15% of target
   - Should be >95%

4. Critical Errors
   - Allergen violations caught
   - Should be 0 reaching users

5. Response Time
   - p50, p95, p99 latency
   - Should be <6s for p95
"""

# STEP 8: Production Checklist
# =============================

"""
Before deploying to production:

Infrastructure:
[ ] Use PostgreSQL instead of SQLite for multi-instance deployment
[ ] Set up Redis for caching
[ ] Configure load balancer
[ ] Set up database backups

Security:
[ ] Enable API authentication
[ ] Set up rate limiting
[ ] Validate all user inputs
[ ] Use HTTPS only
[ ] Rotate API keys regularly

Monitoring:
[ ] Set up application monitoring (New Relic, DataDog, etc.)
[ ] Configure log aggregation (ELK, CloudWatch, etc.)
[ ] Set up alerting for errors
[ ] Create dashboards for key metrics

Testing:
[ ] Run load tests
[ ] Test failover scenarios
[ ] Verify backup/restore procedures
[ ] Test rate limiting
[ ] Security audit
"""

# STEP 9: Troubleshooting
# ========================

"""
Common issues and solutions:

1. Import Errors
   Problem: ModuleNotFoundError: No module named 'langgraph'
   Solution: pip install -r requirements.txt

2. Database Locked
   Problem: SQLite database is locked
   Solution: Switch to PostgreSQL for production:
     from langgraph.checkpoint.postgres import PostgresSaver
     checkpointer = PostgresSaver(connection_string)

3. Slow Response
   Problem: Streaming is slow
   Solution: 
     - Enable caching for nutrition data
     - Reduce message history size
     - Use async tools

4. High Validation Failure
   Problem: >30% validation failures
   Solution:
     - Review LLM prompts
     - Adjust tolerance thresholds
     - Check if database has nutrition data

5. Memory Issues
   Problem: Server runs out of memory
   Solution:
     - Limit thread history retention
     - Clean old checkpoints
     - Increase server memory
"""

# STEP 10: Getting Help
# ======================

"""
Documentation:
- LANGGRAPH_IMPLEMENTATION_SUMMARY.md - Overview and quick start
- LANGGRAPH_INTEGRATION_GUIDE.md - Detailed technical guide
- Code documentation - Inline docstrings

Testing:
- test_langgraph_integration.py - Full test suite
- /docs endpoint - Interactive API testing

Support:
- Check GitHub issues
- Review LangGraph documentation
- Check logs for detailed errors
"""

# Example: Quick Integration
# ===========================

if __name__ == "__main__":
    print("""
    LangGraph + FastAPI System - Quick Start
    ========================================
    
    1. Install: pip install -r requirements.txt
    2. Configure .env with MISTRAL_API_KEY
    3. Test: python test_langgraph_integration.py
    4. Start: uvicorn app:app --reload
    5. Check: http://localhost:8000/api/v2/health
    6. Docs: http://localhost:8000/docs
    
    For detailed instructions, see LANGGRAPH_IMPLEMENTATION_SUMMARY.md
    """)
