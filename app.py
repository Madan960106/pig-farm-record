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
st.title("🐖 養豬場語音紀錄系統 (V52 分娩資訊歸位版)")

# === 側邊欄：雙重身分設定 ===
st.sidebar.header("🏭 現場設定")
work_zone = st.sidebar.selectbox("1️⃣ 選擇區域：", ["A棟-懷孕舍", "B棟-分娩舍", "C棟-保育舍", "D棟-肉豬舍", "隔離舍"])
operator_name = st.sidebar.selectbox("2️⃣ 操作人員：", ["場長", "員工A", "員工B", "員工C", "外勞A", "外勞B"])
st.sidebar.success(f"📍 {work_zone} / 👤 {operator_name}")

# --- 2. 初始化 Session State ---
if 'audio_bytes' not in st.session_state:
    st.session_state.audio_bytes = None
if 'analyzed_data' not in st.session_state:
    st.session_state.analyzed_data = None
# 用來記住上一隻豬的耳號
if 'last_sow_id' not in st.session_state:
    st.session_state.last_sow_id = ""

# --- 3. Google Sheets 連線設定 ---
def get_gspread_client():
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        client = gspread.service_account_from_dict(creds_dict)
        return client
    except Exception as e:
        st.error(f"⚠️ 無法連接 Google Sheets: {e}")
        return None

