"""Pure logic behind the one-screen Folders designer.

Every edit made on the Folders screen goes through this module. Each function
takes the current ``Config``, returns a new ``Config`` (never mutating the one it
was given) and refuses changes that cannot work, so the screen never has to
guess. Nothing here touches the disk; folder moves on disk are returned as a
list of ``(old_rel, new_rel)`` pairs for the caller to run off the GUI thread.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

from download_sorter.architecture import (
    ArchNode,
    FolderArchitecture,
    apply_architecture_to_config,
    architecture_from_config,
)
from download_sorter.config import Config, Rule
from download_sorter.pipeline import TYPE_BUCKETS

MANUAL_KEY = "__manual__"
GENERATED_PREFIX = "arch-"

# Plain-language file types shown in the sentence editor, in display order.
TYPE_CHOICES: tuple[tuple[str, tuple[str, ...]], ...] = tuple(
    (bucket.name, tuple(bucket.extensions)) for bucket in TYPE_BUCKETS
)

_BAD_CHARS = re.compile(r'[\\/:*?"<>|]')
_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
MAX_NAME_LEN = 60


class DesignerError(ValueError):
    """Raised when an edit would break the folder layout. The message is user-facing."""


@dataclass
class FolderCues:
    """What sends files to one folder, in the words the screen shows."""

    extensions: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    skip_words: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.extensions or self.keywords or self.domains)


@dataclass
class Change:
    """Result of one edit: the new settings plus any folder moves to run on disk."""

    config: Config
    message: str
    disk_moves: list[tuple[str, str]] = field(default_factory=list)
    disk_removals: list[tuple[str, str | None]] = field(default_factory=list)
    selected_path: str = ""
    # A folder to create on disk and fill with the loose files that now belong in it.
    fill_path: str = ""
    # More folders to create and fill the same way (for example new project folders).
    fill_paths: list[str] = field(default_factory=list)

    def all_fill_paths(self) -> list[str]:
        out = [self.fill_path] if self.fill_path else []
        out += [p for p in self.fill_paths if p and p not in out]
        return out


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def norm_path(path: str) -> str:
    return "\\".join(p.strip() for p in str(path or "").replace("/", "\\").split("\\") if p.strip())


def _same(a: str, b: str) -> bool:
    return norm_path(a).lower() == norm_path(b).lower()


def _under(path: str, prefix: str) -> bool:
    p, q = norm_path(path).lower(), norm_path(prefix).lower()
    return bool(q) and (p == q or p.startswith(q + "\\"))


def working_architecture(cfg: Config) -> FolderArchitecture:
    """Return the folder tree for ``cfg``, with a folder for every rule destination."""
    arch = architecture_from_config(cfg)
    for rule in cfg.rules:
        dest = norm_path(rule.destination)
        if dest and find_node(arch, dest) is None:
            arch.ensure_path_chain(dest, is_custom=True)
    return arch


def find_node(arch: FolderArchitecture, path: str) -> ArchNode | None:
    target = norm_path(path).lower()
    if not target:
        return None
    for node in arch.nodes:
        if norm_path(arch.node_relative_path(node.id)).lower() == target:
            return node
    return None


def path_of(arch: FolderArchitecture, node: ArchNode) -> str:
    return norm_path(arch.node_relative_path(node.id))


def is_plain_rule(rule: Rule) -> bool:
    """True when a user rule has nothing the sentence editor cannot show."""
    return not (
        rule.regex_pattern
        or rule.content_keywords
        or rule.rename_template
        or rule.min_size_mb
        or rule.max_size_mb
        or rule.min_age_days
        or rule.auto_unzip
        or str(getattr(rule, "match_mode", "any") or "any").lower() not in ("any", "")
    )


def _user_rules_for(cfg: Config, path: str) -> list[Rule]:
    return [r for r in cfg.rules if not str(r.id).startswith(GENERATED_PREFIX) and _same(r.destination, path)]


def _covered(rule: Rule, node: ArchNode | None) -> bool:
    """True when a plain rule only repeats what the folder itself already says."""
    if node is None or not is_plain_rule(rule):
        return False
    return (
        {e.lower() for e in rule.extensions} <= {e.lower() for e in node.extensions}
        and {k.lower() for k in rule.keywords} <= {k.lower() for k in node.keywords}
        and {d.lower() for d in rule.domains} <= {d.lower() for d in node.domains}
    )


def extra_rules_for(cfg: Config, path: str, arch: FolderArchitecture | None = None) -> list[Rule]:
    """The user's own rules that also send files to ``path``, beyond the folder's sentence."""
    arch = arch or working_architecture(cfg)
    node = find_node(arch, path)
    return [r for r in _user_rules_for(cfg, path) if not _covered(r, node)]


def advanced_rules_for(cfg: Config, path: str) -> list[Rule]:
    """User rules for ``path`` that use options beyond types, words and websites."""
    return [r for r in _user_rules_for(cfg, path) if not is_plain_rule(r)]


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        key = v.lower()
        if v and key not in seen:
            seen.add(key)
            out.append(v)
    return out


def folder_cues(cfg: Config, path: str, arch: FolderArchitecture | None = None) -> FolderCues:
    """Return the types, words and websites the folder's own sentence says."""
    arch = arch or working_architecture(cfg)
    node = find_node(arch, path)
    if node is None:
        return FolderCues()
    return FolderCues(
        _dedupe(list(node.extensions)),
        _dedupe(list(node.keywords)),
        _dedupe(list(node.domains)),
        _dedupe(list(node.exclude_keywords)),
    )


