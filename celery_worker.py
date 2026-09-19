import sys
import os

from celery import Celery
sys.path.insert(0, os.getcwd()) # Ensure root is in path for imports like 'ai'

from celery_app import celery_app
from celery.schedules import crontab

# Import task functions to ensure they're registered with Celery
from tasks import dispatch_weekly_meal_plan_batches

celery_app.conf.beat_schedule = {
    "celery-task-test": {
        "task": "tasks.test_celery_setup",
        # "schedule": crontab(minute="*/60"),
        "schedule": crontab(minute="*"),# testing every minute
    },
    "weekly-meal-plan-generation": {
        "task": "tasks.dispatch_weekly_meal_plan_batches",
        # "schedule": crontab(minute=30, hour=5, day_of_week="wed"), 
        "schedule": crontab(minute="*/5"),  # testing
        #daily at 7 am utc
        # "schedule": crontab(minute=0, hour=7),
        "options": {
            "queue": "meal_plan"
        }
    },
}

