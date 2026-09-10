"""Turns conversational context into performance directions (phase 4).

A second, fast, heavily-constrained model call receives the user's message and
Claude's reply and returns a preset and an intensity (build plan 6, option A).
Cheap and boring by design. Claude decides what to say; this decides how the
avatar inhabits the moment, and keeping them apart is what stops stage
directions leaking into the spoken text.

**What it emits, and what it deliberately does not.** The direction is a preset
and an intensity, nothing more. `PRESET_SHAPES` already says what each preset
looks like and the renderer's smoother already interpolates toward it, so
asking the classifier for per-region coefficients would be asking it to
re-derive a table we have -- and inviting it to over-act while doing so. The
protocol keeps the per-region overrides (build plan 5.3) for a director that has
something the preset cannot say; this one does not.

**When it runs.** Not before the turn: the reply is what is being classified,
and waiting for the whole of it would land the direction after the avatar had
finished speaking. It fires on the *first phrase* -- the moment there is both a
user message and something Amanda is about to say -- and runs concurrently with
synthesis and playback, so it costs nothing from the T0-T6 budget.

Arriving a beat late is not a compromise, it is the right shape. The renderer
transitions over `transition_ms` rather than snapping, so a direction that lands
a second into an utterance reads as an expression settling in, which is what a
face actually does. A face that snapped to the correct emotion on the first
syllable would look like a mask being swapped.

**When it fails.** It returns None and the conversation state envelope stands
(`runtime/state_machine.py`). An avatar driven by state alone still looks like
it is participating; that is the whole reason the states own envelopes.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from amanda.avatar.protocol import PerformanceUpdate, Preset
from amanda.claude.prompts import PERFORMANCE_SYSTEM

log = logging.getLogger(__name__)

#: The phase 4 vocabulary. Fewer choices than the protocol has, because a
#: classifier offered twelve presets reaches for the interesting ones.
DEFAULT_PRESETS: tuple[Preset, ...] = (
    Preset.NEUTRAL_ATTENTIVE,
    Preset.WARM,
    Preset.CONSIDERING,
    Preset.MILDLY_AMUSED,
    Preset.CONCERNED,
    Preset.UNCERTAIN,
    Preset.SURPRISED,
)

#: How much of the reply the classifier sees. It is judging delivery, not
#: reading the answer, and the first sentence or two decides that. Also keeps
#: the call cheap on a turn that ran long.
REPLY_CHARS = 400

#: How much of the reply has to exist before it is worth classifying.
#:
#: The first phrase is deliberately short -- 24 characters, because it alone
#: decides when speech starts -- and "It rained most of the morning," is not
#: enough to tell warm from concerned. Waiting for a couple more clauses costs
#: roughly half a second of generation, during which the avatar wears the
#: SPEAKING envelope, which is a perfectly good default. Being right is worth
#: more here than being early: the direction holds for the rest of the
#: utterance either way.
MIN_REPLY_CHARS = 120


@runtime_checkable
class Director(Protocol):
    """Something that can say how a reply should be delivered."""

    @property
    def name(self) -> str: ...

    async def warm(self) -> None: ...

    async def direct(self, user_text: str, reply: str) -> PerformanceUpdate | None:
        """A direction, or None if there isn't one worth sending."""
        ...


@dataclass(frozen=True, slots=True)
class DirectorSettings:
    """The `performance:` block of config/avatar.yaml."""

    model: str = "claude-haiku-4-5"
    max_tokens: int = 128
    presets: tuple[Preset, ...] = DEFAULT_PRESETS

    #: A ceiling, not a target (build plan 18). The prompt asks for restraint
    #: and this enforces it, because JSON schema cannot: structured outputs
    #: support `enum` but not `minimum`/`maximum`, so the range is ours to keep.
    max_intensity: float = 0.45

    transition_ms: int = 450

    #: A classifier that has not answered by now has missed its moment -- the
    #: avatar is already speaking, and a direction arriving after the utterance
    #: is worse than none.
    timeout: float = 4.0

    @classmethod
    def from_config(cls, overrides: dict[str, Any] | None = None) -> DirectorSettings:
        """Built from config, with explicit overrides winning.

        The config keys read as prose rather than as field names, so the mapping
        is spelled out here. `smoothing:` is deliberately not among them: it
        belongs to the renderer's `PerformanceSmoother`, not to the classifier.
        """
        from amanda.config import performance_settings

        values = {**performance_settings(), **(overrides or {})}
        known = {
            "model": values.get("classifier_model"),
            "max_tokens": values.get("classifier_max_tokens"),
            "presets": _presets(values.get("enabled_presets")),
            "max_intensity": values.get("max_intensity"),
            "transition_ms": values.get("default_transition_ms"),
            "timeout": values.get("timeout"),
        }
        return cls(**{key: value for key, value in known.items() if value is not None})


