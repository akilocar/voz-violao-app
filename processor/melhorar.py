#!/usr/bin/env python3
"""Melhora um vídeo de voz e violão com som de estúdio, sem recriar a gravação.

Uso: python melhorar.py entrada.mp4 saida.mp4 [--intensidade 0.7] [--afinacao 0.7]
                         [--sem-estabilizar] [--sem-separar]

Áudio
  1. Separa voz e violão (modelo MDX-Net, onnx). O violão é "mistura - voz",
     então nada some: o que o modelo não pegou continua no violão.
  2. Voz: afinação sutil (afinar.py), limpeza, EQ, de-esser, dois estágios de
     compressão, limiter próprio e reverb de estúdio.
  3. Violão: EQ, compressão leve e reverb curto.
  4. Mix: cola (compressor de barramento), limiter final e -14 LUFS.
Vídeo
  Estabilização, redução de granulado, cor e contraste, nitidez e 1080p.
"""
import argparse, os, subprocess, tempfile

import numpy as np
import noisereduce as nr
import pyloudnorm as pyln
import soundfile as sf
from pedalboard import (Compressor, HighpassFilter, HighShelfFilter,
                        LowShelfFilter, PeakFilter, Pedalboard, Reverb, LowpassFilter)

from afinar import afinar, referencia_cents

SR = 44100
AQUI = os.path.dirname(os.path.abspath(__file__))
MODELO = os.environ.get("MODELO_VOZ", "UVR-MDX-NET-Voc_FT.onnx")
PASTA_MODELOS = os.environ.get("PASTA_MODELOS", os.path.join(AQUI, "modelos"))


def run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def separar(wav, tmp):
    from audio_separator.separator import Separator
    sep = Separator(model_file_dir=PASTA_MODELOS, output_dir=tmp, output_format="WAV",
                    output_single_stem="Vocals")
    sep.load_model(model_filename=MODELO)
    arq = sep.separate(wav)[0]
    voz, _ = sf.read(arq if os.path.isabs(arq) else os.path.join(tmp, arq), always_2d=True)
    return voz.astype(np.float32)


def limitar(x, sr, teto_db, janela_ms=30.0):
    """Limiter de pico com antecipação: nenhum pico passa de teto_db.
    (O Limiter do pedalboard aplica ganho de compensação, por isso este.)"""
    from scipy.ndimage import minimum_filter1d, uniform_filter1d
    teto = 10 ** (teto_db / 20)
    pico = np.abs(x).max(axis=0)
    ganho = np.minimum(1.0, teto / np.maximum(pico, 1e-9))
    L = max(3, int(sr * janela_ms / 1000))
    ganho = minimum_filter1d(ganho, size=2 * L + 1, mode="nearest")
    ganho = uniform_filter1d(ganho, size=2 * L + 1, mode="nearest")
    return (x * ganho[None, :]).astype(np.float32)


def de_esser(x, sr, limiar_db=-28.0, max_reducao_db=8.0):
    """Abaixa o volume só nos 's' e 'ch' (energia entre 5 e 10 kHz)."""
    banda = Pedalboard([HighpassFilter(5000), LowpassFilter(10000)])(x, sr)
    env = np.sqrt(np.convolve((banda ** 2).mean(axis=0), np.ones(int(sr * 0.005)) / int(sr * 0.005), "same"))
    env_db = 20 * np.log10(env + 1e-9)
    gr_db = -np.clip(env_db - limiar_db, 0, max_reducao_db)
    ganho = 10 ** (gr_db / 20)
    # suaviza o ganho (2 ms ataque / 40 ms soltura aproximados por média móvel)
    k = int(sr * 0.01)
    ganho = np.convolve(ganho, np.ones(k) / k, "same")
    return x * ganho[None, :]


