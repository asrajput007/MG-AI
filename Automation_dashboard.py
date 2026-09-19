import streamlit as st
import requests
import pandas as pd
from dotenv import load_dotenv
import os

load_dotenv()

API_BASE = os.getenv("MEAL_DASHBOARD_URL","https://friskaaiccm-api-uat.nouriq.ai")

st.set_page_config(layout="wide")
st.title("🚀 Meal Plan Automation Dashboard")

# ---------------------------------------------------
# 🔹 SAFE API WRAPPER
# ---------------------------------------------------

def safe_request(method, url, **kwargs):
    try:
        response = requests.request(method, url, timeout=5, **kwargs)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.ConnectionError:
        st.error("⚠ API Server is not running or unreachable.")
        return None
    except requests.exceptions.Timeout:
        st.error("⚠ API request timed out.")
        return None
    except requests.exceptions.HTTPError as e:
        st.error(f"⚠ API Error: {e}")
        return None
    except Exception:
        st.error("⚠ Unexpected error while calling API.")
        return None


# ---------------------------------------------------
# 🔹 SECTION 1 — Manual Trigger
# ---------------------------------------------------

st.subheader("Manual Run")

col1, col2 = st.columns([1, 2])

with col1:
    batch_size = st.slider("Batch Size", 5, 50, 25)

with col2:
    if st.button("▶ Start Automation Run"):
        response = safe_request(
            "POST",
            f"{API_BASE}/automation/run-now",
            params={"batch_size": batch_size}
        )
        if response:
            st.success(response)

st.divider()

# ---------------------------------------------------
# 🔹 SECTION 2 — Latest Run Summary
# ---------------------------------------------------

st.subheader("Latest Run Summary")

summary = safe_request(
    "GET",
    f"{API_BASE}/automation/latest-run"
)

selected_run_id = None

if summary:

    col1, col2, col3, col4 = st.columns(4)

    col1.metric("Run ID", summary.get("RunId", "N/A"))
    col2.metric("Total Users", summary.get("TotalUsers", 0))
    col3.metric("Failed", summary.get("FailedCount", 0))
    col4.metric("Status", summary.get("Status", ""))

    col5, col6, col7, col8 = st.columns(4)

    col5.metric("Generated Today", summary.get("GeneratedTodayCount", 0))
    col6.metric("Generated Tomorrow", summary.get("GeneratedTomorrowCount", 0))
    col7.metric("Skipped", summary.get("SkippedCount", 0))
    col8.metric("Total Time (min)", summary.get("TotalTimeMin", 0))

    selected_run_id = summary.get("RunId")

else:
    st.stop()  # Stop dashboard if API is down or no summary

st.divider()

# ---------------------------------------------------
# 🔹 SECTION 3 — User Logs
# ---------------------------------------------------

if selected_run_id:

    st.subheader(f"User Logs for Run {selected_run_id}")

    # 🔄 Refresh Button
    if st.button("🔄 Refresh Logs"):
        st.rerun()

    logs = safe_request(
        "GET",
        f"{API_BASE}/automation/run-users/{selected_run_id}"
    )

    if logs:

        df = pd.DataFrame(logs)

        # 🔹 Compute result status
        df["Result"] = df.apply(
            lambda row: "FAILED"
            if row["TodayStatus"] == "FAILED"
            or row["TomorrowStatus"] == "FAILED"
            else "SUCCESS",
            axis=1
        )

        # 🔹 Filter
        filter_option = st.selectbox(
            "Filter Users",
            ["ALL", "SUCCESS", "FAILED"]
        )

        if filter_option != "ALL":
            df = df[df["Result"] == filter_option]

        # 🔹 Row Highlighting
        def highlight_status(row):
            if row["Result"] == "FAILED":
                return ["background-color: #ffcccc"] * len(row)
            if row["TodayStatus"] == "SKIPPED" and row["TomorrowStatus"] == "SKIPPED":
                return ["background-color: #fff3cd"] * len(row)
            return [""] * len(row)

        st.dataframe(
            df.style.apply(highlight_status, axis=1),
            width="stretch"
        )

        st.divider()

        # ---------------------------------------------------
        # 🔹 Retry Single User
        # ---------------------------------------------------

        failed_users = df[df["Result"] == "FAILED"]["UserId"].tolist()

        if failed_users:

            st.subheader("Retry Single Failed User")

            selected_user = st.selectbox(
                "Select Failed User",
                failed_users
            )

            if st.button("🔁 Retry Selected User"):
                retry_response = safe_request(
                    "POST",
                    f"{API_BASE}/automation/retry-user/{selected_run_id}/{selected_user}"
                )
                if retry_response:
                    st.success(retry_response)
                    st.rerun()

            st.divider()

            # ---------------------------------------------------
            # 🔹 Retry All Failed Users
            # ---------------------------------------------------

            if st.button("🔁 Retry All Failed Users"):
                retry_response = safe_request(
                    "POST",
                    f"{API_BASE}/automation/retry/{selected_run_id}"
                )
                if retry_response:
                    st.success(retry_response)
                    st.rerun()

        else:
            st.info("No failed users available for retry.")

    else:
        st.info("No user logs found for this run.")