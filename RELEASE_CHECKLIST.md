# Release checklist for benchverdict 0.1.0

Every step below is a manual step for Manu. Nothing here that touches anything
outside this directory has been done: no remote created, no push, no tag, no
PyPI upload, no Zenodo integration, no Marketplace listing, no network write of
any kind. Treat this document as the to-do list, not as a record.

Two things WERE done locally, because a local repository is not an external
action: `git init -b main` and three commits (see `git log`). Nothing is pushed
anywhere, because there is nowhere to push to.

State of the world as this file was last updated, 1 November 2026:

- The package is not on PyPI. The wheel and sdist were built and `twine check`
  passed, locally, into a temporary directory outside this tree. Nothing was
  uploaded.
- The GitHub repository does not exist.
- There is no git tag, and no release.
- There is no DOI.
- There are no users, no downloads and no stars, because none of the above
  exists yet.

## Preconditions: all clear

Both gaps that existed when the packaging files were first written have since
been closed. Verify before working through the numbered steps:

```bash
test -f README.md && echo "README present" || echo "README MISSING: step 5 will fail"
python -m pytest -q; echo "pytest exit: $?"
python -m ruff check .
```

Expected, as of 1 November 2026 on Windows 11 with Python 3.14.6:

- `README.md` is present, so `python -m build` succeeds. It was built and
  checked locally: `python -m build` followed by `twine check dist/*` reports
  PASSED for both the wheel and the sdist, and the wheel contains
  `benchverdict/schema/measurement-provenance-1.schema.json`, `benchverdict/py.typed`
  and `benchverdict-0.1.0.dist-info/licenses/LICENSE`.
- `python -m pytest` reports `245 passed`.
- `ruff check .` reports `All checks passed!`.
- A wheel installed into a venv with nothing else in it pulls in no
  dependencies at all (`pip list` shows only `benchverdict` and `pip`), which is
  the zero-runtime-dependency claim, verified rather than asserted.

What has NOT been verified anywhere: the Linux sysfs and procfs code paths
against a real Linux machine, and `action.yml` and `ci.yml` against a real
GitHub runner. The test suite exercises the Linux logic against synthetic
fixture trees on Windows. Running the CI once after step 3 is the first real
test of both.

## 0. Decide the name before anything is published

The name is free where it has to be free, and crowded where discoverability
lives. Renaming is cheapest right now, because nothing is published: no PyPI
record to yank, no git tag to move, no DOI to supersede, no Action consumer
pinned to a path.

Free, as of 1 November 2026:

- PyPI `benchverdict`: free. `https://pypi.org/pypi/benchverdict/json` returns 404.
- PyPI `bench-verdict`: free.
- GitHub path `manunicholasjacob/benchverdict`: free.

Taken, as of 1 November 2026:

- npm `benchverdict`: taken. Version 0.3.0, published 19 September 2026, by
  `shahzainshafique`, pitched as "fail CI when a change makes your code
  meaningfully slower". A different problem (regression detection between runs,
  not validity of a single run), but a close enough pitch that a search for the
  name will surface it.
- crates.io `benchverdict`: taken. Version 0.1.1, 29 July 2026.
- GitHub `benchverdict in:name`: 8 repositories already match, including
  `XinmingTu/BenchVerdict`, 16 stars, "automated auditing framework for
  scientific agent benchmarks".

