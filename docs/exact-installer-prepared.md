# Exact-release Installer: prepared, not started

The explicit exact-release mode prepares an inactive installation from local,
independently pinned release artifacts. It is additional to the legacy installer;
running without the exact options still selects the legacy workflow. This document
does not claim an activated installation, a completed deployment, or a fully
qualified product release.

## Inputs and trust

Use the reviewed bootstrap `install_cosmos.py` together with its adjacent
`installer_release.py`. A release supplies these separate inputs:

- A `cosmos.exact-install-profile.v1` JSON profile and its independently obtained
  SHA-256. The profile pins the release manifest, local artifact paths, safe
  example files, and the required already-installed runtime identity.
- A `cosmos.exact-release.v1` manifest with complete per-artifact and final
  per-role file maps. Producer, consumer, and catalog sources may have different
  origins; they are reconstructed using an explicit base, overlays, and removals.
- Local ZIP artifacts for those sources, native solver, and catalog assets.
  The native artifact must contain `quarkus-app/quarkus-run.jar`.
- A separately supplied `cosmos.exact-site.v1` document with fresh-site choices:
  four distinct ports, public catalog URL, supported authentication mode and
  public metadata, language, contour, LLM backend, and data policy.
- An already prepared compatible Python environment and JDK 21 directory.

The schema is documented in `installer_release.py`; profile/site validation is
defined in `install_cosmos.py`. Unknown keys and unsupported combinations are
rejected. Site choices must not include provider keys, copied authentication
state, or another installation's executor qualification.

The manifest's `public_identity` identifies the actual sanitized public
bootstrap/source revision. Source `origin_commit` fields record provenance;
they do not promise that those origins are fetchable from the public repository.
Do not substitute an origin commit for a real public revision. Keep the manifest
and profile detached from that revision to avoid a self-referential hash.

## Explicit invocation

Use the Python executable belonging to the independently pinned runtime profile.
The placeholders below must be replaced with reviewed local inputs; they are not
download locations or real release pins.

```text
python install_cosmos.py --exact-profile <profile.json> --exact-profile-sha256 <64-hex-digest> --exact-bundle <local-bundle-directory> --exact-site <fresh-site.json> --java-home <jdk21-directory> --target <absent-target-directory> --probe
```

`--probe` checks inputs, runtime identity, and archive contents without creating
the target. Removing only `--probe` prepares the inactive tree. Do not mix exact
arguments with legacy `--answers`, `--port`, `--catalog-port`, or a custom `--repo`.
Missing inputs, runtime mismatch, or unsupported qualification refuse the exact
operation; they never silently fall back to a rolling Git checkout or rebuild.

Preparation does not fetch Git, install dependencies, rebuild Maven, start
services, open ports, configure a tunnel, or activate an installation. Existing
targets are refused rather than merged or replaced. The receipt must say
`prepared-not-started`, with `activated: false` and `live_acceptance: false`.

## Prepared layout and later operation

Producer, consumer, and catalog have separate working directories without
hardlinks. Native files and catalog assets remain separate. Complete hash maps,
ZIP membership, removals, unsafe paths, links, and case collisions are checked.
Only allowlisted public JSON examples are copied to their explicit destinations;
secrets, private site configuration, and TEST qualification are not transferred.

The public catalog navigation URL is preserved independently from loopback
machine callbacks. Generated CDPM launch configuration uses export protocol
`1.21`. The generated manual `run-exact.py` checks its configuration/receipt and
runtime identity; it is not executed during preparation. Actual startup and
own-site executor qualification are separate operational steps. Webapp/CDPM need
a fresh matching channel token supplied for the new installation; provider
setup must be performed explicitly. Do not use the legacy uninstaller against
this exact-layout tree.

## Evidence boundary and current limits

Offline tests exercise synthetic healthy and adversarial archives, three-role
composition, failure cleanup, runtime refusal, public/machine URL separation,
and launch arguments without starting the services. These tests are not a
complete application pipeline or a clean-computer installation demonstration.

Runtime checks pin Python/JDK executables, Java release metadata, and the full
Python distribution name/version map. They are not full wheel, standard-library,
or JDK-library byte attestation. A different or unprepared computer requires a
supported prepared runtime; this mode does not install one automatically.

Recorded TEST calculation/export results must not be broadened into complete
H0 source/catalog acceptance: that scope remains incomplete. Later producer
fixes require their own exact-version functional evidence. H0-B/H0-C evidence
also retains its stated scope. Installer tests establish neither global solver
optimality/full cost nor A12 language-understanding quality. Repository-wide CI
and publication/privacy qualification remain separate; this patch makes no
claim that all CI checks or all release acceptance conditions are green.
