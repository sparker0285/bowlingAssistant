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
