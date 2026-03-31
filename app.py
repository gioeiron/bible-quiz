import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import datetime
import time

# --- SETUP PAGE CONFIG ---
st.set_page_config(page_title="Bible Character Quiz", page_icon="📖", layout="wide")

# --- DATABASE CONNECTIONS (Cached) ---

@st.cache_resource
def get_gspread_client():
    """Establishes the connection to Google Sheets once."""
    if "gcp_service_account" in st.secrets:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(
            creds_dict, 
            ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        )
    else:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
    
    return gspread.authorize(creds)

def get_workbook():
    client = get_gspread_client()
    return client.open("Bible Character Game  - Python")

@st.cache_data(ttl=600) # Data is remembered for 10 minutes
def fetch_master_data():
    """Fetches all static game data in one go to save API calls."""
    sheet = get_workbook()
    categories = sheet.worksheet("1-Category").get_all_records()
    answers = sheet.worksheet("1-CategoryAnswer").get_all_records()
    characters = sheet.worksheet("2-Characters").get_all_records()
    return categories, answers, characters

# --- USER DATA OPERATIONS (Non-Cached) ---

def fetch_user_history(user_id):
    """Fetches dynamic user progress. This cannot be globally cached."""
    sheet = get_workbook()
    total_score = 0
    
    # 1. Mode 1 History
    try:
        m1_data = sheet.worksheet("Mode1_Sessions").get_all_records()
        user_m1 = [r for r in m1_data if str(r['UserEmail']) == user_id]
        history_map = {}
        for row in user_m1:
            c_id = str(row['CategoryID'])
            score = int(row.get('Score', 0))
            if c_id not in history_map or score > history_map[c_id]:
                history_map[c_id] = score
        st.session_state.history_mode1 = history_map
        total_score += sum(history_map.values())
    except: pass 

    # 2. Mode 2 History
    try:
        m2_data = sheet.worksheet("Mode2_Sessions").get_all_records()
        user_m2 = [r for r in m2_data if str(r['UserEmail']) == user_id and str(r['IsSolved']).upper() == "TRUE"]
        solved_chars = set()
        m2_points = 0
        for row in user_m2:
            c_id = str(row['CharacterID'])
            if c_id not in solved_chars:
                solved_chars.add(c_id)
                attempts = int(row.get('CurrentAttempts', 2))
                m2_points += (3 if attempts == 0 else 2 if attempts == 1 else 1)
        total_score += m2_points
        for c_id in solved_chars:
            st.session_state.m2_progress[c_id] = {'attempts': 0, 'solved': True}
    except: pass

    st.session_state.score = total_score

def save_mode1_session(category_id, score, answers):
    sheet = get_workbook().worksheet("Mode1_Sessions")
    row = [f"SESS-{int(time.time())}", category_id, st.session_state.user_id, 
           datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), score] + answers
    row += [""] * (20 - len(row))
    sheet.append_row(row)

def save_mode2_guess(char_id, attempts, solved, guess):
    sheet = get_workbook().worksheet("Mode2_Sessions")
    row = [f"GUESS-{int(time.time())}-{char_id}", char_id, st.session_state.user_id, attempts, str(solved).upper(), guess]
    sheet.append_row(row)

# --- INITIALIZE SESSION STATE ---
for key, val in {
    'page': 'home', 'score': 0, 'user_id': "", 'display_name': "",
    'history_mode1': {}, 'history_mode2': [], 'm1_answers': [], 
    'current_category': None, 'm2_progress': {}
}.items():
    if key not in st.session_state:
        st.session_state[key] = val

# --- UI COMPONENTS ---

def render_sidebar():
    if st.session_state.user_id:
        with st.sidebar:
            st.header(f"👤 {st.session_state.display_name}")
            st.metric("TOTAL SCORE", st.session_state.score)
            st.divider()
            if st.button("🏠 Home", use_container_width=True):
                st.session_state.page = 'menu'
                st.rerun()
            if st.button("Log Out", type="secondary", use_container_width=True):
                st.session_state.clear()
                st.rerun()

# --- PAGES ---

def home_page():
    st.title("📖 Bible Characters Quiz")
    name = st.text_input("Name / Nickname").strip()
    pin = st.text_input("4-Digit PIN", type="password").strip()
    
    if st.button("Start Game", type="primary"):
        if name and len(pin) >= 2:
            st.session_state.user_id = f"{name}_{pin}"
            st.session_state.display_name = name
            with st.spinner("Loading your score..."):
                fetch_user_history(st.session_state.user_id)
            st.session_state.page = 'menu'
            st.rerun()