# --- 4. Gemini AI 分析 (V52: 修改 Prompt 邏輯) ---
def analyze_audio_smart(audio_bytes):
    api_key = st.secrets["GENAI_API_KEY"]
    model_name = "gemini-2.5-flash"
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    
    b64_audio = base64.b64encode(audio_bytes).decode('utf-8')
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    
    # V52 Prompt: 特別指定分娩的資訊寫入規則
    prompt_text = f"""
    你是一個專業的養豬場管理員。請將錄音內容轉換為 JSON。
    參考日期: {today_str}。
    
    【⚠️ 最高指導原則：單一事件制】
    如果錄音中同時包含兩個事件，請優先選擇「繁殖週期事件」(分娩 > 配種 > 斷奶)，將次要事件寫入備註。
    
    【欄位填寫規則 - 請嚴格遵守】
    1. 遇到 "分娩" (生了、下豬) 事件：
       - target_value (數值/對象): 請留空 ""。
       - note (備註): 請將「出生數量」(如12頭) 以及「健康狀況」(如弱仔、健康) 全部寫在這裡。
    
    2. 遇到 "配種" 事件：
       - target_value: 填入公豬品種 (如: 杜洛克)。
       - note: 其他備註。

    3. 遇到 "斷奶"、"醫療"、"測重"：
       - target_value: 填入數量、藥名或重量。
       - note: 其他備註。

    【JSON 結構】
    1. sow_id: 耳號。
    2. event_type: ["配種", "分娩", "斷奶", "醫療", "測重"]。
    3. target_value: 對應上述規則。
    4. date: YYYY-MM-DD。
    5. note: 對應上述規則。

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
        with st.spinner(f"🤖 AI 正在判讀..."):
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

# --- 5. 存檔功能 ---
def save_to_sheet(data_row, zone, person):
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
        
        event = data_row.get("event_type")
        input_date_str = data_row.get("date")
        next_action_date = ""
        
        try:
            event_date = datetime.datetime.strptime(input_date_str, "%Y-%m-%d")
            if event == "配種":
                target_date = event_date + datetime.timedelta(days=114)
                next_action_date = f"預產:{target_date.strftime('%Y-%m-%d')}"
            elif event == "分娩":
                target_date = event_date + datetime.timedelta(days=28)
                next_action_date = f"離乳:{target_date.strftime('%Y-%m-%d')}"
            elif event == "斷奶":
                target_date = event_date + datetime.timedelta(days=5)
                next_action_date = f"發情:{target_date.strftime('%Y-%m-%d')}"
            elif event == "醫療":
                next_action_date = "⚠️查藥籤"
        except: pass

        utc_now = datetime.datetime.utcnow()
        taiwan_time = utc_now + datetime.timedelta(hours=8)
        timestamp_str = taiwan_time.strftime("%Y-%m-%d %H:%M:%S")

        row = [
            timestamp_str,
            data_row.get("date"),
            data_row.get("sow_id"),
            data_row.get("event_type"),
            data_row.get("target_value"),
            data_row.get("note"),
            next_action_date,
            zone,
            person
        ]
        
        sheet.append_row(row)
        return True
    except Exception as e:
        st.error(f"❌ 寫入失敗: {e}")
        return False

# --- 6. UI 介面 ---
tab1, tab2 = st.tabs(["🎙️ 現場錄音", "📊 數據看板"])

with tab1:
    st.info(f"📍 目前設定： **{work_zone}** 由 **{operator_name}** 操作")
    
    audio = mic_recorder(start_prompt="🎤 點我錄音", stop_prompt="⏹️ 完成請點這", just_once=True, key='recorder_v52')

    if audio:
        st.session_state.audio_bytes = audio['bytes']
        st.session_state.analyzed_data = None

    if st.session_state.audio_bytes and st.session_state.analyzed_data is None:
        st.audio(st.session_state.audio_bytes, format='audio/wav')
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("⚡ 開始 AI 分析 (V52)", type="primary"):
                result = analyze_audio_smart(st.session_state.audio_bytes)
                if result:
                    st.session_state.analyzed_data = result
        with col2:
             if st.button("🗑️ 清除重錄"):
                st.session_state.audio_bytes = None
                st.session_state.analyzed_data = None
                st.rerun()

    # --- 資料確認區 ---
    show_form = False
    default_data = {}

    if st.session_state.analyzed_data:
        d = st.session_state.analyzed_data
        if isinstance(d, list): d = d[0]
        default_data = d
        show_form = True
    elif st.session_state.last_sow_id:
        default_data = {
            "date": datetime.date.today().strftime("%Y-%m-%d"),
            "sow_id": st.session_state.last_sow_id,
            "event_type": "醫療",
            "target_value": "",
            "note": ""
        }
        st.info(f"➕ 已保留耳號 **{st.session_state.last_sow_id}**，請輸入第二筆資料：")
        show_form = True

    if show_form:
        with st.form("confirm_form"):
            c1, c2 = st.columns(2)
            new_date = c1.text_input("日期", default_data.get("date", ""))
            new_id = c2.text_input("母豬耳號", default_data.get("sow_id", ""))

            c3, c4 = st.columns(2)
            event_options = ["配種", "分娩", "斷奶", "醫療", "測重"]
            curr_event = default_data.get("event_type")
            default_idx = event_options.index(curr_event) if curr_event in event_options else 0
            
            new_event = c3.selectbox("事件", event_options, index=default_idx)
            new_val = c4.text_input("數值/內容", default_data.get("target_value", ""))
            new_note = st.text_input("備註", default_data.get("note", ""))
            
            st.caption(f"即將寫入：{work_zone} / {operator_name}")

            col_save1, col_save2 = st.columns(2)
            submitted = False
            keep_id = False
            
            with col_save1:
                if st.form_submit_button("✅ 確認上傳 (結束)"):
                    submitted = True
                    keep_id = False
            
            with col_save2:
                if st.form_submit_button("🔄 上傳並保留耳號 (連續輸入)"):
                    submitted = True
                    keep_id = True

            if submitted:
                final_data = {"date": new_date, "sow_id": new_id, "event_type": new_event, "target_value": new_val, "note": new_note}
                
                if save_to_sheet(final_data, work_zone, operator_name):
                    st.toast(f"資料已儲存！({new_event})")
                    
                    if keep_id:
                        st.session_state.last_sow_id = new_id
                        st.session_state.audio_bytes = None
                        st.session_state.analyzed_data = None
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.session_state.last_sow_id = ""
                        st.session_state.audio_bytes = None
                        st.session_state.analyzed_data = None
                        time.sleep(1)
                        st.rerun()

with tab2:
    if st.button("🔄 刷新"): st.rerun()
    st.write("數據看板將顯示於此")







