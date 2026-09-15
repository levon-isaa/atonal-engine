#!/usr/bin/env python3
"""Tests for tagger.py — the PANNs layer, which nothing had ever run.

    python tests/test_tagger.py

Dependency-free in the same way as its neighbours: no pytest, no panns_inference, no torch and
no 300MB checkpoint. Two things make that possible.

The 527 class names are VENDORED in audioset_labels.txt next door, so the bucket rules can be
checked against the names the model really emits rather than against names a test author made
up. When panns_inference IS installed the live list is used instead and the vendored copy is
checked against it, so a checkpoint that renames a class fails here instead of silently
emptying a section months later.

The model itself is replaced by a fake that returns a score vector chosen by the test. That is
enough for everything worth asserting: _clipwise's windowing (which exists to bound memory on a
long upload), the genre/mood selection, and the vocal threshold. What a fake cannot check is
whether Cnn14 is any good, and this file does not pretend to.
"""
import os
import sys
import threading

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import tagger  # noqa: E402

FAILURES = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


def load_labels():
    """The live class list when the package is installed, else the vendored copy.

    Deliberately NOT under _fixtures/, which is gitignored: everything in there is synthesised
    on demand and thrown away, and this file is neither. On a fresh clone it has to be present.
    """
    path = os.path.join(HERE, "audioset_labels.txt")
    vend = None
    if os.path.exists(path):
        vend = [ln.strip() for ln in open(path)
                if ln.strip() and not ln.startswith("#")]
    try:
        from panns_inference.config import labels as live
        return list(live), vend
    except Exception:
        if vend is None:
            raise SystemExit("missing " + path + " and panns_inference is not installed")
        return vend, None


LABELS, VENDORED = load_labels()


def buckets_for(name, rules):
    """Which buckets a single class name opens — asked of _bucket itself.

    Re-deriving the match here instead would test this file against itself: a _bucket that
    ignored its deny lists outright would still pass every assertion below.
    """
    return set(tagger._bucket([name], np.array([1.0]), rules))


def members(bucket, rules):
    """Every class name the rule for `bucket` collects."""
    if bucket not in {b for b, _k, _d in rules}:
        raise AssertionError("no such bucket: " + bucket)
    return [nm for nm in LABELS if bucket in buckets_for(nm, rules)]


# ============================================================ the class list itself
def test_label_list():
    print("label list")
    check(len(LABELS) == 527, f"527 classes ({len(LABELS)})")
    if VENDORED is not None:
        check(VENDORED == LABELS, "vendored copy matches the installed checkpoint's names")
    else:
        print("  --   panns_inference not installed; using the vendored names")

    # GENRE_TAGS and MOOD_TAGS are keyed on EXACT spelling, which is the failure the instrument
    # rules are keyword-matched to avoid: a renamed or repunctuated class does not raise, it
    # just stops firing, and the genre quietly falls back to the heuristic forever.
    missing_g = [k for k in tagger.GENRE_TAGS if k not in set(LABELS)]
    missing_m = [k for k in tagger.MOOD_TAGS if k not in set(LABELS)]
    check(not missing_g, f"every GENRE_TAGS key is a real class name {missing_g or ''}")
    check(not missing_m, f"every MOOD_TAGS key is a real class name {missing_m or ''}")


# ============================================================ what the buckets collect
def test_buckets_are_live():
    """A rule that matches nothing is a typo nobody would notice: the section is just absent."""
    print("bucket rules match something")
    for rules, what in ((tagger.INSTRUMENT_RULES, "instrument"),
                        (tagger.VOICE_RULES, "voice")):
        for b, _keys, _deny in rules:
            check(len(members(b, rules)) > 0, f"{what} bucket '{b}' collects at least one class")


def test_buckets_reject_impostors():
    """Each of these was landing in the bucket named, against the checkpoint's own names."""
    print("buckets reject the classes that are not the instrument")
    I = tagger.INSTRUMENT_RULES
    V = tagger.VOICE_RULES
    for nm in ("Belly laugh", "Bellow", "Doorbell", "Bicycle bell", "Telephone bell ringing"):
        check(nm not in members("bell", I), f"'{nm}' is not a bell instrument")
    check("Singing bowl" not in members("singing", V), "'Singing bowl' is not a voice")
    check("Speech synthesizer" not in members("synth", I), "'Speech synthesizer' is not a synth")
    check("Harpsichord" not in members("strings", I), "'Harpsichord' is not a bowed string")
    check("Drum and bass" not in members("drums", I), "'Drum and bass' is a genre, not a drum")
    # ...and the vehicle horns must never reach brass, which is why there is no bare "horn" key.
    for nm in ("Vehicle horn, car horn, honking", "Air horn, truck horn", "Train horn", "Foghorn"):
        check(nm not in members("brass", I), f"'{nm}' is not a brass instrument")
    check("Computer keyboard" not in members("piano", I), "'Computer keyboard' is not a keyboard")


