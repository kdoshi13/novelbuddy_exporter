Novel Chapter Exporter

Local script:
python scrape_io.py

Render website:
1. Push this folder to a GitHub repo.
2. In Render, choose New > Web Service and connect the repo.
3. Use these settings:
   Runtime: Python
   Build Command: pip install -r requirements.txt
   Start Command: gunicorn app:app --bind 0.0.0.0:$PORT

Render can also read render.yaml automatically.

The website uses:
- app.py for the Render Flask app and API routes
- index.html, styles.css, and app.js for the browser UI
- scrape_io.py for NovelBuddy scraping and TXT/DOCX/PDF export helpers

Use "Start 100-chapter batch" for manual batches, or "Download all chapters" to keep collecting until the novel ends. On Render free tier, the browser tab must stay open while "Download all chapters" is running.

Downloaded chapter folders, index CSVs, debug JSON, resume JSON, and __pycache__ files are ignored so deploys only include app code.
