import streamlit as st
import google.generativeai as genai

st.title("🔍 V57 模型偵探")

# 1. 抓取 API Key (跟之前一樣的邏輯)
api_key = None
if "GEMINI_API_KEY" in st.secrets:
    api_key = st.secrets["GEMINI_API_KEY"]
elif "gcp_service_account" in st.secrets and "GEMINI_API_KEY" in st.secrets["gcp_service_account"]:
    api_key = st.secrets["gcp_service_account"]["GEMINI_API_KEY"]

if not api_key:
    st.error("❌ 找不到 Key，請檢查 Secrets")
    st.stop()

genai.configure(api_key=api_key)

# 2. 直接詢問 Google：我有什麼模型可用？
if st.button("列出所有可用模型", type="primary"):
    try:
        st.info("正在連線 Google 查詢中...")
        available_models = []
        for m in genai.list_models():
            # 我們只找能「產出內容 (generateContent)」的模型
            if 'generateContent' in m.supported_generation_methods:
                available_models.append(m.name)
        
        st.success(f"查詢成功！找到 {len(available_models)} 個模型：")
        st.write(available_models)
        st.caption("請將上方看起來像是 `models/gemini-xx-xx` 的名字複製下來給我。")
        
    except Exception as e:
        st.error(f"查詢失敗：{e}")
