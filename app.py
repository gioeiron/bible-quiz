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
if 'user_id' not in st.session_state:
    st.session_state.user_id = "" # Stores "Name_PIN"
if 'display_name' not in st.session_state:
    st.session_state.display_name = ""

# History State (To remember what is finished)
if 'history_mode1' not in st.session_state:
    st.session_state.history_mode1 = {} # { 'cat_id': best_score }
if 'history_mode2' not in st.session_state:
    st.session_state.history_mode2 = [] 

# Game Specifics
if 'm1_answers' not in st.session_state:
    st.session_state.m1_answers = []
if 'current_category' not in st.session_state:
    st.session_state.current_category = None
if 'm2_progress' not in st.session_state:
    st.session_state.m2_progress = {} 

# --- HELPER FUNCTIONS ---

def fetch_user_history(sheet, user_id):
    """
    Calculates Total Score from Database and loads progress.
    """
    total_calculated_score = 0
    
    # 1. Fetch Mode 1 History & Score
    try:
        m1_sheet = sheet.worksheet("Mode1_Sessions")
        m1_data = m1_sheet.get_all_records()
        
        # Filter for current user
        user_m1 = [r for r in m1_data if str(r['UserEmail']) == user_id]
        history_map = {}
        
        for row in user_m1:
            c_id = str(row['CategoryID'])
            try:
                score = int(row['Score'])
            except:
                score = 0
                
            # Keep only the highest score achieved per category
            if c_id not in history_map or score > history_map[c_id]:
                history_map[c_id] = score
        
        st.session_state.history_mode1 = history_map
        # Add Mode 1 points to total
        total_calculated_score += sum(history_map.values())
        
    except Exception:
        pass 

    # 2. Fetch Mode 2 History & Score
    try:
        m2_sheet = sheet.worksheet("Mode2_Sessions")
        m2_data = m2_sheet.get_all_records()
        
        # Filter: Matches User AND IsSolved = TRUE
        user_m2 = [r for r in m2_data if str(r['UserEmail']) == user_id and str(r['IsSolved']).upper() == "TRUE"]
        
        solved_chars = set()
        m2_points = 0
        
        for row in user_m2:
            c_id = str(row['CharacterID'])
            if c_id not in solved_chars:
                solved_chars.add(c_id)
                
                try:
                    attempts = int(row['CurrentAttempts'])
                except:
                    attempts = 2 # Default to 1pt if data error
                
                # Scoring Logic: 0 attempts=3pts, 1 attempt=2pts, >=2 attempts=1pt
                if attempts == 0:
                    m2_points += 3
                elif attempts == 1:
                    m2_points += 2
                else:
                    m2_points += 1
                    
        total_calculated_score += m2_points
        
        # Pre-fill m2_progress so UI shows "Solved" immediately
        for c_id in solved_chars:
             if c_id not in st.session_state.m2_progress:
                 st.session_state.m2_progress[c_id] = {'attempts': 0, 'solved': True}
             else:
                 st.session_state.m2_progress[c_id]['solved'] = True

    except Exception:
        pass

    # SET THE GLOBAL SCORE
    st.session_state.score = total_calculated_score

def save_mode1_session(sheet, category_id, score, answers):
    session_sheet = sheet.worksheet("Mode1_Sessions")
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    session_id = f"SESS-{int(time.time())}"
    
    # Use user_id (Name_PIN) for storage
    row_data = [session_id, category_id, st.session_state.user_id, timestamp, score] + answers
    while len(row_data) < 20: 
        row_data.append("")
    session_sheet.append_row(row_data)

def save_mode2_guess(sheet, char_id, attempts, solved, guess):
    session_sheet = sheet.worksheet("Mode2_Sessions")
    guess_id = f"GUESS-{int(time.time())}-{char_id}"
    row_data = [guess_id, char_id, st.session_state.user_id, attempts, str(solved).upper(), guess]
    session_sheet.append_row(row_data)

# --- NAVIGATION SIDEBAR ---

def render_sidebar():
    if st.session_state.user_id:
        with st.sidebar:
            st.header(f"👤 {st.session_state.display_name}")
            st.caption(f"ID: {st.session_state.user_id}")
            st.metric("TOTAL SCORE", st.session_state.score)
            
            st.divider()
            st.subheader("Navigation")
            
            if st.button("🏠 Home", use_container_width=True):
                st.session_state.page = 'menu'
                st.rerun()

            if st.button("📂 Name All by Category", use_container_width=True):
                st.session_state.page = 'mode1_select'
                st.session_state.m1_answers = [] 
                st.rerun()

            if st.button("🕵️ Guess the Character", use_container_width=True):
                st.session_state.page = 'mode2_play'
                st.rerun()
                
            st.divider()
            
            if st.button("Log Out", type="secondary", use_container_width=True):
                for key in list(st.session_state.keys()):
                    del st.session_state[key]
                st.rerun()

# --- PAGES ---

