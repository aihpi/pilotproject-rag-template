**Issue**: [Provide the issue URL here]
`https://github.com/aihpi/pilotproject-rag-template/issues/___`

**Description**:
(Why this change, how it works, what you had to take care of — trade-offs, technical
debt, added packages, the unhappy path.)

#### -------------- remove everything below in the final commit message --------------

### Other infos
(Fill this if necessary, or leave empty)

### Checklist when creating a review
1.
- [ ] The PR title follows Conventional Commits (see `Note & instructions` below)
- [ ] I linked the issue in the section above
- [ ] I filled the `Description` section above
2.
- [ ] The code is easy to understand: self-explanatory, or commented if necessary
- [ ] I quickly reviewed the code diff in GitHub
- [ ] Docs and `CHANGELOG.md` are updated in this PR
- [ ] `uv run pytest tests/ -q` passes (and I started the app once if this touches
      ingestion or the UI)

### Checklist before squash merging
- [ ] Re-check that the commit message title is ok (see `Note & instructions` below)
_(GitHub will add the PR id in parentheses at the end, please keep it)_
- [ ] Click on the `squash and merge` button
- [ ] In the squashed commit message body, remove the line `remove everything below in the final commit message` and everything below (so just keep the issue url and the description)

------
#### Note & instructions:

##### Commit message title and PR title rules

We use [Conventional Commits](https://www.conventionalcommits.org/) — see
[CONTRIBUTING.md](../CONTRIBUTING.md#commits):

```
<type>(<optional scope>): <imperative subject, no capital, no dot at the end>
```

`type` is one of `feat`, `fix`, `docs`, `test`, `refactor`, `perf`, `chore`, `ci`.
Use the scope for the area you touched (`config`, `kb`, `tools`, `images`, `ui`, …),
and append `!` (or a `BREAKING CHANGE:` footer) when a config or data layout has to
be migrated. Keep the subject under ~72 characters.

_examples_:
```
feat(kb): add semantic chunking strategy
fix(#29): normalize Unicode hyphens in keyword dedup
refactor!: drop IT-Grundschutz legacy and orphaned code
```

Reference the issue number in the scope (`fix(#29):`) or in the body — the PR id is
appended by GitHub on squash merge, so do not put it in the title yourself.

_note_: this title is important — it lands in the git history, is displayed in our
editors and is what makes the change understandable later.
