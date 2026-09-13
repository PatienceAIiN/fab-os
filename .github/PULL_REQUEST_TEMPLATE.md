### What this changes

One or two sentences. Link the issue if there is one.

### Why

### How I verified it

- [ ] `tests/branding-check.sh vm` passes (paste the failing/passing lines if relevant)
- [ ] `python3 tests/agent-test.py` and `python3 tests/feedback-test.py` pass
- [ ] `tests/boot-test.sh` passes (if boot, image or packaging changed)
- [ ] `tests/ui-tour.sh` frames reviewed (if anything visible changed) — list the frames you looked at
- [ ] Packages still install and remove cleanly on stock Ubuntu 26.04 (if packaging changed)

### Checklist

- [ ] Every commit is signed off (`git commit -s`, Developer Certificate of Origin)
- [ ] No hard-coded identity strings; everything comes from `brand/brand.conf`
- [ ] No third-party trademarks or artwork added; attribution untouched
- [ ] `legal/` updated if the set of modified upstream packages or third-party components changed
- [ ] ADR added or updated for architectural changes (`docs/decisions/`)
- [ ] `UI_REVAMP_CHANGELOG.md` updated for user-visible changes
- [ ] No secrets, credentials or personal data in the diff
