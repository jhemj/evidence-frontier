# Skill Temporal reconstruction

Apply to each important undated lead, uncertain year/timezone, or conflicting
timeline. Unknown time is an investigation question, not the end of inquiry.
This procedure grants no tools or permissions and never overrides source gates.

1. Look first for a time in the retained event's own bytes and parser fields.
   Check neighboring records in the SAME retained file/partition/inode; use
   read_source/read_file with a locator, not a guessed shell command. Distinguish
   the event's clock from an embedded copied/quoted timestamp. Establish host
   timezone/year/clock drift from evidence. Never silently assign local PC time.
2. For a file/configuration lead, inspect same-image filesystem metadata or a
   permitted read_file/static_file result's file_context. Compare mtime, ctime,
   birth/creation time where supported, and atime. Keep partition, OS instance,
   volume/snapshot and inode identity; path/name coincidence is insufficient.
   Do not use extracted artifact files' host timestamps or ingestion times.
3. Linux ctime is last inode status change, NOT creation time. mtime concerns
   content modification, birth time concerns the filesystem object, and atime
   concerns access; none alone proves execution or intrusion. On Windows keep
   CreationTime, LastWriteTime and ChangeTime distinct; cross-check filesystem
   attributes/journals/events only when available. Compare copy/restore/install,
   permission/ownership changes, retained timestamps and possible timestomping.
4. Derive the narrowest DEFENSIBLE point/range/relative order from actual anchors.
   A cron invocation can show the setting was usable at that recorded instant;
   it does not date installation or prove payload success. Two endpoints do not
   prove every intervening day. Nearby log offsets do not prove chronology if
   buffering, clock changes, copied data or multiline records are unexamined.
5. State the subject/proof stage, exact cited anchors, assumptions and confidence
   in visible wording: recorded event time / estimated event time / file-time
   reference / still unknown. If only file metadata exists, describe it as a
   time-search lead, not an event-time bound. Avoid unjustified numeric confidence.
   Request the highest-value next check to distinguish competing time hypotheses.
6. Revise titles/card_summary when a new timestamp or counterexample narrows or
   changes the chronology. Preserve earlier interpretations in history. If checks
   are unavailable or budget-limited, name the missing scope; never fill a date
   merely so a card can be sorted. Use existing schema fields, not invented keys.

Semantics references (definitions, not evidence):
- https://man7.org/linux/man-pages/man3/stat.3type.html
- https://learn.microsoft.com/en-us/windows/win32/sysinfo/file-times