def rule_cues(rule: Rule) -> FolderCues:
    return FolderCues(list(rule.extensions), list(rule.keywords), list(rule.domains), list(rule.exclude_keywords))


def is_manual(cfg: Config, path: str, arch: FolderArchitecture | None = None) -> bool:
    arch = arch or working_architecture(cfg)
    node = find_node(arch, path)
    if node is None:
        return False
    return folder_cues(cfg, path, arch).is_empty and not any(r.enabled for r in extra_rules_for(cfg, path, arch))


def remove_rule(cfg: Config, rule_id: str) -> Change:
    out = copy.deepcopy(cfg)
    gone = [r for r in out.rules if r.id == rule_id]
    if not gone:
        raise DesignerError("That rule no longer exists.")
    out.rules = [r for r in out.rules if r.id != rule_id]
    return Change(out, f"Removed the rule “{gone[0].name}”", selected_path=norm_path(gone[0].destination))


def teach(cfg: Config, dest: str, filename: str, source_url: str = "") -> Change:
    """Learn from one file: files with the same words or website go to ``dest`` from now on."""
    from download_sorter.pipeline import build_rule_from_cues, infer_teach_cues

    arch = working_architecture(cfg)
    if find_node(arch, dest) is None:
        raise DesignerError("That folder no longer exists.")
    info = infer_teach_cues(filename, source_url)
    words = [c["value"] for c in info.get("word_cues", []) if c.get("checked")]
    domains = [info["host"]] if info.get("host") else []
    ext = str(info.get("ext") or "")
    if not words and not domains:
        if not ext:
            raise DesignerError("That file has nothing to learn from.")
        exts = [ext]
    else:
        exts = [ext] if ext and words and not domains else []
    leaf = dest.split("\\")[-1]
    rule = build_rule_from_cues(
        title=f"{leaf}: {' '.join(words) or ', '.join(domains) or ext}",
        destination=dest,
        words=words,
        extensions=exts,
        domains=domains,
    )
    out = copy.deepcopy(cfg)
    out.rules = [r for r in out.rules if r.id != rule.id]
    out.rules.insert(0, rule)
    what = describe_cues(rule_cues(rule))
    return Change(out, f"Learned: {what} go to “{leaf}”", selected_path=norm_path(dest))


def type_names_for(extensions: list[str]) -> tuple[list[str], list[str]]:
    """Split ``extensions`` into whole type names and leftover single extensions."""
    have = {e.lower() for e in extensions}
    names: list[str] = []
    covered: set[str] = set()
    for name, exts in TYPE_CHOICES:
        if exts and set(exts) <= have:
            names.append(name)
            covered |= set(exts)
    leftovers = [e for e in extensions if e.lower() not in covered]
    return names, leftovers


