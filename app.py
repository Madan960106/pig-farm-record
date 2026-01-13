import streamlit as st
import pandas as pd
import json
import requests
from datetime import datetime, timedelta
import pytz
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from streamlit_mic_recorder import speech_to_text

# ==========================================
# 1. 雲端版連線設定
# ==========================================
try:
    # --- A. 取得 API Key ---
    api_key = None
    if "GEMINI_API_KEY" in st.secrets:
        api_key = st.secrets["GEMINI_API_KEY"]
    
    if not api_key:
        st.error("❌ 找不到 GEMINI_API_KEY，請檢查 Secrets 設定。")
        st.stop()

    # --- B. 連接 Google Sheet ---
    if "gcp_service_account" in st.secrets:
        creds_dict = dict(st.secrets["gcp_service_account"])
    else:
        st.error("❌ 找不到 gcp_service_account 設定，請檢查 Secrets。")
        st.stop()

    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    
    # --- C. 開啟試算表 ---
    sheet_url = None
    if "SHEET_CONFIG" in st.secrets and "sheet_url" in st.secrets["SHEET_CONFIG"]:
         sheet_url = st.secrets["SHEET_CONFIG"]["sheet_url"]
    
    if sheet_url:
        sheet = client.open_by_url(sheet_url).worksheet("工作表1")
    else:
        sheet_name = "2026母豬紀錄表" 
        if "SHEET_CONFIG" in st.secrets and "sheet_name" in st.secrets["SHEET_CONFIG"]:
             sheet_name = st.secrets["SHEET_CONFIG"]["sheet_name"]
        sheet = client.open(sheet_name).worksheet("工作表1")

except Exception as e:
    st.error(f"連線設定錯誤：{e}")
    st.stop()

# ==========================================
# 2. AI 核心函式 (V64: 不死心掃描版)
# ==========================================
def call_gemini_api_auto(prompt_text):
    """
    自動掃描多種模型，遇到 429 也不放棄，直到試完所有可能性。
    """
    # 候選名單：調整順序，將最穩定的標準版放前面
    models_to_try = [
        "gemini-1.5-flash",     # 首選：最快、額度最寬鬆
        "gemini-1.5-pro",       # 次選：能力最強
        "gemini-2.0-flash-exp", # 備選：最新實驗版 (容易塞車)
        "gemini-pro"            # 最後手段：舊版
    ]
    
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{
            "parts": [{"text": prompt_text}]
        }]
    }

    last_error_msg = ""
    success_model = None

    for model_name in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        try:
            # 嘗試連線
            response = requests.post(url, headers=headers, json=payload)
            
            # 情況 1: 成功 (200)
            if response.status_code == 200:
                result = response.json()
                success_model = model_name
                # 找到能用的就立刻回傳，不繼續試了
                st.toast(f"✅ 連線成功！使用模型：{model_name}")
                return result['candidates'][0]['content']['parts'][0]['text']
            
            # 情況 2: 失敗 (429 或 404) -> 紀錄錯誤，但「continue」繼續試下一個！
            else:
                error_detail = response.text
                if response.status_code == 429:
                    error_short = "額度滿/忙碌中"
                elif response.status_code == 404:
                    error_short = "模型未找到"
                else:
                    error_short = f"錯誤 {response.status_code}"
                
                last_error_msg += f"\n🔸 {model_name}: {error_short}"
                continue # 關鍵修正：絕對不 Break，繼續試下一個模型！
                
        except Exception as e:
            last_error_msg += f"\n⚠️ {model_name}: 連線異常 ({e})"
            continue

    # 如果程式跑到這裡，代表所有模型都試過了還是全掛
    st.error(f"❌ 所有 AI 模型都嘗試失敗。\n詳細紀錄：{last_error_msg}\n\n請確認 Secrets 中的 API Key 是否正確設定為新專案的 Key。")
    return None

