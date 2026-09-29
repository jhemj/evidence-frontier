# Windows investigation branches v1

For a scheduled-task/service/autorun lead, read the retained record and exact action
path, then search relevant retained event/process records. Distinguish registration,
trigger, process start, exit, connection and objective. Task XML alone proves only
configuration. Preserve principal, channel, source path and OS-instance identity.

Available follow-ups are search, read_source and correlate over retained evidence.
Do not request Linux read_file/static_file/archive_list or invent a Registry/EVTX
parser. Missing raw sources, unsupported parsers and retention gaps remain unknown.

PowerShell 400 is engine start; 403 is stop, not success; 4104 is script content, not
payload completion. Prefer recorded process/outcome context to language in scripts.
SRUM intervals are not remote destinations. Amcache/Shimcache are not execution
proof. Prefetch attribution needs path and OS identity, not only a matching basename.

Keep literal, WOW64-candidate and resolved paths distinct. Registry key last-write,
$SI/$FN and quarantine metadata are not automatically event or intrusion times.
Imported normalized rows and older AI reports do not independently verify raw data.
Compare documented authorized maintenance where available; familiar names, private
addresses or post-incident dates alone do not establish a benign actor.
