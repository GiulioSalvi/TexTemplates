# TeX Templates Library
# Copyright (C) 2026 Giulio Salvi
#
# This is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This software is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-FileCopyrightText: 2026 Giulio Salvi
# SPDX-License-Identifier: GPL-3.0-or-later

"""Exercise Markdown inclusion through Pandoc's real parser and filter engine."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


LIBRARY = Path(__file__).resolve().parents[1]
FILTER = LIBRARY / "filters" / "include.lua"
LAUNCHER = LIBRARY / "bin" / "generate-pdf.sh"
PANDOC = shutil.which("pandoc")


def nodes(value, node_type):
    """Find semantic nodes without depending on the reader's AST layout."""
    found = []
    if isinstance(value, dict):
        if value.get("t") == node_type:
            found.append(value)
        for child in value.values():
            found.extend(nodes(child, node_type))
    elif isinstance(value, list):
        for child in value:
            found.extend(nodes(child, node_type))
    return found


def text(value):
    return " ".join(node["c"] for node in nodes(value, "Str"))


@unittest.skipUnless(PANDOC, "Pandoc is required for Markdown inclusion tests")
class MarkdownIncludeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="markdown-include-tests-")
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name) / "project with spaces"
        self.project.mkdir()

    def write(self, path, content):
        target = self.project / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def run_pandoc(self, source="main.md", reader="markdown", *options):
        return subprocess.run(
            [PANDOC, "--from", reader, "--to", "json", "--lua-filter", str(FILTER),
             *options, str(self.project / source)],
            cwd=self.project, capture_output=True, text=True, encoding="utf-8",
            timeout=30,
        )

    def document(self, source="main.md", reader="markdown", *options):
        result = self.run_pandoc(source, reader, *options)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_included_content_is_parsed_as_markdown(self):
        self.write("main.md", "Before.\n\n!include chapter.md\n\nAfter.\n")
        self.write("chapter.md", "# Included chapter\n\nA **strong** claim and $x^2$.\n\n- First\n- Second\n")
        document = self.document()
        blocks = document["blocks"]
        self.assertEqual([block["t"] for block in blocks],
                         ["Para", "Header", "Para", "BulletList", "Para"])
        self.assertEqual(text(blocks[0]), "Before.")
        self.assertEqual(text(blocks[1]), "Included chapter")
        self.assertEqual(nodes(document, "Math")[0]["c"][1], "x^2")
        self.assertEqual(text(nodes(document, "Strong")[0]), "strong")
        self.assertEqual(text(blocks[-1]), "After.")
        self.assertNotIn("!include", text(document))

    def test_angle_brackets_include_and_adjacent_directive_lines(self):
        self.write("main.md", "!include <first.md>\n!include second.md\n")
        self.write("first.md", "# First chapter\n")
        self.write("second.md", "# Second chapter\n")
        document = self.document()
        self.assertEqual([text(item) for item in nodes(document, "Header")],
                         ["First chapter", "Second chapter"])

    def test_nested_includes_use_each_including_files_directory(self):
        self.write("main.md", "!include chapters/one.md\n")
        self.write("chapters/one.md", "# One\n\n!include sections/two.md\n")
        self.write("chapters/sections/two.md", "## Two\n\n!include ../common.md\n")
        self.write("chapters/common.md", "Common explanation.\n")
        self.write("common.md", "Wrong working directory.\n")
        document = self.document()
        self.assertEqual([text(item) for item in nodes(document, "Header")], ["One", "Two"])
        self.assertIn("Common explanation.", text(document))
        self.assertNotIn("Wrong", text(document))

    def test_root_input_can_live_outside_the_working_directory(self):
        self.write("notes/main.md", "!include chapter.md\n")
        self.write("notes/chapter.md", "# Located next to the root source\n")
        self.write("chapter.md", "# Incorrect sibling\n")
        document = self.document("notes/main.md")
        self.assertEqual(text(nodes(document, "Header")[0]),
                         "Located next to the root source")

    def test_quoted_and_angle_bracket_paths_with_spaces(self):
        self.write("main.md", '!include "chapters/first chapter.md"\n\n'
                   '!include <chapters/second chapter.md>\n')
        self.write("chapters/first chapter.md", "# First\n")
        self.write("chapters/second chapter.md", "# Second\n")
        document = self.document()
        self.assertEqual([text(item) for item in nodes(document, "Header")],
                         ["First", "Second"])

    def test_underscores_and_escaped_markdown_delimiters_in_paths(self):
        self.write("main.md", "!include chapter_01.md\n\n!include \\_notes\\_.md\n")
        self.write("chapter_01.md", "# First\n")
        self.write("_notes_.md", "# Second\n")
        document = self.document()
        self.assertEqual([text(item) for item in nodes(document, "Header")],
                         ["First", "Second"])

    def test_the_same_file_can_be_included_again_after_it_finishes(self):
        self.write("main.md", "!include shared.md\n\n!include shared.md\n")
        self.write("shared.md", "Repeated paragraph.\n")
        document = self.document()
        self.assertEqual([text(block) for block in document["blocks"]],
                         ["Repeated paragraph.", "Repeated paragraph."])

    def test_repeated_headings_keep_root_anchors_and_scope_child_links(self):
        self.write("main.md", "# Introduction\n\n[Root link](#introduction)\n\n"
                   "!include chapters/one.md\n\n!include chapters/two.md\n")
        for chapter in ("one", "two"):
            self.write(f"chapters/{chapter}.md", "# Introduction\n\n[This chapter](#introduction)\n")
        document = self.document()
        identifiers = [item["c"][1][0] for item in nodes(document, "Header")]
        self.assertEqual(identifiers[0], "introduction")
        self.assertEqual(len(set(identifiers)), 3)
        targets = [item["c"][2][0] for item in nodes(document, "Link")]
        self.assertEqual(targets, ["#" + identifier for identifier in identifiers])

    def test_repeated_includes_keep_their_own_explicit_anchor_links(self):
        self.write("main.md", "!include shared.md\n\n!include shared.md\n")
        self.write("shared.md", "# Shared heading {#shared-anchor}\n\n[Local link](#shared-anchor)\n")
        document = self.document()
        identifiers = [item["c"][1][0] for item in nodes(document, "Header")]
        self.assertEqual(identifiers[0], "shared-anchor")
        self.assertEqual(len(set(identifiers)), 2)
        targets = [item["c"][2][0] for item in nodes(document, "Link")]
        self.assertEqual(targets, ["#" + identifier for identifier in identifiers])

    def test_literal_code_inline_code_and_prose_are_not_directives(self):
        source = ('```markdown\n!include absent.md\n```\n\n'
                  '`!include absent.md`\n\n'
                  '!include `absent.md`\n\n'
                  '!include absent.md\nThis is **prose**, rather than a directive-only paragraph.\n\n'
                  'This paragraph mentions !include absent.md.\n')
        self.write("main.md", source)
        document = self.document()
        self.assertEqual(nodes(document, "CodeBlock")[0]["c"][1], "!include absent.md")
        self.assertEqual(nodes(document, "Code")[0]["c"][1], "!include absent.md")
        self.assertEqual(nodes(document, "Code")[1]["c"][1], "absent.md")
        self.assertEqual(text(nodes(document, "Strong")[0]), "prose")
        self.assertIn("This paragraph mentions !include absent.md.", text(document))

    def test_missing_file_fails_with_a_useful_path(self):
        self.write("main.md", "!include missing/chapter.md\n")
        result = self.run_pandoc()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("include.lua: cannot read included file", result.stderr)
        self.assertIn("missing/chapter.md", result.stderr)
        self.assertIn("include chain:", result.stderr)
        self.assertIn("main.md", result.stderr)

    def test_missing_file_in_existing_directory_reports_the_include(self):
        self.write("main.md", "!include chapters/absent.md\n")
        (self.project / "chapters").mkdir()
        result = self.run_pandoc()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("include.lua: cannot read included file", result.stderr)
        self.assertIn("chapters/absent.md", result.stderr)
        self.assertIn("include chain:", result.stderr)
        self.assertIn("main.md", result.stderr)

    def test_regular_file_in_parent_path_reports_the_include(self):
        self.write("main.md", "!include blocked/chapter.md\n")
        self.write("blocked", "This is a file, not a directory.\n")
        result = self.run_pandoc()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("include.lua: cannot read included file", result.stderr)
        self.assertIn("blocked/chapter.md", result.stderr)
        self.assertIn("include chain:", result.stderr)
        self.assertIn("main.md", result.stderr)

    def test_nested_missing_include_reports_its_parent_and_chain(self):
        self.write("main.md", "!include chapters/one.md\n")
        self.write("chapters/one.md", "!include missing/two.md\n")
        result = self.run_pandoc()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("include.lua: cannot read included file", result.stderr)
        self.assertIn("chapters/missing/two.md", result.stderr)
        self.assertIn("include chain:", result.stderr)
        self.assertIn("main.md", result.stderr)
        self.assertIn("chapters/one.md", result.stderr)

    def test_encoded_fragment_links_follow_renamed_unicode_headings(self):
        self.write("main.md", "# Caffè\n\n!include chapter.md\n")
        self.write("chapter.md", "# Caffè\n\n[Details](#caff%C3%A8)\n")
        document = self.document()
        headers = nodes(document, "Header")
        child_identifier = headers[1]["c"][1][0]
        self.assertNotEqual(headers[0]["c"][1][0], child_identifier)
        self.assertEqual(nodes(document, "Link")[0]["c"][2][0], "#" + child_identifier)

    def test_root_span_anchor_does_not_collide_with_included_heading(self):
        self.write("main.md", "[Root anchor]{#introduction}\n\n!include chapter.md\n")
        self.write("chapter.md", "# Introduction\n\n[Details](#introduction)\n")
        document = self.document()
        anchor = nodes(document, "Span")[0]["c"][0][0]
        heading = nodes(document, "Header")[0]["c"][1][0]
        self.assertEqual(anchor, "introduction")
        self.assertNotEqual(anchor, heading)
        self.assertEqual(nodes(document, "Link")[0]["c"][2][0], "#" + heading)

    def test_direct_cycle_fails_instead_of_recursing_forever(self):
        self.write("main.md", "!include main.md\n")
        result = self.run_pandoc()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("main.md", result.stderr)
        self.assertRegex(result.stderr.lower(), r"cycl|circular|recursive")

    def test_nested_cycle_reports_involved_files(self):
        self.write("main.md", "!include chapters/one.md\n")
        self.write("chapters/one.md", "!include two.md\n")
        self.write("chapters/two.md", "!include one.md\n")
        result = self.run_pandoc()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("one.md", result.stderr)
        self.assertIn("two.md", result.stderr)
        self.assertRegex(result.stderr.lower(), r"cycl|circular|recursive")

    def test_directory_alias_and_parent_segments_still_detect_cycles(self):
        self.write("main.md", "!include chapters/one.md\n")
        self.write("chapters/one.md", "!include ../alias/one.md\n")
        try:
            (self.project / "alias").symlink_to("chapters", target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Directory symlinks are unavailable: {error}")
        result = self.run_pandoc()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("include.lua: include cycle:", result.stderr)
        self.assertIn("main.md", result.stderr)
        self.assertIn("chapters/one.md", result.stderr)

    def test_empty_include_directives_fail_explicitly(self):
        for directive in ("!include", "!include <>", '!include ""'):
            with self.subTest(directive=directive):
                self.write("main.md", directive + "\n")
                result = self.run_pandoc()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("include", result.stderr.lower())

    def test_local_images_are_relative_to_the_included_markdown(self):
        self.write("main.md", "!include chapters/one.md\n")
        self.write("chapters/one.md", "![Local figure](assets/chart.svg)\n")
        expected = self.write("chapters/assets/chart.svg", '<svg xmlns="http://www.w3.org/2000/svg"/>')
        document = self.document()
        image = nodes(document, "Image")[0]
        target = image["c"][2][0]
        self.assertEqual((self.project / target).resolve(), expected.resolve())

    def test_remote_and_fragment_image_targets_remain_unchanged(self):
        self.write("main.md", "!include chapter.md\n")
        self.write("chapter.md", "![Remote](https://example.org/chart.svg)\n\n![Fragment](#chart)\n")
        document = self.document()
        self.assertEqual([item["c"][2][0] for item in nodes(document, "Image")],
                         ["https://example.org/chart.svg", "#chart"])

    def test_reader_extensions_are_used_for_included_files(self):
        self.write("main.md", "!include chapter.md\n")
        self.write("chapter.md", "$x^2$\n\n\\newcommand{\\example}{Example}\n")
        document = self.document(reader="markdown-tex_math_dollars+raw_tex")
        self.assertEqual(nodes(document, "Math"), [])
        self.assertIn("$x^2$", text(document))
        raw = nodes(document, "RawBlock")
        self.assertTrue(any(item["c"][0] == "tex" and "newcommand" in item["c"][1]
                            for item in raw))

    def test_root_metadata_survives_included_front_matter(self):
        self.write("main.md", '---\ntitle: Root title\nauthor: Root author\n---\n\n!include chapter.md\n')
        self.write("chapter.md", '---\ntitle: Child title\nauthor: Child author\n---\n\n# Chapter\n')
        document = self.document()
        self.assertEqual(text(document["meta"]["title"]), "Root title")
        self.assertEqual(text(document["meta"]["author"]), "Root author")
        self.assertEqual(text(nodes(document, "Header")[0]), "Chapter")

    def test_launcher_includes_before_project_filters_and_writes_tex(self):
        library = self.project / "library"
        template = library / "templates" / "minimal"
        template.mkdir(parents=True)
        (library / "bin").mkdir()
        (library / "filters").mkdir()
        shutil.copy2(LAUNCHER, library / "bin" / "generate-pdf.sh")
        shutil.copy2(FILTER, library / "filters" / "include.lua")
        (template / "template.tex").write_text("$body$\n", encoding="utf-8")
        (template / "defaults.yaml").write_text(
            "from: markdown\nto: latex\nstandalone: true\n", encoding="utf-8")
        self.write("main.md", "!include chapter.md\n")
        self.write("chapter.md", "# Included chapter\n\nA **Markdown** paragraph.\n")
        self.write("observer.lua", '''function Header(header)
  if pandoc.utils.stringify(header.content) == "Included chapter" then
    header.content = {pandoc.Str("Seen by project filter")}
    return header
  end
end
''')
        self.write("pandoc.yaml", "input-file: main.md\noutput-file: build/notes.tex\n"
                   "filters:\n  - observer.lua\n")
        environment = os.environ.copy()
        environment["TEX_TEMPLATES_HOME"] = str(library)
        result = subprocess.run(
            [str(library / "bin" / "generate-pdf.sh"), "--project", str(self.project),
             "--template", "minimal"],
            cwd=self.project, env=environment, capture_output=True, text=True,
            encoding="utf-8", timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        generated = (self.project / "build" / "notes.tex").read_text(encoding="utf-8")
        self.assertIn("Seen by project filter", generated)
        self.assertIn(r"\textbf{Markdown}", generated)
        self.assertNotIn("!include", generated)


if __name__ == "__main__":
    unittest.main()
