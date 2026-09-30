import os
from google import genai

key = os.environ["GEMINI_API_KEY"]
model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

for label, kwargs in [("gemini-api", {}), ("vertex-express", {"vertexai": True})]:
    try:
        c = genai.Client(api_key=key, **kwargs)
        print(label, "OK:", c.models.generate_content(model=model, contents="Say hi in 3 words").text.strip())
    except Exception as e:
        print(label, "FAIL:", str(e)[:300])
