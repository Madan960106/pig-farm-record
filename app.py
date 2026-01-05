import streamlit as st
import pandas as pd
import json
from datetime import datetime
import google.generativeai as genai
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from streamlit_mic_recorder import speech_to_text

# ==========================================
# 1. 雲端版連線設定
# ==========================================
try:
    # --- A. 設定 Gemini API Key ---
    api_key = None
    if "GEMINI_API_KEY" in st.secrets:
        api_key = st.secrets["GEMINI_API_KEY"]
    elif "gcp_service_account" in st.secrets and "GEMINI_API_KEY" in st.secrets["gcp_service_account"]:
        api_key = st.secrets["gcp_service_account"]["GEMINI_API_KEY"]
    
    if api_key:
        genai.configure(api_key=api_key)
        # 使用最穩定的模型
        model = genai.GenerativeModel('gemini-flash-latest') 
    else:
        st.error("❌ 找不到 GEMINI_API_KEY")
        st.stop()

    # --- B. 連接 Google Sheet ---
    if "gcp_service_account" in st.secrets:
        creds_dict = st.secrets["gcp_service_account"]
    else:
        creds_dict = dict(st.secrets)

    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    
    # 讀取 Sheet 設定，若無則使用預設
    if "SHEET_CONFIG" in st.secrets and "sheet_url" in st.secrets["SHEET_CONFIG"]:
         SHEET_URL = st.secrets["SHEET_CONFIG"]["sheet_url"]
    else:
         SHEET_URL = "https://docs.google.com/spreadsheets/d/1u_8UrS_D3F6T_fhmIHPeNfaBCzKusTafTzwZGUNsEmQ/edit"
    
    sheet = client.open_by_url(SHEET_URL).sheet1

except Exception as e:
    st.error(f"連線設定錯誤：{e}")
    st.stop()

# ==========================================
# 2. 核心 Prompt
# ==========================================
PROMPT_BATCH = """
你是一個養豬場語音助理。使用者會一次口述多隻豬的事件。
請將語音內容拆解，並輸出為一個 JSON Array (陣列)。

規則：
1. **F欄 (Notes_F)**：
   - 分娩：將「活仔數」、「死胎」等數量資訊放入此欄。
   - 斷奶/離乳：將「離乳數量」放入此欄。
   - 健康狀況：如「發燒」、「跛腳」放入此欄。
   - **E欄 (Value_E)** 對於上述事件請留空。

2. **E欄 (Value_E)**：
   - 配種：將「公豬品種」或「精液號碼」放入此欄。
   - 醫療/打針：將「藥物名稱」或「劑量」放入此欄。

3. **邏輯判斷**：
   - 若一句話包含多個耳號(如"101和102都斷奶")，請拆成兩個獨立的 Object。
   - 若未提及日期，預設為今日。

輸出範例：
[
  {"Date": "2024-01-01", "EarTag": "001", "Event": "分娩", "Value_E": "", "Notes_F": "活仔10頭", "NextStage_G": "2024-01-29"},
  {"Date": "2024-01-01", "EarTag": "002", "Event": "配種", "Value_E": "杜洛克", "Notes_F": "", "NextStage_G": "2024-04-25"}
]
直接輸出 JSON，不要 Markdown 標記。
"""

# ==========================================
# 3. 介面設計 (UI)
# ==========================================
st.set_page_config(page_title="養豬場語音紀錄 V57", page_icon="🐖")
st.title("🐖 養豬場語音紀錄系統 (V57 正式版)")
st.info("模式：點擊錄音 ➡ AI 解析 ➡ 批量上傳")

# 側邊欄
with st.sidebar:
    st.header("⚙️ 現場設定")
    operator = st.selectbox("操作人員", ["場長", "阿榮", "小李", "外勞A", "外勞B"])
    zone = st.selectbox("區域", ["A棟-懷孕舍", "B棟-分娩舍", "C棟-保育舍", "D棟-肉豬舍", "隔離舍"])

# --- 錄音與輸入區 ---
st.write("### 🎤 語音輸入")

# 1. 錄音按鈕
text_from_voice = speech_to_text(
    language='zh-TW', 
    start_prompt="🔴 點我開始錄音", 
    stop_prompt="Sz 停止並辨識", 
    just_once=True,
    key='STT'
)

# 2. 文字編輯框 Session State 管理
if 'user_input_content' not in st.session_state:
    st.session_state['user_input_content'] = ""

if text_from_voice:
    st.session_state['user_input_content'] = text_from_voice

user_text = st.text_area(
    "識別結果 (可手動修改)：", 
    value=st.session_state['user_input_content'], 
    height=100,
    placeholder="錄音結果會出現在這裡，也可以直接打字..."
)

# 同步手動輸入
if user_text != st.session_state['user_input_content']:
    st.session_state['user_input_content'] = user_text


if st.button("🤖 AI 解析", type="primary"):
    if not user_text:
        st.warning("請先錄音或輸入內容")
    else:
        with st.spinner("AI 正在拆解資料中..."):
            try:
                # 呼叫 Gemini
                full_prompt = [PROMPT_BATCH, f"今天是 {datetime.now().strftime('%Y-%m-%d')}。內容：{user_text}"]
                response = model.generate_content(full_prompt)
                cleaned_text = response.text.replace("```json", "").replace("```", "").strip()
                data_list = json.loads(cleaned_text)
                
                # 處理資料
                df = pd.DataFrame(data_list)
                df['Operator_I'] = operator
                df['Zone_H'] = zone
                
                expected_cols = ['Date', 'EarTag', 'Event', 'Value_E', 'Notes_F', 'NextStage_G', 'Zone_H', 'Operator_I']
                for c in expected_cols:
                    if c not in df.columns:
                        df[c] = ""
                df = df[expected_cols]
                
                st.session_state['batch_data'] = df
                st.success(f"成功辨識 {len(df)} 筆資料！")
                
            except Exception as e:
                st.error(f"解析失敗：{e}")

# ==========================================
# 4. 確認與上傳區
# ==========================================
if 'batch_data' in st.session_state:
    st.subheader("📋 資料預覽與修正")
    edited_df = st.data_editor(st.session_state['batch_data'], num_rows="dynamic", use_container_width=True)
    
    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("🗑️ 放棄重來"):
            if 'batch_data' in st.session_state: del st.session_state['batch_data']
            if 'user_input_content' in st.session_state: del st.session_state['user_input_content']
            st.rerun()
            
    with col2:
        if st.button("✅ 確認上傳", type="primary"):
            with st.spinner("寫入 Google Sheet..."):
                try:
                    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    rows_to_upload = []
                    for index, row in edited_df.iterrows():
                        # --- 這裡就是剛剛出錯的地方，請確保這行是完整的 ---
                        one_row = [current_time, str(row['Date']), str(row['EarTag']), str(row['Event']), str(row['Value_E']), str(row['Notes_F']), str(row['NextStage_G']), str(row['Zone_H']), str(row['Operator_I'])]
                        rows_to_upload.append(one_row)
                    
                    sheet.append_rows(rows_to_upload)
                    st.toast(f"🎉 成功上傳 {len(rows_to_upload)} 筆！")
                    del st.session_state['batch_data']
                    del st.session_state['user_input_content']
                    st.rerun() # 上傳後自動刷新
                    
                except Exception as e:
                    st.error(f"上傳錯誤：{e}")
