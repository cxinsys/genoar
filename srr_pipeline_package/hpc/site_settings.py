#!/usr/bin/env python3
"""How a value gets decided when the site is the only one who knows it.

The Cell Ranger path set the pattern this generalises. A value is taken from
the flag if one was passed, from the config if it is written there, from the
machine if it can be measured, from the person if there is one to ask, and
otherwise the run stops and says exactly which key to write and where. What it
never does is guess and continue, because every unknown here is a number that
decides how much disk gets filled or how long a queued job runs, and a wrong
guess is discovered after the allocation is spent.

The ladder is the same for every setting, so a new one is a description rather
than a new piece of control flow:

    Setting(key="disk_budget_gb",
            prompt="How much disk may this run occupy at once, in GB",
            where="hpc_config.yaml",
            parse=positive_number,
            detect=lambda: free_space_gb(path))

    value, source = resolve(setting, cli_value, config, interactive=True)

`source` is carried so the plan can say where each number came from. A plan
whose numbers are unattributed is a plan nobody can check.
"""

import sys


class Unknown(Exception):
    """A value nobody could supply. Carries the instruction for fixing that."""


class Setting:
    def __init__(self, key, prompt, where, parse, detect=None, default=None,
                 unit=""):
        self.key = key
        self.prompt = prompt
        self.where = where
        self.parse = parse
        self.detect = detect
        self.default = default
        self.unit = unit

    def __repr__(self):
        return "Setting(%r)" % self.key


# --- parsers ---------------------------------------------------------------
#
# Each returns the value or raises ValueError with something a person can act
# on. They are what stands between a typo and a job that asks for 0 samples.

def positive_int(text):
    value = int(str(text).strip())
    if value <= 0:
        raise ValueError("must be greater than zero")
    return value


def positive_number(text):
    """A finite number above zero.

    `nan` and `inf` parse as floats and pass a `<= 0` test -- nan because every
    comparison with it is false, inf because it really is greater. Either one
    reaching the disk arithmetic makes every batch look like it fits.
    """
    value = float(str(text).strip())
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError("must be an ordinary number")
    if value <= 0:
        raise ValueError("must be greater than zero")
    return value


def yes_or_no(text):
    """A boolean, from the words a config actually contains.

    `bool()` on a string is true for every string, so an unquoted
    `release_input: false` was false and a quoted `"false"` was true -- the
    same setting meaning opposite things depending on punctuation, on the key
    that decides whether inputs are deleted.
    """
    if isinstance(text, bool):
        return text
    value = str(text).strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off", ""):
        return False
    raise ValueError("must be true or false")


def one_of(*allowed):
    def parse(text):
        value = str(text).strip()
        if value not in allowed:
            raise ValueError("must be one of: %s" % ", ".join(allowed))
        return value
    return parse


def walltime(text):
    """`HH:MM:SS`, returned as minutes.

    Accepted in the form the scheduler is given so that the same string can be
    copied from the queue's documentation into the config and into this.
    """
    parts = str(text).strip().split(":")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError("must look like HH:MM:SS")
    hours, minutes, seconds = (int(p) for p in parts)
    total = hours * 60 + minutes + seconds / 60.0
    if total <= 0:
        raise ValueError("must be greater than zero")
    return total


