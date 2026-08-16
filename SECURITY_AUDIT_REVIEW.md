# Security audit review

This document records the maintainer review of the private ERP Workbench 1.0
release-candidate security audit generated on 2026-08-16 at 11:58:06 UTC. The
private raw audit archive is not committed because it is a review artifact; the
final reviewed SBOM will be published with the release.

## Results

| Check | Result | Release assessment |
| --- | --- | --- |
| Package consistency | No broken requirements | Pass |
| Dependency advisories | No known vulnerabilities among 51 installed packages | Pass as of audit time; repeat before release |
| Possible secrets | No candidates found | Pass; repeat on the public-release commit |
| Microsoft Defender | No threats in `dist/ERPWorkbench` | Pass for the accepted private build |
| CycloneDX SBOM | 44 locked runtime components; 45 dependency nodes | Generated and structurally checked |
| Bandit | 0 high, 2 medium, 177 low | Medium findings reviewed below; low findings triaged |

All report-file SHA-256 values in `report_hashes.json` matched the supplied
files during review.

## Bandit review

Bandit reported 179 findings with high confidence:

| Test | Count | Review |
| --- | ---: | --- |
| B101: `assert` used | 117 | Expected in smoke tests; tests are not shipped as application logic |
| B110: `try/except/pass` | 39 | Low-severity reliability/diagnostic visibility concern, not an identified vulnerability |
| B112: `try/except/continue` | 13 | Low-severity reliability/diagnostic visibility concern, not an identified vulnerability |
| B404: `subprocess` imported | 4 | Calls use fixed argument arrays with `shell=False` |
| B603: subprocess without shell | 4 | Fixed commands or a previously authenticated installer path; no shell interpolation |
| B310: generic URL opener | 2 | Reviewed controls below; no unrestricted URL scheme accepted |

The two medium B310 findings were in `erpworkbench/updater.py`:

1. The release-metadata request opens the fixed
   `https://api.github.com/repos/haneechaitanya/Neurophysiology-Workbench/releases/latest`
   endpoint.
2. The installer request occurs only after `validate_release_for_download`
   confirms a stable three-part tag, the configured repository and release
   path, the exact installer filename/version, HTTPS, a size ceiling, and a
   valid SHA-256 supplied by GitHub. After redirects, the final URL must remain
   HTTPS on GitHub or a GitHub-controlled `githubusercontent.com` host. The
   completed file is size-checked and SHA-256-verified before it can be offered
   for execution.

These are false positives caused by Bandit treating `urllib.request.urlopen`
as generic even when surrounding validation restricts the input. The existing
updater smoke test confirms rejection of foreign hosts, HTTP, mismatched
versions/filenames, and missing digests. The Microsoft Store build must still
exclude the GitHub updater entirely because Store updates are Store-managed.

The B110/B112 items are retained as a post-1.0 code-quality backlog: silent
exception handling can make failures harder to diagnose, but the audit did not
identify a confidentiality, integrity, code-execution, or privilege-escalation
path from them. They should be improved incrementally with context-appropriate
logging rather than changed mechanically before release.

## Gate decision

The private source/dependency/secret scan and Defender scan pass for this
release candidate. No security finding currently blocks continuing to Store
packaging and clean-machine testing.

This is not the final security gate. Repeat dependency and secret scanning on
the exact public-release commit, scan the final Store MSIX, validate the Store
package identity and capabilities, and repeat clean install/offline/uninstall
tests before submission. A clean scan does not guarantee that antivirus or
SmartScreen will never warn.
