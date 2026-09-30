import io
import pathlib
import re
import zipfile

import streamlit as st
from streamlit_mic_recorder import speech_to_text

import kb_loader
import llm
import prompts

ROOT = pathlib.Path(__file__).parent
KB = ROOT / "kb"
OUT = ROOT / "output"
MAX_FOLLOWUPS = 3
GENERIC_TERMS = {
    "EUR", "XLS", "XLSX", "CSV", "PDF", "HR", "IT", "BE", "YTD", "OK", "OKE", "TO", "CONFIRM", "BY", "DO", "NOT",
    "NIET", "DEF", "FINAL", "DRAFT", "NEW", "NIEUW", "TODO", "VAN", "AAN", "CC", "RE", "FW", "NV", "BIS", "OUD",
    "LIJST", "KOPIE", "TARIEVEN", "LOONKALENDER", "AFWIJKINGEN", "PERSONEEL", "CONTROLE", "LONCODES", "VERGETEN",
    "WIJZIGEN", "DOORSTUREN", "TOUCH", "EXPORT", "TRUE", "FALSE", "PAD", "MAILTO", "NB", "EN", "OF", "IS", "ALLE",
    "NOOIT", "ZIE", "ASK", "OLD", "ONLY", "BEFORE", "AFTER", "NEVER", "EERST", "NA", "MET", "VOOR", "AF", "ZONDER",
    "REF", "NL",
}
VAGUE_PATTERNS = [
    r"\bzie (?:mail|lijst|macro|vorige map|personeelsdossier|TV|Marleen|Kevin)[^|\n.]{0,20}",
    r"\b(?:ask|vraag|bel|call) (?:Kevin|Marleen|Sofie|Jeroen|Hans|J\.)[^|\n.]{0,25}",
    r"[^|\n]{3,40}\?\?",
    r"\b(?:TODO|nagaan|niet af|to be reviewed|not sure|niet 100%)[^|\n]{0,30}",
    r"\b(?:the usual|my own|known workaround)[^|\n.]{0,20}",
]
MAX_VAGUE = 40

st.set_page_config(page_title="BridgePoint - SD Worx", page_icon="🤝", layout="wide")


# ---------- helpers ----------
def split_front_matter(text):
    if text.startswith("---"):
        _, fm, body = text.split("---", 2)
        meta = dict(line.split(":", 1) for line in fm.strip().splitlines() if ":" in line)
        return {k.strip(): v.strip() for k, v in meta.items()}, body
    return {}, text


@st.cache_data
def load_kb():
    return kb_loader.load_folder(KB)


def load_profile(text):
    meta, body = split_front_matter(text)
    sections = {}
    for chunk in re.split(r"^## ", body, flags=re.MULTILINE)[1:]:
        title, _, content = chunk.partition("\n")
        sections[title.strip().lower()] = content.strip()
    return {
        "name": meta.get("name", ""),
        "role": meta.get("role", ""),
        "role_description": sections.get("role description", ""),
        "manager_topics": sections.get("manager topics", ""),
        "survey": sections.get("pre-interview survey", ""),
        "owned": sections.get("owned documents", "").strip(),
    }


def glossary_terms(text):
    terms = set()
    for line in text.splitlines():
        if line.startswith("|") and not line.startswith("|--"):
            terms.update(t.strip().upper() for t in line.strip("|").split("|")[0].split("/"))
    return terms


def find_gaps(docs, glossary):
    known = glossary_terms(glossary)
    unknown, vague = {}, {}
    # a caps token that also occurs as "Peeters" or "loon" is a name or plain word, not an abbreviation
    plain_words = {w.upper() for text in docs.values() for w in re.findall(r"\b[A-Za-z][a-z]+\b", text)}
    known |= plain_words
    for name, text in docs.items():
        body = f"{pathlib.Path(name).stem.replace('_', ' ')}\n{split_front_matter(text)[1]}"
        for tok in re.findall(r"\b[A-Z][A-Z0-9_-]{1,11}\b", body):
            tok = tok.rstrip("-_")
            # plain codes like L999, E14, M2 are too many to list individually
            if len(tok) < 2 or tok in GENERIC_TERMS or tok in known or re.fullmatch(r"[A-Z]\d+", tok):
                continue
            unknown.setdefault(tok, set()).add(name)
        for pat in VAGUE_PATTERNS:
            for phrase in re.findall(pat, body, flags=re.IGNORECASE):
                if len(vague) < MAX_VAGUE:
                    vague.setdefault(phrase.strip(" |"), set()).add(name)
    return unknown, vague


