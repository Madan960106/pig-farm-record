import streamlit as st
import pandas as pd
import json
from datetime import datetime, timedelta
import pytz
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
    
    if "SHEET_CONFIG" in st.secrets and "sheet_url" in st.secrets["SHEET_CONFIG"]:
         SHEET_URL = st.secrets["SHEET_CONFIG"]["sheet_url"]
    else:
         SHEET_URL = "https://docs.google.com/spreadsheets/d/1u_8UrS_D3F6T_fhmIHPeNfaBCzKusTafTzwZGUNsEmQ/edit"
    
    sheet = client.open_by_url(SHEET_URL).sheet1

except Exception as e:
    st.error(f"連線設定錯誤：{e}")
    st.stop()

# ==========================================
# 2. 核心 Prompt (新增 J 欄與測孕邏輯)
# ==========================================
PROMPT_BATCH = """
你是一個養豬場語音助理。請將語音內容拆解為 JSON Array。

規則：
1. **事件名稱標準化**：
   - 包含：「分娩」、「離乳」(不可用斷奶)、「配種」、「醫療」、「死亡」、「測孕」。

2. **J欄 (PregnancyResult_J) 測孕專用**：
   - 僅當事件為「測孕」或「超音波」時填寫。
   - 若結果為懷孕/有過/有成 -> 填入 "Yes"。
   - 若結果為沒過/沒懷孕/空胎/陰性 -> 填入 "No"。
   - 其他事件此欄留空。

3. **F欄 (Notes_F) 備註規則**：
   - **測孕**：若 J欄為 "No"，**必須**在此欄加上 "❌ 未懷孕，待發情重配"。
   - **分娩**：放入「活仔數」、「死胎」。若未口述數量，此欄必須留空。
   - **離乳**：放入「離乳數量」。若未口述數量，此欄必須留空。
   - **死亡**：放入「死亡原因」或數量。

4. **E欄 (Value_E) 數值規則**：
   - **配種**：放入「公豬品種」或「精液號碼」。
   - **醫療**：放入「藥物名稱」或「劑量」。
   - 其他留空。

5. **邏輯判斷**：
   - 支援多耳號拆解。
   - 未提日期預設今日。
   - NextStage_G 全部留空字串。

輸出範例：
[
  {"Date": "2024-01-01", "EarTag": "001", "Event": "測孕", "Value_E": "", "Notes_F": "", "NextStage_G": "", "PregnancyResult_J": "Yes"},
  {"Date": "2024-01-01", "EarTag": "002", "Event": "測孕", "Value_E": "", "Notes_F": "❌ 未懷孕，待發情重配", "NextStage_G": "", "PregnancyResult_J": "No"}
]
直接輸出 JSON。
"""

# ==========================================
# 3. 介面設計 (UI)
# ==========================================
st.set_page_config(page_title="養豬場語音紀錄 V57+", page_icon="🐖")
st.title("🐖 養豬場語音紀錄 (V57+ 測孕版)")
st.info("模式：點擊錄音 ➡ AI 解析 (含測孕判讀) ➡ 批量上傳")

# 側邊欄
with st.sidebar:
    st.header("⚙️ 現場設定")
    operator = st.selectbox("操作人員", ["場長", "阿榮", "小李", "外勞A", "外勞B"])
    zone = st.selectbox("區域", ["A棟-懷孕舍", "B棟-分娩舍", "C棟-保育舍", "D棟-肉豬舍", "隔離舍"])

# --- 錄音與輸入區 ---
st.write("### 🎤 語音輸入")

text_from_voice = speech_to_text(
    language='zh-TW', 
    start_prompt="🔴 點我開始錄音", 
    stop_prompt="Sz 停止並辨識", 
    just_once=True,
    key='STT'
)

if 'user_input_content' not in st.session_state:
    st.session_state['user_input_content'] = ""

if text_from_voice:
    st.session_state['user_input_content'] = text_from_voice

user_text = st.text_area(
    "識別結果：", 
    value=st.session_state['user_input_content'], 
    height=100,
    placeholder="例：101號測孕有過，102號沒懷孕，103號配種杜洛克..."
)

if user_text != st.session_state['user_input_content']:
    st.session_state['user_input_content'] = user_text


if st.button("🤖 AI 解析", type="primary"):
    if not user_text:
        st.warning("請先錄音或輸入內容")
    else:
        with st.spinner("AI 正在解析並計算日期..."):
            try:
                # 設定台北時區
                taipei_tz = pytz.timezone('Asia/Taipei')
                today_date = datetime.now(taipei_tz).strftime('%Y-%m-%d')
                
                full_prompt = [PROMPT_BATCH, f"今天是 {today_date}。內容：{user_text}"]
                response = model.generate_content(full_prompt)
                cleaned_text = response.text.replace("```json", "").replace("```", "").strip()
                data_list = json.loads(cleaned_text)
                
                df = pd.DataFrame(data_list)
                
                # --- 自動計算邏輯 ---
                if 'NextStage_G' not in df.columns: df['NextStage_G'] = ""
                if 'PregnancyResult_J' not in df.columns: df['PregnancyResult_J'] = ""

                for index, row in df.iterrows():
                    # 配種計算
                    if row['Event'] == '配種':
                        try:
                            event_date = datetime.strptime(row['Date'], "%Y-%m-%d")
                            check_date = event_date + timedelta(days=25)
                            due_date = event_date + timedelta(days=114)
                            df.at[index, 'NextStage_G'] = f"測孕:{check_date.strftime('%m/%d')} 預產:{due_date.strftime('%m/%d')}"
                        except: pass
                
                # 補上 UI 欄位
                df['Operator_I'] = operator
                df['Zone_H'] = zone
                
                # 確保欄位順序 (加入 J 欄)
                expected_cols = ['Date', 'EarTag', 'Event', 'Value_E', 'Notes_F', 'NextStage_G', 'Zone_H', 'Operator_I', 'PregnancyResult_J']
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
    st.subheader("📋 資料預覽")
    edited_df = st.data_editor(st.session_state['batch_data'], num_rows="dynamic", use_container_width=True)
    
    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("🗑️ 放棄"):
            if 'batch_data' in st.session_state: del st.session_state['batch_data']
            if 'user_input_content' in st.session_state: del st.session_state['user_input_content']
            st.rerun()
            
    with col2:
        if st.button("✅ 確認上傳", type="primary"):
            with st.spinner("上傳中..."):
                try:
                    taipei_tz = pytz.timezone('Asia/Taipei')
                    current_time = datetime.now(taipei_tz).strftime("%Y-%m-%d %H:%M:%S")
                    
                    rows_to_upload = []
                    for index, row in edited_df.iterrows():
                        one_row = [
                            current_time, 
                            str(row['Date']), 
                            str(row['EarTag']), 
                            str(row['Event']), 
                            str(row['Value_E']), 
                            str(row['Notes_F']), 
                            str(row['NextStage_G']), 
                            str(row['Zone_H']), 
                            str(row['Operator_I']),
                            str(row['PregnancyResult_J']) # 新增 J 欄上傳
                        ]
                        rows_to_upload.append(one_row)
                    
                    sheet.append_rows(rows_to_upload)
                    st.toast(f"🎉 成功上傳 {len(rows_to_upload)} 筆！")
                    del st.session_state['batch_data']
                    del st.session_state['user_input_content']
                    st.rerun() 
                    
                except Exception as e:
                    st.error(f"上傳錯誤：{e}")
