FROM python:3.12-slim

# FFmpeg do Debian já vem com vidstab (estabilização) e libx264.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg curl build-essential \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Modelo de separação de voz (MDX-Net Voc FT), baixado na construção da imagem.
ENV PASTA_MODELOS=/app/modelos
RUN mkdir -p $PASTA_MODELOS && curl -fsSL -o $PASTA_MODELOS/UVR-MDX-NET-Voc_FT.onnx \
    https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/UVR-MDX-NET-Voc_FT.onnx

COPY melhorar.py afinar.py server.py ./

ENV PORT=8080
EXPOSE 8080
CMD ["python", "server.py"]
