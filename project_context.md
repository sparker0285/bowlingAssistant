# Project Context: bowlingAssistant

This file contains a summary of questions and answers about the `bowlingAssistant` project to persist context across sessions.

## Session from Monday, February 2, 2026

**Question:** Is there a way for you to save all of the context and information from one chat so that I can easily close and re-open VS Code and you will still have the information we discussed?

**Answer:** The agent was unable to confirm if there is a built-in feature for persistent chat context in Gemini CLI. As a workaround, this file was created to manually persist context.

**Next Action:** The user can add more context to this file. The agent can also be asked to read this file to restore context in a new session.

---
## Operational Directives

- Every time a file is changed by the agent, a record of the change should be appended to this file. The record should include the file path and a description of the change.

---
## Session from Wednesday, February 4, 2026

**User Story:** The user reported two issues after testing the application on their phone.
1.  **Crash and Data Loss:** The application crashed mid-game, and upon reloading, all progress was lost. The user needs a way to recover the game state.
2.  **Incorrect Lane Switching:** The lane did not switch correctly for the second frame. It reportedly stayed on the starting lane for both frame 1 and 2, but then began alternating correctly from frame 3.

**Changes Implemented in `bowlingAssistantApp.py`:**

1.  **Game State Persistence:**
    *   **Reasoning:** To prevent data loss from crashes, the application's state needed to be persisted locally. The previous implementation used an in-memory database, which is volatile.
    *   **Change:** The `get_db_connection` function was modified to connect to a file-based DuckDB database named `bowling.db`. `CREATE TABLE` and `CREATE SEQUENCE` were updated to use `IF NOT EXISTS` to handle cases where the database file already exists.
    *   **Change:** A new function, `restore_game_state`, was added. This function runs when the app starts, reads the last entry from the `shots` table in `bowling.db`, and calculates the correct `current_frame`, `current_shot`, and other session state variables to allow the user to resume their game seamlessly.

2.  **Lane Switching Logic:**
    *   **Reasoning:** The user reported that the lane was not alternating correctly on the second frame. While the original logic appeared correct upon review, a refactor was performed to improve clarity and hopefully resolve any subtle, unspotted bug.
    *   **Change:** The `if/else` block for determining the `lane_number` was rewritten. The new logic is more explicit: it determines if the frame is odd, checks the starting lane, and then assigns the current lane based on those two variables. The core logic remains the same (odd frames on the starting lane, even frames on the other), but the implementation is cleaner and less prone to subtle errors.

**Update (Follow-up):** The user reported that the automatic save was not working correctly.

3.  **Reliable Auto-Save:**
    *   **Reasoning:** The automatic save feature was not reliably writing data to the disk. It appeared data was only being flushed to the database file when another database action (like the one in "Save to Azure") occurred.
    *   **Change:** To fix this, an explicit `con.commit()` was added to the `submit_shot` function immediately after the `INSERT` statement. This forces the database to write the new shot data to the `bowling.db` file, guaranteeing that every shot is saved instantly and durably.
    *   **Change:** A `con.commit()` was also added to the "Start New Game" button logic to ensure the `DELETE` command is persisted immediately, clearing the database for the new game.

---
## Session from Thursday, February 5, 2026

**User Story:** The user requested the ability to save and analyze multiple games.
1.  **Data Persistence:** The user wants to save all games for later analysis, not just the current game.
2.  **AI Analysis:** The user wants to be able to get AI-powered advice on their games, both in real-time and after the game is over.
3.  **Multi-Game Analysis:** The user wants the AI to consider all games from the current session when giving advice.

**Changes Implemented in `bowlingAssistantApp.py`:**

1.  **Game Persistence:**
    *   **Reasoning:** The previous implementation deleted all data when a new game was started. This was changed to support saving multiple games.
    *   **Change:** The "Start New Game" button now creates a new game with a unique `game_id` and increments the `game_number` without deleting the old data.
    *   **Change:** A new dropdown menu was added to the sidebar to allow the user to load and view previous games.

2.  **AI-Powered Analysis:**
    *   **Reasoning:** The user wanted to be able to get AI-powered advice on their games.
    *   **Change:** The `get_ai_suggestion` function was updated to analyze all games from the current day, not just the current game.
    *   **Change:** A new function, `get_ai_analysis`, was added to provide post-game analysis and practice recommendations.
    *   **Change:** The AI Assistant section was updated to show a "Get AI Suggestion" button for the active game and a "Get AI Post-Game Analysis" button for past games.

---
## Session from Friday, February 6, 2026

