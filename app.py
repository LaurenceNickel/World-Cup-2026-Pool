from __future__ import annotations

import ast
import base64
import hashlib
import html
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

import altair as alt
import pandas as pd
import streamlit as st

try:
    import gspread
    from gspread.exceptions import APIError, WorksheetNotFound
except ImportError:
    gspread = None
    APIError = Exception
    WorksheetNotFound = Exception


DATA_DIR = Path("data")
PREDICTIONS_DIR = DATA_DIR / "predictions"
AI_PREDICTIONS_DIR = DATA_DIR / "ai_predictions"
STANDINGS_DIR = DATA_DIR / "standings"
DRAFTS_DIR = DATA_DIR / "drafts"

TEAMS_FILE = DATA_DIR / "teams.csv"
MATCHES_FILE = DATA_DIR / "matches.csv"
RESULTS_FILE = DATA_DIR / "results.csv"
USERS_FILE = DATA_DIR / "users.csv"
KNOCKOUT_MATCHUPS_FILE = DATA_DIR / "knockout_matchups.csv"
THIRD_PLACE_COMBINATIONS_FILE = DATA_DIR / "third_place_combinations.csv"
CONFIG_FILE = DATA_DIR / "config.json"
LEADERBOARD_FILE = DATA_DIR / "leaderboard.csv"

SHEET_BACKED_FILES = {
    USERS_FILE: "users",
    RESULTS_FILE: "results",
    LEADERBOARD_FILE: "leaderboard",
}
CONFIG_SHEET = "config"
DRAFTS_SHEET = "drafts"
PREDICTIONS_SHEET = "predictions"
LEADERBOARD_CACHE_SHEET = "leaderboard_snapshot_cache"


class GoogleSheetsRateLimitError(RuntimeError):
    pass

GROUPS = list("ABCDEFGHIJKL")
GROUP_STAGE = "group_stage"
KNOCKOUT_STAGES = [
    "round_of_32",
    "round_of_16",
    "quarter_final",
    "semi_final",
    "third_place",
    "final",
]
STAGES = [GROUP_STAGE, *KNOCKOUT_STAGES]
STAGE_LABELS = {
    GROUP_STAGE: "Group stage",
    "round_of_32": "Round of 32",
    "round_of_16": "Round of 16",
    "quarter_final": "Quarter-final",
    "semi_final": "Semi-final",
    "third_place": "Third place",
    "final": "Final",
}
GROUP_STANDING_POSITION_POINTS = 2
MATCH_OUTCOME_POINTS = 3
MATCH_HOME_GOALS_POINTS = 1
MATCH_AWAY_GOALS_POINTS = 1
KNOCKOUT_STAGE_POINTS = {
    "round_of_16": 5,
    "quarter_final": 10,
    "semi_final": 20,
    "final": 25,
}
THIRD_PLACE_MATCH_ID = "M103"
FINAL_MATCH_ID = "M104"
THIRD_PLACE_WINNER_POINTS = 25
CHAMPION_POINTS = 35
FINISHING_STAGE_ORDER = [
    "Group stage",
    "Round of 32",
    "Round of 16",
    "Quarter-final",
    "Semi-final",
    "Final",
    "Winner",
]

USERS_COLUMNS = ["user_id", "user_name", "total_points"]
PENALTY_WINNER_COLUMN = "penalty_winner"
PREDICTION_COLUMNS = ["user_id", "match_id", "home_goals", "away_goals", PENALTY_WINNER_COLUMN]
DRAFT_QUERY_PARAM = "draft"
PREDICTION_SESSION_VALUES_KEY = "prediction_session_values"
PREDICTION_SESSION_VALUES_USER_KEY = "prediction_session_values_user_id"
STANDING_COLUMNS = [
    "team_id",
    "games_played",
    "wins",
    "draws",
    "losses",
    "goals_for",
    "goals_against",
    "goal_difference",
    "fair_play_score",
    "points",
]
CARD_COLUMNS = [
    "home_yellow_cards",
    "home_indirect_red_cards",
    "home_direct_red_cards",
    "away_yellow_cards",
    "away_indirect_red_cards",
    "away_direct_red_cards",
]
CACHE_SCHEMA_VERSION = "blank-score-widgets-v8"


DEFAULT_THEME = {
    "background": "#f6f8fb",
    "background_soft": "#eaf0f6",
    "panel": "#ffffff",
    "primary": "#002b55",
    "primary_soft": "#063866",
    "accent": "#f04b0b",
    "accent_hover": "#d84008",
    "text": "#082b4c",
    "muted": "#5b6d7d",
    "border": "#d7e1ea",
}

TIMELINE_COLORS = [
    "#4e79a7",
    "#a0cbe8",
    "#f28e2b",
    "#ffbe7d",
    "#59a14f",
    "#8cd17d",
    "#b6992d",
    "#f1ce63",
    "#499894",
    "#86bcb6",
    "#e15759",
    "#ff9d9a",
    "#79706e",
    "#bab0ac",
    "#d37295",
    "#fabfd2",
    "#b07aa1",
    "#d4a6c8",
    "#9d7660",
    "#d7b5a6",
]


def ensure_csv_columns(path: Path, columns: list[str]) -> None:
    if not path.exists():
        return
    table = pd.read_csv(path, dtype=str).fillna("")
    missing_columns = [column for column in columns if column not in table.columns]
    if not missing_columns:
        return
    for column in missing_columns:
        table[column] = ""
    table.to_csv(path, index=False)


def apply_visual_theme() -> None:
    theme = DEFAULT_THEME
    css = """
        <style>
        :root {
            color-scheme: light;
            --pool-bg: __BACKGROUND__;
            --pool-bg-soft: __BACKGROUND_SOFT__;
            --pool-panel: __PANEL__;
            --pool-primary: __PRIMARY__;
            --pool-primary-soft: __PRIMARY_SOFT__;
            --pool-accent: __ACCENT__;
            --pool-accent-hover: __ACCENT_HOVER__;
            --pool-ink: __TEXT__;
            --pool-muted: __MUTED__;
            --pool-border: __BORDER__;
        }

        html,
        body,
        .stApp {
            color-scheme: light;
        }

        html[data-theme="dark"],
        body[data-theme="dark"],
        [data-theme="dark"],
        [data-baseweb] {
            color-scheme: light !important;
        }

        .stApp {
            color: var(--pool-ink);
            background:
                radial-gradient(circle at 9% 8%, color-mix(in srgb, var(--pool-accent) 18%, transparent), transparent 21rem),
                radial-gradient(circle at 94% 12%, color-mix(in srgb, var(--pool-primary) 14%, transparent), transparent 28rem),
                linear-gradient(135deg, var(--pool-bg) 0%, var(--pool-bg-soft) 56%, var(--pool-panel) 100%);
        }

        [data-testid="stHeader"] {
            background: var(--pool-primary);
            border-bottom: 5px solid var(--pool-accent);
        }

        [data-testid="stHeader"] button,
        [data-testid="stHeader"] [role="button"] {
            color: #ffffff !important;
        }

        [data-testid="stHeader"] svg {
            color: #ffffff !important;
        }

        [data-testid="stHeader"] svg path,
        [data-testid="stHeader"] svg line,
        [data-testid="stHeader"] svg polyline,
        [data-testid="stHeader"] svg polygon {
            stroke: #ffffff !important;
            fill: none !important;
        }

        [data-testid="stHeader"] svg circle {
            fill: #ffffff !important;
            stroke: #ffffff !important;
        }

        [data-testid="stHeader"] svg rect {
            fill: transparent !important;
            stroke: none !important;
        }

        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, var(--pool-primary) 0%, var(--pool-primary-soft) 100%);
            border-right: 5px solid var(--pool-accent);
        }

        [data-testid="stSidebar"] * {
            color: #f7fbf8;
        }

        [data-testid="stSidebar"] .stRadio label {
            color: #f7fbf8;
        }

        [data-testid="stSidebarCollapseButton"] svg,
        [data-testid="stSidebarCollapsedControl"] svg,
        [data-testid="collapsedControl"] svg,
        button[kind="header"] svg,
        button[aria-label*="sidebar" i] svg,
        button[aria-label*="navigation" i] svg {
            color: #ffffff !important;
        }

        [data-testid="stSidebarCollapseButton"] svg path,
        [data-testid="stSidebarCollapseButton"] svg line,
        [data-testid="stSidebarCollapseButton"] svg polyline,
        [data-testid="stSidebarCollapsedControl"] svg path,
        [data-testid="stSidebarCollapsedControl"] svg line,
        [data-testid="stSidebarCollapsedControl"] svg polyline,
        [data-testid="collapsedControl"] svg path,
        [data-testid="collapsedControl"] svg line,
        [data-testid="collapsedControl"] svg polyline,
        button[kind="header"] svg path,
        button[kind="header"] svg line,
        button[kind="header"] svg polyline,
        button[aria-label*="sidebar" i] svg path,
        button[aria-label*="sidebar" i] svg line,
        button[aria-label*="sidebar" i] svg polyline,
        button[aria-label*="navigation" i] svg path,
        button[aria-label*="navigation" i] svg line,
        button[aria-label*="navigation" i] svg polyline {
            stroke: #ffffff !important;
            fill: none !important;
        }

        [data-testid="stSidebarCollapseButton"] svg path,
        [data-testid="stSidebarCollapsedControl"] svg path,
        [data-testid="collapsedControl"] svg path,
        button[aria-label*="sidebar" i] svg path,
        button[aria-label*="navigation" i] svg path {
            fill: #ffffff !important;
        }

        [data-testid="stSidebarCollapseButton"] svg circle,
        [data-testid="stSidebarCollapsedControl"] svg circle,
        [data-testid="collapsedControl"] svg circle,
        button[kind="header"] svg circle,
        button[aria-label*="sidebar" i] svg circle,
        button[aria-label*="navigation" i] svg circle {
            fill: #ffffff !important;
            stroke: #ffffff !important;
        }

        [data-testid="stSidebarCollapseButton"] button,
        [data-testid="stSidebarCollapsedControl"] button,
        [data-testid="collapsedControl"] button,
        button[aria-label*="sidebar" i],
        button[aria-label*="navigation" i] {
            color: #ffffff !important;
            background: color-mix(in srgb, #ffffff 14%, transparent);
        }

        [data-testid="collapsedControl"],
        [data-testid="collapsedControl"] *,
        [data-testid="stSidebarCollapseButton"],
        [data-testid="stSidebarCollapseButton"] *,
        [data-testid="stSidebarCollapsedControl"],
        [data-testid="stSidebarCollapsedControl"] *,
        button[aria-label="Open sidebar"],
        button[aria-label="Open sidebar"] *,
        button[aria-label="Close sidebar"],
        button[aria-label="Close sidebar"] *,
        button[title="Open sidebar"],
        button[title="Open sidebar"] *,
        button[title="Close sidebar"],
        button[title="Close sidebar"] * {
            color: #ffffff !important;
        }

        [data-testid="collapsedControl"] svg,
        [data-testid="collapsedControl"] svg *,
        [data-testid="stSidebarCollapseButton"] svg,
        [data-testid="stSidebarCollapseButton"] svg *,
        [data-testid="stSidebarCollapsedControl"] svg,
        [data-testid="stSidebarCollapsedControl"] svg *,
        button[aria-label="Open sidebar"] svg,
        button[aria-label="Open sidebar"] svg *,
        button[aria-label="Close sidebar"] svg,
        button[aria-label="Close sidebar"] svg *,
        button[title="Open sidebar"] svg,
        button[title="Open sidebar"] svg *,
        button[title="Close sidebar"] svg,
        button[title="Close sidebar"] svg * {
            color: #ffffff !important;
            fill: #ffffff !important;
            stroke: #ffffff !important;
        }

        [data-testid="stHeader"] span[data-testid="stIconMaterial"],
        [data-testid="collapsedControl"] span[data-testid="stIconMaterial"],
        [data-testid="stSidebarCollapsedControl"] span[data-testid="stIconMaterial"],
        button[aria-label="Open sidebar"] span[data-testid="stIconMaterial"],
        button[title="Open sidebar"] span[data-testid="stIconMaterial"],
        [data-testid="stHeader"] .material-symbols-rounded,
        [data-testid="collapsedControl"] .material-symbols-rounded,
        [data-testid="stSidebarCollapsedControl"] .material-symbols-rounded,
        button[aria-label="Open sidebar"] .material-symbols-rounded,
        button[title="Open sidebar"] .material-symbols-rounded {
            color: #ffffff !important;
            -webkit-text-fill-color: #ffffff !important;
        }

        button[kind="header"] {
            color: #ffffff !important;
        }

        .block-container {
            padding-top: 2rem;
            padding-bottom: 3rem;
        }

        h1, h2, h3 {
            color: var(--pool-primary);
            letter-spacing: 0;
        }

        .app-title-row {
            display: flex;
            align-items: center;
            gap: 1rem;
            margin-top: 1rem;
            margin-bottom: 2rem;
        }

        .app-title-bar {
            width: 10px;
            height: 6.8rem;
            background: var(--pool-accent);
            flex: 0 0 10px;
            transform: translateY(-1rem);
        }

        .app-title-text {
            line-height: 1.15;
            font-size: 3rem;
            font-weight: 700;
            color: var(--pool-primary);
            letter-spacing: 0;
        }

        div[data-testid="stVerticalBlockBorderWrapper"],
        div[data-testid="stExpander"],
        div[data-testid="stDataFrame"],
        div[data-testid="stForm"] {
            background-color: color-mix(in srgb, var(--pool-panel) 94%, transparent);
            border: 1px solid var(--pool-border);
            border-radius: 8px;
            box-shadow: 0 10px 28px color-mix(in srgb, var(--pool-primary) 6%, transparent);
        }

        div[data-testid="stDataFrame"] {
            overflow: hidden;
        }

        div[data-testid="stExpander"] details,
        div[data-testid="stExpander"] summary,
        div[data-testid="stExpander"] [data-testid="stExpanderDetails"],
        div[data-testid="stExpander"] [data-testid="stExpanderToggleIcon"] {
            background: #ffffff !important;
            color: #000000 !important;
        }

        div[data-testid="stExpander"] summary {
            border-bottom: 1px solid var(--pool-border);
        }

        div[data-testid="stExpander"] summary *,
        div[data-testid="stExpander"] [data-testid="stExpanderDetails"] * {
            color: #000000;
        }

        .stButton > button,
        .stFormSubmitButton > button {
            color: #ffffff;
            background: linear-gradient(135deg, var(--pool-accent), var(--pool-primary));
            border: 0;
            border-radius: 8px;
            font-weight: 700;
        }

        .stButton > button:hover,
        .stFormSubmitButton > button:hover {
            color: #ffffff;
            border: 0;
            background: linear-gradient(135deg, var(--pool-accent-hover), var(--pool-primary));
        }

        textarea {
            background-color: var(--pool-primary);
            border-color: var(--pool-primary);
            color: #ffffff;
        }

        textarea {
            color: #ffffff;
            caret-color: #ffffff;
        }

        textarea::placeholder {
            color: rgba(255, 255, 255, 0.7);
        }

        div[data-testid="stNumberInput"] > div:has(input) {
            border: 1px solid #b8c6d3;
            border-radius: 8px;
            overflow: hidden;
            box-shadow: none;
        }

        div[data-testid="stNumberInput"] > div:has(input):focus-within {
            border-color: var(--pool-accent);
            box-shadow: 0 0 0 1px color-mix(in srgb, var(--pool-accent) 45%, transparent);
        }

        div[data-testid="stNumberInput"] div[data-baseweb="input"] {
            border: 0 !important;
            box-shadow: none !important;
        }

        div[data-testid="stTextInput"] div[data-baseweb="input"] {
            border: 1px solid #b8c6d3;
            border-radius: 8px;
            box-shadow: none;
        }

        div[data-testid="stTextInput"] div[data-baseweb="input"]:focus-within {
            border-color: var(--pool-accent);
            box-shadow: 0 0 0 1px color-mix(in srgb, var(--pool-accent) 45%, transparent);
        }

        div[data-testid="stTextInput"] input {
            border: 0;
            background: #ffffff;
            color: #000000;
            box-shadow: none;
        }

        div[data-testid="stTextInput"] label {
            font-size: 1rem;
            font-weight: 700;
            color: #000000;
        }

        div[data-testid="stSelectbox"] div[data-baseweb="select"],
        div[data-testid="stMultiSelect"] div[data-baseweb="select"] {
            border: 1px solid #b8c6d3;
            border-radius: 8px;
            box-shadow: none;
            background: #ffffff;
            color: #000000;
        }

        div[data-testid="stSelectbox"] div[data-baseweb="select"]:focus-within,
        div[data-testid="stMultiSelect"] div[data-baseweb="select"]:focus-within {
            border-color: var(--pool-accent);
            box-shadow: 0 0 0 1px color-mix(in srgb, var(--pool-accent) 45%, transparent);
        }

        div[data-testid="stSelectbox"] label,
        div[data-testid="stMultiSelect"] label {
            font-size: 1rem;
            font-weight: 700;
            color: #000000;
        }

        div[data-baseweb="popover"],
        div[data-baseweb="popover"] *,
        ul[role="listbox"],
        ul[role="listbox"] * {
            background-color: #ffffff !important;
            color: #000000 !important;
        }

        div[data-testid="stAlert"] {
            border-radius: 8px;
            border-left: 5px solid var(--pool-accent);
        }

        [data-testid="stCaptionContainer"] {
            color: var(--pool-muted);
        }

        .match-label {
            color: #000000;
            font-weight: 600;
            margin: 1rem 0 0.35rem;
        }

        .readonly-score-label {
            color: #000000;
            font-size: 0.88rem;
            font-weight: 600;
            margin-bottom: 0.25rem;
        }

        .readonly-score-box {
            min-height: 2.45rem;
            display: flex;
            align-items: center;
            padding: 0.35rem 0.75rem;
            margin-bottom: 0.75rem;
            border: 1px solid var(--pool-border);
            border-radius: 8px;
            background: #f7f9fc;
            color: #000000;
            font-size: 1rem;
            font-weight: 600;
        }

        .team-badge {
            display: inline-flex;
            align-items: center;
            min-width: 0;
            gap: 0.25rem;
            vertical-align: middle;
        }

        .team-badge img {
            width: 1.35rem;
            height: 1.35rem;
            object-fit: contain;
            flex: 0 0 auto;
        }

        .pool-table {
            width: 100%;
            table-layout: fixed;
            border-collapse: collapse;
            background: var(--pool-panel);
            border: 1px solid var(--pool-border);
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 10px 28px color-mix(in srgb, var(--pool-primary) 6%, transparent);
            font-size: 0.82rem;
            color: #000000;
        }

        .pool-table th,
        .pool-table td {
            border-bottom: 1px solid var(--pool-border);
            padding: 0.48rem 0.45rem;
            text-align: left;
            vertical-align: middle;
        }

        .pool-table th {
            background: #f7f9fc;
            color: #000000;
            font-weight: 700;
            white-space: normal;
            line-height: 1.05;
        }

        .pool-table th:nth-child(1),
        .pool-table td:nth-child(1) {
            width: 10%;
        }

        .pool-table th:nth-child(2),
        .pool-table td:nth-child(2) {
            width: 30%;
        }

        .pool-table th:nth-child(3),
        .pool-table td:nth-child(3) {
            width: 12%;
        }

        .pool-table th:nth-child(4),
        .pool-table td:nth-child(4) {
            width: 12%;
        }

        .pool-table th:nth-child(5),
        .pool-table td:nth-child(5) {
            width: 12%;
        }

        .pool-table th:nth-child(6),
        .pool-table td:nth-child(6) {
            width: 12%;
        }

        .pool-table th:nth-child(7),
        .pool-table td:nth-child(7) {
            width: 12%;
        }

        .third-place-table th:nth-child(1),
        .third-place-table td:nth-child(1) {
            width: 10%;
        }

        .third-place-table th:nth-child(2),
        .third-place-table td:nth-child(2) {
            width: 10%;
        }

        .third-place-table th:nth-child(3),
        .third-place-table td:nth-child(3) {
            width: 30%;
        }

        .third-place-table th:nth-child(4),
        .third-place-table td:nth-child(4) {
            width: 10%;
        }

        .third-place-table th:nth-child(5),
        .third-place-table td:nth-child(5) {
            width: 10%;
        }

        .third-place-table th:nth-child(6),
        .third-place-table td:nth-child(6) {
            width: 10%;
        }

        .third-place-table th:nth-child(7),
        .third-place-table td:nth-child(7) {
            width: 10%;
        }

        .third-place-table th:nth-child(8),
        .third-place-table td:nth-child(8) {
            width: 10%;
        }

        .pool-table td.numeric,
        .pool-table th.numeric {
            text-align: center;
            white-space: nowrap;
        }

        .pool-table .points-column {
            font-weight: 700;
        }

        .leaderboard-table {
            width: 100%;
            border-collapse: separate;
            border-spacing: 0;
            overflow: hidden;
            border: 1px solid var(--pool-border);
            border-radius: 8px;
            background: var(--pool-panel);
            box-shadow: 0 10px 28px color-mix(in srgb, var(--pool-primary) 6%, transparent);
            color: #000000;
            font-size: 0.9rem;
        }

        .leaderboard-table th,
        .leaderboard-table td {
            padding: 0.72rem 0.75rem;
            border-right: 1px solid var(--pool-border);
            border-bottom: 1px solid var(--pool-border);
            text-align: center;
            vertical-align: middle;
        }

        .leaderboard-table th {
            background: #f7f9fc;
            color: #6b7280;
            font-weight: 500;
        }

        .leaderboard-table td.left,
        .leaderboard-table th.left {
            text-align: left;
        }

        .leaderboard-table th.bold,
        .leaderboard-table td.bold {
            font-weight: 800;
        }

        .leaderboard-table tr.ai-row td {
            background: #e9f5ff;
        }

        .leaderboard-table th:last-child,
        .leaderboard-table td:last-child {
            border-right: 0;
        }

        .leaderboard-table tbody tr:last-child td {
            border-bottom: 0;
        }

        .knockout-progression-table {
            table-layout: fixed;
        }

        .knockout-progression-table th:nth-child(1),
        .knockout-progression-table td:nth-child(1) {
            width: 13%;
        }

        .knockout-progression-table th:nth-child(2),
        .knockout-progression-table td:nth-child(2) {
            width: 9%;
        }

        .knockout-progression-table th:nth-child(3),
        .knockout-progression-table td:nth-child(3) {
            width: 12%;
        }

        .knockout-progression-table th:nth-child(4),
        .knockout-progression-table td:nth-child(4) {
            width: 36%;
        }

        .knockout-progression-table th:nth-child(5),
        .knockout-progression-table td:nth-child(5) {
            width: 30%;
        }

        .knockout-progression-table td,
        .knockout-progression-table th {
            overflow-wrap: anywhere;
        }

        .knockout-progression-table details summary {
            cursor: pointer;
            font-weight: 600;
            white-space: nowrap;
        }

        .knockout-detail-results {
            margin-top: 0.55rem;
            line-height: 1.55;
        }

        .team-progress-list {
            display: flex;
            flex-wrap: wrap;
            gap: 0.28rem 0.38rem;
        }

        .team-progress-chip {
            display: inline-block;
            line-height: 1.25;
            font-weight: 600;
        }

        .team-progress-chip.advanced {
            color: #167a3a;
        }

        .team-progress-chip.eliminated {
            color: #b42318;
        }

        .team-progress-chip.pending {
            color: #000000;
        }

        .figure-pad {
            padding: 0.75rem 0.65rem 1.1rem;
            margin: 0.35rem 0 1.25rem;
            background: transparent;
        }

        div[data-testid="stVegaLiteChart"] {
            overflow-x: auto;
        }

        .endgame-position-scroll {
            width: 100%;
            overflow-x: auto;
            margin: 0.35rem 0 1.25rem;
            -webkit-overflow-scrolling: touch;
        }

        .endgame-position-table {
            min-width: 680px;
            width: max-content;
            border-collapse: separate;
            border-spacing: 0;
            overflow: hidden;
            border: 1px solid var(--pool-border);
            border-radius: 8px;
            background: var(--pool-panel);
            box-shadow: 0 10px 28px color-mix(in srgb, var(--pool-primary) 6%, transparent);
            color: #000000;
            font-size: 0.86rem;
        }

        .endgame-position-table th,
        .endgame-position-table td {
            min-width: 4.1rem;
            padding: 0.58rem 0.7rem;
            border-right: 1px solid var(--pool-border);
            border-bottom: 1px solid var(--pool-border);
            text-align: center;
            vertical-align: middle;
            white-space: nowrap;
        }

        .endgame-position-table th:first-child,
        .endgame-position-table td:first-child {
            position: sticky;
            left: 0;
            z-index: 1;
            min-width: 9.5rem;
            max-width: 13rem;
            text-align: left;
            background: #ffffff;
            white-space: normal;
        }

        .endgame-position-table th {
            background: #f7f9fc;
            color: #000000;
            font-weight: 700;
        }

        .endgame-position-table th:first-child {
            z-index: 2;
            background: #f7f9fc;
        }

        .endgame-position-table th:last-child,
        .endgame-position-table td:last-child {
            border-right: 0;
        }

        .endgame-position-table tbody tr:last-child td {
            border-bottom: 0;
        }

        .endgame-position-legend {
            display: flex;
            align-items: center;
            gap: 0.55rem;
            margin: 0.2rem 0 0.6rem;
            color: var(--pool-primary);
            font-size: 0.82rem;
            font-weight: 600;
        }

        .endgame-position-legend-bar {
            width: min(16rem, 48vw);
            height: 0.8rem;
            border: 1px solid var(--pool-border);
            border-radius: 999px;
            background: linear-gradient(90deg, #f7fbff 0%, #deebf7 25%, #9ecae1 50%, #3182bd 75%, #08519c 100%);
        }

        .endgame-overview {
            margin: 0.1rem 0 1.4rem;
        }

        .endgame-bracket {
            position: relative;
            display: grid;
            grid-template-columns: repeat(4, minmax(10.5rem, 1fr));
            grid-template-rows: auto repeat(8, minmax(2.75rem, auto));
            column-gap: 2rem;
            row-gap: 0.45rem;
            align-items: stretch;
            margin: 0.35rem 0 1.15rem;
        }

        .endgame-bracket-mobile {
            display: none;
            margin: 0.35rem 0 1.15rem;
        }

        .endgame-bracket-lines {
            position: absolute;
            z-index: 1;
            left: 0;
            right: 0;
            top: 2.05rem;
            bottom: 0;
            width: 100%;
            height: calc(100% - 2.05rem);
            pointer-events: none;
            overflow: visible;
        }

        .endgame-bracket-lines path {
            fill: none;
            stroke: #b8c6d3;
            stroke-width: 2.4;
            stroke-linecap: round;
            stroke-linejoin: round;
            vector-effect: non-scaling-stroke;
        }

        .endgame-bracket-lines path.final-line {
            stroke: color-mix(in srgb, var(--pool-primary) 45%, #b8c6d3);
        }

        .endgame-bracket-column {
            min-width: 0;
        }

        .endgame-bracket-title {
            position: relative;
            z-index: 2;
            grid-row: 1;
            margin: 0 0 0.15rem;
            color: var(--pool-primary);
            font-size: 0.84rem;
            font-weight: 800;
            text-align: center;
            text-transform: uppercase;
            letter-spacing: 0;
        }

        .endgame-bracket-title.quarter-final {
            grid-column: 1;
        }

        .endgame-bracket-title.semi-final {
            grid-column: 2;
        }

        .endgame-bracket-title.final {
            grid-column: 3;
        }

        .endgame-bracket-title.third-place {
            grid-column: 4;
        }

        .endgame-bracket-card {
            position: relative;
            z-index: 2;
            display: flex;
            flex-direction: column;
            justify-content: center;
            min-height: 6.85rem;
            margin: 0;
            padding: 0.55rem 0.6rem;
            border: 1px solid var(--pool-border);
            border-radius: 8px;
            background: #ffffff;
            box-shadow: 0 8px 20px color-mix(in srgb, var(--pool-primary) 5%, transparent);
        }

        .endgame-bracket-card.qf-1 {
            grid-column: 1;
            grid-row: 2 / span 2;
        }

        .endgame-bracket-card.qf-2 {
            grid-column: 1;
            grid-row: 4 / span 2;
        }

        .endgame-bracket-card.qf-3 {
            grid-column: 1;
            grid-row: 6 / span 2;
        }

        .endgame-bracket-card.qf-4 {
            grid-column: 1;
            grid-row: 8 / span 2;
        }

        .endgame-bracket-card.sf-1 {
            grid-column: 2;
            grid-row: 3 / span 2;
        }

        .endgame-bracket-card.sf-2 {
            grid-column: 2;
            grid-row: 7 / span 2;
        }

        .endgame-bracket-card.final-1 {
            grid-column: 3;
            grid-row: 5 / span 2;
        }

        .endgame-bracket-card.third-place-1 {
            grid-column: 4;
            grid-row: 7 / span 2;
        }

        .endgame-bracket-card.next {
            border-color: var(--pool-accent);
            box-shadow: 0 0 0 1px color-mix(in srgb, var(--pool-accent) 45%, transparent);
        }

        .endgame-bracket-card.completed {
            background: #f7f9fc;
        }

        .endgame-bracket-meta {
            min-height: 1.15rem;
            margin-bottom: 0.28rem;
            color: var(--pool-muted);
            font-size: 0.76rem;
            font-weight: 650;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .endgame-bracket-team {
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto;
            align-items: center;
            gap: 0.45rem;
            min-height: 1.75rem;
            color: #000000;
            font-size: 0.86rem;
            font-weight: 650;
        }

        .endgame-bracket-team.winner {
            color: #167a3a;
            font-weight: 800;
        }

        .endgame-bracket-score {
            color: var(--pool-primary);
            font-weight: 800;
        }

        .endgame-next-match {
            margin: 0.65rem 0 1.1rem;
            padding: 1rem 1.1rem;
            border: 1px solid var(--pool-border);
            border-radius: 8px;
            background: #ffffff;
            box-shadow: 0 10px 28px color-mix(in srgb, var(--pool-primary) 6%, transparent);
        }

        .endgame-next-meta {
            margin-bottom: 0.75rem;
            color: var(--pool-muted);
            font-size: 0.9rem;
            font-weight: 750;
        }

        .endgame-next-teams {
            display: grid;
            grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
            align-items: center;
            gap: 0.85rem;
        }

        .endgame-next-team {
            display: flex;
            align-items: center;
            min-width: 0;
            gap: 0.75rem;
            color: #000000;
            font-size: 1.45rem;
            font-weight: 850;
        }

        .endgame-next-team.away {
            justify-content: flex-end;
            text-align: right;
        }

        .endgame-next-team img {
            width: 3rem;
            height: 3rem;
            object-fit: contain;
            flex: 0 0 auto;
        }

        .endgame-next-team span {
            min-width: 0;
            overflow-wrap: anywhere;
        }

        .endgame-next-vs {
            color: var(--pool-accent);
            font-size: 0.95rem;
            font-weight: 900;
        }

        .endgame-outcome-card {
            min-height: 100%;
            padding: 0.85rem 0.9rem 0.95rem;
            border: 1px solid var(--pool-border);
            border-radius: 8px;
            background: #ffffff;
            box-shadow: 0 10px 28px color-mix(in srgb, var(--pool-primary) 6%, transparent);
        }

        .endgame-outcome-card h4 {
            margin: 0 0 0.65rem;
            color: var(--pool-primary);
            font-size: 1rem;
            font-weight: 850;
        }

        .endgame-card-section-title {
            margin: 0.7rem 0 0.3rem;
            color: var(--pool-muted);
            font-size: 0.78rem;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0;
        }

        .endgame-mini-table {
            width: 100%;
            border-collapse: collapse;
            color: #000000;
            font-size: 0.82rem;
        }

        .endgame-mini-table th,
        .endgame-mini-table td {
            padding: 0.35rem 0.25rem;
            border-bottom: 1px solid var(--pool-border);
            text-align: center;
            vertical-align: middle;
        }

        .endgame-mini-table th {
            color: var(--pool-muted);
            font-weight: 750;
        }

        .endgame-mini-table th:first-child,
        .endgame-mini-table td:first-child {
            width: 2rem;
            text-align: center;
            white-space: nowrap;
        }

        .endgame-consequence-list {
            margin: 0.15rem 0 0;
            padding-left: 1rem;
            color: #000000;
            font-size: 0.84rem;
            line-height: 1.45;
        }

        .endgame-empty-note {
            color: var(--pool-muted);
            font-size: 0.84rem;
            font-weight: 600;
        }

        .endgame-odds-swing-heading {
            margin: 1.45rem 0 0.45rem;
            color: var(--pool-primary);
            font-size: 1rem;
            font-weight: 800;
        }

        @media (max-width: 900px) {
            .endgame-bracket {
                display: none;
            }

            .endgame-bracket-mobile {
                display: grid;
                grid-template-columns: repeat(2, minmax(10.5rem, 1fr));
                gap: 0.85rem;
                align-items: start;
            }

            .endgame-bracket-mobile .endgame-bracket-title {
                margin: 0 0 0.4rem;
            }

            .endgame-bracket-mobile .endgame-bracket-card {
                margin-bottom: 0.55rem;
            }

            .endgame-next-teams {
                grid-template-columns: 1fr;
                gap: 0.55rem;
            }

            .endgame-next-vs {
                text-align: center;
            }

            .endgame-next-team,
            .endgame-next-team.away {
                justify-content: center;
                text-align: center;
                font-size: 1.22rem;
            }
        }

        @media (max-width: 560px) {
            .endgame-bracket-mobile {
                grid-template-columns: 1fr;
            }
        }

        .pool-table tr:last-child td {
            border-bottom: 0;
        }

        .pool-table tr.advancing td {
            background: #dff4e5;
        }

        .standings-legend {
            display: grid;
            grid-template-columns: auto 1fr;
            align-items: center;
            column-gap: 0.45rem;
            row-gap: 0.2rem;
            margin-top: 0.45rem;
            color: #000000;
            font-size: 0.82rem;
        }

        .standings-legend-swatch {
            width: 1.2rem;
            height: 0.75rem;
            border: 1px solid #9bcfa9;
            background: #dff4e5;
            border-radius: 3px;
        }

        .standings-legend-detail {
            grid-column: 2;
        }

        .qualification-marker {
            font-weight: 800;
            margin-left: 0.2rem;
        }

        .rules-phase-heading {
            margin: 1.1rem 0 0.55rem;
            color: var(--pool-primary);
            font-size: 1.35rem;
            font-weight: 700;
            line-height: 1.25;
        }

        .rules-phase-heading-spaced {
            margin-top: 2rem;
        }

        .rules-section-gap {
            height: 1.6rem;
        }

        .submission-deadline {
            margin: 1rem 0 1.1rem;
            color: var(--pool-primary);
            font-size: 1.25rem;
            font-weight: 800;
            line-height: 1.25;
        }

        .section-gap {
            height: 1.75rem;
        }

        </style>
        """
    replacements = {
        "__BACKGROUND__": theme["background"],
        "__BACKGROUND_SOFT__": theme["background_soft"],
        "__PANEL__": theme["panel"],
        "__PRIMARY__": theme["primary"],
        "__PRIMARY_SOFT__": theme["primary_soft"],
        "__ACCENT__": theme["accent"],
        "__ACCENT_HOVER__": theme["accent_hover"],
        "__TEXT__": theme["text"],
        "__MUTED__": theme["muted"],
        "__BORDER__": theme["border"],
    }
    for placeholder, value in replacements.items():
        css = css.replace(placeholder, value)
    st.markdown(css, unsafe_allow_html=True)