def dobrar(x, sr, atraso_ms=16.0):
    """Violão "dobrado": a mesma gravação em cada lado do fone, uma delas
    atrasada alguns milissegundos e com EQ levemente diferente. Abre o som e
    deixa o violão mais cheio, com a voz sozinha no centro."""
    mono = x.mean(axis=0)
    d = int(sr * atraso_ms / 1000)
    esq = Pedalboard([PeakFilter(cutoff_frequency_hz=2500, gain_db=1.0, q=0.7)])(mono[None, :], sr)[0]
    dir_ = np.pad(mono, (d, 0))[: len(mono)]
    dir_ = Pedalboard([PeakFilter(cutoff_frequency_hz=2500, gain_db=-1.0, q=0.7),
                       HighShelfFilter(cutoff_frequency_hz=8000, gain_db=1.0)])(dir_[None, :], sr)[0]
    return (np.stack([esq, dir_]) * 10 ** (-1.5 / 20)).astype(np.float32)


def enviar_reverb(x, sr, sala, mix_db, pre_ms):
    rev = Pedalboard([HighpassFilter(250), LowpassFilter(9000),
                      Reverb(room_size=sala, damping=0.5, wet_level=1.0, dry_level=0.0, width=1.0)])
    pre = np.pad(x, ((0, 0), (int(sr * pre_ms / 1000), 0)))[:, : x.shape[1]]
    return rev(pre, sr) * 10 ** (mix_db / 20)


