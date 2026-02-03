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
        model = genai.GenerativeModel('gemini-pro')

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
                lane_number VARCHAR,
                arrows_pos INTEGER,
                breakpoint_pos INTEGER,
                ball_reaction VARCHAR
            );
        """)
    return st.session_state.db_connection

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

        # Clear the local table for the next game
        con.execute("DELETE FROM shots")

    except KeyError:
        st.error("Azure credentials not found. Please configure .streamlit/secrets.toml")
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

# Get DB connection
con = get_db_connection()

# --- Game Management ---
st.sidebar.header("Game Management")
st.sidebar.metric("Current Game", st.session_state.game_number)
st.sidebar.metric("Current Frame", st.session_state.current_frame)
st.sidebar.metric("Current Shot", st.session_state.current_shot)

if st.sidebar.button("Start New Game"):
    st.session_state.game_number += 1
    st.session_state.current_frame = 1
    st.session_state.current_shot = 1
    st.session_state.pins_left_after_first_shot = []
    con.execute("DELETE FROM shots")
    st.experimental_rerun()

if st.sidebar.button("Save Game to Azure"):
    upload_to_azure(con, st.session_state.game_number)


# --- Shot Input ---
st.header(f"Frame {st.session_state.current_frame} - Shot {st.session_state.current_shot}")

# --- Shot Information UI ---
col1, col2 = st.columns(2)

with col1:
    if st.session_state.current_shot == 1:
        shot_result_options = ["Strike", "Leave"]
        shot_result_label = "First Shot Result"
    else: # Second shot
        shot_result_options = ["Spare", "Open"]
        shot_result_label = "Second Shot Result"
    
    shot_result = st.selectbox(shot_result_label, shot_result_options, key="shot_result")

with col2:
    if st.session_state.current_frame == 1 and st.session_state.current_shot == 1:
        st.session_state.starting_lane = st.selectbox("Starting Lane", ["Left Lane", "Right Lane"], key="lane_select")
        lane_number = st.session_state.starting_lane
    else:
        # Determine lane based on starting lane and frame number
        if st.session_state.starting_lane == "Left Lane":
            lane_number = "Left Lane" if st.session_state.current_frame % 2 != 0 else "Right Lane"
        else: # Started on Right Lane
            lane_number = "Right Lane" if st.session_state.current_frame % 2 != 0 else "Left Lane"
    
    st.metric("Current Lane", lane_number)
    st.session_state.lane_number = lane_number

if st.session_state.current_shot == 1:
    st.subheader("Ball Trajectory")
    col1, col2 = st.columns(2)
    with col1:
        arrows_pos = st.selectbox("Position at Arrows", options=list(range(1, 40)), index=16, key="arrows_pos")
    with col2:
        breakpoint_pos = st.selectbox("Position at Breakpoint", options=list(range(1, 40)), index=9, key="breakpoint_pos")

ball_reaction = st.text_input("Ball Reaction (e.g., broke early, held line)", key="ball_reaction")

# --- Pin Selection UI ---
st.subheader("Pin Selection")

# Base64 encoded SVG for a bowling pin. It's a simple design that can be colored with CSS.
PIN_SVG_RAW = """
<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg">
    <path d="M32 0C24.3 0 18 6.3 18 14c0 3.9 1.6 7.4 4.2 9.9C16.3 26.9 12 34.5 12 43c0 8.3 4.5 15.4 11 19.2V64h18v-1.8c6.5-3.8 11-10.9 11-19.2 0-8.5-4.3-16.1-10.2-19.1 2.6-2.5 4.2-6 4.2-9.9C46 6.3 39.7 0 32 0z" fill="{color}"/>
