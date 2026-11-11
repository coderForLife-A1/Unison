# Secure Unison auto-updater

The updater in [`updater/`](../updater) is a separate Rust module intended to
run from the daemon supervisor, not inside the currently running daemon binary.

## Trust and transport

The updater accepts a signed JSON manifest from an HTTPS URL. GitHub Releases
may be used as the transport source, but the release asset is not trusted just
because it is hosted by GitHub. The manifest is verified using the embedded
Ed25519 public key, then the downloaded payload is checked against its declared
size, SHA-256, and Ed25519 signature. Redirects are disabled and every final
response must remain HTTPS.

The production build must replace
`EMBEDDED_ED25519_PUBLIC_KEY` with the release key. Key rotation requires a
signed application release containing the next key; the update server cannot
rotate trust by itself.

Example manifest shape:

```json
{
  "manifest": {
    "version": "1.4.0",
    "target": "x86_64-pc-windows-msvc",
    "payload_url": "https://updates.example/unison-1.4.0.exe",
    "payload_sha256": "64-lowercase-hex-characters",
    "payload_size": 12345678,
    "payload_signature": "base64-ed25519-signature",
    "patch_base_version": "1.3.0"
  },
  "manifest_signature": "base64-ed25519-signature"
}
```

`patch_base_version` is metadata for binary patches. The updater still verifies
the complete staged result; a patcher must produce that result in a separate,
strictly bounded step before calling the swap routine. Never execute patch
instructions supplied by an unsigned manifest.

## Installation and rollback

1. Download into `<install>/<daemon>.staged`.
2. Flush and sync the staged file.
3. Rename the current binary to `<daemon>.rollback`, then rename the staged
   binary into place. Renames stay on the same filesystem for atomicity.
4. Spawn the new daemon with `--post-update`.
5. Wait at most five seconds for the daemon healthcheck callback.
6. On success, delete the rollback binary; on timeout, spawn failure, or failed
   ping, kill the candidate and restore `<daemon>.rollback`.

On Windows, if the running executable prevents an immediate rename, the module
uses `MoveFileExW(..., MOVEFILE_DELAY_UNTIL_REBOOT)` and returns
`RestartScheduled`. The daemon must show this state to the user and not claim
that the new version is active until the next startup healthcheck succeeds.

The updater never replaces a binary before signature verification, never
deletes the only known-good binary, and never follows an HTTP downgrade. Version
comparison uses semantic versions and only installs a strictly newer release.

## Scheduling and failure behavior

`run_periodic` performs the check at a configured interval. Network failures,
invalid manifests, signature failures, size mismatches, and healthcheck
failures leave the current installation unchanged and are reported to the
daemon supervisor. A production supervisor should add jitter, persist a
pending-restart marker, and rate-limit repeated failures.
