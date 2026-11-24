# TeX Templates - Copyright (C) 2026 Giulio Salvi
# GNU GPL v3 or later; without warranty. See LICENSE.
# SPDX-License-Identifier: GPL-3.0-or-later
import base64
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

LIBRARY=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('mermaid_filter',LIBRARY/'filters/mermaid.py')
mermaid=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(mermaid)


class MermaidTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='mermaid-tests-');self.addCleanup(temporary.cleanup)
        self.project=Path(temporary.name)/'project with spaces';self.project.mkdir()
        self.context=patch.object(mermaid,'PROJECT',self.project);self.context.start();self.addCleanup(self.context.stop)
    def test_local_images_are_embedded_and_changes_invalidate_the_input(self):
        asset=self.project/'image with spaces.png';asset.write_bytes(b'first')
        before=mermaid.embed_assets("<img src='image with spaces.png'>")
        self.assertIn(base64.b64encode(b'first').decode(),before)
        asset.write_bytes(b'second')
        self.assertNotEqual(before,mermaid.embed_assets("<img src='image with spaces.png'>"))
    def test_native_image_nodes_and_remote_images(self):
        (self.project/'image.png').write_bytes(b'image')
        self.assertIn('data:image/png;base64,',mermaid.embed_assets('A@{img: "image.png"}'))
        remote='<img src="https://example.org/image.png">'
        self.assertEqual(mermaid.embed_assets(remote),remote)
    def test_images_from_included_markdown_use_the_included_directory(self):
        child=self.project/'chapters';child.mkdir()
        (child/'image.png').write_bytes(b'included image')
        result=mermaid.embed_assets("<img src='image.png'>",child)
        self.assertIn(base64.b64encode(b'included image').decode(),result)

    def test_missing_image_reports_its_path(self):
        with self.assertRaisesRegex(ValueError,'absent.png'):
            mermaid.embed_assets("<img src='absent.png'>")
    def test_non_mermaid_code_and_prose_are_unchanged(self):
        block={'t':'CodeBlock','c':[['',['python'],[]],'print(1)']}
        self.assertEqual(mermaid.walk(block),block)
    def test_mermaid_block_becomes_a_centered_image(self):
        block={'t':'CodeBlock','c':[['',['mermaid'],[]],'flowchart LR\nA-->B']}
        with patch.object(mermaid,'render',return_value='/generated/diagram.pdf') as render:
            result=mermaid.walk(block)
        self.assertEqual(result['t'],'Div')
        self.assertEqual(result['c'][1][1]['c'][0]['t'],'Image')
        self.assertEqual(result['c'][1][1]['c'][0]['c'][2][0],'/generated/diagram.pdf')
        render.assert_called_once_with(block['c'][1])


class ComponentsTests(unittest.TestCase):
    def latex(self,text):
        result=subprocess.run(['pandoc','-f','markdown+raw_tex','-t','latex','-s','--lua-filter',str(LIBRARY/'filters/components.lua')],input=text,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr);return result.stdout
    def test_shared_boxes_load_only_when_used(self):
        self.assertNotIn('usepackage{tt-boxes}',self.latex('Ordinary text.'))
        self.assertIn('usepackage{tt-boxes}',self.latex('\\TransparentBox{Title}{Text}\n'))
        self.assertIn('usepackage{tt-boxes}',self.latex('\\begin{definitionbox}\nBody\n\\end{definitionbox}\n'))
    def test_existing_headers_and_dimensions_are_preserved(self):
        text='---\nbox-image-diameter: 12mm\nheader-includes:\n  - \\newcommand{\\Custom}{value}\n---\n\n\\TransparentBox{Title}{Text}\n'
        output=self.latex(text)
        self.assertIn('newcommand{\\Custom}{value}',output)
        self.assertIn('renewcommand{\\BoxImageDiameter}{12mm}',output)
        self.assertLess(output.index('usepackage{tt-boxes}'),output.index('newcommand{\\Custom}'))
