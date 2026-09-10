# Evidence

Captured output of every command, run against live Reddit and the live Arctic
Shift archive. Each file starts with the UTC time it was taken and the exact
command line, and ends with the exit code.

How a file gets here: the command's stdout and stderr are captured whole, then
passed through `tools/redact_evidence.py`, which replaces Reddit usernames,
account ids, home-directory paths and email addresses by shape.
`tests/test_no_leak.py` checks these files for the same shapes independently,
so a file that skipped the redactor fails the suite.

Post titles, comment text and permalinks are left in: they are public, and
they are what the evidence proves.

The write commands (`comment`, `reply`, `post`) were run WITHOUT `--submit`.
Nothing was sent to Reddit while building this tool. Their staging screenshots,
and the other screenshots named in these files, are not committed: they show
the signed-in account and other people's names, and they stay on the machine
that took them (`docs/evidence/*.png` is ignored).