def _presets(names: Any) -> tuple[Preset, ...] | None:
    """Config's preset list, dropping anything the protocol does not have.

    A name the code no longer knows should narrow the vocabulary, not stop the
    application starting.
    """
    if not isinstance(names, Sequence) or isinstance(names, str):
        return None
    presets = []
    for name in names:
        try:
            presets.append(Preset(name))
        except ValueError:
            log.warning("ignoring unknown preset %r in config", name)
    return tuple(presets) or None


@dataclass
class PerformanceDirector:
    """Classifies a turn's delivery with a second, cheap model call.

    The SDK client is injected so tests drive the whole path -- schema, parsing,
    clamping, failure -- without an API key. In the application it is shared
    with the conversational client rather than built again, so the classifier
    reuses an open connection instead of paying a handshake mid-utterance.
    """

    settings: DirectorSettings = field(default_factory=DirectorSettings)
    client: Any = None
    system: str = PERFORMANCE_SYSTEM

    #: Directions that landed, and the three ways one does not.
    directions: int = field(default=0, init=False)
    failures: int = field(default=0, init=False)
    clamped: int = field(default=0, init=False)
    #: Wall-clock of the last classification. Worth watching: it is free only
    #: for as long as it is shorter than the utterance it runs under.
    last_latency_ms: int = field(default=0, init=False)

    _warmed: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.client is None:
            import anthropic

            self.client = anthropic.AsyncAnthropic()

    @property
    def name(self) -> str:
        return self.settings.model

    @classmethod
    def from_config(
        cls, overrides: dict[str, Any] | None = None, **kwargs: Any
    ) -> PerformanceDirector:
        return cls(settings=DirectorSettings.from_config(overrides), **kwargs)

    # ----------------------------------------------------------------- #

    async def warm(self) -> None:
        """Pay the schema's one-time compilation cost before the first turn.

        A new JSON schema is compiled on first use and cached for 24 hours, so
        without this the first classification of a run is the slow one -- and
        the first turn is the one where the avatar most needs to look alive.
        Costs one small Haiku call at startup. Failures are ignored: this is an
        optimisation, and the real call reports its own problems.
        """
        if self._warmed:
            return
        self._warmed = True
        try:
            await self.direct("Morning.", "Morning. How did you sleep?")
        except Exception:  # noqa: BLE001 - warming must never block startup
            log.debug("director warm-up failed", exc_info=True)

    async def direct(self, user_text: str, reply: str) -> PerformanceUpdate | None:
        """Classify one exchange. Never raises; returns None when it cannot."""
        if not reply.strip():
            return None

        started = time.monotonic()
        try:
            async with asyncio.timeout(self.settings.timeout):
                response = await self.client.messages.create(**self.build_request(user_text, reply))
        except asyncio.CancelledError:
            # The turn ended or was interrupted. Not a failure, and not ours to
            # report -- the caller cancelled us on purpose.
            raise
        except TimeoutError:
            self.failures += 1
            log.warning("director timed out after %.1fs", self.settings.timeout)
            return None
        except Exception as exc:  # noqa: BLE001 - a bad turn must not be fatal
            self.failures += 1
            log.warning("director failed: %s", exc)
            return None
        finally:
            self.last_latency_ms = round((time.monotonic() - started) * 1000)

        update = self.parse(response)
        if update is not None:
            self.directions += 1
        return update

    # ----------------------------------------------------------------- #

    def build_request(self, user_text: str, reply: str) -> dict[str, Any]:
        """The request body, as its own method so tests can assert on it.

        No `effort` and no thinking: this is a classification with one right
        answer and no reasoning to do, and a classifier that thinks about it is
        a classifier that talks itself into `surprised`. No `cache_control`
        either -- the prompt is far below the minimum cacheable prefix, so
        marking it would only add a field that does nothing.
        """
        return {
            "model": self.settings.model,
            "max_tokens": self.settings.max_tokens,
            "system": self.system,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"User said: {user_text.strip()}\n\n"
                        f"Amanda replies: {reply.strip()[:REPLY_CHARS]}"
                    ),
                }
            ],
            "output_config": {
                "format": {
                    "type": "json_schema",
                    "schema": {
                        "type": "object",
                        "properties": {
                            # The enum is the vocabulary gate. Constrained here
                            # as well as in the prompt, so phase 4's subset is
                            # enforced rather than requested.
                            "preset": {
                                "type": "string",
                                "enum": [preset.value for preset in self.settings.presets],
                            },
                            "intensity": {"type": "number"},
                        },
                        "required": ["preset", "intensity"],
                        "additionalProperties": False,
                    },
                }
            },
        }

    def parse(self, response: Any) -> PerformanceUpdate | None:
        """Read a direction out of a response, or None if there isn't one."""
        blocks = getattr(response, "content", None) or []
        text = next(
            (block.text for block in blocks if getattr(block, "type", None) == "text"), None
        )
        if text is None:
            # A refusal, or a reply that hit max_tokens before any text. The
            # format guarantee does not survive either.
            self.failures += 1
            log.warning("director returned no text (stop_reason=%s)", 
                        getattr(response, "stop_reason", None))
            return None

        try:
            data = json.loads(text)
            preset = Preset(data["preset"])
            intensity = float(data["intensity"])
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            self.failures += 1
            log.warning("director returned something unusable: %s", exc)
            return None

        if preset not in self.settings.presets:
            # The schema's enum should have prevented this. If it happens the
            # vocabulary gate has failed, which is worth saying rather than
            # quietly animating a preset phase 4 has not authored.
            self.failures += 1
            log.warning("director returned %s, which is not enabled", preset.value)
            return None

        # Clamped, not rejected -- the exception to the project's "Python is the
        # producer, so it fails loudly" rule, and deliberately so. An
        # out-of-range coefficient from our own code is a bug to surface; one
        # from a model is a model ignoring an instruction, and the configured
        # ceiling exists precisely to be the thing that holds when it does.
        # The count is the loud part.
        clamped = max(0.0, min(self.settings.max_intensity, intensity))
        if clamped != intensity:
            self.clamped += 1
            log.info("director asked for intensity %.2f, capped at %.2f", intensity, clamped)

        return PerformanceUpdate(
            preset=preset, intensity=clamped, transition_ms=self.settings.transition_ms
        )


