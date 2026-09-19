"""
FastAPI CCM Care Plan Email Service
====================================
Fetches patients from PatientConsentInfo (where IsEmailSent = 0),
downloads their consent PDF from FilePath URL,
attaches the static care_plan.html as a PDF + consent PDF,
sends the styled HTML email, then marks IsEmailSent = 1.

Requirements:
    pip install fastapi uvicorn pyodbc sqlalchemy aiohttp aiofiles jinja2 \
                python-dotenv httpx

Environment variables (put in .env):
    DB_CONN_STR=DRIVER={ODBC Driver 17 for SQL Server};SERVER=...;DATABASE=...;UID=...;PWD=...
    SMTP_SERVER=smtp.example.com
    EMAIL_PORT=587
    EMAIL_USERNAME=your@email.com
    EMAIL_PASSWORD=yourpassword
    EMAIL_FROM=noreply@friska.ai
    LOGO_PATH=./assets/logo.png          # local logo file
    CARE_PLAN_PATH=./care_plan_static.pdf  # static PDF attachment
"""

import asyncio
import html
import logging
import os
import smtplib
import tempfile
import urllib.parse
from email import encoders
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

import httpx
import pyodbc
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from jinja2 import Environment, FileSystemLoader
from azure.storage.blob import generate_blob_sas, BlobSasPermissions
from urllib.parse import urlparse
from datetime import datetime, timedelta
from helpers.database import get_db_connection_dynamic
from helpers.utils import DYNAMIC_DB_NAME, EMAIL_FROM, EMAIL_PASSWORD, EMAIL_PORT, EMAIL_USERNAME, SMTP_SERVER

load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)



AZURE_STORAGE_ACCOUNT_NAME="friskaaiblobstoragenew"
AZURE_STORAGE_ACCOUNT_KEY="vLpjB5Krg8287SBHVuyoIQJnle5wIwIHSL8xXIYdHcIfqjqaJD2n1H4jeN/5+vh5wDT5iNBRarzt+AStQELG1w=="

# Directory of this file (for templates)
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

# helpers/care_plan.py location
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent

TEMPLATE_DIR = PROJECT_ROOT / "templates"
STATIC_DIR = PROJECT_ROOT / "static"

jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR))
)

LOGO_PATH = STATIC_DIR / "friskalogo.png"
CARE_PLAN_PATH = STATIC_DIR / "Care_Plan_Overview.pdf"

def generate_sas_url(blob_url: str, expiry_minutes: int = 15) -> str:
    """
    Generate a temporary SAS URL for a private blob.
    """

    parsed = urlparse(blob_url)

    # Example URL:
    # https://account.blob.core.windows.net/container/folder/file.pdf
    path_parts = parsed.path.lstrip("/").split("/", 1)

    container_name = path_parts[0]
    blob_name = path_parts[1]

    sas_token = generate_blob_sas(
        account_name=AZURE_STORAGE_ACCOUNT_NAME,
        container_name=container_name,
        blob_name=blob_name,
        account_key=AZURE_STORAGE_ACCOUNT_KEY,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.utcnow() + timedelta(minutes=expiry_minutes),
    )

    return f"{blob_url}?{sas_token}"

