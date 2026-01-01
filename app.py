import streamlit as st
import requests
import json

st.set_page_config(page_title="API 診斷工具", page_icon="🕵️‍♂️")
st.title("🕵️‍♂️ Google AI 模型掃描器 (V44)")

# 1. 取得 API Key
try:
    api_key = st.secrets["GENAI_API_KEY"]
    masked_key = f"{api_key[:5]}...{api_key[-5:]}"
    st.success(f"🔑 讀取到 API Key: {masked_key}")
except:
    st.error("❌ 找不到 API Key，請檢查 Secrets！")
    st.stop()

# 2. 掃描按鈕
if st.button("🚀 開始掃描可用模型", type="primary"):
    st.info("正在向 Google 詢問您的帳號權限...")
    
    # 使用 list 網址，查詢所有可用模型
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    
    try:
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            st.balloons()
            st.write("### 🎉 恭喜！連線成功！以下是您的鑰匙可用的模型清單：")
            
            # 整理並顯示模型
            models = data.get('models', [])
            if models:
                valid_models = []
                for m in models:
                    # 我們只關心能生成內容的模型
                    if "generateContent" in m['supportedGenerationMethods']:
                        name = m['name'].replace("models/", "")
                        valid_models.append(name)
                        st.code(name)
                
                if not valid_models:
                    st.warning("⚠️ 連線成功，但沒有找到支援 generateContent 的模型。")
                else:
                    st.success(f"共找到 {len(valid_models)} 個可用模型。請告訴我上面列出了什麼！")
            else:
                st.warning("⚠️ API 回傳了空的模型清單。")
                st.json(data)
                
        else:
            st.error(f"❌ 掃描失敗！狀態碼: {response.status_code}")
            st.code(response.text)
            st.error("這代表您的 API Key 可能有區域限制，或是該 Google 專案未啟用 API 服務。")

    except Exception as e:
        st.error(f"連線錯誤: {e}")















