import streamlit as st
import os
import json
import re
import google.generativeai as genai
import streamlit.components.v1 as components
from dotenv import load_dotenv

# ==========================================
# 1. 초기 설정 및 API 연결
# ==========================================
load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# [1차 생성 모델] 데이터 초안 생성 담당
generator_model = genai.GenerativeModel(
    model_name='gemini-2.5-flash-lite',
    system_instruction="""
당신은 한국인을 위한 전문 중국어 교수이자 사전 편집자입니다. 
제공된 규칙을 준수하여 완벽한 중국어 분석 데이터를 JSON으로 제출하십시오.
    """
)

# [2차 검수 모델] 엄격한 규칙 대조 및 환각 교정 담당 (물리적 분리)
checker_model = genai.GenerativeModel(
    model_name='gemini-2.5-flash-lite',
    system_instruction="""
당신은 엄격한 검수자입니다. 제공된 데이터가 [절대 검수 기준]에 부합하는지 엄격히 검수하십시오. 
1차 답변의 논리를 무시하고 오직 기준 공식에만 의거하여 JSON 및 출판물을 교정하십시오.
    """
)

# ==========================================
# 2. 데이터 구조 정의 (JSON 스키마)
# ==========================================

SENTENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "translated_input": {"type": "string"},
        "pinyin_input": {"type": "string"},
        "analysis": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "pinyin": {"type": "string"},
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
                    "pinyin": {"type": "string"},
                    "meaning": {"type": "string"},
                    "label": {"type": "string"},
                    "grammar_point": {"type": "string"},
                    "explanation": {"type": "string"}
                },
                "required": ["text", "pinyin", "meaning", "label", "grammar_point", "explanation"]
            }
        }
    },
    "required": ["translated_input", "pinyin_input", "analysis", "translation", "suggestions"]
}

WORD_SCHEMA = {
    "type": "object",
    "properties": {
        "word": {"type": "string"},
        "pinyin": {"type": "string"},
        "pos": {"type": "string"},
        "definitions": {"type": "array", "items": {"type": "string"}},
        "usage_tips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "scenario": {"type": "string"},
                    "example_cn": {"type": "string"},
                    "example_py": {"type": "string"},
                    "example_kr": {"type": "string"}
                },
                "required": ["scenario", "example_cn", "example_py", "example_kr"]
            }
        },
        "synonyms_antonyms": {
            "type": "object",
            "properties": {
                "synonyms": {"type": "array", "items": {"type": "string"}},
                "antonyms": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["synonyms", "antonyms"]
        }
    },
    "required": ["word", "pinyin", "pos", "definitions", "usage_tips", "synonyms_antonyms"]
}

# ==========================================
# 3. 파이썬 기반 절대 성조 교정 필터 (Python Post-Processing)
# ==========================================

import re

def get_tone(syllable):
    """병음 음절의 성조를 숫자로 반환. 성조 부호가 없으면 0(경성) 반환"""
    if not syllable: return 0
    if re.search(r'[àèìòùǜ]', syllable): return 4
    if re.search(r'[ǎěǐǒǔǚ]', syllable): return 3
    if re.search(r'[áéíóúǘ]', syllable): return 2
    if re.search(r'[āēīōūǖ]', syllable): return 1
    return 0

def apply_tone(syllable, target_lower):
    """AI가 어떤 오답 성조를 썼든 무시하고, 대소문자를 유지하며 정답 성조로 완전히 덮어씌움"""
    if syllable.islower(): return target_lower
    if syllable.isupper(): return target_lower.upper()
    return target_lower.capitalize()

