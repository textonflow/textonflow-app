FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    fonts-liberation \
    fontconfig \
    fonts-noto-color-emoji \
    fonts-noto \
    && fc-cache -fv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p static/temp

EXPOSE ${PORT:-8000}

CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}