def tratar_audio(wav_in, wav_out, k, forca_afinacao, separa, tmp):
    mix, sr = sf.read(wav_in, always_2d=True)
    mix = mix.T.astype(np.float32)

    if separa:
        voz = separar(wav_in, tmp).T
        n = min(voz.shape[1], mix.shape[1])
        voz, mix = voz[:, :n], mix[:, :n]
        violao = mix - voz  # residual: nada se perde
        stats = {}
        if forca_afinacao > 0:
            ref = referencia_cents(violao.T, sr)
            mono, stats = afinar(voz.mean(axis=0), sr, ref_cents=ref, forca=forca_afinacao)
            voz = np.stack([mono, mono])
        print("afinação:", stats)

        voz = nr.reduce_noise(y=voz, sr=sr, stationary=True, prop_decrease=0.5)
        voz = Pedalboard([
            HighpassFilter(90),
            LowShelfFilter(cutoff_frequency_hz=180, gain_db=-2.0 * k),
            PeakFilter(cutoff_frequency_hz=320, gain_db=-3.0 * k, q=1.2),     # tira o "embolado"
            PeakFilter(cutoff_frequency_hz=1000, gain_db=-1.0 * k, q=1.0),    # tira o "nasal/caixa"
            PeakFilter(cutoff_frequency_hz=3500, gain_db=3.5 * k, q=0.9),     # presença
            HighShelfFilter(cutoff_frequency_hz=10000, gain_db=4.0 * k),      # ar
            Compressor(threshold_db=-24, ratio=3.0, attack_ms=8, release_ms=90),
        ])(voz, sr)
        voz = de_esser(voz, sr)
        voz = Pedalboard([
            Compressor(threshold_db=-14, ratio=4.0, attack_ms=2, release_ms=60),  # segura picos
        ])(voz, sr)
        # Limiter da voz: picos ficam no máximo 12 dB acima do nível médio dela.
        rms_db = 10 * np.log10(np.mean(voz[:, np.abs(voz).max(axis=0) > 1e-3] ** 2) + 1e-12)
        voz = limitar(voz, sr, rms_db + 12.0, janela_ms=15)
        voz_rev = enviar_reverb(voz, sr, sala=0.4, mix_db=-21 + 2 * k, pre_ms=30)

        violao = Pedalboard([
            HighpassFilter(70),
            PeakFilter(cutoff_frequency_hz=200, gain_db=-2.5 * k, q=1.0),   # tira o "boom"
            PeakFilter(cutoff_frequency_hz=5000, gain_db=2.0 * k, q=0.8),   # brilho das cordas
            HighShelfFilter(cutoff_frequency_hz=11000, gain_db=2.0 * k),
            Compressor(threshold_db=-22, ratio=2.0, attack_ms=20, release_ms=150),
        ])(violao, sr)
        violao = dobrar(violao, sr, atraso_ms=16.0)
        violao_rev = enviar_reverb(violao, sr, sala=0.3, mix_db=-25, pre_ms=10)

        audio = voz * 10 ** (1.5 / 20) + voz_rev + violao * 10 ** (-1.5 / 20) + violao_rev
    else:
        audio = nr.reduce_noise(y=mix, sr=sr, stationary=True, prop_decrease=0.35 + 0.35 * k)
        audio = Pedalboard([
            HighpassFilter(75),
            PeakFilter(cutoff_frequency_hz=300, gain_db=-2.0 * k, q=1.0),
            PeakFilter(cutoff_frequency_hz=3200, gain_db=2.5 * k, q=0.8),
            HighShelfFilter(cutoff_frequency_hz=9000, gain_db=3.0 * k),
            Compressor(threshold_db=-20, ratio=2.5, attack_ms=12, release_ms=150),
        ])(audio, sr)
        audio = audio + enviar_reverb(audio, sr, sala=0.4, mix_db=-17, pre_ms=20)

    audio = Pedalboard([
        Compressor(threshold_db=-16, ratio=2.0, attack_ms=30, release_ms=200),  # cola
    ])(audio.astype(np.float32), sr)
    meter = pyln.Meter(sr)
    audio = pyln.normalize.loudness(audio.T, meter.integrated_loudness(audio.T), -14.0).T
    audio = limitar(audio.astype(np.float32), sr, -1.5, janela_ms=25)
    sf.write(wav_out, audio.T, sr, subtype="PCM_24")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("entrada")
    p.add_argument("saida")
    p.add_argument("--intensidade", type=float, default=0.7)
    p.add_argument("--afinacao", type=float, default=0.7)
    p.add_argument("--sem-estabilizar", action="store_true")
    p.add_argument("--sem-separar", action="store_true")
    a = p.parse_args()
    k = max(0.0, min(1.0, a.intensidade))

    with tempfile.TemporaryDirectory() as tmp:
        wav_in, wav_out = os.path.join(tmp, "in.wav"), os.path.join(tmp, "out.wav")
        run(["ffmpeg", "-y", "-i", a.entrada, "-vn", "-ac", "2", "-ar", str(SR), wav_in])
        tratar_audio(wav_in, wav_out, k, max(0.0, min(1.0, a.afinacao)), not a.sem_separar, tmp)

        vf = []
        if not a.sem_estabilizar:
            trf = os.path.join(tmp, "t.trf")
            run(["ffmpeg", "-y", "-i", a.entrada, "-vf",
                 f"vidstabdetect=shakiness=5:accuracy=15:result={trf}", "-f", "null", "-"])
            vf.append(f"vidstabtransform=input={trf}:smoothing=20:zoom=2")
        vf += [
            # 1080p no lado menor, mantendo a proporção
            "scale='if(gt(iw,ih),-2,1080)':'if(gt(iw,ih),1080,-2)':flags=lanczos",
            f"hqdn3d={1.5+1.5*k:.2f}:{1.5+1.5*k:.2f}:{4+3*k:.2f}:{4+3*k:.2f}",
            f"eq=contrast={1+0.12*k:.3f}:brightness={0.015*k:.3f}:saturation={1+0.22*k:.3f}:gamma={1+0.04*k:.3f}",
            f"colorbalance=rm={0.04*k:.3f}:gm={0.01*k:.3f}:bm={-0.04*k:.3f}:rh={0.02*k:.3f}:bh={-0.02*k:.3f}",
            f"unsharp=5:5:{0.6+0.5*k:.2f}:5:5:0",
            "vignette=angle=PI/7",
        ]
        run(["ffmpeg", "-y", "-i", a.entrada, "-i", wav_out, "-map", "0:v:0", "-map", "1:a:0",
             "-vf", ",".join(vf), "-c:v", "libx264", "-preset", "medium", "-crf", "17",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "320k", "-movflags", "+faststart",
             "-shortest", a.saida])


if __name__ == "__main__":
    main()
