#!/bin/sh
set -e

# Start the app (gunicorn) in background so we can warm up endpoints
gunicorn -k uvicorn.workers.UvicornWorker app:app \
  --bind 0.0.0.0:8000 \
  --workers 2 \
  --timeout 120 \
  --max-requests 1000 \
  --max-requests-jitter 100 &
GUN_PID=$!

echo "Started gunicorn (pid=$GUN_PID). Waiting for /health..."
until curl -sf -o /dev/null http://localhost:8000/health; do sleep 1; done

echo 'Server ready. Attempting Duckling warm-up...'

attempt=0
max=20
while [ $attempt -lt $max ]; do
  status=$(curl -s -w '%{http_code}' -o /tmp/duckling_resp -X POST http://localhost:8000/duckling -H 'Content-Type: application/json' -d '{"text":"tomorrow","locale":"en_US"}' --max-time 8 || echo 000)
  echo "Duckling warm-up HTTP status: $status"
  if [ "$status" = "200" ]; then echo 'Duckling warm-up succeeded'; break; fi
  if [ "$status" = "405" ]; then
    echo 'Received 405, trying GET fallback'
    status2=$(curl -s -w '%{http_code}' -o /tmp/duckling_resp_get http://localhost:8000/duckling --max-time 8 || echo 000)
    echo "GET status: $status2"
    if [ "$status2" = "200" ]; then echo 'Duckling warm-up via GET succeeded'; break; fi
  fi
  attempt=$((attempt+1))
  echo "Retry $attempt/$max in 2s"
  sleep 2
done

if [ $attempt -ge $max ]; then echo 'WARNING: Duckling warm-up failed after retries.'; fi

# Wait for the gunicorn process (keeps container running)
wait $GUN_PID