def fix_text_and_pinyin(hanzi, pinyin):
    """한자와 병음을 정밀하게 매칭하여 문맥(구두점, 접두사)까지 고려한 성조 강제 변조"""
    if not hanzi or not pinyin: return pinyin
    
    # 1. 한자만 추출
    hz_chars = [c for c in hanzi if '\u4e00' <= c <= '\u9fff']
    
    # 2. 병음 토큰 분리 및 음절의 '실제 인덱스' 위치 추적
    tokens = re.split(r"([a-zA-Züāēīōūǖáéíóúǘǎěǐǒǔǚàèìòùǜ]+)", pinyin)
    syllables = []
    for idx, t in enumerate(tokens):
        if re.match(r"^[a-zA-Züāēīōūǖáéíóúǘǎěǐǒǔǚàèìòùǜ]+$", t):
            syllables.append((idx, t))
            
    # 3. Fail-safe: 한자와 병음 개수가 안 맞으면 원본 반환 (얼화 등 예외 상황 보호)
    if len(hz_chars) != len(syllables): 
        return pinyin
        
    # 변조를 절대 하지 말아야 할 한자 앞글자 (서수, 요일, 10단위, 특정 고정단어)
    no_sandhi_prefixes = {'第', '初', '期', '周', '十', '唯', '单', '统'}

    for i in range(len(hz_chars) - 1):
        target_hz = hz_chars[i]
        curr_idx, curr_py = syllables[i]
        next_idx, next_py = syllables[i+1]
        
        # [핵심 방어 로직] 현재 글자와 다음 글자 사이에 구두점이 있다면 문맥이 단절된 것 (변조 금지)
        separator = "".join(tokens[curr_idx+1 : next_idx])
        if re.search(r'[,\.\?!\;；：，。？！]', separator):
            continue
            
        current_tone = get_tone(curr_py)
        next_tone = get_tone(next_py)
        
        # [一 의 변조 규칙]
        if target_hz == '一':
            # 방어 1: 서수/요일 등 문법적 예외 처리 (Look-behind)
            if i > 0 and hz_chars[i-1] in no_sandhi_prefixes:
                continue
                
            # 방어 2: 이미 경성 처리된 경우 보호
            if current_tone == 0 or next_tone == 0:
                continue

            base = re.sub(r'[īíǐì]', 'i', curr_py).lower()
            if base == 'yi':
                if next_tone == 4: 
                    # replace를 버리고 강제로 덮어씌움
                    tokens[curr_idx] = apply_tone(curr_py, 'yí')
                elif next_tone in [1, 2, 3]: 
                    tokens[curr_idx] = apply_tone(curr_py, 'yì')
                    
        # [不 의 변조 규칙]
        elif target_hz == '不':
            # 방어: 경성(duì bu qǐ 등) 보호
            if current_tone == 0 or next_tone == 0:
                continue
                
            base = re.sub(r'[ūúǔù]', 'u', curr_py).lower()
            if base == 'bu':
                if next_tone == 4: 
                    tokens[curr_idx] = apply_tone(curr_py, 'bú')
                else: 
                    tokens[curr_idx] = apply_tone(curr_py, 'bù')
                
    # 토큰 배열을 그대로 다시 합쳐서 반환
    return "".join(tokens)

def apply_python_sandhi_filter(data):
    """JSON 데이터를 순회하며 모든 중국어/병음 쌍에 정밀 필터 적용"""
    if not data: return data
    try:
        if 'translated_input' in data and 'pinyin_input' in data:
            data['pinyin_input'] = fix_text_and_pinyin(data['translated_input'], data['pinyin_input'])
            if 'analysis' in data:
                for item in data['analysis']:
                    item['pinyin'] = fix_text_and_pinyin(item.get('text',''), item.get('pinyin',''))
            if 'suggestions' in data:
                for item in data['suggestions']:
                    item['pinyin'] = fix_text_and_pinyin(item.get('text',''), item.get('pinyin',''))
        if 'word' in data and 'pinyin' in data and 'definitions' in data:
            data['pinyin'] = fix_text_and_pinyin(data['word'], data['pinyin'])
            if 'usage_tips' in data:
                for item in data['usage_tips']:
                    item['example_py'] = fix_text_and_pinyin(item.get('example_cn',''), item.get('example_py',''))
    except Exception:
        pass 
    return data