def describe_cues(cues: FolderCues) -> str:
    """One plain sentence for a folder's cues, used in lists and toasts."""
    if cues.is_empty:
        return "Only files you put here yourself"
    names, extra = type_names_for(cues.extensions)
    parts = names + extra
    what = ", ".join(parts) if parts else "Files"
    bits = [what]
    if cues.keywords:
        bits.append("named " + " or ".join(f"“{k}”" for k in cues.keywords[:3]) + ("…" if len(cues.keywords) > 3 else ""))
    if cues.domains:
        bits.append("from " + " or ".join(cues.domains[:2]) + ("…" if len(cues.domains) > 2 else ""))
    return " ".join(bits)


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------
def clean_name(raw: str) -> str:
    name = _BAD_CHARS.sub("", str(raw or ""))
    name = " ".join(name.split())
    return name.strip().rstrip(".").strip()[:MAX_NAME_LEN]


def check_folder_name(
    arch: FolderArchitecture,
    raw: str,
    parent_path: str = "",
    *,
    ignore_path: str = "",
) -> tuple[str, str]:
    """Return ``(clean_name, problem)``. ``problem`` is empty when the name is fine."""
    name = clean_name(raw)
    if not name:
        return "", "Give the folder a name."
    if name.lower() in _RESERVED:
        return name, f"Windows keeps “{name}” for itself. Try another name."
    if name.startswith("_") or name.startswith("."):
        return name, "Names that start with _ or . are kept for the app. Try another name."
    parent = find_node(arch, parent_path) if parent_path else None
    parent_id = parent.id if parent else None
    for sib in arch.children_of(parent_id) if parent_id else arch.nodes_in_tier(1):
        if sib.name.lower() == name.lower() and not (ignore_path and _same(path_of(arch, sib), ignore_path)):
            where = parent.name if parent else "Downloads"
            return name, f"{where} already has a folder called “{sib.name}”."
    return name, ""


# ---------------------------------------------------------------------------
# Compiling
# ---------------------------------------------------------------------------
def _rewrite(path_val: str, old: str, new: str) -> str:
    norm = norm_path(path_val)
    if norm.lower() == old.lower():
        return new
    if norm.lower().startswith(old.lower() + "\\"):
        return new + norm[len(old) :]
    return path_val


def _compile(arch: FolderArchitecture, cfg: Config) -> Config:
    """Turn the edited tree into settings, keeping the user's own rules."""
    base = copy.deepcopy(cfg)
    out = apply_architecture_to_config(arch, base)
    # Extensions that no folder claims any more must stop going to their old place.
    claimed = {e for n in arch.nodes if n.enabled for e in n.extensions if not (n.keywords or n.domains)}
    out.category_map = {ext: dest for ext, dest in out.category_map.items() if ext in claimed or _owned_elsewhere(arch, dest)}
    return out


def _owned_elsewhere(arch: FolderArchitecture, dest: str) -> bool:
    # A category destination with no folder in the tree came from settings the
    # designer does not model (for example the starter map); keep it.
    return find_node(arch, dest) is None


def _drop_covered_rules(cfg: Config, arch: FolderArchitecture, node: ArchNode) -> Config:
    """Drop plain user rules that only repeat the folder's sentence, so editing it is the whole truth."""
    path = path_of(arch, node)
    out = copy.deepcopy(cfg)
    out.rules = [
        r
        for r in out.rules
        if str(r.id).startswith(GENERATED_PREFIX) or not (_same(r.destination, path) and _covered(r, node))
    ]
    return out