def test_buckets_keep_the_score():
    """The denied classes are re-pointed, not dropped: the information stays, in the right place."""
    print("denied classes land in the bucket they belong to")
    I = tagger.INSTRUMENT_RULES
    check("Singing bowl" in members("bell", I), "'Singing bowl' is a struck metal instrument")
    check("Gong" in members("bell", I), "'Gong' is a struck metal instrument")
    check("Harpsichord" in members("plucked", I), "'Harpsichord' is a plucked string")
    check("Speech synthesizer" in members("speech", tagger.VOICE_RULES),
          "'Speech synthesizer' still counts as speech")
    check("Cowbell" in members("bell", I), "'Cowbell' is still a bell")
    check("Drum machine" in members("drums", I), "'Drum machine' is still a drum")


def test_bucket_scoring():
    print("_bucket scoring")
    labels = ["Piano", "Electric piano", "Violin, fiddle", "Trumpet", "Speech"]
    clip = np.array([0.10, 0.40, 0.30, 0.01, 0.90])
    got = tagger._bucket(labels, clip, tagger.INSTRUMENT_RULES)
    check(got.get("piano") == 0.4, f"a bucket takes the MAX of its classes, not the last ({got})")
    check("brass" not in got, "a class under 0.02 does not open a bucket")
    check("speech" not in got, "instrument rules do not collect voice classes")
    check(list(got) == sorted(got, key=lambda k: -got[k]), "buckets come back strongest first")


# ============================================================ windowing (bounds the memory)
class FakeModel:
    """Scores every window as a constant vector; records what it was handed."""

    def __init__(self, n_classes=527, per_window=None):
        self.n = n_classes
        self.seen = []
        self.per_window = per_window or (lambda i, seg: np.full(n_classes, 0.5))

    def inference(self, batch):
        seg = batch[0]
        self.seen.append(len(seg))
        return np.array([self.per_window(len(self.seen) - 1, seg)]), None


def test_clipwise_windowing():
    print("_clipwise windowing")
    W = tagger._WIN

    m = FakeModel()
    out = tagger._clipwise(m, np.zeros(W - 1, dtype="float32"))
    check(m.seen == [W - 1], "a clip at or under the window goes in whole")
    check(abs(float(out[0]) - 0.5) < 1e-9, "and its scores pass straight through")

    m = FakeModel()
    tagger._clipwise(m, np.zeros(9 * 60 * 32000, dtype="float32"))      # a 9-minute upload
    check(max(m.seen) <= W, f"no call sees more than one window ({max(m.seen)} <= {W})")
    check(len(m.seen) == 18, f"a 9-minute track is 18 windows ({len(m.seen)})")

    # a tail shorter than a second is dropped rather than scored on nothing
    m = FakeModel()
    tagger._clipwise(m, np.zeros(W + 16000, dtype="float32"))
    check(m.seen == [W], "a sub-second tail is not scored")

    # ...and a real tail is weighted by its length, not counted as a whole window
    half = W // 2
    m = FakeModel(per_window=lambda i, seg: np.full(527, 1.0 if i == 0 else 0.0))
    out = tagger._clipwise(m, np.zeros(W + half, dtype="float32"))
    want = W / float(W + half)
    check(abs(float(out[0]) - want) < 1e-6,
          f"windows are averaged by length ({float(out[0]):.4f} == {want:.4f})")


# ============================================================ tag() end to end, on a fake model
def fake_tag(scores, sr=32000, n=None):
    """Run tag() against a made-up class vector. `scores` maps class name -> probability."""
    labels = list(LABELS)
    clip = np.zeros(len(labels))
    for nm, v in scores.items():
        clip[labels.index(nm)] = v
    model = FakeModel(len(labels), per_window=lambda i, seg: clip)
    saved = (tagger._model, tagger._labels)
    tagger._model, tagger._labels = model, labels
    try:
        return tagger.tag(np.zeros(n or 32000, dtype="float32"), sr)
    finally:
        tagger._model, tagger._labels = saved


def test_tag_genre():
    print("tag() genre selection")
    r = fake_tag({"Techno": 0.40, "House music": 0.55, "Rock music": 0.10})
    check(r["primary"] == "house", f"the strongest genre wins ({r['primary']})")
    check(r["confidence"] == 0.55, f"confidence is that score ({r['confidence']})")
    check(r["secondary"] == "techno", f"second strongest is secondary ({r['secondary']})")
    check(r["method"] == "panns_cnn14", "method is reported")
    check(len(r["top_tags"]) == 15, f"15 raw tags are kept ({len(r['top_tags'])})")

    # Every genre class exists in the list, so gscore is never empty and the "electronic"
    # fallback inside tag() is unreachable: a silent upload yields a named genre at 0.0, and it
    # is analyze.py's PANNS_MIN_CONF gate that has to catch it. Pinned so that stays true.
    r = fake_tag({})
    check(r["confidence"] == 0.0, f"an empty read has zero confidence ({r['confidence']})")
    check(r["primary"] in set(tagger.GENRE_TAGS.values()),
          "an empty read still names a genre — the confidence is what says it is worthless")


