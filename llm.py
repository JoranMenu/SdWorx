import datetime
import json
import os

from google import genai
from google.genai import types

import prompts

PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "qwiklabs-gcp-04-6de79205cf17")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "europe-west1")
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

_client = None


def client():
    global _client
    if _client is None:
        if os.getenv("GEMINI_API_KEY"):
            _client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        elif os.getenv("GOOGLE_ACCESS_TOKEN"):
            from google.oauth2.credentials import Credentials
            _client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION,
                                   credentials=Credentials(os.environ["GOOGLE_ACCESS_TOKEN"]))
        else:
            _client = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)
    return _client


def _call(prompt, audio_bytes=None, json_mode=False, temperature=0.4):
    parts = [types.Part.from_text(text=prompt)]
    if audio_bytes:
        parts.append(types.Part.from_bytes(data=audio_bytes, mime_type="audio/wav"))
    resp = client().models.generate_content(
        model=MODEL,
        contents=[types.Content(role="user", parts=parts)],
        config=types.GenerateContentConfig(
            system_instruction=prompts.SYSTEM,
            temperature=temperature,
            response_mime_type="application/json" if json_mode else None,
        ),
    )
    return (resp.text or "").strip()


def generate_text(prompt, temperature=0.4):
    return _call(prompt, temperature=temperature)


def generate_json(prompt, schema="", audio_bytes=None, temperature=0.4):
    if schema:
        prompt = f"{prompt}\n\nReturn ONLY JSON with this shape:\n{schema}"
    text = _call(prompt, audio_bytes, json_mode=True, temperature=temperature)
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    return json.loads(text)


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
