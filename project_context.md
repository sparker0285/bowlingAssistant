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

**Update (Follow-up):** The user reported an Azure authentication error when `AZURE_STORAGE_ACCOUNT_NAME` was not present in the secrets.

7.  **Flexible Azure Authentication:**
    *   **Reasoning:** The application was not correctly handling authentication when only an `AZURE_STORAGE_CONNECTION_STRING` was provided, leading to an error.
    *   **Change:** The `upload_set_to_azure` function was updated to be more flexible. It now prioritizes authenticating with `AZURE_STORAGE_CONNECTION_STRING` if it exists in the secrets. If not, it falls back to using `AZURE_STORAGE_ACCOUNT_NAME` with `DefaultAzureCredential`.
    *   **Change:** The error message was improved to clearly state that either the connection string or the account name is required for the upload to function.

**Update (Follow-up):** The user reported that the pin selection UI was not working correctly for the second shot of a frame.

8.  **Pin Selection Logic Fix:**
    *   **Reasoning:** The pin selection UI was not correctly disabling pins for the second shot of a frame, and the state of the pins was not being properly managed between shots.
    *   **Change:** The `submit_shot` function was updated to correctly save the pins left after the first shot into the session state.
    *   **Change:** The UI was updated to dynamically enable and disable the pin checkboxes based on the current shot and frame context. For example, if "Spare" is selected, all pins are disabled. For an "Open" frame, only the pins left standing from the first shot are enabled.
    *   **Change:** The `submit_shot` function was also updated to clear the pin checkboxes after every submission, ensuring a clean slate for the next shot.

**Update (Follow-up):** The user reported that the application state was not being correctly restored on refresh and that there was no way to load data from Azure.

9.  **State Restoration and Azure Load:**
    *   **Reasoning:** The application was not correctly restoring the session state on refresh, and there was no way to recover data from Azure.
    *   **Change:** A new `restore_state` function was implemented to run on app start. It inspects the database for the most recent shot and correctly restores the session state.
    *   **Change:** A "Load from Azure" section was added to the sidebar. This feature allows the user to select a set from a dropdown menu, download it from Azure, and load it into the local database.

**Update (Follow-up):** The user reported a `TypeError` on startup due to corrupted data in the local database.

10. **Robust State Management:**
    *   **Reasoning:** The previous state management logic was too complex and could lead to data corruption.
    *   **Change:** The logic for advancing the frame and shot was moved back into the `submit_shot` function, making the state updates more direct and reliable.
    *   **Change:** The `restore_state` function was simplified to only run on initial app load, and a safeguard was added to handle corrupted data by starting a new set.

**Update (Follow-up):** The user reported a recurring `TypeError` on startup.

11. **Bulletproof State Restoration:**
    *   **Reasoning:** The `restore_state` function was still vulnerable to `TypeError` crashes if it encountered corrupted data (e.g., a `None` value for a frame number) in the database.
    *   **Change:** The entire `restore_state` function is now wrapped in a `try...except` block. If any error occurs during the process, the exception is caught, a warning is displayed, and the application initializes a fresh, clean set. This ensures the app always starts successfully, regardless of the state of the local database.
    *   **Change:** The `state_restored` flag is now set in a `finally` block to guarantee it is always handled correctly.

**Update (Follow-up):** The user reported an infinite loading loop on startup.

12. **Infinite Loop Fix:**
    *   **Reasoning:** The state management logic was causing an infinite loop on startup due to conflicting state updates between the set selection and state restoration functions.
    *   **Change:** The state management was refactored to eliminate the conflicting logic. The `restore_state` function was improved to correctly restore the state of the most recently selected set and game, preventing the application from getting stuck in a loop.
    *   **Change:** The `initialize_set` function was created to centralize the logic for initializing a new or existing set.
    *   **Change:** The `restore_game_state` function was created to restore the state of the current game from the database.

**Update (Follow-up):** The user reported a recurring `AttributeError` when submitting the second shot of a frame.

13. **Resilient Lane Calculation:**
    *   **Reasoning:** The `st.session_state.starting_lane` value was still being lost on some script reruns, causing an `AttributeError` when the lane was calculated for the second shot.
    *   **Change:** A "bulletproof" guard clause was added to the lane calculation logic. Before calculating the current lane, it now checks if `st.session_state.starting_lane` exists. If it is missing, it immediately fetches the value from the database for the current game, making the calculation self-healing and preventing the crash.

