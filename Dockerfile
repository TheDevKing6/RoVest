FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Copies every .py file (roblox_gem_finder.py and queries.py) plus the web page.
COPY *.py index.html ./
ENV PORT=8080 DATA_DIR=/data
# One worker on purpose: scan results live in memory, so extra processes would each hold different data.
CMD gunicorn roblox_gem_finder:app --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:${PORT}
