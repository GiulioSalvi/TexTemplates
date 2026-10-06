# TeX Templates Library

A library of independently versioned Pandoc LaTeX templates, shared PDF build
commands, and release automation. Template repositories are Git submodules in
the development checkout. Published library archives contain the selected
templates expanded from their own release packages.

The library is licensed under GNU GPL version 3 or, at your option, any later
version. See [LICENSE](LICENSE).

## Build a document locally

Install Pandoc, LuaLaTeX, and the TeX packages required by the selected template.
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

- `tex-templates-X.Y.Z.tar.gz`, with `bin/` and expanded `templates/`;
- `templates.lock.json`;
- `SHA256SUMS`.

Template examples, Git metadata, and maintenance workflows are excluded from
the distributed library. The CI uses examples from the development checkout to
verify the extracted package.

## GitHub setup

1. Create an empty GitHub repository for the library, using `main` as its default
   branch. For example, you can name it `TexTemplates`. Publish this local Git
   repository to it. Leave GitHub's automatic README/license initialization off
   so the local history can be pushed directly.
2. In `HandoutsTexTemplate`, open **Settings → Secrets and variables → Actions**.
   Add the repository variable `LIBRARY_REPOSITORY`, containing the parent's
   full name, for example `GiulioSalvi/TexTemplates`.
3. Create a fine-grained personal access token that can access only the library
   repository and has **Contents: Read and write**. Store it as the Actions
   secret `LIBRARY_DISPATCH_TOKEN` in `HandoutsTexTemplate`. A GitHub App
   installation token with equivalent access can be used instead if supplied
   by a suitable token-generation step.
4. Enable GitHub Actions in both repositories and allow the pinned official
   actions used by the workflows. The workflows request their own
   `contents: write` permission for commits and releases. Branch rules must
   allow the library workflow to record the submodule update on `main`.
5. Publish the workflow changes in the template repository before creating its
   first release tag. The parent workflows must already exist on its default
   branch when the notification arrives.

Once both repositories and the variable/secret are ready, create the first
template release from the template repository:

```sh
git tag v0.1.0
git push origin v0.1.0
```

Creating the tag starts the workflow; it creates and publishes the GitHub
release after its package has passed validation. You can also run the template
release workflow manually for an existing tag to retry publication or resend
the notification. The library release workflow is likewise available from the
Actions **Run workflow** button.

Retries skip a library collection that was already published and resume a
matching interrupted draft from its original source commit and version. Stale
notifications do not move a selected template back to an older release.

GitHub release immutability is optional. If enabled, assets are attached to a
draft before it is published. Published assets are not replaced on retry.

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

## Docker consumers

A Docker build can consume a chosen `tex-templates-X.Y.Z.tar.gz`, verify it using
the release checksum, and install its expanded library under
`/opt/tex-templates`. Set `TEX_TEMPLATES_HOME` to that directory and add its
`bin/` directory to `PATH`. Install Pandoc and a coherent TeX Live environment
with the selected templates' requirements.

This release automation publishes the library archives. A Docker image build
and registry publication can consume them independently. Pin the library
release and the toolchain/base-image versions in that Docker build.

## Add another template

Register its repository as `templates/<id>` with `git submodule add`, using a
GitHub HTTPS URL. The repository must publish packages using the same metadata
contract as `handouts`, with its own ID and repository name. Configure its
`LIBRARY_REPOSITORY` variable and `LIBRARY_DISPATCH_TOKEN` secret to notify this
library after a stable release is ready.
