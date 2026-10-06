# TeX Templates Library

A library of independently versioned Pandoc LaTeX templates, shared PDF build
commands, and release automation. Template repositories are Git submodules in
the development checkout. Published library archives contain the selected
templates expanded from their own release packages.

The library is licensed under GNU GPL version 3 or, at your option, any later
version. See [LICENSE](LICENSE).

## Build a document locally

Install Pandoc 3.1 or later, LuaLaTeX, and the TeX packages required by the selected template.
The requirements files use native TeX Live / `tlmgr` package names.

```sh
bin/template-list.sh
bin/template-use.sh handouts
bin/generate-pdf.sh --project "/path/to/course"
```

Each course supplies `pandoc.yaml`, `pandoc.yml`, `defaults.yaml`, or
`defaults.yml`. Template defaults are loaded first, followed by course defaults
and command-line options. The selected template's `environment.sh` is sourced
within the build process.

Selection order is `--template`, the project's `.tex-template`, and the global
default. To pin a template for a course:

```sh
bin/template-use.sh --project "/path/to/course" handouts
```

Build logs are saved in the course's `build/pandoc-generation.log`.

## Include Markdown chapters

Keep one master file for each volume and reference its chapters with standalone
`!include` directives. The shared `filters/include.lua` is loaded automatically
by `generate-pdf.sh`, before any filters configured by the template or course.
It expands the included Markdown into Pandoc blocks before the LaTeX writer
runs. Pandoc provides the Lua interpreter; no separate Lua installation or
preprocessing script is required.

For example, a course's `volume.md` can contain:

```markdown
---
title: Database Systems
author: Giulio Salvi
---

\part{Foundations}

!include chapters/01-relational-model.md

!include chapters/02-relational-algebra.md
```

Point the course defaults at the master file:

```yaml
input-file: volume.md
output-file: build/volume.pdf
```

Then run `generate-pdf.sh --template handouts` as usual.

Paths are relative to the file containing the directive, including nested
inclusions. Paths containing spaces can be quoted, such as
`!include "chapters/01 relational model.md"`; `!include <chapter.md>` is also
accepted. Markdown still parses these lines first: escape Markdown punctuation
in filenames when necessary, for example `!include \_chapter\_.md`. Put each
directive on its own line and separate it from surrounding prose with blank
lines. Consecutive directive-only lines are supported too.

Headings, lists, mathematics, and raw TeX remain Pandoc content. Local Markdown
images and links in included chapters are resolved against their chapter's
directory. The master document's metadata remains authoritative; included YAML
metadata is discarded. Repeated identifiers from different chapters receive
unique suffixes, and local fragment links within each chapter follow those
identifiers. Missing files, recursive cycles, ambiguous multiple input
directories, and excessive nesting produce errors with the include chain.
Code blocks and inline code examples remain literal.

The filter supports Pandoc's `markdown` reader and its enabled or disabled
extensions. When calling Pandoc directly, enable it explicitly:

```sh
pandoc volume.md --from=markdown+raw_tex \
  --lua-filter="$TEX_TEMPLATES_HOME/filters/include.lua" \
  --to=latex --output=build/volume.tex
```

Set `TEX_TEMPLATES_HOME` to the installed library directory for this direct
command. Shared filters are included in new library archives and therefore in
Docker images built from those releases. Existing images retain the library
version with which they were built.

## Release model

1. A template tag such as `v0.1.0` triggers that repository's release workflow.
   It packages the runtime files, verifies the package, and publishes an archive,
   metadata JSON, and checksums.
2. After publication, the template sends a `template-released`
   `repository_dispatch` event to this library.
3. The library verifies the notification against its registered submodules,
   updates the selected submodule to the released commit, and records the update
   in Git.
4. The same workflow downloads the release packages matching all selected
   submodule commits, verifies them, assembles the complete library, and
   publishes a library release.
5. After publication, or when the collection is already published, the release
   workflow explicitly dispatches the separate Docker image workflow for that
   library version. Image build results appear in their own Actions execution.

The committed submodule references select the versions. `templates.lock.json`
is generated from that selection and included in each published release. It
records the exact template versions, repositories, commits, and archive hashes.

The library uses stable three-part versions. Its first automatic version is
`v0.1.0`; subsequent automatic releases increment the patch version. A manual
workflow run can choose a greater version for a minor or major change.

Updating submodules manually and pushing to `main` also starts publication.
The first push that creates `main` only bootstraps the repository; publication
starts with a template notification or a manual workflow run. Selected commits
must have corresponding, published template packages.

The library release contains:

- `tex-templates-X.Y.Z.tar.gz`, with `bin/`, shared `filters/`, and expanded `templates/`;
- `templates.lock.json`;
- `SHA256SUMS`.

Template examples, Git metadata, and maintenance workflows are excluded from
the distributed library. The CI uses examples from the development checkout to
verify the extracted package.

## Build automation locally

Python 3.12+, Git, and authenticated GitHub CLI (`gh`) are used by the library's
release helper. Its help describes the available commands:

```sh
python3 scripts/library-release.py --help
```

The helper can calculate the next version, process a notification, assemble a
release, check whether a collection was already published, and publish or
resume a validated draft. Local tests run
in temporary repositories and do not publish releases.

## Docker images

The separate **Build and publish Docker images** workflow builds a selected,
already published library release. It downloads the release archive and lock
file, verifies their checksums, and prepares the expanded runtime under
`/opt/tex-templates`. The image includes Pandoc, native TeX Live with the
library's package requirements, and an unprivileged `vscode` user. The library's
commands are on `PATH`, and TeX caches live in the user's writable home.
Pandoc's Lua interpreter is built in; a separate Lua installation is not needed
for normal Pandoc filters.

AMD64 and ARM64 images are built on native GitHub runners. Each image compiles
the templates' examples with its unprivileged account. Example sources come
from the exact child commits recorded in the release lock; they remain outside
the image. Only after both architectures pass are the version tags published
to both registries:

```text
giuliosalvi485/tex-templates:0.1.0
ghcr.io/giuliosalvi/tex-templates:0.1.0
```

Docker Hub hosts the project's public image under the account's namespace.

Library releases and image builds remain separate workflows. The release
workflow explicitly uses `workflow_dispatch` with its effective published tag.
It does not rely on a `release: published` event, because a release created with
`GITHUB_TOKEN` does not start another workflow through that event. A failed image
build leaves the published library release available and can be retried
independently.

### Use an image in a course Dev Container

Create `.devcontainer/devcontainer.json` in the course repository:

```json
{
  "name": "University handouts",
  "image": "giuliosalvi485/tex-templates:0.1.0",
  "remoteUser": "vscode"
}
```

VS Code mounts the course repository in the container. Keep Markdown, course
defaults, and figures in that repository, then run:

```sh
generate-pdf.sh --template handouts
```

Pin the image version in each course. The GHCR reference shown above can be used
in the same configuration when its package is public or your Docker client is
authenticated. Courses requiring additional tools can use a Dockerfile based
on this image.

## Add another template

Register its repository as `templates/<id>` with `git submodule add`, using a
GitHub HTTPS URL. The repository must publish packages using the same metadata
contract as `handouts`, with its own ID and repository name. Configure its
`LIBRARY_REPOSITORY` variable and `LIBRARY_DISPATCH_TOKEN` secret to notify this
library after a stable release is ready.
