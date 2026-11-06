## What I Built
I built ask.py, a Python command-line tool that answers questions from a folder of documents using retrieval-augmented generation. It splits each HTML or PDF document into one chunk per section, stores the chunks in a local Chroma database using Chroma's built-in embedding model, retrieves the 4 most similar chunks for each question, and sends them to Claude (claude-sonnet-5) with instructions to answer only from those sources and cite the source file for every fact. I first built it for Copperline Manufacturing's finance documents. To make it work with Larkfield Credit Union's documents, I added a --docs option to choose the document folder, kept a separate git-ignored Chroma database for each folder, made Claude's instructions generic instead of Copperline-specific, and added a --save option that writes the retrieved chunks and the answer to a text file. I then saved seven runs in week-03/runs: the same overdraft-fee question before and after fixing a duplicate fee schedule (q1-before, q1-after), and five more questions (q2 to q6) covering rush card replacement fees, fraud liability, refund approval across two documents, check deposit availability, and a mortgage question the documents don't cover.

## Why This Approach
Chunking method: structure
Makes sense on its own: Yes, because every chunk starts with its document title, section heading, and version line, so for example the Wire transfers chunk reads "Document: Consumer Fee Schedule / Section: Wire transfers" and its $45 fee is clear without the rest of the page.
Label and source: Both, because each chunk's text begins with "Document:" and "Section:" lines, and the source file is stored as metadata and printed with every retrieved chunk, such as "Rank 1 | similarity 0.649 | consumer-fee-schedule.html".
Short enough: Yes, because I measured every chunk with the embedding model's own tokenizer: the longest Larkfield chunk is 128 words (173 tokens) and the longest overall is 147 words (207 tokens), all under the model's 256-token limit, so none are cut off.

## Example Run
```text
Question: Can I refund the outgoing international wire transfer fee for a member without approval from a manager?

Rank 1 | similarity 0.649 | consumer-fee-schedule.html
Document: Consumer Fee Schedule
Section: Wire transfers
Document status: Version 8 (current). Replaces Version 7. Owner: Member Services.

- Incoming domestic wire transfer: $10.
- Incoming international wire transfer: $15.
- Outgoing domestic wire transfer: $25.
- Outgoing international wire transfer: $45. The receiving bank or an intermediary bank may deduct its own fees from the amount sent.

Rank 2 | similarity 0.594 | fee-refund-guide.html
Document: Fee Refund Guide for Member Service Staff
Section: Refunds that need a branch manager
Document status: Version 2 (current). Owner: Member Services.

A refund needs the branch manager's approval when the fee is more than $30, when the member has already had a refund in the last 90 days, or when the member asks for several fees to be refunded at once. A branch manager may approve refunds of up to $150 in total for one member in any 90-day period.

Rank 3 | similarity 0.569 | fee-refund-guide.html
Document: Fee Refund Guide for Member Service Staff
Section: What a member service representative can approve
Document status: Version 2 (current). Owner: Member Services.

A member service representative may approve a refund of a single fee of $30 or less without anyone else's approval. A representative may approve only one refund for the same member in any 90-day period.

Rank 4 | similarity 0.496 | fee-refund-guide.html
Document: Fee Refund Guide for Member Service Staff
Section: Refunds that need the Member Services Director
Document status: Version 2 (current). Owner: Member Services.

Refunds of more than $150 in total for one member in any 90-day period need the Member Services Director's approval. So does any refund for a business account.

ANSWER
No. The outgoing international wire transfer fee is $45 [consumer-fee-schedule.html]. A member service representative can only approve refunds of a single fee of $30 or less without anyone else's approval [fee-refund-guide.html]. Since $45 exceeds that threshold, it requires branch manager approval (refunds over $30 need the branch manager, and a branch manager may approve up to $150 total per member in a 90-day period) [fee-refund-guide.html].
```

## Reflection
What was wrong: consumer-fee-schedule-v7.html was an outdated Version 7 fee schedule that still called itself "(current)", and the retrieved chunks for Question 1 showed it filling ranks 2 and 4 with a conflicting $35 overdraft fee and four-fees-per-day limit, right behind Version 8's correct $29 fee at a similarity only 0.015 higher.
How I fixed it: Removed consumer-fee-schedule-v7.html from the week-03/larkfield-docs folder and rebuilt the database with --rebuild, which dropped it from 39 to 30 chunks so every retrieved chunk in q1-after came from the current Version 8.
Prevention: Archive process, so whenever Member Services publishes a new version of a document, the old version is moved out of the library into an archive folder that the RAG tool never indexes, on the same day.
Question 6: Declined, because retrieval returned only weakly related chunks with low similarity scores (0.20 to 0.27) about share certificate dividend rates, stop payment fees, and checking account fees, and none of them mentioned mortgages.

## Pledge and AI Use Disclosure
I pledge that I have neither given nor received unauthorized help on this assignment. I used Claude Sonnet to help craft my responses to the writeup section and to generate the code and python tools used in this assignment. -John Budd-