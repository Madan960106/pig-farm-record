import streamlit as st
from streamlit_mic_recorder import mic_recorder
import gspread
import json
import datetime
import time
import requests  # 新增：用於發送 HTTP 請求
import base64    # 新增：用於編碼音訊

# --- 1. 頁面基礎設定 ---
st.set_page_config(page_title="母豬繁殖紀錄", page_icon="🐖", layout="wide")
st.title("🐖 養豬場語音紀錄系統 (V41 直連版)")

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

# --- 4. Gemini AI 分析 (全新邏輯：REST API) ---
def analyze_audio_direct(audio_bytes):
    # 1. 準備 API 金鑰和網址
    api_key = st.secrets["GENAI_API_KEY"]
    # 直接指定使用 v1beta 的 gemini-1.5-flash 模型
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    
    # 2. 將音訊轉為 Base64 字串 (API 要求格式)
    b64_audio = base64.b64encode(audio_bytes).decode('utf-8')
    
    # 3. 準備提示詞
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    prompt_text = f"""
    你是一個專業的養豬場管理員。請聽錄音，將內容轉換為 JSON。
    參考日期: {today_str} (若說"今天"以此為準，"昨天"則減一天)。
    規則：
    1. sow_id (母豬耳號): 字串。
    2. event_type (事件): 配種/分娩/斷奶/醫療。
    3. target_value (數值/對象): 品種或數量。
    4. date (日期): YYYY-MM-DD。
    5. note (備註): 細節。
    範例: "168號今天配種杜洛克" -> {{"sow_id":"168", "event_type":"配種", "target_value":"杜洛克", "date":"{today_str}", "note":""}}
    請只回傳 JSON 字串，不要包含 ```json 等標記。
    """

    # 4. 構建 JSON Payload (這是原本 SDK 幫我們做的事，現在手動做)
    payload = {
        "contents": [{
            "parts": [
                {"text": prompt_text},
                {
                    "inline_data": {
                        "mime_type": "audio/wav",
                        "data": b64_audio
                    }
                }
            ]
        }]
    }

    headers = {'Content-Type': 'application/json'}

    try:
        with st.spinner("🤖 正在透過雲端直連分析..."):
            # 發送 POST 請求
            response = requests.post(url, headers=headers, json=payload)
            
            # 檢查 HTTP 狀態碼
            if response.status_code != 200:
                st.error(f"❌ API 請求失敗 (Code {response.status_code}): {response.text}")
                return None
            
            # 解析回應
            result_json = response.json()
            
            # 嘗試提取文字內容 (Gemini 的回應結構很深)
            try:
                text_content = result_json['candidates'][0]['content']['parts'][0]['text']
                # 清理 Markdown 標記
                clean_text = text_content.replace("```json", "").replace("```", "").strip()
                return json.loads(clean_text)
            except (KeyError, IndexError, json.JSONDecodeError) as parse_err:
                st.error(f"❌ 解析回傳資料失敗: {parse_err}")
                st.warning(f"原始回傳: {result_json}")
                return None

    except Exception as e:
        st.error(f"❌ 連線發生錯誤: {e}")
        return None

# --- 5. 存檔功能 ---
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
        
        due_date = ""
        if data_row.get("event_type") == "配種":
            try:
                m_date = datetime.datetime.strptime(data_row.get("date"), "%Y-%m-%d")
                due_date = (m_date + datetime.timedelta(days=114)).strftime("%Y-%m-%d")
            except: pass

        row = [
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            data_row.get("date"),
            data_row.get("sow_id"),
            data_row.get("event_type"),
            data_row.get("target_value"),
            data_row.get("note"),
            due_date
        ]
        sheet.append_row(row)
        return True
    except Exception as e:
        st.error(f"❌ 寫入失敗: {e}")
        return False

# --- 6. UI 介面 ---
tab1, tab2 = st.tabs(["🎙️ 現場錄音", "📊 數據看板"])

with tab1:
    st.info("請點擊下方按鈕開始錄音：")
    audio = mic_recorder(start_prompt="🎤 點我錄音", stop_prompt="⏹️ 完成請點這", just_once=True, key='recorder_v41')

    if audio:
        st.session_state.audio_bytes = audio['bytes']

    if st.session_state.audio_bytes:
        st.audio(st.session_state.audio_bytes, format='audio/wav')
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("⚡ 開始 AI 分析 (V41)", type="primary"):
                # 使用新的直連函式
                result = analyze_audio_direct(st.session_state.audio_bytes)
                if result:
                    st.session_state.analyzed_data = result
        
        with col2:
             if st.button("🗑️ 清除重錄"):
                st.session_state.audio_bytes = None
                st.session_state.analyzed_data = None
                st.rerun()

    if st.session_state.analyzed_data:
        st.divider()
        st.success("✅ 解析成功！")
        with st.form("confirm_form"):
            d = st.session_state.analyzed_data
            c1, c2 = st.columns(2)
            new_date = c1.text_input("日期", d.get("date"))
            new_id = c2.text_input("母豬耳號", d.get("sow_id"))
            c3, c4 = st.columns(2)
            new_event = c3.selectbox("事件", ["配種", "分娩", "斷奶", "醫療"], index=0)
            new_val = c4.text_input("數值/品種", d.get("target_value"))
            new_note = st.text_input("備註", d.get("note"))
            
            if st.form_submit_button("✅ 確認上傳"):
                final_data = {"date": new_date, "sow_id": new_id, "event_type": new_event, "target_value": new_val, "note": new_note}
                if save_to_sheet(final_data):
                    st.success("🎉 已成功儲存！")
                    st.session_state.audio_bytes = None
                    st.session_state.analyzed_data = None
                    time.sleep(2)
                    st.rerun()

with tab2:
    if st.button("🔄 刷新"): st.rerun()
    st.write("數據看板區")












