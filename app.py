import streamlit as st
import cv2
import numpy as np
import pytesseract
import re
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from collections import Counter

# --- CONFIGURATION ---
SPREADSHEET_ID = "1SGCuphPzqKF9v_lzByO4i-YbC0qEu5G4gHiFM9088ks"
VARIANT_SHEET_GID = 1000522256

@st.cache_resource
def get_google_sheet_client():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(st.secrets["gcp_service_account"], scope)
    client = gspread.authorize(creds)
    return client

def get_variant_list():
    try:
        client = get_google_sheet_client()
        sh = client.open_by_key(SPREADSHEET_ID)
        worksheet = next((ws for ws in sh.worksheets() if ws.id == VARIANT_SHEET_GID), None)
        if worksheet:
            variants = worksheet.col_values(1)
            return [x for x in variants if x.strip()]
        else:
            return ["Error: Tab not found", "Manual Entry"]
    except Exception as e:
        return [f"Connection Error: {e}", "Manual Entry"]

def process_image(image_file):
    # 1. Read Image
    file_bytes = np.asarray(bytearray(image_file.read()), dtype=np.uint8)
    original_img = cv2.imdecode(file_bytes, 1)
    
    # --- PASS 1: HEAVY DILATION (For Dot Codes) ---
    gray = cv2.cvtColor(original_img, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)
    kernel_heavy = cv2.getStructuringElement(cv2.MORPH_RECT, (2,2))
    dilation_heavy = cv2.dilate(thresh, kernel_heavy, iterations=1)
    
    custom_config = r'--oem 3 --psm 6'
    text_heavy = pytesseract.image_to_string(dilation_heavy, config=custom_config)
    
    # Find Codes
    code_pattern = re.compile(r'\b[A-Z0-9]{3}\s[A-Z0-9]{3}\s[A-Z0-9]{3}\s[A-Z0-9]{3}\b')
    found_codes = code_pattern.findall(text_heavy)

    # --- PASS 2: LIGHT TOUCH (For "MFD ON" Dates) ---
    # We use the original threshold (no dilation) to read small text clearly
    _, thresh_light = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)
    text_light = pytesseract.image_to_string(thresh_light, config=custom_config)
    
    # Combine texts to search in both
    full_text = text_light + "\n" + text_heavy
    
    # Regex Strategies for Date
    found_dates = []
    
    # Strategy A: Look specifically for "MFD ON" followed by a date
    # Matches: "MFD ON 21/08/25", "MFD ON: 21.08.25", etc.
    mfd_pattern = re.compile(r'MFD\s*ON[:\s]*(\d{2}[./\-\s]\d{2}[./\-\s]\d{2,4})', re.IGNORECASE)
    mfd_matches = mfd_pattern.findall(full_text)
    found_dates.extend(mfd_matches)
    
    # Strategy B: If no "MFD ON" found, look for standalone dates as backup
    if not found_dates:
        date_pattern = re.compile(r'\b\d{2}[./\-\s]\d{2}[./\-\s]\d{2,4}\b')
        found_dates = date_pattern.findall(text_light)
    
    detected_date = ""
    if found_dates:
        # Normalize date format (replace dots/spaces with slashes)
        clean_dates = [d.replace('.', '/').replace('-', '/').replace(' ', '/') for d in found_dates]
        # Pick the most common one found
        detected_date = Counter(clean_dates).most_common(1)[0][0]

    return found_codes, detected_date

# --- THE APP INTERFACE ---
st.title("🚬 Cigarette Scan & Log")

with st.expander("👤 Supervisor Details", expanded=True):
    col1, col2 = st.columns(2)
    sup_name = col1.text_input("Supervisor Name", "Supervisor's Name")
    sup_code = col2.text_input("Supervisor Code", "SUP-001")

with st.expander("🚚 Issued To (FWP Details)", expanded=True):
    col3, col4 = st.columns(2)
    fwp_name = col3.text_input("Issued to FWP Name")
    fwp_code = col4.text_input("FWP Code")

with st.expander("📦 Product Details", expanded=True):
    available_variants = get_variant_list()
    variant_name = st.selectbox("Variant Name", available_variants)
    sku_code = st.selectbox("SKU / Pack Size", ["10's", "20's", "5's"])
    # DELETED: manual_date fallback is gone.

st.write("---")
st.subheader("📷 Scan Packs")
uploaded_file = st.file_uploader("Take a photo", type=['jpg', 'png', 'jpeg'])

if uploaded_file is not None:
    st.image(uploaded_file, caption='Uploaded Image', use_column_width=True)
    
    with st.spinner('Scanning for Codes and "MFD ON"...'):
        codes, detected_mfg_date = process_image(uploaded_file)
    
    # --- RESULT SECTION ---
    if codes:
        st.info(f"✅ Found {len(codes)} codes")
        
        # 1. DATE VERIFICATION
        # We show the detected date in an input box. 
        # If it's correct, user does nothing. If wrong/empty, user types it.
        final_date = st.text_input("📅 Manufacturing Date (Verify)", value=detected_mfg_date, help="App reads this from 'MFD ON'. Edit if incorrect.")
        
        if not final_date:
            st.warning("⚠️ No 'MFD ON' date detected. Please type the date manually above before saving.")

        # 2. CODE VERIFICATION
        codes_text = "\n".join(codes)
        edited_codes = st.text_area("Verify Scanned Codes", codes_text, height=150)
        
        # 3. SAVE BUTTON
        if st.button("Save Data to Sheet"):
            if not final_date:
                st.error("❌ Cannot save: Manufacturing Date is empty.")
            else:
                try:
                    client = get_google_sheet_client()
                    sh = client.open_by_key(SPREADSHEET_ID)
                    sheet = sh.get_worksheet(0)
                    
                    final_code_list = edited_codes.split('\n')
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    
                    for code in final_code_list:
                        if code.strip():
                            row = [timestamp, sup_name, sup_code, fwp_name, fwp_code, variant_name, sku_code, final_date, code.strip(), uploaded_file.name]
                            sheet.append_row(row)
                    
                    st.balloons()
                    st.success(f"Saved {len(final_code_list)} entries with Date: {final_date}")
                    
                except Exception as e:
                    st.error(f"Error connecting to Google Sheets: {e}")
    else:
        st.warning("No codes detected. Please try a clearer photo.")
