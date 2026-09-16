#!/usr/bin/env python3
"""The analysis queue in server.py — the one lock the whole service passes through.

    python tests/test_queue.py

_Slot serialises analysis: one track at a time, with keys that bought a pack carrying
`priority` served first. Everything about the service's behaviour under load is this class, and
it had no test. The failure modes are the bad kind:

  - a slot that is not released leaves _BUSY set and NOTHING is ever analysed again. The server
    keeps answering /health, the page keeps saying "waiting", and only a restart fixes it.
  - two holders at once is two analyses sharing a box sized for one -- see analyze.py's cost
    law, 1495 MB fixed plus 4.95 MB per second of audio.
  - ordering that is merely usually right is a paid feature that usually works. The docstring
    records that the first version of this "was not even reliably first-come".

Threads only: no server, no database, no audio. The body of each slot is a sleep.
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ATONAL_NO_WARM", "1")

import server  # noqa: E402

FAILURES = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


def reset():
    """The module globals are process-wide; each test starts from a known state."""
    with server._QCOND:
        server._BUSY = False
        server._RUNNING = None
        del server._WAITING[:]
        server._QCOND.notify_all()


def drain(threads, timeout=20):
    """Join, and say so rather than hanging the suite if the queue has wedged."""
    end = time.time() + timeout
    for t in threads:
        t.join(max(0.0, end - time.time()))
    return [t for t in threads if t.is_alive()]


def test_mutual_exclusion():
    """Never two at once, and everyone gets through."""
    print("mutual exclusion")
    reset()
    inside = []
    peak = [0]
    lock = threading.Lock()
    done = []

    def worker(i):
        with server._Slot(job="", secs=1.0):
            with lock:
                inside.append(i)
                peak[0] = max(peak[0], len(inside))
            time.sleep(0.02)
            with lock:
                inside.remove(i)
        done.append(i)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
    for t in ts:
        t.start()
    stuck = drain(ts)
    check(not stuck, "all 12 finished (%d still running)" % len(stuck))
    check(peak[0] == 1, "never more than one holder at a time (peak %d)" % peak[0])
    check(len(done) == 12, "every slot ran (%d of 12)" % len(done))
    check(not server._WAITING and not server._BUSY,
          "the queue is empty and free afterwards (waiting=%d busy=%s)"
          % (len(server._WAITING), server._BUSY))


def test_slot_survives_an_exception():
    """A failing analysis must release the slot. If it does not, the service is over."""
    print("release on failure")
    reset()

    class Boom(Exception):
        pass

    try:
        with server._Slot(job="", secs=1.0):
            raise Boom()
    except Boom:
        pass
    check(not server._BUSY, "_BUSY is clear after the body raised")
    check(server._RUNNING is None, "and nothing is recorded as running")

    ran = []

    def after():
        with server._Slot(job="", secs=1.0):
            ran.append(1)
    t = threading.Thread(target=after)
    t.start()
    stuck = drain([t], timeout=5)
    check(not stuck and ran, "the next upload can still take the slot")


def test_exception_while_waiting_frees_the_seat():
    """The other cleanup path: a waiter that dies IN the queue, rather than in the body.

    __enter__ catches BaseException around the wait, removes itself from _WAITING and notifies.
    Without that, the dead waiter stays at the head of the list and everyone behind it waits on
    a thread that is never coming back -- the same total stall as a leaked slot, reached from
    the other direction. Provoked by making _say_waiting raise, which is the only call inside
    that loop that can.
    """
    print("release when a waiter dies in the queue")
    reset()
    holder_go = threading.Event()
    holder_done = threading.Event()

    def holder():
        with server._Slot(job="", secs=1.0):
            holder_go.set()
            holder_done.wait(5)

    h = threading.Thread(target=holder)
    h.start()
    holder_go.wait(5)

    real = server._Slot._say_waiting
    boom = []

    def exploding(self):
        boom.append(1)
        raise RuntimeError("client vanished")

    server._Slot._say_waiting = exploding
    victim_err = []

    def victim():
        try:
            with server._Slot(job="", secs=1.0):
                pass
        except RuntimeError as e:
            victim_err.append(str(e))

    v = threading.Thread(target=victim)
    v.start()
    drain([v], timeout=5)
    server._Slot._say_waiting = real

    check(victim_err == ["client vanished"], "the waiter's exception propagated (%s)" % victim_err)
    check(not any(s for s in server._WAITING),
          "and it left no seat behind (waiting=%d)" % len(server._WAITING))

    ran = []

    def after():
        with server._Slot(job="", secs=1.0):
            ran.append(1)

    a = threading.Thread(target=after)
    a.start()
    holder_done.set()
    stuck = drain([h, a], timeout=6)
    check(not stuck and ran, "and the queue still serves the next arrival")


def _order_run(kinds, hold=0.05):
    """Seat one slot per entry in `kinds` ('p' priority, 'n' not) behind a holder, release it,
    and return the order they actually ran in."""
    reset()
    order = []
    lock = threading.Lock()
    go = threading.Event()
    release = threading.Event()

    def holder():
        with server._Slot(job="", secs=1.0):
            go.set()
            release.wait(10)

    h = threading.Thread(target=holder)
    h.start()
    go.wait(5)

    def worker(tag, prio):
        with server._Slot(job="", secs=1.0, prio=prio):
            with lock:
                order.append(tag)
            time.sleep(0.005)

    ts = []
    for i, k in enumerate(kinds):
        tag = "%s%d" % (k, i)
        t = threading.Thread(target=worker, args=(tag, k == "p"))
        t.start()
        # Seated one at a time so "first come" is a fact about this test and not a race in it.
        deadline = time.time() + 5
        while time.time() < deadline:
            with server._QCOND:
                if len(server._WAITING) == i + 1:
                    break
            time.sleep(0.002)
        ts.append(t)
    release.set()
    drain([h] + ts, timeout=15)
    return order


def test_handoff_is_prompt():
    """How long the slot sits idle between one analysis ending and the next starting.

    The waiters use a TIMED wait -- _QCOND.wait(0.5) -- so the notify_all in __exit__ is not
    required for correctness: drop it and everyone still wakes up, just up to half a second
    later. That is exactly why it survived every other check here. It is still worth holding on
    to: half a second per handoff is dead time on the one resource the whole service queues for,
    and it grows with the length of the queue.

    MEASURED with the notify in place: seven handoffs, 0.0 to 2.0 ms. The bound is 150 ms, which
    is fifty times the observed worst and still well inside the 500 ms the timed wait would give
    on its own, so this fails on a lost notify rather than on a busy machine.
    """
    print("handoff latency")
    reset()
    marks = []
    lock = threading.Lock()

    def worker(i):
        with server._Slot(job="", secs=1.0):
            with lock:
                marks.append(("in", time.time()))
            time.sleep(0.01)
            with lock:
                marks.append(("out", time.time()))

    ts = []
    for i in range(8):
        t = threading.Thread(target=worker, args=(i,))
        t.start()
        ts.append(t)
        time.sleep(0.01)
    stuck = drain(ts, timeout=30)
    check(not stuck, "all eight ran (%d stuck)" % len(stuck))
    marks.sort(key=lambda m: m[1])
    gaps = [(b[1] - a[1]) * 1000 for a, b in zip(marks, marks[1:])
            if a[0] == "out" and b[0] == "in"]
    check(len(gaps) >= 5, "there were handoffs to measure (%d)" % len(gaps))
    if gaps:
        check(max(gaps) < 150.0,
              "the slot is handed over in %.1f ms at worst, not half a second" % max(gaps))


def test_order():
    print("order")
    order = _order_run(["n", "n", "n", "n"])
    check(order == ["n0", "n1", "n2", "n3"],
          "first come, first served among equals (%s)" % order)

    order = _order_run(["n", "n", "p", "n"])
    check(order[0] == "p2", "a priority key goes to the front (%s)" % order)
    check([t for t in order if t.startswith("n")] == ["n0", "n1", "n3"],
          "and the rest keep their own order behind it (%s)" % order)

    order = _order_run(["n", "p", "n", "p", "n"])
    check(order[:2] == ["p1", "p3"],
          "priority stays first-come within itself, not reversed (%s)" % order)
    check(order[2:] == ["n0", "n2", "n4"],
          "and so does everyone else (%s)" % order)


def test_priority_can_starve_the_rest():
    """Not a defect -- a property, measured, because it is the thing to know before selling it.

    Priority is absolute: a new priority upload is seated ahead of everyone unprioritised, every
    time. A steady stream of them therefore holds an ordinary upload at the back indefinitely.
    That is what "served first" means and it is the right behaviour for a paid tier; it is
    recorded here so it is a decision rather than a surprise, and so that a later change to
    ageing or fairness has something to fail against.
    """
    print("priority is absolute, by design")
    reset()
    go = threading.Event()
    release = threading.Event()

    def holder():
        with server._Slot(job="", secs=1.0):
            go.set()
            release.wait(10)

    h = threading.Thread(target=holder)
    h.start()
    go.wait(5)
    ordinary = server._Slot(job="", secs=1.0, prio=False)
    with server._QCOND:
        ordinary._seat()
        for _ in range(5):
            server._Slot(job="", secs=1.0, prio=True)._seat()
        pos = server._WAITING.index(ordinary)
        depth = len(server._WAITING)
    check(pos == depth - 1,
          "an ordinary upload seated FIRST is last of %d after five priority arrivals" % depth)
    release.set()
    drain([h], timeout=5)
    reset()


if __name__ == "__main__":
    test_mutual_exclusion()
    test_slot_survives_an_exception()
    test_exception_while_waiting_frees_the_seat()
    test_handoff_is_prompt()
    test_order()
    test_priority_can_starve_the_rest()
    print()
    if FAILURES:
        print("%d FAILED" % len(FAILURES))
        for f in FAILURES:
            print("  - " + f)
        sys.exit(1)
    print("all passed")
