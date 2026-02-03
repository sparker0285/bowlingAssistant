# 🎳 Bowling Assistant

A Streamlit web application to act as a bowling assistant during games, allowing users to track pin breakdown and analyze performance.

## 🚀 Features

*   Visual 10-pin interface to select pins left standing.
*   Track throw results (Strike, Spare, Open) and lane number (Left/Right).
*   In-memory database using DuckDB to store frame data for the session.
*   A simple analytical dashboard showing strike percentages for each lane.

## 🏃‍♀️ How to Run

1.  **Install dependencies:**
    Make sure you have Python installed. Then, install the required libraries using pip:
    ```bash
    pip install -r requirements.txt
    ```

2.  **Run the Streamlit application:**
    ```bash
    streamlit run bowlingAssistantApp.py
    ```

3.  Open your web browser and navigate to the local URL provided by Streamlit.
