import streamlit as st
from streamlit_mic_recorder import mic_recorder
from google import genai
from google.genai import types
import os
import json
from datetime import datetime
import re
import time  # 💡 自動リトライの時間待ちに必要

# ==========================================
# 🔑 APIキーの設定
# ==========================================
api_key = "AIzaSyC2fsv9wURqNkW5aRt5jMH3QFffMhaF_LY"  # ← 必ず本物の英数字キーに書き換えてください
client = genai.Client(api_key=api_key)

# ==========================================
# 📁 科目データの管理
# ==========================================
SUBJECTS_FILE = "subjects.txt"

def load_subjects():
    if os.path.exists(SUBJECTS_FILE):
        with open(SUBJECTS_FILE, "r", encoding="utf-8") as f:
            return [line.strip() for line in f.readlines() if line.strip()]
    else:
        return ["未分類", "共通科目", "専門科目"]

def save_subjects(subjects_list):
    with open(SUBJECTS_FILE, "w", encoding="utf-8") as f:
        for sub in subjects_list:
            f.write(f"{sub}\n")

if "subjects" not in st.session_state:
    st.session_state.subjects = load_subjects()

# ==========================================
# 🛠️ 便利なJSON解析関数
# ==========================================
def parse_ai_json(text):
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return json.loads(text.strip())

# ==========================================
# 🛠️ サイドバー設定
# ==========================================
st.sidebar.title("⚙️ アプリ設定")
make_quiz = st.sidebar.toggle("📝 復習用テスト問題を作成する", value=True)
st.sidebar.write("---")
st.sidebar.subheader("📚 科目の編集")

new_subject = st.sidebar.text_input("新しい科目名を入力:")
if st.sidebar.button("➕ 科目を追加"):
    if new_subject and new_subject not in st.session_state.subjects:
        st.session_state.subjects.append(new_subject)
        save_subjects(st.session_state.subjects)
        st.sidebar.success(f"「{new_subject}」を追加しました！")
        st.rerun()

if len(st.session_state.subjects) > 0:
    del_subject = st.sidebar.selectbox("削除する科目を選択:", st.session_state.subjects)
    if st.sidebar.button("❌ 選択した科目を削除"):
        st.session_state.subjects.remove(del_subject)
        save_subjects(st.session_state.subjects)
        st.sidebar.error(f"「{del_subject}」を削除しました。")
        st.rerun()

# ==========================================
# 🎬 メイン画面（タブ構造）
# ==========================================
st.title("🎓 講義録音 ＆ 日付管理ノートアプリ")

selected_subject = st.selectbox("📌 対象の科目を選択してください:", st.session_state.subjects)

history_file = f"history_{selected_subject}.md"
quiz_file = f"quiz_{selected_subject}.json"

tab1, tab2 = st.tabs(["📝 ノート＆録音", "🧠 一問一答テスト"])

