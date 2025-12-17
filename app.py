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
# The ID is the long code in your Google Sheet URL
SPREADSHEET_ID = "1SGCuphPzqKF9v_lzByO4i-YbC0qEu5G4gHiFM9088ks"
# The GID is the specific tab ID for your "Sheet 2" (Variant names)
VARIANT_SHEET_GID = 1000522256

@st.cache_resource
def get_google_sheet_client():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(st.secrets["gcp_service_account"], scope)
    client = gspread.authorize(creds)
    return client

def get_variant_list():
    """Fetches products from the specific tab (Sheet 2)"""
    try:
        client = get_google_sheet_client()
        sh = client.open_by_key(SPREADSHEET_ID)
        # Find the specific worksheet by its GID
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
    img = cv2.imdecode(file_bytes, 1)

    # 2. Pre-processing
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2,2))
    dilation = cv2.dilate(thresh, kernel, iterations=1)

    # 3. OCR Extraction
    custom_config = r'--oem 3 --psm 6'
    text = pytesseract.image_to_string(dilation, config=custom_config)

    # 4. Find Dot Codes
    code_pattern = re.compile(r'\b[A-Z0-9]{3}\s[A-Z0-9]{3}\s[A-Z0-9]{3}\s[A-Z0-9]{3}\b')
    found_codes = code_pattern.findall(text)
    
    # 5. Find Dates (Smart Date Detect)
    date_pattern = re.compile(r'\b\d{2}/\d{2}/\d{2,4}\b')
    found_dates = date_pattern.findall(text)
    
    detected_date = None
    if found_dates:
        detected_date = Counter(found_dates).most_common(1)[0][0]

    return found_codes, detected_date

# --- THE APP INTERFACE ---
st.title("🚬 Cigarette Scan & Log")

# --- SECTION 1: SUPERVISOR DETAILS ---
with st.expander("👤 Supervisor Details", expanded=True):
    col1, col2 = st.columns(2)
    with col1:
        sup_name = st.text_input("Supervisor Name", "Supervisor Name")
    with col2:
        sup_code = st.text_input("Supervisor Code", "SUP-001")

# --- SECTION 2: FWP DETAILS ---
with st.expander("🚚 Issued To (FWP Details)", expanded=True):
    col3, col4 = st.columns(2)
    with col3:
        fwp_name = st.text_input("Issued to FWP Name")
    with col4:
        fwp_code = st.text_input("FWP Code")

# --- SECTION 3: PRODUCT DETAILS ---
with st.expander("📦 Product Details", expanded=True):
    # DYNAMIC DROPDOWN for Variant
    available_variants = get_variant_list()
    variant_name = st.selectbox("Variant Name", available_variants)
    
    # NEW DROPDOWN for SKU (10's, 20's, 5's)
    sku_code = st.selectbox("SKU / Pack Size", ["10's", "20's", "5's"])
    
    manual_date = st.text_input("Default Mfg Date (Fallback)", "21/08/25")

# --- SECTION 4: SCANNING ---
st.write("---")
st.subheader("📷 Scan Packs")
uploaded_file = st.file_uploader("Take a photo", type=['jpg', 'png', 'jpeg'])

if uploaded_file is not None:
    st.image(uploaded_file, caption='Uploaded Image', use_column_width=True)
    
    with st.spinner('Reading Codes & Dates...'):
        codes, detected_mfg_date = process_image(uploaded_file)
    
    # Date Logic
    final_date = manual_date
    if detected_mfg_date:
        final_date = detected_mfg_date
        st.info(f"📅 Smart Date: Detected **{final_date}** on the pack!")
    else:
        st.warning(f"📅 No date found on pack. Using manual date: **{final_date}**")

    if codes:
        st.success(f"Found {len(codes)} codes!")
        codes_text = "\n".join(codes)
        edited_codes = st.text_area("Verify Scanned Codes", codes_text, height=150)
        
        if st.button("Save Data to Sheet"):
            try:
                client = get_google_sheet_client()
                # Open the EXACT same spreadsheet for saving
                sh = client.open_by_key(SPREADSHEET_ID)
                # Saving to the FIRST sheet (Index 0) by default
                sheet = sh.get_worksheet(0)
                
                final_code_list = edited_codes.split('\n')
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                for code in final_code_list:
                    if code.strip():
                        row = [
                            timestamp,          # A
                            sup_name,           # B
                            sup_code,           # C
                            fwp_name,           # D
                            fwp_code,           # E
                            variant_name,       # F
                            sku_code,           # G (Now saves 10's, 20's, or 5's)
                            final_date,         # H
                            code.strip(),       # I
                            uploaded_file.name  # J
                        ]
                        sheet.append_row(row)
                
                st.balloons()
                st.success(f"Saved {len(final_code_list)} entries to Sheet 1!")
                
            except Exception as e:
                st.error(f"Error connecting to Google Sheets: {e}")
    else:
        st.warning("No codes detected. Please try again.")
