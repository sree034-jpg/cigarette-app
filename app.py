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
    gray = cv2.cvtColor(original_img, cv2.COLOR_BGR2GRAY)
    
    all_found_codes = set()
    all_found_dates = []
    
    custom_config = r'--oem 3 --psm 6'
    
    # --- MULTI-PASS STRATEGY ---
    passes = [
        ("Original", gray), 
        ("Threshold", cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)[1]), 
        ("Light Dilation", cv2.dilate(cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)[1], cv2.getStructuringElement(cv2.MORPH_RECT, (1,1)), iterations=1)),
        ("Heavy Dilation", cv2.dilate(cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)[1], cv2.getStructuringElement(cv2.MORPH_RECT, (2,2)), iterations=1))
    ]
    
    for pass_name, processed_img in passes:
        text = pytesseract.image_to_string(processed_img, config=custom_config)
        
        # Find Codes
        code_pattern = re.compile(r'\b[A-Z0-9]{3}\s[A-Z0-9]{3}\s[A-Z0-9]{3}\s[A-Z0-9]{3}\b')
        all_found_codes.update(code_pattern.findall(text))
        
        # Find Dates (Looks for "MFD ON" or just standard date formats)
        # 1. Look for explicit "MFD ON"
        mfd_pattern = re.compile(r'MFD\.?\s*ON[:\s]*(\d{2}[./\-\s]\d{2}[./\-\s]\d{2,4})', re.IGNORECASE)
        matches = mfd_pattern.findall(text)
        
        # 2. If no MFD tag, look for loose dates like 18/11/25
        if not matches:
             loose_date_pattern = re.compile(r'\b\d{2}[/]\d{2}[/]\d{2,4}\b')
             matches = loose_date_pattern.findall(text)
             
        all_found_dates.extend(matches)

    # --- FINALIZE RESULTS ---
    final_codes = sorted(list(all_found_codes))
    
    detected_date = ""
    if all_found_dates:
        # Normalize: replace dots/spaces with slashes
        clean_dates = [d.replace('.', '/').replace('-', '/').replace(' ', '/') for d in all_found_dates]
        # Pick the most common one
        detected_date = Counter(clean_dates).most_common(1)[0][0]

    return final_codes, detected_date

# --- THE APP INTERFACE ---
st.title("🚬 Cigarette Scan & Log")

with st.expander("👤 Supervisor Details", expanded=True):
    col1, col2 = st.columns(2)
    sup_name = col1.text_input("Supervisor Name", "NAME OF THE SUPERVISOR")
    sup_code = col2.text_input("Supervisor Code", "SUP-001")

with st.expander("🚚 Issued To (FWP Details)", expanded=True):
    col3, col4 = st.columns(2)
    fwp_name = col3.text_input("Issued to FWP Name")
    fwp_code = col4.text_input("FWP Code")

with st.expander("📦 Product Details", expanded=True):
    available_variants = get_variant_list()
    variant_name = st.selectbox("Variant Name", available_variants)
    sku_code = st.selectbox("SKU / Pack Size", ["10's", "20's", "5's"])

st.write("---")
st.subheader("📷 Scan Packs")
uploaded_file = st.file_uploader("Take a photo", type=['jpg', 'png', 'jpeg'])

if uploaded_file is not None:
    st.image(uploaded_file, caption='Uploaded Image', use_column_width=True)
    
    with st.spinner('Scanning...'):
        codes, detected_mfg_date = process_image(uploaded_file)
    
    if codes:
        st.success(f"✅ Found {len(codes)} unique codes")
        
        # --- DATE HANDLING LOGIC ---
        # 1. Determine what to put in the box (Detected Date OR Empty)
        default_date_val = detected_mfg_date if detected_mfg_date else ""
        
        # 2. Show the input box
        # If auto-detected, it is pre-filled. If not, it is empty. User can edit both.
        final_date = st.text_input("📅 Manufacturing Date", value=default_date_val, placeholder="DD/MM/YY (e.g. 18/11/25)")
        
        # 3. Helper Message
        if detected_mfg_date:
            st.caption("✨ Auto-detected from pack. You can edit above if incorrect.")
        else:
            st.warning("⚠️ Date not detected. Please type it manually above.")

        # --- CODE VERIFICATION ---
        codes_text = "\n".join(codes)
        edited_codes = st.text_area("Verify Scanned Codes", codes_text, height=150)
        
        # --- SAVE BUTTON ---
        if st.button("Save Data to Sheet"):
            # Validation: We only block saving if the box is COMPLETELY empty
            if not final_date.strip():
                st.error("❌ Please enter a Manufacturing Date before saving.")
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
                    st.success(f"Saved {len(final_code_list)} entries! (Date: {final_date})")
                    
                except Exception as e:
                    st.error(f"Error connecting to Google Sheets: {e}")
    else:
        st.error("❌ No codes found. Please try again.")
