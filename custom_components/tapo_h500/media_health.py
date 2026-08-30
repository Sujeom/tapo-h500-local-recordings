"""Whether the hub is serving recordings, and the record of when it was not.

This hub stops serving video every so often and keeps no record of having done
it. It stops in two different ways -- refusing sessions outright on port 8800,
or answering them cleanly with nothing in them -- and the two are detected by
different means, so the state lives in one place rather than being assembled
from wherever each signal happened to land.

Everything here is in memory. Nothing is written to disk, so the record spans
this Home Assistant's uptime rather than the hub's life, which is what the
sensor built on it says.
"""
from __future__ import annotations

from homeassistant.util import dt as dt_util

from .const import WEDGE_HISTORY_SECONDS


class MediaHealth:
    """The media path's state, and the log of its outages.

    Its own object because these eleven pieces of state answer to each other
    and to nothing else: the two failure signals, the outage log, the
    automatic-restart breaker and the freshness of the evidence they all rest
    on. Spread across a coordinator that also polls, downloads, tracks faces
    and manages retention, "is it wedged, and when did that start" was a
    question you answered by reading eleven attributes in four places.
    """

    def __init__(self) -> None:
        # What the port-8800 sentinel last said: healthy, wedged, unreachable,
        # silent, or None before it has ever run.
        self.status: str | None = None
        # Consecutive downloads that completed cleanly and carried no video.
        # One counter, not one per camera: the failure is the hub's media
        # daemon and it fails for every camera at once.
        self._empty = 0
        # When a media session last taught us anything -- bytes served or a
        # confirmed empty answer. Stale evidence plus an indexed clip is what
        # triggers the deep check.
        self.evidence_at = 0.0
        # When the last automatic restart happened, and whether one failed to
        # cure what it fired for, which stops further attempts until real
        # recovery re-arms them.
        self.restarted_at = 0.0
        self.restart_broken = False
        # When to force the deep check after an automatic restart, so the cure
        # is verified rather than assumed. None means nothing pending.
        self.recheck_at: float | None = None
        # One fresh player_id per wedge episode: the case-D experiment.
        self.rotated = False
        # One entry per outage: when it started, what was tried, when it
        # ended.
        self.wedges: list[dict] = []
        self._healthy_since = dt_util.utcnow().timestamp()
        self._longest_healthy = 0.0
        self._was_wedged = False

    # -- the two signals ----------------------------------------------------
    def note_status(self, status: str) -> bool:
        """Record the sentinel's verdict. True if this was a recovery."""
        recovered = status == "healthy" and self.status == "wedged"
        self.status = status
        self._note_edge()
        return recovered

    def note_empty(self) -> None:
        """A download completed cleanly and carried no video."""
        self._empty += 1
        self.evidence_at = dt_util.utcnow().timestamp()
        self._note_edge()

    def note_served(self) -> bool:
        """Bytes arrived. True if this ended an outage.

        Whatever was wrong is over, so a tripped breaker re-arms and future
        failures may be automatically cured again.
        """
        recovering = self.serving_empty
        self._empty = 0
        self.evidence_at = dt_util.utcnow().timestamp()
        self.restart_broken = False
        self._note_edge()
        return recovering

    def note_restarting(self) -> None:
        """The hub is being rebooted to cure a media failure.

        The empty counter starts fresh, because sessions from before a reboot
        say nothing about the hub after it. Deliberately not `note_served`,
        which means bytes arrived: that would close the outage in the log and
        report a cure at the moment the cure was only being attempted.
        """
        self._empty = 0
        self.evidence_at = dt_util.utcnow().timestamp()
        self.note_attempt("hub restart")

    # -- what the state is --------------------------------------------------
    @property
    def serving_empty(self) -> bool:
        """Two clean-but-empty downloads in a row: the hub serves nothing.

        One could be a freak clip; two consecutive recordings with real
        durations answering zero bytes is the state measured on hardware,
        where it held for every clip of every age. A single served download
        clears it.
        """
        return self._empty >= 2

    @property
    def wedged(self) -> bool:
        """Whether the hub is refusing to serve recordings right now.

        Either signal is enough and they are independent: the sentinel's
        handshake against port 8800, and two clean-but-empty downloads in a
        row. Serving-empty counts whether or not a handshake has ever run,
        because the downloads themselves are the evidence.
        """
        return self.serving_empty or self.status == "wedged"

    @property
    def healthy_seconds(self) -> float:
        """How long the media path has been serving, this run.

        Zero while it is not, climbing while it is -- so the recorder's
        long-term graph is a sawtooth whose peaks are the times to wedge and
        whose resets are the wedges.
        """
        if self.wedged:
            return 0.0
        return max(0.0, dt_util.utcnow().timestamp() - self._healthy_since)

    @property
    def longest_healthy_seconds(self) -> float:
        """The best run yet, the one in progress included."""
        return max(self._longest_healthy, self.healthy_seconds)

    def wedges_since(self, seconds: float) -> int:
        cutoff = dt_util.utcnow().timestamp() - seconds
        return sum(1 for wedge in self.wedges if wedge["at"] >= cutoff)

    # -- the log ------------------------------------------------------------
    def _note_edge(self) -> None:
        """Open or close an outage, if the state has actually changed.

        The hub keeps no such history, and neither does Home Assistant in any
        form that outlives a purge: binary sensors get no long-term
        statistics. This is what makes "how often, and how long between"
        answerable months later, which is the question a support case turns
        on.
        """
        wedged = self.wedged
        if wedged == self._was_wedged:
            return
        self._was_wedged = wedged
        now = dt_util.utcnow().timestamp()
        if wedged:
            self._longest_healthy = max(
                self._longest_healthy, now - self._healthy_since)
            self.wedges.append({"at": now, "tried": [], "ended": None})
            # A wedge every twelve hours over the kept window is a few hundred
            # small records. Older than that has stopped being evidence about
            # the hub as it is now.
            cutoff = now - WEDGE_HISTORY_SECONDS
            self.wedges = [w for w in self.wedges if w["at"] >= cutoff]
        else:
            self._healthy_since = now
            if self.wedges and self.wedges[-1]["ended"] is None:
                self.wedges[-1]["ended"] = now

    def note_attempt(self, what: str) -> None:
        """Record something tried to cure the outage that is running.

        Every wedge used to start the diagnosis from nothing: the log showed
        that it happened and never what was done about it, so "does restarting
        actually help?" stayed a matter of memory.

        Ignored when nothing is wrong, because there is no episode for it to
        belong to and inventing one would report an outage that never was.
        """
        if self.wedges and self.wedges[-1]["ended"] is None:
            self.wedges[-1]["tried"].append(
                {"what": what, "at": dt_util.utcnow().timestamp()})

    def recovery_log(self, limit: int = 10) -> list[dict]:
        """The newest episodes first, in the shape a person reads.

        Times as ISO strings and lengths in minutes, because the audience is
        somebody reading a diagnostics file or a support thread, not code.
        """
        entries = []
        for wedge in reversed(self.wedges[-limit:]):
            ended = wedge["ended"]
            entries.append({
                "at": dt_util.utc_from_timestamp(wedge["at"]).isoformat(),
                "lasted_minutes": (
                    None if ended is None
                    else round((ended - wedge["at"]) / 60, 1)),
                "tried": [
                    {"what": attempt["what"],
                     "after_minutes":
                         round((attempt["at"] - wedge["at"]) / 60, 1)}
                    for attempt in wedge["tried"]],
            })
        return entries
