"""Citation extraction fixtures: forms, chains, silence, stretch.

EXTRACT: (id, text, expected [(kind, text, reporter, volume,
page, year, pin_cite, antecedent)]). Full, short, id., supra,
statute, and journal forms with resolved antecedents where the
passage supplies them. The fabricated-volume case pins the
honesty boundary: known reporters parse whether or not the
volume exists (existence needs gap G3).

SILENCE: (id, text). Prose, unknown reporters, and
non-citation strings must yield nothing.

STRETCH: state-code statute forms, reported never gated:
reporters-db coverage of state codes varies by version, so a
reporters-db upgrade must not flip this gate silently — the
report shows the drift instead.

Known limits (documented, not gated): spans are not asserted
(byte offsets are the most version-fragile surface); only one
id./supra chain shape is pinned.
"""

from __future__ import annotations

# (variant id, text, expected citation tuples).
EXTRACT = (
    ("full-pin", "See Bush v. Gore, 531 U.S. 98, 99-100 (2000).",
     [("full_case", "531 U.S. 98", "U.S.", "531", "98", "2000",
       "99-100", "")]),
    ("short-lone", "As held in 531 U.S., at 99, end.",
     [("short_case", "531 U.S., at 99", "U.S.", "531", "99", "",
       "99", "")]),
    ("id-chain", "Foo v. Bar, 1 U.S. 2 (1999). Id. at 3.",
     [("full_case", "1 U.S. 2", "U.S.", "1", "2", "1999", "", ""),
      ("id", "Id.", "", "", "", "", "at 3", "1 U.S. 2")]),
    ("supra-chain", "Foo v. Bar, 1 U.S. 2 (1999). Foo, supra, at 5.",
     [("full_case", "1 U.S. 2", "U.S.", "1", "2", "1999", "", ""),
      ("supra", "supra,", "", "", "", "", "at 5", "1 U.S. 2")]),
    ("statute", "Liability under 42 U.S.C. § 1983.",
     [("law", "42 U.S.C. § 1983", "U.S.C.", "", "", "", "", "")]),
    ("journal", "See 1 Minn. L. Rev. 1.",
     [("journal", "1 Minn. L. Rev. 1", "Minn. L. Rev.", "1", "1",
       "", "", "")]),
    ("fabricated-volume", "See 999 U.S. 999 (2026).",
     [("full_case", "999 U.S. 999", "U.S.", "999", "999", "2026",
       "", "")]),
)

# (variant id, text). Each must yield nothing.
SILENCE = (
    ("prose", "No citations here, just the weekly status discussion."),
    ("unknown-reporter", "See 22 Fake Reporter 1 for details."),
    ("fixture-string", "Cal. Civ. Code (fixture excerpt)"),
)

# (variant id, text, expected citation tuples). Reported only.
STRETCH = (
    ("state-code", "Damages under Cal. Civ. Code § 1542.",
     [("law", "Cal. Civ. Code § 1542", "Cal. Civ. Code", "", "", "",
       "", "")]),
    ("state-code-mass", "See Mass. Gen. Laws ch. 1, § 2.",
     [("law", "Mass. Gen. Laws ch. 1, § 2", "Mass. Gen. Laws", "", "",
       "", "", "")]),
)
