import streamlit as st
import pandas as pd
import json
from datetime import datetime
import google.generativeai as genai
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ==========================================
# 1. 雲端版連線設定 & API Key 讀取
# ==========================================
try:
    # --- A. 設定 Gemini API Key (根據您的清單，我們選用 gemini-2.0-flash) ---
    api_key = None
    # 1. 先找最外層
    if "GEMINI_API_KEY" in st.secrets:
        api_key = st.secrets["GEMINI_API_KEY"]
    # 2. 再找 gcp_service_account 裡面
    elif "gcp_service_account" in st.secrets and "GEMINI_API_KEY" in st.secrets["gcp_service_account"]:
        api_key = st.secrets["gcp_service_account"]["GEMINI_API_KEY"]
    
    if api_key:
        genai.configure(api_key=api_key)
        # ★★★ 這裡改成您清單中確認存在的型號 ★★★
        model = genai.GenerativeModel('gemini-flash-latest') 
    else:
        st.error("❌ 找不到 GEMINI_API_KEY，請檢查 Secrets 設定 (建議放在第一行)")
        st.stop()

    # --- B. 連接 Google Sheet ---
    # 自動抓取 secrets 裡的設定
    if "gcp_service_account" in st.secrets:
        creds_dict = st.secrets["gcp_service_account"]
    else:
        # 相容舊寫法
        creds_dict = dict(st.secrets)

    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    
    # 嘗試讀取試算表網址 (如果 secrets 有設定就用 secrets 的，沒有就用寫死的)
    if "SHEET_CONFIG" in st.secrets and "sheet_url" in st.secrets["SHEET_CONFIG"]:
         SHEET_URL = st.secrets["SHEET_CONFIG"]["sheet_url"]
    else:
         # 預設網址 (請確認這是不是您 V56 在用的那個)
         SHEET_URL = "https://docs.google.com/spreadsheets/d/1u_8UrS_D3F6T_fhmIHPeNfaBCzKusTafTzwZGUNsEmQ/edit"
    
    sheet = client.open_by_url(SHEET_URL).sheet1

except Exception as e:
    st.error(f"連線設定錯誤：{e}")
    st.stop()


# ==========================================
# 2. 核心 Prompt (V57 批量版)
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
st.set_page_config(page_title="V57 批量實驗室", page_icon="🧪")
st.title("🧪 V57 批量輸入 (Gemini 2.0 Flash)")
st.info("模式：一次唸多隻 ➡ 表格確認 ➡ 批量上傳")

# 側邊欄
with st.sidebar:
    st.header("⚙️ 現場設定")
    operator = st.selectbox("操作人員", ["場長", "阿榮", "小李", "外勞A", "外勞B"])
    zone = st.selectbox("區域", ["A棟-懷孕舍", "B棟-分娩舍", "C棟-保育舍", "D棟-肉豬舍", "隔離舍"])

# 輸入區
user_text = st.text_area("請輸入語音內容 (模擬)", height=100, 
                        placeholder="例如：測試01號打針安默西林，測試02號分娩10頭，測試03和04號都斷奶")

if st.button("🤖 AI 解析", type="primary"):
    if not user_text:
        st.warning("請先輸入內容")
    else:
        with st.spinner("AI 正在拆解資料中 (使用 Gemini 2.0)..."):
            try:
                # 呼叫 Gemini
                full_prompt = [PROMPT_BATCH, f"今天是 {datetime.now().strftime('%Y-%m-%d')}。內容：{user_text}"]
                response = model.generate_content(full_prompt)
                
                # 清洗回傳格式 (去除 ```json 等標記)
                cleaned_text = response.text.replace("```json", "").replace("```", "").strip()
                
                # 解析 JSON Array
                data_list = json.loads(cleaned_text)
                
                # 轉換為 DataFrame
                df = pd.DataFrame(data_list)
                
                # 補上 UI 欄位
                df['Operator_I'] = operator
                df['Zone_H'] = zone
                
                # 確保欄位存在並排序
                expected_cols = ['Date', 'EarTag', 'Event', 'Value_E', 'Notes_F', 'NextStage_G', 'Zone_H', 'Operator_I']
                for c in expected_cols:
                    if c not in df.columns:
                        df[c] = ""
                df = df[expected_cols]
                
                # 存入 Session State
                st.session_state['batch_data'] = df
                st.success(f"成功辨識 {len(df)} 筆資料！")
                
            except Exception as e:
                st.error(f"解析失敗：{e}")
                st.write("原始回傳：", response.text if 'response' in locals() else "無回應")

# ==========================================
# 4. 確認與上傳區
# ==========================================
if 'batch_data' in st.session_state:
    st.subheader("📋 資料預覽與修正")
    
    # 可編輯表格
    edited_df = st.data_editor(
        st.session_state['batch_data'], 
        num_rows="dynamic", 
        use_container_width=True,
        key="data_editor"
    )
    
    col1, col2 = st.columns([1, 1])
    
    with col1:
        if st.button("🗑️ 放棄重來"):
            del st.session_state['batch_data']
            st.rerun()
            
    with col2:
        if st.button("✅ 確認上傳 (Batch Upload)", type="primary"):
            with st.spinner("正在寫入 Google Sheet..."):
                try:
                    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
                            str(row['Operator_I'])
                        ]
                        rows_to_upload.append(one_row)
                    
                    sheet.append_rows(rows_to_upload)
                    st.toast(f"🎉 已成功上傳 {len(rows_to_upload)} 筆資料！")
                    del st.session_state['batch_data'] # 清空暫存
                    
                except Exception as e:
                    st.error(f"上傳錯誤：{e}")
