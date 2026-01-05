import streamlit as st
import pandas as pd
import json
from datetime import datetime
import google.generativeai as genai
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ==========================================
# 1. 雲端版連線設定 (自動抓取您原本的設定)
# ==========================================
# ⚠️ 請注意：這裡假設您在 Streamlit Cloud 後台已經設定好 secrets
# 如果這段報錯，請直接去複製您 app.py 裡面「連接 Google Sheet」的那幾行程式碼來取代這裡

try:
    # 嘗試從 secrets 讀取 (這是最標準的雲端寫法)
    # 這裡的 "gcp_service_account" 可能需要改成您 secrets 裡的名稱，例如 "connections" 或 "gsheets"
    # 如果您不確定，請參考您 app.py 是怎麼寫的
    
    # --- 這裡我們先用一個通用的方式，若失敗請改用 app.py 的寫法 ---
    if "gcp_service_account" in st.secrets:
        creds_dict = st.secrets["gcp_service_account"]
    elif "connections" in st.secrets and "gsheets" in st.secrets["connections"]:
        creds_dict = st.secrets["connections"]["gsheets"]
    else:
        # 如果真的找不到，就試試看直接讀取 (有些舊版寫法是直接把 key 放在根目錄)
        creds_dict = dict(st.secrets)

    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    
    # ★ 請確認這裡的網址是正確的 V56 表格網址 ★
    SHEET_URL = "https://docs.google.com/spreadsheets/d/1u_8UrS_D3F6T_fhmIHPeNfaBCzKusTafTzwZGUNsEmQ/edit"
    sheet = client.open_by_url(SHEET_URL).sheet1
    
    # 設定 Gemini API Key
    if "GEMINI_API_KEY" in st.secrets:
        genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
    else:
        st.error("找不到 GEMINI_API_KEY，請檢查 Secrets 設定")

except Exception as e:
    st.error(f"連線設定錯誤：{e}")
    st.warning("💡 建議：請直接打開您的 app.py，把最上面「連接 Google Sheet」和「設定 API Key」的那幾行複製過來取代這邊的代碼。")
    st.stop()


# ==========================================
# 2. 核心 Prompt (V57 批量版)
# ==========================================
PROMPT_BATCH = """
你是一個養豬場語音助理。使用者會一次口述多隻豬的事件。
請將語音內容拆解，並輸出為一個 JSON Array (陣列)。
規則：
1. 分娩/斷奶的「數量/狀況」放入 F欄(Notes_F)，E欄留空。
2. 配種/醫療的「品種/藥名」放入 E欄(Value_E)。
3. 若一句話包含多個耳號(如A和B都斷奶)，請拆成兩個 Object。
4. 日期格式：YYYY-MM-DD。

輸出範例：
[
  {"Date": "2024-01-01", "EarTag": "001", "Event": "分娩", "Value_E": "", "Notes_F": "活仔10頭", "NextStage_G": "2024-01-29"},
  {"Date": "2024-01-01", "EarTag": "002", "Event": "配種", "Value_E": "杜洛克", "Notes_F": "", "NextStage_G": "2024-04-25"}
]
直接輸出 JSON，不要 Markdown。
"""

# ==========================================
# 3. 介面設計 (UI)
# ==========================================
st.set_page_config(page_title="V57 批量實驗室", page_icon="🧪")
st.title("🧪 V57 批量輸入測試 (雲端版)")
st.info("模式：一次唸多隻 ➡ 表格確認 ➡ 批量上傳")

# 側邊欄模擬
with st.sidebar:
    operator = st.selectbox("操作人員", ["場長", "阿榮", "小李"])
    zone = st.selectbox("區域", ["A棟", "B棟", "分娩舍"])

# 文字輸入模擬
user_text = st.text_area("請輸入語音內容 (模擬)", height=100, 
                        placeholder="例如：測試01號打針安默西林，測試02號分娩10頭")

if st.button("AI 解析"):
    model = genai.GenerativeModel('gemini-1.5-flash-latest') # 確保模型名稱正確
    
    if not user_text:
        st.warning("請先輸入內容")
    else:
        with st.spinner("AI 正在拆解資料中..."):
            try:
                # 呼叫 Gemini
                response = model.generate_content([PROMPT_BATCH, f"今天是 {datetime.now().strftime('%Y-%m-%d')}。內容：{user_text}"])
                cleaned_text = response.text.replace("```json", "").replace("```", "").strip()
                
                # 解析 JSON Array
                data_list = json.loads(cleaned_text)
                
                # 將資料轉換為 Pandas DataFrame
                df = pd.DataFrame(data_list)
                
                # 補上 UI 欄位
                df['Operator_I'] = operator
                df['Zone_H'] = zone
                
                # 欄位排序
                cols = ['Date', 'EarTag', 'Event', 'Value_E', 'Notes_F', 'NextStage_G', 'Zone_H', 'Operator_I']
                # 防呆：避免 AI 沒回傳某些欄位導致報錯
                for c in cols:
                    if c not in df.columns:
                        df[c] = ""
                df = df[cols]
                
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
    
    if st.button("確認上傳 (Batch Upload)", type="primary"):
        with st.spinner("正在寫入 Google Sheet..."):
            try:
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                rows_to_upload = []
                
                for index, row in edited_df.iterrows():
                    one_row = [
                        current_time,
                        row['Date'],
                        str(row['EarTag']),
                        row['Event'],
                        row['Value_E'],
                        row['Notes_F'],
                        row['NextStage_G'],
                        row['Zone_H'],
                        row['Operator_I']
                    ]
                    rows_to_upload.append(one_row)
                
                sheet.append_rows(rows_to_upload)
                st.toast(f"✅ 已成功上傳 {len(rows_to_upload)} 筆資料！")
                del st.session_state['batch_data']
                
            except Exception as e:
                st.error(f"上傳錯誤：{e}")
