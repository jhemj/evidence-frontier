# Skill Reader-facing evidence cards

Apply when writing a candidate claim, dossier finding, or final hypothesis.
The reader must understand the substance without opening the source drawer.
This is a writing procedure, not a detector, fixed incident template, or verdict.

1. Read the actual source and identify its subject, artifact context, observed
   action and proof stage. Do not reuse a detector/category name as the title.
   A broad label such as "suspicious persistence" does not say what happened.
2. Title: specific subject + distinguishing observation. Prefer a compact Korean
   sentence or phrase (roughly 25–65 characters), retaining meaningful accounts,
   frequency, source context or paths only when they distinguish this lead.
   Do not dump a long path/hash or observation ID in place of an explanation.
3. card_summary: two or three short Korean sentences (normally 100–220 characters,
   at most 280). State what the source shows, why it bears on the incident, and
   the key uncertainty/competing explanation that prevents an overstrong reading.
   This field is a concise evidence-backed explanation, not private reasoning.
   Keep the fuller source comparison in reason; do not merely repeat the title.
4. A configuration is not a successful execution. A command string in a manual
   is not a host command record. Missing target bytes do not clear an invocation.
   Preserve these distinctions in the visible title/summary, not only deep in
   alternatives. A familiar name and static capabilities alone prove neither
   malicious use nor legitimate authorization.
5. Prefer plain terms first; explain an essential technical term briefly. Never
   invent an account, event time, operational impact, confidence or connection to
   make a nicer card. Use only cited evidence and retain source IDs separately.
6. Before submitting, read title and card_summary together without the other
   fields: can an analyst tell what was seen, why it matters, and what remains
   unproved? If not, rewrite them. If the source is too limited, say exactly what
   is unavailable and which narrow observation is nevertheless retained.
7. Evaluate incident relevance separately from confidence in a narrow fact.
   A verified static symbol, detector match, readable file or successful follow-up
   does not by itself make a lead central to the incident. Use 핵심 only with a
   source-backed explanation of its relationship to the investigation question;
   otherwise retain it as context and explain what link is still missing. New
   behavioral, temporal or identity evidence may later raise its relevance. Do
   not use familiar vendor/file names as a shortcut to relevance or normality.

Existing saved findings without card_summary remain historical; do not silently
rewrite them as if the model had produced a new explanation.