def home_page(sheet):
    st.title("📖 Bible Characters Quiz")
    st.write("Enter your Name and a secret PIN to access your game.")
    
    col1, col2 = st.columns([2, 1])
    with col1:
        name = st.text_input("Name / Nickname", placeholder="e.g. Juan").strip()
    with col2:
        pin = st.text_input("4-Digit PIN", type="password", placeholder="1234", max_chars=4).strip()
    
    if st.button("Start Game", type="primary"):
        if name and len(pin) >= 2:
            # Create Unique ID: Name_PIN
            unique_id = f"{name}_{pin}"
            
            st.session_state.user_id = unique_id
            st.session_state.display_name = name
            
            # Load previous progress
            with st.spinner("Loading your score..."):
                fetch_user_history(sheet, unique_id)
            
            st.session_state.page = 'menu'
            st.rerun()
        else:
            st.error("Please enter a Name and a PIN (at least 2 digits).")

def menu_page():
    st.title(f"Welcome, {st.session_state.display_name}!")
    st.write("Select a game mode:")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.info("### Name All by Category")
        st.write("Name the characters that match the category.")
        if st.button("Play Category Mode", type="primary"): # Made PROMINENT (Red)
            st.session_state.page = 'mode1_select'
            st.rerun()

    with col2:
        st.info("### Guess the Character")
        st.write("Guess the Bible character from a series of clues that describe them.")
        if st.button("Play Character Mode"):
            st.session_state.page = 'mode2_play'
            st.rerun()

def mode1_select(sheet):
    st.title("📂 Name All by Category")
    
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
                        st.session_state.m1_answers = [] 
                        st.session_state.page = 'mode1_play'
                        st.rerun()

def mode1_play(sheet):
    cat = st.session_state.current_category
    if not cat:
        st.session_state.page = 'mode1_select'
        st.rerun()
        return

    st.title(f"Topic: {cat['CategoryName']}")
    # Updated Instruction Text
    st.write(f"You need a total of **{cat['TotalRequired']}** answers. Provide one name at a time. Check your spelling!")
    
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
            new_score = len(st.session_state.m1_answers)
            c_id = str(cat['CategoryID'])
            
            # --- SCORING FIX ---
            previous_best = st.session_state.history_mode1.get(c_id, 0)
            
            if new_score > previous_best:
                diff = new_score - previous_best
                st.session_state.score += diff
                st.session_state.history_mode1[c_id] = new_score
                st.success(f"New High Score! Added +{diff} points.")
            else:
                st.info(f"Saved. (You didn't beat your previous record of {previous_best})")

            save_mode1_session(sheet, cat['CategoryID'], new_score, st.session_state.m1_answers)
            
            time.sleep(2)
            st.session_state.page = 'mode1_select' 
            st.rerun()

    else:
        st.balloons()
        st.success("🎉 PERFECT SCORE!")
        if st.button("Finish & Save"):
            new_score = len(st.session_state.m1_answers)
            c_id = str(cat['CategoryID'])
            
            previous_best = st.session_state.history_mode1.get(c_id, 0)
            
            if new_score > previous_best:
                diff = new_score - previous_best
                st.session_state.score += diff
                st.session_state.history_mode1[c_id] = new_score
            
            save_mode1_session(sheet, cat['CategoryID'], len(st.session_state.m1_answers), st.session_state.m1_answers)
            
            st.session_state.page = 'mode1_select'
            st.rerun()

def mode2_play(sheet):
    st.title("🕵️ Guess the Character")
    # Updated Instruction Text
    st.info("Guess the character based on the clue. First attempt gives you 3 points. Wrong answers will provide more clues but minus 1 point. Check your spelling!")
    
    try:
        char_sheet = sheet.worksheet("2-Characters")
        characters = char_sheet.get_all_records()
    except Exception as e:
        st.error(f"Error loading characters: {e}")
        return

    # Use Enumerate for Display Label
    for i, char in enumerate(characters):
        c_id = str(char['CharacterID_Old'])
        correct_name = str(char['CharacterName']).strip()
        display_label = f"Character #{i + 1}" # Generic Label
        
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
                    st.markdown(f"**{display_label}**") # Show Generic Label
                    clues = [char['Clue1'], char['Clue2'], char['Clue3']]
                    visible_count = min(state['attempts'] + 1, 3)
                    
                    for k in range(visible_count):
                        st.write(f"🔹 *Clue {k+1}:* {clues[k]}")
                    
                    if state['attempts'] >= 2:
                        st.warning("⚠️ Final Clue!")

            with col_interaction:
                if not state['solved']:
                    # WRAP IN FORM TO ENABLE ENTER KEY
                    with st.form(key=f"form_{c_id}", clear_on_submit=False):
                        guess = st.text_input("Guess", key=f"input_{c_id}")
                        submitted = st.form_submit_button("Submit")
                        
                        if submitted:
                            clean_guess = guess.strip().lower()
                            clean_answer = correct_name.lower()
                            
                            if clean_guess == clean_answer:
                                # --- SCORING LOGIC ---
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