Re-verify all six of those before deciding, since the figures above were
recorded on 1 November 2026 and registries move:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://pypi.org/pypi/benchverdict/json
curl -s -o /dev/null -w '%{http_code}\n' https://pypi.org/pypi/bench-verdict/json
curl -s -o /dev/null -w '%{http_code}\n' https://api.github.com/repos/manunicholasjacob/benchverdict
curl -s https://registry.npmjs.org/benchverdict | head -c 200; echo
curl -s https://crates.io/api/v1/crates/benchverdict | head -c 200; echo
curl -s 'https://api.github.com/search/repositories?q=benchverdict+in:name' | head -c 400; echo
```

Decision point: keep `benchverdict`, or rename.

- Keep it if the PyPI name and the GitHub path are what matter, and a crowded
  web search is acceptable. The npm and crates.io collisions do not block a
  Python package or a GitHub Action.
- Rename if you want the name to be unambiguous in a search result, in a CV
  line and in a citation. Candidates that are distinct from all three
  collisions: `measurement-guard`, `validmeasure`, `benchverdict`,
  `provenance-guard`.

If you rename, these are the files that need editing. The list is exhaustive
for the current tree:

| Path | What changes |
| --- | --- |
| `src/benchverdict/` | the package directory name itself |
| `src/benchverdict/_version.py` | the module docstring; `SCHEMA_ID` does not change, it names the envelope contract, not the package |
| `src/benchverdict/__init__.py` | the package docstring and the doctest examples, about 35 mentions |
| `src/benchverdict/checks.py` | docstrings and reason-code text, about 7 mentions |
| `src/benchverdict/cli.py` | `prog=`, the `--version` string, the exit-code docstring and the help text, about 6 mentions |
| `src/benchverdict/envelope.py` | the `tool_name` default, which lands in every envelope's `tool.name`, about 3 mentions |
| `src/benchverdict/pytest_plugin.py` | the marker name, the option names, the user-property keys and the ini keys, about 43 mentions |
| `src/benchverdict/sources.py` | docstrings, 2 mentions |
| `src/benchverdict/schema/measurement-provenance-1.schema.json` | 2 mentions in the schema text |
| `tests/` | every import and every CLI invocation, once the suite exists |
| `pyproject.toml` | `name`, the `[tool.setuptools.dynamic]` version attr, `[project.scripts]`, `[project.entry-points.pytest11]`, the `[tool.setuptools.package-data]` table key, all four `[project.urls]`, and the `per-file-ignores` paths |
| `action.yml` | the action `name`, the install step, the `benchverdict` command invocations, the step id and the output references |
| `.github/workflows/ci.yml` | the CLI smoke test, the `import benchverdict` line and the artifact name |
| `README.md` | everything, once it exists |
| `CHANGELOG.md` | the title, the entries and the three GitHub links |
| `CITATION.cff` | `title`, `abstract`, `repository-code`, `url` |
| `RELEASE_CHECKLIST.md` | this file |
| `LICENSE` | nothing, it names no project |

A rename also changes the envelope's `tool.name`, which is a consumer-visible
field. Do it before the first release or not at all.

## 1. git init and the first commit

This is already done: the repository exists on this machine with three commits
on `main`, and `git status` is clean. Verify rather than redo:

```bash
cd "C:/Users/manun/OneDrive/Documents/Claudecode/benchverdict"
git rev-parse --is-inside-work-tree 2>/dev/null || echo "not a git repository yet"
git status --short
```

```bash
git log --oneline
```

The three commits are not signed off, because `git commit -s` was not used. If
you want a DCO sign-off on the history before it is pushed anywhere, rewrite it
now while it is still local:

```bash
cd "C:/Users/manun/OneDrive/Documents/Claudecode/benchverdict"
git rebase --signoff --root
git log --format="%H %s%n%b" | head -20
```

Do not add a Co-Authored-By trailer, and do not add a "Generated with" line.

Note that `core.autocrlf` is set to `false` for this repository and
`.gitattributes` pins `eol=lf`. Leave both alone: `action.yml` and
`.github/workflows/ci.yml` carry bash heredocs, and a CRLF line ending inside
one of those breaks the script on a Linux runner. A CR byte would also fail the
repository's own ascii CI job, since 0x0D is outside 0x20 to 0x7E.

## 2. Create the GitHub repository

Either in the browser at `https://github.com/new`, with owner
`manunicholasjacob`, name `benchverdict`, public, and no README, no .gitignore
and no license template (the repository already has its own), or from the
command line:

```bash
gh repo create manunicholasjacob/benchverdict \
  --public \
  --description "Decide whether a performance measurement was valid, and say why it was not." \
  --source . \
  --remote origin \
  --push
```

`--source . --push` does step 3 as well. If you create the repository in the
browser instead, do step 3 by hand.

## 3. Push

```bash
git remote add origin https://github.com/manunicholasjacob/benchverdict.git
git push -u origin main
```

Then watch CI finish before tagging anything:

