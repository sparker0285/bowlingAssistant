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

# --- Constants ---
PIN_NEIGHBORS = {
    1: {2, 3}, 2: {1, 3, 4, 5}, 3: {1, 2, 5, 6}, 4: {2, 5, 7, 8}, 5: {2, 3, 4, 6, 8, 9},
    6: {3, 5, 9, 10}, 7: {4, 8}, 8: {4, 5, 7, 9}, 9: {5, 6, 8, 10}, 10: {6, 9}
}

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

def get_ai_game_plan(api_key, df_sets, user_goal, model_name):
    """Analyzes multiple sets and provides a strategic game plan."""
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
        data_summary = df_sets.to_string()

        prompt = f"""
        You are an expert bowling coach reviewing a bowler's history to create a game plan.
        Analyze the following data, which represents several past sessions:
        {data_summary}

        The bowler's goal for tonight is: "{user_goal}"

        YOUR TASK:
        1.  **Identify High-Level Trends:** What are the bowler's consistent strengths and weaknesses across all these sets? (e.g., "You consistently have a higher strike percentage on the left lane," or "You struggle with 10-pin spares in later games.")
        2.  **Analyze Equipment Performance:** Which bowling balls tend to perform best? Are there patterns where a ball works well early but struggles later?
        3.  **Create a Strategic Game Plan:** Based on the data and the bowler's goal, provide a clear, actionable game plan for their next session. This should include:
            *   A recommended starting ball and a target on the lane.
            *   Key things to watch for (e.g., "If you start leaving 4-pins, that's your cue to...").
            *   Specific adjustments to make if those cues appear (e.g., "...move 2 boards left with your feet.").
            *   A recommendation for when to consider a ball change and which ball to switch to.

        Provide a concise, strategic plan.
        """
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"An error occurred during analysis: {e}"


# --- Database Setup ---
con = duckdb.connect(database='bowling.db', read_only=False)
con.execute("CREATE SEQUENCE IF NOT EXISTS seq_shots_id START 1;")
con.execute("""
    CREATE TABLE IF NOT EXISTS shots (
        id INTEGER PRIMARY KEY DEFAULT nextval('seq_shots_id'),
        set_id VARCHAR,
        set_name VARCHAR,
        bowling_center VARCHAR,
        game_id VARCHAR,
        game_number INTEGER,
        frame_number INTEGER,
        shot_number INTEGER,
        shot_result VARCHAR,
        pins_knocked_down VARCHAR,
        pins_left VARCHAR,
        is_split BOOLEAN,
        lane_number VARCHAR,
        bowling_ball VARCHAR,
        arrows_pos INTEGER,
        breakpoint_pos INTEGER,
        ball_reaction VARCHAR,
        shot_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
""")
con.execute("CREATE TABLE IF NOT EXISTS arsenal (ball_name VARCHAR PRIMARY KEY);")

# Backwards compatibility
schema_migrations = {
    'bowling_ball': 'VARCHAR',
    'bowling_center': 'VARCHAR',
    'is_split': 'BOOLEAN'
}
for col, col_type in schema_migrations.items():
    try:
        con.execute(f"ALTER TABLE shots ADD COLUMN {col} {col_type};")
        con.commit()
    except duckdb.Error:
        pass

if con.execute("SELECT COUNT(*) FROM arsenal").fetchone()[0] == 0:
    default_balls = [
        "Storm Phaze II - Pin Down", "Storm IQ Tour - Pin Down", "Roto Grip Attention Star - Pin Up",
        "Storm Lightning Blackout - Pin Up", "Storm Absolute - Pin Up", "Brunswick Prism - Pin Up"
    ]
    for ball in default_balls:
        con.execute("INSERT INTO arsenal (ball_name) VALUES (?)", (ball,))
    con.commit()

# --- Scoring & Game Logic ---
def is_split(pins_left):
    if not pins_left or 1 in pins_left or len(pins_left) <= 1:
        return False
    
    pins_left.sort()
    for i in range(len(pins_left) - 1):
        if pins_left[i+1] not in PIN_NEIGHBORS.get(pins_left[i], set()):
            q = [pins_left[i]]
            visited = {pins_left[i]}
            found_path = False
            while q:
                curr = q.pop(0)
                if curr == pins_left[i+1]:
                    found_path = True
                    break
                for neighbor in PIN_NEIGHBORS.get(curr, set()):
                    if neighbor in pins_left and neighbor not in visited:
                        visited.add(neighbor)
                        q.append(neighbor)
            if not found_path:
                return True
    return False

def get_pins_from_str(pins_str):
    if not pins_str or pins_str == "N/A": return []
    return [int(p.strip()) for p in pins_str.split(',')]

