"""A per tenant token budget, admitted on an estimate and settled on the fact.

The gateway cannot know what a request will cost until the upstream has
answered, and it has to decide whether to forward it before that. So admission
charges a guess and reconciliation replaces the guess with the reported usage.
A tenant can therefore finish a window having spent more than its budget, and
the amount is the overshoot. It is not a bug to be removed. It is the price of
deciding early and it can be bounded, measured and published.

Reserving the declared output ceiling is the sound policy and almost nobody
can afford it. A caller that declares a ceiling of 4096 and typically uses 200
holds 4096 of the budget for the length of every request, so the budget sits
mostly idle and the quota refuses traffic it had room for. The alternative is
to reserve a forecast of the output and accept that the forecast is sometimes
short. `reserve_fraction` is that choice, and the overshoot it buys is the
thing this module exists to measure rather than to hide.

Three things produce overshoot and they are separable.

The estimate of the prompt can be short. gateway/tokens.py guesses four
characters to a token and a real tokeniser charges more for digits and
punctuation, so a payload full of identifiers is cheaper in the guess than in
the fact.

The forecast of the output can be short, which is the term `reserve_fraction`
controls. At a fraction of one it is zero while the upstream honours the
ceiling. Nothing here can make that true of a real provider.

And the counter can be read by two requests at once. A budget shared across
gateway processes lives in a store with a round trip on it, and a read, a
decision and a write with no serialisation between them is a race whose width
is that round trip. This is the term that grows with load and the other two
do not.

`Admission` is the bound. A semaphore caps how many requests are in detection
at once, which is the answer to a service whose detection cost is linear in
payload size and whose body limit is a megabyte.
"""

import threading
import time
from dataclasses import dataclass

from . import tokens


class QuotaExceeded(Exception):
    """Admission refused because the budget could not cover the estimate."""


class Overloaded(Exception):
    """Admission refused because the concurrency limit was already full."""


@dataclass
class Reservation:
    tenant: str
    estimated: int
    prompt_estimated: int
    output_ceiling: int


@dataclass
class Settlement:
    reserved: int
    actual: int
    # Positive when the reservation was short, which is the direction that
    # spends budget the admission decision did not account for.
    shortfall: int


class CounterStore:
    """The shared counter a gateway with more than one process has to keep.

    An in-process dictionary with a configurable round trip, because the shape
    that matters is that reading it and writing it are two separate calls. The
    round trip defaults to nothing and the load test sets it, so the width of
    the admission race is a parameter rather than a property of the host's
    scheduler.
    """

    def __init__(self, round_trip=0.0):
        self.round_trip = round_trip
        self._state = {}
        self.reads = 0
        self.writes = 0

    def read(self, tenant):
        self.reads += 1
        if self.round_trip:
            time.sleep(self.round_trip)
        return self._state.get(tenant, (0, 0))

    def write(self, tenant, spent, reserved):
        self.writes += 1
        if self.round_trip:
            time.sleep(self.round_trip)
        self._state[tenant] = (spent, reserved)


