import streamlit as st
from streamlit_mic_recorder import mic_recorder
import google.generativeai as genai
import gspread
import json
import pandas as pd
import datetime
import time

# --- 1. 頁面基礎設定 ---
st.set_page_config(page_title="母豬繁殖紀錄", page_icon="🐖", layout="wide")
st.title("🐖 養豬場語音紀錄系統 (V38 強力膠版)")

# CSS 優化
st.markdown("""
    <style>
    div.stButton > button:first-child {
        height: 3.5em;
        font-size: 20px;
        font-weight: bold;
        width: 100%;
        border-radius: 10px;
    }
    </style>
""", unsafe_allow_html=True)

# --- 2. 初始化 Session State (這是關鍵！) ---
# 用來記住錄音檔，防止按按鈕時消失
if 'audio_bytes' not in st.session_state:
    st.session_state.audio_bytes = None
if 'analyzed_data' not in st.session_state:
    st.session_state.analyzed_data = None

# --- 3. 連線設定 ---
try:
    genai.configure(api_key=st.secrets["GENAI_API_KEY"])
except Exception as e:
    st.error(f"⚠️ API Key 設定錯誤: {e}")

def get_gspread_client():
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        client = gspread.service_account_from_dict(creds_dict)
        return client
    except Exception as e:
        st.error(f"⚠️ 無法連接 Google Sheets: {e}")
        return None

# --- 4. Gemini AI 分析 ---
def analyze_audio_gemini(audio_data):
    model = genai.GenerativeModel('gemini-1.5-flash')
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    
    prompt = f"""
    你是一個專業的養豬場管理員。請聽錄音，將內容轉換為 JSON。
    參考日期: {today_str} (若說"今天"以此為準，"昨天"則減一天)。
    規則：
    1. sow_id (母豬耳號): 字串。
    2. event_type (事件): 配種/分娩/斷奶/醫療。
    3. target_value (數值/對象): 品種或數量。
    4. date (日期): YYYY-MM-DD。
    5. note (備註): 細節。
    範例: "168號今天配種杜洛克" -> {{"sow_id":"168", "event_type":"配種", "target_value":"杜洛克", "date":"{today_str}", "note":""}}
    請只回傳 JSON 字串，不要有 Markdown 格式。
    """
    
    try:
        with st.spinner("🤖 AI 正在分析數據... (請稍候)"):
            response = model.generate_content([
                prompt,
                {"mime_type": "audio/wav", "data": audio_data}
            ])
            
            # 診斷訊息 (顯示在畫面上讓您確認 AI 有活著)
            st.info(f"🔍 AI 原始回覆: {response.text}")

            text = response.text
            if "```json" in text:
                text = text.replace("```json", "").replace("```", "")
            text = text.strip()
            return json.loads(text)
    except Exception as e:
        st.error(f"❌ 分析失敗: {e}")
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
            st.warning(f"💡 請確認已將機器人 Email 加入共用: {st.secrets['gcp_service_account']['client_email']}")
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
    st.warning("測試步驟：1.按錄音 -> 2.講話 -> 3.按停止 -> 4.等待「開始分析」按鈕出現 -> 5.點擊分析")
    
    # 錄音元件
    audio = mic_recorder(
        start_prompt="🎤 點我錄音",
        stop_prompt="⏹️ 完成請點這",
        just_once=True,
        key='recorder_v38'
    )

    # ⚠️ 關鍵修改：一旦錄音完成，馬上存入 Session State，死都不放手
    if audio:
        st.session_state.audio_bytes = audio['bytes']

    # 檢查 Session State 裡有沒有聲音檔
    if st.session_state.audio_bytes:
        st.audio(st.session_state.audio_bytes, format='audio/wav')
        
        col1, col2 = st.columns(2)
        with col1:
            # 這裡按鈕按下去，因為 audio_bytes 已經在 session_state 裡，所以不會消失
            if st.button("⚡ 開始 AI 分析 (V38)", type="primary"):
                result = analyze_audio_gemini(st.session_state.audio_bytes)
                if result:
                    st.session_state.analyzed_data = result
        
        with col2:
             if st.button("🗑️ 清除重錄"):
                st.session_state.audio_bytes = None
                st.session_state.analyzed_data = None
                st.rerun()

    # 顯示分析結果
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
                    # 上傳成功後清除資料
                    st.session_state.audio_bytes = None
                    st.session_state.analyzed_data = None
                    time.sleep(2)
                    st.rerun()

with tab2:
    if st.button("🔄 刷新"): st.rerun()
    st.write("數據看板區")









