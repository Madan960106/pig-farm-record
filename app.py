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
st.title("🐖 養豬場語音紀錄系統 (V37 診斷版)")

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

# 初始化 Session State
if 'analyzed_data' not in st.session_state:
    st.session_state.analyzed_data = None

# --- 2. 連線設定 ---
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

# --- 3. Gemini AI 分析 (V37: 增加除錯訊息) ---
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
    請只回傳 JSON 字串，不要有 Markdown 格式。
    """
    
    try:
        with st.spinner("🤖 AI 正在思考中..."):
            response = model.generate_content([
                prompt,
                {"mime_type": "audio/wav", "data": audio_bytes}
            ])
            
            # --- V37 診斷訊息: 顯示原始回覆 ---
            raw_text = response.text
            st.warning(f"🔍 [診斷] AI 原始回覆內容:\n{raw_text}") 
            # -------------------------------

            text = raw_text
            if "```json" in text:
                text = text.replace("```json", "").replace("```", "")
            text = text.strip()
            
            return json.loads(text)
    except Exception as e:
        st.error(f"❌ 分析發生錯誤: {e}")
        return None

# --- 4. 存檔功能 ---
def save_to_sheet(data_row):
    client = get_gspread_client()
    if not client: return False
    try:
        sheet_config = st.secrets["SHEET_CONFIG"]
        sheet_name = sheet_config["sheet_name"]
        
        # 嘗試開啟試算表
        try:
            sheet = client.open(sheet_name).sheet1
        except Exception as e:
            st.error(f"❌ 找不到試算表或無權限: {e}")
            st.info(f"💡 請確認已將 'client_email' 加入共用: {st.secrets['gcp_service_account']['client_email']}")
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
        st.error(f"❌ 寫入過程失敗: {e}")
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
        wav_audio_data = audio['bytes']
        st.audio(wav_audio_data, format='audio/wav')
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("⚡ 開始 AI 分析", type="primary"):
                st.write("🔄 正在傳送錄音給 Gemini...") # 診斷點
                result = analyze_audio_gemini(wav_audio_data)
                if result:
                    st.session_state.analyzed_data = result
                    st.success("✅ 解析成功！請確認下方資料：")
                else:
                    st.error("⚠️ AI 分析沒有回傳有效結果，請查看上方的診斷訊息。")
        with col2:
             if st.button("🗑️ 清除重錄"):
                st.session_state.analyzed_data = None
                st.rerun()

    if st.session_state.analyzed_data:
        st.divider()
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
                    st.success("🎉 已成功儲存至 Google Sheet！")
                    st.session_state.analyzed_data = None
                    time.sleep(2)
                    st.rerun()

with tab2:
    if st.button("🔄 刷新"): st.rerun()
    st.write("數據看板區")








