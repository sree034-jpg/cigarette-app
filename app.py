import streamlit as st
import cv2
import numpy as np
import pytesseract
import re
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials

@st.cache_resource
def get_google_sheet_client():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(st.secrets["gcp_service_account"], scope)
    client = gspread.authorize(creds)
    return client

def get_variant_list():
    try:
        client = get_google_sheet_client()
        sheet = client.open("Cigarette_Data").worksheet("Products")
        return [x for x in sheet.col_values(1) if x.strip()]
    except:
        return ["Marlboro Red", "Manual Entry"]

def process_image(image_file):
    file_bytes = np.asarray(bytearray(image_file.read()), dtype=np.uint8)
    img = cv2.imdecode(file_bytes, 1)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2,2))
    dilation = cv2.dilate(thresh, kernel, iterations=1)
    custom_config = r'--oem 3 --psm 6'
    text = pytesseract.image_to_string(dilation, config=custom_config)
    pattern = re.compile(r'\b[A-Z0-9]{3}\s[A-Z0-9]{3}\s[A-Z0-9]{3}\s[A-Z0-9]{3}\b')
    return pattern.findall(text)

st.title("🚬 Cigarette Scan & Log")

with st.expander("👤 Operator Details", expanded=True):
    col1, col2 = st.columns(2)
    emp_name = col1.text_input("Operator Name")
    emp_code = col2.text_input("Operator Code")

with st.expander("🚚 Issued To (FWP Details)", expanded=True):
    col3, col4 = st.columns(2)
    fwp_name = col3.text_input("Issued to FWP Name")
    fwp_code = col4.text_input("FWP Code")

with st.expander("📦 Product Details", expanded=True):
    variant_name = st.selectbox("Variant Name", get_variant_list())
    sku_code = st.text_input("SKU / Item Code")
    mfg_date_str = st.date_input("Manufacturing Date").strftime("%d-%m-%Y")

st.write("---")
uploaded_file = st.file_uploader("Take a photo", type=['jpg', 'png', 'jpeg'])

if uploaded_file:
    st.image(uploaded_file, caption='Uploaded Image')
    with st.spinner('Reading Dot Codes...'):
        codes = process_image(uploaded_file)
    
    if codes:
        st.success(f"Found {len(codes)} codes!")
        edited_codes = st.text_area("Verify Scanned Codes", "\n".join(codes), height=150)
        
        if st.button("Save Data to Sheet"):
            try:
                client = get_google_sheet_client()
                sheet = client.open("Cigarette_Data").sheet1 
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                for code in edited_codes.split('\n'):
                    if code.strip():
                        sheet.append_row([timestamp, emp_name, emp_code, fwp_name, fwp_code, variant_name, sku_code, mfg_date_str, code.strip(), uploaded_file.name])
                st.balloons()
                st.success("Saved successfully!")
            except Exception as e:
                st.error(f"Error: {e}")
    else:
        st.warning("No codes detected.")
