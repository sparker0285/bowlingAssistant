import streamlit as st
import duckdb
from azure.storage.blob import BlobServiceClient
import datetime
import io
import base64
import pandas as pd
import google.generativeai as genai

# --- AI Suggestion Logic ---
def get_ai_suggestion(api_key, df_shots):
    """
    Analyzes game data and provides a suggestion using the Gemini AI model.
    """
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('models/gemini-flash-latest')

        # Create a summary of the dataframe to pass to the model
        data_summary = df_shots.to_string()

        prompt = f"""
        You are an expert bowling coach. Your task is to analyze a bowler's recent performance and provide a strategic suggestion for the next shot.

        Analyze the following data, which represents the shots taken in the current game:
        {data_summary}

        Here are the key columns:
        - shot_result: The outcome of the shot (Strike, Spare, Open, Leave).
        - lane_number: The lane the shot was on.
        - pins_knocked_down: The specific pins knocked down.
        - arrows_pos: The board the ball crossed at the arrows (1-40). 'None' for spare shots.
        - breakpoint_pos: The board the ball was at its furthest point down the lane (1-40). 'None' for spare shots.
        - ball_reaction: The bowler's notes on how the ball behaved (e.g., "broke early", "held line", "light hit").

        THINGS TO CONSIDER:
        1.  **Look for Patterns:** Don't overreact to a single bad shot. Look for trends over the last 2-3 frames on a given lane. For example, is the bowler consistently missing high (light hit, leaving corner pins)? Or missing low (Brooklyn, splitting)?
        2.  **Analyze Ball Reaction:** The `ball_reaction` notes are crucial. If the ball is "breaking early" or "hooking too much" consistently on one lane, that's a strong indicator that the oil is breaking down and a move is needed.
        3.  **Filter Out User Error:** If a shot has a trajectory (`arrows_pos`, `breakpoint_pos`) that is very different from the shots around it, it's likely the bowler missed their target. Acknowledge this as a likely execution error and advise them to focus on hitting their target before considering a move. For example, if they usually hit board 17 at the arrows and then suddenly hit board 12, they pulled the ball.
        4.  **Provide Actionable Advice:** Your suggestion should be clear and concise. Should the bowler move their feet? Move their target? Or stay put and focus on execution? A good suggestion might be: "Your ball has been finishing high on the right lane for the last two frames (leaving the 10-pin). I suggest moving your feet 2 boards left on your approach to get the ball into the oil earlier."

        YOUR TASK:
        Based on the data provided, what is your single most important suggestion for the next shot? Explain your reasoning in a brief paragraph.
        """

        response = model.generate_content(prompt)
        return response.text

    except Exception as e:
        return f"An error occurred while getting a suggestion: {e}"


# --- Database Setup ---
def get_db_connection():
    """Initializes and returns a DuckDB database connection."""
    if 'db_connection' not in st.session_state:
        st.session_state.db_connection = duckdb.connect(database=':memory:', read_only=False)
        st.session_state.db_connection.execute("CREATE SEQUENCE seq_shots_id START 1;")
        st.session_state.db_connection.execute("""
            CREATE TABLE shots (
                id INTEGER PRIMARY KEY DEFAULT nextval('seq_shots_id'),
                game_number INTEGER,
                frame_number INTEGER,
                shot_number INTEGER,
                shot_result VARCHAR,
                pins_knocked_down VARCHAR,
                pins_left VARCHAR,
                lane_number VARCHAR,
                arrows_pos INTEGER,
                breakpoint_pos INTEGER,
                ball_reaction VARCHAR
            );
        """)
    return st.session_state.db_connection

# --- Scoring Logic ---
def get_pins_from_str(pins_str):
    """Helper to convert comma-separated pin string to a list of ints."""
    if not pins_str or pins_str == "N/A":
        return []
    return [int(p.strip()) for p in pins_str.split(',')]

