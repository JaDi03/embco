// The part of EmbcoCampaigns the agent uses. Written by hand; chain.test.ts checks
// every item against the compiled contract so the two can't drift apart.

import { parseAbi } from "viem";

export const CAMPAIGNS_ABI = parseAbi([
  "struct Campaign { address owner; address agent; bool paused; uint32 taskCount; uint8 maxSubmissionsPerTask; uint256 reward; uint256 balance; uint256 capPerPeriod; uint256 workerCapPerPeriod; uint256 periodLength; uint256 periodStart; uint256 spentInPeriod; }",
  "struct Submission { uint256 campaignId; address worker; uint32 taskId; uint8 status; bytes32 contentHash; bytes32 decisionHash; }",
  "struct WorkerSpend { uint256 spent; uint256 periodStart; }",
  "function submissionCount() view returns (uint256)",
  "function getCampaign(uint256 campaignId) view returns (Campaign)",
  "function getSubmission(uint256 submissionId) view returns (Submission)",
  "function getWorkerSpend(uint256 campaignId, address worker) view returns (WorkerSpend)",
  "function pay(uint256 submissionId, bytes32 decisionHash)",
  "function escalate(uint256 submissionId, bytes32 decisionHash)",
  "function reject(uint256 submissionId, bytes32 decisionHash)",
  "error InvalidParams()",
  "error UnknownCampaign()",
  "error UnknownTask()",
  "error NotOwner()",
  "error NotAgent()",
  "error NotOwnerOrAgent()",
  "error IsPaused()",
  "error AlreadySubmitted()",
  "error TaskFull()",
  "error EmptyHash()",
  "error WrongStatus()",
  "error InsufficientBudget()",
  "error OverPeriodCap()",
  "error OverWorkerCap()",
  "error NotLower()",
  "error TransferFailed()",
]);

/** Order matches `enum Status` in the contract. */
export const SUBMISSION_STATUS = ["none", "submitted", "escalated", "paid", "rejected"] as const;
export type SubmissionStatus = (typeof SUBMISSION_STATUS)[number];
