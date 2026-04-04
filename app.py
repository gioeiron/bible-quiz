import streamlit as st
from supabase import create_client, Client
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
# DATABASE CONNECTION
# ---------------------------------------------------------------------------

@st.cache_resource
def get_supabase() -> Client:
    """
    Creates the Supabase client once and caches it globally across all users.

    Unlike Google Sheets, Supabase uses a lightweight stateless HTTP client —
    there is no concept of a "workbook handle" that can go stale. One client
    instance shared across all users is the correct pattern.

    Credentials are read from Streamlit secrets:
        [supabase]
        url = "https://your-project-id.supabase.co"
        key = "your-anon-key"
    """
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["key"]
    return create_client(url, key)


# ---------------------------------------------------------------------------
# STATIC GAME DATA
# ---------------------------------------------------------------------------
#
# In the Google Sheets version, categories, answers, and characters were stored
# as worksheet tabs and fetched with get_all_records(). With Supabase these
# remain in Google Sheets (read-only, never written to) OR you can copy them
# into Supabase tables.
#
# RECOMMENDED: Keep static data in Google Sheets and cache it as before,
# OR copy it into Supabase tables named "categories", "category_answers",
# and "characters". The fetch below supports both approaches — just ensure
# the column names match what your code expects.
#
# If you want to keep static data in Sheets, you can leave the original
# fetch_master_data() function using gspread for READS ONLY (no writes),
# since the quota problem was caused by writes. The function below assumes
# you have migrated static data into Supabase tables.
#
# If you kept static data in Sheets, replace fetch_master_data() with the
# original gspread version and keep only gspread + oauth2client in requirements.

@st.cache_data(ttl=600)
def fetch_master_data():
    """
    Fetches all static game data from Supabase. Cached for 10 minutes and
    shared across all users — same pattern as before, but now using proper
    SQL queries rather than downloading entire sheets.

    Expected Supabase tables
    ────────────────────────
    categories        — CategoryID, CategoryName, TotalRequired
    category_answers  — CategoryID, CorrectAnswer
    characters        — CharacterID_Old, CharacterName, Clue1, Clue2, Clue3
    """
    sb = get_supabase()

    try:
        categories = sb.table("categories").select("*").execute().data
        answers    = sb.table("category_answers").select("*").execute().data
        characters = sb.table("characters").select("*").execute().data
        return categories, answers, characters
    except Exception as exc:
        logger.error("fetch_master_data failed: %s", exc)
        raise


# ---------------------------------------------------------------------------
# USER DATA — reads
# ---------------------------------------------------------------------------

def fetch_user_history(user_id: str):
    """
    Loads the user's Mode 1 and Mode 2 history from Supabase into
    session_state. Called at login and on every menu visit (subject to
    a 60-second throttle).

    Key improvements over the Google Sheets version
    ────────────────────────────────────────────────
    • Supabase filters rows server-side (.eq("user_id", user_id)) so only
      the relevant rows travel over the wire — not the entire sheet.
    • Answers are stored as a JSONB array in one column, so no more parsing
      across 15 unnamed columns with fragile column-offset logic.
    • No per-minute quota — reads are limited only by Supabase's free tier
      daily allowance (50,000 reads/day), which this app will not approach.
    """
    sb          = get_supabase()
    total_score = 0

    # ---- Mode 1 ----
    try:
        rows = (
            sb.table("mode1_sessions")
            .select("category_id, score, answers")
            .eq("user_id", user_id)
            .order("score", desc=True)   # highest score first
            .execute()
            .data
        )

        history_map       = {}  # {category_id: best_score}
        saved_answers_map = {}  # {category_id: [answer words]}

        for row in rows:
            c_id  = str(row["category_id"])
            score = int(row["score"])

            # Because rows are ordered by score DESC, the first row for each
            # category is already the best — skip duplicates
            if c_id not in history_map:
                history_map[c_id]       = score
                # answers is a native Python list thanks to JSONB
                saved_answers_map[c_id] = row["answers"] or []

        st.session_state.history_mode1    = history_map
        st.session_state.m1_saved_answers = saved_answers_map
        total_score += sum(history_map.values())

    except Exception as exc:
        logger.warning("Could not load Mode 1 history for %s: %s", user_id, exc)
        st.warning("⚠️ Could not load your Category Mode history. "
                   "Previous progress may not be shown.")

    # ---- Mode 2 ----
    try:
        rows = (
            sb.table("mode2_sessions")
            .select("character_id, attempts")
            .eq("user_id", user_id)
            .eq("is_solved", True)
            .execute()
            .data
        )

        seen = set()
        m2_points = 0

        for row in rows:
            c_id = str(row["character_id"])
            if c_id not in seen:
                seen.add(c_id)
                attempts   = int(row["attempts"])
                m2_points += 3 if attempts == 0 else 2 if attempts == 1 else 1
                st.session_state.m2_progress[c_id] = {
                    "attempts": attempts,
                    "solved":   True,
                }

        total_score += m2_points

    except Exception as exc:
        logger.warning("Could not load Mode 2 history for %s: %s", user_id, exc)
        st.warning("⚠️ Could not load your Character Mode history. "
                   "Previous progress may not be shown.")

    st.session_state.score                = total_score
    st.session_state.last_history_refresh = time.time()