def docs_block(docs):
    return "\n\n".join(f"### {n}\n{split_front_matter(t)[1].strip()}" for n, t in docs.items()) or "(none)"


def safe(label, fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as e:  # demo-proof: never crash the UI
        st.session_state["last_error"] = f"{label} failed: {e}"
        st.error(st.session_state["last_error"])
        return None


def topic(tid):
    return next((t for t in s.plan if t["id"] == tid), None)


def first_open():
    return next((t for t in s.plan if not t["covered"]), None)


def say(text, tid):
    s.messages.append({"role": "assistant", "text": text, "topic_id": tid})
    s.current_q = text


def move_to_next(question=None):
    nxt = first_open()
    if nxt is None:
        s.done = True
        say(question or f"That's everything, thank you {s.profile['name'].split()[0]}! Your successor will be grateful.", None)
        return
    s.current_id = nxt["id"]
    say(question or nxt["questions"][0], nxt["id"])


def handle_answer(text=None, audio=None):
    tid = s.current_id
    res = safe(
        "Interview turn",
        llm.interview_turn,
        s.messages, s.plan, tid, answer_text=text, audio_bytes=audio, profile=s.profile,
        force_close=s.followups.get(tid, 0) >= MAX_FOLLOWUPS,
    )
    if res is None:
        s.turn += 1
        return
    s.messages.append({"role": "user", "text": res.get("transcript") or text or "(inaudible)", "topic_id": tid})
    s.terms.extend(res.get("new_terms") or [])
    for nt in res.get("new_topics") or []:
        nt.update(id=f"T{len(s.plan) + 1}", covered=False, added=True)
        nt.setdefault("questions", [f"Can you tell me more about {nt.get('title', 'this')}?"])
        s.plan.append(nt)
    if res.get("topic_covered") or s.followups.get(tid, 0) >= MAX_FOLLOWUPS:
        topic(tid)["covered"] = True
        move_to_next(res.get("next_question"))
    else:
        s.followups[tid] = s.followups.get(tid, 0) + 1
        say(res.get("next_question") or "Could you give a concrete example?", tid)
    s.turn += 1


def transcript_md():
    lines, last = [], "none"
    for m in s.messages:
        if m["topic_id"] != last and m["topic_id"]:
            t = topic(m["topic_id"])
            lines.append(f"\n## {t['title']}\n")
            last = m["topic_id"]
        who = "Interviewer" if m["role"] == "assistant" else s.profile["name"]
        lines.append(f"**{who}:** {m['text']}\n")
    return "\n".join(lines)


# ---------- state ----------
profile_file = ROOT / "leaver_profile.md"
default_profile = load_profile(profile_file.read_text(encoding="utf-8") if profile_file.exists() else "")

s = st.session_state
for k, v in dict(stage="Prepare", plan=[], current_id=None, followups={}, messages=[], terms=[],
                 current_q="", turn=0, done=False, outputs={}, gaps=None, profile=default_profile,
                 docs={}, uploads={}).items():
    s.setdefault(k, v)

kb = {**load_kb(), **s.uploads}
glossary = kb.get("glossary.md", "")
kb_docs = [n for n in kb if n != "glossary.md"]

# ---------- sidebar ----------
with st.sidebar:
    st.title("🤝 BridgePoint")
    st.caption("Turn a leaving colleague's know-how into knowledge base documents.")
    for i, step in enumerate(["Prepare", "Interview", "Generate"], 1):
        st.markdown(f"**▶ {i}. {step}**" if s.stage == step else f"&nbsp;&nbsp;&nbsp;{i}. {step}")
    if s.plan:
        st.divider()
        covered = sum(t["covered"] for t in s.plan)
        st.subheader("Topic coverage")
        st.progress(covered / len(s.plan), f"{covered}/{len(s.plan)} topics")
        for t in s.plan:
            icon = "⏭️" if t.get("skipped") else "✅" if t["covered"] else "▶️" if t["id"] == s.current_id and s.stage == "Interview" else "⬜"
            st.markdown(f"{icon} {t['title']}" + (" *(new)*" if t.get("added") else ""))
    st.divider()
    llm.MODEL = st.text_input("Cursor model", llm.MODEL)
    speech_lang = st.selectbox("Speech language", ["en-US", "nl-BE", "fr-BE"])
    if st.button("Reset"):
        s.clear()
        st.rerun()

# ---------- 1. Prepare ----------
if s.stage == "Prepare":
    st.header("1 · Prepare the exit interview")
    p = s.profile
    c1, c2 = st.columns(2)
    with c1:
        p["name"] = st.text_input("Leaving colleague", p["name"])
        p["role"] = st.text_input("Role", p["role"])
        p["role_description"] = st.text_area("Role description", p["role_description"], height=140)
    with c2:
        p["manager_topics"] = st.text_area("Topics the manager wants covered", p["manager_topics"], height=110)
        p["survey"] = st.text_area("Pre-interview survey: what do you do repeatedly?", p["survey"], height=110)
    uploaded = st.file_uploader(
        "Add the leaver's own files (Excel, Word, PDF, CSV, text)", accept_multiple_files=True,
        type=[ext.lstrip(".") for ext in kb_loader.SUPPORTED],
    )
    new = [f for f in uploaded or [] if f.name not in s.uploads]
    if new:
        for f in new:
            s.uploads[f.name] = kb_loader.extract_bytes(f.name, f.getvalue())
        st.rerun()
    owned = kb_docs if p["owned"].lower() == "all" else [d for d in kb_docs if d in p["owned"]]
    selected = st.multiselect(
        f"Documents of the leaver ({len(kb_docs)} in the knowledge base)", kb_docs,
        default=owned + [n for n in s.uploads if n not in owned],
    )

    docs = {n: kb[n] for n in selected}
    unknown, vague = find_gaps(docs, glossary)
    g1, g2 = st.columns(2)
    with g1:
        st.markdown(f"**🔎 {len(unknown)} unexplained abbreviations & codes**")
        by_spread = sorted(unknown, key=lambda t: (-len(unknown[t]), t))
        st.markdown(" ".join(f"`{t}`" for t in by_spread) or "_none_")
    with g2:
        st.markdown(f"**🌫️ {len(vague)} vague references & open questions**")
        with st.container(height=260):
            st.markdown("\n".join(f"- *\"{v}\"* ({', '.join(sorted(f))})" for v, f in vague.items()) or "_none_")

    if st.button("Build interview plan", type="primary"):
        with st.spinner("Analysing the documents and preparing questions..."):
            plan = safe("Building the plan", llm.build_plan, p, docs_block(docs), sorted(unknown), list(vague))
        if plan:
            s.update(plan=plan, docs=docs, gaps=(unknown, vague))

    if s.plan:
        st.subheader("Proposed interview plan")
        for t in s.plan:
            with st.expander(f"{t['id']} · {t['title']}  —  {t.get('category', '')}"):
                st.caption(t.get("why", ""))
                t["questions"] = [q for q in st.text_area(
                    "Questions (one per line, editable)", "\n".join(t["questions"]), key=f"q_{t['id']}"
                ).splitlines() if q.strip()] or t["questions"]
        if st.button("Start interview ▶", type="primary"):
            s.stage = "Interview"
            say(f"Hi {p['name'].split()[0]}, thanks for taking the time! I'll walk you through "
                f"{len(s.plan)} topics so your successor can take over smoothly, even without meeting you. "
                "Answer by voice or text, and be as concrete as you can.", None)
            move_to_next()
            st.rerun()

# ---------- 2. Interview ----------
elif s.stage == "Interview":
    st.header(f"2 · Exit interview with {s.profile['name']}")
    if s.get("last_error"):
        st.error(s.pop("last_error") + " - please try again.")
    last_tid = "none"
    for m in s.messages:
        if m["role"] == "assistant" and m["topic_id"] and m["topic_id"] != last_tid:
            st.caption(f"📌 Topic: {topic(m['topic_id'])['title']}")
            last_tid = m["topic_id"]
        with st.chat_message(m["role"], avatar="🤝" if m["role"] == "assistant" else "🧑‍💼"):
            st.markdown(m["text"])

    if not s.done:
        spoken = speech_to_text(language=speech_lang, start_prompt="🎙️ Answer by voice",
                                stop_prompt="⏹️ Stop and send", just_once=True, key=f"stt_{s.turn}")
        text = st.chat_input("...or type your answer") or spoken
        c1, c2 = st.columns(2)
        if c1.button("⏭️ Skip topic"):
            topic(s.current_id).update(covered=True, skipped=True)
            move_to_next()
            s.turn += 1
            st.rerun()
        if c2.button("🏁 Finish interview"):
            s.done = True
            st.rerun()

        if text:
            with st.spinner("Thinking..."):
                handle_answer(text=text)
            st.rerun()
    elif st.button("Generate knowledge base documents ▶", type="primary"):
        s.stage = "Generate"
        st.rerun()

# ---------- 3. Generate ----------
elif s.stage == "Generate":
    st.header("3 · Knowledge base documents")
    transcript = transcript_md()
    if not s.outputs:
        ctx = dict(
            name=s.profile["name"], role=s.profile["role"], glossary=glossary, docs=docs_block(s.docs),
            terms="\n".join(f"- {t.get('term')}: {t.get('meaning')}" for t in s.terms) or "(none)",
        )
        bar = st.progress(0.0)
        for i, (kind, (title, _)) in enumerate(prompts.DOC_TYPES.items()):
            bar.progress(i / len(prompts.DOC_TYPES), f"Writing: {title}...")
            doc = safe(title, llm.generate_doc, kind, transcript, ctx)
            if doc:
                s.outputs[kind] = doc.removeprefix("```markdown").removeprefix("```").removesuffix("```").strip()
        bar.empty()
        slug = re.sub(r"\W+", "_", s.profile["name"].lower()).strip("_")
        OUT.mkdir(exist_ok=True)
        for kind, text in s.outputs.items():
            (OUT / f"{slug}_{kind}.md").write_text(text, encoding="utf-8")
        (OUT / f"{slug}_transcript.md").write_text(f"# Exit interview - {s.profile['name']}\n{transcript}", encoding="utf-8")

    slug = re.sub(r"\W+", "_", s.profile["name"].lower()).strip("_")
    files = {f"{slug}_{k}.md": v for k, v in s.outputs.items()}
    files[f"{slug}_transcript.md"] = f"# Exit interview - {s.profile['name']}\n{transcript}"
    st.success(f"{len(files)} documents saved to `{OUT}`")

    labels = [prompts.DOC_TYPES[k][0] for k in s.outputs] + ["Transcript"]
    for tab, text in zip(st.tabs(labels), files.values()):
        with tab:
            st.markdown(split_front_matter(text)[1] if text.startswith("---") else text)
            with st.expander("Raw markdown (with front matter)"):
                st.code(text, language="markdown")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for fname, text in files.items():
            z.writestr(fname, text)
    c1, c2 = st.columns(2)
    c1.download_button("⬇️ Download all (.zip)", buf.getvalue(), f"{slug}_handover.zip", type="primary")
    if c2.button("🔄 Regenerate"):
        s.outputs = {}
        st.rerun()
