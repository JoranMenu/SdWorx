SYSTEM = """You are BridgePoint, an assistant at SD Worx (payroll & HR services) that runs
exit interviews with colleagues who are leaving. Your goal is to capture tacit knowledge that is
NOT in the knowledge base yet, so that a successor who never meets the leaver can take over
smoothly. You are warm, respectful and concise. You ask ONE question at a time, and you always
push for specifics: concrete examples, frequency, who is involved, where the file or mail lives,
exact steps and deadlines."""

CATEGORIES = [
    "Abbreviations & internal jargon",
    "Recurring manual work & Excel files",
    "Frequently answered emails & questions not in the KB",
    "Contacts & stakeholders",
    "Workarounds & known issues",
    "Where files and information live",
    "Ongoing work & deadlines",
    "Conflicting document versions & which one is correct",
]

# ---------- (a) interview plan ----------
PLAN_PROMPT = """Prepare an exit interview plan.

LEAVER: {name} - {role}

ROLE DESCRIPTION:
{role_description}

TOPICS THE MANAGER WANTS COVERED:
{manager_topics}

PRE-INTERVIEW SURVEY (what the leaver does repeatedly):
{survey}

ABBREVIATIONS IN THE LEAVER'S DOCUMENTS THAT ARE NOT IN THE OFFICIAL GLOSSARY:
{unknown_terms}

VAGUE REFERENCES FOUND IN THE DOCUMENTS:
{vague_refs}

KNOWLEDGE BASE DOCUMENTS OWNED BY THE LEAVER:
{docs}

Find the knowledge gaps a successor would hit. Abbreviations are only ONE of them: also look
for recurring manual work (Excel files the leaver maintains, reports), emails/questions the
leaver answers often that are not documented, key contacts and stakeholders, informal
workarounds, where files live (SharePoint, Teams, mailbox, personal drive), and ongoing work
with deadlines. Group the unknown abbreviations into one or two topics instead of one per term.

The documents are real working files (Excel sheets, Word notes, mail threads, PDFs, macros), often
mixing Dutch and English. Pay special attention to: several versions of the same document that
contradict each other (_final, _new, v2.0, KOPIE (2)...), notes and '??' left in cells, magic
numbers without a source, files or macros that only exist on the leaver's laptop, hard-coded
exceptions for specific people, and contacts who have left. Ask which version or value is correct
and why. Write the questions in English, but quote file names, tabs and codes exactly.

Create 7 to 9 topics covering these categories where relevant: {categories}.
Order them from most to least critical for the successor. Each topic has an opening question
and 1-2 backup questions. Questions must be specific to this person's documents and work
(mention the file, client segment, tool or abbreviation by name)."""

PLAN_SCHEMA = """{"topics": [{"title": "short title", "category": "one of the categories",
"why": "one sentence: which gap this closes, cite the document if relevant",
"questions": ["opening question", "backup question", "backup question"]}]}"""

# ---------- (b) interviewer ----------
TURN_PROMPT = """You are interviewing {name} ({role}) in an exit interview.

INTERVIEW PLAN (id [status] title (category): planned questions):
{plan}

RECENT CONVERSATION:
{history}

CURRENT TOPIC: {current_id} - {current_title}
NEXT OPEN TOPIC: {next_open}

{answer}

Your job for this turn:
1. Transcribe the leaver's answer (verbatim if it is audio, clean up filler words).
2. Decide if the CURRENT topic is sufficiently covered: could a successor act on it without
   asking anyone? That requires concrete steps, names, locations, frequencies or deadlines.
   If something is vague ("the usual file", "I just know", an unexplained abbreviation, a
   person without a role), it is NOT covered yet.
3. If not covered: ask ONE concrete follow-up about the most important missing detail
   (example, frequency, who, where the file lives, what goes wrong). Briefly acknowledge the answer first.
4. If covered: thank them in a few words and ask the opening question of the NEXT OPEN TOPIC
   (adapt it naturally). If there is no next topic, next_question is a short closing thank-you.
5. If the answer reveals an important area that is not in the plan at all, add it to new_topics.
6. Collect any abbreviation or jargon the leaver explained in new_terms.
{force}"""

TURN_SCHEMA = """{"transcript": "the leaver's answer", "topic_id": "id of the current topic",
"topic_covered": true, "next_question": "the next question to ask",
"new_topics": [{"title": "...", "category": "...", "why": "...", "questions": ["opening question"]}],
"new_terms": [{"term": "...", "meaning": "..."}]}"""

ANSWER_AUDIO = "The leaver's answer to the last question is in the attached audio."
ANSWER_TEXT = 'The leaver answered the last question: "{answer}"'
FORCE_CLOSE = "\nThe follow-up limit for this topic is reached: set topic_covered to true and move on."

# ---------- (c) output documents ----------
DOC_TYPES = {
    "handover": (
        "Handover document",
        "Handover for the successor: role summary, key responsibilities, recurring tasks with "
        "frequency (table), key clients/accounts needing attention, tools and where files live, "
        "top tips from the leaver.",
    ),
    "glossary": (
        "Glossary additions",
        "Markdown table (Term | Meaning | Where it is used). Only terms NOT in the existing glossary. "
        "Unknown terms that were never explained get meaning 'TO CONFIRM'.",
    ),
    "howto": (
        "How-to guides",
        "Step-by-step how-to guides for each recurring task, Excel file and workaround described. "
        "Per guide: purpose, frequency/trigger, prerequisites, numbered steps, pitfalls.",
    ),
    "faq": (
        "FAQ for the successor",
        "FAQ based on the questions and emails the leaver used to answer. Format: '### Q: ...' then "
        "the answer, and 'Usually asked by: ...' when known.",
    ),
    "contacts": (
        "Who to ask for what",
        "Markdown table (Topic | Person / team | How to reach | Notes) with every contact mentioned.",
    ),
    "open_items": (
        "Open items & deadlines",
        "Markdown table (Item | Status | Deadline | Next step | Stakeholder), sorted by deadline, "
        "plus a short list of recurring deadlines (monthly/quarterly/yearly).",
    ),
}

DOC_PROMPT = """Write a knowledge base document based on an exit interview.

LEAVER: {name} - {role}
DOCUMENT: {doc_title}
INSTRUCTIONS: {doc_instructions}

EXISTING GLOSSARY:
{glossary}

TERMS EXPLAINED DURING THE INTERVIEW:
{terms}

LEAVER'S EXISTING KB DOCUMENTS:
{docs}

INTERVIEW TRANSCRIPT:
{transcript}

Rules: only use information from the transcript and documents, never invent facts. Where
information is missing write "TO CONFIRM with team lead". Clear English markdown, no code fences
(the source documents may be in Dutch: translate, but keep file names, tabs and codes as they are).
Where the leaver said which version of a document or value is correct, state it explicitly.
Start with exactly this front matter:
---
title: {doc_title} - {name}
source: exit interview
author: {name}
date: {date}
type: {kind}
---"""
