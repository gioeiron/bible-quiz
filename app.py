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
# DATABASE CONNECTIONS (CACHED)
# ---------------------------------------------------------------------------
@st.cache_resource
def get_gspread_client():
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
    return get_gspread_client().open("Bible Character Game  - Python")

@st.cache_data(ttl=600)
def fetch_master_data():
    wb = get_workbook()
    categories = wb.worksheet("1-Category").get_all_records()
    answers    = wb.worksheet("1-CategoryAnswer").get_all_records()
    characters = wb.worksheet("2-Characters").get_all_records()
    return categories, answers, characters

# ---------------------------------------------------------------------------
# USER DATA — READS
# ---------------------------------------------------------------------------
def fetch_user_history(user_id):
    """Loads a user's full history from Sheets ONCE at login."""
    wb          = get_workbook()
    total_score = 0

    # ---- Mode 1 ----
    try:
        m1_data = wb.worksheet("Mode1_Sessions").get_all_records()
        user_m1 = [r for r in m1_data if str(r["UserEmail"]) == user_id]

        history_map       = {}  
        saved_answers_map = {}  
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
        st.warning("⚠️ Could not load your Category Mode history.")

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
                
                st.session_state.m2_progress[c_id] = {
                    "attempts": attempts,
                    "solved":   True,
                }

        total_score += m2_points

    except Exception as exc:
        logger.warning("Could not load Mode 2 history for %s: %s", user_id, exc)
        st.warning("⚠️ Could not load your Character Mode history.")

    st.session_state.score = total_score


def _recalculate_score():
    """Derives total score purely from trusted local memory."""
    m1_total = sum(st.session_state.history_mode1.values())

    m2_total = sum(
        3 if v.get("attempts", 2) == 0 else 2 if v.get("attempts", 2) == 1 else 1
        for v in st.session_state.m2_progress.values()
        if v.get("solved", False)
    )

    st.session_state.score = m1_total + m2_total


# ---------------------------------------------------------------------------
# USER DATA — WRITES (With Retry Logic)
# ---------------------------------------------------------------------------
def save_mode1_session(category_id, score, answers):
    """Appends Mode 1 session with a 3-attempt retry for heavy traffic."""
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
            return True 
            
        except Exception as exc:
            if attempt < max_retries - 1:
                time.sleep(1) 
                continue
            logger.error("Failed to save Mode 1 session after %s attempts: %s", max_retries, exc)
            return False 

def save_mode2_solve(char_id, attempts_before_solve):
    """Persists a single SOLVED character immediately."""
    try:
        sheet = get_workbook().worksheet("Mode2_Sessions")
        row = [
            f"SOLVE-{int(time.time())}-{char_id}",
            char_id,
            st.session_state.user_id,
            attempts_before_solve,
            "TRUE",
            "",   
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
    "history_mode1":         {},   
    "history_mode2":         [],
    "m1_answers":            [],   
    "current_category":      None,
    "m2_progress":           {},   
    "m1_saved_answers":      {},   
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
            with st.spinner("Loading your progress..."):
                fetch_user_history(st.session_state.user_id)
            st.session_state.page = "menu"
            st.rerun()
        else:
            st.error("Please enter a name and a PIN of at least 2 characters.")

def menu_page():
    st.title(f"Welcome, {st.session_state.display_name}! 👋")
    col1, col2 = st.columns(2)
    if col1.button("📂 Play Category Mode", type="primary", use_container_width=True):
        st.session_state.page = "mode1_select"
        st.rerun()
    if col2.button("🕵️ Play Character Mode", type="primary", use_container_width=True):
        st.session_state.page = "mode2_play"
        st.rerun()

def mode1_select(categories):
    st.title("📂 Name All by Category")

    for cat in categories:
        c_id = str(cat["CategoryID"])
        req  = int(cat["TotalRequired"]) # Cast to Integer
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
                    # Safely load previous answers if they exist
                    st.session_state.m1_answers = (
                        st.session_state.m1_saved_answers.get(c_id, []).copy()
                    )
                    st.session_state.page = "mode1_play"
                    st.rerun()


def mode1_play(all_answers_data):
    cat  = st.session_state.current_category
    req  = int(cat["TotalRequired"]) # Cast to Integer
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
    """Saves to Sheets first, then updates local memory if successful."""
    new_score     = len(st.session_state.m1_answers)
    previous_best = st.session_state.history_mode1.get(c_id, 0)

    if new_score > previous_best:
        success = save_mode1_session(cat["CategoryID"], new_score, st.session_state.m1_answers)
        
        if success:
            st.session_state.history_mode1[c_id]    = new_score
            st.session_state.m1_saved_answers[c_id] = st.session_state.m1_answers.copy()
            _recalculate_score()
            st.session_state.page = navigate_to
            st.rerun()
        else:
            st.error("❌ The database is currently busy. Please click Save again.")
    else:
        _recalculate_score()
        st.session_state.page = navigate_to
        st.rerun()


def mode2_play(characters):
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
                            st.session_state.m2_progress[c_id] = {
                                "attempts": attempts_used,
                                "solved":   True,
                            }
                            _recalculate_score()
                            save_mode2_solve(c_id, attempts_used)
                            st.rerun()
                        else:
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
