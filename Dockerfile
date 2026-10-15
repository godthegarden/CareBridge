FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py carebridge.json ./
COPY carebridge ./carebridge
COPY samples ./samples
COPY .streamlit/config.toml ./.streamlit/config.toml
COPY start.sh ./
CMD ["sh", "start.sh"]