def pipeline_tree_of(source):
    """Which Stage 3 tree a config asks for: "legacy" or "current".

    One rule, because there are several spellings and three readers, and while
    each read it for itself they disagreed: the dispatcher took an empty
    `safe_mode:` as false while the bundle generator looked only at
    `pipeline_mode`. The value decides which code analyses the data, so the
    three had better agree.

    `legacy_pipeline` is the spelling to write. It says what it selects and
    nothing about which is better -- `legacy` is "the older one", not "the
    wrong one", and the pairing does not accuse either of anything. The
    earlier spellings still answer, because a config already written should
    not stop working:

        legacy_pipeline: true          -> legacy
        pipeline_mode: verified        -> legacy
        pipeline_mode: corrected       -> current
        safe_mode: true                -> legacy
        safe_mode: false               -> current

    Absent, empty, or a word that is neither is the CURRENT tree. That default
    changed when the reason for the old one went away: it was there so a run
    could not drift from the tree a cluster had checked, and nothing is
    running on that cluster now. What is left is a pipeline handed to people
    who will run it on hardware nobody here can see, and the one that has had
    its silent failures fixed is the one to hand them.

    `srr_pipeline_package/pipeline_entry.sh` carries the same rule in the
    shell, because it runs inside the container with nothing to import. A test
    puts the same table through both.
    """
    source = source or {}
    if source.get("legacy_pipeline") is not None \
            and str(source.get("legacy_pipeline")).strip():
        try:
            return "legacy" if yes_or_no(source["legacy_pipeline"]) else "current"
        except ValueError:
            return "current"
    named = source.get("pipeline_mode")
    if named is not None and str(named).strip():
        return "legacy" if str(named).strip().lower() == "verified" else "current"
    older = source.get("safe_mode")
    if older is None or not str(older).strip():
        return "current"
    try:
        return "legacy" if yes_or_no(older) else "current"
    except ValueError:
        return "current"


# --- the ladder ------------------------------------------------------------

def resolve(setting, cli_value=None, config=None, interactive=False,
            review=False, ask=input, out=sys.stderr):
    """Decide one setting. Returns (value, source).

    `review` asks about everything, with whatever was found offered as the
    answer to accept. It is for the first run at an unfamiliar site, where the
    detected numbers are worth a person's eye before a week of queue time is
    committed to them.
    """
    config = config or {}

    if cli_value is not None:
        return _parsed(setting, cli_value, "command line"), "command line"

    found, source = None, None
    if setting.key in config and config[setting.key] not in (None, ""):
        found, source = config[setting.key], setting.where
    else:
        if setting.detect is not None:
            detected = setting.detect()
            if detected is not None:
                value, how = (detected if isinstance(detected, tuple)
                              else (detected, "measured"))
                found, source = value, how
        # A default is the last thing tried, not the second: a value measured
        # from this machine beats a number that was true of some other one.
        if found is None and setting.default is not None:
            found, source = setting.default, "default"

    if found is not None and not (review and interactive):
        return _parsed(setting, found, source), source

    if not interactive:
        # `found is not None` was handled above -- reaching here without
        # `review and interactive` means nothing was found at all.
        raise Unknown(
            "%s is not set and could not be worked out here.\n"
            "  Write it as `%s:` in %s, or pass --%s.\n"
            "  %s"
            % (setting.key, setting.key, setting.where,
               setting.key.replace("_", "-"), setting.prompt))

    return _prompt(setting, found, source, ask, out)


def _parsed(setting, raw, source):
    try:
        return setting.parse(raw)
    except (TypeError, ValueError) as exc:
        raise Unknown("%s came from %s as %r, which %s.\n  %s"
                      % (setting.key, source, raw, exc, setting.prompt))


def _prompt(setting, found, source, ask, out):
    """Ask, until the answer parses or the person gives up.

    An empty answer takes what was found. There is no third state where the
    question is skipped and the value stays unknown: whoever is at the keyboard
    is the last rung of the ladder.
    """
    suffix = " [%s]" % found if found is not None else ""
    if found is not None:
        out.write("  %s: %s, from %s. Enter to accept.\n"
                  % (setting.key, found, source))
    while True:
        try:
            answer = ask("%s%s%s: " % (setting.prompt,
                                       " (%s)" % setting.unit if setting.unit else "",
                                       suffix))
        except EOFError:
            raise Unknown("%s was asked for and the input ended.\n"
                          "  Write it as `%s:` in %s instead."
                          % (setting.key, setting.key, setting.where))
        answer = answer.strip()
        if not answer and found is not None:
            return _parsed(setting, found, source), source
        if not answer:
            out.write("    %s has no default here; %s\n"
                      % (setting.key, setting.prompt.lower()))
            continue
        try:
            return setting.parse(answer), "asked"
        except (TypeError, ValueError) as exc:
            out.write("    %s. Try again, or Ctrl-C and write it in %s.\n"
                      % (str(exc)[:1].upper() + str(exc)[1:], setting.where))
