import streamlit as st
import duckdb
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
import datetime
import io
import json
import os
import re
import pandas as pd
import google.generativeai as genai

# USBC split definitions (embedded for reliability; same as splits.json)
_SPLITS_DATA = [
    {"pins": [7, 10], "name": "Bedposts", "category": "Extreme Wide"},
    {"pins": [4, 6, 7, 10], "name": "Big Four", "category": "Extreme Wide"},
    {"pins": [4, 6, 7, 8, 10], "name": "Greek Church (Right Hand)", "category": "Complex Split"},
    {"pins": [4, 6, 7, 9, 10], "name": "Greek Church (Left Hand)", "category": "Complex Split"},
    {"pins": [2, 4, 6, 7, 10], "name": "Big Five (Right Hand)", "category": "Complex Split"},
    {"pins": [3, 4, 6, 7, 10], "name": "Big Five (Left Hand)", "category": "Complex Split"},
    {"pins": [5, 7, 10], "name": "Sour Apple / Lily", "category": "Middle Split"},
    {"pins": [2, 7], "name": "Baby Split (Right Hand)", "category": "Baby Split"},
    {"pins": [3, 10], "name": "Baby Split (Left Hand)", "category": "Baby Split"},
    {"pins": [5, 7], "name": "Dime Store (Right Hand)", "category": "Dime Store"},
    {"pins": [5, 10], "name": "Dime Store (Left Hand)", "category": "Dime Store"},
    {"pins": [4, 5], "name": "Steam Fitter", "category": "Fit Split"},
    {"pins": [5, 6], "name": "Fit Split", "category": "Fit Split"},
    {"pins": [2, 3], "name": "Fit Split", "category": "Fit Split"},
    {"pins": [7, 8], "name": "Back Row Fit Split", "category": "Fit Split"},
    {"pins": [9, 10], "name": "Back Row Fit Split", "category": "Fit Split"},
    {"pins": [4, 9], "name": "Parallel Split", "category": "Distant Split"},
    {"pins": [6, 8], "name": "Parallel Split", "category": "Distant Split"},
    {"pins": [4, 7, 10], "name": "Corner Split", "category": "Triangular"},
    {"pins": [6, 7, 10], "name": "Corner Split", "category": "Triangular"},
    {"pins": [2, 7, 10], "name": "Christmas Tree", "category": "Triangular"},
    {"pins": [3, 7, 10], "name": "Christmas Tree", "category": "Triangular"},
    {"pins": [7, 9], "name": "Cincinnati", "category": "Back Row"},
    {"pins": [8, 10], "name": "Cincinnati", "category": "Back Row"},
    {"pins": [4, 6], "name": "Golden Gate / Cincinnati", "category": "Middle Row"},
]

_SPLITS_CACHE = None

def _load_splits():
    """Return dict of (sorted pins tuple) -> split name. Uses embedded _SPLITS_DATA."""
    global _SPLITS_CACHE
    if _SPLITS_CACHE is not None:
        return _SPLITS_CACHE
    _SPLITS_CACHE = {
        tuple(sorted(int(p) for p in entry["pins"])): entry["name"]
        for entry in _SPLITS_DATA
    }
    return _SPLITS_CACHE

def _normalize_pins_list(pins_left_list):
    """Convert to list of ints 1-10; return [] if invalid or headpin (1) present."""
    if not pins_left_list:
        return []
    out = []
    for p in pins_left_list:
        try:
            v = int(p) if not isinstance(p, int) else p
            if 1 <= v <= 10:
                out.append(v)
        except (TypeError, ValueError):
            continue
    return out

def get_split_name(pins_left_list):
    """If pins_left (standing) matches a known split in splits.json, return its name; else None."""
    pins = _normalize_pins_list(pins_left_list)
    if len(pins) < 2 or 1 in pins:
        return None
    key = tuple(sorted(pins))
    return _load_splits().get(key)

# --- AI Logic ---
def get_ai_suggestion(api_key, df_set, balls_in_bag, model_name):
    """
    Analyzes game data from a set and provides a suggestion for the next shot.
    """
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
        df_set = df_set.sort_values(by=['game_number', 'id'])
        data_summary = df_set.to_string()
        in_bag_summary = ", ".join(balls_in_bag)

        prompt = f"""
        You are an expert bowling coach. Your task is to analyze a bowler's recent performance and provide a strategic suggestion for the next shot, including potential ball changes from the available equipment.

        Analyze the following data, which represents all shots taken in the current set of games:
        {data_summary}

        Here are the bowling balls the bowler has with them right now:
        {in_bag_summary}

        THINGS TO CONSIDER:
        1.  **Look for Patterns Across Games & Balls:** If the bowler is leaving 10-pins with their "Storm Phaze II", but was striking with the "Roto Grip Attention Star" on the same lane earlier, it might be time to switch back.
        2.  **Analyze Ball Reaction:** The `ball_reaction` notes are crucial. If the notes for a specific ball consistently say "breaking early" or "too much hook", it's a strong signal to switch to a different ball from their bag.
        3.  **Suggest Specific Ball Changes:** Your advice must be actionable. Suggest a specific ball *from the list of balls they have with them*. For example: "Your 'Storm Phaze II' is starting to hook too early on the right lane. I recommend switching to your 'Storm IQ Tour' to get more length."

        YOUR TASK:
        Based on all the data, what is your single most important suggestion for the next shot? This could be a move on the lane OR a ball change. Explain your reasoning.
        """
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"An error occurred while getting a suggestion: {e}"

def get_ai_analysis(api_key, df_game, model_name):
    """
    Performs a post-game analysis and provides practice recommendations.
    """
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
        data_summary = df_game.to_string()

        prompt = f"""
        You are an expert bowling coach. Your task is to analyze a completed bowling game and provide practice recommendations.

        Analyze the following game data:
        {data_summary}

        YOUR TASK:
        1.  **Identify Strengths:** What did the bowler do well in this game?
        2.  **Identify Weaknesses:** What was the biggest struggle?
        3.  **Provide Actionable Practice Tips:** Based on the weaknesses, suggest 1-2 specific things to work on.

        Provide a concise, easy-to-read analysis.
        """
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"An error occurred while getting analysis: {e}"

def get_ai_historical_game_plan(api_key, df_combined, user_goal, model_name):
    """
    Strategic analysis over multiple sets. Uses same AI config; prompt is goal-driven.
    """
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
        df_combined = df_combined.sort_values(by=['set_name', 'game_number', 'id'])
        data_summary = df_combined.to_string()

        prompt = f"""
        You are an expert bowling coach. The bowler has selected multiple past sets and is asking for a strategic game plan.

        Their goal or question:
        {user_goal}

        Combined shot data from the selected sets (all games, all shots):
        {data_summary}

        YOUR TASK:
        Provide a clear, actionable game plan. Consider patterns across sets (e.g., ball reaction, lane play, spare issues), and give specific recommendations (ball choice, line, adjustments) for the situation they described. Be concise and strategic.
        """
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"An error occurred while getting the game plan: {e}"


