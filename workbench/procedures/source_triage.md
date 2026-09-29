# Skill Source triage

Purpose: decide which available evidence can answer the user's question before
spending checks on familiar-looking or repeatedly presented records.

Apply when first reviewing evidence, when a new source family appears, or when
many detector matches compete for attention. This skill is reasoning guidance,
not a whitelist, severity verdict, command runner, or replacement for raw sources.

1. Separate artifact content, configuration, recorded invocation, runtime state
   and outcome. A command in documentation or a detection script is content,
   not a host execution record. A filename or vendor directory alone cannot
   establish that distinction: inspect the retained context and provenance.
   A familiar executable/library name is not a verified benign identity. Without
   a source-backed package/content comparison, state that the observed static
   trait does not establish malicious behavior, not that the file is known-good.
   Even a local package match is one integrity baseline, not authorization or
   proof that the package database itself is independently trustworthy.
   Do not upgrade "a legitimate alternative exists" to "probably normal"
   without discriminating source evidence. This applies to every file name.
2. Treat detector labels as leads. Infer the narrow behavior from the bytes. A
   socket path reference can be a reachability test; general ELF imports can
   occur in many programs. Ask what discriminates an interactive shell or code
   injection from the legitimate alternative instead of repeating a rule name.
3. Inspect the selection audit. Repeated matches from one rule/source family do
   not establish greater importance. Missing families are not cleared families.
   Prefer unresolved cross-source behavior, live persistence targets, privilege
   changes and source-backed outcomes over another example of an already-known
   static trait when that check would better distinguish competing explanations.
4. Keep ordinary activity as context without making it the sole investigation.
   Explain why the next check is useful: which plausible explanations will it
   separate, and how will each possible result change the assessment?
5. Preserve exact source IDs, offsets, path/volume identity and reported time
   basis. Do not join an authentication and a command solely because their
   account or timestamp is similar. A test or maintenance label is not proof of
   authorization. Distinct log files may contain copies of the same source event.
   In command logs, distinguish the recorded working directory from the command
   verb and its arguments. A directory in the log prefix is not the destination
   of cd or the target of a file operation. A recorded account/session label does
   not establish a unique person; a command string does not establish success.

Output: short scoped candidate facts, legitimate alternatives, and the highest
information-value permitted follow-ups. No preselected IOC, path or conclusion.

When deferring context/noise, state why its current role does not discriminate
the case and what would reopen it (actual invocation, object mutation, new
question/episode link or contrary evidence). Unreviewed budget-deferred content
is not reviewed benign content. Do not silently clear the former. Related new
evidence can invalidate an interpretation without erasing still-valid source
facts; unrelated noise must not restart every question or page review.