def fetch_pending_patients():
    """
    Returns list of dicts for patients whose consent email hasn't been sent.
    Joins PatientConsentInfo with Patient on PatientId.
    """
    conn = get_db_connection_dynamic(DYNAMIC_DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            pci.Id               AS ConsentId,
            pci.PatientId,
            pci.FilePath         AS ConsentFilePath,
            pci.ConsentSignDate,
            p.Email,
            p.PatientFirstName,
            p.EnrollmentStatus,
            p.IsActive
        FROM [dbo].[PatientConsentInfo] pci
        INNER JOIN [dbo].[Patient] p ON pci.PatientId = p.PatientUserId
        WHERE pci.IsEmailSent = 0
          AND p.IsActive = 1
          AND p.EnrollmentStatus = 'ENROLLED'
    """)
    columns = [col[0] for col in cursor.description]
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    cursor.close()
    conn.close()
    return rows


def mark_email_sent(consent_id: int):
    """Set IsEmailSent = 1 for the given PatientConsentInfo row."""
    conn = get_db_connection_dynamic(DYNAMIC_DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE [dbo].[PatientConsentInfo] SET IsEmailSent = 1 WHERE Id = ?",
        consent_id,
    )
    conn.commit()
    cursor.close()
    conn.close()


# ===========================================================================
# LINK TRACKING HELPER
# ===========================================================================

from urllib.parse import urlparse, urlencode, parse_qsl, urlunparse

def create_tracked_link(original_url: str, recipient_email: str, link_type: str):

    encoded_email = recipient_email

    parsed = urlparse(original_url)

    # Get existing query params
    query_params = dict(parse_qsl(parsed.query))

    # Add tracking params
    query_params.update({
        "utm_source": "email",
        "utm_medium": "ccm",
        "utm_campaign": link_type,
        "email": encoded_email
    })

    # Build new query string
    new_query = urlencode(query_params)

    # Reconstruct full URL
    new_url = urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        new_query,
        parsed.fragment
    ))

    return new_url, link_type

# def create_tracked_link(original_url: str, recipient_email: str, link_type: str):
#     """
#     Simple pass-through tracker.  Replace this body with your real tracking
#     implementation (e.g. write to DB and return a redirect URL).
#     Returns (tracked_url, tracking_id).
#     """
#     encoded_email = urllib.parse.quote(recipient_email)
#     tracked = f"{original_url}?utm_source=email&utm_medium=ccm&utm_campaign={link_type}&email={encoded_email}"
#     return tracked, link_type


# ===========================================================================
# HTML EMAIL BUILDER
# ===========================================================================

def build_care_plan_email_html(recipient_name: str, recipient_email: str) -> str:
    """
    Renders care_plan.html (Jinja2) with dynamic values.
    """
    safe_name = html.escape(recipient_name or "there")

    app_store_link = (
        "https://apps.apple.com/app/apple-store/id6752922040"
        "?pt=126576299&ct=feb18_email_launch&mt=8"
    )
    play_store_link = (
        "https://play.google.com/store/apps/details?id=com.friskaaiccm.app"
    )

    tracked_ios, _ = create_tracked_link(app_store_link, recipient_email, "download_ios")
    tracked_android, _ = create_tracked_link(play_store_link, recipient_email, "download_android")

    tracked_ios = html.escape(tracked_ios, quote=True)
    tracked_android = html.escape(tracked_android, quote=True)

    template = jinja_env.get_template("care_plan.html")
    return template.render(
        recipient_name=safe_name,
        tracked_ios_link=tracked_ios,
        tracked_android_link=tracked_android,
    )


# ===========================================================================
# ATTACHMENT DOWNLOAD
# ===========================================================================

async def download_file_bytes(url: str) -> Optional[bytes]:
    """Download a remote file and return its bytes, or None on failure."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url, follow_redirects=True)
            response.raise_for_status()
            return response.content
    except Exception as exc:
        logger.error("Failed to download %s: %s", url, exc)
        return None


from azure.storage.blob import BlobClient

def download_blob_bytes(blob_url: str):
    parsed = urlparse(blob_url)
    path_parts = parsed.path.lstrip("/").split("/", 1)

    container = path_parts[0]
    blob_name = path_parts[1]

    blob_client = BlobClient(
        account_url=f"https://{AZURE_STORAGE_ACCOUNT_NAME}.blob.core.windows.net",
        container_name=container,
        blob_name=blob_name,
        credential=AZURE_STORAGE_ACCOUNT_KEY,
    )

    stream = blob_client.download_blob()
    return stream.readall()

# ===========================================================================
# EMAIL SENDER
# ===========================================================================

def send_care_plan_email(
    recipient_email: str,
    recipient_name: str,
    html_body: str,
    consent_pdf_bytes: Optional[bytes] = None,
    consent_filename: str = "Consent_Form.pdf",
):
    """
    Sends the HTML email with:
      - CID-embedded logo
      - Static care plan PDF attachment
      - Patient's consent PDF attachment (downloaded from URL)
    """
    msg = MIMEMultipart("related")
    msg["Subject"] = "The Support You Didn't Know You Needed – FriskaAi CCM"
    msg["From"] = EMAIL_FROM
    msg["To"] = recipient_email

    # ── HTML alternative ────────────────────────────────────────────────────
    alternative = MIMEMultipart("alternative")
    msg.attach(alternative)

    html_part = MIMEText(html_body, "html", "utf-8")
    alternative.attach(html_part)

    # ── CID logo ────────────────────────────────────────────────────────────
    logo_path = Path(LOGO_PATH)
    if logo_path.exists():
        with open(logo_path, "rb") as f:
            logo_data = f.read()
        logo_img = MIMEImage(logo_data)
        logo_img.add_header("Content-ID", "<logo_image>")
        logo_img.add_header("Content-Disposition", "inline", filename=logo_path.name)
        msg.attach(logo_img)
    else:
        logger.warning("Logo not found at %s – skipping CID embed.", LOGO_PATH)

    # App Store badge
    app_store_path = STATIC_DIR / "appstore.png"
    if app_store_path.exists():
        with open(app_store_path, "rb") as f:
            img = MIMEImage(f.read())
            img.add_header("Content-ID", "<app_store_badge>")
            img.add_header("Content-Disposition", "inline", filename="appstore.png")
            msg.attach(img)
    
    # Google Play badge
    google_play_path = STATIC_DIR / "googleplay.png"
    if google_play_path.exists():
        with open(google_play_path, "rb") as f:
            img = MIMEImage(f.read())
            img.add_header("Content-ID", "<google_play_badge>")
            img.add_header("Content-Disposition", "inline", filename="googleplay.png")
            msg.attach(img)
    
    # ── Static care plan PDF ─────────────────────────────────────────────────
    care_plan_path = Path(CARE_PLAN_PATH)
    if care_plan_path.exists():
        with open(care_plan_path, "rb") as f:
            cp_bytes = f.read()
        cp_part = MIMEBase("application", "pdf")
        cp_part.set_payload(cp_bytes)
        encoders.encode_base64(cp_part)
        cp_part.add_header(
            "Content-Disposition",
            "attachment",
            filename="FriskaAi_CCM_Care_Plan_Overview.pdf",
        )
        msg.attach(cp_part)
    else:
        logger.warning("Care plan PDF not found at %s – skipping.", CARE_PLAN_PATH)

    # ── Patient consent PDF ──────────────────────────────────────────────────
    if consent_pdf_bytes:
        consent_part = MIMEBase("application", "pdf")
        consent_part.set_payload(consent_pdf_bytes)
        encoders.encode_base64(consent_part)
        consent_part.add_header(
            "Content-Disposition",
            "attachment",
            filename=consent_filename,
        )
        msg.attach(consent_part)

    # ── SMTP send ────────────────────────────────────────────────────────────
    with smtplib.SMTP(SMTP_SERVER, EMAIL_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(EMAIL_USERNAME, EMAIL_PASSWORD)
        server.sendmail(EMAIL_FROM, recipient_email, msg.as_string())

    logger.info("Email sent to %s (%s)", recipient_email, recipient_name)


# ===========================================================================
# CORE PROCESSING LOGIC
# ===========================================================================
def insert_email_log(
    consent_id: int,
    patient_id: str,
    email: str,
    full_name: str,
    status: str,
    error_message: str = None,
):
    conn = get_db_connection_dynamic(DYNAMIC_DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO [dbo].[CarePlanEmailLogs]
        (ConsentId, PatientId, Email, FullName, Status, ErrorMessage, SentAt)
        VALUES (?, ?, ?, ?, ?, ?, 
            CASE WHEN ? = 'sent' THEN SYSDATETIME() ELSE NULL END
        )
    """, (
        consent_id,
        patient_id,
        email,
        full_name,
        status,
        error_message,
        status
    ))

    conn.commit()
    cursor.close()
    conn.close()