# --- Database Setup ---
con = duckdb.connect(database='bowling.db', read_only=False)
con.execute("CREATE SEQUENCE IF NOT EXISTS seq_shots_id START 1;")
con.execute("""
    CREATE TABLE IF NOT EXISTS shots (
        id INTEGER PRIMARY KEY DEFAULT nextval('seq_shots_id'),
        set_id VARCHAR,
        set_name VARCHAR,
        game_id VARCHAR,
        game_number INTEGER,
        frame_number INTEGER,
        shot_number INTEGER,
        shot_result VARCHAR,
        pins_knocked_down VARCHAR,
        pins_left VARCHAR,
        lane_number VARCHAR,
        bowling_ball VARCHAR,
        arrows_pos INTEGER,
        breakpoint_pos INTEGER,
        ball_reaction VARCHAR,
        shot_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
""")
con.execute("""
    CREATE TABLE IF NOT EXISTS arsenal (
        ball_name VARCHAR PRIMARY KEY
    );
""")

# Pre-populate the arsenal if it's empty
if con.execute("SELECT COUNT(*) FROM arsenal").fetchone()[0] == 0:
    default_balls = [
        "Storm Phaze II - Pin Down", "Storm IQ Tour - Pin Down", "Roto Grip Attention Star - Pin Up",
        "Storm Lightning Blackout - Pin Up", "Storm Absolute - Pin Up", "Brunswick Prism - Pin Up"
    ]
    for ball in default_balls:
        con.execute("INSERT INTO arsenal (ball_name) VALUES (?)", (ball,))
    con.commit()

try:
    con.execute("ALTER TABLE shots ADD COLUMN bowling_ball VARCHAR;")
    con.commit()
except duckdb.Error:
    pass
try:
    con.execute("ALTER TABLE shots ADD COLUMN bowling_center VARCHAR;")
    con.commit()
except duckdb.Error:
    pass
try:
    con.execute("ALTER TABLE shots ADD COLUMN split_name VARCHAR;")
    con.commit()
except duckdb.Error:
    pass

# --- Scoring Logic (per bowl.com / USBC) ---
def get_pins_from_str(pins_str):
    if not pins_str or pins_str == "N/A" or (isinstance(pins_str, float) and pd.isna(pins_str)):
        return []
    s = str(pins_str).strip()
    if not s:
        return []
    return [int(p.strip()) for p in s.replace(',', ' ').split() if p.strip().isdigit()]

def _ball_scores_from_shots(df, shots_ordered):
    """Build list of pins knocked down per delivery for scoring."""
    raw = []
    for s in shots_ordered:
        if s['shot_result'] == 'Strike':
            raw.append(10)
        elif s['shot_result'] == 'Spare':
            shot1 = next((x for x in shots_ordered if x['frame_number'] == s['frame_number'] and x['shot_number'] == 1), None)
            if shot1 is not None:
                left = get_pins_from_str(shot1.get('pins_left'))
                raw.append(10 - len(left) if left is not None else 10)
            else:
                raw.append(0)
        else:
            kn = get_pins_from_str(s.get('pins_knocked_down'))
            raw.append(len(kn) if kn is not None else 0)
    return raw

def calculate_scores(df):
    """Returns (frame_scores[10], total_score, max_possible). Simple frame-by-frame per USBC."""
    if df is None or df.empty:
        return [None] * 10, 0, 300

    shots = df.sort_values(by=['frame_number', 'shot_number', 'id']).to_dict('records')
    balls = _ball_scores_from_shots(df, shots)
    frame_scores = [None] * 10
    total = 0
    i = 0

    for frame in range(10):
        frame_num = frame + 1
        if i >= len(shots):
            break
        cur = shots[i]
        if cur['frame_number'] != frame_num:
            break

        if frame_num < 10:
            if cur['shot_result'] == 'Strike':
                # 10 + next two balls
                if i + 2 < len(balls):
                    total += 10 + balls[i + 1] + balls[i + 2]
                else:
                    break
                frame_scores[frame] = total
                i += 1
            else:
                # two shots in frame
                if i + 1 >= len(shots) or shots[i + 1]['frame_number'] != frame_num:
                    break
                s1, s2 = balls[i], balls[i + 1]
                if shots[i + 1]['shot_result'] == 'Spare':
                    if i + 2 < len(balls):
                        total += 10 + balls[i + 2]
                    else:
                        break
                else:
                    total += s1 + s2
                frame_scores[frame] = total
                i += 2
        else:
            # Frame 10: sum of all balls in frame (1, 2, or 3)
            frame_10_indices = [j for j in range(len(shots)) if shots[j]['frame_number'] == 10]
            frame_10_balls = [balls[j] for j in frame_10_indices]
            total += sum(frame_10_balls)
            frame_scores[frame] = total
            i += len(frame_10_indices)

    # Max possible per USBC: spare frame = 10+next ball (max 20); strike = 10+next two (max 30)
    max_score = 0
    last_done = next((j for j in range(9, -1, -1) if frame_scores[j] is not None), -1)
    if last_done >= 0:
        max_score = frame_scores[last_done]
    start = last_done + 1  # first unscored frame (1-based frame num = start + 1)
    if start < 10:
        # Current incomplete frame: no balls -> 30; one ball strike -> 30; one ball leave -> 20; frame 10 with 2 balls -> 20 or 30
        shots_in_frame = [s for s in shots if s['frame_number'] == start + 1]
        if not shots_in_frame:
            max_score += 30
        elif len(shots_in_frame) == 1:
            if shots_in_frame[0]['shot_result'] == 'Strike':
                max_score += 30
            else:
                max_score += 20  # leave -> best is spare (10+10)
        else:
            # frame 10 with 2 balls (waiting for fill): strike first -> 30, else spare -> 20
            if shots_in_frame[0]['shot_result'] == 'Strike':
                max_score += 30
            else:
                max_score += 20
        for _ in range(start + 1, 10):
            max_score += 30
    return frame_scores, total, max_score if max_score > 0 else 300

def _shot_display_symbol(shot, is_first_shot):
    """Symbol for score sheet: X, /, -, S (split), or count. shot is a dict with shot_result, pins_left, pins_knocked_down."""
    shot_result = shot.get('shot_result') or ''
    pins_left_str = shot.get('pins_left')
    pins = get_pins_from_str(pins_left_str) if pins_left_str else []
    if shot_result == 'Strike':
        return 'X'
    if shot_result == 'Spare':
        return '/'
    if shot_result in ('Leave', 'Leave - Split') and is_first_shot:
        if get_split_name(pins):
            return 'S' + str(10 - len(pins))  # e.g. S8 for 7,10 split
        return str(10 - len(pins)) if pins else '-'
    if shot_result == 'Open':
        kn = get_pins_from_str(shot.get('pins_knocked_down'))
        return str(len(kn)) if kn else '-'
    return '-'

