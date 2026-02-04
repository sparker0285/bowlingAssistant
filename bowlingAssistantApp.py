import streamlit as st
import duckdb
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
import datetime
import io
import os
import re
import pandas as pd
import google.generativeai as genai

# --- AI Logic ---
def get_ai_suggestion(api_key, df_set):
    """
    Analyzes game data from a set and provides a suggestion for the next shot.
    """
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('models/gemini-flash-latest')
        df_set = df_set.sort_values(by=['game_number', 'id'])
        data_summary = df_set.to_string()

        prompt = f"""
        You are an expert bowling coach. Your task is to analyze a bowler's recent performance across a set of games and provide a strategic suggestion for the next shot.

        Analyze the following data, which represents all shots taken in the current set of games, sorted chronologically:
        {data_summary}

        THINGS TO CONSIDER:
        1.  **Look for Patterns Across Games:** The key is to see how the lane conditions are changing over the entire session. If the bowler was striking on the left lane in game 1 but is now leaving 10-pins in game 3 on the same lane, the oil is breaking down. Your advice should reflect this trend.
        2.  **Analyze Ball Reaction:** The `ball_reaction` notes are crucial. A pattern of "breaking early" or "not finishing" across several frames is a strong signal for an adjustment.
        3.  **Provide Actionable Advice:** Your suggestion should be clear, concise, and based on the most recent data. For example: "In the last game, you started leaving the 4-pin on the right lane. This game, it happened again. The lanes are getting drier. For your next shot on the right lane, I suggest moving your feet 2 boards right to find more oil."

        YOUR TASK:
        Based on all the data provided for the set, what is your single most important suggestion for the next shot? Explain your reasoning, focusing on the most recent frames as the primary evidence.
        """
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"An error occurred while getting a suggestion: {e}"

def get_ai_analysis(api_key, df_game):
    """
    Performs a post-game analysis and provides practice recommendations.
    """
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('models/gemini-flash-latest')
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
        arrows_pos INTEGER,
        breakpoint_pos INTEGER,
        ball_reaction VARCHAR,
        shot_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
