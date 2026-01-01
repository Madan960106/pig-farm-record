import streamlit as st
from streamlit_mic_recorder import mic_recorder
import gspread
import json
import datetime
import time
import requests
import base64
import pandas as pd # V55 新增：引入 Pandas 做數據分析

# --- 1. 頁面基礎設定 ---
st.set_page_config(page_title="母豬繁殖紀錄", page_icon="🐖", layout="wide")
st.title("🐖 養豬場語音紀錄系統 (V55 全功能戰情室版)")

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

# --- V55 新增：讀取並整理數據的函數 ---
# 使用 cache_data 避免每次點擊都重新讀取 Google Sheet，節省資源
@st.cache_data(ttl=60) # 每 60 秒才會真的去 Google 抓一次新資料
def load_data_from_sheet():
    client = get_gspread_client()
    if not client: return pd.DataFrame()
    
    try:
        sheet_config = st.secrets["SHEET_CONFIG"]
        sheet = client.open(sheet_config["sheet_name"]).sheet1
        data = sheet.get_all_values()
        
        # 轉換成 Pandas DataFrame
        if len(data) > 1:
            headers = data[0]
            rows = data[1:]
            df = pd.DataFrame(rows, columns=headers)
            return df
        else:
            return pd.DataFrame()
    except Exception as e:
        st.error(f"❌ 讀取數據失敗: {e}")
        return pd.DataFrame()

