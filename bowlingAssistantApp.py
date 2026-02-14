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
def get_ai_suggestion(api_key, df_set, balls_in_bag):
    pass

def get_ai_game_plan(api_key, df_sets, user_goal):
    pass

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
con.execute("CREATE TABLE IF NOT EXISTS arsenal (ball_name VARCHAR PRIMARY KEY);")

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
    shot_cursor = 0

    for frame in range(1, 11):
        frame_shots = [s for s in shots if s.get('frame_number') == frame]
        if not frame_shots:
            break

        shot1 = frame_shots[0]
        shot1_pins_left = get_pins_from_str(shot1.get('pins_left', ''))
        shot1_pins_knocked = 10 - len(shot1_pins_left)

        frame_score = 0
        is_frame_complete = False

        if frame < 10:
            if shot1.get('shot_result') == 'Strike':
                # Look ahead for the next two shots' pinfall
                future_shots = [s for s in shots if s['id'] > shot1['id']]
                if len(future_shots) >= 2:
                    bonus1_pins_left = get_pins_from_str(future_shots[0].get('pins_left', ''))
                    bonus1 = 10 - len(bonus1_pins_left)
                    
                    if future_shots[0].get('shot_result') == 'Strike':
                        bonus2 = 10 - len(get_pins_from_str(future_shots[1].get('pins_left', '')))
                    else:
                        bonus2 = len(bonus1_pins_left) - len(get_pins_from_str(future_shots[1].get('pins_left', '')))
                    frame_score = 10 + bonus1 + bonus2
                    is_frame_complete = True
            else: # Not a strike
                if len(frame_shots) > 1:
                    shot2 = frame_shots[1]
                    if shot2.get('shot_result') == 'Spare':
                        future_shots = [s for s in shots if s['id'] > shot2['id']]
                        if len(future_shots) >= 1:
                            bonus = 10 - len(get_pins_from_str(future_shots[0].get('pins_left', '')))
                            frame_score = 10 + bonus
                            is_frame_complete = True
                    else: # Open frame
                        shot2_pins_left = get_pins_from_str(shot2.get('pins_left', ''))
                        shot2_pins_knocked = len(shot1_pins_left) - len(shot2_pins_left)
                        frame_score = shot1_pins_knocked + shot2_pins_knocked
                        is_frame_complete = True
        else: # 10th Frame
            frame_score = shot1_pins_knocked
            if len(frame_shots) > 1:
                shot2 = frame_shots[1]
                shot2_pins_left = get_pins_from_str(shot2.get('pins_left', ''))
                if shot1.get('shot_result') == 'Strike':
                    frame_score += 10 - len(shot2_pins_left)
                else:
                    frame_score += len(shot1_pins_left) - len(shot2_pins_left)
            
            if len(frame_shots) > 2:
                shot3 = frame_shots[2]
                shot3_pins_left = get_pins_from_str(shot3.get('pins_left', ''))
                if shot_is_fill_ball(frame_shots):
                    frame_score += 10 - len(shot3_pins_left)
                else:
                    frame_score += len(get_pins_from_str(frame_shots[1].get('pins_left',''))) - len(shot3_pins_left)


            if len(frame_shots) == 3 or (frame_shots[0].get('shot_result') != 'Strike' and len(frame_shots) == 2 and (len(frame_shots) < 2 or frame_shots[1].get('shot_result') != 'Spare')):
                 is_frame_complete = True


        if is_frame_complete:
            total_score += frame_score
            frame_scores[frame - 1] = total_score
            
    return frame_scores, total_score, 300

def shot_is_fill_ball(frame10_shots):
    if len(frame10_shots) < 3: return False
    return frame10_shots[0].get('shot_result') == 'Strike' or frame10_shots[1].get('shot_result') == 'Spare'

# --- Main App ---
st.set_page_config(layout="wide")
st.title("🎳 PinDeck: Bowling Assistant")

# ... (The rest of the app logic remains the same)
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

# --- UI Rendering ---
df_set = con.execute("SELECT * FROM shots WHERE set_id = ?", [st.session_state.set_id]).fetchdf()
df_current_game = df_set[df_set['game_number'] == st.session_state.game_number] if not df_set.empty else pd.DataFrame()

st.header("Score Sheet")
frame_scores, total_score, max_score = calculate_scores(df_current_game)
score_sheet_cols = st.columns(10)
for i in range(10):
    with score_sheet_cols[i]:
        box1, box2, box3 = " ", " ", " "
        if not df_current_game.empty and 'frame_number' in df_current_game.columns:
            frame_shots = df_current_game[df_current_game['frame_number'] == i + 1].sort_values('shot_number')
            if not frame_shots.empty:
                shot1 = frame_shots.iloc[0]
                pins_left1 = get_pins_from_str(shot1.get('pins_left', ''))
                
                if shot1['shot_result'] == 'Strike':
                    box1 = "X"
                else:
                    shot1_pins = 10 - len(pins_left1)
                    box1 = f"S{shot1_pins}" if shot1.get('is_split') else str(shot1_pins)
                    
                    if len(frame_shots) > 1:
                        shot2 = frame_shots.iloc[1]
                        if shot2['shot_result'] == 'Spare':
                            box2 = "/"
                        else:
                            pins_left2 = get_pins_from_str(shot2.get('pins_left', ''))
                            shot2_pins = len(pins_left1) - len(pins_left2)
                            box2 = str(shot2_pins)
        
        frame_str = f"**{i+1}**<br>{box1} | {box2}<br>**{frame_scores[i] or ''}**"
        st.markdown(f"<div>{frame_str}</div>", unsafe_allow_html=True)

st.markdown(f"**Total Score:** {total_score} | **Max Possible:** {max_score}")

st.header("Game Data")
edited_df = st.data_editor(df_set, key="data_editor")

# ... (rest of the app)
