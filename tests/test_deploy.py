"""Pure parts of the deploy tool (no handheld needed)."""
import io
import os
import sys
import tarfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import deploy  # noqa: E402

GL = """<?xml version="1.0"?>
<gameList>
\t<game>
\t\t<path>./Limbo.sh</path>
\t\t<name>Limbo</name>
\t</game>
\t<game id="123" source="ScreenScraper">
\t\t<path>./Net Analyzer.sh</path>
\t\t<name>Net Analyzer</name>
\t</game>
\t<game>
\t\t<path>./Fallout 2.sh</path>
\t\t<name>Fallout &amp; more</name>
\t</game>
</gameList>
"""


def test_gamelist_swaps_only_the_old_entry_even_with_attributes():
    out, n = deploy.new_gamelist(GL)
    assert n == 1
    assert "Net Analyzer" not in out and out.count("<path>./Field Terminal.sh</path>") == 1
    assert "<path>./Limbo.sh</path>" in out and "Fallout &amp; more" in out
    out2, n2 = deploy.new_gamelist(out)
    assert n2 == 1 and out2.count("Field Terminal.sh</path>") == 1


def test_gamelist_without_closing_tag_is_refused():
    import pytest
    with pytest.raises(Exception):
        deploy.new_gamelist("<gameList>\n<game><path>./Limbo.sh</path></game>\n")


def test_package_contents():
    t = tarfile.open(fileobj=io.BytesIO(deploy.pack()))
    names = t.getnames()
    assert "Field Terminal.sh" in names and "fieldterminal/main.py" in names
    assert not [n for n in names if "__pycache__" in n or n.endswith((".pyc", "config.json", "cache.json"))]
    assert not [n for n in names if "/logs" in n or "/captures" in n]
    modes = {m.name: m.mode for m in t.getmembers()}
    assert modes["fieldterminal/service/fieldterminal_log"] == 0o755


def test_smb_ls_parses_names_with_spaces():
    import re
    sample = ("  .                                   D        0  Tue Nov 24 23:52:53 2026\n"
              "  Castlevania 2 Simon's Quest Revamped.sh      N     1781  Mon Nov 23 23:25:58 2026\n"
              "  netanalyzer                         D        0  Tue Nov 24 22:45:21 2026\n")
    found = {}
    for line in sample.splitlines():
        m = re.match(r"^\s{2}(.+?)\s+([ADHNRS]*)\s+(\d+)\s+\w{3} \w{3}\s+\d+ ", line)
        if m and m.group(1) not in (".", ".."):
            found[m.group(1)] = (int(m.group(3)), "D" in m.group(2))
    assert found == {"Castlevania 2 Simon's Quest Revamped.sh": (1781, False), "netanalyzer": (0, True)}
