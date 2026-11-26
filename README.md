# Voz e Violão

App web que recebe um vídeo de voz e violão gravado no celular e devolve o mesmo vídeo com som de estúdio e imagem mais limpa, **sem recriar a gravação**. A ideia é soar como a pessoa num dia bom, não como uma voz de IA.

## O que o processamento faz

**Áudio** (`processor/melhorar.py`, com [pedalboard](https://github.com/spotify/pedalboard))
- Passa-alta em 75 Hz e redução de ruído parcial, para não "aguar" a voz
- EQ: tira o som embolado de celular (220 e 450 Hz), dá presença (3,2 kHz) e brilho (9 kHz)
- Compressão leve, reverb de sala curto e limitador
- Volume final em -14 LUFS, o padrão de Instagram e YouTube

**Imagem** (FFmpeg)
- Estabilização (vidstab), redução de granulado (hqdn3d) e ajuste leve de contraste, cor e nitidez

Tudo é regulado por uma **intensidade** de 0 a 1 escolhida no site.

## Arquitetura (Cloudflare)

```
navegador ──► Worker (src/index.ts) ──► R2: originais/ e resultados/
                 │   upload em partes de 50 MB
                 ├─► D1: tabela jobs (status do pedido)
                 └─► Queue ──► Worker (consumidor) ──► Container "Processor"
                                                       (FFmpeg + Python, 4 vCPU)
```

- `public/` é o site (HTML estático).
- `src/index.ts` tem a API, o consumidor da fila e a classe do container.
- `processor/` tem a imagem Docker do processamento.

## Deploy automático

O Worker está ligado a este repositório pelo **Workers Builds** do Cloudflare. Todo push na `main` roda `npm run deploy:cf`, que cria a fila (se faltar), aplica as migrações do D1 e publica Worker e container. O GitHub Actions (`.github/workflows/checagem.yml`) só checa o código e testa o processamento.

Containers e Queues exigem o plano Workers Paid.

## Rodar o processamento localmente

```sh
pip install -r processor/requirements.txt   # precisa de ffmpeg instalado
python processor/melhorar.py entrada.mp4 saida.mp4 --intensidade 0.6
```

## Próximos passos

1. Afinação leve: separar voz e violão e corrigir só as notas claramente fora, preservando vibrato.
2. Contas de usuário e pagamento.
