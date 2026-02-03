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
# Mode 1 Specifics
if 'm1_answers' not in st.session_state:
    st.session_state.m1_answers = []
if 'current_category' not in st.session_state:
    st.session_state.current_category = None
# Mode 2 Specifics
if 'm2_index' not in st.session_state:
    st.session_state.m2_index = 0
if 'm2_attempts' not in st.session_state:
    st.session_state.m2_attempts = 0

# --- HELPER FUNCTIONS ---

def save_mode1_session(sheet, category_id, score, answers):
    """Saves the results of a Category Mode session."""
    session_sheet = sheet.worksheet("Mode1_Sessions")
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    session_id = f"SESS-{int(time.time())}"
    
    # Prepare row data
    row_data = [session_id, category_id, st.session_state.user_email, timestamp, score] + answers
    
    # Ensure row is not too short if there are fewer answers than columns
    while len(row_data) < 20: 
        row_data.append("")
        
    session_sheet.append_row(row_data)

def save_mode2_guess(sheet, char_id, attempts, solved, guess):
    """Saves a single guess attempt in Character Mode."""
    session_sheet = sheet.worksheet("Mode2_Sessions")
    guess_id = f"GUESS-{int(time.time())}-{char_id}"
    row_data = [guess_id, char_id, st.session_state.user_email, attempts, str(solved).upper(), guess]
    session_sheet.append_row(row_data)

# --- NAVIGATION SIDEBAR ---

def render_sidebar():
    """Renders the side navigation menu if the user is logged in."""
    if st.session_state.user_email:
        with st.sidebar:
            st.header(f"👤 Player")
            st.write(st.session_state.user_email)
            st.metric("Total Score", st.session_state.score)
            
            st.divider()
            st.subheader("Navigation")
            
            if st.button("🏠 Main Menu", use_container_width=True):
                st.session_state.page = 'menu'
                st.rerun()

            if st.button("📂 Mode 1: Categories", use_container_width=True):
                st.session_state.page = 'mode1_select'
                st.session_state.m1_answers = [] # Reset answers so they don't carry over
                st.rerun()

            if st.button("🕵️ Mode 2: Characters", use_container_width=True):
                st.session_state.page = 'mode2_play'
                st.session_state.m2_index = 0 # Reset to start of list
                st.rerun()
                
            st.divider()
            
            if st.button("Log Out", type="primary", use_container_width=True):
                # Reset everything
                for key in list(st.session_state.keys()):
                    del st.session_state[key]
                st.rerun()

# --- PAGE LOGIC ---

def home_page():
    st.title("📖 Bible Character Quiz")
    st.markdown("### Welcome!")
    st.write("Please enter your email to start tracking your score.")
    
    email = st.text_input("Email Address", placeholder="you@example.com")
    
    if st.button("Start Game", type="primary"):
        if email:
            st.session_state.user_email = email
            st.session_state.page = 'menu'
            st.rerun()
        else:
            st.error("Please enter an email address.")

def menu_page():
    st.title("Game Menu")
    st.write("Select a game mode from the sidebar or the options below.")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.info("### Mode 1: Category")
        st.write("Name a specific number of items that match a category (e.g., 'The 12 Apostles').")
        if st.button("Play Category Mode"):
            st.session_state.page = 'mode1_select'
            st.rerun()

    with col2:
        st.info("### Mode 2: Characters")
        st.write("Guess the Bible character based on 3 progressively easier clues.")
        if st.button("Play Character Mode"):
            st.session_state.page = 'mode2_play'
            st.session_state.m2_index = 0
            st.rerun()

def mode1_select(sheet):
    st.title("📂 Mode 1: Select Category")
    
    try:
        cat_sheet = sheet.worksheet("1-Category")
        categories = cat_sheet.get_all_records()
    except Exception as e:
        st.error(f"Error loading categories: {e}")
        return
    
    # Create display names for the dropdown
    cat_options = [f"{c['CategoryName']} (Needs {c['TotalRequired']})" for c in categories]
    selected_option = st.selectbox("Choose a category:", cat_options)
    
    if st.button("Start Category", type="primary"):
        # Find the selected category dictionary
        index = cat_options.index(selected_option)
        st.session_state.current_category = categories[index]
        st.session_state.m1_answers = [] # Clear previous answers
        st.session_state.page = 'mode1_play'
        st.rerun()