def calculate_scores(df):
    """Calculates frame scores, total score, and max possible score."""
    if df.empty:
        return [0] * 10, 0, 300

    shots = df.sort_values(by='id').to_dict('records')
    
    # Create a list of pin counts for each shot
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
        if shot_idx >= len(shots):
            break
            
        frame_num = frame_idx + 1
        current_shot = shots[shot_idx]
        
        if current_shot['frame_number'] != frame_num:
            continue
            
        frame_score = 0
        
        if frame_num < 10:
            if current_shot['shot_result'] == 'Strike':
                if shot_idx + 2 < len(raw_pins):
                    frame_score = 10 + raw_pins[shot_idx + 1] + raw_pins[shot_idx + 2]
                else: # Not enough shots yet for full bonus
                    break
                shot_idx += 1
            else: # Leave
                if shot_idx + 1 < len(shots) and shots[shot_idx+1]['frame_number'] == frame_num:
                    shot2 = shots[shot_idx+1]
                    if shot2['shot_result'] == 'Spare':
                        if shot_idx + 2 < len(raw_pins):
                            frame_score = 10 + raw_pins[shot_idx + 2]
                        else: # Not enough shots yet for full bonus
                            break
                    else: # Open
                        frame_score = raw_pins[shot_idx] + raw_pins[shot_idx+1]
                    shot_idx += 2
                else: # Incomplete frame
                    break
        else: # Frame 10
            frame_10_shots = [s for s in shots if s['frame_number'] == 10]
            frame_10_pins = [p for i, p in enumerate(raw_pins) if shots[i]['frame_number'] == 10]
            
            is_done = False
            if frame_10_shots[0]['shot_result'] == 'Strike':
                if len(frame_10_shots) == 3: is_done = True
            elif 'Spare' in [s['shot_result'] for s in frame_10_shots]:
                if len(frame_10_shots) == 3: is_done = True
            else: # Open
                if len(frame_10_shots) == 2: is_done = True
            
            if is_done:
                frame_score = sum(frame_10_pins)
            else:
                break
            shot_idx += len(frame_10_shots)

        total_score += frame_score
        frame_scores[frame_idx] = total_score

    # --- Max Possible Score ---
    max_score = total_score
    
    # Calculate potential of current, unfinished frame
    current_frame_idx = st.session_state.current_frame -1
    if current_frame_idx < 10 and frame_scores[current_frame_idx] is None:
        
        # Add score for current work
        frame_shots = [s for s in shots if s['frame_number'] == st.session_state.current_frame]
        if frame_shots:
            if frame_shots[0]['shot_result'] == 'Leave':
                 max_score += (10 - raw_pins[shot_idx]) # Pins for a spare
                 max_score += 10 # Strike on fill ball
        
        # Add 30 for all future frames
        for i in range(st.session_state.current_frame, 10):
            max_score += 30

    return frame_scores, total_score, max_score


# --- Azure Integration ---
def upload_to_azure(con, game_number):
    """Uploads the current game data to Azure Blob Storage."""
    try:
        connection_string = st.secrets["AZURE_STORAGE_CONNECTION_STRING"]
        container_name = st.secrets["AZURE_STORAGE_CONTAINER_NAME"]

        blob_service_client = BlobServiceClient.from_connection_string(connection_string)
        
        df = con.execute("SELECT * FROM shots").fetchdf()
        if df.empty:
            st.warning("No data to save.")
            return

        # Convert dataframe to CSV
        csv_buffer = io.StringIO()
        df.to_csv(csv_buffer, index=False)
        
        # Create a unique blob name
        today = datetime.date.today().strftime("%Y-%m-%d")
        blob_name = f"game-{today}-g{game_number}.csv"
        
        blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)
        
        blob_client.upload_blob(csv_buffer.getvalue(), overwrite=True)
        
        st.success(f"Game {game_number} saved successfully to Azure as {blob_name}")

    except KeyError:
        st.error("Azure storage credentials not found. Please configure them in your Streamlit secrets.")
    except Exception as e:
        st.error(f"Failed to upload to Azure: {e}")


# --- Main Application ---
st.set_page_config(layout="wide")
st.title("🎳 PinDeck: Bowling Lane Breakdown Tracker")

# Initialize session state for game number
if 'game_number' not in st.session_state:
    st.session_state.game_number = 1
if 'current_frame' not in st.session_state:
    st.session_state.current_frame = 1
if 'current_shot' not in st.session_state:
    st.session_state.current_shot = 1
if 'pins_left_after_first_shot' not in st.session_state:
    st.session_state.pins_left_after_first_shot = []
if 'starting_lane' not in st.session_state:
    st.session_state.starting_lane = "Left Lane"
if 'game_over' not in st.session_state:
    st.session_state.game_over = False

# Get DB connection
con = get_db_connection()

# --- Game Management & Scoring ---
st.sidebar.header("Game Management")
st.sidebar.metric("Current Game", st.session_state.game_number)

if not st.session_state.game_over:
    st.sidebar.metric("Current Frame", st.session_state.current_frame)
    st.sidebar.metric("Current Shot", st.session_state.current_shot)
else:
    st.sidebar.success("🎉 Game Over! 🎉")