# async def process_single_patient(patient: dict) -> dict:
#     """Download consent, build email, send, mark sent. Returns result dict."""
#     consent_id = patient["ConsentId"]
#     email = patient["Email"]
#     first = patient.get("PatientFirstName") or ""
#     last = patient.get("PatientLastName") or ""
#     full_name = f"{first} {last}".strip() or "there"
#     consent_url = patient.get("ConsentFilePath", "")

#     result = {
#         "consent_id": consent_id,
#         "patient_id": patient["PatientId"],
#         "email": email,
#         "name": full_name,
#         "status": "pending",
#         "error": None,
#     }

#     try:
#         # 1. Download consent PDF
#         consent_bytes = None
#         consent_filename = "ConsentForm.pdf"
#         if consent_url:
#         #     sas_url = generate_sas_url(consent_url)
#         #     consent_bytes = await download_file_bytes(sas_url)

#             consent_bytes = await loop.run_in_executor(
#             None,
#             download_blob_bytes,
#             consent_url
#         )
        
#         # 2. Build HTML
#         html_body = build_care_plan_email_html(full_name, email)

#         # 3. Send email (run in executor since smtplib is blocking)
#         loop = asyncio.get_event_loop()
#         await loop.run_in_executor(
#             None,
#             send_care_plan_email,
#             email,
#             full_name,
#             html_body,
#             consent_bytes,
#             consent_filename,
#         )