def _html_esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def render_score_sheet(df_game, frame_scores, total_score, max_score):
    """Score sheet as a formatted HTML table: 10 frames with symbols, running total, max at end."""
    if df_game is None or df_game.empty:
        cells = [" "] * 10
        run_cells = [""] * 10
        total_str, max_str = "0", "300"
    else:
        shots = df_game.sort_values(by=['frame_number', 'shot_number', 'id']).to_dict('records')
        by_frame = {}
        for s in shots:
            fn = int(s['frame_number'])
            if fn not in by_frame:
                by_frame[fn] = []
            by_frame[fn].append(s)
        cells = []
        for f in range(1, 11):
            if f not in by_frame:
                cells.append(" ")
                continue
            fr = by_frame[f]
            if len(fr) == 1:
                cells.append(_shot_display_symbol(fr[0], True))
            elif len(fr) == 2:
                cells.append(f"{_shot_display_symbol(fr[0], True)} {_shot_display_symbol(fr[1], False)}")
            else:
                cells.append(f"{_shot_display_symbol(fr[0], True)} {_shot_display_symbol(fr[1], False)} {_shot_display_symbol(fr[2], False)}")
        total_str, max_str = str(total_score), str(max_score)
        run_cells = [str(frame_scores[f - 1]) if f - 1 < len(frame_scores) and frame_scores[f - 1] is not None else "" for f in range(1, 11)]

    header = "".join(f"<th style='border:1px solid #ccc;padding:6px 8px;color:#1a1a1a;background:#e0e0e0;'>{f}</th>" for f in range(1, 11)) + "<th style='border:1px solid #ccc;padding:6px 8px;color:#1a1a1a;background:#e0e0e0;'>Total</th><th style='border:1px solid #ccc;padding:6px 8px;color:#1a1a1a;background:#e0e0e0;'>Max</th>"
    row1 = "".join(f"<td style='border:1px solid #ccc;padding:6px 8px;text-align:center;'>{_html_esc(c)}</td>" for c in cells) + f"<td style='border:1px solid #ccc;padding:6px 8px;text-align:center;font-weight:bold;'>{total_str}</td><td style='border:1px solid #ccc;padding:6px 8px;text-align:center;'>{max_str}</td>"
    row2 = "".join(f"<td style='border:1px solid #ccc;padding:6px 8px;text-align:center;'>{_html_esc(r)}</td>" for r in run_cells) + "<td></td><td></td>"
    st.markdown(
        f"<table style='border-collapse:collapse;margin:8px 0;'>"
        f"<thead><tr>{header}</tr></thead>"
        f"<tbody><tr>{row1}</tr><tr>{row2}</tr></tbody>"
        f"</table>",
        unsafe_allow_html=True,
    )


# --- Azure Integration ---
def get_azure_client():
    try:
        container_name = st.secrets.get("AZURE_STORAGE_CONTAINER_NAME")
        connection_string = st.secrets.get("AZURE_STORAGE_CONNECTION_STRING")
        account_name = st.secrets.get("AZURE_STORAGE_ACCOUNT_NAME")

        if not container_name:
            st.error("Azure secret `AZURE_STORAGE_CONTAINER_NAME` not found.")
            return None

        if connection_string:
            return BlobServiceClient.from_connection_string(connection_string)
        elif account_name:
            return BlobServiceClient(account_url=f"https://{account_name}.blob.core.windows.net", credential=DefaultAzureCredential())
        else:
            st.error("Azure credentials not found. Please add `AZURE_STORAGE_CONNECTION_STRING` or `AZURE_STORAGE_ACCOUNT_NAME`.")
            return None
    except Exception as e:
        st.error(f"Failed to connect to Azure: {e}")
        return None

def get_storage_account_name_from_secrets():
    """Parse storage account name from connection string or use AZURE_STORAGE_ACCOUNT_NAME. Returns None if not found."""
    try:
        connection_string = st.secrets.get("AZURE_STORAGE_CONNECTION_STRING")
        if connection_string:
            for part in connection_string.split(";"):
                part = part.strip()
                if part.lower().startswith("accountname="):
                    return part.split("=", 1)[1].strip()
        return st.secrets.get("AZURE_STORAGE_ACCOUNT_NAME")
    except Exception:
        return None

def upload_set_to_azure(con, set_id):
    blob_service_client = get_azure_client()
    if not blob_service_client: return

    try:
        container_name = st.secrets["AZURE_STORAGE_CONTAINER_NAME"]
        df = con.execute("SELECT * FROM shots WHERE set_id = ?", [set_id]).fetchdf()
        if df.empty:
            st.warning("No data in this set to save.")
            return

        set_name = df['set_name'].iloc[0]
        bowling_center = "Unknown"
        if 'bowling_center' in df.columns and pd.notna(df['bowling_center'].iloc[0]) and str(df['bowling_center'].iloc[0]).strip():
            bowling_center = str(df['bowling_center'].iloc[0]).strip().replace(' ', '_')
        csv_buffer = io.StringIO()
        df.to_csv(csv_buffer, index=False)
        
        blob_name = f"set-{set_name.replace(' ', '_')}-{bowling_center}-{set_id}.csv"
        blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)
        blob_client.upload_blob(csv_buffer.getvalue(), overwrite=True)

        # Option A: one blob per set — delete any other blob whose name contains this set_id
        container_client = blob_service_client.get_container_client(container_name)
        for b in container_client.list_blobs(name_starts_with="set-"):
            if set_id in b.name and b.name != blob_name:
                container_client.delete_blob(b.name)

        st.success(f"Set '{set_name}' saved successfully to Azure.")
    except Exception as e:
        st.error(f"An unexpected error occurred during upload: {e}")

def download_and_load_set(blob_name):
    blob_service_client = get_azure_client()
    if not blob_service_client: return

    try:
        container_name = st.secrets["AZURE_STORAGE_CONTAINER_NAME"]
        blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)
        
        downloader = blob_client.download_blob()
        df = pd.read_csv(io.BytesIO(downloader.readall()))

        if 'set_id' not in df.columns:
            st.error("Downloaded file is not a valid set file.")
            return

        if 'bowling_center' not in df.columns:
            df['bowling_center'] = ''
        if 'split_name' not in df.columns:
            df['split_name'] = None

        set_id_to_load = df['set_id'].iloc[0]
        con.execute("DELETE FROM shots WHERE set_id = ?", (set_id_to_load,))
        
        con.register('df_to_insert', df)
        con.execute('INSERT INTO shots SELECT * FROM df_to_insert')
        con.unregister('df_to_insert')
        con.commit()

        st.success(f"Successfully loaded set '{df['set_name'].iloc[0]}'.")
        st.session_state.set_id = set_id_to_load
        st.session_state.state_restored = False
        st.rerun()

    except Exception as e:
        st.error(f"Failed to download or load set: {e}")

