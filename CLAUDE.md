# cc-ai-reddit

Read README.md for what the tool is and its guardrails, and
.claude/skills/reddit-browser/SKILL.md for how to drive it and the traps.

Tests: `py -3.11 -m unittest discover -s tests` (no browser, no network).
This repository is public: tests/test_no_leak.py must stay green.
