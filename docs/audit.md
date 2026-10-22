# Audit the agent

Every time the agent works it writes what woke it, each tool it called, the exact answer it read
and what it decided into a journal. After money moves, and at least once a day, it writes one
number summarizing the whole journal on Arc: the Merkle root. This page shows how to check a copy
of the journal against those numbers, without trusting embco's server.

## What you can prove

- A copy of the journal is the one the agent anchored: no entry was changed, removed or reordered
  since. A single changed letter gives a different root.
- What the agent saw and decided before a payment or an Earn move, as it was written that day.

The root does not say what changed, only that something did. That is why the owner keeps a
downloaded copy: with an old good copy and a new one, the entries that differ are the ones that
changed.

## 1. Get the journal

In the dashboard, open **Agent activity** and press **Download journal**. The file has:

| Field | What it is |
|---|---|
| `shop` | The shop contract the journal belongs to |
| `entries` | Every entry exactly as stored: `kind`, `run`, `invoice`, `at`, `body`, `hash` |
| `merkle` | The root of the whole file, computed by the browser that downloaded it. A convenience: recompute it |
| `anchors_on_arc` | The roots read from Arc at download time, and whether each matches this file |
| `chain_intact` | Whether each entry's hash follows from the one before it |
| `decisions` | One proof per decision of the agent that is anchored already (see [Prove one decision](#4-prove-one-decision)) |
| `decisions_not_anchored_yet` | How many decisions the next anchor will cover |

## 2. Check it

**In the browser, nothing to install.** Open `/audit/` on the dashboard's site, choose the file
and read the result. The file never leaves your computer and no sign-in is needed. The page reads
the roots from the contract on Arc, not from the file.

**On the command line.** From a clone of the repository, with any Arc Testnet node:

```
embco audit embco-journal-0xbb7b11-2026-10-22.json --rpc-url <Arc Testnet node>
```

```
Journal of 0xbb7b118c...: 1158 entries
Hash chain: intact
Merkle root of the whole file: cc7722bb390a9e96...

  #  entries  written on Arc         root on Arc     root from the file
  1     1156  2026-10-22 08:20 UTC   ba19b1bb5356..  ba19b1bb5356..      OK
  2     1158  2026-10-22 08:33 UTC   cc7722bb390a..  cc7722bb390a..      OK

RESULT: the file matches every anchor on Arc; the last covers 1158 of its 1158 entries.
```

It exits with 0 when everything matches and 1 when something does not.

**By hand.** Each anchor is an `Anchored(shop, index, root, size, agent)` event of the
`JournalAnchor` contract. Read them in the **Logs** tab of the
[contract](https://explorer.testnet.arc.io/address/0x4385a2F8acda166F3FE3B3825F6950BA7dAc2582).
If the explorer shows the raw `data`, the first 64 hex characters are the `root` and the next 64
the `size`. Then compute the root of the first `size` entries of the file and compare.

## How the root is computed

1. Each entry's hash is SHA-256 of `embco-journal-v1`, the previous entry's hash and the entry's
   content, in canonical JSON (sorted keys, no spaces). The first entry follows 64 zeros.
2. The leaves are those hashes, in order. The root is the Merkle tree hash of
   [RFC 9162](https://www.rfc-editor.org/rfc/rfc9162) (the one Certificate Transparency uses):
   leaves hashed with a `0x00` prefix, nodes with `0x01`.
3. The anchor of size N covers the first N entries, so a journal that keeps growing still matches
   every older anchor.

The implementations are `agent/memory/models.py` and `agent/memory/merkle.py` (Python) and
`dashboard/journal.js` (browser). Both pass the Certificate Transparency reference vectors and
produce the same hashes for the same journal.

## 3. Prove one entry

For a dispute you rarely need the whole journal. `embco prove` makes a proof for one entry: the
entry itself, the hash of the entry before it, and the Merkle audit path (RFC 9162) from the
entry to the root of the first anchored entries.

```
embco prove embco-journal-0xbb7b11-2026-10-22.json --entry 1158 --out proof-1158.json --rpc-url <Arc Testnet node>
```

`--entry` counts from 1, as the entries are listed in the file. The proof uses the first anchor
on Arc that covers that entry. Anyone checks it with:

```
embco check-proof proof-1158.json --rpc-url <Arc Testnet node>
```

```
Entry 1158 of the journal of 0xbb7b118c...: SESSION at 2026-10-22T08:32:15.759680+00:00
Its text: {"alarms":[],"decided":[],...  [7156 characters in the proof file]

Its hash follows from the entry before it:  yes
It is in the tree of the first 1158 entries:  yes
That tree's root is written on Arc:  yes, 2026-10-22T08:33:24+00:00

RESULT: this entry was anchored on Arc, unchanged.
```

The checker recomputes the entry's hash from its text and the previous hash, follows the audit
path to the root, and reads the root from Arc. A proof file that claims a root Arc never wrote
fails. The proof does not contain the other entries. It does contain this entry's full text, so
send only what the dispute needs.

## 4. Prove one decision

A decision of the agent is more than one entry. Each session of the agent has an id, written in its
`SESSION` entry, in the `AGENT` entry of each choice it made and in each `PAYMENT` it sent. The proof
of one decision holds every entry it rests on, each with its own audit path:

- the `SESSION`: what woke the agent, each tool it called and what it read;
- the `AGENT` entry: its choice and its reason;
- the `DECISION` it was made on (the rule checks, by its `fingerprint`), that check's `RUN` and the
  `POLICY` the run names;
- the owner's `ANSWER` to that check, if any, and the session's `PAYMENT` entries for the invoice.

The proofs are made from the journal and the anchors when they are asked for: nothing is stored.
The dashboard shows the result under each receipt, in **What the agent saw**, and puts every
decision proof in the download under `decisions`. To share one decision without the rest of the
journal, save that item as its own file. Check it on `/audit/` or with:

```
embco check-proof decision.json --rpc-url <Arc Testnet node>
```

The checker proves each entry against Arc and refuses an entry that does not belong to the
decision: another session, another invoice or another rule check. Sessions written before
sessions had an id have no decision proof; their receipts show the proof of the session entry.

## 5. Keep a witness

An anchor is written by the agent's wallet. A **witness** is a record kept by someone else, which
shows when each anchor was first seen and compares it with a copy of the journal:

```
embco witness --shop 0xBB7b118C8d6640e7Cb168e71A385A56147CB952d --journal embco-journal-0xbb7b11-2026-10-22.json --rpc-url <Arc Testnet node>
```

Run it on a schedule with your latest copy of the journal. The first run records each anchor it
finds on Arc with the time it was first seen, and checks the ones your copy covers:

- `matches your copy`: the anchor is the first entries of your copy.
- `not checked yet`: the anchor covers more entries than your copy. Download a newer journal.
- `ALERT`: an anchor does not match your copy, an anchor seen before changed or disappeared, or
  your copy breaks the hash chain. Exit code 1, so a scheduler can notify you.

The record is a file (`witness-<shop>.json` by default). Keep it somewhere the agent's operator
cannot change: the copy in your own hands is what makes it a witness.

## What the root does not pin

A Merkle root alone does not say how many entries it covers. Each anchor on Arc carries its size,
and both `audit` and `check-proof` require the size to match an anchor with that root. A proof
that claims a different size fails, even though the path would compute the same root.

## What this does not prove

- That an entry was true when it was written: the journal records what the agent read and said,
  not that the ERP or the chain told it the truth.
- That nothing is missing after the last anchor: the newest entries are covered by the next one.
- Anything before the first anchor.