def _derive_shot_result_and_pins_from_pins_left(row, edited_df):
    """Given a row and the full edited df, derive shot_result and pins_knocked_down from pins_left."""
    frame = row.get('frame_number')
    shot = row.get('shot_number')
    pins_left_str = row.get('pins_left')
    pins_left = get_pins_from_str(pins_left_str) if pins_left_str is not None else []
    if shot == 1:
        if not pins_left or len(pins_left) == 0:
            return 'Strike', '1, 2, 3, 4, 5, 6, 7, 8, 9, 10'
        return 'Leave', ', '.join(str(p) for p in range(1, 11) if p not in pins_left)
    else:
        shot1_row = edited_df[(edited_df['game_id'] == row['game_id']) & (edited_df['frame_number'] == frame) & (edited_df['shot_number'] == 1)]
        if shot1_row.empty:
            return row.get('shot_result'), row.get('pins_knocked_down')
        pins_after_1 = get_pins_from_str(shot1_row.iloc[0].get('pins_left'))
        if not pins_after_1:
            pins_after_1 = list(range(1, 11))
        if not pins_left or len(pins_left) == 0:
            return 'Spare', ', '.join(str(p) for p in pins_after_1)
        knocked_2 = [p for p in pins_after_1 if p not in pins_left]
        return 'Open', ', '.join(str(p) for p in knocked_2) if knocked_2 else 'N/A'

def apply_edits_to_db(con, edited_df):
    """Persist edited dataframe to DB. Derives shot_result/pins_knocked_down/split_name from pins_left for consistency."""
    if edited_df is None or edited_df.empty:
        return
    for _, row in edited_df.iterrows():
        sid = row.get('id')
        if pd.isna(sid) or sid is None:
            continue
        shot_result, pins_knocked_down = _derive_shot_result_and_pins_from_pins_left(row, edited_df)
        pins_left = row.get('pins_left')
        pins_left_list = get_pins_from_str(pins_left) if pins_left is not None else []
        split_name_val = None
        if shot_result == "Leave" and row.get('shot_number') == 1 and pins_left_list:
            sn = get_split_name(pins_left_list)
            if sn:
                shot_result = "Leave - Split"
                split_name_val = sn
        if pins_left is None or (isinstance(pins_left, float) and pd.isna(pins_left)):
            pins_left_str = ''
        else:
            pins_left_str = str(pins_left).strip()
            if pins_left_str.lower() == 'nan':
                pins_left_str = ''
        lane_number = row.get('lane_number')
        bowling_ball = row.get('bowling_ball')
        arrows_pos = row.get('arrows_pos')
        breakpoint_pos = row.get('breakpoint_pos')
        ball_reaction = row.get('ball_reaction')
        con.execute("""
            UPDATE shots SET shot_result=?, pins_knocked_down=?, pins_left=?, lane_number=?, bowling_ball=?, arrows_pos=?, breakpoint_pos=?, ball_reaction=?, split_name=?
            WHERE id=?
        """, (shot_result, pins_knocked_down, pins_left_str, lane_number, bowling_ball, arrows_pos, breakpoint_pos, ball_reaction, split_name_val, int(sid)))
    con.commit()

def download_blob_to_dataframe(blob_name):
    """Download a single set blob from Azure and return as DataFrame, or None on error."""
    blob_service_client = get_azure_client()
    if not blob_service_client:
        return None
    try:
        container_name = st.secrets["AZURE_STORAGE_CONTAINER_NAME"]
        blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)
        downloader = blob_client.download_blob()
        return pd.read_csv(io.BytesIO(downloader.readall()))
    except Exception:
        return None


# --- Main Application ---
st.set_page_config(layout="wide")
st.title("🎳 PinDeck: Bowling Set Tracker")

def initialize_set(set_id=None, set_name=None, bowling_center=None):
    if set_id is None:
        st.session_state.set_id = f"set-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
        st.session_state.set_name = set_name or f"League {datetime.datetime.now().strftime('%m-%d-%y')}"
        st.session_state.bowling_center = (bowling_center or "").strip()
        st.session_state.game_id = f"game-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
        st.session_state.game_number = 1
        st.session_state.current_frame = 1
        st.session_state.current_shot = 1
        st.session_state.pins_left_after_first_shot = []
        st.session_state.starting_lane = "Left Lane"
        st.session_state.game_over = False
    else:
        st.session_state.set_id = set_id
        st.session_state.set_name = set_name
        first_shot = con.execute("SELECT bowling_center FROM shots WHERE set_id = ? LIMIT 1", [set_id]).fetchone()
        st.session_state.bowling_center = (first_shot[0] or "") if first_shot and first_shot[0] else ""

        latest_game = con.execute("SELECT game_id, game_number FROM shots WHERE set_id = ? ORDER BY game_number DESC, id DESC LIMIT 1", [set_id]).fetchone()
        if latest_game:
            st.session_state.game_id, st.session_state.game_number = latest_game
            restore_game_state()
        else:
            st.session_state.game_id = f"game-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
            st.session_state.game_number = 1
            st.session_state.current_frame = 1
            st.session_state.current_shot = 1
            st.session_state.pins_left_after_first_shot = []
            st.session_state.starting_lane = "Left Lane"
            st.session_state.game_over = False

def restore_game_state():
    try:
        latest_shot = con.execute("SELECT * FROM shots WHERE game_id = ? ORDER BY id DESC LIMIT 1", [st.session_state.game_id]).fetchone()
        if not latest_shot:
            st.session_state.current_frame = 1
            st.session_state.current_shot = 1
            st.session_state.pins_left_after_first_shot = []
            st.session_state.starting_lane = "Left Lane"
            st.session_state.game_over = False
            return

        (id, set_id, set_name, game_id, game_number, frame, shot, 
         shot_result, _, pins_left_str, _, *__) = latest_shot

        if frame is None or shot is None:
            raise ValueError("Corrupted data in last shot.")

        next_frame, next_shot = frame, shot
        pins_left = get_pins_from_str(pins_left_str)
        game_over = False

        if frame < 10:
            if shot == 2 or shot_result == "Strike":
                next_frame += 1
                next_shot = 1
                pins_left = []
            else:
                next_shot = 2
        else:
            shots_in_frame10 = con.execute("SELECT shot_number, shot_result FROM shots WHERE game_id = ? AND frame_number = 10", [game_id]).fetchall()
            shot1_res = shots_in_frame10[0][1] if len(shots_in_frame10) > 0 else ''
            
            if shot == 1:
                next_shot = 2
                if shot_result == "Strike": pins_left = []
            elif shot == 2:
                if shot1_res == "Strike" or shot_result == "Spare":
                    next_shot = 3
                    pins_left = []
                else: game_over = True
            else:
                game_over = True

        st.session_state.current_frame = next_frame
        st.session_state.current_shot = next_shot
        st.session_state.pins_left_after_first_shot = pins_left
        st.session_state.game_over = game_over
        
        first_shot_of_game = con.execute("SELECT lane_number FROM shots WHERE game_id = ? AND frame_number = 1 AND shot_number = 1", [game_id]).fetchone()
        st.session_state.starting_lane = first_shot_of_game[0] if first_shot_of_game else "Left Lane"
        
    except Exception as e:
        st.warning(f"Could not restore game state due to an error: {e}. Starting a fresh game.")
        st.session_state.current_frame = 1
        st.session_state.current_shot = 1
        st.session_state.pins_left_after_first_shot = []
        st.session_state.game_over = False