if st.sidebar.button("Start New Game"):
    st.session_state.game_number += 1
    st.session_state.current_frame = 1
    st.session_state.current_shot = 1
    st.session_state.pins_left_after_first_shot = []
    st.session_state.game_over = False
    con.execute("DELETE FROM shots")
    st.rerun()

if st.sidebar.button("Save Game to Azure"):
    upload_to_azure(con, st.session_state.game_number)

# --- Scoring Display ---
df_for_score = con.execute("SELECT * FROM shots ORDER BY id").fetchdf()
frame_scores, total_score, max_score = calculate_scores(df_for_score)

st.sidebar.header("Score")
st.sidebar.metric("Current Score", total_score)
st.sidebar.metric("Max Possible Score", max_score)


# --- Shot Input ---
st.header(f"Frame {st.session_state.current_frame} - Shot {st.session_state.current_shot}")

# --- Shot Information UI ---
col1, col2 = st.columns(2)

with col1:
    shot_result_options = []
    shot_result_label = "Shot Result"
    
    # Logic for 10th frame shot options
    if st.session_state.current_frame == 10:
        if st.session_state.current_shot == 1:
            shot_result_options = ["Strike", "Leave"]
        elif st.session_state.current_shot == 2:
            frame10_shot1_res = con.execute("SELECT shot_result FROM shots WHERE frame_number = 10 AND shot_number = 1").fetchone()
            if frame10_shot1_res and frame10_shot1_res[0] == 'Strike':
                shot_result_options = ["Strike", "Leave"]
            else:
                shot_result_options = ["Spare", "Open"]
        elif st.session_state.current_shot == 3:
            shot_result_options = ["Strike", "Leave", "Open"] # Can be anything on the fill ball
    
    # Logic for frames 1-9
    if not shot_result_options:
        if st.session_state.current_shot == 1:
            shot_result_options = ["Strike", "Leave"]
        else:
            shot_result_options = ["Spare", "Open"]

    shot_result = st.radio(shot_result_label, shot_result_options, key="shot_result", horizontal=True)

with col2:
    if st.session_state.current_frame == 1 and st.session_state.current_shot == 1:
        sub_col1, sub_col2 = st.columns(2)
        with sub_col1:
            # The key 'starting_lane' will directly update the session state variable
            st.selectbox("Starting Lane", ["Left Lane", "Right Lane"], key="starting_lane")
        
        lane_number = st.session_state.starting_lane
        with sub_col2:
            st.metric("Current Lane", lane_number)
    else:
        # Determine lane based on starting lane and frame number
        if st.session_state.starting_lane == "Left Lane":
            lane_number = "Left Lane" if st.session_state.current_frame % 2 != 0 else "Right Lane"
        else: # Started on Right Lane
            lane_number = "Right Lane" if st.session_state.current_frame % 2 != 0 else "Left Lane"
        st.metric("Current Lane", lane_number)

    st.session_state.lane_number = lane_number

# Hide trajectory for spare shots or 10th frame non-first shots
if st.session_state.current_shot == 1 or (st.session_state.current_frame == 10 and st.session_state.current_shot > 1):
    st.subheader("Ball Trajectory")
    col1a, col2a = st.columns(2)
    with col1a:
        arrows_pos = st.selectbox("Position at Arrows", options=list(range(1, 40)), index=16, key="arrows_pos")
    with col2a:
        breakpoint_pos = st.selectbox("Position at Breakpoint", options=list(range(1, 40)), index=9, key="breakpoint_pos")

ball_reaction = st.text_input("Ball Reaction (e.g., broke early, held line)", key="ball_reaction")

# --- Pin Selection UI ---
st.subheader("Pin Selection")