class TokenBudget:
    """One window's budget for one tenant, with admission on the estimate.

    `serialise` is the whole experiment. With it the read, the decision and the
    write are one critical section and the only overshoot left is the estimate
    being short. Without it they are three steps a second request can land in
    the middle of, which is what a deployment gets for free when it keeps the
    counter in a store and forgets that check and set are two round trips.
    """

    def __init__(self, tenant, budget, store=None, serialise=True, reserve_fraction=1.0):
        if budget <= 0:
            raise ValueError("a budget of %d admits nothing, so it measures nothing" % budget)
        if not 0 < reserve_fraction <= 1.0:
            raise ValueError("a reserve fraction of %r is not a share of the ceiling"
                             % reserve_fraction)
        self.tenant = tenant
        self.budget = budget
        self.store = store if store is not None else CounterStore()
        self.serialise = serialise
        self.reserve_fraction = reserve_fraction
        self._lock = threading.Lock()
        self.admitted = 0
        self.refused_over_budget = 0
        # Reservation shortfall summed over settled requests. This is the
        # quantity the policy controls and it is separate from the overshoot,
        # which is what is left of it after the budget's slack has absorbed
        # some. A run where the two disagree is the interesting one.
        self.shortfall_tokens = 0
        # Reservations handed back by requests that never reached the upstream.
        # A blocked request returns its whole estimate, so a corpus carrying
        # attacks funds the shortfall out of traffic nobody was charged for.
        self.headroom_returned_tokens = 0
        self.released = 0

    @property
    def spent(self):
        return self.store.read(self.tenant)[0]

    @property
    def reserved(self):
        return self.store.read(self.tenant)[1]

    @property
    def overshoot(self):
        """Reconciled spend above the budget. Never negative."""
        return max(0, self.spent - self.budget)

    def reserve(self, text, output_ceiling):
        """Charge a guess and return the reservation, or refuse.

        The estimate covers the prompt and the output ceiling the caller
        declared, because a reservation that only covers the prompt is not a
        reservation at all.
        """
        prompt = tokens.estimate(text)
        forecast = max(1, int(output_ceiling * self.reserve_fraction))
        estimated = prompt + forecast
        if self.serialise:
            with self._lock:
                admitted = self._try(estimated)
        else:
            admitted = self._try(estimated)
        if not admitted:
            self.refused_over_budget += 1
            raise QuotaExceeded(
                "%s needs %d tokens and the window has %d of %d left"
                % (self.tenant, estimated, max(0, self.budget - self.spent - self.reserved),
                   self.budget))
        self.admitted += 1
        return Reservation(tenant=self.tenant, estimated=estimated,
                           prompt_estimated=prompt, output_ceiling=output_ceiling)

    def _try(self, estimated):
        """Read, decide, write. The three steps the lock exists to join."""
        spent, reserved = self.store.read(self.tenant)
        if spent + reserved + estimated > self.budget:
            return False
        self.store.write(self.tenant, spent, reserved + estimated)
        return True

    def reconcile(self, reservation, actual):
        """Replace the reservation with the usage the upstream reported."""
        if actual < 0:
            raise ValueError("an upstream reported %d tokens, which is not a usage" % actual)
        shortfall = max(0, actual - reservation.estimated)
        with self._lock:
            spent, reserved = self.store.read(self.tenant)
            self.store.write(self.tenant, spent + actual,
                             max(0, reserved - reservation.estimated))
            self.shortfall_tokens += shortfall
        return Settlement(reserved=reservation.estimated, actual=actual,
                          shortfall=shortfall)

    def release(self, reservation):
        """Give the estimate back when the request never reached the upstream."""
        with self._lock:
            spent, reserved = self.store.read(self.tenant)
            self.store.write(self.tenant, spent, max(0, reserved - reservation.estimated))
            self.headroom_returned_tokens += reservation.estimated
            self.released += 1


class Admission:
    """A cap on how many requests are in detection at once.

    Detection walks the whole payload, so its cost is linear in body size and
    a body may be a megabyte. Without a cap the only thing bounding the work
    in flight is how many sockets the host will accept, which is not a number
    anybody chose. The semaphore is that number.

    It refuses rather than queues. A queue in front of a linear cost turns a
    load spike into a latency spike every caller waits through, and a refusal
    is a thing a client can retry.
    """

    def __init__(self, limit):
        if limit <= 0:
            raise ValueError("a limit of %d admits nothing" % limit)
        self.limit = limit
        self._slots = threading.Semaphore(limit)
        self._held = 0
        self._lock = threading.Lock()
        self.peak_held = 0
        self.refused_overloaded = 0

    def __enter__(self):
        if not self._slots.acquire(blocking=False):
            with self._lock:
                self.refused_overloaded += 1
            raise Overloaded("%d requests already in detection" % self.limit)
        with self._lock:
            self._held += 1
            self.peak_held = max(self.peak_held, self._held)
        return self

    def __exit__(self, *exc):
        with self._lock:
            self._held -= 1
        self._slots.release()
        return False
