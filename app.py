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
st.title("🐖 養豬場語音紀錄系統 (V37-診斷版)")

# CSS
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

# Session State
if 'audio_bytes' not in st.session_state:
    st.session_state.audio_bytes = None
if 'analyzed_data' not in st.session_state:
    st.session_state.analyzed_data = None

# --- 2. 連線與診斷 ---
# 診斷 1: 檢查 API Key 是否存在
if "GENAI_API_KEY" in st.secrets:
    try:
        genai.configure(api_key=st.secrets["GENAI_API_KEY"])
    except Exception as e:
        st.error(f"❌ API Key 設定格式錯誤: {e}")
else:
    st.error("❌ 嚴重錯誤：找不到 'GENAI_API_KEY'！請檢查 Secrets 設定。")

def get_gspread_client():
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        client = gspread.service_account_from_dict(creds_dict)
        return client
    except Exception as e:
        st.error(f"❌ 無法連接 Google Sheets: {e}")
        return None

# --- 3. Gemini AI 分析 (加強診斷) ---
def analyze_audio_gemini(audio_bytes):
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
    請只回傳 JSON 字串。
    """
    
    try:
        with st.spinner("🤖 AI 正在分析數據..."):
            # 診斷 2: 顯示正在呼叫 AI
            st.info("正在傳送音訊給 Gemini...")
            
            response = model.generate_content([
                prompt,
                {"mime_type": "audio/wav", "data": audio_bytes}
            ])
            
            # 診斷 3: 印出 AI 回傳的原始文字，確認它有沒有說話
            raw_text = response.text
            st.text(f"🔍 AI 回傳原始內容: {raw_text}")
            
            text = raw_text.replace("```json", "").replace("```", "").strip()
            return json.loads(text)
    except Exception as e:
        # 診斷 4: 如果失敗，印出詳細錯誤
        st.error(f"❌ 分析失敗，原因: {e}")
        return None

# --- 4. 存檔功能 ---
def save_to_sheet(data_row):
    client = get_gspread_client()
    if not client: return False
    try:
        sheet_name = st.secrets["SHEET_CONFIG"]["sheet_name"]
        sheet = client.open(sheet_name).sheet1
        
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

# --- 5. UI 介面 ---
tab1, tab2 = st.tabs(["🎙️ 現場錄音", "📊 數據看板"])

with tab1:
    st.info("請點擊下方按鈕開始錄音：")
    
    audio = mic_recorder(
        start_prompt="🎤 點我錄音",
        stop_prompt="⏹️ 完成請點這",
        just_once=True,
        key='recorder'
    )

    if audio:
        st.session_state.audio_bytes = audio['bytes']

    if st.session_state.audio_bytes:
        st.audio(st.session_state.audio_bytes, format='audio/wav')
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("⚡ 開始 AI 分析", type="primary"):
                result = analyze_audio_gemini(st.session_state.audio_bytes)
                if result:
                    st.session_state.analyzed_data = result
                    st.success("解析成功！請往下確認數據")
                else:
                    st.error("AI 分析回傳了空的結果，請查看上方的錯誤訊息。")
        with col2:
             if st.button("🗑️ 清除重錄"):
                st.session_state.audio_bytes = None
                st.session_state.analyzed_data = None
                st.rerun()

    if st.session_state.analyzed_data:
        st.divider()
        st.write("### 📝 請確認解析結果：")
        with st.form("confirm_form"):
            d = st.session_state.analyzed_data
            c1, c2 = st.columns(2)
            new_date = c1.text_input("日期", d.get("date"))
            new_id = c2.text_input("母豬耳號", d.get("sow_id"))
            c3, c4 = st.columns(2)
            new_event = c3.selectbox("事件", ["配種", "分娩", "斷奶", "醫療"], index=["配種", "分娩", "斷奶", "醫療"].index(d.get("event_type")) if d.get("event_type") in ["配種", "分娩", "斷奶", "醫療"] else 0)
            new_val = c4.text_input("數值/品種", d.get("target_value"))
            new_note = st.text_input("備註", d.get("note"))
            
            if st.form_submit_button("✅ 確認上傳"):
                final_data = {"date": new_date, "sow_id": new_id, "event_type": new_event, "target_value": new_val, "note": new_note}
                if save_to_sheet(final_data):
                    st.success("已儲存！")
                    st.session_state.analyzed_data = None
                    st.session_state.audio_bytes = None
                    time.sleep(1)
                    st.rerun()

with tab2:
    st.header("📊 繁殖紀錄總表")
    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("🔄 刷新數據"):
            st.rerun()
    
    client = get_gspread_client()
    if client:
        try:
            sheet_name = st.secrets["SHEET_CONFIG"]["sheet_name"]
            sheet = client.open(sheet_name).sheet1
            data = sheet.get_all_records()
            
            if len(data) > 0:
                df = pd.DataFrame(data)
                st.dataframe(df, use_container_width=True)
            else:
                st.info("目前試算表中還沒有資料，請先去錄音！")
                
        except Exception as e:
            st.error(f"讀取失敗: {e}")