def mode1_play(sheet):
    cat = st.session_state.current_category
    if not cat:
        st.error("No category selected.")
        st.session_state.page = 'mode1_select'
        st.rerun()
        return

    st.title(f"Topic: {cat['CategoryName']}")
    st.write(f"Please list **{cat['TotalRequired']}** answers one by one.")
    
    # -- 1. Calculate Progress --
    progress_val = len(st.session_state.m1_answers) / cat['TotalRequired']
    st.progress(progress_val)
    st.write(f"Found: **{len(st.session_state.m1_answers)} / {cat['TotalRequired']}**")
    
    # -- 2. Display Correct Answers Found --
    if st.session_state.m1_answers:
        st.success(f"✅ Correct so far: {', '.join(st.session_state.m1_answers)}")

    # -- 3. Input Form --
    if len(st.session_state.m1_answers) < cat['TotalRequired']:
        with st.form("ans_form", clear_on_submit=True):
            user_input = st.text_input("Enter an answer:")
            submitted = st.form_submit_button("Submit")
            
            if submitted and user_input:
                # --- NEW ROBUST CHECKING LOGIC ---
                ans_sheet = sheet.worksheet("1-CategoryAnswer")
                all_answers = ans_sheet.get_all_records()
                
                # Clean the IDs to ensure string matching
                target_cat_id = str(cat['CategoryID']).strip()
                
                # Filter and Clean correct answers from sheet (Strip whitespace!)
                valid_answers = []
                for r in all_answers:
                    if str(r['CategoryID']).strip() == target_cat_id:
                        valid_answers.append(str(r['CorrectAnswer']).strip().lower())
                
                # Clean user input
                clean_input = user_input.strip().lower()
                
                # Check logic
                if clean_input in valid_answers:
                    # Check for duplicates in current session
                    current_found = [x.lower() for x in st.session_state.m1_answers]
                    if clean_input in current_found:
                        st.warning(f"⚠️ You already entered '{user_input}'!")
                    else:
                        # Add the original formatted text (title case) for display
                        st.session_state.m1_answers.append(user_input.strip().title())
                        st.rerun()
                else:
                    st.error(f"❌ '{user_input}' is not in the acceptable list.")
    else:
        # -- 4. Win State --
        st.balloons()
        st.success("🎉 Category Complete!")
        if st.button("Finish & Save Score"):
            save_mode1_session(sheet, cat['CategoryID'], len(st.session_state.m1_answers), st.session_state.m1_answers)
            st.session_state.score += len(st.session_state.m1_answers)
            st.session_state.page = 'menu'
            st.rerun()

def mode2_play(sheet):
    st.title("🕵️ Guess the Character")
    
    try:
        char_sheet = sheet.worksheet("2-Characters")
        characters = char_sheet.get_all_records()
    except Exception as e:
        st.error(f"Error loading characters: {e}")
        return
    
    # Check if we reached the end of the list
    if st.session_state.m2_index >= len(characters):
        st.success("🎉 You have finished all available characters!")
        if st.button("Back to Menu"):
            st.session_state.page = 'menu'
            st.rerun()
        return

    current_char = characters[st.session_state.m2_index]
    
    # Display Clues based on attempts
    st.markdown("### Clues")
    clues = [current_char['Clue1'], current_char['Clue2'], current_char['Clue3']]
    
    for i in range(st.session_state.m2_attempts + 1):
        if i < 3:
            # Use different colors/icons for subsequent clues
            icon = "ONE" if i == 0 else "TWO" if i == 1 else "THREE"
            st.info(f"**Clue {i+1}:** {clues[i]}")
            
    # Guess Form
    with st.form("guess_form", clear_on_submit=True):
        guess = st.text_input("Who is this?")
        submitted = st.form_submit_button("Submit Guess")
        
        if submitted and guess:
            # Clean inputs
            clean_guess = guess.strip().lower()
            clean_answer = str(current_char['CharacterName']).strip().lower()
            
            if clean_guess == clean_answer:
                st.success(f"✅ Correct! It was {current_char['CharacterName']}.")
                st.session_state.score += 1
                save_mode2_guess(sheet, current_char['CharacterID_Old'], st.session_state.m2_attempts, True, guess)
                
                # Advance to next character
                st.session_state.m2_index += 1
                st.session_state.m2_attempts = 0
                time.sleep(1.5) # Pause so user can see the success message
                st.rerun()
            else:
                st.error("❌ Wrong answer.")
                save_mode2_guess(sheet, current_char['CharacterID_Old'], st.session_state.m2_attempts, False, guess)
                
                if st.session_state.m2_attempts < 2:
                    st.session_state.m2_attempts += 1
                    st.rerun()
                else:
                    st.error(f"💀 Out of clues! The correct answer was **{current_char['CharacterName']}**.")
                    if st.form_submit_button("Next Character"):
                         st.session_state.m2_index += 1
                         st.session_state.m2_attempts = 0
                         st.rerun()

# --- MAIN APP ORCHESTRATOR ---

def main():
    # 1. Connect to DB
    try:
        sheet = connect_to_sheet()
    except Exception as e:
        st.error("❌ Could not connect to Google Sheets.")
        st.warning("Please check your 'credentials.json' (local) or Streamlit Secrets (cloud).")
        st.code(str(e))
        return

    # 2. Render Sidebar (if logged in)
    render_sidebar()

    # 3. Route to correct page
    if st.session_state.page == 'home':
        home_page()
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
