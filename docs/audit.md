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

## What this does not prove

- That an entry was true when it was written: the journal records what the agent read and said,
  not that the ERP or the chain told it the truth.
- That nothing is missing after the last anchor: the newest entries are covered by the next one.
- Anything before the first anchor.