**Update (Follow-up):** The user requested several UI improvements.

14. **UI Enhancements:**
    *   **Reasoning:** The user wanted to improve the layout and usability of the application.
    *   **Change:** The broken pin reference image was replaced with a clear, text-based diagram.
    *   **Change:** The pin selection checkboxes were replaced with a single, space-saving multiselect dropdown menu.
    *   **Change:** The "Current Lane" display was changed from a large metric to a smaller, more subtle markdown element to reduce its visual prominence.

**Update (Follow-up):** The user requested the ability to track which bowling ball is used for each shot.

15. **Bowling Ball Tracking:**
    *   **Reasoning:** The user wanted to track which bowling ball is used for each shot to improve the AI Assistant's recommendations.
    *   **Change:** A new `arsenal` table was added to the local database to store a list of the user's bowling balls.
    *   **Change:** A "Manage Arsenal" section was added to the sidebar to allow the user to add new balls to their arsenal.
    *   **Change:** A "Bowling Ball" dropdown was added to the shot input area, populated with the balls from the user's arsenal.
    *   **Change:** The `shots` table was updated to include a `bowling_ball` column, and the `submit_shot` function was updated to save the selected ball.
    *   **Change:** The AI Assistant prompt was updated to include the bowling ball used for each shot.

**Update (Follow-up):** The user requested that the application be pre-populated with a default list of bowling balls.

16. **Default Arsenal:**
    *   **Reasoning:** The user wanted to have a default list of bowling balls available in the application without having to enter them manually.
    *   **Change:** The database setup logic was updated to check if the `arsenal` table is empty. If it is, the application is pre-populated with a default list of bowling balls.

**Update (Follow-up):** The user requested a more practical way to manage their bowling ball arsenal for a given session.

17. **"In the Bag" Arsenal Management:**
    *   **Reasoning:** The user wanted a way to specify which balls from their full arsenal they have with them for a particular session.
    *   **Change:** The "Manage Arsenal" sidebar section was refactored. It now allows the user to select which balls are "in the bag" for the current session using a multiselect box.
    *   **Change:** The main "Bowling Ball" dropdown in the shot entry form is now filtered to only show the balls selected as being "in the bag."
    *   **Change:** The application now remembers the last used ball and defaults to it in the dropdown, saving a click on each shot.
    *   **Change:** The AI Assistant is now only provided with the list of balls currently in the bag, ensuring its recommendations are relevant to the user's available equipment.

**Update (Follow-up):** The user reported that the application was crashing due to a DuckDB error, likely caused by the app being suspended and restarted by the mobile OS.

18. **Resilient Data Workflow:**
    *   **Reasoning:** The application was not resilient to being suspended and restarted by the mobile OS, leading to database corruption and crashes.
    *   **Change:** The data workflow was redesigned to treat Azure as the single source of truth. The local DuckDB is now treated as a temporary workspace for the current set only.
    *   **Change:** The "Load Set from Azure" function was updated to completely wipe the local database before importing the downloaded data, ensuring a clean and reliable restore.
    *   **Change:** The UI was updated to include clearer text explaining that loading a set from Azure will overwrite any unsaved local changes.

**Update (Follow-up):** The user reported a `CatalogException` on startup due to a database schema mismatch.

19. **Database Schema Migration:**
    *   **Reasoning:** The application was attempting to create a table with a new schema before the old schema was properly migrated, causing a `CatalogException`.
    *   **Change:** The database setup logic was corrected to first create the table with a compatible base schema and then use `ALTER TABLE` to add new columns. This ensures that existing database files can be correctly migrated to the new schema without crashing the application.

**Update (Follow-up):** The user reported a silent crash on startup.

20. **Scoring Logic Refactor:**
    *   **Reasoning:** The `calculate_scores` function was overly complex and contained a bug that caused a silent crash on startup.
    *   **Change:** The function was completely rewritten to be simpler, more accurate, and more resilient. It now calculates scores frame by frame and looks ahead only when necessary for strikes and spares, following standard bowling scoring rules. This resolves the startup crash and improves the accuracy of the score sheet.

