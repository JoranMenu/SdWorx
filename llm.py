import datetime
import json
import os
import pathlib

import queue
import sys
import threading

from cursor_sdk import Agent, AgentOptions, LocalAgentOptions
from cursor_sdk import _bridge

import prompts


def _read_discovery_threaded(process, timeout):
    # cursor_sdk polls the bridge's stderr pipe with select(), which only accepts sockets on Windows.
    lines = queue.Queue()

    def pump():
        for line in process.stderr:
            lines.put(line)
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    seen = []
    try:
        while True:
            line = lines.get(timeout=timeout)
            if line is None:
                raise _bridge.CursorSDKError(f"Bridge exited before discovery: {''.join(seen)}")
            seen.append(line)
            discovery = _bridge.parse_discovery_line(line)
            if discovery is not None:
                return discovery
    except queue.Empty:
        raise _bridge.CursorSDKError("Timed out waiting for bridge discovery")


if sys.platform == "win32":
    _bridge._read_discovery = _read_discovery_threaded

MODEL = os.getenv("CURSOR_MODEL", "composer-2.5")
# The agent can use tools, so it runs in an empty folder where it has nothing to touch.
SANDBOX = pathlib.Path(__file__).parent / ".agent_sandbox"

NO_TOOLS = (
    "You are used as a plain text-completion API. Do NOT use any tools, do NOT read, search or write files, "
    "do NOT run commands. Reply immediately with only the requested output, no preamble."
)


def _call(prompt, audio_bytes=None, json_mode=False, temperature=0.4):
    SANDBOX.mkdir(exist_ok=True)
    full = f"{NO_TOOLS}\n\n{prompts.SYSTEM}\n\n{prompt}"
    if json_mode:
        full += "\n\nOutput raw JSON only, no markdown fences."
    result = Agent.prompt(
        full,
        AgentOptions(
            api_key=os.environ["CURSOR_API_KEY"],
            model=MODEL,
            local=LocalAgentOptions(cwd=str(SANDBOX)),
        ),
    )
    if result.status != "finished":
        raise RuntimeError(f"Cursor run {result.id} ended with status {result.status}")
    return (result.result or "").strip()


def generate_text(prompt, temperature=0.4):
    return _call(prompt, temperature=temperature)


def generate_json(prompt, schema="", audio_bytes=None, temperature=0.4):
    if schema:
        prompt = f"{prompt}\n\nReturn ONLY JSON with this shape:\n{schema}"
    text = _call(prompt, audio_bytes, json_mode=True, temperature=temperature)
    return json.loads(text[text.index("{"): text.rindex("}") + 1])


def build_plan(profile, docs, unknown_terms, vague_refs):
    prompt = prompts.PLAN_PROMPT.format(
        name=profile["name"],
        role=profile["role"],
        role_description=profile["role_description"],
        manager_topics=profile["manager_topics"],
        survey=profile["survey"],
        unknown_terms=", ".join(unknown_terms) or "(none)",
        vague_refs="; ".join(vague_refs) or "(none)",
        docs=docs,
        categories=", ".join(prompts.CATEGORIES),
    )
    topics = generate_json(prompt, prompts.PLAN_SCHEMA)["topics"]
    for i, t in enumerate(topics):
        t["id"] = f"T{i + 1}"
        t["covered"] = False
    return topics


def interview_turn(history, plan, current_id, answer_text=None, audio_bytes=None, profile=None, force_close=False):
    """history: list of {role, text, topic_id}. Returns dict with transcript, topic_id,
    topic_covered, next_question, new_topics, new_terms."""
    current = next(t for t in plan if t["id"] == current_id)
    remaining = [t for t in plan if not t["covered"] and t["id"] != current_id]
    plan_txt = "\n".join(
        f"- {t['id']} [{'covered' if t['covered'] else 'open'}] {t['title']} ({t.get('category', '')}): "
        + " | ".join(t.get("questions", []))
        for t in plan
    )
    hist_txt = "\n".join(
        f"{'Interviewer' if m['role'] == 'assistant' else 'Leaver'} [{m.get('topic_id', '')}]: {m['text']}"
        for m in history[-16:]
    )
    prompt = prompts.TURN_PROMPT.format(
        name=profile["name"],
        role=profile["role"],
        plan=plan_txt,
        history=hist_txt or "(none)",
        current_id=current_id,
        current_title=current["title"],
        next_open=f"{remaining[0]['id']} {remaining[0]['title']}" if remaining else "(none - this is the last topic)",
        answer=prompts.ANSWER_AUDIO if audio_bytes else prompts.ANSWER_TEXT.format(answer=answer_text),
        force=prompts.FORCE_CLOSE if force_close else "",
    )
    return generate_json(prompt, prompts.TURN_SCHEMA, audio_bytes=audio_bytes)


def simulate_answer(question, history, profile, docs):
    hist_txt = "\n".join(
        f"{'Interviewer' if m['role'] == 'assistant' else 'Leaver'}: {m['text']}" for m in history[-8:]
    )
    return generate_text(
        prompts.SIMULATE_PROMPT.format(
            name=profile["name"], role=profile["role"], docs=docs, history=hist_txt, question=question
        ),
        temperature=0.9,
    )


def generate_doc(kind, transcript, context):
    title, instructions = prompts.DOC_TYPES[kind]
    return generate_text(
        prompts.DOC_PROMPT.format(
            name=context["name"],
            role=context["role"],
            doc_title=title,
            doc_instructions=instructions,
            glossary=context["glossary"],
            terms=context["terms"],
            docs=context["docs"],
            transcript=transcript,
            date=datetime.date.today().isoformat(),
            kind=kind,
        ),
        temperature=0.2,
    )