# ---------------------------------------------------------------------------
# Edits
# ---------------------------------------------------------------------------
def add_folder(cfg: Config, name: str, parent_path: str = "", cues: FolderCues | None = None) -> Change:
    arch = working_architecture(cfg)
    parent = find_node(arch, parent_path) if parent_path else None
    if parent_path and parent is None:
        raise DesignerError("That folder no longer exists.")
    clean, problem = check_folder_name(arch, name, parent_path)
    if problem:
        raise DesignerError(problem)
    tier = parent.tier + 1 if parent else 1
    if tier > FolderArchitecture.MAX_TIERS:
        raise DesignerError(f"Folders can go {FolderArchitecture.MAX_TIERS} levels deep. Put this one higher up.")
    node = arch.add_custom_node(name=clean, tier=tier, parent_id=parent.id if parent else None)
    node.premade_key = MANUAL_KEY
    change_cfg = _compile(arch, cfg)
    path = path_of(arch, node)
    if cues is not None and not cues.is_empty:
        result = set_cues(change_cfg, path, cues)
        result.message = f"Added “{clean}”"
        result.selected_path = path
        return result
    return Change(change_cfg, f"Added “{clean}”", selected_path=path, fill_path=path)


def set_cues(cfg: Config, path: str, cues: FolderCues) -> Change:
    """Replace what sends files to ``path``. Empty cues make it a manual folder."""
    arch = working_architecture(cfg)
    node = find_node(arch, path)
    if node is None:
        raise DesignerError("That folder no longer exists.")
    work = _drop_covered_rules(cfg, arch, node)
    exts = _dedupe([e.lower() if e.startswith(".") else f".{e.lower()}" for e in cues.extensions if e.strip(". ")])
    node.extensions = exts
    node.keywords = _dedupe([k.strip() for k in cues.keywords if k.strip()])
    node.domains = _dedupe([d.strip().lower() for d in cues.domains if d.strip()])
    node.exclude_keywords = _dedupe([s.strip() for s in cues.skip_words if s.strip()])
    node.premade_key = MANUAL_KEY if (not exts and not node.keywords and not node.domains) else (
        "" if node.premade_key == MANUAL_KEY else node.premade_key
    )
    released: list[str] = []
    if exts and not (node.keywords or node.domains):
        # Each type lives in one place. Take it from any other type-only folder.
        want = set(exts)
        for other in arch.nodes:
            if other.id == node.id or other.keywords or other.domains:
                continue
            lost = [e for e in other.extensions if e in want]
            if lost:
                other.extensions = [e for e in other.extensions if e not in want]
                released.append(other.name)
    msg = f"Updated “{node.name}”"
    if released:
        msg += " (moved those types here from " + ", ".join(dict.fromkeys(released)) + ")"
    node_path = path_of(arch, node)
    return Change(_compile(arch, work), msg, selected_path=node_path, fill_path=node_path)


def rename_folder(cfg: Config, path: str, new_name: str) -> Change:
    arch = working_architecture(cfg)
    node = find_node(arch, path)
    if node is None:
        raise DesignerError("That folder no longer exists.")
    old_path = path_of(arch, node)
    parent_path = path_of(arch, arch.get_node(node.parent_id)) if node.parent_id else ""
    clean, problem = check_folder_name(arch, new_name, parent_path, ignore_path=old_path)
    if problem:
        raise DesignerError(problem)
    if clean == node.name:
        return Change(copy.deepcopy(cfg), "No change", selected_path=old_path)
    old_name = node.name
    node.name = clean
    new_path = path_of(arch, node)
    work = _retarget(cfg, old_path, new_path)
    return Change(
        _compile(arch, work),
        f"Renamed “{old_name}” to “{clean}”",
        disk_moves=[(old_path, new_path)],
        selected_path=new_path,
    )


def valid_move_targets(cfg: Config, path: str) -> list[str]:
    """Folders ``path`` can move into. ``""`` means the top of Downloads."""
    arch = working_architecture(cfg)
    node = find_node(arch, path)
    if node is None:
        return []
    out: list[str] = []
    current_parent = node.parent_id
    if current_parent is not None and arch.can_reparent(node.id, None) and not _name_taken(arch, node, None):
        out.append("")
    for other in arch.nodes:
        if other.id in (node.id, current_parent):
            continue
        if arch.can_reparent(node.id, other.id) and not _name_taken(arch, node, other.id):
            out.append(path_of(arch, other))
    return sorted(out, key=str.lower)


def _name_taken(arch: FolderArchitecture, node: ArchNode, parent_id: str | None) -> bool:
    siblings = arch.children_of(parent_id) if parent_id else arch.nodes_in_tier(1)
    return any(s.id != node.id and s.name.lower() == node.name.lower() for s in siblings)


