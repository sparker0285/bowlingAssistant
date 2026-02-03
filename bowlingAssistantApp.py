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

# Static reference image for pin layout provided by the user
PIN_LAYOUT_IMAGE = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAKMAAACUCAMAAADIzWmnAAAArlBMVEX/////AAAAAAD/9PT/+/v/8fH/9/f/7Oz/5+f/3Nz/xMSioqL/0tL/S0v/Jyf6+vr/dXX/Nzf/PT3/CQnR0dH/ISH/Ly//V1f/a2v/19f/l5f/Rkb/nJz/u7v/iIj/p6ft7e3k5OT/XV3/y8v/kZH/Y2P/GBh8fHyFhYXb29sPDw//gYH/e3v/rq7CwsL/UVEvLy9dXV2wsLCXl5cZGRlwcHBBQUE4ODhPT08iIiJX6mZ9AAAP/UlEQVR4nO1cWWOqOhAGBAVEUBE3RHGtXMHdav//H7uZsCUhaD2nnvah30PrEpPJzGQyS4IgZFD14cwRbXM5nKvCfVQGUbNtiyt342nyg7aqt+itULf9kVF70FRQ58OlaYvObKjzSFDDtpihvbjbXTTLm6763r2mWrObt20MK/fW1hYkCSFLZWWBv+i6jYZr4peTUv4YeFSn3Wg0Wja8nGllTZUw79aBV3ZUSqE8wW1NaBvPa0HNqLaEz8aeplSriuE14V2zZM4joKs3MdRqtaLqI5i6o/Obqjtg9MLTKtUK6rYP3W5K5l6JB/UMIEHzxvBuSUhTQ3TbY4K3KvygxZO3DD9uzPOBqhGi0p7whjXQbOyRkv9WAyrdKq9trQUUkiSM0a+7mYRqSLpdhhX6CpGiCAUAiQuaFRUQAkcpNTRI26A/i0A1OEQqDcRwlgTEObOWj1HkmYYI7xf68kBT2Q+Bt+aAN2yjoC8GiLtII2KwWdBq4O0y7gEtly5HrBriJKvhQDhPrH0O05G6tDkqPUfdFtQXsXfFWXg1xMkFvFCR0nB1HvGszaz/DY+30FmjMJ8B6rbAWwBavj1G2mqbqyxI3EhdgARkHsbxJ9vLG8YliN8jTozovpBSpFQHb+eP/clP3s1RZ1RTuZ9z3Dq+78+XtKmwK8xnhJYL+neU3tJf1K8f162F1T+M55CMW5f2GFKQDdyl+honrEfwz7fA3+4PVvK+x3ACaUo3kb61lrbT4PCeEqmjZUPT2BXtueBfztI6+eBtv/W30hFzBclSz9go1OMm9Wv6W8QLUktkVxRTrXuTOnjmx+T9nFkKSKLD5CUeTLCkS/K+gnYpSv810CD/Ug/OCY1HaYv+riUfs0UXhjkDgg7+l40LMiCFPTDFVvr6sJ/i4U/J+2Ul7siBl7mSXyRoKtyklOdjhud4GGsqdP6LabQuZ2jakep4UQyFGWEo42nj0dMJLolvvFzUaJI0H2WG5yuxnZJ8DvwAUjvJBzrD82Xy05RG//wO/6YfN7y9zARHdGmrcVhnL6toCRLfTIj5+/uDb3Wul5Q3wBxyGSNjlHUY03jI5qMyCol0uUrSOI3VYvr+bgmKKzrQGWUJOtI0fyOKO5rG3EhND9fbx5ufvUcCm1M0Zr9MmC2B6DAq9NRhoQscGv0bWmZVZNUEm6Fx/U6N1CD2PZLG4HwYIj7eMiJpGmWCRusgdazp20dGo0LTKDfu02izsrbwkkpQLmsrXjOdzFrAbkXuzYSsBX8t3a6nY9azyvCxRNZ+Juul2CX1qJ5z5t6a6fyHza21vybN0Zqx6TXTYvaoupT27OXmDqOwZj7iNXM+IFvSRSQMaUPwfiLeEEYOYDhiO6XxHNN4TWmsmGKDtD2zwgZ7+EhfLRjbM0y2pILtOSW2hza+2w+f+PGy1IYjOfi4m3Rhs/Ykyqc3xU39TNRKL98KMFJxpTQiswtt34DxG1BztSXaGQOstwPxW2MlmhQrxvnAaG/1heCW2bwZZy9MvJ6ptPat7f6UminElR3tgZriyhCCbV06bDu41WXfQYSiNVazsc6MCe3wr4ThEfqsI0b6FJ3D7XC7pM3R1k5PRw6zPcpfv99uh2zzEoo+0gQ2w+MBEFsz63S7vW+FjDjwzeYCB7pY0PswdlASZPZbqDQKfiVyZvNFZBFfIC1gdg2QZdGnBMwT3wxo7XKiWYPjz6Idm3HXYpT4uA2O6wzDFuhBjDSNYlu1m8q4Ois6s4jEFm14YkCsUCASYoVVwZ+toBXWKxAJbnhYpAbClQKR4DamwY+KftdixpibfDZAzDVkPuvxY64B6tZl5u6til44ABx5k9G4AeLSKvs9xK7igjAzGsSuPBKxMRPber4ulQgNWwh8MEBbxBFB5QBi1x43wAYixSZJAmQlSJdMhRbmxsM6VfOaJhOAkxjh6HyE8zyysQAm8hUejQNBc2usYyOkRk2YTViSWsBpCLPp4VEVbwMkNGgpLOIMiug48f8ub2nEMBpJpif5idgvT2GFSXfOKv7f4iYLksl3aRLMBdtC2+T5INEelqZwYJaRS7Rd3s1JGU2iqTMqnw2QMLSJxhseCVUvbLRbLXcXzu+mtwTIiox6brvVdpeTR2lAJLdm3O3G4GZRSFTm4c5ttdqN0CtvK9eUR/nEfPDawzGJbj/bVJCV2qdJ+MUvfvFHUAbeYtaYbaLHVQEoCzR3u/5I1x6veNWYhLPGcuhpjwzdQwqHvcyouot7hh35C+mm8rDYgEz7OC8hzEaftmEcyMN4C3NME+8AdqEeQYzqJhukGf+my/WbY+C8OOrONONZmeVFjEdQZ7H74Bma4U3wplZMKSfAnkZrHOmaNo+GmEnjkqbYzRVX4UQfoH5HWFDNz9t4CpBsRu5SKghZXaAPVlwvrApuZTvKNKsyB49nyZch1GHMUbaZVAfg8bBRyecALmqLdrq0Em8W4gJxTMsLKJnxRAhfMH6f5/DDlUeQ25xUPhR6nKK4YdiC0wVhBaeCAIIes7SrZRO6jxm/PgNFCtZY6Hzuoji66J+rfEWFCgJnQvcB4/K4D1UXNp5plawPxN42K4mwhGEGL2y7jwqTgz9mWQYNMZIm3suHnZ4w0iReoTABqQ87k45/fHs7bZOOobbwnLQHorgjRNqRsqQXDEzr3iyPZ1A7QJr0h0CV7ndBSKFzfdtuD2kRA4pazy2bkCLEv+1zGgdMxQ+xJquQdA6CBUjfL+mkpKC0RCfdrIIPYLd/TWhkCwSP0RUdYlLryymnkS1dRETfHTKlFX9HaapGCHQt4X/TdEJorg3hCdRWopu/Cz6m65xGNjs/JpLMMR+JfpjanZfPx/qPmY+M+PIMjYZN9G29H+NcYIIJrZAzIhHceT9BiiynEqk1acCI7OhUqgeny3qbt90RWYlPQCdlVH8XKBojWnFcIlnUkY7T6TGraEHKmcp2NEXbyJrebsfgKL0RyssvgpaArBYFUAwhafRoC9klkjoWblXPK2VdmsZ+bgS3El7ReRUKfWlzcmelmBPJrQNkyNflfNyJLcav7PyXMdKmaQzzVGdHwvnxqXRLv3ySj5qdVScQUxBuUv2Y5rOZLH8fEsUUpte0HCszuwqhykFMnL/PKqgtcfWMSy7nNq9Th43jXVqfUqGEdCafJDleLcFHmskfMO1az3XIii29/7FPPqiZTLr6EXpM/pnQR7WbVabTcVM6rBvm9TGrxo4Zr6JGrPO4yyCrQnnc5OkdsMaXoNFjPBQ1r4hYB9g1OvtrwkbFZaoc1X5uTAOodFiHc5L2l5t0wfExVDvftBCFt/359p5sWibriIVE+et2vd2uWaUzomuizCed/e1wu6VbIXJ82k8GDAsyGz4NptMgiJkzYop9WIBOtmqsaba5CapTYE2VOt0RbIPstcs/8XIPaskpGXCjWZFsEAs4oYuMNvY+624hTXF4ITDiSevpEJZ72gicArGQXlV2iLcFswEKZhbzBk2Rt+VBlv05bcQYc4jUnUIdDQABwI4hR4Zoj5MnV9qcMgeQ+JxjlgCSz9SxQRVHqLymBoS5HqHysod0xeYmKxQIa0fkjAaQPmADkE8CSFptUl4MFtB5n7/2INAVe1EiRCWCRAR7xi0FPnHophl3OT7J+EdcBHg402+3m2FzhzMkTkEXUyj4BKXtLMOwbz44uSmE0Jm9moVhMz7l2foDXUyhLshMvxje2/O9Btm0cTeFM++TbZ3R45TcPdS8XtdZ2bbTde8XLpDt08K2iZquzG74qIQgq8N218FtZ/of5nrosee6/sksYc3QdeOT3nRloOtz9beG8Itf/CTI1b/Jtf8DGJNhs9/fjLzHhqamT0ajUWT8bY3jOSiT1irbJGbzeyauGvXirVFcmf35v2P8xBEp9MtD90mLatkrcTO+GvFdjtZorqqq5jWBXn6xAUkZb8tOf6Lr3qiH2fl0QvlPAOeCxCbhXEzcsloMvsTSzJxIOYKyRO+PihxPAZ9xoX1Xpc93UQeIRIdqWoECRPvVOjScSy2We7i1Dzg4s2NdGjh9xT/g83UY8u5n4KiuEEjNuKeYBveKdF8CSMIni3ja2W6DLJRGsY9LN53kDPe39fWpk7SF4Pd+/fYvsUkjV6v+caifpEN6/lFhw33Q28TQBB8f9eNJWvvx22HJ3ZEvgpId8JvuIe0U5MnDiDErXh7/b3GuKE+G2oVk4Fciz1T58S2OdXbWFTGSWku7PLieYuLyOxWTv4gCH2OZCdSKhXzKT5Nv2PPhLr0/T7OEH4pzX7e04Vwz/clbfnEgosyPwSqdf8iO+kKB52Ubt9piKih+nviGa01EipPJmdcPV+JqQ5O5P/OVGJjMBaLL3ir5ckHneoLtibh/wVwb+GoaqRO6RymgviTykuNCGizIqzaL0kOdfw+tS1X06uQtFrgoQEyAQwZ1D+llfIS0XP5uC3YnLw5GVGKS1Mepj/9dMq43n60CP4MGoexHnGw/ZVcwRpQ7RK7rS1xyf0/5qPReuK5BSKl92UqnLcIlpbHao7dhIgN/Sa6npfo4sF/p+iCnZRn3HlylM/m//865Yc6upsbY5Ww97q+X9eGcFS+fLqY/Bzcd2Qo6MfzkmxnjQerZdAQwPcdt2hAuNzkv3K9hKXS5XyCSbEp+hN/DYPFavwcn4pccXYJ8OJP8hroEz0+MSg7lfB00rhtda3NI73GvrMMhttf64TF7lkzGQTfpkzYx4N5H4VI13KkoeyLA1wHiqx2paVX4pMsRK7y1N6nIW4Y6jPsPcioexPI7rwZDyYqGj3EuuRoG91/EhpecipcVnLRovszkITHYxZWDzXjTj0+slkkvTmmzmeEkmiz6OE9RWjz5YlQi4tkdot0rD/Nkj3hyB8zrlYaRhTfugeBstz98YEiiTUpmY/wXlaI/gqJqg4H2mdKFqs2jSWRo/0QRf/GLX3w9nrkV9R2oeqGb3C77t3WOT8OgLv+NXppj/DPI4/S2Y3qJ8tkTRS8HdnG6Yy+5NdoHQkuOh3wXoIQgknc7NTgCMvtJRMIZpDajgPBAqkJZ4fsAZ7kaBfcHrtA0ec2/BciT7XI4Nuc8kOq7oNJBfX5eEMVn7g9xyEKyEmddske34Ms2P4ORcFE/83Q71zeCRiTtp473vwzUE1rq2ylBI2SmfoSwN3RamaJx9PwB1pfApRMAFI3Gs+epXwTmnCtFo/LiHNlnwVxLomgU+Gm2f457NMo/hsYeuclQNLL3kL4LLn03haJx8EO27Hu2Z/hDbA9z/cInaZy9Oq/8ScD5k8z4TNcX6bZOK/46+3DGb0Of8Cn8I1SWgvybn+FTwOJdcbN2Ef/q8bcArZoWJ6CGx+P+EDaiHY973AiegvQjDE8MHHMx4oYiye4nHTQFnq2IZ2XLKpyT4z+Q6tug4ic4LOiHUv8kLgKUcZxByR/uHf0EZ4KBFhLnWt3hTzE6DFR90XPwU+HnX6qJ/wPwGS+SC9x2MAAAAABJRU5ErkJggg=="
st.markdown(f'<img src="{PIN_LAYOUT_IMAGE}" alt="Pin layout">', unsafe_allow_html=True)

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