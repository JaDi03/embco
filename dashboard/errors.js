// Turn wallet and contract errors into sentences an owner can act on.

const CONTRACT_ERRORS = {
  NotOwner: "Only the owner of this shop contract can do that.",
  NotAuthorized: "This wallet is not allowed to do that on this shop contract.",
  IsPaused: "The agent is paused. Resume it first.",
  PayeeNotApproved: "That supplier wallet is not approved yet.",
  AlreadyPaid: "That invoice was already paid.",
  OverPaymentLimit: "The amount is above the per-payment limit.",
  OverWeeklyCap: "The amount is above what is left of this week's cap.",
  InvalidLimits: "The per-payment limit must be greater than zero and not above the weekly cap.",
  InvalidAddress: "That wallet address cannot be used here (it may be your own wallet).",
  InvalidPayment: "A payment needs a supplier, an amount and an invoice reference.",
  TransferFailed: "The USDC transfer failed. Check your balance and that payments are authorized in Settings.",
};

/** A readable message for anything thrown by MetaMask, ethers or the contracts. */
export function describeError(error) {
  if (!error) return "Something went wrong.";
  if (error.code === "ACTION_REJECTED" || error.code === 4001 || error?.info?.error?.code === 4001) {
    return "You cancelled the request in MetaMask.";
  }
  const name = error.revert?.name;
  if (name && CONTRACT_ERRORS[name]) return CONTRACT_ERRORS[name];
  if (error.code === "INSUFFICIENT_FUNDS") {
    return "Not enough USDC in your wallet to pay the network fee. Get test USDC from the faucet.";
  }
  if (error.name === "AmountError") return error.message;
  if (isBusyNetwork(error)) {
    return "The Arc test network is busy right now. Wait a minute and reload the page.";
  }
  const message = error.shortMessage || error.message || String(error);
  return message.length > 200 ? `${message.slice(0, 200)}...` : message;
}

/** Public nodes refuse reads when they are overloaded; ethers then reports no revert data. */
function isBusyNetwork(error) {
  let info = "";
  try {
    info = JSON.stringify(error.info ?? {}, (_key, value) => (typeof value === "bigint" ? value.toString() : value));
  } catch {
    info = "";
  }
  const text = `${error.shortMessage ?? ""} ${error.message ?? ""} ${info}`;
  return /rate limit|too many requests|missing revert data|-32005|429/i.test(text);
}

export { CONTRACT_ERRORS };