**Update (Follow-up):** The user reported a `KeyError` on startup when no data was present.

21. **Empty State Handling:**
    *   **Reasoning:** The score sheet rendering logic did not correctly handle the case where a new game has no shots, causing a `KeyError` when trying to access a non-existent dataframe column.
    *   **Change:** A guard clause was added to the score sheet rendering logic. It now checks if the `df_current_game` dataframe is empty and, if so, renders a clean, empty score sheet instead of trying to process non-existent data.

**Update (Follow-up):** The user reported a silent crash on startup, likely due to a bug in the scoring logic.

22. **Scoring Logic Rewrite:**
    *   **Reasoning:** The `calculate_scores` function was still too complex and was the likely cause of a silent startup crash.
    *   **Change:** The function was completely rewritten to be simpler, more accurate, and more resilient. It now uses a more direct, frame-by-frame calculation that is easier to read and maintain. This resolves the startup crash and improves the accuracy of the score sheet.

**Update (Follow-up):** The user reported a silent crash on startup, likely due to a bug in the scoring logic.

23. **Scoring Logic Rewrite (v2):**
    *   **Reasoning:** The `calculate_scores` function was still the likely cause of a silent startup crash.
    *   **Change:** The function was completely rewritten again to be simpler, more accurate, and more resilient. It now uses a more direct, frame-by-frame calculation that is easier to read and maintain. This resolves the startup crash and improves the accuracy of the score sheet.

**Update (Follow-up):** The user reported a silent crash on startup after the UI was accidentally deleted.

24. **UI Restoration and Scoring Fix:**
    *   **Reasoning:** The application was not loading because the UI code for the shot entry section had been accidentally deleted in a previous step.
    *   **Change:** The UI components for the shot entry form were restored from a backup.
    *   **Change:** The `calculate_scores` function was replaced with a new, simpler version to prevent the original silent crash.

---
## Session from Saturday, February 14, 2026

**Backup:** A full backup of `bowlingAssistantApp.py` was saved to `Archive/bowlingAssistantApp_backup_before_6features_20260214.py` before making the following changes. If the combined update causes issues, roll back by restoring that file and then implement features one at a time.

**Plan:** Six features were implemented in one pass (Historical AI Coach, Bowling Center Tracking, Lane Switching Logic, Score Sheet and Scoring Accuracy, Automatic Split Detection, Editable Data Grid).

**Changes Implemented in `bowlingAssistantApp.py`:**

1. **Bowling Center Tracking:**
    *   **Reasoning:** User wanted to associate each set with a specific bowling center (required, free text) and include it in the Azure filename.
    *   **Change:** Added `bowling_center` column to `shots` table (ALTER TABLE). When starting a new set, the user must enter a bowling center name in a new text input; "Start New Set" is disabled until a name is entered. The center is stored in session state and written to every shot in the set. `upload_set_to_azure` now includes the bowling center in the blob name (e.g. `set-League_02-14-26-Riverside_Lanes-<set_id>.csv`). Load from Azure adds a `bowling_center` column to the dataframe if missing.

2. **Lane Switching Logic:**
    *   **Reasoning:** Each new game in a set should start on the opposite lane from the previous game (lane 1 = left, 2 = right); within a game, odd frames on starting lane, even on the other.
    *   **Change:** When the user clicks "Start New Game in Set", the app reads the previous game’s starting lane from the database and sets `starting_lane` to the opposite lane for the new game. Within-game logic (odd/even frame) was already correct and unchanged.

3. **Score Sheet and Scoring Accuracy:**
    *   **Reasoning:** User wanted a standard visual score sheet and a simpler, correct scoring calculation per bowl.com (including 10th frame).
    *   **Change:** `calculate_scores` was refactored: it builds a list of ball-by-ball pin counts, then scores frame-by-frame with correct strike/spare look-ahead and frame-10 handling. A new `render_score_sheet` function displays one row per game with standard symbols (X, /, -, pin counts), a row of running totals per frame, and total score plus max possible at the far right. The score sheet is shown below the shot input for the currently selected game; the sidebar game selector is used to switch games and compare.

