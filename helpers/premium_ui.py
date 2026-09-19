"""
MG AI premium presentation layer for the Personalised Meal Plan Generator.

This module is a pure rendering layer: it consumes the already-generated
meal-plan data (parsed via helpers.premium_meal_plan_parser) and the existing
profile/session-state dictionaries, and renders a luxury wellness-style UI.

It does NOT call any backend APIs, does NOT change session-state keys used by
the rest of the app, and does NOT invent nutrition data - every number shown
here comes from the already-generated plan or profile.
"""

import streamlit as st
from typing import Dict, List, Optional

from helpers.premium_meal_plan_parser import DayPlan, Meal, FoodItem

# ---------------------------------------------------------------------------
# Design tokens
# ---------------------------------------------------------------------------
COLOR_BG = "#F7F7F5"
COLOR_SURFACE = "#FFFFFF"
COLOR_DARK = "#151515"
COLOR_SAGE = "#6E8B74"
COLOR_GOLD = "#C9A86A"
COLOR_TEXT = "#151515"
COLOR_TEXT_SECONDARY = "#6B6B6B"
COLOR_TEXT_MUTED = "#999999"
COLOR_BORDER = "rgba(0,0,0,0.08)"

MEAL_ICONS = {
    "Breakfast": "🌅",
    "Morning Snack": "🍵",
    "Lunch": "🥗",
    "Evening Snack": "🍎",
    "Dinner": "🌙",
}


