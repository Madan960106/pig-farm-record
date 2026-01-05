import streamlit as st
from streamlit_mic_recorder import mic_recorder
import gspread
import json
import datetime
import time
import requests
import base64
import pandas as pd

# --- 1. 頁面基礎設定 ---
st.set_page_config(page_title="母豬繁殖紀錄", page_icon="🐖", layout="wide")
st.title("🐖 養豬場語音紀錄系統 (V56 離乳資訊歸位版)")

# === 側邊欄：現場設定 ===
st.sidebar.header("🏭 現場設定")
work_zone = st.sidebar.selectbox("1️⃣ 選擇區域：", ["A棟-懷孕舍", "B棟-分娩舍", "C棟-保育舍", "D棟-肉豬舍", "隔離舍"])
operator_name = st.sidebar.selectbox("2️⃣ 操作人員：", ["場長", "員工A", "員工B", "員工C", "外勞A", "外勞B"])
st.sidebar.success(f"📍 {work_zone}\n\n👤 {operator_name}")

# --- 2. 初始化 Session State ---
if 'audio_bytes' not in st.session_state:
    st.session_state.audio_bytes = None
if 'analyzed_data' not in st.session_state:
    st.session_state.analyzed_data = None
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

# --- 讀取數據 (用於儀表板) ---
@st.cache_data(ttl=60)
def load_data_from_sheet():
    client = get_gspread_client()
    if not client: return pd.DataFrame()
    try:
        sheet_config = st.secrets["SHEET_CONFIG"]
        sheet = client.open(sheet_config["sheet_name"]).sheet1
        data = sheet.get_all_values()
        if len(data) > 1:
            return pd.DataFrame(data[1:], columns=data[0])
        return pd.DataFrame()
    except Exception as e:
        return pd.DataFrame()