# ==========================================
# 3. Prompt 設定
# ==========================================
PROMPT_BATCH = """
你是一個養豬場語音助理。請將語音內容拆解為 JSON Array。

規則：
1. **事件名稱標準化 (D欄)**：
   - 關鍵字「超音波」、「測孕」➡ Event 為 **「超音波」**。
   - 關鍵字「斷奶」、「離乳」➡ Event 為 **「離乳」**。
   - 關鍵字「打針」、「注射」、「治療」、「用藥」➡ Event 為 **「醫療」**。
   - 其他標準事件：分娩、配種、死亡。

2. **公豬與精液號碼校正 (重要！)**：
   - 養豬場常用公豬品系為：**L (Landrace), D (Duroc), Y (Yorkshire)**。
   - 語音識別常將英文誤判為中文，請依下列規則**強制修正**配種公豬號碼：
     - **D (杜洛克)**：若聽到「第」、「弟」、「低」、「地」 + 數字 ➡ 改為 **D** + 數字 (例：「第12」➡「D12」)。
     - **Y (約克夏)**：若聽到「歪」、「外」、「Why」 + 數字 ➡ 改為 **Y** + 數字 (例：「歪50」➡「Y50」)。
     - **L (藍瑞斯)**：若聽到「艾爾」、「埃」、「A」 + 數字 ➡ 改為 **L** + 數字。
   - 此修正主要應用於 **配種** 事件的 **Value_E** 欄位。

3. **數值欄位定義 (E欄 Value_E)**：
   - **配種**：在此欄填入「修正後的公豬號碼」(如 D12, L33, Y9)。
   - **其他事件**：留空。

4. **備註欄位 (F欄 Notes_F)**：
   - **醫療**：填入「藥物名稱」或「劑量」。
   - **超音波**：若 J欄為 "No"，必須加上 "❌ 未懷孕，待發情重配"。
   - **分娩**：只記錄口述數據 (如"活仔12")，**嚴禁**自行補腦死胎數據。
   - **離乳**：放入「離乳數量」。

5. **判讀結果 (J欄 PregnancyResult_J)**：
   - 僅當事件為「超音波」時填寫 (Yes/No)。

6. **邏輯判斷**：
   - 支援多耳號拆解。
   - 未提日期預設今日。

輸出範例：
[
  {"Date": "2024-01-01", "EarTag": "101", "Event": "配種", "Value_E": "D12", "Notes_F": "", "NextStage_G": "", "PregnancyResult_J": ""},
  {"Date": "2024-01-01", "EarTag": "102", "Event": "配種", "Value_E": "Y50", "Notes_F": "", "NextStage_G": "", "PregnancyResult_J": ""}
]
直接輸出 JSON。
"""

# ==========================================
# 4. 介面設計 (UI)
# ==========================================
st.set_page_config(page_title="養豬場語音紀錄 V64", page_icon="🐖")
st.title("🐖 養豬場語音紀錄 (V64 不死心版)")
st.info("模式：點擊錄音 ➡ AI 自動掃描所有模型 ➡ 批量上傳")

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
    placeholder="例：測試101號配種第12 (AI會自動改成D12)..."
)

if user_text != st.session_state['user_input_content']:
    st.session_state['user_input_content'] = user_text


if st.button("🤖 AI 解析", type="primary"):
    if not user_text:
        st.warning("請先錄音或輸入內容")
    else:
        with st.spinner("AI 正在掃描可用模型 (遇錯自動跳過)..."):
            try:
                taipei_tz = pytz.timezone('Asia/Taipei')
                today_date = datetime.now(taipei_tz).strftime('%Y-%m-%d')
                
                full_prompt = f"{PROMPT_BATCH}\n今天是 {today_date}。內容：{user_text}"
                
                # 呼叫 V64 自動掃描函式
                ai_response_text = call_gemini_api_auto(full_prompt)
                
                if ai_response_text:
                    cleaned_text = ai_response_text.replace("```json", "").replace("```", "").strip()
                    data_list = json.loads(cleaned_text)
                    
                    df = pd.DataFrame(data_list)
                    
                    # --- 自動計算邏輯 ---
                    if 'NextStage_G' not in df.columns: df['NextStage_G'] = ""
                    if 'PregnancyResult_J' not in df.columns: df['PregnancyResult_J'] = ""

                    for index, row in df.iterrows():
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
                    
                    # 欄位順序確保
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
# 5. 確認與上傳區
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
                            str(row['PregnancyResult_J'])
                        ]
                        rows_to_upload.append(one_row)
                    
                    sheet.append_rows(rows_to_upload)
                    st.toast(f"🎉 成功上傳 {len(rows_to_upload)} 筆！")
                    del st.session_state['batch_data']
                    del st.session_state['user_input_content']
                    st.rerun() 
                    
                except Exception as e:
                    st.error(f"上傳錯誤：{e}")
