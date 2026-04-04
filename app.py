# FULL FIXED VERSION (WITH ORIGINAL UI/UX RESTORED)

import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import datetime
import time
import logging
import hashlib

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

st.set_page_config(page_title="Bible Character Quiz", page_icon="📖", layout="wide")

# -----------------------------
# UTILITIES
# -----------------------------

def generate_user_id(name, pin):
    return hashlib.sha256(f"{name}_{pin}".encode()).hexdigest()


def safe_append(sheet, row, retries=3):
    for _ in range(retries):
        try:
            sheet.append_row(row)
            return True
        except Exception:
            time.sleep(1)
    return False

# -----------------------------
# DB CONNECTION
# -----------------------------

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

# -----------------------------
# USER HISTORY
# -----------------------------

def fetch_user_history(user_id):
    wb = get_workbook()
    total_score = 0

    history_map = {}
    saved_answers_map = {}
    latest_time_map = {}

    try:
        rows = wb.worksheet("Mode1_Sessions").get_all_records()
        for r in rows:
            if str(r["UserEmail"]) != user_id:
                continue

            c_id = str(r["CategoryID"])
            score = int(r.get("Score", 0))
            timestamp = r.get("Timestamp", "")

            words = [
                str(v).strip()
                for k, v in r.items()
                if k not in {"SessionID","CategoryID","UserEmail","Timestamp","Score"}
                and str(v).strip() != ""
            ]

            if c_id not in history_map or timestamp > latest_time_map.get(c_id, ""):
                history_map[c_id] = score
                saved_answers_map[c_id] = words
                latest_time_map[c_id] = timestamp

        st.session_state.history_mode1 = history_map
        st.session_state.m1_saved_answers = saved_answers_map
        total_score += sum(history_map.values())

    except Exception as e:
        logger.warning(e)

    # MODE 2
    try:
        rows = wb.worksheet("Mode2_Sessions").get_all_records()
        solved = {}

        for r in rows:
            if str(r["UserEmail"]) != user_id:
                continue
            if str(r["IsSolved"]).upper() != "TRUE":
                continue

            c_id = str(r["CharacterID"])
            attempts = int(r.get("CurrentAttempts", 2))

            if c_id not in solved:
                solved[c_id] = attempts

        st.session_state.m2_progress = {
            k: {"attempts": v, "solved": True} for k, v in solved.items()
        }

        total_score += sum(
            3 if v == 0 else 2 if v == 1 else 1
            for v in solved.values()
        )

    except Exception as e:
        logger.warning(e)

    st.session_state.score = total_score

# -----------------------------
# SAVE FUNCTIONS
# -----------------------------

def save_mode1_session(category_id, score, answers):
    sheet = get_workbook().worksheet("Mode1_Sessions")

    row = [
        f"SESS-{int(time.time())}",
        category_id,
        st.session_state.user_id,
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        score
    ] + answers

    row += [""] * max(0, 20 - len(row))

    safe_append(sheet, row)


def save_mode2_solve(char_id, attempts):
    sheet = get_workbook().worksheet("Mode2_Sessions")

    row = [
        f"SOLVE-{int(time.time())}-{char_id}",
        char_id,
        st.session_state.user_id,
        attempts,
        "TRUE",
        ""
    ]

    safe_append(sheet, row)

# -----------------------------
# SESSION DEFAULTS
# -----------------------------

DEFAULTS = {
    "page":"home",
    "score":0,
    "user_id":"",
    "display_name":"",
    "history_mode1":{},
    "m1_saved_answers":{},
    "m1_answers":[],
    "current_category":None,
    "m2_progress":{}
}

for k,v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k]=v

# -----------------------------
# SIDEBAR (RESTORED)
# -----------------------------

def render_sidebar():
    if not st.session_state.user_id:
        return

    with st.sidebar:
        st.header(f"👤 {st.session_state.display_name}")
        st.metric("TOTAL SCORE", st.session_state.score)

        if st.button("🏠 Home"):
            st.session_state.page="menu"
            st.rerun()

        if st.button("Log Out"):
            st.session_state.clear()
            st.rerun()

# -----------------------------
# PAGES (RESTORED UX)
# -----------------------------

