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


# FIX 1 — Cache get_workbook() so the workbook lookup is not a fresh API call
# on every write. Previously every save_*() called get_workbook() which called
# client.open() each time, consuming quota. Now it is resolved once globally.
@st.cache_resource
def get_workbook():
    """
    Opens the Google Sheet once and caches the reference globally.
    All worksheet reads/writes go through this single cached handle.
    """
    return get_gspread_client().open("Bible Character Game  - Python")


@st.cache_data(ttl=600)
def fetch_master_data():
    """
    Fetches all static game data in one pass. Result is cached for 10 minutes
    and shared across every user, so it costs 3 API reads per 10-minute window
    regardless of how many users are active.
    """
    wb = get_workbook()
    categories = wb.worksheet("1-Category").get_all_records()
    answers    = wb.worksheet("1-CategoryAnswer").get_all_records()
    characters = wb.worksheet("2-Characters").get_all_records()
    return categories, answers, characters


# ---------------------------------------------------------------------------
# USER DATA — reads (not cached; each user's data is personal)
# ---------------------------------------------------------------------------

def fetch_user_history(user_id):
    """
    Loads a single user's Mode 1 and Mode 2 history from Sheets.

    FIX 2 — Uses the cached workbook (no extra auth round-trip).
    FIX 4 — Replaces bare `except: pass` with logged warnings shown to user.

    For Mode 1 we keep the session that achieved the highest score for each
    category, and we store both the score and the actual answer words so the
    user can see them when they return to an in-progress category.

    GAMEPLAY — A category is considered COMPLETE when its stored score equals
    TotalRequired. Completed categories are locked (no Play button shown) and
    their saved answers are displayed as a read-only trophy view.
    """
    wb          = get_workbook()
    total_score = 0

    # ---- Mode 1 ----
    try:
        m1_data = wb.worksheet("Mode1_Sessions").get_all_records()
        user_m1 = [r for r in m1_data if str(r["UserEmail"]) == user_id]

        history_map       = {}  # {category_id: best_score}
        saved_answers_map = {}  # {category_id: [answer, ...]}
        known_cols = {"SessionID", "CategoryID", "UserEmail", "Timestamp", "Score"}

        for row in user_m1:
            c_id  = str(row["CategoryID"])
            score = int(row.get("Score", 0))

            if c_id not in history_map or score > history_map[c_id]:
                history_map[c_id] = score
                # Everything beyond the standard columns is an answer word
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
                attempts   = int(row.get("CurrentAttempts", 2))
                m2_points += 3 if attempts == 0 else 2 if attempts == 1 else 1

        total_score += m2_points

        for c_id in solved_chars:
            st.session_state.m2_progress[c_id] = {"attempts": 0, "solved": True}

    except Exception as exc:
        logger.warning("Could not load Mode 2 history for %s: %s", user_id, exc)
        st.warning("⚠️ Could not load your Character Mode history. "
                   "Previous progress may not be shown.")

    # FIX 5 — Set score directly from fetched totals, never via +=
    st.session_state.score = total_score


def _recalculate_score():
    """
    FIX 5 (helper) — Derives the current total score purely from the history
    maps in session_state. Call this after any score-affecting event instead
    of mutating st.session_state.score with +=, which double-counts on rapid
    re-clicks or unexpected reruns.
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
    Appends one Mode 1 session row to Google Sheets.
    Uses the cached workbook (FIX 1) — no extra auth overhead.
    Errors are surfaced to the user rather than silently swallowed (FIX 4).
    """
    try:
        sheet = get_workbook().worksheet("Mode1_Sessions")
        row = [
            f"SESS-{int(time.time())}",
            category_id,
            st.session_state.user_id,
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            score,
        ] + answers
        # Pad to 20 columns so the sheet structure stays consistent
        row += [""] * max(0, 20 - len(row))
        sheet.append_row(row)
    except Exception as exc:
        logger.error("Failed to save Mode 1 session: %s", exc)
        st.error("❌ Your progress could not be saved. Please try again.")


