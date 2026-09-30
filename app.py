import io
import pathlib
import re
import zipfile

import streamlit as st

import llm
import prompts

ROOT = pathlib.Path(__file__).parent
KB = ROOT / "kb"
OUT = ROOT / "output"
MAX_FOLLOWUPS = 3
GENERIC_TERMS = {"EUR", "XLS", "CSV", "PDF", "HR", "IT", "BE", "YTD", "OK", "TO", "CONFIRM", "BY"}
VAGUE_PATTERNS = [r"the usual [a-z]+", r"known workaround", r"my own file", r"Wave \d"]

st.set_page_config(page_title="KnowledgeBridge - SD Worx", page_icon="🤝", layout="wide")


# ---------- helpers ----------
def split_front_matter(text):
    if text.startswith("---"):
        _, fm, body = text.split("---", 2)
        meta = dict(line.split(":", 1) for line in fm.strip().splitlines() if ":" in line)
        return {k.strip(): v.strip() for k, v in meta.items()}, body
    return {}, text


def load_kb():
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(KB.glob("*.md"))}


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
        "owned": re.findall(r"[\w-]+\.md", sections.get("owned documents", "")),
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
    for name, text in docs.items():
        body = split_front_matter(text)[1]
        for tok in re.findall(r"\b[A-Z][A-Z0-9-]{1,5}\b", body):
            tok = tok.rstrip("-")
            if len(tok) < 2 or tok in GENERIC_TERMS or tok in known:
                continue
            unknown.setdefault(tok, set()).add(name)
        for pat in VAGUE_PATTERNS:
            for phrase in re.findall(pat, body):
                vague.setdefault(phrase.strip(), set()).add(name)
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
kb = load_kb()
glossary = kb.get("glossary.md", "")
kb_docs = [n for n in kb if not n.startswith("_") and n != "glossary.md"]
default_profile = load_profile(kb.get("_leaver_profile.md", ""))

s = st.session_state
for k, v in dict(stage="Prepare", plan=[], current_id=None, followups={}, messages=[], terms=[],
                 current_q="", turn=0, done=False, outputs={}, gaps=None, profile=default_profile,
                 docs={}).items():
    s.setdefault(k, v)

# ---------- sidebar ----------
with st.sidebar:
    st.title("🤝 KnowledgeBridge")
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
    llm.MODEL = st.text_input("Gemini model", llm.MODEL)
    demo = st.toggle("Demo mode (simulate answers)", value=False)
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
    selected = st.multiselect(
        "Knowledge base documents owned by the leaver", kb_docs, default=[d for d in p["owned"] if d in kb_docs]
    )

    docs = {n: kb[n] for n in selected}
    unknown, vague = find_gaps(docs, glossary)
    g1, g2 = st.columns(2)
    with g1:
        st.markdown(f"**🔎 {len(unknown)} abbreviations not in the glossary**")
        st.markdown(" ".join(f"`{t}`" for t in sorted(unknown)) or "_none_")
    with g2:
        st.markdown(f"**🌫️ {len(vague)} vague references**")
        st.markdown("\n".join(f"- *\"{v}\"* ({', '.join(sorted(f))})" for v, f in vague.items()) or "_none_")

    if st.button("Build interview plan", type="primary"):
        with st.spinner("Gemini is analysing the documents and preparing questions..."):
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
        audio = st.audio_input("🎙️ Answer by voice", key=f"audio_{s.turn}")
        text = st.chat_input("...or type your answer")
        c1, c2, c3 = st.columns(3)
        simulate = c1.button("🎭 Simulate answer", disabled=not demo)
        if c2.button("⏭️ Skip topic"):
            topic(s.current_id).update(covered=True, skipped=True)
            move_to_next()
            s.turn += 1
            st.rerun()
        if c3.button("🏁 Finish interview"):
            s.done = True
            st.rerun()

        if audio is not None:
            with st.spinner("Listening..."):
                handle_answer(audio=audio.getvalue())
            st.rerun()
        elif text:
            with st.spinner("Thinking..."):
                handle_answer(text=text)
            st.rerun()
        elif simulate:
            with st.spinner("Simulating the leaver..."):
                ans = safe("Simulation", llm.simulate_answer, s.current_q, s.messages, s.profile, docs_block(s.docs))
                if ans:
                    handle_answer(text=ans)
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