def home():
    st.title("📖 Bible Characters Quiz")
    name = st.text_input("Name / Nickname")
    pin = st.text_input("PIN", type="password")

    if st.button("Start Game", type="primary"):
        if name and pin:
            st.session_state.user_id = generate_user_id(name,pin)
            st.session_state.display_name = name
            fetch_user_history(st.session_state.user_id)
            st.session_state.page="menu"
            st.rerun()


def menu():
    st.title(f"Welcome, {st.session_state.display_name}! 👋")

    col1,col2 = st.columns(2)

    if col1.button("📂 Play Category Mode", use_container_width=True):
        st.session_state.page="mode1"
        st.rerun()

    if col2.button("🕵️ Play Character Mode", use_container_width=True):
        st.session_state.page="mode2"
        st.rerun()

# -----------------------------
# MODE 1 (RESTORED UI + AUTOSAVE)
# -----------------------------

def mode1(categories,answers):
    st.title("📂 Name All by Category")

    for cat in categories:
        cid=str(cat["CategoryID"])
        req=int(cat["TotalRequired"])
        best = st.session_state.history_mode1.get(cid,0)

        with st.container(border=True):
            col1,col2 = st.columns([3,1])
            col1.subheader(cat["CategoryName"])

            if best>=req:
                col1.success(f"Completed ({best}/{req})")
                saved = st.session_state.m1_saved_answers.get(cid,[])
                if saved:
                    col1.caption(", ".join(saved))
            else:
                if col2.button("Play",key=cid):
                    st.session_state.current_category=cat
                    st.session_state.m1_answers = st.session_state.m1_saved_answers.get(cid,[])
                    st.session_state.page="play1"
                    st.rerun()


def play1(answers):
    cat = st.session_state.current_category
    cid=str(cat["CategoryID"])
    req=int(cat["TotalRequired"])

    st.title(f"Topic: {cat['CategoryName']}")
    st.progress(min(len(st.session_state.m1_answers)/req,1.0))

    if st.session_state.m1_answers:
        st.success(", ".join(st.session_state.m1_answers))

    guess = st.text_input("Enter answer")

    if st.button("Submit"):
        valid=[str(r["CorrectAnswer"]).lower() for r in answers if str(r["CategoryID"])==cid]

        if guess.lower() in valid and guess.title() not in st.session_state.m1_answers:
            st.session_state.m1_answers.append(guess.title())

            # AUTOSAVE
            save_mode1_session(cid,len(st.session_state.m1_answers),st.session_state.m1_answers)

            st.rerun()
        else:
            st.error("Incorrect or duplicate")

    if len(st.session_state.m1_answers)>=req:
        st.balloons()
        st.success("Completed!")

# -----------------------------
# MODE 2 (RESTORED UI)
# -----------------------------

def mode2(characters):
    st.title("🕵️ Guess the Character")

    for i,char in enumerate(characters):
        cid=str(char["CharacterID_Old"])
        name=str(char["CharacterName"]).lower()

        state=st.session_state.m2_progress.setdefault(cid,{"attempts":0,"solved":False})

        with st.container(border=True):
            col1,col2 = st.columns([3,1])

            if state["solved"]:
                col1.success(name)
            else:
                clues=[char["Clue1"],char["Clue2"],char["Clue3"]]
                for k in range(min(state["attempts"]+1,3)):
                    col1.write(clues[k])

                guess = col2.text_input("Guess",key=f"g_{cid}")

                if col2.button("Submit",key=f"b_{cid}"):
                    if guess.lower()==name:
                        save_mode2_solve(cid,state["attempts"])
                        st.session_state.m2_progress[cid]={"attempts":state["attempts"],"solved":True}
                        st.rerun()
                    else:
                        state["attempts"]+=1
                        st.rerun()

# -----------------------------
# MAIN
# -----------------------------

def main():
    render_sidebar()

    categories,answers,characters = fetch_master_data()

    if st.session_state.page=="home":
        home()
    elif st.session_state.page=="menu":
        menu()
    elif st.session_state.page=="mode1":
        mode1(categories,answers)
    elif st.session_state.page=="play1":
        play1(answers)
    elif st.session_state.page=="mode2":
        mode2(characters)

if __name__=="__main__":
    main()