# FIX 3 — Mode 2 guesses are now buffered in session_state and flushed as a
# single batch write (append_rows) instead of one append_row() per guess.
# Previously every correct or incorrect guess blocked the Streamlit thread
# with a synchronous network call; under load this queues up and can exhaust
# the thread pool and hit the Sheets quota simultaneously.
def queue_mode2_guess(char_id, attempts, solved, guess):
    """Adds one Mode 2 guess to the in-memory write buffer."""
    st.session_state.m2_pending_saves.append([
        f"GUESS-{int(time.time())}-{char_id}",
        char_id,
        st.session_state.user_id,
        attempts,
        str(solved).upper(),
        guess,
    ])


def flush_mode2_session():
    """
    Writes all buffered Mode 2 guesses to Sheets in a single API call.
    Call on logout, Home navigation, or any session-exit point.
    append_rows() sends the entire list in one HTTP request.
    """
    pending = st.session_state.get("m2_pending_saves", [])
    if not pending:
        return
    try:
        get_workbook().worksheet("Mode2_Sessions").append_rows(pending)
        st.session_state.m2_pending_saves = []
    except Exception as exc:
        logger.error("Failed to flush Mode 2 session: %s", exc)
        st.error("❌ Some guesses could not be saved. Please check your connection.")


# ---------------------------------------------------------------------------
# SESSION STATE DEFAULTS
# ---------------------------------------------------------------------------
_DEFAULTS = {
    "page":             "home",
    "score":            0,
    "user_id":          "",
    "display_name":     "",
    "history_mode1":    {},  # {category_id: best_score}
    "history_mode2":    [],
    "m1_answers":       [],  # answers for the currently active category
    "current_category": None,
    "m2_progress":      {},  # {char_id: {attempts, solved}}
    "m1_saved_answers": {},  # {category_id: [answer words from best session]}
    "m2_pending_saves": [],  # FIX 3: buffered rows waiting to be flushed
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
            flush_mode2_session()  # FIX 3: flush buffered guesses before leaving
            st.session_state.page = "menu"
            st.rerun()

        if st.button("Log Out", type="secondary", use_container_width=True):
            flush_mode2_session()  # FIX 3: flush before clearing state
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
            with st.spinner("Loading your progress..."):
                fetch_user_history(st.session_state.user_id)
            st.session_state.page = "menu"
            st.rerun()
        else:
            st.error("Please enter a name and a PIN of at least 2 characters.")


# ---------------------------------------------------------------------------
# MODE 1 — Category select
# ---------------------------------------------------------------------------

def mode1_select(categories):
    """
    Lists all categories with their completion state.

    GAMEPLAY FIX A — Completed categories (best_score >= TotalRequired) are
    locked: the Play button is hidden and replaced with a trophy icon. The
    answers from the user's best session are shown so they can review what
    they submitted.

    GAMEPLAY FIX B — In-progress categories show the Play button as normal.
    Entering the play screen pre-loads the previously saved answers so the
    user continues from where they left off.
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
                # ---- COMPLETED & LOCKED ----
                col1.success(f"✅ Completed! ({best_score}/{req})")

                # Show the answers from the best session as a read-only trophy
                saved = st.session_state.m1_saved_answers.get(c_id, [])
                if saved:
                    col1.caption("Your answers: " + ", ".join(saved))

                # No Play button — category is permanently locked
                col2.markdown("### 🏆")

            else:
                # ---- NOT STARTED OR IN-PROGRESS ----
                if best_score > 0:
                    col1.write(f"In progress — best so far: {best_score}/{req}")
                else:
                    col1.write(f"Not started — 0/{req}")

                if col2.button("Play", key=f"p_{c_id}", type="primary"):
                    st.session_state.current_category = cat
                    # GAMEPLAY FIX B: pre-load saved answers so user resumes
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
    The active quiz screen for a single category.

    Key behaviours
    ─────────────
    • Progress bar and running list of correct answers shown at all times.
    • Correct answers are appended and de-duplicated in-memory (no Sheet
      write on each guess — fast and quota-friendly).
    • "Save & Exit" saves current partial progress and returns to the list.
      The user can come back and their answers will be waiting for them.
    • When all answers are found, balloons fire and a "Finish" button writes
      the final session and locks the category permanently.

    GAMEPLAY FIX A — On Finish, history_mode1[c_id] is set to req locally
    so the category shows as locked on the very next render of mode1_select
    without needing another Sheets read.

    FIX 5 — Score is recalculated from history maps after any update.
    """
    cat  = st.session_state.current_category
    req  = int(cat["TotalRequired"])
    c_id = str(cat["CategoryID"])

    st.title(f"Topic: {cat['CategoryName']}")
    st.progress(min(len(st.session_state.m1_answers) / req, 1.0))

    if st.session_state.m1_answers:
        st.success("✅ Correct so far: " + ", ".join(st.session_state.m1_answers))

    # ---- Active game loop (category not yet complete) ----
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

        # Save & Exit — persists partial progress, user can resume later
        if st.button("💾 Save & Exit"):
            _commit_mode1_session(cat, c_id, req, navigate_to="mode1_select")

    # ---- Completed state ----
    else:
        st.balloons()
        st.info(f"🎉 You found all {req} answers for **{cat['CategoryName']}**!")

        if st.button("✅ Finish & Lock Category", type="primary"):
            _commit_mode1_session(cat, c_id, req, navigate_to="mode1_select")


def _commit_mode1_session(cat, c_id, req, navigate_to):
    """
    Shared save logic called by both Save & Exit and Finish.

    1. Compares new score to previous best and updates history maps only if
       the new score is an improvement.
    2. Recalculates total score from maps (FIX 5 — no +=).
    3. Writes to Google Sheets only when there is a new best (avoids
       redundant writes if the user exits without improving their score).
    4. Navigates to the requested page.

    GAMEPLAY FIX A — Because history_mode1[c_id] is updated to new_score
    before navigation, the select screen immediately reflects the completed
    state on the next render without an extra Sheets read.
    """
    new_score     = len(st.session_state.m1_answers)
    previous_best = st.session_state.history_mode1.get(c_id, 0)

    if new_score > previous_best:
        # Update both the score map and the answer word map
        st.session_state.history_mode1[c_id]    = new_score
        st.session_state.m1_saved_answers[c_id] = st.session_state.m1_answers.copy()

        # FIX 5 — Recalculate from maps, never +=
        _recalculate_score()

        # Persist to Sheets
        save_mode1_session(cat["CategoryID"], new_score, st.session_state.m1_answers)

    else:
        # No improvement — still recalculate in case state drifted
        _recalculate_score()

    st.session_state.page = navigate_to
    st.rerun()


# ---------------------------------------------------------------------------
# MODE 2 — Character guess
# ---------------------------------------------------------------------------

def mode2_play(characters):
    """
    Displays all characters. Solved ones show the name; unsolved ones show
    progressive clues and an inline guess form.

    FIX 3 — Guesses are queued in session_state and flushed to Sheets in a
    single batch on logout / Home navigation instead of one append_row() per
    guess.

    FIX 5 — Score is recalculated from m2_progress after each solve.
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
                            st.session_state.m2_progress[c_id]["solved"] = True
                            # FIX 5 — recalculate, never +=
                            _recalculate_score()
                            # FIX 3 — buffer, don't write immediately
                            queue_mode2_guess(c_id, state["attempts"], True, guess)
                            st.rerun()
                        else:
                            st.session_state.m2_progress[c_id]["attempts"] += 1
                            queue_mode2_guess(c_id, state["attempts"], False, guess)
                            st.rerun()


# ---------------------------------------------------------------------------
# MAIN ROUTER
# ---------------------------------------------------------------------------

def main():
    render_sidebar()

    # Static data — one cached fetch shared across all users
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
        st.title(f"Welcome, {st.session_state.display_name}! 👋")
        col1, col2 = st.columns(2)
        if col1.button("📂 Play Category Mode", type="primary", use_container_width=True):
            st.session_state.page = "mode1_select"
            st.rerun()
        if col2.button("🕵️ Play Character Mode", type="primary", use_container_width=True):
            st.session_state.page = "mode2_play"
            st.rerun()

    elif page == "mode1_select":
        mode1_select(categories)

    elif page == "mode1_play":
        mode1_play(answers)

    elif page == "mode2_play":
        mode2_play(characters)


if __name__ == "__main__":
    main()