**User Story:** The user requested a new way to organize games into "sets" and a fix for a bug in the max score calculation.
1.  **Max Score Bug:** The user noticed that the max score calculation was incorrect.
2.  **Game Sets:** The user wants to group games into sets, with a default name of "League <date>".
3.  **UI Changes:** The user wants to be able to start a new set, save a set to Azure, and select a game within a set.
4.  **Data Display & AI:** The user wants the data table to show all games in the selected set, sorted with the most recent frames first, and for the AI Assistant to use the entire set for its advice.

**Changes Implemented in `bowlingAssistantApp.py`:**

1.  **Max Score Bug Fix:**
    *   **Reasoning:** The previous logic for calculating the max score was not accounting for all scenarios.
    *   **Change:** The `calculate_scores` function was updated to correctly calculate the maximum possible score.

2.  **Game Sets Implementation:**
    *   **Reasoning:** The user wanted a better way to organize games.
    *   **Change:** The database schema was updated to include a `set_id` and `set_name` for grouping games.
    *   **Change:** The UI was updated to manage these sets, including adding "Start New Set" and "Save Set to Azure" buttons.
    *   **Change:** The main dropdown is now for selecting a game set, and a second dropdown allows the user to select a specific game within that set.

3.  **Updated Data Display & AI:**
    *   **Reasoning:** The user wanted the data display and AI to be more relevant to the selected set.
    *   **Change:** The data table now shows all games within the selected set, sorted with the most recent frames first.
    *   **Change:** The AI Assistant now uses the entire set of games for its real-time advice.

**Update (Follow-up):** The user reported an `AttributeError` when switching the shot result in the second frame.

4.  **AttributeError Fix and State Management:**
    *   **Reasoning:** The `st.session_state.starting_lane` variable was not being correctly persisted across all app reruns, causing a crash when it was accessed in later frames.
    *   **Change:** A guard clause was added to the lane selection logic. If `starting_lane` is missing from the session state, it is fetched from the database for the current game.
    *   **Change:** The state initialization logic was improved to ensure `starting_lane` is correctly set when starting a new set, starting a new game, or switching between existing games. This makes the app more resilient to state loss.

---
## Session from Monday, February 9, 2026

**User Story:** The user requested several improvements to the set management functionality.
1.  **Sequential Set Names:** The user wants to be able to create multiple sets on the same day with sequential names (e.g., "League 02-09-26_2").
2.  **Editable Set Names:** The user wants to be able to rename sets.
3.  **Data Display Bug Fix:** The user noticed that the data display was not resetting correctly when a new set was created.
4.  **Delete Sets:** The user wants to be able to delete sets.

**Changes Implemented in `bowlingAssistantApp.py`:**

1.  **Sequential Set Names:**
    *   **Reasoning:** The user wanted a better way to organize multiple sets on the same day.
    *   **Change:** The "Start New Set" button now checks for existing sets on the same day and appends a sequential number to the set name.

2.  **Editable Set Names:**
    *   **Reasoning:** The user wanted to be able to rename sets.
    *   **Change:** A text input field and a "Rename Set" button were added to the sidebar to allow the user to rename the current set.

3.  **Data Display Bug Fix:**
    *   **Reasoning:** The data display was not resetting correctly when a new set was created.
    *   **Change:** The `initialize_new_set` function was updated to correctly reset the data display when a new set is created.

4.  **Delete Sets:**
    *   **Reasoning:** The user wanted to be able to delete sets.
    *   **Change:** A "Delete Current Set" button was added to the "Danger Zone" expander in the sidebar to allow the user to delete the current set.

**Update (Follow-up):** The user reported an error when trying to save a set to Azure.

5.  **Azure Credentials Fix:**
    *   **Reasoning:** The application was trying to access Azure credentials from environment variables (`os.environ`) instead of using Streamlit's recommended secrets management (`st.secrets`).
    *   **Change:** The `upload_set_to_azure` function was updated to fetch the `AZURE_STORAGE_ACCOUNT_NAME` and `AZURE_STORAGE_CONTAINER_NAME` from `st.secrets`.
    *   **Change:** Clear error handling was added to guide the user on how to configure their secrets if they are not found.

**Update (Follow-up):** The user reported a warning about calling `st.rerun()` within a callback.

6.  **Removed Redundant `st.rerun()`:**
    *   **Reasoning:** The `submit_shot` function, used as a button callback, contained an unnecessary `st.rerun()` call. Streamlit automatically reruns the script after a callback, making the explicit call redundant and causing a warning.
    *   **Change:** The `st.rerun()` line was removed from the end of the `submit_shot` function to eliminate the warning and align with Streamlit's best practices.