def mode1_select(categories):
    st.title("📂 Name All by Category")
    for cat in categories:
        c_id, req, name = str(cat['CategoryID']), cat['TotalRequired'], cat['CategoryName']
        best_score = st.session_state.history_mode1.get(c_id, 0)
        is_complete = (best_score >= req)
        
        with st.container(border=True):
            col1, col2 = st.columns([3, 1])
            col1.subheader(name)
            if is_complete: col1.success(f"✅ Completed! ({best_score}/{req})")
            else: col1.write(f"Best Score: {best_score}/{req}")
            
            if not is_complete and col2.button("Play", key=f"p_{c_id}", type="primary"):
                st.session_state.current_category = cat
                st.session_state.m1_answers = [] 
                st.session_state.page = 'mode1_play'
                st.rerun()

def mode1_play(all_answers_data):
    cat = st.session_state.current_category
    st.title(f"Topic: {cat['CategoryName']}")
    
    req = int(cat['TotalRequired'])
    st.progress(len(st.session_state.m1_answers) / req)
    
    if st.session_state.m1_answers:
        st.success(f"✅ Correct: {', '.join(st.session_state.m1_answers)}")

    if len(st.session_state.m1_answers) < req:
        with st.form("ans_form", clear_on_submit=True):
            user_input = st.text_input("Enter an answer:").strip().lower()
            if st.form_submit_button("Submit") and user_input:
                valid_answers = [str(r['CorrectAnswer']).lower() for r in all_answers_data 
                                 if str(r['CategoryID']) == str(cat['CategoryID'])]
                
                if user_input in valid_answers:
                    if user_input not in [x.lower() for x in st.session_state.m1_answers]:
                        st.session_state.m1_answers.append(user_input.title())
                        st.rerun()
                else: st.error("Incorrect.")

        # --- FIX: Update session state before saving ---
        if st.button("💾 Save & Exit"):
            c_id = str(cat['CategoryID'])
            new_score = len(st.session_state.m1_answers)
            previous_best = st.session_state.history_mode1.get(c_id, 0)
            
            # Update local memory so the UI changes immediately
            if new_score > previous_best:
                st.session_state.score += (new_score - previous_best)
                st.session_state.history_mode1[c_id] = new_score

            save_mode1_session(cat['CategoryID'], new_score, st.session_state.m1_answers)
            st.session_state.page = 'mode1_select'
            st.rerun()
    else:
        st.balloons()
        
        # --- FIX: Update session state before finishing ---
        if st.button("Finish"):
            c_id = str(cat['CategoryID'])
            new_score = len(st.session_state.m1_answers)
            previous_best = st.session_state.history_mode1.get(c_id, 0)
            
            # Update local memory so the UI changes immediately
            if new_score > previous_best:
                st.session_state.score += (new_score - previous_best)
                st.session_state.history_mode1[c_id] = new_score

            save_mode1_session(cat['CategoryID'], new_score, st.session_state.m1_answers)
            st.session_state.page = 'mode1_select'
            st.rerun()

def mode2_play(characters):
    st.title("🕵️ Guess the Character")
    for i, char in enumerate(characters):
        c_id, correct_name = str(char['CharacterID_Old']), str(char['CharacterName']).strip()
        state = st.session_state.m2_progress.setdefault(c_id, {'attempts': 0, 'solved': False})
        
        with st.container(border=True):
            col1, col2 = st.columns([3, 1])
            if state['solved']: col1.success(f"✅ **Character #{i+1}:** {correct_name}")
            else:
                col1.markdown(f"**Character #{i+1}**")
                clues = [char['Clue1'], char['Clue2'], char['Clue3']]
                for k in range(min(state['attempts'] + 1, 3)):
                    col1.write(f"🔹 *Clue {k+1}:* {clues[k]}")
                
                with col2.form(key=f"f2_{c_id}"):
                    guess = st.text_input("Guess").strip().lower()
                    if st.form_submit_button("Submit"):
                        if guess == correct_name.lower():
                            pts = {0:3, 1:2}.get(state['attempts'], 1)
                            st.session_state.m2_progress[c_id]['solved'] = True
                            st.session_state.score += pts
                            save_mode2_guess(c_id, state['attempts'], True, guess)
                            st.rerun()
                        else:
                            st.session_state.m2_progress[c_id]['attempts'] += 1
                            save_mode2_guess(c_id, state['attempts'], False, guess)
                            st.rerun()

def main():
    render_sidebar()
    # Fetch all static data once (Cached for 10 mins)
    try:
        categories, answers, characters = fetch_master_data()
    except Exception as e:
        st.error("Database Connection Failed")
        return

    if st.session_state.page == 'home': home_page()
    elif st.session_state.page == 'menu': 
        st.title(f"Welcome, {st.session_state.display_name}!")
        col1, col2 = st.columns(2)
        if col1.button("Play Category Mode", type="primary", use_container_width=True):
            st.session_state.page = 'mode1_select'; st.rerun()
        if col2.button("Play Character Mode", type="primary", use_container_width=True):
            st.session_state.page = 'mode2_play'; st.rerun()
    elif st.session_state.page == 'mode1_select': mode1_select(categories)
    elif st.session_state.page == 'mode1_play': mode1_play(answers)
    elif st.session_state.page == 'mode2_play': mode2_play(characters)

if __name__ == "__main__":
    main()
