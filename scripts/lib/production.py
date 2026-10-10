"""production.yaml, read once: the production facts the tools used to hard-code.

Read from the text root (a worktree reads
its own copy). Group and slug come from the path of the main checkout, never from the file:
  slug   basename of the media root (the main checkout), so a worktree gets the same slug
  group  the folder above it
Values still holding a template placeholder ({{…}}) count as unset.

A folder without production.yaml (a production not yet through the template)
loads with defaults from the path: label_prefix = slug, and no wip, cap or title — a tool
that needs one of those says so and names the flag that supplies it. Nothing is guessed.
"""
import os

from lib import paths


def load_yaml(path):
    try:
        import yaml
    except ImportError:
        raise SystemExit("needs PyYAML: pip3 install --user pyyaml")
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _set(v):
    return None if v is None or "{{" in str(v) else v


class Production:
    def __init__(self, text, media=None):
        self.text = os.path.abspath(text)
        self.media = os.path.abspath(media or paths.main_checkout(text))
        self.file = os.path.join(self.text, "production.yaml")
        self.found = os.path.isfile(self.file)
        doc = load_yaml(self.file) if self.found else {}
        self.data = doc if isinstance(doc, dict) else {}
        self.slug = os.path.basename(self.media)
        self.group = os.path.basename(os.path.dirname(self.media))
        g = lambda k: _set(self.data.get(k))
        self.title = g("title")
        self.genre = g("genre")
        self.label_prefix = g("label_prefix") or self.slug
        self.wip = g("wip")
        c = self.data.get("credits") if isinstance(self.data.get("credits"), dict) else {}
        self.cap, self.spent, self.as_of = _set(c.get("cap")), _set(c.get("spent")), _set(c.get("as_of"))
        self.standards = self.data.get("standards") if isinstance(self.data.get("standards"), dict) else {}

    @property
    def ref(self):
        return f"{self.group}/{self.slug}"

    def note(self):
        """One line for stderr when the file is missing: what the defaults were."""
        if self.found:
            return None
        return (f"no production.yaml in {self.text}: label_prefix = {self.slug} (the folder name); "
                "no wip, credits cap or title")


def load(text, media=None):
    return Production(text, media)