def why_cannot_move(cfg: Config, path: str, target_path: str) -> str:
    arch = working_architecture(cfg)
    node = find_node(arch, path)
    if node is None:
        return "That folder no longer exists."
    target = find_node(arch, target_path) if target_path else None
    if target_path and target is None:
        return "That folder no longer exists."
    if target is not None and (target.id == node.id or arch.is_descendant_of(target.id, node.id)):
        return "A folder can't go inside itself."
    if (target is not None and target.id == node.parent_id) or (target is None and node.parent_id is None):
        return f"“{node.name}” is already there."
    if _name_taken(arch, node, target.id if target else None):
        where = target.name if target else "Downloads"
        return f"{where} already has a folder called “{node.name}”."
    if not arch.can_reparent(node.id, target.id if target else None):
        return f"Folders can go {FolderArchitecture.MAX_TIERS} levels deep. That would go deeper."
    return ""


def move_folder(cfg: Config, path: str, target_path: str) -> Change:
    problem = why_cannot_move(cfg, path, target_path)
    if problem:
        raise DesignerError(problem)
    arch = working_architecture(cfg)
    node = find_node(arch, path)
    target = find_node(arch, target_path) if target_path else None
    assert node is not None
    old_path = path_of(arch, node)
    arch.reparent_node(node.id, target.id if target else None)
    new_path = path_of(arch, node)
    work = _retarget(cfg, old_path, new_path)
    where = target.name if target else "Downloads"
    return Change(
        _compile(arch, work),
        f"Moved “{node.name}” into {where}",
        disk_moves=[(old_path, new_path)],
        selected_path=new_path,
    )


def merge_targets(cfg: Config, path: str) -> list[str]:
    arch = working_architecture(cfg)
    node = find_node(arch, path)
    if node is None:
        return []
    return sorted(
        (path_of(arch, n) for n in arch.nodes if n.id != node.id and arch.can_merge(node.id, n.id)),
        key=str.lower,
    )


def merge_folder(cfg: Config, path: str, target_path: str) -> Change:
    arch = working_architecture(cfg)
    node = find_node(arch, path)
    target = find_node(arch, target_path)
    if node is None or target is None:
        raise DesignerError("That folder no longer exists.")
    if not arch.can_merge(node.id, target.id):
        raise DesignerError("Those two folders can't be combined: one is inside the other.")
    old_path = path_of(arch, node)
    tgt_path = path_of(arch, target)
    name = node.name
    arch.merge_nodes(node.id, target.id)
    work = _retarget(cfg, old_path, tgt_path)
    return Change(
        _compile(arch, work),
        f"Combined “{name}” into “{target.name}”",
        disk_moves=[(old_path, tgt_path)],
        selected_path=tgt_path,
    )


def removal_summary(cfg: Config, path: str) -> dict:
    """What removing ``path`` would affect, for the confirm sheet."""
    arch = working_architecture(cfg)
    node = find_node(arch, path)
    if node is None:
        return {"subfolders": 0, "rules": 0, "parent": ""}
    sub = len(arch.descendants_of(node.id))
    rules = [r for r in cfg.rules if not str(r.id).startswith(GENERATED_PREFIX) and _under(r.destination, path)]
    parent = path_of(arch, arch.get_node(node.parent_id)) if node.parent_id else ""
    return {"subfolders": sub, "rules": len(rules), "parent": parent, "name": node.name}


def remove_folder(cfg: Config, path: str) -> Change:
    """Remove ``path`` and its subfolders. Files inside go up to the parent folder."""
    arch = working_architecture(cfg)
    node = find_node(arch, path)
    if node is None:
        raise DesignerError("That folder no longer exists.")
    old_path = path_of(arch, node)
    parent_path = path_of(arch, arch.get_node(node.parent_id)) if node.parent_id else ""
    name = node.name
    arch.remove_node(node.id)
    work = copy.deepcopy(cfg)
    work.rules = [
        r for r in work.rules if str(r.id).startswith(GENERATED_PREFIX) or not _under(r.destination, old_path)
    ]
    out = _compile(arch, work)
    out.category_map = {e: d for e, d in out.category_map.items() if not _under(d, old_path)}
    where = parent_path.split("\\")[-1] if parent_path else "Downloads"
    return Change(
        out,
        f"Removed “{name}”. Files inside will go to {where}",
        disk_removals=[(old_path, parent_path or None)],
        selected_path=parent_path,
    )


