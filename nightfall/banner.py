"""Terminal presentation helpers."""
from __future__ import annotations
import os, sys

def _tty() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

def c(text: str, code: str) -> str:
    if not _tty():
        return text
    return f"\033[{code}m{text}\033[0m"

def bold(t): return c(t,"1")
def dim(t): return c(t,"2")
def green(t): return c(t,"32")
def yellow(t): return c(t,"33")
def red(t): return c(t,"31")
def cyan(t): return c(t,"36")
def magenta(t): return c(t,"35")

def ok(msg: str=""): print(f"  {green('✔')} {msg}")
def fail(msg: str=""): print(f"  {red('✘')} {msg}")
def info(msg: str=""): print(f"  {cyan('▸')} {msg}")
def warn(msg: str=""): print(f"  {yellow('◆')} {msg}")

BANNER_LINES = [
    "╭──────────────────────────────────────────────────╮",
    "│  🌙  NIGHTFALL · Private Cinema Gateway          │",
    "│  Movies + Anime — one unified gateway :8399      │",
    "╰──────────────────────────────────────────────────╯",
]

def banner(version: str=""):
    for ln in BANNER_LINES:
        print(c(ln, "35"))
    if version:
        print(dim(f"  v{version}"))
