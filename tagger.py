"""
Layer 4 (real): music tagging via PANNs Cnn14 (AudioSet, 527 tags).
Maps relevant tags -> genre buckets + a coarse mood vector.
Falls back cleanly if the model/checkpoint isn't available (analyze.py handles that).
"""
import os, numpy as np
import threading

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

_load_lock = threading.Lock()

def _load():
    # The server is a ThreadingHTTPServer and the tagger now also runs on its own thread inside a
    # single analysis, so two callers can reach here at once. Without the lock both would build an
    # AudioTagging -- two checkpoint loads, two copies of the weights in memory, and whichever
    # finished last would win the global.
    global _model, _labels
    with _load_lock:
        if _model is None:
            ensure_model()                   # make sure labels CSV + checkpoint exist first
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
    ("piano",       ("piano", "electric piano", "keyboard (musical)"), ()),
    ("organ",       ("organ",), ()),
    ("guitar",      ("guitar",), ()),
    ("bass",        ("bass guitar", "double bass"), ()),
    ("drums",       ("drum", "snare", "hi-hat", "cymbal", "percussion", "timpani", "tabla",
                     "rimshot", "wood block", "tambourine", "rattle (instrument)", "maraca"),
                    ("drum and bass",)),
    ("synth",       ("synthesizer", "sampler", "electronic organ", "theremin"),
                    ("speech synth",)),
    ("strings",     ("violin", "cello", "fiddle", "string section", "bowed string", "harp"),
                    ("harpsichord",)),
    ("brass",       ("trumpet", "trombone", "french horn", "brass", "didgeridoo", "shofar"), ()),
    ("woodwind",    ("saxophone", "flute", "clarinet", "oboe", "bassoon", "woodwind",
                     "bagpipe"), ()),
    ("mallet",      ("marimba", "xylophone", "vibraphone", "glockenspiel", "mallet",
                     "steelpan"), ()),
    ("plucked",     ("banjo", "ukulele", "mandolin", "sitar", "plucked string", "zither",
                     "harpsichord"), ()),
    ("orchestra",   ("orchestra",), ()),
    ("bell",        ("bell", "chime", "gong", "singing bowl"),
                    ("belly", "bellow", "doorbell", "bicycle bell", "telephone bell")),
    ("accordion",   ("accordion", "harmonica"), ()),
]
VOICE_RULES = [
    ("singing",  ("singing", "vocal music", "a capella", "yodeling", "humming", "chant"),
                 ("singing bowl",)),
    ("choir",    ("choir",), ()),
    ("rapping",  ("rapping",), ()),
    ("speech",   ("speech", "narration", "conversation"), ()),
    ("whistle",  ("whistling",), ()),
]

# EACH RULE CARRIES A DENY LIST, because a keyword that is right for a bucket is not right for
# every label that contains it. Matching by substring is still the correct default -- see the
# note above -- but four of the fourteen buckets were collecting labels that are not the
# instrument at all, and the deny keywords are matched the same forgiving way so a repunctuated
# label degrades to today's behaviour rather than to an empty bucket.
#
# What was landing in the wrong bucket, against the checkpoint's own 527 names:
#
#   "bell"      <- Belly laugh, Bellow, Doorbell, Bicycle bell, Telephone bell ringing
#   "singing"   <- Singing bowl              (a struck metal bowl, scored as a VOICE)
#   "synth"     <- Speech synthesizer        (already counted as speech, which is right)
#   "strings"   <- Harpsichord               (plucked, not bowed; it moves to "plucked")
#   "drums"     <- Drum and bass             (a GENRE_TAGS entry, not an instrument)
#
# The singing bowl is the one that reached the director. MEASURED on a synthesised struck bowl
# -- five inharmonic partials, separate decays, audible beating -- the model returned
# Singing bowl 0.194, and tag() reported vocals {presence 0.194, types {singing: 0.194}}: an
# instrumental take, 0.006 under the is_vocal bar, one real recording away from putting a lead
# vocal in the director where there is none. That is the same failure the speech bucket is
# already excluded to avoid.
#
# The bowl, the gong and the harpsichord are not merely denied, they are re-pointed at the
# bucket they belong to, so the fix adds information rather than dropping it. The same pass
# picked up thirteen instruments the model scores on every run and no rule was reading --
# zither, tambourine, maraca, gong, steelpan, harmonica, bagpipes and the rest. Their placement
# is a judgement about instrument families, not a measurement.


def _bucket(labels, clip, rules):
    """Max score per bucket, over every label whose name contains one of its keywords."""
    out = {}
    for i, nm in enumerate(labels):
        low = nm.lower()
        for bucket, keys, deny in rules:
            if any(k in low for k in keys) and not any(d in low for d in deny):
                v = float(clip[i])
                if v > out.get(bucket, 0.0):
                    out[bucket] = v
    return {k: round(v, 3) for k, v in sorted(out.items(), key=lambda kv: -kv[1]) if v >= 0.02}

# 30 seconds at 32kHz. Cnn14 takes whatever length it is given as ONE tensor and pools globally
# at the end, so the activations through the conv stack scale with the track — a whole upload went
# in as a single array and the memory went with it.
#
# MEASURED, each variant in its own process so the peak is clean, on a 6:39 track:
#
#     whole clip    8584 MB   2.31 s
#     60s windows   2920 MB   1.67 s
#     30s windows   1843 MB   1.72 s      <- 4.7x less memory, 25% faster
#     15s windows   1389 MB   1.89 s
#
# and a 9-minute track reached 11276 MB, because this scales with length: the failure mode is an
# OOM on somebody's long upload rather than a slow response.
#
# WINDOWED IS NOT BIT-IDENTICAL AND IT IS WORTH SAYING SO. The clipwise output is a sigmoid over
# pooled embeddings, so a mean of window scores is not the same as scoring the whole thing —
# mean-of-sigmoids is not sigmoid-of-mean. In practice it differs where the model was diluting a
# real event across a long track: on the 6:39 recording the whole-clip pass found only male
# speech, and the windowed pass found both male (0.284) and female (0.341), which is correct.
# Checked on what this module actually EMITS rather than on raw tag order — primary genre,
# instrument buckets and the vocal flag agreed on every track where the model had any confidence
# at all, and differed only where confidence was 0.001, i.e. where the answer was noise anyway.
#
# Weighted by window length so a short tail cannot count as much as a full window, and a tail
# under a second is dropped: there is nothing to judge in it and Cnn14 wants a reasonable input.
_WIN = 30 * 32000

def _clipwise(model, y):
    """The 527-way clipwise vector, in bounded memory whatever the track length."""
    n = len(y)
    if n <= _WIN:
        cw, _ = model.inference(y[None, :])            # (1, 527)
        return cw[0]
    acc, wsum = None, 0.0
    for start in range(0, n, _WIN):
        seg = y[start:start + _WIN]
        if len(seg) < 32000:
            break
        cw, _ = model.inference(seg[None, :])
        w = float(len(seg))
        acc = cw[0] * w if acc is None else acc + cw[0] * w
        wsum += w
    return acc / wsum

def tag(mono, sr):
    model, labels = _load()
    if sr != 32000:
        import librosa                       # only the resample needs it, and only off 32k
        y = librosa.resample(mono, orig_sr=sr, target_sr=32000)
    else:
        y = mono
    clip = _clipwise(model, y)
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
