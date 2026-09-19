from __future__ import annotations

import pandas as pd
import json
import random
import hashlib
import re
import os
import logging
import difflib
import asyncio
from typing import List, Dict, Set, Optional, Tuple, Any
from datetime import datetime
from collections import defaultdict, Counter
from pydantic import BaseModel, Field
import pdfplumber
import json
# Configure logger
logger = logging.getLogger(__name__)


try:
    from .tool_core import BaseTool, LLMService, ToolResult, ToolType
except (ImportError, ModuleNotFoundError):
    class BaseTool:
        def __init__(self, name, description):
            self.name = name
            self.description = description

    class LLMService:
        async def query(self, prompt, system_prompt, max_tokens):
            return "Mock response"

    class ToolType:
        GENERAL_FITNESS_QUERY = "general_fitness_query"

# ============ COMPATIBILITY LAYER ============
class ToolResult:
    """Compatibility class for agent_core.py."""
    def __init__(self, success: bool, data: dict = None, error: str = None, metadata: dict = None):
        self.success = success
        self.data = data or {}
        self.error = error
        self.metadata = metadata or {}


class ActivityBlock(BaseModel):
    type: str
    duration: Optional[str] = None
    frequency: Optional[str] = None
    requires_dataset_exercises: bool = True
    prescribed: bool = False
    equipment: Optional[str] = None
    intensity: Optional[str] = None
    source_text: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)


class ParsedNoteProfile(BaseModel):
    source_type: str
    generation_mode: str = "expert_note"
    goal: str
    days_per_week: int
    session_duration: str
    fitness_medical_conditions: List[str] = Field(default_factory=list)
    physical_limitations: List[str] = Field(default_factory=list)
    avoid_terms: List[str] = Field(default_factory=list)
    available_equipment: List[str] = Field(default_factory=list)
    prescribed_exercises: List[dict] = Field(default_factory=list)
    weekly_schedule: Dict[str, List[dict]] = Field(default_factory=dict)
    flags: Dict[str, bool] = Field(default_factory=dict)


try:
    ActivityBlock.model_rebuild()
    ParsedNoteProfile.model_rebuild()
except Exception:
    pass

_REQUIRED_EXERCISE_KEYS = [
    "name",
    "gui_id",
    "benefit",
    "steps",
    "sets",
    "reps",
    "time",
    "side",
    "intensity_rpe",
    "rest",
    "equipment",
    "est_calories",
    "safety_cue",
    "met_value",
    "est_time_sec",
    "est_time_human",
    "planned_sets_count",
    "planned_total_cal",
    "_meta",
]

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
# BASE_DIR = os.path.dirname(_MODULE_DIR) if os.path.basename(_MODULE_DIR) == "core" else _MODULE_DIR
# DATASETS_DIR = os.path.join(BASE_DIR, "dataset")
BASE_DIR = os.path.dirname(_MODULE_DIR) # Go up to the root directory
DATASETS_DIR = os.path.join(BASE_DIR, "datasets") # Use the correct plural name
_NULLISH_TEXT = {"", "nan", "none", "null", "n/a"}