# Static reference image for pin layout
PIN_LAYOUT_SVG = """
<svg width="250" height="150" viewBox="0 0 250 150" xmlns="http://www.w3.org/2000/svg">
  <style>
    .pin-circle {{ fill: var(--background-color); stroke: var(--text-color); stroke-width: 1.5; }}
    .pin-text {{ font-family: sans-serif; font-size: 22px; text-anchor: middle; dominant-baseline: middle; fill: var(--text-color); font-weight: bold; }}
  </style>
  <g transform="translate(45, 0)">
    <g> <!-- Row 4 -->
      <circle cx="20" cy="115" r="18" class="pin-circle" /><text x="20" y="115" class="pin-text">7</text>
      <circle cx="65" cy="115" r="18" class="pin-circle" /><text x="65" y="115" class="pin-text">8</text>
      <circle cx="110" cy="115" r="18" class="pin-circle" /><text x="110" y="115" class="pin-text">9</text>
      <circle cx="155" cy="115" r="18" class="pin-circle" /><text x="155" y="115" class="pin-text">10</text>
    </g>
    <g> <!-- Row 3 -->
      <circle cx="42.5" cy="80" r="18" class="pin-circle" /><text x="42.5" y="80" class="pin-text">4</text>
      <circle cx="87.5" cy="80" r="18" class="pin-circle" /><text x="87.5" y="80" class="pin-text">5</text>
      <circle cx="132.5" cy="80" r="18" class="pin-circle" /><text x="132.5" y="80" class="pin-text">6</text>
    </g>
    <g> <!-- Row 2 -->
      <circle cx="65" cy="45" r="18" class="pin-circle" /><text x="65" y="45" class="pin-text">2</text>
      <circle cx="110" cy="45" r="18" class="pin-circle" /><text x="110" y="45" class="pin-text">3</text>
    </g>
    <g> <!-- Row 1 -->
      <circle cx="87.5" cy="10" r="18" class="pin-circle" /><text x="87.5" y="10" class="pin-text">1</text>
    </g>
  </g>
</svg>
"""
st.image(PIN_LAYOUT_SVG)

pins_selected = {}
def pin_checkbox(pin_num, disabled=False):
    """Creates a standard checkbox for a given pin number."""
    return st.checkbox(str(pin_num), key=f"pin_{pin_num}", disabled=disabled)

# Determine which pins should be disabled based on the shot context
is_strike = st.session_state.shot_result == "Strike"
is_spare = st.session_state.shot_result == "Spare"
pins_available_for_shot2 = st.session_state.pins_left_after_first_shot

# In frame 10, after a strike or spare, the deck is reset
pins_are_reset = False
if st.session_state.current_frame == 10:
    if st.session_state.current_shot == 2:
        shot1_res = con.execute("SELECT shot_result FROM shots WHERE frame_number = 10 AND shot_number = 1").fetchone()
        if shot1_res and shot1_res[0] == 'Strike':
            pins_are_reset = True
    elif st.session_state.current_shot == 3:
        pins_are_reset = True  # Always reset for a fill ball

if st.session_state.current_shot == 1 or pins_are_reset:
    st.write("Select the pins **left standing**.")
else:
    st.write("Select the pins **still standing** (for an Open frame).")

# Create a robust 2-column layout for checkboxes
c1, c2 = st.columns(2)
with c1:
    for pin in range(1, 6):
        disable_pin = is_strike or is_spare or (st.session_state.current_shot == 2 and not pins_are_reset and pin not in pins_available_for_shot2)
        pins_selected[pin] = pin_checkbox(pin, disabled=disable_pin)
with c2:
    for pin in range(6, 11):
        disable_pin = is_strike or is_spare or (st.session_state.current_shot == 2 and not pins_are_reset and pin not in pins_available_for_shot2)
        pins_selected[pin] = pin_checkbox(pin, disabled=disable_pin)

