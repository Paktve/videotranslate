FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir requests edge-tts

COPY container_src/server.py /app/server.py

EXPOSE 8080

CMD ["python", "/app/server.py"] 