# ==========================================
# 4. 정합성 검수 엔진 (Chain of Verification)
# ==========================================

def get_verified_response(prompt, schema, target_input):
    """생성된 데이터의 정합성을 확인하고, 파이썬 강제 필터를 거쳐 반환하는 엔진"""
    try:
        # Step 1: 초안 생성 (LLM 1차 호출)
        draft_response = generator_model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json", "response_schema": schema, "temperature": 0.5} # 초안은 약간의 유연성 허용
        )
        draft_json = json.loads(draft_response.text)

        # Step 2: 파이썬 기반 절대 성조 교정 (LLM이 헛소리를 설명하기 전에 물리적으로 병음부터 정답으로 박아버림)
        corrected_json = apply_python_sandhi_filter(draft_json)

        # Step 3: 설명 텍스트 최종 검수 및 모순 제거 (LLM 2차 호출)
        verify_prompt = f"""
        입력값 '{target_input}'에 대해 1차 분석 및 [기계적 발음 기호 교정]이 완료된 데이터입니다:
        {json.dumps(corrected_json, ensure_ascii=False)}

        이 데이터의 모든 'pinyin' 필드에 적힌 발음은 수학적 알고리즘으로 완벽하게 교정된 **절대 정답**입니다. 
        당신의 임무는 이 완벽한 병음 데이터를 건드리는 것이 아니라, **한국어 설명 텍스트들이 이 병음과 모순되지 않는지 확인하고 교정**하는 것입니다.

        [절대 검수 기준]
        1. **설명 모순 제거 (Critical)**: `reason`, `grammar_point`, `explanation` 등의 텍스트 필드에 "원래 1성이지만 변조되어..." 같은 불필요한 성조 변조 설명이 있다면 헛소리(환각)일 확률이 높으므로 즉시 삭제하거나 문맥에 맞게 수정하십시오. 기재된 병음과 설명이 다르면 무조건 설명을 고치십시오. 병음은 건드리지 마십시오.
        2. **용어 및 로컬라이징**: `pos` 등 설명 필드에 한자가 섞였는지 확인하고 100% 한국어로 교정하세요. 아라비아 숫자는 중국어 수사로 치환되었는지 확인하세요.
        3. **사족 금지**: 데이터 내에 '수정했습니다' 같은 메타 텍스트가 섞이지 않게 하세요.

        병음은 유지한 채 텍스트의 논리적 모순만 수정한 최종 JSON을 출력하십시오.
        """
        # 검수는 극히 보수적으로 진행
        final_response = checker_model.generate_content(
            verify_prompt,
            generation_config={"response_mime_type": "application/json", "response_schema": schema, "temperature": 0.1}
        )
        
        # 만일의 사태(LLM 2차가 또 병음을 건드렸을 경우)를 대비해 안전장치로 필터 한 번 더 통과
        final_json = apply_python_sandhi_filter(json.loads(final_response.text))
        
        return final_json
        
    except Exception as e:
        st.error(f"엔진 오류 발생: {e}")
        return None

# ==========================================
# 5. 핵심 AI 분석 함수 (프롬프트 유지)
# ==========================================

