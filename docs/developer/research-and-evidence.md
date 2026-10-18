# Research and evidence

Stage 0 retains the existing decomposition workflow and short-reference contract.
`decomposer.py` splits questions into at most five parts, invokes RAG workers
with a bounded thread pool (default four), restores question order, then combines
worker prose. Timeline routing separately uses the researcher graph; decomposed
questions do not inherit its validation/repair workflow.

## Short references and current safeguards

Small local models can copy short labels more reliably than long chunk hashes.
`_build_label_map` assigns `REF_A1`, `REF_B1`, etc. in worker/opened-chunk order;
shared chunks retain the first label. Only opened chunks establish eligible IDs.
A worker's citations list cannot create evidence. Failed workers supply no labels.
`_format_sub_answers_labelled` supplies labelled prose to the actual
`synthesize_answers.txt` template or its built-in fallback. There is no restored
`_format_sub_answers` compatibility shim: the tests now exercise the intended API.

After generation, known labels map back to chunk IDs. Only references actually
supplied in the labelled worker prose are eligible for the generated answer.
Unknown, numeric, partial, empty or invented markers reject the entire draft;
removing a marker cannot make its claim grounded. Empty or uncited output also
rejects the draft. At least half of nonempty blank-line-separated paragraphs
must cite eligible evidence. This conservative mechanical threshold follows the
existing researcher's density threshold; it does not establish factual support.
Exact raw eligible chunk markers remain accepted for compatibility.

A rejected draft or generation exception falls back to worker prose that passes
the same checks against that worker's own opened IDs. Failed, empty or invalid
workers are omitted with an explicit “Research incomplete for” notice. Returned
citation lists follow first appearance and contain only references present in
accepted text; unused opened chunks are excluded. With no usable answers, the
response abstains and identifies missing parts. A single surviving answer keeps
the legacy passthrough path but is checked and cannot hide missing siblings.
Known URL citations remain supported for the existing web paths until Stage 1.

`None` list/context/token fields are normalized. Synthesis usage is counted even
when its draft is rejected; unknown usage remains zero under the legacy contract.
The supervisor's preserved merge normalization supplies empty display defaults.
UI numberization remains a later chunk-ID → numbered-source projection.

## Limits and deferred semantics

These checks establish reference membership and paragraph coverage, not truth,
entailment or complete question coverage. A valid marker can cite irrelevant
text; one cited clause can mask unsupported clauses. The legacy `opened` record
has no immutable snapshot/excerpt identity or proof of exactly what generation
saw. Worker abstention prose and technical failure still lack typed, distinct
terminal results. An answer with a citation can still be an abstention, and a
nonempty worker answer may omit requested scope. No semantic classifier is added
to pretend these distinctions are solved.

Partial notices identify known failed/empty workers (and rejected fallback
workers); generated synthesis is not checked for omission of every viable task.
The legacy passthrough variant name also covers all-unusable results; inspect
its missing-parts metadata and answer instead of treating the name as success.
Synthesis fallback is checked worker prose, not independently verified findings.
There is no synthesis repair call in Stage 0. Threads share clients; duplicate
questions, cancellation, resource admission and trace propagation remain later
work. Summary-only context is still omitted by the legacy synthesis prompt path.

Stage 2 replaces these semantics with independent 1..3 tasks, typed statuses,
locally hydrated/presented excerpt records, validated evidence-linked findings,
deterministic packet assembly and structured synthesis for every task count.
Final validation and at most one repair must reject invalid references; a safe
fallback then renders validated findings with explicit gaps. Those guarantees
are specified in [the plan, §4](../migration.md#4-proposed-final-architecture),
not implemented here. Historical dates, uncertain ranges and conflicting accounts
still require explicit support and human evaluation; Simplewiki is an encyclopedia,
not a primary-source archive.