def test_tag_vocals():
    print("tag() vocals")
    r = fake_tag({"Speech": 0.95, "Narration, monologue": 0.80})
    check(r["vocals"]["presence"] == 0.0, "speech alone is not a vocal")
    check(r["vocals"]["is_vocal"] is False, "an MC over an instrumental is not a lead vocal")
    check(r["vocals"]["types"].get("speech") == 0.95, "but speech is still reported")

    r = fake_tag({"Female singing": 0.61, "Choir": 0.30})
    check(r["vocals"]["presence"] == 0.61, f"presence is the strongest sung class ({r['vocals']['presence']})")
    check(r["vocals"]["is_vocal"] is True, "a sung track is vocal")

    r = fake_tag({"Rapping": 0.50})
    check(r["vocals"]["is_vocal"] is True, "rapping counts as a vocal")

    r = fake_tag({"Singing": 0.20})
    check(r["vocals"]["is_vocal"] is True, "the is_vocal bar is inclusive at 0.20")
    r = fake_tag({"Singing": 0.19})
    check(r["vocals"]["is_vocal"] is False, "and 0.19 is below it")

    # The measured regression: a struck metal bowl scored 0.194 as "Singing bowl", and that
    # reached the director as a lead vocal at presence 0.194.
    r = fake_tag({"Singing bowl": 0.194})
    check(r["vocals"]["presence"] == 0.0, "a singing bowl is not a singer")
    check(r["instruments"].get("bell") == 0.194, "it is a bell, and keeps its score")


def test_tag_moods_and_instruments():
    print("tag() moods and instruments")
    r = fake_tag({"Sad music": 0.30, "Happy music": 0.70, "Angry music": 0.01})
    check(list(r["moods"])[:3] == ["happy", "sad", "angry"],
          f"moods come back strongest first ({r['moods']})")
    # Unlike the instrument buckets there is no floor here: all seven moods are always present,
    # most of them at 0.0. layer3_emotion reads them with .get(name, 0) and biases valence,
    # tension and darkness by the sum, so a zero is the same as an absence — harmless, and
    # pinned because a floor added here would change the shape analyze.py is written against.
    check(len(r["moods"]) == len(tagger.MOOD_TAGS), "every mood is reported, floor or not")
    check(all(v >= 0.0 for v in r["moods"].values()), "and none of them is negative")

    r = fake_tag({"Electric guitar": 0.80, "Bass guitar": 0.50})
    check(r["instruments"].get("guitar") == 0.8, "a bass guitar is a guitar")
    check(r["instruments"].get("bass") == 0.5, "and it is also a bass")

    r = fake_tag({"Drum and bass": 0.90})
    check("drums" not in r["instruments"], "a drum and bass tag is not a drum kit")
    check(r["primary"] == "drum & bass", "it is a genre, and it is read as one")


def test_tag_json_safe():
    """server.py json.dumps()es this straight out; a numpy scalar in there is a 500."""
    print("tag() output is JSON-safe")
    import json
    r = fake_tag({"Techno": 0.5, "Female singing": 0.4, "Piano": 0.3, "Happy music": 0.2})
    try:
        json.dumps(r)
        check(True, "the whole dict serialises")
    except TypeError as e:
        check(False, f"the whole dict serialises ({e})")


# ============================================================ one model, not two
def test_load_is_single_flight():
    """The server threads requests AND runs the tagger on its own thread inside one analysis, so
    two callers reach _load() at once. Without the lock both build an AudioTagging: two
    checkpoint reads and two copies of ~300MB of weights."""
    print("_load builds one model under concurrency")
    import types
    built = []

    class SlowAudioTagging:
        def __init__(self, checkpoint_path=None, device="cpu"):
            built.append(1)
            threading.Event().wait(0.05)     # long enough for the racer to get inside

    mod = types.ModuleType("panns_inference")
    mod.AudioTagging = SlowAudioTagging
    cfg = types.ModuleType("panns_inference.config")
    cfg.labels = list(LABELS)
    mod.config = cfg

    saved_mods = {k: sys.modules.get(k) for k in ("panns_inference", "panns_inference.config")}
    saved = (tagger._model, tagger._labels, tagger.ensure_model)
    sys.modules["panns_inference"], sys.modules["panns_inference.config"] = mod, cfg
    tagger._model, tagger._labels = None, None
    tagger.ensure_model = lambda: True       # no download, no filesystem
    try:
        ts = [threading.Thread(target=tagger._load) for _ in range(6)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        check(len(built) == 1, f"six concurrent callers built one model ({len(built)})")
        check(tagger._labels == list(LABELS), "and the labels came with it")
    finally:
        tagger._model, tagger._labels, tagger.ensure_model = saved
        for k, v in saved_mods.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


if __name__ == "__main__":
    test_label_list()
    test_buckets_are_live()
    test_buckets_reject_impostors()
    test_buckets_keep_the_score()
    test_bucket_scoring()
    test_clipwise_windowing()
    test_tag_genre()
    test_tag_vocals()
    test_tag_moods_and_instruments()
    test_tag_json_safe()
    test_load_is_single_flight()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED")
        for f in FAILURES:
            print("  -", f)
        sys.exit(1)
    print("all passed")