def _refresh_history_if_needed():
    """
    Calls fetch_user_history() only if more than 60 seconds have passed
    since the last read. Invoked on every menu page visit.
    """
    now  = time.time()
    last = st.session_state.get("last_history_refresh", 0)
    if now - last > 60:
        fetch_user_history(st.session_state.user_id)


def _recalculate_score():
    """
    Re-derives the total score from the in-memory history maps.
    Used after local state changes (Mode 2 solves) so the sidebar updates
    immediately without an extra database read.
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

def save_mode1_session(category_id: str, score: int, answers: list) -> bool:
    """
    Inserts one Mode 1 session row into Supabase, then re-reads history
    so session_state is immediately in sync.

    Why always insert (no update/upsert)?
    ──────────────────────────────────────
    Keeping every session row is the right approach for a game: it gives you
    a full audit trail and lets fetch_user_history() always find the best
    score without extra logic. The table is indexed on (user_id, category_id)
    so the query is fast regardless of how many rows accumulate.

    answers is stored as a native JSON array — no column-padding needed.
    """
    try:
        sb = get_supabase()
        sb.table("mode1_sessions").insert({
            "session_id":  f"SESS-{int(time.time())}",
            "user_id":     st.session_state.user_id,
            "category_id": category_id,
            "score":       score,
            "answers":     answers,      # Supabase handles list → JSONB natively
            "created_at":  datetime.datetime.utcnow().isoformat(),
        }).execute()

        # Re-read from DB so session_state exactly matches persisted state
        fetch_user_history(st.session_state.user_id)
        return True

    except Exception as exc:
        logger.error("Failed to save Mode 1 session for %s / %s: %s",
                     st.session_state.user_id, category_id, exc)
        st.error("❌ Your progress could not be saved. Please try again.")
        return False


def save_mode2_solve(char_id: str, attempts: int) -> None:
    """
    Inserts a single solved-character row into Supabase immediately on
    correct guess. Wrong guesses are not written — they are tracked in
    session_state only. One write per solve keeps usage low and guarantees
    the solve survives a tab close or session timeout.
    """
    try:
        sb = get_supabase()
        sb.table("mode2_sessions").insert({
            "solve_id":     f"SOLVE-{int(time.time())}-{char_id}",
            "user_id":      st.session_state.user_id,
            "character_id": char_id,
            "attempts":     attempts,
            "is_solved":    True,
            "created_at":   datetime.datetime.utcnow().isoformat(),
        }).execute()

    except Exception as exc:
        logger.error("Failed to save Mode 2 solve for %s: %s", char_id, exc)
        st.error("❌ Solve could not be saved. Please try again.")


# ---------------------------------------------------------------------------
# SESSION STATE DEFAULTS
# ---------------------------------------------------------------------------
_DEFAULTS = {
    "page":                 "home",
    "score":                0,
    "user_id":              "",
    "display_name":         "",
    "history_mode1":        {},
    "history_mode2":        [],
    "m1_answers":           [],
    "current_category":     None,
    "m2_progress":          {},
    "m1_saved_answers":     {},
    "last_history_refresh": 0,
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
    cat  = st.session_state.current_category
    req  = int(cat["TotalRequired"])
    c_id = str(cat["CategoryID"])

    st.title(f"Topic: {cat['CategoryName']}")
    st.progress(min(len(st.session_state.m1_answers) / req, 1.0))

    if st.session_state.m1_answers:
        st.success("✅ Correct so far: " + ", ".join(st.session_state.m1_answers))

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

    else:
        st.balloons()
        st.info(f"🎉 You found all {req} answers for **{cat['CategoryName']}**!")

        if st.button("✅ Finish & Lock Category", type="primary"):
            _commit_mode1_session(cat, c_id, navigate_to="mode1_select")


def _commit_mode1_session(cat, c_id, navigate_to):
    """
    Saves the current session unconditionally and navigates away on success.
    Stays on the play screen if the save fails so the user can retry.
    """
    new_score = len(st.session_state.m1_answers)

    with st.spinner("Saving your progress..."):
        saved_ok = save_mode1_session(
            c_id,
            new_score,
            st.session_state.m1_answers,
        )

    if saved_ok:
        st.session_state.page = navigate_to
        st.rerun()


# ---------------------------------------------------------------------------
# MODE 2 — Character guess
# ---------------------------------------------------------------------------

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
                            st.session_state.last_history_refresh = 0
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