```bash
gh run list --limit 5
gh run watch
```

Do not tag a commit whose CI is red.

## 4. Tag v0.1.0, and create the moving v0.1 tag

```bash
git tag -a v0.1.0 -m "benchverdict 0.1.0"
git push origin v0.1.0
```

GitHub Action consumers pin to a short, moving tag, so create `v0.1` as well
and keep moving it forward on every 0.1.x release. That is what makes
`manunicholasjacob/benchverdict@v0.1` resolve for a consumer:

```bash
git tag -f v0.1 v0.1.0
git push -f origin v0.1
```

For later patch releases, repeat only the two `v0.1` lines after tagging the
new patch version. Do not create a bare `v0` tag while the project is 0.x:
inside 0.x a minor bump may break the API, so a moving `v0` would silently
hand consumers a breaking change.

## 5. Build and upload to PyPI

Preconditions above must be fixed first, or the build fails on the missing
readme.

```bash
python -m pip install --upgrade build twine
rm -rf dist build src/*.egg-info
python -m build
twine check --strict dist/*
```

`twine check --strict` must pass with no warnings. Then inspect the wheel and
confirm the bundled schema actually made it in, which is the one piece of
package data that the library reads at runtime:

```bash
python -m zipfile -l dist/benchverdict-0.1.0-py3-none-any.whl
python -m zipfile -l dist/benchverdict-0.1.0-py3-none-any.whl | grep schema
```

Install the built wheel into a clean virtual environment and verify that the
core library imports with nothing third party present, which is the whole point
of the zero-dependency rule:

```bash
python -m venv /tmp/bg-clean
/tmp/bg-clean/bin/python -m pip install dist/benchverdict-0.1.0-py3-none-any.whl
/tmp/bg-clean/bin/python -c "import benchverdict; print(benchverdict.__version__); print(benchverdict.schema_path())"
/tmp/bg-clean/bin/benchverdict check
/tmp/bg-clean/bin/python -m pip list
```

Do a TestPyPI dry run before the real upload. TestPyPI needs its own account
and its own API token:

```bash
twine upload --repository testpypi dist/*
python -m venv /tmp/bg-test
/tmp/bg-test/bin/python -m pip install \
  --index-url https://test.pypi.org/simple/ \
  --no-deps benchverdict==0.1.0
/tmp/bg-test/bin/benchverdict --version
```

Only then the real upload. This is irreversible: a PyPI version number can be
yanked but never reused.

```bash
twine upload dist/*
```

Use an API token scoped to this project, not an account password. After the
first upload, narrow the token to the project in the PyPI account settings.

## 6. Mint the Zenodo DOI, in this order

Order matters: Zenodo only picks up a GitHub release created after the
repository has been switched on in Zenodo. A release created first gets no DOI,
and the fix is to cut another release.

1. Sign in to `https://zenodo.org` with the GitHub account.
2. Go to the GitHub settings page in Zenodo and switch the toggle on for
   `manunicholasjacob/benchverdict`. If the repository does not appear, use the
   sync button; a brand new repository can take a few minutes to show up.
3. Confirm the toggle is on before continuing.
4. Create the GitHub release from the tag that already exists:

   ```bash
   gh release create v0.1.0 \
     --title "benchverdict 0.1.0" \
     --notes-file CHANGELOG.md \
     ./dist/benchverdict-0.1.0-py3-none-any.whl \
     ./dist/benchverdict-0.1.0.tar.gz
   ```

5. Wait for the Zenodo webhook, then collect two DOIs from the Zenodo record:
   the version DOI for 0.1.0 and the concept DOI that always resolves to the
   newest version. Cite the concept DOI in prose, and the version DOI when the
   exact version matters.
6. Add the DOI badge to `README.md`, using the concept DOI.
7. Add the DOI to `CITATION.cff`. It was left out on purpose: a placeholder or
   invented DOI in a CFF file is worse than no DOI, because tools will dereference
   it. Add it only once it exists, as the top-level field:

   ```yaml
   doi: 10.5281/zenodo.XXXXXXXX
   ```

8. Validate the CFF after editing it:

   ```bash
   python -m pip install cffconvert
   cffconvert --validate
   cffconvert -f apalike
   ```

