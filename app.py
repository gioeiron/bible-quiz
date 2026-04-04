import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import datetime
import time
import logging

# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PAGE CONFIG
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Bible Character Quiz", page_icon="📖", layout="wide")


# ---------------------------------------------------------------------------
# DATABASE CONNECTIONS
# ---------------------------------------------------------------------------

@st.cache_resource
def get_gspread_client():
    """
    Authenticates with Google once and caches the client globally.
    Shared across all users and all sessions on this Streamlit instance.
    """
    if "gcp_service_account" in st.secrets:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(
            creds_dict,
            ["https://spreadsheets.google.com/feeds",
             "https://www.googleapis.com/auth/drive"]
        )
    else:
        scope = ["https://spreadsheets.google.com/feeds",
                 "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
    return gspread.authorize(creds)


@st.cache_resource
def get_workbook():
    """
    Opens the Google Sheet once and caches the reference globally.
    All worksheet reads/writes share this single handle (avoids re-auth
    overhead on every write).
    """
    return get_gspread_client().open("Bible Character Game  - Python")


@st.cache_data(ttl=600)
def fetch_master_data():
    """
    Fetches all static game data in one pass. Result is cached for 10 minutes
    and shared across every user — 3 API reads per 10-minute window total,
    regardless of concurrent users.
    """
    wb = get_workbook()
    categories = wb.worksheet("1-Category").get_all_records()
    answers    = wb.worksheet("1-CategoryAnswer").get_all_records()
    characters = wb.worksheet("2-Characters").get_all_records()
    return categories, answers, characters


# ---------------------------------------------------------------------------
# USER DATA — reads
# ---------------------------------------------------------------------------

def fetch_user_history(user_id):
    """
    Loads a user's full Mode 1 and Mode 2 history from Sheets and writes it
    into session_state. Called at login AND every time the user returns to the
    Home menu, so session_state always reflects the latest persisted state.

    ROOT CAUSE FIX 2 — Previously this was only called at login. Users who
    played, saved, went Home, and re-entered a mode would see stale
    session_state (missing progress from the session just completed).
    Now it is called on every menu visit via _refresh_history_if_needed().

    ROOT CAUSE FIX 3 — m2_progress now stores the actual attempt count used
    at solve time (not always 0), so _recalculate_score() can compute the
    correct point value (3 / 2 / 1) for each solved character.
    """
    wb          = get_workbook()
    total_score = 0

    # ---- Mode 1 ----
    try:
        m1_data = wb.worksheet("Mode1_Sessions").get_all_records()
        user_m1 = [r for r in m1_data if str(r["UserEmail"]) == user_id]

        history_map       = {}  # {category_id: best_score}
        saved_answers_map = {}  # {category_id: [answer words]}
        known_cols = {"SessionID", "CategoryID", "UserEmail", "Timestamp", "Score"}

        for row in user_m1:
            c_id  = str(row["CategoryID"])
            score = int(row.get("Score", 0))

            if c_id not in history_map or score > history_map[c_id]:
                history_map[c_id] = score
                words = [
                    str(v).strip()
                    for k, v in row.items()
                    if k not in known_cols and str(v).strip() != ""
                ]
                saved_answers_map[c_id] = words

        st.session_state.history_mode1    = history_map
        st.session_state.m1_saved_answers = saved_answers_map
        total_score += sum(history_map.values())

    except Exception as exc:
        logger.warning("Could not load Mode 1 history for %s: %s", user_id, exc)
        st.warning("⚠️ Could not load your Category Mode history. "
                   "Previous progress may not be shown.")

    # ---- Mode 2 ----
    try:
        m2_data = wb.worksheet("Mode2_Sessions").get_all_records()
        user_m2 = [
            r for r in m2_data
            if str(r["UserEmail"]) == user_id
            and str(r["IsSolved"]).upper() == "TRUE"
        ]

        solved_chars = set()
        m2_points    = 0

        for row in user_m2:
            c_id = str(row["CharacterID"])
            if c_id not in solved_chars:
                solved_chars.add(c_id)
                # FIX 3 — preserve the real attempt count so score calc is correct
                attempts   = int(row.get("CurrentAttempts", 2))
                m2_points += 3 if attempts == 0 else 2 if attempts == 1 else 1
                # Store the real attempt count (not always 0) so
                # _recalculate_score() can re-derive points accurately
                st.session_state.m2_progress[c_id] = {
                    "attempts": attempts,
                    "solved":   True,
                }

        total_score += m2_points

    except Exception as exc:
        logger.warning("Could not load Mode 2 history for %s: %s", user_id, exc)
        st.warning("⚠️ Could not load your Character Mode history. "
                   "Previous progress may not be shown.")

    st.session_state.score = total_score


def _refresh_history_if_needed():
    """
    ROOT CAUSE FIX 2 — Refreshes history from Sheets whenever the user
    arrives at the Home menu. Uses a timestamp gate so we only hit the API
    once per 60-second window rather than on every single Streamlit rerun.

    Why 60 s and not the 10-minute master cache?
    fetch_user_history() reads Mode1_Sessions and Mode2_Sessions, which are
    write-heavy (user data). The master cache only covers static game data.
    60 seconds is short enough to feel live while keeping quota usage low.
    """
    now  = time.time()
    last = st.session_state.get("last_history_refresh", 0)

    if now - last > 60:
        fetch_user_history(st.session_state.user_id)
        st.session_state.last_history_refresh = now


def _recalculate_score():
    """
    Derives the total score purely from the history maps in session_state.
    Call this after any local score-affecting event to keep the sidebar
    metric accurate without hitting Sheets again.

    FIX 3 — Uses the actual stored attempt count per character (which now
    reflects the real solve attempt, not always 0) so points are correct.
    """
    m1_total = sum(st.session_state.history_mode1.values())

    m2_total = sum(
        3 if v.get("attempts", 2) == 0 else 2 if v.get("attempts", 2) == 1 else 1
        for v in st.session_state.m2_progress.values()
        if v.get("solved", False)
    )

    st.session_state.score = m1_total + m2_total


# ---------------------------------------------------------------------------
# USER DATA — writes
# ---------------------------------------------------------------------------

def save_mode1_session(category_id, score, answers):
    """
    Appends one Mode 1 session row to Google Sheets synchronously.
    Now includes a retry mechanism for rate limits and returns True/False.
    """
    max_retries = 3
    for attempt in range(max_retries):
        try:
            sheet = get_workbook().worksheet("Mode1_Sessions")
            row = [
                f"SESS-{int(time.time())}",
                category_id,
                st.session_state.user_id,
                datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                score,
            ] + answers
            row += [""] * max(0, 20 - len(row))
            sheet.append_row(row)
            return True # Successfully saved
            
        except Exception as exc:
            if attempt < max_retries - 1:
                time.sleep(1) # Wait 1 second and try again
                continue
            logger.error("Failed to save Mode 1 session after %s attempts: %s", max_retries, exc)
            return False # Failed permanently


# ---------------------------------------------------------------------------
# ROOT CAUSE FIX 1 — Mode 2 writes: write on every SOLVE, not on every guess
# ---------------------------------------------------------------------------
#
# The previous version buffered ALL guesses (correct and wrong) and flushed
# them only when the user clicked Home or Log Out. This meant:
#   • If the tab was closed, refreshed, or the session timed out, the buffer
#     in session_state was gone and solved characters were never persisted.
#   • Users who solved characters but didn't click Home before closing would
#     lose all Mode 2 progress on next login.
#
# New approach:
#   • Wrong guesses are NOT written to Sheets at all. They are only tracked
#     in session_state to control how many clues are revealed. This is safe
#     because wrong-guess counts are already derived from CurrentAttempts in
#     the solved row — we don't need a separate row per wrong guess.
#   • A CORRECT guess (solve) is written to Sheets immediately as a single
#     row. One small write at the moment of solve is far safer than buffering
#     many rows that may never be flushed.
#
# This eliminates the lost-progress scenario entirely for Mode 2 without
# sacrificing quota safety (one write per solve, not per guess).

def save_mode2_solve(char_id, attempts_before_solve):
    """
    Persists a single SOLVED character to Sheets immediately.
    Called only when the user gets the correct answer.
    Wrong guesses are tracked in session_state only — no write needed.
    """
    try:
        sheet = get_workbook().worksheet("Mode2_Sessions")
        row = [
            f"SOLVE-{int(time.time())}-{char_id}",
            char_id,
            st.session_state.user_id,
            attempts_before_solve,
            "TRUE",
            "",   # no wrong guess text for a solve row
        ]
        sheet.append_row(row)
    except Exception as exc:
        logger.error("Failed to save Mode 2 solve for %s: %s", char_id, exc)
        st.error("❌ Solve could not be saved. Please try again.")


# ---------------------------------------------------------------------------
# SESSION STATE DEFAULTS
# ---------------------------------------------------------------------------
_DEFAULTS = {
    "page":                  "home",
    "score":                 0,
    "user_id":               "",
    "display_name":          "",
    "history_mode1":         {},   # {category_id: best_score}
    "history_mode2":         [],
    "m1_answers":            [],   # answers for the currently active category
    "current_category":      None,
    "m2_progress":           {},   # {char_id: {attempts, solved}}
    "m1_saved_answers":      {},   # {category_id: [answer words from best session]}
    "last_history_refresh":  0,    # FIX 2: epoch timestamp of last Sheets read
}

for _key, _val in _DEFAULTS.items():
    if _key not in st.session_state:
        st.session_state[_key] = _val


# ---------------------------------------------------------------------------
# SIDEBAR
# ---------------------------------------------------------------------------

def render_sidebar():
    if not st.session_state.user_id:
        return

    with st.sidebar:
        st.header(f"👤 {st.session_state.display_name}")
        st.metric("TOTAL SCORE", st.session_state.score)
        st.divider()

        if st.button("🏠 Home", use_container_width=True):
            # No flush needed — Mode 2 solves are now written immediately
            st.session_state.page = "menu"
            st.rerun()

        if st.button("Log Out", type="secondary", use_container_width=True):
            st.session_state.clear()
            st.rerun()


# ---------------------------------------------------------------------------
# PAGES
# ---------------------------------------------------------------------------

def home_page():
    st.title("📖 Bible Characters Quiz")
    name = st.text_input("Name / Nickname").strip()
    pin  = st.text_input("4-Digit PIN", type="password").strip()

    if st.button("Start Game", type="primary"):
        if name and len(pin) >= 2:
            st.session_state.user_id      = f"{name}_{pin}"
            st.session_state.display_name = name
            # Reset last_history_refresh so fetch runs immediately on first menu visit
            st.session_state.last_history_refresh = 0
            with st.spinner("Loading your progress..."):
                fetch_user_history(st.session_state.user_id)
            st.session_state.last_history_refresh = time.time()
            st.session_state.page = "menu"
            st.rerun()
        else:
            st.error("Please enter a name and a PIN of at least 2 characters.")


# ---------------------------------------------------------------------------
# MENU
# ---------------------------------------------------------------------------

def menu_page():
    """
    FIX 2 — Calls _refresh_history_if_needed() on every visit so that
    progress earned in the previous mode is reflected when the user comes
    back to choose another mode (or the same one again).
    """
    # Refresh from Sheets if more than 60 s have passed since last read
    with st.spinner("Refreshing your progress..."):
        _refresh_history_if_needed()

    st.title(f"Welcome, {st.session_state.display_name}! 👋")
    col1, col2 = st.columns(2)
    if col1.button("📂 Play Category Mode", type="primary", use_container_width=True):
        st.session_state.page = "mode1_select"
        st.rerun()
    if col2.button("🕵️ Play Character Mode", type="primary", use_container_width=True):
        st.session_state.page = "mode2_play"
        st.rerun()


# ---------------------------------------------------------------------------
# MODE 1 — Category select
# ---------------------------------------------------------------------------

def mode1_select(categories):
    """
    Lists all categories. Completed ones are locked and show saved answers.
    In-progress ones show the Play button and resume from the last save.
    """
    st.title("📂 Name All by Category")

    for cat in categories:
        c_id = str(cat["CategoryID"])
        req  = int(cat["TotalRequired"])
        name = cat["CategoryName"]

        best_score  = st.session_state.history_mode1.get(c_id, 0)
        is_complete = best_score >= req

        with st.container(border=True):
            col1, col2 = st.columns([3, 1])
            col1.subheader(name)

            if is_complete:
                col1.success(f"✅ Completed! ({best_score}/{req})")
                saved = st.session_state.m1_saved_answers.get(c_id, [])
                if saved:
                    col1.caption("Your answers: " + ", ".join(saved))
                col2.markdown("### 🏆")

            else:
                if best_score > 0:
                    col1.write(f"In progress — best so far: {best_score}/{req}")
                else:
                    col1.write(f"Not started — 0/{req}")

                if col2.button("Play", key=f"p_{c_id}", type="primary"):
                    st.session_state.current_category = cat
                    st.session_state.m1_answers = (
                        st.session_state.m1_saved_answers.get(c_id, []).copy()
                    )
                    st.session_state.page = "mode1_play"
                    st.rerun()


# ---------------------------------------------------------------------------
# MODE 1 — Play screen
# ---------------------------------------------------------------------------

def mode1_play(all_answers_data):
    """
    Active quiz screen for one category.
    Answers are tracked in-memory. Sheets is written only on Save & Exit
    or Finish — never on each individual guess.
    """
    cat  = st.session_state.current_category
    req  = int(cat["TotalRequired"])
    c_id = str(cat["CategoryID"])

    st.title(f"Topic: {cat['CategoryName']}")
    st.progress(min(len(st.session_state.m1_answers) / req, 1.0))

    if st.session_state.m1_answers:
        st.success("✅ Correct so far: " + ", ".join(st.session_state.m1_answers))

    # ---- Active game loop ----
    if len(st.session_state.m1_answers) < req:

        with st.form("ans_form", clear_on_submit=True):
            user_input = st.text_input("Enter an answer:").strip().lower()
            submitted  = st.form_submit_button("Submit")

        if submitted and user_input:
            valid_answers = [
                str(r["CorrectAnswer"]).lower()
                for r in all_answers_data
                if str(r["CategoryID"]) == c_id
            ]
            already_found = [x.lower() for x in st.session_state.m1_answers]

            if user_input in valid_answers:
                if user_input not in already_found:
                    st.session_state.m1_answers.append(user_input.title())
                    st.rerun()
                else:
                    st.info("You already found that one!")
            else:
                st.error("Incorrect — try again.")

        if st.button("💾 Save & Exit"):
            _commit_mode1_session(cat, c_id, navigate_to="mode1_select")

    # ---- Completed state ----
    else:
        st.balloons()
        st.info(f"🎉 You found all {req} answers for **{cat['CategoryName']}**!")

        if st.button("✅ Finish & Lock Category", type="primary"):
            _commit_mode1_session(cat, c_id, navigate_to="mode1_select")


def _commit_mode1_session(cat, c_id, navigate_to):
    """
    Shared save logic for Save & Exit and Finish.
    Now waits for Sheets validation BEFORE updating local session state.
    """
    new_score     = len(st.session_state.m1_answers)
    previous_best = st.session_state.history_mode1.get(c_id, 0)

    if new_score > previous_best:
        # 1. Try to save to Google Sheets FIRST
        success = save_mode1_session(cat["CategoryID"], new_score, st.session_state.m1_answers)
        
        if success:
            # 2. ONLY update local memory if the database accepted it
            st.session_state.history_mode1[c_id]    = new_score
            st.session_state.m1_saved_answers[c_id] = st.session_state.m1_answers.copy()
            _recalculate_score()
            
            # Mark history as stale so the next menu visit re-reads from Sheets
            st.session_state.last_history_refresh = 0
            
            st.session_state.page = navigate_to
            st.rerun()
        else:
            # 3. If it failed, show the error and DO NOT rerun, allowing the user to try again
            st.error("❌ The database is currently busy. Please click Save again.")
    else:
        # If the score isn't a new personal best, just navigate away safely
        _recalculate_score()
        st.session_state.last_history_refresh = 0
        st.session_state.page = navigate_to
        st.rerun()


# ---------------------------------------------------------------------------
# MODE 2 — Character guess
# ---------------------------------------------------------------------------

def mode2_play(characters):
    """
    Displays all characters with progressive clues and inline guess forms.

    FIX 1 (root cause) — A correct guess is written to Sheets immediately
    via save_mode2_solve(). Wrong guesses are tracked only in session_state.
    This eliminates the lost-progress scenario where a buffered solve was
    never flushed because the tab was closed or the session timed out.
    """
    st.title("🕵️ Guess the Character")

    for i, char in enumerate(characters):
        c_id         = str(char["CharacterID_Old"])
        correct_name = str(char["CharacterName"]).strip()
        state        = st.session_state.m2_progress.setdefault(
            c_id, {"attempts": 0, "solved": False}
        )

        with st.container(border=True):
            col1, col2 = st.columns([3, 1])

            if state["solved"]:
                col1.success(f"✅ **Character #{i + 1}:** {correct_name}")
            else:
                col1.markdown(f"**Character #{i + 1}**")
                clues = [char["Clue1"], char["Clue2"], char["Clue3"]]
                for k in range(min(state["attempts"] + 1, 3)):
                    col1.write(f"🔹 *Clue {k + 1}:* {clues[k]}")

                with col2.form(key=f"f2_{c_id}"):
                    guess = st.text_input("Guess").strip().lower()
                    if st.form_submit_button("Submit"):
                        if guess == correct_name.lower():
                            attempts_used = state["attempts"]
                            # Update local state with the real attempt count
                            st.session_state.m2_progress[c_id] = {
                                "attempts": attempts_used,
                                "solved":   True,
                            }
                            _recalculate_score()
                            # FIX 1 — write the solve to Sheets immediately
                            save_mode2_solve(c_id, attempts_used)
                            # Mark history stale so next menu visit re-reads
                            st.session_state.last_history_refresh = 0
                            st.rerun()
                        else:
                            # Wrong guess — update clue counter in memory only
                            st.session_state.m2_progress[c_id]["attempts"] += 1
                            st.rerun()


# ---------------------------------------------------------------------------
# MAIN ROUTER
# ---------------------------------------------------------------------------

def main():
    render_sidebar()

    try:
        categories, answers, characters = fetch_master_data()
    except Exception as exc:
        logger.error("fetch_master_data failed: %s", exc)
        st.error("❌ Could not connect to the database. Please refresh the page.")
        return

    page = st.session_state.page

    if page == "home":
        home_page()

    elif page == "menu":
        menu_page()

    elif page == "mode1_select":
        mode1_select(categories)

    elif page == "mode1_play":
        mode1_play(answers)

    elif page == "mode2_play":
        mode2_play(characters)


if __name__ == "__main__":
    main()
