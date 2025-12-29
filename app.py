import streamlit as st
from streamlit_audiorecorder import audiorecorder
import google.generativeai as genai
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json
import pandas as pd
import datetime
import time

# --- 1. 頁面基礎設定 ---
st.set_page_config(page_title="母豬繁殖紀錄", page_icon="🐖", layout="wide")

# 手機版面優化 CSS: 加大按鈕高度，避免手指誤觸
st.markdown("""
    <style>
    div.stButton > button:first-child {
        height: 3.5em;
        font-size: 20px;
        font-weight: bold;
        width: 100%;
        border-radius: 10px;
    }
    .stTabs [data-baseweb="tab-list"] button [data-testid="stMarkdownContainer"] p {
        font-size: 1.2rem;
    }
    </style>
""", unsafe_allow_html=True)

st.title("🐖 養豬場語音紀錄系統")

# 初始化 Session State (確保資料不會因重新整理而消失)
if 'analyzed_data' not in st.session_state:
    st.session_state.analyzed_data = None

# --- 2. 連線設定與核心函數 ---

# A. 設定 Gemini API
try:
    genai.configure(api_key=st.secrets["GENAI_API_KEY"])
except Exception as e:
    st.error("⚠️ API Key 設定錯誤，請檢查 Streamlit Secrets。")

# B. 設定 Google Sheets 連線
def get_gspread_client():
    try:
        # 從 secrets 讀取憑證
        creds_dict = dict(st.secrets["gcp_service_account"])
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        st.error(f"⚠️ 無法連接 Google Sheets: {e}")
        return None

# C. Gemini V32 語音分析邏輯
def analyze_audio_gemini(audio_bytes):
    model = genai.GenerativeModel('gemini-1.5-flash')
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    
    # 針對養豬場優化的 Prompt
    prompt = f"""
    你是一個專業的養豬場管理員。請聽錄音，將內容轉換為 JSON。
    參考日期: {today_str} (若說"今天"以此為準，"昨天"則減一天)。

    規則：
    1. 提取欄位：
       - sow_id (母豬耳號/編號): 字串。
       - event_type (事件): 必須是 "配種", "分娩", "斷奶", "醫療" 其中之一。
       - target_value (數值/對象): 配種填公豬品種(如杜洛克); 分娩填仔數; 斷奶填頭數。
       - date (日期): YYYY-MM-DD。
       - note (備註): 額外資訊(如:難產, 疫苗名)。
    
    2. 範例: "168號今天配種杜洛克" -> 
       {{"sow_id":"168", "event_type":"配種", "target_value":"杜洛克", "date":"{today_str}", "note":""}}
       
    請務必只回傳 JSON 字串，不要包含 ```json 標記。
    """
    
    try:
        with st.spinner("🤖 AI 正在聽取並分析數據..."):
            response = model.generate_content([
                prompt,
                {"mime_type": "audio/wav", "data": audio_bytes}
            ])
            # 清理 Markdown 標記
            text = response.text.replace("```json", "").replace("```", "").strip()
            return json.loads(text)
    except Exception as e:
        st.error(f"分析失敗: {e}")
        return None

# D. 寫入 Google Sheets
def save_to_sheet(data_row):
    client = get_gspread_client()
    if not client: return False
    
    try:
        sheet_name = st.secrets["SHEET_CONFIG"]["sheet_name"]
        sheet = client.open(sheet_name).sheet1
        
        # 自動計算預產期 (若為配種)
        due_date = ""
        if data_row.get("event_type") == "配種":
            try:
                m_date = datetime.datetime.strptime(data_row.get("date"), "%Y-%m-%d")
                due_date = (m_date + datetime.timedelta(days=114)).strftime("%Y-%m-%d")
            except:
                due_date = "日期格式錯誤"

        # 準備寫入的一列 (順序必須對應 Sheet 標題)
        row = [
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), # 紀錄時間
            data_row.get("date"),        # 事件日期
            data_row.get("sow_id"),      # 母豬耳號
            data_row.get("event_type"),  # 事件
            data_row.get("target_value"),# 數值/品種
            data_row.get("note"),        # 備註
            due_date                     # 預產期
        ]
        
        sheet.append_row(row)
        return True
    except Exception as e:
        st.error(f"寫入失敗: {e}")
        return False

