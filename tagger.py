"""
Layer 4 (real): music tagging via PANNs Cnn14 (AudioSet, 527 tags).
Maps relevant tags -> genre buckets + a coarse mood vector.
Falls back cleanly if the model/checkpoint isn't available (analyze.py handles that).
"""
import os, numpy as np

_model = None
_labels = None
DATA = os.path.expanduser("~/panns_data")
CKPT = os.path.join(DATA, "Cnn14_mAP=0.431.pth")
CSV  = os.path.join(DATA, "class_labels_indices.csv")
CSV_URL  = "http://storage.googleapis.com/us_audioset/youtube_corpus/v1/csv/class_labels_indices.csv"
CKPT_URL = "https://zenodo.org/record/3987831/files/Cnn14_mAP%3D0.431.pth?download=1"

def ensure_model():
    """Download the AudioSet labels + Cnn14 checkpoint (~300MB) if missing. Safe to call repeatedly."""
    import urllib.request
    os.makedirs(DATA, exist_ok=True)
    if not os.path.exists(CSV):
        print("[tagger] downloading AudioSet labels…"); urllib.request.urlretrieve(CSV_URL, CSV)
    if not (os.path.exists(CKPT) and os.path.getsize(CKPT) > 100_000_000):
        print("[tagger] downloading PANNs Cnn14 checkpoint (~300MB, one-time)…"); urllib.request.urlretrieve(CKPT_URL, CKPT)
    return os.path.exists(CKPT) and os.path.getsize(CKPT) > 100_000_000

def available():
    """True if the tagging model can be used (installed; downloads on first use)."""
    import importlib.util
    return importlib.util.find_spec("panns_inference") is not None

def _load():
    global _model, _labels
    if _model is None:
        ensure_model()                       # make sure labels CSV + checkpoint exist first
        from panns_inference import AudioTagging
        from panns_inference.config import labels
        _model = AudioTagging(checkpoint_path=None, device="cpu")
        _labels = list(labels)
    return _model, _labels

GENRE_TAGS = {
    "Techno": "techno", "House music": "house", "Electronic dance music": "edm",
    "Electronic music": "electronic", "Drum and bass": "drum & bass", "Dubstep": "dubstep",
    "Trance music": "trance", "Ambient music": "ambient", "Hip hop music": "hip hop",
    "Rock music": "rock", "Jazz": "jazz", "Classical music": "classical", "Disco": "disco",
    "Funk": "funk", "Rhythm and blues": "r&b", "Soul music": "soul", "Pop music": "pop",
    "Reggae": "reggae", "Dance music": "dance",
}
MOOD_TAGS = {
    "Happy music": "happy", "Sad music": "sad", "Tender music": "tender",
    "Exciting music": "exciting", "Angry music": "angry", "Scary music": "scary",
    "Funny music": "funny",
}

# INSTRUMENTATION AND VOICE. The Cnn14 forward pass already scores all 527 AudioSet classes and
# we were keeping two of them; the instrument and voice classes were computed and discarded on
# every run. This costs nothing extra.
#
# Matched by KEYWORD against the checkpoint's own label list rather than by exact string. The
# AudioSet names carry commas and parenthetical qualifiers ("Violin, fiddle", "Keyboard
# (musical)", "Male speech, man speaking"), and a dict keyed on exact spelling silently returns
# nothing the day a label is punctuated differently — the failure mode is an empty section, not
# an error, which is the kind that survives for months.
INSTRUMENT_RULES = [
    ("piano",       ("piano", "electric piano")),
    ("organ",       ("organ",)),
    ("guitar",      ("guitar",)),
    ("bass",        ("bass guitar", "double bass")),
    ("drums",       ("drum", "snare", "hi-hat", "cymbal", "percussion", "timpani", "tabla")),
    ("synth",       ("synthesizer", "sampler", "electronic organ", "theremin")),
    ("strings",     ("violin", "cello", "fiddle", "string section", "bowed string", "harp")),
    ("brass",       ("trumpet", "trombone", "french horn", "brass")),
    ("woodwind",    ("saxophone", "flute", "clarinet", "oboe", "bassoon", "woodwind")),
    ("mallet",      ("marimba", "xylophone", "vibraphone", "glockenspiel", "mallet")),
    ("plucked",     ("banjo", "ukulele", "mandolin", "sitar", "plucked string")),
    ("orchestra",   ("orchestra",)),
    ("bell",        ("bell", "chime")),
    ("accordion",   ("accordion",)),
]
VOICE_RULES = [
    ("singing",  ("singing", "vocal music", "a capella", "yodeling", "humming", "chant")),
    ("choir",    ("choir",)),
    ("rapping",  ("rapping",)),
    ("speech",   ("speech", "narration", "conversation")),
    ("whistle",  ("whistling",)),
]


def _bucket(labels, clip, rules):
    """Max score per bucket, over every label whose name contains one of its keywords."""
    out = {}
    for i, nm in enumerate(labels):
        low = nm.lower()
        for bucket, keys in rules:
            if any(k in low for k in keys):
                v = float(clip[i])
                if v > out.get(bucket, 0.0):
                    out[bucket] = v
    return {k: round(v, 3) for k, v in sorted(out.items(), key=lambda kv: -kv[1]) if v >= 0.02}

def tag(mono, sr):
    import librosa
    model, labels = _load()
    y = librosa.resample(mono, orig_sr=sr, target_sr=32000) if sr != 32000 else mono
    clipwise, _ = model.inference(y[None, :])          # (1, 527)
    clip = clipwise[0]
    idx = np.argsort(clip)[::-1][:15]
    top = [{"tag": labels[i], "p": round(float(clip[i]), 3)} for i in idx]
    gscore, mscore = {}, {}
    for i in range(len(clip)):
        nm = labels[i]; v = float(clip[i])
        if nm in GENRE_TAGS: gscore[GENRE_TAGS[nm]] = max(gscore.get(GENRE_TAGS[nm], 0), v)
        if nm in MOOD_TAGS:  mscore[MOOD_TAGS[nm]] = v
    if gscore:
        order = sorted(gscore.items(), key=lambda kv: kv[1], reverse=True)
        primary, conf = order[0]
        secondary = order[1][0] if len(order) > 1 else None
    else:
        primary, conf, secondary = "electronic", 0.0, None
    instruments = _bucket(labels, clip, INSTRUMENT_RULES)
    voices = _bucket(labels, clip, VOICE_RULES)
    # A track is "vocal" on the strongest VOICE bucket, but speech is scored separately and not
    # counted toward it: AudioSet fires "Speech" on a spoken sample or an MC over an instrumental,
    # and calling that a vocal track would put a lead vocal in the director where there is none.
    sung = max([v for k, v in voices.items() if k in ("singing", "choir", "rapping")] or [0.0])
    return {"primary": primary, "confidence": round(conf, 3), "secondary": secondary,
            "method": "panns_cnn14", "top_tags": top,
            "moods": {k: round(v, 3) for k, v in sorted(mscore.items(), key=lambda kv: -kv[1])},
            "instruments": instruments,
            "vocals": {"presence": round(sung, 3),
                       "is_vocal": bool(sung >= 0.20),
                       "types": voices}}