# --------------------------------------------------------------------------- #
# Offline
# --------------------------------------------------------------------------- #


#: What the scripted director cycles through. Chosen to exercise the renderer's
#: hysteresis and decay rather than to be plausible: a real conversation would
#: be neutral_attentive most of the way down.
SCRIPTED_DIRECTIONS: tuple[tuple[Preset, float], ...] = (
    (Preset.NEUTRAL_ATTENTIVE, 0.14),
    (Preset.WARM, 0.22),
    (Preset.CONSIDERING, 0.26),
    (Preset.MILDLY_AMUSED, 0.30),
    (Preset.NEUTRAL_ATTENTIVE, 0.12),
    (Preset.CONCERNED, 0.28),
    (Preset.UNCERTAIN, 0.20),
)


@dataclass
class ScriptedDirector:
    """An offline stand-in with the director's shape and none of its judgement.

    It cycles a fixed list. It is emphatically *not* a cheap classifier or a
    fallback worth shipping -- it does not read either argument -- and the
    distinction matters, because a keyword heuristic here would look like one
    and would be believed. This exists so the bridge, the renderer and the
    smoother can be exercised on a machine with no API key, which is the machine
    this was built on.

    The latency is real, though: the classifier's whole premise is that it lands
    part-way through an utterance, and a stand-in that answered instantly would
    hide the case where it does not.
    """

    directions: Sequence[tuple[Preset, float]] = SCRIPTED_DIRECTIONS
    latency: float = 0.5
    transition_ms: int = 450

    _cycle: itertools.cycle = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._cycle = itertools.cycle(self.directions)

    @property
    def name(self) -> str:
        return "scripted"

    async def warm(self) -> None:
        return None

    async def direct(self, user_text: str, reply: str) -> PerformanceUpdate | None:
        if not reply.strip():
            return None
        await asyncio.sleep(self.latency)
        preset, intensity = next(self._cycle)
        return PerformanceUpdate(
            preset=preset, intensity=intensity, transition_ms=self.transition_ms
        )


@dataclass
class NullDirector:
    """No direction at all. The conversation state envelopes stand alone.

    Not a degraded mode so much as the phase 1-3 behaviour, kept reachable:
    presets driven by conversation state look like participation, and it is
    worth being able to switch the classifier off and see whether the difference
    is one anybody notices.
    """

    @property
    def name(self) -> str:
        return "none"

    async def warm(self) -> None:
        return None

    async def direct(self, user_text: str, reply: str) -> PerformanceUpdate | None:
        return None


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #


def build(name: str | None = None, client: Any = None, **overrides: Any) -> Director:
    """A director by name; the real one when there are credentials.

    "auto" is the default and degrades quietly, in the same way the rest of the
    pipeline does: no key means the scripted stand-in rather than a failure to
    start (build plan 26). The classifier is the one component whose absence is
    genuinely invisible from the outside, so it must never be the reason a run
    does not happen.
    """
    if name in {"none", "off"}:
        return NullDirector()
    if name == "scripted":
        return ScriptedDirector()

    if name not in {None, "auto", "claude"}:
        raise ValueError(f"unknown director {name!r}. Known: claude, scripted, none")

    try:
        director = PerformanceDirector.from_config(overrides or None, client=client)
    except Exception as exc:  # noqa: BLE001 - config or import problem
        if name == "claude":
            raise
        log.warning("could not build the performance director (%s); using the stand-in", exc)
        return ScriptedDirector()

    # Asked of the SDK client for the same reason ClaudeClient asks: it resolves
    # a credential from several places, and construction succeeds either way --
    # only the request fails, which is mid-utterance and too late to say so.
    if not (
        getattr(director.client, "api_key", None) or getattr(director.client, "auth_token", None)
    ):
        if name == "claude":
            raise ValueError("no Anthropic credentials for the performance director")
        return ScriptedDirector()

    return director
