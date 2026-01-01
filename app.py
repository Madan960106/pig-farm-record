import streamlit as st
from streamlit_mic_recorder import mic_recorder
import gspread
import json
import datetime
import time
import requests
import base64

# --- 1. 頁面基礎設定 ---
st.set_page_config(page_title="母豬繁殖紀錄", page_icon="🐖", layout="wide")
st.title("🐖 養豬場語音紀錄系統 (V48 週期計算版)")

# --- 2. 初始化 Session State ---
if 'audio_bytes' not in st.session_state:
    st.session_state.audio_bytes = None
if 'analyzed_data' not in st.session_state:
    st.session_state.analyzed_data = None

# --- 3. Google Sheets 連線設定 ---
def get_gspread_client():
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        client = gspread.service_account_from_dict(creds_dict)
        return client
    except Exception as e:
        st.error(f"⚠️ 無法連接 Google Sheets: {e}")
        return None

# --- 4. Gemini AI 分析 (V48: 加強事件關鍵字判讀) ---
def analyze_audio_smart(audio_bytes):
    api_key = st.secrets["GENAI_API_KEY"]
    model_name = "gemini-2.5-flash"
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    
    b64_audio = base64.b64encode(audio_bytes).decode('utf-8')
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    
    # 優化 Prompt: 特別強調「離乳/斷奶」的區分，以及其他事件的邏輯
    prompt_text = f"""
    你是一個專業的養豬場管理員。請將錄音內容轉換為 JSON。
    參考日期: {today_str}。
    
    【關鍵字對照表 - 請嚴格遵守】
    - 聽到 "離乳"、"斷奶"、"抓小豬" -> event_type 必須是 "斷奶"。 (絕對不可以是配種!)
    - 聽到 "生了"、"分娩"、"下豬" -> event_type 必須是 "分娩"。
    - 聽到 "配種"、"授精"、"做愛" -> event_type 必須是 "配種"。
    - 聽到 "打針"、"治療"、"疫苗" -> event_type 必須是 "醫療"。
    
    【JSON 欄位規則】
    1. sow_id (字串): 耳號。
    2. event_type (字串): 只能是 ["配種", "分娩", "斷奶", "醫療", "測重"] 其中之一。
    3. target_value (字串): 
       - 配種 -> 填公豬品種 (如: 杜洛克)。
       - 分娩 -> 填活仔數/死胎數 (如: 12活1死)。
       - 斷奶 -> 填離乳頭數 (如: 10頭)。
       - 醫療 -> 填藥名或原因 (如: 肺炎)。
    4. date (YYYY-MM-DD)。
    5. note (字串): 備註。

    【範例】
    "101號今天離乳" -> {{"sow_id":"101", "event_type":"斷奶", "target_value":"", "date":"{today_str}", "note":""}}
    "205號分娩12隻" -> {{"sow_id":"205", "event_type":"分娩", "target_value":"12隻", "date":"{today_str}", "note":""}}

    請只回傳 JSON 字串。
    """

    payload = {
        "contents": [{
            "parts": [
                {"text": prompt_text},
                {"inline_data": {"mime_type": "audio/wav", "data": b64_audio}}
            ]
        }]
    }
    headers = {'Content-Type': 'application/json'}

    try:
        with st.spinner(f"🤖 AI 正在判讀事件類型..."):
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            if response.status_code == 200:
                result_json = response.json()
                text_content = result_json['candidates'][0]['content']['parts'][0]['text']
                clean_text = text_content.replace("```json", "").replace("```", "").strip()
                return json.loads(clean_text)
            else:
                st.error(f"❌ 請求失敗: {response.status_code}")
                return None
    except Exception as e:
        st.error(f"❌ 連線異常: {e}")
        return None