def analyze_sentence(sentence):
    if not sentence.strip(): return None
    
    prompt = f"""
    # [IDENTITY]
    당신은 한국어의 정서를 중국어의 '상황 중심 사고'로 치환하는 전문 중국어 교수입니다.

    # [RULE 1: OUTPUT LANGUAGE CONTROL]
    1. 중국어 전용 필드: `translated_input`, `analysis` 내 `text`, `suggestions` 내 `text`. (반드시 성조가 포함된 한어병음을 병기하세요.)
    2. 한국어 전용 필드: `analysis` 내 `pos` 및 `reason`, `suggestions` 내 `meaning`, `grammar_point`, `explanation` 등 모든 설명 필드. 
    3. 숫자 처리 원칙: 입력 문장에 포함된 모든 아라비아 숫자는 문맥에 적합한 **중국어 수사(汉자)로 반드시 변환**하여 출력하세요. (예: 1시 -> 一点, 100개 -> 一百개)

    # [RULE 2: PHONETIC PRECISION (MUST FOLLOW SANDHI RULES)]
    1. **1:1 띄어쓰기 강제 (Critical)**: 기계적 후처리 필터의 정합성을 위해, 모든 병음은 단어 단위로 뭉쳐 쓰지 말고 **반드시 한 글자마다 띄어쓰기**를 하십시오. (예: yíwàn yìqiān (X) -> yí wàn yì qiān (O))
    2. **문맥적 발음 확정**: 다음자(多音字)와 경성(Neutral Tone)은 사전적 기본형이 아닌 **현재 문맥에서 발음되는 실제 소리**를 확정하여 기재하세요. (예: 好的 -> hǎo de, 炸鸡 -> zhá jī)
    3. **가독성 및 표준 준수**: 제3성 변조와 같이 표기법상 기본형을 유지하는 경우는 원칙을 따르되, 사용자가 '보이는 병음 그대로' 읽었을 때 현지 발음에 가장 가깝도록 표기하세요.

    # [RULE 3: LINGUISTIC DESIGN PRINCIPLES]
    1. 상황 중심: 동작의 보고보다 '상황의 변화와 결과'를 중시하는 중국어식 사고를 투영하세요.
    2. 어기 매칭: 한국어 감정 종결어미를 적합한 중국어 어기조사(了, 吧, 呢, 嘛, 呀, 咯 등)로 치환하세요.
    3. 덩어리 우선: 단어 나열이 아닌 현지 관용적 덩어리 표현(Chunk)을 최우선 제안하세요.

    # [RULE 4: SUGGESTIONS COMPOSITION GUIDE]
    1. 필수 포함 카테고리 (최소 2개):
       - '생생한 구어체': 현지 일상 및 SNS 느낌의 자연스러운 표현.
       - '축약된 표현': 성분이 생략된, 일상적인 입말 길이의 요약 버전.
       - '격식있는 표현': 정중하고 정제된 비즈니스/공적 관계의 표현.
    2. 추가 제안 카테고리 (선택):
       - '신조어 및 SNS 용어': 메신저를 통해 사용될법한 표현.
       - '비속어': 일상적으로는 다소 잘 사용되지 않을 수도 있는, 강렬한 비어와 속어.
    3. grammar_point 작성 지침:
       - 핵심 패턴 명시: 해당 표현의 뉘앙스를 결정짓는 문법 장치를 20자 내외의 명쾌한 한국어 문장으로 요약하세요.
       - 예시: "정도보어 '死了'를 사용하여 고통의 극심함을 강조함", "어기조사 '啊'를 더해 문장의 끝을 부드럽게 마무리"
    4. 미묘한 맛 보존: `meaning` 필드에 한국어의 뉘앙스를 100% 살린 자연스러운 번역을 포함하세요.

    # [RULE 5: ZERO-INFERENCE CONSTRAINT]
    1. 배경 지식이나 개인적 추측으로 사용자의 의도를 넘겨짚지 마세요.
    2. 오직 언어학적 사실과 객관적 용법에 기반하여 한국어로 설명하세요.

    # [INPUT]
    입력 문장: {sentence}
    """
    return get_verified_response(prompt, SENTENCE_SCHEMA, sentence)