def clear_stale_streamlit_cache() -> None:
    if st.session_state.get("cache_schema_version") == CACHE_SCHEMA_VERSION:
        return
    st.cache_data.clear()
    st.session_state["cache_schema_version"] = CACHE_SCHEMA_VERSION


def ensure_data_files() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    PREDICTIONS_DIR.mkdir(exist_ok=True)
    STANDINGS_DIR.mkdir(exist_ok=True)
    DRAFTS_DIR.mkdir(exist_ok=True)

    if not USERS_FILE.exists():
        pd.DataFrame(columns=USERS_COLUMNS).to_csv(USERS_FILE, index=False)

    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(json.dumps({"submissions_open": True}, indent=2), encoding="utf-8")

    ensure_csv_columns(RESULTS_FILE, ["match_id", "home_goals", "away_goals", PENALTY_WINNER_COLUMN, *CARD_COLUMNS])
    for path in PREDICTIONS_DIR.glob("predictions_*.csv"):
        ensure_csv_columns(path, PREDICTION_COLUMNS)


def google_sheets_enabled() -> bool:
    try:
        return str(st.secrets.get("GOOGLE_SHEETS_BACKEND", "")).strip().lower() in {"1", "true", "yes"}
    except Exception:
        return False


def google_sheet_id() -> str:
    value = str(st.secrets.get("GOOGLE_SHEET_ID", "")).strip()
    match = re.search(r"/spreadsheets/d/([^/]+)", value)
    if match:
        return match.group(1)
    return value


def google_sheets_target_label() -> str:
    if not google_sheets_enabled():
        return "Google Sheets disabled"
    sheet_id = google_sheet_id()
    sheet_hint = f"...{sheet_id[-8:]}" if sheet_id else "missing GOOGLE_SHEET_ID"
    try:
        title = str(getattr(sheets_workbook(), "title", "")).strip()
    except Exception:
        title = ""
    return f"{title} ({sheet_hint})" if title else sheet_hint


def is_google_sheets_rate_limit_error(error: Exception) -> bool:
    response = getattr(error, "response", None)
    status_code = getattr(response, "status_code", None)
    text = str(error)
    if response is not None:
        text = f"{text} {getattr(response, 'text', '')}"
    text = text.lower()
    return status_code == 429 or "quota" in text or "rate limit" in text or "resource_exhausted" in text


def raise_if_google_sheets_rate_limited(error: Exception) -> None:
    if is_google_sheets_rate_limit_error(error):
        raise GoogleSheetsRateLimitError from error


@st.cache_resource
def sheets_workbook():
    if gspread is None:
        raise RuntimeError("gspread is not installed. Add it to requirements.txt and redeploy.")
    sheet_id = google_sheet_id()
    if not sheet_id:
        raise RuntimeError("GOOGLE_SHEET_ID is missing from Streamlit secrets.")
    credentials = dict(st.secrets["gcp_service_account"])
    client = gspread.service_account_from_dict(credentials)
    try:
        return client.open_by_key(sheet_id)
    except APIError as error:
        raise_if_google_sheets_rate_limited(error)
        raise


def get_worksheet(name: str):
    try:
        return cached_worksheet(name)
    except APIError as error:
        raise_if_google_sheets_rate_limited(error)
        raise


@st.cache_resource
def cached_worksheet(name: str):
    return sheets_workbook().worksheet(name)


def get_or_create_worksheet(name: str, rows: int = 1000, cols: int = 20):
    workbook = sheets_workbook()
    try:
        return cached_worksheet(name)
    except WorksheetNotFound:
        try:
            worksheet = workbook.add_worksheet(title=name, rows=rows, cols=cols)
            st.cache_resource.clear()
            return worksheet
        except APIError as error:
            raise_if_google_sheets_rate_limited(error)
            raise
    except APIError as error:
        raise_if_google_sheets_rate_limited(error)
        raise


def sheet_values_to_frame(name: str, columns: tuple[str, ...] = ()) -> pd.DataFrame:
    if not google_sheets_enabled():
        return pd.DataFrame(columns=list(columns))
    try:
        values = get_worksheet(name).get_all_values()
    except WorksheetNotFound:
        return pd.DataFrame(columns=list(columns))
    except APIError as error:
        raise_if_google_sheets_rate_limited(error)
        raise
    if not values:
        return pd.DataFrame(columns=list(columns))

    header = [str(value).strip() for value in values[0]]
    rows = []
    for raw_row in values[1:]:
        padded = [*raw_row, *[""] * max(0, len(header) - len(raw_row))]
        rows.append(dict(zip(header, padded[: len(header)])))
    table = pd.DataFrame(rows).fillna("")
    for column in columns:
        if column not in table.columns:
            table[column] = ""
    return table[list(columns)].copy() if columns else table


@st.cache_data(ttl=10)
def read_sheet(name: str, columns: tuple[str, ...] = ()) -> pd.DataFrame:
    return sheet_values_to_frame(name, columns)


def read_sheet_fresh(name: str, columns: tuple[str, ...] = ()) -> pd.DataFrame:
    return sheet_values_to_frame(name, columns)


def write_sheet(name: str, table: pd.DataFrame, columns: list[str] | None = None) -> None:
    if not google_sheets_enabled():
        return
    output = table.copy().fillna("")
    if columns is not None:
        for column in columns:
            if column not in output.columns:
                output[column] = ""
        output = output[columns]
    output = output.astype(str)
    values = [output.columns.tolist(), *output.values.tolist()]
    row_count = max(1000, len(values) + 10)
    col_count = max(20, len(output.columns) + 5)
    worksheet = get_or_create_worksheet(name, rows=row_count, cols=col_count)
    try:
        worksheet.update(values=values, range_name="A1")
    except APIError as error:
        raise_if_google_sheets_rate_limited(error)
        raise
    clear_cache()


def sheet_columns_for_path(path: Path) -> tuple[str, ...]:
    if path == USERS_FILE:
        return tuple(USERS_COLUMNS)
    if path == RESULTS_FILE:
        return ("match_id", "home_goals", "away_goals", PENALTY_WINNER_COLUMN, *CARD_COLUMNS)
    if path == LEADERBOARD_FILE:
        return ("rank", "user_id", "user_name", "total_points")
    return ()


@st.cache_data
def load_csv(path: Path, modified_time: float) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype=str).fillna("")


def read_csv(path: Path) -> pd.DataFrame:
    if google_sheets_enabled() and path in SHEET_BACKED_FILES:
        return read_sheet(SHEET_BACKED_FILES[path], sheet_columns_for_path(path))
    modified_time = path.stat().st_mtime if path.exists() else 0
    return load_csv(path, modified_time)


def clear_cache() -> None:
    st.cache_data.clear()


def load_config() -> dict[str, Any]:
    if google_sheets_enabled():
        config = read_sheet(CONFIG_SHEET, ("key", "value"))
        if config.empty:
            return {"submissions_open": True}
        values = dict(zip(config["key"].astype(str), config["value"].astype(str)))
        return {"submissions_open": values.get("submissions_open", "true").strip().lower() in {"1", "true", "yes"}}

    ensure_data_files()
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"submissions_open": True}


def submissions_are_open() -> bool:
    return bool(load_config().get("submissions_open", True))


def save_users(users: pd.DataFrame) -> None:
    if google_sheets_enabled():
        existing = normalize_users(read_sheet_fresh("users", tuple(USERS_COLUMNS)))
        incoming = normalize_users(users)
        if not existing.empty:
            retained = existing[~existing["user_id"].isin(incoming["user_id"])]
            incoming = pd.concat([retained, incoming], ignore_index=True)
        write_sheet("users", incoming[USERS_COLUMNS], USERS_COLUMNS)
        return
    users.to_csv(USERS_FILE, index=False)
    clear_cache()


def user_exists(users: pd.DataFrame, user_name: str) -> bool:
    return users["user_name"].str.lower().eq(user_name.strip().lower()).any()


def existing_user_by_name(users: pd.DataFrame, user_name: str) -> pd.Series | None:
    matching = users[users["user_name"].str.lower().eq(user_name.strip().lower())]
    if matching.empty:
        return None
    return matching.iloc[0]


def clear_prediction_session() -> None:
    for key in list(st.session_state.keys()):
        if key.startswith(("pred_home_", "pred_away_", "pred_penalty_")):
            del st.session_state[key]
    st.session_state.pop("prediction_session_initialized_for", None)
    st.session_state.pop(PREDICTION_SESSION_VALUES_KEY, None)
    st.session_state.pop(PREDICTION_SESSION_VALUES_USER_KEY, None)


def activate_user(user_id: str, user_name: str) -> None:
    if st.session_state.get("user_id") != user_id:
        clear_prediction_session()
    st.session_state["user_id"] = user_id
    st.session_state["user_name"] = user_name


def new_draft_id() -> str:
    return uuid.uuid4().hex


def valid_draft_id(draft_id: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{32}", draft_id.strip().lower()))


def draft_file(draft_id: str) -> Path:
    return DRAFTS_DIR / f"{draft_id}.json"


def get_query_param(name: str) -> str:
    value = st.query_params.get(name, "")
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value)


def set_draft_query_param(draft_id: str) -> bool:
    if get_query_param(DRAFT_QUERY_PARAM) != draft_id:
        st.query_params[DRAFT_QUERY_PARAM] = draft_id
        return True
    return False


def load_draft(draft_id: str) -> dict[str, Any] | None:
    draft_id = draft_id.strip().lower()
    if not valid_draft_id(draft_id):
        return None

    if google_sheets_enabled():
        drafts = read_sheet(DRAFTS_SHEET, ("draft_id", "user_id", "user_name", "predictions_json"))
        matching = drafts[drafts["draft_id"].astype(str).str.lower().eq(draft_id)]
        if matching.empty:
            return None
        row = matching.iloc[0]
        try:
            predictions = json.loads(str(row.get("predictions_json", "[]")))
        except json.JSONDecodeError:
            predictions = []
        return {
            "draft_id": draft_id,
            "user_id": str(row.get("user_id", "")),
            "user_name": str(row.get("user_name", "")),
            "predictions": predictions,
        }

    path = draft_file(draft_id)
    if not path.exists():
        return None
    try:
        draft = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(draft, dict):
        return None
    return draft


