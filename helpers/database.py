import pyodbc
import os
import asyncio
import logging
from queue import Queue, Empty
from threading import Lock
from contextlib import contextmanager
from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)

SERVER = os.getenv("SERVER")
DYNAMIC_USERNAME = os.getenv("DYNAMIC_USERNAME")
DYNAMIC_PASSWORD = os.getenv("DYNAMIC_PASSWORD")

NOURIQAI_SERVER = os.getenv("NOURIQAI_SERVER")
NOURIQAI_DB_USERNAME = os.getenv("NOURIQAI_DB_USERNAME")
NOURIQAI_DB_PASSWORD = os.getenv("NOURIQAI_DB_PASSWORD")
NOURIQAI_DB_NAME = os.getenv("NOURIQAI_DB_NAME")

FITNESS_DB_SERVER = os.getenv("FITNESS_DB_SERVER")
FITNESS_DB_USERNAME = os.getenv("FITNESS_DB_USERNAME")
FITNESS_DB_PASSWORD = os.getenv("FITNESS_DB_PASSWORD")

def get_db_connection_dynamic(database_name: str):
    conn_str = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={SERVER};"
        f"DATABASE={database_name};"
        f"UID={DYNAMIC_USERNAME};"
        f"PWD={DYNAMIC_PASSWORD};"
        "Encrypt=yes;"
        "TrustServerCertificate=Yes;"
    )
    return pyodbc.connect(conn_str)


def workout_db_connection():
    """Get a workout database connection from the pool"""
    conn_str = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={NOURIQAI_SERVER};"
        f"DATABASE={NOURIQAI_DB_NAME};"
        f"UID={NOURIQAI_DB_USERNAME};"
        f"PWD={NOURIQAI_DB_PASSWORD};"
        "Encrypt=yes;"
        "TrustServerCertificate=Yes;"
    )
    return pyodbc.connect(conn_str)

def fitness_ai_db_connection(database_name: str):
    """Get a fitness AI database connection from the pool"""
    conn_str = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={FITNESS_DB_SERVER};"
        f"DATABASE={database_name};"
        f"UID={FITNESS_DB_USERNAME};"
        f"PWD={FITNESS_DB_PASSWORD};"
        "Encrypt=yes;"
        "TrustServerCertificate=Yes;"
    )
    return pyodbc.connect(conn_str)