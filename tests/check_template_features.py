#!/usr/bin/env python3
# TeX Templates - Copyright (C) 2026 Giulio Salvi
# GNU GPL v3 or later; without warranty. See LICENSE.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compile actual fixtures for shared components, localization and Mermaid."""
import os
import shutil
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import zlib

LIBRARY = Path(os.environ.get('TEX_TEMPLATES_HOME', str(Path(__file__).resolve().parents[1])))
LAUNCHER = LIBRARY / 'bin/generate-pdf.sh'


def image(path):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind+data))
    pixels = b''.join(b'\x00' + b'\x50\x80\xc0'*48 for _ in range(24))
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB',48,24,8,2,0,0,0)) + chunk(b'IDAT',zlib.compress(pixels)) + chunk(b'IEND',b''))


def run(project, template, environment):
    result = subprocess.run([str(LAUNCHER),'--project',str(project),'--template',template],env=environment,capture_output=True,text=True)
    if result.returncode:raise RuntimeError(result.stdout+result.stderr)
    pdf = project/'build/result.pdf'
    assert pdf.read_bytes().startswith(b'%PDF-')
    if os.environ.get('TEX_TEMPLATES_TEST_OUTPUT'):
        output = Path(os.environ['TEX_TEMPLATES_TEST_OUTPUT']); output.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pdf, output / (project.name + '.pdf'))
    result = subprocess.run(['pdftotext','-layout',str(pdf),'-'],capture_output=True,text=True,check=True)
    return result.stdout


def main():
    with tempfile.TemporaryDirectory(prefix='template-features-') as temporary:
        root = Path(temporary)
        environment = dict(os.environ, TEX_TEMPLATES_HOME=str(LIBRARY), TEXMFVAR=str(root/'texmf-var'), TEXMFCONFIG=str(root/'texmf-config'), TEXMFCACHE=str(root/'texmf-var'))
        for template in ['handouts','slides']:
            for language,label in [('en','Figure'),('it','Figura'),('en-GB','Figure'),('it-IT','Figura')]:
                project=root/f'{template}-{language}';project.mkdir();image(project/'figure.png')
                (project/'defaults.yaml').write_text("from: markdown+raw_tex\ninput-file: main.md\noutput-file: build/result.pdf\nnumber-sections: true\nmetadata-file: metadata.yml\n")
                (project/'metadata.yml').write_text(f'lang: {language}\nsection-pages: true\nsection-page-label: false\n')
                source='''# Shared features

## Semantic boxes

```{=latex}
\\begin{definitionbox}{Sample}
Definition body.\\label{sample}
\\end{definitionbox}
\\begin{lemmabox}
Lemma body.
\\end{lemmabox}
```

## Presentation box

```{=latex}
\\TransparentBox[figure.png]{Shared title}{Shared body}
```

## Image caption

![Caption text](figure.png){width=20%}
'''
                (project/'main.md').write_text(source)
                text=run(project,template,environment)
                assert re.search(r'\b'+label+r'\s+\d',text),(template,language,text)
                assert 'Shared title' in text and 'Shared body' in text
                assert ('Definizione' if language=='it' else 'Definition') in text
                assert 'Section 1' not in text and 'Sezione 1' not in text
                if template=='slides' and 'SlidesSectionPageLabel' in (LIBRARY/'templates/slides/template.tex').read_text():
                    assert 'Shared features' in text
                print(f'Compiled {template}/{language}: shared boxes and {label} captions')
        project=root/'section-label';project.mkdir()
        (project/'defaults.yaml').write_text('from: markdown+raw_tex\ninput-file: main.md\noutput-file: build/result.pdf\n')
        (project/'main.md').write_text('---\nlang: en\nsection-pages: true\n---\n# Labelled section\n\n## Body\n\nContent\n')
        text=run(project,'slides',environment)
        if 'SlidesSectionPageLabel' in (LIBRARY/'templates/slides/template.tex').read_text():
            assert 'Section 1' in text and 'Labelled section' in text
            print('Verified the default numbered section card')
        project=root/'mermaid';project.mkdir();image(project/'figure.png')
        (project/'defaults.yaml').write_text('from: markdown+raw_tex\ninput-file: main.md\noutput-file: build/result.pdf\n')
        (project/'main.md').write_text('''## Diagram

```mermaid
flowchart LR
    source[Source] --> result[PDF]
```
''')
        text=run(project,'slides',environment)
        assert 'Source' in text and 'PDF' in text
        cached=next((project/'build/mermaid').glob('*.pdf'));stamp=cached.stat().st_mtime_ns
        run(project,'slides',environment)
        assert cached.stat().st_mtime_ns==stamp
        print('Compiled inline Mermaid and reused its cached PDF')


if __name__=='__main__':main()
