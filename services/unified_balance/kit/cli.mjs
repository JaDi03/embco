// One command per run, for the Python agent: `node cli.mjs <command>` with the input as JSON
// on stdin and the answer as one JSON line on stdout. Circle's key and entity secret come from
// the environment, never from the arguments.

import { run } from "./unified.mjs";

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const text = Buffer.concat(chunks).toString("utf8");
const answer = await run(process.argv[2], text ? JSON.parse(text) : {}, process.env);
process.stdout.write(`${JSON.stringify(answer, (_, v) => (typeof v === "bigint" ? v.toString() : v))}\n`);
process.exitCode = answer.error ? 1 : 0;