# ------------------------------------------
# タブ1: 録音・資料解析・日付別履歴
# ------------------------------------------
with tab1:
    st.subheader(f"📖 【{selected_subject}】の新規記録")
    
    lecture_date = st.date_input("📅 講義日を選択してください:", datetime.now())
    save_date_str = lecture_date.strftime("%Y-%m-%d")
    
    uploaded_file = st.file_uploader(
        "📂 今日の講義資料（PDF・画像）をアップロード", 
        type=["pdf", "png", "jpg", "jpeg", "txt"],
        key="new_doc"
    )
    
    audio_data = mic_recorder(
        start_prompt="🔴 録音開始",
        stop_prompt="⏹️ 録音停止",
        key="recorder"
    )

    if audio_data and "bytes" in audio_data:
        st.session_state.active_audio = audio_data["bytes"]
    
    if "active_audio" in st.session_state and st.session_state.active_audio:
        st.audio(st.session_state.active_audio)
        
        if st.button("🔥 講義の解析・すり合わせを実行", type="primary"):
            status_container = st.empty()
            with st.spinner("最新のGemini 2.5で解析中..."):
                try:
                    contents_payload = []
                    audio_part = types.Part.from_bytes(data=st.session_state.active_audio, mime_type="audio/wav")
                    contents_payload.append(audio_part)
                    
                    if uploaded_file:
                        contents_payload.append(types.Part.from_bytes(data=uploaded_file.read(), mime_type=uploaded_file.type))
                    
                    prompt = """
                    音声と資料を突き合わせ、矛盾のない正確な講義要約を作成してください。
                    出力は必ず以下のキーを持つJSONオブジェクトのみとしてください。Markdownのコードブロック(```)は付けず、直接JSONのテキストを出力してください。
                    
                    {
                      "note_markdown": "専門用語解説、講義の要点、結論を含めた美しいMarkdown形式 of 講義ノートの本文",
                      "quizzes": [
                        {"question": "問題文", "answer": "答え", "explanation": "解説"}
                      ]
                    }
                    """
                    if not make_quiz:
                        prompt = prompt.replace(',\n                      "quizzes": [\n                        {"question": "問題文", "answer": "答え", "explanation": "解説"}\n                      ]', '')
                    
                    contents_payload.append(prompt)
                    config = types.GenerateContentConfig(response_mime_type="application/json")
                    
                    # 💡 【渋滞突破】503エラーが出たら自動で4回まで再トライするループ
                    response = None
                    for attempt in range(4):
                        try:
                            response = client.models.generate_content(
                                model="gemini-2.5-flash", 
                                contents=contents_payload,
                                config=config
                            )
                            break  # 成功したらループを抜ける
                        except Exception as api_error:
                            if "503" in str(api_error) and attempt < 3:
                                status_container.warning(f"⚠️ Googleサーバーが混雑しています。自動リトライ中... ({attempt+1}/3回目)")
                                time.sleep(3)  # 3秒待ってから再挑戦
                                continue
                            raise api_error  # それ以外のエラーや上限に達したら上に投げる

                    result_data = parse_ai_json(response.text)
                    note_body = result_data.get("note_markdown", "ノートの生成に失敗しました。")
                    new_quizzes = result_data.get("quizzes", [])
                    
                    final_markdown = note_body
                    
                    if new_quizzes and make_quiz:
                        final_markdown += "\n\n### 📝 復習用テスト\n\n"
                        for i, q in enumerate(new_quizzes):
                            final_markdown += f"**Q{i+1}. {q.get('question', '')}**\n\n"
                            final_markdown += f"> **【正解】** {q.get('answer', '')}  \n"
                            final_markdown += f"> **【解説】** {q.get('explanation', '')}\n\n"
                    
                    status_container.empty()
                    st.success("解析完了！")
                    st.markdown(final_markdown)
                    
                    current_time = datetime.now().strftime("%H:%M:%S")
                    with open(history_file, "a", encoding="utf-8") as f:
                        f.write(f"\n\n---\n# 📅 講義日: {save_date_str} (記録時間: {current_time})\n")
                        if uploaded_file:
                            f.write(f"📎 添付資料: {uploaded_file.name}\n\n")
                        f.write(final_markdown)
                    
                    if new_quizzes and make_quiz:
                        for q in new_quizzes:
                            q["date"] = save_date_str
                        
                        existing = []
                        if os.path.exists(quiz_file):
                            with open(quiz_file, "r", encoding="utf-8") as f:
                                try: existing = json.load(f)
                                except: existing = []
                        
                        existing.extend(new_quizzes)
                        with open(quiz_file, "w", encoding="utf-8") as f:
                            json.dump(existing, f, ensure_ascii=False, indent=2)
                            
                    st.session_state.active_audio = None
                    st.rerun()
                except Exception as e:
                    status_container.empty()
                    st.error(f"エラー: {e}")

    # ------------------------------------------
    # 📚 過去ノート履歴
    # ------------------------------------------
    st.write("---")
    st.subheader(f"📚 【{selected_subject}】の過去ノート履歴")
    
    if os.path.exists(history_file):
        with open(history_file, "r", encoding="utf-8") as f:
            history_text = f.read()
        
        history_text = history_text.replace("### 📝 復習用テスト\n\n\n\n", "")
        
        sections = [s for s in re.split(r'(?=# 📅|# 🕒|# 🛠️)', history_text) if s.strip()]
        
        history_options = []
        section_mapping = {}
        
        for section in sections:
            first_line = section.strip().split('\n')[0]
            date_match = re.search(r'\d{4}-\d{2}-\d{2}', section)
            time_match = re.search(r'\d{2}:\d{2}:\d{2}', section)
            
            if date_match:
                date_str = date_match.group(0)
                time_str = f" ({time_match.group(0)})" if time_match else ""
                
                if "補強" in first_line or "🛠️" in first_line:
                    opt_title = f"🛠️ 補強ログ: {date_str}{time_str}"
                else:
                    opt_title = f"📅 講義ノート: {date_str}{time_str}"
            else:
                cleaned = first_line.replace("#", "").strip()
                if not cleaned or cleaned == "---" or "復習用テスト" in cleaned:
                    continue
                opt_title = f"📜 過去の講義データ: {cleaned[:20]}"
            
            base_title = opt_title
            counter = 1
            while opt_title in section_mapping:
                opt_title = f"{base_title} ({counter})"
                counter += 1
                
            history_options.append(opt_title)
            section_mapping[opt_title] = section

        if history_options:
            with st.container(height=400):
                for opt_title in reversed(history_options):
                    with st.expander(opt_title, expanded=False):
                        st.markdown(section_mapping[opt_title])
            
            # 🎯 特定の日付を選択して丸ごとすり合わせ
            st.write("---")
            st.info("💡 **特定の過去ノートを指定して資料で補強・すり合わせ**")
            
            selected_target_note = st.selectbox(
                "すり合わせを行いたい過去の履歴を選択してください:", 
                options=reversed(history_options),
                key="target_note_select"
            )
            
            past_doc = st.file_uploader(
                "すり合わせる講義資料（PDF・画像）をアップロード:", 
                type=["pdf", "png", "jpg", "jpeg", "txt"], 
                key="past_doc"
            )
            
            if past_doc and st.button("✨ 選択した履歴とのすり合わせを実行"):
                recon_status = st.empty()
                with st.spinner(f"「{selected_target_note}」の内容全体と資料を照合中..."):
                    try:
                        p_doc_part = types.Part.from_bytes(data=past_doc.read(), mime_type=past_doc.type)
                        target_section_text = section_mapping[selected_target_note]
                        
                        reconcile_prompt = f"""
                        以下の【指定された過去のノート】と、添付した【新しい講義資料】を照らし合わせ、
                        内容の矛盾を修正したり、足りない情報を補足した「完全版の補足ノート」を作成してください。
                        関係のない日付の情報は含めないでください。
                        
                        出力は必ず以下のキーを持つJSONオブジェクトのみとしてください。
                        {{
                          "note_markdown": "補足・修正された完全版の講義ノートの本文(Markdown形式)"
                        }}
                        
                        【指定された過去のノート】:
                        {target_section_text}
                        """
                        
                        config = types.GenerateContentConfig(response_mime_type="application/json")
                        
                        # 💡 こちらの機能にも自動リトライを搭載
                        res = None
                        for attempt in range(4):
                            try:
                                res = client.models.generate_content(
                                    model="gemini-2.5-flash", 
                                    contents=[p_doc_part, reconcile_prompt],
                                    config=config
                                )
                                break
                            except Exception as api_error:
                                if "503" in str(api_error) and attempt < 3:
                                    recon_status.warning(f"⚠️ Googleサーバーが混雑しています。自動リトライ中... ({attempt+1}/3回目)")
                                    time.sleep(3)
                                    continue
                                raise api_error

                        res_data = parse_ai_json(res.text)
                        reinforced_note = res_data.get("note_markdown", "生成失敗")
                        
                        recon_status.empty()
                        st.subheader("✅ 補強・すり合わせされた内容")
                        st.markdown(reinforced_note)
                        
                        current_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        with open(history_file, "a", encoding="utf-8") as f:
                            f.write(f"\n\n---\n# 🛠️ 資料による補強ログ ({current_time_str})\n")
                            f.write(f"**対象にした履歴:** {selected_target_note}\n\n")
                            f.write(reinforced_note)
                            
                        st.success(f"「{selected_target_note}」に対する補強内容を履歴に追記しました！")
                        st.rerun()
                    except Exception as e:
                        recon_status.empty()
                        st.error(f"エラー: {e}")
        else:
            st.info("有効な講義ノートの履歴がまだありません。")
    else:
        st.info("まだ記録がありません。")

# ==========================================
# 🧠 タブ2: 一問一答テスト
# ==========================================
with tab2:
    st.subheader(f"🧠 【{selected_subject}】の復習テスト")
    if os.path.exists(quiz_file):
        with open(quiz_file, "r", encoding="utf-8") as f:
            try:
                quizzes = json.load(f)
                if quizzes:
                    for i, q in enumerate(reversed(quizzes)):
                        q_date = q.get('date', '過去のデータ')
                        st.markdown(f"#### Q. [{q_date}] {q.get('question', '')}")
                        with st.expander("👉 解答を表示"):
                            st.success(f"**正解:** {q.get('answer', '')}")
                            st.write(f"**根拠:** {q.get('explanation', '')}")
                        st.write("")
                else: st.info("問題がありません。")
            except: st.error("データの読み込み失敗")
    else: st.info("テスト問題がありません。")