# --- 5. 存檔功能 (V48: 植入養豬週期邏輯) ---
def save_to_sheet(data_row):
    client = get_gspread_client()
    if not client: return False
    try:
        sheet_config = st.secrets["SHEET_CONFIG"]
        sheet_name = sheet_config["sheet_name"]
        try:
            sheet = client.open(sheet_name).sheet1
        except Exception as e:
            st.error(f"❌ 找不到試算表: {e}")
            return False
        
        # === V48 核心：養豬週期計算邏輯 ===
        event = data_row.get("event_type")
        input_date_str = data_row.get("date")
        next_action_date = "" # G欄內容
        
        try:
            event_date = datetime.datetime.strptime(input_date_str, "%Y-%m-%d")
            
            if event == "配種":
                # 配種 -> 預產期 (+114天)
                target_date = event_date + datetime.timedelta(days=114)
                next_action_date = f"預產:{target_date.strftime('%Y-%m-%d')}"
                
            elif event == "分娩":
                # 分娩 -> 預定離乳日 (+28天)
                target_date = event_date + datetime.timedelta(days=28)
                next_action_date = f"離乳:{target_date.strftime('%Y-%m-%d')}"
                
            elif event == "斷奶":
                # 斷奶 -> 預定發情日 (+5天)
                target_date = event_date + datetime.timedelta(days=5)
                next_action_date = f"發情:{target_date.strftime('%Y-%m-%d')}"
                
            elif event == "醫療":
                next_action_date = "⚠️注意停藥期"
                
        except Exception as e:
            print(f"日期計算錯誤: {e}")

        # 台灣時間
        utc_now = datetime.datetime.utcnow()
        taiwan_time = utc_now + datetime.timedelta(hours=8)
        timestamp_str = taiwan_time.strftime("%Y-%m-%d %H:%M:%S")

        row = [
            timestamp_str,                 # A: 系統時間
            data_row.get("date"),          # B: 日期
            data_row.get("sow_id"),        # C: 耳號
            data_row.get("event_type"),    # D: 事件
            data_row.get("target_value"),  # E: 數值/對象
            data_row.get("note"),          # F: 備註
            next_action_date               # G: 預定下階段/提示日 (V48更新)
        ]
        
        sheet.append_row(row)
        return True
    except Exception as e:
        st.error(f"❌ 寫入失敗: {e}")
        return False

# --- 6. UI 介面 ---
tab1, tab2 = st.tabs(["🎙️ 現場錄音", "📊 數據看板"])

with tab1:
    st.info("💡 提示：請記得將 Google Sheet G欄標題改為「預定下階段/提示日」。")
    audio = mic_recorder(start_prompt="🎤 點我錄音", stop_prompt="⏹️ 完成請點這", just_once=True, key='recorder_v48')

    if audio:
        st.session_state.audio_bytes = audio['bytes']

    if st.session_state.audio_bytes:
        st.audio(st.session_state.audio_bytes, format='audio/wav')
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("⚡ 開始 AI 分析 (V48)", type="primary"):
                result = analyze_audio_smart(st.session_state.audio_bytes)
                if result:
                    st.session_state.analyzed_data = result
        with col2:
             if st.button("🗑️ 清除重錄"):
                st.session_state.audio_bytes = None
                st.session_state.analyzed_data = None
                st.rerun()

    if st.session_state.analyzed_data:
        st.divider()
        st.success("✅ 解析成功！請確認欄位是否正確：")
        with st.form("confirm_form"):
            d = st.session_state.analyzed_data
            
            col_a, col_b = st.columns(2)
            new_date = col_a.text_input("📅 日期", d.get("date"))
            new_id = col_b.text_input("🐷 母豬耳號", d.get("sow_id"))
            
            col_c, col_d = st.columns(2)
            # V48: 確保下拉選單包含所有正確選項
            new_event = col_c.selectbox("📋 事件", ["配種", "分娩", "斷奶", "醫療", "測重"], index=["配種", "分娩", "斷奶", "醫療", "測重"].index(d.get("event_type")) if d.get("event_type") in ["配種", "分娩", "斷奶", "醫療", "測重"] else 0)
            
            new_val = col_d.text_input("🔢 數值/品種/藥名", d.get("target_value"))
            new_note = st.text_input("📝 備註", d.get("note"))
            
            if st.form_submit_button("✅ 確認上傳"):
                final_data = {"date": new_date, "sow_id": new_id, "event_type": new_event, "target_value": new_val, "note": new_note}
                if save_to_sheet(final_data):
                    st.success("🎉 資料已寫入！週期計算已自動完成。")
                    st.session_state.audio_bytes = None
                    st.session_state.analyzed_data = None
                    time.sleep(2)
                    st.rerun()

with tab2:
    if st.button("🔄 刷新"): st.rerun()
    st.write("數據看板將顯示於此")