""")
for col, col_type in {'set_id': 'VARCHAR', 'set_name': 'VARCHAR'}.items():
    try:
        con.execute(f"ALTER TABLE shots ADD COLUMN {col} {col_type};")
        con.commit()
    except duckdb.Error:
        pass

# --- Scoring Logic ---
def get_pins_from_str(pins_str):
    if not pins_str or pins_str == "N/A": return []
    return [int(p.strip()) for p in pins_str.split(',')]

def calculate_scores(df):
    if df.empty:
        return [0] * 10, 0, 300

    shots = df.sort_values(by='id').to_dict('records')
    raw_pins = []
    for s in shots:
        if s['shot_result'] == 'Strike':
            raw_pins.append(10)
        elif s['shot_result'] == 'Spare':
            shot1_pins_str = [sh['pins_knocked_down'] for sh in shots if sh['frame_number'] == s['frame_number'] and sh['shot_number'] == 1][0]
            raw_pins.append(10 - len(get_pins_from_str(shot1_pins_str)))
        else:
            raw_pins.append(len(get_pins_from_str(s['pins_knocked_down'])))

    frame_scores = [None] * 10
    total_score = 0
    shot_idx = 0

    for frame_idx in range(10):
        if shot_idx >= len(shots): break
        frame_num = frame_idx + 1
        current_shot = shots[shot_idx]
        if current_shot['frame_number'] != frame_num: continue

        frame_score = 0
        if frame_num < 10:
            if current_shot['shot_result'] == 'Strike':
                if shot_idx + 2 < len(raw_pins):
                    frame_score = 10 + raw_pins[shot_idx + 1] + raw_pins[shot_idx + 2]
                else: break
                shot_idx += 1
            else:
                if shot_idx + 1 < len(shots) and shots[shot_idx+1]['frame_number'] == frame_num:
                    shot2 = shots[shot_idx+1]
                    if shot2['shot_result'] == 'Spare':
                        if shot_idx + 2 < len(raw_pins):
                            frame_score = 10 + raw_pins[shot_idx + 2]
                        else: break
                    else:
                        frame_score = raw_pins[shot_idx] + raw_pins[shot_idx+1]
                    shot_idx += 2
                else: break
        else:
            frame_10_shots = [s for s in shots if s['frame_number'] == 10]
            frame_10_pins = [p for i, p in enumerate(raw_pins) if shots[i]['frame_number'] == 10]
            is_done = False
            if frame_10_shots and frame_10_shots[0]['shot_result'] == 'Strike':
                if len(frame_10_shots) == 3: is_done = True
            elif 'Spare' in [s['shot_result'] for s in frame_10_shots]:
                if len(frame_10_shots) == 3: is_done = True
            else:
                if len(frame_10_shots) == 2: is_done = True
            if is_done:
                frame_score = sum(frame_10_pins)
            else: break
            shot_idx += len(frame_10_shots)

        total_score += frame_score
        frame_scores[frame_idx] = total_score

    max_score = 0
    if not df.empty:
        last_scored_frame_idx = -1
        for i in range(9, -1, -1):
            if frame_scores[i] is not None:
                last_scored_frame_idx = i
                break
        
        max_score = frame_scores[last_scored_frame_idx] if last_scored_frame_idx != -1 else 0
        
        start_frame_idx = last_scored_frame_idx + 1
        
        if start_frame_idx < 10:
            shots_in_unscored_frame = [s for s in shots if s['frame_number'] == start_frame_idx + 1]
            if shots_in_unscored_frame and shots_in_unscored_frame[0]['shot_result'] == 'Leave':
                max_score += 20 
            else:
                max_score += 30
            
            for i in range(start_frame_idx + 1, 10):
                max_score += 30

    return frame_scores, total_score, max_score if max_score > 0 else 300


# --- Azure Integration ---
def upload_set_to_azure(con, set_id):
    """Uploads all games in a set to Azure Blob Storage using credentials from st.secrets."""
    try:
        container_name = st.secrets.get("AZURE_STORAGE_CONTAINER_NAME")
        connection_string = st.secrets.get("AZURE_STORAGE_CONNECTION_STRING")
        account_name = st.secrets.get("AZURE_STORAGE_ACCOUNT_NAME")

        if not container_name:
            st.error("Azure secret `AZURE_STORAGE_CONTAINER_NAME` not found. Please add it to your Streamlit secrets.")
            return

        blob_service_client = None
        if connection_string:
            blob_service_client = BlobServiceClient.from_connection_string(connection_string)
        elif account_name:
            blob_service_client = BlobServiceClient(
                account_url=f"https://{account_name}.blob.core.windows.net",
                credential=DefaultAzureCredential()
            )
        else:
            st.error("Azure credentials not found. Please add either `AZURE_STORAGE_CONNECTION_STRING` or `AZURE_STORAGE_ACCOUNT_NAME` to your Streamlit secrets.")
            return

        df = con.execute("SELECT * FROM shots WHERE set_id = ?", [set_id]).fetchdf()
        if df.empty:
            st.warning("No data in this set to save.")
            return

        set_name = df['set_name'].iloc[0]
        csv_buffer = io.StringIO()
        df.to_csv(csv_buffer, index=False)
        
        blob_name = f"set-{set_name.replace(' ', '_')}-{set_id}.csv"
        blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)
        blob_client.upload_blob(csv_buffer.getvalue(), overwrite=True)
        
        st.success(f"Set '{set_name}' saved successfully to Azure.")

    except Exception as e:
        st.error(f"An unexpected error occurred while uploading to Azure: {e}")


# --- Main Application ---
st.set_page_config(layout="wide")
st.title("🎳 PinDeck: Bowling Set Tracker")

def initialize_new_set(new_set_name):
    """Resets session state for a new set."""
    st.session_state.set_id = f"set-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
    st.session_state.set_name = new_set_name
    st.session_state.game_id = f"game-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
    st.session_state.game_number = 1
    st.session_state.current_frame = 1
    st.session_state.current_shot = 1
    st.session_state.pins_left_after_first_shot = []
    st.session_state.starting_lane = "Left Lane"
    st.session_state.game_over = False

if 'set_id' not in st.session_state:
    initialize_new_set(f"League {datetime.datetime.now().strftime('%m-%d-%y')}")

# --- Sidebar ---
st.sidebar.header("Set Management")

all_sets_from_db = con.execute("SELECT DISTINCT set_id, set_name FROM shots ORDER BY set_name DESC").fetchall()
set_map = {s[0]: s[1] for s in all_sets_from_db}
if st.session_state.set_id not in set_map and st.session_state.set_name:
    set_map[st.session_state.set_id] = st.session_state.set_name

if set_map:
    try:
        current_set_index = list(set_map.keys()).index(st.session_state.set_id)
    except ValueError:
        current_set_index = 0
    
    selected_set_name = st.sidebar.selectbox(
        "Select Set to View/Analyze",
        options=list(set_map.values()),
        index=current_set_index
    )
    selected_set_id = [sid for sid, name in set_map.items() if name == selected_set_name][0]

    if selected_set_id != st.session_state.set_id:
        st.session_state.set_id = selected_set_id
        st.session_state.set_name = selected_set_name
        latest_game = con.execute("SELECT game_id, game_number FROM shots WHERE set_id = ? ORDER BY game_number DESC LIMIT 1", [selected_set_id]).fetchone()
        if latest_game:
            st.session_state.game_id, st.session_state.game_number = latest_game
            first_shot = con.execute("SELECT lane_number FROM shots WHERE game_id = ? AND frame_number = 1 AND shot_number = 1", [latest_game[0]]).fetchone()
            st.session_state.starting_lane = first_shot[0] if first_shot else "Left Lane"
        else:
            initialize_new_set(selected_set_name)
        st.rerun()

if st.sidebar.button("Start New Set"):
    today_str = datetime.datetime.now().strftime('%m-%d-%y')
    base_name = f"League {today_str}"
    existing_sets_today = con.execute("SELECT set_name FROM shots WHERE set_name LIKE ? ORDER BY set_name DESC", [f"{base_name}%"]).fetchall()
    
    next_seq = 1
    if existing_sets_today:
        last_set_name = existing_sets_today[0][0]
        match = re.search(r'_(\d+)$', last_set_name)
        if match:
            next_seq = int(match.group(1)) + 1
        else:
            next_seq = 2

    new_set_name = f"{base_name}_{next_seq}" if next_seq > 1 else base_name
    initialize_new_set(new_set_name)
    st.rerun()

new_name = st.sidebar.text_input("Rename Current Set", value=st.session_state.get('set_name', ''))
if st.sidebar.button("Rename Set"):
    if new_name:
        con.execute("UPDATE shots SET set_name = ? WHERE set_id = ?", (new_name, st.session_state.set_id))
        con.commit()
        st.session_state.set_name = new_name
        st.rerun()

if st.sidebar.button("Save Set to Azure"):
    upload_set_to_azure(con, st.session_state.set_id)

with st.sidebar.expander("⚠️ Danger Zone"):
    if st.button("Delete Current Set"):
        con.execute("DELETE FROM shots WHERE set_id = ?", (st.session_state.set_id,))
        con.commit()
        st.success(f"Set '{st.session_state.set_name}' has been deleted.")
        initialize_new_set(f"League {datetime.datetime.now().strftime('%m-%d-%y')}")
        st.rerun()


# --- Game Selection & Data Fetching ---
st.sidebar.header("Game Management")
df_set = con.execute("SELECT * FROM shots WHERE set_id = ?", [st.session_state.set_id]).fetchdf()

games_in_set = df_set['game_number'].unique()
games_in_set.sort()
game_map = {f"Game {g}": g for g in games_in_set}
if st.session_state.game_number not in game_map.values():
    game_map[f"Game {st.session_state.game_number}"] = st.session_state.game_number

selected_game_name = st.sidebar.selectbox(
    "Select Game",
    options=list(game_map.keys()),
    index=list(game_map.values()).index(st.session_state.game_number)
)
selected_game_number = game_map[selected_game_name]

if selected_game_number != st.session_state.game_number:
    st.session_state.game_number = selected_game_number
    game_id_res = df_set[df_set['game_number'] == selected_game_number]['game_id'].iloc[0]
    st.session_state.game_id = game_id_res
    first_shot = con.execute("SELECT lane_number FROM shots WHERE game_id = ? AND frame_number = 1 AND shot_number = 1", [game_id_res]).fetchone()
    st.session_state.starting_lane = first_shot[0] if first_shot else "Left Lane"
    st.rerun()

if st.sidebar.button("Start New Game in Set"):
    new_game_num = (max(games_in_set) if games_in_set.size > 0 else 0) + 1
    st.session_state.game_id = f"game-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
    st.session_state.game_number = new_game_num
    st.session_state.current_frame = 1
    st.session_state.current_shot = 1
    st.session_state.game_over = False
    st.session_state.starting_lane = "Left Lane"
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
            lane_number = st.session_state.starting_lane
        else:
            if 'starting_lane' not in st.session_state or not st.session_state.starting_lane:
                first_shot = con.execute("SELECT lane_number FROM shots WHERE game_id = ? AND frame_number = 1 AND shot_number = 1", [st.session_state.game_id]).fetchone()
                st.session_state.starting_lane = first_shot[0] if first_shot else "Left Lane"
            
            is_odd_frame = st.session_state.current_frame % 2 != 0
            starts_on_left = st.session_state.starting_lane == "Left Lane"
            lane_number = st.session_state.starting_lane if is_odd_frame else ("Right Lane" if starts_on_left else "Left Lane")
        st.metric("Current Lane", lane_number)
        st.session_state.lane_number = lane_number
    
    if st.session_state.current_shot == 1 or (st.session_state.current_frame == 10 and st.session_state.current_shot > 1):
        st.subheader("Ball Trajectory")
        st.selectbox("Position at Arrows", options=list(range(1, 40)), index=16, key="arrows_pos")
        st.selectbox("Position at Breakpoint", options=list(range(1, 40)), index=9, key="breakpoint_pos")

    st.text_input("Ball Reaction", key="ball_reaction")
    st.subheader("Pins Left Standing")
    pins_selected = {pin: st.checkbox(str(pin), key=f"pin_{pin}") for pin in range(1, 11)}

    def submit_shot():
        use_trajectory = st.session_state.current_shot == 1 or (st.session_state.current_frame == 10 and st.session_state.current_shot > 1)
        arrows = st.session_state.arrows_pos if use_trajectory else None
        breakpoint = st.session_state.breakpoint_pos if use_trajectory else None
        pins_left_standing = sorted([pin for pin, selected in pins_selected.items() if selected])
        pins_left_standing_str = ", ".join(map(str, pins_left_standing))

        con.execute(
            "INSERT INTO shots (set_id, set_name, game_id, game_number, frame_number, shot_number, shot_result, pins_knocked_down, pins_left, lane_number, arrows_pos, breakpoint_pos, ball_reaction) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (st.session_state.set_id, st.session_state.set_name, st.session_state.game_id, st.session_state.game_number, st.session_state.current_frame, st.session_state.current_shot, st.session_state.shot_result, "N/A", pins_left_standing_str, st.session_state.lane_number, arrows, breakpoint, st.session_state.ball_reaction)
        )
        con.commit()
        
        shot_res = st.session_state.shot_result
        if st.session_state.current_frame < 10:
            if st.session_state.current_shot == 2 or shot_res == "Strike":
                st.session_state.current_frame += 1
                st.session_state.current_shot = 1
            else:
                st.session_state.current_shot = 2
        else:
            shot1_res_df = df_current_game[(df_current_game['frame_number'] == 10) & (df_current_game['shot_number'] == 1)]
            shot1_res = shot1_res_df['shot_result'].iloc[0] if not shot1_res_df.empty else ''
            if st.session_state.current_shot == 1:
                st.session_state.current_shot = 2
            elif st.session_state.current_shot == 2:
                if shot1_res == "Strike" or shot_res == "Spare":
                    st.session_state.current_shot = 3
                else: st.session_state.game_over = True
            else:
                st.session_state.game_over = True

    st.button("Submit Shot", use_container_width=True, on_click=submit_shot)

# --- Analytical Dashboard ---
st.header(f"📊 Data for Set: {st.session_state.set_name}")
if not df_set.empty:
    display_df = df_set.sort_values(by=['game_number', 'frame_number', 'id'], ascending=[True, False, False])
    st.dataframe(display_df.drop(columns=['id', 'set_id', 'game_id', 'pins_knocked_down', 'shot_timestamp']), hide_index=True)
else:
    st.info("No shots submitted for this set yet.")

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
                    suggestion = get_ai_suggestion(api_key, df_set)
                    st.markdown(suggestion)
            else:
                st.info("Submit some shots first.")
    if not df_current_game.empty:
        if st.button("Get AI Post-Game Analysis"):
            with st.spinner("🤖 Analyzing your game..."):
                analysis = get_ai_analysis(api_key, df_current_game)
                st.markdown(analysis)
