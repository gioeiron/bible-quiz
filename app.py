import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import datetime
import time

# --- SETUP PAGE CONFIG ---
st.set_page_config(page_title="Bible Character Quiz", page_icon="📖", layout="wide")

# --- CONNECT TO GOOGLE SHEETS ---
@st.cache_resource
def connect_to_sheet():
    # Attempt to load from Streamlit Secrets (for Cloud Deployment)
    if "gcp_service_account" in st.secrets:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(
            creds_dict, 
            ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        )
    # Fallback to local file (for Local Testing)
    else:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
    
    client = gspread.authorize(creds)
    return client.open("Bible Character Game  - Python")

# --- INITIALIZE SESSION STATE ---
if 'page' not in st.session_state:
    st.session_state.page = 'home'
if 'score' not in st.session_state:
    st.session_state.score = 0
if 'user_email' not in st.session_state:
    st.session_state.user_email = ""
    
# History State (To remember what is finished)
if 'history_mode1' not in st.session_state:
    st.session_state.history_mode1 = {} # { 'cat_id': best_score }
if 'history_mode2' not in st.session_state:
    st.session_state.history_mode2 = [] # [ 'char_id_solved', ... ]

# Mode 1 Specifics
if 'm1_answers' not in st.session_state:
    st.session_state.m1_answers = []
if 'current_category' not in st.session_state:
    st.session_state.current_category = None
    
# Mode 2 Specifics
if 'm2_progress' not in st.session_state:
    st.session_state.m2_progress = {} 

# --- HELPER FUNCTIONS ---

def fetch_user_history(sheet, email):
    """
    Reads the session sheets to find out what the user has already completed.
    This prevents re-playing finished levels even after browser refresh.
    """
    # 1. Fetch Mode 1 History
    try:
        m1_sheet = sheet.worksheet("Mode1_Sessions")
        m1_data = m1_sheet.get_all_records()
        
        # Filter for current user and find max score per category
        user_m1 = [r for r in m1_data if str(r['UserEmail']).lower() == email.lower()]
        history_map = {}
        for row in user_m1:
            c_id = str(row['CategoryID'])
            score = int(row['Score']) if row['Score'] else 0
            # Keep only the highest score achieved
            if c_id not in history_map or score > history_map[c_id]:
                history_map[c_id] = score
        
        st.session_state.history_mode1 = history_map
    except Exception:
        pass # If sheet is empty or error, just ignore history

    # 2. Fetch Mode 2 History (Optional: To calculate Total Score correctly on login)
    try:
        m2_sheet = sheet.worksheet("Mode2_Sessions")
        m2_data = m2_sheet.get_all_records()
        user_m2 = [r for r in m2_data if str(r['UserEmail']).lower() == email.lower() and str(r['IsSolved']).upper() == "TRUE"]
        # In a real app we might sum these up, for now we just track logic
    except Exception:
        pass

def save_mode1_session(sheet, category_id, score, answers):
    session_sheet = sheet.worksheet("Mode1_Sessions")
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    session_id = f"SESS-{int(time.time())}"
    row_data = [session_id, category_id, st.session_state.user_email, timestamp, score] + answers
    while len(row_data) < 20: 
        row_data.append("")
    session_sheet.append_row(row_data)
    
    # Update local history immediately
    current_best = st.session_state.history_mode1.get(str(category_id), 0)
    if score > current_best:
        st.session_state.history_mode1[str(category_id)] = score

def save_mode2_guess(sheet, char_id, attempts, solved, guess):
    session_sheet = sheet.worksheet("Mode2_Sessions")
    guess_id = f"GUESS-{int(time.time())}-{char_id}"
    row_data = [guess_id, char_id, st.session_state.user_email, attempts, str(solved).upper(), guess]
    session_sheet.append_row(row_data)

# --- NAVIGATION SIDEBAR ---

