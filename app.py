import streamlit as st
import os
import json
import google.generativeai as genai
import streamlit.components.v1 as components
from dotenv import load_dotenv

# 1. 환경 변수 로드 및 API 설정
load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# [고도화] 스키마 확장: pinyin 필드 추가 및 구조 최적화
combined_schema = {
    "type": "object",
    "properties": {
        "translated_input": {"type": "string"},
        "pinyin_input": {"type": "string"}, # 원문/번역문 전체 병음
        "analysis": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "pinyin": {"type": "string"}, # 성분별 병음
                    "pos": {"type": "string"},
                    "role": {
                        "type": "string",
                        "enum": ["주어", "술어", "목적어", "관형어", "부사어", "보어", "조사", "문장부호", "기타"]
                    },
                    "reason": {"type": "string"}
                },
                "required": ["text", "pinyin", "pos", "role", "reason"]
            }
        },
        "translation": {
            "type": "object",
            "properties": {
                "literal": {"type": "string"},
                "natural": {"type": "string"}
            },
            "required": ["literal", "natural"]
        },
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "pinyin": {"type": "string"}, # 제안 문장 병음
                    "label": {"type": "string"},
                    "grammar_point": {"type": "string"},
                    "explanation": {"type": "string"}
                },
                "required": ["text", "pinyin", "label", "grammar_point", "explanation"]
            }
        }
    },
    "required": ["translated_input", "pinyin_input", "analysis", "translation", "suggestions"]
}

model = genai.GenerativeModel(
    model_name='gemini-2.5-flash-lite',
    system_instruction="당신은 한국인을 위한 전문 중국어 문법 교수입니다. 사용자가 어떤 언어로 입력하든 완벽한 중국어로 변환하고, 반드시 모든 중국어 표현에 '한어병음'을 포함하여 분석하십시오."
)

# 2. UI 및 스타일링
st.set_page_config(page_title="중국어 마법사 ChaChaChina", layout="wide", page_icon="🇨🇳")

st.markdown("""
    <style>
    .main { background-color: #f8f9fa; }
    .source-info { background-color: #e3f2fd; padding: 18px; border-radius: 12px; border-left: 6px solid #1565C0; margin-bottom: 20px; }
    .pinyin-text { color: #555; font-family: 'Courier New', monospace; font-size: 14px; margin-top: 4px; }
    .translation-box { background-color: #ffffff; padding: 25px; border-radius: 15px; border-left: 6px solid #1565C0; box-shadow: 0 4px 6px rgba(0,0,0,0.05); margin-bottom: 25px; }
    .suggestion-card { background-color: #ffffff; padding: 20px; border-radius: 12px; border: 1px solid #e0e0e0; border-left: 5px solid #C62828; margin-bottom: 15px; }
    .grammar-badge { background-color: #f1f3f4; color: #1565C0; padding: 4px 10px; border-radius: 6px; font-size: 13px; font-weight: bold; border: 1px solid #d1d9e0; }
    </style>
    """, unsafe_allow_html=True)

st.title("🇨🇳 중국어 마법사 ChaChaChina")
st.caption("다국어 입력 지원 및 성분별 병음 표기는 물론, 생생한 구어체 제안까지!")

def analyze_pro(sentence):
    if not sentence.strip(): return None
    
    prompt = f"""
    당신은 한국어의 미묘한 정서를 중국어의 상황 중심 사고로 완벽하게 치환하는 전문 중국어 문법 교수입니다. 
    단순한 단어 치환을 넘어, 다음의 [언어 설계 원칙]에 따라 입력 문장을 재구성하십시오.

    **반드시 모든 분석 단계(translated_input, analysis, suggestions)는 '중국어'를 기준으로 수행하십시오.**

    [지침]
    1. 입력이 중국어가 아니라면 원문의 뉘앙스를 100% 살린 가장 자연스러운 현대 중국어로 번역하십시오.
    2. 모든 중국어 출력물에는 반드시 성조가 포함된 '한어병음'을 병기하십시오.
    3. [언어 설계 원칙]:
       - 상황 중심: '누가 무엇을 했다'는 보고보다 '상황이 어떻게 변했는지(결과)'를 중시하는 중국어식 사고를 반영하십시오.
       - 어기 매칭: 한국어의 감정적 종결어미(~지롱, ~네, ~거등 등)를 그에 적합한 중국어 어기조사(了, 吧, 呢, 嘛, 呀, 咯 등)로 치환하십시오.
       - 덩어리 우선: 단어의 나열이 아닌, 현지인들이 특정 상황에서 통째로 사용하는 관용적 덩어리 표현(Chunk)을 우선적으로 제안하십시오.
    4. 제안(suggestions) 섹션 필수 포함:
       - '생생한 구어체': 실제 현지인이 일상이나 SNS에서 사용하는 생동감 넘치는 표현.
       - '비즈니스/격식': 상황에 맞는 정중하고 정제된 표현.

    입력 문장: {sentence}
    """
    
    try:
        response = model.generate_content(
            prompt,
            generation_config={
                "response_mime_type": "application/json",
                "response_schema": combined_schema,
                "temperature": 0.1
            }
        )
        return json.loads(response.text)
    except Exception as e:
        st.error(f"분석 중 오류 발생: {e}")
        return None