def inject_premium_css():
    """Injects the MG AI luxury design system. Safe to call multiple times per rerun."""
    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=Manrope:wght@400;500;600;700;800&display=swap');

        html, body, [class*="css"] {{
            font-family: 'Manrope', -apple-system, BlinkMacSystemFont, sans-serif;
        }}

        .stApp {{
            background-color: {COLOR_BG};
        }}

        footer {{ visibility: hidden; height: 0; }}
        /* Keep the native header (hamburger menu / Deploy button) usable, just blend it in visually. */
        header[data-testid="stHeader"] {{ background: transparent; }}
        .block-container {{ padding-top: 1.2rem; max-width: 1180px; }}

        .mg-serif {{ font-family: 'DM Serif Display', Georgia, serif; }}

        /* ---- Top nav ---- */
        .mg-navbar {{
            display: flex; align-items: center; justify-content: space-between;
            padding: 18px 4px 22px 4px; border-bottom: 1px solid {COLOR_BORDER};
            margin-bottom: 8px;
        }}
        .mg-logo {{ font-size: 1.5rem; font-weight: 800; letter-spacing: 0.5px; color: {COLOR_TEXT}; }}
        .mg-logo-sub {{ font-size: 0.68rem; letter-spacing: 2px; color: {COLOR_TEXT_MUTED}; font-weight: 600; text-transform: uppercase; margin-top: -2px; }}
        .mg-nav-links {{ display: flex; gap: 28px; font-size: 0.92rem; color: {COLOR_TEXT_SECONDARY}; font-weight: 600; }}
        .mg-avatar {{
            width: 38px; height: 38px; border-radius: 50%; background: {COLOR_SAGE};
            color: white; display: flex; align-items: center; justify-content: center;
            font-weight: 700; font-size: 0.95rem;
        }}

        /* ---- Hero ---- */
        .mg-hero-label {{ font-size: 0.72rem; letter-spacing: 3px; color: {COLOR_SAGE}; font-weight: 700; text-transform: uppercase; margin-bottom: 10px; }}
        .mg-hero-title {{ font-size: 2.6rem; line-height: 1.15; color: {COLOR_TEXT}; margin-bottom: 14px; }}
        .mg-hero-sub {{ font-size: 1.02rem; color: {COLOR_TEXT_SECONDARY}; max-width: 620px; line-height: 1.6; margin-bottom: 28px; }}

        .mg-stat-row {{ display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 8px; }}
        .mg-stat {{
            background: {COLOR_SURFACE}; border: 1px solid {COLOR_BORDER}; border-radius: 20px;
            padding: 18px 24px; min-width: 130px; flex: 1;
        }}
        .mg-stat-value {{ font-size: 1.5rem; font-weight: 800; color: {COLOR_TEXT}; }}
        .mg-stat-label {{ font-size: 0.78rem; color: {COLOR_TEXT_MUTED}; margin-top: 2px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }}

        /* ---- Chips ---- */
        .mg-chip {{
            display: inline-block; padding: 7px 16px; margin: 0 8px 8px 0;
            border-radius: 999px; background: {COLOR_SURFACE}; border: 1px solid {COLOR_BORDER};
            font-size: 0.85rem; color: {COLOR_TEXT}; font-weight: 600;
        }}
        .mg-chip-gold {{ border-color: rgba(201,168,106,0.5); background: rgba(201,168,106,0.08); color: #8a6d33; }}
        .mg-section-title {{ font-size: 1.5rem; color: {COLOR_TEXT}; margin: 34px 0 6px 0; }}
        .mg-section-sub {{ font-size: 0.92rem; color: {COLOR_TEXT_SECONDARY}; margin-bottom: 18px; }}

        /* ---- Day selector ---- */
        .mg-day-pill {{
            text-align: center; padding: 10px 4px; border-radius: 16px;
            border: 1px solid {COLOR_BORDER}; background: {COLOR_SURFACE};
            font-weight: 700; font-size: 0.82rem; color: {COLOR_TEXT_SECONDARY};
        }}

        /* ---- Cards ---- */
        .mg-card {{
            background: {COLOR_SURFACE}; border: 1px solid {COLOR_BORDER}; border-radius: 22px;
            padding: 22px; margin-bottom: 18px;
        }}
        .mg-meal-image {{
            width: 100%; height: 140px; border-radius: 16px; margin-bottom: 14px;
            display: flex; align-items: center; justify-content: center; font-size: 2.4rem;
            background: linear-gradient(135deg, rgba(110,139,116,0.14), rgba(201,168,106,0.14));
        }}
        .mg-meal-type {{ font-size: 0.72rem; letter-spacing: 1.5px; text-transform: uppercase; color: {COLOR_SAGE}; font-weight: 700; }}
        .mg-meal-name {{ font-size: 1.15rem; font-weight: 700; color: {COLOR_TEXT}; margin: 4px 0 10px 0; }}
        .mg-meal-name-list {{ list-style: none; margin: 4px 0 10px 0; padding: 0; }}
        .mg-meal-name-list li {{ font-size: 1.15rem; font-weight: 700; color: {COLOR_TEXT}; line-height: 1.5; }}
        .mg-macro-row {{ display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 4px; }}
        .mg-macro-badge {{ font-size: 0.78rem; color: {COLOR_TEXT_SECONDARY}; background: {COLOR_BG}; border-radius: 10px; padding: 4px 10px; font-weight: 600; }}

        /* ---- Macro bars ---- */
        .mg-bar-track {{ background: {COLOR_BG}; border-radius: 8px; height: 10px; width: 100%; overflow: hidden; margin: 6px 0 14px 0; }}
        .mg-bar-fill {{ height: 100%; border-radius: 8px; }}

        /* ---- Insight card ---- */
        .mg-insight-card {{
            background: {COLOR_DARK}; color: #F0F0EE; border-radius: 24px; padding: 30px 32px; margin: 18px 0;
            border: 1px solid rgba(155,124,255,0.35);
        }}
        .mg-insight-title {{ font-size: 1.3rem; margin-bottom: 4px; color: #B9A6FF; }}
        .mg-insight-sub {{ font-size: 0.82rem; color: rgba(185,166,255,0.75); margin-bottom: 16px; text-transform: uppercase; letter-spacing: 1.5px; font-weight: 700; }}
        .mg-insight-body {{ font-size: 1.02rem; line-height: 1.7; color: #000000; }}

        .mg-footer {{ text-align: center; padding: 40px 0 20px 0; color: {COLOR_TEXT_MUTED}; font-size: 0.85rem; border-top: 1px solid {COLOR_BORDER}; margin-top: 40px; }}

        /* Restyle default streamlit buttons to feel premium */
        div.stButton > button {{
            background: {COLOR_DARK}; color: white; border-radius: 999px; border: none;
            padding: 10px 22px; font-weight: 700; font-size: 0.88rem;
            box-shadow: none; transition: all 0.15s ease;
        }}
        div.stButton > button:hover {{ background: {COLOR_SAGE}; color: white; transform: translateY(-1px); }}
        div.stButton > button p {{ font-weight: 700; }}

        div[data-testid="stExpander"] {{
            border-radius: 18px !important; border: 1px solid {COLOR_BORDER} !important; background: {COLOR_SURFACE};
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_top_nav(active_tab: str = "My Week"):
    initial = "J"
    name = (st.session_state.get("current_constraints", {}) or {}).get("name", "")
    if name:
        initial = name.strip()[0].upper()

    tabs_html = "".join(
        f'<span style="{"color:#151515;" if t == active_tab else ""}">{t}</span>'
        for t in ["My Week", "Nutrition", "Profile"]
    )

    st.markdown(
        f"""
        <div class="mg-navbar">
            <div>
                <div class="mg-logo">MG AI</div>
                <div class="mg-logo-sub">Personal Nutrition Intelligence</div>
            </div>
            <div class="mg-nav-links">{tabs_html}</div>
            <div class="mg-avatar">{initial}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_hero(profile: Dict, days: List[DayPlan]):
    reference_day = days[0] if days else None
    calories = f"{reference_day.total_calories:,.0f}" if reference_day else "—"
    protein = f"{reference_day.total_protein_g:,.0f}" if reference_day else "—"
    carbs = f"{reference_day.total_carbs_g:,.0f}" if reference_day else "—"
    fat = f"{reference_day.total_fat_g:,.0f}" if reference_day else "—"

    st.markdown('<div class="mg-hero-label">Your Personal Nutrition Plan</div>', unsafe_allow_html=True)
    st.markdown('<div class="mg-hero-title mg-serif">Your week, intelligently designed.</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="mg-hero-sub">A personalised nutrition plan created around your profile, '
        'preferences and nutritional goals.</div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        f"""
        <div class="mg-stat-row">
            <div class="mg-stat"><div class="mg-stat-value">{calories} kcal</div><div class="mg-stat-label">Daily target</div></div>
            <div class="mg-stat"><div class="mg-stat-value">{protein} g</div><div class="mg-stat-label">Protein</div></div>
            <div class="mg-stat"><div class="mg-stat-value">{carbs} g</div><div class="mg-stat-label">Carbohydrates</div></div>
            <div class="mg-stat"><div class="mg-stat-value">{fat} g</div><div class="mg-stat-label">Fat</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_profile_chips(profile: Dict):
    st.markdown('<div class="mg-section-title mg-serif">Your Profile</div>', unsafe_allow_html=True)

    chips = []
    if profile.get("age"):
        chips.append(f"{profile['age']} years")
    if profile.get("weight_kg"):
        chips.append(f"{float(profile['weight_kg']):.0f} kg")
    if profile.get("gender"):
        chips.append(str(profile["gender"]))
    if profile.get("activity_level"):
        chips.append(str(profile["activity_level"]).title())

    diet_pref = profile.get("dietary_preference", [])
    if isinstance(diet_pref, str):
        diet_pref = [diet_pref]
    chips.extend(diet_pref)

    cuisine = profile.get("cuisine", [])
    if isinstance(cuisine, str):
        cuisine = [cuisine]
    chips.extend(f"{c} Cuisine" for c in cuisine[:2])

    chips_html = "".join(f'<span class="mg-chip">{c}</span>' for c in chips if c)
    st.markdown(f'<div>{chips_html}</div>', unsafe_allow_html=True)

    restrictions = profile.get("restrictions", []) or ["No added restrictions"]
    allergies = profile.get("allergies", []) or ["None"]
    digestive = profile.get("digestive_issues", []) or ["None reported"]
    medical = profile.get("medical_conditions", []) or ["None reported"]

    col1, col2, col3, col4 = st.columns(4)
    for col, label, values in [
        (col1, "Restrictions", restrictions),
        (col2, "Allergies", allergies),
        (col3, "Digestive considerations", digestive),
        (col4, "Medical conditions", medical),
    ]:
        with col:
            st.markdown(f'<div style="color:{COLOR_TEXT_MUTED}; font-size:0.75rem; font-weight:700; text-transform:uppercase; letter-spacing:0.5px; margin-top:14px;">{label}</div>', unsafe_allow_html=True)
            chip_html = "".join(f'<span class="mg-chip mg-chip-gold">{v}</span>' for v in values)
            st.markdown(f'<div>{chip_html}</div>', unsafe_allow_html=True)


def render_day_selector(days: List[DayPlan], state_key: str = "mg_selected_day_idx") -> int:
    if state_key not in st.session_state or st.session_state[state_key] >= len(days):
        st.session_state[state_key] = 0

    st.markdown('<div class="mg-section-title mg-serif">Week at a Glance</div>', unsafe_allow_html=True)

    cols = st.columns(len(days))
    for i, (col, day) in enumerate(zip(cols, days)):
        with col:
            label = f"{day.day_name[:3].upper()} {i + 1}"
            btn_type = "primary" if i == st.session_state[state_key] else "secondary"
            if st.button(label, key=f"mg_day_btn_{i}", use_container_width=True, type=btn_type):
                st.session_state[state_key] = i
                st.rerun()

    return st.session_state[state_key]


def _bar(pct: float, color: str) -> str:
    pct = max(0.0, min(100.0, pct))
    return f'<div class="mg-bar-track"><div class="mg-bar-fill" style="width:{pct}%; background:{color};"></div></div>'


def render_daily_overview(day: DayPlan):
    st.markdown(
        f'<div class="mg-section-title mg-serif">{day.day_name}, {day.date_label}</div>'
        f'<div class="mg-section-sub">{day.total_calories:,.0f} kcal planned</div>',
        unsafe_allow_html=True,
    )

    total_cal = day.total_calories or 1
    c1, c2, c3, c4 = st.columns(4)
    for col, label, value, color in [
        (c1, "Calories", f"{day.total_calories:,.0f} kcal", COLOR_DARK),
        (c2, "Protein", f"{day.total_protein_g:,.0f} g", COLOR_SAGE),
        (c3, "Carbs", f"{day.total_carbs_g:,.0f} g", COLOR_GOLD),
        (c4, "Fat", f"{day.total_fat_g:,.0f} g", "#B08968"),
    ]:
        with col:
            st.markdown(f'<div class="mg-stat-value">{value}</div><div class="mg-stat-label">{label}</div>', unsafe_allow_html=True)


def render_meal_card(meal: Meal, day_number: int, index: int):
    icon = MEAL_ICONS.get(meal.name, "🍽️")
    st.markdown('<div class="mg-card">', unsafe_allow_html=True)
    st.markdown(f'<div class="mg-meal-image">{icon}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="mg-meal-type">{meal.name}</div>', unsafe_allow_html=True)

    if meal.items:
        names_html = "".join(f"<li>{item.name}</li>" for item in meal.items)
        st.markdown(f'<ul class="mg-meal-name-list">{names_html}</ul>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="mg-meal-name">No items generated</div>', unsafe_allow_html=True)

    st.markdown(
        f"""
        <div class="mg-macro-row">
            <span class="mg-macro-badge">{meal.display_calories:,.0f} kcal</span>
            <span class="mg-macro-badge">Protein {meal.total_protein_g:,.0f}g</span>
            <span class="mg-macro-badge">Carbs {meal.total_carbs_g:,.0f}g</span>
            <span class="mg-macro-badge">Fat {meal.total_fat_g:,.0f}g</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.expander("View recipe & detailed nutrition"):
        for item in meal.items:
            st.markdown(f"**{item.name}**")
            meta_bits = []
            if item.household_measure:
                meta_bits.append(item.household_measure)
            if item.portion_weight:
                meta_bits.append(item.portion_weight)
            if meta_bits:
                st.caption(" · ".join(meta_bits))

            nutrient_rows = [
                ("Calories", item.nutrients.get("calories"), "kcal"),
                ("Protein", item.nutrients.get("protein_g"), "g"),
                ("Carbohydrates", item.nutrients.get("carbs_g"), "g"),
                ("Fat", item.nutrients.get("fat_g"), "g"),
                ("Fiber", item.nutrients.get("fiber_g"), "g"),
                ("Sodium", item.nutrients.get("sodium_mg"), "mg"),
                ("Sugar", item.nutrients.get("sugar_g"), "g"),
                ("Cholesterol", item.nutrients.get("cholesterol_mg"), "mg"),
                ("Iodine", item.nutrients.get("iodine_mcg"), "mcg"),
            ]
            table_md = "| Nutrient | Amount |\n|---|---|\n"
            for label, value, unit in nutrient_rows:
                if value is not None:
                    table_md += f"| {label} | {value:g} {unit} |\n"
            st.markdown(table_md)

            if item.ingredients:
                st.markdown("**Ingredients**")
                st.markdown("\n".join(f"- {ing}" for ing in item.ingredients))

            if item.recipe:
                st.markdown("**How to prepare**")
                for step_i, step in enumerate(item.recipe, 1):
                    st.markdown(f"{step_i}. {step}")

            checkin_key = f"mg_checkin_{day_number}_{index}_{item.food_id or item.name}"
            checked = st.checkbox("Mark as prepared", key=checkin_key)
            if checked:
                st.caption("Saved for this session.")
            st.markdown("---")

    st.markdown('</div>', unsafe_allow_html=True)


def render_nutrition_intelligence(day: DayPlan):
    st.markdown('<div class="mg-section-title mg-serif">Nutrition Intelligence</div>', unsafe_allow_html=True)
    st.markdown('<div class="mg-section-sub">Macro distribution for the selected day</div>', unsafe_allow_html=True)

    total = day.total_protein_g + day.total_carbs_g + day.total_fat_g
    if total <= 0:
        st.caption("Macro data is not available for this day yet.")
        return

    protein_pct = day.total_protein_g / total * 100
    carbs_pct = day.total_carbs_g / total * 100
    fat_pct = day.total_fat_g / total * 100

    for label, pct, color in [
        ("Protein", protein_pct, COLOR_SAGE),
        ("Carbohydrates", carbs_pct, COLOR_GOLD),
        ("Fat", fat_pct, "#B08968"),
    ]:
        st.markdown(f'<div style="display:flex; justify-content:space-between; font-size:0.88rem; font-weight:600; color:{COLOR_TEXT};"><span>{label}</span><span>{pct:.0f}%</span></div>', unsafe_allow_html=True)
        st.markdown(_bar(pct, color), unsafe_allow_html=True)


def render_ai_insight(day: DayPlan, profile: Dict):
    st.markdown('<div class="mg-insight-card">', unsafe_allow_html=True)
    st.markdown('<div class="mg-insight-title mg-serif">MG AI Insight</div>', unsafe_allow_html=True)
    st.markdown('<div class="mg-insight-sub">Personalised nutrition intelligence</div>', unsafe_allow_html=True)

    diet_pref = profile.get("dietary_preference", [])
    if isinstance(diet_pref, str):
        diet_pref = [diet_pref]
    diet_str = diet_pref[0] if diet_pref else "your dietary preferences"
    meal_count = sum(len(m.items) for m in day.meals)

    insight_text = (
        f"Based on your current profile and generated meal plan, {day.day_name}'s plan is structured "
        f"around a {day.total_calories:,.0f} kcal target across {len(day.meals)} meals and {meal_count} food items, "
        f"designed to align with {diet_str.lower()} while maintaining variety across your week."
    )
    st.markdown(f'<div class="mg-insight-body">{insight_text}</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)


def render_personalisation_score(profile: Dict) -> Optional[int]:
    """Product-only UI metric, not a medical score. Returns None if not enough
    profile signal exists to compute it meaningfully."""
    signals = [
        "dietary_preference", "cuisine", "allergies", "medical_conditions",
        "digestive_issues", "restrictions",
    ]
    present = sum(1 for s in signals if profile.get(s))
    if present == 0:
        return None
    score = int(40 + (present / len(signals)) * 55)
    return min(score, 95)


def render_footer():
    st.markdown(
        f"""
        <div class="mg-footer">
            <div style="font-weight:800; color:{COLOR_TEXT}; margin-bottom:4px;">MG AI</div>
            Personal nutrition intelligence.
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_empty_plan_state():
    st.markdown('<div class="mg-hero-title mg-serif" style="font-size:2rem;">Your personalised week is waiting.</div>', unsafe_allow_html=True)
    st.markdown('<div class="mg-hero-sub">Generate your first 7-day nutrition plan.</div>', unsafe_allow_html=True)