def render_sidebar():
    if st.session_state.user_email:
        with st.sidebar:
            st.header(f"👤 Player")
            st.write(st.session_state.user_email)
            st.metric("Current Session Points", st.session_state.score)
            
            st.divider()
            st.subheader("Navigation")
            
            if st.button("🏠 Bible Quiz Home", use_container_width=True):
                st.session_state.page = 'menu'
                st.rerun()

            if st.button("📂 Mode 1: Categories", use_container_width=True):
                st.session_state.page = 'mode1_select'
                st.session_state.m1_answers = [] 
                st.rerun()

            if st.button("🕵️ Mode 2: Characters", use_container_width=True):
                st.session_state.page = 'mode2_play'
                st.rerun()
                
            st.divider()
            
            if st.button("Log Out", type="primary", use_container_width=True):
                for key in list(st.session_state.keys()):
                    del st.session_state[key]
                st.rerun()

# --- PAGES ---

def home_page(sheet):
    st.title("📖 Bible Characters Quiz")
    st.write("Please enter your email to start.")
    
    email = st.text_input("Email Address", placeholder="you@example.com")
    
    if st.button("Start Game", type="primary"):
        if email:
            st.session_state.user_email = email
            # Load previous progress
            with st.spinner("Loading your profile..."):
                fetch_user_history(sheet, email)
            
            st.session_state.page = 'menu'
            st.rerun()
        else:
            st.error("Please enter an email address.")

def menu_page():
    st.title("Bible Characters Quiz")
    st.write("Select a game mode:")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.info("### Mode 1: Category")
        st.write("Name a specific number of items that match a category.")
        if st.button("Play Category Mode"):
            st.session_state.page = 'mode1_select'
            st.rerun()

    with col2:
        st.info("### Mode 2: Characters")
        st.write("Guess the Bible character from clues. (Points: 3, 2, or 1)")
        if st.button("Play Character Mode"):
            st.session_state.page = 'mode2_play'
            st.rerun()

def mode1_select(sheet):
    st.title("📂 Mode 1: Categories")
    st.write("Choose a category to play. Completed categories are locked.")
    
    try:
        cat_sheet = sheet.worksheet("1-Category")
        categories = cat_sheet.get_all_records()
    except Exception as e:
        st.error(f"Error loading categories: {e}")
        return
    
    # Display Categories as a List of Cards
    for cat in categories:
        c_id = str(cat['CategoryID'])
        req = cat['TotalRequired']
        name = cat['CategoryName']
        
        # Check History
        best_score = st.session_state.history_mode1.get(c_id, 0)
        is_complete = (best_score >= req)
        
        with st.container(border=True):
            col_info, col_action = st.columns([3, 1])
            
            with col_info:
                st.subheader(name)
                if is_complete:
                    st.success(f"✅ Completed! ({best_score}/{req})")
                elif best_score > 0:
                    st.warning(f"⚠️ In Progress: Best Score {best_score}/{req}")
                else:
                    st.write(f"Target: {req} items")
            
            with col_action:
                if is_complete:
                    st.button("Done", key=f"done_{c_id}", disabled=True)
                else:
                    if st.button("Play", key=f"play_{c_id}", type="primary"):
                        st.session_state.current_category = cat
                        st.session_state.m1_answers = [] # Reset for new run
                        st.session_state.page = 'mode1_play'
                        st.rerun()

def mode1_play(sheet):
    cat = st.session_state.current_category
    if not cat:
        st.session_state.page = 'mode1_select'
        st.rerun()
        return

    st.title(f"Topic: {cat['CategoryName']}")
    st.write(f"Please list **{cat['TotalRequired']}** answers.")
    
    # Progress Bar
    progress_val = len(st.session_state.m1_answers) / cat['TotalRequired']
    st.progress(progress_val)
    st.write(f"Found: **{len(st.session_state.m1_answers)} / {cat['TotalRequired']}**")
    
    if st.session_state.m1_answers:
        st.success(f"✅ Correct so far: {', '.join(st.session_state.m1_answers)}")

    # Game Loop
    if len(st.session_state.m1_answers) < cat['TotalRequired']:
        with st.form("ans_form", clear_on_submit=True):
            user_input = st.text_input("Enter an answer:")
            submitted = st.form_submit_button("Submit")
            
            if submitted and user_input:
                # Validation Logic
                ans_sheet = sheet.worksheet("1-CategoryAnswer")
                all_answers = ans_sheet.get_all_records()
                target_cat_id = str(cat['CategoryID']).strip()
                
                valid_answers = [
                    str(r['CorrectAnswer']).strip().lower() 
                    for r in all_answers 
                    if str(r['CategoryID']).strip() == target_cat_id
                ]
                
                clean_input = user_input.strip().lower()
                
                if clean_input in valid_answers:
                    current_found = [x.lower() for x in st.session_state.m1_answers]
                    if clean_input in current_found:
                        st.warning(f"⚠️ Duplicate: '{user_input}'")
                    else:
                        st.session_state.m1_answers.append(user_input.strip().title())
                        st.rerun()
                else:
                    st.error(f"❌ '{user_input}' is incorrect.")
        
        st.markdown("---")
        if st.button("💾 Give Up & Save Score"):
            points = len(st.session_state.m1_answers)
            save_mode1_session(sheet, cat['CategoryID'], points, st.session_state.m1_answers)
            st.session_state.score += points
            st.success(f"Saved! You got {points} points.")
            time.sleep(1.5)
            st.session_state.page = 'mode1_select' # Go back to list
            st.rerun()

    else:
        st.balloons()
        st.success("🎉 PERFECT SCORE!")
        if st.button("Finish & Save"):
            save_mode1_session(sheet, cat['CategoryID'], len(st.session_state.m1_answers), st.session_state.m1_answers)
            st.session_state.score += len(st.session_state.m1_answers)
            st.session_state.page = 'mode1_select'
            st.rerun()