# 3. 색상 테마 및 렌더링
THEME = {
    "주어": {"bg": "#E3F2FD", "border": "#1565C0"}, "술어": {"bg": "#FFEBEE", "border": "#C62828"},
    "목적어": {"bg": "#E8F5E9", "border": "#2E7D32"}, "보어": {"bg": "#F3E5F5", "border": "#7B1FA2"},
    "부사어": {"bg": "#FFFDE7", "border": "#FBC02D"}, "관형어": {"bg": "#E0F7FA", "border": "#00838F"},
    "조사": {"bg": "#F5F5F5", "border": "#757575"}, "문장부호": {"bg": "#FFFFFF", "border": "#EEEEEE"},
    "기타": {"bg": "#FFFFFF", "border": "#DDDDDD"}
}

input_text = st.text_input("분석할 문장을 입력하세요:", placeholder="예: '배고파 죽겠어', '어제 술 너무 많이 마셨어'")

if input_text:
    with st.spinner('병음 생성 및 정밀 분석 중...'):
        data = analyze_pro(input_text)
        
        if data:
            # 상단 번역 및 병음
            st.markdown(f"""
            <div class="source-info">
                <div style="font-size: 14px; color: #666; margin-bottom: 5px;">중국어 변환</div>
                <div style="font-size: 24px; font-weight: bold; color: #1a1a1a;">{data['translated_input']}</div>
                <div class="pinyin-text" style="font-size: 18px; color: #1565C0;">{data['pinyin_input']}</div>
            </div>
            """, unsafe_allow_html=True)

            # --- 섹션 1: 구조 분석 ---
            st.subheader("📌 성분별 정밀 분석")
            blocks_html = ""
            for item in data['analysis']:
                style = THEME.get(item['role'], THEME["기타"])
                blocks_html += f"""
                <div style="background-color: {style['bg']}; border: 2px solid {style['border']}; padding: 10px 15px; border-radius: 12px; text-align: center; box-shadow: 2px 2px 5px rgba(0,0,0,0.05); min-width: 80px;">
                    <div style="font-size: 13px; color: #666; margin-bottom: 2px;">{item['pinyin']}</div>
                    <div style="font-size: 26px; font-weight: bold; color: #222; margin-bottom: 2px;">{item['text']}</div>
                    <div style="font-size: 13px; color: {style['border']}; font-weight: 700; border-top: 1px solid {style['border']}; padding-top: 2px;">{item['role']}</div>
                </div>
                """
            full_display_html = f"""<div style="display: flex; flex-wrap: wrap; gap: 12px; font-family: sans-serif; padding: 10px;">{blocks_html}</div>"""
            components.html(full_display_html, height=180, scrolling=True)

            st.dataframe(data['analysis'], use_container_width=True)
            st.divider()

            # --- 섹션 2: 상세 해석 ---
            st.subheader("📖 번역 및 해석")
            st.markdown(f"""
            <div class="translation-box">
                <p style="margin-bottom: 12px; font-size: 20px; font-weight: 600; color: #1565C0;">{data['translation']['natural']}</p>
                <hr style="border: 0; border-top: 1px solid #eee; margin: 15px 0;">
                <p style="color: #666; font-size: 15px;"><strong>직역 가이드:</strong> {data['translation']['literal']}</p>
            </div>
            """, unsafe_allow_html=True)

            # --- 섹션 3: 대안 제안 (병음 포함) ---
            st.subheader("💡 상황별 표현 제안")
            for sug in data['suggestions']:
                label_color = "#C62828" if "교정" in sug['label'] else "#2E7D32"
                if "구어" in sug['label']: label_color = "#E65100" # 구어체는 오렌지색 강조
                
                st.markdown(f"""
                <div class="suggestion-card" style="border-left-color: {label_color};">
                    <span style="background-color: {label_color}; color: white; padding: 3px 10px; border-radius: 5px; font-size: 12px; font-weight: bold;">{sug['label']}</span>
                    <p style="font-size: 22px; font-weight: bold; margin: 12px 0 2px 0; color: #1a1a1a;">{sug['text']}</p>
                    <p class="pinyin-text" style="font-size: 16px; margin-bottom: 12px; color: {label_color};">{sug['pinyin']}</p>
                    <div style="margin: 10px 0;">
                        <span class="grammar-badge">📍 포인트</span>
                        <span style="font-size: 15px; margin-left: 8px; color: #1565C0; font-weight: 600;">{sug['grammar_point']}</span>
                    </div>
                    <p style="font-size: 15px; color: #444; line-height: 1.6;">{sug['explanation']}</p>
                </div>
                """, unsafe_allow_html=True)