# --- Submission Logic ---
def submit_shot():
    pins_knocked_down_str = "N/A"
    
    # Determine arrows/breakpoint
    use_trajectory = st.session_state.current_shot == 1 or \
        (st.session_state.current_frame == 10 and st.session_state.current_shot > 1)
    arrows = st.session_state.arrows_pos if use_trajectory else None
    breakpoint = st.session_state.breakpoint_pos if use_trajectory else None

    shot_res = st.session_state.shot_result
    
    pins_left_standing = sorted([pin for pin, selected in pins_selected.items() if st.session_state.get(f"pin_{pin}")])
    
    # Calculate pins knocked down based on context
    if st.session_state.current_shot == 1:
        if shot_res == "Strike":
            pins_knocked_down = list(range(1, 11))
            st.session_state.pins_left_after_first_shot = []
        else: # Leave
            pins_knocked_down = [p for p in range(1, 11) if p not in pins_left_standing]
            st.session_state.pins_left_after_first_shot = pins_left_standing
    else: # Shots 2 or 3
        prev_pins_left = st.session_state.pins_left_after_first_shot
        if shot_res == "Spare":
            pins_knocked_down = prev_pins_left
        elif shot_res == "Strike": # e.g., Shot 2 in 10th frame
            pins_knocked_down = list(range(1, 11))
        else: # Open
            pins_knocked_down = [p for p in prev_pins_left if p not in pins_left_standing]
        
        st.session_state.pins_left_after_first_shot = pins_left_standing # Update for shot 3 if needed

    pins_left_standing_str = ", ".join(map(str, pins_left_standing))
    ball_reaction_str = st.session_state.ball_reaction if st.session_state.ball_reaction else "N/A"

    con.execute(
        "INSERT INTO shots (game_number, frame_number, shot_number, shot_result, pins_knocked_down, pins_left, lane_number, arrows_pos, breakpoint_pos, ball_reaction) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (st.session_state.game_number, st.session_state.current_frame, st.session_state.current_shot, shot_res, pins_knocked_down_str, pins_left_standing_str, st.session_state.lane_number, arrows, breakpoint, ball_reaction_str)
    )
    st.success(f"Frame {st.session_state.current_frame}, Shot {st.session_state.current_shot} submitted!")

    # --- State Transition Logic ---
    frame = st.session_state.current_frame
    shot = st.session_state.current_shot
    
    if frame < 10:
        if shot == 2 or shot_res == "Strike":
            st.session_state.current_frame += 1
            st.session_state.current_shot = 1
            st.session_state.pins_left_after_first_shot = []
        else:
            st.session_state.current_shot = 2
    else: # Frame 10
        if shot == 1:
            st.session_state.current_shot = 2
            if shot_res == "Strike":
                st.session_state.pins_left_after_first_shot = [] # Reset for next shot
        elif shot == 2:
            frame10_shot1 = con.execute("SELECT shot_result FROM shots WHERE frame_number = 10 and shot_number = 1").fetchone()[0]
            if frame10_shot1 == "Strike" or shot_res == "Spare":
                st.session_state.current_shot = 3
                st.session_state.pins_left_after_first_shot = [] # Reset for fill ball
            else: # Game over
                st.session_state.game_over = True
        elif shot == 3: # Game over
            st.session_state.game_over = True

    # Clear UI elements
    for i in range(1, 11): st.session_state[f'pin_{i}'] = False
    st.session_state.ball_reaction = ""

if not st.session_state.game_over:
    st.button("Submit Shot", use_container_width=True, on_click=submit_shot)
else:
    st.balloons()
    st.header("🎉 Game Over! Final Score: " + str(total_score))

# --- Analytical Dashboard ---
st.header("📊 Game Data")
try:
    df = con.execute("SELECT * FROM shots ORDER BY frame_number, shot_number").fetchdf()
    if not df.empty:
        col1, col2, col3 = st.columns(3)
        strike_df = df[(df['shot_result'] == 'Strike')]
        strike_counts = strike_df['lane_number'].value_counts()
        total_frames_left = df[df['lane_number'] == 'Left Lane']['frame_number'].nunique()
        total_frames_right = df[df['lane_number'] == 'Right Lane']['frame_number'].nunique()
        
        with col1:
            left_strike_percentage = (strike_counts.get('Left Lane', 0) / total_frames_left * 100) if total_frames_left > 0 else 0
            st.metric(label="Left Lane Strike %", value=f"{left_strike_percentage:.2f}%")
        with col2:
            right_strike_percentage = (strike_counts.get('Right Lane', 0) / total_frames_right * 100) if total_frames_right > 0 else 0
            st.metric(label="Right Lane Strike %", value=f"{right_strike_percentage:.2f}%")
        with col3:
             st.metric(label="Total Strikes", value=len(strike_df))

        # Display full data table, hiding the ID and renaming columns
        display_df = df.drop(columns=['id', 'pins_knocked_down'])
        
        column_renames = {
            "game_number": "Game",
            "frame_number": "Frame",
            "shot_number": "Shot",
            "shot_result": "Result",
            "pins_left": "Pins Left",
            "lane_number": "Lane",
            "arrows_pos": "At Arrows",
            "breakpoint_pos": "At Break",
            "ball_reaction": "Notes",
        }
        display_df = display_df.rename(columns=column_renames)
        
        st.dataframe(display_df, hide_index=True)
    else:
        st.info("No shots submitted yet. Submit a shot to see the data.")
except duckdb.Error as e:
    st.error(f"An error occurred with the database: {e}")

# --- AI Assistant ---
st.header("🤖 AI Assistant")



if 'df' in locals() and not df.empty:
    if st.button("Get AI Suggestion"):
        if "GEMINI_API_KEY" not in st.secrets or not st.secrets["GEMINI_API_KEY"]:
            st.error("Please add your Gemini API Key to your Streamlit secrets.")
        else:
            api_key = st.secrets["GEMINI_API_KEY"]
            suggestion_placeholder = st.empty()
            with st.spinner("🤖 Calling the coach for advice..."):
                suggestion = get_ai_suggestion(api_key, df)
                suggestion_placeholder.markdown(suggestion)
else:
    st.info("Submit some shots to get an AI-powered analysis.")