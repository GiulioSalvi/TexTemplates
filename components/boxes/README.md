# Shared boxes

This is a reusable TeX component, distributed and versioned with the TeX Templates library under GNU GPL version 3 or later. Its dependencies are included in the library's combined `requirements.txt`.

`generate-pdf.sh` loads `tt-boxes` automatically when a document uses its box commands or environments. Templates remain selectable under `templates/`; reusable components live under `components/`.

## Public interfaces

- Semantic environments: `propertybox`, `definitionbox`, `theorembox`, `axiombox`, `lemmabox`, `observationbox`, and `tipbox`.
- Supporting environments: `sidebarsection`, `proofsection`, and `timeanalysissection`.
- Presentation box: `\TransparentBox[image-path]{Title}{Body}`.
- Existing helpers such as `\beginbox`, `\endbox`, and the semantic counter/title commands remain available.

Semantic counters reset by chapter in books and by section in documents without chapters, including Beamer. English and Italian semantic names follow Babel. The presentation box uses the slide heading font when available and the document's bold font otherwise.

## Project metadata

```yaml
lang: it
box-image-diameter: 8mm
box-padding: 4pt
box-title-padding: 3pt
```

The diameter controls both image size and positioning. The title covers the bottom tenth of the circular image. The padding reserves enough space to keep the title from overlapping body text.

For boxes hidden inside external TeX macros or inputs, set `boxes: true` to request loading explicitly. With direct LaTeX/Pandoc invocations outside the library launcher, add this component directory to `TEXINPUTS` and load `\usepackage{tt-boxes}`.

The package provides the familiar semantic palette and `BoxBorder`/`BoxTitle`. Existing project color definitions are preserved. Header includes may override the public macros with `\renewcommand` after the package is loaded.
