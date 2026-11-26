"""Afinação sutil da voz, sem efeito robótico.

Ideia: a voz só é puxada na direção da nota certa quando a *nota pretendida*
(o contorno de altura suavizado, sem vibrato) está fora por mais que a
tolerância. O vibrato e as passagens entre notas ficam intactos, porque a
correção é calculada sobre o contorno suavizado e aplicada devagar.
A referência é a afinação do violão, não 440 Hz, para voz e violão baterem.
A ressíntese usa PSOLA (Praat), que preserva o timbre (formantes).
"""
import numpy as np
import parselmouth
from parselmouth.praat import call
from scipy.ndimage import median_filter, uniform_filter1d


def referencia_cents(instrumental, sr):
    """Desvio de afinação do violão em relação a 440 Hz, em cents."""
    import librosa
    y = instrumental if instrumental.ndim == 1 else instrumental.mean(axis=1)
    return float(librosa.estimate_tuning(y=y.astype(np.float32), sr=sr)) * 100.0


def afinar(voz, sr, ref_cents=0.0, forca=0.7, tolerancia=12.0, passo=0.01):
    """voz: mono float. forca: 0..1 (fração do desvio corrigida).
    Retorna (voz_afinada, estatisticas)."""
    snd = parselmouth.Sound(voz.astype(np.float64), sr)
    manip = call(snd, "To Manipulation", passo, 75, 700)
    pitch = snd.to_pitch_ac(time_step=passo, pitch_floor=75, pitch_ceiling=700)
    t = pitch.xs()
    f = pitch.selected_array["frequency"]
    voz_on = f > 0
    if voz_on.sum() < 10:
        return voz, {"corrigido_pct": 0.0}

    semis = np.zeros_like(f)
    semis[voz_on] = 69 + 12 * np.log2(f[voz_on] / 440.0) - ref_cents / 100.0

    # Contorno suavizado (tira vibrato) só dentro de cada trecho cantado.
    suave = semis.copy()
    ini = None
    for i in range(len(f) + 1):
        on = i < len(f) and voz_on[i]
        if on and ini is None:
            ini = i
        elif not on and ini is not None:
            seg = semis[ini:i]
            if len(seg) >= 5:
                seg = median_filter(seg, size=min(15, len(seg) | 1), mode="nearest")
                seg = uniform_filter1d(seg, size=min(9, len(seg)), mode="nearest")
            suave[ini:i] = seg
            ini = None

    alvo = np.round(suave)
    desvio = (suave - alvo) * 100.0  # cents
    # Joelho suave: abaixo da tolerância não mexe; acima, corrige 'forca' do excesso.
    excesso = np.sign(desvio) * np.maximum(0.0, np.abs(desvio) - tolerancia)
    corr = np.where(voz_on, -forca * (excesso + np.sign(excesso) * tolerancia * (excesso != 0)), 0.0)
    corr = uniform_filter1d(corr, size=7, mode="nearest")  # ~70 ms de "retune"

    pt = call("Create PitchTier", "novo", snd.xmin, snd.xmax)
    for ti, fi, ci in zip(t, f, corr):
        if fi > 0:
            call(pt, "Add point", float(ti), float(fi * 2 ** (ci / 1200.0)))
    call([pt, manip], "Replace pitch tier")
    saida = call(manip, "Get resynthesis (overlap-add)").values[0]
    n = len(voz)
    saida = np.pad(saida, (0, max(0, n - len(saida))))[:n]
    stats = {
        "desvio_mediano_cents": float(np.median(np.abs(desvio[voz_on]))),
        "corrigido_pct": float(np.mean(np.abs(corr[voz_on]) > 3) * 100),
        "ref_cents": ref_cents,
    }
    return saida.astype(np.float32), stats
