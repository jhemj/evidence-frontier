# Linux investigation branches v1

For a cron/systemd/autorun lead, follow the available evidence chain:
- Read the exact retained configuration and preserve partition, inode and path.
- Establish what registration/change time is actually known; file mtime or the
  first preserved log is not automatically the registration or intrusion time.
- Search the exact target path or discriminating command token in retained logs.
- Inspect invocation versus process start, outcome and any connection separately.
- Request read_file/static_file for the exact target if available; never execute it.
- Compare package/deployment/maintenance context and permissions. Equal hashes
  establish equal bytes, not equal authorization or execution at another path.
- Preserve absent execution/remote records as gaps, not proof of harmlessness.

For authentication or lateral-movement questions, compare recorded account,
session/host identity, key/path and explicit-zone times. Account/IP/time proximity
alone cannot join two actors or prove a successful follow-on action.

For network/exfiltration questions, distinguish configured destination, attempt,
failure, established connection and destination-side receipt. Prefer actual result
records over command intent. Missing network capture is not negative evidence.

Investigation scripts may contain signatures and malware names. Their presence in
a script is not a detection on the examined host. Compare the actual recorded result.