#         # 4. Mark sent
#         await loop.run_in_executor(None, mark_email_sent, consent_id)

#         result["status"] = "sent"
#         logger.info("✅ Processed consent_id=%s → %s", consent_id, email)

#     except Exception as exc:
#         result["status"] = "failed"
#         result["error"] = str(exc)
#         logger.error("❌ Failed consent_id=%s: %s", consent_id, exc)

#     return result

#working without logs
# async def process_single_patient(patient: dict) -> dict:

    # loop = asyncio.get_running_loop()   # ✅ define FIRST

    # consent_id = patient["ConsentId"]
    # email = patient["Email"]
    # first = patient.get("PatientFirstName") or ""
    # consent_url = patient.get("ConsentFilePath", "")

    # full_name = first.strip() or "there"

    # result = {
    #     "consent_id": consent_id,
    #     "patient_id": patient["PatientId"],
    #     "email": email,
    #     "name": full_name,
    #     "status": "pending",
    #     "error": None,
    # }

    # try:
    #     # 1. Download consent PDF
    #     consent_bytes = None
    #     consent_filename = "ConsentForm.pdf"

    #     if consent_url:
    #         consent_bytes = await loop.run_in_executor(
    #             None,
    #             download_blob_bytes,
    #             consent_url
    #         )

    #     # 2. Build HTML
    #     html_body = build_care_plan_email_html(full_name, email)

    #     # 3. Send email
    #     await loop.run_in_executor(
    #         None,
    #         send_care_plan_email,
    #         email,
    #         full_name,
    #         html_body,
    #         consent_bytes,
    #         consent_filename,
    #     )

    #     # 4. Mark sent
    #     await loop.run_in_executor(None, mark_email_sent, consent_id)

    #     result["status"] = "sent"
    #     logger.info("✅ Processed consent_id=%s → %s", consent_id, email)

    # except Exception as exc:
    #     result["status"] = "failed"
    #     result["error"] = str(exc)
    #     logger.error("❌ Failed consent_id=%s: %s", consent_id, exc)

    # return result