# --- 4. Gemini AI 分析 (核心 V54 不動) ---
def analyze_audio_smart(audio_bytes):
    api_key = st.secrets["GENAI_API_KEY"]
    model_name = "gemini-2.5-flash"
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    
    b64_audio = base64.b64encode(audio_bytes).decode('utf-8')
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    
    prompt_text = f"""
    你是一個專業的養豬場管理員。請將錄音內容轉換為 JSON。
    參考日期: {today_str}。
    
    【⚠️ 最高指導原則：單一事件制】
    如果錄音中同時包含兩個事件，請優先選擇「繁殖週期事件」(分娩 > 配種 > 斷奶)，將次要事件寫入備註。
    
    【欄位填寫規則】
    1. 遇到 "分娩" (生了、下豬)：
       - target_value (E欄): 請留空 ""。
       - note (F欄): 請將「出生數量」(如12頭) 及「健康狀況」全部寫在這裡。
    
    2. 遇到 "配種"：
       - target_value: 填公豬品種。
       - note: 其他備註。

    3. 遇到 "斷奶"、"醫療"、"測重"：
       - target_value: 填數量/藥名/重量。
       - note: 備註。

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
        # 清除快取，確保下次看報表是新的
        load_data_from_sheet.clear()
        return True
    except Exception as e:
        st.error(f"❌ 寫入失敗: {e}")
        return False

# --- 6. UI 介面 ---
tab1, tab2 = st.tabs(["🎙️ 現場錄音", "📊 數據看板"])

# === Tab 1: 錄音介面 (維持原樣) ===
with tab1:
    st.info(f"📍 目前設定： **{work_zone}** 由 **{operator_name}** 操作")
    audio = mic_recorder(start_prompt="🎤 點我錄音", stop_prompt="⏹️ 完成請點這", just_once=True, key='recorder_v55')

    if audio:
        st.session_state.audio_bytes = audio['bytes']
        st.session_state.analyzed_data = None

    if st.session_state.audio_bytes and st.session_state.analyzed_data is None:
        st.audio(st.session_state.audio_bytes, format='audio/wav')
        col1, col2 = st.columns(2)
        with col1:
            if st.button("⚡ 開始 AI 分析 (V55)", type="primary"):
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
            new_note = st.text_input("備註 (F欄)", default_data.get("note", ""))
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

# === Tab 2: 數據看板 (V55 全新功能) ===
with tab2:
    st.markdown("### 📊 豬場即時戰情室")
    
    if st.button("🔄 刷新數據"):
        load_data_from_sheet.clear()
        st.rerun()
    
    # 1. 讀取資料
    df = load_data_from_sheet()
    
    if not df.empty:
        # 資料清理：確保日期格式正確
        # 假設您的 Google Sheet 標題是: 
        # A:系統時間, B:事件日期, C:母豬耳號, D:事件類型, E:數值, F:備註, G:預定下階段, H:區域, I:人員
        # 我們用 index 來取欄位比較保險，或者用您設定的標題名稱
        # 這裡假設您有照著建議設標題，我們嘗試用標準名稱映射
        try:
            df.columns = ["Timestamp", "Date", "SowID", "Event", "Value", "Note", "NextAction", "Zone", "Operator"]
            
            # 將 Date 轉為 datetime 物件以便運算
            df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
            
            # === KPI 區塊 ===
            st.markdown("#### 📅 本週關鍵指標")
            today = pd.Timestamp.now().normalize()
            start_week = today - pd.Timedelta(days=today.dayofweek) # 本週一
            end_week = start_week + pd.Timedelta(days=6) # 本週日
            
            # 篩選本週資料
            this_week_df = df[(df['Date'] >= start_week) & (df['Date'] <= end_week)]
            
            kpi1, kpi2, kpi3 = st.columns(3)
            with kpi1:
                farrow_count = len(this_week_df[this_week_df['Event'].str.contains("分娩", na=False)])
                st.metric("🐷 本週分娩頭數", f"{farrow_count} 頭")
            with kpi2:
                mate_count = len(this_week_df[this_week_df['Event'].str.contains("配種", na=False)])
                st.metric("❤️ 本週配種頭數", f"{mate_count} 頭")
            with kpi3:
                wean_count = len(this_week_df[this_week_df['Event'].str.contains("斷奶", na=False)])
                st.metric("✂️ 本週離乳頭數", f"{wean_count} 頭")

            st.divider()

            # === 圖表區塊 ===
            col_chart1, col_chart2 = st.columns(2)
            
            with col_chart1:
                st.markdown("#### 🏗️ 各棟舍配種進度 (總計)")
                # 篩選配種事件
                mating_df = df[df['Event'] == "配種"]
                if not mating_df.empty:
                    # 依區域分組計數
                    zone_counts = mating_df['Zone'].value_counts()
                    st.bar_chart(zone_counts)
                else:
                    st.info("尚無配種數據")

            with col_chart2:
                st.markdown("#### 🚑 近期事件分佈")
                event_counts = df['Event'].value_counts()
                st.bar_chart(event_counts, color="#ffaa00")

            st.divider()

            # === 警示清單 (Parsing G欄) ===
            st.markdown("#### 🚨 未來 7 天預警清單 (接生/離乳/發情)")
            
            # 處理 G 欄 (NextAction)，格式如 "預產:2026-04-25"
            # 1. 提取日期字串
            df['AlertDateStr'] = df['NextAction'].astype(str).str.extract(r'(\d{4}-\d{2}-\d{2})')
            # 2. 轉為 datetime
            df['AlertDate'] = pd.to_datetime(df['AlertDateStr'], errors='coerce')
            
            # 3. 篩選：警示日期在 (今天) ~ (今天+7天) 之間
            mask = (df['AlertDate'] >= today) & (df['AlertDate'] <= today + pd.Timedelta(days=7))
            alert_df = df[mask].copy()
            
            if not alert_df.empty:
                # 整理顯示欄位
                display_df = alert_df[['AlertDateStr', 'SowID', 'NextAction', 'Zone']]
                display_df.columns = ["預定日期", "母豬耳號", "待辦事項", "位置"]
                # 依照日期排序
                display_df = display_df.sort_values(by="預定日期")
                
                st.dataframe(
                    display_df, 
                    hide_index=True,
                    column_config={
                        "預定日期": st.column_config.TextColumn("📅 預定日期"),
                        "母豬耳號": st.column_config.TextColumn("🐷 耳號"),
                        "待辦事項": st.column_config.TextColumn("⚡ 任務"),
                        "位置": st.column_config.TextColumn("📍 位置"),
                    },
                    use_container_width=True
                )
            else:
                st.success("🎉 未來 7 天無緊急待辦事項！")

        except Exception as e:
            st.error(f"數據解析錯誤，請檢查 Google Sheet 標題列是否正確。錯誤訊息: {e}")
            st.write("原始數據預覽:", df.head())
    else:
        st.warning("📊 目前還沒有資料，請先去 Tab 1 錄音輸入！")








