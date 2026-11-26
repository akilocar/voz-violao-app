# Voz e Violão

App web que recebe um vídeo de voz e violão gravado no celular e devolve o mesmo vídeo com som de estúdio e imagem mais limpa, **sem recriar a gravação**. A ideia é soar como a pessoa num dia bom, não como uma voz de IA.

## O que o processamento faz

**Áudio** (`processor/melhorar.py`)
1. Separa voz e violão com o modelo MDX-Net Voc FT (onnx). O violão é "mistura menos voz", então nada se perde.
2. **Voz**: afinação sutil (`afinar.py`: corrige só as notas que ficam fora mais de 12 cents, puxando 70% do desvio, sem mexer no vibrato, com ressíntese PSOLA que preserva o timbre), redução de ruído, EQ, de-esser, dois compressores, limiter e reverb de estúdio com pré-delay.
3. **Violão**: EQ (tira o "boom", realça as cordas), compressão leve e reverb curto.
4. **Mix**: compressor de barramento, -14 LUFS e limiter de pico em -1,5 dB.

**Imagem** (FFmpeg): estabilização, 1080p, redução de granulado, contraste, cor quente, nitidez e vinheta leve.

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