#working with logs
# async def process_single_patient(patient: dict) -> dict:

    # loop = asyncio.get_running_loop()

    # consent_id = patient["ConsentId"]
    # patient_id = patient["PatientId"]
    # email = patient["Email"]
    # first = patient.get("PatientFirstName") or ""
    # last = patient.get("PatientLastName") or ""
    # full_name = f"{first} {last}".strip() or "there"
    # consent_url = patient.get("ConsentFilePath", "")

    # result = {
    #     "consent_id": consent_id,
    #     "patient_id": patient_id,
    #     "email": email,
    #     "name": full_name,
    #     "status": "pending",
    #     "error": None,
    # }

    # try:
    #     consent_bytes = None
    #     consent_filename = "ConsentForm.pdf"

    #     if consent_url:
    #         consent_bytes = await asyncio.to_thread(
    #             download_blob_bytes,
    #             consent_url
    #         )

    #     html_body = build_care_plan_email_html(full_name, email)

    #     await asyncio.to_thread(
    #         send_care_plan_email,
    #         email,
    #         full_name,
    #         html_body,
    #         consent_bytes,
    #         consent_filename,
    #     )

    #     await asyncio.to_thread(mark_email_sent, consent_id)

    #     # ✅ Insert success log
    #     await asyncio.to_thread(
    #         insert_email_log,
    #         consent_id,
    #         patient_id,
    #         email,
    #         full_name,
    #         "sent",
    #         None
    #     )

    #     result["status"] = "sent"

    # except Exception as exc:

    #     error_message = str(exc)

    #     # ✅ Insert failure log
    #     await asyncio.to_thread(
    #         insert_email_log,
    #         consent_id,
    #         patient_id,
    #         email,
    #         full_name,
    #         "failed",
    #         error_message
    #     )

    #     result["status"] = "failed"
    #     result["error"] = error_message

    # return result

async def process_single_patient(patient: dict) -> dict:

    consent_id = patient["ConsentId"]
    patient_id = patient["PatientId"]
    email = patient.get("Email")
    consent_url = patient.get("ConsentFilePath")

    first = patient.get("PatientFirstName") or ""
    full_name = first.strip() or "there"

    result = {
        "consent_id": consent_id,
        "patient_id": patient_id,
        "email": email,
        "name": full_name,
        "status": "pending",
        "error": None,
    }

    # -------------------------------
    # 🔎 MISSING DATA CHECK
    # -------------------------------

    if not email and not consent_url:
        status = "missing_email_and_consent"
        error_msg = "Email and Consent file both missing"

    elif not email:
        status = "missing_email"
        error_msg = "Email missing"

    elif not consent_url:
        status = "missing_consent"
        error_msg = "Consent file missing"

    else:
        status = None
        error_msg = None

    # If missing data → log and return
    if status:
        await asyncio.to_thread(
            insert_email_log,
            consent_id,
            patient_id,
            email or "",
            full_name,
            status,
            error_msg
        )

        result["status"] = status
        result["error"] = error_msg
        return result

    # -------------------------------
    # NORMAL EMAIL FLOW
    # -------------------------------

    try:
        consent_bytes = await asyncio.to_thread(
            download_blob_bytes,
            consent_url
        )

        html_body = build_care_plan_email_html(full_name, email)

        await asyncio.to_thread(
            send_care_plan_email,
            email,
            full_name,
            html_body,
            consent_bytes,
            "ConsentForm.pdf",
        )

        await asyncio.to_thread(mark_email_sent, consent_id)

        await asyncio.to_thread(
            insert_email_log,
            consent_id,
            patient_id,
            email,
            full_name,
            "sent",
            None
        )

        result["status"] = "sent"

    except Exception as exc:
        error_message = str(exc)

        await asyncio.to_thread(
            insert_email_log,
            consent_id,
            patient_id,
            email,
            full_name,
            "failed",
            error_message
        )

        result["status"] = "failed"
        result["error"] = error_message

    return result

def fetch_failed_patients():
    """
    Fetch patients whose latest email attempt failed.
    """

    conn = get_db_connection_dynamic(DYNAMIC_DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT 
            pci.Id AS ConsentId,
            pci.PatientId,
            pci.FilePath AS ConsentFilePath,
            p.Email,
            p.PatientFirstName
        FROM CarePlanEmailLogs log
        INNER JOIN (
            SELECT ConsentId, MAX(CreatedAt) AS LatestAttempt
            FROM CarePlanEmailLogs
            GROUP BY ConsentId
        ) latest ON log.ConsentId = latest.ConsentId 
                 AND log.CreatedAt = latest.LatestAttempt
        INNER JOIN PatientConsentInfo pci 
            ON pci.Id = log.ConsentId
        INNER JOIN Patient p 
            ON pci.PatientId = p.PatientUserId
        WHERE log.Status = 'failed'
    """)

    columns = [col[0] for col in cursor.description]
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

    cursor.close()
    conn.close()

    return rows