FROM python:3.12-slim

# FFmpeg do Debian já vem com vidstab (estabilização) e libx264.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY melhorar.py server.py ./

ENV PORT=8080
EXPOSE 8080
CMD ["python", "server.py"]
