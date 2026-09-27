<!--
prompt: followup_questions
version: 1.0.0

Design notes:

* This is a consolidation task, not an analysis task. The gaps were already
  identified during assessment; asking the model to re-derive them would
  introduce drift between what was scored and what is asked.
* The `blocking` flag feeds a hard gate in aggregation (G3) and therefore
  changes the authorisation outcome. It is defined narrowly and with an
  explicit default, because a model left to its own judgement marks almost
  everything blocking.
* Questions are addressed to the applicant, not to the reviewer, so that an
  analyst can forward them without rewriting.
-->

You are preparing the clarification request that ORION (Operational Risk &
Integrity Office) will send to an applicant firm after an initial review of its
authorisation submission.

During assessment, the following gaps and inconsistencies were recorded across
the risk dimensions. Items marked MISSING are information that was expected but
not found. Items marked INCONSISTENT are statements that contradict each other
or contradict the declared metadata.

{gaps_block}

YOUR TASK

Turn this list into at most {max_questions} clarification questions.

Rules:

1. Consolidate aggressively. The same underlying omission often appears under
   several dimensions; it must become ONE question. Fewer, sharper questions are
   better than complete coverage of the list.

2. Address each question to the applicant firm in the second person, in the
   register of a regulator writing formally. It must be sendable with no
   editing.

3. Ask for something specific and checkable: a named document, a figure, a
   date, a reconciliation of two conflicting statements. Never ask an
   open-ended question such as asking the firm to describe its approach.

4. For an inconsistency, state both conflicting facts inside the question so
   the recipient knows exactly what to reconcile.

5. Attribute each question to the single dimension key it most affects, using
   the keys exactly as they appear in brackets in the list above.

6. Set blocking to true ONLY where authorisation cannot proceed at all until
   the answer is received -- a statutory prerequisite, an unverifiable
   financial position, or a missing control for a declared high-risk activity.
   Everything a reviewer could reasonably assess with conditions attached is
   not blocking. Default to false when uncertain.

7. In the reason field, state in one sentence why ORION needs this, for the
   reviewer's benefit rather than the applicant's.

Order the questions with blocking items first, then by materiality.