def analyze_word(word):
    if not word.strip(): return None
    
    prompt = f"""
    # [IDENTITY]
    당신은 전문 중국어 사전 편집자입니다. 한국인 학습자를 위해 단어의 용법을 정밀 분석하십시오.

    # [RULE 1: FIELD LANGUAGE CONTROL]
    1. 중국어 사용 필드: 분석 대상 단어(`word`), 예문(`example_cn`), 모든 병음(`pinyin`) 데이터.
    2. 한국어 사용 필드: 품사(`pos`), 주요 의미(`definitions`), 상황 설정(`scenario`), 예문 해석(`example_kr`).
    3. 숫자 단독 입력 대응: 아라비아 숫자가 입력될 경우, 이를 해당 **중국어 수사(汉자)**로 치환하여 단어로서 정밀 분석하세요. (예: "1" 입력 시 "一"로 분석)

    # [RULE 2: PHONETIC PRECISION (MUST FOLLOW SANDHI RULES)]
    1. **1:1 띄어쓰기 강제 (Critical)**: 기계적 후처리 필터의 정합성을 위해, 모든 병음은 단어 단위로 뭉쳐 쓰지 말고 **반드시 한 글자마다 띄어쓰기**를 하십시오. (예: yíwàn yìqiān (X) -> yí wàn yì qiān (O))
    2. **문맥적 발음 확정**: 다음자(多音字)와 경성(Neutral Tone)은 사전적 기본형이 아닌 **현재 문맥에서 발음되는 실제 소리**를 확정하여 기재하세요. (예: 好的 -> hǎo de, 炸鸡 -> zhá jī)
    3. **가독성 및 표준 준수**: 제3성 변조와 같이 표기법상 기본형을 유지하는 경우는 원칙을 따르되, 사용자가 '보이는 병음 그대로' 읽었을 때 현지 발음에 가장 가깝도록 표기하세요.

    # [RULE 3: DICTIONARY DESIGN PRINCIPLES]
    1. 다음자(多音字) 전수 분석: 입력어에 여러 발음이 존재할 경우, `pinyin` 필드에 모든 발음을 나열하고 `definitions`에는 각 발음별 의미를 번호를 매겨 상세히 나열하세요.
    2. 유의어/반의어 데이터: 의미적으로 대응하는 단어를 선정하되, 반드시 **"한자(병음)"** 형식을 준수하여 기재하세요. (데이터가 없는 경우에만 빈 리스트 [] 반환)
    3. 뉘앙스 정밀도: 단어가 주는 사회적/감정적 거리감과 사용 금기 등을 한국어로 명확히 정의하세요.
    4. 실전 예문 구성: `usage_tips`에는 실제 대화의 생생한 덩어리(Chunk) 표현을 넣으세요. 다음자의 경우 각 발음의 용법이 대조되는 예문을 우선 구성하세요.
    
    # [RULE 4: ZERO-INFERENCE CONSTRAINT]
    1. 배경 지식이나 개인적 추측으로 사용자의 의도를 넘겨짚지 마세요.
    2. 오직 언어학적 사실과 객관적 용법에 기반하여 한국어로 설명하세요.

    # [INPUT]
    입력 단어: {word}
    """
    return get_verified_response(prompt, WORD_SCHEMA, word)

# ==========================================
# 6. UI 및 렌더링 (기존 유지)
# ==========================================
st.set_page_config(page_title="중국어 마법사 ChaChaChina", layout="wide", page_icon="🇨🇳")

ROLE_THEME = {
    "주어": {"bg": "#E3F2FD", "border": "#1565C0"}, "술어": {"bg": "#FFEBEE", "border": "#C62828"},
    "목적어": {"bg": "#E8F5E9", "border": "#2E7D32"}, "보어": {"bg": "#F3E5F5", "border": "#7B1FA2"},
    "부사어": {"bg": "#FFFDE7", "border": "#FBC02D"}, "관형어": {"bg": "#E0F7FA", "border": "#00838F"},
    "조사": {"bg": "#F5F5F5", "border": "#757575"}, "문장부호": {"bg": "#FFFFFF", "border": "#EEEEEE"},
    "기타": {"bg": "#FFFFFF", "border": "#DDDDDD"}
}