if 'set_id' not in st.session_state:
    initialize_set()

# --- Sidebar ---
st.sidebar.header("Set Management")

all_sets_from_db = con.execute("SELECT DISTINCT set_id, set_name FROM shots ORDER BY set_name DESC").fetchall()
set_map = {s[0]: s[1] for s in all_sets_from_db}
if st.session_state.get('set_id') not in set_map and st.session_state.get('set_name'):
    set_map[st.session_state.set_id] = st.session_state.set_name

if set_map:
    try:
        current_set_index = list(set_map.keys()).index(st.session_state.set_id)
    except (ValueError, KeyError):
        current_set_index = 0
    
    selected_set_name = st.sidebar.selectbox("Select Set", options=list(set_map.values()), index=current_set_index)
    if set_map and selected_set_name in set_map.values():
        selected_set_id = [sid for sid, name in set_map.items() if name == selected_set_name][0]

        if selected_set_id != st.session_state.set_id:
            initialize_set(selected_set_id, selected_set_name)
            st.rerun()

st.sidebar.caption("Start New Set (bowling center required)")
new_set_bowling_center = st.sidebar.text_input("Bowling center name", key="new_set_bowling_center", placeholder="e.g. Riverside Lanes")
if st.sidebar.button("Start New Set", disabled=not (new_set_bowling_center and str(new_set_bowling_center).strip())):
    today_str = datetime.datetime.now().strftime('%m-%d-%y')
    base_name = f"League {today_str}"
    existing_sets_today = con.execute("SELECT set_name FROM shots WHERE set_name LIKE ? ORDER BY set_name DESC", [f"{base_name}%"]).fetchall()
    
    next_seq = 1
    if existing_sets_today:
        last_set_name = existing_sets_today[0][0]
        match = re.search(r'_(\d+)$', last_set_name)
        if match: next_seq = int(match.group(1)) + 1
        else: next_seq = 2

    new_set_name = f"{base_name}_{next_seq}" if next_seq > 1 else base_name
    initialize_set(set_name=new_set_name, bowling_center=str(new_set_bowling_center).strip())
    st.rerun()

new_name = st.sidebar.text_input("Rename Current Set", value=st.session_state.get('set_name', ''))
if st.sidebar.button("Rename Set"):
    if new_name:
        con.execute("UPDATE shots SET set_name = ? WHERE set_id = ?", (new_name, st.session_state.set_id))
        con.commit()
        st.session_state.set_name = new_name
        st.rerun()

with st.sidebar.expander("🎳 Manage Arsenal"):
    st.markdown("**Your Full Arsenal**")
    arsenal = [row[0] for row in con.execute("SELECT ball_name FROM arsenal ORDER BY ball_name").fetchall()]
    
    if 'balls_in_bag' not in st.session_state:
        st.session_state.balls_in_bag = arsenal

    st.multiselect(
        "Select balls in your bag for this session:", 
        options=arsenal, 
        key="balls_in_bag"
    )

    new_ball_name = st.text_input("Add New Ball to Arsenal")
    if st.button("Add Ball"):
        if new_ball_name and new_ball_name not in arsenal:
            con.execute("INSERT INTO arsenal (ball_name) VALUES (?)", (new_ball_name,))
            con.commit()
            st.success(f"Added '{new_ball_name}' to your arsenal.")
            st.session_state.balls_in_bag.append(new_ball_name)
            st.rerun()
        elif not new_ball_name:
            st.warning("Please enter a ball name.")
        else:
            st.warning(f"'{new_ball_name}' is already in your arsenal.")

with st.sidebar.expander("☁️ Azure Cloud Storage"):
    if st.button("Save Current Set to Azure"):
        upload_set_to_azure(con, st.session_state.set_id)
    
    azure_client = get_azure_client()
    if azure_client:
        try:
            container_name = st.secrets["AZURE_STORAGE_CONTAINER_NAME"]
            container_client = azure_client.get_container_client(container_name)
            blob_list = [b.name for b in container_client.list_blobs() if b.name.startswith('set-')]
            if blob_list:
                selected_blob = st.selectbox("Load Set from Azure", options=blob_list)
                if st.button("Download and Load Set"):
                    download_and_load_set(selected_blob)
            else:
                st.write("No sets found in Azure.")
        except Exception as e:
            st.error(f"Could not list Azure blobs: {e}")

with st.sidebar.expander("📜 Historical Analysis"):
    st.caption("Select saved sets (newest first) and ask for a game plan.")
    azure_client_ha = get_azure_client()
    historical_blob_options = []
    if azure_client_ha:
        try:
            container_name = st.secrets.get("AZURE_STORAGE_CONTAINER_NAME")
            if container_name:
                container_client = azure_client_ha.get_container_client(container_name)
                blobs = [b for b in container_client.list_blobs() if b.name.startswith('set-')]
                def _blob_sort_key(b):
                    t = b.last_modified
                    if t is None:
                        return (0, datetime.datetime.min)
                    return (1, t)
                blobs_sorted = sorted(blobs, key=_blob_sort_key, reverse=True)
                historical_blob_options = [b.name for b in blobs_sorted]
        except Exception:
            pass
    if historical_blob_options:
        st.multiselect("Select sets", options=historical_blob_options, key="historical_sets", default=[])
        st.text_area("Your goal or question", key="historical_goal", placeholder="e.g. Look at my last 4 sets and give me a game plan for tonight...", height=80)
        if st.button("Get game plan", key="btn_historical_plan"):
            st.session_state.run_historical_plan = True
            st.rerun()
    else:
        st.info("Save sets to Azure first, then they will appear here.")

with st.sidebar.expander("🤖 AI Settings"):
    model_options = {
        "Gemini 2.5 Flash (Recommended)": "gemini-2.5-flash",
        "Gemini 1.5 Flash (Economical)": "gemini-1.5-flash"
    }
    selected_model_label = st.selectbox("Select AI Model", options=list(model_options.keys()))
    selected_model_id = model_options[selected_model_label]
    st.session_state.selected_model_id = selected_model_id

with st.sidebar.expander("⚠️ Danger Zone"):
    storage_account_name = get_storage_account_name_from_secrets()
    if storage_account_name:
        st.markdown("[**Open Azure Portal**](https://portal.azure.com) (sign in to view your Storage account)")
        st.caption("Storage account name (copy to search in portal):")
        st.code(storage_account_name, language=None)
    else:
        st.caption("Add AZURE_STORAGE_CONNECTION_STRING or AZURE_STORAGE_ACCOUNT_NAME to secrets to see a link here.")
    if st.button("Delete Current Set"):
        con.execute("DELETE FROM shots WHERE set_id = ?", (st.session_state.set_id,))
        con.commit()
        st.success(f"Set '{st.session_state.set_name}' has been deleted.")
        initialize_set()
        st.rerun()

