#!/bin/bash

# Start Duckling service in the background on port 8001
# Note: Ensure 'duckling-example-exe' is in your PATH or provide the full path.
# We use port 8001 to avoid conflict with the main app on 8000.
# You may need to update DUCKLING_URL in your env to http://localhost:8001
duckling-example-exe -p 8001 &

# Start the FastAPI application using Gunicorn/Uvicorn on port 8000
# Using exec ensures it receives signals correctly
exec gunicorn -k uvicorn.workers.UvicornWorker app:app --bind 0.0.0.0:8000 --workers=4 --timeout=120