"""Send a completion to the first provider that will take it.

The order is fixed and the list is short, because a weighted pool is a
different problem and this one is about what happens in the seconds after a
provider stops answering. Two failure shapes matter and they are not the same
shape at all. A provider whose listener is gone refuses the connection
immediately. A provider that is still accepting connections and has stopped
answering costs the whole attempt timeout, on every request, until something
decides to stop asking.

That something is the breaker. `threshold=0` leaves it off, which is the
control arm the sweep needs rather than a setting anybody should deploy.

Nothing here retries the same provider. A retry and a failover are different
decisions and mixing them makes the counts unreadable.
"""

import json
import socket
import threading
import time
import urllib.error
import urllib.request


class Provider:
    """One backend, and the TLS identity to reach it with.

    `context` is None for plain HTTP, which is what the sweep uses, because
    the failover decision does not depend on the transport and running the
    sweep over TLS would make every arm pay a handshake for no extra answer.
    """

    def __init__(self, name, url, context=None):
        self.name = name
        self.url = url
        self.context = context

    def __repr__(self):
        return "Provider(%r)" % self.name


class Breaker:
    """Stop asking a provider that has failed `threshold` times in a row.

    Consecutive rather than a rate over a window. A rate needs a window length
    and that is a second constant to argue about, and the thing being defended
    against here is a provider that is uniformly down rather than one that is
    flapping.

    Every method takes the lock. The first version of this did not, and under a
    threaded server the half open check was a read of `opened_at` followed by a
    write to it, so every thread that arrived after the cooldown saw an elapsed
    breaker and went through. That is the same defect as dropping the critical
    section around the token counter, which this repository has already
    measured once, and it is worse here because the whole point of half open is
    that exactly one request finds out.
    """

    def __init__(self, threshold, cooldown):
        self.threshold = threshold
        self.cooldown = cooldown
        self.consecutive = 0
        self.opened_at = None
        self.opens = 0
        # True between letting a probe through and hearing how it went. A
        # second thread arriving in that gap is refused rather than sent at a
        # provider nobody has confirmed is back.
        self.probing = False
        self._lock = threading.Lock()

    @property
    def enabled(self):
        return self.threshold > 0

    def allows(self, now):
        if not self.enabled:
            return True
        with self._lock:
            if self.opened_at is None:
                return True
            if self.probing:
                return False
            if now - self.opened_at >= self.cooldown:
                self.probing = True
                return True
            return False

    def failed(self, now):
        with self._lock:
            self.probing = False
            self.consecutive += 1
            if (self.enabled and self.consecutive >= self.threshold
                    and self.opened_at is None):
                self.opened_at = now
                self.opens += 1
            elif self.enabled and self.opened_at is not None:
                # The half open probe failed, so the cooldown restarts from
                # now rather than from when it first opened.
                self.opened_at = now

    def succeeded(self):
        with self._lock:
            self.probing = False
            self.consecutive = 0
            self.opened_at = None


class Pool:
    """The providers in order, with one breaker each.

    The counters are shared across threads, so every increment is inside the
    lock. An unguarded `+= 1` is a read and a write and it loses increments
    under load, which would make the published attempt counts smaller than the
    traffic that produced them.

    A failover is a request the first provider did not serve, and it is split
    in two because the halves mean different things. `failovers_after_an_attempt` is one the pool found out about by
    asking. `failovers_past_an_open_breaker` is one the pool routed straight
    past without asking anything, which is the breaker working rather than
    detection happening. Summing them and calling the total detection reads
    the breaker's own saving as extra detection. `lost` is requests no
    provider answered.
    """

    def __init__(self, providers, attempt_timeout, breaker_threshold=0,
                 breaker_cooldown=0.0, clock=time.monotonic):
        if not providers:
            raise ValueError("a pool with no providers answers nothing")
        self.providers = list(providers)
        self.attempt_timeout = attempt_timeout
        self.clock = clock
        self.breakers = {p.name: Breaker(breaker_threshold, breaker_cooldown)
                         for p in self.providers}
        self._lock = threading.Lock()
        self.attempts = 0
        self.failovers_after_attempt = 0
        self.failovers_past_breaker = 0
        self.lost = 0
        self.answered = 0
        self.short_circuits = 0
        self.per_provider = {p.name: {"attempts": 0, "answered": 0, "refused": 0,
                                      "timed_out": 0, "errored": 0}
                             for p in self.providers}

    def opens(self):
        return {name: b.opens for name, b in self.breakers.items()}

    def send(self, prompt, ceiling):
        """Try each provider in turn. Returns the answer, or None if all failed.

        The second return value is the trace, which is what the sweep reads to
        decide whether an arm graded anything. An arm whose trace shows the
        first provider was never attempted measured the second provider on its
        own and nothing about failover.
        """
        trace = []
        attempted_and_failed = False
        skipped_by_breaker = False
        for provider in self.providers:
            breaker = self.breakers[provider.name]
            now = self.clock()
            if not breaker.allows(now):
                with self._lock:
                    self.short_circuits += 1
                trace.append((provider.name, "breaker-open"))
                skipped_by_breaker = True
                continue
            with self._lock:
                self.attempts += 1
                self.per_provider[provider.name]["attempts"] += 1
            outcome, answer = self._attempt(provider, prompt, ceiling)
            with self._lock:
                self.per_provider[provider.name][outcome] += 1
            trace.append((provider.name, outcome))
            if outcome == "answered":
                breaker.succeeded()
                with self._lock:
                    self.answered += 1
                    # Both can be true in one request, on a pool of three
                    # where the first is broken open and the second is still
                    # being asked. The attempt is the stronger statement, so
                    # it wins.
                    if attempted_and_failed:
                        self.failovers_after_attempt += 1
                    elif skipped_by_breaker:
                        self.failovers_past_breaker += 1
                return answer, trace
            breaker.failed(self.clock())
            attempted_and_failed = True
        with self._lock:
            self.lost += 1
        return None, trace

    def _attempt(self, provider, prompt, ceiling):
        request = urllib.request.Request(
            provider.url,
            data=json.dumps({"prompt": prompt, "max_output_tokens": ceiling}).encode("utf-8"),
            headers={"content-type": "application/json"},
        )
        kwargs = {"timeout": self.attempt_timeout}
        if provider.context is not None:
            kwargs["context"] = provider.context
        try:
            with urllib.request.urlopen(request, **kwargs) as response:
                return "answered", json.loads(response.read())
        except socket.timeout:
            return "timed_out", None
        except urllib.error.URLError as exc:
            # A refused connection and a read that ran out of time are the two
            # shapes this whole module is about, and urllib wraps both in
            # URLError, so the reason has to be unwrapped to tell them apart.
            reason = getattr(exc, "reason", None)
            if isinstance(reason, socket.timeout):
                return "timed_out", None
            if isinstance(reason, ConnectionRefusedError):
                return "refused", None
            if isinstance(reason, OSError) and reason.errno == 111:
                return "refused", None
            return "errored", None
        except (OSError, ValueError):
            return "errored", None

    def counters(self):
        return {
            "attempts": self.attempts,
            "answered": self.answered,
            "failovers_after_an_attempt": self.failovers_after_attempt,
            "failovers_past_an_open_breaker": self.failovers_past_breaker,
            "requests_lost": self.lost,
            "breaker_short_circuits": self.short_circuits,
            "breaker_opens": self.opens(),
            "per_provider": self.per_provider,
        }
