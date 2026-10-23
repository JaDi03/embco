// A decision proof carried inside a link, after the "#": browsers never send that part to the
// server (RFC 3986, section 3.5), so the proof goes from the owner to the reader and Embco keeps
// nothing. The proof is gzipped and written in base64url.

const PREFIX = "#proof=";

async function through(bytes, stream) {
  return new Uint8Array(await new Response(new Blob([bytes]).stream().pipeThrough(stream)).arrayBuffer());
}

function toBase64Url(bytes) {
  let text = "";
  for (let i = 0; i < bytes.length; i += 0x8000) text += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(text).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function fromBase64Url(text) {
  const binary = atob(text.replaceAll("-", "+").replaceAll("_", "/"));
  return Uint8Array.from(binary, (c) => c.charCodeAt(0));
}

/** The link to the audit page that opens and checks this proof. */
export async function proofLink(bundle, auditPage) {
  const zipped = await through(new TextEncoder().encode(JSON.stringify(bundle)), new CompressionStream("gzip"));
  return `${auditPage}${PREFIX}${toBase64Url(zipped)}`;
}

/** The proof in a link's hash, or null when the hash carries none. Throws a sentence when the
 * link is broken (cut short when it was copied, for example). */
export async function proofFromHash(hash) {
  if (!String(hash ?? "").startsWith(PREFIX)) return null;
  try {
    const bytes = await through(fromBase64Url(hash.slice(PREFIX.length)), new DecompressionStream("gzip"));
    return JSON.parse(new TextDecoder().decode(bytes));
  } catch {
    throw new Error("This link is incomplete: it may have been cut when it was copied. Ask for it again.");
  }
}