st.markdown("""
    <style>
    .main { background-color: #f8f9fa; }
    .source-info { background-color: #e3f2fd; padding: 18px; border-radius: 12px; border-left: 6px solid #1565C0; margin-bottom: 20px; }
    .pinyin-text { color: #555; font-family: 'Courier New', monospace; font-size: 14px; margin-top: 4px; }
    .translation-box { background-color: #ffffff; padding: 25px; border-radius: 15px; border-left: 6px solid #1565C0; box-shadow: 0 4px 6px rgba(0,0,0,0.05); margin-bottom: 25px; }
    .suggestion-card { background-color: #ffffff; padding: 20px; border-radius: 12px; border: 1px solid #e0e0e0; border-left: 5px solid #C62828; margin-bottom: 15px; }
    .grammar-badge { background-color: #f1f3f4; color: #1565C0; padding: 4px 10px; border-radius: 6px; font-size: 13px; font-weight: bold; border: 1px solid #d1d9e0; }
    .dict-card { background-color: #fff; padding: 20px; border-radius: 15px; border: 1px solid #eee; box-shadow: 0 2px 4px rgba(0,0,0,0.02); margin-bottom: 15px; }
    </style>
    """, unsafe_allow_html=True)

if 's_result' not in st.session_state: st.session_state.s_result = None
if 'w_result' not in st.session_state: st.session_state.w_result = None

st.title("🇨🇳 중국어 마법사 ChaChaChina")
st.caption("다국어 입력 지원 및 성분별 병음 표기는 물론, 생생한 구어체 제안까지!")

tab_sentence, tab_word = st.tabs(["🔍 문장 정밀 분석", "📚 AI 스마트 단어 사전"])

with tab_sentence:
    def trigger_s_analysis(): st.session_state.s_result = "PENDING"
    st.text_input("분석할 문장을 입력하세요:", placeholder="예: '배고파 죽겠어.' (KR), '我饿死了' (CN), ...", key="sentence_input_val", on_change=trigger_s_analysis)

    if st.session_state.s_result == "PENDING":
        with st.spinner('🔎 정합성 검토를 포함하여 AI가 정밀 분석 중입니다 ...'):
            st.session_state.s_result = analyze_sentence(st.session_state.sentence_input_val)

    s_data = st.session_state.s_result
    if s_data and s_data != "PENDING":
        st.markdown(f"""
        <div class="source-info">
            <div style="font-size: 14px; color: #666; margin-bottom: 5px;">중국어 변환</div>
            <div style="font-size: 24px; font-weight: bold; color: #1a1a1a;">{s_data['translated_input']}</div>
            <div class="pinyin-text" style="font-size: 18px; color: #1565C0;">{s_data['pinyin_input']}</div>
        </div>
        """, unsafe_allow_html=True)

        st.subheader("📌 성분별 정밀 분석")
        blocks_html = "".join([f"""
            <div style="background-color: {ROLE_THEME.get(i['role'], ROLE_THEME['기타'])['bg']}; border: 2px solid {ROLE_THEME.get(i['role'], ROLE_THEME['기타'])['border']}; padding: 10px 15px; border-radius: 12px; text-align: center; box-shadow: 2px 2px 5px rgba(0,0,0,0.05); min-width: 80px;">
                <div style="font-size: 13px; color: #666; margin-bottom: 2px;">{i['pinyin']}</div>
                <div style="font-size: 26px; font-weight: bold; color: #222; margin-bottom: 2px;">{i['text']}</div>
                <div style="font-size: 13px; color: {ROLE_THEME.get(i['role'], ROLE_THEME['기타'])['border']}; font-weight: 700; border-top: 1px solid {ROLE_THEME.get(i['role'], ROLE_THEME['기타'])['border']}; padding-top: 2px;">{i['role']}</div>
            </div>
            """ for i in s_data['analysis']])
        components.html(f"<div style='display: flex; flex-wrap: wrap; gap: 12px; font-family: sans-serif; padding: 10px;'>{blocks_html}</div>", height=180, scrolling=True)

        st.dataframe(s_data['analysis'], use_container_width=True)
        st.divider()

        st.subheader("📖 번역 및 해석")
        st.markdown(f"""
        <div class="translation-box">
            <p style="margin-bottom: 12px; font-size: 20px; font-weight: 600; color: #1565C0;">{s_data['translation']['natural']}</p>
            <hr style="border: 0; border-top: 1px solid #eee; margin: 15px 0;">
            <p style="color: #666; font-size: 15px;"><strong>직역 가이드:</strong> {s_data['translation']['literal']}</p>
        </div>
        """, unsafe_allow_html=True)

        st.subheader("💡 상황별 표현 제안")
        for sug in s_data['suggestions']:
            color = "#C62828" if "교정" in sug['label'] else "#2E7D32"
            if "구어" in sug['label']: color = "#E65100"
            st.markdown(f"""
            <div class="suggestion-card" style="border-left-color: {color};">
                <span style="background-color: {color}; color: white; padding: 3px 10px; border-radius: 5px; font-size: 12px; font-weight: bold;">{sug['label']}</span>
                <p style="font-size: 22px; font-weight: bold; margin: 12px 0 2px 0; color: #1a1a1a;">{sug['text']}</p>
                <p class="pinyin-text" style="font-size: 16px; margin-bottom: 4px; color: {color};">{sug['pinyin']}</p>
                <p style="font-size: 16px; color: #555; font-style: italic; margin-bottom: 12px;">(뜻: {sug['meaning']})</p>
                <div style="margin: 10px 0;">
                    <span class="grammar-badge">📍 포인트</span>
                    <span style="font-size: 15px; margin-left: 8px; color: #1565C0; font-weight: 600;">{sug['grammar_point']}</span>
                </div>
                <p style="font-size: 15px; color: #444; line-height: 1.6;">{sug['explanation']}</p>
            </div>
            """, unsafe_allow_html=True)

