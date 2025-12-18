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
    
    # Heavy kernel to connect big dots
    kernel_heavy = cv2.getStructuringElement(cv2.MORPH_RECT, (2,2))
    dilation_heavy = cv2.dilate(thresh, kernel_heavy, iterations=1)
    
    custom_config = r'--oem 3 --psm 6'
    text_heavy = pytesseract.image_to_string(dilation_heavy, config=custom_config)
    
    # Find Codes
    code_pattern = re.compile(r'\b[A-Z0-9]{3}\s[A-Z0-9]{3}\s[A-Z0-9]{3}\s[A-Z0-9]{3}\b')
    found_codes = code_pattern.findall(text_heavy)

    # --- PASS 2: LIGHT DILATION (For Dates) ---
    # Dates are smaller, so heavy dilation blurs them. We use less or no dilation here.
    # We invert the threshold because dates sometimes read better as black text on white
    _, thresh_light = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)
    
    # Run OCR on the cleaner, lighter version
    text_light = pytesseract.image_to_string(thresh_light, config=custom_config)
    
    # Regex to find dates (Flexible: supports DD/MM/YY, DD.MM.YY, DD-MM-YY, and spaces)
    # Looks for: 2 digits + separator + 2 digits + separator + 2-4 digits
    date_pattern = re.compile(r'\b\d{2}[./\-\s]\d{2}[./\-\s]\d{2,4}\b')
    found_dates = date_pattern.findall(text_light)
    
    # Also check the heavy text just in case
    found_dates += date_pattern.findall(text_heavy)
    
    detected_date = None
    if found_dates:
        # Clean up dates (replace spaces/dots with slashes for consistency)
        clean_dates = [d.replace('.', '/').replace('-', '/').replace(' ', '/') for d in found_dates]
        # Find the most common date in the batch
        detected_date = Counter(clean_dates).most_common(1)[0][0]

    return found_codes, detected_date

# --- THE APP INTERFACE ---
st.title("🚬 Cigarette Scan & Log")

with st.expander("👤 Supervisor Details", expanded=True):
    col1, col2 = st.columns(2)
    sup_name = col1.text_input("Supervisor Name", "Name")
    sup_code = col2.text_input("Supervisor Code", "SUP-001")

with st.expander("🚚 Issued To (FWP Details)", expanded=True):
    col3, col4 = st.columns(2)
    fwp_name = col3.text_input("Issued to FWP Name")
    fwp_code = col4.text_input("FWP Code")

with st.expander("📦 Product Details", expanded=True):
    available_variants = get_variant_list()
    variant_name = st.selectbox("Variant Name", available_variants)
    sku_code = st.selectbox("SKU / Pack Size", ["10's", "20's", "5's"])
    manual_date = st.text_input("Default Mfg Date (Fallback)", "21/08/25")

st.write("---")
st.subheader("📷 Scan Packs")
uploaded_file = st.file_uploader("Take a photo", type=['jpg', 'png', 'jpeg'])

if uploaded_file is not None:
    st.image(uploaded_file, caption='Uploaded Image', use_column_width=True)
    
    with st.spinner('Scanning (Dual Pass Method)...'):
        codes, detected_mfg_date = process_image(uploaded_file)
    
    # Smart Date Logic
    final_date = manual_date
    if detected_mfg_date:
        final_date = detected_mfg_date
        st.success(f"📅 **Smart Date Detected:** {final_date}")
    else:
        st.warning(f"📅 Could not read date automatically. Using manual: **{final_date}**")

    if codes:
        st.info(f"✅ Found {len(codes)} codes")
        codes_text = "\n".join(codes)
        edited_codes = st.text_area("Verify Scanned Codes", codes_text, height=150)
        
        if st.button("Save Data to Sheet"):
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
                st.success(f"Saved {len(final_code_list)} entries!")
                
            except Exception as e:
                st.error(f"Error connecting to Google Sheets: {e}")
    else:
        st.warning("No codes detected. Please try a clearer photo.")
