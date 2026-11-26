#!/usr/bin/env python3
"""Protótipo: melhora um vídeo de voz e violão sem recriar a gravação.

Uso: python melhorar.py entrada.mp4 saida.mp4 [--intensidade 0.6] [--sem-estabilizar]

Áudio: passa-alta, redução de ruído suave, EQ, compressão leve, reverb de sala
curto e volume final em -14 LUFS (padrão de Instagram/YouTube).
Vídeo: redução de granulado, estabilização e correção de cor sutil.
"""
import argparse, os, subprocess, tempfile

import numpy as np
import noisereduce as nr
import pyloudnorm as pyln
import soundfile as sf
from pedalboard import (Compressor, HighpassFilter, HighShelfFilter, Limiter,
                        LowShelfFilter, PeakFilter, Pedalboard, Reverb)

SR = 48000


def run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def tratar_audio(wav_in, wav_out, k):
    audio, sr = sf.read(wav_in, always_2d=True)
    audio = audio.T.astype(np.float32)  # (canais, amostras)

    # Ruído: estima o chiado de fundo e remove só parte dele, para não "aguar" a voz.
    audio = nr.reduce_noise(y=audio, sr=sr, stationary=True, prop_decrease=0.35 + 0.35 * k)

    board = Pedalboard([
        HighpassFilter(cutoff_frequency_hz=75),
        LowShelfFilter(cutoff_frequency_hz=220, gain_db=-1.5 * k),     # tira o "embolado" do celular
        PeakFilter(cutoff_frequency_hz=450, gain_db=-1.5 * k, q=1.0),  # som de caixa
        PeakFilter(cutoff_frequency_hz=3200, gain_db=2.0 * k, q=0.8),  # presença da voz
        HighShelfFilter(cutoff_frequency_hz=9000, gain_db=2.5 * k),    # brilho/ar
        Compressor(threshold_db=-20, ratio=1.5 + 1.5 * k, attack_ms=12, release_ms=150),
        Reverb(room_size=0.22, damping=0.6, wet_level=0.05 + 0.08 * k,
               dry_level=1.0, width=0.8),
        Limiter(threshold_db=-1.5, release_ms=120),
    ])
    audio = board(audio, sr)

    meter = pyln.Meter(sr)
    loud = meter.integrated_loudness(audio.T)
    audio = pyln.normalize.loudness(audio.T, loud, -14.0)
    audio = np.clip(audio, -0.95, 0.95)  # teto de segurança (~ -0,45 dBFS)
    sf.write(wav_out, audio, sr, subtype="PCM_24")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("entrada")
    p.add_argument("saida")
    p.add_argument("--intensidade", type=float, default=0.6)
    p.add_argument("--sem-estabilizar", action="store_true")
    a = p.parse_args()
    k = max(0.0, min(1.0, a.intensidade))

    with tempfile.TemporaryDirectory() as tmp:
        wav_in, wav_out = os.path.join(tmp, "in.wav"), os.path.join(tmp, "out.wav")
        run(["ffmpeg", "-y", "-i", a.entrada, "-vn", "-ac", "2", "-ar", str(SR), wav_in])
        tratar_audio(wav_in, wav_out, k)

        vf = []
        if not a.sem_estabilizar:
            trf = os.path.join(tmp, "t.trf")
            run(["ffmpeg", "-y", "-i", a.entrada, "-vf",
                 f"vidstabdetect=shakiness=5:accuracy=15:result={trf}", "-f", "null", "-"])
            vf.append(f"vidstabtransform=input={trf}:smoothing=20:zoom=1")
        vf += [
            f"hqdn3d={1+1.5*k:.2f}:{1+1.5*k:.2f}:{3+3*k:.2f}:{3+3*k:.2f}",
            f"eq=contrast={1+0.05*k:.3f}:saturation={1+0.08*k:.3f}:gamma={1+0.03*k:.3f}",
            f"unsharp=5:5:{0.25+0.25*k:.2f}:5:5:0",
        ]
        run(["ffmpeg", "-y", "-i", a.entrada, "-i", wav_out, "-map", "0:v:0", "-map", "1:a:0",
             "-vf", ",".join(vf), "-c:v", "libx264", "-preset", "medium", "-crf", "18",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart",
             "-shortest", a.saida])


if __name__ == "__main__":
    main()