# --- 4. Gemini AI 分析 (V56: 修改斷奶規則) ---
def analyze_audio_smart(audio_bytes):
    api_key = st.secrets["GENAI_API_KEY"]
    model_name = "gemini-2.5-flash"
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    
    b64_audio = base64.b64encode(audio_bytes).decode('utf-8')
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    
    # Prompt: 針對 分娩 和 斷奶，都將數量寫入備註 (F欄)
    prompt_text = f"""
    你是一個專業的養豬場管理員。請將錄音內容轉換為 JSON。
    參考日期: {today_str}。
    
    【⚠️ 最高指導原則：單一事件制】
    如果錄音中包含兩個事件，優先選擇「繁殖週期事件」(分娩 > 配種 > 斷奶)，次要事件寫入備註。
    
    【欄位填寫規則 - 請嚴格遵守】
    1. 遇到 "分娩" (生了):
       - target_value (E欄): 留空 ""。
       - note (F欄): 寫入「出生數量」及「健康狀況」。
       
    2. 遇到 "斷奶" (離乳、抓小豬):
       - target_value (E欄): 留空 ""。
       - note (F欄): 寫入「離乳頭數」(例如: 離乳10頭)。

    3. 遇到 "配種":
       - target_value (E欄): 填入公豬品種。
       - note (F欄): 備註。

    4. 遇到 "醫療"、"測重":
       - target_value (E欄): 填入藥名或重量。
       - note (F欄): 備註。

    【JSON 結構】
    1. sow_id: 耳號。
    2. event_type: ["配種", "分娩", "斷奶", "醫療", "測重"]。
    3. target_value: 字串。
    4. date: YYYY-MM-DD。
    5. note: 字串。

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
            return None
    except Exception as e:
        return None

# --- 5. 存檔功能 ---
def save_to_sheet(data_row, zone, person):
    client = get_gspread_client()
    if not client: return False
    try:
        sheet_config = st.secrets["SHEET_CONFIG"]
        sheet = client.open(sheet_config["sheet_name"]).sheet1
        
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
        load_data_from_sheet.clear()
        return True
    except Exception as e:
        st.error(f"❌ 寫入失敗: {e}")
        return False

# --- 6. UI 介面 ---
tab1, tab2 = st.tabs(["🎙️ 現場錄音", "📊 數據看板"])

# === Tab 1: 錄音介面 ===
with tab1:
    st.info(f"📍 目前設定： **{work_zone}** 由 **{operator_name}** 操作")
    audio = mic_recorder(start_prompt="🎤 點我錄音", stop_prompt="⏹️ 完成請點這", just_once=True, key='recorder_v56')

    if audio:
        st.session_state.audio_bytes = audio['bytes']
        st.session_state.analyzed_data = None

    if st.session_state.audio_bytes and st.session_state.analyzed_data is None:
        st.audio(st.session_state.audio_bytes, format='audio/wav')
        col1, col2 = st.columns(2)
        with col1:
            if st.button("⚡ 開始 AI 分析 (V56)", type="primary"):
                result = analyze_audio_smart(st.session_state.audio_bytes)
                if result:
                    st.session_state.analyzed_data = result
        with col2:
             if st.button("🗑️ 清除重錄"):
                st.session_state.audio_bytes = None
                st.session_state.analyzed_data = None
                st.rerun()

    # 資料確認表格
    show_form = False
    default_data = {}
    if st.session_state.analyzed_data:
        d = st.session_state.analyzed_data
        if isinstance(d, list): d = d[0]
        default_data = d
        show_form = True
    elif st.session_state.last_sow_id:
        default_data = {"date": datetime.date.today().strftime("%Y-%m-%d"), "sow_id": st.session_state.last_sow_id, "event_type": "醫療", "target_value": "", "note": ""}
        st.info(f"➕ 已保留耳號 **{st.session_state.last_sow_id}**，請輸入第二筆資料：")
        show_form = True

    if show_form:
        with st.form("confirm_form"):
            c1, c2 = st.columns(2)
            new_date = c1.text_input("日期", default_data.get("date", ""))
            new_id = c2.text_input("母豬耳號", default_data.get("sow_id", ""))
            c3, c4 = st.columns(2)
            event_opts = ["配種", "分娩", "斷奶", "醫療", "測重"]
            curr_evt = default_data.get("event_type")
            idx = event_opts.index(curr_evt) if curr_evt in event_opts else 0
            new_event = c3.selectbox("事件", event_opts, index=idx)
            new_val = c4.text_input("數值 (E欄)", default_data.get("target_value", ""))
            
            # 這裡特別標註，讓使用者知道離乳頭數會在這裡
            new_note = st.text_input("備註 (F欄 - 分娩/離乳數量在此)", default_data.get("note", ""))
            
            st.caption(f"即將寫入：{work_zone} / {operator_name}")

            s1, s2 = st.columns(2)
            sub = False
            keep = False
            if s1.form_submit_button("✅ 確認上傳"): sub = True
            if s2.form_submit_button("🔄 上傳並保留"): sub = True; keep = True

            if sub:
                final_data = {"date": new_date, "sow_id": new_id, "event_type": new_event, "target_value": new_val, "note": new_note}
                if save_to_sheet(final_data, work_zone, operator_name):
                    st.toast(f"資料已儲存！")
                    st.session_state.last_sow_id = new_id if keep else ""
                    st.session_state.audio_bytes = None
                    st.session_state.analyzed_data = None
                    time.sleep(1)
                    st.rerun()

# === Tab 2: 數據看板 ===
with tab2:
    st.markdown("### 📊 豬場即時戰情室")
    if st.button("🔄 刷新數據"):
        load_data_from_sheet.clear()
        st.rerun()
    
    df = load_data_from_sheet()
    if not df.empty:
        try:
            df.columns = ["Timestamp", "Date", "SowID", "Event", "Value", "Note", "NextAction", "Zone", "Operator"]
            df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
            
            st.markdown("#### 📅 本週關鍵指標")
            today = pd.Timestamp.now().normalize()
            start_week = today - pd.Timedelta(days=today.dayofweek)
            end_week = start_week + pd.Timedelta(days=6)
            this_week_df = df[(df['Date'] >= start_week) & (df['Date'] <= end_week)]
            
            k1, k2, k3 = st.columns(3)
            with k1: st.metric("🐷 本週分娩", f"{len(this_week_df[this_week_df['Event'].str.contains('分娩', na=False)])} 頭")
            with k2: st.metric("❤️ 本週配種", f"{len(this_week_df[this_week_df['Event'].str.contains('配種', na=False)])} 頭")
            with k3: st.metric("✂️ 本週離乳", f"{len(this_week_df[this_week_df['Event'].str.contains('斷奶', na=False)])} 頭")

            st.divider()
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("#### 🏗️ 各棟舍配種進度")
                mating_df = df[df['Event'] == "配種"]
                if not mating_df.empty: st.bar_chart(mating_df['Zone'].value_counts())
                else: st.info("無數據")
            with c2:
                st.markdown("#### 🚑 近期事件分佈")
                st.bar_chart(df['Event'].value_counts(), color="#ffaa00")

            st.divider()
            st.markdown("#### 🚨 未來 7 天預警清單")
            df['AlertDateStr'] = df['NextAction'].astype(str).str.extract(r'(\d{4}-\d{2}-\d{2})')
            df['AlertDate'] = pd.to_datetime(df['AlertDateStr'], errors='coerce')
            mask = (df['AlertDate'] >= today) & (df['AlertDate'] <= today + pd.Timedelta(days=7))
            alert_df = df[mask].copy().sort_values(by="AlertDate")
            
            if not alert_df.empty:
                display = alert_df[['AlertDateStr', 'SowID', 'NextAction', 'Zone']]
                display.columns = ["預定日期", "母豬耳號", "待辦事項", "位置"]
                st.dataframe(display, hide_index=True, use_container_width=True)
            else:
                st.success("🎉 未來 7 天無緊急待辦事項！")
        except Exception as e:
            st.error(f"解析錯誤: {e}")
    else:
        st.warning("📊 尚無資料")