def calculate_scores(df):
    if df.empty:
        return [None] * 10, 0, 300

    shots = df.sort_values(by='id').to_dict('records')
    frame_scores = [None] * 10
    total_score = 0
    
    for i in range(len(shots)):
        current_shot = shots[i]
        frame = current_shot['frame_number']
        
        if frame > 10: continue

        score = 0
        is_frame_complete = False

        if current_shot['shot_result'] == 'Strike':
            if i + 2 < len(shots):
                next1_pins = 10 - len(get_pins_from_str(shots[i+1].get('pins_left', '')))
                if shots[i+1]['shot_result'] == 'Strike':
                    next2_pins = 10 - len(get_pins_from_str(shots[i+2].get('pins_left', '')))
                else:
                    next2_pins = len(get_pins_from_str(shots[i+1].get('pins_left', ''))) - len(get_pins_from_str(shots[i+2].get('pins_left', '')))
                score = 10 + next1_pins + next2_pins
                is_frame_complete = True
        elif current_shot['shot_result'] == 'Spare':
            if i + 1 < len(shots):
                next_pins = 10 - len(get_pins_from_str(shots[i+1].get('pins_left', '')))
                score = 10 + next_pins
                is_frame_complete = True
        else: # Open frame
            shot1_in_frame = next((s for s in shots if s['frame_number'] == frame and s['shot_number'] == 1), None)
            if shot1_in_frame and current_shot['shot_number'] == 2:
                shot1_pins_knocked = 10 - len(get_pins_from_str(shot1_in_frame.get('pins_left', '')))
                shot2_pins_knocked = len(get_pins_from_str(shot1_in_frame.get('pins_left', ''))) - len(get_pins_from_str(current_shot.get('pins_left', '')))
                score = shot1_pins_knocked + shot2_pins_knocked
                is_frame_complete = True

        if is_frame_complete:
            prev_frame_score = frame_scores[frame - 2] if frame > 1 else 0
            frame_scores[frame - 1] = (prev_frame_score or 0) + score

    final_score = 0
    if frame_scores:
        valid_scores = [s for s in frame_scores if s is not None]
        if valid_scores:
            final_score = valid_scores[-1]

    return frame_scores, final_score, 300


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
        csv_buffer = io.StringIO()
        df.to_csv(csv_buffer, index=False)
        
        blob_name = f"set-{set_name.replace(' ', '_')}-{set_id}.csv"
        blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)
        blob_client.upload_blob(csv_buffer.getvalue(), overwrite=True)
        
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

        con.execute("DELETE FROM shots")
        
        con.register('df_to_insert', df)
        con.execute('INSERT INTO shots SELECT * FROM df_to_insert')
        con.unregister('df_to_insert')
        con.commit()

        st.success(f"Successfully loaded set '{df['set_name'].iloc[0]}'.")
        st.session_state.clear()
        st.rerun()

    except Exception as e:
        st.error(f"Failed to download or load set: {e}")


# --- Main Application ---
st.set_page_config(layout="wide")
st.title("🎳 PinDeck: Bowling Assistant")

def initialize_set(set_id=None, set_name=None, center_name=None):
    if set_id is None:
        st.session_state.set_id = f"set-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
        st.session_state.set_name = set_name or f"League {datetime.datetime.now().strftime('%m-%d-%y')}"
        st.session_state.bowling_center = center_name or ""
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
        
        latest_game = con.execute("SELECT game_id, game_number, bowling_center FROM shots WHERE set_id = ? ORDER BY game_number DESC, id DESC LIMIT 1", [set_id]).fetchone()
        if latest_game:
            st.session_state.game_id, st.session_state.game_number, st.session_state.bowling_center = latest_game
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

if st.sidebar.button("Start New Set"):
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
    initialize_set(set_name=new_set_name)
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

with st.sidebar.expander("🤖 AI Settings"):
    model_options = {
        "Gemini 2.5 Flash (Recommended)": "gemini-2.5-flash",
        "Gemini 1.5 Flash (Economical)": "gemini-1.5-flash"
    }
    selected_model_label = st.selectbox("Select AI Model", options=list(model_options.keys()))
    selected_model_id = model_options[selected_model_label]

with st.sidebar.expander("⚠️ Danger Zone"):
    if st.button("Delete Current Set"):
        con.execute("DELETE FROM shots WHERE set_id = ?", (st.session_state.set_id,))
        con.commit()
        st.success(f"Set '{st.session_state.set_name}' has been deleted.")
        initialize_set()
        st.rerun()

# --- Game Selection & Data Fetching ---
st.sidebar.header("Game Management")
df_set = con.execute("SELECT * FROM shots WHERE set_id = ?", [st.session_state.set_id]).fetchdf()

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
    st.session_state.game_id = f"game-{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
    st.session_state.game_number = new_game_num
    st.session_state.current_frame = 1
    st.session_state.current_shot = 1
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

        con.execute(
            "INSERT INTO shots (set_id, set_name, game_id, game_number, frame_number, shot_number, shot_result, pins_knocked_down, pins_left, lane_number, bowling_ball, arrows_pos, breakpoint_pos, ball_reaction) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (st.session_state.set_id, st.session_state.set_name, st.session_state.game_id, st.session_state.game_number, st.session_state.current_frame, st.session_state.current_shot, shot_res, pins_knocked_down_str, pins_left_standing_str, lane_number, st.session_state.bowling_ball, arrows, breakpoint, st.session_state.ball_reaction)
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
                    suggestion = get_ai_suggestion(api_key, df_set, st.session_state.get('balls_in_bag', []), selected_model_id)
                    st.markdown(suggestion)
            else:
                st.info("Submit some shots first.")
    if not df_current_game.empty:
        if st.button("Get AI Post-Game Analysis"):
            with st.spinner("🤖 Analyzing your game..."):
                analysis = get_ai_analysis(api_key, df_current_game, selected_model_id)
                st.markdown(analysis)
