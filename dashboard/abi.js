// The parts of the contracts the dashboard uses, as human-readable ABI.
// Kept in sync with contracts/src/ShopPayables.sol and ShopPayablesFactory.sol.

export const FACTORY_ABI = [
  "function create(address agent, uint256 maxPerPayment, uint256 weeklyCap) returns (address)",
  "function shopsOf(address owner) view returns (address[])",
  "event ShopCreated(address indexed owner, address indexed shop, address agent, uint256 maxPerPayment, uint256 weeklyCap)",
  "error InvalidLimits()",
  "error InvalidAddress()",
];

export const SHOP_ABI = [
  "function owner() view returns (address)",
  "function agent() view returns (address)",
  "function maxPerPayment() view returns (uint256)",
  "function weeklyCap() view returns (uint256)",
  "function paused() view returns (bool)",
  "function remainingThisWeek() view returns (uint256)",
  "function approvedPayee(address) view returns (bool)",
  "function setLimits(uint256 maxPerPayment, uint256 weeklyCap)",
  "function setAgent(address agent)",
  "function setPayee(address payee, bool approved)",
  "function pause()",
  "function unpause()",
  "function pay(address payee, uint256 amount, bytes32 invoiceRef)",
  "event Paid(bytes32 indexed invoiceRef, address indexed payee, uint256 amount, address indexed by)",
  "event PayeeSet(address indexed payee, bool approved)",
  "error OwnableUnauthorizedAccount(address account)",
  "error NotAuthorized()",
  "error EnforcedPause()",
  "error ExpectedPause()",
  "error PayeeNotApproved(address payee)",
  "error AlreadyPaid(bytes32 invoiceRef)",
  "error OverPaymentLimit(uint256 amount, uint256 maxPerPayment)",
  "error OverWeeklyCap(uint256 amount, uint256 remaining)",
  "error InvalidLimits()",
  "error InvalidAddress()",
  "error InvalidPayment()",
  "error SafeERC20FailedOperation(address token)",
  "error OwnershipFixed()",
  "error ReentrancyGuardReentrantCall()",
];

// contracts/src/ShopFundingFactory.sol: one ShopFunding per owner on each source chain.
export const FUNDING_FACTORY_ABI = [
  "function create(address agent, uint256 maxPerMove, uint256 weeklyCap) returns (address)",
  "function fundingsOf(address owner) view returns (address[])",
  "function predict(address owner, address agent, uint256 maxPerMove, uint256 weeklyCap) view returns (address)",
  "error InvalidLimits()",
  "error InvalidAddress()",
];

// Circle Gateway's GatewayWallet (circlefin/evm-gateway-contracts, modules/wallet/Delegation.sol).
export const GATEWAY_WALLET_ABI = ["function addDelegate(address token, address delegate)"];

export const USDC_ABI = [
  "function balanceOf(address) view returns (uint256)",
  "function allowance(address owner, address spender) view returns (uint256)",
  "function approve(address spender, uint256 amount) returns (bool)",
];
