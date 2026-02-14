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
    # This function remains the same
    pass

def get_ai_game_plan(api_key, df_sets, user_goal):
    """Analyzes multiple sets and provides a strategic game plan."""
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('models/gemini-flash-latest')
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
# Use a base schema for initial creation
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

# Add new columns with error handling for backward compatibility
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
        pass # Column already exists

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
        return [0] * 10, 0, 300

    shots = df.sort_values(by='id').to_dict('records')
    frame_scores = [None] * 10
    total_score = 0
    
    for i in range(1, 11):
        frame_shots = [s for s in shots if s['frame_number'] == i]
        if not frame_shots: continue

        shot1 = frame_shots[0]
        shot1_pins_left = get_pins_from_str(shot1.get('pins_left', ''))
        shot1_pins_knocked_down = 10 - len(shot1_pins_left)
        
        frame_score = 0
        is_frame_complete = False

        if i < 10:
            if shot1['shot_result'] == 'Strike':
                next_shots = [s for s in shots if s['id'] > shot1['id']][:2]
                if len(next_shots) == 2:
                    bonus1_pins_left = get_pins_from_str(next_shots[0].get('pins_left', ''))
                    bonus1 = 10 - len(bonus1_pins_left)
                    bonus2 = 0
                    if next_shots[0]['shot_result'] != 'Strike':
                        bonus2 = len(bonus1_pins_left) - len(get_pins_from_str(next_shots[1].get('pins_left', '')))
                    else:
                        bonus2 = 10 - len(get_pins_from_str(next_shots[1].get('pins_left', '')))
                    frame_score = 10 + bonus1 + bonus2
                    is_frame_complete = True
            else:
                if len(frame_shots) > 1:
                    shot2 = frame_shots[1]
                    shot2_pins_left = get_pins_from_str(shot2.get('pins_left', ''))
                    shot2_pins_knocked_down = len(shot1_pins_left) - len(shot2_pins_left)
                    if shot2['shot_result'] == 'Spare':
                        next_shot = [s for s in shots if s['id'] > shot2['id']][:1]
                        if next_shot:
                            bonus = 10 - len(get_pins_from_str(next_shot[0].get('pins_left', '')))
                            frame_score = 10 + bonus
                            is_frame_complete = True
                    else:
                        frame_score = shot1_pins_knocked_down + shot2_pins_knocked_down
                        is_frame_complete = True
        else: # 10th Frame
            if frame_shots:
                shot1_pins = 10 - len(get_pins_from_str(frame_shots[0].get('pins_left', '')))
                frame_score += shot1_pins
                if len(frame_shots) > 1:
                    shot2_pins = len(get_pins_from_str(frame_shots[0].get('pins_left', ''))) - len(get_pins_from_str(frame_shots[1].get('pins_left', '')))
                    frame_score += shot2_pins
                if len(frame_shots) > 2:
                    shot3_pins = 10 - len(get_pins_from_str(frame_shots[2].get('pins_left', ''))) if frame_shots[1]['shot_result'] == 'Spare' or frame_shots[0]['shot_result'] == 'Strike' else len(get_pins_from_str(frame_shots[1].get('pins_left', ''))) - len(get_pins_from_str(frame_shots[2].get('pins_left', '')))
                    frame_score += shot3_pins

            if frame_shots[0]['shot_result'] == 'Strike':
                if len(frame_shots) == 3: is_frame_complete = True
            elif len(frame_shots) > 1 and frame_shots[1]['shot_result'] == 'Spare':
                if len(frame_shots) == 3: is_frame_complete = True
            elif len(frame_shots) == 2:
                is_frame_complete = True

        if is_frame_complete:
            total_score += frame_score
            frame_scores[i-1] = total_score
    
    return frame_scores, total_score, 300

# --- Main App ---
st.set_page_config(layout="wide")
st.title("🎳 PinDeck: Bowling Assistant")

# ... (Azure functions and state management functions remain the same)

# --- UI Rendering ---
# ... (Sidebar logic)

# --- Score Sheet UI ---
st.header("Score Sheet")
# ... (Score sheet rendering logic)

# --- Editable Data Grid ---
st.header("Game Data")
# ... (Data editor logic)