# E. 讀取數據 (給報表用)
def load_data():
    client = get_gspread_client()
    if not client: return pd.DataFrame()
    try:
        sheet_name = st.secrets["SHEET_CONFIG"]["sheet_name"]
        sheet = client.open(sheet_name).sheet1
        data = sheet.get_all_records()
        return pd.DataFrame(data)
    except Exception as e:
        st.warning("尚無資料或讀取失敗")
        return pd.DataFrame()

# --- 3. UI 主畫面 ---

tab1, tab2 = st.tabs(["🎙️ 現場錄音", "📊 數據看板"])

# === 分頁 1: 錄音輸入 ===
with tab1:
    st.info("請點擊下方按鈕開始錄音，完成後再點一次停止。")
    
    # 錄音元件
    audio = audiorecorder("🔴 點擊錄音", "⬛ 停止錄音")

    if len(audio) > 0:
        # 顯示播放器確認
        st.audio(audio.export().read())
        
        col_act1, col_act2 = st.columns(2)
        with col_act1:
            if st.button("⚡ 開始 AI 分析", type="primary"):
                audio_bytes = audio.export(format="wav").read()
                result = analyze_audio_gemini(audio_bytes)
                if result:
                    st.session_state.analyzed_data = result
                    st.success("解析成功！請在下方確認資料。")
        with col_act2:
            if st.button("🗑️ 清除重錄"):
                st.session_state.analyzed_data = None
                st.rerun()

    # 資料確認與編輯表單
    if st.session_state.analyzed_data:
        st.divider()
        st.subheader("📝 資料確認")
        
        with st.form("confirm_form"):
            d = st.session_state.analyzed_data
            
            # 兩欄排列比較好按
            c1, c2 = st.columns(2)
            new_date = c1.text_input("日期", d.get("date"))
            new_id = c2.text_input("母豬耳號", d.get("sow_id"))
            
            c3, c4 = st.columns(2)
            event_options = ["配種", "分娩", "斷奶", "醫療"]
            # 嘗試自動對應 index
            try:
                idx = event_options.index(d.get("event_type"))
            except:
                idx = 0
            new_event = c3.selectbox("事件類型", event_options, index=idx)
            new_val = c4.text_input("數值 / 品種", d.get("target_value"))
            
            new_note = st.text_input("備註 (可選)", d.get("note"))
            
            submit_btn = st.form_submit_button("✅ 確認上傳雲端", type="primary")
            
            if submit_btn:
                final_data = {
                    "date": new_date,
                    "sow_id": new_id,
                    "event_type": new_event,
                    "target_value": new_val,
                    "note": new_note
                }
                
                with st.spinner("正在寫入 Google Sheets..."):
                    if save_to_sheet(final_data):
                        st.success(f"母豬 {new_id} 資料已儲存！")
                        st.session_state.analyzed_data = None # 清空
                        time.sleep(1)
                        st.rerun()

# === 分頁 2: 數據看板 ===
with tab2:
    if st.button("🔄 刷新最新數據"):
        st.rerun()
        
    df = load_data()
    
    if not df.empty:
        # 簡單的指標
        today_count = 0
        today_str = datetime.date.today().strftime("%Y-%m-%d")
        if "日期" in df.columns:
            today_count = len(df[df["日期"] == today_str])
        
        st.metric("今日新增紀錄", f"{today_count} 筆")
        
        # 使用 Expander 收折表格，避免手機版面太亂
        with st.expander("📂 點擊查看完整資料表", expanded=True):
            st.dataframe(df, use_container_width=True, hide_index=True)
            
        # 簡單的警示邏輯 (如果有預產期欄位)
        if "預產期(自動計算)" in df.columns:
            st.subheader("⚠️ 近期預產警示")
            df["預產期"] = pd.to_datetime(df["預產期(自動計算)"], errors='coerce')
            future_date = pd.Timestamp(datetime.date.today() + datetime.timedelta(days=3))
            
            # 篩選條件：配種且預產期快到了
            alerts = df[
                (df["事件"] == "配種") & 
                (df["預產期"] <= future_date) & 
                (df["預產期"] >= pd.Timestamp(datetime.date.today()))
            ]
            
            if not alerts.empty:
                st.error(f"有 {len(alerts)} 頭母豬即將分娩！")
                st.dataframe(alerts[["母豬耳號", "預產期(自動計算)"]], hide_index=True)
            else:
                st.success("未來 3 天無緊急待產母豬。")
    else:
        st.info("目前資料庫為空，請至「現場錄音」頁面新增資料。")