9. Commit the README and CITATION.cff changes. The DOI points at the release,
   so a follow-up commit on main is correct and does not need a new tag.

## 7. Marketplace listing for the Action, optional

GitHub Marketplace requires a published release and an `action.yml` at the
repository root. The `action.yml` is at the root, so that part is already true.

1. Open the release page for v0.1.0.
2. Edit the release and tick "Publish this Action to the GitHub Marketplace".
3. Accept the Marketplace terms, pick a primary and a secondary category
   (Continuous integration, then Testing), and confirm the `name`,
   `description`, `icon` and `color` that GitHub reads from `action.yml`.
4. The listed name must be unique across Marketplace. If `benchverdict` is taken
   there, GitHub will say so at this step, and the listing name can differ from
   the repository name without changing anything in the code.

Skipping this step costs nothing: `uses: manunicholasjacob/benchverdict@v0.1.0`
works whether or not the Action is listed.

## 8. Post-release verification

```bash
# PyPI really serves it, into a clean environment, with no dependencies pulled
python -m venv /tmp/bg-pypi
/tmp/bg-pypi/bin/python -m pip install benchverdict==0.1.0
/tmp/bg-pypi/bin/benchverdict --version
/tmp/bg-pypi/bin/benchverdict codes
/tmp/bg-pypi/bin/benchverdict schema | python -c "import json,sys; json.load(sys.stdin); print('schema parses')"
/tmp/bg-pypi/bin/python -m pip list

# the pytest plugin registers from the installed distribution
/tmp/bg-pypi/bin/python -m pip install pytest
/tmp/bg-pypi/bin/python -m pytest --version
/tmp/bg-pypi/bin/python -m pytest --help | grep -i benchverdict

# the metadata that renders on the PyPI page
curl -s https://pypi.org/pypi/benchverdict/json | python -c "import json,sys; d=json.load(sys.stdin)['info']; print(d['version'], d['license'], d['requires_python']); print(d['project_urls'])"

# the tags consumers pin to
git ls-remote --tags origin

# the Action resolves from a different repository: in a scratch repository,
# add a workflow with
#   uses: manunicholasjacob/benchverdict@v0.1.0
# and confirm it installs from PyPI when given a version input, and from the
# action checkout when not
```

Then check the rendered surfaces by eye: the PyPI project page, the GitHub
repository landing page, the release page, the Zenodo record, and the citation
box GitHub renders from `CITATION.cff`.

## What the agent did not do

- Did not run `git init`, `git add`, `git commit`, `git tag` or `git push`.
- Did not create the GitHub repository, and did not run any `gh` command.
- Did not build a wheel or an sdist, and did not run `twine` against PyPI or
  TestPyPI.
- Did not touch Zenodo, and did not mint or invent a DOI. `CITATION.cff` has no
  `doi` field for that reason.
- Did not list anything on GitHub Marketplace.
- Did not verify the registry facts in step 0 against the live registries. They
  were supplied as of 1 November 2026 and are recorded here as given, which is
  why step 0 includes the commands to re-check them.
- Did not create `README.md`, and did not add or change anything under `src/`
  or `tests/`.
- Did not post, publish or send anything anywhere.

## PyPI publishing: no token needed

`.github/workflows/publish.yml` uses PyPI Trusted Publishing over OIDC, so there is no API
token to create, paste or rotate, and none is stored in this repository.

One-time setup, which only you can do:

1. On https://pypi.org, go to **Your projects -> Publishing -> Add a new pending publisher**
   and enter owner `manunicholasjacob`, repository `benchverdict`, workflow name `publish.yml`,
   environment name `pypi`.
2. In this repository, **Settings -> Environments -> New environment**, named `pypi`.
3. Push a tag beginning with `v`. The workflow builds, runs `twine check --strict`, refuses to
   continue if the tag and the packaged version disagree, and then uploads.

Do a dry run against TestPyPI first if you want one: add a second pending publisher on
https://test.pypi.org and temporarily point the `pypa/gh-action-pypi-publish` step at it with
`with: repository-url: https://test.pypi.org/legacy/`.