with tab_word: 
    def trigger_w_analysis(): st.session_state.w_result = "PENDING"
    st.text_input("단어를 입력하세요:", placeholder="'취하다' (KR), '醉' (CN), ...", key="word_input_val", on_change=trigger_w_analysis)

    if st.session_state.w_result == "PENDING":
        with st.spinner('🔎 데이터 정합성을 점검하며 단어를 분석 중입니다 ...'):
            st.session_state.w_result = analyze_word(st.session_state.word_input_val)

    w_data = st.session_state.w_result
    if w_data and w_data != "PENDING":
        st.markdown(f"""
        <div class="source-info" style="border-left-color: #2E7D32; background-color: #F1F8E9;">
            <div style="display: flex; align-items: baseline; gap: 15px;">
                <span style="font-size: 32px; font-weight: bold;">{w_data['word']}</span>
                <span style="font-size: 20px; color: #2E7D32; font-family: courier;">{w_data['pinyin']}</span>
                <span class="grammar-badge" style="background-color: #E8F5E9; color: #2E7D32;">{w_data['pos']}</span>
            </div>
            <div style="margin-top: 10px; font-size: 18px; color: #333;">
                <strong>주요 의미:</strong> {', '.join(w_data['definitions'])}
            </div>
        </div>
        """, unsafe_allow_html=True)

        sa = w_data.get('synonyms_antonyms', {})
        syns, ants = sa.get('synonyms', []), sa.get('antonyms', [])
        if syns or ants:
            col1, col2 = st.columns(2)
            with col1: 
                if syns: st.info(f"🔗 **유의어:** {', '.join(syns)}")
            with col2: 
                if ants: st.warning(f"↔️ **반의어:** {', '.join(ants)}")

        st.write("#### 💡 실전 활용 예문")
        for usage in w_data['usage_tips']:
            st.markdown(f"""
            <div class="dict-card">
                <div style="color: #2E7D32; font-weight: bold; font-size: 14px; margin-bottom: 8px;">📍 상황: {usage['scenario']}</div>
                <div style="font-size: 20px; font-weight: bold; color: #1a1a1a;">{usage['example_cn']}</div>
                <div class="pinyin-text" style="font-size: 15px; margin-bottom: 8px;">{usage['example_py']}</div>
                <div style="font-size: 15px; color: #666; border-top: 1px dashed #eee; padding-top: 8px;">{usage['example_kr']}</div>
            </div>
            """, unsafe_allow_html=True)