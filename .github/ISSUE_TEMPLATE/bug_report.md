---
name: Bug report
about: Something in Fab OS does not work as described
title: ''
labels: bug
assignees: ''
---

**Do not report security problems here.** Follow `SECURITY.md` (info@patienceai.in) instead.

### What happened

A clear description of the problem and what you expected instead.

### Where

- Fab OS version (`cat /etc/os-release`, or the image/ISO file name):
- Profile: VM image / live ISO / installed system / stock Ubuntu 26.04 with `fabos-desktop-meta`
- Component: boot / greeter / desktop shell / an application / agent / Fab OS Updates / Fab Feedback / build scripts / website
- Hardware or VM (CPU, GPU, RAM; QEMU flags if a VM):

### Steps to reproduce

1.
2.
3.

### Evidence

Screenshots (`tests/ui-tour.sh` frames or `scripts/vm-screenshot.sh`), `journalctl -b` excerpts,
`build/serial-vm.log` lines, or the failing line from `tests/branding-check.sh`.

### Checklist

- [ ] I searched existing issues.
- [ ] The report contains no passwords, API keys or personal data of other people.
