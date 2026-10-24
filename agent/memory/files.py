"""What the agent keeps knowing between sessions: a few short files, through Claude's memory tool.

The journal records what happened; these files hold what stays true and is in no ERP: what the
owner wants (`owner.md`), how a supplier behaves (one file per supplier) and facts about the shop
(`shop.md`). The model reads and edits them with the standard memory tool (`memory_20250818`);
the storage is the SDK's own filesystem backend, under `<shop>/memories/`.

What the code holds to, whatever the model writes:
- Flat Markdown files only, each short, and few of them. A full wallet address is refused: a
  wallet lives in the ERP and is proven by signature, never remembered.
- `owner.md` changes only in a session where the owner wrote: what an invoice or an email says
  can never become the owner's wish.
- A session works on its own copy. Only a finished session's memory is kept, like its other
  choices; one that fails or is cut off leaves the memory as it was.
- Memory is never authority: limits, approved wallets and signatures are still enforced by the
  contract and the checks.
"""

import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from anthropic.lib.tools import ToolError

# The SDK's reference filesystem backend: path checks against /memories and symlink escapes,
# atomic writes, files readable by the owner of the process only.
from anthropic.lib.tools._beta_builtin_memory_tool import BetaLocalFilesystemMemoryTool

MEMORY_TOOL = {"type": "memory_20250818", "name": "memory"}
FOLDER = "memories"
OWNER = "owner.md"
MAX_FILES = 30
MAX_FILE_CHARS = 3000
MAX_SHOWN = MAX_FILE_CHARS  # of owner.md, put in full in every briefing
NAME = re.compile(r"^/memories/[A-Za-z0-9][A-Za-z0-9 ._&()-]{0,79}\.md$")
ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}(?![0-9a-fA-F])")
WRITES = frozenset({"create", "str_replace", "insert", "delete", "rename"})


class ShopMemory:
    """The memory files of one shop."""

    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)

    @property
    def root(self) -> Path:
        return self.folder / FOLDER

    def session(self, owner_wrote: bool) -> "SessionMemory":
        """A copy of the memory for one session; `commit` keeps it, `discard` drops it. One
        session runs at a time per shop: a copy left by a process that stopped mid-session is
        dropped."""
        for left in self.folder.glob(".memory-*"):
            shutil.rmtree(left, ignore_errors=True)
        staged = self.folder / f".memory-{uuid.uuid4().hex}"
        if self.root.is_dir():
            shutil.copytree(self.root, staged / FOLDER)
        return SessionMemory(self, staged, owner_wrote)


class SessionMemory(BetaLocalFilesystemMemoryTool):
    """The memory tool for one session, on its own copy of the shop's files."""

    def __init__(self, memory: ShopMemory, staged: Path, owner_wrote: bool) -> None:
        super().__init__(base_path=str(staged))
        self._memory = memory
        self._staged = staged
        self.owner_wrote = owner_wrote
        self.changed = False

    def brief(self) -> dict[str, Any]:
        """For the briefing: what the owner told the agent, in full, and what else it keeps."""
        files = sorted(self.memory_root.glob("*.md"))
        try:
            owner = (self.memory_root / OWNER).read_text(encoding="utf-8")[:MAX_SHOWN]
        except OSError:
            owner = ""
        return {"owner.md": owner,
                "files": [{"file": f"/memories/{f.name}", "about": _first_line(f)}
                          for f in files if f.name != OWNER]}

    def run(self, args: Any) -> str:
        """One memory command from the model. Raises ToolError for one it must correct."""
        if not isinstance(args, dict) or not isinstance(args.get("command"), str):
            raise ToolError("memory takes a command (view, create, str_replace, insert, delete, "
                            "rename) and its path")
        if args["command"] in WRITES:
            self._check_write(args)
        try:
            result = self.call(args)
        except (AttributeError, KeyError, TypeError, ValueError, NotImplementedError):
            raise ToolError(f"memory {args['command']} is missing an input or has a wrong one; "
                            "see the memory tool's commands") from None
        if args["command"] in WRITES:
            self.changed = True
        return str(result)

    def _check_write(self, args: dict[str, Any]) -> None:
        paths = [args.get("path"), args.get("old_path"), args.get("new_path")]
        paths = [p for p in paths if p is not None]
        for path in paths:
            if not isinstance(path, str) or not NAME.match(path):
                raise ToolError("keep memory as a few flat files: /memories/<name>.md")
            if path == f"/memories/{OWNER}" and not self.owner_wrote:
                raise ToolError("owner.md holds what the owner told you: change it only in a "
                                "session where the owner wrote to you")
        text = " ".join(str(args.get(k) or "") for k in ("file_text", "new_str", "insert_text"))
        if ADDRESS.search(text):
            raise ToolError("never keep a wallet address in memory: the ERP holds it and the "
                            "supplier proves it by signature")
        new = args["command"] == "create" and not (
            self.memory_root / Path(args["path"]).name).exists()
        if new and len(list(self.memory_root.glob("*.md"))) >= MAX_FILES:
            raise ToolError(f"at most {MAX_FILES} memory files: add to one that fits")
        if len(self._after(args)) > MAX_FILE_CHARS:
            raise ToolError(f"a memory file holds at most {MAX_FILE_CHARS} characters: keep only "
                            "what stays useful")

    def _after(self, args: dict[str, Any]) -> str:
        """What the file would hold after this command, to cap its size."""
        command = args["command"]
        if command == "create":
            return str(args.get("file_text") or "")
        if command not in ("str_replace", "insert"):
            return ""
        try:
            now = (self.memory_root / Path(args["path"]).name).read_text(encoding="utf-8")
        except OSError:
            return ""
        if command == "insert":
            return now + str(args.get("insert_text") or "")
        return now.replace(str(args.get("old_str") or ""), str(args.get("new_str") or ""), 1)

    def commit(self) -> None:
        """The session finished: its memory becomes the shop's."""
        if not self.changed:
            self.discard()
            return
        live, old = self._memory.root, self._memory.folder / f".memory-old-{uuid.uuid4().hex}"
        if live.is_dir():
            os.replace(live, old)
        os.replace(self.memory_root, live)
        shutil.rmtree(old, ignore_errors=True)
        shutil.rmtree(self._staged, ignore_errors=True)

    def discard(self) -> None:
        shutil.rmtree(self._staged, ignore_errors=True)


def _first_line(path: Path) -> str:
    try:
        with path.open(encoding="utf-8") as f:
            line = f.readline()
    except OSError:
        return ""
    return line.strip().lstrip("#").strip()[:120]