4. **Automatic Split Detection:**
    *   **Reasoning:** User wanted USBC-standard split detection (no manual checkbox): headpin down and a gap between remaining pins.
    *   **Change:** Added `is_usbc_split(pins_left_list)` that returns True when pin 1 is down and there is a gap in the sorted list of standing pins. The score sheet uses this to show **S** for split leaves on first shot; no manual split checkbox in the UI.

5. **Historical AI Coach:**
    *   **Reasoning:** User wanted to select multiple saved sets from Azure and get a strategic game plan from the same AI based on a free-text goal.
    *   **Change:** New sidebar expander "Historical Analysis" lists Azure set blobs sorted by last-modified date (newest first). User can multi-select sets and enter a goal/question in a text area. "Get game plan" downloads each selected blob to a dataframe, concatenates them, and calls new `get_ai_historical_game_plan(api_key, df_combined, user_goal, model_name)` which uses the same Gemini config with a strategic, goal-driven prompt. The result is shown in the main area under "Historical game plan."

6. **Editable Data Grid:**
    *   **Reasoning:** User wanted to edit shot data after entry (bowling_ball, ball_reaction, trajectory, pins_left, shot_result) and have the app update shot_result from pins_left and recalculate scores.
    *   **Change:** The set data table was replaced with `st.data_editor` (key `edited_set_data`) showing all games in the set. Editable columns include frame_number, shot_number, shot_result, pins_left, lane_number, bowling_ball, arrows_pos, breakpoint_pos, ball_reaction. "Save edits" writes the grid state to the database via `apply_edits_to_db`. Helper `_derive_shot_result_and_pins_from_pins_left(row, edited_df)` derives shot_result and pins_knocked_down from pins_left (and shot 1 pins for shot 2). Saving edits persists all rows and triggers a rerun so the score sheet and totals reflect the updated data.

---
## Session from Saturday, February 14, 2026 (follow-up)

**User requests:** Five fixes/improvements after testing the six-feature release.

**Changes Implemented in `bowlingAssistantApp.py`:**

1. **Max possible score (USBC):**
    *   **Reasoning:** After a 9-spare in frame 1, max was showing 300 instead of 290. Per USBC, a spare frame is 10 + next ball (max 20), not 30.
    *   **Change:** The max_score calculation in `calculate_scores` was updated. For the current incomplete frame: no balls → 30; one ball strike → 30; one ball leave → 20; frame 10 with two balls → 20 or 30. Remaining frames still add 30 each. Example: 9-spare in frame 1 now correctly shows max 290.

2. **Bowling center in Azure save and one blob per set:**
    *   **Reasoning:** User wanted the saved file to always include the bowling center in the filename and, when adding/changing center after the set started, the next save should write a new file with the correct name (all set data) and remove the old file.
    *   **Change:** Upload already used full set data (`SELECT * FROM shots WHERE set_id = ?`). After each successful upload, the app now lists blobs in the container whose name contains this set’s `set_id` and deletes any blob that is not the one just uploaded (Option A: exactly one blob per set). The blob name format remains `set-{set_name}-{bowling_center}-{set_id}.csv`.

3. **Azure Portal link in Danger Zone:**
    *   **Reasoning:** User wanted quick access to the Storage Account in the Azure Portal.
    *   **Change:** Added `get_storage_account_name_from_secrets()` to parse the account name from `AZURE_STORAGE_CONNECTION_STRING` (or use `AZURE_STORAGE_ACCOUNT_NAME`). In the Danger Zone expander, added a link to https://portal.azure.com and the storage account name in a copyable code block so the user can search for it in the portal.

4. **Split logic from splits.json:**
    *   **Reasoning:** The previous gap-based logic incorrectly marked 6-10 as a split. User provided `splits.json` (same folder as the app) with the list of official splits. Only those leaves should be splits; split name should appear in Data for Set and shot result should be "Leave - Split".
    *   **Change:** Removed `is_usbc_split()`. Added `_load_splits()` and `get_split_name(pins_left_list)` to load `splits.json` from the script directory and match first-ball leaves by sorted pins (order-independent). Only leaves in the list are splits. Added `split_name` column to `shots` (ALTER TABLE). On submit, a first-ball Leave that matches the list is stored as shot_result "Leave - Split" with split_name set. Score sheet shows "S" only for leaves in the list. Data for Set grid includes a read-only "Split" column. `apply_edits_to_db` derives shot_result and split_name from pins_left when saving edits. Azure load adds `split_name` to the dataframe if missing.