# --- Save Edits (run before refetch so next run sees updated data) ---
def _parse_game_frame_shot(gfs):
    """Parse 'game-frame-shot' string to (game_number, frame_number, shot_number)."""
    if pd.isna(gfs) or gfs is None or str(gfs).strip() == "":
        return None, None, None
    parts = str(gfs).strip().split("-")
    if len(parts) != 3:
        return None, None, None
    try:
        return int(parts[0]), int(parts[1]), int(parts[2])
    except (ValueError, TypeError):
        return None, None, None

if st.session_state.get('save_edits_clicked'):
    edited_data = st.session_state.get('pending_save_edits')
    if edited_data is None:
        edited_data = st.session_state.get('edited_set_data')
    set_id_for_save = st.session_state.get('save_edits_set_id') or st.session_state.get('set_id')
    did_save = False
    if edited_data is not None and isinstance(edited_data, pd.DataFrame) and not edited_data.empty and set_id_for_save and "game-frame-shot" in edited_data.columns:
        full_df = con.execute("SELECT * FROM shots WHERE set_id = ?", [set_id_for_save]).fetchdf()
        full_df = full_df.sort_values(by=['game_number', 'frame_number', 'shot_number', 'id']).reset_index(drop=True)
        if not full_df.empty:
            merged = full_df.copy()
            edit_cols = [c for c in edited_data.columns if c != "game-frame-shot" and c in merged.columns]
            for idx, full_row in merged.iterrows():
                g, f, s = int(full_row["game_number"]), int(full_row["frame_number"]), int(full_row["shot_number"])
                for ed_idx, edit_row in edited_data.iterrows():
                    eg, ef, es = _parse_game_frame_shot(edit_row.get("game-frame-shot"))
                    if (eg, ef, es) == (g, f, s):
                        for col in edit_cols:
                            merged.at[idx, col] = edit_row[col]
                        break
            apply_edits_to_db(con, merged)
            did_save = True
    elif edited_data is not None and not isinstance(edited_data, pd.DataFrame):
        try:
            df_edit = pd.DataFrame(edited_data)
            if not df_edit.empty and "game-frame-shot" in df_edit.columns and set_id_for_save:
                full_df = con.execute("SELECT * FROM shots WHERE set_id = ?", [set_id_for_save]).fetchdf()
                full_df = full_df.sort_values(by=['game_number', 'frame_number', 'shot_number', 'id']).reset_index(drop=True)
                if not full_df.empty:
                    merged = full_df.copy()
                    edit_cols = [c for c in df_edit.columns if c != "game-frame-shot" and c in merged.columns]
                    for idx, full_row in merged.iterrows():
                        g, f, s = int(full_row["game_number"]), int(full_row["frame_number"]), int(full_row["shot_number"])
                        for _, edit_row in df_edit.iterrows():
                            eg, ef, es = _parse_game_frame_shot(edit_row.get("game-frame-shot"))
                            if (eg, ef, es) == (g, f, s):
                                for col in edit_cols:
                                    merged.at[idx, col] = edit_row[col]
                                break
                    apply_edits_to_db(con, merged)
                    did_save = True
        except Exception:
            pass
    if 'save_edits_clicked' in st.session_state:
        del st.session_state['save_edits_clicked']
    if 'save_edits_set_id' in st.session_state:
        del st.session_state['save_edits_set_id']
    if 'pending_save_edits' in st.session_state:
        del st.session_state['pending_save_edits']
    if 'edited_set_data' in st.session_state:
        del st.session_state['edited_set_data']
    if did_save:
        st.session_state.edits_saved_message = True
    st.rerun()

# --- Game Selection & Data Fetching ---
st.sidebar.header("Game Management")
df_set = con.execute("SELECT * FROM shots WHERE set_id = ?", [st.session_state.set_id]).fetchdf()
if st.session_state.get('edits_saved_message'):
    st.success("Edits saved. Score sheet and totals updated.")
    del st.session_state['edits_saved_message']

games_in_set = df_set['game_number'].unique()
games_in_set.sort()
game_map = {f"Game {g}": g for g in games_in_set}
if st.session_state.game_number not in game_map.values():
    game_map[f"Game {st.session_state.game_number}"] = st.session_state.game_number

selected_game_name = st.sidebar.selectbox("Select Game", options=list(game_map.keys()), index=list(game_map.values()).index(st.session_state.game_number))
selected_game_number = game_map[selected_game_name]

if selected_game_number != st.session_state.game_number:
    st.session_state.game_number = selected_game_number
    game_id_res = df_set[df_set['game_number'] == selected_game_number]['game_id'].iloc[0]
    st.session_state.game_id = game_id_res
    restore_game_state()
    st.rerun()

if st.sidebar.button("Start New Game in Set"):
    new_game_num = (max(games_in_set) if games_in_set.size > 0 else 0) + 1
    # Next game starts on the opposite lane from the previous game (lane 1 = left, 2 = right)
    prev_starting_lane = "Left Lane"
    if games_in_set.size > 0:
        prev_game_num = int(max(games_in_set))
        prev_first = con.execute("SELECT lane_number FROM shots WHERE set_id = ? AND game_number = ? AND frame_number = 1 AND shot_number = 1", [st.session_state.set_id, prev_game_num]).fetchone()
        if prev_first and prev_first[0]:
            prev_starting_lane = prev_first[0]
    st.session_state.starting_lane = "Right Lane" if prev_starting_lane == "Left Lane" else "Left Lane"
    st.session_state.game_id = f"game-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
    st.session_state.game_number = new_game_num
    st.session_state.current_frame = 1
    st.session_state.current_shot = 1
    st.session_state.pins_left_after_first_shot = []
    st.session_state.game_over = False
    st.rerun()

df_current_game = df_set[df_set['game_number'] == st.session_state.game_number] if not df_set.empty else pd.DataFrame()

# --- Scoring Display ---
frame_scores, total_score, max_score = calculate_scores(df_current_game)
st.sidebar.header(f"Game {st.session_state.game_number} Score")
st.sidebar.metric("Total Score", total_score)
if not st.session_state.game_over:
    st.sidebar.metric("Max Possible", max_score)

# --- Shot Input Area ---
st.header(f"Entering Data for: {st.session_state.set_name} - Game {st.session_state.game_number}")
if st.session_state.game_over:
    st.success("🎉 Game Over! Start a new game to continue.")
