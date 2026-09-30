# BridgePoint - SD Worx exit interview assistant (PoC)

Prepares and guides the exit interview with a leaving colleague, then turns it into
knowledge base documents (handover, glossary, how-tos, FAQ, contacts, open items).

## Run

```powershell
pip install -r requirements.txt
$env:CURSOR_API_KEY="crsr_..."   # Cursor Dashboard -> Integrations
python -m streamlit run app.py
```

Optional: `CURSOR_MODEL` (default `composer-2.5`), also editable in the sidebar.
Open the app in Chrome or Edge: voice input uses the browser's speech recognition.

## Demo flow

1. **Prepare** - form is prefilled from `leaver_profile.md`; the app reads the leaver's files in `kb/`
   (xlsx, docx, pdf, csv, txt, md - more can be uploaded in the app), flags unexplained abbreviations
   (anything not in an optional `kb/glossary.md`) and vague references, then the LLM builds the plan.
2. **Interview** - answer by voice or text; the LLM asks follow-ups and tracks topic coverage.
3. **Generate** - documents with front matter are saved to `output/` and downloadable as a zip.