def save_draft(draft_id: str, user_id: str, user_name: str, predictions: pd.DataFrame) -> None:
    if not valid_draft_id(draft_id):
        return
    draft = {
        "draft_id": draft_id,
        "user_id": user_id,
        "user_name": user_name.strip(),
        "predictions": predictions[PREDICTION_COLUMNS].fillna("").to_dict(orient="records"),
    }
    if google_sheets_enabled():
        drafts = read_sheet_fresh(DRAFTS_SHEET, ("draft_id", "user_id", "user_name", "predictions_json"))
        drafts = drafts[~drafts["draft_id"].astype(str).str.lower().eq(draft_id)]
        updated = pd.concat(
            [
                drafts,
                pd.DataFrame(
                    [
                        {
                            "draft_id": draft_id,
                            "user_id": user_id,
                            "user_name": user_name.strip(),
                            "predictions_json": json.dumps(draft["predictions"]),
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
        write_sheet(DRAFTS_SHEET, updated, ["draft_id", "user_id", "user_name", "predictions_json"])
        return
    draft_file(draft_id).write_text(json.dumps(draft, indent=2), encoding="utf-8")


def autosave_draft(
    draft_id: str,
    user_id: str,
    user_name: str,
    predictions: pd.DataFrame,
    min_interval_seconds: float = 5.0,
) -> None:
    if not google_sheets_enabled():
        save_draft(draft_id, user_id, user_name, predictions)
        return

    signature = json.dumps(
        {
            "draft_id": draft_id,
            "user_id": user_id,
            "user_name": user_name.strip(),
            "predictions": predictions[PREDICTION_COLUMNS].fillna("").to_dict(orient="records"),
        },
        sort_keys=True,
    )
    now = time.time()
    if st.session_state.get("last_saved_draft_signature") == signature:
        return
    last_save = float(st.session_state.get("last_saved_draft_at", 0))
    if now - last_save < min_interval_seconds:
        return
    save_draft(draft_id, user_id, user_name, predictions)
    st.session_state["last_saved_draft_signature"] = signature
    st.session_state["last_saved_draft_at"] = now


def prediction_widget_state_exists(matches: pd.DataFrame) -> bool:
    for match_id in matches["match_id"]:
        if f"pred_home_{match_id}" in st.session_state or f"pred_away_{match_id}" in st.session_state:
            return True
    return False


def prediction_widget_state_complete(matches: pd.DataFrame) -> bool:
    for match_id in matches["match_id"]:
        if f"pred_home_{match_id}" not in st.session_state or f"pred_away_{match_id}" not in st.session_state:
            return False
    return True


def prediction_frame_from_records(records: Any) -> pd.DataFrame:
    predictions = pd.DataFrame(records if isinstance(records, list) else [])
    for column in PREDICTION_COLUMNS:
        if column not in predictions.columns:
            predictions[column] = ""
    return predictions[PREDICTION_COLUMNS].fillna("")


def remember_prediction_session(matches: pd.DataFrame, user_id: str) -> pd.DataFrame | None:
    if not prediction_widget_state_exists(matches):
        return None
    predictions = make_score_df_from_session(matches, user_id)
    st.session_state[PREDICTION_SESSION_VALUES_KEY] = (
        predictions[PREDICTION_COLUMNS].fillna("").to_dict(orient="records")
    )
    st.session_state[PREDICTION_SESSION_VALUES_USER_KEY] = user_id
    return predictions


def remembered_prediction_session(user_id: str) -> pd.DataFrame | None:
    if st.session_state.get(PREDICTION_SESSION_VALUES_USER_KEY) != user_id:
        return None
    return prediction_frame_from_records(st.session_state.get(PREDICTION_SESSION_VALUES_KEY, []))


def persist_active_prediction_session(matches: pd.DataFrame) -> None:
    user_id = str(st.session_state.get("user_id", "")).strip()
    if not user_id:
        return
    predictions = remember_prediction_session(matches, user_id)
    if predictions is None:
        return

    draft_id = str(st.session_state.get("draft_id", "")).strip().lower()
    if not valid_draft_id(draft_id):
        return
    user_name = str(st.session_state.get("draft_user_name", st.session_state.get("user_name", ""))).strip()
    autosave_draft(draft_id, user_id, user_name, predictions, min_interval_seconds=0)


def restore_prediction_widgets(predictions: pd.DataFrame, matches: pd.DataFrame, user_id: str) -> None:
    existing_lookup = {row["match_id"]: row for _, row in predictions.iterrows()}
    for match_id in matches["match_id"]:
        row = existing_lookup.get(match_id)
        home = None if row is None else optional_natural(row["home_goals"])
        away = None if row is None else optional_natural(row["away_goals"])
        st.session_state[f"pred_home_{match_id}"] = home
        st.session_state[f"pred_away_{match_id}"] = away
        st.session_state[f"pred_penalty_{match_id}"] = (
            "" if row is None else str(row.get(PENALTY_WINNER_COLUMN, "")).strip()
        )
    st.session_state["prediction_session_initialized_for"] = user_id
    st.session_state[PREDICTION_SESSION_VALUES_KEY] = (
        predictions[PREDICTION_COLUMNS].fillna("").to_dict(orient="records")
    )
    st.session_state[PREDICTION_SESSION_VALUES_USER_KEY] = user_id


def restore_draft_from_url(matches: pd.DataFrame) -> None:
    if "user_id" in st.session_state:
        return

    draft_id = get_query_param(DRAFT_QUERY_PARAM).strip().lower()
    draft = load_draft(draft_id)
    if draft is None:
        return

    user_id = str(draft.get("user_id", "PENDING")).strip() or "PENDING"
    user_name = str(draft.get("user_name", "")).strip()

    predictions = prediction_frame_from_records(draft.get("predictions", []))

    st.session_state["draft_id"] = draft_id
    st.session_state["user_id"] = user_id
    st.session_state["user_name"] = user_name
    st.session_state["draft_user_name"] = user_name
    restore_prediction_widgets(predictions, matches, user_id)


def ensure_active_draft() -> str:
    draft_id = str(st.session_state.get("draft_id", "")).strip().lower()
    if not valid_draft_id(draft_id):
        draft_id = new_draft_id()
        st.session_state["draft_id"] = draft_id
    if set_draft_query_param(draft_id):
        st.info("Preparing your draft link...")
        st.rerun()
    return draft_id


def user_name_taken_by_other(users: pd.DataFrame, user_id: str, user_name: str) -> bool:
    matching = users[users["user_name"].str.lower().eq(user_name.strip().lower())]
    return not matching.empty and not matching["user_id"].eq(user_id).all()


def commit_user_if_needed(users: pd.DataFrame, user_id: str, user_name: str) -> pd.DataFrame:
    if google_sheets_enabled():
        users = normalize_users(read_sheet_fresh("users", tuple(USERS_COLUMNS)))
        id_match = users[users["user_id"].eq(user_id)]
        if not id_match.empty and not id_match["user_name"].eq(user_name).any():
            raise ValueError("This generated user ID is already in use. Submit again to get a fresh ID.")
    if users["user_id"].eq(user_id).any():
        return users
    if user_exists(users, user_name):
        raise ValueError("This name already exists. Use a unique name.")

    updated = pd.concat(
        [
            users,
            pd.DataFrame([{"user_id": user_id, "user_name": user_name, "total_points": 0}]),
        ],
        ignore_index=True,
    )
    save_users(updated[USERS_COLUMNS])
    return updated[USERS_COLUMNS]


def prediction_file(user_id: str) -> Path:
    return PREDICTIONS_DIR / f"predictions_{user_id}.csv"


@st.cache_data
def load_prediction_csv(path: Path, modified_time: float) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=PREDICTION_COLUMNS)
    predictions = pd.read_csv(path, dtype=str).fillna("")
    for column in PREDICTION_COLUMNS:
        if column not in predictions.columns:
            predictions[column] = ""
    return predictions[PREDICTION_COLUMNS].copy()


def load_user_predictions(user_id: str) -> pd.DataFrame:
    if google_sheets_enabled():
        predictions = read_sheet(PREDICTIONS_SHEET, tuple(PREDICTION_COLUMNS))
        if predictions.empty:
            return pd.DataFrame(columns=PREDICTION_COLUMNS)
        return predictions[predictions["user_id"].astype(str).eq(str(user_id))][PREDICTION_COLUMNS].copy()

    path = prediction_file(user_id)
    if not path.exists():
        return pd.DataFrame(columns=PREDICTION_COLUMNS)
    return load_prediction_csv(path, path.stat().st_mtime)


def save_user_predictions(user_id: str, predictions: pd.DataFrame) -> None:
    predictions = predictions[PREDICTION_COLUMNS].copy()
    if google_sheets_enabled():
        all_predictions = read_sheet_fresh(PREDICTIONS_SHEET, tuple(PREDICTION_COLUMNS))
        if not all_predictions.empty:
            all_predictions = all_predictions[~all_predictions["user_id"].astype(str).eq(str(user_id))]
        updated = pd.concat([all_predictions, predictions], ignore_index=True)
        write_sheet(PREDICTIONS_SHEET, updated, PREDICTION_COLUMNS)
        return
    predictions.to_csv(prediction_file(user_id), index=False)
    clear_cache()


def normalize_users(users: pd.DataFrame) -> pd.DataFrame:
    if users.empty:
        return pd.DataFrame(columns=USERS_COLUMNS)

    for column in USERS_COLUMNS:
        if column not in users.columns:
            users[column] = "0" if column == "total_points" else ""

    users = users[USERS_COLUMNS].copy().fillna("")
    users = users[users["user_id"].str.strip().ne("") & users["user_name"].str.strip().ne("")]
    users["total_points"] = users["total_points"].apply(lambda value: str(to_int(value, 0)))
    return users.reset_index(drop=True)


def next_user_id(users: pd.DataFrame) -> str:
    max_id = 0
    for value in users.get("user_id", []):
        match = re.fullmatch(r"U(\d+)", str(value).strip())
        if match:
            max_id = max(max_id, int(match.group(1)))
    return f"U{max_id + 1:03d}"


def to_int(value: Any, default: int = 0) -> int:
    try:
        if value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def optional_natural(value: Any) -> int | None:
    text = str(value).strip()
    if text == "":
        return None
    if not re.fullmatch(r"\d+", text):
        return None
    return int(text)


def team_lookup(teams: pd.DataFrame) -> dict[str, str]:
    return dict(zip(teams["team_id"], teams["team_name"]))


def team_logo_lookup(teams: pd.DataFrame) -> dict[str, str]:
    if "logo_link" not in teams.columns:
        return {}
    return dict(zip(teams["team_id"], teams["logo_link"]))


@st.cache_data
def image_data_uri(path_text: str) -> str:
    path = Path(path_text)
    if not path.is_file():
        return ""
    suffix = path.suffix.lower().lstrip(".") or "png"
    mime = "image/svg+xml" if suffix == "svg" else f"image/{suffix}"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def team_badge_html(team_id: str | None, teams: pd.DataFrame, fallback: str = "TBD") -> str:
    if not team_id:
        return html.escape(fallback)

    names = team_lookup(teams)
    logos = team_logo_lookup(teams)
    name = names.get(team_id, team_id)
    logo = image_data_uri(logos.get(team_id, ""))
    safe_name = html.escape(name)

    if not logo:
        return safe_name
    return f'<span class="team-badge"><img src="{logo}" alt=""> <span>{safe_name}</span></span>'


def team_badge_with_status_html(
    team_id: str | None,
    teams: pd.DataFrame,
    qualification_statuses: dict[str, str] | None = None,
    fallback: str = "TBD",
) -> str:
    badge = team_badge_html(team_id, teams, fallback)
    status = (qualification_statuses or {}).get(str(team_id), "")
    if status == "advanced":
        return f'{badge} <strong class="qualification-marker">(A)</strong>'
    if status == "eliminated":
        return f'{badge} <strong class="qualification-marker">(E)</strong>'
    return badge


def table_cell_class(numeric: bool, extra_class: str = "") -> str:
    classes = []
    if numeric:
        classes.append("numeric")
    if extra_class:
        classes.append(extra_class)
    return f' class="{" ".join(classes)}"' if classes else ""


def normalize_table_header(header: tuple[Any, ...]) -> tuple[str, bool, str]:
    label = str(header[0])
    numeric = bool(header[1]) if len(header) > 1 else False
    extra_class = str(header[2]) if len(header) > 2 else ""
    return label, numeric, extra_class


def normalize_table_cell(cell: tuple[Any, ...]) -> tuple[str, bool, str]:
    value = str(cell[0])
    numeric = bool(cell[1]) if len(cell) > 1 else False
    extra_class = str(cell[2]) if len(cell) > 2 else ""
    return value, numeric, extra_class


def render_html_table(headers: list[tuple[Any, ...]], rows: list[Any], table_class: str = "") -> None:
    header_html = ""
    for header in headers:
        label, numeric, extra_class = normalize_table_header(header)
        header_html += (
            f'<th{table_cell_class(numeric, extra_class)}>{html.escape(label).replace(chr(10), "<br>")}</th>'
        )
    row_html = []
    for item in rows:
        if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], str):
            row, row_class = item
        else:
            row, row_class = item, ""
        cells = ""
        for cell in row:
            value, numeric, extra_class = normalize_table_cell(cell)
            cells += f"<td{table_cell_class(numeric, extra_class)}>{value}</td>"
        class_attr = f' class="{row_class}"' if row_class else ""
        row_html.append(f"<tr{class_attr}>{cells}</tr>")
    class_attr = "pool-table" if not table_class else f"pool-table {table_class}"
    st.markdown(
        f'<table class="{class_attr}"><thead><tr>{header_html}</tr></thead><tbody>{"".join(row_html)}</tbody></table>',
        unsafe_allow_html=True,
    )


def display_advancing_legend() -> None:
    st.markdown(
        (
            '<div class="standings-legend">'
            '<span class="standings-legend-swatch"></span>'
            '<span>Green row: currently advances to the next round.</span>'
            '<div class="standings-legend-detail"><strong>(A)</strong>: Advanced to Round of 32</div>'
            '<div class="standings-legend-detail"><strong>(E)</strong>: Eliminated</div>'
            '</div>'
        ),
        unsafe_allow_html=True,
    )


def team_name(team_id: str | None, teams: pd.DataFrame) -> str:
    if not team_id:
        return "TBD"
    lookup = team_lookup(teams)
    return lookup.get(team_id, team_id)


def stage_label(stage: str) -> str:
    return STAGE_LABELS.get(str(stage), str(stage).replace("_", " ").title())


def score_lookup(score_df: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if score_df.empty or "match_id" not in score_df.columns:
        return {}
    return {str(row["match_id"]): row for row in score_df.to_dict("records")}


def make_score_df_from_session(matches: pd.DataFrame, user_id: str) -> pd.DataFrame:
    rows = []
    stage_lookup = dict(zip(matches["match_id"], matches["stage"]))
    for match_id in matches["match_id"]:
        home = optional_natural(st.session_state.get(f"pred_home_{match_id}", ""))
        away = optional_natural(st.session_state.get(f"pred_away_{match_id}", ""))
        penalty_winner = ""
        if stage_lookup.get(match_id) in KNOCKOUT_STAGES:
            penalty_winner = str(st.session_state.get(f"pred_penalty_{match_id}", "")).strip()
        rows.append(
            {
                "user_id": user_id,
                "match_id": match_id,
                "home_goals": "" if home is None else home,
                "away_goals": "" if away is None else away,
                PENALTY_WINNER_COLUMN: penalty_winner,
            }
        )
    return pd.DataFrame(rows, columns=PREDICTION_COLUMNS)


def completed_score(row: dict[str, Any] | pd.Series | None) -> tuple[int, int] | None:
    if row is None:
        return None
    home = optional_natural(row.get("home_goals", ""))
    away = optional_natural(row.get("away_goals", ""))
    if home is None or away is None:
        return None
    return home, away


def fair_play_delta(row: dict[str, Any], side: str) -> int:
    yellow = to_int(row.get(f"{side}_yellow_cards", 0))
    indirect = to_int(row.get(f"{side}_indirect_red_cards", 0))
    direct = to_int(row.get(f"{side}_direct_red_cards", 0))
    return -(yellow + 3 * indirect + 4 * direct)


def head_to_head_stats(
    team_ids: list[str],
    group_matches: pd.DataFrame,
    score_rows: dict[str, dict[str, Any]],
) -> dict[str, dict[str, int]]:
    involved = set(team_ids)
    stats = {
        team_id: {
            "points": 0,
            "goals_for": 0,
            "goals_against": 0,
            "goal_difference": 0,
        }
        for team_id in team_ids
    }

    for match in group_matches.to_dict("records"):
        home_id = match["home_team"]
        away_id = match["away_team"]
        if home_id not in involved or away_id not in involved:
            continue

        score = completed_score(score_rows.get(match["match_id"]))
        if score is None:
            continue

        home_goals, away_goals = score
        stats[home_id]["goals_for"] += home_goals
        stats[home_id]["goals_against"] += away_goals
        stats[away_id]["goals_for"] += away_goals
        stats[away_id]["goals_against"] += home_goals

        if home_goals > away_goals:
            stats[home_id]["points"] += 3
        elif home_goals < away_goals:
            stats[away_id]["points"] += 3
        else:
            stats[home_id]["points"] += 1
            stats[away_id]["points"] += 1

    for team_id in team_ids:
        stats[team_id]["goal_difference"] = stats[team_id]["goals_for"] - stats[team_id]["goals_against"]

    return stats


def group_fallback_key(team_id: str, table_by_team: dict[str, dict[str, Any]], rankings: dict[str, int]) -> tuple:
    row = table_by_team[team_id]
    return (
        -to_int(row["goal_difference"]),
        -to_int(row["goals_for"]),
        -to_int(row["fair_play_score"]),
        rankings.get(team_id, 9999),
    )


def resolve_group_points_tie(
    team_ids: list[str],
    group_matches: pd.DataFrame,
    score_rows: dict[str, dict[str, Any]],
    table_by_team: dict[str, dict[str, Any]],
    rankings: dict[str, int],
) -> list[str]:
    if len(team_ids) <= 1:
        return team_ids

    h2h = head_to_head_stats(team_ids, group_matches, score_rows)
    ordered = sorted(
        team_ids,
        key=lambda team_id: (
            -h2h[team_id]["points"],
            -h2h[team_id]["goal_difference"],
            -h2h[team_id]["goals_for"],
        ),
    )

    resolved: list[str] = []
    position = 0
    while position < len(ordered):
        current = ordered[position]
        current_key = (
            h2h[current]["points"],
            h2h[current]["goal_difference"],
            h2h[current]["goals_for"],
        )
        tied = [current]
        position += 1

        while position < len(ordered):
            candidate = ordered[position]
            candidate_key = (
                h2h[candidate]["points"],
                h2h[candidate]["goal_difference"],
                h2h[candidate]["goals_for"],
            )
            if candidate_key != current_key:
                break
            tied.append(candidate)
            position += 1

        if len(tied) == 1:
            resolved.extend(tied)
        elif len(tied) == len(team_ids):
            resolved.extend(sorted(tied, key=lambda team_id: group_fallback_key(team_id, table_by_team, rankings)))
        else:
            resolved.extend(resolve_group_points_tie(tied, group_matches, score_rows, table_by_team, rankings))

    return resolved


def sort_group_table(
    table: pd.DataFrame,
    group_matches: pd.DataFrame,
    score_rows: dict[str, dict[str, Any]],
    rankings: dict[str, int],
) -> pd.DataFrame:
    table_by_team = {str(row["team_id"]): row for row in table.to_dict("records")}
    ordered_team_ids = sort_group_rows(table_by_team, group_matches, score_rows, rankings)
    return pd.DataFrame([table_by_team[team_id] for team_id in ordered_team_ids])


def sort_group_rows(
    table_by_team: dict[str, dict[str, Any]],
    group_matches: pd.DataFrame,
    score_rows: dict[str, dict[str, Any]],
    rankings: dict[str, int],
) -> list[str]:
    ordered_by_points = sorted(table_by_team, key=lambda team_id: -to_int(table_by_team[team_id]["points"]))

    ordered_team_ids: list[str] = []
    position = 0
    while position < len(ordered_by_points):
        current = ordered_by_points[position]
        points = to_int(table_by_team[current]["points"])
        tied = [current]
        position += 1

        while position < len(ordered_by_points):
            candidate = ordered_by_points[position]
            if to_int(table_by_team[candidate]["points"]) != points:
                break
            tied.append(candidate)
            position += 1

        ordered_team_ids.extend(resolve_group_points_tie(tied, group_matches, score_rows, table_by_team, rankings))

    return ordered_team_ids


def calculate_single_group_standing(
    group: str,
    teams: pd.DataFrame,
    matches: pd.DataFrame,
    score_df: pd.DataFrame,
    use_cards: bool,
) -> pd.DataFrame:
    score_rows = score_lookup(score_df)
    return group_standing_from_score_rows(group, teams, matches, score_rows, use_cards)


def calculate_group_standings(
    teams: pd.DataFrame,
    matches: pd.DataFrame,
    score_df: pd.DataFrame,
    use_cards: bool,
    score_rows: dict[str, dict[str, Any]] | None = None,
    rankings: dict[str, int] | None = None,
    teams_by_group: dict[str, list[str]] | None = None,
    matches_by_group: dict[str, pd.DataFrame] | None = None,
) -> dict[str, pd.DataFrame]:
    standings: dict[str, pd.DataFrame] = {}
    score_rows = score_rows if score_rows is not None else score_lookup(score_df)
    rankings = rankings if rankings is not None else team_rankings_lookup(teams)
    teams_by_group = teams_by_group if teams_by_group is not None else team_ids_by_group(teams)
    matches_by_group = matches_by_group if matches_by_group is not None else group_match_rows_by_group(matches, teams)

    for group in GROUPS:
        standings[group] = group_standing_from_score_rows(
            group,
            teams,
            matches,
            score_rows,
            use_cards,
            rankings=rankings,
            group_teams=teams_by_group.get(group, []),
            group_matches=matches_by_group.get(group, matches.iloc[0:0]),
        )

    return standings


def calculate_third_place_standings(group_standings: dict[str, pd.DataFrame], teams: pd.DataFrame) -> pd.DataFrame:
    rankings = {row["team_id"]: to_int(row["world_cup_ranking"], 9999) for _, row in teams.iterrows()}
    rows = []
    for group, table in group_standings.items():
        if len(table) >= 3:
            row = table.iloc[2].to_dict()
            row["group"] = group
            row["_ranking"] = rankings.get(row["team_id"], 9999)
            rows.append(row)
    if not rows:
        return pd.DataFrame(columns=[*STANDING_COLUMNS, "group"])

    third = pd.DataFrame(rows)
    third = third.sort_values(
        by=["points", "goal_difference", "goals_for", "fair_play_score", "_ranking"],
        ascending=[False, False, False, False, True],
    ).drop(columns=["_ranking"])
    return third[[*STANDING_COLUMNS, "group"]].reset_index(drop=True)


def find_third_place_combination(third_place: pd.DataFrame, combinations: pd.DataFrame) -> pd.Series | None:
    advanced_groups = sorted(third_place.head(8)["group"].tolist())
    if len(advanced_groups) < 8:
        return None

    for _, row in combinations.iterrows():
        try:
            listed = sorted(ast.literal_eval(row["list_of_advanced_third_placed_teams"]))
        except (ValueError, SyntaxError):
            continue
        if listed == advanced_groups:
            return row
    return None


def resolve_position_slot(
    slot: str,
    group_standings: dict[str, pd.DataFrame],
    confirmed_position_slots: set[str] | None = None,
) -> str | None:
    match = re.fullmatch(r"([123])([A-L])", slot)
    if not match:
        return None
    position = int(match.group(1)) - 1
    group = match.group(2)
    if confirmed_position_slots is not None and slot not in confirmed_position_slots:
        return None
    table = group_standings.get(group)
    if table is None or len(table) <= position:
        return None
    return str(table.iloc[position]["team_id"])


def resolve_slot(
    slot: str,
    counterpart_slot: str,
    group_standings: dict[str, pd.DataFrame],
    combination_row: pd.Series | None,
    winners: dict[str, str],
    losers: dict[str, str],
    confirmed_position_slots: set[str] | None = None,
) -> str | None:
    slot = str(slot).strip()
    if not slot:
        return None
    if re.fullmatch(r"T\d+", slot):
        return slot
    if re.fullmatch(r"[12][A-L]|3[A-L]", slot):
        return resolve_position_slot(slot, group_standings, confirmed_position_slots)
    if re.fullmatch(r"3[A-L]{2,}", slot):
        if combination_row is None or counterpart_slot not in combination_row.index:
            return None
        concrete_slot = str(combination_row[counterpart_slot])
        return resolve_position_slot(concrete_slot, group_standings, confirmed_position_slots)
    if slot.startswith("winner_"):
        return winners.get(slot.replace("winner_", ""))
    if slot.startswith("loser_"):
        return losers.get(slot.replace("loser_", ""))
    return None


def confirmed_group_position_slots(
    group_standings: dict[str, pd.DataFrame],
    matches: pd.DataFrame,
    score_df: pd.DataFrame,
    teams: pd.DataFrame,
    score_rows: dict[str, dict[str, Any]] | None = None,
    matches_by_group: dict[str, pd.DataFrame] | None = None,
    completed_groups: set[str] | None = None,
) -> set[str]:
    confirmed_slots: set[str] = set()
    score_rows = score_rows if score_rows is not None else score_lookup(score_df)
    matches_by_group = matches_by_group if matches_by_group is not None else group_match_rows_by_group(matches, teams)
    completed_groups = (
        completed_groups
        if completed_groups is not None
        else completed_groups_from_rows(score_rows, matches_by_group)
    )

    for group, table in group_standings.items():
        if group in completed_groups:
            confirmed_slots.update(f"{position + 1}{group}" for position in range(min(3, len(table))))
            continue

        context = group_lock_context(
            group,
            table,
            matches,
            score_df,
            teams,
            score_rows=score_rows,
            group_matches=matches_by_group.get(group, matches.iloc[0:0]),
        )
        ordered_team_ids = [str(team_id) for team_id in table["team_id"]]
        for position, team_id in enumerate(ordered_team_ids[:3]):
            teams_above = ordered_team_ids[:position]
            teams_below = ordered_team_ids[position + 1 :]
            can_overtake_team_above = any(
                can_finish_above(team_id, other_id, context) for other_id in teams_above
            )
            can_be_overtaken_by_team_below = any(
                can_finish_above(other_id, team_id, context) for other_id in teams_below
            )
            if not can_overtake_team_above and not can_be_overtaken_by_team_below:
                confirmed_slots.add(f"{position + 1}{group}")

    return confirmed_slots


def dataframe_cache_key(table: pd.DataFrame) -> tuple[Any, ...]:
    if table.empty:
        return (tuple(table.columns), 0)
    hashed = pd.util.hash_pandas_object(table.fillna("").astype(str), index=True)
    return (tuple(table.columns), len(table), int(hashed.sum()))


@st.cache_data(show_spinner=False, hash_funcs={pd.DataFrame: dataframe_cache_key})
def derive_tournament_state(
    teams: pd.DataFrame,
    matches: pd.DataFrame,
    score_df: pd.DataFrame,
    knockout_matchups: pd.DataFrame,
    third_place_combinations: pd.DataFrame,
    use_cards: bool,
    require_confirmed_placements: bool = False,
    cache_schema_version: str = CACHE_SCHEMA_VERSION,
) -> dict[str, Any]:
    score_rows = score_lookup(score_df)
    rankings = team_rankings_lookup(teams)
    teams_by_group = team_ids_by_group(teams)
    matches_by_group = group_match_rows_by_group(matches, teams)
    completed_groups = completed_groups_from_rows(score_rows, matches_by_group)
    group_standings = calculate_group_standings(
        teams,
        matches,
        score_df,
        use_cards,
        score_rows=score_rows,
        rankings=rankings,
        teams_by_group=teams_by_group,
        matches_by_group=matches_by_group,
    )
    third_place = calculate_third_place_standings(group_standings, teams)
    confirmed_position_slots = None
    third_place_for_combination = third_place
    if require_confirmed_placements:
        confirmed_position_slots = confirmed_group_position_slots(
            group_standings,
            matches,
            score_df,
            teams,
            score_rows=score_rows,
            matches_by_group=matches_by_group,
            completed_groups=completed_groups,
        )
        if all(group in completed_groups for group in GROUPS):
            third_place_for_combination = third_place
        else:
            third_place_for_combination = third_place.iloc[0:0].copy()
    combination_row = find_third_place_combination(third_place_for_combination, third_place_combinations)
    matchup_rows = {row["match_id"]: row.to_dict() for _, row in knockout_matchups.iterrows()}

    winners: dict[str, str] = {}
    losers: dict[str, str] = {}
    resolved_rows = []

    for _, match in matches.iterrows():
        match_id = match["match_id"]
        stage = match["stage"]
        if stage == GROUP_STAGE:
            home_id = str(match["home_team"]).strip() or None
            away_id = str(match["away_team"]).strip() or None
        else:
            matchup = matchup_rows.get(match_id, {})
            home_slot = str(matchup.get("home_team", "")).strip()
            away_slot = str(matchup.get("away_team", "")).strip()
            home_id = resolve_slot(
                home_slot,
                away_slot,
                group_standings,
                combination_row,
                winners,
                losers,
                confirmed_position_slots,
            )
            away_id = resolve_slot(
                away_slot,
                home_slot,
                group_standings,
                combination_row,
                winners,
                losers,
                confirmed_position_slots,
            )

        score = completed_score(score_rows.get(match_id))
        home_goals = away_goals = None
        winner_id = loser_id = None
        if score is not None:
            home_goals, away_goals = score
            if home_id and away_id and stage in KNOCKOUT_STAGES and home_goals == away_goals:
                penalty_winner = str(score_rows.get(match_id, {}).get(PENALTY_WINNER_COLUMN, "")).strip()
                if penalty_winner in {home_id, away_id}:
                    winner_id = penalty_winner
                    loser_id = away_id if penalty_winner == home_id else home_id
            elif home_id and away_id and home_goals != away_goals:
                winner_id = home_id if home_goals > away_goals else away_id
                loser_id = away_id if home_goals > away_goals else home_id
            if winner_id and loser_id:
                winners[match_id] = winner_id
                losers[match_id] = loser_id

        resolved_rows.append(
            {
                "match_id": match_id,
                "stage": stage,
                "match_date": match.get("match_date", ""),
                "home_team": home_id or "",
                "away_team": away_id or "",
                "home_goals": "" if home_goals is None else home_goals,
                "away_goals": "" if away_goals is None else away_goals,
                "winner": winner_id or "",
                "loser": loser_id or "",
            }
        )

    resolved_matches = pd.DataFrame(resolved_rows)
    return {
        "group_standings": group_standings,
        "third_place": third_place,
        "combination_row": combination_row,
        "resolved_matches": resolved_matches,
        "winners": winners,
        "losers": losers,
        "confirmed_position_slots": confirmed_position_slots or set(),
    }


def stage_entrants(resolved_matches: pd.DataFrame, stage: str) -> set[str]:
    return stage_entrants_by_stage(resolved_matches).get(stage, set())


def stage_entrants_by_stage(resolved_matches: pd.DataFrame) -> dict[str, set[str]]:
    entrants = {stage: set() for stage in STAGES}
    if resolved_matches.empty or "stage" not in resolved_matches.columns:
        return entrants
    for row in resolved_matches.to_dict("records"):
        stage = str(row.get("stage", ""))
        if stage not in entrants:
            entrants[stage] = set()
        home_team = str(row.get("home_team", "")).strip()
        away_team = str(row.get("away_team", "")).strip()
        if home_team:
            entrants[stage].add(home_team)
        if away_team:
            entrants[stage].add(away_team)
    return entrants


def score_side(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "home"
    if home_goals < away_goals:
        return "away"
    return "draw"


def winner_side_from_resolved(row: dict[str, Any] | pd.Series | None) -> str:
    if row is None:
        return ""
    winner = str(row.get("winner", "")).strip()
    if not winner:
        return ""
    if winner == str(row.get("home_team", "")).strip():
        return "home"
    if winner == str(row.get("away_team", "")).strip():
        return "away"
    return ""


def calculate_user_points(
    user_predictions: pd.DataFrame,
    actual_results: pd.DataFrame,
    teams: pd.DataFrame,
    matches: pd.DataFrame,
    knockout_matchups: pd.DataFrame,
    third_place_combinations: pd.DataFrame,
) -> int:
    return calculate_user_score_breakdown(
        user_predictions,
        actual_results,
        teams,
        matches,
        knockout_matchups,
        third_place_combinations,
    )["total_points"]


def team_groups_lookup(teams: pd.DataFrame) -> dict[str, str]:
    if teams.empty or "team_id" not in teams.columns or "group" not in teams.columns:
        return {}
    return dict(zip(teams["team_id"].astype(str), teams["group"].astype(str)))


def team_rankings_lookup(teams: pd.DataFrame) -> dict[str, int]:
    if teams.empty or "team_id" not in teams.columns or "world_cup_ranking" not in teams.columns:
        return {}
    return {
        str(team_id): to_int(ranking, 9999)
        for team_id, ranking in zip(teams["team_id"], teams["world_cup_ranking"])
    }


def team_ids_by_group(teams: pd.DataFrame) -> dict[str, list[str]]:
    grouped = {group: [] for group in GROUPS}
    if teams.empty or "team_id" not in teams.columns or "group" not in teams.columns:
        return grouped
    for team_id, group in zip(teams["team_id"], teams["group"]):
        group_text = str(group)
        if group_text in grouped:
            grouped[group_text].append(str(team_id))
    return grouped


def match_group(match: pd.Series, teams: pd.DataFrame) -> str:
    if str(match.get("stage", "")) != GROUP_STAGE:
        return ""
    team_groups = team_groups_lookup(teams)
    home_group = team_groups.get(str(match.get("home_team", "")), "")
    away_group = team_groups.get(str(match.get("away_team", "")), "")
    return home_group if home_group and home_group == away_group else ""


def group_final_match_ids(matches: pd.DataFrame, teams: pd.DataFrame) -> dict[str, str]:
    final_match_ids: dict[str, str] = {}
    for _, match in matches.iterrows():
        group = match_group(match, teams)
        if group:
            final_match_ids[group] = str(match["match_id"])
    return final_match_ids


def completed_match_ids(results: pd.DataFrame, matches: pd.DataFrame) -> list[str]:
    result_rows = score_lookup(results)
    ids = []
    for match_id in matches["match_id"]:
        if completed_score(result_rows.get(match_id)) is not None:
            ids.append(str(match_id))
    return ids


def results_through_match(results: pd.DataFrame, matches: pd.DataFrame, through_match_id: str | None) -> pd.DataFrame:
    if not through_match_id:
        return results.iloc[0:0].copy()
    match_ids = list(matches["match_id"])
    if through_match_id not in match_ids:
        return results.copy()
    allowed = set(match_ids[: match_ids.index(through_match_id) + 1])
    return results[results["match_id"].isin(allowed)].copy()


def results_for_stage(results: pd.DataFrame, matches: pd.DataFrame, stage: str) -> pd.DataFrame:
    match_ids = set(matches[matches["stage"].eq(stage)]["match_id"].astype(str))
    return results[results["match_id"].astype(str).isin(match_ids)].copy()


def group_is_complete(group: str, matches: pd.DataFrame, results: pd.DataFrame, teams: pd.DataFrame) -> bool:
    group_matches = group_match_rows(group, matches, teams)
    result_rows = score_lookup(results)
    return not group_matches.empty and all(
        completed_score(result_rows.get(str(match_id))) is not None for match_id in group_matches["match_id"]
    )


def completed_groups_lookup(results: pd.DataFrame, matches: pd.DataFrame, teams: pd.DataFrame) -> set[str]:
    result_rows = score_lookup(results)
    matches_by_group = group_match_rows_by_group(matches, teams)
    return completed_groups_from_rows(result_rows, matches_by_group)


def completed_groups_from_rows(
    result_rows: dict[str, dict[str, Any]],
    matches_by_group: dict[str, pd.DataFrame],
) -> set[str]:
    completed_groups: set[str] = set()
    for group, group_matches in matches_by_group.items():
        if group_matches.empty:
            continue
        if all(completed_score(result_rows.get(str(match_id))) is not None for match_id in group_matches["match_id"]):
            completed_groups.add(group)
    return completed_groups


def group_match_rows(group: str, matches: pd.DataFrame, teams: pd.DataFrame) -> pd.DataFrame:
    return group_match_rows_by_group(matches, teams).get(group, matches.iloc[0:0])


def group_match_rows_by_group(matches: pd.DataFrame, teams: pd.DataFrame) -> dict[str, pd.DataFrame]:
    empty = matches.iloc[0:0]
    grouped = {group: empty for group in GROUPS}
    if matches.empty or teams.empty:
        return grouped
    required_columns = {"stage", "home_team", "away_team"}
    if not required_columns.issubset(matches.columns):
        return grouped

    team_groups = team_groups_lookup(teams)
    group_matches = matches[matches["stage"].eq(GROUP_STAGE)]
    home_groups = group_matches["home_team"].astype(str).map(team_groups)
    away_groups = group_matches["away_team"].astype(str).map(team_groups)
    for group in GROUPS:
        grouped[group] = group_matches[home_groups.eq(group) & away_groups.eq(group)]
    return grouped


def remaining_group_matches(group: str, matches: pd.DataFrame, results: pd.DataFrame, teams: pd.DataFrame) -> pd.DataFrame:
    result_rows = score_lookup(results)
    group_matches = group_match_rows(group, matches, teams)
    return group_matches[
        group_matches["match_id"].map(lambda match_id: completed_score(result_rows.get(str(match_id))) is None)
    ]


def group_standing_from_score_rows(
    group: str,
    teams: pd.DataFrame,
    matches: pd.DataFrame,
    score_rows: dict[str, dict[str, Any]],
    use_cards: bool,
    rankings: dict[str, int] | None = None,
    group_teams: list[str] | None = None,
    group_matches: pd.DataFrame | None = None,
) -> pd.DataFrame:
    ordered_team_ids, rows = ordered_group_rows_from_score_rows(
        group,
        teams,
        matches,
        score_rows,
        use_cards,
        rankings=rankings,
        group_teams=group_teams,
        group_matches=group_matches,
    )
    if not ordered_team_ids:
        return pd.DataFrame(columns=STANDING_COLUMNS)
    return pd.DataFrame([rows[team_id] for team_id in ordered_team_ids])[STANDING_COLUMNS].reset_index(drop=True)


def ordered_group_rows_from_score_rows(
    group: str,
    teams: pd.DataFrame,
    matches: pd.DataFrame,
    score_rows: dict[str, dict[str, Any]],
    use_cards: bool,
    rankings: dict[str, int] | None = None,
    group_teams: list[str] | None = None,
    group_matches: pd.DataFrame | None = None,
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    rankings = rankings if rankings is not None else team_rankings_lookup(teams)
    group_teams = group_teams if group_teams is not None else team_ids_by_group(teams).get(group, [])
    rows = {
        team_id: {
            "team_id": team_id,
            "games_played": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "goals_for": 0,
            "goals_against": 0,
            "goal_difference": 0,
            "fair_play_score": 0,
            "points": 0,
        }
        for team_id in group_teams
    }
    group_matches = group_matches if group_matches is not None else group_match_rows(group, matches, teams)

    for match in group_matches.to_dict("records"):
        match_id = str(match["match_id"])
        score = completed_score(score_rows.get(match_id))
        if score is None:
            continue

        home_id = str(match["home_team"])
        away_id = str(match["away_team"])
        home_goals, away_goals = score

        rows[home_id]["games_played"] += 1
        rows[away_id]["games_played"] += 1
        rows[home_id]["goals_for"] += home_goals
        rows[home_id]["goals_against"] += away_goals
        rows[away_id]["goals_for"] += away_goals
        rows[away_id]["goals_against"] += home_goals

        if home_goals > away_goals:
            rows[home_id]["wins"] += 1
            rows[home_id]["points"] += 3
            rows[away_id]["losses"] += 1
        elif home_goals < away_goals:
            rows[away_id]["wins"] += 1
            rows[away_id]["points"] += 3
            rows[home_id]["losses"] += 1
        else:
            rows[home_id]["draws"] += 1
            rows[away_id]["draws"] += 1
            rows[home_id]["points"] += 1
            rows[away_id]["points"] += 1

        if use_cards:
            card_row = score_rows.get(match_id, {})
            rows[home_id]["fair_play_score"] += fair_play_delta(card_row, "home")
            rows[away_id]["fair_play_score"] += fair_play_delta(card_row, "away")

    for row in rows.values():
        row["goal_difference"] = row["goals_for"] - row["goals_against"]
    ordered_team_ids = sort_group_rows(rows, group_matches, score_rows, rankings)
    return ordered_team_ids, {team_id: rows[team_id] for team_id in ordered_team_ids}


def head_to_head_points_between(
    team_id: str,
    other_id: str,
    group_matches: pd.DataFrame,
    score_rows: dict[str, dict[str, Any]],
) -> tuple[int, int] | None:
    for match in group_matches.to_dict("records"):
        home_id = str(match["home_team"])
        away_id = str(match["away_team"])
        if {home_id, away_id} != {team_id, other_id}:
            continue
        score = completed_score(score_rows.get(str(match["match_id"])))
        if score is None:
            return None
        home_goals, away_goals = score
        if home_goals == away_goals:
            return 1, 1
        home_points, away_points = (3, 0) if home_goals > away_goals else (0, 3)
        if team_id == home_id:
            return home_points, away_points
        return away_points, home_points
    return None


def group_lock_context(
    group: str,
    table: pd.DataFrame,
    matches: pd.DataFrame,
    results: pd.DataFrame,
    teams: pd.DataFrame,
    score_rows: dict[str, dict[str, Any]] | None = None,
    group_matches: pd.DataFrame | None = None,
) -> dict[str, Any]:
    score_rows = score_rows if score_rows is not None else score_lookup(results)
    group_matches = group_matches if group_matches is not None else group_match_rows(group, matches, teams)
    team_ids = [str(team_id) for team_id in table["team_id"]]
    points = {str(row["team_id"]): to_int(row["points"]) for row in table.to_dict("records")}
    remaining_by_team = {team_id: 0 for team_id in team_ids}
    for match in group_matches.to_dict("records"):
        if completed_score(score_rows.get(str(match["match_id"]))) is not None:
            continue
        remaining_by_team[str(match["home_team"])] += 1
        remaining_by_team[str(match["away_team"])] += 1
    max_points = {
        team_id: points.get(team_id, 0) + 3 * remaining_by_team.get(team_id, 0)
        for team_id in team_ids
    }
    return {
        "group_matches": group_matches,
        "score_rows": score_rows,
        "team_ids": team_ids,
        "points": points,
        "max_points": max_points,
    }


def can_finish_above(team_id: str, other_id: str, context: dict[str, Any]) -> bool:
    team_max = context["max_points"].get(team_id, 0)
    other_points = context["points"].get(other_id, 0)
    if team_max > other_points:
        return True
    if team_max < other_points:
        return False

    h2h = head_to_head_points_between(
        team_id,
        other_id,
        context["group_matches"],
        context["score_rows"],
    )
    if h2h is None:
        return True
    team_h2h_points, other_h2h_points = h2h
    return team_h2h_points >= other_h2h_points


def match_score_points_for_match(
    match: pd.Series,
    predicted_scores: dict[str, dict[str, Any]],
    actual_scores: dict[str, dict[str, Any]],
    predicted_resolved_rows: dict[str, dict[str, Any]],
    actual_resolved_rows: dict[str, dict[str, Any]],
) -> dict[str, int]:
    match_id = str(match["match_id"])
    predicted = completed_score(predicted_scores.get(match_id))
    actual = completed_score(actual_scores.get(match_id))
    if predicted is None or actual is None:
        return {
            "winner_points": 0,
            "home_goal_points": 0,
            "away_goal_points": 0,
            "total_points": 0,
            "correct_winner": 0,
            "exact_home": 0,
            "exact_away": 0,
            "exact_score": 0,
        }

    pred_home, pred_away = predicted
    real_home, real_away = actual
    stage = str(match["stage"])
    if stage != GROUP_STAGE:
        return {
            "winner_points": 0,
            "home_goal_points": 0,
            "away_goal_points": 0,
            "total_points": 0,
            "correct_winner": 0,
            "exact_home": 0,
            "exact_away": 0,
            "exact_score": 0,
        }

    correct_winner = 0
    if stage == GROUP_STAGE:
        correct_winner = int(score_side(pred_home, pred_away) == score_side(real_home, real_away))

    exact_home = int(pred_home == real_home)
    exact_away = int(pred_away == real_away)
    exact_score = int(exact_home and exact_away)
    winner_points = MATCH_OUTCOME_POINTS * correct_winner
    home_goal_points = MATCH_HOME_GOALS_POINTS * exact_home
    away_goal_points = MATCH_AWAY_GOALS_POINTS * exact_away
    return {
        "winner_points": winner_points,
        "home_goal_points": home_goal_points,
        "away_goal_points": away_goal_points,
        "total_points": winner_points + home_goal_points + away_goal_points,
        "correct_winner": correct_winner,
        "exact_home": exact_home,
        "exact_away": exact_away,
        "exact_score": exact_score,
    }


def calculate_user_score_breakdown(
    user_predictions: pd.DataFrame,
    actual_results: pd.DataFrame,
    teams: pd.DataFrame,
    matches: pd.DataFrame,
    knockout_matchups: pd.DataFrame,
    third_place_combinations: pd.DataFrame,
) -> dict[str, int]:
    prediction_state = derive_tournament_state(
        teams, matches, user_predictions, knockout_matchups, third_place_combinations, use_cards=False
    )
    actual_state = derive_tournament_state(
        teams,
        matches,
        actual_results,
        knockout_matchups,
        third_place_combinations,
        use_cards=True,
        require_confirmed_placements=True,
    )
    return calculate_user_score_breakdown_from_states(
        user_predictions, prediction_state, actual_results, actual_state, teams, matches
    )


def calculate_user_score_breakdown_from_states(
    user_predictions: pd.DataFrame,
    prediction_state: dict[str, Any],
    actual_results: pd.DataFrame,
    actual_state: dict[str, Any],
    teams: pd.DataFrame,
    matches: pd.DataFrame,
    awarded_group_standings: set[str] | None = None,
    completed_groups: set[str] | None = None,
    prediction_stage_entrants: dict[str, set[str]] | None = None,
    actual_stage_entrants: dict[str, set[str]] | None = None,
    predicted_scores: dict[str, dict[str, Any]] | None = None,
    actual_scores: dict[str, dict[str, Any]] | None = None,
    match_records: list[dict[str, Any]] | None = None,
) -> dict[str, int]:
    match_score_points = 0
    group_standings_points = 0
    knockout_progression_points = 0
    correct_winners = 0
    exact_home_goals = 0
    exact_away_goals = 0
    exact_scores = 0
    predicted_scores = predicted_scores if predicted_scores is not None else score_lookup(user_predictions)
    actual_scores = actual_scores if actual_scores is not None else score_lookup(actual_results)
    match_records = match_records if match_records is not None else matches.to_dict("records")
    predicted_resolved = prediction_state["resolved_matches"]
    actual_resolved = actual_state["resolved_matches"]
    prediction_stage_entrants = (
        prediction_stage_entrants
        if prediction_stage_entrants is not None
        else stage_entrants_by_stage(predicted_resolved)
    )
    actual_stage_entrants = (
        actual_stage_entrants
        if actual_stage_entrants is not None
        else stage_entrants_by_stage(actual_resolved)
    )

    for match in match_records:
        scored = match_score_points_for_match(
            match, predicted_scores, actual_scores, {}, {}
        )
        match_score_points += scored["total_points"]
        correct_winners += scored["correct_winner"]
        exact_home_goals += scored["exact_home"]
        exact_away_goals += scored["exact_away"]
        exact_scores += scored["exact_score"]

    for group in GROUPS:
        group_complete = (
            group in completed_groups
            if completed_groups is not None
            else group_is_complete(group, matches, actual_results, teams)
        )
        if not group_complete:
            continue
        if awarded_group_standings is not None and group not in awarded_group_standings:
            continue
        predicted_table = prediction_state["group_standings"][group]
        actual_table = actual_state["group_standings"][group]
        for position in range(min(len(predicted_table), len(actual_table))):
            if predicted_table.iloc[position]["team_id"] == actual_table.iloc[position]["team_id"]:
                group_standings_points += GROUP_STANDING_POSITION_POINTS

    for stage, stage_points in KNOCKOUT_STAGE_POINTS.items():
        actual_entrants = actual_stage_entrants.get(stage, set())
        if actual_entrants:
            predicted_entrants = prediction_stage_entrants.get(stage, set())
            knockout_progression_points += len(predicted_entrants & actual_entrants) * stage_points

    predicted_third_place = prediction_state["winners"].get(THIRD_PLACE_MATCH_ID)
    actual_third_place = actual_state["winners"].get(THIRD_PLACE_MATCH_ID)
    if predicted_third_place and predicted_third_place == actual_third_place:
        knockout_progression_points += THIRD_PLACE_WINNER_POINTS

    predicted_winner = prediction_state["winners"].get(FINAL_MATCH_ID)
    actual_winner = actual_state["winners"].get(FINAL_MATCH_ID)
    if predicted_winner and predicted_winner == actual_winner:
        knockout_progression_points += CHAMPION_POINTS

    total_points = match_score_points + group_standings_points + knockout_progression_points
    return {
        "total_points": total_points,
        "match_score_points": match_score_points,
        "group_standings_points": group_standings_points,
        "knockout_progression_points": knockout_progression_points,
        "correct_winners": correct_winners,
        "exact_home_goals": exact_home_goals,
        "exact_away_goals": exact_away_goals,
        "exact_goal_components": exact_home_goals + exact_away_goals,
        "exact_scores": exact_scores,
    }


def update_leaderboard_file() -> pd.DataFrame:
    ensure_data_files()
    teams = read_csv(TEAMS_FILE)
    matches = read_csv(MATCHES_FILE)
    results = normalize_results(read_csv(RESULTS_FILE))
    users = normalize_users(read_csv(USERS_FILE))
    knockout_matchups = read_csv(KNOCKOUT_MATCHUPS_FILE)
    third_place_combinations = read_csv(THIRD_PLACE_COMBINATIONS_FILE)

    participants = leaderboard_participants(users, include_ai=False)
    snapshot = leaderboard_snapshot(
        participants,
        results,
        teams,
        matches,
        knockout_matchups,
        third_place_combinations,
    )
    leaderboard = (
        snapshot[["rank", "user_id", "user_name", "total_points"]].copy()
        if not snapshot.empty
        else pd.DataFrame(columns=["rank", "user_id", "user_name", "total_points"])
    )

    if google_sheets_enabled():
        write_sheet("leaderboard", leaderboard, ["rank", "user_id", "user_name", "total_points"])
    else:
        leaderboard.to_csv(LEADERBOARD_FILE, index=False)
    users = users.drop(columns=["total_points"], errors="ignore").merge(
        leaderboard[["user_id", "total_points"]] if not leaderboard.empty else pd.DataFrame(columns=["user_id", "total_points"]),
        on="user_id",
        how="left",
    )
    users["total_points"] = users["total_points"].fillna(0).astype(int).astype(str)
    save_users(users[USERS_COLUMNS])
    clear_cache()
    return leaderboard


def normalize_results(results: pd.DataFrame) -> pd.DataFrame:
    columns = ["match_id", "home_goals", "away_goals", PENALTY_WINNER_COLUMN, *CARD_COLUMNS]
    if results.empty:
        return pd.DataFrame(columns=columns)
    for column in columns:
        if column not in results.columns:
            results[column] = ""
    return results[columns].copy()


def validate_sources(
    teams: pd.DataFrame,
    matches: pd.DataFrame,
    users: pd.DataFrame,
    results: pd.DataFrame,
    knockout_matchups: pd.DataFrame,
    third_place_combinations: pd.DataFrame,
    validate_predictions: bool = False,
) -> list[str]:
    errors: list[str] = []

    required = {
        "teams.csv": (teams, ["team_id", "team_name", "group", "world_cup_ranking", "logo_link"]),
        "matches.csv": (matches, ["match_id", "stage", "match_date", "home_team", "away_team"]),
        "knockout_matchups.csv": (knockout_matchups, ["match_id", "home_team", "away_team"]),
        "third_place_combinations.csv": (
            third_place_combinations,
            ["combination_id", "list_of_advanced_third_placed_teams", "1A", "1B", "1D", "1E", "1G", "1I", "1K", "1L"],
        ),
    }

    for name, (table, columns) in required.items():
        missing = [column for column in columns if column not in table.columns]
        if missing:
            errors.append(f"{name} is missing columns: {', '.join(missing)}")

    if errors:
        return errors

    valid_groups = set(GROUPS)
    valid_match_ids = set(matches["match_id"])
    valid_user_ids = set(users["user_id"]) if "user_id" in users.columns else set()

    if teams["team_id"].duplicated().any():
        errors.append("teams.csv has duplicate team_id values.")
    if not teams["group"].isin(GROUPS).all():
        errors.append("teams.csv contains a group outside A-L.")
    if teams["world_cup_ranking"].apply(optional_natural).isna().any():
        errors.append("teams.csv contains an invalid world_cup_ranking.")

    if matches["match_id"].duplicated().any():
        errors.append("matches.csv has duplicate match_id values.")
    if not matches["stage"].isin(STAGES).all():
        errors.append("matches.csv contains an invalid stage.")

    valid_teams = set(teams["team_id"])
    group_matches = matches[matches["stage"] == GROUP_STAGE]
    invalid_home = group_matches[~group_matches["home_team"].isin(valid_teams)]
    invalid_away = group_matches[~group_matches["away_team"].isin(valid_teams)]
    if not invalid_home.empty or not invalid_away.empty:
        errors.append("matches.csv contains invalid group-stage team IDs.")

    if not users.empty and users["user_name"].str.lower().duplicated().any():
        errors.append("users.csv has duplicate user_name values.")
    if not users.empty and users["user_id"].duplicated().any():
        errors.append("users.csv has duplicate user_id values.")

    if knockout_matchups["match_id"].duplicated().any():
        errors.append("knockout_matchups.csv has duplicate match_id values.")
    invalid_knockout_match_ids = knockout_matchups[~knockout_matchups["match_id"].isin(valid_match_ids)]
    if not invalid_knockout_match_ids.empty:
        errors.append("knockout_matchups.csv contains match_id values not found in matches.csv.")

    for _, row in knockout_matchups.iterrows():
        for column in ["home_team", "away_team"]:
            slot = str(row[column]).strip()
            if not valid_knockout_slot(slot, valid_match_ids):
                errors.append(
                    f"knockout_matchups.csv has invalid {column} slot '{slot}' for match_id {row['match_id']}."
                )

    if third_place_combinations["combination_id"].duplicated().any():
        errors.append("third_place_combinations.csv has duplicate combination_id values.")
    for _, row in third_place_combinations.iterrows():
        combination_id = row["combination_id"]
        try:
            advanced_groups = ast.literal_eval(row["list_of_advanced_third_placed_teams"])
        except (ValueError, SyntaxError):
            errors.append(
                f"third_place_combinations.csv has an invalid list_of_advanced_third_placed_teams for {combination_id}."
            )
            advanced_groups = []
        if not isinstance(advanced_groups, list) or len(advanced_groups) != 8:
            errors.append(
                f"third_place_combinations.csv combination {combination_id} must list exactly 8 advanced groups."
            )
        elif len(set(advanced_groups)) != 8 or not set(advanced_groups).issubset(valid_groups):
            errors.append(
                f"third_place_combinations.csv combination {combination_id} contains invalid or duplicate groups."
            )
        for column in ["1A", "1B", "1D", "1E", "1G", "1I", "1K", "1L"]:
            slot = str(row[column]).strip()
            if not re.fullmatch(r"3[A-L]", slot):
                errors.append(
                    f"third_place_combinations.csv combination {combination_id} has invalid {column} value '{slot}'."
                )

    result_errors = validate_results_file(results, valid_match_ids)
    errors.extend(result_errors)

    if validate_predictions:
        prediction_errors = validate_prediction_files(valid_user_ids, valid_match_ids)
        errors.extend(prediction_errors)

    return errors


def valid_knockout_slot(slot: str, valid_match_ids: set[str]) -> bool:
    if re.fullmatch(r"[123][A-L]", slot):
        return True
    if re.fullmatch(r"3[A-L]{2,}", slot):
        groups = set(slot[1:])
        return len(groups) == len(slot[1:]) and groups.issubset(set(GROUPS))
    if slot.startswith("winner_"):
        return slot.replace("winner_", "", 1) in valid_match_ids
    if slot.startswith("loser_"):
        return slot.replace("loser_", "", 1) in valid_match_ids
    return False


def validate_results_file(results: pd.DataFrame, valid_match_ids: set[str]) -> list[str]:
    errors: list[str] = []
    if results.empty:
        return errors

    if results["match_id"].duplicated().any():
        errors.append("results.csv has duplicate match_id values.")
    invalid_match_ids = results[~results["match_id"].isin(valid_match_ids)]
    if not invalid_match_ids.empty:
        errors.append("results.csv contains match_id values not found in matches.csv.")

    numeric_columns = ["home_goals", "away_goals", *CARD_COLUMNS]
    for column in numeric_columns:
        invalid = results[
            results[column].astype(str).str.strip().ne("")
            & results[column].apply(optional_natural).isna()
        ]
        if not invalid.empty:
            errors.append(f"results.csv contains invalid natural-number values in {column}.")

    return errors


def validate_prediction_files(valid_user_ids: set[str], valid_match_ids: set[str]) -> list[str]:
    errors: list[str] = []
    if google_sheets_enabled():
        predictions = read_sheet(PREDICTIONS_SHEET, tuple(PREDICTION_COLUMNS))
        if predictions.empty:
            return errors
        missing = [column for column in PREDICTION_COLUMNS if column not in predictions.columns]
        if missing:
            return [f"{PREDICTIONS_SHEET} sheet is missing columns: {', '.join(missing)}"]
        invalid_users = predictions[~predictions["user_id"].isin(valid_user_ids)]
        if not invalid_users.empty:
            errors.append(f"{PREDICTIONS_SHEET} sheet contains user_id values not found in users.")
        invalid_matches = predictions[~predictions["match_id"].isin(valid_match_ids)]
        if not invalid_matches.empty:
            errors.append(f"{PREDICTIONS_SHEET} sheet contains match_id values not found in matches.")
        if predictions[["user_id", "match_id"]].duplicated().any():
            errors.append(f"{PREDICTIONS_SHEET} sheet has duplicate user_id/match_id combinations.")
        for column in ["home_goals", "away_goals"]:
            invalid = predictions[predictions[column].apply(optional_natural).isna()]
            if not invalid.empty:
                errors.append(f"{PREDICTIONS_SHEET} sheet contains invalid natural-number values in {column}.")
        return errors

    if not PREDICTIONS_DIR.exists():
        return errors

    for path in sorted(PREDICTIONS_DIR.glob("predictions_*.csv")):
        try:
            predictions = pd.read_csv(path, dtype=str).fillna("")
        except (OSError, pd.errors.ParserError):
            errors.append(f"{path.name} could not be read as a valid CSV file.")
            continue

        missing = [column for column in PREDICTION_COLUMNS if column not in predictions.columns]
        if missing:
            errors.append(f"{path.name} is missing columns: {', '.join(missing)}")
            continue

        predictions = predictions[PREDICTION_COLUMNS].copy()
        invalid_users = predictions[~predictions["user_id"].isin(valid_user_ids)]
        if not invalid_users.empty:
            errors.append(f"{path.name} contains user_id values not found in users.csv.")

        invalid_matches = predictions[~predictions["match_id"].isin(valid_match_ids)]
        if not invalid_matches.empty:
            errors.append(f"{path.name} contains match_id values not found in matches.csv.")

        if predictions["match_id"].duplicated().any():
            errors.append(f"{path.name} has duplicate match_id values.")

        for column in ["home_goals", "away_goals"]:
            invalid = predictions[predictions[column].apply(optional_natural).isna()]
            if not invalid.empty:
                errors.append(f"{path.name} contains invalid natural-number values in {column}.")

    return errors


def validate_prediction_submission(predictions: pd.DataFrame, matches: pd.DataFrame) -> list[str]:
    errors = []

    def match_list(match_ids: pd.Series | list[Any]) -> str:
        return ", ".join(str(match_id) for match_id in match_ids)

    missing = predictions[
        predictions["home_goals"].astype(str).str.strip().eq("")
        | predictions["away_goals"].astype(str).str.strip().eq("")
    ]
    if not missing.empty:
        errors.append(f"Fill in scores for all matches. Missing: {match_list(missing['match_id'])}")

    invalid = predictions[
        predictions["home_goals"].apply(optional_natural).isna()
        | predictions["away_goals"].apply(optional_natural).isna()
    ]
    if not invalid.empty:
        errors.append(f"All scores must be natural numbers. Check: {match_list(invalid['match_id'])}")

    knockout_ids = set(matches[matches["stage"].isin(KNOCKOUT_STAGES)]["match_id"])
    missing_penalty_winners = []
    for _, row in predictions.iterrows():
        if row["match_id"] in knockout_ids:
            home = optional_natural(row["home_goals"])
            away = optional_natural(row["away_goals"])
            if home is not None and away is not None and home == away:
                penalty_winner = str(row.get(PENALTY_WINNER_COLUMN, "")).strip()
                if not penalty_winner:
                    missing_penalty_winners.append(row["match_id"])
    if missing_penalty_winners:
        errors.append(
            "Select a penalties winner for tied knockout matches: "
            f"{match_list(missing_penalty_winners)}"
        )

    return errors


def write_actual_standings(state: dict[str, Any]) -> None:
    if google_sheets_enabled():
        return
    STANDINGS_DIR.mkdir(exist_ok=True)
    for group, table in state["group_standings"].items():
        table.to_csv(STANDINGS_DIR / f"group_{group}.csv", index=False)
    state["third_place"][STANDING_COLUMNS].to_csv(STANDINGS_DIR / "third_place_standings.csv", index=False)


def advancing_team_ids_from_standings(
    group_standings: dict[str, pd.DataFrame],
    third_place: pd.DataFrame,
) -> set[str]:
    advancing_team_ids: set[str] = set()
    for table in group_standings.values():
        advancing_team_ids.update(str(team_id) for team_id in table.head(2)["team_id"])
    if not third_place.empty:
        advancing_team_ids.update(str(team_id) for team_id in third_place.head(8)["team_id"])
    return advancing_team_ids


def group_qualification_statuses(
    group_standings: dict[str, pd.DataFrame],
    third_place: pd.DataFrame,
    matches: pd.DataFrame,
    results: pd.DataFrame,
    teams: pd.DataFrame,
) -> dict[str, str]:
    statuses: dict[str, str] = {}
    advancing_team_ids = advancing_team_ids_from_standings(group_standings, third_place)
    all_groups_complete = all(group_is_complete(group, matches, results, teams) for group in GROUPS)

    for group, table in group_standings.items():
        group_team_ids = [str(team_id) for team_id in table["team_id"]]
        group_complete = group_is_complete(group, matches, results, teams)
        if group_complete:
            for position, team_id in enumerate(group_team_ids):
                if position < 2:
                    statuses[team_id] = "advanced"
                elif all_groups_complete and team_id in advancing_team_ids:
                    statuses[team_id] = "advanced"
                elif position >= 3 or all_groups_complete:
                    statuses[team_id] = "eliminated"
            continue

        context = group_lock_context(group, table, matches, results, teams)
        for team_id in group_team_ids:
            teams_that_can_finish_above = sum(
                1
                for other_id in context["team_ids"]
                if other_id != team_id and can_finish_above(other_id, team_id, context)
            )
            teams_team_can_finish_above = sum(
                1
                for other_id in context["team_ids"]
                if other_id != team_id and can_finish_above(team_id, other_id, context)
            )
            if teams_that_can_finish_above <= 1:
                statuses[team_id] = "advanced"
            elif teams_team_can_finish_above == 0:
                statuses[team_id] = "eliminated"

    return statuses


def display_group_standings(group_standings: dict[str, pd.DataFrame], teams: pd.DataFrame) -> None:
    third_place = calculate_third_place_standings(group_standings, teams)
    advancing_team_ids = advancing_team_ids_from_standings(group_standings, third_place)
    for first, second, third in zip(GROUPS[0::3], GROUPS[1::3], GROUPS[2::3]):
        cols = st.columns(3)
        for col, group in zip(cols, [first, second, third]):
            col.markdown(f"**Group {group}**")
            with col:
                display_single_group_standing(group_standings[group], teams, advancing_team_ids)
                display_advancing_legend()


def display_single_group_standing(
    table: pd.DataFrame,
    teams: pd.DataFrame,
    advancing_team_ids: set[str] | None = None,
    qualification_statuses: dict[str, str] | None = None,
) -> None:
    advancing_team_ids = advancing_team_ids or set()
    table = table.copy()
    table.insert(0, "pos", range(1, len(table) + 1))
    rows = []
    for _, row in table.iterrows():
        row_cells = (
            [
                (str(row["pos"]), True),
                (team_badge_with_status_html(row["team_id"], teams, qualification_statuses), False),
                (str(row["games_played"]), True),
                (str(row["goals_for"]), True),
                (str(row["goals_against"]), True),
                (str(row["goal_difference"]), True),
                (str(row["points"]), True, "points-column"),
            ]
        )
        rows.append((row_cells, "advancing" if row["team_id"] in advancing_team_ids else ""))
    render_html_table(
        [
            ("Position", True),
            ("Team", False),
            ("Games\nplayed", True),
            ("Goals\nfor", True),
            ("Goals\nagainst", True),
            ("Goal\ndifference", True),
            ("Points", True, "points-column"),
        ],
        rows,
    )


def standings_rows(
    table: pd.DataFrame,
    teams: pd.DataFrame,
    include_group: bool = False,
    advancing_team_ids: set[str] | None = None,
    qualification_statuses: dict[str, str] | None = None,
) -> list[tuple[list[tuple[str, bool]], str]]:
    advancing_team_ids = advancing_team_ids or set()
    rows = []
    for _, row in table.iterrows():
        rendered_row = [(str(row.iloc[0]), True)]
        if include_group:
            rendered_row.append((html.escape(str(row["group"])), True))
        rendered_row.extend(
            [
                (team_badge_with_status_html(row["team_id"], teams, qualification_statuses), False),
                (str(row["games_played"]), True),
                (str(row["goals_for"]), True),
                (str(row["goals_against"]), True),
                (str(row["goal_difference"]), True),
                (str(row["points"]), True, "points-column"),
            ]
        )
        rows.append((rendered_row, "advancing" if row["team_id"] in advancing_team_ids else ""))
    return rows


def display_third_place(
    third_place: pd.DataFrame,
    teams: pd.DataFrame,
    advancing_team_ids: set[str] | None = None,
    qualification_statuses: dict[str, str] | None = None,
) -> None:
    table = third_place.copy()
    table.insert(0, "rank", range(1, len(table) + 1))
    render_html_table(
        [
            ("Rank", True),
            ("Group", True),
            ("Team", False),
            ("Games\nplayed", True),
            ("Goals\nfor", True),
            ("Goals\nagainst", True),
            ("Goal\ndifference", True),
            ("Points", True, "points-column"),
        ],
        standings_rows(
            table.head(12),
            teams,
            include_group=True,
            advancing_team_ids=advancing_team_ids,
            qualification_statuses=qualification_statuses,
        ),
        table_class="third-place-table",
    )


def match_rows(rows: pd.DataFrame, teams: pd.DataFrame) -> list[list[tuple[str, bool]]]:
    rendered_rows = []
    for _, row in rows.iterrows():
        score = "" if row["home_goals"] == "" or row["away_goals"] == "" else f"{row['home_goals']} - {row['away_goals']}"
        rendered_rows.append(
            [
                (html.escape(str(row["match_id"])), False),
                (html.escape(str(row.get("match_date", ""))), False),
                (team_badge_html(row["home_team"], teams), False),
                (html.escape(score), False),
                (team_badge_html(row["away_team"], teams), False),
            ]
        )
    return rendered_rows


def display_match_table(rows: pd.DataFrame, teams: pd.DataFrame, include_stage: bool = False) -> None:
    rendered_rows = match_rows(rows, teams)
    headers = [("match", False), ("date", False), ("home", False), ("score", False), ("away", False)]
    if include_stage:
        headers.insert(1, ("stage", False))
        for rendered_row, (_, source_row) in zip(rendered_rows, rows.iterrows()):
            rendered_row.insert(1, (html.escape(str(source_row["stage"]).replace("_", " ").title()), False))
    render_html_table(headers, rendered_rows)


def display_bracket(resolved_matches: pd.DataFrame, teams: pd.DataFrame) -> None:
    for stage in KNOCKOUT_STAGES:
        rows = resolved_matches[resolved_matches["stage"] == stage].copy()
        if rows.empty:
            continue
        st.markdown(f"**{stage.replace('_', ' ').title()}**")
        display_match_table(rows, teams)


def group_prediction_is_complete(matches: pd.DataFrame) -> bool:
    group_matches = matches[matches["stage"] == GROUP_STAGE]
    for match_id in group_matches["match_id"]:
        home = optional_natural(st.session_state.get(f"pred_home_{match_id}", ""))
        away = optional_natural(st.session_state.get(f"pred_away_{match_id}", ""))
        if home is None or away is None:
            return False
    return True


def guard_knockout_prediction(field_key: str, group_match_ids: list[str], reset_value: Any = None) -> None:
    for match_id in group_match_ids:
        home = optional_natural(st.session_state.get(f"pred_home_{match_id}", ""))
        away = optional_natural(st.session_state.get(f"pred_away_{match_id}", ""))
        if home is None or away is None:
            st.session_state[field_key] = reset_value
            st.session_state["show_group_required_dialog"] = True
            return


def render_group_required_dialog() -> None:
    if not st.session_state.pop("show_group_required_dialog", False):
        return

    message = "Please fill in all group-stage scores before entering knockout-stage predictions."
    if hasattr(st, "dialog"):
        @st.dialog("Group scores required")
        def group_required_dialog() -> None:
            st.write(message)

        group_required_dialog()
    else:
        st.warning(message)


def normalize_prediction_widget_value(key: str) -> None:
    value = optional_natural(st.session_state.get(key, ""))
    st.session_state[key] = value


def group_predictions_are_complete(group_match_ids: list[str]) -> bool:
    for match_id in group_match_ids:
        home = optional_natural(st.session_state.get(f"pred_home_{match_id}", ""))
        away = optional_natural(st.session_state.get(f"pred_away_{match_id}", ""))
        if home is None or away is None:
            return False
    return True


def render_goal_control(
    label: str,
    key: str,
    is_knockout: bool,
    group_match_ids: list[str],
    label_visibility: str = "collapsed",
) -> None:
    st.number_input(
        label,
        min_value=0,
        max_value=99,
        step=1,
        value=None,
        format="%d",
        key=key,
        label_visibility=label_visibility,
        on_change=guard_knockout_prediction if is_knockout else None,
        args=(key, group_match_ids) if is_knockout else None,
        width="stretch",
    )


def render_penalty_winner_control(
    match_id: str,
    home_id: str,
    away_id: str,
    teams: pd.DataFrame,
    group_match_ids: list[str],
) -> None:
    key = f"pred_penalty_{match_id}"
    options = [""] + [team_id for team_id in [home_id, away_id] if team_id]
    home_goals = optional_natural(st.session_state.get(f"pred_home_{match_id}", ""))
    away_goals = optional_natural(st.session_state.get(f"pred_away_{match_id}", ""))
    is_tied_score = home_goals is not None and away_goals is not None and home_goals == away_goals
    current_value = str(st.session_state.get(key, "")).strip()
    if current_value not in options or (current_value and not is_tied_score):
        st.session_state[key] = ""

    labels = {"": "-"}
    labels.update({team_id: team_name(team_id, teams) for team_id in options if team_id})
    st.selectbox(
        "Penalties",
        options=options,
        key=key,
        format_func=lambda value: labels.get(value, value),
        on_change=guard_knockout_prediction,
        args=(key, group_match_ids, ""),
        disabled=len(options) < 3 or not is_tied_score,
        width="stretch",
    )


def render_prediction_inputs(
    matches: pd.DataFrame,
    resolved: pd.DataFrame,
    teams: pd.DataFrame,
    group_standings: dict[str, pd.DataFrame],
    third_place: pd.DataFrame,
) -> None:
    render_group_required_dialog()
    group_by_team = dict(zip(teams["team_id"], teams["group"]))
    group_match_ids = matches[matches["stage"] == GROUP_STAGE]["match_id"].tolist()
    advancing_team_ids = advancing_team_ids_from_standings(group_standings, third_place)

    for group in GROUPS:
        group_matches = matches[
            (matches["stage"] == GROUP_STAGE)
            & (matches["home_team"].map(group_by_team) == group)
            & (matches["away_team"].map(group_by_team) == group)
        ]
        if group_matches.empty:
            continue

        with st.expander(f"Group {group}", expanded=True):
            match_col, table_col = st.columns([0.34, 0.66], gap="large")
            with match_col:
                for _, match in group_matches.iterrows():
                    render_prediction_match(match, resolved, teams, group_match_ids)
            with table_col:
                st.markdown(f"**Group {group} Standings**")
                display_single_group_standing(group_standings[group], teams, advancing_team_ids)
                display_advancing_legend()

    st.markdown("#### Third-Place Ranking")
    display_third_place(third_place, teams, advancing_team_ids)
    display_advancing_legend()
    st.markdown('<div class="section-gap"></div>', unsafe_allow_html=True)

    knockout_matches = matches[matches["stage"].isin(KNOCKOUT_STAGES)]
    if knockout_matches.empty:
        return

    st.subheader("Knockout Phase")
    for stage in KNOCKOUT_STAGES:
        stage_matches = knockout_matches[knockout_matches["stage"] == stage]
        if stage_matches.empty:
            continue
        with st.expander(stage.replace("_", " ").title(), expanded=True):
            for _, match in stage_matches.iterrows():
                render_prediction_match(match, resolved, teams, group_match_ids)


def readonly_value(value: Any) -> str:
    text = str(value).strip()
    return "-" if text == "" else html.escape(text)


def render_readonly_box(value: str, label: str = "") -> None:
    label_html = f'<div class="readonly-score-label">{html.escape(label)}</div>' if label else ""
    st.markdown(
        f'{label_html}<div class="readonly-score-box">{value}</div>',
        unsafe_allow_html=True,
    )


def render_readonly_match(match: pd.Series, resolved: pd.DataFrame, teams: pd.DataFrame) -> None:
    match_id = match["match_id"]
    resolved_row = resolved.loc[match_id]
    home_id = resolved_row["home_team"]
    away_id = resolved_row["away_team"]
    is_knockout = match["stage"] in KNOCKOUT_STAGES
    label = (
        f'<div class="match-label">{html.escape(str(match_id))}: '
        f'{team_badge_html(home_id, teams)} &nbsp;vs&nbsp;&nbsp; {team_badge_html(away_id, teams)}</div>'
    )
    st.markdown(label, unsafe_allow_html=True)

    home_value = readonly_value(resolved_row["home_goals"])
    away_value = readonly_value(resolved_row["away_goals"])
    if is_knockout:
        penalty_value = "-"
        home_goals = optional_natural(resolved_row["home_goals"])
        away_goals = optional_natural(resolved_row["away_goals"])
        if home_goals is not None and away_goals is not None and home_goals == away_goals:
            penalty_value = html.escape(team_name(resolved_row["winner"], teams)) if resolved_row["winner"] else "-"

        col1, col2, col3 = st.columns([0.28, 0.28, 0.44], gap="small")
        with col1:
            render_readonly_box(home_value, "Home")
        with col2:
            render_readonly_box(away_value, "Away")
        with col3:
            render_readonly_box(penalty_value, "Penalties")
    else:
        col1, col2 = st.columns([0.5, 0.5], gap="small")
        with col1:
            render_readonly_box(home_value)
        with col2:
            render_readonly_box(away_value)


def render_readonly_results_inputs(
    matches: pd.DataFrame,
    resolved: pd.DataFrame,
    teams: pd.DataFrame,
    group_standings: dict[str, pd.DataFrame],
    third_place: pd.DataFrame,
) -> None:
    group_by_team = dict(zip(teams["team_id"], teams["group"]))
    advancing_team_ids = advancing_team_ids_from_standings(group_standings, third_place)
    result_rows = []
    for _, match in matches.iterrows():
        resolved_row = resolved.loc[match["match_id"]].to_dict() if match["match_id"] in resolved.index else {}
        result_rows.append(
            {
                "match_id": match["match_id"],
                "home_goals": resolved_row.get("home_goals", ""),
                "away_goals": resolved_row.get("away_goals", ""),
                PENALTY_WINNER_COLUMN: resolved_row.get("winner", ""),
            }
        )
    scoped_results = pd.DataFrame(result_rows)
    qualification_statuses = group_qualification_statuses(
        group_standings,
        third_place,
        matches,
        scoped_results,
        teams,
    )

    for group in GROUPS:
        group_matches = matches[
            (matches["stage"] == GROUP_STAGE)
            & (matches["home_team"].map(group_by_team) == group)
            & (matches["away_team"].map(group_by_team) == group)
        ]
        if group_matches.empty:
            continue

        with st.expander(f"Group {group}", expanded=True):
            match_col, table_col = st.columns([0.34, 0.66], gap="large")
            with match_col:
                for _, match in group_matches.iterrows():
                    render_readonly_match(match, resolved, teams)
            with table_col:
                st.markdown(f"**Group {group} Standings**")
                display_single_group_standing(
                    group_standings[group],
                    teams,
                    advancing_team_ids,
                    qualification_statuses=qualification_statuses,
                )
                display_advancing_legend()

    st.markdown("#### Third-Place Ranking")
    display_third_place(
        third_place,
        teams,
        advancing_team_ids,
        qualification_statuses=qualification_statuses,
    )
    display_advancing_legend()
    st.markdown('<div class="section-gap"></div>', unsafe_allow_html=True)

    knockout_matches = matches[matches["stage"].isin(KNOCKOUT_STAGES)]
    if knockout_matches.empty:
        return

    st.subheader("Knockout Phase")
    for stage in KNOCKOUT_STAGES:
        stage_matches = knockout_matches[knockout_matches["stage"] == stage]
        if stage_matches.empty:
            continue
        with st.expander(stage.replace("_", " ").title(), expanded=True):
            for _, match in stage_matches.iterrows():
                render_readonly_match(match, resolved, teams)


def render_prediction_match(
    match: pd.Series,
    resolved: pd.DataFrame,
    teams: pd.DataFrame,
    group_match_ids: list[str],
) -> None:
    match_id = match["match_id"]
    resolved_row = resolved.loc[match_id]
    home_id = resolved_row["home_team"]
    away_id = resolved_row["away_team"]
    is_knockout = match["stage"] in KNOCKOUT_STAGES
    label = (
        f'<div class="match-label">{html.escape(str(match_id))}: '
        f'{team_badge_html(home_id, teams)} &nbsp;vs&nbsp;&nbsp; {team_badge_html(away_id, teams)}</div>'
    )
    st.markdown(label, unsafe_allow_html=True)
    home_key = f"pred_home_{match_id}"
    away_key = f"pred_away_{match_id}"
    normalize_prediction_widget_value(home_key)
    normalize_prediction_widget_value(away_key)
    if is_knockout:
        col1, col2, col3 = st.columns([0.28, 0.28, 0.44], gap="small")
        with col1:
            render_goal_control("Home", home_key, is_knockout, group_match_ids, label_visibility="visible")
        with col2:
            render_goal_control("Away", away_key, is_knockout, group_match_ids, label_visibility="visible")
        with col3:
            render_penalty_winner_control(match_id, home_id, away_id, teams, group_match_ids)
    else:
        col1, col2 = st.columns([0.5, 0.5], gap="small")
        with col1:
            render_goal_control("Home", home_key, is_knockout, group_match_ids)
        with col2:
            render_goal_control("Away", away_key, is_knockout, group_match_ids)


def initialize_prediction_session(user_id: str, matches: pd.DataFrame) -> None:
    if (
        st.session_state.get("prediction_session_initialized_for") == user_id
        and prediction_widget_state_complete(matches)
    ):
        return

    remembered = remembered_prediction_session(user_id)
    if remembered is not None:
        restore_prediction_widgets(remembered, matches, user_id)
        return

    draft_id = str(st.session_state.get("draft_id", "")).strip().lower()
    draft = load_draft(draft_id) if valid_draft_id(draft_id) else None
    if draft is not None and str(draft.get("user_id", "")).strip() == user_id:
        restore_prediction_widgets(prediction_frame_from_records(draft.get("predictions", [])), matches, user_id)
        return

    existing = load_user_predictions(user_id)
    restore_prediction_widgets(existing, matches, user_id)


def render_prediction_panel(
    user_id: str,
    user_name_value: str,
    teams: pd.DataFrame,
    matches: pd.DataFrame,
    users: pd.DataFrame,
    knockout_matchups: pd.DataFrame,
    third_place_combinations: pd.DataFrame,
) -> None:
    draft_id = ensure_active_draft()
    st.session_state.setdefault("draft_user_name", user_name_value)
    edited_user_name = st.text_input("Name", key="draft_user_name").strip()
    user_name_value = edited_user_name
    st.session_state["user_name"] = edited_user_name
    st.info("To continue this draft later, bookmark or copy the current page URL before closing the tab.")

    predictions = make_score_df_from_session(matches, user_id)
    state = derive_tournament_state(
        teams,
        matches,
        predictions,
        knockout_matchups,
        third_place_combinations,
        use_cards=False,
        require_confirmed_placements=True,
    )

    st.subheader("Group Stage")
    st.markdown(
        """
        <div style="background: #fff3ed; border-left: 5px solid var(--pool-accent); border-radius: 8px; padding: 0.85rem 1rem; margin: 0.4rem 0 1.35rem;">
            <span style="font-size: 1.08rem; font-weight: 700; text-decoration: underline;">
                Tip: use the up and down arrow keys to increase or decrease a score, and press Tab to move to the next score field.
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    resolved = state["resolved_matches"].set_index("match_id")
    render_prediction_inputs(matches, resolved, teams, state["group_standings"], state["third_place"])

    if st.button("Submit predictions", type="primary"):
        current = make_score_df_from_session(matches, user_id)
        errors = validate_prediction_submission(current, matches)
        if not user_name_value:
            errors.insert(0, "Enter a name.")
        if errors:
            for error in errors:
                st.error(error)
        else:
            current_users = (
                normalize_users(read_sheet_fresh("users", tuple(USERS_COLUMNS)))
                if google_sheets_enabled()
                else users
            )
            saved_user = current_users[current_users["user_id"].eq(user_id)]
            committed_user_id = user_id if not saved_user.empty else next_user_id(current_users)
            if user_name_taken_by_other(current_users, committed_user_id, user_name_value):
                st.error(
                    "Since you started this draft, another participant has submitted predictions using this name. "
                    "Please choose a unique name and submit again. Your predictions are still saved in this draft."
                )
                save_draft(draft_id, user_id, user_name_value, current)
                return
            try:
                commit_user_if_needed(current_users, committed_user_id, user_name_value)
            except ValueError as error:
                st.error(
                    "Since you started this draft, another participant has submitted predictions using this name. "
                    "Please choose a unique name and submit again. Your predictions are still saved in this draft."
                )
                save_draft(draft_id, user_id, user_name_value, current)
                return
            current["user_id"] = committed_user_id
            save_user_predictions(committed_user_id, current)
            st.session_state["user_id"] = committed_user_id
            user_id = committed_user_id
            st.success(f"Predictions saved for {user_name_value}.")

    current_draft = make_score_df_from_session(matches, user_id)
    remember_prediction_session(matches, user_id)
    autosave_draft(draft_id, st.session_state["user_id"], user_name_value, current_draft)


def render_login(users: pd.DataFrame) -> None:
    st.header("Login")
    st.markdown(
        """
        Enter your name to start your World Cup prediction form. After you continue, you can fill in every match score, see the group tables update live, and submit your predictions when everything is complete.
        """
    )
    st.markdown(
        '<div class="submission-deadline">Submission deadline: June 11 at 21:00</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        """
        **Tabs overview**

        | Tab | What you can do |
        |---|---|
        | Home | Enter and submit your predictions |
        | Rules | Check the scoring system and tie-breaking rules |
        | Results | Follow the official results, standings, and knockout bracket |
        """
    )
    if not users.empty:
        st.markdown("**Participants already registered**")
        st.dataframe(
            users[["user_name"]].rename(columns={"user_name": "Name"}),
            hide_index=True,
            width="stretch",
        )
    else:
        st.info("No participants have submitted predictions yet.")

    new_name = st.text_input("Name").strip()
    if st.button("Start predictions", type="primary"):
        if not new_name:
            st.error("Enter a name.")
            return
        if user_exists(users, new_name):
            st.error("This name already exists. Use a unique name.")
            return
        activate_user("PENDING", new_name)
        draft_id = new_draft_id()
        st.session_state["draft_id"] = draft_id
        st.session_state["draft_user_name"] = new_name
        save_draft(draft_id, "PENDING", new_name, pd.DataFrame(columns=PREDICTION_COLUMNS))
        set_draft_query_param(draft_id)
        st.rerun()


def render_home(
    teams: pd.DataFrame,
    matches: pd.DataFrame,
    users: pd.DataFrame,
    knockout_matchups: pd.DataFrame,
    third_place_combinations: pd.DataFrame,
) -> None:
    if "user_id" not in st.session_state:
        render_login(users)
        return

    user_id = st.session_state["user_id"]
    user_name_value = st.session_state["user_name"]
    initialize_prediction_session(user_id, matches)

    st.header(f"Home: {user_name_value}")
    render_prediction_panel(
        user_id,
        user_name_value,
        teams,
        matches,
        users,
        knockout_matchups,
        third_place_combinations,
    )


def render_results(
    teams: pd.DataFrame,
    matches: pd.DataFrame,
    results: pd.DataFrame,
    knockout_matchups: pd.DataFrame,
    third_place_combinations: pd.DataFrame,
) -> None:
    st.header("Results")
    state = derive_tournament_state(
        teams,
        matches,
        results,
        knockout_matchups,
        third_place_combinations,
        use_cards=True,
        require_confirmed_placements=True,
    )
    write_actual_standings(state)
    st.subheader("Group Stage")
    resolved = state["resolved_matches"].set_index("match_id")
    render_readonly_results_inputs(
        matches,
        resolved,
        teams,
        state["group_standings"],
        state["third_place"],
    )


def render_rules() -> None:
    st.header("Rules")
    st.subheader("Scoring")
    st.markdown('<div class="rules-phase-heading">Group Phase</div>', unsafe_allow_html=True)
    st.markdown(
        f"""
        | Category | Points | Added when |
        |---|---:|---|
        | **Per match** |  |  |
        | &nbsp;&nbsp;&nbsp;Correct winner/draw per match | {MATCH_OUTCOME_POINTS} | Match is completed |
        | &nbsp;&nbsp;&nbsp;Correct home-team goals per match | {MATCH_HOME_GOALS_POINTS} | Match is completed |
        | &nbsp;&nbsp;&nbsp;Correct away-team goals per match | {MATCH_AWAY_GOALS_POINTS} | Match is completed |
        | **Final group standings** |  |  |
        | &nbsp;&nbsp;&nbsp;Correct final group placement per team | {GROUP_STANDING_POSITION_POINTS} | All matches in that group are complete |
        """
    )
    st.markdown('<div class="rules-phase-heading rules-phase-heading-spaced">Knockout Phase</div>', unsafe_allow_html=True)
    st.markdown(
        f"""

        | Category | Points | Added when |
        |---|---:|---|
        | **Progression** |  |  |
        | &nbsp;&nbsp;&nbsp;Correct round-of-16 team, per team | {KNOCKOUT_STAGE_POINTS["round_of_16"]} | Team advances from the round of 32 |
        | &nbsp;&nbsp;&nbsp;Correct quarter-finalist, per team | {KNOCKOUT_STAGE_POINTS["quarter_final"]} | Team advances from the round of 16 |
        | &nbsp;&nbsp;&nbsp;Correct semi-finalist, per team | {KNOCKOUT_STAGE_POINTS["semi_final"]} | Team advances from the quarter-finals |
        | &nbsp;&nbsp;&nbsp;Correct finalist, per team | {KNOCKOUT_STAGE_POINTS["final"]} | Team advances from the semi-finals |
        | &nbsp;&nbsp;&nbsp;Correct third-place winner | {THIRD_PLACE_WINNER_POINTS} | Third-place match is completed |
        | &nbsp;&nbsp;&nbsp;Correct World Cup winner | {CHAMPION_POINTS} | Final is completed |
        """
    )
    st.markdown('<div class="rules-section-gap"></div>', unsafe_allow_html=True)
    st.subheader("Tie-breaking criteria for group stage ranking")
    st.markdown('<div class="rules-phase-heading">Group Standings</div>', unsafe_allow_html=True)
    st.markdown(
        """
        | Order | Criterion |
        |---:|---|
        | 1 | Points in all group matches |
        | 2 | Points in matches between the tied teams |
        | 3 | Goal difference in matches between the tied teams |
        | 4 | Goals scored in matches between the tied teams |
        | 5 | Reapply criteria 2-4 to any teams still tied |
        | 6 | Goal difference in all group matches |
        | 7 | Goals scored in all group matches |
        | 8 | Fair-play score in all group matches |
        | 9 | FIFA Men's World Ranking |

        For predictions, fair-play scores are not predicted, so every team has a fair-play score of 0. For actual results, fair-play score is calculated from the official yellow and red card counts.
        """
    )
    st.markdown('<div class="rules-phase-heading rules-phase-heading-spaced">Fair-Play Deductions</div>', unsafe_allow_html=True)
    st.markdown(
        """
        | Card event | Points |
        |---|---:|
        | Yellow card | -1 |
        | Indirect red card / second yellow | -3 |
        | Direct red card | -4 |
        """
    )
    st.markdown('<div class="rules-phase-heading rules-phase-heading-spaced">Third-Place Ranking</div>', unsafe_allow_html=True)
    st.markdown(
        """
        The third-placed team from each group is ranked separately. The eight best third-placed teams advance using this order:

        | Order | Criterion |
        |---:|---|
        | 1 | Points |
        | 2 | Goal difference |
        | 3 | Goals scored |
        | 4 | Fair-play score |
        | 5 | FIFA Men's World Ranking |

        After the eight advancing third-placed groups are known, the [official third-place assignment table](https://en.wikipedia.org/wiki/2026_FIFA_World_Cup_knockout_stage#Combinations_of_matches_in_the_round_of_32) decides which third-placed team is assigned to each round-of-32 slot against the relevant group winner.
        """
    )


@st.cache_data
def load_ai_predictions_from_files(file_signatures: tuple[tuple[str, float], ...]) -> list[dict[str, Any]]:
    participants = []
    for path_text, modified_time in file_signatures:
        path = Path(path_text)
        predictions = load_prediction_csv(path, modified_time)
        user_id = str(predictions["user_id"].iloc[0]).strip() if not predictions.empty else path.stem
        name = path.stem.replace("predictions_", "")
        participants.append(
            {
                "user_id": user_id,
                "user_name": name,
                "is_ai": True,
                "predictions": predictions[PREDICTION_COLUMNS].copy(),
            }
        )
    return participants


def load_ai_predictions() -> list[dict[str, Any]]:
    if not AI_PREDICTIONS_DIR.exists():
        return []
    file_signatures = tuple(
        (str(path), path.stat().st_mtime)
        for path in sorted(AI_PREDICTIONS_DIR.glob("predictions_*.csv"))
    )
    return load_ai_predictions_from_files(file_signatures)


def load_human_predictions(users: pd.DataFrame) -> list[dict[str, Any]]:
    participants = []
    all_predictions = (
        read_sheet(PREDICTIONS_SHEET, tuple(PREDICTION_COLUMNS))
        if google_sheets_enabled()
        else pd.DataFrame(columns=PREDICTION_COLUMNS)
    )
    for _, user in users.iterrows():
        user_id = str(user["user_id"])
        if google_sheets_enabled():
            predictions = all_predictions[all_predictions["user_id"].astype(str).eq(user_id)][PREDICTION_COLUMNS].copy()
        else:
            predictions = load_user_predictions(user_id)
        participants.append(
            {
                "user_id": user_id,
                "user_name": user["user_name"],
                "is_ai": False,
                "predictions": predictions,
            }
        )
    return participants


def leaderboard_participants(users: pd.DataFrame, include_ai: bool) -> list[dict[str, Any]]:
    participants = load_human_predictions(users)
    if include_ai:
        participants.extend(load_ai_predictions())
    return participants


def prediction_states_for_participants(
    participants: list[dict[str, Any]],
    teams: pd.DataFrame,
    matches: pd.DataFrame,
    knockout_matchups: pd.DataFrame,
    third_place_combinations: pd.DataFrame,
) -> dict[str, dict[str, Any]]:
    return {
        str(participant["user_id"]): derive_tournament_state(
            teams,
            matches,
            participant["predictions"],
            knockout_matchups,
            third_place_combinations,
            use_cards=False,
        )
        for participant in participants
    }


LEADERBOARD_SNAPSHOT_COLUMNS = [
    "rank",
    "user_id",
    "user_name",
    "is_ai",
    "total_points",
    "match_score_points",
    "group_standings_points",
    "knockout_progression_points",
    "correct_winners",
    "exact_home_goals",
    "exact_away_goals",
    "exact_goal_components",
    "exact_scores",
]

LEADERBOARD_CACHE_COLUMNS = ["cache_key", "checkpoint_id", "rank_change", *LEADERBOARD_SNAPSHOT_COLUMNS]


def add_rank(table: pd.DataFrame, points_column: str = "total_points") -> pd.DataFrame:
    if table.empty:
        return table
    table = table.sort_values(by=[points_column, "user_name"], ascending=[False, True]).reset_index(drop=True)
    ranks = []
    previous_points = None
    previous_rank = 0
    for index, row in table.iterrows():
        points = row[points_column]
        rank = previous_rank if previous_points == points else index + 1
        ranks.append(rank)
        previous_points = points
        previous_rank = rank
    table.insert(0, "rank", ranks)
    return table


def leaderboard_snapshot(
    participants: list[dict[str, Any]],
    results: pd.DataFrame,
    teams: pd.DataFrame,
    matches: pd.DataFrame,
    knockout_matchups: pd.DataFrame,
    third_place_combinations: pd.DataFrame,
    awarded_group_standings: set[str] | None = None,
    precomputed_prediction_states: dict[str, dict[str, Any]] | None = None,
    precomputed_prediction_scores: dict[str, dict[str, dict[str, Any]]] | None = None,
    precomputed_prediction_stage_entrants: dict[str, dict[str, set[str]]] | None = None,
    precomputed_match_records: list[dict[str, Any]] | None = None,
    precomputed_actual_state: dict[str, Any] | None = None,
    precomputed_completed_groups: set[str] | None = None,
    precomputed_actual_scores: dict[str, dict[str, Any]] | None = None,
    precomputed_actual_stage_entrants: dict[str, set[str]] | None = None,
) -> pd.DataFrame:
    rows = []
    actual_state = (
        precomputed_actual_state
        if precomputed_actual_state is not None
        else derive_tournament_state(
            teams,
            matches,
            results,
            knockout_matchups,
            third_place_combinations,
            use_cards=True,
            require_confirmed_placements=True,
        )
    )
    completed_groups = (
        precomputed_completed_groups
        if precomputed_completed_groups is not None
        else completed_groups_lookup(results, matches, teams)
    )
    actual_scores = precomputed_actual_scores if precomputed_actual_scores is not None else score_lookup(results)
    match_records = precomputed_match_records if precomputed_match_records is not None else matches.to_dict("records")
    actual_stage_entrants = (
        precomputed_actual_stage_entrants
        if precomputed_actual_stage_entrants is not None
        else stage_entrants_by_stage(actual_state["resolved_matches"])
    )
    prediction_stage_entrants_by_user = dict(precomputed_prediction_stage_entrants or {})
    if precomputed_prediction_stage_entrants is None:
        prediction_stage_entrants_by_user = {
            str(user_id): stage_entrants_by_stage(state["resolved_matches"])
            for user_id, state in (precomputed_prediction_states or {}).items()
        }
    prediction_scores_by_user = dict(precomputed_prediction_scores or {})
    if precomputed_prediction_scores is None:
        prediction_scores_by_user = {
            str(participant["user_id"]): score_lookup(participant["predictions"])
            for participant in participants
        }
    for participant in participants:
        user_id = str(participant["user_id"])
        prediction_state = (precomputed_prediction_states or {}).get(user_id)
        if prediction_state is None:
            prediction_state = derive_tournament_state(
                teams,
                matches,
                participant["predictions"],
                knockout_matchups,
                third_place_combinations,
                use_cards=False,
            )
            prediction_stage_entrants_by_user[user_id] = stage_entrants_by_stage(
                prediction_state["resolved_matches"]
            )
        breakdown = calculate_user_score_breakdown_from_states(
            participant["predictions"],
            prediction_state,
            results,
            actual_state,
            teams,
            matches,
            awarded_group_standings=awarded_group_standings,
            completed_groups=completed_groups,
            prediction_stage_entrants=prediction_stage_entrants_by_user.get(user_id),
            actual_stage_entrants=actual_stage_entrants,
            predicted_scores=prediction_scores_by_user.get(user_id),
            actual_scores=actual_scores,
            match_records=match_records,
        )
        rows.append(
            {
                "user_id": participant["user_id"],
                "user_name": participant["user_name"],
                "is_ai": participant["is_ai"],
                **breakdown,
            }
        )
    if not rows:
        return pd.DataFrame(columns=LEADERBOARD_SNAPSHOT_COLUMNS)
    return add_rank(pd.DataFrame(rows), "total_points")


def leaderboard_cache_version() -> str:
    return str(load_config().get("leaderboard_cache_version", "1")).strip() or "1"


def leaderboard_cache_key(
    users: pd.DataFrame,
    results: pd.DataFrame,
    teams: pd.DataFrame,
    matches: pd.DataFrame,
    knockout_matchups: pd.DataFrame,
    third_place_combinations: pd.DataFrame,
    checkpoint: dict[str, Any],
) -> str:
    user_signature = tuple(
        (str(row["user_id"]), str(row["user_name"]))
        for _, row in users[["user_id", "user_name"]].iterrows()
    )
    key_parts = {
        "schema": "leaderboard-checkpoint-v1",
        "version": leaderboard_cache_version(),
        "checkpoint_id": str(checkpoint["checkpoint_id"]),
        "through_match_id": str(checkpoint["through_match_id"]),
        "awarded_group_standings": tuple(sorted(checkpoint["awarded_group_standings"])),
        "users": user_signature,
        "results": dataframe_cache_key(results),
        "teams": dataframe_cache_key(teams),
        "matches": dataframe_cache_key(matches),
        "knockout_matchups": dataframe_cache_key(knockout_matchups),
        "third_place_combinations": dataframe_cache_key(third_place_combinations),
    }
    encoded = json.dumps(key_parts, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def normalize_leaderboard_cache_rows(rows: pd.DataFrame) -> pd.DataFrame:
    for column in LEADERBOARD_CACHE_COLUMNS:
        if column not in rows.columns:
            rows[column] = ""
    rows = rows[LEADERBOARD_CACHE_COLUMNS].copy()
    numeric_columns = [
        "rank",
        "total_points",
        "match_score_points",
        "group_standings_points",
        "knockout_progression_points",
        "correct_winners",
        "exact_home_goals",
        "exact_away_goals",
        "exact_goal_components",
        "exact_scores",
    ]
    for column in numeric_columns:
        rows[column] = pd.to_numeric(rows[column], errors="coerce").fillna(0).astype(int)
    rows["is_ai"] = rows["is_ai"].map(
        lambda value: str(value).strip().lower() in {"1", "true", "yes"}
    )
    rows["rank_change"] = rows["rank_change"].replace("", "-")
    return rows


def read_leaderboard_cache(cache_key: str) -> pd.DataFrame | None:
    if not google_sheets_enabled():
        return None
    cache = read_sheet_fresh(LEADERBOARD_CACHE_SHEET, tuple(LEADERBOARD_CACHE_COLUMNS))
    if cache.empty or "cache_key" not in cache.columns:
        return None
    matching = cache[cache["cache_key"].astype(str).eq(str(cache_key))].copy()
    if matching.empty:
        return None
    rows = normalize_leaderboard_cache_rows(matching)
    return rows[["rank_change", *LEADERBOARD_SNAPSHOT_COLUMNS]].reset_index(drop=True)


def write_leaderboard_cache(cache_key: str, checkpoint_id: str, snapshot: pd.DataFrame) -> str:
    if not google_sheets_enabled():
        return "disabled"
    if snapshot.empty:
        return "empty"
    try:
        existing = read_sheet_fresh(LEADERBOARD_CACHE_SHEET, tuple(LEADERBOARD_CACHE_COLUMNS))
        if not existing.empty and "cache_key" in existing.columns:
            existing = existing[~existing["cache_key"].astype(str).eq(str(cache_key))]
        cache_rows = snapshot.copy().fillna("")
        for column in ["rank_change", *LEADERBOARD_SNAPSHOT_COLUMNS]:
            if column not in cache_rows.columns:
                cache_rows[column] = "-" if column == "rank_change" else ""
        cache_rows = cache_rows[["rank_change", *LEADERBOARD_SNAPSHOT_COLUMNS]]
        cache_rows.insert(0, "checkpoint_id", checkpoint_id)
        cache_rows.insert(0, "cache_key", cache_key)
        updated = pd.concat([existing, cache_rows], ignore_index=True)
        write_sheet(LEADERBOARD_CACHE_SHEET, updated, LEADERBOARD_CACHE_COLUMNS)
        confirmed = read_sheet_fresh(LEADERBOARD_CACHE_SHEET, tuple(LEADERBOARD_CACHE_COLUMNS))
    except GoogleSheetsRateLimitError:
        raise
    except Exception as error:
        return f"error:{type(error).__name__}: {error}"
    if (
        not confirmed.empty
        and "cache_key" in confirmed.columns
        and confirmed["cache_key"].astype(str).eq(str(cache_key)).any()
    ):
        return f"verified:{len(cache_rows)}"
    return "not_visible"


def compute_checkpoint_snapshot(
    participants: list[dict[str, Any]],
    results: pd.DataFrame,
    matches: pd.DataFrame,
    checkpoint: dict[str, Any],
    previous_checkpoint: dict[str, Any] | None,
    teams: pd.DataFrame,
    knockout_matchups: pd.DataFrame,
    third_place_combinations: pd.DataFrame,
) -> pd.DataFrame:
    prediction_states = prediction_states_for_participants(
        participants, teams, matches, knockout_matchups, third_place_combinations
    )
    prediction_scores = {
        str(participant["user_id"]): score_lookup(participant["predictions"])
        for participant in participants
    }
    prediction_stage_entrants = {
        user_id: stage_entrants_by_stage(state["resolved_matches"])
        for user_id, state in prediction_states.items()
    }
    match_records = matches.to_dict("records")
    current_results = results_through_match(results, matches, str(checkpoint["through_match_id"]))
    current = leaderboard_snapshot(
        participants,
        current_results,
        teams,
        matches,
        knockout_matchups,
        third_place_combinations,
        awarded_group_standings=set(checkpoint["awarded_group_standings"]),
        precomputed_prediction_states=prediction_states,
        precomputed_prediction_scores=prediction_scores,
        precomputed_prediction_stage_entrants=prediction_stage_entrants,
        precomputed_match_records=match_records,
    )
    previous_ranks = {}
    if previous_checkpoint is not None:
        previous_results = results_through_match(results, matches, str(previous_checkpoint["through_match_id"]))
        previous = leaderboard_snapshot(
            participants,
            previous_results,
            teams,
            matches,
            knockout_matchups,
            third_place_combinations,
            awarded_group_standings=set(previous_checkpoint["awarded_group_standings"]),
            precomputed_prediction_states=prediction_states,
            precomputed_prediction_scores=prediction_scores,
            precomputed_prediction_stage_entrants=prediction_stage_entrants,
            precomputed_match_records=match_records,
        )
        previous_ranks = dict(zip(previous["user_id"], previous["rank"]))
    current["rank_change"] = current.apply(
        lambda row: format_change(to_int(row["rank"]), previous_ranks.get(row["user_id"])),
        axis=1,
    )
    return current


def completed_match_options(results: pd.DataFrame, matches: pd.DataFrame, teams: pd.DataFrame) -> list[tuple[str, str]]:
    result_rows = score_lookup(results)
    options = []
    for _, match in matches.iterrows():
        match_id = str(match["match_id"])
        score = completed_score(result_rows.get(match_id))
        if score is None:
            continue
        home_name = team_name(match.get("home_team", ""), teams)
        away_name = team_name(match.get("away_team", ""), teams)
        options.append((match_id, f"{match_id}: {home_name} {score[0]}-{score[1]} {away_name}"))
    return options


def checkpoint_match_label(
    match: pd.Series,
    score: tuple[int, int],
    teams: pd.DataFrame,
    resolved_rows: dict[str, dict[str, Any]],
    matchup_rows: dict[str, dict[str, Any]],
) -> str:
    match_id = str(match["match_id"])
    stage = str(match.get("stage", ""))
    resolved = resolved_rows.get(match_id, {})
    matchup = matchup_rows.get(match_id, {})

    home_id = str(resolved.get("home_team", "") or match.get("home_team", "")).strip()
    away_id = str(resolved.get("away_team", "") or match.get("away_team", "")).strip()
    home_fallback = str(matchup.get("home_team", "")).strip() or "TBD"
    away_fallback = str(matchup.get("away_team", "")).strip() or "TBD"
    home_name = team_name(home_id, teams) if home_id else home_fallback
    away_name = team_name(away_id, teams) if away_id else away_fallback

    return f"{stage_label(stage)}: {home_name} {score[0]}-{score[1]} {away_name}"


def leaderboard_checkpoint_options(
    results: pd.DataFrame,
    matches: pd.DataFrame,
    teams: pd.DataFrame,
    knockout_matchups: pd.DataFrame,
    third_place_combinations: pd.DataFrame,
) -> list[dict[str, Any]]:
    result_rows = score_lookup(results)
    final_match_ids = group_final_match_ids(matches, teams)
    final_match_groups = {match_id: group for group, match_id in final_match_ids.items()}
    state = derive_tournament_state(
        teams,
        matches,
        results,
        knockout_matchups,
        third_place_combinations,
        use_cards=True,
        require_confirmed_placements=True,
    )
    resolved_rows = {
        str(row["match_id"]): row.to_dict()
        for _, row in state["resolved_matches"].iterrows()
    }
    matchup_rows = {
        str(row["match_id"]): row.to_dict()
        for _, row in knockout_matchups.iterrows()
    }
    awarded_groups: set[str] = set()
    options: list[dict[str, Any]] = []

    for _, match in matches.iterrows():
        match_id = str(match["match_id"])
        score = completed_score(result_rows.get(match_id))
        if score is None:
            continue

        options.append(
            {
                "checkpoint_id": f"match:{match_id}",
                "through_match_id": match_id,
                "label": checkpoint_match_label(match, score, teams, resolved_rows, matchup_rows),
                "awarded_group_standings": set(awarded_groups),
            }
        )

        group = final_match_groups.get(match_id, "")
        if group and group_is_complete(group, matches, results, teams):
            awarded_groups.add(group)
            options.append(
                {
                    "checkpoint_id": f"group:{group}",
                    "through_match_id": match_id,
                    "label": f"Final Standing Group {group}",
                    "awarded_group_standings": set(awarded_groups),
                }
            )

    return options


def format_change(current_rank: int, previous_rank: int | None) -> str:
    if previous_rank is None:
        return "-"
    delta = previous_rank - current_rank
    if delta > 0:
        return f"+{delta}"
    if delta < 0:
        return str(delta)
    return "0"


def snapshot_with_rank_change(
    participants: list[dict[str, Any]],
    results: pd.DataFrame,
    matches: pd.DataFrame,
    selected_match_id: str,
    teams: pd.DataFrame,
    knockout_matchups: pd.DataFrame,
    third_place_combinations: pd.DataFrame,
) -> pd.DataFrame:
    prediction_states = prediction_states_for_participants(
        participants, teams, matches, knockout_matchups, third_place_combinations
    )
    prediction_scores = {
        str(participant["user_id"]): score_lookup(participant["predictions"])
        for participant in participants
    }
    prediction_stage_entrants = {
        user_id: stage_entrants_by_stage(state["resolved_matches"])
        for user_id, state in prediction_states.items()
    }
    match_records = matches.to_dict("records")
    current_results = results_through_match(results, matches, selected_match_id)
    current = leaderboard_snapshot(
        participants,
        current_results,
        teams,
        matches,
        knockout_matchups,
        third_place_combinations,
        precomputed_prediction_states=prediction_states,
        precomputed_prediction_scores=prediction_scores,
        precomputed_prediction_stage_entrants=prediction_stage_entrants,
        precomputed_match_records=match_records,
    )
    match_ids = completed_match_ids(results, matches)
    selected_index = match_ids.index(selected_match_id) if selected_match_id in match_ids else -1
    previous_ranks = {}
    if selected_index > 0:
        previous_results = results_through_match(results, matches, match_ids[selected_index - 1])
        previous = leaderboard_snapshot(
            participants,
            previous_results,
            teams,
            matches,
            knockout_matchups,
            third_place_combinations,
            precomputed_prediction_states=prediction_states,
            precomputed_prediction_scores=prediction_scores,
            precomputed_prediction_stage_entrants=prediction_stage_entrants,
            precomputed_match_records=match_records,
        )
        previous_ranks = dict(zip(previous["user_id"], previous["rank"]))
    current["rank_change"] = current.apply(
        lambda row: format_change(to_int(row["rank"]), previous_ranks.get(row["user_id"])), axis=1
    )
    return current


def snapshot_with_checkpoint_rank_change(
    participants: list[dict[str, Any]],
    results: pd.DataFrame,
    matches: pd.DataFrame,
    selected_checkpoint_id: str,
    checkpoints: list[dict[str, Any]],
    teams: pd.DataFrame,
    knockout_matchups: pd.DataFrame,
    third_place_combinations: pd.DataFrame,
) -> pd.DataFrame:
    prediction_states = prediction_states_for_participants(
        participants, teams, matches, knockout_matchups, third_place_combinations
    )
    prediction_scores = {
        str(participant["user_id"]): score_lookup(participant["predictions"])
        for participant in participants
    }
    prediction_stage_entrants = {
        user_id: stage_entrants_by_stage(state["resolved_matches"])
        for user_id, state in prediction_states.items()
    }
    match_records = matches.to_dict("records")
    checkpoint_lookup = {checkpoint["checkpoint_id"]: checkpoint for checkpoint in checkpoints}
    current_checkpoint = checkpoint_lookup[selected_checkpoint_id]
    current_results = results_through_match(
        results, matches, str(current_checkpoint["through_match_id"])
    )
    current = leaderboard_snapshot(
        participants,
        current_results,
        teams,
        matches,
        knockout_matchups,
        third_place_combinations,
        awarded_group_standings=set(current_checkpoint["awarded_group_standings"]),
        precomputed_prediction_states=prediction_states,
        precomputed_prediction_scores=prediction_scores,
        precomputed_prediction_stage_entrants=prediction_stage_entrants,
        precomputed_match_records=match_records,
    )
    selected_index = next(
        (
            index
            for index, checkpoint in enumerate(checkpoints)
            if checkpoint["checkpoint_id"] == selected_checkpoint_id
        ),
        -1,
    )
    previous_ranks = {}
    if selected_index > 0:
        previous_checkpoint = checkpoints[selected_index - 1]
        previous_results = results_through_match(
            results, matches, str(previous_checkpoint["through_match_id"])
        )
        previous = leaderboard_snapshot(
            participants,
            previous_results,
            teams,
            matches,
            knockout_matchups,
            third_place_combinations,
            awarded_group_standings=set(previous_checkpoint["awarded_group_standings"]),
            precomputed_prediction_states=prediction_states,
            precomputed_prediction_scores=prediction_scores,
            precomputed_prediction_stage_entrants=prediction_stage_entrants,
            precomputed_match_records=match_records,
        )
        previous_ranks = dict(zip(previous["user_id"], previous["rank"]))
    current["rank_change"] = current.apply(
        lambda row: format_change(to_int(row["rank"]), previous_ranks.get(row["user_id"])), axis=1
    )
    return current


def table_display_value(value: Any) -> str:
    if pd.isna(value):
        return ""
    return html.escape(str(value))


def is_left_aligned_column(
    column: str,
    left_columns: set[str] | None = None,
    centered_columns: set[str] | None = None,
) -> bool:
    column_lower = column.lower()
    left_lookup = {item.lower() for item in (left_columns or set())}
    center_lookup = {item.lower() for item in (centered_columns or set())}
    if column_lower in center_lookup:
        return False
    return column_lower in {"user name", "user_name", "name"} or column_lower in left_lookup


def render_centered_dataframe(
    table: pd.DataFrame,
    left_columns: set[str] | None = None,
    centered_columns: set[str] | None = None,
    bold_columns: set[str] | None = None,
    row_classes: list[str] | None = None,
) -> None:
    if table.empty:
        st.markdown(
            '<table class="leaderboard-table"><tbody><tr><td>No data available.</td></tr></tbody></table>',
            unsafe_allow_html=True,
        )
        return

    bold_lookup = {column.lower() for column in (bold_columns or set())}
    headers = []
    for column in table.columns:
        classes = []
        if is_left_aligned_column(str(column), left_columns, centered_columns):
            classes.append("left")
        if str(column).lower() in bold_lookup:
            classes.append("bold")
        class_attr = f' class="{" ".join(classes)}"' if classes else ""
        headers.append(f"<th{class_attr}>{html.escape(str(column))}</th>")

    body_rows = []
    for index, (_, row) in enumerate(table.iterrows()):
        row_class = row_classes[index] if row_classes and index < len(row_classes) else ""
        class_attr = f' class="{html.escape(row_class)}"' if row_class else ""
        cells = []
        for column in table.columns:
            classes = []
            if is_left_aligned_column(str(column), left_columns, centered_columns):
                classes.append("left")
            if str(column).lower() in bold_lookup:
                classes.append("bold")
            cell_class = f' class="{" ".join(classes)}"' if classes else ""
            cells.append(f"<td{cell_class}>{table_display_value(row[column])}</td>")
        body_rows.append(f"<tr{class_attr}>{''.join(cells)}</tr>")

    st.markdown(
        f'<table class="leaderboard-table"><thead><tr>{"".join(headers)}</tr></thead><tbody>{"".join(body_rows)}</tbody></table>',
        unsafe_allow_html=True,
    )


def display_leaderboard_table(snapshot: pd.DataFrame, include_change: bool = True, highlight_ai: bool = False) -> None:
    if snapshot.empty:
        st.info("No submitted predictions yet.")
        return
    columns = {
        "rank": "Rank",
        "rank_change": "Change",
        "user_name": "User name",
        "total_points": "Total points",
        "match_score_points": "Points from match scores",
        "group_standings_points": "Points from group standings",
        "knockout_progression_points": "Points from knockout-phase progression",
    }
    selected_columns = [key for key in columns if include_change or key != "rank_change"]
    display = snapshot[selected_columns].rename(columns=columns)
    row_classes = ["ai-row" if bool(value) else "" for value in snapshot.get("is_ai", [])] if highlight_ai else None
    render_centered_dataframe(display, bold_columns={"Total points"}, row_classes=row_classes)


def prediction_score_text(row: dict[str, Any] | pd.Series | None) -> str:
    score = completed_score(row)
    if score is None:
        return "-"
    return f"{score[0]}-{score[1]}"


def prediction_winner_label(
    match: pd.Series,
    prediction: dict[str, Any] | pd.Series | None,
    resolved_row: dict[str, Any] | pd.Series | None,
    teams: pd.DataFrame,
) -> str:
    score = completed_score(prediction)
    if score is None:
        return "No prediction"
    home_goals, away_goals = score
    if str(match["stage"]) == GROUP_STAGE:
        if home_goals == away_goals:
            return "Draw"
        return team_name(match["home_team"] if home_goals > away_goals else match["away_team"], teams)
    winner = str(resolved_row.get("winner", "")).strip() if resolved_row is not None else ""
    return team_name(winner, teams) if winner else "No winner"


def predicted_winner_bucket(
    match: pd.Series,
    prediction: dict[str, Any] | pd.Series | None,
    resolved_row: dict[str, Any] | pd.Series | None,
    teams: pd.DataFrame,
) -> str:
    if str(match["stage"]) in KNOCKOUT_STAGES:
        winner_side = winner_side_from_resolved(resolved_row)
        if winner_side == "home":
            return "Home"
        if winner_side == "away":
            return "Away"
        return "No winner"
    return prediction_winner_label(match, prediction, resolved_row, teams)


def matchup_text_from_resolved(row: dict[str, Any] | pd.Series | None, teams: pd.DataFrame) -> str:
    if row is None:
        return "TBD vs TBD"
    home = team_name(str(row.get("home_team", "")), teams)
    away = team_name(str(row.get("away_team", "")), teams)
    return f"{home} vs {away}"


def render_pie_chart(table: pd.DataFrame, names_column: str, values_column: str) -> None:
    display_table = table.copy()
    total = display_table[values_column].sum()
    display_table["Percentage"] = display_table[values_column].apply(
        lambda value: round(100 * value / total, 1) if total else 0
    )
    display_table["Label"] = display_table.apply(
        lambda row: f"{row[names_column]} ({row['Percentage']:.1f}%)",
        axis=1,
    )
    chart = (
        alt.Chart(display_table)
        .mark_arc(innerRadius=45)
        .encode(
            theta=alt.Theta(f"{values_column}:Q"),
            color=alt.Color("Label:N", legend=alt.Legend(title=None)),
            tooltip=[names_column, values_column, alt.Tooltip("Percentage:Q", format=".1f", title="Percentage")],
        )
        .properties(height=320)
        .configure_view(strokeWidth=0)
        .configure_axis(labelColor=DEFAULT_THEME["primary"], titleColor=DEFAULT_THEME["primary"], tickColor=DEFAULT_THEME["primary"], domainColor=DEFAULT_THEME["primary"])
        .configure_legend(labelColor=DEFAULT_THEME["primary"], titleColor=DEFAULT_THEME["primary"])
        .configure(background="transparent")
    )
    with st.container():
        st.markdown('<div class="figure-pad">', unsafe_allow_html=True)
        st.altair_chart(chart, width="stretch")
        st.markdown("</div>", unsafe_allow_html=True)


def render_padded_bar_chart(table: pd.DataFrame, x: str, y: str) -> None:
    display_table = table.copy()
    total = display_table[y].sum()
    display_table["Percentage"] = display_table[y].apply(lambda value: round(100 * value / total, 1) if total else 0)
    display_table["Percentage label"] = display_table["Percentage"].apply(lambda value: f"{value:.1f}%")
    y_max = float(display_table[y].max()) if not display_table.empty else 0
    y_domain_max = max(1.0, y_max * 1.18)
    chart = (
        alt.Chart(display_table)
        .mark_bar(color="#0b71c9")
        .encode(
            x=alt.X(f"{x}:N", title=x, sort=None),
            y=alt.Y(f"{y}:Q", title=y, scale=alt.Scale(domain=[0, y_domain_max])),
            tooltip=[x, y, alt.Tooltip("Percentage:Q", format=".1f", title="Percentage")],
        )
    )
    labels = (
        alt.Chart(display_table)
        .mark_text(
            align="center",
            baseline="bottom",
            dy=-6,
            color=DEFAULT_THEME["primary"],
            fontWeight="bold",
        )
        .encode(
            x=alt.X(f"{x}:N", sort=None),
            y=alt.Y(f"{y}:Q", scale=alt.Scale(domain=[0, y_domain_max])),
            text="Percentage label:N",
        )
    )
    chart = (
        (chart + labels)
        .properties(height=330)
        .configure_view(strokeWidth=0)
        .configure_axis(labelColor=DEFAULT_THEME["primary"], titleColor=DEFAULT_THEME["primary"], tickColor=DEFAULT_THEME["primary"], domainColor=DEFAULT_THEME["primary"])
        .configure_legend(labelColor=DEFAULT_THEME["primary"], titleColor=DEFAULT_THEME["primary"])
        .configure(background="transparent")
    )
    with st.container():
        st.markdown('<div class="figure-pad">', unsafe_allow_html=True)
        st.altair_chart(chart, width="stretch")
        st.markdown("</div>", unsafe_allow_html=True)


def render_chart_with_scrollable_legend(
    chart: alt.Chart,
    labels: list[str],
    color_domain: list[str],
    participant_types: dict[str, str],
) -> None:
    color_by_label = {
        label: TIMELINE_COLORS[index % len(TIMELINE_COLORS)]
        for index, label in enumerate(color_domain)
    }
    legend_items = []
    for label in labels:
        color = color_by_label[label]
        if participant_types.get(label) == "AI":
            marker = (
                '<span style="width:0;height:0;border-left:0.42rem solid transparent;'
                'border-right:0.42rem solid transparent;'
                f'border-bottom:0.75rem solid {color};flex:0 0 auto;"></span>'
            )
        else:
            marker = (
                f'<span style="width:0.75rem;height:0.75rem;border-radius:50%;'
                f'background:{color};flex:0 0 auto;"></span>'
            )
        legend_items.append(
            '<div style="display:flex;align-items:center;gap:0.45rem;margin:0.35rem 0;">'
            f"{marker}<span>{html.escape(label)}</span></div>"
        )
    legend_html = "".join(legend_items)
    chart_col, legend_col = st.columns([0.86, 0.14], gap="small")
    with chart_col:
        st.altair_chart(chart, width="stretch")
    with legend_col:
        st.markdown(
            (
                '<div style="height:330px;display:flex;align-items:center;">'
                '<div style="width:100%;max-height:240px;overflow-y:auto;'
                'padding-right:0.35rem;color:#5b6d7d;">'
                f"{legend_html}</div></div>"
            ),
            unsafe_allow_html=True,
        )


def render_padded_line_chart(
    table: pd.DataFrame,
    x: str,
    y: str,
    color: str,
    reverse_y: bool = False,
    y_values: list[int] | None = None,
    color_domain: list[str] | None = None,
) -> None:
    labels = sorted(table[color].dropna().astype(str).unique(), key=str.lower)
    participant_types = (
        table[[color, "Participant type"]]
        .drop_duplicates(subset=[color])
        .set_index(color)["Participant type"]
        .astype(str)
        .to_dict()
    )
    color_domain = color_domain or labels
    color_range = [
        TIMELINE_COLORS[index % len(TIMELINE_COLORS)]
        for index in range(len(color_domain))
    ]
    if reverse_y and y_values:
        y_scale = alt.Scale(domain=[max(y_values), min(y_values)], nice=False)
    elif reverse_y:
        y_scale = alt.Scale(reverse=True, nice=False)
    else:
        y_scale = alt.Scale()
    y_axis = alt.Axis(values=y_values, format="d") if y_values else alt.Axis()
    base = alt.Chart(table).encode(
        x=alt.X(f"{x}:N", title=x, sort=None),
        y=alt.Y(f"{y}:Q", title=y, scale=y_scale, axis=y_axis),
        color=alt.Color(
            f"{color}:N",
            legend=None,
            scale=alt.Scale(domain=color_domain, range=color_range),
        ),
        tooltip=[x, y, color, "Participant type"],
    )
    chart = (
        (base.mark_line() + base.mark_point(size=65).encode(
            shape=alt.Shape(
                "Participant type:N",
                legend=None,
                scale=alt.Scale(domain=["Human", "AI"], range=["circle", "triangle-up"]),
            )
        ))
        .properties(height=330)
        .configure_view(strokeWidth=0)
        .configure_axis(labelColor=DEFAULT_THEME["primary"], titleColor=DEFAULT_THEME["primary"], tickColor=DEFAULT_THEME["primary"], domainColor=DEFAULT_THEME["primary"])
        .configure_legend(labelColor=DEFAULT_THEME["primary"], titleColor=DEFAULT_THEME["primary"])
        .configure(background="transparent")
    )
    with st.container():
        st.markdown('<div class="figure-pad">', unsafe_allow_html=True)
        render_chart_with_scrollable_legend(chart, labels, color_domain, participant_types)
        st.markdown("</div>", unsafe_allow_html=True)


def render_default_leaderboard(
    users: pd.DataFrame,
    results: pd.DataFrame,
    teams: pd.DataFrame,
    matches: pd.DataFrame,
    knockout_matchups: pd.DataFrame,
    third_place_combinations: pd.DataFrame,
) -> None:
    overview_rendered = render_leaderboard_endgame_overview(
        users,
        results,
        teams,
        matches,
        knockout_matchups,
        third_place_combinations,
    )
    if overview_rendered:
        st.markdown('<div class="section-gap"></div>', unsafe_allow_html=True)
        st.subheader("Leaderboard")

    checkpoints = leaderboard_checkpoint_options(
        results,
        matches,
        teams,
        knockout_matchups,
        third_place_combinations,
    )
    if not checkpoints:
        st.info("The leaderboard will appear once the first match has been played.")
        return
    labels = [checkpoint["label"] for checkpoint in checkpoints]
    selected_label = st.selectbox(
        "Show leaderboard after",
        labels,
        index=len(checkpoints) - 1,
        key="leaderboard_after_match",
    )
    selected_checkpoint_id = {
        checkpoint["label"]: checkpoint["checkpoint_id"] for checkpoint in checkpoints
    }[selected_label]
    selected_index = next(
        index
        for index, checkpoint in enumerate(checkpoints)
        if checkpoint["checkpoint_id"] == selected_checkpoint_id
    )
    checkpoint = checkpoints[selected_index]
    cache_key = leaderboard_cache_key(
        users,
        results,
        teams,
        matches,
        knockout_matchups,
        third_place_combinations,
        checkpoint,
    )
    snapshot = read_leaderboard_cache(cache_key)
    cache_status = "hit" if snapshot is not None else "miss"
    if snapshot is None:
        participants = leaderboard_participants(users, include_ai=False)
        previous_checkpoint = checkpoints[selected_index - 1] if selected_index > 0 else None
        with st.spinner("Calculating leaderboard..."):
            snapshot = compute_checkpoint_snapshot(
                participants,
                results,
                matches,
                checkpoint,
                previous_checkpoint,
                teams,
                knockout_matchups,
                third_place_combinations,
            )
        cache_status = write_leaderboard_cache(cache_key, str(checkpoint["checkpoint_id"]), snapshot)
    display_leaderboard_table(snapshot, include_change=True)
    if cache_status == "disabled":
        st.warning(
            "Leaderboard cache was not written because `GOOGLE_SHEETS_BACKEND` is not enabled "
            "for this running app."
        )
    elif cache_status == "empty":
        st.info("Leaderboard cache was not written because the computed snapshot was empty.")
    elif cache_status == "not_visible":
        cache_target = google_sheets_target_label()
        st.error(
            f"The cache write call completed, but `{LEADERBOARD_CACHE_SHEET}` in {cache_target} "
            "did not contain the written cache key on immediate read-back."
        )
    elif cache_status.startswith("error:"):
        cache_target = google_sheets_target_label()
        st.error(f"Leaderboard cache write failed for {cache_target}: {cache_status.removeprefix('error:')}")


def render_additional_rankings(
    users: pd.DataFrame,
    results: pd.DataFrame,
    teams: pd.DataFrame,
    matches: pd.DataFrame,
    knockout_matchups: pd.DataFrame,
    third_place_combinations: pd.DataFrame,
) -> None:
    participants = leaderboard_participants(users, include_ai=False)
    scoped_results = results_for_stage(results, matches, GROUP_STAGE)
    snapshot = leaderboard_snapshot(
        participants,
        scoped_results,
        teams,
        matches,
        knockout_matchups,
        third_place_combinations,
    )
    if snapshot.empty:
        st.info("Additional rankings will appear once participants have submitted predictions.")
        return

    st.subheader("Most Correct Outcomes")
    winners = add_rank(
        snapshot[["user_name", "correct_winners"]].rename(
            columns={"correct_winners": "Correct outcomes"}
        ),
        "Correct outcomes",
    )
    render_centered_dataframe(
        winners.rename(columns={"rank": "Rank", "user_name": "User name"}),
        bold_columns={"Correct outcomes"},
    )

    st.subheader("Most Exact Score Components")
    components = add_rank(
        snapshot[["user_name", "exact_home_goals", "exact_away_goals", "exact_goal_components"]].rename(
            columns={
                "exact_home_goals": "Exact home goals",
                "exact_away_goals": "Exact away goals",
                "exact_goal_components": "Total exact goal components",
            }
        ),
        "Total exact goal components",
    )
    render_centered_dataframe(
        components.rename(columns={"rank": "Rank", "user_name": "User name"}),
        bold_columns={"Total exact goal components"},
    )

    st.subheader("Most Exact Scores")
    exact = add_rank(snapshot[["user_name", "exact_scores"]].rename(columns={"exact_scores": "Exact scores"}), "Exact scores")
    render_centered_dataframe(
        exact.rename(columns={"rank": "Rank", "user_name": "User name"}),
        bold_columns={"Exact scores"},
    )

    predictability_rows = group_match_predictability(participants, scoped_results, teams, matches)
    st.subheader("Top 10 Biggest Upsets")
    exclude_upset_draws = st.checkbox("Exclude draws", key="biggest_upsets_exclude_draws")
    upset_rows = predictability_rows
    if exclude_upset_draws and not upset_rows.empty:
        upset_rows = upset_rows[upset_rows["Outcome"] != "Draw"]
    render_centered_dataframe(
        upset_rows.head(10),
        bold_columns={"Actual outcome predicted by (%)"},
    )

    st.subheader("Top 10 Most Predictable Matches")
    exclude_predictable_draws = st.checkbox("Exclude draws", key="most_predictable_exclude_draws")
    predictable_rows = predictability_rows
    if exclude_predictable_draws and not predictable_rows.empty:
        predictable_rows = predictable_rows[predictable_rows["Outcome"] != "Draw"]
    render_centered_dataframe(
        predictable_rows.sort_values("Actual outcome predicted by (%)", ascending=False).head(10),
        bold_columns={"Actual outcome predicted by (%)"},
    )


def group_match_predictability(
    participants: list[dict[str, Any]],
    results: pd.DataFrame,
    teams: pd.DataFrame,
    matches: pd.DataFrame,
) -> pd.DataFrame:
    rows = []
    actual_rows = score_lookup(results)
    participant_prediction_rows = {
        participant["user_id"]: score_lookup(participant["predictions"])
        for participant in participants
    }
    for _, match in matches[matches["stage"] == GROUP_STAGE].iterrows():
        match_id = str(match["match_id"])
        actual = completed_score(actual_rows.get(match_id))
        if actual is None:
            continue
        actual_side = score_side(*actual)
        total = 0
        correct = 0
        for participant in participants:
            prediction = participant_prediction_rows[participant["user_id"]].get(match_id)
            predicted = completed_score(prediction)
            if predicted is None:
                continue
            total += 1
            correct += int(score_side(*predicted) == actual_side)
        if total == 0:
            continue
        if actual_side == "draw":
            outcome = "Draw"
        elif actual_side == "home":
            outcome = f"{team_name(match['home_team'], teams)} Win"
        else:
            outcome = f"{team_name(match['away_team'], teams)} Win"
        rows.append(
            {
                "Match": (
                    f"{match_id}: {team_name(match['home_team'], teams)} "
                    f"{actual[0]}-{actual[1]} {team_name(match['away_team'], teams)}"
                ),
                "Outcome": outcome,
                "Actual outcome predicted by (%)": round(100 * correct / total, 1),
            }
        )
    columns = ["Match", "Outcome", "Actual outcome predicted by (%)"]
    if not rows:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(rows, columns=columns).sort_values("Actual outcome predicted by (%)", ascending=True)


def per_match_score_options(
    results: pd.DataFrame,
    matches: pd.DataFrame,
    teams: pd.DataFrame,
    stage_filter: list[str] | None = None,
) -> tuple[list[tuple[str, str]], int]:
    result_rows = score_lookup(results)
    options = []
    first_uncompleted_index = None
    filtered_matches = matches
    if stage_filter is not None:
        filtered_matches = matches[matches["stage"].isin(stage_filter)]

    for _, match in filtered_matches.iterrows():
        match_id = str(match["match_id"])
        home_team = team_name(match.get("home_team", ""), teams)
        away_team = team_name(match.get("away_team", ""), teams)
        score = completed_score(result_rows.get(match_id))
        if score is None:
            label = f"{match_id}: {home_team} vs {away_team}"
            if first_uncompleted_index is None:
                first_uncompleted_index = len(options)
        else:
            label = f"{match_id}: {home_team} {score[0]}-{score[1]} {away_team}"
        options.append((match_id, label))

    if not options:
        return [], 0
    if first_uncompleted_index is not None:
        return options, first_uncompleted_index
    return options, len(options) - 1


def knockout_round_options() -> list[tuple[str, str]]:
    return [
        ("round_of_32", "Round of 32"),
        ("round_of_16", "Round of 16"),
        ("quarter_final", "Quarter-finals"),
        ("semi_final", "Semi-finals"),
        ("third_place", "Third-place match"),
        ("final", "Final"),
    ]


def stage_results_complete(stage: str, results: pd.DataFrame, matches: pd.DataFrame) -> bool:
    stage_matches = matches[matches["stage"].eq(stage)]
    if stage_matches.empty:
        return False
    result_rows = score_lookup(results)
    return all(
        completed_score(result_rows.get(str(match_id))) is not None
        for match_id in stage_matches["match_id"]
    )


def current_knockout_round_index(
    options: list[tuple[str, str]], results: pd.DataFrame, matches: pd.DataFrame
) -> int:
    if not stage_results_complete(GROUP_STAGE, results, matches):
        return 0
    for index, (stage, _) in enumerate(options):
        if not stage_results_complete(stage, results, matches):
            return index
    return max(0, len(options) - 1)


def default_per_match_phase_index(results: pd.DataFrame, matches: pd.DataFrame) -> int:
    return 1 if stage_results_complete(GROUP_STAGE, results, matches) else 0


def sync_default_per_match_phase(results: pd.DataFrame, matches: pd.DataFrame, phase_options: list[str]) -> int:
    default_index = default_per_match_phase_index(results, matches)
    default_phase = phase_options[default_index]
    default_state_key = "per_match_phase_default"
    if st.session_state.get(default_state_key) != default_phase:
        st.session_state["per_match_phase"] = default_phase
        st.session_state[default_state_key] = default_phase
    return default_index


def sync_default_per_user_phase(results: pd.DataFrame, matches: pd.DataFrame, phase_options: list[str]) -> int:
    default_index = default_per_match_phase_index(results, matches)
    default_phase = phase_options[default_index]
    default_state_key = "per_user_phase_default"
    if st.session_state.get(default_state_key) != default_phase:
        st.session_state["per_user_phase"] = default_phase
        st.session_state[default_state_key] = default_phase
    return default_index


def sync_default_per_user_knockout_stage(
    options: list[tuple[str, str]],
    results: pd.DataFrame,
    matches: pd.DataFrame,
) -> int:
    default_index = current_knockout_round_index(options, results, matches)
    default_stage = options[default_index][1]
    default_state_key = "per_user_knockout_stage_default"
    if st.session_state.get(default_state_key) != default_stage:
        st.session_state["per_user_knockout_stage"] = default_stage
        st.session_state[default_state_key] = default_stage
    return default_index


def knockout_round_entrants_locked(stage: str, results: pd.DataFrame, matches: pd.DataFrame) -> bool:
    previous_stage = {
        "round_of_32": GROUP_STAGE,
        "round_of_16": "round_of_32",
        "quarter_final": "round_of_16",
        "semi_final": "quarter_final",
        "third_place": "semi_final",
        "final": "semi_final",
    }.get(stage)
    return bool(previous_stage) and stage_results_complete(previous_stage, results, matches)


def knockout_round_points(stage: str) -> int:
    if stage == "round_of_32":
        return KNOCKOUT_STAGE_POINTS["round_of_16"]
    if stage == "round_of_16":
        return KNOCKOUT_STAGE_POINTS["quarter_final"]
    if stage == "quarter_final":
        return KNOCKOUT_STAGE_POINTS["semi_final"]
    if stage == "semi_final":
        return KNOCKOUT_STAGE_POINTS["final"]
    if stage == "third_place":
        return THIRD_PLACE_WINNER_POINTS
    if stage == "final":
        return CHAMPION_POINTS
    return 0


def knockout_round_advancement_label(stage: str) -> str:
    labels = {
        "round_of_32": "Predicted to reach Round of 16",
        "round_of_16": "Predicted to reach quarter-finals",
        "quarter_final": "Predicted to reach semi-finals",
        "semi_final": "Predicted to reach final",
        "third_place": "Predicted third-place winner",
        "final": "Predicted champion",
    }
    return labels.get(stage, "Predicted to advance")


def knockout_result_text(row: dict[str, Any] | pd.Series | None, teams: pd.DataFrame) -> str:
    if row is None:
        return "-"
    home_id = str(row.get("home_team", "")).strip()
    away_id = str(row.get("away_team", "")).strip()
    if not home_id or not away_id:
        return "-"
    score = completed_score(row)
    if score is None:
        return f"{team_name(home_id, teams)} vs {team_name(away_id, teams)}"
    home_goals, away_goals = score
    text = f"{team_name(home_id, teams)} {home_goals}-{away_goals} {team_name(away_id, teams)}"
    if home_goals == away_goals:
        winner_id = str(row.get("winner", "")).strip()
        if winner_id:
            text = f"{text}, {team_name(winner_id, teams)} on penalties"
    return text


def knockout_winner_first_result_text(row: dict[str, Any] | pd.Series | None, teams: pd.DataFrame) -> str:
    if row is None:
        return "-"
    home_id = str(row.get("home_team", "")).strip()
    away_id = str(row.get("away_team", "")).strip()
    if not home_id or not away_id:
        return "-"
    score = completed_score(row)
    winner_id = str(row.get("winner", "")).strip()
    if not winner_id or score is None:
        return knockout_result_text(row, teams)

    loser_id = away_id if winner_id == home_id else home_id
    winner_goals, loser_goals = score if winner_id == home_id else (score[1], score[0])
    text = f"{team_name(winner_id, teams)} {winner_goals}-{loser_goals} {team_name(loser_id, teams)}"
    if score[0] == score[1]:
        text = f"{text}, {team_name(winner_id, teams)} on penalties"
    return text


def knockout_team_progress_status(
    team_id: str,
    actual_round_rows: pd.DataFrame,
    actual_resolved_matches: pd.DataFrame,
    entrants_locked: bool,
    qualification_statuses: dict[str, str] | None = None,
) -> str:
    team_id = str(team_id)
    for _, row in actual_round_rows.iterrows():
        home_id = str(row.get("home_team", "")).strip()
        away_id = str(row.get("away_team", "")).strip()
        if not home_id or not away_id:
            continue
        winner_id = str(row.get("winner", "")).strip()
        if team_id in {home_id, away_id}:
            if not winner_id:
                return "pending"
            return "advanced" if winner_id == team_id else "eliminated"
    knockout_rows = actual_resolved_matches[actual_resolved_matches["stage"].isin(KNOCKOUT_STAGES)]
    for _, row in knockout_rows.iterrows():
        if str(row.get("loser", "")).strip() == team_id:
            return "eliminated"
    group_status = (qualification_statuses or {}).get(str(team_id), "")
    if group_status == "eliminated":
        return "eliminated"
    return "eliminated" if entrants_locked else "pending"


def predicted_team_progress_html(
    team_ids: list[str],
    teams: pd.DataFrame,
    actual_round_rows: pd.DataFrame,
    actual_resolved_matches: pd.DataFrame,
    entrants_locked: bool,
    qualification_statuses: dict[str, str] | None = None,
) -> str:
    if not team_ids:
        return "-"
    labels = sorted((team_name(team_id, teams), team_id) for team_id in team_ids)
    chips = []
    for label, team_id in labels:
        status = knockout_team_progress_status(
            team_id,
            actual_round_rows,
            actual_resolved_matches,
            entrants_locked,
            qualification_statuses,
        )
        chips.append(
            f'<span class="team-progress-chip {status}">{html.escape(label)}</span>'
        )
    return f'<div class="team-progress-list">{"".join(chips)}</div>'


def render_knockout_progression_table(
    rows: list[dict[str, str]],
    detail_rows: dict[str, list[str]],
    advancement_label: str,
) -> None:
    if not rows:
        st.info("No predictions available.")
        return

    headers = ["User name", "Correct", "Points earned", advancement_label, "Predicted results"]
    header_html = "".join(
        f'<th class="{" ".join(["left" if header in {"User name", advancement_label, "Predicted results"} else "", "bold" if header == "Points earned" else ""]).strip()}">{html.escape(header)}</th>'
        for header in headers
    )
    body_rows = []
    for row in rows:
        user_name = row["User name"]
        details = detail_rows.get(user_name, [])
        details_html = "<br>".join(html.escape(detail) for detail in details) or "-"
        cells = [
            f'<td class="left">{html.escape(user_name)}</td>',
            f"<td>{html.escape(row['Correct'])}</td>",
            f'<td class="bold">{html.escape(row["Points earned"])}</td>',
            f'<td class="left">{row.get("_advancement_html", html.escape(row[advancement_label]))}</td>',
            (
                '<td class="left">'
                "<details>"
                "<summary>Show predicted results</summary>"
                f'<div class="knockout-detail-results">{details_html}</div>'
                "</details>"
                "</td>"
            ),
        ]
        body_rows.append(f"<tr>{''.join(cells)}</tr>")

    st.markdown(
        (
            '<table class="leaderboard-table knockout-progression-table">'
            f"<thead><tr>{header_html}</tr></thead>"
            f"<tbody>{''.join(body_rows)}</tbody>"
            "</table>"
        ),
        unsafe_allow_html=True,
    )


def render_knockout_progression_scores(
    participants: list[dict[str, Any]],
    results: pd.DataFrame,
    teams: pd.DataFrame,
    matches: pd.DataFrame,
    knockout_matchups: pd.DataFrame,
    third_place_combinations: pd.DataFrame,
) -> None:
    options = knockout_round_options()
    labels = [label for _, label in options]
    default_round_index = current_knockout_round_index(options, results, matches)
    selected_label = st.selectbox(
        "Knockout round",
        labels,
        index=default_round_index,
        key="per_match_knockout_stage",
    )
    selected_stage = dict((label, stage) for stage, label in options)[selected_label]
    round_matches = matches[matches["stage"].eq(selected_stage)]
    if round_matches.empty:
        st.info("No matches are available for this knockout round.")
        return

    actual_state = derive_tournament_state(
        teams,
        matches,
        results,
        knockout_matchups,
        third_place_combinations,
        use_cards=True,
        require_confirmed_placements=True,
    )
    actual_winners = {
        winner
        for match_id, winner in actual_state["winners"].items()
        if match_id in set(round_matches["match_id"]) and winner
    }
    actual_round_rows = actual_state["resolved_matches"][
        actual_state["resolved_matches"]["stage"].eq(selected_stage)
    ]
    qualification_statuses = group_qualification_statuses(
        actual_state["group_standings"],
        actual_state["third_place"],
        matches,
        results,
        teams,
    )
    entrants_locked = knockout_round_entrants_locked(selected_stage, results, matches)
    stage_points = knockout_round_points(selected_stage)
    advancement_label = knockout_round_advancement_label(selected_stage)

    rows = []
    detail_rows: dict[str, list[str]] = {}
    predicted_counts: dict[str, int] = {}
    for participant in participants:
        prediction_state = derive_tournament_state(
            teams,
            matches,
            participant["predictions"],
            knockout_matchups,
            third_place_combinations,
            use_cards=False,
        )
        predicted_resolved_rows = score_lookup(prediction_state["resolved_matches"])
        predicted_winners = [
            prediction_state["winners"].get(str(match_id), "")
            for match_id in round_matches["match_id"]
        ]
        predicted_winners = [winner for winner in predicted_winners if winner]
        for winner in predicted_winners:
            predicted_counts[winner] = predicted_counts.get(winner, 0) + 1
        predicted_winner_set = set(predicted_winners)
        correct_count = len(predicted_winner_set & actual_winners) if actual_winners else 0
        correct_display = f"{correct_count}/{len(actual_winners)}" if actual_winners else "-"
        points_display = str(correct_count * stage_points) if actual_winners else "-"
        predicted_team_names = ", ".join(
            sorted(team_name(team_id, teams) for team_id in predicted_winners)
        ) or "-"

        rows.append(
            {
                "User name": participant["user_name"],
                "Correct": correct_display,
                "Points earned": points_display,
                advancement_label: predicted_team_names,
                "_advancement_html": predicted_team_progress_html(
                    predicted_winners,
                    teams,
                    actual_round_rows,
                    actual_state["resolved_matches"],
                    entrants_locked,
                    qualification_statuses,
                ),
            }
        )
        detail_rows[participant["user_name"]] = [
            result
            for result in sorted(
                knockout_winner_first_result_text(predicted_resolved_rows.get(str(match_id)), teams)
                for match_id in round_matches["match_id"]
            )
        ]

    render_knockout_progression_table(rows, detail_rows, advancement_label)

    if participants and predicted_counts:
        summary_rows = [
            {
                "Team": team_name(team_id, teams),
                "Predictions": count,
                "Percentage": f"{round(100 * count / len(participants), 1)}%",
            }
            for team_id, count in predicted_counts.items()
        ]
        summary_table = pd.DataFrame(summary_rows).sort_values(
            ["Predictions", "Team"], ascending=[False, True]
        )
        st.subheader("Most Predicted Teams to Advance/Win")
        render_centered_dataframe(
            summary_table,
            {"Team"},
            bold_columns={"Percentage"},
        )


def render_per_match_scores(
    users: pd.DataFrame,
    results: pd.DataFrame,
    teams: pd.DataFrame,
    matches: pd.DataFrame,
    knockout_matchups: pd.DataFrame,
    third_place_combinations: pd.DataFrame,
) -> None:
    humans = leaderboard_participants(users, include_ai=False)
    ais = load_ai_predictions()
    human_names = sorted([participant["user_name"] for participant in humans], key=str.lower)
    ai_names = sorted([participant["user_name"] for participant in ais], key=str.lower)
    phase_options = ["Group stage", "Knockout phase"]
    phase = st.selectbox(
        "Phase",
        phase_options,
        index=sync_default_per_match_phase(results, matches, phase_options),
        key="per_match_phase",
    )
    user_col, ai_col = st.columns([0.6, 0.4])
    with user_col:
        selected_humans = st.multiselect("Users", human_names, default=human_names, key="per_match_users")
    with ai_col:
        selected_ais = st.multiselect("AI predictions", ai_names, key="per_match_ai")
    participants = sorted(
        [p for p in humans if p["user_name"] in selected_humans]
        + [p for p in ais if p["user_name"] in selected_ais],
        key=lambda participant: participant["user_name"].lower(),
    )
    if phase == "Knockout phase":
        render_knockout_progression_scores(
            participants,
            results,
            teams,
            matches,
            knockout_matchups,
            third_place_combinations,
        )
        return

    match_options, default_match_index = per_match_score_options(
        results, matches, teams, stage_filter=[GROUP_STAGE]
    )
    if not match_options:
        st.info("No group-stage matches are available.")
        return
    match_labels = [label for _, label in match_options]
    selected_match_label = st.selectbox(
        "Match",
        match_labels,
        index=default_match_index,
        key="per_match_match",
    )
    selected_match_id = dict((label, match_id) for match_id, label in match_options)[selected_match_label]
    match = matches[matches["match_id"].eq(selected_match_id)].iloc[0]
    scoped_results = results_through_match(results, matches, selected_match_id)
    actual_rows = score_lookup(scoped_results)
    is_knockout_match = str(match["stage"]) in KNOCKOUT_STAGES
    actual_resolved_rows = {}
    if is_knockout_match:
        actual_state = derive_tournament_state(
            teams,
            matches,
            scoped_results,
            knockout_matchups,
            third_place_combinations,
            use_cards=True,
            require_confirmed_placements=True,
        )
        actual_resolved_rows = score_lookup(actual_state["resolved_matches"])
    rows = []
    winner_counts: dict[str, int] = {}
    matchup_counts: dict[str, int] = {}
    score_counts: dict[str, int] = {}
    for participant in participants:
        prediction_rows = score_lookup(participant["predictions"])
        prediction_resolved_rows = {}
        if is_knockout_match:
            prediction_state = derive_tournament_state(
                teams,
                matches,
                participant["predictions"],
                knockout_matchups,
                third_place_combinations,
                use_cards=False,
            )
            prediction_resolved_rows = score_lookup(prediction_state["resolved_matches"])
        points = match_score_points_for_match(
            match, prediction_rows, actual_rows, prediction_resolved_rows, actual_resolved_rows
        )
        prediction = prediction_rows.get(selected_match_id)
        resolved_row = prediction_resolved_rows.get(selected_match_id)
        winner = predicted_winner_bucket(match, prediction, resolved_row, teams)
        score_text = prediction_score_text(prediction)
        winner_counts[winner] = winner_counts.get(winner, 0) + 1
        score_counts[score_text] = score_counts.get(score_text, 0) + 1
        if is_knockout_match:
            matchup = matchup_text_from_resolved(resolved_row, teams)
            matchup_counts[matchup] = matchup_counts.get(matchup, 0) + 1
        rows.append(
            {
                "User name": participant["user_name"],
                "Prediction": score_text,
                "Actual score": prediction_score_text(actual_rows.get(selected_match_id)),
                "Points earned": points["total_points"],
            }
        )
    render_centered_dataframe(
        pd.DataFrame(rows),
        centered_columns={"User name"},
        bold_columns={"Points earned"},
    )
    if matchup_counts:
        st.subheader("Most Common Predicted Matchup")
        matchup_table = pd.DataFrame(
            {"Matchup": list(matchup_counts), "Predictions": list(matchup_counts.values())}
        ).sort_values("Predictions", ascending=False)
        render_padded_bar_chart(matchup_table, x="Matchup", y="Predictions")
    if winner_counts:
        st.subheader("Most Common Predicted Winner")
        winner_table = pd.DataFrame({"Winner": list(winner_counts), "Predictions": list(winner_counts.values())})
        render_pie_chart(winner_table, "Winner", "Predictions")
    if score_counts:
        st.subheader("Most Common Predicted Score")
        score_table = pd.DataFrame({"Prediction": list(score_counts), "Count": list(score_counts.values())}).sort_values("Count", ascending=False)
        render_padded_bar_chart(score_table, x="Prediction", y="Count")


def render_per_user_scores(
    users: pd.DataFrame,
    results: pd.DataFrame,
    teams: pd.DataFrame,
    matches: pd.DataFrame,
    knockout_matchups: pd.DataFrame,
    third_place_combinations: pd.DataFrame,
) -> None:
    humans = leaderboard_participants(users, include_ai=False)
    ais = load_ai_predictions()
    human_names = sorted([participant["user_name"] for participant in humans], key=str.lower)
    ai_names = sorted([participant["user_name"] for participant in ais], key=str.lower)
    default_humans = leaderboard_default_human_names(
        humans,
        results,
        teams,
        matches,
        knockout_matchups,
        third_place_combinations,
        rank_limit=3,
    )
    user_col, ai_col = st.columns([0.6, 0.4])
    with user_col:
        selected_humans = st.multiselect(
            "Users",
            human_names,
            default=default_humans,
            key="per_user_users_leaders",
        )
    with ai_col:
        selected_ais = st.multiselect("AI predictions", ai_names, key="per_user_ai")
    phase_options = ["Group stage", "Knockout phase"]
    phase = st.selectbox(
        "Phase",
        phase_options,
        index=sync_default_per_user_phase(results, matches, phase_options),
        key="per_user_phase",
    )
    selected = sorted(
        [participant for participant in humans if participant["user_name"] in selected_humans]
        + [participant for participant in ais if participant["user_name"] in selected_ais],
        key=lambda participant: participant["user_name"].lower(),
    )
    if phase == "Group stage":
        stage_filter = [GROUP_STAGE]
    else:
        options = knockout_round_options()
        labels = [label for _, label in options]
        selected_label = st.selectbox(
            "Stage",
            labels,
            index=sync_default_per_user_knockout_stage(options, results, matches),
            key="per_user_knockout_stage",
        )
        selected_stage = dict((label, stage) for stage, label in options)[selected_label]
        stage_filter = [selected_stage]
    rows = []
    actual_rows = score_lookup(results)
    prediction_rows = {
        participant["user_id"]: score_lookup(participant["predictions"])
        for participant in selected
    }
    actual_resolved_rows = {}
    prediction_states = {}
    prediction_resolved_rows = {}
    if phase == "Knockout phase":
        actual_state = derive_tournament_state(
            teams,
            matches,
            results,
            knockout_matchups,
            third_place_combinations,
            use_cards=True,
            require_confirmed_placements=True,
        )
        actual_resolved_rows = score_lookup(actual_state["resolved_matches"])
        prediction_states = {
            participant["user_id"]: derive_tournament_state(
                teams,
                matches,
                participant["predictions"],
                knockout_matchups,
                third_place_combinations,
                use_cards=False,
            )
            for participant in selected
        }
        prediction_resolved_rows = {
            user_id: score_lookup(state["resolved_matches"])
            for user_id, state in prediction_states.items()
        }
    for _, match in matches[matches["stage"].isin(stage_filter)].iterrows():
        if phase == "Group stage":
            row = {
                "Match": match["match_id"],
                "Matchup": f"{team_name(match.get('home_team', ''), teams)} vs {team_name(match.get('away_team', ''), teams)}",
                "Actual score": prediction_score_text(actual_rows.get(match["match_id"])),
            }
            for participant in selected:
                row[participant["user_name"]] = prediction_score_text(
                    prediction_rows[participant["user_id"]].get(match["match_id"])
                )
        else:
            actual_score = completed_score(actual_rows.get(match["match_id"]))
            row = {
                "Match": match["match_id"],
                "Stage": str(match["stage"]).replace("_", " ").title(),
                "Actual matchup": (
                    matchup_text_from_resolved(actual_resolved_rows.get(match["match_id"]), teams)
                    if actual_score is not None
                    else "-"
                ),
                "Actual score": prediction_score_text(actual_rows.get(match["match_id"])),
            }
            for participant in selected:
                resolved = prediction_resolved_rows[participant["user_id"]].get(match["match_id"], {})
                prediction_row = prediction_rows[participant["user_id"]].get(match["match_id"])
                predicted_score = completed_score(prediction_row)
                home = team_name(str(resolved.get("home_team", "")), teams)
                away = team_name(str(resolved.get("away_team", "")), teams)
                winner = str(resolved.get("winner", ""))
                is_penalty_prediction = predicted_score is not None and predicted_score[0] == predicted_score[1]
                if is_penalty_prediction and winner == str(resolved.get("home_team", "")):
                    home += "*"
                elif is_penalty_prediction and winner == str(resolved.get("away_team", "")):
                    away += "*"
                row[f"{participant['user_name']} matchup"] = f"{home} vs {away}"
                row[f"{participant['user_name']} score"] = prediction_score_text(prediction_row)
        rows.append(row)
    render_centered_dataframe(
        pd.DataFrame(rows),
        bold_columns={"Actual score"},
    )


def timeline_table(
    participants: list[dict[str, Any]],
    results: pd.DataFrame,
    teams: pd.DataFrame,
    matches: pd.DataFrame,
    knockout_matchups: pd.DataFrame,
    third_place_combinations: pd.DataFrame,
) -> pd.DataFrame:
    rows = []
    completed_ids = completed_match_ids(results, matches)
    columns = ["Match", "User name", "Participant type", "Points", "Rank"]
    prediction_states = prediction_states_for_participants(
        participants, teams, matches, knockout_matchups, third_place_combinations
    )
    prediction_scores = {
        str(participant["user_id"]): score_lookup(participant["predictions"])
        for participant in participants
    }
    prediction_stage_entrants = {
        user_id: stage_entrants_by_stage(state["resolved_matches"])
        for user_id, state in prediction_states.items()
    }
    match_records = matches.to_dict("records")
    for match_id in completed_ids:
        scoped_results = results_through_match(results, matches, match_id)
        snapshot = leaderboard_snapshot(
            participants,
            scoped_results,
            teams,
            matches,
            knockout_matchups,
            third_place_combinations,
            precomputed_prediction_states=prediction_states,
            precomputed_prediction_scores=prediction_scores,
            precomputed_prediction_stage_entrants=prediction_stage_entrants,
            precomputed_match_records=match_records,
        )
        for row in snapshot.to_dict("records"):
            rows.append(
                {
                    "Match": match_id,
                    "User name": row["user_name"],
                    "Participant type": "AI" if row["is_ai"] else "Human",
                    "Points": row["total_points"],
                    "Rank": row["rank"],
                }
            )
    return pd.DataFrame(rows, columns=columns)


def leaderboard_default_human_names(
    humans: list[dict[str, Any]],
    results: pd.DataFrame,
    teams: pd.DataFrame,
    matches: pd.DataFrame,
    knockout_matchups: pd.DataFrame,
    third_place_combinations: pd.DataFrame,
    rank_limit: int,
) -> list[str]:
    human_names = sorted(
        [participant["user_name"] for participant in humans],
        key=str.lower,
    )
    completed_ids = completed_match_ids(results, matches)
    if not completed_ids:
        return human_names[: min(rank_limit, len(human_names))]

    scoped_results = results_through_match(results, matches, completed_ids[-1])
    snapshot = leaderboard_snapshot(
        humans,
        scoped_results,
        teams,
        matches,
        knockout_matchups,
        third_place_combinations,
    )
    return snapshot[snapshot["rank"] <= rank_limit]["user_name"].tolist()


def render_timelines(
    users: pd.DataFrame,
    results: pd.DataFrame,
    teams: pd.DataFrame,
    matches: pd.DataFrame,
    knockout_matchups: pd.DataFrame,
    third_place_combinations: pd.DataFrame,
) -> None:
    humans = leaderboard_participants(users, include_ai=False)
    ais = load_ai_predictions()
    human_names = sorted([participant["user_name"] for participant in humans], key=str.lower)
    ai_names = sorted([participant["user_name"] for participant in ais], key=str.lower)
    default_humans = leaderboard_default_human_names(
        humans,
        results,
        teams,
        matches,
        knockout_matchups,
        third_place_combinations,
        rank_limit=5,
    )
    user_col, ai_col = st.columns([0.6, 0.4])
    with user_col:
        selected_humans = st.multiselect(
            "Users",
            human_names,
            default=default_humans,
            key="timeline_users_leaders",
        )
    with ai_col:
        selected_ais = st.multiselect("AI predictions", ai_names, key="timeline_ai")
    timeline_color_domain = sorted(
        [participant["user_name"] for participant in humans + ais],
        key=str.lower,
    )
    full_rank_timeline = timeline_table(
        humans, results, teams, matches, knockout_matchups, third_place_combinations
    )
    human_score_timeline = (
        full_rank_timeline[full_rank_timeline["User name"].isin(selected_humans)]
        if not full_rank_timeline.empty
        else full_rank_timeline
    )
    ai_score_timeline = (
        timeline_table(ais, results, teams, matches, knockout_matchups, third_place_combinations)
        if selected_ais
        else pd.DataFrame(columns=full_rank_timeline.columns)
    )
    ai_score_timeline = (
        ai_score_timeline[ai_score_timeline["User name"].isin(selected_ais)]
        if not ai_score_timeline.empty
        else ai_score_timeline
    )
    score_timeline = pd.concat(
        [human_score_timeline, ai_score_timeline],
        ignore_index=True,
    )
    if not score_timeline.empty:
        st.subheader("Score Timeline")
        render_padded_line_chart(
            score_timeline,
            x="Match",
            y="Points",
            color="User name",
            color_domain=timeline_color_domain,
        )
    rank_timeline = (
        full_rank_timeline[full_rank_timeline["User name"].isin(selected_humans)]
        if not full_rank_timeline.empty
        else full_rank_timeline
    )
    if not rank_timeline.empty:
        st.subheader("Rank Timeline")
        max_rank = max(1, int(rank_timeline["Rank"].max()))
        rank_ticks = list(range(1, max_rank + 1))
        render_padded_line_chart(
            rank_timeline,
            x="Match",
            y="Rank",
            color="User name",
            reverse_y=True,
            y_values=rank_ticks,
            color_domain=timeline_color_domain,
        )


def render_human_vs_ai(
    users: pd.DataFrame,
    results: pd.DataFrame,
    teams: pd.DataFrame,
    matches: pd.DataFrame,
    knockout_matchups: pd.DataFrame,
    third_place_combinations: pd.DataFrame,
) -> None:
    participants = leaderboard_participants(users, include_ai=True)
    prediction_states = prediction_states_for_participants(
        participants, teams, matches, knockout_matchups, third_place_combinations
    )
    prediction_scores = {
        str(participant["user_id"]): score_lookup(participant["predictions"])
        for participant in participants
    }
    prediction_stage_entrants = {
        user_id: stage_entrants_by_stage(state["resolved_matches"])
        for user_id, state in prediction_states.items()
    }
    match_records = matches.to_dict("records")
    match_ids = completed_match_ids(results, matches)
    scoped_results = results_through_match(results, matches, match_ids[-1] if match_ids else None)
    snapshot = leaderboard_snapshot(
        participants,
        scoped_results,
        teams,
        matches,
        knockout_matchups,
        third_place_combinations,
        precomputed_prediction_states=prediction_states,
        precomputed_prediction_scores=prediction_scores,
        precomputed_prediction_stage_entrants=prediction_stage_entrants,
        precomputed_match_records=match_records,
    )
    group_stage_results = results_for_stage(results, matches, GROUP_STAGE)
    group_stage_snapshot = leaderboard_snapshot(
        participants,
        group_stage_results,
        teams,
        matches,
        knockout_matchups,
        third_place_combinations,
        precomputed_prediction_states=prediction_states,
        precomputed_prediction_scores=prediction_scores,
        precomputed_prediction_stage_entrants=prediction_stage_entrants,
        precomputed_match_records=match_records,
    )
    if snapshot.empty:
        st.info("No predictions available.")
        return
    metrics = []
    for label, subset, group_stage_subset in [
        (
            "Humans",
            snapshot[~snapshot["is_ai"]],
            group_stage_snapshot[~group_stage_snapshot["is_ai"]],
        ),
        (
            "AI",
            snapshot[snapshot["is_ai"]],
            group_stage_snapshot[group_stage_snapshot["is_ai"]],
        ),
    ]:
        metrics.append(
            {
                "Group": label,
                "Average score": round(float(subset["total_points"].mean()), 1) if not subset.empty else 0,
                "Best score": int(subset["total_points"].max()) if not subset.empty else 0,
                "Correct outcomes per user": (
                    round(float(group_stage_subset["correct_winners"].mean()), 1)
                    if not group_stage_subset.empty
                    else 0
                ),
                "Exact score components per user": (
                    round(float(group_stage_subset["exact_goal_components"].mean()), 1)
                    if not group_stage_subset.empty
                    else 0
                ),
            }
        )
    st.subheader("Human vs AI Summary")
    render_centered_dataframe(
        pd.DataFrame(metrics),
        bold_columns={"Average score"},
    )
    st.subheader("Leaderboard Including AI")
    display_leaderboard_table(snapshot, include_change=False, highlight_ai=True)


def furthest_stage_for_team(state: dict[str, Any], team_id: str) -> str:
    if state["winners"].get("M104") == team_id:
        return "Winner"
    stages = [("final", "Final"), ("semi_final", "Semi-final"), ("quarter_final", "Quarter-final"), ("round_of_16", "Round of 16"), ("round_of_32", "Round of 32")]
    resolved = state["resolved_matches"]
    for stage, label in stages:
        if team_id in stage_entrants(resolved, stage):
            return label
    return "Group stage"


def render_prediction_analysis(
    users: pd.DataFrame,
    teams: pd.DataFrame,
    matches: pd.DataFrame,
    knockout_matchups: pd.DataFrame,
    third_place_combinations: pd.DataFrame,
) -> None:
    humans = leaderboard_participants(users, include_ai=False)
    ais = load_ai_predictions()
    human_col, ai_col = st.columns([0.6, 0.4])
    with human_col:
        include_humans = st.checkbox("Humans", value=True, key="prediction_analysis_include_humans")
    with ai_col:
        include_ai = st.checkbox("AI predictions", value=False, key="prediction_analysis_include_ai")
    if not include_humans and not include_ai:
        st.info("Select at least one category: Humans or AI predictions.")
        return
    participants = []
    if include_humans:
        participants.extend(humans)
    if include_ai:
        participants.extend(ais)
    participants = sorted(participants, key=lambda participant: participant["user_name"].lower())
    winner_counts: dict[str, int] = {}
    states = []
    for participant in participants:
        state = derive_tournament_state(teams, matches, participant["predictions"], knockout_matchups, third_place_combinations, use_cards=False)
        states.append((participant, state))
        winner = state["winners"].get("M104")
        winner_counts[team_name(winner, teams)] = winner_counts.get(team_name(winner, teams), 0) + 1
    st.subheader("Predicted World Cup Winners")
    if winner_counts:
        winner_table = pd.DataFrame(
            {"Country": list(winner_counts), "Predictions": list(winner_counts.values())}
        ).sort_values(
            ["Predictions", "Country"],
            ascending=[False, True],
            key=lambda column: column.str.lower() if column.name == "Country" else column,
        )
        render_padded_bar_chart(
            winner_table,
            x="Country",
            y="Predictions",
        )
    st.subheader("Predicted Finishing Stage per Country")
    team_names = sorted(teams["team_name"].tolist(), key=str.lower)
    default_index = team_names.index("Netherlands") if "Netherlands" in team_names else 0
    selected_country = st.selectbox("Country", team_names, index=default_index, key="prediction_analysis_country")
    selected_team_id = teams[teams["team_name"].eq(selected_country)].iloc[0]["team_id"]
    stage_counts: dict[str, int] = {}
    for _, state in states:
        stage = furthest_stage_for_team(state, selected_team_id)
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
    stage_table = pd.DataFrame({"Stage": list(stage_counts), "Number of Predictions": list(stage_counts.values())})
    stage_order = {stage: index for index, stage in enumerate(FINISHING_STAGE_ORDER)}
    render_centered_dataframe(
        stage_table.sort_values("Stage", key=lambda column: column.map(stage_order))
    )


ENDGAME_MAX_SCENARIOS = 32768
ENDGAME_OVERVIEW_STAGES = ["quarter_final", "semi_final", "third_place", "final"]
ENDGAME_BRACKET_DISPLAY_STAGES = ["quarter_final", "semi_final", "final", "third_place"]
ENDGAME_BRACKET_STAGE_LABELS = {
    "quarter_final": "Quarter-finals",
    "semi_final": "Semi-finals",
    "third_place": "Third place",
    "final": "Final",
}
ENDGAME_BRACKET_SLOT_CLASSES = {
    ("quarter_final", 0): "qf-1",
    ("quarter_final", 1): "qf-2",
    ("quarter_final", 2): "qf-3",
    ("quarter_final", 3): "qf-4",
    ("semi_final", 0): "sf-1",
    ("semi_final", 1): "sf-2",
    ("final", 0): "final-1",
    ("third_place", 0): "third-place-1",
}


def result_rows_frame(score_rows: dict[str, dict[str, Any]]) -> pd.DataFrame:
    return normalize_results(pd.DataFrame(score_rows.values()))


def scenario_result_row(match_id: str, home_wins: bool) -> dict[str, Any]:
    return {
        "match_id": match_id,
        "home_goals": 1 if home_wins else 0,
        "away_goals": 0 if home_wins else 1,
        PENALTY_WINNER_COLUMN: "",
        **{column: "" for column in CARD_COLUMNS},
    }


def enumerate_endgame_scenarios(
    results: pd.DataFrame,
    teams: pd.DataFrame,
    matches: pd.DataFrame,
    knockout_matchups: pd.DataFrame,
    third_place_combinations: pd.DataFrame,
    max_scenarios: int = ENDGAME_MAX_SCENARIOS,
) -> tuple[list[dict[str, Any]], bool]:
    initial_rows = {
        str(row["match_id"]): row
        for row in normalize_results(results).to_dict("records")
    }
    initial_results = result_rows_frame(initial_rows)
    initial_state = derive_tournament_state(
        teams,
        matches,
        initial_results,
        knockout_matchups,
        third_place_combinations,
        use_cards=True,
        require_confirmed_placements=True,
    )
    matchup_rows = {
        str(row["match_id"]): row
        for row in knockout_matchups.to_dict("records")
    }
    future_matches = [
        match
        for match in matches.to_dict("records")
        if str(match.get("stage", "")) in KNOCKOUT_STAGES
        and completed_score(initial_rows.get(str(match["match_id"]))) is None
    ]
    scenarios: list[dict[str, Any]] = []
    truncated = False

    def resolved_teams(match: dict[str, Any], winners: dict[str, str], losers: dict[str, str]) -> tuple[str, str]:
        match_id = str(match["match_id"])
        matchup = matchup_rows.get(match_id, {})
        home_slot = str(matchup.get("home_team", "")).strip()
        away_slot = str(matchup.get("away_team", "")).strip()
        home_id = resolve_slot(
            home_slot,
            away_slot,
            initial_state["group_standings"],
            initial_state["combination_row"],
            winners,
            losers,
            initial_state["confirmed_position_slots"],
        )
        away_id = resolve_slot(
            away_slot,
            home_slot,
            initial_state["group_standings"],
            initial_state["combination_row"],
            winners,
            losers,
            initial_state["confirmed_position_slots"],
        )
        return str(home_id or ""), str(away_id or "")

    def walk(
        match_index: int,
        score_rows: dict[str, dict[str, Any]],
        winners: dict[str, str],
        losers: dict[str, str],
        events: list[dict[str, Any]],
    ) -> None:
        nonlocal truncated
        if len(scenarios) >= max_scenarios:
            truncated = True
            return

        if match_index >= len(future_matches):
            scenario_results = result_rows_frame(score_rows)
            scenarios.append(
                {
                    "scenario_id": len(scenarios) + 1,
                    "results": scenario_results,
                    "events": events,
                }
            )
            return

        match = future_matches[match_index]
        match_id = str(match["match_id"])
        home_id, away_id = resolved_teams(match, winners, losers)
        if not home_id or not away_id:
            return
        for home_wins, winner_id, loser_id in [(True, home_id, away_id), (False, away_id, home_id)]:
            next_rows = {key: value.copy() for key, value in score_rows.items()}
            next_rows[match_id] = scenario_result_row(match_id, home_wins)
            next_winners = {**winners, match_id: winner_id}
            next_losers = {**losers, match_id: loser_id}
            walk(
                match_index + 1,
                next_rows,
                next_winners,
                next_losers,
                [
                    *events,
                    {
                        "match_id": match_id,
                        "stage": str(match.get("stage", "")),
                        "winner": winner_id,
                        "loser": loser_id,
                    },
                ],
            )

    walk(0, initial_rows, dict(initial_state["winners"]), dict(initial_state["losers"]), [])
    return scenarios, truncated


def scenario_event_label(event: dict[str, Any], teams: pd.DataFrame) -> str:
    winner = team_name(str(event.get("winner", "")), teams)
    loser = team_name(str(event.get("loser", "")), teams)
    stage = str(event.get("stage", ""))
    if str(event.get("match_id", "")) == FINAL_MATCH_ID:
        return f"{winner} wins the World Cup"
    if str(event.get("match_id", "")) == THIRD_PLACE_MATCH_ID:
        return f"{winner} wins the third-place match"
    return f"{winner} beats {loser} in the {stage_label(stage)}"


def scenario_event_group_label(events: list[dict[str, Any]], teams: pd.DataFrame) -> str:
    if not events:
        return ""
    representative = events[0]
    winner = team_name(str(representative.get("winner", "")), teams)
    stage = str(representative.get("stage", ""))
    match_id = str(representative.get("match_id", ""))
    if match_id == FINAL_MATCH_ID:
        return f"{winner} wins the World Cup"
    if match_id == THIRD_PLACE_MATCH_ID:
        return f"{winner} wins the third-place match"

    loser_ids = sorted(
        {
            str(event.get("loser", "")).strip()
            for event in events
            if str(event.get("loser", "")).strip()
        }
    )
    if len(loser_ids) == 1:
        return f"{winner} beats {team_name(loser_ids[0], teams)} in the {stage_label(stage)}"
    return f"{winner} wins in the {stage_label(stage)}"


def scenario_path_text(events: list[dict[str, Any]], teams: pd.DataFrame) -> str:
    ordered_events = sorted(events, key=scenario_event_sort_key)
    return "; ".join(scenario_event_label(event, teams) for event in ordered_events) or "Current standings hold"


def scenario_stage_entrants(
    initial_stage_entrants: dict[str, set[str]],
    events: list[dict[str, Any]],
) -> dict[str, set[str]]:
    entrants = {stage: set(values) for stage, values in initial_stage_entrants.items()}
    advancement_stage = {
        "round_of_32": "round_of_16",
        "round_of_16": "quarter_final",
        "quarter_final": "semi_final",
        "semi_final": "final",
    }
    for event in events:
        next_stage = advancement_stage.get(str(event.get("stage", "")))
        winner = str(event.get("winner", "")).strip()
        if next_stage and winner:
            entrants.setdefault(next_stage, set()).add(winner)
    return entrants


def scenario_winners(initial_winners: dict[str, str], events: list[dict[str, Any]]) -> dict[str, str]:
    winners = dict(initial_winners)
    for event in events:
        match_id = str(event.get("match_id", "")).strip()
        winner = str(event.get("winner", "")).strip()
        if match_id and winner:
            winners[match_id] = winner
    return winners


def endgame_scenario_rankings(
    participants: list[dict[str, Any]],
    scenarios: list[dict[str, Any]],
    current_snapshot: pd.DataFrame,
    current_state: dict[str, Any],
    teams: pd.DataFrame,
    matches: pd.DataFrame,
    knockout_matchups: pd.DataFrame,
    third_place_combinations: pd.DataFrame,
) -> pd.DataFrame:
    rows = []
    prediction_states = prediction_states_for_participants(
        participants, teams, matches, knockout_matchups, third_place_combinations
    )
    prediction_stage_entrants = {
        user_id: stage_entrants_by_stage(state["resolved_matches"])
        for user_id, state in prediction_states.items()
    }
    prediction_winners = {
        user_id: state["winners"]
        for user_id, state in prediction_states.items()
    }
    fixed_points = {
        str(row["user_id"]): int(row["match_score_points"]) + int(row["group_standings_points"])
        for row in current_snapshot.to_dict("records")
    }
    user_names = {
        str(participant["user_id"]): str(participant["user_name"])
        for participant in participants
    }
    initial_stage_entrants = stage_entrants_by_stage(current_state["resolved_matches"])
    initial_winners = current_state["winners"]
    for scenario in scenarios:
        actual_entrants = scenario_stage_entrants(initial_stage_entrants, scenario["events"])
        actual_winners = scenario_winners(initial_winners, scenario["events"])
        scenario_rows = []
        for participant in participants:
            user_id = str(participant["user_id"])
            total_points = fixed_points.get(user_id, 0)
            for stage, stage_points in KNOCKOUT_STAGE_POINTS.items():
                actual_stage = actual_entrants.get(stage, set())
                if actual_stage:
                    total_points += len(prediction_stage_entrants[user_id].get(stage, set()) & actual_stage) * stage_points
            predicted_third_place = prediction_winners[user_id].get(THIRD_PLACE_MATCH_ID)
            actual_third_place = actual_winners.get(THIRD_PLACE_MATCH_ID)
            if predicted_third_place and predicted_third_place == actual_third_place:
                total_points += THIRD_PLACE_WINNER_POINTS
            predicted_winner = prediction_winners[user_id].get(FINAL_MATCH_ID)
            actual_winner = actual_winners.get(FINAL_MATCH_ID)
            if predicted_winner and predicted_winner == actual_winner:
                total_points += CHAMPION_POINTS
            scenario_rows.append(
                {
                    "user_id": user_id,
                    "user_name": user_names.get(user_id, user_id),
                    "total_points": total_points,
                }
            )
        scenario_snapshot = add_rank(pd.DataFrame(scenario_rows), "total_points")
        for row in scenario_snapshot.to_dict("records"):
            rows.append(
                {
                    "scenario_id": int(scenario["scenario_id"]),
                    "user_id": str(row["user_id"]),
                    "user_name": str(row["user_name"]),
                    "rank": int(row["rank"]),
                    "total_points": int(row["total_points"]),
                }
            )
    return pd.DataFrame(rows, columns=["scenario_id", "user_id", "user_name", "rank", "total_points"])


def endgame_position_probability_table(
    scenario_rankings: pd.DataFrame,
    current_snapshot: pd.DataFrame,
) -> pd.DataFrame:
    columns = ["User name", "Current rank", "Position", "Probability", "Label"]
    if scenario_rankings.empty or current_snapshot.empty:
        return pd.DataFrame(columns=columns)

    total_scenarios = scenario_rankings["scenario_id"].nunique()
    max_rank = int(scenario_rankings["rank"].max())
    current_rows = current_snapshot.sort_values(["rank", "user_name"]).to_dict("records")
    rows = []
    for current in current_rows:
        user_id = str(current["user_id"])
        user_rows = scenario_rankings[scenario_rankings["user_id"].astype(str).eq(user_id)]
        counts = user_rows["rank"].value_counts().to_dict()
        for position in range(1, max_rank + 1):
            probability = 100 * counts.get(position, 0) / total_scenarios if total_scenarios else 0
            rows.append(
                {
                    "User name": str(current["user_name"]),
                    "Current rank": int(current["rank"]),
                    "Position": position,
                    "Probability": probability,
                    "Label": f"{probability:.1f}%",
                }
            )
    return pd.DataFrame(rows, columns=columns)


def render_endgame_position_heatmap(
    scenario_rankings: pd.DataFrame,
    current_snapshot: pd.DataFrame,
) -> None:
    probability_table = endgame_position_probability_table(scenario_rankings, current_snapshot)
    if probability_table.empty:
        st.info("No final-position probabilities are available yet.")
        return

    user_order = (
        current_snapshot.sort_values(["rank", "user_name"])["user_name"]
        .astype(str)
        .tolist()
    )
    position_order = sorted(probability_table["Position"].unique().tolist())
    probability_lookup = {
        (str(row["User name"]), int(row["Position"])): float(row["Probability"])
        for row in probability_table.to_dict("records")
    }
    headers = ["<th>Final Rank</th>", *[f"<th>{position}</th>" for position in position_order]]
    rows = []
    for user_name in user_order:
        cells = [f"<td>{html.escape(user_name)}</td>"]
        for position in position_order:
            probability = probability_lookup.get((user_name, position), 0.0)
            intensity = max(0.0, min(1.0, probability / 100))
            red = int(247 - (239 * intensity))
            green = int(251 - (170 * intensity))
            blue = int(255 - (99 * intensity))
            text_color = "#ffffff" if probability >= 35 else DEFAULT_THEME["text"]
            cells.append(
                f'<td style="background: rgb({red}, {green}, {blue}); color: {text_color};">'
                f"{probability:.1f}%"
                "</td>"
            )
        rows.append(f"<tr>{''.join(cells)}</tr>")

    st.markdown(
        '<div class="endgame-position-legend" aria-label="Probability color scale">'
        "<span>0%</span>"
        '<span class="endgame-position-legend-bar"></span>'
        "<span>100%</span>"
        "</div>"
        '<div class="endgame-position-scroll">'
        f'<table class="endgame-position-table"><thead><tr>{"".join(headers)}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
        "</div>",
        unsafe_allow_html=True,
    )


def winning_scenarios_by_user(
    scenarios: list[dict[str, Any]],
    scenario_rankings: pd.DataFrame,
) -> dict[str, list[dict[str, Any]]]:
    scenarios_by_id = {int(scenario["scenario_id"]): scenario for scenario in scenarios}
    winners: dict[str, list[dict[str, Any]]] = {}
    if scenario_rankings.empty:
        return winners
    winner_rows = scenario_rankings[scenario_rankings["rank"].eq(1)]
    for row in winner_rows.to_dict("records"):
        user_id = str(row["user_id"])
        scenario = scenarios_by_id.get(int(row["scenario_id"]))
        if scenario is not None:
            winners.setdefault(user_id, []).append(scenario)
    return winners


def event_key(event: dict[str, Any]) -> tuple[str, str]:
    return str(event.get("match_id", "")), str(event.get("winner", ""))


def match_number(match_id: Any) -> int:
    match = re.search(r"\d+", str(match_id))
    return int(match.group(0)) if match else 9999


def scenario_event_sort_key(event: dict[str, Any]) -> tuple[int, int]:
    stage = str(event.get("stage", ""))
    stage_index = KNOCKOUT_STAGES.index(stage) if stage in KNOCKOUT_STAGES else len(KNOCKOUT_STAGES)
    return stage_index, match_number(event.get("match_id", ""))


def clean_text_value(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def first_text_value(row: dict[str, Any] | pd.Series, columns: list[str]) -> str:
    for column in columns:
        if column not in row:
            continue
        text = clean_text_value(row.get(column, ""))
        if text:
            return text
    return ""


def format_date_text(date_text: str) -> str:
    date_text = clean_text_value(date_text)
    if not date_text:
        return ""
    parsed = pd.to_datetime(date_text, dayfirst=True, errors="coerce")
    if pd.isna(parsed):
        return date_text
    has_time = bool(parsed.hour or parsed.minute or parsed.second)
    return parsed.strftime("%d %b %Y, %H:%M" if has_time else "%d %b %Y").lstrip("0")


def match_datetime_label(match: dict[str, Any] | pd.Series) -> str:
    date_text = first_text_value(match, ["date", "match_date", "kickoff_date", "start_date"])
    time_text = first_text_value(match, ["time", "match_time", "kickoff_time", "start_time"])
    parts = [format_date_text(date_text), time_text]
    return " - ".join(part for part in parts if part)


def endgame_match_records(current_state: dict[str, Any], matches: pd.DataFrame) -> list[dict[str, Any]]:
    resolved_matches = current_state.get("resolved_matches", pd.DataFrame())
    resolved_lookup = {
        str(row["match_id"]): row
        for row in resolved_matches.to_dict("records")
        if "match_id" in row
    }
    records = []
    for match in matches.to_dict("records"):
        if str(match.get("stage", "")) not in ENDGAME_OVERVIEW_STAGES:
            continue
        match_id = str(match.get("match_id", ""))
        records.append({**match, **resolved_lookup.get(match_id, {})})
    return records


def next_unresolved_endgame_match(
    current_state: dict[str, Any],
    matches: pd.DataFrame,
    results: pd.DataFrame,
) -> dict[str, Any] | None:
    score_rows = score_lookup(results)
    for match in endgame_match_records(current_state, matches):
        match_id = str(match.get("match_id", ""))
        if completed_score(score_rows.get(match_id)) is None:
            return match
    return None


def endgame_feature_team_html(team_id: str | None, teams: pd.DataFrame, side: str) -> str:
    team_id = clean_text_value(team_id)
    name = team_name(team_id, teams) if team_id else "TBD"
    logo = image_data_uri(team_logo_lookup(teams).get(team_id, "")) if team_id else ""
    image = f'<img src="{logo}" alt="">' if logo else ""
    side_class = " away" if side == "away" else ""
    return (
        f'<div class="endgame-next-team{side_class}">'
        f"{image}<span>{html.escape(name)}</span>"
        "</div>"
    )


def endgame_bracket_team_html(
    team_id: str | None,
    teams: pd.DataFrame,
    goals: int | None,
    is_winner: bool,
) -> str:
    class_name = "endgame-bracket-team winner" if is_winner else "endgame-bracket-team"
    score = "" if goals is None else str(goals)
    return (
        f'<div class="{class_name}">'
        f"<span>{team_badge_html(team_id, teams)}</span>"
        f'<span class="endgame-bracket-score">{html.escape(score)}</span>'
        "</div>"
    )


def endgame_stage_css_class(stage: str) -> str:
    return str(stage).replace("_", "-")


def endgame_bracket_card_html(
    record: dict[str, Any],
    score_rows: dict[str, dict[str, Any]],
    teams: pd.DataFrame,
    next_match_id: str,
    slot_class: str = "",
) -> str:
    match_id = str(record.get("match_id", ""))
    score = completed_score(score_rows.get(match_id))
    winner = clean_text_value(record.get("winner", ""))
    home_id = clean_text_value(record.get("home_team", ""))
    away_id = clean_text_value(record.get("away_team", ""))
    card_classes = ["endgame-bracket-card"]
    if slot_class:
        card_classes.append(slot_class)
    if match_id == next_match_id:
        card_classes.append("next")
    if score is not None:
        card_classes.append("completed")
    home_goals = score[0] if score is not None else None
    away_goals = score[1] if score is not None else None
    meta = match_datetime_label(record) or "&nbsp;"
    meta_html = html.escape(meta) if meta != "&nbsp;" else meta
    return (
        f'<article class="{" ".join(card_classes)}">'
        f'<div class="endgame-bracket-meta">{meta_html}</div>'
        f'{endgame_bracket_team_html(home_id or None, teams, home_goals, winner == home_id)}'
        f'{endgame_bracket_team_html(away_id or None, teams, away_goals, winner == away_id)}'
        "</article>"
    )


def endgame_bracket_connector_svg() -> str:
    return (
        '<svg class="endgame-bracket-lines" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">'
        '<path d="M 23 12.5 H 25 V 25 H 27" />'
        '<path d="M 23 37.5 H 25 V 25 H 27" />'
        '<path d="M 23 62.5 H 25 V 75 H 27" />'
        '<path d="M 23 87.5 H 25 V 75 H 27" />'
        '<path class="final-line" d="M 48 25 H 50 V 50 H 52" />'
        '<path class="final-line" d="M 48 75 H 50 V 50" />'
        "</svg>"
    )


def render_endgame_bracket_overview(
    current_state: dict[str, Any],
    matches: pd.DataFrame,
    results: pd.DataFrame,
    teams: pd.DataFrame,
    next_match_id: str,
) -> None:
    records = endgame_match_records(current_state, matches)
    if not records:
        return

    score_rows = score_lookup(results)
    titles = [
        (
            f'<div class="endgame-bracket-title {endgame_stage_css_class(stage)}">'
            f"{html.escape(ENDGAME_BRACKET_STAGE_LABELS.get(stage, stage_label(stage)))}"
            "</div>"
        )
        for stage in ENDGAME_BRACKET_DISPLAY_STAGES
    ]
    desktop_cards = []
    mobile_columns = []
    for stage in ENDGAME_BRACKET_DISPLAY_STAGES:
        stage_records = [row for row in records if str(row.get("stage", "")) == stage]
        stage_cards = []
        for index, record in enumerate(stage_records):
            slot_class = ENDGAME_BRACKET_SLOT_CLASSES.get((stage, index), "")
            desktop_cards.append(endgame_bracket_card_html(record, score_rows, teams, next_match_id, slot_class))
            stage_cards.append(endgame_bracket_card_html(record, score_rows, teams, next_match_id))
        stage_body = "".join(stage_cards) if stage_cards else '<div class="endgame-empty-note">No matches</div>'
        mobile_columns.append(
            '<div class="endgame-bracket-column">'
            f'<div class="endgame-bracket-title">{html.escape(ENDGAME_BRACKET_STAGE_LABELS.get(stage, stage_label(stage)))}</div>'
            f"{stage_body}"
            "</div>"
        )

    st.markdown(
        f'<div class="endgame-bracket">{"".join(titles)}{endgame_bracket_connector_svg()}{"".join(desktop_cards)}</div>'
        f'<div class="endgame-bracket-mobile">{"".join(mobile_columns)}</div>',
        unsafe_allow_html=True,
    )


def render_next_endgame_match_header(match: dict[str, Any], teams: pd.DataFrame) -> None:
    home_id = clean_text_value(match.get("home_team", ""))
    away_id = clean_text_value(match.get("away_team", ""))
    meta_parts = [stage_label(str(match.get("stage", ""))), match_datetime_label(match)]
    meta = " - ".join(part for part in meta_parts if part)
    st.markdown(
        '<section class="endgame-next-match">'
        f'<div class="endgame-next-meta">{html.escape(meta)}</div>'
        '<div class="endgame-next-teams">'
        f'{endgame_feature_team_html(home_id or None, teams, "home")}'
        '<div class="endgame-next-vs">VS</div>'
        f'{endgame_feature_team_html(away_id or None, teams, "away")}'
        "</div>"
        "</section>",
        unsafe_allow_html=True,
    )


def scenario_ids_for_match_winner(
    scenarios: list[dict[str, Any]],
    match_id: str,
    winner_id: str,
) -> set[int]:
    scenario_ids = set()
    for scenario in scenarios:
        for event in scenario.get("events", []):
            if str(event.get("match_id", "")) == match_id and str(event.get("winner", "")) == winner_id:
                scenario_ids.add(int(scenario["scenario_id"]))
                break
    return scenario_ids


def rankings_for_scenario_ids(
    scenario_rankings: pd.DataFrame,
    scenario_ids: set[int],
) -> pd.DataFrame:
    if scenario_rankings.empty or not scenario_ids:
        return scenario_rankings.iloc[0:0].copy()
    return scenario_rankings[scenario_rankings["scenario_id"].isin(scenario_ids)].copy()


def rank_probability_lookup(
    scenario_rankings: pd.DataFrame,
    scenario_ids: set[int],
    rank_limit: int,
) -> dict[str, float]:
    rankings = rankings_for_scenario_ids(scenario_rankings, scenario_ids)
    if rankings.empty:
        return {}
    total_scenarios = rankings["scenario_id"].nunique()
    qualified = rankings[rankings["rank"].astype(int).le(rank_limit)]
    counts = qualified.groupby("user_id")["scenario_id"].nunique().to_dict()
    return {
        str(user_id): 100 * int(count) / total_scenarios
        for user_id, count in counts.items()
    }


def rank_stats_by_user(
    scenario_rankings: pd.DataFrame,
    scenario_ids: set[int],
    top_rank_limit: int,
) -> dict[str, dict[str, Any]]:
    rankings = rankings_for_scenario_ids(scenario_rankings, scenario_ids)
    if rankings.empty:
        return {}
    total_scenarios = rankings["scenario_id"].nunique()
    stats = {}
    for user_id, rows in rankings.groupby("user_id"):
        ranks = rows["rank"].astype(int)
        points = rows["total_points"].astype(float)
        stats[str(user_id)] = {
            "user_id": str(user_id),
            "user_name": str(rows.iloc[0]["user_name"]),
            "avg_rank": float(ranks.mean()),
            "min_rank": int(ranks.min()),
            "max_rank": int(ranks.max()),
            "avg_points": float(points.mean()),
            "first_pct": 100 * int(ranks.eq(1).sum()) / total_scenarios,
            "top_pct": 100 * int(ranks.le(top_rank_limit).sum()) / total_scenarios,
        }
    return stats


def format_probability(value: float) -> str:
    return f"{float(value):.1f}%"


def format_probability_with_delta(value: float, baseline: float) -> str:
    delta = float(value) - float(baseline)
    if abs(delta) < 0.05:
        return format_probability(value)
    return f"{format_probability(value)} ({delta:+.1f})"


def ordered_user_ids(current_snapshot: pd.DataFrame) -> list[str]:
    if current_snapshot.empty:
        return []
    return [
        str(row["user_id"])
        for row in current_snapshot.sort_values(["rank", "user_name"]).to_dict("records")
    ]


def limited_user_names_html(
    user_ids: list[str],
    user_names: dict[str, str],
    ordered_ids: list[str],
    limit: int = 4,
) -> str:
    user_id_set = set(user_ids)
    ordered = [user_id for user_id in ordered_ids if user_id in user_id_set]
    ordered.extend(sorted(user_id_set - set(ordered), key=lambda user_id: user_names.get(user_id, user_id)))
    labels = [html.escape(user_names.get(user_id, user_id)) for user_id in ordered[:limit]]
    remaining = len(ordered) - len(labels)
    if remaining > 0:
        labels.append(f"+{remaining} more")
    return ", ".join(labels)


def results_with_match_winner(
    results: pd.DataFrame,
    match_id: str,
    winner_id: str,
    home_id: str,
) -> pd.DataFrame:
    normalized = normalize_results(results)
    branch_rows = normalized[~normalized["match_id"].astype(str).eq(str(match_id))].copy()
    result_row = scenario_result_row(str(match_id), str(winner_id) == str(home_id))
    return normalize_results(pd.concat([branch_rows, pd.DataFrame([result_row])], ignore_index=True))


def leaderboard_snapshot_after_match_winner(
    participants: list[dict[str, Any]],
    results: pd.DataFrame,
    teams: pd.DataFrame,
    matches: pd.DataFrame,
    knockout_matchups: pd.DataFrame,
    third_place_combinations: pd.DataFrame,
    match_id: str,
    winner_id: str,
    home_id: str,
) -> pd.DataFrame:
    branch_results = results_with_match_winner(results, match_id, winner_id, home_id)
    return leaderboard_snapshot(
        participants,
        branch_results,
        teams,
        matches,
        knockout_matchups,
        third_place_combinations,
    )


def snapshot_rows_by_user(snapshot: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if snapshot.empty or "user_id" not in snapshot.columns:
        return {}
    return {str(row["user_id"]): row for row in snapshot.to_dict("records")}


def format_rank_delta(delta: int) -> str:
    if delta > 0:
        return f"+{delta} place{'s' if delta != 1 else ''}"
    if delta < 0:
        return f"{delta} place{'s' if delta != -1 else ''}"
    return "No change"


def format_points_delta(delta: int) -> str:
    if delta > 0:
        return f"+{delta} pts"
    if delta < 0:
        return f"{delta} pts"
    return "0 pts"


def branch_top_leaderboard_table_html(
    branch_snapshot: pd.DataFrame,
    current_snapshot: pd.DataFrame,
    limit: int = 5,
) -> str:
    if branch_snapshot.empty:
        return '<div class="endgame-empty-note">No branch leaderboard available.</div>'
    current_rows = snapshot_rows_by_user(current_snapshot)
    rows = branch_snapshot.sort_values(["rank", "user_name"]).head(limit).to_dict("records")
    body = []
    for row in rows:
        user_id = str(row["user_id"])
        points = to_int(row.get("total_points", 0))
        current_points = to_int(current_rows.get(user_id, {}).get("total_points", points))
        body.append(
            "<tr>"
            f"<td>{to_int(row.get('rank', 0))}</td>"
            f"<td>{html.escape(str(row['user_name']))}</td>"
            f"<td>{points}</td>"
            f"<td>{format_points_delta(points - current_points)}</td>"
            "</tr>"
        )
    return (
        '<table class="endgame-mini-table">'
        "<thead><tr><th>Rank</th><th>User</th><th>Points</th><th>Change</th></tr></thead>"
        f"<tbody>{''.join(body)}</tbody>"
        "</table>"
    )


def branch_consequence_items(
    all_stats: dict[str, dict[str, Any]],
    branch_stats: dict[str, dict[str, Any]],
    now_first: dict[str, float],
    branch_first: dict[str, float],
    current_snapshot: pd.DataFrame,
    branch_snapshot: pd.DataFrame,
    user_names: dict[str, str],
    ordered_ids: list[str],
) -> list[str]:
    epsilon = 0.0001

    def ordered_matching(predicate: Any) -> list[str]:
        return [
            user_id
            for user_id in ordered_ids
            if predicate(user_id)
        ]

    clinches_first = ordered_matching(
        lambda user_id: branch_first.get(user_id, 0.0) >= 100 - epsilon
        and now_first.get(user_id, 0.0) < 100 - epsilon
    )
    eliminated_first = ordered_matching(
        lambda user_id: now_first.get(user_id, 0.0) > epsilon
        and branch_first.get(user_id, 0.0) <= epsilon
    )
    cannot_below_second = ordered_matching(
        lambda user_id: user_id in branch_stats
        and branch_stats[user_id]["max_rank"] <= 2
        and all_stats.get(user_id, {}).get("max_rank", 99) > 2
    )
    clinches_top = ordered_matching(
        lambda user_id: user_id in branch_stats
        and branch_stats[user_id]["top_pct"] >= 100 - epsilon
        and all_stats.get(user_id, {}).get("top_pct", 0.0) < 100 - epsilon
    )
    loses_top = ordered_matching(
        lambda user_id: all_stats.get(user_id, {}).get("top_pct", 0.0) > epsilon
        and branch_stats.get(user_id, {}).get("top_pct", 0.0) <= epsilon
    )
    current_rows = snapshot_rows_by_user(current_snapshot)
    branch_rows = snapshot_rows_by_user(branch_snapshot)
    shared_ids = [user_id for user_id in ordered_ids if user_id in current_rows and user_id in branch_rows]

    takes_first = [
        user_id
        for user_id in shared_ids
        if to_int(current_rows[user_id].get("rank", 999)) > 1
        and to_int(branch_rows[user_id].get("rank", 999)) == 1
    ]
    enters_top_five = [
        user_id
        for user_id in shared_ids
        if to_int(current_rows[user_id].get("rank", 999)) > 5
        and to_int(branch_rows[user_id].get("rank", 999)) <= 5
    ]
    drops_from_top_five = [
        user_id
        for user_id in shared_ids
        if to_int(current_rows[user_id].get("rank", 999)) <= 5
        and to_int(branch_rows[user_id].get("rank", 999)) > 5
    ]
    rank_gains = {
        user_id: to_int(current_rows[user_id].get("rank", 999)) - to_int(branch_rows[user_id].get("rank", 999))
        for user_id in shared_ids
    }
    biggest_gain = max([value for value in rank_gains.values() if value > 0], default=0)
    biggest_fall = max([-value for value in rank_gains.values() if value < 0], default=0)
    biggest_climbers = [user_id for user_id, value in rank_gains.items() if value == biggest_gain] if biggest_gain else []
    biggest_fallers = [user_id for user_id, value in rank_gains.items() if value == -biggest_fall] if biggest_fall else []

    items = []
    if takes_first:
        items.append(f"<strong>Moves into first:</strong> {limited_user_names_html(takes_first, user_names, ordered_ids)}")
    if clinches_first:
        items.append(f"<strong>Clinches first:</strong> {limited_user_names_html(clinches_first, user_names, ordered_ids)}")
    if eliminated_first:
        items.append(
            f"<strong>Eliminated from first-place contention:</strong> {limited_user_names_html(eliminated_first, user_names, ordered_ids)}"
        )
    if cannot_below_second:
        items.append(
            f"<strong>Cannot finish below second:</strong> {limited_user_names_html(cannot_below_second, user_names, ordered_ids)}"
        )
    if clinches_top:
        items.append(f"<strong>Guarantees top 3:</strong> {limited_user_names_html(clinches_top, user_names, ordered_ids)}")
    if loses_top:
        items.append(f"<strong>Loses top-3 path:</strong> {limited_user_names_html(loses_top, user_names, ordered_ids)}")
    if enters_top_five:
        items.append(f"<strong>Enters top 5:</strong> {limited_user_names_html(enters_top_five, user_names, ordered_ids)}")
    if drops_from_top_five:
        items.append(f"<strong>Drops out of top 5:</strong> {limited_user_names_html(drops_from_top_five, user_names, ordered_ids)}")
    if biggest_climbers:
        items.append(
            f"<strong>Highest jumper:</strong> {limited_user_names_html(biggest_climbers, user_names, ordered_ids)} "
            f"({format_rank_delta(biggest_gain)})"
        )
    if biggest_fallers:
        items.append(
            f"<strong>Biggest faller:</strong> {limited_user_names_html(biggest_fallers, user_names, ordered_ids)} "
            f"({format_rank_delta(-biggest_fall)})"
        )
    return items


def outcome_card_html(
    winner_id: str,
    branch_stats: dict[str, dict[str, Any]],
    all_stats: dict[str, dict[str, Any]],
    now_first: dict[str, float],
    branch_first: dict[str, float],
    current_snapshot: pd.DataFrame,
    branch_snapshot: pd.DataFrame,
    teams: pd.DataFrame,
    user_names: dict[str, str],
    ordered_ids: list[str],
) -> str:
    consequences = branch_consequence_items(
        all_stats,
        branch_stats,
        now_first,
        branch_first,
        current_snapshot,
        branch_snapshot,
        user_names,
        ordered_ids,
    )
    consequence_html = (
        '<ul class="endgame-consequence-list">'
        + "".join(f"<li>{item}</li>" for item in consequences)
        + "</ul>"
        if consequences
        else '<div class="endgame-empty-note">No major leaderboard or contention changes from this result alone.</div>'
    )
    return (
        '<div class="endgame-outcome-card">'
        f"<h4>If {team_badge_html(winner_id, teams)} advances</h4>"
        '<div class="endgame-card-section-title">Top 5 leaderboard</div>'
        f"{branch_top_leaderboard_table_html(branch_snapshot, current_snapshot)}"
        '<div class="endgame-card-section-title">Pool consequences</div>'
        f"{consequence_html}"
        "</div>"
    )


def render_first_place_odds_swing(
    current_snapshot: pd.DataFrame,
    now_first: dict[str, float],
    home_first: dict[str, float],
    away_first: dict[str, float],
    home_id: str,
    away_id: str,
    teams: pd.DataFrame,
) -> None:
    user_names = {
        str(row["user_id"]): str(row["user_name"])
        for row in current_snapshot.to_dict("records")
    }
    current_rank = {
        str(row["user_id"]): int(row["rank"])
        for row in current_snapshot.to_dict("records")
    }
    home_name = team_name(home_id, teams)
    away_name = team_name(away_id, teams)
    home_column = f"If {home_name} advances"
    away_column = f"If {away_name} advances"
    rows = []
    for user_id, user_name in user_names.items():
        now = now_first.get(user_id, 0.0)
        home = home_first.get(user_id, 0.0)
        away = away_first.get(user_id, 0.0)
        if max(now, home, away) <= 0:
            continue
        rows.append(
            {
                "User name": user_name,
                "Now": format_probability(now),
                home_column: format_probability_with_delta(home, now),
                away_column: format_probability_with_delta(away, now),
                "_sort_now": now,
                "_sort_rank": current_rank.get(user_id, 999),
            }
        )
    if not rows:
        return
    rows.sort(key=lambda row: (-float(row["_sort_now"]), int(row["_sort_rank"]), str(row["User name"])))
    table = pd.DataFrame(rows).drop(columns=["_sort_now", "_sort_rank"])
    st.markdown('<div class="endgame-odds-swing-heading">First-place odds swing</div>', unsafe_allow_html=True)
    render_centered_dataframe(
        table,
        centered_columns={"User name"},
        bold_columns={home_column, away_column},
    )


def render_next_game_outcome_overview(
    next_match: dict[str, Any],
    participants: list[dict[str, Any]],
    scenarios: list[dict[str, Any]],
    scenario_rankings: pd.DataFrame,
    current_snapshot: pd.DataFrame,
    results: pd.DataFrame,
    teams: pd.DataFrame,
    matches: pd.DataFrame,
    knockout_matchups: pd.DataFrame,
    third_place_combinations: pd.DataFrame,
) -> None:
    match_id = str(next_match.get("match_id", ""))
    home_id = clean_text_value(next_match.get("home_team", ""))
    away_id = clean_text_value(next_match.get("away_team", ""))
    if not home_id or not away_id:
        st.info("The next match will show outcome consequences once both teams are resolved.")
        return

    all_scenario_ids = {int(scenario["scenario_id"]) for scenario in scenarios}
    home_scenario_ids = scenario_ids_for_match_winner(scenarios, match_id, home_id)
    away_scenario_ids = scenario_ids_for_match_winner(scenarios, match_id, away_id)
    if not home_scenario_ids or not away_scenario_ids:
        st.info("Outcome consequences are not available for the next match yet.")
        return

    max_rank = int(scenario_rankings["rank"].max()) if not scenario_rankings.empty else 0
    top_rank_limit = min(3, max_rank) if max_rank else 3
    now_first = rank_probability_lookup(scenario_rankings, all_scenario_ids, 1)
    home_first = rank_probability_lookup(scenario_rankings, home_scenario_ids, 1)
    away_first = rank_probability_lookup(scenario_rankings, away_scenario_ids, 1)
    all_stats = rank_stats_by_user(scenario_rankings, all_scenario_ids, top_rank_limit)
    home_stats = rank_stats_by_user(scenario_rankings, home_scenario_ids, top_rank_limit)
    away_stats = rank_stats_by_user(scenario_rankings, away_scenario_ids, top_rank_limit)
    home_snapshot = leaderboard_snapshot_after_match_winner(
        participants,
        results,
        teams,
        matches,
        knockout_matchups,
        third_place_combinations,
        match_id,
        home_id,
        home_id,
    )
    away_snapshot = leaderboard_snapshot_after_match_winner(
        participants,
        results,
        teams,
        matches,
        knockout_matchups,
        third_place_combinations,
        match_id,
        away_id,
        home_id,
    )
    user_names = {
        str(row["user_id"]): str(row["user_name"])
        for row in current_snapshot.to_dict("records")
    }
    ordered_ids = ordered_user_ids(current_snapshot)

    home_column, away_column = st.columns(2)
    with home_column:
        st.markdown(
            outcome_card_html(
                home_id,
                home_stats,
                all_stats,
                now_first,
                home_first,
                current_snapshot,
                home_snapshot,
                teams,
                user_names,
                ordered_ids,
            ),
            unsafe_allow_html=True,
        )
    with away_column:
        st.markdown(
            outcome_card_html(
                away_id,
                away_stats,
                all_stats,
                now_first,
                away_first,
                current_snapshot,
                away_snapshot,
                teams,
                user_names,
                ordered_ids,
            ),
            unsafe_allow_html=True,
        )

    render_first_place_odds_swing(
        current_snapshot,
        now_first,
        home_first,
        away_first,
        home_id,
        away_id,
        teams,
    )


def render_endgame_next_game_overview(
    participants: list[dict[str, Any]],
    scenarios: list[dict[str, Any]],
    scenario_rankings: pd.DataFrame,
    current_snapshot: pd.DataFrame,
    current_state: dict[str, Any],
    results: pd.DataFrame,
    teams: pd.DataFrame,
    matches: pd.DataFrame,
    knockout_matchups: pd.DataFrame,
    third_place_combinations: pd.DataFrame,
) -> None:
    next_match = next_unresolved_endgame_match(current_state, matches, results)
    next_match_id = str(next_match.get("match_id", "")) if next_match else ""

    render_endgame_bracket_overview(current_state, matches, results, teams, next_match_id)

    if not next_match:
        st.info("All quarter-final onward matches have been completed.")
        return

    render_next_endgame_match_header(next_match, teams)
    render_next_game_outcome_overview(
        next_match,
        participants,
        scenarios,
        scenario_rankings,
        current_snapshot,
        results,
        teams,
        matches,
        knockout_matchups,
        third_place_combinations,
    )


def render_user_winning_scenarios(
    participants: list[dict[str, Any]],
    scenarios: list[dict[str, Any]],
    scenario_rankings: pd.DataFrame,
    current_snapshot: pd.DataFrame,
    teams: pd.DataFrame,
) -> None:
    winners = winning_scenarios_by_user(scenarios, scenario_rankings)
    total_scenarios = len(scenarios)
    ordered_users = []
    for user in current_snapshot.to_dict("records"):
        user_id = str(user["user_id"])
        winning = winners.get(user_id, [])
        probability = 100 * len(winning) / total_scenarios if total_scenarios else 0
        ordered_users.append({**user, "winning_scenarios": winning, "win_probability": probability})
    ordered_users.sort(
        key=lambda row: (
            -float(row["win_probability"]),
            int(row["rank"]),
            str(row["user_name"]),
        )
    )
    for user in ordered_users:
        user_name = str(user["user_name"])
        winning = user["winning_scenarios"]
        probability = float(user["win_probability"])
        with st.expander(f"{user_name}: {probability:.1f}% chance to finish first", expanded=probability > 0):
            if not winning:
                st.write("No remaining outcome combination leaves this participant in first place.")
                continue

            events_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
            for scenario in winning:
                for event in scenario["events"]:
                    key = event_key(event)
                    events_by_key.setdefault(key, []).append(event)

            labels = {
                key: scenario_event_group_label(events, teams)
                for key, events in events_by_key.items()
            }

            required_keys = [
                key
                for key, events in events_by_key.items()
                if len(events) == len(winning)
            ]
            if required_keys:
                st.markdown("Required in every winning path:")
                for key in sorted(
                    required_keys,
                    key=lambda item: scenario_event_sort_key(events_by_key[item][0]),
                ):
                    st.write(f"- {labels[key]}")

            common_rows = [
                {
                    "Outcome": labels[key],
                    "Winning paths": len(events),
                    "Share of winning paths": f"{100 * len(events) / len(winning):.1f}%",
                }
                for key, events in events_by_key.items()
                if len(events) < len(winning)
            ]
            if common_rows:
                if required_keys:
                    st.markdown('<div class="section-gap"></div>', unsafe_allow_html=True)
                st.markdown("Most common events in winning paths:")
                common_table = pd.DataFrame(common_rows).sort_values(
                    ["Winning paths", "Outcome"],
                    ascending=[False, True],
                ).head(8)
                render_centered_dataframe(common_table, centered_columns={"Outcome"}, bold_columns={"Share of winning paths"})

            if required_keys or common_rows:
                st.markdown('<div class="section-gap"></div>', unsafe_allow_html=True)
            path_rows = [
                {
                    "Required outcomes": scenario_path_text(scenario["events"], teams),
                }
                for scenario in winning
            ]
            st.markdown("Winning paths:")
            st.dataframe(pd.DataFrame(path_rows), hide_index=True, use_container_width=True)


def endgame_scenarios_context(
    users: pd.DataFrame,
    results: pd.DataFrame,
    teams: pd.DataFrame,
    matches: pd.DataFrame,
    knockout_matchups: pd.DataFrame,
    third_place_combinations: pd.DataFrame,
    show_empty_message: bool = True,
) -> dict[str, Any] | None:
    participants = leaderboard_participants(users, include_ai=False)
    if not participants:
        if show_empty_message:
            st.info("Endgame scenarios will appear once participants have submitted predictions.")
        return None

    with st.spinner("Calculating remaining outcome combinations..."):
        scenarios, truncated = enumerate_endgame_scenarios(
            results,
            teams,
            matches,
            knockout_matchups,
            third_place_combinations,
        )
        current_snapshot = leaderboard_snapshot(
            participants,
            results,
            teams,
            matches,
            knockout_matchups,
            third_place_combinations,
        )
        current_state = derive_tournament_state(
            teams,
            matches,
            results,
            knockout_matchups,
            third_place_combinations,
            use_cards=True,
            require_confirmed_placements=True,
        )
        scenario_rankings = endgame_scenario_rankings(
            participants,
            scenarios,
            current_snapshot,
            current_state,
            teams,
            matches,
            knockout_matchups,
            third_place_combinations,
        )

    if not scenarios or scenario_rankings.empty:
        if show_empty_message:
            st.info("No remaining endgame outcome combinations could be resolved from the current bracket.")
        return None

    return {
        "participants": participants,
        "scenarios": scenarios,
        "truncated": truncated,
        "current_snapshot": current_snapshot,
        "current_state": current_state,
        "scenario_rankings": scenario_rankings,
    }


def render_leaderboard_endgame_overview(
    users: pd.DataFrame,
    results: pd.DataFrame,
    teams: pd.DataFrame,
    matches: pd.DataFrame,
    knockout_matchups: pd.DataFrame,
    third_place_combinations: pd.DataFrame,
) -> bool:
    context = endgame_scenarios_context(
        users,
        results,
        teams,
        matches,
        knockout_matchups,
        third_place_combinations,
        show_empty_message=False,
    )
    if context is None:
        return False

    render_endgame_next_game_overview(
        context["participants"],
        context["scenarios"],
        context["scenario_rankings"],
        context["current_snapshot"],
        context["current_state"],
        results,
        teams,
        matches,
        knockout_matchups,
        third_place_combinations,
    )
    return True


def render_endgame_scenarios(
    users: pd.DataFrame,
    results: pd.DataFrame,
    teams: pd.DataFrame,
    matches: pd.DataFrame,
    knockout_matchups: pd.DataFrame,
    third_place_combinations: pd.DataFrame,
) -> None:
    context = endgame_scenarios_context(
        users,
        results,
        teams,
        matches,
        knockout_matchups,
        third_place_combinations,
        show_empty_message=True,
    )
    if context is None:
        return

    participants = context["participants"]
    scenarios = context["scenarios"]
    truncated = context["truncated"]
    current_snapshot = context["current_snapshot"]
    scenario_rankings = context["scenario_rankings"]

    st.subheader("Potential Final Positions")
    render_endgame_position_heatmap(scenario_rankings, current_snapshot)
    scenario_count = len(scenarios)
    scenario_label = f"{scenario_count:,} remaining knockout outcome combinations"
    if truncated:
        scenario_label = f"First {scenario_label} shown because the scenario limit was reached"
    st.caption(f"Based on {scenario_label}, weighted equally.")

    st.divider()
    st.subheader("Winning Scenarios")
    render_user_winning_scenarios(
        participants,
        scenarios,
        scenario_rankings,
        current_snapshot,
        teams,
    )


def render_leaderboard(
    users: pd.DataFrame,
    results: pd.DataFrame,
    teams: pd.DataFrame,
    matches: pd.DataFrame,
    knockout_matchups: pd.DataFrame,
    third_place_combinations: pd.DataFrame,
) -> None:
    st.header("Leaderboard")
    sections = [
        "Leaderboard",
        "Endgame Scenarios",
        "Additional Rankings",
        "Per Match Scores",
        "Per User Scores",
        "Timelines",
        "Human vs AI",
        "Prediction Analysis",
    ]
    selected_section = st.segmented_control(
        "Leaderboard section",
        sections,
        default=sections[0],
        required=True,
        key="leaderboard_section",
        label_visibility="collapsed",
        width="stretch",
    )
    if selected_section == "Leaderboard":
        render_default_leaderboard(users, results, teams, matches, knockout_matchups, third_place_combinations)
    elif selected_section == "Additional Rankings":
        render_additional_rankings(users, results, teams, matches, knockout_matchups, third_place_combinations)
    elif selected_section == "Per Match Scores":
        render_per_match_scores(users, results, teams, matches, knockout_matchups, third_place_combinations)
    elif selected_section == "Per User Scores":
        render_per_user_scores(users, results, teams, matches, knockout_matchups, third_place_combinations)
    elif selected_section == "Timelines":
        render_timelines(users, results, teams, matches, knockout_matchups, third_place_combinations)
    elif selected_section == "Human vs AI":
        render_human_vs_ai(users, results, teams, matches, knockout_matchups, third_place_combinations)
    elif selected_section == "Prediction Analysis":
        render_prediction_analysis(users, teams, matches, knockout_matchups, third_place_combinations)
    elif selected_section == "Endgame Scenarios":
        render_endgame_scenarios(users, results, teams, matches, knockout_matchups, third_place_combinations)


def main() -> None:
    st.set_page_config(page_title="World Cup 2026 Pool", layout="wide")
    clear_stale_streamlit_cache()
    apply_visual_theme()
    ensure_data_files()

    teams = read_csv(TEAMS_FILE)
    matches = read_csv(MATCHES_FILE)
    users = normalize_users(read_csv(USERS_FILE))
    results = normalize_results(read_csv(RESULTS_FILE))
    knockout_matchups = read_csv(KNOCKOUT_MATCHUPS_FILE)
    third_place_combinations = read_csv(THIRD_PLACE_COMBINATIONS_FILE)

    st.markdown(
        """
        <div class="app-title-row">
            <div class="app-title-bar"></div>
            <div class="app-title-text">World Cup 2026 Pool</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    errors = validate_sources(teams, matches, users, results, knockout_matchups, third_place_combinations)
    if errors:
        st.error("Fix the source CSV files before using the app.")
        for error in errors:
            st.write(f"- {error}")
        return

    restore_draft_from_url(matches)

    submissions_open = submissions_are_open()

    if submissions_open:
        available_pages = ["Home", "Rules", "Results"]
    else:
        available_pages = ["Leaderboard", "Results", "Rules"]

    page = st.sidebar.radio("Tabs", available_pages)
    st.sidebar.caption("Submissions are open." if submissions_open else "Submissions are closed.")

    if page != "Home":
        persist_active_prediction_session(matches)

    if page == "Home":
        render_home(teams, matches, users, knockout_matchups, third_place_combinations)
    elif page == "Rules":
        render_rules()
    elif page == "Results":
        render_results(teams, matches, results, knockout_matchups, third_place_combinations)
    elif page == "Leaderboard":
        render_leaderboard(users, results, teams, matches, knockout_matchups, third_place_combinations)


def render_google_sheets_rate_limit_dialog() -> None:
    title = "Google Sheets limit reached"
    message = (
        "The app has reached the temporary Google Sheets API request limit. "
        "Wait about one minute, then try again. Your submitted data is stored in Google Sheets; "
        "this is a temporary quota throttle, not a data-loss error."
    )

    dialog = getattr(st, "dialog", None)
    if callable(dialog):
        @dialog(title)
        def quota_dialog() -> None:
            st.warning(message)
            if st.button("Try again"):
                st.rerun()

        quota_dialog()
    else:
        st.error(f"{title}: {message}")
        if st.button("Try again"):
            st.rerun()
    st.stop()


if __name__ == "__main__":
    try:
        main()
    except GoogleSheetsRateLimitError:
        render_google_sheets_rate_limit_dialog()