else:
    st.subheader(f"Frame {st.session_state.current_frame} - Shot {st.session_state.current_shot}")
    
    col1, col2 = st.columns(2)
    with col1:
        shot_result_options = []
        if st.session_state.current_frame == 10:
            if st.session_state.current_shot == 1: shot_result_options = ["Strike", "Leave"]
            elif st.session_state.current_shot == 2:
                shot1_res_df = df_current_game[(df_current_game['frame_number'] == 10) & (df_current_game['shot_number'] == 1)]
                shot1_res = shot1_res_df['shot_result'].iloc[0] if not shot1_res_df.empty else ''
                shot_result_options = ["Strike", "Leave"] if shot1_res == 'Strike' else ["Spare", "Open"]
            else: shot_result_options = ["Strike", "Leave", "Open"]
        else:
            shot_result_options = ["Strike", "Leave"] if st.session_state.current_shot == 1 else ["Spare", "Open"]
        st.radio("Shot Result", shot_result_options, key="shot_result", horizontal=True)
    
    with col2:
        if st.session_state.current_frame == 1 and st.session_state.current_shot == 1:
            st.selectbox("Starting Lane", ["Left Lane", "Right Lane"], key="starting_lane")
        
        if 'starting_lane' not in st.session_state or not st.session_state.starting_lane:
            first_shot_db = con.execute("SELECT lane_number FROM shots WHERE game_id = ? AND frame_number = 1 AND shot_number = 1", [st.session_state.game_id]).fetchone()
            st.session_state.starting_lane = first_shot_db[0] if first_shot_db else "Left Lane"

        is_odd_frame = st.session_state.current_frame % 2 != 0
        starts_on_left = st.session_state.starting_lane == "Left Lane"
        lane_number = st.session_state.starting_lane if is_odd_frame else ("Right Lane" if starts_on_left else "Left Lane")
        st.markdown(f"**Current Lane:** {lane_number}")

    # Ball Selection
    balls_in_bag = st.session_state.get('balls_in_bag', [])
    last_used_ball = st.session_state.get('last_used_ball')
    default_index = 0
    if last_used_ball and last_used_ball in balls_in_bag:
        default_index = balls_in_bag.index(last_used_ball)
    st.selectbox("Bowling Ball", options=balls_in_bag, key="bowling_ball", index=default_index)

    if st.session_state.current_shot == 1 or (st.session_state.current_frame == 10 and st.session_state.current_shot > 1):
        st.subheader("Ball Trajectory")
        st.selectbox("Position at Arrows", options=list(range(1, 40)), index=16, key="arrows_pos")
        st.selectbox("Position at Breakpoint", options=list(range(1, 40)), index=9, key="breakpoint_pos")

    st.text_input("Ball Reaction", key="ball_reaction")
    
    st.subheader("Pins Left Standing")
    st.code("""
    7   8   9   10
      4   5   6
        2   3
          1
    """, language=None)

    is_spare_or_strike = st.session_state.shot_result in ["Spare", "Strike"]
    
    if st.session_state.current_shot == 1:
        options = list(range(1, 11))
        help_text = "Select the pins left standing after your first shot."
    else:
        options = st.session_state.get('pins_left_after_first_shot', [])
        help_text = "Select the pins still standing to record an open frame."

    st.multiselect(
        "Pins Left Standing",
        options=options,
        key="pins_left_multiselect",
        help=help_text,
        disabled=is_spare_or_strike
    )

    def submit_shot():
        use_trajectory = st.session_state.current_shot == 1 or (st.session_state.current_frame == 10 and st.session_state.current_shot > 1)
        arrows = st.session_state.arrows_pos if use_trajectory else None
        breakpoint = st.session_state.breakpoint_pos if use_trajectory else None
        
        shot_res = st.session_state.shot_result
        pins_left_standing = st.session_state.pins_left_multiselect
        pins_knocked_down_str = "N/A"

        if st.session_state.current_shot == 1:
            if shot_res == "Strike":
                st.session_state.pins_left_after_first_shot = []
                pins_knocked_down_str = "1, 2, 3, 4, 5, 6, 7, 8, 9, 10"
            else:
                st.session_state.pins_left_after_first_shot = pins_left_standing
                knocked_down = [p for p in range(1, 11) if p not in pins_left_standing]
                pins_knocked_down_str = ", ".join(map(str, knocked_down))
        else:
            prev_pins_left = st.session_state.get('pins_left_after_first_shot', [])
            if shot_res == "Spare":
                pins_knocked_down_str = ", ".join(map(str, prev_pins_left))
            else:
                knocked_down = [p for p in prev_pins_left if p not in pins_left_standing]
                pins_knocked_down_str = ", ".join(map(str, knocked_down))

        pins_left_standing_str = ", ".join(map(str, pins_left_standing))
        st.session_state.last_used_ball = st.session_state.bowling_ball

        split_name_val = None
        if shot_res == "Leave" and st.session_state.current_shot == 1 and pins_left_standing:
            split_name_val = get_split_name(pins_left_standing)
            if split_name_val:
                shot_res = "Leave - Split"

        bowling_center = getattr(st.session_state, 'bowling_center', '') or ''
        ins_args = (
            str(st.session_state.set_id),
            str(st.session_state.set_name),
            str(st.session_state.game_id),
            int(st.session_state.game_number),
            int(st.session_state.current_frame),
            int(st.session_state.current_shot),
            str(shot_res),
            str(pins_knocked_down_str),
            str(pins_left_standing_str),
            str(lane_number) if lane_number else None,
            str(st.session_state.bowling_ball) if st.session_state.bowling_ball else None,
            int(arrows) if arrows is not None and not (isinstance(arrows, float) and pd.isna(arrows)) else None,
            int(breakpoint) if breakpoint is not None and not (isinstance(breakpoint, float) and pd.isna(breakpoint)) else None,
            str(st.session_state.ball_reaction) if st.session_state.ball_reaction else None,
            str(bowling_center) if bowling_center else None,
            str(split_name_val) if split_name_val else None,
        )
        con.execute(
            "INSERT INTO shots (set_id, set_name, game_id, game_number, frame_number, shot_number, shot_result, pins_knocked_down, pins_left, lane_number, bowling_ball, arrows_pos, breakpoint_pos, ball_reaction, bowling_center, split_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ins_args,
        )
        con.commit()
        
        if st.session_state.current_frame < 10:
            if st.session_state.current_shot == 2 or shot_res == "Strike":
                st.session_state.current_frame += 1
                st.session_state.current_shot = 1
                st.session_state.pins_left_after_first_shot = []
            else:
                st.session_state.current_shot = 2
        else:
            shot1_res_df = df_current_game[(df_current_game['frame_number'] == 10) & (df_current_game['shot_number'] == 1)]
            shot1_res = shot1_res_df['shot_result'].iloc[0] if not shot1_res_df.empty else ''
            if st.session_state.current_shot == 1:
                st.session_state.current_shot = 2
                if shot_res == "Strike": st.session_state.pins_left_after_first_shot = []
            elif st.session_state.current_shot == 2:
                if shot1_res == "Strike" or shot_res == "Spare":
                    st.session_state.current_shot = 3
                    st.session_state.pins_left_after_first_shot = []
                else: st.session_state.game_over = True
            else:
                st.session_state.game_over = True
        
        st.session_state.pins_left_multiselect = []
        st.session_state.ball_reaction = ""
        
    st.button("Submit Shot", use_container_width=True, on_click=submit_shot)