def _normalize_clinical_terms(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw_items = value
    else:
        raw_items = re.split(r"[,;/|]", str(value))
    out: List[str] = []
    for item in raw_items:
        text = str(item or "").strip().lower()
        if text and text not in _NULLISH_TEXT:
            out.append(text)
    return out


def _clean_csv_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    # Normalize non-standard hyphens (en-dash, em-dash) to standard hyphen.
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    if text.lower() in _NULLISH_TEXT:
        return ""
    return text


def _clean_prescription_cell(value: Any) -> str:
    return _clean_csv_text(value)


def _resolve_prescription_fields(row: Any = None, fallback: Any = "") -> Dict[str, Optional[str]]:
    """
    Return mutually exclusive prescription fields with strict priority:
    Side > Time > Reps.

    Exactly one field is populated when data is available; the others stay None.
    """
    def _pick(source: Any, *keys: str) -> str:
        if source is None:
            return ""
        for key in keys:
            try:
                value = source.get(key, "")
            except Exception:
                value = ""
            clean = _clean_prescription_cell(value)
            if clean:
                return clean
        return ""

    side_value = _pick(row, "Side", "side")
    time_value = _pick(row, "Time", "time")
    reps_value = _pick(row, "Reps", "reps")

    if side_value:
        return {"reps": None, "time": None, "side": side_value}
    if time_value:
        return {"reps": None, "time": time_value, "side": None}
    if reps_value:
        return {"reps": reps_value, "time": None, "side": None}

    fallback_value = _clean_prescription_cell(fallback)
    if not fallback_value:
        return {"reps": None, "time": None, "side": None}

    low = fallback_value.lower()
    if "each side" in low or "per side" in low:
        return {"reps": None, "time": None, "side": fallback_value}
    if any(tok in low for tok in (" sec", "secs", "second", "seconds", " min", "mins", "minute", "minutes", "hold")):
        return {"reps": None, "time": fallback_value, "side": None}
    return {"reps": fallback_value, "time": None, "side": None}


def _resolve_reps_time_or_side(row: Any, fallback: Any = "") -> str:
    """
    Resolve exercise prescription text with priority:
    Side (each-side formats) > Time (time-based formats) > Reps > fallback.
    """
    rx = _resolve_prescription_fields(row=row, fallback=fallback)
    return rx.get("side") or rx.get("time") or rx.get("reps") or ""


def _display_prescription(ex: Optional[Dict[str, Any]]) -> str:
    data = ex or {}
    for key in ("side", "time", "reps"):
        value = _clean_prescription_cell(data.get(key))
        if value:
            return value
    return "N/A"


def _clinical_guardrail_patterns(context: Optional[Dict[str, Any]], slot_type: str = "main") -> List[str]:
    ctx = context or {}
    flags = ctx.get("flags") or {}
    patterns: List[str] = []
    literal_terms: Set[str] = set()

    for key in ("conditions", "fitness_medical_conditions", "limitations"):
        literal_terms.update(_normalize_clinical_terms(ctx.get(key)))
    for key in ("physical_limitation", "physical_limitations"):
        literal_terms.update(_normalize_clinical_terms(ctx.get(key)))

    if flags.get("knee_sensitive"):
        patterns.append(r"\bknee\b|\bacl\b|\bmeniscus\b|\bpatella\b|\blunge\b|\bsquat\b|\bstep[\s-]?up\b|\bjump\w*\b|\bplyo\w*\b|\bhop\w*\b|\bbound\w*\b|\bskater\w*\b")
    if flags.get("avoid_floor_work"):
        patterns.append(r"\bfloor\b|\bsupine\b|\bprone\b|\blying\b|\bkneeling\b|\bmat\b|\bquadruped\b|\btable[\s-]?top\b|\bdonkey kick\b|\bpush[\s-]?up\b|\bplank\b|\bbridge\b|\bdead[\s-]?bug\b|\bbird[\s-]?dog\b|\bcat[\s-]*cow\b|\bcobra\b|\bgate pose\b|\bchild(?:'s)? pose\b|\bknees?\s+(?:into|to)\s+chest\b")
    if flags.get("high_impact_restricted") and slot_type in {"warmup", "main"}:
        patterns.append(r"\bhigh[\s-]?impact\b|\bjump\w*\b|\bplyo\w*\b|\bhop\w*\b|\bbound\w*\b|\bskater\w*\b|\bsprint\w*\b")

    for term in sorted(literal_terms):
        patterns.append(re.escape(term))
    return [p for p in patterns if p]


def _hard_medical_exclusion(df: pd.DataFrame, context: Optional[Dict[str, Any]], slot_type: str = "main") -> pd.DataFrame:
    if df is None or df.empty:
        return df
    patterns = _clinical_guardrail_patterns(context, slot_type)
    filtered = df.copy()
    filtered["_safety_penalty"] = 0
    if not patterns:
        return filtered

    safety_pattern = "|".join(patterns)
    text_cols = [
        "Exercise Name",
        "Primary Category",
        "Body Region",
        "Tags",
        "Physical limitations",
        "is_not_suitable_for",
        "Safety cue",
        "Health benefit",
        "Steps to perform",
    ]
    combined = pd.Series("", index=filtered.index, dtype="object")
    for col in text_cols:
        if col in filtered.columns:
            combined = combined + " " + filtered[col].fillna("").astype(str)

    filtered["_safety_penalty"] = combined.str.contains(
        safety_pattern,
        case=False,
        na=False,
        regex=True,
    ).astype(int)
    return filtered[filtered["_safety_penalty"] == 0].copy()

# ============ LAYER 1: DATASET LAYER (ETL) ============
class FitnessDataset:
    POSSIBLE_PATHS = [
        os.path.join(DATASETS_DIR, "fitness.csv"),
        os.path.join(DATASETS_DIR, "fitness.csv.csv"),
        os.path.join(DATASETS_DIR, "Newdata 1.csv"),
    ]

    @classmethod
    def _empty_frame(cls) -> pd.DataFrame:
        cols = [
            "unique id", "guidid", "exercise name", "age suitability", "goal", "primary category",
            "body region", "equipments", "fitness level", "physical limitations",
            "sets", "reps", "time", "side", "rpe", "rest", "rest intervals", "health benefit",
            "steps to perform", "safety cue", "met value", "is_not_suitable_for", "tags",
        ]
        return cls._add_legacy_column_aliases(pd.DataFrame(columns=cols))

    @staticmethod
    def _add_legacy_column_aliases(df: pd.DataFrame) -> pd.DataFrame:
        alias_map = {
            "unique id": "Unique ID",
            "guidid": "GuidId",
            "exercise name": "Exercise Name",
            "age suitability": "Age Suitability",
            "goal": "Goal",
            "primary category": "Primary Category",
            "body region": "Body Region",
            "equipments": "Equipments",
            "fitness level": "Fitness Level",
            "physical limitations": "Physical limitations",
            "sets": "Sets",
            "reps": "Reps",
            "time": "Time",
            "side": "Side",
            "rpe": "RPE",
            "rest": "Rest",
            "rest intervals": "Rest intervals",
            "health benefit": "Health benefit",
            "steps to perform": "Steps to perform",
            "safety cue": "Safety cue",
            "met value": "MET value",
            "is_not_suitable_for": "is_not_suitable_for",
            "tags": "Tags",
        }
        for lower_col, legacy_col in alias_map.items():
            if lower_col in df.columns and legacy_col not in df.columns:
                df[legacy_col] = df[lower_col]
        return df

    @staticmethod
    def _normalize_tags(value: Any) -> str:
        raw = str(value or "")
        parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
        normalized: List[str] = []
        for part in parts:
            token = re.sub(r"\s+", " ", part)
            if re.fullmatch(r"warm[\s\-]*up", token):
                token = "warm up"
            elif re.fullmatch(r"cool[\s\-]*down", token):
                token = "cooldown"
            elif re.fullmatch(r"main\s*work[\s\-]*out|main\s*workout", token):
                token = "main work out"
            if token not in normalized:
                normalized.append(token)
        return ", ".join(normalized)

    @classmethod
    def load(cls, preferred_path: Optional[str] = None) -> pd.DataFrame:
        found_path = None
        candidate_paths = [preferred_path] if preferred_path else []
        candidate_paths.extend(cls.POSSIBLE_PATHS)
        for path in candidate_paths:
            if not path:
                continue
            if os.path.exists(path):
                found_path = path
                break

        if not found_path:
            try:
                candidates = [
                    os.path.join(DATASETS_DIR, f)
                    for f in os.listdir(DATASETS_DIR)
                    if str(f).lower().endswith(".csv")
                ]
                fitness_named = [p for p in candidates if "fitness" in os.path.basename(p).lower()]
                found_path = (fitness_named[0] if fitness_named else (candidates[0] if candidates else None))
            except Exception:
                found_path = None

        if not found_path:
            logger.error(f"Fitness CSV not found. Searched: {cls.POSSIBLE_PATHS}")
            return cls._empty_frame()

        try:
            try:
                df = pd.read_csv(found_path, on_bad_lines='skip')
            except Exception:
                df = pd.read_csv(found_path, error_bad_lines=False, engine='python')

            df.columns = [c.strip().replace('\ufeff', '').lower() for c in df.columns]

            column_mapping = {
                'unique id': ['unique id', 'id', 'uid', 'unique_id', 'exercise_id', 'exerciseid'],
                'guidid': ['guidid', 'guid_id', 'gui_id'],
                'physical limitations': ['physical limitations', 'limitations', 'injuries'],
                'is_not_suitable_for': ['is_not_suitable_for', 'medical contraindications'],
                'tags': ['tags'],
                'equipments': ['equipment', 'equipments'],
                'exercise name': ['exercise name', 'name'],
                'age suitability': ['age suitability', 'age'],
                'primary category': ['primary category', 'category'],
                'body region': ['body region', 'muscle group'],
                'met value': ['met value', 'met'],
                'health benefit': ['health benefit', 'benefit'],
                'safety cue': ['safety cue', 'safety'],
                'rest': ['rest', 'rest intervals'],
                'steps to perform': ['steps to perform', 'instructions', 'steps'],
            }

            for standard, variations in column_mapping.items():
                if standard not in df.columns:
                    for v in variations:
                        match = next((col for col in df.columns if col == v), None)
                        if match:
                            df.rename(columns={match: standard}, inplace=True)
                            break

            required_cols = [
                'unique id', 'guidid', 'physical limitations', 'is_not_suitable_for', 'tags',
                'equipments', 'exercise name', 'primary category', 'body region',
                'age suitability', 'met value', 'safety cue', 'rest',
                'goal', 'fitness level', 'health benefit', 'steps to perform',
            ]
            for col in required_cols:
                if col not in df.columns:
                    if col == 'met value':
                        df[col] = 3.0
                    elif col in ['unique id']:
                        df[col] = 'N/A'
                    else:
                        df[col] = 'None'

            df['met value'] = pd.to_numeric(df['met value'], errors='coerce').fillna(3.0)
            text_cols = [c for c in df.columns if c != 'met value']
            for col in text_cols:
                df[col] = df[col].fillna('').astype(str).str.strip()
            df['unique id'] = df['unique id'].replace(['', 'nan', 'None', 'none', 'null', 'NULL'], 'N/A')
            if 'tags' in df.columns:
                df['tags'] = df['tags'].apply(cls._normalize_tags)

            df = df[
                df['guidid'].notna() &
                (df['guidid'] != '') &
                (df['guidid'].str.lower() != 'none') &
                (df['guidid'].str.lower() != '9fad6875-5995-4b77-b6d3-515edd6d42a0')
            ].copy()

            # Algorithmic fixes
            df.loc[df['exercise name'].str.contains('Arm|Shoulder|Neck|Wrist|Elbow', case=False, na=False), 'body region'] = 'Upper'
            df.loc[df['exercise name'].str.contains('Stretch|Yoga|Fold|Butterfly', case=False, na=False), 'primary category'] = 'Flexibility/Stretching'
            df.loc[df['exercise name'].str.contains('Balance|Stork|Single Leg', case=False, na=False), 'primary category'] = 'Balance & Stability'

            return cls._add_legacy_column_aliases(df)

        except Exception as e:
            logger.error(f"Data Loading Error: {e}")
            return cls._empty_frame()


# ============ LAYER 2: FILTERING LAYER ============
class ExerciseFilter:
    @staticmethod
    def _parse_age_suitability(user_age: Optional[int], range_str: str) -> bool:
        if user_age is None:
            user_age = 30
        rs = str(range_str).lower()
        if 'all ages' in rs or not rs or rs == 'nan':
            return True
        nums = [int(x) for x in re.findall(r'\d+', rs)]
        if '+' in rs and nums:
            return user_age >= nums[0]
        elif len(nums) == 2:
            return nums[0] <= user_age <= nums[1]
        elif len(nums) == 1:
            return user_age <= nums[0]
        return True

    @staticmethod
    def apply_filters(df: pd.DataFrame, profile: Dict) -> pd.DataFrame:
        if df.empty:
            return df
        filtered = df.copy()

        user_age = profile.get('age', 30)
        if user_age is None:
            user_age = 30

        filtered = filtered[filtered['Age Suitability'].apply(
            lambda x: ExerciseFilter._parse_age_suitability(user_age, x)
        )]

        equip_list = profile.get('available_equipment') or []
        user_inventory = set([str(e).lower() for e in equip_list])

        if "full gym access" not in user_inventory:
            user_inventory.add('bodyweight')
            user_inventory.add('none')
            user_inventory.add('bodyweight only')

            def is_compatible(exo_eq):
                if not exo_eq or exo_eq.lower() == 'nan' or exo_eq.lower() == 'none':
                    return True
                reqs = [x.strip().lower() for x in exo_eq.split(',')]
                for req in reqs:
                    if not any(item in req or req in item for item in user_inventory):
                        return False
                return True

            filtered = filtered[filtered['Equipments'].apply(is_compatible)]

        conditions = profile.get('fitness_medical_conditions') or []
        limitations = profile.get('physical_limitation', '')

        avoid_terms = [c.lower() for c in conditions if c and c.lower() != "none"]
        if limitations and limitations.lower() != 'none':
            avoid_terms.extend([t.strip().lower() for t in limitations.split(',') if t.strip()])

        for term in avoid_terms:
            if len(term) < 4:
                continue
            filtered = filtered[~filtered['is_not_suitable_for'].str.contains(term, case=False, na=False, regex=False)]
            filtered = filtered[~filtered['Physical limitations'].str.contains(term, case=False, na=False, regex=False)]

        return filtered


# ============ LAYER 3: PLANNING LAYER ============
class WorkoutPlanner:
    @staticmethod
    def get_weekly_split(goal: str, num_days: int) -> List[str]:
        cycle = [
            "Upper Focus",
            "Lower Focus",
            "Cardio Focus",
            "Core Focus",
            "Full Body",
        ]
        count = max(1, int(num_days or 1))
        return [cycle[i % len(cycle)] for i in range(count)]

    @staticmethod
    def get_volume_intensity(goal: str, level: str) -> Tuple[str, str, str, str]:
        g = str(goal).lower()
        l = str(level).lower()
        rpe = "5-7"
        sets = "3"
        reps = "10"
        rest = "60s"
        if "loss" in g:
            rpe = "4-7"
            reps = "12-15"
            rest = "30-45s"
            sets = "2" if "beginner" in l else "3"
        elif "gain" in g or "muscle" in g:
            rpe = "6-8"
            reps = "8-12"
            rest = "90-120s"
            sets = "3-4"
        return sets, reps, rpe, rest

    @staticmethod
    def get_exercise_count(duration_str: Optional[str]) -> int:
        if duration_str is None:
            return 1
        raw = str(duration_str).strip()
        if not raw or raw.lower() in ['none', 'nan', 'null']:
            return 1
        numbers = re.findall(r'\d+', raw)
        if not numbers:
            return 1
        minutes = int(numbers[0])
        if minutes <= 2: return 1
        if minutes <= 10: return 2
        if minutes < 20: return 2
        if minutes < 40: return 4
        if minutes < 55: return 5
        return 6


# ============ LAYER 4: SELECTION LAYER ============
class ExerciseSelector:
    def __init__(self, random_seed: int = None):
        self.rng = random.Random(random_seed) if random_seed is not None else random.Random()
        self.context: Dict[str, Any] = {}

    def set_context(self, profile: Optional[Dict[str, Any]] = None, day_type: str = "", target_tag: str = "") -> None:
        self.context = {
            "profile": dict(profile or {}),
            "day_type": str(day_type or ""),
            "target_tag": str(target_tag or ""),
        }

    def _score_candidate(self, row: pd.Series) -> float:
        ctx = self.context or {}
        profile = ctx.get("profile") or {}
        day_type = str(ctx.get("day_type") or "").lower()
        target_tag = str(ctx.get("target_tag") or "").lower()

        goal = str(profile.get("primary_goal") or profile.get("goal") or "").lower()
        level = str(profile.get("fitness_level") or "").lower()
        equipment = [str(e).lower() for e in (profile.get("available_equipment") or [])]

        # Normalize target_body_parts for matching (e.g., "LOWERBODY" → "lower")
        raw_target_body = str(profile.get("target_body_parts") or "").lower().replace("body", "").strip()

        row_goal = str(row.get("Goal", "")).lower()
        row_region = str(row.get("Body Region", "")).lower()
        row_eq = str(row.get("Equipments", "")).lower()
        row_level = str(row.get("Fitness Level", "")).lower()
        row_tags = str(row.get("Tags", "")).lower()
        row_cat = str(row.get("Primary Category", "")).lower()
        row_name = str(row.get("Exercise Name", "")).lower()

        score = 0.0
        if goal and goal in row_goal: score += 5
        if day_type and (day_type in row_region or day_type in row_cat or day_type in row_name): score += 4
        
        # Boost exercises matching user's preferred body region focus
        if raw_target_body and raw_target_body in row_region: score += 3
        
        if equipment and any(eq in row_eq for eq in equipment): score += 3
        if level and level in row_level: score += 2
        if target_tag and target_tag in row_tags: score += 1
        return score

    def select_unique(self, pool: pd.DataFrame, used_set: Set[str]) -> Optional[pd.Series]:
        if pool.empty:
            return None
        avail = pool[~pool['Exercise Name'].isin(used_set)]
        if avail.empty:
            return None
        candidates = []
        for _, row in avail.iterrows():
            name = row['Exercise Name']
            is_fuzzy_dup = False
            for used in used_set:
                if difflib.SequenceMatcher(None, name.lower(), used.lower()).ratio() > 0.85:
                    is_fuzzy_dup = True
                    break
            if not is_fuzzy_dup:
                candidates.append(row)
        if not candidates:
            return None
        sample_size = min(15, len(candidates))
        sampled = self.rng.sample(candidates, sample_size) if len(candidates) > sample_size else list(candidates)
        weights = [max(1.0, self._score_candidate(row) + 1.0) for row in sampled]
        return self.rng.choices(sampled, weights=weights, k=1)[0] if sampled else None


# ============ LAYER 5: COMPOSITION LAYER ============
class WorkoutComposer:
    def __init__(self, selector: ExerciseSelector):
        self.selector = selector

    def _safe_pool(self, pool, slot):
        safe = self._apply_medical_guardrails(
            pool,
            self._active_clinical_context,
            slot
        )
        return safe if safe is not None and not safe.empty else None

    def _calc_calories(self, met, weight, duration_min):
        return round((met * 3.5 * weight / 200) * duration_min, 1)

    def _calculate_duration_seconds(self, sets_str, reps_str, rest_str):
        try:
            sets = int(re.search(r'\d+', str(sets_str)).group())
        except Exception:
            sets = 3

        rest_sec = 60
        rest_clean = str(rest_str).lower()
        if 'min' in rest_clean:
            match = re.search(r'(\d+)', rest_clean)
            if match:
                rest_sec = int(match.group(1)) * 60
        else:
            match = re.search(r'(\d+)', rest_clean)
            if match:
                rest_sec = int(match.group(1))

        reps_clean = str(reps_str).lower()
        active_time_per_set = 60

        if 'min' in reps_clean or 'sec' in reps_clean:
            match = re.search(r'(\d+)', reps_clean)
            val = int(match.group(1)) if match else 30
            active_time_per_set = val * 60 if 'min' in reps_clean else val
        else:
            match = re.findall(r'\d+', reps_clean)
            avg_reps = sum([int(x) for x in match]) / len(match) if match else 10
            active_time_per_set = avg_reps * 5

        per_set_time = active_time_per_set + rest_sec
        return int(per_set_time)

    def _generate_dynamic_title(self, day_type, main_exercises):
        if not main_exercises:
            return f"{day_type} Session"

        TITLE_MAP = {
            "Upper": [
                "Upper Body Strength & Definition", "Upper Body Strength Builder",
                "Upper Body Functional Strength", "Upper Body Strength & Stability",
                "Upper Body Mobility Flow", "Upper Body Strength & Mobility",
                "Upper Body Movement & Control",
            ],
            "Lower": [
                "Lower Body Strength & Power", "Lower Body Strength Builder",
                "Lower Body Stability & Strength", "Lower Body Mobility & Balance",
                "Lower Body Strength & Mobility", "Lower Body Movement & Control",
            ],
            "Core": [
                "Core Strength & Stability", "Core Control & Conditioning",
                "Core Strength Builder", "Core Mobility & Stability",
                "Core Activation & Control",
            ],
            "Full": [
                "Full Body Strength & Conditioning", "Full Body Functional Strength",
                "Full Body Strength & Stability", "Full Body Cardio Conditioning",
                "Low Impact Cardio Session", "Moderate Intensity Cardio",
                "Full Body Mobility Flow", "Full Body Recovery & Stretch",
                "Full Body Movement & Balance",
            ],
            "Cardio": [
                "Progressive Cardio Conditioning", "Interval Cardio Session",
                "Steady Pace Cardio", "Cardio & Endurance Builder",
                "Moderate Intensity Cardio Flow",
            ],
        }

        regions = []
        for ex in main_exercises:
            name = ex.get("name", "").lower()
            if any(x in name for x in ['squat', 'lunge', 'leg', 'calf', 'deadlift']):
                regions.append("Lower")
            elif any(x in name for x in ['press', 'push', 'row', 'pull', 'chest', 'arm', 'shoulder']):
                regions.append("Upper")
            elif any(x in name for x in ['plank', 'core', 'crunch', 'abs', 'twist']):
                regions.append("Core")
            elif any(x in name for x in ['run', 'cardio', 'jump', 'burpee', 'hiit']):
                regions.append("Cardio")

        if not regions:
            return day_type

        dominant = Counter(regions).most_common(1)[0][0]
        unique_regions = set(regions)
        if len(unique_regions) > 2:
            dominant = "Full"

        titles = TITLE_MAP.get(dominant, TITLE_MAP["Full"])
        index = len(main_exercises) % len(titles)
        return titles[index]

    def _infer_missing_details(self, name: str, original_text: str) -> pd.Series:
        lower = name.lower()
        cat, region, equip, met = "Strength", "Full Body", "Bodyweight", 3.5

        if any(x in lower for x in ['run', 'jog', 'cycle', 'walk', 'swim', 'cardio']):
            cat, met = "Cardio", 7.0
        elif any(x in lower for x in ['stretch', 'yoga']):
            cat, met = "Flexibility", 2.5

        if any(x in lower for x in ['press', 'push', 'row', 'chest', 'arm']):
            region = "Upper"
        elif any(x in lower for x in ['squat', 'lunge', 'leg']):
            region = "Lower"

        if any(x in lower for x in ['dumbbell', 'barbell', 'weight']):
            equip = "Weights"

        steps = f"1. Prepare for {name}.\n2. Perform movement with control.\n3. Breathe rhythmically."
        return pd.Series({
            'Exercise Name': name,
            'Unique ID': 'N/A',
            'Primary Category': cat,
            'Body Region': region,
            'Equipments': equip,
            'MET value': met,
            'Health benefit': f"Improves {region} {cat}",
            'Safety cue': "Maintain good form.",
            'Steps to perform': steps,
            'Rest': '60s',
        })

    def format_exercise(self, row: pd.Series, sets, reps, rest, rpe, weight: float,
                        meta: Dict = None, is_new_user: bool = False) -> Dict:
        csv_rest = str(row.get('Rest', '')).strip()
        final_rest = csv_rest if (csv_rest and csv_rest.lower() not in ['nan', 'none', '']) else rest
        guid = str(row.get('guidid', '')).strip()
        prescription = _resolve_prescription_fields(row=row, fallback=reps)
        active_prescription = prescription.get("side") or prescription.get("time") or prescription.get("reps") or ""

        try:
            sets_int = int(re.search(r'\d+', str(sets)).group())
        except Exception:
            sets_int = 1

        total_sec = self._calculate_duration_seconds(sets, active_prescription, final_rest)
        if is_new_user and total_sec > 120:
            total_sec = 110

        m = total_sec // 60
        s = total_sec % 60
        m_suffix = "mins" if m > 1 else "min"
        s_suffix = "sec" if s == 1 else "secs"
        time_human = f"{m} {m_suffix} {s} {s_suffix}"
        met = float(row.get('MET value', 3.0))
        cals = self._calc_calories(met, weight, total_sec / 60.0)

        steps_raw = str(row.get('Steps to perform', ''))
        steps = [s.strip() for s in steps_raw.split('\n') if s.strip()] if steps_raw else []

        return {
            "name": row['Exercise Name'],
            "gui_id": guid if guid and guid.lower() != 'none' else None,
            "benefit": row.get('Health benefit', 'General Fitness'),
            "steps": steps,
            "sets": str(sets),
            "reps": prescription.get("reps"),
            "time": prescription.get("time"),
            "side": prescription.get("side"),
            "intensity_rpe": rpe,
            "rest": _clean_csv_text(final_rest),
            "equipment": row.get('Equipments', 'Bodyweight'),
            "est_calories": f"{int(cals)}",
            "safety_cue": row.get('Safety cue', 'None'),
            "met_value": met,
            "est_time_sec": total_sec,
            "est_time_human": time_human,
            "planned_sets_count": sets_int,
            "planned_total_cal": int(cals),
            "_meta": meta or {},
        }

    def _select_or_fail(self, pools: List[pd.DataFrame], used_set: Set[str],
                        error_msg: str) -> Tuple[pd.Series, bool]:
        for i, pool in enumerate(pools):
            ex = self.selector.select_unique(pool, used_set)
            if ex is not None:
                return ex, (i > 0)
        raise ValueError(f"Constraint Violation: {error_msg} - No suitable exercises found.")

    

def _stable_hash_int(val: str) -> int:
    return int(hashlib.md5(val.encode()).hexdigest(), 16)


_DAY_FOCUS_CYCLE = [
    "Upper Focus",
    "Lower Focus",
    "Cardio Focus",
    "Core Focus",
    "Full Body",
]


def _current_day_focus(clinical_context: Optional[Dict[str, Any]]) -> str:
    ctx = clinical_context or {}
    day_index = int(ctx.get("day_index", 0) or 0)
    return _DAY_FOCUS_CYCLE[day_index % len(_DAY_FOCUS_CYCLE)]


def _exercise_text_blob(row: pd.Series) -> str:
    parts = [
        str(row.get("Exercise Name", "")),
        str(row.get("Primary Category", "")),
        str(row.get("Body Region", "")),
        str(row.get("Tags", "")),
    ]
    return " ".join(parts).lower()


def _classify_exercise_categories(row: pd.Series) -> Set[str]:
    text = _exercise_text_blob(row)
    name = str(row.get("Exercise Name", "")).lower()
    body = str(row.get("Body Region", "")).lower()
    categories: Set[str] = set()
    upper_signal = bool(re.search(r"\b(press|push|dip|chest|shoulder|tricep|raise|row|pull|curl|face pull|rear delt|lat|bicep)\b", text))
    lower_signal = bool(re.search(r"\b(squat|lunge|hinge|deadlift|glute|hamstring|quad|calf|leg|step-up|sit-to-stand|hip|heel|toe|ankle)\b", text))
    core_signal = bool(re.search(r"\b(core|rotation|anti-rotation|stability|balance|oblique|ab)\b", text))
    cardio_signal = bool(
        "cardio" in text
        or "hiit" in text
        or "circuit" in text
        or re.search(r"\b(run|walk|march|jog|bike|cycle|rower|stepper|ski|boxing|rope|thruster|burpee|skater|jack|mountain climber|shuffle|tap|fast feet)\b", text)
    )

    if cardio_signal:
        categories.add("cardio")

    if "core" in body or core_signal or re.search(r"\b(plank|dead bug|bird dog|hollow)\b", text):
        categories.add("core")

    if "lower" in body or (lower_signal and not upper_signal):
        categories.add("lower_body")

    if ("upper" in body or "full" in body) and re.search(r"\b(press|push|dip|chest|shoulder|tricep|raise)\b", text):
        categories.add("upper_push")
    if ("upper" in body or "full" in body) and re.search(r"\b(row|pull|curl|face pull|rear delt|lat|bicep)\b", text):
        categories.add("upper_pull")

    if not categories:
        if "upper" in body:
            if re.search(r"\b(row|pull|curl)\b", name):
                categories.add("upper_pull")
            else:
                categories.add("upper_push")
        elif "lower" in body:
            categories.add("lower_body")
        elif "core" in body:
            categories.add("core")
        elif "full" in body:
            categories.update({"lower_body", "cardio"})

    return categories


def _classify_warmup_bucket(row: pd.Series) -> str:
    text = _exercise_text_blob(row)
    if re.search(r"\b(cardio|march|walk|step|jog|cycle|bike)\b", text):
        return "light_cardio"
    if re.search(r"\b(activation|band|glute|scap|brace|isometric|stability)\b", text):
        return "activation"
    return "mobility"


def _classify_cooldown_bucket(row: pd.Series) -> str:
    text = _exercise_text_blob(row)
    body = str(row.get("Body Region", "")).lower()
    if re.search(r"\b(breath|breathing|box breath|relax|recovery)\b", text):
        return "breathing"
    if "upper" in body:
        return "upper"
    if "lower" in body:
        return "lower"
    return "breathing"


def _focus_categories_for_day(day_focus: str) -> List[str]:
    mapping = {
        "Upper Focus": ["upper_push", "upper_pull", "core", "cardio", "lower_body"],
        "Lower Focus": ["lower_body", "core", "cardio", "upper_push", "upper_pull"],
        "Cardio Focus": ["cardio", "lower_body", "core", "upper_push", "upper_pull"],
        "Core Focus": ["core", "lower_body", "upper_pull", "upper_push", "cardio"],
        "Full Body": ["upper_push", "upper_pull", "lower_body", "core", "cardio"],
    }
    return mapping.get(day_focus, ["upper_push", "upper_pull", "lower_body", "core", "cardio"])


def _matches_day_focus(row: pd.Series, day_focus: str) -> bool:
    cats = _classify_exercise_categories(row)
    body_region = str(row.get("Body Region", "")).lower()
    text = _exercise_text_blob(row)
    if day_focus == "Upper Focus":
        return bool({"upper_push", "upper_pull"} & cats) or "upper" in body_region
    if day_focus == "Lower Focus":
        return "lower_body" in cats or "lower" in body_region
    if day_focus == "Cardio Focus":
        return "cardio" in cats or "cardio" in str(row.get("Primary Category", "")).lower()
    if day_focus == "Core Focus":
        return "core" in cats or "stability" in text
    if day_focus == "Full Body":
        return "full" in body_region or len(cats) >= 2
    return True


def _apply_day_based_main_variation(pool: pd.DataFrame, clinical_context: Optional[Dict[str, Any]]) -> pd.DataFrame:
    if pool is None or pool.empty:
        return pool

    day_focus = _current_day_focus(clinical_context)
    mask = pool.apply(lambda row: _matches_day_focus(row, day_focus), axis=1)
    varied_pool = pool[mask]
    return varied_pool if varied_pool is not None and not varied_pool.empty else pool


def _prepare_rotation_frame(
    pool: pd.DataFrame,
    week_counts: Dict[str, int],
    seen_today: Set[str],
    day_index: int,
    desired_categories: Optional[Set[str]] = None,
    preferred_focus: Optional[str] = None,
) -> pd.DataFrame:
    frame = pool.copy()
    seen_lower = {str(x).lower().strip() for x in seen_today}
    frame["_usage"] = frame["Exercise Name"].apply(
        lambda value: int(week_counts.get(str(value).lower().strip(), 0))
    )
    frame["_repeat_penalty"] = frame["Exercise Name"].apply(
        lambda value: 1 if str(value).lower().strip() in seen_lower else 0
    )
    if "_safety_penalty" not in frame.columns:
        frame["_safety_penalty"] = 0
    frame["_safety_penalty"] = pd.to_numeric(frame["_safety_penalty"], errors="coerce").fillna(0).astype(int)
    desired_categories = set(desired_categories or set())
    preferred_focus = str(preferred_focus or "").strip()
    frame["_category_penalty"] = frame.apply(
        lambda row: 0 if not desired_categories or (_classify_exercise_categories(row) & desired_categories) else 1,
        axis=1,
    )
    frame["_focus_penalty"] = frame.apply(
        lambda row: 0 if not preferred_focus or _matches_day_focus(row, preferred_focus) else 1,
        axis=1,
    )
    frame["_random_factor"] = [random.randint(0, 14) for _ in range(len(frame))]
    frame["_score"] = (
        frame["_usage"] * 10
        + frame["_repeat_penalty"] * 15
        + frame["_safety_penalty"] * 100
        + (frame["_category_penalty"] + frame["_focus_penalty"]) * 5
        + frame["_random_factor"]
    )
    return frame


def _sample_rotation_candidates(frame: pd.DataFrame) -> Optional[pd.Series]:
    if frame is None or frame.empty:
        return None

    # Use a small epsilon-style transform via +1 denominator, and handle NaNs safely.
    score_series = pd.to_numeric(frame["_score"], errors="coerce").fillna(0).clip(lower=0)
    weights = 1.0 / (1.0 + score_series)

    # CRITICAL: with replace=False, sample size must be <= number of non-zero weights.
    non_zero_count = int((weights > 0).sum())
    candidate_count = min(15, len(frame), non_zero_count)
    if candidate_count == 0:
        return frame.sample(1).iloc[0]

    try:
        candidates = frame.sample(
            n=candidate_count,
            weights=weights,
            replace=False,
        )
    except ValueError:
        # Final fallback: take any valid row without weighted sampling.
        return frame.sample(1).iloc[0]

    if candidates.empty:
        return None
    return candidates.sample(1).iloc[0]


def _select_rotated_dataset_row(
    self,
    pool: pd.DataFrame,
    week_counts: Dict[str, int],
    seen_today: Set[str],
    clinical_context: Optional[Dict[str, Any]] = None,
    slot: str = "main",
    desired_categories: Optional[Set[str]] = None,
    allow_category_relaxation: bool = True,
    allow_usage_relaxation: bool = True,
    allow_repeat_relaxation: bool = True,
) -> Optional[pd.Series]:
    if pool is None or pool.empty:
        return None

    ctx = clinical_context or self._active_clinical_context or {}
    day_index = int(ctx.get("day_index", 0) or 0)
    day_focus = _current_day_focus(ctx)
    base_pool = pool.copy()
    if slot == "main":
        base_pool = _apply_day_based_main_variation(base_pool, ctx)

    safe_pool = self._apply_medical_guardrails(base_pool, ctx, slot)
    if safe_pool is None or safe_pool.empty:
        return None
    if "_safety_penalty" in safe_pool.columns:
        safe_pool = safe_pool[safe_pool["_safety_penalty"] == 0]
    if safe_pool is None or safe_pool.empty:
        return None

    prepared = _prepare_rotation_frame(
        safe_pool,
        week_counts,
        seen_today,
        day_index,
        desired_categories=desired_categories,
        preferred_focus=day_focus if slot == "main" else "",
    )
    relaxation_stages = [
        prepared[
            (prepared["_usage"] == 0)
            & (prepared["_repeat_penalty"] == 0)
            & (prepared["_category_penalty"] == 0)
            & (prepared["_safety_penalty"] == 0)
        ],
        prepared[
            ((prepared["_usage"] <= 1) if allow_usage_relaxation else (prepared["_usage"] == 0))
            & (prepared["_repeat_penalty"] == 0)
            & (prepared["_category_penalty"] == 0)
            & (prepared["_safety_penalty"] == 0)
        ],
        prepared[
            ((prepared["_usage"] <= 2) if allow_repeat_relaxation else (prepared["_usage"] <= 1))
            & ((prepared["_repeat_penalty"] <= 1) if allow_repeat_relaxation else (prepared["_repeat_penalty"] == 0))
            & (prepared["_category_penalty"] == 0)
            & (prepared["_safety_penalty"] == 0)
        ],
        prepared[
            ((prepared["_usage"] <= 2) if allow_repeat_relaxation else True)
            & ((prepared["_category_penalty"] <= 1) if allow_category_relaxation else (prepared["_category_penalty"] == 0))
            & (prepared["_safety_penalty"] == 0)
        ],
        prepared[prepared["_safety_penalty"] == 0],
    ]

    for stage in relaxation_stages:
        if stage is None or stage.empty:
            continue
        selected = _sample_rotation_candidates(stage)
        if selected is not None:
            return selected

    fallback_prepared = _prepare_rotation_frame(
        safe_pool,
        week_counts,
        seen_today,
        day_index,
        desired_categories=None if allow_category_relaxation else desired_categories,
        preferred_focus="",
    )
    selected = _sample_rotation_candidates(fallback_prepared)
    if selected is not None:
        return selected

    return None


def _workoutcomposer_init(self, selector: ExerciseSelector):
    self.selector = selector
    self._dataset = FitnessDataset._empty_frame()
    self._active_clinical_context = {}


def _workoutcomposer_apply_medical_guardrails(
    self,
    df: pd.DataFrame,
    context: Optional[Dict[str, Any]],
    slot_type: str = "main",
) -> pd.DataFrame:
    return _hard_medical_exclusion(df, context, slot_type)


def _workoutcomposer_get_rotated_exercise(
    self,
    pool: pd.DataFrame,
    week_counts: Dict[str, int],
    seen_today: Set[str],
    clinical_context: Optional[Dict[str, Any]] = None,
    slot: str = "main",
    desired_categories: Optional[Set[str]] = None,
    allow_category_relaxation: bool = True,
    allow_usage_relaxation: bool = True,
    allow_repeat_relaxation: bool = True,
) -> Optional[pd.Series]:
    return _select_rotated_dataset_row(
        self,
        pool,
        week_counts,
        seen_today,
        clinical_context,
        slot,
        desired_categories=desired_categories,
        allow_category_relaxation=allow_category_relaxation,
        allow_usage_relaxation=allow_usage_relaxation,
        allow_repeat_relaxation=allow_repeat_relaxation,
    )


def _build_composer_day_shell(day_name: str, day_type: str, params: Dict, is_minimal_plan: bool) -> Dict:
    return {
        "day_name": day_name,
        "workout_title": f"{day_type}",
        "difficulty_level": params.get("fitness_level", "Beginner"),
        "warmup_duration": "None" if is_minimal_plan else "5-7 mins",
        "main_workout_category": day_type,
        "cooldown_duration": "None" if is_minimal_plan else "5 mins",
        "warmup": [],
        "main_workout": [],
        "cooldown": [],
        "safety_notes": ["Stay hydrated", "Monitor RPE"],
    }


def _finalize_day_metrics(plan: Dict[str, Any]) -> Dict[str, Any]:
    total_day_sec = 0
    total_day_calories = 0
    for section in ["warmup", "main_workout", "cooldown"]:
        for ex in plan.get(section, []) or []:
            total_day_sec += _to_int(ex.get("est_time_sec"), 0)
            total_day_calories += _to_int(ex.get("planned_total_cal"), 0)
    plan["est_total_calories"] = f"{total_day_calories} est. cal"
    mins = round(total_day_sec / 60)
    suffix = "mins" if mins > 1 else "min"
    plan["est_total_duration"] = f"~{mins} {suffix}"
    return plan


def _workoutcomposer_build_day(
    self,
    day_name: str,
    day_type: str,
    df_base: pd.DataFrame,
    df_tagged: Dict[str, pd.DataFrame],
    params: Dict,
    global_main_used: Set[str],
    global_warmup_used: Set[str] = None,
    global_cooldown_used: Set[str] = None,
    mandatory_exercises: List[Dict] = None,
    is_minimal_plan: bool = False,
    structured_mode: bool = False,
) -> Dict:
    weight = params['weight']
    weekly_usage = params.get("weekly_usage")
    if weekly_usage is None:
        weekly_usage = Counter()
    shadow_boxing_used = bool(params.get("shadow_boxing_used"))

    plan = _build_composer_day_shell(day_name, day_type, params, is_minimal_plan)

    day_warmup_used: Set[str] = global_warmup_used if global_warmup_used is not None else set()
    day_cooldown_used: Set[str] = global_cooldown_used if global_cooldown_used is not None else set()
    target_main = max(1, int(params.get("max_main") or 5))
    target_cooldown = 0 if is_minimal_plan else max(1, min(2, int(params.get("max_cooldown") or 2)))
    target_warmup = 0 if is_minimal_plan else max(1, min(3, int(params.get("max_warmup") or 3)))
    day_focus = _current_day_focus(self._active_clinical_context)

    def _record_weekly_use(name: Any) -> None:
        nonlocal shadow_boxing_used
        key = str(name or "").strip().lower()
        if not key:
            return
        weekly_usage[key] += 1
        if "shadow boxing" in key:
            shadow_boxing_used = True

    def _claim_usage(name: Any, seen_set: Set[str]) -> None:
        text = str(name or "").strip()
        if not text:
            return
        seen_set.add(text)
        _record_weekly_use(text)

    def _is_warmup_safe(name: str) -> bool:
        return not bool(re.search(
            r"burpee|thruster|jump(?!\s*rope)|sprint|plyom|heavy\s+deadlift|max\s+effort|1\s*rm|one\s+rep\s+max",
            str(name or "").lower(),
            flags=re.I,
        ))

    def _is_dup_name(name: str) -> bool:
        n = str(name or "").strip().lower()
        if not n:
            return True

        if shadow_boxing_used and "shadow boxing" in n:
            return True

        existing_names = []
        for sec in ["warmup", "main_workout", "cooldown"]:
            existing_names.extend(str(ex.get("name", "")).lower() for ex in plan.get(sec, []))

        existing_names.extend(str(x).lower() for x in global_main_used)
        existing_names.extend(str(x).lower() for x in day_warmup_used)
        existing_names.extend(str(x).lower() for x in day_cooldown_used)

        return any(difflib.SequenceMatcher(None, n, en).ratio() > 0.85 for en in existing_names)

    def _slot_seen(slot_name: str) -> Set[str]:
        if slot_name == "warmup":
            return day_warmup_used
        if slot_name == "cooldown":
            return day_cooldown_used
        return global_main_used

    def _slot_type(slot_name: str) -> str:
        return "main" if slot_name == "main_workout" else slot_name

    def _append_entry(slot_name: str, row: pd.Series, sets: Any, reps: Any, rest: Any, rpe: Any, meta: Dict[str, Any]) -> bool:
        row = _guard_single_row(row, slot_name)
        if row is None:
            return False
        guid = str(row.get("guidid", "")).strip()
        if not guid or guid.lower() == "none":
            return False
        exercise_name = str(row.get("Exercise Name", "")).strip()
        seen_set = _slot_seen(slot_name)
        if exercise_name.lower() in {x.lower() for x in seen_set}:
            return False
        if not exercise_name or _is_dup_name(exercise_name):
            return False
        plan[slot_name].append(self.format_exercise(row, sets, reps, rest, rpe, weight, meta, is_new_user=is_minimal_plan))
        _claim_usage(exercise_name, seen_set)
        return True

    def _guard_single_row(row: pd.Series, slot_name: str) -> Optional[pd.Series]:
        guarded = self._apply_medical_guardrails(
            pd.DataFrame([row.to_dict()]),
            self._active_clinical_context,
            _slot_type(slot_name),
        )
        if guarded is None or guarded.empty:
            return None
        return guarded.iloc[0]

    def resolve_mandatory_row(p_ex: Dict) -> Tuple[pd.Series, Dict[str, Any]]:
        name = str(p_ex.get("name") or "").strip()
        if not name:
            return (self._infer_missing_details("Prescribed Exercise", ""), {"source": "infer_missing", "confidence": 0.35})

        exact = df_base[df_base['Exercise Name'].str.lower() == name.lower()]
        if not exact.empty:
            return exact.iloc[0], {"source": "dataset_exact", "confidence": 1.0}

        best_idx, best_score = None, 0.0
        for idx, row_cand in df_base.iterrows():
            score = difflib.SequenceMatcher(None, name.lower(), str(row_cand.get("Exercise Name", "")).lower()).ratio()
            if score > best_score:
                best_idx, best_score = idx, score
        if best_idx is not None and best_score >= 0.60:
            return df_base.loc[best_idx], {"source": "dataset_fuzzy", "confidence": round(best_score, 2)}

        full_ds = getattr(self, "_dataset", df_base)
        best_idx2, best_score2 = None, 0.0
        for idx, row_cand in full_ds.iterrows():
            score = difflib.SequenceMatcher(None, name.lower(), str(row_cand.get("Exercise Name", "")).lower()).ratio()
            if score > best_score2:
                best_idx2, best_score2 = idx, score
        if best_idx2 is not None and best_score2 >= 0.60:
            return full_ds.loc[best_idx2], {"source": "dataset_fuzzy_full", "confidence": round(best_score2, 2)}

        # ── No hard-coded name substitution: use dataset-driven rotated selection ──
        cat_hint = str(p_ex.get("category", "")).lower()
        if "warm" in cat_hint:
            _fb_pool = df_tagged.get("warmup", pd.DataFrame()) if isinstance(df_tagged, dict) else pd.DataFrame()
            _fb_slot = "warmup"
            _fb_seen = day_warmup_used
        elif "cool" in cat_hint:
            _fb_pool = df_tagged.get("cooldown", pd.DataFrame()) if isinstance(df_tagged, dict) else pd.DataFrame()
            _fb_slot = "cooldown"
            _fb_seen = day_cooldown_used
        else:
            _fb_pool = df_base
            _fb_slot = "main"
            _fb_seen = global_main_used

        _safe_fb = self._apply_medical_guardrails(
            _fb_pool if (_fb_pool is not None and not _fb_pool.empty) else df_base,
            self._active_clinical_context,
            _fb_slot,
        )
        rotated = self._get_rotated_exercise(
            _safe_fb if (_safe_fb is not None and not _safe_fb.empty) else df_base,
            weekly_usage,
            _fb_seen,
            self._active_clinical_context,
            _fb_slot,
        )
        if rotated is not None:
            return rotated, {"source": "dataset_rotated_fallback", "confidence": 0.55}

        return (self._infer_missing_details(name, p_ex.get('original_text', '')), {"source": "doctor_note_fallback", "confidence": 0.45})

    warmup_pool = df_tagged["warmup"] if not df_tagged["warmup"].empty else pd.DataFrame()
    if not warmup_pool.empty:
        warmup_pool = warmup_pool[
            warmup_pool["Tags"].str.contains(r"warm|mobility|dynamic|activation|light", case=False, na=False)
        ]
        warmup_pool = warmup_pool[warmup_pool["Exercise Name"].apply(_is_warmup_safe)]
    warmup_pool = self._apply_medical_guardrails(warmup_pool, self._active_clinical_context, "warmup")

    cooldown_pool = df_tagged["cooldown"] if not df_tagged["cooldown"].empty else pd.DataFrame()
    if not cooldown_pool.empty:
        cooldown_pool = cooldown_pool[
            (pd.to_numeric(cooldown_pool["MET value"], errors="coerce").fillna(2.5) <= 4.0)
            & (~cooldown_pool["Tags"].str.contains("HIIT|Explosive|Agility", case=False, na=False))
        ]
    cooldown_pool = self._apply_medical_guardrails(cooldown_pool, self._active_clinical_context, "cooldown")

    main_mask = (
        df_base["Tags"].str.contains(r"main\s*work", case=False, na=False, regex=True)
        | df_base["Primary Category"].str.contains("Strength|Cardio|HIIT|Balance|Stability", case=False, na=False)
    )
    main_df = df_base[main_mask] if not df_base[main_mask].empty else df_base
    main_df = self._apply_medical_guardrails(main_df, self._active_clinical_context, "main")
    if main_df is None or main_df.empty:
        main_df = self._apply_medical_guardrails(df_base, self._active_clinical_context, "main")
    if main_df is None or main_df.empty:
        main_df = FitnessDataset._empty_frame()

    cardio_only_pool = main_df[main_df.apply(lambda row: "cardio" in _classify_exercise_categories(row), axis=1)] if not main_df.empty else main_df
    focus_pool = _apply_day_based_main_variation(main_df, self._active_clinical_context) if not main_df.empty else main_df
    category_sequence = _focus_categories_for_day(day_focus)
    required_categories = ["upper_push", "upper_pull", "lower_body", "core", "cardio"]
    selected_main_categories: List[str] = []

    def _guarded_base_pool(slot_type: str) -> pd.DataFrame:
        guarded_base = self._apply_medical_guardrails(df_base, self._active_clinical_context, slot_type)
        return guarded_base if guarded_base is not None else FitnessDataset._empty_frame()

    def _lower_body_fallback_pool(pool: pd.DataFrame) -> pd.DataFrame:
        if pool is None or pool.empty:
            return FitnessDataset._empty_frame()
        seated_pool = pool[
            pool.apply(
                lambda row: bool(
                    (
                        "lower_body" in _classify_exercise_categories(row)
                        or "lower" in str(row.get("Body Region", "")).lower()
                        or re.search(r"\b(leg|calf|quad|hamstring|glute|hip|ankle|heel|toe|march|step)\b", _exercise_text_blob(row))
                    )
                    and re.search(r"\b(seated|chair|supported|sit[\s-]?to[\s-]?stand|march|heel|toe|ankle|calf|extension|curl|tap|leg raise|step)\b", _exercise_text_blob(row))
                ),
                axis=1,
            )
        ]
        return seated_pool if seated_pool is not None and not seated_pool.empty else FitnessDataset._empty_frame()

    def _pick_pool(slot_name: str, category: Optional[str] = None, prefer_focus: bool = False) -> pd.DataFrame:
        if slot_name == "warmup":
            return warmup_pool if warmup_pool is not None else FitnessDataset._empty_frame()
        if slot_name == "cooldown":
            return cooldown_pool if cooldown_pool is not None else FitnessDataset._empty_frame()
        pool = cardio_only_pool if category == "cardio" and cardio_only_pool is not None and not cardio_only_pool.empty else main_df
        if prefer_focus and focus_pool is not None and not focus_pool.empty:
            pool = focus_pool
        if category:
            cat_pool = pool[pool.apply(lambda row: category in _classify_exercise_categories(row), axis=1)]
            if not cat_pool.empty:
                return cat_pool
            if category == "lower_body":
                fallback_lower = _lower_body_fallback_pool(pool)
                if not fallback_lower.empty:
                    return fallback_lower
        return pool if pool is not None else FitnessDataset._empty_frame()

    def _safe_get_rotated(slot_name: str, pool: pd.DataFrame, seen_set: Set[str], desired_categories: Optional[Set[str]] = None) -> Optional[pd.Series]:
        safe_pool = self._apply_medical_guardrails(pool, self._active_clinical_context, _slot_type(slot_name))
        if safe_pool is None or safe_pool.empty:
            return None
        if "_safety_penalty" in safe_pool.columns:
            safe_pool = safe_pool[safe_pool["_safety_penalty"] == 0]
        if safe_pool is None or safe_pool.empty:
            return None
        pool_size = len(safe_pool)
        return self._get_rotated_exercise(
            safe_pool,
            weekly_usage,
            seen_set,
            self._active_clinical_context,
            _slot_type(slot_name),
            desired_categories=desired_categories,
            allow_usage_relaxation=pool_size < 15,
            allow_repeat_relaxation=pool_size < 15,
            allow_category_relaxation=pool_size < 15,
        )

    def _fill_slot_from_bucket(slot_name: str, bucket_name: str, classify_fn, sets: str, reps: str, rest: str, rpe: str, meta_prefix: str) -> bool:
        base_pool = _pick_pool(slot_name)
        if base_pool is None or base_pool.empty:
            base_pool = _guarded_base_pool(_slot_type(slot_name))
        bucket_pool = base_pool[base_pool.apply(lambda row: classify_fn(row) == bucket_name, axis=1)] if base_pool is not None and not base_pool.empty else base_pool
        for candidate_pool in [bucket_pool, base_pool, _guarded_base_pool(_slot_type(slot_name))]:
            ex = _safe_get_rotated(slot_name, candidate_pool, _slot_seen(slot_name))
            if ex is None:
                continue
            if slot_name == "cooldown":
                ex = ex.copy()
                ex["MET value"] = min(float(ex.get("MET value", 2.5)), 2.5)
            if _append_entry(slot_name, ex, sets, reps, rest, rpe, {"slot": f"{meta_prefix}_{bucket_name}"}):
                return True
        return False

    if mandatory_exercises:
        for p_ex in mandatory_exercises:
            row, inject_meta = resolve_mandatory_row(p_ex)
            tags_check = str(row.get("Tags", "")).lower()
            cat_check = str(p_ex.get("category", "")).lower()
            if re.search(r"warmup|warm.?up|mobility", tags_check, re.I):
                target_slot = "warmup"
            elif re.search(r"cooldown|cool.?down|stretch", tags_check, re.I):
                target_slot = "cooldown"
            elif re.search(r"main|strength|cardio|hiit", tags_check, re.I):
                target_slot = "main_workout"
            else:
                target_slot = "warmup" if "warm" in cat_check else "cooldown" if "cool" in cat_check else "main_workout"

            if is_minimal_plan and target_slot in ("warmup", "cooldown"):
                continue
            if target_slot == "main_workout" and len(plan["main_workout"]) >= target_main:
                continue
            if target_slot == "warmup" and len(plan["warmup"]) >= target_warmup:
                continue
            if target_slot == "cooldown" and len(plan["cooldown"]) >= target_cooldown:
                continue

            safe_row = _guard_single_row(row, target_slot)
            if safe_row is None:
                desired = set(_classify_exercise_categories(row)) if target_slot == "main_workout" else None
                safe_row = _safe_get_rotated(
                    target_slot,
                    _pick_pool(target_slot, next(iter(desired)) if desired else None, prefer_focus=True),
                    _slot_seen(target_slot),
                    desired,
                )
            if safe_row is None:
                continue

            prescribed_meta = {"slot": "prescribed"}
            if isinstance(p_ex.get("_meta"), dict):
                prescribed_meta.update(p_ex["_meta"])
            prescribed_meta.update(inject_meta)

            if _append_entry(
                target_slot,
                safe_row,
                p_ex.get("sets") or params["sets"],
                p_ex.get("reps") or params["reps"],
                params["rest"],
                params["rpe"],
                prescribed_meta,
            ):
                if target_slot == "main_workout":
                    selected_main_categories.extend(sorted(_classify_exercise_categories(safe_row)))
                if p_ex.get("equipment"):
                    plan[target_slot][-1]["equipment"] = p_ex["equipment"]

    if is_minimal_plan:
        plan["warmup"] = []
        plan["cooldown"] = []
    else:
        for bucket in ["light_cardio", "mobility", "activation"]:
            _fill_slot_from_bucket(
                "warmup",
                bucket,
                _classify_warmup_bucket,
                "1",
                "1-2 minutes" if bucket == "light_cardio" else "10-15",
                "None",
                "2-3",
                "warmup_mix",
            )
        plan["warmup"] = plan["warmup"][:target_warmup]

        for bucket in ["upper", "lower", "breathing"]:
            _fill_slot_from_bucket(
                "cooldown",
                bucket,
                _classify_cooldown_bucket,
                "1",
                "Hold 30s",
                "None",
                "1-2",
                "cooldown_mix",
            )
        plan["cooldown"] = plan["cooldown"][:target_cooldown]

    def _append_main_for_category(category: str, slot_label: str, prefer_focus: bool = True) -> bool:
        for candidate_pool in [
            _pick_pool("main_workout", category, prefer_focus=prefer_focus),
            _pick_pool("main_workout", category, prefer_focus=False),
            _guarded_base_pool("main"),
        ]:
            ex = _safe_get_rotated("main_workout", candidate_pool, global_main_used, {category})
            if ex is None:
                continue
            if _append_entry("main_workout", ex, params["sets"], params["reps"], params["rest"], params["rpe"], {"slot": slot_label, "bucket": category}):
                matched = [cat for cat in required_categories if cat in _classify_exercise_categories(ex)]
                selected_main_categories.extend(matched or [category])
                return True
        return False

    if day_focus == "Cardio Focus":
        required_categories = ["cardio", "cardio", "cardio", "core", "lower_body"]
    else:
        required_categories = _focus_categories_for_day(day_focus)

    selection_plan = list(required_categories)
    while len(selection_plan) < target_main:
        selection_plan.extend(_focus_categories_for_day(day_focus))
    selection_plan = selection_plan[:target_main]

    for category in selection_plan:
        if len(plan["main_workout"]) >= target_main:
            break
        if category != "cardio" and category in selected_main_categories:
            continue
        if _append_main_for_category(category, f"main_required_{category}", prefer_focus=True):
            selected_main_categories.append(category)
            continue
        if category == "lower_body" and _append_main_for_category("lower_body", "main_lower_fallback", prefer_focus=False):
            selected_main_categories.append("lower_body")

    for category in required_categories:
        if len(plan["main_workout"]) >= target_main:
            break
        if category != "cardio" and category in selected_main_categories:
            continue
        if _append_main_for_category(category, f"main_completion_{category}", prefer_focus=False):
            selected_main_categories.append(category)

    filler_attempts = 0
    while len(plan["main_workout"]) < target_main and filler_attempts < 20:
        filler_attempts += 1
        fallback_category = selection_plan[len(plan["main_workout"]) % len(selection_plan)] if selection_plan else "cardio"
        ex = _safe_get_rotated(
            "main_workout",
            _pick_pool("main_workout", fallback_category, prefer_focus=False),
            global_main_used,
            {fallback_category},
        )
        if ex is None:
            ex = _safe_get_rotated(
                "main_workout",
                _guarded_base_pool("main"),
                global_main_used,
            )
        if ex is not None:
            success = _append_entry("main_workout", ex, params["sets"], params["reps"], params["rest"], params["rpe"], {"slot": "main_filler"})
            if not success:
                ex_name = str(ex.get("Exercise Name", "")).strip()
                if ex_name:
                    global_main_used.add(ex_name)
        else:
            break


    plan['workout_title'] = self._generate_dynamic_title(day_type, plan['main_workout'])
    plan["main_workout"] = plan["main_workout"][:target_main]
    plan["warmup"] = [] if is_minimal_plan else plan["warmup"][:target_warmup]
    plan["cooldown"] = [] if is_minimal_plan else plan["cooldown"][:target_cooldown]
    return _finalize_day_metrics(plan)


WorkoutComposer.__init__ = _workoutcomposer_init
WorkoutComposer._apply_medical_guardrails = _workoutcomposer_apply_medical_guardrails
WorkoutComposer._get_rotated_exercise = _workoutcomposer_get_rotated_exercise
WorkoutComposer.build_day = _workoutcomposer_build_day

# ============ LAYER 6: ORCHESTRATOR ============

# 🔥 STEP 1: NORMALIZER FUNCTION - Ensures prescription & generator output match
def _normalize_plan_json(plans_json: Dict) -> Dict:
    """Normalize all plans to match FitnessPlanGeneratorTool schema."""
    REQUIRED_DAY_KEYS = [
        "day_name",
        "workout_title",
        "difficulty_level",
        "warmup_duration",
        "main_workout_category",
        "cooldown_duration",
        "warmup",
        "main_workout",
        "cooldown",
        "safety_notes",
        "est_total_calories",
        "est_total_duration"
    ]

    REQUIRED_EX_KEYS = [
        "name",
        "gui_id",
        "benefit",
        "steps",
        "sets",
        "reps",
        "time",
        "side",
        "intensity_rpe",
        "rest",
        "equipment",
        "est_calories",
        "safety_cue",
        "met_value",
        "est_time_sec",
        "est_time_human",
        "planned_sets_count",
        "planned_total_cal",
        "_meta"
    ]

    normalized = {}

    for day, data in (plans_json or {}).items():
        if data is None:
            data = {}
        day_data = {}

        # 🔥 Ensure all required day keys exist
        for key in REQUIRED_DAY_KEYS:
            if key in ["warmup", "main_workout", "cooldown"]:
                day_data[key] = data.get(key, [])
            else:
                day_data[key] = data.get(key, "None")

        # 🔥 Ensure all exercises have required keys
        for section in ["warmup", "main_workout", "cooldown"]:
            fixed_list = []
            for ex in (day_data[section] or []):
                if ex is None:
                    continue
                fixed_ex = {}
                for k in REQUIRED_EX_KEYS:
                    fixed_ex[k] = ex.get(k, None)
                fixed_list.append(fixed_ex)
            day_data[section] = fixed_list

        normalized[day] = day_data

    return normalized


class FitnessPlanGeneratorTool:
    def __init__(self, *args, **kwargs):
        pass

    async def safe_execute(self, **kwargs) -> ToolResult:
        try:
            return await self.execute(**kwargs)
        except Exception as e:
            logger.error(f"FitnessPlanGeneratorTool.safe_execute failed: {e}", exc_info=True)
            return ToolResult(
                False,
                data={"answer": "I hit a temporary technical issue while generating the fitness plan. Please try again."},
                error=str(e),
                metadata={"error_handled": True, "error_type": "SAFE_EXECUTE_ERROR", "tool_name": "fitness_plan_generator"},
            )

    async def execute(self, query: str = "", constraints: dict = None, **kwargs) -> ToolResult:
        try:
            profile = kwargs.get('user_profile') or kwargs.get('profile') or constraints
            if profile and isinstance(profile, dict) and 'fitness_profile_data' in profile:
                profile = profile['fitness_profile_data']

            if not profile:
                return ToolResult(False, error="No user profile provided.")

            # Safety Guardrail Stop
            matched_condition = ExerciseClassifier.is_safety_restricted(
                fitness_medical_conditions=profile.get('fitness_medical_conditions') or [],
                physical_limitations=str(profile.get('physical_limitation') or profile.get('physical_limitations') or "None")
            )
            if matched_condition:
                logger.info(f"[FitnessPlanGenerator] Safety restriction: {matched_condition}. Aborting generation.")
                return ToolResult(
                    True,
                    data={
                        "answer": f"I cannot generate a workout plan because your profile indicates a safety restriction: {matched_condition}. Please consult your doctor before starting any new exercise program.",
                        "plans_json": {}
                    },
                    metadata={
                        "is_safety_restricted": True,
                        "matched_condition": matched_condition,
                        "safety_stop": True
                    }
                )

            # Normalize training days: prioritize DaysOfTrain, handle shorthand mapping
            day_map = {
                "MON": "Monday", "TUE": "Tuesday", "WED": "Wednesday",
                "THU": "Thursday", "FRI": "Friday", "SAT": "Saturday", "SUN": "Sunday"
            }
            raw_days = profile.get('DaysOfTrain') or profile.get('days_per_week')
            
            if not raw_days:
                days_list = ["Monday", "Wednesday", "Friday"]
            elif isinstance(raw_days, str):
                parts = [p.strip().upper() for p in raw_days.split(',') if p.strip()]
                days_list = [day_map.get(p, p.capitalize()) for p in parts]
            elif isinstance(raw_days, list):
                days_list = [day_map.get(str(d).strip().upper(), str(d).capitalize()) for d in raw_days]
            else:
                days_list = ["Monday", "Wednesday", "Friday"]

            days_list = [d for d in days_list if d and str(d).lower() not in _NULLISH_TEXT]
            profile['days_per_week'] = days_list if days_list else ["Monday", "Wednesday", "Friday"]

            df = FitnessDataset.load()
            if df.empty:
                return ToolResult(False, error="Dataset load failed.")

            session_dur = profile.get('session_duration', '')
            session_raw = '' if session_dur is None else str(session_dur).strip()
            has_duration_number = bool(re.search(r'\d+', session_raw))
            is_minimal_plan = (not session_raw) or (session_raw.lower() in ['none', 'nan', 'null']) or (not has_duration_number)
            if has_duration_number:
                num_match = re.search(r'\d+', session_raw)
                if num_match:
                    try:
                        minutes = int(num_match.group(0))
                        if minutes <= 10:
                            is_minimal_plan = True
                    except ValueError:
                        pass

            filtered_df = ExerciseFilter.apply_filters(df, profile)
            if filtered_df.empty:
                filtered_df = df.copy()

            planner = WorkoutPlanner()
            sets, reps, rpe, rest = planner.get_volume_intensity(
                profile.get('primary_goal', ''), profile.get('fitness_level', '')
            )
            count = planner.get_exercise_count(session_raw)
            generation_mode = str(profile.get("generation_mode") or profile.get("input_mode") or "intake").lower()
            note_mode = generation_mode in {"expert_note", "soap_note"} or bool(profile.get("note_mode"))
            note_blocks_by_day = profile.get("note_activity_blocks") if isinstance(profile.get("note_activity_blocks"), dict) else {}

            params = {
                "sets": sets,
                "reps": reps,
                "rpe": rpe,
                "rest": rest,
                "max_main": count,  # Dynamic limit based on session_duration
                "weight": profile.get('weight_kg', 70),
                "fitness_level": profile.get("fitness_level", "Beginner"),
                "_profile": profile,
            }

            # In FitnessPlanGeneratorTool.execute
            # Updated Regex Logic
            # Replace your current tagged_pools block with this:
# Updated Regex Logic to handle "Warm up, Main workout"
            tagged_pools = {
                "warmup": filtered_df[filtered_df['Tags'].str.contains(
                    r"warm|mobility|dynamic|activation",
                    case=False,
                    na=False
                )],
                "cooldown": filtered_df[filtered_df['Tags'].str.contains(r'cool\s*down|stretch', case=False, na=False, regex=True)]
            }

            selector = ExerciseSelector(random_seed=kwargs.get('seed', None))
            composer = WorkoutComposer(selector)
            composer._dataset = df
            clinical_terms = []
            for key in ("physical_limitations", "physical_limitation", "specific_avoidance", "avoid_terms", "restrictions"):
                clinical_terms.extend(_to_list(profile.get(key)))
            clinical_context = {
                "flags": profile.get("flags") if isinstance(profile.get("flags"), dict) else {},
                "conditions": profile.get("fitness_medical_conditions") or [],
                "fitness_medical_conditions": profile.get("fitness_medical_conditions") or [],
                "limitations": clinical_terms,
                "physical_limitation": profile.get("physical_limitation") or ", ".join(clinical_terms),
                "physical_limitations": profile.get("physical_limitations") or clinical_terms,
            }
            composer._active_clinical_context = clinical_context

            plans_json = {}
            global_main_used = set()
            global_warmup_used = set()
            global_cooldown_used = set()
            self._weekly_usage = getattr(self, "_weekly_usage", Counter())
            weekly_usage = self._weekly_usage
            shadow_boxing_used = False
            parsed_output = kwargs.get("parsed_output") if isinstance(kwargs.get("parsed_output"), dict) else {}
            parsed_mode = str(
                parsed_output.get("mode") or parsed_output.get("note_type") or ""
            ).lower()

            days = profile.get("days_per_week", ["Monday", "Wednesday", "Friday"])
            if note_mode:
                plans_json = _build_note_week(profile, kwargs.get("raw_text", ""), seed=kwargs.get("seed", None))
            else:
                split = planner.get_weekly_split(profile.get("primary_goal", ""), len(days))

            for i, day in enumerate([] if note_mode else days):
                if note_mode:
                    plans_json[day], shadow_boxing_used = _build_note_day(
                        day=day,
                        day_index=i,
                        activity_blocks=note_blocks_by_day.get(day, []),
                        profile=profile,
                        composer=composer,
                        filtered_df=filtered_df,
                        tagged_pools=tagged_pools,
                        base_params=params,
                        global_main_used=global_main_used,
                        global_warmup_used=global_warmup_used,
                        global_cooldown_used=global_cooldown_used,
                        weekly_usage=weekly_usage,
                        shadow_boxing_used=shadow_boxing_used,
                        session_raw=session_raw,
                    )
                    params["shadow_boxing_used"] = shadow_boxing_used
                    continue

                day_type = split[i]
                day_params = dict(params)
                day_params["weekly_usage"] = weekly_usage
                day_params["shadow_boxing_used"] = shadow_boxing_used
                composer._active_clinical_context["day_index"] = i

                plans_json[day] = composer.build_day(
                    day,
                    day_type,
                    filtered_df,
                    tagged_pools,
                    day_params,
                    global_main_used,
                    global_warmup_used=global_warmup_used,
                    global_cooldown_used=global_cooldown_used,
                    is_minimal_plan=is_minimal_plan, # 🔥 Dynamic use of the calculated minimal plan flag
                )
                
                for section in ("warmup", "main_workout", "cooldown"):
                    for ex in plans_json[day].get(section, []) or []:
                        name = str(ex.get("name") or "").strip().lower()
                        if not name:
                            continue
                        if "shadow boxing" in name:
                            shadow_boxing_used = True
                params["shadow_boxing_used"] = shadow_boxing_used

            self._weekly_usage = weekly_usage
            
            # 🔥 STEP 2: NORMALIZE before return - ensures prescription matches generator schema
            plans_json = _validate_strict_plan_json(plans_json)
            
            formatted_string = self._generate_raw(profile, plans_json)
            logged_performance = self._generate_log_skeleton(plans_json)

            # 🔥 STEP 4: DEBUG - Verify structure (can be removed after validation)
            logger.debug(f"Normalized plans_json: {json.dumps(plans_json, indent=2, default=str)[:500]}...")

            return ToolResult(True, data={
                "answer": formatted_string,
                "fitness_plan": formatted_string,
                "plans_json": plans_json,
                "json_plan": plans_json,
                "raw_text": formatted_string,
                "logged_performance": logged_performance,
            })

        except Exception as e:
            logger.error(f"Execution Error: {e}", exc_info=True)
            return ToolResult(False, error=str(e))

    def _generate_log_skeleton(self, plans: Dict) -> Dict:
        skeleton = {}
        for day, details in plans.items():
            day_log = {}
            for section in ['warmup', 'main_workout', 'cooldown']:
                for i, _ in enumerate(details.get(section, [])):
                    day_log[f"{section}_{i+1}"] = {"actual_sets": 0, "actual_reps": 0}
            skeleton[day] = day_log
        return skeleton

    @staticmethod
    def _generate_raw(profile, plans_json) -> str:
        safe_profile = profile or {}
        lines: List[str] = [
            f"Generated On: {datetime.now().strftime('%Y-%m-%d')}",
            f"User Name: {safe_profile.get('name', 'User')}",
            f"Goal: {safe_profile.get('primary_goal', 'N/A')}",
            "",
        ]

        for day, details in (plans_json or {}).items():
            safe_details = details or {}
            lines.append(f"Day: {day}")
            lines.append(f"Workout Title: {safe_details.get('workout_title', safe_details.get('day_name', day))}")
            lines.append(f"Main Workout Category: {safe_details.get('main_workout_category', 'N/A')}")
            lines.append("")

            for section_label, section_key in [
                ("Warmup", "warmup"),
                ("Main Workout", "main_workout"),
                ("Cooldown", "cooldown"),
            ]:
                lines.append(f"{section_label}:")
                section_items = safe_details.get(section_key, []) or []
                if not section_items:
                    lines.append("Exercise Name: N/A")
                    lines.append("Sets: N/A")
                    lines.append("Prescription: N/A")
                    lines.append("")
                    continue

                for ex in section_items:
                    safe_ex = ex or {}
                    lines.append(f"Exercise Name: {safe_ex.get('name', 'N/A')}")
                    lines.append(f"Sets: {safe_ex.get('sets', 'N/A')}")
                    lines.append(f"Prescription: {_display_prescription(safe_ex)}")
                    lines.append("")

            lines.append("")

        return "\n".join(lines).strip()

    @staticmethod
    def generate_standard_json_output(profile, plans_json):
        return FitnessPlanGeneratorTool._generate_raw(profile, plans_json)

    @staticmethod
    def _generate_markdown(profile, plans_json) -> str:
        md = f"# FriskaAI Fitness Plan\n\n**Generated on:** {datetime.now().strftime('%B %d, %Y')}\n"
        for day, details in plans_json.items():
            md += f"## {day}\n"
            for icon, key in [("🔥 Warmup", "warmup"), ("💪 Main Workout", "main_workout"), ("🧘 Cooldown", "cooldown")]:
                md += f"### {icon}\n"
                for ex in details.get(key, []):
                    md += f"- **{ex['name']}**: {ex.get('sets', 'N/A')} x {_display_prescription(ex)}\n"
        return md


class WorkoutAdjusterTool:
    def __init__(self, *args, **kwargs):
        pass

    async def safe_execute(self, **kwargs) -> ToolResult:
        try:
            return await self.execute(**kwargs)
        except Exception as e:
            logger.error(f"WorkoutAdjusterTool.safe_execute failed: {e}", exc_info=True)
            return ToolResult(
                False,
                data={"answer": "I hit a temporary technical issue while adjusting the workout plan. Please try again."},
                error=str(e),
                metadata={"error_handled": True, "error_type": "SAFE_EXECUTE_ERROR", "tool_name": "workout_adjuster"},
            )

    async def execute(self, query: str, current_plan: dict, modification_data: dict,
                      user_profile: dict, **kwargs) -> ToolResult:
        try:
            if not current_plan:
                return ToolResult(False, error="No active fitness plan found to modify.")

            df = FitnessDataset.load()
            filtered_df = ExerciseFilter.apply_filters(df, user_profile)
            if filtered_df.empty:
                filtered_df = df
            broad_df = df

            day_target = modification_data.get('day')
            target_idx = modification_data.get('target_index')
            target_name = modification_data.get('target_name')
            mod_type = modification_data.get('modification_type', 'replace')
            target_section_request = modification_data.get('target_section')

            def clean_str(s): return str(s).lower().strip().replace('s', '').replace('-', ' ')

            found_day = found_section = None
            found_idx = -1
            original_ex = None
            days_to_search = [day_target] if day_target else list(current_plan.keys())

            for d in days_to_search:
                if d not in current_plan: continue
                day_data = current_plan[d]
                search_order = [target_section_request] if target_section_request else ['main_workout', 'warmup', 'cooldown']
                for sec in search_order:
                    if sec not in day_data: continue
                    exercises = day_data[sec]
                    if target_idx is not None:
                        idx_0 = target_idx - 1
                        if 0 <= idx_0 < len(exercises):
                            found_day, found_section, found_idx = d, sec, idx_0
                            original_ex = exercises[idx_0]
                            break
                    elif target_name:
                        t_clean = clean_str(target_name)
                        for i, ex in enumerate(exercises):
                            ex_name_clean = clean_str(ex['name'])
                            if t_clean in ex_name_clean or ex_name_clean in t_clean:
                                found_day, found_section, found_idx = d, sec, i
                                original_ex = ex
                                break
                            if difflib.SequenceMatcher(None, ex_name_clean, t_clean).ratio() > 0.75:
                                found_day, found_section, found_idx = d, sec, i
                                original_ex = ex
                                break
                    if original_ex: break
                if original_ex: break

            if not original_ex:
                debug_msg = f"Target: '{target_name or target_idx}' in {day_target or 'plan'} ({target_section_request or 'any section'})."
                return ToolResult(False, error=f"I couldn't find that exercise to modify. ({debug_msg})")

            orig_met = float(original_ex.get('met_value', 3.0))
            candidates = filtered_df.copy()
            candidates = candidates[candidates['Exercise Name'] != original_ex['name']]

            if mod_type == 'harder':
                candidates = candidates[candidates['MET value'] > orig_met]
            elif mod_type == 'easier':
                candidates = candidates[candidates['MET value'] < orig_met]

            orig_benefit = str(original_ex.get('benefit', '')).lower()
            orig_cat = ""
            if 'cardio' in orig_benefit or found_section == 'warmup_pulse':
                candidates = candidates[candidates['Primary Category'].str.contains('Cardio|HIIT', case=False, na=False)]
                orig_cat = "Cardio"
            elif found_section in ['warmup', 'cooldown']:
                candidates = candidates[candidates['Primary Category'].str.contains('Mobility|Flexibility|Stretch', case=False, na=False)]
                orig_cat = "Mobility/Stretch"

            if candidates.empty:
                candidates = broad_df[broad_df['Exercise Name'] != original_ex['name']]
                if mod_type == 'harder':
                    candidates = candidates[candidates['MET value'] > orig_met]
                elif mod_type == 'easier':
                    candidates = candidates[candidates['MET value'] < orig_met]
                if orig_cat == "Cardio":
                    candidates = candidates[candidates['Primary Category'].str.contains('Cardio|HIIT', case=False, na=False)]
                elif orig_cat == "Mobility/Stretch":
                    candidates = candidates[candidates['Primary Category'].str.contains('Mobility|Flexibility|Stretch', case=False, na=False)]

            if candidates.empty:
                return ToolResult(False, error=f"I couldn't find a suitable '{mod_type}' replacement for {original_ex['name']}.")

            new_row = candidates.sample(1).iloc[0]
            selector = ExerciseSelector()
            composer = WorkoutComposer(selector)
            composer._dataset = df if 'df' in locals() else FitnessDataset.load()
            weight = user_profile.get('weight_kg', 70)
            s = original_ex.get('sets', '3')
            r = _display_prescription(original_ex)
            if r == "N/A":
                r = "10"
            rest = original_ex.get('rest', '60s')
            rpe = original_ex.get('intensity_rpe', 'RPE 5').replace('RPE', '').strip()
            new_ex_json = composer.format_exercise(new_row, s, r, rest, rpe, weight)
            current_plan[found_day][found_section][found_idx] = new_ex_json
            new_markdown = FitnessPlanGeneratorTool._generate_markdown(user_profile, current_plan)
            raw_text = FitnessPlanGeneratorTool._generate_raw(user_profile, current_plan)

            return ToolResult(True, data={
                "answer": f"I've updated your plan. **{original_ex['name']}** has been replaced with **{new_row['Exercise Name']}** ({mod_type}) in {found_day}'s {found_section}.\n\n{new_markdown}",
                "fitness_plan": new_markdown,
                "plans_json": current_plan,
                "json_plan": current_plan,
                "raw_text": raw_text,
            })

        except Exception as e:
            logger.error(f"Error in WorkoutAdjuster: {e}", exc_info=True)
            return ToolResult(False, error=f"Failed to modify the plan: {str(e)}")


class GeneralFitnessQueryTool(BaseTool):
    def __init__(self, llm_service: LLMService):
        super().__init__(
            ToolType.GENERAL_FITNESS_QUERY.value,
            "Handles general questions about exercises, workout techniques, fitness science, and recovery."
        )
        self.llm_service = llm_service

    async def execute(self, query: str, constraints: Dict, **kwargs) -> ToolResult:
        user_profile = constraints
        profile_context = ""
        if user_profile:
            details = []
            if user_profile.get('name'): details.append(f"Name: {user_profile['name']}")
            if user_profile.get('fitness_level'): details.append(f"Fitness Level: {user_profile['fitness_level']}")
            if user_profile.get('injuries'): details.append(f"Injuries: {', '.join(user_profile['injuries'])}")
            if user_profile.get('primary_goal'): details.append(f"Goal: {user_profile['primary_goal']}")
            if details:
                profile_context = "**USER PROFILE CONTEXT:**\n" + "\n".join([f"- {d}" for d in details]) + "\n\n"

        system_prompt = f"""You are Friska, an expert AI fitness coach and exercise scientist.
{profile_context}
**YOUR SCOPE:** Exercise Form, Fitness Concepts, Recovery, Safety.
**FORBIDDEN:** Medical diagnosis, nutrition plans, non-fitness topics.
**TONE:** Encouraging, professional, safety-focused.
"""
        try:
            answer = await self.llm_service.query(
                prompt=query, system_prompt=system_prompt, max_tokens=800
            )
            return ToolResult(success=True, data={"answer": answer})
        except Exception as e:
            return ToolResult(success=False, error=f"Error processing fitness query: {str(e)}")


class ParsedExercise(BaseModel):
    name: str
    sets: str = "1"
    reps: str = "10"
    category: Optional[str] = None


# ===========================================================================
# PRESCRIPTION PARSER TOOL — Production-Grade 5-Stage Architecture
# ===========================================================================

_DAY_ORDER: List[str] = [
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"
]

_MOD_MAP: Dict[str, str] = {
    "cardio": "Cardio", "aerobic": "Cardio", "cardiovascular": "Cardio",
    "resistance": "Resistance", "strength": "Resistance", "weights": "Resistance",
    "upper": "Upper", "lower": "Lower",
    "hiit": "HIIT", "h.i.i.t.": "HIIT", "h.i.i.t": "HIIT",
    "interval": "HIIT", "conditioning": "HIIT", "tabata": "HIIT",
    "yoga": "Yoga", "pose": "Yoga", "flow": "Yoga",
    "mobility": "Mobility", "stretch": "Mobility",
    "pilates": "Pilates", "mat": "Pilates",
    "rest": "Rest", "recovery": "Rest",
}

_EXCLUDE_PATTERNS: List[str] = [
    r"\bcalorie(s)?\b", r"\bkcal(s)?\b", r"\bstep(s)?\b", r"\bwater\b",
    r"\bmonitor(ing)?\b", r"\bappointment(s)?\b", r"\bbuy\b", r"\bpurchase\b",
    r"\bnext visit\b", r"\bfollow[- ]?up\b", r"\bbp\b",
    r"\bheart rate\b", r"\bblood pressure\b",
]

_MOVEMENT_KEYS: List[str] = [
    "squat", "lunge", "press", "curl", "stretch", "plank", "dog", "pose",
    "roll", "hundred", "cobra", "bird", "row", "lift", "bridge",
    "raise", "extension", "flexion", "twist", "series",
    "push", "pull", "push-up", "pushup", "pullup", "pull-up",
    "hammer", "tricep", "bicep", "shoulder", "chest", "back",
    "hip", "glute", "calf", "deadlift", "swing", "dip", "crunch",
    "jump", "step", "lunge", "squat", "hinge", "rotate",
    "march", "kick", "reach", "hold", "balance",
    "cardio", "cycle", "walk", "jog", "run", "bike", "swim",
    "climb", "sprint", "skip", "hop",
    "cat", "cow", "down-dog", "downdog", "child", "warrior", "mountain",
    "pigeon", "cobra", "sphinx", "supine", "seated", "prone",
    "band", "apart", "fly", "flye", "shrug", "press-up", "dumbbell",
    "barbell", "kettlebell", "cable", "resistance",
    "knee", "heel", "toe", "ankle", "wrist", "neck", "spine", "quad",
    "hamstring", "lat", "trap", "pec", "delt", "ab", "core",
]

_NARRATIVE_BLOCKLIST: Set[str] = {
    "reported", "continues", "tolerated", "scheduled", "purchase", "record",
    "monitor", "goal", "kcals", "calories", "steps", "water",
    "appointment", "heart rate", "blood pressure",
}

_PILATES_KEYS: List[str] = ["roll up", "hundred", "criss-cross", "leg series", "side lying", "mat"]
_YOGA_KEYS: List[str]    = ["cobra", "pose", "flow", "sun salutation", "child", "downward dog", "warrior"]
_MOBILITY_KEYS: List[str] = ["mobility", "open books", "cat/cow", "thoracic", "range of motion", "bird dog"]

_EXERCISE_VERBS: Set[str] = {
    "do", "perform", "hold", "repeat", "walk", "run", "jog", "cycle", "bike", "row",
    "squat", "lunge", "press", "pull", "push", "lift", "stretch", "flow", "pose",
    "plank", "hinge", "rotate", "twist", "curl", "raise", "extend", "march", "bridge",
}

_REQUIRED_TOP_KEYS = frozenset([
    "profile_attributes", "schedule", "frequency_rules",
    "duration_rules", "protocols", "exercises_mentioned",
])


def detect_note_sections(note_text: str) -> Dict[str, str]:
    """Universal section detector for unstructured notes."""
    t = str(note_text or "")
    lines = t.splitlines()
    anchors = {
        "profile": re.compile(r"^\s*(profile|patient profile|client profile)\s*:?\s*$", re.I),
        "plan_of_action": re.compile(r"^\s*plan\s*of\s*action\s*:?\s*$", re.I),
        "exercise_session": re.compile(r"^\s*exercise\s*session\s*:?\s*$", re.I),
        "sample_week": re.compile(r"^\s*sample\s*week\s*:?\s*$", re.I),
    }
    starts: Dict[str, int] = {}

    for i, line in enumerate(lines):
        for key, pat in anchors.items():
            if key not in starts and pat.search(line):
                starts[key] = i

    inline_patterns = {
        "profile": r"\b(profile|patient profile|client profile)\b",
        "plan_of_action": r"\bplan\s*of\s*action\b",
        "exercise_session": r"\bexercise\s*session\b",
        "sample_week": r"\bsample\s*week\b",
    }
    for key, pat in inline_patterns.items():
        if key not in starts:
            m = re.search(pat, t, re.I)
            if m:
                starts[key] = t[:m.start()].count("\n")

    out = {"profile": "", "plan_of_action": "", "exercise_session": "", "sample_week": ""}
    if not starts:
        out["profile"] = t
        return out

    spans = sorted((line_no, key) for key, line_no in starts.items())
    for idx, (start, key) in enumerate(spans):
        end = spans[idx + 1][0] if idx + 1 < len(spans) else len(lines)
        out[key] = "\n".join(lines[start:end]).strip()

    if not out["profile"]:
        first = min(starts.values())
        if first > 0:
            out["profile"] = "\n".join(lines[:first]).strip()
    return out


class PrescriptionRuleEngine:
    """Deterministic Plan-of-Action interpreter."""
    _MOD_MAP = {
        "cardio": "Cardio", "cardiovascular": "Cardio", "aerobic": "Cardio",
        "resistance": "Resistance", "strength": "Resistance", "weights": "Resistance",
        "hiit": "HIIT", "h.i.i.t": "HIIT", "interval": "HIIT", "tabata": "HIIT",
        "yoga": "Yoga", "pilates": "Pilates", "mobility": "Mobility", "stretch": "Mobility",
    }

    @classmethod
    def _norm_mod(cls, raw: str) -> Optional[str]:
        t = str(raw or "").strip().lower()
        if not t: return None
        t_nodot = t.replace(".", "")
        for k, v in cls._MOD_MAP.items():
            kk = k.replace(".", "")
            if kk in t_nodot: return v
        return None

    @staticmethod
    def _max_num(text: str) -> Optional[int]:
        nums = [int(x) for x in re.findall(r"\d+", str(text or ""))]
        return max(nums) if nums else None

    @classmethod
    def interpret(cls, plan_text: str) -> Dict[str, Any]:
        text = str(plan_text or "")
        out = {"modalities": {}, "duration": {}, "intensity": {}, "calorie_targets": []}
        for line in re.split(r"[\n.;]+", text):
            line = str(line or "").strip()
            if not line: continue
            mod = cls._norm_mod(line)
            if not mod: continue
            freq = re.search(
                r"(\d+)\s*[-–]?\s*(\d+)?\s*[xX]?\s*(?:times?)?\s*(?:per\s*week|/week|weekly)",
                line, re.I
            )
            if freq:
                n = cls._max_num(freq.group(0))
                if n:
                    out["modalities"][mod.lower()] = max(int(out["modalities"].get(mod.lower(), 0)), int(n))
            dur = re.search(r"(\d{1,3})\s*(?:min|mins|minutes)\b", line, re.I)
            if dur:
                out["duration"][mod.lower()] = int(dur.group(1))
            rpe = re.search(r"\bRPE\s*([0-9]+(?:\s*[-/]\s*[0-9]+)?)", line, re.I)
            if rpe:
                out["intensity"][mod.lower()] = f"RPE {rpe.group(1).replace('/', '-')}"
            cal = re.search(r"(\d{2,4})\s*(?:kcal|calories?)", line, re.I)
            if cal:
                out["calorie_targets"].append(int(cal.group(1)))
        return out


class SampleWeekInterpreter:
    """Strict Sample Week schedule parser."""
    @staticmethod
    def interpret(sample_week_text: str) -> Dict[str, List[str]]:
        text = str(sample_week_text or "")
        out: Dict[str, List[str]] = {}
        if not text.strip(): return out
        day_pat = re.compile(r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b", re.I)
        parts = day_pat.split(text)
        i = 1
        while i < len(parts) - 1:
            day = str(parts[i]).capitalize()
            payload = str(parts[i + 1] or "")
            mods: List[str] = []
            low = payload.lower()
            if any(k in low for k in ["cardio", "cardiovascular", "aerobic"]): mods.append("Cardio")
            if any(k in low for k in ["resistance", "strength", "weights"]): mods.append("Resistance")
            if any(k in low for k in ["hiit", "h.i.i.t", "interval", "tabata"]): mods.append("HIIT")
            if "yoga" in low: mods.append("Yoga")
            if "pilates" in low: mods.append("Pilates")
            if any(k in low for k in ["mobility", "stretch"]): mods.append("Mobility")
            if any(k in low for k in ["rest", "recovery"]): mods.append("Rest")
            out[day] = list(dict.fromkeys(mods)) if mods else ["General Fitness"]
            i += 2
        return out


class DatasetRanker:
    """Deterministic scoring/ranking for dataset-first exercise selection."""
    @staticmethod
    def rank(df: pd.DataFrame, profile: Dict[str, Any], modality: str = "", target_slot: str = "") -> pd.DataFrame:
        if df is None or df.empty: return df
        goal = str(profile.get("primary_goal") or profile.get("goal") or "").lower()
        level = str(profile.get("fitness_level") or "").lower()
        equipment = [str(x).lower() for x in (profile.get("available_equipment") or [])]
        modality = str(modality or "").lower()
        target_slot = str(target_slot or "").lower()
        ranked = df.copy()

        def _score(row: pd.Series) -> int:
            s = 0
            row_goal = str(row.get("Goal", "")).lower()
            row_region = str(row.get("Body Region", "")).lower()
            row_eq = str(row.get("Equipments", "")).lower()
            row_level = str(row.get("Fitness Level", "")).lower()
            row_tags = str(row.get("Tags", "")).lower()
            row_cat = str(row.get("Primary Category", "")).lower()
            if goal and goal in row_goal: s += 3
            if modality and (modality in row_cat or modality in row_tags): s += 2
            if equipment and any(e in row_eq for e in equipment): s += 2
            if level and level in row_level: s += 2
            if target_slot == "warmup" and any(k in row_tags for k in ["warmup", "mobility"]): s += 3
            if target_slot == "cooldown" and any(k in row_tags for k in ["cooldown", "stretch"]): s += 3
            if target_slot == "main" and not any(k in row_tags for k in ["warmup", "cooldown", "stretch", "mobility"]): s += 3
            if any(k in row_region for k in ["upper", "lower", "core", "full"]): s += 1
            return s

        ranked["_rank_score"] = ranked.apply(_score, axis=1)
        ranked = ranked.sort_values(by=["_rank_score", "Exercise Name"], ascending=[False, True], kind="mergesort")
        return ranked.drop(columns=["_rank_score"], errors="ignore")


class RuleEnforcer:
    """Deterministic weekly frequency enforcer."""
    @staticmethod
    def enforce_frequency(
        day_mods: Dict[str, List[str]],
        rules: Dict[str, Any],
        sample_week_exists: bool = False,
    ) -> Dict[str, List[str]]:
        if sample_week_exists:
            return {d: list(dict.fromkeys(day_mods.get(d, []))) for d in _DAY_ORDER}
        out = {d: list(dict.fromkeys(day_mods.get(d, []))) for d in _DAY_ORDER}
        mods = dict((rules or {}).get("modalities") or {})
        mod_norm = {
            "cardio": "Cardio", "resistance": "Resistance", "hiit": "HIIT",
            "yoga": "Yoga", "pilates": "Pilates", "mobility": "Mobility",
        }
        for k, v in mods.items():
            m = mod_norm.get(str(k).lower())
            need = int(v) if str(v).isdigit() else None
            if not m or not need: continue
            have = sum(1 for d in _DAY_ORDER if m in out.get(d, []))
            for d in _DAY_ORDER:
                if have >= need: break
                if "Rest" in out.get(d, []): continue
                if m not in out[d]:
                    out[d].append(m)
                    have += 1
        return out


class HIITProtocolInterpreter:
    @staticmethod
    def interpret(note_text: str, protocols: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        txt = str(note_text or "")
        if protocols:
            p = protocols[0]
            return {
                "type": str(p.get("type") or "Interval"),
                "rounds": int(p.get("rounds") or 8),
                "work": int(p.get("work_seconds") or 20),
                "rest": int(p.get("rest_seconds") or 10),
            }
        typ = "Tabata" if re.search(r"\btabata\b", txt, re.I) else "HIIT Circuit"
        rounds = 4
        m_rounds = re.search(r"(\d+)\s*round", txt, re.I)
        if m_rounds: rounds = int(m_rounds.group(1))
        work = 40
        rest = 20
        m_work = re.search(r"(\d+)\s*(?:sec|seconds)\s*(?:work|on)", txt, re.I)
        m_rest = re.search(r"(\d+)\s*(?:sec|seconds)\s*(?:rest|off)", txt, re.I)
        if m_work: work = int(m_work.group(1))
        if m_rest: rest = int(m_rest.group(1))
        return {"type": typ, "rounds": rounds, "work": work, "rest": rest}


class CardioSessionBuilder:
    @staticmethod
    def build(day: str, rule_pack: Dict[str, Any], profile: Dict[str, Any],
              cardio_pool: pd.DataFrame) -> Dict[str, Any]:
        duration = int((rule_pack.get("duration") or {}).get("cardio") or 45)
        intensity = str((rule_pack.get("intensity") or {}).get("cardio") or "RPE 5-6")
        mode_name = "Walking / Cycling / Elliptical"
        if cardio_pool is not None and not cardio_pool.empty:
            mode_name = str(cardio_pool.iloc[0].get("Exercise Name") or mode_name)
        return {
            "name": f"Cardio Session ({mode_name})",
            "sets": "1",
            "reps": f"{duration} min",
            "category": "main",
            "_meta": {"source": "cardio_session_builder", "confidence": 0.99},
            "benefit": "Cardiorespiratory conditioning",
            "safety_cue": intensity,
            # ── BUG FIX #4: tag as session_payload so postprocess never replaces it ──
            "_is_session_payload": True,
        }


class CircuitBuilder:
    @staticmethod
    def build(exercises: List[Dict[str, Any]], rounds: int = 3,
              sets: str = "3", reps: str = "8-12") -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for ex in (exercises or []):
            name = str(ex.get("name") or "").strip()
            if not name: continue
            out.append({
                "name": name,
                "sets": str(ex.get("sets") or sets),
                "reps": str(ex.get("reps") or reps),
                "category": "main",
                "equipment": str(ex.get("equipment") or ""),
                "_meta": {"source": "circuit_builder", "confidence": 0.98, "rounds": rounds},
            })
        return out


_LLM_SYSTEM_PROMPT = """
You are a STRICT clinical exercise prescription parsing engine.
Your ONLY job is to EXTRACT information explicitly present in the fitness, rehab, or clinical note supplied.

YOU MUST NEVER:
- Create a workout plan
- Auto-fill missing exercises
- Generate, suggest or invent exercises
- Interpret beyond extraction
- Assign days unless they are explicitly written
- Fill missing days with assumed defaults
- Interpret calories, steps, hydration, heart-rate, blood-pressure,
  monitoring instructions, appointment notes, or purchase advice as exercises

YOU MUST RETURN valid JSON only. No explanations, no markdown fences, no extra keys, no missing keys.

EXTRACTION RULES:
1. Extract ONLY what is explicitly written in the note.
2. Sets and reps: only if explicitly written; otherwise empty string "".
3. Modality: only if clearly stated (Cardio/Resistance/Upper/Lower/HIIT/Yoga/Mobility/Pilates/Rest); otherwise null.
4. explicit_days: only days literally named in the note.
5. frequency_rules: populate only when "N times per week" is written.
6. days_per_week: only if explicitly stated.
7. Rest days: must appear explicitly; never infer them.
8. Protocols (Tabata/Interval): separate from exercises; include only when explicitly written.
9. Exercise names: must appear verbatim in the source note; never hallucinate.
10. If uncertain: use null / empty array / empty string.

OUTPUT SCHEMA (strict — exactly these top-level keys, no others):
{
  "profile_attributes": {"weight_kg": null, "age": null, "goal": null, "injuries": [], "equipment": []},
  "schedule": {"days_per_week": null, "explicit_days": {}},
  "frequency_rules": [],
  "duration_rules": [],
  "protocols": [],
  "exercises_mentioned": []
}

Each item in exercises_mentioned:
  {"name": "<string>", "sets": "<string|empty>", "reps": "<string|empty>", "modality": "<string|null>"}
Each item in frequency_rules:
  {"modality": "<string>", "times_per_week": <int>}
Each item in duration_rules:
  {"modality": "<string>", "duration_minutes": <int>}
Each item in protocols:
  {"type": "<Tabata|Interval>", "rounds": <int|null>, "work_seconds": <int|null>, "rest_seconds": <int|null>}
"""


class PrescriptionParserTool:
    """
    Production-grade 5-stage clinical exercise prescription parser.
    async execute(text_content, user_profile) -> ToolResult
    """

    def __init__(self, api_key: Optional[str] = None, endpoint: Optional[str] = None,
                 model_name: str = "gpt-4o-mini") -> None:
        self.model_name = model_name
        self.endpoint = endpoint or os.getenv("OPENAI_BASE_URL")
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self._client = None
        if self.endpoint and self.api_key:
            try:
                from openai import OpenAI
                self._client = OpenAI(base_url=self.endpoint, api_key=self.api_key)
            except Exception as exc:
                logger.warning("OpenAI client init failed: %s — regex fallback active.", exc)
        self._dataset: pd.DataFrame = FitnessDataset.load()
        self._source_text: str = ""
        self._detected_person_names: Set[str] = set()
        self._active_clinical_context: Dict[str, Any] = {}

    # ── PUBLIC ENTRY POINT ────────────────────────────────────────────────────

    async def execute(self, text_content: str, user_profile: Dict[str, Any]) -> ToolResult:
        safe_profile = dict(user_profile or {})
        try:
            extracted = await self._stage1_extract(str(text_content or ""))
            resolved = self._stage2_resolve(extracted, str(text_content or ""))
            merged_profile = self._merge_profile(safe_profile, resolved["extract"]["profile_attributes"])
            plans_json = self._stage3_build_week(resolved, merged_profile)
            logged = FitnessPlanGeneratorTool()._generate_log_skeleton(plans_json)
            raw_text = FitnessPlanGeneratorTool._generate_raw(merged_profile, plans_json)
            return ToolResult(True, data={
                "profile": merged_profile,
                "plans_json": plans_json,
                "json_plan": plans_json,
                "raw_text": raw_text,
                "logged_performance": logged,
                "parsed_output": resolved,
            })
        except Exception as exc:
            logger.error("PrescriptionParserTool.execute failed: %s", exc, exc_info=True)
            fallback_plan = {
                "Monday": {
                    "day_name": "Monday",
                    "main_workout_category": "General Fitness",
                    "warmup": [], "main_workout": [], "cooldown": [],
                    "safety_notes": ["Fallback plan — parser encountered an error."],
                }
            }
            logged = FitnessPlanGeneratorTool()._generate_log_skeleton(fallback_plan)
            raw_text = FitnessPlanGeneratorTool._generate_raw(safe_profile, fallback_plan)
            return ToolResult(True, data={
                "profile": safe_profile,
                "plans_json": fallback_plan,
                "json_plan": fallback_plan,
                "raw_text": raw_text,
                "logged_performance": logged,
            })

    # ── STAGE 1 ───────────────────────────────────────────────────────────────

    async def _stage1_extract(self, text: str) -> Dict[str, Any]:
        self._source_text = text
        self._detected_person_names = self._collect_person_names(text)
        if self._client is None:
            return self._stage1_fallback_extract(text)
        for attempt in range(2):
            try:
                raw = await asyncio.to_thread(self._call_llm_sync, text)
                if raw is None: continue
                if set(raw.keys()) != _REQUIRED_TOP_KEYS:
                    logger.debug("LLM key mismatch on attempt %d", attempt)
                    continue
                normalised = self._normalise_extract(raw)
                if self._strict_validate(normalised):
                    return normalised
            except Exception as exc:
                logger.warning("LLM attempt %d failed: %s", attempt, exc)
        logger.warning("LLM extraction failed — using regex fallback.")
        return self._stage1_fallback_extract(text)

    def _call_llm_sync(self, text: str) -> Optional[Dict[str, Any]]:
        resp = self._client.chat.completions.create(
            model=self.model_name,
            temperature=0,
            messages=[
                {"role": "system", "content": _LLM_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
        )
        return self._extract_json_from_text(resp.choices[0].message.content or "")

    # ── STAGE 1 FALLBACK ──────────────────────────────────────────────────────

    def _detect_sections(self, note_text: str) -> Dict[str, str]:
        detected = detect_note_sections(note_text)
        return {
            "profile_section": detected.get("profile", ""),
            "plan_of_action_section": detected.get("plan_of_action", ""),
            "exercise_session_section": detected.get("exercise_session", ""),
            "sample_week_section": detected.get("sample_week", ""),
        }

    def _stage1_fallback_extract(self, text: str) -> Dict[str, Any]:
        """Pure-regex extraction; same output schema as LLM extractor."""
        out = self._empty_extract()
        t = str(text or "")
        sections = self._detect_sections(t)
        profile_text = sections.get("profile_section") or t
        plan_text = sections.get("plan_of_action_section") or t
        session_text = sections.get("exercise_session_section") or ""
        sample_week_text = sections.get("sample_week_section") or ""

        out["profile_attributes"]["age"] = (
            self._pick_int(profile_text, r"\bage\s*[:\-]?\s*(\d{1,3})\b") or
            self._pick_int(t, r"\bage\s*[:\-]?\s*(\d{1,3})\b") or
            self._pick_int(t, r"\b(\d{1,3})\s*[-]?\s*(?:y(?:ear)?(?:s)?(?:[-\s]?old)?)\b") or
            self._pick_int(t, r"\b(\d{2,3})\s*y\.?o\.?\b")
        )
        out["profile_attributes"]["weight_kg"] = (
            self._pick_float(profile_text, r"\bweight\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*(?:kg|kgs)?\b") or
            self._pick_float(profile_text, r"\b(\d+(?:\.\d+)?)\s*(?:kg|kgs)\b") or
            self._pick_float(t, r"\bweight\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*(?:kg|kgs)?\b")
        )
        out["schedule"]["days_per_week"] = self._pick_int(t, r"\b(\d)\s*(?:days?|sessions?)\s*(?:per|/)\s*week\b")

        inj = re.search(r"\binjur(?:y|ies)\s*[:\-]?\s*([^\n]{2,120})", profile_text, re.I)
        if inj:
            for x in re.split(r",|/|;|\band\b", inj.group(1), flags=re.I):
                val = x.strip().strip(".")
                if val and val.lower() not in [i.lower() for i in out["profile_attributes"]["injuries"]]:
                    out["profile_attributes"]["injuries"].append(val)
        med = re.search(r"\bmedical\s+conditions?\s*[:\-]?\s*([^\n]{2,120})", profile_text, re.I)
        if med:
            for x in re.split(r",|/|;|\band\b", med.group(1), flags=re.I):
                val = x.strip().strip(".")
                if val and val.lower() not in [i.lower() for i in out["profile_attributes"]["injuries"]]:
                    out["profile_attributes"]["injuries"].append(val)
        eq = re.search(r"\b(?:equipment|available\s+equipment)\s*[:\-]?\s*([^\n]{2,120})", profile_text, re.I)
        if eq:
            out["profile_attributes"]["equipment"] = [
                x.strip() for x in re.split(r",|/|;|\band\b", eq.group(1), flags=re.I) if x.strip()
            ]

        constraint_pat = re.compile(
            r"(?:avoid|contraindicated?|do\s+not|no\s+|limit|restrict|"
            r"post[-\s]|history\s+of|diagnosed\s+with|"
            r"rehab(?:ilitation)?\s+for|condition\s*[:\-])\s*[:\-]?\s*([^\n.]{3,80})",
            re.I
        )
        for m in constraint_pat.finditer(t):
            constraint = m.group(1).strip().rstrip(".,;")
            if (constraint and 2 <= len(constraint.split()) <= 6 and
                    not any(skip in constraint.lower() for skip in
                            ["calories", "steps", "water", "logging", "food", "app"])):
                existing = [x.lower() for x in out["profile_attributes"]["injuries"]]
                if constraint.lower() not in existing:
                    out["profile_attributes"]["injuries"].append(constraint)

        block_lines = self._extract_structured_block_lines(session_text if session_text else t)
        candidate_lines = block_lines if block_lines else [l.strip() for l in (session_text if session_text else t).splitlines() if l.strip()]
        merged_lines = self._merge_exercise_lines(candidate_lines)

        # ── Sample Week parsing ──
        sw_match = re.search(
            r"sample\s*week\s*[:\-]?\s*(.+?)(?=\n\n|\Z)",
            sample_week_text if sample_week_text else t, re.I | re.S
        )
        if sw_match:
            out["sample_week_exists"] = True
            sw_text = sw_match.group(1)
            day_split_pat = re.compile(
                r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b", re.I
            )
            sw_parts = day_split_pat.split(sw_text)
            i = 1
            while i < len(sw_parts) - 1:
                day_name = sw_parts[i].strip().capitalize()
                content = sw_parts[i + 1].strip() if i + 1 < len(sw_parts) else ""
                content_clean = re.sub(r"\s*\d+\s*[-–]?\d*\s*min(?:s|utes?)?\s*", " ", content, flags=re.I).strip()
                d = self._normalise_day(day_name)
                if d and content_clean:
                    mods = self._parse_mods(content_clean)
                    if mods:
                        out["schedule"]["explicit_days"][d] = list(dict.fromkeys(mods))
                    dur = self._pick_int(content, r"(\d{1,3})\s*(?:min|mins|minutes)\b")
                    if dur:
                        for m in (mods if mods else []):
                            if m != "Rest":
                                out["duration_rules"].append({"modality": m, "duration_minutes": dur})
                i += 2

        for line in merged_lines:
            if self._is_non_exercise_line(line): continue
            day_match = re.match(
                r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b\s*[:\-]?\s*(.*)$",
                line, flags=re.I,
            )
            if day_match:
                day = self._normalise_day(day_match.group(1))
                mods = self._parse_mods(day_match.group(2))
                if day and mods:
                    out["schedule"]["explicit_days"][day] = mods
                dur = self._pick_int(day_match.group(2), r"(\d{1,3})\s*(?:min|mins|minutes)\b")
                if dur:
                    for m in mods:
                        if m != "Rest":
                            out["duration_rules"].append({"modality": m, "duration_minutes": dur})
                continue
            if self._is_protocol_line(line):
                proto = self._parse_protocol(line)
                if proto: out["protocols"].append(proto)
                continue
            ex = self._extract_exercise_from_line(line, structured=bool(block_lines))
            if ex:
                out["exercises_mentioned"].append(ex)

        rpe_m = re.search(r"\bRPE\s*([\d]+(?:[/\-][\d]+)?)", t, re.I)
        if rpe_m:
            out["profile_attributes"]["rpe_target"] = rpe_m.group(1).replace("/", "-")

        plan_scope = plan_text if plan_text else t

        daily_pat = re.compile(
            r"(homework|exercise|stretching|mobility|yoga|cardio|resistance)"
            r"[^\n]{0,20}?\b(?:daily|every\s+day|7\s*[xX]\s*(?:per\s+)?week)\b",
            re.I
        )
        for m in daily_pat.finditer(plan_scope):
            mod = self._parse_mod(m.group(1))
            if mod and not any(r.get("modality") == mod for r in out["frequency_rules"]):
                out["frequency_rules"].append({"modality": mod, "times_per_week": 7})
        if re.search(r"\b(?:daily|every\s+day)\b", t, re.I) and not out["schedule"]["days_per_week"]:
            out["schedule"]["days_per_week"] = 5

        freq_pat = re.compile(
            r"(?:"
            r"(cardio|cardiovascular|aerobic|resistance|strength|weights|hiit|h\.i\.i\.t\.?"
            r"|yoga|mobility|pilates|circuit|interval|tabata)"
            r"[^\n]{0,40}?(\d+)\s*[-–]?\s*(\d+)?\s*[xX]\s*(?:per\s+week|/\s*week)?"
            r"|"
            r"(\d+)\s*[-–]?\s*(\d+)?\s*[xX]\s*(?:per\s+week|/\s*week)[^\n]{0,40}?"
            r"(cardio|cardiovascular|aerobic|resistance|strength|weights|hiit|h\.i\.i\.t\.?"
            r"|yoga|mobility|pilates|circuit|interval|tabata)"
            r")",
            re.I
        )
        for m in freq_pat.finditer(plan_scope):
            if m.group(1):
                raw_mod = m.group(1)
                cnt_low = self._to_int(m.group(2))
                cnt_high = self._to_int(m.group(3))
            else:
                raw_mod = m.group(6)
                cnt_low = self._to_int(m.group(4))
                cnt_high = self._to_int(m.group(5))
            mod = self._parse_mod(raw_mod)
            cnt = cnt_high if cnt_high and cnt_high > cnt_low else cnt_low
            if mod and cnt and not any(r.get("modality") == mod for r in out["frequency_rules"]):
                out["frequency_rules"].append({"modality": mod, "times_per_week": cnt})

        textual_freq_pat = re.compile(
            r"(cardio|cardiovascular|aerobic|resistance|strength|weights|hiit|h\.i\.i\.t\.?|yoga|mobility|pilates)"
            r"[^\n]{0,25}?\b(once|twice|thrice)\s+weekly\b",
            re.I
        )
        word_to_int = {"once": 1, "twice": 2, "thrice": 3}
        for m in textual_freq_pat.finditer(plan_scope):
            mod = self._parse_mod(m.group(1))
            cnt = word_to_int.get(m.group(2).lower())
            if mod and cnt and not any(r.get("modality") == mod for r in out["frequency_rules"]):
                out["frequency_rules"].append({"modality": mod, "times_per_week": cnt})

        # ── Yoga day detection ──
        for yoga_m in re.finditer(r"\byoga\b", t, re.I):
            ctx_start = max(0, yoga_m.start() - 80)
            ctx_end = min(len(t), yoga_m.end() + 80)
            ctx = t[ctx_start:ctx_end]
            is_class_listing = bool(re.search(r"\d{1,2}:\d{2}\s*(am|pm)", ctx, re.I))
            is_prescribed = bool(re.search(
                r"(gentle|try|prescribed|do\b|perform|yoga\s*[-–]|meditation|"
                r"morning|daily|home|warmup|cool)",
                ctx, re.I
            ))
            if is_class_listing and not is_prescribed:
                continue
            day_ctx_start = max(0, yoga_m.start() - 30)
            day_ctx_end = min(len(t), yoga_m.end() + 150)
            day_ctx = t[day_ctx_start:day_ctx_end]
            for day_name in re.findall(
                r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
                day_ctx, flags=re.I
            ):
                d = self._normalise_day(day_name)
                if d:
                    existing = out["schedule"]["explicit_days"].get(d, [])
                    if "Yoga" not in existing:
                        out["schedule"]["explicit_days"][d] = existing + ["Yoga"]

        dur_pat = re.compile(
            r"(?:"
            r"(cardio|aerobic|resistance|strength|weights|hiit|yoga|mobility|pilates)"
            r"[^\n]{0,20}?(\d{1,3})\s*(?:min|mins|minutes)"
            r"|"
            r"(\d{1,3})\s*(?:min|mins|minutes)[^\n]{0,20}?"
            r"(cardio|aerobic|resistance|strength|weights|hiit|yoga|mobility|pilates)"
            r")",
            re.I
        )
        for m in dur_pat.finditer(plan_scope):
            if m.group(1):
                mod, mins = self._parse_mod(m.group(1)), self._to_int(m.group(2))
            else:
                mod, mins = self._parse_mod(m.group(4)), self._to_int(m.group(3))
            if mod and mins and mins > 0:
                if not any(r.get("modality") == mod for r in out["duration_rules"]):
                    out["duration_rules"].append({"modality": mod, "duration_minutes": mins})

        # ── Plan-of-Action exercise extraction ──
        poa_text = plan_text if plan_text else t

        _POA_NOISE_SPLIT = re.compile(
            r"\b(?:on the mat|sit back on heels|sit back|stretch arms forward|"
            r"lay flat back?|hug (?:right|left|both) knee|hold \d+[^a-z]{0,3}seconds?|"
            r"pedal feet|press both heels|to feel a stretch|feel a stretch|"
            r"seconds? to|keep it moving|stretch hips?|stretch back|"
            r"left knee|right knee|both knees|hug both|next|then)\b",
            re.I
        )
        _POA_SKIP_FREQ = re.compile(r"\bper\s+(week|session|day)\b|\btimes\b|\bworkout\b", re.I)

        poa_sentences = re.split(r'(?<=\.)\s+(?=[A-Z])', poa_text)

        poa_nxm_pat = re.compile(
            r"([A-Za-z\"'][A-Za-z '\-/\"]{2,50}?)\s+(\d+)\s*[xX]\s*(\d+)",
            re.I
        )

        def _clean_poa_name(raw: str) -> str:
            if not raw: return ""
            raw = raw.strip().rstrip("-, ").strip('"\'')
            parts = _POA_NOISE_SPLIT.split(raw)
            for part in parts:
                part = part.strip().strip('"\'')
                if part and any(k in part.lower() for k in _MOVEMENT_KEYS):
                    words = part.split()
                    if len(words) > 5:
                        for n in range(min(5, len(words)), 0, -1):
                            sub = " ".join(words[-n:]).strip().strip('"\'')
                            if any(k in sub.lower() for k in _MOVEMENT_KEYS):
                                return sub
                    return part
            words = raw.split()
            for n in range(min(5, len(words)), 0, -1):
                sub = " ".join(words[-n:]).strip().strip('"\'')
                if any(k in sub.lower() for k in _MOVEMENT_KEYS):
                    return sub
            return raw[:40]

        for sent in poa_sentences:
            for m in poa_nxm_pat.finditer(sent):
                raw_name = m.group(1)
                sets_raw, reps_raw = m.group(2), m.group(3)
                if _POA_SKIP_FREQ.search(raw_name): continue
                candidate = _clean_poa_name(raw_name)
                if not candidate or not any(k in candidate.lower() for k in _MOVEMENT_KEYS): continue
                name = self._clean_name(candidate)
                if not self._is_valid_exercise_name(name): continue
                # ── BUG FIX #6: warmup/mobility exercises from NxM get correct category ──
                inferred_mod = self._infer_modality(name)
                exercise_cat = "main"
                if inferred_mod in ("Mobility", "Yoga", "Pilates"):
                    exercise_cat = "warmup"
                if not any(e["name"].lower() == name.lower() for e in out["exercises_mentioned"]):
                    out["exercises_mentioned"].append({
                        "name": name, "sets": sets_raw, "reps": reps_raw,
                        "modality": inferred_mod,
                        "_confidence": 0.90,
                        "category": exercise_cat,
                    })

        # Named exercise anchors without NxM
        _POA_NAMED_ANCHORS = re.compile(
            r"\b("
            r"Cat(?:\s+and\s+|\s*/\s*)Cow"
            r"|Child'?s?\s+Pose"
            r"|Bird\s*[-\s]?Dog"
            r"|Plank\s+to\s+Down-?\s*dog"
            r"|Cobras?(?:\s+\"?Press\s+Ups?\"?)?"
            r"|Band\s+Pull[-\s]Aparts?"
            r"|Body\s+Squat"
            r"|Stretch\s+Quads?"
            r"|Quad\s+Stretch"
            r"|Knee\s+(?:hugs?|into\s+chest)"
            r"|Down-?\s*dog"
            r"|Press\s+Ups?"
            r")\b",
            re.I
        )
        _ANCHOR_CANONICAL = {
            "cat and cow": "Cat/Cow", "cat/cow": "Cat/Cow",
            "child's pose": "Child's Pose", "childs pose": "Child's Pose",
            "bird dog": "Bird Dog", "bird-dog": "Bird Dog",
            "plank to down-dog": "Plank to Down-dog", "plank to downdog": "Plank to Down-dog",
            "cobras": "Cobras", "cobras press ups": "Cobras / Press Ups",
            "band pull-aparts": "Band Pull-Aparts", "band pull aparts": "Band Pull-Aparts",
            "body squat": "Body Squat", "stretch quads": "Stretch Quads",
            "quad stretch": "Quad Stretch", "knee hugs": "Knee Hugs",
            "knee into chest": "Knee to Chest Stretch",
            "down-dog": "Downward Dog", "downdog": "Downward Dog",
            "press ups": "Push-Ups",
        }
        _WARMUP_ANCHORS = {
            "Cat/Cow", "Child's Pose", "Bird Dog", "Cobras", "Cobras / Press Ups",
            "Plank to Down-dog", "Downward Dog", "Knee Hugs", "Knee to Chest Stretch",
        }
        _COOLDOWN_ANCHORS = {"Stretch Quads", "Quad Stretch"}

        for am in _POA_NAMED_ANCHORS.finditer(poa_text):
            raw = am.group(0).strip()
            canonical = _ANCHOR_CANONICAL.get(raw.lower(), raw)
            cat = "warmup" if canonical in _WARMUP_ANCHORS else (
                "cooldown" if canonical in _COOLDOWN_ANCHORS else "main"
            )
            tail = poa_text[am.end():am.end() + 30]
            sr = re.search(r"(\d+)\s*[xX]\s*(\d+)", tail)
            sets_r = sr.group(1) if sr else ""
            reps_r = sr.group(2) if sr else ""
            already = any(
                e["name"].lower() == canonical.lower() or
                difflib.SequenceMatcher(None, e["name"].lower(), canonical.lower()).ratio() > 0.80
                for e in out["exercises_mentioned"]
            )
            if not already:
                out["exercises_mentioned"].append({
                    "name": canonical, "sets": sets_r, "reps": reps_r,
                    "modality": self._infer_modality(canonical),
                    "_confidence": 0.88, "category": cat,
                })

        # Pattern B: "Exercise Name N/Mlbs"
        poa_weight_pat = re.compile(
            r"([A-Z][A-Za-z '\-/\(\)]{1,40}?)\s+([\d]+(?:/[\d]+)?)\s*(lbs?|kg|pounds?)\b",
            re.I
        )
        for m in poa_weight_pat.finditer(poa_text):
            raw_name = m.group(1).strip().rstrip("-, ")
            weight_ann = f"{m.group(2)} {m.group(3)}"
            if _POA_SKIP_FREQ.search(raw_name): continue
            if not any(k in raw_name.lower() for k in _MOVEMENT_KEYS): continue
            cap_parts = re.split(r'(?<=[a-z\)])\s+(?=[A-Z])', raw_name)
            best = raw_name
            for part in reversed(cap_parts):
                part = part.strip()
                if any(k in part.lower() for k in _MOVEMENT_KEYS) and 1 <= len(part.split()) <= 5:
                    best = part
                    break
            name = self._clean_name(best)
            if not self._is_valid_exercise_name(name): continue
            if not any(e["name"].lower() == name.lower() for e in out["exercises_mentioned"]):
                out["exercises_mentioned"].append({
                    "name": name, "sets": "3", "reps": "8-12",
                    "equipment": weight_ann,
                    "modality": self._infer_modality(name),
                    "_confidence": 0.92, "category": "main",
                })

        # Pattern B2: Resistance exercise anchors without weight
        _RESISTANCE_NAME_ANCHORS = re.compile(
            r"\b("
            r"Band\s+Pull[-\s]Aparts?"
            r"|Body\s+Squat"
            r"|Goblet\s+Squat"
            r"|Romanian\s+Deadlift"
            r"|Lat\s+Pull[-\s]?down"
            r"|Chest\s+Fly"
            r"|Face\s+Pull"
            r"|Arnold\s+Press"
            r"|Hammer\s+Curl"
            r"|Overhead\s+Press"
            r"|Bent[-\s]Over\s+Row"
            r"|Sumo\s+Squat"
            r"|Hip\s+Thrust"
            r"|Glute\s+Bridge"
            r")\b",
            re.I
        )
        for anm in _RESISTANCE_NAME_ANCHORS.finditer(poa_text):
            raw = anm.group(0).strip()
            name = self._clean_name(raw)
            if not self._is_valid_exercise_name(name): continue
            tail = poa_text[anm.end():anm.end() + 30]
            wt = re.search(r"([\d/]+)\s*(lbs?|kg)", tail)
            equip = f"{wt.group(1)} {wt.group(2)}" if wt else ""
            already = any(e["name"].lower() == name.lower() for e in out["exercises_mentioned"])
            if not already:
                out["exercises_mentioned"].append({
                    "name": name, "sets": "3", "reps": "8-12",
                    "equipment": equip, "modality": "Resistance",
                    "_confidence": 0.90, "category": "main",
                })

        # Homework Daily pattern
        homework_pat = re.compile(r"homework\s*daily\s*[-–:]?\s*([^\n]{5,200})", re.I)
        hm = homework_pat.search(t)
        if hm:
            hw_raw = hm.group(1)
            hw_items = re.split(r",|\band\b", hw_raw, flags=re.I)
            for item in hw_items:
                item = item.strip().rstrip(".")
                item = re.sub(r"\s*\d+\s*[xX]\s*\d+\s*", "", item).strip()
                if not item or len(item) < 3: continue
                if re.match(r"^\d+$", item): continue
                if len(item.split()) > 6: continue
                out.setdefault("homework_exercises", [])
                if item.lower() not in [h.get("name", "").lower() for h in out["homework_exercises"]]:
                    out["homework_exercises"].append({
                        "name": item, "sets": "", "reps": "",
                        "category": "warmup", "is_daily_homework": True,
                    })

        weight_note_pat = re.compile(r"(\d+(?:\.\d+)?)\s*(lbs?|kg|pounds?)\b", re.I)
        wm = weight_note_pat.search(t)
        if wm:
            out["weight_note"] = f"{wm.group(1)} {wm.group(2)}"

        # Circuit header parsing
        circuit_hdr = None
        _CIRCUIT_PATTERNS = [
            re.compile(r"(\d+)\s*rounds?\s+(\d+)\s*sets?\s*/\s*(\d+(?:[\-–]\d+)?)\s*reps?", re.I),
            re.compile(r"(\d+)\s*sets?\s*[/\s]\s*(\d+(?:[\-–]\d+)?)\s*reps?(?:\s+to\s+\w+)?", re.I),
            re.compile(r"(\d+)\s*[xX]\s*each[^\n/]{0,20}?(\d+(?:[\-–]\d+)?)\s*reps?", re.I),
        ]
        for pat in _CIRCUIT_PATTERNS:
            m_c = pat.search(t)
            if m_c:
                if len(m_c.groups()) >= 3:
                    out["circuit_sets"] = m_c.group(2)
                    out["circuit_reps"] = m_c.group(3)
                else:
                    out["circuit_sets"] = m_c.group(1)
                    out["circuit_reps"] = m_c.group(2)
                break

        # POA inline mobility/warmup exercises → homework_exercises
        if not out.get("homework_exercises"):
            poa_hw = [
                e for e in out.get("exercises_mentioned", [])
                if (e.get("category") in ("warmup", "cooldown")) or
                   e.get("modality") in ("Mobility", "Yoga", "Pilates") or
                   self._infer_modality(e.get("name", "")) in ("Mobility", "Yoga", "Pilates")
            ]
            if poa_hw:
                out.setdefault("homework_exercises", [])
                for e in poa_hw:
                    if e["name"].lower() not in [h.get("name", "").lower() for h in out["homework_exercises"]]:
                        out["homework_exercises"].append({
                            "name": e["name"], "sets": e.get("sets", ""),
                            "reps": e.get("reps", ""),
                            "category": e.get("category", "warmup"),
                            "is_daily_homework": True,
                        })
                out["exercises_mentioned"] = [
                    e for e in out.get("exercises_mentioned", [])
                    if e.get("category") not in ("warmup", "cooldown") and
                       e.get("modality") not in ("Mobility", "Yoga", "Pilates") and
                       self._infer_modality(e.get("name", "")) not in ("Mobility", "Yoga", "Pilates")
                ]
        else:
            out["exercises_mentioned"] = [
                e for e in out.get("exercises_mentioned", [])
                if e.get("category") not in ("warmup", "cooldown") and
                   e.get("modality") not in ("Mobility", "Yoga", "Pilates") and
                   self._infer_modality(e.get("name", "")) not in ("Mobility", "Yoga", "Pilates")
            ]

        out["exercise_session_exercises"] = []
        if session_text:
            s_lines = [l.strip() for l in session_text.splitlines() if l.strip()]
            for l in self._merge_exercise_lines(s_lines):
                ex_s = self._extract_exercise_from_line(l, structured=True)
                if ex_s and not any(e["name"].lower() == ex_s["name"].lower() for e in out["exercise_session_exercises"]):
                    out["exercise_session_exercises"].append(ex_s)

        out["plan_of_action_exercises"] = []
        poa_lines = [l.strip() for l in poa_text.splitlines() if l.strip()]
        for l in self._merge_exercise_lines(poa_lines):
            ex_p = self._extract_exercise_from_line(l, structured=False)
            if ex_p and not any(e["name"].lower() == ex_p["name"].lower() for e in out["plan_of_action_exercises"]):
                out["plan_of_action_exercises"].append(ex_p)

        regex_items = self._hybrid_regex_extract_exercises(t)
        existing = {str(e.get("name", "")).lower() for e in out.get("exercises_mentioned", [])}
        for rx in regex_items:
            n = str(rx.get("name", "")).lower()
            if not n or n in existing: continue
            out["exercises_mentioned"].append(rx)
            existing.add(n)

        return self._normalise_extract(out)


    # ── STAGE 2 ───────────────────────────────────────────────────────────────

    def _stage2_resolve(self, extracted: Dict[str, Any], original_text: str) -> Dict[str, Any]:
        ex = self._normalise_extract(extracted)
        sections = detect_note_sections(original_text)
        rule_pack = PrescriptionRuleEngine.interpret(sections.get("plan_of_action", "") or original_text)
        sample_map = SampleWeekInterpreter.interpret(sections.get("sample_week", ""))

        if sample_map:
            ex.setdefault("schedule", {}).setdefault("explicit_days", {})
            ex["schedule"]["explicit_days"] = sample_map
            ex["sample_week_exists"] = True

        mod_title = {
            "cardio": "Cardio", "resistance": "Resistance", "hiit": "HIIT",
            "yoga": "Yoga", "pilates": "Pilates", "mobility": "Mobility",
        }
        for k, v in (rule_pack.get("modalities") or {}).items():
            mod = mod_title.get(str(k).lower())
            cnt = self._to_int(v)
            if mod and cnt and not any((r.get("modality") == mod) for r in ex.get("frequency_rules", [])):
                ex.setdefault("frequency_rules", []).append({"modality": mod, "times_per_week": cnt})
        for k, v in (rule_pack.get("duration") or {}).items():
            mod = mod_title.get(str(k).lower())
            mins = self._to_int(v)
            if mod and mins and not any((r.get("modality") == mod) for r in ex.get("duration_rules", [])):
                ex.setdefault("duration_rules", []).append({"modality": mod, "duration_minutes": mins})
        for _, rpe_text in (rule_pack.get("intensity") or {}).items():
            if rpe_text and not ex.get("profile_attributes", {}).get("rpe_target"):
                ex["profile_attributes"]["rpe_target"] = str(rpe_text).replace("RPE ", "")
                break
        cals = rule_pack.get("calorie_targets") or []
        if cals and not ex.get("profile_attributes", {}).get("calorie_target"):
            ex["profile_attributes"]["calorie_target"] = int(max(cals))

        note_type = self._detect_note_type(original_text, ex)
        mode = "session" if note_type == "session_log" else "weekly"

        block_lines = self._extract_structured_block_lines(original_text)
        structured_sections: Dict[str, List] = {}
        if block_lines:
            structured_sections = self._parse_structured_sections(block_lines)

        if mode == "session" and structured_sections.get("all"):
            ex["exercises_mentioned"] = structured_sections["all"]
        elif mode == "weekly" and structured_sections.get("all"):
            existing_names = {e.get("name", "").lower() for e in ex.get("exercises_mentioned", [])}
            for se in structured_sections.get("all", []):
                if se.get("name", "").lower() not in existing_names:
                    ex.setdefault("exercises_mentioned", []).append(se)

        weight_note = ex.get("weight_note") or ""
        circuit_sets = str(ex.get("circuit_sets") or "")
        circuit_reps = str(ex.get("circuit_reps") or "")
        if structured_sections.get("all"):
            for se in structured_sections["all"]:
                if weight_note and not se.get("equipment"):
                    se["equipment"] = weight_note
                if circuit_sets and not se.get("sets"):
                    se["sets"] = circuit_sets
                if circuit_reps and not se.get("reps"):
                    se["reps"] = circuit_reps

        homework_exercises = list(ex.get("homework_exercises") or [])
        day_mods = self._resolve_day_mods(ex, mode)
        day_mods = RuleEnforcer.enforce_frequency(
            day_mods=day_mods,
            rules=rule_pack,
            sample_week_exists=bool(sample_map),
        )
        mandatory = self._resolve_mandatory(
            ex, day_mods, mode, structured_sections,
            homework_exercises=homework_exercises,
        )

        has_circuit_directive = bool(re.search(r"\bcircuit\b", str(original_text or ""), re.I))
        if mode != "session" and has_circuit_directive:
            resistance_days = [
                d for d, mods in day_mods.items()
                if any(m in mods for m in ["Resistance", "Upper", "Lower"])
            ]
            if resistance_days:
                pool_src = list(
                    ex.get("exercise_session_exercises") or
                    ex.get("plan_of_action_exercises") or
                    ex.get("exercises_mentioned") or []
                )
                if pool_src:
                    cir_rounds = self._to_int(ex.get("circuit_rounds")) or 3
                    cir_sets = str(ex.get("circuit_sets") or "3")
                    cir_reps = str(ex.get("circuit_reps") or "8-12")
                    circuit_payload = CircuitBuilder.build(pool_src, rounds=cir_rounds, sets=cir_sets, reps=cir_reps)
                    if circuit_payload:
                        for d in resistance_days:
                            for item in circuit_payload:
                                if not any(
                                    str(x.get("name", "")).lower() == str(item.get("name", "")).lower()
                                    for x in mandatory.get(d, [])
                                ):
                                    mandatory[d].append(dict(item))

        return {
            "extract": ex,
            "day_mods": day_mods,
            "mandatory": mandatory,
            "mode": mode,
            "rule_pack": rule_pack,
            "structured_sections": structured_sections,
            "note_type": note_type,
            "weight_note": weight_note,
            "homework_exercises": homework_exercises,
            "circuit_sets": circuit_sets,
            "circuit_reps": circuit_reps,
        }

    def _resolve_day_mods(self, ex: Dict[str, Any], mode: str) -> Dict[str, List[str]]:
        day_mods: Dict[str, List[str]] = {d: [] for d in _DAY_ORDER}
        fixed: Set[str] = set()
        rest_days: Set[str] = set()

        if ex.get("sample_week_exists") and (ex.get("schedule") or {}).get("explicit_days"):
            for day_raw, mods in (ex.get("schedule") or {}).get("explicit_days", {}).items():
                d = self._normalise_day(day_raw)
                if d:
                    day_mods[d] = list(dict.fromkeys(mods or []))
            if mode == "session":
                dominant = self._dominant_modality([m for d in _DAY_ORDER for m in day_mods[d]])
                day_mods = {d: [] for d in _DAY_ORDER}
                day_mods["Monday"] = [dominant or "Mobility"]
            return day_mods

        for day_raw, mods in (ex.get("schedule") or {}).get("explicit_days", {}).items():
            d = self._normalise_day(day_raw)
            if not d: continue
            day_mods[d] = list(dict.fromkeys(mods)) if mods else []
            fixed.add(d)
            if "Rest" in (mods or []):
                rest_days.add(d)

        for rule in (ex.get("frequency_rules") or []):
            mod = rule.get("modality")
            need = self._to_int(rule.get("times_per_week")) or 0
            if not mod or not need: continue
            have = sum(1 for d in _DAY_ORDER if mod in day_mods.get(d, []))
            for d in _DAY_ORDER:
                if have >= need: break
                if d in rest_days: continue
                if mod not in (day_mods.get(d) or []):
                    day_mods[d].append(mod)
                    have += 1

        if not any(day_mods.values()):
            n = self._to_int((ex.get("schedule") or {}).get("days_per_week")) or 3
            n = max(1, min(7, n))
            goal = str((ex.get("profile_attributes") or {}).get("goal") or "").lower()
            base = (
                ["Cardio", "Resistance", "Cardio"]
                if ("loss" in goal or "fat" in goal)
                else ["Resistance", "Upper", "Lower"]
            )
            for i, d in enumerate(_DAY_ORDER[:n]):
                day_mods[d] = [base[i % len(base)]]

        if mode == "session":
            dominant = self._dominant_modality([m for d in _DAY_ORDER for m in day_mods[d]])
            day_mods = {d: [] for d in _DAY_ORDER}
            day_mods["Monday"] = [dominant or "Mobility"]

        return day_mods

    def _resolve_mandatory(
        self,
        ex: Dict[str, Any],
        day_mods: Dict[str, List[str]],
        mode: str,
        structured_sections: Dict[str, List],
        homework_exercises: Optional[List[Dict]] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        mandatory: Dict[str, List[Dict]] = {d: [] for d in _DAY_ORDER}
        exercises = list(ex.get("exercises_mentioned") or [])
        hw_exercises = list(homework_exercises or [])
        poa_exercises = list(ex.get("plan_of_action_exercises") or [])
        session_exercises = list(ex.get("exercise_session_exercises") or [])

        def _to_entry(src: Dict, override_category: Optional[str] = None) -> Dict[str, Any]:
            return {
                "name": str(src.get("name") or "").strip(),
                "sets": str(src.get("sets") or ""),
                "reps": str(src.get("reps") or ""),
                "category": override_category or str(src.get("category") or "main"),
                "equipment": str(src.get("equipment") or ""),
                "is_daily_homework": bool(src.get("is_daily_homework", False)),
            }

        active_days = [d for d in _DAY_ORDER if day_mods.get(d) and "Rest" not in day_mods[d]]
        resistance_days = [d for d in active_days if any(
            m in ("Resistance", "Upper", "Lower", "HIIT") for m in day_mods.get(d, [])
        )]
        hiit_days = [d for d in active_days if "HIIT" in day_mods.get(d, [])]
        pure_resistance_days = [d for d in active_days if
                                 "Resistance" in day_mods.get(d, []) and
                                 "HIIT" not in day_mods.get(d, [])]
        yoga_days = [d for d in active_days if "Yoga" in day_mods.get(d, [])]

        def _place_priority(src_list: List[Dict[str, Any]], fallback_category: str = "main") -> None:
            for src in src_list:
                entry = _to_entry(src, src.get("category") or fallback_category)
                if not entry["name"]: continue
                if self._is_modality_phrase(entry["name"]): continue
                mod = self._parse_mod(str(src.get("modality") or "")) or self._infer_modality(src.get("name", ""))
                targets = [d for d in active_days if mod and mod in (day_mods.get(d) or [])]
                if not targets: targets = active_days
                day = targets[0] if targets else "Monday"
                if not any(m.get("name", "").lower() == entry["name"].lower() for m in mandatory[day]):
                    mandatory[day].append(entry)

        _place_priority(poa_exercises, "main")
        _place_priority(session_exercises, "main")

        circuit_exercises = list(structured_sections.get("all") or session_exercises or [])
        if not circuit_exercises:
            circuit_exercises = [
                e for e in exercises
                if self._infer_modality(e.get("name", "")) not in ("Yoga", "Mobility", "Cardio")
            ]

        if mode == "session":
            for e in (structured_sections.get("warmup") or []):
                mandatory["Monday"].append(_to_entry(e, "warmup"))
            for e in (structured_sections.get("main") or exercises):
                mandatory["Monday"].append(_to_entry(e, "main"))
            for e in (structured_sections.get("cooldown") or []):
                mandatory["Monday"].append(_to_entry(e, "cooldown"))
            for hw in hw_exercises[:2]:
                mandatory["Monday"].append(_to_entry(hw, "warmup"))
            return mandatory

        # Weekly mode
        # ── BUG FIX #5: circuit → ALL pure resistance days, not just first ──
        if circuit_exercises:
            circuit_target_days = pure_resistance_days if pure_resistance_days else (resistance_days + active_days)[:2]
            for day in circuit_target_days:
                for ex_src in circuit_exercises:
                    entry = _to_entry(ex_src, "main")
                    if not any(m.get("name") == entry["name"] for m in mandatory[day]):
                        mandatory[day].append(entry)

        # Homework → rotate across ALL active days (every day gets warmup items)
        n_hw = len(hw_exercises)
        for day_idx, day in enumerate(active_days):
            if n_hw == 0: break
            start = (day_idx * 1) % n_hw
            items_for_day = []
            for offset in range(min(2, n_hw)):
                hw = hw_exercises[(start + offset) % n_hw]
                items_for_day.append(hw)
            for hw in items_for_day:
                entry = _to_entry(hw, "warmup")
                if not any(m.get("name") == entry["name"] for m in mandatory[day]):
                    mandatory[day].append(entry)

        # Yoga cooldown marker on yoga days
        for day in yoga_days:
            mandatory[day].append({
                "name": "Gentle Yoga Session",
                "sets": "", "reps": "10 min",
                "category": "cooldown", "equipment": "",
                "_is_yoga_marker": True,
            })

        # Non-circuit explicitly-mentioned exercises → best-fit day
        for src in exercises:
            if any(src.get("name") == e.get("name") for e in circuit_exercises):
                continue
            entry = _to_entry(src)
            if self._is_modality_phrase(entry["name"]): continue
            mod = self._parse_mod(str(src.get("modality") or "")) or self._infer_modality(src.get("name", ""))
            targets = [d for d in _DAY_ORDER
                       if mod and mod in (day_mods.get(d) or [])
                       and "Rest" not in (day_mods.get(d) or [])]
            if not targets: targets = active_days
            day = targets[0] if targets else "Monday"
            if not any(m.get("name") == entry["name"] for m in mandatory[day]):
                mandatory[day].append(entry)

        return mandatory


    # ── STAGE 3 ───────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_bp_tuple(value: Any) -> Tuple[Optional[int], Optional[int]]:
        match = re.search(r"(\d{2,3})\s*/\s*(\d{2,3})", str(value or ""))
        if not match:
            return None, None
        return int(match.group(1)), int(match.group(2))

    def _build_clinical_context(self, resolved: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, Any]:
        extract = resolved.get("extract") or {}
        profile_attrs = extract.get("profile_attributes") or {}
        conditions: List[str] = []
        for items in (profile.get("fitness_medical_conditions") or [], profile_attrs.get("injuries") or []):
            for item in items:
                text = str(item or "").strip()
                if text:
                    conditions.append(text)

        note_text = " ".join([
            str(self._source_text or ""),
            json.dumps(extract, ensure_ascii=True),
            " ".join(conditions),
            str(profile.get("physical_limitation") or ""),
            str(profile.get("blood_pressure") or profile.get("bp") or ""),
        ]).lower()
        systolic, diastolic = self._parse_bp_tuple(profile.get("blood_pressure") or profile.get("bp"))

        def _has(pattern: str) -> bool:
            return bool(re.search(pattern, note_text, re.I))

        return {
            "conditions": sorted(set(c.lower() for c in conditions if c)),
            "note_text": note_text,
            "flags": {
                "knee_sensitive": _has(r"\bknee\b|\bacl\b|\bmeniscus\b|\bpatella\b|\bbruise[ds]?\b"),
                "avoid_floor_work": _has(
                    r"\bavoid floor\b|\bno floor\b|\bavoid mat\b|\bno mat\b|\bno kneeling\b|"
                    r"\bfloor\b|\bknee\b|\bacl\b|\bmeniscus\b|\bpatella\b|\bbruise[ds]?\b"
                ),
                "high_impact_restricted": _has(r"\bknee\b|\bacl\b|\bmeniscus\b|\bpatella\b|\bbruise[ds]?\b|\bhigh[\s\-]?impact\b"),
                "diabetes": _has(r"type\s*2\s*diabetes|type\s*ii\s*diabetes|\bt2dm\b|\btype\s*2\b"),
                "hypertension": _has(r"\bhypertension\b|\bhigh blood pressure\b") or bool(
                    (systolic is not None and systolic >= 140) or (diastolic is not None and diastolic >= 90)
                ),
            },
            "blood_pressure": {"systolic": systolic, "diastolic": diastolic},
        }

    def _medical_blacklist_patterns(self, clinical_context: Dict[str, Any], slot: str = "") -> List[str]:
        flags = clinical_context.get("flags") or {}
        patterns: List[str] = []
        if flags.get("high_impact_restricted"):
            patterns.append(
                r"jump|plyo|burpee|box\s+jump|jumping\s+jacks?|skater|high\s+knees|"
                r"sprint|hop|bounding|impact|tuck\s+jump|power\s+skip"
            )
        if flags.get("knee_sensitive"):
            patterns.append(r"kneeling|tall\s+kneeling|half\s+kneeling|deep\s+knee\s+flex")
        if flags.get("avoid_floor_work"):
            patterns.append(
                r"\bfloor\b|\bmat\b|supine|prone|kneeling|floor|side[-\s]?lying|sit[-\s]?up|crunch|"
                r"mountain\s+climber|plank|dead\s+bug|roll\s?up|get[-\s]?up"
            )
        if slot == "cooldown":
            patterns.append(r"dynamic|mobility|march|walk|bike|jog|run|high\s+knees|circles?")
        return patterns

    def _is_medically_safe_text(self, text: str, clinical_context: Dict[str, Any], slot: str = "") -> bool:
        hay = str(text or "").lower()
        for pattern in self._medical_blacklist_patterns(clinical_context, slot):
            if re.search(pattern, hay, re.I):
                return False
        return True

    def _row_is_medically_compatible(self, row: pd.Series, clinical_context: Dict[str, Any], slot: str = "") -> bool:
        blob = " | ".join([
            str(row.get("Exercise Name", "")),
            str(row.get("Tags", "")),
            str(row.get("Primary Category", "")),
            str(row.get("Physical limitations", "")),
            str(row.get("is_not_suitable_for", "")),
            str(row.get("Steps to perform", "")),
        ])
        return self._is_medically_safe_text(blob, clinical_context, slot)

    def _apply_medical_guardrails(self, df: pd.DataFrame, context: Dict[str, Any]) -> pd.DataFrame:
        return _hard_medical_exclusion(df, context, "main")

    @staticmethod
    def _exercise_usage_key(name: Any = "", unique_id: Any = "") -> str:
        uid = str(unique_id or "").strip()
        if uid and uid.lower() not in {"n/a", "none", "nan", "null"}:
            return f"id:{uid.lower()}"
        nm = str(name or "").strip().lower()
        return f"name:{nm}" if nm else ""

    @classmethod
    def _usage_key_from_row(cls, row: Optional[pd.Series]) -> str:
        if row is None:
            return ""
        return cls._exercise_usage_key(row.get("Exercise Name", ""), row.get("Unique ID", ""))

    @classmethod
    def _usage_key_from_exercise(cls, ex: Optional[Dict[str, Any]]) -> str:
        if not ex:
            return ""
        return cls._exercise_usage_key(ex.get("name", ""), ex.get("unique_id", ""))

    @classmethod
    def _filter_used_exercises(cls, df: pd.DataFrame, used_log: Dict[str, int], max_uses: int = 2) -> pd.DataFrame:
        if df is None or df.empty:
            return df
        filtered = df[~df.apply(
            lambda row: int(used_log.get(cls._usage_key_from_row(row), 0)) >= max_uses,
            axis=1,
        )]
        return filtered if not filtered.empty else df

    @staticmethod
    def _warmup_bucket_from_text(name: str, tags: str = "", body_region: str = "", benefit: str = "") -> str:
        blob = " ".join([str(name or ""), str(tags or ""), str(body_region or ""), str(benefit or "")]).lower()
        if re.search(r"walk|march|cycle|bike|step|cardio|aerobic|elliptical|butt kicks|jacks?", blob):
            return "cardio"
        if re.search(r"shoulder|chest|thoracic|upper|arm|wrist|lat|open book|cat|cow|rotation", blob):
            return "upper_stretch"
        if re.search(r"hip|hamstring|quad|glute|calf|ankle|lower|leg|knee|adductor|abductor", blob):
            return "lower_stretch"
        return "other"

    @staticmethod
    def _warmup_kind_from_row(row: pd.Series) -> str:
        tags = str(row.get("Tags", "") or "")
        if not re.search(r"warm\s*up|warmup", tags, re.I):
            return "other"
        if re.search(r"cardio", tags, re.I):
            return "cardio"
        if re.search(r"mobility | activation mobility", tags, re.I):
            return "mobility_flexibility"
        return "other"

    def _is_valid_warmup_row(self, row: pd.Series) -> bool:
        tags = str(row.get("Tags", ""))
        name = str(row.get("Exercise Name", ""))
        if not re.search(r"warm\s*up|warmup", tags, re.I):
            return False
        if re.search(r"neck\s*tilts?", name, re.I):
            return False
        if not self._is_section_safe_name("warmup", name):
            return False
        if self._warmup_kind_from_row(row) == "other":
            return False
        return True

    @staticmethod
    def _is_static_stretch_text(name: str, tags: str = "", benefit: str = "") -> bool:
        blob = " ".join([str(name or ""), str(tags or ""), str(benefit or "")]).lower()
        has_static = bool(re.search(r"stretch|pose|hold|recovery|cooldown", blob))
        has_dynamic = bool(re.search(r"march|walk|bike|jog|run|dynamic|mobility|circles?", blob))
        return has_static and not has_dynamic

    def _format_rpe(self, section: str, profile: Dict[str, Any], clinical_context: Dict[str, Any], current: Any = "") -> str:
        raw = str(current or "").strip()
        if raw:
            return raw if raw.upper().startswith("RPE") else f"RPE {raw}"
        flags = clinical_context.get("flags") or {}
        if section == "warmup":
            return "RPE 2-3"
        if section == "cooldown":
            return "RPE 1-2"
        if flags.get("diabetes") or flags.get("hypertension") or flags.get("knee_sensitive"):
            return "RPE 4-5"
        return "RPE 5-6"

    def _format_rest(self, section: str, current: Any = "") -> str:
        raw = str(current or "").strip()
        if raw:
            return raw
        if section == "warmup":
            return "15-30 sec"
        if section == "cooldown":
            return "30 sec between stretches"
        return "45-75 sec"

    def _compose_safety_cue(self, base_cue: Any = "", clinical_context: Optional[Dict[str, Any]] = None) -> str:
        context = clinical_context or self._active_clinical_context or {}
        parts: List[str] = []
        cue = str(base_cue or "").strip()
        if cue:
            parts.append(cue.rstrip("."))
        flags = context.get("flags") or {}
        if flags.get("diabetes"):
            parts.append("Monitor blood glucose; avoid exercise if BG > 280 mg/dL per clinical notes")
        if flags.get("knee_sensitive"):
            parts.append("Avoid knee pressure and stop if knee pain increases")
        if flags.get("avoid_floor_work"):
            parts.append("Keep movements off the floor and use standing or seated options")
        if flags.get("hypertension"):
            parts.append("Breathe continuously and avoid straining or breath holding")
        if not parts:
            parts.append("Maintain control; stop if pain occurs")
        return ". ".join(dict.fromkeys(p for p in parts if p)).strip() + "."

    def _adapt_exercise_entry(
        self,
        ex: Dict[str, Any],
        safe_pool: pd.DataFrame,
        section: str,
        clinical_context: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        candidate = dict(ex or {})
        name = str(candidate.get("name") or "").strip()
        if not name:
            return None
        if self._is_medically_safe_text(name, clinical_context, section):
            return candidate
        row, conf = self._fuzzy_match(name, safe_pool)
        if row is None or conf < 0.60:
            return None
        replacement = self._build_dataset_entry(row, section, "", {})
        replacement["_meta"] = {"source": "medical_semantic_replacement", "confidence": round(conf, 2)}
        return replacement

    def _find_similar_dataset_replacement(
        self,
        name: str,
        section: str,
        pool: pd.DataFrame,
        profile: Dict[str, Any],
        clinical_context: Dict[str, Any],
        week_counts: Optional[Dict[str, int]] = None,
        max_uses: int = 3,
    ) -> Optional[Dict[str, Any]]:
        if pool is None or pool.empty:
            return None
        week_counts = week_counts or {}
        safe_pool = self._apply_medical_guardrails(pool, clinical_context, section)
        safe_pool = self._filter_used_exercises(safe_pool, week_counts, max_uses=max_uses)
        if safe_pool is None or safe_pool.empty:
            return None
        source_match = self._fuzzy_match(name, safe_pool)
        source_row = source_match[0] if isinstance(source_match, tuple) else None
        source_region = str(source_row.get("Body Region", "")).lower() if source_row is not None else ""
        source_cat = str(source_row.get("Primary Category", "")).lower() if source_row is not None else ""
        source_eq = str(source_row.get("Equipments", "")).lower() if source_row is not None else ""
        target_norm = PrescriptionExerciseMatcher._semantic_normalize(name)
        best_row = None
        best_score = 0.0
        for _, row in safe_pool.iterrows():
            candidate_name = str(row.get("Exercise Name", "")).strip()
            if not candidate_name or candidate_name.lower() == str(name or "").strip().lower():
                continue
            cand_norm = PrescriptionExerciseMatcher._semantic_normalize(candidate_name)
            seq = difflib.SequenceMatcher(None, target_norm, cand_norm).ratio()
            token_a = set(re.findall(r"[a-z0-9]+", target_norm))
            token_b = set(re.findall(r"[a-z0-9]+", cand_norm))
            overlap = len(token_a & token_b) / max(1, len(token_a | token_b))
            score = (0.65 * seq) + (0.2 * overlap)
            if source_region and source_region == str(row.get("Body Region", "")).lower():
                score += 0.1
            if source_cat and source_cat == str(row.get("Primary Category", "")).lower():
                score += 0.05
            if source_eq and source_eq == str(row.get("Equipments", "")).lower():
                score += 0.05
            if section == "warmup":
                bucket_a = self._warmup_bucket_from_text(name)
                bucket_b = self._warmup_bucket_from_text(
                    row.get("Exercise Name", ""),
                    row.get("Tags", ""),
                    row.get("Body Region", ""),
                    row.get("Health benefit", ""),
                )
                if bucket_a == bucket_b:
                    score += 0.15
            if score > best_score:
                best_score = score
                best_row = row
        if best_row is None:
            return None
        replacement = self._build_dataset_entry(best_row, section, "", profile)
        replacement["_meta"] = {
            "source": "weekly_variability_replacement",
            "confidence": round(best_score, 2),
        }
        return replacement

    @staticmethod
    def _as_float(value: Any, default: float = 70.0) -> float:
        try:
            return float(value)
        except Exception:
            match = re.search(r"(\d+(?:\.\d+)?)", str(value or ""))
            return float(match.group(1)) if match else default

    def _expand_to_full_week(self, day_mods: Dict[str, List[str]], profile: Dict[str, Any], clinical_context: Dict[str, Any]) -> Dict[str, List[str]]:
        out = {day: list(day_mods.get(day) or []) for day in _DAY_ORDER}
        goal = str(profile.get("primary_goal") or "").lower()
        flags = clinical_context.get("flags") or {}

        if "diabetes" in goal:
            template = [
                ["Cardio"],
                ["Upper"],
                ["Cardio"],
                ["Lower"],
                ["Mobility"],
                ["Resistance"],
                ["Cardio"],
            ]
        else:
            template = [
                ["Cardio"],
                ["Upper"],
                ["Mobility"],
                ["Lower"],
                ["Cardio"],
                ["Resistance"],
                ["Mobility"],
            ]
        if flags.get("knee_sensitive"):
            template = [["Mobility"] if "Lower" in mods else mods for mods in template]
            template[0] = ["Cardio"]
            template[4] = ["Mobility"]

        for idx, day in enumerate(_DAY_ORDER):
            if not out.get(day):
                out[day] = list(template[idx])
        return out

    def _stage3_build_week(self, resolved: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, Any]:
        df = ExerciseFilter.apply_filters(self._dataset, profile)
        if df is None or df.empty:
            df = self._dataset
        clinical_context = self._build_clinical_context(resolved, profile)
        self._active_clinical_context = clinical_context
        df = self._apply_medical_guardrails(df, clinical_context)

        matcher = ExerciseDatasetMatcher(self._dataset, profile)
        planner = WorkoutPlanner()
        sets, reps, rpe, rest = planner.get_volume_intensity(
            profile.get("primary_goal", ""), profile.get("fitness_level", "")
        )

        day_mods  = self._expand_to_full_week(resolved["day_mods"], profile, clinical_context)
        mandatory = resolved["mandatory"]
        mode      = resolved["mode"]
        extract   = resolved["extract"]

        plans: Dict[str, Any] = {}
        global_main_used:     Set[str] = set()
        global_warmup_used:   Set[str] = set()
        global_cooldown_used: Set[str] = set()
        global_exercise_used: Set[str] = set()
        weekly_usage_counts: Dict[str, int] = defaultdict(int)
        filler_usage_counts: Dict[str, int] = defaultdict(int)
        used_exercise_log: Dict[str, int] = weekly_usage_counts

        def _seen_like(name: str) -> bool:
            n = str(name or "").lower().strip()
            if not n: return False
            for u in global_exercise_used:
                if difflib.SequenceMatcher(None, n, u).ratio() > 0.92:
                    return True
            return False

        def _mark_seen(name: str) -> None:
            n = str(name or "").lower().strip()
            if n:
                global_exercise_used.add(n)

        resistance_days = [
            d for d in _DAY_ORDER
            if any(m in ("Resistance", "Upper", "Lower", "HIIT") for m in (day_mods.get(d) or []))
            and "Rest" not in (day_mods.get(d) or [])
        ]
        circuit_day = resistance_days[0] if resistance_days else None

        # ── Pre-enrich ALL mandatory exercises via matcher ──
        enriched_mandatory_map: Dict[str, List[Dict]] = {}
        for day, raw_list in mandatory.items():
            enriched_mandatory_map[day] = matcher.match_many(raw_list)

        # ── Cap prescribed exercise repetition: max 2-3 days per exercise ──
        # Count how many active days exist, then distribute prescribed exercises
        # so no single exercise appears on more than MAX_PRESCRIBED_DAYS days.
        _active_days = [d for d in _DAY_ORDER if enriched_mandatory_map.get(d)]
        _MAX_DAYS = min(3, max(2, len(_active_days) // 2))

        # Count how many days each main exercise appears across the week
        _ex_day_count: Dict[str, int] = defaultdict(int)
        for _d, _elist in enriched_mandatory_map.items():
            for _e in _elist:
                if str(_e.get("category", "main")).lower() not in ("warmup", "cooldown"):
                    _ex_day_count[str(_e.get("name", "")).lower()] += 1

        # For exercises exceeding the cap, distribute evenly across the week
        # using a stride-based rotation so exercises spread instead of front-loading.
        _ex_kept: Dict[str, int] = defaultdict(int)
        if _active_days:
            _stride = max(1, len(_active_days) // _MAX_DAYS)
        else:
            _stride = 1
        for _d_idx, _d in enumerate(_active_days):
            if _d not in enriched_mandatory_map:
                continue
            filtered = []
            for _e in enriched_mandatory_map[_d]:
                _cat = str(_e.get("category", "main")).lower()
                _name_key = str(_e.get("name", "")).lower()
                if _cat in ("warmup", "cooldown"):
                    filtered.append(_e)  # warmup/cooldown: always keep
                elif _ex_day_count.get(_name_key, 1) > _MAX_DAYS:
                    # Keep only on every _stride-th day to spread evenly
                    if _ex_kept[_name_key] < _MAX_DAYS and (_d_idx % _stride == 0 or _ex_kept[_name_key] == 0):
                        filtered.append(_e)
                        _ex_kept[_name_key] += 1
                else:
                    filtered.append(_e)
                    _ex_kept[_name_key] += 1
            enriched_mandatory_map[_d] = filtered

        for day in _DAY_ORDER:
            mods = day_mods.get(day) or []
            if not mods: continue

            if "Rest" in mods:
                plans[day] = {
                    "day_name": day, "main_workout_category": "Rest Day",
                    "warmup": [], "main_workout": [], "cooldown": [],
                    "safety_notes": ["Rest day explicitly prescribed."],
                }
                continue

            # Primary modality selection
            if "Yoga" in mods and all(m not in mods for m in ["Resistance", "Upper", "Lower", "HIIT"]):
                primary_mod = "Yoga"
            else:
                _MOD_PRIORITY = ["Resistance", "HIIT", "Upper", "Lower", "Cardio", "Yoga", "Mobility", "Pilates"]
                primary_mod = next((m for m in _MOD_PRIORITY if m in mods), mods[0])

            # ── BUG FIX #3: Cardio is always present on dual-modality days ──
            # Identify whether this day has both Cardio AND a strength modality
            has_cardio = "Cardio" in mods
            has_strength = any(m in mods for m in ["Resistance", "Upper", "Lower"])

            pool = self._pool_for_modalities(df, [primary_mod])
            if pool is None or pool.empty:
                if primary_mod in ("Yoga", "Mobility", "Pilates"):
                    yoga_pool = df[df["Primary Category"].str.contains(
                        "Flex|Yoga|Stretch|Mobil|Pilates", case=False, na=False
                    )]
                    pool = yoga_pool if not yoga_pool.empty else df
                else:
                    pool = df
            pool = self._apply_medical_guardrails(pool, clinical_context, "main")
            pool = self._filter_used_exercises(pool, used_exercise_log, max_uses=3)
            pool = DatasetRanker.rank(pool, profile, modality=primary_mod, target_slot="main")

            df_tagged_warmup = pool[
                pool["Tags"].str.contains(r"warm\s*up", case=False, na=False, regex=True)
            ]
            df_tagged_cooldown = pool[
                pool["Tags"].str.contains(r"cool\s*down|stretch", case=False, na=False, regex=True)
            ]
            if df_tagged_warmup.empty:
                df_tagged_warmup = df[
                    df["Tags"].str.contains(r"warm\s*up", case=False, na=False, regex=True)
                ]
            if df_tagged_cooldown.empty:
                df_tagged_cooldown = df[
                    df["Tags"].str.contains(r"cool\s*down|stretch", case=False, na=False, regex=True)
                ]
            df_tagged_warmup = self._apply_medical_guardrails(df_tagged_warmup, clinical_context, "warmup")
            df_tagged_cooldown = self._apply_medical_guardrails(df_tagged_cooldown, clinical_context, "cooldown")
            df_tagged_warmup = self._filter_used_exercises(df_tagged_warmup, used_exercise_log, max_uses=3)
            df_tagged_cooldown = self._filter_used_exercises(df_tagged_cooldown, used_exercise_log, max_uses=3)
            df_tagged_warmup   = DatasetRanker.rank(df_tagged_warmup, profile, modality=primary_mod, target_slot="warmup")
            df_tagged_cooldown = DatasetRanker.rank(df_tagged_cooldown, profile, modality=primary_mod, target_slot="cooldown")

            _HI_INTENSITY_PAT = (
                r"push[\s\-]?up|pushup|squat\s+thrust|plank\s+jack|burpee|"
                r"switch\s+kick|jumping|jump\s+lunge|jump\s+squat|box\s+jump|"
                r"sprint|thruster|mountain\s+climber|star\s+jump|tuck\s+jump|"
                r"speed\s+skater|lateral\s+shuffle|high\s+knees|"
                r"plank\s+thruster|leg\s+flutter|plank\s+to\s+squat|"
                r"staggered\s+push|plyom|impact|pop\s+squat"
            )
            if not df_tagged_cooldown.empty:
                safe_cd = df_tagged_cooldown[
                    ~df_tagged_cooldown["Exercise Name"].str.contains(
                        _HI_INTENSITY_PAT, flags=re.I, regex=True, na=False
                    )
                ]
                if not safe_cd.empty:
                    df_tagged_cooldown = safe_cd
            tagged = {"warmup": df_tagged_warmup, "cooldown": df_tagged_cooldown}

            rule_pack = resolved.get("rule_pack") or {}

            # ── BUG FIX #3: Cardio payload for dual-modality (Cardio+Resistance) days ──
            if primary_mod == "HIIT":
                proto = HIITProtocolInterpreter.interpret(self._source_text, extract.get("protocols") or [])
                raw_m = list(enriched_mandatory_map.get(day, []))
                note_main = [
                    str(x.get("name", "")).strip() for x in raw_m
                    if str(x.get("category", "main")).lower() == "main" and str(x.get("name", "")).strip()
                ]
                default_hiit = ["Jump Squats", "Push-Ups", "Mountain Climbers", "Dumbbell Thrusters"]
                selected_hiit: List[str] = []
                for n in note_main + default_hiit:
                    if not n:
                        continue
                    if any(difflib.SequenceMatcher(None, n.lower(), s.lower()).ratio() > 0.92 for s in selected_hiit):
                        continue
                    selected_hiit.append(n)
                    if len(selected_hiit) >= 4:
                        break
                hiit_payloads: List[Dict[str, Any]] = []
                rounds = int(proto.get("rounds") or 4)
                work_s = int(proto.get("work") or 40)
                rest_s = int(proto.get("rest") or 20)
                for nm in selected_hiit:
                    hiit_payloads.append({
                        "name": nm,
                        "sets": str(rounds),
                        "reps": f"{work_s} sec work / {rest_s} sec rest",
                        "category": "main",
                        "_meta": {"source": "hiit_protocol", "confidence": 0.99},
                        "_is_session_payload": True,
                    })
                enriched_mandatory_map[day] = hiit_payloads + [x for x in raw_m if str(x.get("category", "main")).lower() != "main"]

            if primary_mod == "Yoga":
                yoga_poses = [
                    {"name": "Cat Cow", "sets": "1", "reps": "8", "category": "main"},
                    {"name": "Child Pose", "sets": "1", "reps": "60 sec", "category": "main"},
                    {"name": "Downward Dog", "sets": "1", "reps": "45 sec", "category": "main"},
                    {"name": "Cobra", "sets": "1", "reps": "45 sec", "category": "main"},
                    {"name": "Warrior Pose", "sets": "1", "reps": "45 sec each side", "category": "main"},
                ]
                raw_m = enriched_mandatory_map.get(day, [])
                names_lower = {str(x.get("name", "")).lower() for x in raw_m}
                for yp in yoga_poses:
                    if yp["name"].lower() not in names_lower:
                        raw_m.append(dict(yp))
                enriched_mandatory_map[day] = raw_m
                pool = pool[
                    pool["Primary Category"].str.contains("Yoga|Stretch|Flex|Mobility|Pilates", case=False, na=False) |
                    pool["Tags"].str.contains("Yoga|Mobility|Stretch|Warmup|Cooldown", case=False, na=False)
                ]
                if pool.empty:
                    pool = df[df["Primary Category"].str.contains("Yoga|Stretch|Flex|Mobility|Pilates", case=False, na=False)]

            # Duration → max_main
            dur_mins = None
            for rule in (extract.get("duration_rules") or []):
                if rule.get("modality") in mods:
                    dur_mins = self._to_int(rule.get("duration_minutes"))
                    break
            max_main = (
                4 if (dur_mins and dur_mins < 25) else
                5 if not dur_mins or dur_mins < 40 else
                6 if dur_mins < 55 else 6
            )
            if primary_mod == "Cardio":
                max_main = max(5, min(7, max_main))
            if primary_mod == "Yoga":
                max_main = min(max_main, 5)

            raw_mandatory = enriched_mandatory_map.get(day, [])
            non_main = [e for e in raw_mandatory if str(e.get("category", "main")).lower() in ("warmup", "cooldown")]
            main_only = [e for e in raw_mandatory if str(e.get("category", "main")).lower() not in ("warmup", "cooldown")]
            raw_mandatory = non_main + main_only

            enriched_mandatory = [self._stage4_enrich(e, pool) for e in raw_mandatory]

            prescribed_main_count = len([e for e in enriched_mandatory if e.get("category") == "main"])

            # ── BUG FIX #5: max_main must accommodate the full prescribed circuit ──
            if prescribed_main_count > max_main:
                max_main = prescribed_main_count

            suppress_autofill = (
                (day == circuit_day and prescribed_main_count >= 1) or
                prescribed_main_count >= 4
            )
            # Only suppress autofill for Cardio/Yoga/HIIT if there are ACTUAL prescribed
            # exercises on this specific day — not just because the modality is Cardio.
            # This prevents empty main workouts on days where prescribed exercises were
            # rotated away by the repetition cap.
            if primary_mod in ("Cardio", "Yoga", "HIIT") and prescribed_main_count >= 1:
                suppress_autofill = True
            if suppress_autofill:
                max_main = max(prescribed_main_count, 4)
                if primary_mod in ("Cardio", "HIIT"):
                    max_main = max(5, min(7, max_main))

            # For dual-modality Cardio+Resistance, always ensure enough slots for both
            if has_cardio and has_strength:
                # Cardio session (1) + circuit exercises
                max_main = max(max_main, prescribed_main_count)
                suppress_autofill = True

            day_seed = sum((i + 1) * ord(ch) for i, ch in enumerate(day))
            day_composer = WorkoutComposer(ExerciseSelector(random_seed=day_seed))
            day_composer._dataset = self._dataset
            day_composer._active_clinical_context = clinical_context

            circuit_sets = resolved.get("circuit_sets", "")
            circuit_reps = resolved.get("circuit_reps", "")
            day_sets = circuit_sets if (day in resistance_days and circuit_sets) else sets
            day_reps = circuit_reps if (day in resistance_days and circuit_reps) else reps

            params = {
                "sets": day_sets, "reps": day_reps, "rpe": rpe, "rest": rest,
                "max_main": max(1, max_main),
                "weight": self._as_float(profile.get("weight_kg", 70), 70.0),
                "_profile": profile,
                "weekly_usage": weekly_usage_counts,
                "filler_usage": filler_usage_counts,
                "shadow_boxing_used": False,
            }

            try:
                built = day_composer.build_day(
                    day_name=day,
                    day_type=primary_mod,
                    df_base=pool,
                    df_tagged=tagged,
                    params=params,
                    global_main_used=global_main_used,
                    global_warmup_used=global_warmup_used,
                    global_cooldown_used=global_cooldown_used,
                    mandatory_exercises=enriched_mandatory,
                    is_minimal_plan=True,
                    structured_mode=suppress_autofill,
                )
            except Exception as build_err:
                logger.error("build_day failed for %s: %s", day, build_err, exc_info=True)
                built = {
                    "day_name": day, "main_workout_category": primary_mod,
                    "warmup": [], "main_workout": [], "cooldown": [],
                    "safety_notes": [f"Build error: {build_err}"],
                }

            if suppress_autofill:
                built["main_workout"] = built.get("main_workout", [])[:max_main]

            proto_notes = self._protocol_notes(extract.get("protocols") or [])
            if proto_notes:
                existing = list(dict.fromkeys(built.get("safety_notes") or []))
                built["safety_notes"] = list(dict.fromkeys(existing + proto_notes))
            if primary_mod == "HIIT":
                built.setdefault("safety_notes", [])
                if "Rest between rounds: 60 sec" not in built["safety_notes"]:
                    built["safety_notes"].append("Rest between rounds: 60 sec")

            weight_note = resolved.get("weight_note", "")
            if weight_note and any(m in mods for m in ("Resistance", "Upper", "Lower", "HIIT")):
                wn = f"Use {weight_note} for prescribed circuit exercises"
                if wn not in (built.get("safety_notes") or []):
                    built.setdefault("safety_notes", []).append(wn)

            plans[day] = {
                "day_name": day,
                "main_workout_category": ("HIIT Circuit" if primary_mod == "HIIT" else str(built.get("main_workout_category") or "/".join(mods))),
                "warmup":       [self._stage4_final_format(e) for e in (built.get("warmup") or [])],
                "main_workout": [self._stage4_final_format(e) for e in (built.get("main_workout") or [])],
                "cooldown":     [self._stage4_final_format(e) for e in (built.get("cooldown") or [])],
                "safety_notes": [str(n) for n in (built.get("safety_notes") or [])],
            }
            plans[day] = self._postprocess_day_plan(
                day_plan=plans[day],
                profile=profile,
                pool=pool,
                modality=primary_mod,
                clinical_context=clinical_context,
                week_counts=weekly_usage_counts,
            )
            day_frequency_counter: Set[str] = set()
            for sec in ("warmup", "main_workout", "cooldown"):
                for ex in plans[day].get(sec, []) or []:
                    usage_key = self._usage_key_from_exercise(ex)
                    if usage_key and usage_key not in day_frequency_counter:
                        weekly_usage_counts[usage_key] += 1
                        day_frequency_counter.add(usage_key)

        if not plans:
            plans = {"Monday": {
                "day_name": "Monday", "main_workout_category": "General Fitness",
                "warmup": [], "main_workout": [], "cooldown": [],
                "safety_notes": ["No scheduled days found — fallback plan."],
            }}
        for day in _DAY_ORDER:
            if day not in plans:
                plans[day] = {
                    "day_name": day,
                    "main_workout_category": "Rest Day",
                    "warmup": [],
                    "main_workout": [],
                    "cooldown": [],
                    "safety_notes": ["Recovery day to support the weekly prescription."],
                }
        return plans


    # ── STAGE 4 ───────────────────────────────────────────────────────────────

    def _stage4_enrich(self, ex: Dict[str, Any], pool: pd.DataFrame) -> Dict[str, Any]:
        name = str(ex.get("name") or "").strip()
        if not name:
            return ex

        # ── BUG FIX #4: preserve session-payload entries untouched ──
        if ex.get("_is_session_payload"):
            return ex

        cached_row  = ex.get("_matched_row") if ex.get("_matched_row") is not None else ex.get("matched_row")
        cached_conf = float(ex.get("match_confidence") or 0.0)
        cached_src  = str(ex.get("match_source") or "")

        if cached_row is not None and cached_conf >= 0.60:
            row, conf, source = cached_row, cached_conf, cached_src
        else:
            row, conf = self._fuzzy_match(name, pool)
            if row is None or conf < 0.60:
                row, conf = self._fuzzy_match(name, self._dataset)
            source = "dataset_match" if row is not None else "doctor_note_fallback"

        matched_name = str(row.get("Exercise Name")) if row is not None else name

        category = str(ex.get("category") or "main")
        if row is not None:
            tags = str(row.get("Tags", "")).lower()
            if "cooldown" in tags or "cool down" in tags or "stretch" in tags:
                category = "cooldown"
            elif "warmup" in tags or "warm up" in tags or "mobility" in tags:
                category = "warmup"
            elif "main" in tags:
                category = "main"

        prescription = _resolve_prescription_fields(
            row=row,
            fallback=ex.get("side") or ex.get("time") or ex.get("reps") or "",
        )

        enriched = {
            "name": matched_name,
            "unique_id": str(row.get("Unique ID", "N/A") if row is not None else ex.get("unique_id") or "N/A"),
            "sets": str(ex.get("sets") or ""),
            "reps": prescription.get("reps"),
            "time": prescription.get("time"),
            "side": prescription.get("side"),
            "category": category,
            "_meta": {"source": source, "confidence": round(conf, 2)},
        }

        equipment = str(ex.get("equipment") or "").strip()
        if equipment:
            enriched["equipment"] = equipment

        if ex.get("is_daily_homework"):
            enriched["is_daily_homework"] = True

        return enriched

    def _stage4_final_format(self, ex: Dict[str, Any]) -> Dict[str, Any]:
        name = str(ex.get("name") or "").strip()
        meta = ex.get("_meta") if isinstance(ex.get("_meta"), dict) else {}
        row = None

        if "source" not in meta or "confidence" not in meta:
            row, conf = self._fuzzy_match(name, self._dataset)
            meta = {
                "source": "dataset_match" if row is not None else "doctor_note_fallback",
                "confidence": round(conf, 2) if row is not None else 0.35,
            }
            if row is not None:
                ex.setdefault("benefit", str(row.get("Health benefit", "")))
                ex.setdefault("safety_cue", str(row.get("Safety cue", "")))
                steps_raw = str(row.get("Steps to perform", ""))
                ex.setdefault("steps", [s.strip() for s in steps_raw.splitlines() if s.strip()])
                ex.setdefault("met_value", float(row.get("MET value", 3.0) or 3.0))

        equipment_str = str(ex.get("equipment") or "").strip()
        if not equipment_str and row is not None:
            equipment_str = str(row.get("Equipments", "")).strip()

        prescription = _resolve_prescription_fields(
            row=row,
            fallback=ex.get("side") or ex.get("time") or ex.get("reps") or "",
        )

        result = {
            "name": name,
            "unique_id": str(ex.get("unique_id") or (row.get("Unique ID", "N/A") if row is not None else "N/A") or "N/A"),
            "sets": str(ex.get("sets") or ""),
            "reps": prescription.get("reps"),
            "time": prescription.get("time"),
            "side": prescription.get("side"),
            "benefit": str(ex.get("benefit") or "General conditioning"),
            "steps": ex.get("steps") if isinstance(ex.get("steps"), list) else [],
            "intensity_rpe": self._format_rpe(
                str(ex.get("category") or "main"),
                {},
                self._active_clinical_context,
                ex.get("intensity_rpe") or (row.get("RPE", "") if row is not None else ""),
            ),
            "rest": self._format_rest(
                str(ex.get("category") or "main"),
                ex.get("rest") or (row.get("Rest", "") if row is not None else ""),
            ),
            "safety_cue": self._compose_safety_cue(
                ex.get("safety_cue") or (row.get("Safety cue", "") if row is not None else ""),
                self._active_clinical_context,
            ),
            "met_value": float(ex.get("met_value") or 3.0),
            "_meta": {
                "source": str(meta.get("source") or "doctor_note_fallback"),
                "confidence": float(meta.get("confidence") or 0.35),
            },
        }
        if equipment_str:
            result["equipment"] = equipment_str
        if ex.get("is_daily_homework"):
            result["is_daily_homework"] = True
        # Preserve session payload marker
        if ex.get("_is_session_payload"):
            result["_is_session_payload"] = True
        return result

    @staticmethod
    def _token_overlap_score(a: str, b: str) -> float:
        ta = {t for t in re.findall(r"[a-z0-9]+", str(a).lower()) if len(t) > 1}
        tb = {t for t in re.findall(r"[a-z0-9]+", str(b).lower()) if len(t) > 1}
        if not ta or not tb: return 0.0
        return len(ta & tb) / float(len(ta | tb))

    @staticmethod
    def _partial_ratio(a: str, b: str) -> float:
        s1, s2 = str(a).lower(), str(b).lower()
        if len(s1) > len(s2): s1, s2 = s2, s1
        if not s1: return 0.0
        best = 0.0
        for i in range(0, max(1, len(s2) - len(s1) + 1)):
            best = max(best, difflib.SequenceMatcher(None, s1, s2[i:i + len(s1)]).ratio())
        return best

    def _fuzzy_match(self, name: str, df: pd.DataFrame) -> Tuple[Optional[pd.Series], float]:
        if df is None or df.empty: return None, 0.0
        semantic_aliases = {
            "rows bent over": "bent over row",
            "bent over rows": "bent over row",
            "shoulder presses": "shoulder press",
            "bicep curls": "bicep curl",
            "squats": "squat",
        }
        def _norm(text: str) -> str:
            out = str(text or "").lower().strip()
            out = semantic_aliases.get(out, out)
            out = re.sub(r"\b(db|bb)\b", "dumbbell", out)
            out = re.sub(r"[^a-z0-9\s]+", " ", out)
            out = re.sub(r"\brows\b", "row", out)
            out = re.sub(r"\bpresses\b", "press", out)
            out = re.sub(r"\bcurls\b", "curl", out)
            out = re.sub(r"\bsquats\b", "squat", out)
            out = re.sub(r"\s+", " ", out).strip()
            return out
        best_idx, best_score = None, 0.0
        name_lower = _norm(name)
        for idx, row in df.iterrows():
            candidate = _norm(str(row.get("Exercise Name", "")))
            seq = difflib.SequenceMatcher(None, name_lower, candidate).ratio()
            part = self._partial_ratio(name_lower, candidate)
            overlap = self._token_overlap_score(name_lower, candidate)
            anchored = 0.10 if any(tok in candidate for tok in name_lower.split()[:2]) else 0.0
            score = (0.40 * seq) + (0.25 * part) + (0.25 * overlap) + anchored
            if score > best_score:
                best_score, best_idx = score, idx
        if best_idx is None or best_score < 0.60:
            return None, 0.0
        return df.loc[best_idx], round(best_score, 2)

    # ── HELPERS ───────────────────────────────────────────────────────────────

    @staticmethod
    def _is_modality_phrase(name: str) -> bool:
        t = str(name or "").strip().lower()
        if not t: return False
        modality_words = {
            "cardio", "resistance", "strength", "training", "workout", "session",
            "yoga", "pilates", "mobility", "hiit", "upper", "lower", "full body",
            "cardiovascular", "aerobic",
        }
        if t in modality_words: return True
        toks = [x for x in re.findall(r"[a-z]+", t) if x]
        if not toks: return False
        movement_verbs = {
            "squat", "lunge", "press", "row", "curl", "plank", "bridge", "dog",
            "pose", "stretch", "push", "pull", "raise", "twist", "hinge", "walk",
            "run", "bike", "jog", "jump", "reach", "hold", "extension", "flexion"
        }
        has_movement = any(v in t for v in movement_verbs)
        mostly_modality = sum(1 for tok in toks if tok in modality_words) >= max(1, len(toks) - 1)
        return mostly_modality and not has_movement

    @staticmethod
    def _empty_extract() -> Dict[str, Any]:
        return {
            "profile_attributes": {
                "weight_kg": None, "age": None, "goal": None,
                "injuries": [], "equipment": [],
            },
            "schedule": {"days_per_week": None, "explicit_days": {}},
            "frequency_rules": [],
            "duration_rules": [],
            "protocols": [],
            "exercises_mentioned": [],
        }

    def _normalise_extract(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        out = self._empty_extract()
        if not isinstance(raw, dict): return out

        pa = raw.get("profile_attributes") or {}
        out["profile_attributes"]["weight_kg"] = self._to_float(pa.get("weight_kg"))
        out["profile_attributes"]["age"]       = self._to_int(pa.get("age"))
        out["profile_attributes"]["goal"]      = str(pa.get("goal")).strip() if pa.get("goal") else None
        out["profile_attributes"]["injuries"]  = self._listify(pa.get("injuries"))
        out["profile_attributes"]["equipment"] = self._listify(pa.get("equipment"))

        sch = raw.get("schedule") or {}
        out["schedule"]["days_per_week"] = self._to_int(sch.get("days_per_week"))
        explicit = sch.get("explicit_days") or {}
        if isinstance(explicit, dict):
            for day_raw, mods_raw in explicit.items():
                day = self._normalise_day(day_raw)
                if not day: continue
                mods_str = mods_raw if isinstance(mods_raw, str) else ", ".join(str(m) for m in (mods_raw or []))
                mods = self._parse_mods(mods_str)
                if mods:
                    out["schedule"]["explicit_days"][day] = mods

        for r in (raw.get("frequency_rules") or []):
            if not isinstance(r, dict): continue
            mod = self._parse_mod(r.get("modality"))
            cnt = self._to_int(r.get("times_per_week"))
            if mod and cnt and cnt > 0:
                out["frequency_rules"].append({"modality": mod, "times_per_week": cnt})

        for r in (raw.get("duration_rules") or []):
            if not isinstance(r, dict): continue
            mod  = self._parse_mod(r.get("modality"))
            mins = self._to_int(r.get("duration_minutes"))
            if mod and mins and mins > 0:
                out["duration_rules"].append({"modality": mod, "duration_minutes": mins})

        for p in (raw.get("protocols") or []):
            if isinstance(p, dict) and p.get("type"):
                out["protocols"].append(p)

        _PURE_MODALITY_NAMES: Set[str] = {
            "cardio", "resistance", "resistance training", "strength training",
            "yoga", "yoga class", "pilates", "hiit", "mobility", "stretching",
            "upper body", "lower body", "full body", "weight training",
            "cardio and resistance training", "cardio and yoga class",
            "cardio and resistance", "resistance and cardio",
        }

        for ex in (raw.get("exercises_mentioned") or []):
            if not isinstance(ex, dict) or not ex.get("name"): continue
            name = self._clean_name(str(ex["name"]).strip())
            if not self._is_valid_exercise_name(name): continue
            if self._is_modality_phrase(name): continue
            if name.lower() not in str(self._source_text or "").lower(): continue
            if name.lower() in _PURE_MODALITY_NAMES: continue
            if (len(name.split()) <= 2 and
                    self._parse_mod(name) is not None and
                    not any(k in name.lower() for k in ["curl", "press", "row", "lift",
                                                         "squat", "lunge", "plank", "push",
                                                         "pull", "bridge", "crunch", "raise"])):
                continue
            conf = self._score_confidence(name, str(ex.get("sets") or ""), str(ex.get("reps") or ""), True)
            if conf < 0.75: continue
            out["exercises_mentioned"].append({
                "name":        name,
                "sets":        str(ex.get("sets") or ""),
                "reps":        str(ex.get("reps") or ""),
                "modality":    self._parse_mod(ex.get("modality")) or self._infer_modality(name),
                "_confidence": conf,
            })

        for pass_key in [
            "homework_exercises", "weight_note", "circuit_sets", "circuit_reps",
            "sample_week_exists", "plan_of_action_exercises", "exercise_session_exercises",
        ]:
            if raw.get(pass_key):
                out[pass_key] = raw[pass_key]

        return out

    def _hybrid_regex_extract_exercises(self, text: str) -> List[Dict[str, Any]]:
        t = str(text or "")
        lines = [l.strip() for l in t.splitlines() if l.strip()]
        keywords = r"squat|press|row|curl|plank|stretch|pose|dog|bridge|lunge"
        narrative_block = re.compile(r"\b(reported|tolerated|continues|scheduled)\b", re.I)
        out: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        for line in lines:
            if narrative_block.search(line): continue
            if not re.search(keywords, line, re.I): continue
            m = re.search(
                r"([A-Za-z][A-Za-z '\-/]{2,50}?)\s*(?:[-:]?\s*(\d+)\s*[xX]\s*([0-9]+(?:[-/][0-9]+)?))?$",
                line, re.I,
            )
            if not m: continue
            name = self._clean_name(m.group(1))
            if not self._is_valid_exercise_name(name): continue
            low = name.lower()
            if low in seen: continue
            seen.add(low)
            out.append({
                "name": name, "sets": str(m.group(2) or ""),
                "reps": str(m.group(3) or ""),
                "modality": self._infer_modality(name),
                "_confidence": 0.86, "category": "main",
            })
        return out

    def _extract_exercise_from_line(self, line: str, structured: bool = False) -> Optional[Dict[str, Any]]:
        raw = str(line or "").strip().lstrip("-*0123456789. ").strip()
        raw = re.split(
            r"\s+-\s+|,\s*(?=sit|hold|then|to feel|stretch|lay|press|pedal)|\b(?:then|next)\b",
            raw, maxsplit=1, flags=re.I,
        )[0].strip()
        low = raw.lower()
        if any(bad in low for bad in [
            "reported", "continues", "tolerated", "scheduled", "goal", "appointment",
            "monitor", "steps", "water", "calories"
        ]):
            return None
        if self._is_modality_phrase(raw): return None
        if not raw or self._is_non_exercise_line(raw) or self._is_protocol_line(raw): return None
        if not self._has_valid_movement_verb(raw) and not self._is_pilates_name(raw): return None
        if len(raw.split()) > 12 and not self._is_pilates_name(raw): return None

        m = re.search(
            r"^(.+?)\s*[-:]\s*(\d+)\s*(?:sets?|x)\s*(?:of)?\s*"
            r"([0-9]+(?:\s*[-/]\s*[0-9]+)?(?:\s*(?:reps?|sec|seconds|min|minutes))?)\s*$",
            raw, flags=re.I,
        )
        if m: return self._make_ex(m.group(1), m.group(2), m.group(3), structured)

        m = re.search(r"^(\d+)\s*[xX]\s*([0-9]+(?:\s*[-/]\s*[0-9]+)?)\s+(.+?)\s*$", raw, flags=re.I)
        if m: return self._make_ex(m.group(3), m.group(1), m.group(2), structured)

        m = re.search(
            r"^(.+?)\s+(\d+)\s*[xX]\s*([0-9]+(?:\s*[-/]\s*[0-9]+)?"
            r"(?:\s*(?:reps?|sec|seconds|min|minutes))?)\s*$",
            raw, flags=re.I,
        )
        if m: return self._make_ex(m.group(1), m.group(2), m.group(3), structured)

        cand = self._clean_name(raw)
        first = (cand.split()[0] if cand.split() else "").strip().lower()
        if (cand[:1].isupper() or first in _EXERCISE_VERBS or structured or self._is_pilates_name(cand)) and self._is_valid_exercise_name(cand):
            conf = self._score_confidence(cand, "", "", structured)
            if conf >= 0.75:
                return {"name": cand, "sets": "", "reps": "", "modality": self._infer_modality(cand),
                        "_confidence": conf, "category": "main"}
        return None

    def _make_ex(self, name_raw: str, sets_raw: str, reps_raw: str, structured: bool) -> Optional[Dict[str, Any]]:
        name = self._clean_name(name_raw.strip())
        if not self._is_valid_exercise_name(name): return None
        conf = self._score_confidence(name, sets_raw.strip(), reps_raw.strip(), structured)
        if conf < 0.75: return None
        return {"name": name, "sets": sets_raw.strip(), "reps": reps_raw.strip(),
                "modality": self._infer_modality(name), "_confidence": conf, "category": "main"}

    def _extract_structured_block_lines(self, text: str) -> List[str]:
        lines = [l.strip() for l in str(text or "").splitlines() if l.strip()]
        start = None
        for i, line in enumerate(lines):
            ll = line.lower()
            if "exercise session" in ll or ll.startswith("warm-up") or ll.startswith("warm up"):
                start = i
                break
        if start is None:
            for i, line in enumerate(lines):
                if self._looks_like_sets_only(line) or re.search(r"\d+\s*[xX]\s*\d+", line):
                    start = i
                    break
        if start is None: return []
        end = len(lines)
        for i in range(start, len(lines)):
            ll = lines[i].lower()
            if "plan of action" in ll:
                end = i
                break
            already_have = i > start + 2
            is_narrative = (
                len(lines[i].split()) > 18 and "." in lines[i] and
                any(w in ll for w in _NARRATIVE_BLOCKLIST)
            )
            if already_have and is_narrative:
                end = i
                break
        return [l for l in lines[start:end] if l and "plan of action" not in l.lower()]

    def _parse_structured_sections(self, lines: List[str]) -> Dict[str, List[Dict[str, Any]]]:
        sections: Dict[str, List] = {"warmup": [], "main": [], "cooldown": [], "all": []}
        section = "main"
        for line in self._merge_exercise_lines(lines):
            ll = line.lower()
            if "warm-up" in ll or "warm up" in ll: section = "warmup"; continue
            if "cooldown" in ll or "cool-down" in ll: section = "cooldown"; continue
            if "main workout" in ll: section = "main"; continue
            ex = self._extract_exercise_from_line(line, structured=True)
            if not ex: continue
            ex["category"] = section
            sections[section].append(ex)
            sections["all"].append(ex)
        if sections["all"] and not sections["main"] and not sections["warmup"]:
            sections["main"] = list(sections["all"])
        return sections

    def _detect_note_type(self, text: str, extracted: Dict[str, Any]) -> str:
        t = str(text or "").lower()
        has_weekdays    = bool(re.search(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", t))
        has_freq        = bool(re.search(r"\b\d+\s*[xX]\s*(?:per\s+week|/\s*week)", t))
        has_schedule    = bool((extracted.get("schedule") or {}).get("explicit_days"))
        has_session_hdr = bool(re.search(r"exercise session\s*:", t))
        has_poa         = "plan of action" in t
        ex_count        = len(extracted.get("exercises_mentioned") or [])
        if has_weekdays or has_freq or has_schedule: return "weekly_prescription"
        if has_session_hdr and has_poa: return "weekly_prescription"
        if has_session_hdr and ex_count >= 1: return "session_log"
        if ex_count >= 3 and not has_weekdays: return "session_log"
        return "weekly_prescription"

    def _strict_validate(self, data: Dict[str, Any]) -> bool:
        if not isinstance(data, dict) or set(data.keys()) != _REQUIRED_TOP_KEYS: return False
        sch = data.get("schedule")
        if not isinstance(sch, dict) or not isinstance(sch.get("explicit_days"), dict): return False
        if not isinstance(data.get("exercises_mentioned"), list): return False
        for ex in data["exercises_mentioned"]:
            if not isinstance(ex, dict): return False
            name = str(ex.get("name") or "").strip()
            if not self._is_valid_exercise_name(name): return False
            try:
                conf = float(ex.get("_confidence", 0.0))
            except (TypeError, ValueError):
                return False
            if not (0.0 <= conf <= 1.0) or conf < 0.75: return False
        return True

    @staticmethod
    def _has_valid_movement_verb(text: str) -> bool:
        t = str(text or "").lower()
        verbs = ["squat", "lunge", "press", "row", "curl", "plank", "stretch",
                 "pose", "flow", "bridge", "raise", "dog", "cat", "cow"]
        return any(v in t for v in verbs)

    def _is_valid_exercise_name(self, name: str) -> bool:
        n = self._clean_name(str(name or "").strip())
        if not n or len(n) < 3: return False
        if set(n) <= {":", "-", " "}: return False
        if n.count(":") > 1: return False
        if len(n) > 80 and not self._is_pilates_name(n): return False
        if n.count(",") > 2: return False
        if self._contains_date_or_time(n): return False
        words = n.split()
        if not (1 <= len(words) <= 14) and not self._is_pilates_name(n): return False
        if self._is_non_exercise_line(n): return False
        if any(b in n.lower() for b in _NARRATIVE_BLOCKLIST): return False
        if re.search(r"\b(per|/)\s*week\b", n, flags=re.I): return False
        if re.search(r"^\d+\s*(?:x|times?)\b", n, flags=re.I): return False
        if self._contains_person_name(n): return False
        if self._is_modality_phrase(n): return False
        if not self._has_valid_movement_verb(n) and not self._is_pilates_name(n): return False
        if not self._is_pilates_name(n) and not any(k in n.lower() for k in _MOVEMENT_KEYS): return False
        return True

    def _score_confidence(self, name: str, sets: str, reps: str, structured_position: bool) -> float:
        if not self._is_valid_exercise_name(name): return 0.0
        if self._is_pilates_name(name): return 0.95
        if re.search(r"\d", sets) and re.search(r"\d", reps): return 1.0
        return 0.9 if structured_position else 0.5

    def _pool_for_modalities(self, df: pd.DataFrame, mods: List[str]) -> pd.DataFrame:
        if df is None or df.empty: return df
        pools: List[pd.DataFrame] = []
        for m in mods:
            if m == "Cardio":
                mask = df["Primary Category"].str.contains(
                    r"Cardio|Aerobic|HIIT|Cardiovascular|Endurance", case=False, na=False, regex=True)
                pool = df[mask]
                if pool.empty:
                    pool = df[df["Tags"].str.contains(r"Cardio|Aerobic|HIIT", case=False, na=False, regex=True)]
                pools.append(pool)
            elif m == "HIIT":
                mask = df["Primary Category"].str.contains(
                    r"HIIT|Cardio|Condition|Circuit|Interval", case=False, na=False, regex=True)
                pool = df[mask]
                if pool.empty:
                    pool = df[df["Tags"].str.contains(r"HIIT|Cardio|Circuit", case=False, na=False, regex=True)]
                pools.append(pool)
            elif m == "Resistance":
                mask = df["Primary Category"].str.contains(
                    r"Strength|Hypertrophy|Power|Resistance|Muscular|Functional|Weight", case=False, na=False, regex=True)
                pool = df[mask]
                if pool.empty:
                    pool = df[df["Tags"].str.contains(r"Strength|Resistance|Weight", case=False, na=False, regex=True)]
                pools.append(pool)
            elif m == "Upper":
                mask = (
                    df["Body Region"].str.contains(r"Upper|Arm|Chest|Back|Shoulder", case=False, na=False, regex=True) |
                    df["Primary Category"].str.contains(r"Upper|Strength", case=False, na=False, regex=True)
                )
                pools.append(df[mask])
            elif m == "Lower":
                mask = (
                    df["Body Region"].str.contains(r"Lower|Leg|Hip|Glute|Hamstring|Quad|Calf", case=False, na=False, regex=True) |
                    df["Primary Category"].str.contains(r"Lower|Strength", case=False, na=False, regex=True)
                )
                pools.append(df[mask])
            elif m in ("Yoga", "Pilates"):
                mask = df["Primary Category"].str.contains(
                    r"Flex|Yoga|Stretch|Pilates|Mobility|Balance", case=False, na=False, regex=True)
                pool = df[mask]
                if pool.empty:
                    pool = df[df["Tags"].str.contains(r"Yoga|Pilates|Stretch|Flex", case=False, na=False, regex=True)]
                pools.append(pool)
            elif m == "Mobility":
                mask = df["Primary Category"].str.contains(
                    r"Mobility|Flex|Stretch|Balance|Stability|Recovery", case=False, na=False, regex=True)
                pool = df[mask]
                if pool.empty:
                    pool = df[df["Tags"].str.contains(r"Mobil|Stretch|Flex|Balance", case=False, na=False, regex=True)]
                pools.append(pool)
            else:
                pools.append(df)
        if not pools: return df
        non_empty = [p for p in pools if not p.empty]
        if not non_empty: return df
        combined = pd.concat(non_empty).drop_duplicates(subset=["Exercise Name"])
        return combined if not combined.empty else df

    def _parse_mod(self, raw: Any) -> Optional[str]:
        t = str(raw or "").strip().lower()
        if not t: return None
        t_nodots = re.sub(r"\.(?=[a-z])", "", t).rstrip(".")
        if t_nodots in _MOD_MAP: return _MOD_MAP[t_nodots]
        if t in _MOD_MAP: return _MOD_MAP[t]
        for key_src in (t_nodots, t):
            for k, v in _MOD_MAP.items():
                if k in key_src: return v
        return None

    def _parse_mods(self, raw: Any) -> List[str]:
        out: List[str] = []
        for part in re.split(r"[/,]|\bor\b|\band\b|&", str(raw or ""), flags=re.I):
            m = self._parse_mod(part.strip())
            if m and m not in out:
                out.append(m)
        return out

    def _infer_modality(self, text: str) -> Optional[str]:
        t = str(text or "").lower()
        if any(k in t for k in ["roll", "hundred", "criss", "mat", "leg stretch", "pilates",
                                  "single straight leg stretch", "side lying leg series"]):
            return "Pilates"
        if any(k in t for k in ["pose", "flow", "warrior", "cobra", "child", "downward dog"]):
            return "Yoga"
        if any(k in t for k in ["cat cow", "open books", "thoracic", "bird dog"]):
            return "Mobility"
        if self._is_pilates_name(t): return "Pilates"
        if any(k in t for k in _PILATES_KEYS): return "Pilates"
        if any(k in t for k in _YOGA_KEYS): return "Yoga"
        if any(k in t for k in _MOBILITY_KEYS): return "Mobility"
        return self._parse_mod(t)

    def _dominant_modality(self, mods: List[str]) -> Optional[str]:
        filtered = [m for m in mods if m != "Rest"]
        if not filtered: return None
        return Counter(filtered).most_common(1)[0][0]

    def _merge_exercise_lines(self, lines: List[str]) -> List[str]:
        merged: List[str] = []
        i = 0
        while i < len(lines):
            cur = str(lines[i] or "").strip()
            nxt = str(lines[i + 1] or "").strip() if i + 1 < len(lines) else ""
            if cur and nxt and self._looks_like_name_only(cur) and self._looks_like_sets_only(nxt):
                merged.append(f"{cur} {nxt}")
                i += 2
            else:
                merged.append(cur)
                i += 1
        return merged

    def _is_non_exercise_line(self, line: str) -> bool:
        ll = str(line or "").strip().lower()
        if not ll: return True
        return any(re.search(p, ll, flags=re.I) for p in _EXCLUDE_PATTERNS)

    @staticmethod
    def _is_protocol_line(line: str) -> bool:
        ll = str(line or "").lower().strip()
        has_protocol = bool(re.search(
            r"\b(tabata|interval|rounds?|seconds?\s*on|seconds?\s*off|circuit|each exercise)\b", ll,
        ))
        has_movement = any(k in ll for k in _MOVEMENT_KEYS)
        if has_protocol and not has_movement: return True
        if re.match(r"^\d+\s*(?:lbs?|kg|pounds?)\s*$", ll): return True
        return False

    @staticmethod
    def _is_pilates_name(text: str) -> bool:
        t = str(text or "").lower()
        return any(k in t for k in [
            "roll", "hundred", "criss", "leg", "side lying", "cobra", "stretch",
            "pose", "open books", "mat", "single straight leg stretch", "side lying leg series",
        ])

    @staticmethod
    def _looks_like_name_only(line: str) -> bool:
        l = str(line or "").strip()
        return bool(l) and not re.search(r"\d", l)

    @staticmethod
    def _looks_like_sets_only(line: str) -> bool:
        l = str(line or "").strip().lower()
        return bool(re.search(r"^\d+\s*[xX]\s*\d+(\s*(reps?|sec|seconds|min|minutes))?$", l) or
                    re.search(r"^\d+\s*sets?\s*of\s*\d+", l))

    def _parse_protocol(self, line: str) -> Optional[Dict[str, Any]]:
        l = str(line or "")
        is_tabata = bool(re.search(r"\btabata\b|20\s*seconds?\s*on", l, flags=re.I))
        return {
            "type": "Tabata" if is_tabata else "Interval",
            "rounds": self._pick_int(l, r"(\d+)\s*round"),
            "work_seconds": (self._pick_int(l, r"(\d+)\s*(?:sec|seconds)\s*work") or
                             self._pick_int(l, r"(\d+)\s*(?:sec|seconds)\s*on")),
            "rest_seconds": (self._pick_int(l, r"(\d+)\s*(?:sec|seconds)\s*rest") or
                             self._pick_int(l, r"(\d+)\s*(?:sec|seconds)\s*off")),
        }

    def _protocol_notes(self, protocols: List[Dict[str, Any]]) -> List[str]:
        notes: List[str] = []
        for p in (protocols or []):
            bits = [str(p.get("type") or "Protocol")]
            if p.get("rounds"):       bits.append(f"rounds={p['rounds']}")
            if p.get("work_seconds"): bits.append(f"work={p['work_seconds']}s")
            if p.get("rest_seconds"): bits.append(f"rest={p['rest_seconds']}s")
            notes.append(" ".join(bits))
        return list(dict.fromkeys(notes))

    def _is_generic_title_like(self, name: str) -> bool:
        n = str(name or "").strip().lower()
        if not n: return True
        if any(d in n for d in ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]):
            return True
        # ── BUG FIX #1: NEVER block session/HIIT/cardio payload names ──
        # These are legitimate prescribed workout entries, not generic titles
        _SESSION_PAYLOAD_WHITELIST = [
            "cardio session", "hiit session", "interval session", "tabata session",
            "yoga session", "pilates session", "cardio and resistance",
        ]
        if any(n.startswith(prefix) for prefix in _SESSION_PAYLOAD_WHITELIST):
            return False
        bad_phrases = [
            "stretch hips and back", "plan of action",
        ]
        if any(b in n for b in bad_phrases): return True
        movement_keys = ["squat", "press", "row", "curl", "plank", "stretch", "pose",
                         "dog", "bridge", "lunge", "walk", "cycle", "run"]
        if len(n.split()) > 5 and not any(k in n for k in movement_keys): return True
        return False

    @staticmethod
    def _is_section_safe_name(section: str, name: str) -> bool:
        n = str(name or "").lower()
        if section == "warmup":
            disallowed = bool(re.search(
                r"burpee|jump|sprint|squat\s+jump|thruster|push[\s\-]?up|pushup|plyometric|heavy|deadlift",
                n, flags=re.I
            ))
            allowed = bool(re.search(
                r"cat|cow|bird\s*dog|mobility|activation|hip|shoulder|ankle|thoracic|"
                r"stretch|pose|flow|roll|circle|bridge|march|walk|step|jacks?|"
                r"butt\s*kicks|rotation|arm\s*circles?|ankle\s*circles?|thoracic\s*rotation|"
                r"heel\s*digs?|punch|torso\s*rotation|trunk\s*rotation|wall\s*slide|chest\s*opener",
                n, flags=re.I
            ))
            return allowed and not disallowed
        if section == "cooldown":
            return not bool(re.search(
                r"burpee|jump|thruster|high\s+knees|sprint|mountain\s+climber|"
                r"push[\s\-]?up|pushup|hip\s*circles?",
                n, flags=re.I
            ))
        return True

    @staticmethod
    def _is_dataset_sourced(ex: Dict[str, Any]) -> bool:
        src = str((ex.get("_meta") or {}).get("source") or "").lower()
        return any(k in src for k in ["dataset", "exact", "fuzzy", "synonym", "token_overlap", "partial"])

    def _build_dataset_entry(self, row: pd.Series, section: str, modality: str,
                              profile: Dict[str, Any]) -> Dict[str, Any]:
        sets = "1" if section == "cooldown" else str(row.get("Sets", "") or ("1" if section != "main_workout" else "3"))
        reps = _resolve_reps_time_or_side(row, (
            "10-15 reps" if section == "warmup" else ("Hold 30s" if section == "cooldown" else "8-12")))
        clinical_context = self._active_clinical_context or {}
        return {
            "name": str(row.get("Exercise Name", "")),
            "unique_id": str(row.get("Unique ID", "N/A") or "N/A"),
            "sets": sets, "reps": reps,
            "benefit": str(row.get("Health benefit", "") or "General conditioning"),
            "steps": [s.strip() for s in str(row.get("Steps to perform", "")).splitlines() if s.strip()],
            "intensity_rpe": self._format_rpe(section, profile, clinical_context, row.get("RPE", "")),
            "rest": self._format_rest(section, row.get("Rest", "")),
            "safety_cue": self._compose_safety_cue(row.get("Safety cue", ""), clinical_context),
            "met_value": float(row.get("MET value", 3.0) or 3.0),
            "equipment": str(row.get("Equipments", "") or ""),
            "_meta": {"source": "dataset_ranked_fill", "confidence": 0.95},
        }

    def _fallback_warmup_entry(self, bucket: str, profile: Dict[str, Any]) -> Dict[str, Any]:
        templates = {
            "cardio": [
                {
                    "name": "March in Place",
                    "benefit": "Gently raises heart rate and prepares the body for exercise",
                    "steps": [
                        "Stand tall with feet hip-width apart.",
                        "March slowly in place while swinging the arms naturally.",
                        "Keep the movement light and pain-free."
                    ],
                },
                {
                    "name": "Seated March",
                    "benefit": "Elevates circulation with low joint stress",
                    "steps": [
                        "Sit upright near the front of a chair.",
                        "Lift one knee at a time in a steady marching rhythm.",
                        "Move the arms naturally while keeping posture tall."
                    ],
                },
                {
                    "name": "Step Touch",
                    "benefit": "Provides a gentle cardio warm-up without impact",
                    "steps": [
                        "Stand tall with soft knees.",
                        "Step side to side at an easy pace.",
                        "Keep the movement smooth and pain-free."
                    ],
                },
            ],
            "mobility_flexibility": [
                {
                    "name": "Standing Shoulder Stretch",
                    "benefit": "Improves upper-body mobility before training",
                    "steps": [
                        "Stand upright with relaxed shoulders.",
                        "Bring one arm across the chest and support it with the other arm.",
                        "Hold gently without forcing the shoulder."
                    ],
                },
                {
                    "name": "Seated Torso Rotation",
                    "benefit": "Improves gentle spinal mobility before exercise",
                    "steps": [
                        "Sit tall near the front of a chair with feet flat.",
                        "Rotate gently to each side through a comfortable range.",
                        "Keep the movement slow and controlled."
                    ],
                },
                {
                    "name": "Standing Hamstring Stretch",
                    "benefit": "Reduces lower-body stiffness before exercise",
                    "steps": [
                        "Stand tall and place one heel slightly forward.",
                        "Hinge at the hips until a light stretch is felt.",
                        "Keep the spine long and the stretch gentle."
                    ],
                },
                {
                    "name": "Seated Ankle Circles",
                    "benefit": "Prepares the ankles and lower legs for activity",
                    "steps": [
                        "Sit tall in a chair with one foot lifted slightly.",
                        "Make slow circles at the ankle in both directions.",
                        "Repeat on the other side without forcing the motion."
                    ],
                },
            ],
        }
        variants = templates[bucket]
        used = sum(1 for name, count in (profile.get("_week_counts") or {}).items() if name in {
            v["name"].lower() for v in variants
        } for _ in range(count))
        chosen = variants[used % len(variants)]
        return {
            "name": chosen["name"],
            "unique_id": "N/A",
            "sets": "1",
            "reps": "30-45 sec",
            "benefit": chosen["benefit"],
            "steps": chosen["steps"],
            "intensity_rpe": self._format_rpe("warmup", profile, self._active_clinical_context),
            "rest": self._format_rest("warmup", ""),
            "equipment": "Bodyweight",
            "safety_cue": self._compose_safety_cue("", self._active_clinical_context),
            "met_value": 2.5,
            "_meta": {"source": "warmup_fallback_template", "confidence": 0.6},
        }

    def _apply_medical_guardrails(self, df: pd.DataFrame, context: Dict[str, Any], slot_type: str = "main") -> pd.DataFrame:
        return _hard_medical_exclusion(df, context, slot_type)

    def _workoutcomposer_get_rotated_exercise(
        self,
        pool,
        week_counts,
        seen_today,
        clinical_context=None,
        slot="main",
    ):
        return _select_rotated_dataset_row(
            self,
            pool,
            week_counts,
            seen_today,
            clinical_context,
            slot,
        )

    def _postprocess_day_plan(self, day_plan: Dict[str, Any], profile: Dict[str, Any],
                               pool: pd.DataFrame, modality: str,
                               clinical_context: Optional[Dict[str, Any]] = None,
                               week_counts: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
        """
        REPETITION & VARIETY GUARD: Implements Weekly Rotation and Semantic Similarity checks.
        """
        clinical_context = clinical_context or self._active_clinical_context or {}
        freq = defaultdict(int, {str(k).lower().strip(): int(v) for k, v in (week_counts or {}).items()})
        seen_today = set()

        def _get_tiered_match(slot_pool, region_patterns=None, slot_type="main"):
            if slot_pool is None or slot_pool.empty:
                return None

            if region_patterns:
                pattern = '|'.join(region_patterns)
                regional_df = slot_pool[slot_pool["Body Region"].str.contains(pattern, case=False, na=False)]
                if not regional_df.empty:
                    slot_pool = regional_df

            row = _select_rotated_dataset_row(
                self,
                slot_pool,
                freq,
                seen_today,
                clinical_context,
                slot_type,
            )
            if row is None:
                return None

            seen_today.add(str(row["Exercise Name"]).strip())
            return self._build_dataset_entry(row, slot_type, modality, profile)

        # --- WARMUP: 1 Pulse Raiser + 2 Regional Mobility ---
        final_warmup = []
        pulse_pool = self._dataset[self._dataset["Tags"].str.contains(r"warm\s*up", case=False, na=False, regex=True)]
        
        # Slot 1: Cardio (Pulse Raiser)
        cardio_ex = _get_tiered_match(pulse_pool[pulse_pool["Primary Category"].str.contains("cardio", case=False, na=False)], slot_type="warmup")
        if not cardio_ex: cardio_ex = _get_tiered_match(pulse_pool, slot_type="warmup") # Relaxation
        if cardio_ex: 
            cardio_ex["reps"] = "1-2 minutes"
            final_warmup.append(cardio_ex)

        # Slot 2 & 3: Upper/Lower Mobility
        for region in [["Upper", "Shoulder"], ["Lower", "Hip"]]:
            mob_ex = _get_tiered_match(pulse_pool, region_patterns=region, slot_type="warmup")
            if mob_ex: final_warmup.append(mob_ex)

        for ex in final_warmup: ex["sets"] = "1"

        # --- MAIN WORKOUT: Volume Target 6 ---
        final_main = []
        main_pool = self._dataset[self._dataset['Tags'].str.contains('main work out', case=False, na=False)]
        for _ in range(6):
            ex = _get_tiered_match(main_pool, slot_type="main")
            if ex: final_main.append(ex)

        # --- COOLDOWN: MET Guard (<= 3.0) ---
        final_cooldown = []
        cd_pool = self._dataset[self._dataset['Tags'].str.contains(r'cool\s*down|stretch', case=False, na=False, regex=True)]
        cd_pool = cd_pool[pd.to_numeric(cd_pool['MET value'], errors='coerce').fillna(2.0) <= 3.0]
        
        for reg in [["Upper"], ["Lower"], ["Core"]]:
            cd_ex = _get_tiered_match(cd_pool, region_patterns=reg, slot_type="cooldown")
            if cd_ex:
                cd_ex["sets"], cd_ex["reps"] = "1", "Hold 30s"
                final_cooldown.append(cd_ex)

        day_plan.update({"warmup": final_warmup, "main_workout": final_main, "cooldown": final_cooldown})
        return day_plan
        
    @staticmethod
    def _merge_profile(user_profile: Dict[str, Any], extracted_attrs: Dict[str, Any]) -> Dict[str, Any]:
        p = dict(user_profile or {})
        ea = extracted_attrs or {}
        if ea.get("weight_kg") is not None and not p.get("weight_kg"): p["weight_kg"] = ea["weight_kg"]
        if ea.get("age") is not None and not p.get("age"): p["age"] = ea["age"]
        if ea.get("goal") and not p.get("primary_goal"): p["primary_goal"] = ea["goal"]
        if ea.get("injuries") and not p.get("fitness_medical_conditions"): p["fitness_medical_conditions"] = ea["injuries"]
        if ea.get("equipment") and not p.get("available_equipment"): p["available_equipment"] = ea["equipment"]
        return p

    @staticmethod
    def _clean_name(text: str) -> str:
        raw = str(text or "").strip().strip("-").strip()
        q = re.search(r'["\']([^"\']{2,80})["\']', raw)
        if q: raw = q.group(1).strip()
        raw = re.sub(r"^\s*pilates\s+", "", raw, flags=re.I)
        raw = re.sub(r"\([^)]*\)", "", raw).strip()
        raw = re.split(r"\s+-\s+|\b(?:then|next|to feel a stretch)\b", raw, maxsplit=1, flags=re.I)[0].strip()
        if "," in raw: raw = raw.split(",", 1)[0].strip()
        return re.sub(r"\s+", " ", raw).strip()

    @staticmethod
    def _normalise_day(raw: Any) -> Optional[str]:
        t = str(raw or "").strip().lower()
        for d in _DAY_ORDER:
            if d.lower() == t: return d
        return None

    @staticmethod
    def _to_int(v: Any) -> Optional[int]:
        try:
            return None if v in (None, "") else int(float(str(v)))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_float(v: Any) -> Optional[float]:
        try:
            return None if v in (None, "") else float(str(v))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _listify(v: Any) -> List[str]:
        if v is None: return []
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return [x.strip() for x in re.split(r"[,;/]", str(v)) if x.strip()]

    @staticmethod
    def _pick_int(text: str, pattern: str) -> Optional[int]:
        m = re.search(pattern, text, flags=re.I)
        return int(m.group(1)) if m else None

    @staticmethod
    def _pick_float(text: str, pattern: str) -> Optional[float]:
        m = re.search(pattern, text, flags=re.I)
        return float(m.group(1)) if m else None

    def _collect_person_names(self, text: str) -> Set[str]:
        names: Set[str] = set()
        t = str(text or "")
        for pat in [
            r"\bname\s*[:\-]\s*([A-Za-z][A-Za-z'\-]+(?:\s+[A-Za-z][A-Za-z'\-]+)?)",
            r"\bpatient\s*[:\-]\s*([A-Za-z][A-Za-z'\-]+(?:\s+[A-Za-z][A-Za-z'\-]+)?)",
        ]:
            for m in re.finditer(pat, t, flags=re.I):
                for token in m.group(1).split():
                    names.add(token.lower())
                names.add(m.group(1).strip().lower())
        lines = [l.strip() for l in t.splitlines() if l.strip()]
        if lines:
            first = lines[0]
            if re.match(r"^[A-Z][a-zA-Z\-']+(?:\s+[A-Z][a-zA-Z\-']+){0,2}$", first):
                for token in first.split():
                    names.add(token.lower())
                names.add(first.lower())
        return names

    def _contains_person_name(self, name: str) -> bool:
        if not self._detected_person_names: return False
        n = str(name or "").lower()
        if n in self._detected_person_names: return True
        return any(tok in self._detected_person_names for tok in re.split(r"\s+", n) if tok)

    @staticmethod
    def _contains_date_or_time(text: str) -> bool:
        t = str(text or "")
        if re.search(r"\b\d{1,2}[:.]\d{2}\s*(am|pm)?\b", t, flags=re.I): return True
        if re.search(r"\b\d{1,2}[/-]\d{1,2}([/-]\d{2,4})?\b", t): return True
        if re.search(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\b", t, flags=re.I): return True
        return False

    @staticmethod
    def _extract_json_from_text(text: str) -> Optional[Dict[str, Any]]:
        cleaned = re.sub(r"```json|```", "", str(text or ""), flags=re.I)
        s, e = cleaned.find("{"), cleaned.rfind("}")
        if s < 0 or e <= s: return None
        try:
            return json.loads(cleaned[s: e + 1])
        except json.JSONDecodeError:
            return None


# ============ LAYER 7: PRESCRIPTION EXERCISE MATCHER ============
class PrescriptionExerciseMatcher:
    """
    Priority-1 dataset-matching layer that resolves prescribed exercise names
    to concrete dataset rows BEFORE any AI generation is attempted.

    Matching pipeline (priority order):
      1. Exact match          (case-insensitive)
      2. Synonym / alias      (built-in table)
      3. SequenceMatcher fuzzy (threshold >= 0.72)
      4. Partial-substring    (threshold >= 0.72)
      5. Token-overlap Jaccard (threshold >= 0.55)
      6. No match             (AI gap-fill kicks in)
    """

    _SYNONYMS: Dict[str, str] = {
        "cat/cow": "Cat/Cow", "cat and cow": "Cat/Cow", "cat cow": "Cat/Cow",
        "bird-dog": "Bird Dog", "bird dog": "Bird Dog",
        "childs pose": "Child's Pose", "child pose": "Child's Pose", "child's pose": "Child's Pose",
        "downward dog": "Downward Dog", "down dog": "Downward Dog", "down-dog": "Downward Dog",
        "plank to down-dog": "Plank to Down-dog", "plank to downdog": "Plank to Down-dog",
        "press ups": "Push-Ups", "press-ups": "Push-Ups",
        "cobras": "Cobra Stretch", "cobra": "Cobra Stretch",
        "knee hugs": "Knee to Chest Stretch", "knee into chest": "Knee to Chest Stretch",
        "both knees hug": "Knee to Chest Stretch", "open books": "Open Books",
        "body squat": "Bodyweight Squat", "bodyweight squat": "Bodyweight Squat",
        "band pull-aparts": "Band Pull-Aparts", "band pull aparts": "Band Pull-Aparts",
        "bench rows": "Bent Over Row", "bench rows incline": "Incline Dumbbell Row",
        "rows bent over": "Bent Over Row", "bent over rows": "Bent Over Row",
        "row bent over": "Bent Over Row", "bent over db row": "Bent Over Row",
        "bicep curls": "Bicep Curl", "bicep curl": "Bicep Curl",
        "tricep extension": "Tricep Extension", "tricep extensions": "Tricep Extension",
        "shoulder press": "Shoulder Press", "overhead press": "Overhead Press",
        "chest press": "Chest Press", "hip bridge": "Glute Bridge", "glute bridge": "Glute Bridge",
        "hip thrust": "Hip Thrust", "back extension cobras": "Back Extension",
        "back extensions": "Back Extension",
        "brisk walk": "Brisk Walking", "brisk outdoor walking": "Brisk Walking",
        "treadmill": "Treadmill Walking", "stationary bike": "Stationary Cycling",
        "elliptical": "Elliptical Trainer", "arc trainer": "Elliptical Trainer",
        "spinning": "Stationary Cycling", "water aerobics": "Water Aerobics",
        "dance aerobics": "Dance Aerobics", "jumping jacks": "Jumping Jacks",
        "quad stretch": "Standing Quad Stretch", "stretch quads": "Standing Quad Stretch",
        "hamstring stretch": "Standing Hamstring Stretch",
        "hip flexor stretch": "Hip Flexor Stretch", "pigeon pose": "Pigeon Pose",
        "tabata": "Tabata Interval", "hiit": "HIIT Circuit", "interval training": "Interval Training",
    }

    _WARMUP_TAGS   = re.compile(r"warmup|warm.?up|mobility|warm\s+up", re.I)
    _COOLDOWN_TAGS = re.compile(r"cooldown|cool.?down|stretch|recovery|cool\s+down", re.I)
    _HI_INTENSITY  = re.compile(
        r"push[\s\-]?up|pushup|burpee|thruster|jump|jacks?|high\s*knees|"
        r"squat(\s|$)|lunge|mountain\s+climber|plank\s+jack|shadow\s+boxing|"
        r"punch|sprint|skater|handstand|pop\s+squat|switch\s+kick", re.I
    )

    def __init__(self, dataset: pd.DataFrame, profile: Optional[Dict[str, Any]] = None):
        self._full_df = dataset if dataset is not None and not dataset.empty else pd.DataFrame()
        if profile and not self._full_df.empty:
            filtered = ExerciseFilter.apply_filters(self._full_df, profile)
            self._df = filtered if (filtered is not None and not filtered.empty) else self._full_df
        else:
            self._df = self._full_df
        self._name_lower_index: Dict[str, int] = {}
        self._semantic_index: Dict[str, int] = {}
        if not self._df.empty:
            for idx, row in self._df.iterrows():
                n = str(row.get("Exercise Name", "")).strip().lower()
                if n:
                    self._name_lower_index[n] = idx
                    semantic_key = self._semantic_normalize(n)
                    if semantic_key and semantic_key not in self._semantic_index:
                        self._semantic_index[semantic_key] = idx

    @staticmethod
    def _semantic_normalize(text: str) -> str:
        out = str(text or "").lower().strip()
        out = re.sub(r"\brows\b", "row", out)
        out = re.sub(r"\bpresses\b", "press", out)
        out = re.sub(r"\bcurls\b", "curl", out)
        out = re.sub(r"\bsquats\b", "squat", out)
        out = re.sub(r"\bdb\b", "dumbbell", out)
        out = re.sub(r"[^a-z0-9\s]+", " ", out)
        out = re.sub(r"\s+", " ", out).strip()
        return out

    def match(self, name: str) -> Dict[str, Any]:
        name = str(name or "").strip()
        if not name: return self._no_match(name)
        r = self._exact(name)
        if r["confidence"] >= 1.0: return r
        r = self._synonym(name)
        if r["confidence"] >= 0.95: return r
        r = self._semantic_exact(name)
        if r["confidence"] >= 0.93: return r
        r = self._fuzzy(name)
        if r["confidence"] >= 0.72: return r
        r = self._partial(name)
        if r["confidence"] >= 0.72: return r
        r = self._token(name)
        if r["confidence"] >= 0.55: return r
        return self._no_match(name)

    def match_many(self, exercises: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for ex in exercises:
            ex = dict(ex)
            # Preserve session payloads untouched — they are not dataset exercises
            if ex.get("_is_session_payload"):
                out.append(ex)
                continue
            m = self.match(str(ex.get("name") or ""))
            ex["matched_row"]      = m["row"]
            ex["match_confidence"] = m["confidence"]
            ex["match_source"]     = m["source"]
            ex["resolved_slot"]    = m["slot"]
            if m["row"] is not None and m["confidence"] >= 0.72:
                ex["name"] = m["name"]
                row = m["row"]
                if not ex.get("sets"):    ex["sets"]      = str(row.get("Sets", "") or "")
                if not any(ex.get(k) for k in ("reps", "time", "side")):
                    rx = _resolve_prescription_fields(row=row, fallback="")
                    ex["reps"] = rx.get("reps")
                    ex["time"] = rx.get("time")
                    ex["side"] = rx.get("side")
                if not ex.get("equipment"): ex["equipment"] = str(row.get("Equipments", "") or "")
            out.append(ex)
        return out

    def slot_for(self, name: str) -> str:
        m = self.match(name)
        return m["slot"]

    def filter_pool(self, slot: str, used: Optional[Set[str]] = None,
                    n: int = 8, seed: Optional[int] = None) -> pd.DataFrame:
        if self._df.empty: return pd.DataFrame()
        rng = random.Random(seed)
        used_lower = {s.lower() for s in (used or set())}
        if slot == "warmup":
            pool = self._df[self._df["Tags"].str.contains(
                r"Warmup|Mobility|Warm.?Up", case=False, na=False, regex=True)]
        elif slot == "cooldown":
            mask = self._df["Tags"].str.contains(
                r"Cooldown|Stretch|Cool.?Down|Recovery", case=False, na=False, regex=True)
            pool = self._df[mask]
            if not pool.empty:
                safe = pool[~pool["Exercise Name"].str.contains(self._HI_INTENSITY, regex=True, na=False)]
                pool = safe if not safe.empty else pool
        else:
            wc_mask = self._df["Tags"].str.contains(
                r"Warmup|Cooldown|Mobility|Stretch|Warm.?Up|Cool.?Down",
                case=False, na=False, regex=True)
            pool = self._df[~wc_mask]
        if pool.empty: pool = self._df
        avail = pool[~pool["Exercise Name"].str.lower().isin(used_lower)]
        if avail.empty: avail = pool
        rows = avail.to_dict("records")
        rng.shuffle(rows)
        return pd.DataFrame(rows[:n]) if rows else pd.DataFrame()

    def _slot_from_row(self, row: pd.Series) -> str:
        if row is None: return "main"
        tags = str(row.get("Tags", "")).lower()
        if self._WARMUP_TAGS.search(tags): return "warmup"
        if self._COOLDOWN_TAGS.search(tags): return "cooldown"
        return "main"

    def _make_result(self, name: str, row: Optional[pd.Series],
                     conf: float, source: str) -> Dict[str, Any]:
        canonical = str(row.get("Exercise Name", name)).strip() if row is not None else name
        return {"name": canonical, "row": row, "confidence": round(conf, 3),
                "source": source, "slot": self._slot_from_row(row)}

    def _no_match(self, name: str) -> Dict[str, Any]:
        return {"name": name, "row": None, "confidence": 0.0, "source": "no_match", "slot": "main"}

    def _exact(self, name: str) -> Dict[str, Any]:
        idx = self._name_lower_index.get(name.lower())
        if idx is not None:
            return self._make_result(name, self._df.loc[idx], 1.0, "exact")
        return self._no_match(name)

    def _synonym(self, name: str) -> Dict[str, Any]:
        canonical = self._SYNONYMS.get(name.lower())
        if canonical:
            idx = self._name_lower_index.get(canonical.lower())
            if idx is not None:
                return self._make_result(canonical, self._df.loc[idx], 0.95, "synonym")
            if not self._full_df.empty:
                for idx2, row in self._full_df.iterrows():
                    if str(row.get("Exercise Name", "")).lower() == canonical.lower():
                        return self._make_result(canonical, row, 0.90, "synonym_full_ds")
        return self._no_match(name)

    def _semantic_exact(self, name: str) -> Dict[str, Any]:
        semantic_key = self._semantic_normalize(name)
        idx = self._semantic_index.get(semantic_key)
        if idx is not None:
            return self._make_result(name, self._df.loc[idx], 0.93, "semantic_exact")
        if not self._full_df.empty:
            for idx2, row in self._full_df.iterrows():
                candidate = self._semantic_normalize(str(row.get("Exercise Name", "")))
                if candidate and candidate == semantic_key:
                    return self._make_result(name, row, 0.91, "semantic_exact_full_ds")
        return self._no_match(name)

    def _fuzzy(self, name: str) -> Dict[str, Any]:
        if not self._name_lower_index: return self._no_match(name)
        nl = self._semantic_normalize(name)
        best_idx, best_score = None, 0.0
        for n_l, idx in self._name_lower_index.items():
            cand = self._semantic_normalize(n_l)
            seq = difflib.SequenceMatcher(None, nl, cand).ratio()
            overlap = len(set(nl.split()) & set(cand.split())) / max(1, len(set(nl.split()) | set(cand.split())))
            s = (0.8 * seq) + (0.2 * overlap)
            if s > best_score:
                best_score, best_idx = s, idx
        if best_idx is not None and best_score >= 0.72:
            return self._make_result(name, self._df.loc[best_idx], best_score, "fuzzy")
        return self._no_match(name)

    def _partial(self, name: str) -> Dict[str, Any]:
        if not self._name_lower_index: return self._no_match(name)
        s1 = self._semantic_normalize(name)
        best_idx, best_score = None, 0.0
        for n_l, idx in self._name_lower_index.items():
            cand = self._semantic_normalize(n_l)
            short, long_ = (s1, cand) if len(s1) <= len(cand) else (cand, s1)
            if not short: continue
            score = max(
                difflib.SequenceMatcher(None, short, long_[i:i + len(short)]).ratio()
                for i in range(max(1, len(long_) - len(short) + 1))
            )
            if score > best_score:
                best_score, best_idx = score, idx
        if best_idx is not None and best_score >= 0.72:
            return self._make_result(name, self._df.loc[best_idx], best_score, "partial")
        return self._no_match(name)

    def _token(self, name: str) -> Dict[str, Any]:
        if not self._name_lower_index: return self._no_match(name)
        ta = set(re.findall(r"[a-z0-9]+", self._semantic_normalize(name)))
        if not ta: return self._no_match(name)
        best_idx, best_score = None, 0.0
        for n_l, idx in self._name_lower_index.items():
            tb = set(re.findall(r"[a-z0-9]+", self._semantic_normalize(n_l)))
            if not tb: continue
            score = len(ta & tb) / len(ta | tb)
            if score > best_score:
                best_score, best_idx = score, idx
        if best_idx is not None and best_score >= 0.55:
            return self._make_result(name, self._df.loc[best_idx], best_score, "token_overlap")
        return self._no_match(name)


class ExerciseDatasetMatcher(PrescriptionExerciseMatcher):
    """Compatibility wrapper with 0.60 minimum confidence threshold."""
    def match(self, name: str) -> Dict[str, Any]:
        result = super().match(name)
        conf = float(result.get("confidence") or 0.0)
        if result.get("row") is not None and conf >= 0.60:
            return result
        if conf < 0.60:
            return {"name": str(name or ""), "row": None, "confidence": conf,
                    "source": "no_match", "slot": "main"}
        return result

# ============ SAFETY CONDITIONS (loaded once at import) ============

_SAFETY_CONDITIONS = None

def _load_safety_conditions():
    """Load safety conditions from JSON config. Cached after first call."""
    global _SAFETY_CONDITIONS
    if _SAFETY_CONDITIONS is not None:
        return _SAFETY_CONDITIONS

    safety_path = os.path.join(DATASETS_DIR, "safety_conditions.json")
    try:
        with open(safety_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _SAFETY_CONDITIONS = {
            "medical": [kw.lower().strip() for kw in data.get("medical_conditions", [])],
            "physical": [kw.lower().strip() for kw in data.get("physical_limitations", [])]
        }
        logger.info(f"[SafetyConditions] Loaded {len(_SAFETY_CONDITIONS['medical'])} medical + {len(_SAFETY_CONDITIONS['physical'])} physical keywords")
    except Exception as e:
        logger.warning(f"[SafetyConditions] Failed to load {safety_path}: {e}. Falling back to fractures only.")
        _SAFETY_CONDITIONS = {
            "medical": ["fractures"],
            "physical": ["fractures"]
        }
    return _SAFETY_CONDITIONS
    
# ============ EXERCISE CLASSIFIER ============

class ExerciseClassifier:
    """Classifies and retrieves detailed exercise metadata with safety-first logic."""
    @staticmethod
    def is_safety_restricted(fitness_medical_conditions: List[str], physical_limitations: str) -> Optional[str]:
        """
        Returns the name of the matched condition if a safety restriction is triggered, 
        otherwise returns None.
        """
        safety = _load_safety_conditions()
        
        # Medical check
        medical_keywords = safety.get("medical") or safety.get("medical_conditions") or []
        if fitness_medical_conditions and medical_keywords:
            medical_keywords_lower = set(k.lower() for k in medical_keywords)
            for condition in fitness_medical_conditions:
                if not condition: continue
                condition_lower = condition.lower()
                for keyword in medical_keywords_lower:
                    if keyword in condition_lower:
                        return condition.strip()

        # Physical check
        physical_keywords = safety.get("physical") or safety.get("physical_limitations") or []
        if physical_limitations and physical_limitations.lower() != 'none' and physical_keywords:
            physical_keywords_lower = set(k.lower() for k in physical_keywords)
            limitations_lower = physical_limitations.lower()
            for keyword in physical_keywords_lower:
                if keyword in limitations_lower:
                    return physical_limitations.strip()

        return None

    @staticmethod
    def get_suitable_exercises(
        age: int,
        fitness_medical_conditions: List[str],
        physical_limitations: str,
        limit: int = None
    ) -> List[Dict[str, Any]]:
        """
        Returns a randomized list of dictionaries with exercise name and GUIID. 
        If a safety condition is detected, returns SAFETY_001.
        """
        matched_condition = ExerciseClassifier.is_safety_restricted(fitness_medical_conditions, physical_limitations)

        if matched_condition:
            logger.info(f"[ExerciseClassifier] Safety condition detected: '{matched_condition}'. Returning SAFETY_001.")
            return [{
                "exercise_name": f"Safety Restriction: {matched_condition}",
                "GUIID": "SAFETY_001",
            }]
 
 
        specific_db_path = os.path.join(DATASETS_DIR, "Exercise_Video_Database.csv")
        df = FitnessDataset.load(preferred_path=specific_db_path)
        if df.empty:
            return []
 
        
        df = df[df['Age Suitability'].apply(lambda x: ExerciseFilter._parse_age_suitability(age, x))]
 
        
        avoid_terms = [c.lower().strip() for c in fitness_medical_conditions if c and c.lower() != "none"]
        if physical_limitations and physical_limitations.lower() != 'none':
            avoid_terms.extend([t.strip().lower() for t in physical_limitations.split(',') if t.strip()])
 
        
        for term in avoid_terms:
            if len(term) < 3: continue
            df = df[~df['is_not_suitable_for'].str.contains(term, case=False, na=False)]
            # Handling potential column name variations
            phys_col = 'Physical limitation' if 'Physical limitation' in df.columns else 'Physical limitations'
            if phys_col in df.columns:
                df = df[~df[phys_col].str.contains(term, case=False, na=False)]

        results = []
        for _, row in df.iterrows():
            guid = str(row.get('guidid', '')).strip()
            if not guid or guid.lower() == 'none' or guid.lower() == '9fad6875-5995-4b77-b6d3-515edd6d42a0':
                continue
            results.append({
                "exercise_name": row.get('Exercise Name') or row.get('video_name', 'N/A'),
                "GUIID": guid
            })

        random.shuffle(results)

        if limit:
            return results[:limit]

        return results       

# ================================================


import re
import pdfplumber

class ClinicalExtractionTool:
    def __init__(self):
        # Precise anchors based on the patient report structure
        self.sections = {
            "history": r"History of present illness",
            "plan_of_action": r"Plan of Action",
            "findings": r"Functional & Diet Findings",
            "exercise_session": r"Exercise Session",
            "vitals": r"Vitals"
        }

    def extract_raw_text(self, pdf_file):
        full_text = ""
        with pdfplumber.open(pdf_file) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    full_text += text + "\n"
        return full_text

    def parse_sections(self, text):
        extracted = {}

        # 1. Identify all occurrences of "Exercise Session"
        exercise_headers = list(re.finditer(r"Exercise Session", text, re.I))
        
        if exercise_headers:
            last_header_pos = exercise_headers[-1].start()
            
            # Capture Findings from 'Functional & Diet Findings' until the last header position
            findings_chunk = text[:last_header_pos]
            findings_match = re.search(
                r"Functional & Diet Findings(.*?)$", 
                findings_chunk, 
                re.S | re.I
            )
            extracted["findings"] = findings_match.group(1).strip() if findings_match else "Not found"

            # 2. Extract Actual Exercise Session from last header until Personalized Meal Plan or Vitals
            exercise_text_chunk = text[last_header_pos:]
            actual_workout_match = re.search(
                r"Exercise Session(.*?)(?=Personalized Meal Plan|Vitals)", 
                exercise_text_chunk, re.S | re.I
            )
            extracted["exercise_session"] = actual_workout_match.group(1).strip() if actual_workout_match else "Not found"
        else:
            extracted["findings"] = "Not found"
            extracted["exercise_session"] = "Not found"

        # 3. Standard narrative sections
        extracted["history"] = self._simple_grab(text, "History of present illness", "Plan of Action")
        extracted["plan_of_action"] = self._simple_grab(text, "Plan of Action", "Smart Goals|Summary")

        # 4. Extract Vitals with the corrected CSV table logic
        vitals_text = self._simple_grab(text, "Vitals", "Tasks and Time Tracking")
        extracted["structured_vitals"] = self._parse_vitals_safe(vitals_text)

        return extracted

    def _simple_grab(self, text, start_key, end_key):
        pattern = f"{start_key}(.*?)(?={end_key})"
        match = re.search(pattern, text, re.S | re.I)
        return match.group(1).strip() if match else "Not found"

    def _parse_vitals_safe(self, v_text):
        """
        Robust vital extraction for quoted CSV-style PDF tables.
        Handles: "Weight\n","265.00 lbs\n"
        """
        vitals = {"Weight": "N/A", "BP": "N/A", "BMI": "N/A"}
        
        # New pattern looks for the Label, then skips everything until the first digit in the next set of quotes
        patterns = {
            "Weight": r"\"Weight\s*\\n?\"\s*,\s*\"([\d\.]+\s*lbs)",
            "BP": r"\"BP\s*\\n?\"\s*,\s*\"([\d/]+\s*mmHg)",
            "BMI": r"\"BMI\s*\\n?\"\s*,\s*\"([\d\.]+)"
        }

        for key, regex in patterns.items():
            # We search the raw text; if N/A persists, we try a secondary fallback
            match = re.search(regex, v_text, re.I | re.S)
            if match:
                vitals[key] = match.group(1).strip()
            else:
                # Fallback: Just look for the label and grab the next numeric-looking value in quotes
                fallback = fr"\"{key}\s*.*?\".*?\"([\d\./]+\s*[a-zA-Z]*)\""
                fm = re.search(fallback, v_text, re.I | re.S)
                if fm:
                    vitals[key] = fm.group(1).strip()
        
        return vitals
# ============ INTEGRATION: SOAP/EXPERT NOTE PARSING ============
# This section integrates clinical note parsing into the fitness.py workout
# generation engine. It intentionally reuses the existing planner/composer.

from typing import Sequence, Iterable

# ============ CORE PARSING FUNCTIONS ============

def _parse_note(text: str, mode: str) -> Dict[str, Any]:
    """Parse clinical note by attempting Azure AI first, then fallback to local regex parsing."""
    ai_data = _try_azure_parse(text) if mode == "expert" else None
    parsed = _normalize_ai(ai_data) if ai_data else _local_parse(text, mode)
    parsed["avoid_terms"] = _merge_unique(parsed.get("avoid_terms", []), _extract_avoid_terms(text))
    parsed["restrictions"] = _merge_unique(parsed.get("restrictions", []), _restrictions_from_avoid(parsed["avoid_terms"]))
    parsed["weekly_schedule"] = _ensure_week(parsed.get("weekly_schedule", {}), text, parsed)
    parsed["focus"] = _focus_from_text(text, parsed.get("focus", []))
    return parsed


def _try_azure_parse(text: str) -> Optional[Dict[str, Any]]:
    """Attempt to parse using Azure OpenAI. Returns None if unavailable."""
    api_key = os.getenv("AZURE_AI_KEY", "").strip()
    if not api_key:
        return None
    endpoint = os.getenv("AZURE_AI_ENDPOINT", "https://nouriqfriskacc7470931625.cognitiveservices.azure.com/").strip()
    model = os.getenv("AZURE_AI_MODEL", "gpt-4.1-mini-Fitness-Model").strip()
    api_version = os.getenv("AZURE_AI_API_VERSION", "2024-12-01-preview").strip()
    try:
        from openai import AzureOpenAI  # type: ignore
        client = AzureOpenAI(api_key=api_key, azure_endpoint=endpoint, api_version=api_version)
        prompt = (
            "Return only JSON with keys: days_per_week, session_duration, goal, focus, fitness_medical_conditions, "
            "physical_limitations, activity_restrictions, prescribed_exercises, avoid_exercises, "
            "equipment_required, target_body_parts, weekly_schedule.\n\nNOTES:\n" + text
        )
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a clinical fitness expert."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=1800,
        )
        content = response.choices[0].message.content
        return json.loads(content) if content else None
    except Exception:
        return None


def _normalize_ai(data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize Azure OpenAI response to standard parsed structure."""
    weekly = data.get("weekly_schedule", {})
    if not isinstance(weekly, dict):
        weekly = {}
    prescribed_names: List[str] = []
    for item in _to_list(data.get("prescribed_exercises", [])):
        if item:
            prescribed_names.append(item)
    return {
        "days_per_week": _to_int(data.get("days_per_week"), 3),
        "session_duration": _txt(data.get("session_duration")) or "45-60 minutes",
        "goal": _txt(data.get("goal")) or "General Fitness",
        "focus": _to_list(data.get("focus", [])),
        "fitness_medical_conditions": _to_list(data.get("fitness_medical_conditions", [])),
        "physical_limitations": _to_list(data.get("physical_limitations", [])),
        "restrictions": _to_list(data.get("activity_restrictions", [])),
        "avoid_terms": _to_list(data.get("avoid_exercises", [])),
        "equipment_required": _to_list(data.get("equipment_required", [])),
        "target_body_parts": _to_list(data.get("target_body_parts", [])) or ["Full Body"],
        "weekly_schedule": {_normalize_day(k): _txt(v) for k, v in weekly.items() if _normalize_day(k)},
        "prescribed_exercises": prescribed_names,
    }


def _local_parse(text: str, mode: str) -> Dict[str, Any]:
    """Regex-based fallback parser for clinical notes."""
    weekly = _schedule_from_text(text)
    frequency = _extract_frequency(text) or _count_active_days(weekly) or 3
    if not weekly:
        weekly = _schedule_from_frequency(frequency, _focus_from_text(text, []))
    if mode == "soap":
        restrictions = _extract_soap_restrictions(text)
    else:
        restrictions = _extract_restrictions(text)
    return {
        "days_per_week": frequency,
        "session_duration": _infer_duration(text),
        "goal": _infer_goal(text),
        "focus": _focus_from_text(text, []),
        "fitness_medical_conditions": _extract_fitness_medical_conditions(text),
        "physical_limitations": _extract_physical_limitations(text),
        "restrictions": restrictions,
        "avoid_terms": _extract_avoid_terms(text),
        "equipment_required": _extract_equipment(text),
        "target_body_parts": ["Full Body"],
        "weekly_schedule": weekly,
        "prescribed_exercises": _extract_prescribed_names(text),
    }


# ============ EXTRACTION FUNCTIONS ============

def _extract_fitness_medical_conditions(text: str) -> List[str]:
    """Extract medical conditions from text."""
    t = text.lower()
    checks = {
        "diabetes": ["diabetes", "diabetic", "blood sugar"],
        "hypertension": ["hypertension", "high blood pressure", "elevated bp"],
        "obesity": ["obesity", "obese", "overweight"],
        "arthritis": ["arthritis"],
        "asthma": ["asthma"],
    }
    return [label for label, keys in checks.items() if any(k in t for k in keys)]


def _extract_physical_limitations(text: str) -> List[str]:
    """Extract physical limitations from text."""
    t = text.lower()
    checks = {
        "knee pain": ["knee pain", "acl", "meniscus", "patella"],
        "low back pain": ["low back", "lumbar", "back pain", "sciatica"],
        "shoulder pain": ["shoulder pain", "rotator cuff", "impingement"],
        "neck pain": ["neck pain", "cervical"],
        "hip pain": ["hip pain"],
    }
    return [label for label, keys in checks.items() if any(k in t for k in keys)]


def _extract_restrictions(text: str) -> List[str]:
    """Extract general restrictions from text."""
    t = text.lower()
    checks = {
        "avoid jumping": ["avoid jumping", "no jumping", "no jump", "no plyo", "no plyometric"],
        "avoid high impact": ["avoid high impact", "no high impact", "low impact only"],
        "avoid floor work": ["avoid floor", "cannot lie down", "no floor work", "no prone"],
        "avoid overhead": ["avoid overhead", "no overhead", "overhead restricted"],
        "avoid spinal loading": ["avoid spinal loading", "spinal load", "heavy spine"],
        "avoid running": ["avoid running", "no running"],
    }
    return [label for label, keys in checks.items() if any(k in t for k in keys)]


def _extract_soap_restrictions(text: str) -> List[str]:
    """Extract SOAP-specific restrictions from text."""
    t = text.lower()
    checks = {
        "knee pain": ["knee pain", "acl", "meniscus", "patella"],
        "low back pain": ["low back", "lumbar", "back pain", "sciatica"],
        "shoulder pain": ["shoulder pain", "rotator cuff", "impingement"],
        "avoid floor work": ["avoid floor", "cannot lie down", "difficulty getting up from floor"],
        "hypertension": ["hypertension", "high blood pressure"],
        "diabetes": ["diabetes", "blood sugar"],
    }
    out = [label for label, keys in checks.items() if any(k in t for k in keys)]
    return _merge_unique(out, _restrictions_from_avoid(_extract_avoid_terms(text)))


def _extract_avoid_terms(text: str) -> List[str]:
    """Extract specific exercises/activities to avoid."""
    t = text.lower()
    out: List[str] = []
    fixed = [
        (r"\bavoid\s+squats?\b|\bno\s+squats?\b", "squat"),
        (r"\bavoid\s+jumping\b|\bno\s+jumping\b|\bno\s+jumps?\b", "jump"),
        (r"\bavoid\s+planks?\b|\bno\s+planks?\b", "plank"),
        (r"\bavoid\s+running\b|\bno\s+running\b", "running"),
        (r"\bavoid\s+cycling\b|\bno\s+cycling\b", "cycling"),
    ]
    for pattern, term in fixed:
        if re.search(pattern, t):
            out.append(term)
    generic = re.findall(r"\b(?:avoid|no)\s+([a-z][a-z\s-]{2,32})", t)
    for phrase in generic:
        phrase = re.split(r"[\n\.;,]", phrase)[0]
        for token in re.split(r"\band\b|/|\bor\b", phrase):
            clean = re.sub(r"[^a-z0-9\s-]", " ", token).strip()
            if clean:
                out.append(clean)
    return _merge_unique(out, [])


def _extract_equipment(text: str) -> List[str]:
    """Extract available equipment from text."""
    t = text.lower()
    mapping = {
        "dumbbell": "Dumbbells",
        "dumbbells": "Dumbbells",
        "band": "Resistance Bands",
        "resistance band": "Resistance Bands",
        "kettlebell": "Kettlebell",
        "bench": "Bench",
        "treadmill": "Treadmill",
        "bike": "Stationary Bike",
        "cycling": "Stationary Bike",
        "elliptical": "Elliptical",
    }
    out: List[str] = []
    for k, v in mapping.items():
        if k in t and v not in out:
            out.append(v)
    return out or ["Bodyweight"]


def _extract_prescribed_names(text: str) -> List[str]:
    """Extract exercise names from text."""
    candidates = re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})\b", text)
    banned = {"History", "Plan", "Action", "Vitals", "Summary", "Exercise", "Session", "Patient", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}
    out: List[str] = []
    for item in candidates:
        if item in banned or len(item) < 4:
            continue
        if item not in out:
            out.append(item)
    return out[:12]


def _extract_frequency(text: str) -> Optional[int]:
    """Extract training frequency (days per week) from text."""
    t = text.lower()
    m = re.search(r"(\d+)\s*(?:x|times?|days?)\s*(?:per\s*)?week", t)
    if m:
        return int(m.group(1))
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}
    m2 = re.search(r"(one|two|three|four|five|six|seven)\s*(?:times?|days?)\s*(?:per\s*)?week", t)
    if m2:
        return words[m2.group(1)]
    return None


def _schedule_from_text(text: str) -> Dict[str, str]:
    """Extract weekly schedule from text (e.g., 'Monday: Strength')."""
    schedule: Dict[str, str] = {}
    day_re = re.compile(r"\b(mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|thu(?:rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)\b", re.I)
    day_alias = {
        "mon": "Monday", "monday": "Monday",
        "tue": "Tuesday", "tuesday": "Tuesday",
        "wed": "Wednesday", "wednesday": "Wednesday",
        "thu": "Thursday", "thursday": "Thursday",
        "fri": "Friday", "friday": "Friday",
        "sat": "Saturday", "saturday": "Saturday",
        "sun": "Sunday", "sunday": "Sunday",
    }
    def assign(day: str, activity: str) -> None:
        activity = _txt(activity) or "Workout"
        existing = schedule.get(day)
        if existing and not _is_rest(existing):
            existing_low = existing.lower()
            activity_low = activity.lower()
            if activity_low in existing_low:
                return
            if existing_low in activity_low:
                schedule[day] = activity
                return
            schedule[day] = f"{existing} + {activity}"
            return
        schedule[day] = activity

    candidate_lines: List[str] = []
    for raw_line in [ln.strip() for ln in text.splitlines() if ln.strip()]:
        candidate_lines.extend([part.strip() for part in re.split(r"(?<=[\.])\s+|;", raw_line) if part.strip()])
    for line in candidate_lines:
        line = _resolve_or_schedule(line)
        matches = list(day_re.finditer(line))
        if not matches:
            continue
        prefix = line[:matches[0].start()].strip(" :-\t,;")
        prefix_has_activity = bool(re.search(rf"\b({_ACTIVITY_KEYWORDS})\b", prefix, re.I))
        if prefix and prefix_has_activity:
            for m in matches:
                assign(day_alias.get(m.group(1).lower(), m.group(1).title()), prefix)
            continue
        for idx, m in enumerate(matches):
            day = day_alias.get(m.group(1).lower(), m.group(1).title())
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(line)
            activity = line[m.end():end].strip(" :-\t,;") or "Workout"
            assign(day, activity)
    return schedule


def _focus_from_text(text: str, current: Sequence[str]) -> List[str]:
    """Extract fitness focus areas from text."""
    t = text.lower()
    out = [c for c in _to_list(current)]
    if any(k in t for k in ["hiit", "interval", "tabata", "circuit"]) and "full_body_cardio" not in [x.lower() for x in out]:
        out.append("full_body_cardio")
    if any(k in t for k in ["strength", "resistance", "weights", "dumbbell", "band"]) and "resistance" not in [x.lower() for x in out]:
        out.append("resistance")
    if any(k in t for k in ["cardio", "walk", "walking", "run", "treadmill", "cycling", "elliptical"]) and "cardio" not in [x.lower() for x in out]:
        out.append("cardio")
    if "yoga" in t and "yoga" not in [x.lower() for x in out]:
        out.append("yoga")
    return out or ["resistance", "cardio"]


def _flags(parsed: Dict[str, Any], text: str) -> Dict[str, bool]:
    """Extract safety flags from parsed data and text."""
    merged = " ".join(
        [
            text.lower(),
            " ".join(_to_list(parsed.get("physical_limitations", []))).lower(),
            " ".join(_to_list(parsed.get("restrictions", []))).lower(),
            " ".join(_to_list(parsed.get("avoid_terms", []))).lower(),
        ]
    )
    flags: Dict[str, bool] = {}
    if any(k in merged for k in ["knee", "acl", "meniscus", "patella", "avoid jumping"]):
        flags["knee_sensitive"] = True
    if any(k in merged for k in ["avoid floor", "no floor", "cannot lie down", "plank", "prone", "supine"]):
        flags["avoid_floor_work"] = True
    if any(k in merged for k in ["high impact", "jump", "plyo", "sprint"]):
        flags["high_impact_restricted"] = True
    if "spinal loading" in merged:
        flags["avoid_spinal_loading"] = True
    if "overhead" in merged:
        flags["overhead_restricted"] = True
    return flags


# ============ UTILITY FUNCTIONS ============

def _merge_unique(a: Sequence[str], b: Sequence[str]) -> List[str]:
    """Merge two sequences, keeping unique items (case-insensitive)."""
    out: List[str] = []
    seen: Set[str] = set()
    for src in (a, b):
        for item in src:
            t = _txt(item)
            if not t:
                continue
            k = t.lower()
            if k in seen:
                continue
            seen.add(k)
            out.append(t)
    return out


def _to_list(v: Any) -> List[str]:
    """Convert value to list of strings, splitting on common delimiters."""
    if v is None:
        return []
    if isinstance(v, str):
        parts = re.split(r"[,;\n|]", v)
    elif isinstance(v, Iterable):
        parts = [str(x) for x in v]
    else:
        parts = [str(v)]
    out: List[str] = []
    seen: Set[str] = set()
    for p in parts:
        t = _txt(p)
        if not t:
            continue
        k = t.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out


def _txt(v: Any) -> str:
    """Convert value to stripped string."""
    return str(v or "").strip()


def _to_int(v: Any, default: int = 0) -> int:
    """Convert value to int with default fallback."""
    try:
        return int(float(_txt(v)))
    except Exception:
        return default


def _to_float(v: Any, default: float = 0.0) -> float:
    """Convert value to float with default fallback."""
    try:
        return float(_txt(v))
    except Exception:
        return default


def _normalize_day(v: Any) -> Optional[str]:
    """Normalize day name (return canonical form or None)."""
    day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    t = _txt(v).lower()
    for day in day_order:
        if day.lower() == t:
            return day
    return None


def _count_active_days(schedule: Dict[str, str]) -> int:
    """Count non-rest days in schedule."""
    return sum(1 for _, act in schedule.items() if not _is_rest(act))


def _active_days(schedule: Dict[str, str]) -> List[str]:
    """Get list of active day names from schedule."""
    day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    return [d for d in day_order if not _is_rest(schedule.get(d, "Rest"))]


def _is_rest(v: Any) -> bool:
    """Check if value represents a rest day."""
    return bool(re.search(r"\brest\b", _txt(v).lower()))


def _distributed_days(n: int) -> List[str]:
    """Distribute n training days across the week."""
    day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    n = max(1, min(7, int(n or 3)))
    if n == 1:
        return ["Monday"]
    if n == 2:
        return ["Monday", "Thursday"]
    if n == 3:
        return ["Monday", "Wednesday", "Friday"]
    if n == 4:
        return ["Monday", "Tuesday", "Thursday", "Saturday"]
    if n == 5:
        return ["Monday", "Tuesday", "Wednesday", "Friday", "Saturday"]
    if n == 6:
        return ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    return day_order[:]


def _stable_seed(prefix: str, text: str) -> int:
    """Generate stable random seed from text."""
    return int(hashlib.md5(f"{prefix}|{text}".encode("utf-8")).hexdigest()[:8], 16)


def _matches_avoidance(text: str, terms: Sequence[str]) -> bool:
    """Check if text contains any avoided terms."""
    norm = re.sub(r"[^a-z0-9\s]+", " ", _txt(text).lower())
    norm = re.sub(r"\s+", " ", norm).strip()
    if not norm:
        return False
    for term_raw in terms:
        term = re.sub(r"\s+", " ", _txt(term_raw).lower()).strip()
        if not term:
            continue
        variants = {term}
        if term.endswith("ies") and len(term) > 3:
            variants.add(term[:-3] + "y")
        if term.endswith("s") and len(term) > 1:
            variants.add(term[:-1])
        else:
            variants.add(term + "s")
        for v in variants:
            if v and (v in norm or norm in v):
                return True
    return False


def _split_steps(text: str) -> List[str]:
    """Split exercise steps by newlines or numbered format."""
    t = _txt(text)
    if not t:
        return []
    return [p.strip() for p in re.split(r"\n+|(?=\d+\.\s)", t) if p.strip()]


def _infer_goal(text: str) -> str:
    """Infer fitness goal from text."""
    t = text.lower()
    if any(k in t for k in ["weight loss", "fat loss", "obesity", "diabetes"]):
        return "Weight Loss"
    if any(k in t for k in ["muscle", "hypertrophy", "strength", "resistance"]):
        return "Muscle Gain"
    if any(k in t for k in ["mobility", "flexibility", "range of motion"]):
        return "Mobility"
    return "General Fitness"


def _infer_duration(text: str) -> str:
    """Infer session duration from text."""
    m = re.search(r"(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?\s*(?:minutes|min|mins)", text, re.I)
    if not m:
        return "45-60 minutes"
    if m.group(2):
        return f"{m.group(1)}-{m.group(2)} minutes"
    return f"{m.group(1)} minutes"


def _infer_level(text: str) -> str:
    """Infer fitness level from text."""
    t = text.lower()
    if "advanced" in t:
        return "Advanced"
    if "intermediate" in t:
        return "Intermediate"
    return "Beginner"


def _equipment_allow(equipment_required: Sequence[str], text: str) -> List[str]:
    """Get list of available equipment."""
    merged = _merge_unique(_to_list(equipment_required), _extract_equipment(text))
    if not merged:
        return ["Bodyweight"]
    return merged


def _is_bodyweight_only(allow_eq: Set[str]) -> bool:
    """Check if only bodyweight equipment is available."""
    if not allow_eq:
        return True
    return all(any(k in item for k in ["bodyweight", "no equipment"]) for item in allow_eq)


def _cardio_mode(profile: Dict[str, Any], parsed: Dict[str, Any]) -> str:
    """Determine appropriate cardio modality based on profile."""
    available = " ".join(_to_list(profile.get("available_equipment", []))).lower()
    restricted = " ".join(_to_list(parsed.get("restrictions", [])) + _to_list(parsed.get("avoid_terms", []))).lower()
    if "stationary bike" in available or "bike" in available:
        if "cycling" not in restricted:
            return "Stationary Bike"
    if "treadmill" in available and "running" not in restricted:
        return "Treadmill Walking"
    if "elliptical" in available:
        return "Elliptical"
    return "Brisk Walking"


def _restrictions_from_avoid(avoid_terms: Sequence[str]) -> List[str]:
    """Convert avoided exercises to general restrictions."""
    out: List[str] = []
    for term in _to_list(avoid_terms):
        t = term.lower()
        if "jump" in t and "avoid jumping" not in out:
            out.append("avoid jumping")
        if "plank" in t and "avoid floor work" not in out:
            out.append("avoid floor work")
        if "squat" in t and "knee pain" not in out:
            out.append("knee pain")
        if "running" in t and "avoid running" not in out:
            out.append("avoid running")
    return out


def _schedule_from_frequency(freq: int, focus: Sequence[str]) -> Dict[str, str]:
    """Generate weekly schedule from frequency and focus."""
    freq = max(1, min(7, int(freq or 3)))
    day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    days = _distributed_days(freq)
    f = [s.lower() for s in _to_list(focus)]
    has_yoga = any("yoga" in s for s in f)
    has_cardio = any(any(k in s for k in ["cardio", "hiit", "interval"]) for s in f)
    has_strength = any(any(k in s for k in ["strength", "resistance", "weight"]) for s in f)
    labels: List[str] = []
    if has_strength and has_cardio:
        labels.append("Strength + Cardio")
    elif has_strength:
        labels.append("Strength")
    elif has_cardio:
        labels.append("Cardio")
    if has_yoga:
        labels.append("Yoga")
    if not labels:
        labels = ["Strength + Cardio"]
    out = {d: "Rest" for d in day_order}
    for i, day in enumerate(days):
        out[day] = labels[i % len(labels)]
    return out


def _ensure_week(schedule: Dict[str, str], text: str, parsed: Dict[str, Any]) -> Dict[str, str]:
    """Ensure schedule covers full week with proper defaults."""
    day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    out = {d: "Rest" for d in day_order}
    for k, v in schedule.items():
        day = _normalize_day(k)
        if day:
            out[day] = _txt(v) or "Rest"
    if _count_active_days(out) == 0:
        freq = _to_int(parsed.get("days_per_week"), _extract_frequency(text) or 3)
        fallback = _schedule_from_frequency(freq, parsed.get("focus", []))
        for d in day_order:
            out[d] = fallback.get(d, "Rest")
    low = "\n".join(_resolve_or_schedule(line) for line in text.splitlines()).lower()
    for day in day_order:
        if _is_rest(out.get(day, "Rest")) and re.search(rf"\b{day.lower()}\b[^\n\.]*\byoga\b", low):
            out[day] = "Yoga"
    return out


# ============ MAIN INTEGRATION FUNCTIONS ============

_NOTE_DAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
NORMALIZED_ACTIVITY_MAP = {
    "hiit": "hybrid",
    "h.i.i.t": "hybrid",
    "interval": "hybrid",
    "interval training": "hybrid",
    "tabata": "hybrid",
    "circuit": "hybrid",
    "circuit training": "hybrid",
    "strength": "strength",
    "resistance": "strength",
    "resistance training": "strength",
    "weight training": "strength",
    "weight": "strength",
    "weights": "strength",
    "cardio": "cardio",
    "aerobic": "cardio",
    "walking": "cardio",
    "walk": "cardio",
    "treadmill": "cardio",
    "cycling": "cardio",
    "bike": "cardio",
    "elliptical": "cardio",
    "yoga": "yoga",
    "pilates": "pilates",
    "meditation": "meditation",
    "mindfulness": "meditation",
    "class": "class",
    "group exercise": "class",
    "hatha yoga class": "class",
    "mobility": "mobility",
    "stretch": "mobility",
}
_SESSION_ACTIVITY_TYPES = {"cardio", "walking", "yoga", "pilates", "class", "meditation", "recovery"}
_DATASET_ACTIVITY_TYPES = {"strength", "hybrid", "mobility"}
_ACTIVITY_KEYWORDS = r"cardio|strength|resistance(?:\s+training)?|weight(?:\s+training)?|hiit|h\.i\.i\.t|interval(?:\s+training)?|tabata|circuit(?:\s+training)?|yoga|pilates|mobility|class|meditation|walking|walk|treadmill|bike|cycling|elliptical|recovery"


def _model_dump_compat(model: BaseModel) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _duration_minutes(value: Any, default: int = 45) -> int:
    text = _txt(value)
    nums = [int(x) for x in re.findall(r"\d+", text)]
    if not nums:
        return default
    return int(sum(nums[:2]) / min(2, len(nums))) if len(nums) >= 2 else nums[0]


def _exercise_count_for_duration(duration: Any, prescribed_count: int = 0) -> int:
    minutes = _duration_minutes(duration, 45)
    if minutes <= 30:
        base = 5 if prescribed_count >= 5 else 4
    elif minutes <= 45:
        base = 7 if prescribed_count >= 7 else 6
    else:
        base = 10 if prescribed_count >= 10 else 8
    return max(base, min(10, int(prescribed_count or 0))) if prescribed_count else base


def _normalize_activity_type(value: Any) -> str:
    low = _txt(value).lower().strip()
    low = re.sub(r"[^a-z0-9\.\s]+", " ", low)
    low = re.sub(r"\s+", " ", low).strip()
    if low in NORMALIZED_ACTIVITY_MAP:
        return NORMALIZED_ACTIVITY_MAP[low]
    for key, canonical in sorted(NORMALIZED_ACTIVITY_MAP.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(rf"\b{re.escape(key)}\b", low):
            return canonical
    return low or "strength"


def _block_type(block: Any) -> str:
    raw = block.get("type") if isinstance(block, dict) else getattr(block, "type", "")
    return _normalize_activity_type(raw)


def _session_type_from_block(block: Dict[str, Any]) -> str:
    meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
    explicit = _normalize_activity_type(block.get("type"))
    raw_type = _txt(block.get("type")).lower()
    raw = " ".join([raw_type, _txt(meta.get("raw_activity")), _txt(meta.get("session_type"))]).lower()
    if explicit in {"yoga", "pilates", "meditation", "class", "recovery"}:
        return explicit
    if explicit == "cardio" and "walk" not in raw_type:
        return "cardio"
    if "walk" in raw_type:
        return "walking"
    if "meditation" in raw or "mindfulness" in raw:
        return "meditation"
    if "pilates" in raw:
        return "pilates"
    if "yoga" in raw:
        return "yoga"
    if "class" in raw or "group exercise" in raw:
        return "class"
    if "recovery" in raw:
        return "recovery"
    if "cardio" in raw or explicit == "cardio":
        return "cardio"
    return explicit or "class"


def _exercise_count_for_note_blocks(duration: Any, blocks: Sequence[Dict[str, Any]], prescribed_count: int = 0) -> int:
    minutes = _duration_minutes(duration, 45)
    types = [_block_type(block) for block in blocks]
    if minutes <= 30:
        count = 5 if "hybrid" in types else 4
    elif minutes <= 45:
        count = 7 if "hybrid" in types else 6
    else:
        count = 10 if "hybrid" in types else 8
    if "strength" in types and "hybrid" not in types:
        count = max(4, count - 1)
    session_count = sum(1 for block in blocks if not bool((block or {}).get("requires_dataset_exercises", True)))
    if session_count and any(t in _DATASET_ACTIVITY_TYPES for t in types):
        count = max(4, count - session_count)
    if prescribed_count:
        count = max(count, min(10, int(prescribed_count)))
    return max(1, min(10, count))


def _activity_types_from_text(text: Any) -> List[str]:
    low = _txt(text).lower()
    if not low or _is_rest(low):
        return []
    types: List[str] = []
    if any(k in low for k in ["hiit", "h.i.i.t", "tabata", "interval", "circuit"]):
        types.append("hybrid")
    if any(k in low for k in ["strength", "resistance", "weight training", "weight", "weights", "upper", "lower"]):
        types.append("strength")
    if any(k in low for k in ["cardio", "walk", "walking", "treadmill", "bike", "cycling", "elliptical", "aerobic"]):
        types.append("cardio")
    if "yoga" in low:
        types.append("class" if "class" in low else "yoga")
    if "pilates" in low:
        types.append("class" if "class" in low else "pilates")
    if any(k in low for k in ["meditation", "mindfulness", "breathing session"]):
        types.append("meditation")
    if "group exercise" in low:
        types.append("class")
    if "recovery" in low:
        types.append("recovery")
    if any(k in low for k in ["mobility", "stretch", "range of motion"]):
        types.append("mobility")
    if not types:
        types.append("strength")
    normalized = [_normalize_activity_type(t) for t in types]
    if "hybrid" in normalized:
        normalized = [t for t in normalized if t != "cardio"]
    return list(dict.fromkeys(normalized))


def _activity_duration_from_text(text: Any, fallback: str) -> str:
    value = _txt(text)
    match = re.search(r"(\d{1,3})(?:\s*[-–]\s*(\d{1,3}))?\s*(?:minutes|min|mins)\b", value, re.I)
    if match:
        if match.group(2):
            return f"{match.group(1)}-{match.group(2)} minutes"
        return f"{match.group(1)} minutes"
    return fallback


def _activity_intensity_from_text(text: Any) -> Optional[str]:
    raw = _txt(text)
    match = re.search(r"\b(?:rpe|intensity)\s*[:=]?\s*(\d{1,2}(?:\s*[-/]\s*\d{1,2})?)\b", raw, re.I)
    return match.group(1).replace(" ", "") if match else None


def _activity_equipment_from_text(text: Any) -> Optional[str]:
    raw = _txt(text).lower()
    equipment_terms = []
    checks = [
        ("Resistance Band", ["resistance band", "band"]),
        ("Dumbbells", ["dumbbell", "dumbbells"]),
        ("Treadmill", ["treadmill"]),
        ("Stationary Bike", ["stationary bike", "bike", "cycling"]),
        ("Elliptical", ["elliptical"]),
        ("Bodyweight", ["bodyweight", "no equipment"]),
    ]
    for label, keys in checks:
        if any(key in raw for key in keys):
            equipment_terms.append(label)
    return ", ".join(dict.fromkeys(equipment_terms)) or None


def _blocks_from_activity_text(activity_text: Any, fallback_duration: str) -> List[ActivityBlock]:
    types = _activity_types_from_text(activity_text)
    duration = _activity_duration_from_text(activity_text, fallback_duration)
    source_text = _txt(activity_text)
    equipment = _activity_equipment_from_text(activity_text)
    intensity = _activity_intensity_from_text(activity_text)
    has_strength = "strength" in types
    has_cardio = "cardio" in types
    has_hybrid = "hybrid" in types

    blocks: List[ActivityBlock] = []
    for activity_type in types:
        requires_dataset = activity_type in _DATASET_ACTIVITY_TYPES
        if activity_type == "cardio" and (has_strength or not has_hybrid):
            requires_dataset = False
        if activity_type in {"yoga", "pilates", "class", "meditation", "recovery"}:
            requires_dataset = False
        blocks.append(ActivityBlock(
            type=activity_type,
            duration=duration,
            equipment=equipment,
            intensity=intensity,
            source_text=source_text,
            requires_dataset_exercises=requires_dataset,
            meta={
                "raw_activity": source_text,
                "session_type": _session_type_from_block({"type": activity_type, "meta": {"raw_activity": source_text}}),
            },
        ))
    return blocks


def _build_activity_blocks(parsed: Dict[str, Any], raw_text: str, source_type: str) -> ParsedNoteProfile:
    """
    Convert parsed note output into canonical activity blocks.

    This is the boundary between extraction and orchestration: downstream note
    planning consumes ActivityBlocks, not raw note phrases and not intake splits.
    """
    schedule = parsed.get("weekly_schedule") if isinstance(parsed.get("weekly_schedule"), dict) else {}
    fallback_duration = _txt(parsed.get("session_duration")) or _infer_duration(raw_text)
    weekly_schedule: Dict[str, List[dict]] = {}

    for day in _NOTE_DAY_ORDER:
        activity = _resolve_or_schedule(schedule.get(day, "Rest"))
        if _is_rest(activity):
            continue
        blocks = _blocks_from_activity_text(activity, fallback_duration)
        if blocks:
            weekly_schedule[day] = [_model_dump_compat(block) for block in blocks]

    if not weekly_schedule:
        focus_text = " + ".join(_to_list(parsed.get("focus", []))) or parsed.get("goal") or "Strength"
        for day in _distributed_days(_to_int(parsed.get("days_per_week"), 3)):
            weekly_schedule[day] = [
                _model_dump_compat(block)
                for block in _blocks_from_activity_text(focus_text, fallback_duration)
            ]

    prescribed = []
    activity_name_blacklist = set(NORMALIZED_ACTIVITY_MAP) | {
        "cardio", "strength", "resistance", "yoga", "pilates", "hiit", "interval",
        "tabata", "circuit", "mobility", "class", "meditation", "workout",
    }
    for item in _to_list(parsed.get("prescribed_exercises", [])):
        if item and item.strip().lower() not in activity_name_blacklist:
            prescribed.append({"name": item, "category": "main", "prescribed": True})

    return ParsedNoteProfile(
        source_type=source_type,
        generation_mode=source_type,
        goal=_txt(parsed.get("goal")) or "General Fitness",
        days_per_week=len(weekly_schedule) or _to_int(parsed.get("days_per_week"), 3),
        session_duration=fallback_duration or "45-60 minutes",
        fitness_medical_conditions=_to_list(parsed.get("fitness_medical_conditions", [])),
        physical_limitations=_to_list(parsed.get("physical_limitations", [])),
        avoid_terms=_to_list(parsed.get("avoid_terms", [])),
        available_equipment=_to_list(parsed.get("equipment_required", [])) or ["Bodyweight"],
        prescribed_exercises=prescribed,
        weekly_schedule=weekly_schedule,
        flags=_flags(parsed, raw_text),
    )


def _note_title_from_blocks(blocks: Sequence[Any]) -> str:
    block_types = [_block_type(b) for b in blocks]
    raw_session_types = [
        _session_type_from_block(dict(b or {})) if isinstance(b, dict) else _block_type(b)
        for b in blocks
    ]
    has_hybrid = "hybrid" in block_types
    has_strength = "strength" in block_types
    has_cardio = "cardio" in block_types and not has_hybrid
    has_yoga = any(t in {"yoga", "pilates"} for t in block_types)
    has_class = any(t in {"class", "meditation"} for t in block_types) or any(t in {"class", "meditation"} for t in raw_session_types)

    labels: List[str] = []
    if has_hybrid:
        labels.append("Full Body Hybrid")
    elif has_strength:
        labels.append("Strength")
    elif has_cardio:
        labels.append("Cardio")

    if has_cardio and has_strength and not has_hybrid:
        labels = ["Strength", "Cardio"]
    if has_yoga:
        labels.append("Yoga")
    elif has_class and not labels:
        labels.append("Meditation" if "meditation" in raw_session_types else "Class Session")
    if not labels and "mobility" in block_types:
        labels.append("Mobility")
    return " + ".join(list(dict.fromkeys(labels))[:2]) or "Workout"


def _generate_note_based_title(activity_blocks: Sequence[Any]) -> str:
    """Title engine for expert_note and soap_note modes only."""
    return _note_title_from_blocks(activity_blocks)


def _primary_day_type_from_blocks(blocks: Sequence[Dict[str, Any]]) -> str:
    types = [_block_type(b) for b in blocks]
    if "hybrid" in types:
        return "Cardio Focus"
    if "strength" in types:
        return "Full Body"
    if "mobility" in types:
        return "Core Focus"
    if "cardio" in types:
        return "Cardio Focus"
    return "Full Body"


def _build_session_block(
    session_type,
    duration,
    title=None,
    intensity=None,
    calories=None,
    equipment=None,
) -> Dict[str, Any]:
    """Build a schema-complete non-exercise session entry for note mode."""
    block_type = _normalize_activity_type(session_type)
    raw_type = _txt(session_type).lower()
    if "walk" in raw_type:
        block_type = "walking"
    minutes = _duration_minutes(duration, 30)
    session_names = {
        "cardio": "Cardio Session",
        "walking": "Walking Session",
        "yoga": "Yoga Session",
        "pilates": "Pilates Session",
        "meditation": "Meditation Session",
        "class": "Group Exercise Class",
        "recovery": "Recovery Session",
    }
    benefit_by_type = {
        "cardio": "Improve aerobic conditioning and energy expenditure.",
        "walking": "Build low-impact aerobic capacity.",
        "yoga": "Improve flexibility and relaxation.",
        "pilates": "Improve core control, posture, and body awareness.",
        "meditation": "Support relaxation, breathing control, and recovery.",
        "class": "Complete the prescribed supervised group exercise session.",
        "recovery": "Promote recovery, circulation, and gentle mobility.",
    }
    cue_by_type = {
        "cardio": "Keep pace controlled and stay within the prescribed effort range.",
        "walking": "Use an even stride and avoid pain-provoking pace changes.",
        "yoga": "Maintain controlled breathing.",
        "pilates": "Move with control and avoid breath holding.",
        "meditation": "Use slow nasal breathing and stop if symptoms occur.",
        "class": "Follow instructor guidance and scale any painful movement.",
        "recovery": "Keep movements easy and pain-free.",
    }
    met_by_type = {"cardio": 5.0, "walking": 3.5, "yoga": 2.5, "pilates": 3.0, "meditation": 1.3, "class": 4.0, "recovery": 2.0}
    met = met_by_type.get(block_type, 3.0)
    total_sec = minutes * 60
    planned_cal = _to_int(calories, 0) if calories is not None else int(round((met * 3.5 * 70.0 / 200) * minutes))
    return {
        "name": title or session_names.get(block_type, "Class Session"),
        "gui_id": None,
        "benefit": benefit_by_type.get(block_type, "Structured session supporting the prescribed activity focus."),
        "steps": [],
        "sets": None,
        "reps": None,
        "time": f"{minutes} minutes",
        "side": None,
        "intensity_rpe": intensity or ("4-6" if block_type in {"cardio", "walking", "class"} else "3-4" if block_type == "yoga" else "2-4"),
        "rest": None,
        "equipment": _txt(equipment) or "None",
        "est_calories": f"{planned_cal}",
        "safety_cue": cue_by_type.get(block_type, "Breathe continuously and keep the session pain-free."),
        "met_value": met,
        "est_time_sec": total_sec,
        "est_time_human": f"{minutes} mins" if minutes > 1 else f"{minutes} min",
        "planned_sets_count": 1,
        "planned_total_cal": planned_cal,
        "_meta": {"session_type": block_type, "is_session_block": True},
    }


def _make_session_block(block: Dict[str, Any], profile: Dict[str, Any]) -> Dict[str, Any]:
    session_type = _session_type_from_block(block)
    duration = block.get("duration") or profile.get("session_duration") or "30 minutes"
    raw = str((block.get("meta") or {}).get("raw_activity") or "").lower()
    title = None
    if session_type == "class" and "yoga" in raw:
        title = "Hatha Yoga Class"
    calories = None
    if profile:
        minutes = _duration_minutes(duration, 30)
        met = {"cardio": 5.0, "walking": 3.5, "yoga": 2.5, "pilates": 3.0, "meditation": 1.3, "class": 4.0, "recovery": 2.0}.get(session_type, 3.0)
        calories = int(round((met * 3.5 * _to_float(profile.get("weight_kg"), 70.0) / 200) * minutes))
    equipment = None
    if session_type in {"cardio", "walking", "class"}:
        equipment = block.get("equipment") or ", ".join(_to_list(profile.get("available_equipment"))) or "None"
    session = _build_session_block(
        session_type,
        duration,
        title=title,
        intensity=block.get("intensity"),
        calories=calories,
        equipment=equipment,
    )
    session["_meta"].update(block.get("meta") or {})
    session["_meta"].update({"source_text": block.get("source_text") or raw})
    return session


def _apply_session_blocks(day_plan: Dict[str, Any], blocks: Sequence[Dict[str, Any]], profile: Dict[str, Any]) -> Dict[str, Any]:
    if not blocks:
        return day_plan
    session_blocks = [
        _make_session_block(block, profile)
        for block in blocks
        if not bool(block.get("requires_dataset_exercises", True))
    ]
    if session_blocks:
        day_plan.setdefault("main_workout", [])
        day_plan["main_workout"].extend(session_blocks)
    title = _note_title_from_blocks(blocks)
    day_plan["workout_title"] = title
    day_plan["main_workout_category"] = title

    total_sec = 0
    total_cal = 0
    for section in ("warmup", "main_workout", "cooldown"):
        for ex in day_plan.get(section, []) or []:
            total_sec += int(ex.get("est_time_sec") or 0)
            total_cal += int(ex.get("planned_total_cal") or 0)
    day_plan["est_total_calories"] = f"{total_cal} est. cal"
    mins = round(total_sec / 60)
    suffix = "mins" if mins > 1 else "min"
    day_plan["est_total_duration"] = f"~{mins} {suffix}"
    return day_plan


def _exercise_identity(value: Any) -> str:
    text = _txt(value).lower()
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _dedupe_day_plan(day_plan: Dict[str, Any]) -> Dict[str, Any]:
    seen: Set[str] = set()
    for section in ("warmup", "main_workout", "cooldown"):
        fixed: List[Dict[str, Any]] = []
        for ex in day_plan.get(section, []) or []:
            key = _exercise_identity((ex or {}).get("name"))
            session_type = ((ex or {}).get("_meta") or {}).get("session_type") if isinstance((ex or {}).get("_meta"), dict) else None
            if session_type:
                key = f"session:{session_type}"
            if not key or key in seen:
                continue
            seen.add(key)
            fixed.append(ex)
        day_plan[section] = fixed
    return day_plan


def _validate_strict_plan_json(plan: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _normalize_plan_json(plan)
    for _, day_data in normalized.items():
        _dedupe_day_plan(day_data)
        for section in ("warmup", "main_workout", "cooldown"):
            fixed = []
            for ex in day_data.get(section, []) or []:
                fixed.append({key: ex.get(key, None) for key in _REQUIRED_EXERCISE_KEYS})
            day_data[section] = fixed
    return normalized


def _exercise_violates_note_safety(exercise: Dict[str, Any], profile: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(exercise, dict):
        return True
    meta = exercise.get("_meta") if isinstance(exercise.get("_meta"), dict) else {}
    if meta.get("is_session_block"):
        return False

    flags = (profile or {}).get("flags") if isinstance((profile or {}).get("flags"), dict) else {}
    restrictions = _to_list((profile or {}).get("physical_limitations"))
    restrictions.extend(_to_list((profile or {}).get("physical_limitation")))
    restrictions.extend(_to_list((profile or {}).get("specific_avoidance")))
    restrictions.extend(_to_list((profile or {}).get("avoid_terms")))
    restrictions.extend(_to_list((profile or {}).get("restrictions")))
    blob = " ".join(
        [
            _txt(exercise.get("name")),
            _txt(exercise.get("benefit")),
            _txt(exercise.get("safety_cue")),
            _txt(exercise.get("equipment")),
            " ".join(_to_list(exercise.get("steps"))),
        ]
    ).lower()

    if _matches_avoidance(blob, restrictions):
        return True
    if flags.get("knee_sensitive") and re.search(r"\b(jump|hop|plyo|burpee|deep\s+squat|lunge)\b", blob):
        return True
    if flags.get("avoid_floor_work") and re.search(r"\b(floor|prone|supine|lying|kneeling|plank|push[\s-]?up|bridge)\b", blob):
        return True
    if flags.get("high_impact_restricted") and re.search(r"\b(high impact|jump|hop|plyo|sprint|running)\b", blob):
        return True
    if flags.get("avoid_spinal_loading") and re.search(r"\b(deadlift|back squat|good morning|loaded spinal|spinal loading)\b", blob):
        return True
    if flags.get("overhead_restricted") and re.search(r"\b(overhead|shoulder press|snatch|clean and press)\b", blob):
        return True
    return False


def _dataset_required_for_note_blocks(blocks: Sequence[Dict[str, Any]]) -> bool:
    return any(bool((block or {}).get("requires_dataset_exercises", True)) for block in blocks)


def _build_note_day(
    *,
    day: str,
    day_index: int,
    activity_blocks: Sequence[Dict[str, Any]],
    profile: Dict[str, Any],
    composer: WorkoutComposer,
    filtered_df: pd.DataFrame,
    tagged_pools: Dict[str, pd.DataFrame],
    base_params: Dict[str, Any],
    global_main_used: Set[str],
    global_warmup_used: Set[str],
    global_cooldown_used: Set[str],
    weekly_usage: Counter,
    shadow_boxing_used: bool,
    session_raw: str,
) -> Tuple[Dict[str, Any], bool]:
    """Central activity-driven daily orchestrator for SOAP/Expert note modes."""
    blocks = [dict(block or {}) for block in activity_blocks or []]
    title = _generate_note_based_title(blocks)
    day_params = dict(base_params)
    block_duration = next((b.get("duration") for b in blocks if b.get("duration")), session_raw)
    day_params["max_main"] = _exercise_count_for_note_blocks(
        block_duration or session_raw,
        blocks,
        len(profile.get("prescribed_exercises") or []),
    )
    day_params["weekly_usage"] = weekly_usage
    day_params["shadow_boxing_used"] = shadow_boxing_used

    composer._active_clinical_context["day_index"] = day_index
    requires_dataset = _dataset_required_for_note_blocks(blocks)

    if requires_dataset:
        day_plan = composer.build_day(
            day,
            _primary_day_type_from_blocks(blocks),
            filtered_df,
            tagged_pools,
            day_params,
            global_main_used,
            global_warmup_used=global_warmup_used,
            global_cooldown_used=global_cooldown_used,
            mandatory_exercises=profile.get("prescribed_exercises") or None,
            is_minimal_plan=False,
            structured_mode=True,
        )
    else:
        day_plan = {
            "day_name": day,
            "workout_title": title,
            "difficulty_level": profile.get("fitness_level", "Beginner"),
            "warmup_duration": "None",
            "main_workout_category": title,
            "cooldown_duration": "None",
            "warmup": [],
            "main_workout": [],
            "cooldown": [],
            "safety_notes": ["Follow clinician-prescribed session guidance."],
            "est_total_calories": "0 est. cal",
            "est_total_duration": "~0 min",
        }

    day_plan = _apply_session_blocks(day_plan, blocks, profile)
    day_plan["workout_title"] = title
    day_plan["main_workout_category"] = title
    day_plan = _dedupe_day_plan(day_plan)

    for section in ("warmup", "main_workout", "cooldown"):
        for ex in day_plan.get(section, []) or []:
            if "shadow boxing" in str(ex.get("name") or "").lower():
                shadow_boxing_used = True
                break

    return day_plan, shadow_boxing_used


def _note_profile_to_engine_profile(profile: Any, raw_text: str = "") -> Dict[str, Any]:
    if isinstance(profile, ParsedNoteProfile):
        return _map_note_profile_to_fitness_profile(profile, raw_text)
    if isinstance(profile, dict) and "parsed_note_profile" in profile:
        return dict(profile)
    if isinstance(profile, dict):
        return _map_note_profile_to_fitness_profile(_parsed_note_profile(profile, raw_text, str(profile.get("source_type") or "expert_note")), raw_text)
    raise ValueError("profile must be a ParsedNoteProfile or parsed note dict")


def _build_note_week(
    note_profile: Any,
    raw_text: str = "",
    *,
    seed: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Central notes-mode weekly orchestrator.

    The note weekly_schedule is the source of truth. This function does not call
    WorkoutPlanner.get_weekly_split and does not synthesize intake-style splits.
    """
    profile = _note_profile_to_engine_profile(note_profile, raw_text)
    profile["generation_mode"] = profile.get("generation_mode") or profile.get("input_mode") or "expert_note"
    profile["note_mode"] = True

    note_blocks_by_day = profile.get("note_activity_blocks") if isinstance(profile.get("note_activity_blocks"), dict) else {}
    days = [day for day in _NOTE_DAY_ORDER if day in note_blocks_by_day]
    if not days:
        days = [day for day in _NOTE_DAY_ORDER if day in (profile.get("weekly_schedule") or {})]
    if not days:
        raise RuntimeError("No scheduled note activity blocks were found")

    df = FitnessDataset.load()
    if df.empty:
        raise RuntimeError("Dataset load failed")
    filtered_df = ExerciseFilter.apply_filters(df, profile)
    if filtered_df.empty:
        filtered_df = df.copy()

    tagged_pools = {
        "warmup": filtered_df[filtered_df["Tags"].str.contains(
            r"warm|mobility|dynamic|activation",
            case=False,
            na=False,
        )],
        "cooldown": filtered_df[filtered_df["Tags"].str.contains(r"cool\s*down|stretch", case=False, na=False, regex=True)],
    }

    planner = WorkoutPlanner()
    sets, reps, rpe, rest = planner.get_volume_intensity(
        profile.get("primary_goal", ""), profile.get("fitness_level", "")
    )
    session_raw = _txt(profile.get("session_duration")) or "45-60 minutes"
    params = {
        "sets": sets,
        "reps": reps,
        "rpe": rpe,
        "rest": rest,
        "max_main": 6,
        "weight": profile.get("weight_kg", 70),
        "fitness_level": profile.get("fitness_level", "Beginner"),
        "_profile": profile,
    }

    selector = ExerciseSelector(random_seed=seed)
    composer = WorkoutComposer(selector)
    composer._dataset = df
    clinical_terms = []
    for key in ("physical_limitations", "physical_limitation", "specific_avoidance", "avoid_terms", "restrictions"):
        clinical_terms.extend(_to_list(profile.get(key)))
    composer._active_clinical_context = {
        "flags": profile.get("flags") if isinstance(profile.get("flags"), dict) else {},
        "conditions": profile.get("fitness_medical_conditions") or [],
        "fitness_medical_conditions": profile.get("fitness_medical_conditions") or [],
        "limitations": clinical_terms,
        "physical_limitation": profile.get("physical_limitation") or ", ".join(clinical_terms),
        "physical_limitations": profile.get("physical_limitations") or clinical_terms,
        "generation_mode": profile["generation_mode"],
    }

    plans_json: Dict[str, Any] = {}
    global_main_used: Set[str] = set()
    global_warmup_used: Set[str] = set()
    global_cooldown_used: Set[str] = set()
    weekly_usage: Counter = Counter()
    shadow_boxing_used = False

    for i, day in enumerate(days):
        plans_json[day], shadow_boxing_used = _build_note_day(
            day=day,
            day_index=i,
            activity_blocks=note_blocks_by_day.get(day, []),
            profile=profile,
            composer=composer,
            filtered_df=filtered_df,
            tagged_pools=tagged_pools,
            base_params=params,
            global_main_used=global_main_used,
            global_warmup_used=global_warmup_used,
            global_cooldown_used=global_cooldown_used,
            weekly_usage=weekly_usage,
            shadow_boxing_used=shadow_boxing_used,
            session_raw=session_raw,
        )
        params["shadow_boxing_used"] = shadow_boxing_used

    validated = _validate_note_plan(plans_json, note_blocks_by_day, profile)
    return _normalize_plan_json(validated)


def _validate_note_plan(plan: Dict[str, Any], note_schedule: Dict[str, Any], profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Final strict validation for note-generated plans."""
    normalized = dict(plan or {})
    allowed_days = [day for day in _NOTE_DAY_ORDER if day in (note_schedule or {})]
    if allowed_days:
        normalized = {day: normalized[day] for day in allowed_days if day in normalized}

    avoid_terms = _to_list((profile or {}).get("avoid_terms") or (profile or {}).get("specific_avoidance") or [])
    for day, day_data in normalized.items():
        blocks = note_schedule.get(day, []) if isinstance(note_schedule, dict) else []
        expected_title = _generate_note_based_title(blocks)
        day_data["workout_title"] = expected_title
        day_data["main_workout_category"] = expected_title
        session_seen: Set[str] = set()
        for section in ("warmup", "main_workout", "cooldown"):
            fixed: List[Dict[str, Any]] = []
            seen_names: Set[str] = set()
            for ex in day_data.get(section, []) or []:
                ex = dict(ex or {})
                meta = ex.get("_meta") if isinstance(ex.get("_meta"), dict) else {}
                session_type = meta.get("session_type") if meta.get("is_session_block") else None
                if session_type:
                    if session_type not in {"cardio", "walking", "yoga", "pilates", "meditation", "class", "recovery"}:
                        continue
                    if session_type in session_seen:
                        continue
                    session_seen.add(session_type)
                    for key in _REQUIRED_EXERCISE_KEYS:
                        ex.setdefault(key, None)
                    ex["_meta"] = {**meta, "session_type": session_type, "is_session_block": True}
                else:
                    for key in _REQUIRED_EXERCISE_KEYS:
                        ex.setdefault(key, None)
                name_key = _exercise_identity(ex.get("name"))
                if not name_key or name_key in seen_names:
                    continue
                if avoid_terms and _matches_avoidance(name_key, avoid_terms):
                    continue
                if _exercise_violates_note_safety(ex, profile):
                    continue
                seen_names.add(name_key)
                fixed.append(ex)
            day_data[section] = fixed

        total_sec = 0
        total_cal = 0
        for section in ("warmup", "main_workout", "cooldown"):
            for ex in day_data.get(section, []) or []:
                total_sec += _to_int(ex.get("est_time_sec"), 0)
                total_cal += _to_int(ex.get("planned_total_cal"), 0)
        day_data["est_total_calories"] = f"{total_cal} est. cal"
        mins = round(total_sec / 60)
        suffix = "mins" if mins > 1 else "min"
        day_data["est_total_duration"] = f"~{mins} {suffix}"
    return normalized


def _resolve_or_schedule(value: Any) -> str:
    """
    Resolve note schedule alternatives like "Yoga Mon/Wed/Fri OR Tue/Thu".

    Notes mode must pick one branch, never union both sides. The first branch is
    treated as the explicit sample week unless it is unusable.
    """
    line = _txt(value)
    if not re.search(r"\bor\b", line, re.I):
        return line

    day_pattern = r"\b(?:mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|thu(?:rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)\b"
    activity_match = re.search(rf"\b({_ACTIVITY_KEYWORDS})\b", line, re.I)
    inherited_activity = activity_match.group(1) if activity_match else ""
    prefix = ""
    if activity_match:
        first_day = re.search(day_pattern, line, re.I)
        if first_day and activity_match.start() < first_day.start():
            prefix = line[:first_day.start()].strip(" :-,\t;")

    branches = [p.strip(" ,;") for p in re.split(r"\bor\b", line, flags=re.I) if p.strip(" ,;")]
    scored: List[Tuple[int, int, str]] = []
    for idx, branch in enumerate(branches):
        branch_days = re.findall(day_pattern, branch, flags=re.I)
        if not branch_days:
            continue
        branch_has_activity = bool(re.search(rf"\b({_ACTIVITY_KEYWORDS})\b", branch, re.I))
        candidate = branch
        if not branch_has_activity and prefix:
            candidate = f"{prefix} {branch}"
        elif not branch_has_activity and inherited_activity:
            candidate = f"{inherited_activity} {branch}"
        day_count = len(branch_days)
        plausible = 1 <= day_count <= 5
        score = (100 if plausible else 0) - (idx * 10) - max(0, day_count - 3)
        scored.append((score, -idx, candidate))

    if not scored:
        return line
    scored.sort(reverse=True)
    return scored[0][2]


def _choose_or_branch(line: str) -> str:
    return _resolve_or_schedule(line)

def _active_days_from_parsed(parsed: Dict[str, Any]) -> List[str]:
    """Return concrete training days for the existing planner."""
    schedule = parsed.get("weekly_schedule") if isinstance(parsed.get("weekly_schedule"), dict) else {}
    days = _active_days(schedule)
    if days:
        return days
    return _distributed_days(_to_int(parsed.get("days_per_week"), 3))


def _parsed_note_profile(parsed: Dict[str, Any], raw_text: str, source_type: str) -> ParsedNoteProfile:
    return _build_activity_blocks(parsed, raw_text, source_type)


def _map_note_profile_to_fitness_profile(note_profile: ParsedNoteProfile, raw_text: str = "") -> Dict[str, Any]:
    """
    Convert a normalized clinical note profile into the schema consumed by the
    existing FitnessPlanGeneratorTool, while preserving the normalized note
    fields for callers that inspect the generated profile.
    """
    goal = note_profile.goal or "General Fitness"
    days = [day for day in _NOTE_DAY_ORDER if day in note_profile.weekly_schedule]
    fitness_medical_conditions = list(note_profile.fitness_medical_conditions or [])
    physical_limitations = list(note_profile.physical_limitations or [])
    avoid_terms = list(note_profile.avoid_terms or [])
    equipment = list(note_profile.available_equipment or ["Bodyweight"])

    physical_limitation_text = ", ".join(
        _merge_unique(physical_limitations, avoid_terms)
    )

    return {
        "goal": goal,
        "days_per_week": days,
        "session_duration": note_profile.session_duration or "45-60 minutes",
        "fitness_medical_conditions": fitness_medical_conditions,
        "physical_limitations": physical_limitations,
        "specific_avoidance": avoid_terms,
        "available_equipment": equipment,
        "focus": [],
        "weekly_schedule": note_profile.weekly_schedule,
        "prescribed_exercises": list(note_profile.prescribed_exercises or []),
        "restrictions": avoid_terms,
        "avoid_terms": avoid_terms,
        "flags": dict(note_profile.flags or {}),
        "input_mode": note_profile.source_type,
        "generation_mode": note_profile.source_type,
        "note_mode": True,
        "note_activity_blocks": note_profile.weekly_schedule,
        "parsed_note_profile": _model_dump_compat(note_profile),

        "primary_goal": goal,
        "fitness_level": _infer_level(raw_text),
        "DaysOfTrain": days,
        "fitness_medical_conditions": fitness_medical_conditions,
        "physical_limitation": physical_limitation_text,
    }


def _map_parsed_to_fitness_profile(parsed: Dict[str, Any], raw_text: str = "", source_type: str = "expert_note") -> Dict[str, Any]:
    return _map_note_profile_to_fitness_profile(_parsed_note_profile(parsed, raw_text, source_type), raw_text)


def _run_async_sync(coro):
    """Run an async engine call from sync entrypoints, even inside a live loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    import concurrent.futures

    def runner():
        return asyncio.run(coro)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(runner).result()


def generate_workout_plan(profile: Dict[str, Any]) -> dict:
    """
    Public synchronous facade for the existing advanced workout planner.

    This does not create a separate planner. It delegates to
    FitnessPlanGeneratorTool, which uses FitnessDataset, ExerciseFilter,
    ExerciseSelector, WorkoutComposer, rotation, formatter and normalization.
    """
    if not isinstance(profile, dict) or not profile:
        raise ValueError("profile must be a non-empty dict")

    tool = FitnessPlanGeneratorTool()
    result = _run_async_sync(tool.execute(profile=profile))
    if not getattr(result, "success", False):
        raise RuntimeError(getattr(result, "error", None) or "Workout generation failed")

    plan = (getattr(result, "data", {}) or {}).get("plans_json") or {}
    if not plan:
        raise RuntimeError("Workout generation returned an empty plan")
    return _validate_strict_plan_json(plan)


def generate_expert_note_plan(raw_text: str) -> dict:
    """
    Generate a strict JSON workout plan from raw Expert Note text.

    Flow:
    raw_text -> _parse_note -> ParsedNoteProfile -> ActivityBlocks
    -> _build_note_week -> _validate_note_plan -> _normalize_plan_json.

    Notes mode uses the clinician schedule as the source of truth and never
    calls the intake weekly split planner.
    
    Args:
        raw_text: Clinical expert note as raw string
    
    Returns:
        dict: Strict JSON workout plan keyed by scheduled training day.
    """
    try:
        if not raw_text or not isinstance(raw_text, str) or not raw_text.strip():
            raise ValueError("raw_text must be a non-empty string")

        parsed = _parse_note(raw_text, mode="expert")
        note_profile = _build_activity_blocks(parsed, raw_text, "expert_note")
        plan = _build_note_week(note_profile, raw_text, seed=_stable_seed("expert_note", raw_text))
        return _validate_strict_plan_json(plan)
    except Exception as e:
        logger.error(f"Error in generate_expert_note_plan: {str(e)}")
        raise


def generate_soap_note_plan(raw_text: str) -> dict:
    """
    Generate a strict JSON workout plan from raw SOAP Note text.

    Flow:
    raw_text -> _parse_note -> ParsedNoteProfile -> ActivityBlocks
    -> _build_note_week -> _validate_note_plan -> _normalize_plan_json.

    SOAP mode uses SOAP-specific restriction parsing and never calls the
    intake weekly split planner when the note supplies a schedule.
    
    Args:
        raw_text: Clinical SOAP note as raw string
    
    Returns:
        dict: Strict JSON workout plan keyed by scheduled training day.
    """
    try:
        if not raw_text or not isinstance(raw_text, str) or not raw_text.strip():
            raise ValueError("raw_text must be a non-empty string")

        parsed = _parse_note(raw_text, mode="soap")
        note_profile = _build_activity_blocks(parsed, raw_text, "soap_note")
        plan = _build_note_week(note_profile, raw_text, seed=_stable_seed("soap_note", raw_text))
        return _validate_strict_plan_json(plan)
    except Exception as e:
        logger.error(f"Error in generate_soap_note_plan: {str(e)}")
        raise