</svg>
"""

# CSS to style checkboxes as clickable images
st.markdown(f"""
<style>
    .stCheckbox > input {{
        display: none;
    }}
    .stCheckbox > label {{
        display: inline-block;
        width: 50px;
        height: 50px;
        background-image: url("data:image/svg+xml;base64,{
            base64.b64encode(PIN_SVG_RAW.format(color="gray").encode()).decode()
        }");
        background-size: contain;
        background-repeat: no-repeat;
        cursor: pointer;
        position: relative;
        text-align: center;
        line-height: 50px;
        font-weight: bold;
        color: black;
        font-size: 18px;
    }}
    .stCheckbox > input:checked + label {{
        background-image: url("data:image/svg+xml;base64,{
            base64.b64encode(PIN_SVG_RAW.format(color="white").encode()).decode()
        }");
        color: white;
    }}
    .stCheckbox > input:disabled + label {{
        background-image: url("data:image/svg+xml;base64,{
            base64.b64encode(PIN_SVG_RAW.format(color="#333").encode()).decode()
        }");
        cursor: not-allowed;
    }}
</style>
""", unsafe_allow_html=True)

pins_selected = {}

# --- Pin Display Logic ---
_, center_col, _ = st.columns([1, 1, 1])
with center_col:
    # Helper to create a checkbox for a pin
    def pin_checkbox(pin_num, disabled=False):
        return st.checkbox(str(pin_num), key=f"pin_{pin_num}", disabled=disabled)

    # Pin layout using columns
    pin_layout = {
        'row4': [7, 8, 9, 10],
        'row3': [4, 5, 6],
        'row2': [2, 3],
        'row1': [1]
    }
    
    is_strike = st.session_state.current_shot == 1 and st.session_state.shot_result == "Strike"
    is_spare = st.session_state.current_shot == 2 and st.session_state.shot_result == "Spare"
    pins_available_for_shot2 = st.session_state.pins_left_after_first_shot

    if st.session_state.current_shot == 1:
        st.write("Select the pins **left standing**.")
    else:
        st.write("Select the pins **still standing** (for an Open frame).")

    # Row 4
    row4_cols = st.columns(4)
    for i, pin in enumerate(pin_layout['row4']):
        with row4_cols[i]:
            disable_pin = is_strike or is_spare or (st.session_state.current_shot == 2 and pin not in pins_available_for_shot2)
            pins_selected[pin] = pin_checkbox(pin, disabled=disable_pin)
    
    # Row 3
    _, r3c1, r3c2, r3c3, _ = st.columns([0.5, 1, 1, 1, 0.5])
    row3_cols = [r3c1, r3c2, r3c3]
    for i, pin in enumerate(pin_layout['row3']):
        with row3_cols[i]:
            disable_pin = is_strike or is_spare or (st.session_state.current_shot == 2 and pin not in pins_available_for_shot2)
            pins_selected[pin] = pin_checkbox(pin, disabled=disable_pin)

    # Row 2
    _, r2c1, r2c2, _ = st.columns([1, 1, 1, 1])
    row2_cols = [r2c1, r2c2]
    for i, pin in enumerate(pin_layout['row2']):
        with row2_cols[i]:
            disable_pin = is_strike or is_spare or (st.session_state.current_shot == 2 and pin not in pins_available_for_shot2)
            pins_selected[pin] = pin_checkbox(pin, disabled=disable_pin)
    
    # Row 1
    _, r1c1, _ = st.columns([1.5, 1, 1.5])
    with r1c1:
        pin = pin_layout['row1'][0]
        disable_pin = is_strike or is_spare or (st.session_state.current_shot == 2 and pin not in pins_available_for_shot2)
        pins_selected[pin] = pin_checkbox(pin, disabled=disable_pin)


# --- Submission Logic ---
def submit_shot():
    pins_knocked_down_str = "N/A"
    
    arrows = st.session_state.arrows_pos if st.session_state.current_shot == 1 else None
    breakpoint = st.session_state.breakpoint_pos if st.session_state.current_shot == 1 else None

    if st.session_state.current_shot == 1:
        if st.session_state.shot_result == "Strike":
            pins_knocked_down = list(range(1, 11))
            st.session_state.pins_left_after_first_shot = []
        else: # Leave
            pins_left = sorted([pin for pin, selected in pins_selected.items() if st.session_state.get(f"pin_{pin}")])
            pins_knocked_down = [p for p in range(1, 11) if p not in pins_left]
            st.session_state.pins_left_after_first_shot = pins_left
        
        pins_knocked_down_str = ", ".join(map(str, pins_knocked_down))

    else: # Second shot
        pins_left_after_shot1 = st.session_state.pins_left_after_first_shot
        if st.session_state.shot_result == "Spare":
            pins_knocked_down = pins_left_after_shot1
        else: # Open
            pins_still_standing = sorted([pin for pin, selected in pins_selected.items() if st.session_state.get(f"pin_{pin}")])
            pins_knocked_down = [p for p in pins_left_after_shot1 if p not in pins_still_standing]

        pins_knocked_down_str = ", ".join(map(str, pins_knocked_down))


    ball_reaction_str = st.session_state.ball_reaction if st.session_state.ball_reaction else "N/A"

    # Insert data into DuckDB
    con.execute(
        "INSERT INTO shots (game_number, frame_number, shot_number, shot_result, pins_knocked_down, lane_number, arrows_pos, breakpoint_pos, ball_reaction) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (st.session_state.game_number, st.session_state.current_frame, st.session_state.current_shot, st.session_state.shot_result, pins_knocked_down_str, st.session_state.lane_number, arrows, breakpoint, ball_reaction_str)
    )
    st.success(f"Frame {st.session_state.current_frame}, Shot {st.session_state.current_shot} submitted!")

    # --- State Transition Logic ---
    # Advance to next shot or frame
    if st.session_state.current_shot == 2 or st.session_state.shot_result == "Strike":
        st.session_state.current_frame += 1
        st.session_state.current_shot = 1
        st.session_state.pins_left_after_first_shot = []
    else: # Move to shot 2
        st.session_state.current_shot = 2

    # Clear pin selections for the next shot
    for i in range(1, 11):
        st.session_state[f'pin_{i}'] = False
    
    # Reset ball reaction
    st.session_state.ball_reaction = ""


st.button("Submit Shot", use_container_width=True, on_click=submit_shot)

# --- Analytical Dashboard ---
st.header("📊 Game Data")

# Query the database
try:
    df = con.execute("SELECT * FROM shots ORDER BY frame_number, shot_number").fetchdf()

    if not df.empty:
        # Calculate Strike Percentage
        # A strike is a single shot in a frame with the result 'Strike'
        strike_df = df[(df['shot_result'] == 'Strike')]
        
        # Count strikes per lane
        strike_counts = strike_df['lane_number'].value_counts()
        
        # Count total frames per lane. We can approximate this by looking at the unique frame numbers per lane.
        total_frames_left = df[df['lane_number'] == 'Left Lane']['frame_number'].nunique()
        total_frames_right = df[df['lane_number'] == 'Right Lane']['frame_number'].nunique()

        # Display results
        col1, col2 = st.columns(2)
        with col1:
            left_strike_percentage = (strike_counts.get('Left Lane', 0) / total_frames_left * 100) if total_frames_left > 0 else 0
            st.metric(
                label="Left Lane Strike %",
                value=f"{left_strike_percentage:.2f}%"
            )
        with col2:
            right_strike_percentage = (strike_counts.get('Right Lane', 0) / total_frames_right * 100) if total_frames_right > 0 else 0
            st.metric(
                label="Right Lane Strike %",
                value=f"{right_strike_percentage:.2f}%"
            )

        # Reorder and select columns for display
        display_df = df[['game_number', 'frame_number', 'shot_number', 'shot_result', 'lane_number', 'pins_knocked_down', 'arrows_pos', 'breakpoint_pos', 'ball_reaction']]
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
            st.error("Please add your Gemini API Key to the .streamlit/secrets.toml file.")
        else:
            api_key = st.secrets["GEMINI_API_KEY"]
            suggestion_placeholder = st.empty()
            with st.spinner("🤖 Calling the coach for advice..."):
                suggestion = get_ai_suggestion(api_key, df)
                suggestion_placeholder.markdown(suggestion)

else:
    st.info("Submit some shots to get an AI-powered analysis.")


