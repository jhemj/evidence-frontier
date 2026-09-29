# Source-backed evidence selection

For source/comparison/focus working pages, use `source_selections` mode `excerpt` with passages to carry the
exact decisive text of a long excerpt into later comparison. Copy a contiguous
quote verbatim, its observation ID and (for a fragment) its parent source_span_id.
The harness resolves occurrence and verifies the exact quote, UTF-8 field range
and hash; it does not guess or repair text. Choose sufficient surrounding context
and contrary passages, not merely a matching keyword. A selection is partial:
unselected text remains in the immutable page and proves neither absence nor
completeness. Do not infer an entire file or count from a short quote. Other
fields, locators, checks and open objections remain binding. If the excerpt is
not necessary for your proposition, select a different original source instead
of citing an unneeded binary dump. `focus_page` means the previous combination
did not fit: reread the original page and produce a more focused source-backed
note, not another verbose copy of the same citations. The model chooses the
evidence; no source is removed by a relevance keyword rule. All proposed checks
remain proposals until actually run. Binary content alone does not prove code
execution, and a command in shell history does not prove connection success.

Historical assessments can include harness-owned `open_objections`,
`publication_status` and `publication_limit`: do not copy these into a finding.
Return only fields declared in the current output schema. `timeline_role` is
핵심, 참고 or 반증됨; fact operators are only equals or contains. A quoted literal
must be actual decoded text from the field, not a guessed hexadecimal escape.
Fact pointers retain the SAME meaning in review and reports: they address the
retained observation field, not a selected fragment substituted into the view.
For a short passage from a paged/selected excerpt use `contains`, not `equals`.
`equals` asserts the entire retained field is that value. The assertion must
match both the currently presented text and the retained field; hidden text is
never available for citation. This rule applies to page notes and final findings.

Do not transcribe binary padding or long runs of control characters as an
excerpt selection. Select meaningful evidence-bearing content and its needed
context. If a binary fragment adds no interpretable support or contradiction,
state that limit without inventing a fact or calling the file benign; do not
carry it as a positive citation merely because the page was read. Structured
hash/format metadata and static analysis are separate observations when present.

When a narrow proposition needs only existing non-excerpt fields (for example
the file size, inode, returned range or partial result), use
`source_selections` mode `metadata_only` with the observation ID and reason. This carries
all non-excerpt fields and an explicit body-omission marker. It is not a quote:
never put path/size JSON from another field into excerpt passages. Do not
cite discarded body spans or assert their text. This cannot discard an open
objection's source or justify content/execution/absence claims. The original
body remains immutable and may be revisited by later investigation.

Consolidate all propositions for the same required dossier into ONE finding.
Put each executed test outcome in `check_assessments`, not a second finding.
For metadata-only selections keep `evidence_span_ids` empty for that source in
both findings and check assessments: those span IDs refer to the omitted body,
not to the retained inode/path/size fields. Cite its observation ID and exact
non-excerpt fact_assertions instead.

Each observation can have only ONE choice: excerpt OR metadata_only. Excerpt
mode already retains all other metadata, so do not add another metadata choice.
Omit a source from source_selections to keep its whole presented view. A source
with no excerpt body needs no selection. If exactly one body field/span is
presented for the observation, its parent is unambiguous; with multiple body
spans identify the exact parent SPAN. A supplied wrong parent is never repaired.

Source selection covers citations in check_assessments too, not just findings.
A supports/refutes check outcome citing an excerpt-bearing result must retain
that body or select an exact decisive passage. Do not choose metadata_only for
that source while claiming its omitted body confirms a command or context.
For an inconclusive assessment based ONLY on check status/coverage, use no
observation citations; the check_id binds those metadata. Specific content
that affects the interpretation must still be cited and preserved.
