# KnowledgeBridge - SD Worx exit interview assistant (PoC)

Prepares and guides the exit interview with a leaving colleague, then turns it into
knowledge base documents (handover, glossary, how-tos, FAQ, contacts, open items).

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
gcloud auth application-default login
gcloud auth application-default set-quota-project qwiklabs-gcp-04-6de79205cf17
.\.venv\Scripts\streamlit run app.py
```

Optional environment variables: `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION` (default
`europe-west1`), `GEMINI_MODEL` (default `gemini-2.5-flash`), or `GEMINI_API_KEY` to use an
AI Studio key instead of Vertex AI.

## Demo flow

1. **Prepare** - form is prefilled from `kb/_leaver_profile.md`; the app scans the leaver's KB
   docs for abbreviations missing from `kb/glossary.md` and vague references, then Gemini builds the plan.
2. **Interview** - answer by voice (microphone) or text; Gemini transcribes, asks follow-ups
   and tracks topic coverage. Toggle *Demo mode* in the sidebar to let Gemini simulate the leaver.
3. **Generate** - documents with front matter are saved to `output/` and downloadable as a zip.