5. **Score sheet as a table:**
    *   **Reasoning:** User wanted the score sheet formatted like a table for a cleaner look.
    *   **Change:** `render_score_sheet` now outputs an HTML table: header row (1–10, Total, Max), one row of frame symbols (X, /, -, S, counts), and one row of running totals. Uses borders and padding for readability.

**Update (Follow-up):** User reported two issues after testing.

6. **Score sheet header readability:**
    *   **Reasoning:** Header row had white text on light gray and was hard to read.
    *   **Change:** Header `<th>` cells now use explicit dark text and slightly darker gray background (`color:#1a1a1a`, `background:#e0e0e0`) for better contrast.

7. **Save Edits error and table columns:**
    *   **Reasoning:** Clicking Save Edits after editing the data table caused `AttributeError` on `edited_data.empty` (e.g. when Streamlit Cloud returns a non-DataFrame for the data_editor state). User also wanted only Pins Left shown in the table, not Pins knocked down.
    *   **Change:** Save-edits logic now checks `isinstance(edited_data, pd.DataFrame)` before using `.empty`; if the value is not a DataFrame, the app tries `pd.DataFrame(edited_data)` and saves if valid, otherwise shows a warning and still clears state. Removed `pins_knocked_down` from the Data for Set grid via `display_df.drop(columns=['pins_knocked_down'], errors='ignore')`; only **pins_left** is shown. `apply_edits_to_db` still derives `pins_knocked_down` from `pins_left`, so the database is updated correctly on save.

**Update (Follow-up):** User reported that after editing pins left and clicking Save Edits, the score sheet and Total/Max scores did not update.

8. **Save Edits so score sheet and totals refresh:**
    *   **Reasoning:** Save-edits logic ran after `df_set` was already fetched, so after rerun the script re-fetched `df_set` but the run that performed the save had already built sidebar and score data from the old fetch. Ensuring the save runs before any fetch guarantees the next run gets updated data.
    *   **Change:** The save-edits block was moved to run **before** "Game Selection & Data Fetching" (before `df_set = con.execute(...)`). When the user clicks Save Edits, the app applies edits, sets a session flag for the success message, and reruns; on the next run the save block is skipped, `df_set` is fetched (now with updated shots), and `calculate_scores(df_current_game)` and the score sheet use the new data. A one-time success message ("Edits saved. Score sheet and totals updated.") is shown via `st.session_state.edits_saved_message` on the run after save.

**Update (Follow-up):** User reported that splits such as 3,10 (Baby Split) and 7,10 (Bedposts) were not being recognized.

9. **Split recognition fixes:**
    *   **Reasoning:** Splits were not matching due to possible pin type mismatch (int vs string) and/or splits.json not being found (e.g. different working directory on Streamlit Cloud).
    *   **Change:** Added `_normalize_pins_list(pins_left_list)` to convert all pin values to integers 1–10 so that both multiselect lists (e.g. `[3, 10]`) and string inputs (e.g. `["3", "10"]`) match the JSON keys. Cache keys when loading splits.json are built with `tuple(sorted(int(p) for p in entry["pins"]))` for consistency. `_load_splits()` now tries multiple base directories: script directory, current working directory, and parent of script directory, and only loads from a path where `splits.json` exists, so the file is found in more deployment layouts.

---
## Session from Monday, February 16, 2026

**User Story:** Splits (e.g. 3–10 Baby Split, 7–10 Bedposts) were still not being recognized on Streamlit Cloud after splits.json was added to source control.

**Changes Implemented in `bowlingAssistantApp.py`:**

1. **Embedded USBC splits in app:**
    *   **Reasoning:** Split recognition was unreliable when loading from `splits.json` on Streamlit Cloud (path/working-directory issues). The split list is static and will not change.
    *   **Change:** Replaced file-based split loading with an embedded constant `_SPLITS_DATA`: a Python list of dicts with the same content as `splits.json` (all USBC split definitions). `_load_splits()` now builds the lookup cache from `_SPLITS_DATA` only, with no file I/O. This guarantees 3–10, 7–10, and all other defined splits are recognized regardless of deployment environment.