# --- Score Sheet (current game) ---
st.subheader(f"Score sheet — Game {st.session_state.game_number}")
st.caption("Use the sidebar to select a game to compare.")
render_score_sheet(df_current_game, frame_scores, total_score, max_score)

# --- Analytical Dashboard (editable grid) ---
st.header(f"📊 Data for Set: {st.session_state.set_name}")
if not df_set.empty:
    full_sorted = df_set.sort_values(by=['game_number', 'frame_number', 'shot_number', 'id']).reset_index(drop=True)
    visible_cols = [
        "shot_result", "pins_left", "lane_number", "bowling_ball", "arrows_pos", "breakpoint_pos",
        "ball_reaction", "split_name", "bowling_center", "set_name", "shot_timestamp"
    ]
    visible_cols = [c for c in visible_cols if c in full_sorted.columns]
    display_visible = full_sorted[visible_cols].copy()
    display_visible = display_visible.iloc[::-1].reset_index(drop=True)
    gfs = full_sorted.iloc[::-1].reset_index(drop=True).apply(
        lambda r: f"{int(r['game_number'])}-{int(r['frame_number'])}-{int(r['shot_number'])}", axis=1
    )
    display_visible.insert(0, "game-frame-shot", gfs)
    # Coerce dtypes for Streamlit data_editor compatibility
    for col in display_visible.columns:
        if col == "shot_timestamp":
            if display_visible[col].dtype == object:
                display_visible[col] = pd.to_datetime(display_visible[col], errors="coerce")
            continue
        if display_visible[col].dtype == object or pd.api.types.is_string_dtype(display_visible[col]):
            display_visible[col] = display_visible[col].fillna("").astype(str).replace("nan", "")
        elif pd.api.types.is_integer_dtype(display_visible[col]) and display_visible[col].isna().any():
            display_visible[col] = display_visible[col].astype(float)
    # lane_number is VARCHAR (e.g. "Left Lane"); arrows/breakpoint can be int or float (NaN)
    column_config = {
        "game-frame-shot": st.column_config.TextColumn("game-frame-shot", disabled=True),
        "set_name": st.column_config.TextColumn("set_name", disabled=True),
        "shot_timestamp": st.column_config.DatetimeColumn("shot_timestamp", disabled=True),
        "shot_result": st.column_config.TextColumn("shot_result", disabled=False),
        "pins_left": st.column_config.TextColumn("pins_left", disabled=False),
        "lane_number": st.column_config.TextColumn("lane_number", disabled=False),
        "bowling_ball": st.column_config.TextColumn("bowling_ball", disabled=False),
        "arrows_pos": st.column_config.NumberColumn("arrows_pos", disabled=False),
        "breakpoint_pos": st.column_config.NumberColumn("breakpoint_pos", disabled=False),
        "ball_reaction": st.column_config.TextColumn("ball_reaction", disabled=False),
        "split_name": st.column_config.TextColumn("Split", disabled=True),
        "bowling_center": st.column_config.TextColumn("bowling_center", disabled=True),
    }
    column_config = {k: v for k, v in column_config.items() if k in display_visible.columns}
    st.caption("Edit cells as needed. Changing 'pins_left' will auto-update shot_result and recalculate scores. **Click outside the edited cell or press Enter before clicking Save edits.**")

    with st.form("save_edits_form"):
        edited_visible = st.data_editor(
            display_visible,
            key="edited_set_data",
            use_container_width=True,
            hide_index=True,
            column_config=column_config,
        )
        submitted = st.form_submit_button("Save edits")

    if submitted and edited_visible is not None and not edited_visible.empty and "game-frame-shot" in edited_visible.columns:
        set_id_for_save = st.session_state.get("set_id")
        if set_id_for_save:
            full_df = con.execute("SELECT * FROM shots WHERE set_id = ?", [set_id_for_save]).fetchdf()
            full_df = full_df.sort_values(by=['game_number', 'frame_number', 'shot_number', 'id']).reset_index(drop=True)
            if not full_df.empty:
                merged = full_df.copy()
                edit_cols = [c for c in edited_visible.columns if c != "game-frame-shot" and c in merged.columns]
                for idx, full_row in merged.iterrows():
                    g, f, s = int(full_row["game_number"]), int(full_row["frame_number"]), int(full_row["shot_number"])
                    for _, edit_row in edited_visible.iterrows():
                        eg, ef, es = _parse_game_frame_shot(edit_row.get("game-frame-shot"))
                        if (eg, ef, es) == (g, f, s):
                            for col in edit_cols:
                                merged.at[idx, col] = edit_row[col]
                            break
                apply_edits_to_db(con, merged)
                st.success("Edits saved. Score sheet and totals will update.")
                st.rerun()
else:
    st.info("No shots submitted for this set yet.")

# --- Historical Analysis result (run when requested) ---
if st.session_state.get('run_historical_plan'):
    st.session_state.run_historical_plan = False
    selected_blobs = st.session_state.get('historical_sets', [])
    goal = (st.session_state.get('historical_goal') or '').strip()
    if not selected_blobs or not goal:
        st.warning("Select at least one set and enter your goal.")
        st.session_state.historical_plan_result = None
    else:
        with st.spinner("Downloading sets and building your game plan..."):
            dfs = []
            for blob_name in selected_blobs:
                d = download_blob_to_dataframe(blob_name)
                if d is not None and not d.empty:
                    dfs.append(d)
            if dfs:
                combined = pd.concat(dfs, ignore_index=True)
                api_key_ha = st.secrets.get("GEMINI_API_KEY")
                model_id_ha = st.session_state.get('selected_model_id', 'gemini-2.5-flash')
                result = get_ai_historical_game_plan(api_key_ha, combined, goal, model_id_ha)
                st.session_state.historical_plan_result = result
            else:
                st.session_state.historical_plan_result = None
                st.error("Could not load any of the selected sets.")

if st.session_state.get('historical_plan_result'):
    st.header("📜 Historical game plan")
    st.markdown(st.session_state.historical_plan_result)
    st.divider()

# --- AI Assistant ---
st.header("🤖 AI Assistant")
api_key = st.secrets.get("GEMINI_API_KEY")
if not api_key:
    st.error("Please add your Gemini API Key to your Streamlit secrets.")
else:
    if not st.session_state.game_over:
        if st.button("Get AI Suggestion for Next Shot"):
            if not df_set.empty:
                with st.spinner("🤖 Calling the coach for advice..."):
                    suggestion = get_ai_suggestion(api_key, df_set, st.session_state.get('balls_in_bag', []), selected_model_id)
                    st.markdown(suggestion)
            else:
                st.info("Submit some shots first.")
    if not df_current_game.empty:
        if st.button("Get AI Post-Game Analysis"):
            with st.spinner("🤖 Analyzing your game..."):
                analysis = get_ai_analysis(api_key, df_current_game, selected_model_id)
                st.markdown(analysis)