def mode2_play(sheet):
    st.title("🕵️ Guess the Character")
    st.info("Points: 1st Try = 3pts | 2nd Try = 2pts | 3rd Try = 1pt")
    
    try:
        char_sheet = sheet.worksheet("2-Characters")
        characters = char_sheet.get_all_records()
    except Exception as e:
        st.error(f"Error loading characters: {e}")
        return

    for char in characters:
        c_id = str(char['CharacterID_Old'])
        correct_name = str(char['CharacterName']).strip()
        
        # Init State
        if c_id not in st.session_state.m2_progress:
            st.session_state.m2_progress[c_id] = {'attempts': 0, 'solved': False}
        
        state = st.session_state.m2_progress[c_id]
        
        with st.container(border=True):
            col_clues, col_interaction = st.columns([3, 1])
            
            with col_clues:
                if state['solved']:
                    st.success(f"✅ **SOLVED:** {correct_name}")
                else:
                    st.markdown(f"**Character #{c_id}**")
                    clues = [char['Clue1'], char['Clue2'], char['Clue3']]
                    visible_count = min(state['attempts'] + 1, 3)
                    
                    for i in range(visible_count):
                        st.write(f"🔹 *Clue {i+1}:* {clues[i]}")
                    
                    if state['attempts'] >= 2:
                        st.warning("⚠️ Final Clue!")

            with col_interaction:
                if not state['solved']:
                    guess = st.text_input("Guess", key=f"input_{c_id}")
                    if st.button("Submit", key=f"btn_{c_id}"):
                        clean_guess = guess.strip().lower()
                        clean_answer = correct_name.lower()
                        
                        if clean_guess == clean_answer:
                            # --- SCORING LOGIC UPDATE ---
                            # Attempts=0 -> 3pts, Attempts=1 -> 2pts, Attempts>=2 -> 1pt
                            points_map = {0: 3, 1: 2}
                            points_earned = points_map.get(state['attempts'], 1)
                            
                            st.session_state.m2_progress[c_id]['solved'] = True
                            st.session_state.score += points_earned
                            
                            save_mode2_guess(sheet, c_id, state['attempts'], True, guess)
                            st.toast(f"Correct! +{points_earned} Points")
                            st.rerun()
                        else:
                            st.error("Wrong")
                            st.session_state.m2_progress[c_id]['attempts'] += 1
                            save_mode2_guess(sheet, c_id, state['attempts'], False, guess)
                            st.rerun()

# --- MAIN APP ---

def main():
    try:
        sheet = connect_to_sheet()
    except Exception as e:
        st.error("❌ Database Connection Failed")
        st.code(str(e))
        return

    render_sidebar()

    if st.session_state.page == 'home':
        home_page(sheet)
    elif st.session_state.page == 'menu':
        menu_page()
    elif st.session_state.page == 'mode1_select':
        mode1_select(sheet)
    elif st.session_state.page == 'mode1_play':
        mode1_play(sheet)
    elif st.session_state.page == 'mode2_play':
        mode2_play(sheet)

if __name__ == "__main__":
    main()