def add_project_folders(cfg: Config, parent_path: str, projects: list[tuple[str, list[str], list[str]]]) -> Change:
    """Add one folder per found project inside ``parent_path`` and fill each with its files.

    ``projects`` holds ``(name, keywords, extensions)``. A name already used is skipped.
    """
    added: list[str] = []
    for name, keywords, extensions in projects:
        try:
            step = add_folder(cfg, name, parent_path, FolderCues(extensions=list(extensions), keywords=list(keywords)))
        except DesignerError:
            continue
        cfg = step.config
        added.append(step.selected_path)
    if not added:
        return Change(cfg, "No change")
    leaf = norm_path(parent_path).split("\\")[-1] or "Downloads"
    noun = "folder" if len(added) == 1 else "folders"
    return Change(cfg, f"Added {len(added)} project {noun} in “{leaf}”", selected_path=parent_path, fill_paths=added)


def new_folders_in(cfg: Config, scanned: FolderArchitecture) -> list[str]:
    """Paths in ``scanned`` (folders found on disk) that the settings do not list yet."""
    have = {path_of(a, n).lower() for a in [working_architecture(cfg)] for n in a.nodes}
    out: list[str] = []
    for node in sorted(scanned.nodes, key=lambda n: (n.tier, scanned.node_relative_path(n.id).lower())):
        path = norm_path(scanned.node_relative_path(node.id))
        if path and path.lower() not in have:
            out.append(path)
    return out


def adopt_existing_folders(cfg: Config, scanned: FolderArchitecture, chosen: list[str]) -> Change:
    """Add the chosen folders found on disk (and the folders above them) to the settings."""
    from download_sorter.architecture import merge_architectures

    wanted = {norm_path(p).lower() for p in chosen if p}
    keep: set[str] = set()
    for node in scanned.nodes:
        path = norm_path(scanned.node_relative_path(node.id)).lower()
        if any(path == w or w.startswith(path + "\\") for w in wanted):
            keep.add(node.id)
    picked = scanned.clone()
    picked.nodes = [n for n in picked.nodes if n.id in keep]
    if not picked.nodes:
        return Change(cfg, "No change")
    count = len(new_folders_in(cfg, picked))
    if not count:
        return Change(cfg, "No change")
    merged = merge_architectures(working_architecture(cfg), picked)
    noun = "folder" if count == 1 else "folders"
    return Change(_compile(merged, cfg), f"Added {count} {noun} from your Downloads")


def _retarget(cfg: Config, old_path: str, new_path: str) -> Config:
    out = copy.deepcopy(cfg)
    for rule in out.rules:
        rule.destination = _rewrite(rule.destination, old_path, new_path)
    out.category_map = {e: _rewrite(d, old_path, new_path) for e, d in out.category_map.items()}
    return out


def apply_layout(cfg: Config, arch: FolderArchitecture) -> Config:
    """Compile a whole layout (for example a restored snapshot) into settings."""
    return _compile(arch.clone(), cfg)


__all__ = [
    "Change",
    "DesignerError",
    "FolderCues",
    "MANUAL_KEY",
    "TYPE_CHOICES",
    "add_folder",
    "add_project_folders",
    "adopt_existing_folders",
    "new_folders_in",
    "advanced_rules_for",
    "extra_rules_for",
    "remove_rule",
    "rule_cues",
    "teach",
    "apply_layout",
    "check_folder_name",
    "clean_name",
    "describe_cues",
    "find_node",
    "folder_cues",
    "is_manual",
    "merge_folder",
    "merge_targets",
    "move_folder",
    "norm_path",
    "path_of",
    "remove_folder",
    "removal_summary",
    "rename_folder",
    "set_cues",
    "type_names_for",
    "valid_move_targets",
    "why_cannot_move",
    "working_architecture",
]
